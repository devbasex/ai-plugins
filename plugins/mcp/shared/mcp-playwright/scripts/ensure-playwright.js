#!/usr/bin/env node

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PLUGIN_ROOT = process.env.PLUGIN_ROOT || process.env.CODEX_PLUGIN_ROOT || process.env.CLAUDE_PLUGIN_ROOT || __dirname;
const FLAG_FILE = path.join(os.homedir(), '.claude-playwright-installed');
const BROWSER_PATH = path.join(os.homedir(), '.cache', 'ms-playwright');
const TIMEOUT_MS = 5 * 60 * 1000; // 5分タイムアウト
const PLAYWRIGHT_VERSION_PATTERN = /^[~^]?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;
const EXEC_FILE_OPTIONS = process.platform === 'win32' ? { shell: true } : {};

function readInstalledVersion() {
  if (!fs.existsSync(FLAG_FILE)) {
    return null;
  }
  try {
    const flagData = JSON.parse(fs.readFileSync(FLAG_FILE, 'utf-8'));
    return typeof flagData.playwrightVersion === 'string' ? flagData.playwrightVersion : null;
  } catch {
    return null;
  }
}

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('🎭 Playwright MCP Plugin: 初回セットアップ');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('');
console.log('Playwright Chromiumブラウザをインストール中...');
console.log('ネットワーク環境により1-2分かかる場合があります。');
console.log('');

try {
  // @playwright/mcpが依存するPlaywrightのバージョンを取得
  console.log('🔍 @playwright/mcpが使用するPlaywrightバージョンを確認中...');
  const depsOutput = execFileSync('npm', ['view', '@playwright/mcp@latest', 'dependencies', '--json'], {
    encoding: 'utf-8',
    cwd: PLUGIN_ROOT,
    timeout: 30000,
    ...EXEC_FILE_OPTIONS
  });

  const deps = JSON.parse(depsOutput);
  const playwrightVersion = deps.playwright || deps['playwright-core'];

  if (!playwrightVersion) {
    throw new Error('@playwright/mcpの依存関係からPlaywrightバージョンを取得できませんでした');
  }
  if (!PLAYWRIGHT_VERSION_PATTERN.test(playwrightVersion)) {
    throw new Error(`不正なPlaywrightバージョンです: ${playwrightVersion}`);
  }

  console.log(`✓ Playwright ${playwrightVersion} を使用します`);
  console.log('');

  const installedVersion = readInstalledVersion();
  if (installedVersion === playwrightVersion) {
    console.log('既に同じ Playwright バージョンでセットアップ済みです。');
    process.exit(0);
  }

  // @playwright/mcpと互換性のあるバージョンのChromiumをインストール
  console.log(`📦 Playwright ${playwrightVersion} でChromiumをインストール中...`);
  execFileSync('npx', ['-y', `playwright@${playwrightVersion}`, 'install', 'chromium'], {
    stdio: 'inherit',
    cwd: PLUGIN_ROOT,
    timeout: TIMEOUT_MS,
    ...EXEC_FILE_OPTIONS,
    env: {
      ...process.env,
      PLAYWRIGHT_BROWSERS_PATH: BROWSER_PATH,
      PLAYWRIGHT_SKIP_BROWSER_GC: '1'
    }
  });

  // インストール成功フラグを作成
  const flagData = {
    installed: new Date().toISOString(),
    plugin: 'playwright',
    browser: 'chromium',
    browserPath: BROWSER_PATH,
    playwrightVersion: playwrightVersion
  };

  fs.writeFileSync(FLAG_FILE, JSON.stringify(flagData, null, 2));

  console.log('');
  console.log('✅ セットアップ完了！Playwright Chromiumの準備ができました。');
  console.log(`   Playwrightバージョン: ${playwrightVersion}`);
  console.log(`   ブラウザパス: ${BROWSER_PATH}`);
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  process.exit(0);

} catch (error) {
  console.error('');
  console.error('❌ Playwright Chromiumのインストールに失敗しました');
  console.error('');
  console.error('エラー:', error.message);
  console.error('');
  console.error('手動でインストールするには以下を実行してください:');
  console.error(`  npm view @playwright/mcp@latest dependencies`);
  console.error(`  PLAYWRIGHT_BROWSERS_PATH=${BROWSER_PATH} npx playwright@<version> install chromium`);
  console.error('');
  console.error('トラブルシューティング:');
  console.error('  https://playwright.dev/docs/browsers');
  console.error('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  process.exit(1);
}
