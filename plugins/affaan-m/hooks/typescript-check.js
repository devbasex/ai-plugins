#!/usr/bin/env node

/**
 * typescript-check.js
 * TypeScript型チェックを自動実行
 *
 * PostToolUse Hook
 *
 * 注: 現在、編集されたファイル情報を取得するClaude Code APIがないため、
 * このフックは情報メッセージのみを表示します。
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function main() {
  try {
    // tsconfigが存在するか確認
    const tsconfigPath = path.join(process.cwd(), 'tsconfig.json');
    const hasTsConfig = fs.existsSync(tsconfigPath);

    if (hasTsConfig) {
      try {
        // Check if TypeScript is available
        try {
          execSync('npx tsc --version', { stdio: 'ignore' });
        } catch (versionError) {
          // TypeScript未インストール
          const output = {
            hookSpecificOutput: {
              hookEventName: "PostToolUse",
              additionalContext: "💡 [affaan-m] TypeScriptのヒント: TypeScriptがインストールされていません。`npm install typescript` でインストールしてください。"
            }
          };
          console.log(JSON.stringify(output));
          process.exit(0);
        }

        // TypeScript型チェックを実行
        execSync('npx tsc --noEmit', { stdio: 'ignore' });

        const output = {
          hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: "✅ [affaan-m] TypeScript型チェック: 問題なし"
          }
        };
        console.log(JSON.stringify(output));
      } catch (error) {
        // 型エラーがある場合
        const output = {
          hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: "⚠️ [affaan-m] TypeScript型エラーが検出されました。`npx tsc --noEmit` で詳細を確認してください。"
          }
        };
        console.log(JSON.stringify(output));
      }
    } else {
      const output = {
        hookSpecificOutput: {
          hookEventName: "PostToolUse",
          additionalContext: "💡 [affaan-m] TypeScriptのヒント: tsconfig.jsonが見つかりません。"
        }
      };
      console.log(JSON.stringify(output));
    }

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
