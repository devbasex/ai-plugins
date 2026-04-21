#!/usr/bin/env node

/**
 * context-monitor.js
 * コンテキスト使用率を監視し、閾値を超えたら警告を表示
 *
 * PreToolUse Hook
 */

function main() {
  try {
    // 注: 現在、Claude Code APIからコンテキスト使用率を直接取得する方法がないため
    // このフックは警告メッセージのみを表示します

    const output = {
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        additionalContext: "💡 [affaan-m] コンテキスト管理のヒント: 長時間のセッションでコンテキストが増加したら /compact を実行してください。"
      }
    };

    console.log(JSON.stringify(output));
    process.exit(0);
  } catch (error) {
    console.error(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        error: error.message
      }
    }));
    process.exit(1);
  }
}

main();
