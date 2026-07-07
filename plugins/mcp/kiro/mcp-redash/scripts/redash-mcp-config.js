#!/usr/bin/env node

/**
 * redash-mcp-config.js
 *
 * project root の .mcp.json を操作して Redash MCP の追加・削除・一覧・状態確認を行う。
 *
 * Usage:
 *   node redash-mcp-config.js add <suffix>
 *   node redash-mcp-config.js remove <suffix>
 *   node redash-mcp-config.js list
 *   node redash-mcp-config.js status
 */

const fs = require('fs');
const path = require('path');

const MCP_PACKAGE = '@suthio/redash-mcp';
const PROJECT_ROOT = resolveProjectRoot();
const MCP_JSON_PATH = path.join(PROJECT_ROOT, '.mcp.json');
const KIRO_AGENT_PATH = path.join(PROJECT_ROOT, '.kiro', 'agents', 'default.json');

// --- helpers ---

function envName(suffix) {
  const upper = suffix.toUpperCase().replaceAll('-', '_');
  return {
    url: `REDASH_${upper}_URL`,
    key: `REDASH_${upper}_API_KEY`,
  };
}

function mcpName(suffix) {
  return `redash-${suffix}`;
}

function validateSuffix(suffix) {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(suffix)) {
    console.error('エラー: suffix は英小文字・数字・ハイフンのみで指定してください。');
    console.error('例: dev, stg, prod2, sandbox-1');
    process.exit(1);
  }
}

function mcpEntry(suffix) {
  const env = envName(suffix);
  return {
    command: 'npx',
    args: ['-y', MCP_PACKAGE],
    env: {
      REDASH_URL: `\${${env.url}}`,
      REDASH_API_KEY: `\${${env.key}}`,
    },
  };
}

function isKiroRuntime() {
  const normalizedDir = path.normalize(__dirname);
  const runtimePath = path.join('mcp', 'kiro') + path.sep;
  return Boolean(process.env.KIRO_WORKSPACE_ROOT) || normalizedDir.includes(runtimePath);
}

function isCodexRuntime() {
  const normalizedDir = path.normalize(__dirname);
  const runtimePath = path.join('mcp', 'codex') + path.sep;
  return Boolean(process.env.CODEX_WORKSPACE_ROOT) || normalizedDir.includes(runtimePath);
}

function resolveProjectRoot() {
  const envNames = [
    'PROJECT_ROOT',
    'WORKSPACE_ROOT',
    'GIT_WORK_TREE',
    'CLAUDE_PROJECT_DIR',
    'CODEX_WORKSPACE_ROOT',
    'KIRO_WORKSPACE_ROOT',
  ];

  for (const name of envNames) {
    const value = process.env[name];
    if (value && fs.existsSync(value) && fs.statSync(value).isDirectory()) {
      return path.resolve(value);
    }
  }

  let dir = process.cwd();
  let nearestMcpDir = null;
  while (true) {
    if (!nearestMcpDir && fs.existsSync(path.join(dir, '.mcp.json'))) {
      nearestMcpDir = dir;
    }
    if (fs.existsSync(path.join(dir, '.git'))) {
      return dir;
    }

    const parent = path.dirname(dir);
    if (parent === dir) {
      return nearestMcpDir || process.cwd();
    }
    dir = parent;
  }
}

function createMcpJson() {
  return isCodexRuntime() ? {} : { mcpServers: {} };
}

function normalizeWritableMcpJson(data) {
  if (Object.prototype.hasOwnProperty.call(data, 'mcpServers')) {
    return data;
  }
  if (Object.prototype.hasOwnProperty.call(data, 'mcp_servers')) {
    const normalized = { ...data, mcpServers: data.mcp_servers };
    delete normalized.mcp_servers;
    return normalized;
  }
  return data;
}

function serverMap(data, create) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null;
  }

  if (Object.prototype.hasOwnProperty.call(data, 'mcpServers')) {
    if (!data.mcpServers && create) {
      data.mcpServers = {};
    }
    return data.mcpServers && typeof data.mcpServers === 'object' && !Array.isArray(data.mcpServers)
      ? data.mcpServers
      : null;
  }

  if (Object.prototype.hasOwnProperty.call(data, 'mcp_servers')) {
    if (!data.mcp_servers && create) {
      data.mcp_servers = {};
    }
    return data.mcp_servers && typeof data.mcp_servers === 'object' && !Array.isArray(data.mcp_servers)
      ? data.mcp_servers
      : null;
  }

  return data;
}

function readMcpJson() {
  if (!fs.existsSync(MCP_JSON_PATH)) {
    return null;
  }
  const raw = fs.readFileSync(MCP_JSON_PATH, 'utf-8');
  try {
    return JSON.parse(raw);
  } catch {
    return undefined; // 破損
  }
}

function writeMcpJson(data) {
  fs.writeFileSync(MCP_JSON_PATH, JSON.stringify(data, null, 2) + '\n', 'utf-8');
}

function syncKiroAgent(data) {
  if (!isKiroRuntime()) {
    return;
  }

  const servers = serverMap(data, false) || {};
  let agent = {};
  if (fs.existsSync(KIRO_AGENT_PATH)) {
    try {
      agent = JSON.parse(fs.readFileSync(KIRO_AGENT_PATH, 'utf-8'));
    } catch {
      console.error('エラー: .kiro/agents/default.json の JSON が壊れています。手動で修正してください。');
      process.exit(1);
    }
  } else {
    agent = {
      name: 'default',
      resources: ['skill://.kiro/skills/**/SKILL.md'],
    };
  }

  if (!agent.mcpServers || typeof agent.mcpServers !== 'object' || Array.isArray(agent.mcpServers)) {
    agent.mcpServers = {};
  }

  for (const name of Object.keys(agent.mcpServers)) {
    if (isRedashServer(name)) {
      delete agent.mcpServers[name];
    }
  }
  for (const [name, server] of Object.entries(servers)) {
    if (isRedashServer(name)) {
      agent.mcpServers[name] = server;
    }
  }

  fs.mkdirSync(path.dirname(KIRO_AGENT_PATH), { recursive: true });
  fs.writeFileSync(KIRO_AGENT_PATH, JSON.stringify(agent, null, 2) + '\n', 'utf-8');
}

function isRedashServer(name) {
  return name === 'redash' || name.startsWith('redash-');
}

// --- commands ---

function cmdAdd(suffix) {
  if (!suffix || suffix === 'default') {
    console.error('エラー: デフォルトの redash は plugin 同梱のため追加できません。');
    console.error('suffix には dev, stg, prod2, sandbox などを指定してください。');
    process.exit(1);
  }
  validateSuffix(suffix);

  const name = mcpName(suffix);
  let data = readMcpJson();

  if (data === undefined) {
    console.error('エラー: .mcp.json の JSON が壊れています。手動で修正してください。');
    process.exit(1);
  }

  if (data === null) {
    data = createMcpJson();
  } else {
    data = normalizeWritableMcpJson(data);
  }

  const servers = serverMap(data, true);
  if (!servers) {
    console.error('エラー: .mcp.json の MCP server 定義形式を解釈できません。');
    process.exit(1);
  }

  if (servers[name]) {
    console.log(`${name} は既に登録されています。変更はありません。`);
    process.exit(0);
  }

  servers[name] = mcpEntry(suffix);
  writeMcpJson(data);
  syncKiroAgent(data);

  const env = envName(suffix);
  console.log(`${name} を .mcp.json に追加しました。`);
  if (isKiroRuntime()) {
    console.log(`${name} を .kiro/agents/default.json に同期しました。`);
  }
  console.log('');
  console.log('必要な環境変数:');
  console.log(`  ${env.url}`);
  console.log(`  ${env.key}`);
  console.log('');
  console.log('プロジェクトの .env に設定してください。');
}

function cmdRemove(suffix) {
  if (!suffix || suffix === 'default') {
    console.error('エラー: デフォルトの redash は plugin 同梱のため削除できません。');
    process.exit(1);
  }
  validateSuffix(suffix);

  const name = mcpName(suffix);
  let data = readMcpJson();

  if (data === undefined) {
    console.error('エラー: .mcp.json の JSON が壊れています。手動で修正してください。');
    process.exit(1);
  }

  if (data !== null) {
    data = normalizeWritableMcpJson(data);
  }

  const servers = serverMap(data, false);
  if (data === null || !servers || !servers[name]) {
    console.log(`${name} は登録されていません。変更はありません。`);
    process.exit(0);
  }

  delete servers[name];
  writeMcpJson(data);
  syncKiroAgent(data);

  console.log(`${name} を .mcp.json から削除しました。`);
  if (isKiroRuntime()) {
    console.log(`${name} を .kiro/agents/default.json から削除しました。`);
  }
}

function cmdList() {
  // plugin 同梱分
  console.log('redash        (plugin bundled)');

  // project .mcp.json 分
  const data = readMcpJson();
  if (data === undefined) {
    console.error('警告: .mcp.json の JSON が壊れています。');
    return;
  }
  const servers = serverMap(data, false);
  if (servers) {
    const names = Object.keys(servers)
      .filter((n) => isRedashServer(n) && n !== 'redash')
      .sort();
    for (const name of names) {
      console.log(`${name.padEnd(14)}(project)`);
    }
  }
}

function cmdStatus() {
  console.log('=== Redash MCP Status ===');
  console.log('');

  // plugin 同梱
  console.log('[redash] (plugin bundled)');
  console.log('  環境変数: REDASH_URL, REDASH_API_KEY');
  printEnvWarnings(['REDASH_URL', 'REDASH_API_KEY']);
  console.log('');

  // project .mcp.json 分
  const data = readMcpJson();
  if (data === undefined) {
    console.error('警告: .mcp.json の JSON が壊れています。');
    return;
  }
  const servers = serverMap(data, false);
  if (servers) {
    const names = Object.keys(servers)
      .filter((n) => isRedashServer(n) && n !== 'redash')
      .sort();
    for (const name of names) {
      const suffix = name.replace('redash-', '');
      const env = envName(suffix);
      console.log(`[${name}] (project)`);
      console.log(`  環境変数: ${env.url}, ${env.key}`);
      printEnvWarnings([env.url, env.key]);
      console.log('');
    }
    if (names.length === 0) {
      console.log('追加の Redash MCP はありません。');
      console.log('/redash-add <suffix> で追加できます。');
    }
  } else {
    console.log('追加の Redash MCP はありません。');
    console.log('/redash-add <suffix> で追加できます。');
  }
}

function printEnvWarnings(vars) {
  const missing = vars.filter((v) => !process.env[v]);
  if (missing.length > 0) {
    for (const v of missing) {
      console.log(`  ⚠ ${v} が未設定です`);
    }
  }
}

// --- main ---

const [, , command, ...rest] = process.argv;
const suffix = rest[0] ? rest[0].toLowerCase() : '';

switch (command) {
  case 'add':
    cmdAdd(suffix);
    break;
  case 'remove':
    cmdRemove(suffix);
    break;
  case 'list':
    cmdList();
    break;
  case 'status':
    cmdStatus();
    break;
  default:
    console.error('Usage: redash-mcp-config.js <add|remove|list|status> [suffix]');
    process.exit(1);
}
