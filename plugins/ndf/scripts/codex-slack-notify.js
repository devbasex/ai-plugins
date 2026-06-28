#!/usr/bin/env node
/**
 * Slack notification script for Codex Stop hooks.
 *
 * Opt-in only: set NDF_CODEX_SLACK_NOTIFY=true plus the same Slack variables
 * used by the Claude Code hook: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, and
 * optional SLACK_USER_MENTION. The script avoids model calls and summarizes
 * from Codex's local session JSONL when available.
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const { spawnSync } = require('child_process');
const os = require('os');
const crypto = require('crypto');

const CONFIG = {
  MAX_SESSION_FILES: 80,
  MAX_LINES: 240,
  MAX_TEXT: 180,
  LOCK_TIMEOUT_MS: 30000,
  COOLDOWN_MS: 5000,
  DELETE_DELAY_MS: 500,
  FALLBACK_SUMMARY: 'Codexの作業が完了しました',
  LOG_DIR: path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'log'),
};

const RUN_ID = crypto.randomBytes(4).toString('hex');

const isEnabled = () => /^(1|true|yes|on)$/i.test(process.env.NDF_CODEX_SLACK_NOTIFY || '');
const isDebug = () => /^(1|true|yes|on)$/i.test(process.env.DEBUG_CODEX_SLACK_NOTIFY || '');

function log(message, ...args) {
  if (!isDebug()) return;
  const line = `[${new Date().toISOString()}] [codex-slack:${RUN_ID}] ${message} ${args.map(String).join(' ')}\n`;
  process.stderr.write(line);
  try {
    fs.mkdirSync(CONFIG.LOG_DIR, { recursive: true });
    fs.appendFileSync(path.join(CONFIG.LOG_DIR, 'ndf-codex-slack-notify.log'), line);
  } catch (_) {
    // Debug logging must never break hook execution.
  }
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch (_) {
    return null;
  }
}

async function readStdinJson() {
  if (process.stdin.isTTY) return {};
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  log('stdin bytes:', input.length);
  return safeJsonParse(input) || {};
}

function loadEnvFile() {
  let current = process.cwd();
  while (current && current !== path.dirname(current)) {
    const envFile = path.join(current, '.env');
    if (fs.existsSync(envFile)) {
      for (const rawLine of fs.readFileSync(envFile, 'utf8').split('\n')) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const match = line.match(/^([^=]+)=(.*)$/);
        if (!match) continue;
        const key = match[1].trim();
        let value = match[2].trim();
        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        process.env[key] ??= value;
      }
      return;
    }
    if (fs.existsSync(path.join(current, '.git'))) return;
    current = path.dirname(current);
  }
}

function lockPath() {
  const id = `${process.cwd()}:${process.env.CODEX_SESSION_ID || ''}`;
  const hash = crypto.createHash('sha256').update(id).digest('hex').slice(0, 12);
  return path.join(os.tmpdir(), `ndf-codex-slack-${hash}.lock`);
}

function acquireLock() {
  const file = lockPath();
  const now = Date.now();
  const existing = fs.existsSync(file) ? safeJsonParse(fs.readFileSync(file, 'utf8')) : null;
  if (existing?.completedAt && now - existing.completedAt < CONFIG.COOLDOWN_MS) return false;
  if (existing?.timestamp && !existing.completedAt && now - existing.timestamp < CONFIG.LOCK_TIMEOUT_MS) return false;
  fs.writeFileSync(file, JSON.stringify({ pid: process.pid, timestamp: now, completedAt: null }));
  return true;
}

function releaseLock() {
  const file = lockPath();
  const data = fs.existsSync(file) ? safeJsonParse(fs.readFileSync(file, 'utf8')) || {} : {};
  data.completedAt = Date.now();
  fs.writeFileSync(file, JSON.stringify(data));
}

function gitValue(args, fallback = '') {
  const result = spawnSync('git', args, { cwd: process.cwd(), encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : fallback;
}

function repoInfo() {
  const root = gitValue(['rev-parse', '--show-toplevel'], process.cwd());
  const branch = gitValue(['branch', '--show-current'], '');
  return {
    name: path.basename(root || process.cwd()),
    root,
    branch,
  };
}

function codexHome() {
  return process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
}

function findSessionFiles() {
  const base = path.join(codexHome(), 'sessions');
  if (!fs.existsSync(base)) return [];
  const files = [];
  const stack = [base];
  while (stack.length) {
    const dir = stack.pop();
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(p);
      else if (entry.isFile() && entry.name.endsWith('.jsonl')) {
        files.push({ path: p, mtimeMs: fs.statSync(p).mtimeMs });
      }
    }
  }
  return files.sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, CONFIG.MAX_SESSION_FILES);
}

function textFromContent(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((item) => item?.text || item?.content || '').filter(Boolean).join('\n');
  }
  if (content && typeof content === 'object') return content.text || content.message || '';
  return '';
}

function textFromItem(item) {
  const payload = item?.payload || {};
  if (payload.type === 'message') {
    return textFromContent(payload.content);
  }
  if (payload.type === 'agent_message') return payload.message || '';
  if (item.type === 'event_msg' && payload.message) return payload.message;
  return '';
}

function summarizeText(text) {
  const oneLine = String(text || '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!oneLine) return null;
  return oneLine.length > CONFIG.MAX_TEXT ? `${oneLine.slice(0, CONFIG.MAX_TEXT - 1)}…` : oneLine;
}

function readSessionSummary(hookInput) {
  const explicitPath = hookInput.transcript_path || hookInput.session_path || hookInput.session_file;
  const candidates = explicitPath ? [{ path: explicitPath }] : findSessionFiles();
  const cwd = hookInput.cwd || process.cwd();
  const sessionId = hookInput.session_id || hookInput.thread_id || process.env.CODEX_SESSION_ID || '';
  const lastAssistantSummary = summarizeText(textFromContent(hookInput.last_assistant_message));

  for (const candidate of candidates) {
    if (!candidate.path || !fs.existsSync(candidate.path)) continue;
    const lines = fs.readFileSync(candidate.path, 'utf8').trim().split('\n').slice(-CONFIG.MAX_LINES);
    const parsed = lines.map(safeJsonParse).filter(Boolean);
    const meta = parsed.find((item) => item.type === 'session_meta')?.payload || {};
    if (!explicitPath && meta.cwd && meta.cwd !== cwd) continue;
    if (!explicitPath && sessionId && meta.session_id && meta.session_id !== sessionId) continue;

    const final = [...parsed].reverse().find((item) => {
      const payload = item.payload || {};
      return payload.phase === 'final_answer' || payload.type === 'agent_message';
    });
    const user = [...parsed].reverse().find((item) => {
      const payload = item.payload || {};
      return payload.type === 'message' && payload.role === 'user';
    });
    const tokenEvent = [...parsed].reverse().find((item) => item.type === 'event_msg' && item.payload?.type === 'token_count');

    return {
      sessionId: meta.session_id || sessionId,
      summary: lastAssistantSummary || summarizeText(textFromItem(final)) || summarizeText(textFromItem(user)),
      model: meta.model || meta.model_slug || '',
      tokenInfo: tokenEvent?.payload?.info || null,
      file: candidate.path,
    };
  }

  return { sessionId, summary: lastAssistantSummary, model: '', tokenInfo: null, file: null };
}

function formatTokenInfo(info) {
  const usage = info?.total_token_usage || info?.last_token_usage;
  const window = info?.model_context_window;
  if (!usage?.total_tokens || !window) return '';
  const pct = Math.round((usage.total_tokens / window) * 100);
  return `tokens: ${usage.total_tokens}/${window} (${pct}%)`;
}

function formatMessage(repo, session, includeMention = false) {
  const mention = includeMention && process.env.SLACK_USER_MENTION ? `${process.env.SLACK_USER_MENTION} ` : '';
  const parts = [
    `${mention}[${repo.name}] Codex: ${session.summary || CONFIG.FALLBACK_SUMMARY}`,
    repo.branch ? `branch: ${repo.branch}` : '',
    session.model ? `model: ${session.model}` : '',
    formatTokenInfo(session.tokenInfo),
    session.sessionId ? `session: ${session.sessionId}` : '',
    `cwd: ${process.cwd()}`,
  ].filter(Boolean);
  return parts.join('\n');
}

function slackApiRequest(apiPath, data) {
  const token = process.env.SLACK_BOT_TOKEN;
  if (!token) return Promise.resolve(null);

  return new Promise((resolve) => {
    const body = JSON.stringify(data);
    const req = https.request({
      hostname: 'slack.com',
      port: 443,
      path: apiPath,
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': Buffer.byteLength(body),
      },
    }, (res) => {
      let response = '';
      res.on('data', (chunk) => { response += chunk; });
      res.on('end', () => {
        const json = safeJsonParse(response);
        log('slack api:', apiPath, 'ok:', json?.ok, 'error:', json?.error || '');
        resolve(json?.ok === true ? json : null);
      });
    });
    req.on('error', (error) => {
      log('slack error:', error.message);
      resolve(null);
    });
    req.write(body);
    req.end();
  });
}

function postSlack(text) {
  const channel = process.env.SLACK_CHANNEL_ID;
  if (!channel) return Promise.resolve(null);
  return slackApiRequest('/api/chat.postMessage', { channel, text });
}

function deleteSlack(ts) {
  const channel = process.env.SLACK_CHANNEL_ID;
  if (!channel || !ts) return Promise.resolve(null);
  return slackApiRequest('/api/chat.delete', { channel, ts });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  loadEnvFile();
  if (!isEnabled()) {
    log('disabled');
    return;
  }
  if (!process.env.SLACK_BOT_TOKEN || !process.env.SLACK_CHANNEL_ID) {
    log('missing slack env');
    return;
  }
  if (!acquireLock()) {
    log('lock rejected');
    return;
  }
  try {
    const hookInput = await readStdinJson();
    const repo = repoInfo();
    const session = readSessionSummary(hookInput);
    if (!process.env.SLACK_USER_MENTION) {
      await postSlack(formatMessage(repo, session, false));
      return;
    }

    const mentionResult = await postSlack(formatMessage(repo, session, true));
    if (!mentionResult) {
      log('failed to send mention message');
      return;
    }

    await sleep(CONFIG.DELETE_DELAY_MS);

    const cleanResult = await postSlack(formatMessage(repo, session, false));
    if (!cleanResult) log('failed to send clean message');

    const deleteResult = await deleteSlack(mentionResult.ts);
    if (!deleteResult) log('failed to delete mention message');
  } finally {
    releaseLock();
  }
}

main().catch((error) => {
  log('fatal:', error.stack || error.message);
  releaseLock();
  process.exit(0);
});
