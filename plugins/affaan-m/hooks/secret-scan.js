#!/usr/bin/env node

/**
 * secret-scan.js
 * コミット前にシークレット混入をチェック
 *
 * PreCommit Hook
 */

const { execSync } = require('child_process');
const fs = require('fs');

const SECRET_PATTERNS = {
  'AWS_ACCESS_KEY_ID': /AKIA[0-9A-Z]{16}/,
  'AWS_SECRET_ACCESS_KEY': /(AWS_SECRET_ACCESS_KEY|aws_secret_access_key)\s*[:=]\s*['"]?[A-Za-z0-9/+=]{40}['"]?/i,
  'GITHUB_TOKEN': /ghp_[A-Za-z0-9]{36}/,
  'SLACK_TOKEN': /xox[baprs]-[0-9a-zA-Z-]+/,
  'PRIVATE_KEY': /-----BEGIN (RSA |DSA |EC )?PRIVATE KEY-----/,
};

function scanFile(filePath) {
  try {
    // Skip binary files and common non-text extensions
    const binaryExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.pdf', '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.woff', '.woff2', '.ttf', '.eot'];
    if (binaryExtensions.some(ext => filePath.toLowerCase().endsWith(ext))) {
      return [];
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');
    const detected = [];

    Object.entries(SECRET_PATTERNS).forEach(([name, regex]) => {
      lines.forEach((line, index) => {
        if (regex.test(line)) {
          detected.push({
            file: filePath,
            line: index + 1,
            pattern: name,
            snippet: line.trim().substring(0, 80)
          });
        }
      });
    });

    return detected;
  } catch (error) {
    // エンコーディングエラーやファイル読み込みエラーは無視
    return [];
  }
}

function main() {
  try {
    // git diffでステージされたファイルを取得
    let stagedFiles = [];
    try {
      const output = execSync('git diff --cached --name-only --diff-filter=ACM', {
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'ignore']
      });
      stagedFiles = output.trim().split('\n').filter(Boolean);
    } catch (error) {
      // gitリポジトリでない、または変更がない場合
    }

    if (stagedFiles.length === 0) {
      const output = {
        hookSpecificOutput: {
          hookEventName: "PreCommit",
          additionalContext: "🔒 [affaan-m] シークレットスキャン: チェック対象ファイルなし"
        }
      };
      console.log(JSON.stringify(output));
      process.exit(0);
    }

    // ステージされたファイルをスキャン
    let allDetected = [];
    for (const file of stagedFiles) {
      if (file.includes('node_modules/') || file.includes('.git/')) continue;
      const detected = scanFile(file);
      allDetected = allDetected.concat(detected);
    }

    if (allDetected.length > 0) {
      let detailsMessage = '';
      allDetected.forEach(item => {
        detailsMessage += `\n  ファイル: ${item.file}:${item.line}`;
        detailsMessage += `\n  パターン: ${item.pattern}`;
        detailsMessage += `\n  内容: ${item.snippet}`;
      });

      const output = {
        hookSpecificOutput: {
          hookEventName: "PreCommit",
          additionalContext: `🚨 [affaan-m] シークレット混入を検出しました:${detailsMessage}\n\n❌ コミットをブロックしました。シークレットを削除してから再度コミットしてください。`,
          error: "シークレット検出によりコミットをブロックしました"
        }
      };
      console.log(JSON.stringify(output));
      process.exit(1);
    } else {
      const output = {
        hookSpecificOutput: {
          hookEventName: "PreCommit",
          additionalContext: `✅ [affaan-m] シークレットスキャン: 問題なし（${stagedFiles.length}個のファイルをスキャン）`
        }
      };
      console.log(JSON.stringify(output));
      process.exit(0);
    }
  } catch (error) {
    console.error(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreCommit",
        error: error.message
      }
    }));
    process.exit(1);
  }
}

main();
