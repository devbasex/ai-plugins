---
name: ndf-policies
description: "Apply core NDF project policies, including the branch strategy for applying the same fix to environment branches (qa/staging/release) without contaminating feature branches."
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
