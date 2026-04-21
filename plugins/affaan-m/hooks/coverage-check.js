#!/usr/bin/env node

/**
 * coverage-check.js
 * テストカバレッジを検証（80%以上推奨）
 *
 * PreCommit Hook
 *
 * 注: 現在、カバレッジ情報を取得するClaude Code APIがないため、
 * このフックは情報メッセージのみを表示します。
 */

function main() {
  try {
    const output = {
      hookSpecificOutput: {
        hookEventName: "PreCommit",
        additionalContext: "📊 [affaan-m] テストカバレッジのヒント: `npm test -- --coverage` でカバレッジを確認できます（推奨: 80%以上）。"
      }
    };

    console.log(JSON.stringify(output));
    process.exit(0);
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
