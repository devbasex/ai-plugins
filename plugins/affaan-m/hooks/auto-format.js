#!/usr/bin/env node

/**
 * auto-format.js
 * コード編集後に自動フォーマット（Prettier/ESLint）を実行
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
        additionalContext: "✨ [affaan-m] コードフォーマットのヒント: Prettierがインストールされていれば `npx prettier --write .` で自動フォーマットできます。"
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
