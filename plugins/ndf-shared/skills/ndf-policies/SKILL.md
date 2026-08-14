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

## v7.0.0 で移動した Skill（v8.0.0 で削除）

ブラウザ自動テストの 4 Skill を **`playwright-kit` プラグイン**へ分離した。**Skill 名は
変わらない**ため、`/playwright-` まで打てば従来どおり候補に出る。

| 旧コマンド | 移行先 |
| --- | --- |
| `/ndf:playwright-planning` | `/playwright-kit:playwright-planning` |
| `/ndf:playwright-authoring` | `/playwright-kit:playwright-authoring` |
| `/ndf:playwright-evidence` | `/playwright-kit:playwright-evidence` |
| `/ndf:playwright-kit-ops` | `/playwright-kit:playwright-kit-ops` |

利用するにはプラグインを別途インストールする。

```bash
# Claude Code
/plugin install playwright-kit@ai-plugins
# Codex
codex plugin add playwright-kit@ai-plugins
# Kiro CLI
bash plugins/playwright-kit-kiro/install.sh
```

分離したのは、Skill の `name` と `description` が起動時の一覧として常時注入され、その予算が
プラグイン横断で共有されるためである。ブラウザ自動テストは使う場面が限られる一方で MCP
ツールを多用するため frontmatter が大きく（4 個で約 2,400 文字）、全利用者へ常時注入する
取り分に見合わなかった。

v6.0.0 の対応表（`/ndf:review` → `/ndf:pr-review`）は、予告どおり本バージョンで削除した。
v6.0.0 以前から移行する場合は v6.1.0 の `ndf-policies` を参照する。
