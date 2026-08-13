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

## v6.0.0 で変わったコマンド名（v7.0.0 で削除）

| 旧コマンド | 移行先 |
| --- | --- |
| `/ndf:review` | `/ndf:pr-review` |

引数と挙動は変わらない。改名したのは、`review` が `code-review`（Claude Code 組み込み）
`security-review` `cross-review` の末尾要素で、`/` メニューで `review` と打つと候補に
埋もれるため（[#83](https://github.com/devbasex/ai-plugins/issues/83)）。`/pr-rev` まで
打てば一意に決まる。

`pr-review` は Claude Code では自然文で起動しない。組み込みの `code-review` が同じ用途を
持ち、そちらが常に選ばれる。**`/ndf:pr-review` で明示的に起動する。**

v5.0.0 で 49 個を 29 個へ整理したときの対応表は、予告どおり本バージョンで削除した。
v4.20.1 以前から移行する場合は v5.0.0 の `ndf-policies` を参照する。
