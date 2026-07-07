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

const MCP_JSON_PATH = path.resolve(process.cwd(), '.mcp.json');
const MCP_PACKAGE = '@suthio/redash-mcp';

// --- helpers ---

function envName(suffix) {
  const upper = suffix.toUpperCase();
  return {
    url: `REDASH_${upper}_URL`,
    key: `REDASH_${upper}_API_KEY`,
  };
}

function mcpName(suffix) {
  return `redash-${suffix}`;
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

  const name = mcpName(suffix);
  let data = readMcpJson();

  if (data === undefined) {
    console.error('エラー: .mcp.json の JSON が壊れています。手動で修正してください。');
    process.exit(1);
  }

  if (data === null) {
    data = { mcpServers: {} };
  }

  if (!data.mcpServers) {
    data.mcpServers = {};
  }

  if (data.mcpServers[name]) {
    console.log(`${name} は既に登録されています。変更はありません。`);
    process.exit(0);
  }

  data.mcpServers[name] = mcpEntry(suffix);
  writeMcpJson(data);

  const env = envName(suffix);
  console.log(`${name} を .mcp.json に追加しました。`);
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

  const name = mcpName(suffix);
  let data = readMcpJson();

  if (data === undefined) {
    console.error('エラー: .mcp.json の JSON が壊れています。手動で修正してください。');
    process.exit(1);
  }

  if (data === null || !data.mcpServers || !data.mcpServers[name]) {
    console.log(`${name} は登録されていません。変更はありません。`);
    process.exit(0);
  }

  delete data.mcpServers[name];
  writeMcpJson(data);

  console.log(`${name} を .mcp.json から削除しました。`);
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
  if (data && data.mcpServers) {
    const names = Object.keys(data.mcpServers)
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
  if (data && data.mcpServers) {
    const names = Object.keys(data.mcpServers)
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
