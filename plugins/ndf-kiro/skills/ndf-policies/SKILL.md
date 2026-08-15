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

## v8.0.0 で改名した Skill（v9.0.0 で削除）

構造改善の Skill を **`/ndf:refactoring`** へ改名し、分岐・反復・定数の表現を決める観点を
統合した。引数と手順は変わらない。

| 旧コマンド | 移行先 |
| --- | --- |
| `/ndf:safe-refactoring` | `/ndf:refactoring` |

`safe-` を外したのは、`/refactoring` で一意に決まり、入力が短くなるためである。統合した観点は
`references/data-representation.md` にあり、スメル一覧からも参照される。

v7.0.0 の対応表（playwright 系 4 Skill の `playwright-kit` プラグインへの分離）は、予告どおり
本バージョンで削除した。v7.0.0 以前から移行する場合は v7.1.0 の `ndf-policies` を参照する。
