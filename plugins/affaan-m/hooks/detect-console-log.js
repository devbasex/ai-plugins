#!/usr/bin/env node

/**
 * detect-console-log.js
 * console.log や debugger の使用を検出して警告
 *
 * PostToolUse Hook
 *
 * 注: 現在、編集されたファイル情報を取得するClaude Code APIがないため、
 * このフックは情報メッセージのみを表示します。
 */

function main() {
  try {
    const output = {
      hookSpecificOutput: {
        hookEventName: "PostToolUse",
        additionalContext: "🔍 [affaan-m] デバッグログのヒント: console.log や debugger は本番コードから削除してください。"
      }
    };

    console.log(JSON.stringify(output));
    process.exit(0);
  } catch (error) {
    console.error(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PostToolUse",
        error: error.message
      }
    }));
    process.exit(1);
  }
}

main();
