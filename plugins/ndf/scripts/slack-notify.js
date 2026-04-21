#!/usr/bin/env node
/**
 * Slack notification script for Claude Code session completion
 *
 * Reads transcript_path from stdin (hook input JSON) and generates
 * a session-specific summary using Claude CLI.
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const { spawn } = require('child_process');
const os = require('os');
const crypto = require('crypto');

// ============================================================================
// Constants
// ============================================================================

// Generate unique run ID for this execution to trace logs
const RUN_ID = crypto.randomBytes(4).toString('hex');

const CONFIG = {
  CLI_TIMEOUT_MS: 60000,
  MAX_RESPONSES: 5,
  MAX_TRANSCRIPT_LINES: 100,
  MAX_FILE_SIZE_BYTES: 10 * 1024 * 1024, // 10MB
  MIN_TEXT_LENGTH: 5,
  MIN_RESPONSE_LENGTH: 10,
  MAX_CONTENT_LENGTH: 500,
  MAX_CONTEXT_LENGTH: 2500,
  FALLBACK_REPO_NAME: 'unknown',
  NO_SUMMARY_MESSAGE: 'Claude Codeのセッションが終了しました(要約なし)',
  LOCK_TIMEOUT_MS: 30000, // Lock expires after 30 seconds
  NOTIFICATION_COOLDOWN_MS: 5000, // Prevent duplicate notifications within 5 seconds
  DELETE_DELAY_MS: 500, // Wait before deleting mention message
  LOG_DIR: path.join(os.homedir(), '.claude', 'logs')
};

const META_CONTENT_PREFIXES = ['Caveat:', '<local-command', '<command-name>'];

// ============================================================================
// Utilities
// ============================================================================

/**
 * Get log file path for today's date
 * Format: ~/.claude/logs/slack-notify-YYYY-MM-DD.log
 */
function getLogFilePath() {
  const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
  return path.join(CONFIG.LOG_DIR, `slack-notify-${today}.log`);
}

/**
 * Ensure log directory exists
 */
function ensureLogDirectory() {
  if (!fs.existsSync(CONFIG.LOG_DIR)) {
    fs.mkdirSync(CONFIG.LOG_DIR, { recursive: true });
  }
}

const isDebugMode = () => process.env.DEBUG_SLACK_NOTIFY === 'true';

const debugLog = (message, ...args) => {
  if (isDebugMode()) {
    const timestamp = new Date().toISOString();
    const logMessage = `[${timestamp}] [RUN:${RUN_ID}] [PID:${process.pid}] ${message}`;

    // Format additional arguments
    const formattedArgs = args.map(arg => {
      if (typeof arg === 'object') {
        return JSON.stringify(arg);
      }
      return String(arg);
    }).join(' ');

    const fullMessage = formattedArgs ? `${logMessage} ${formattedArgs}\n` : `${logMessage}\n`;

    // Write to stderr for backward compatibility
    process.stderr.write(fullMessage);

    // Write to log file
    try {
      ensureLogDirectory();
      fs.appendFileSync(getLogFilePath(), fullMessage, 'utf8');
    } catch (error) {
      // If file write fails, at least stderr output is available
      process.stderr.write(`[ERROR] Failed to write to log file: ${error.message}\n`);
    }
  }
};

const safeJsonParse = (str) => {
  try {
    return JSON.parse(str);
  } catch (e) {
    debugLog('JSON parse error:', e.message, 'Input:', str?.substring(0, 100));
    return null;
  }
};

const readFileLines = (filePath) => {
  if (!fs.existsSync(filePath)) {
    debugLog('File does not exist:', filePath);
    return null;
  }
  try {
    const stats = fs.statSync(filePath);
    debugLog('Transcript file stats:', {
      path: filePath,
      size: stats.size,
      mtime: stats.mtime.toISOString(),
      mtimeMs: stats.mtimeMs
    });
    if (stats.size > CONFIG.MAX_FILE_SIZE_BYTES) {
      debugLog('Transcript file too large:', stats.size, 'bytes');
      return null;
    }
    const content = fs.readFileSync(filePath, 'utf8').trim();
    const lines = content.split('\n');
    debugLog('Read', lines.length, 'lines from transcript | File size:', stats.size);
    return lines;
  } catch (error) {
    debugLog('Error reading transcript file:', error.message);
    return null;
  }
};

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// ============================================================================
// Lock Management (prevent duplicate execution)
// ============================================================================

function getLockFilePath() {
  // Use working directory hash instead of session ID for robust deduplication
  // This prevents multiple Claude Code instances in the same project from sending duplicate notifications
  const cwd = process.cwd();
  const hash = crypto.createHash('md5').update(cwd).digest('hex').substring(0, 8);
  return path.join(os.tmpdir(), `slack-notify-${hash}.lock`);
}

function acquireLock() {
  const lockFile = getLockFilePath();
  const now = Date.now();

  debugLog('Attempting to acquire lock:', lockFile);

  try {
    // Check if lock exists
    if (fs.existsSync(lockFile)) {
      const lockData = safeJsonParse(fs.readFileSync(lockFile, 'utf8'));
      debugLog('Existing lock found:', lockData);
      if (lockData) {
        // Check if notification was sent recently (cooldown period)
        if (lockData.completedAt && (now - lockData.completedAt) < CONFIG.NOTIFICATION_COOLDOWN_MS) {
          const elapsed = now - lockData.completedAt;
          debugLog('LOCK REJECTED: Notification sent recently | Elapsed:', elapsed, 'ms | Cooldown:', CONFIG.NOTIFICATION_COOLDOWN_MS, 'ms | Previous PID:', lockData.pid);
          return false;
        }
        // Check if another process is still holding the lock
        if (!lockData.completedAt && (now - lockData.timestamp) < CONFIG.LOCK_TIMEOUT_MS) {
          const elapsed = now - lockData.timestamp;
          debugLog('LOCK REJECTED: Lock held by another process | PID:', lockData.pid, '| Elapsed:', elapsed, 'ms | Timeout:', CONFIG.LOCK_TIMEOUT_MS, 'ms');
          return false;
        }
      }
      debugLog('Lock expired or cooldown ended, proceeding');
    } else {
      debugLog('No existing lock file found');
    }

    // Create lock
    const lockData = {
      pid: process.pid,
      runId: RUN_ID,
      timestamp: now,
      completedAt: null
    };
    fs.writeFileSync(lockFile, JSON.stringify(lockData));
    debugLog('LOCK ACQUIRED:', lockData);
    return true;
  } catch (error) {
    debugLog('Lock acquisition error:', error.message);
    return false;
  }
}

function releaseLock() {
  const lockFile = getLockFilePath();
  try {
    if (fs.existsSync(lockFile)) {
      // Update lock with completion time instead of deleting
      // This enables cooldown period to prevent duplicate notifications
      const lockData = safeJsonParse(fs.readFileSync(lockFile, 'utf8')) || {};
      lockData.completedAt = Date.now();
      lockData.releasedByRunId = RUN_ID;
      fs.writeFileSync(lockFile, JSON.stringify(lockData));
      debugLog('LOCK RELEASED:', lockData);
    } else {
      debugLog('Lock file not found during release');
    }
  } catch (error) {
    debugLog('Lock release error:', error.message);
  }
}

// ============================================================================
// Environment Management
// ============================================================================

function loadEnvFile() {
  let currentDir = __dirname;

  while (currentDir !== path.dirname(currentDir)) {
    currentDir = path.dirname(currentDir);
    const envFile = path.join(currentDir, '.env');

    if (fs.existsSync(envFile)) {
      debugLog('Loading env file:', envFile);
      parseEnvFile(envFile);
      break;
    }
    if (fs.existsSync(path.join(currentDir, '.git'))) break;
  }
}

function parseEnvFile(envFile) {
  const content = fs.readFileSync(envFile, 'utf8');

  for (const rawLine of content.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;

    const match = line.match(/^([^=]+)=(.*)$/);
    if (!match) continue;

    const key = match[1].trim();
    let value = match[2].trim();

    // Remove surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }

    process.env[key] ??= value;
  }
}

// ============================================================================
// Transcript Processing
// ============================================================================

function isMetaContent(content) {
  return typeof content === 'string' &&
    META_CONTENT_PREFIXES.some(prefix => content.startsWith(prefix));
}

function extractTextFromContent(content) {
  if (typeof content === 'string') {
    return isMetaContent(content) ? null : content.substring(0, CONFIG.MAX_CONTENT_LENGTH);
  }

  if (Array.isArray(content)) {
    const textItem = content.find(item =>
      item?.type === 'text' && typeof item.text === 'string'
    );
    return textItem?.text?.substring(0, CONFIG.MAX_CONTENT_LENGTH) ?? null;
  }

  return null;
}

function parseTranscriptData(transcriptPath) {
  debugLog('Parsing transcript:', transcriptPath);
  const allLines = readFileLines(transcriptPath);
  if (!allLines) {
    debugLog('No lines read from transcript');
    return { firstRequest: null, assistantResponses: [] };
  }

  // Process only recent lines to avoid memory issues with large files
  const lines = allLines.slice(-CONFIG.MAX_TRANSCRIPT_LINES);
  debugLog('Processing', lines.length, 'recent lines');

  let firstRequest = null;
  const assistantResponses = [];
  let parsedCount = 0;
  let userCount = 0;
  let assistantCount = 0;

  for (const line of lines) {
    const data = safeJsonParse(line);
    if (!data?.message?.role) continue;
    parsedCount++;

    const { role, content } = data.message;
    const text = extractTextFromContent(content);

    // Get first valid user request (within recent lines)
    if (!firstRequest &&
        role === 'user' &&
        data.type === 'user' &&
        !data.isMeta &&
        text?.length >= CONFIG.MIN_TEXT_LENGTH) {
      firstRequest = text;
      userCount++;
      debugLog('Found user request:', text.substring(0, 50) + '...');
    }

    // Collect assistant responses
    if (role === 'assistant' && text?.length >= CONFIG.MIN_RESPONSE_LENGTH) {
      assistantResponses.push(text);
      assistantCount++;
    }
  }

  debugLog('Parsed messages:', parsedCount, '| User:', userCount, '| Assistant:', assistantCount);

  return {
    firstRequest,
    assistantResponses: assistantResponses.slice(-CONFIG.MAX_RESPONSES)
  };
}

function formatContextForSummary({ firstRequest, assistantResponses }) {
  if (!firstRequest && assistantResponses.length === 0) {
    debugLog('No content for summary context');
    return null;
  }

  const parts = [];
  if (firstRequest) {
    parts.push(`【ユーザーのリクエスト】\n${firstRequest}`);
  }
  if (assistantResponses.length > 0) {
    parts.push(`【アシスタントの応答】\n${assistantResponses.join('\n\n')}`);
  }

  const context = parts.join('\n\n');
  debugLog('Context length:', context.length);
  return context.length >= CONFIG.MIN_RESPONSE_LENGTH ? context : null;
}

function createSummarizationPrompt(context) {
  return `以下の情報から、**このセッションで実施した作業**を日本語で40文字以内で要約してください。

## 重要
- ユーザーのリクエストとアシスタントの応答から、このセッション固有の作業内容を特定してください
- 「調査」「確認」などの一般的な表現より、具体的な作業内容（例：「Slack通知の要約生成を修正」）を優先してください

## 出力形式
日本語の要約文のみ（1行、40文字以内）

## 必須事項
- 必ず日本語で出力すること
- このセッションで実際に行った具体的な作業を要約すること
- 技術的な作業内容を含めること（ファイル名、機能名など）

## 禁止事項
- 英語での出力
- 「ユーザーさん」「調査結果」などの前置き
- 「要約：」「作業内容：」などのラベル
- 「〜を確認しました」「〜を調査しました」などの曖昧な表現（具体的な結果を書く）
- 挨拶文や説明文
- 複数行の出力

## 良い例
- Slack通知の要約生成ロジックを修正
- BigQueryクエリのパフォーマンスを最適化
- JWT認証のセキュリティレビューを実施
- PRマージ後のブランチクリーンアップを実行

## 悪い例
- Added new MCP server（英語は禁止）
- コードを調査しました（曖昧すぎる）
- ユーザーさん、調査結果をまとめます：
- 要約：〜を実施しました

## セッション情報
${context.substring(0, CONFIG.MAX_CONTEXT_LENGTH)}

## 日本語の要約（40文字以内）:`;
}

// ============================================================================
// Claude CLI Integration (with MCP tools disabled)
// ============================================================================

// Auth priority: OAuth > Bedrock > API Key (cost optimization)
const AUTH_CHECKS = [
  {
    name: 'oauth',
    detect: () => {
      try {
        const creds = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude', '.credentials.json'), 'utf8'));
        const oauth = creds?.claudeAiOauth;
        return oauth?.accessToken && oauth?.expiresAt > Date.now();
      } catch (e) {
        debugLog('OAuth detection failed:', e.message);
        return false;
      }
    },
    removeKeys: ['ANTHROPIC_API_KEY', 'CLAUDE_CODE_USE_BEDROCK'],
  },
  {
    name: 'bedrock',
    detect: () => !!process.env.CLAUDE_CODE_USE_BEDROCK,
    removeKeys: ['ANTHROPIC_API_KEY'],
  },
  {
    name: 'apikey',
    detect: () => process.env.ANTHROPIC_API_KEY?.startsWith('sk-ant-'),
    removeKeys: ['CLAUDE_CODE_USE_BEDROCK'],
  },
];

const SESSION_VARS = ['CLAUDECODE', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_ENTRYPOINT'];

function buildCleanEnv(skip = []) {
  const env = { ...process.env };
  for (const key of SESSION_VARS) delete env[key];

  const auth = AUTH_CHECKS.find(a => !skip.includes(a.name) && a.detect()) || { name: 'none', removeKeys: [] };
  for (const key of auth.removeKeys) delete env[key];
  if (auth.name === 'none' && env.ANTHROPIC_API_KEY && !env.ANTHROPIC_API_KEY.startsWith('sk-ant-')) {
    delete env.ANTHROPIC_API_KEY;
  }
  debugLog(`Auth: ${auth.name} | removed: [${[...SESSION_VARS, ...auth.removeKeys].join(', ')}]`);
  return { env, authName: auth.name };
}

function spawnClaudeCLI(prompt, env) {
  return new Promise((resolve) => {
    const args = [
      '--print', '--no-session-persistence',
      '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
      '--tools', '', '--model', 'haiku', '-p', prompt
    ];

    const claude = spawn('claude', args, { stdio: ['ignore', 'pipe', 'pipe'], env });
    if (claude.pid) debugLog('Claude CLI spawned with PID:', claude.pid);

    let stdout = '', stderr = '', resolved = false;
    const safeResolve = (value, reason) => {
      if (resolved) return;
      resolved = true;
      clearTimeout(timeoutId);
      debugLog('CLI resolved:', reason, '| Output length:', value?.length || 0);
      resolve(value);
    };

    claude.stdout.on('data', (d) => { stdout += d; });
    claude.stderr.on('data', (d) => { stderr += d; });

    const timeoutId = setTimeout(() => {
      debugLog('Claude CLI timeout after', CONFIG.CLI_TIMEOUT_MS, 'ms');
      claude.kill('SIGTERM');
      safeResolve(null, 'timeout');
    }, CONFIG.CLI_TIMEOUT_MS);

    claude.on('close', (code, signal) => {
      if (signal) { safeResolve(null, 'signal:' + signal); return; }
      debugLog('Claude CLI exit code:', code);
      debugLog('CLI stdout:', stdout.substring(0, 200));
      debugLog('CLI stderr:', stderr.substring(0, 200));
      (code === 0 && stdout.trim()) ? safeResolve(stdout.trim(), 'success')
        : safeResolve(null, 'error:' + code);
    });

    claude.on('error', (err) => {
      debugLog('Claude CLI spawn error:', err.message);
      safeResolve(null, 'spawn-error');
    });
  });
}

async function callClaudeCLI(prompt) {
  debugLog('=== Calling Claude CLI for summary generation ===');
  const skip = [];
  // Try each auth method in priority order, falling back on failure
  for (let i = 0; i <= AUTH_CHECKS.length; i++) {
    const { env, authName } = buildCleanEnv(skip);
    const result = await spawnClaudeCLI(prompt, env);
    if (result) return result;
    if (authName === 'none') break;
    debugLog(`Auth ${authName} failed, trying next`);
    skip.push(authName);
  }
  return null;
}

function cleanSummaryResponse(summary) {
  if (!summary) {
    debugLog('No summary to clean');
    return null;
  }

  debugLog('Raw summary:', summary);

  const cleaned = summary
    .replace(/^(要約[:：]?\s*|作業内容[:：]?\s*)/i, '')
    .replace(/^[\s\n]+/, '')
    .split('\n')[0]
    .trim();

  debugLog('Cleaned summary:', cleaned);
  return cleaned.length >= CONFIG.MIN_TEXT_LENGTH ? cleaned : null;
}

async function generateSummary(transcriptPath) {
  debugLog('=== Starting summary generation ===');

  const transcriptData = parseTranscriptData(transcriptPath);
  debugLog('Transcript data:', {
    hasFirstRequest: !!transcriptData.firstRequest,
    responseCount: transcriptData.assistantResponses.length
  });

  const context = formatContextForSummary(transcriptData);
  if (!context) {
    debugLog('No context available for summary');
    return null;
  }

  const prompt = createSummarizationPrompt(context);
  debugLog('Prompt length:', prompt.length);

  const output = await callClaudeCLI(prompt);
  const result = cleanSummaryResponse(output);

  debugLog('=== Summary generation complete:', result ? 'SUCCESS' : 'FAILED', '===');
  return result;
}

// ============================================================================
// Slack API Integration
// ============================================================================

function makeSlackApiRequest(apiPath, data, token) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(data);
    debugLog('Slack API request:', apiPath, '| Payload size:', payload.length);

    const req = https.request({
      hostname: 'slack.com',
      port: 443,
      path: apiPath,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': Buffer.byteLength(payload)
      }
    }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        const result = safeJsonParse(body);
        debugLog('Slack API response:', apiPath, '| ok:', result?.ok, '| error:', result?.error || 'none');
        if (result?.ok) {
          resolve(result);
        } else {
          debugLog('Slack API error details:', result);
          resolve(null);
        }
      });
    });

    req.on('error', (error) => {
      debugLog('Slack API network error:', error.message);
      reject(error);
    });
    req.write(payload);
    req.end();
  });
}

const sendSlackMessage = (channelId, token, text) =>
  makeSlackApiRequest('/api/chat.postMessage', { channel: channelId, text }, token);

const deleteSlackMessage = (channelId, token, ts) =>
  makeSlackApiRequest('/api/chat.delete', { channel: channelId, ts }, token);

// ============================================================================
// Git Integration
// ============================================================================

function getRepositoryName() {
  return new Promise((resolve) => {
    const git = spawn('git', ['rev-parse', '--show-toplevel']);
    let output = '';

    git.stdout.on('data', (data) => { output += data; });
    git.on('close', (code) => {
      const repoName = code === 0 && output.trim()
        ? path.basename(output.trim())
        : process.env.GIT_REPO || CONFIG.FALLBACK_REPO_NAME;
      debugLog('Repository name:', repoName);
      resolve(repoName);
    });
    git.on('error', () => resolve(process.env.GIT_REPO || CONFIG.FALLBACK_REPO_NAME));
  });
}

// ============================================================================
// Hook Input Processing
// ============================================================================

async function readHookInput() {
  debugLog('=== Reading hook input from stdin ===');
  if (process.stdin.isTTY) {
    debugLog('stdin is TTY, no hook input available');
    return { transcriptPath: null, stopHookActive: false };
  }

  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  debugLog('Raw hook input length:', input.length, '| Preview:', input.substring(0, 200));
  const parsed = safeJsonParse(input);
  if (parsed) {
    debugLog('Parsed hook input:', JSON.stringify(parsed, null, 2));
  } else {
    debugLog('Failed to parse hook input');
  }

  const result = {
    transcriptPath: parsed?.transcript_path ?? null,
    stopHookActive: parsed?.stop_hook_active === true
  };
  debugLog('Extracted hook input values:', result);

  return result;
}

// ============================================================================
// Message Formatting
// ============================================================================

function formatNotificationMessage(repoName, summary, includeMention = false) {
  const userMention = process.env.SLACK_USER_MENTION;
  const content = summary || CONFIG.NO_SUMMARY_MESSAGE;

  // Add debug info to message if in debug mode
  const debugInfo = isDebugMode()
    ? ` [RUN:${RUN_ID}|PID:${process.pid}]`
    : '';

  const message = `[${repoName}] ${content}${debugInfo}`;

  return includeMention && userMention ? `${userMention} ${message}` : message;
}

// ============================================================================
// Main Flow
// ============================================================================

async function main() {
  debugLog('=== Slack notify script started ===');
  debugLog('RUN_ID:', RUN_ID);
  debugLog('PID:', process.pid);
  debugLog('CWD:', process.cwd());
  debugLog('ENV - CLAUDE_SESSION_ID:', process.env.CLAUDE_SESSION_ID || 'not set');
  debugLog('ENV - DEBUG_SLACK_NOTIFY:', process.env.DEBUG_SLACK_NOTIFY || 'not set');

  loadEnvFile();

  const { SLACK_CHANNEL_ID: channelId, SLACK_BOT_TOKEN: token } = process.env;
  if (!channelId || !token) {
    debugLog('Missing required env vars - SLACK_CHANNEL_ID:', !!channelId, '| SLACK_BOT_TOKEN:', !!token);
    process.exit(0);
  }

  // Prevent duplicate execution
  if (!acquireLock()) {
    debugLog('EXITING: Could not acquire lock');
    process.exit(0);
  }

  try {
    const [hookInput, repoName] = await Promise.all([
      readHookInput(),
      getRepositoryName()
    ]);

    const { transcriptPath, stopHookActive } = hookInput;

    debugLog('Hook input received:', {
      transcriptPath,
      stopHookActive,
      hasTranscriptPath: !!transcriptPath
    });

    // Skip if this is a recursive hook call (e.g., from internal Claude CLI for summary generation)
    if (stopHookActive) {
      debugLog('EXITING: stop_hook_active is true (preventing recursive execution)');
      releaseLock();
      process.exit(0);
    }

    debugLog('Transcript path:', transcriptPath);
    debugLog('Repository name:', repoName);

    const summaryStartTime = Date.now();
    const summary = transcriptPath ? await generateSummary(transcriptPath) : null;
    const summaryDuration = Date.now() - summaryStartTime;
    debugLog('Final summary:', summary, '| Generation time:', summaryDuration, 'ms');

    // Send message with mention to trigger notification
    const mentionMessage = formatNotificationMessage(repoName, summary, true);
    debugLog('=== Sending mention message ===', mentionMessage.substring(0, 100));
    const mentionSendTime = Date.now();
    const mentionResult = await sendSlackMessage(channelId, token, mentionMessage);
    if (!mentionResult) {
      debugLog('FAILED to send mention message');
      process.exit(1);
    }
    debugLog('Mention message sent | ts:', mentionResult.ts, '| Duration:', Date.now() - mentionSendTime, 'ms');

    // Wait a bit before sending clean message and deleting
    debugLog('Waiting', CONFIG.DELETE_DELAY_MS, 'ms before cleanup');
    await sleep(CONFIG.DELETE_DELAY_MS);

    // Send clean message without mention
    const cleanMessage = formatNotificationMessage(repoName, summary, false);
    debugLog('=== Sending clean message ===', cleanMessage.substring(0, 100));
    const cleanSendTime = Date.now();
    const cleanResult = await sendSlackMessage(channelId, token, cleanMessage);
    if (!cleanResult) {
      debugLog('FAILED to send clean message');
    } else {
      debugLog('Clean message sent | ts:', cleanResult.ts, '| Duration:', Date.now() - cleanSendTime, 'ms');
    }

    // Delete the mention message
    if (mentionResult.ts) {
      debugLog('=== Deleting mention message | ts:', mentionResult.ts, '===');
      const deleteStartTime = Date.now();
      const deleteResult = await deleteSlackMessage(channelId, token, mentionResult.ts);
      if (deleteResult) {
        debugLog('Mention message deleted successfully | Duration:', Date.now() - deleteStartTime, 'ms');
      } else {
        debugLog('FAILED to delete mention message');
      }
    }

    debugLog('=== Slack notify script completed successfully | Total runtime:', Date.now() - summaryStartTime + summaryDuration, 'ms ===');
  } finally {
    releaseLock();
  }
}

main().catch((error) => {
  debugLog('Fatal error:', error.message, error.stack);
  releaseLock();
  process.exit(1);
});
