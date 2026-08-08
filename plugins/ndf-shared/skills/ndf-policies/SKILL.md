---
name: ndf-policies
description: "Core NDF project policies. 知識として参照するだけで、手順として実行しない。判断に迷ったときの基準として使う: ブランチ戦略、環境ブランチ (qa/staging/release) へ同じ修正を適用する原則、feature ブランチを汚さない運用、PR 運用ルール。"
user-invocable: false
---

# NDFポリシー

このスキルはNDFプラグインの基本ポリシーを定義します。

## ブランチ運用の原則

環境ブランチ（`qa/*`, `staging/*`, `release/*`）へ同じ修正を適用する場面全般に適用する。実行手順は `/ndf:cherry-pick-pr` にある。

1. **修正は feature ブランチに先に commit し、cherry-pick で環境ブランチへ届ける。** 短命ブランチに先に commit して feature へ再実装すると、二重作業と不整合の原因になる
2. **環境ブランチを feature ブランチに merge しない。** conflict 解消目的でも禁止。`feature → main` の PR へ環境固有コードが混入する（ブランチ汚染）
3. **短命ブランチを push する前に `origin/main` を取り込む。** CI に最新 main 必須の Workflow があるため
4. **マージ済みブランチには push しない。** 既存 PR の状態を確認し、マージ済みなら新ブランチ + 新 PR を作る（サフィックス `-v2`, `-v3`）
5. **revert を連鎖させない。** 最終的なあるべき状態を直接コミットする方が履歴上の意図が明確になり、後の cherry-pick も簡単になる

## v5.0.0 で変わったコマンド名（v6.0.0 で削除）

Skill の棚卸で 49 個を 29 個へ整理した。旧コマンドは存在しない。次のメジャー
バージョンでこの節ごと削除する。

| 旧コマンド | 移行先 |
| --- | --- |
| `/ndf:review-branch` | `/ndf:review --branch` |
| `/ndf:review-pr-comments` | `/ndf:fix --classify-only` |
| `/ndf:resolve-pr-comments` | `/ndf:fix` |
| `/ndf:clean` | `/ndf:merged` |
| `/ndf:sync-main` | `/ndf:merged` |
| `/ndf:branch-fix-strategy` | `/ndf:cherry-pick-pr`（原則は本 Skill の「ブランチ運用の原則」） |
| `/ndf:codex` `/ndf:gemini` | `/ndf:external-ai` |
| `/ndf:playwright-test-planning` `/ndf:playwright-scenario-test` | `/ndf:playwright-planning` |
| `/ndf:playwright-script-creation` `/ndf:playwright-execution` `/ndf:browser-test` `/ndf:playwright-browser-connect` | `/ndf:playwright-authoring` |
| `/ndf:playwright-report` `/ndf:playwright-evidence-drive` | `/ndf:playwright-evidence` |

移行先を用意せず削除したもの（いずれも起動実績がなく、現在のモデルの標準能力か
汎用コマンドで足りる）:
`/ndf:git-gh-operations` `/ndf:python-execution` `/ndf:data-analyst-export`
`/ndf:data-analyst-sql-optimization` `/ndf:deepwiki-transfer` `/ndf:google-chat`
`/ndf:knowledge-reorg` `/ndf:mcp-builder`

`data-analyst-export` と `data-analyst-sql-optimization` の内容は `data-analyst`
エージェントの定義へ移した。

そのほかの非互換な変更:

- `merged` / `pr` / `review` / `pr-tests` が自然文の依頼でも起動するようになった。
  取り消しの難しい手順の前には確認を取る
- Kiro はエージェント名が `default` → `ndf` に変わった。`install.sh` を再実行する
- Codex では `deploy` / `cherry-pick-pr` が暗黙起動の一覧から外れる。プラグイン配布の
  Skill は抑止すると `$<skill 名>` も効かないため、SKILL.md のパスを示して読ませる
  （`plugins/ndf-codex/README.md`）

