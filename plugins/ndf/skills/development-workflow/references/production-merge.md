# 自動反映の本番チャネルへのマージ（承認ゲート 2）

`SKILL.md` の「本番系へ届く操作」のうち、Pull Request のマージが承認ゲート 2 に当たる場面の細部。条件そのもの
（宛先が自動反映の本番チャネルのときだけ当たる）は `SKILL.md` にある。

## 例: project-trygroup-prd の 2 本のマージ

宣言が `.ndf/worktree.json` の `base_branch: develop`・`production_branch: main` と、`.ndf/project.json` の
`delivery` に `develop` へのマージで stg・`main` へのマージで prd へ自動で反映する 2 行を持つとき:

1. 実装の Pull Request（`develop` 宛て）は、`develop` が本番チャネルでないため止まらずにマージする
2. 昇格の Pull Request（`develop` → `main`）は、`main` が本番チャネルで `delivery` に `kind: auto`・`branch: main` の行が
   あるため、`merged-steps.py` が終了コード 10 で止まり、承認資料を書く
3. 利用者が承認したら、同じコマンドに `--gate-approved user` を付けて打ち直し、マージする

**設計 Pull Request の宛先が自動反映の本番チャネルなら、そのマージは本番への反映も起こす。** 承認ゲート 1 の承認資料に
「このマージで本番へ出る」ことを書き、1 回の承認で両方を通す。承認を得たら conductor が
`merged-steps.py merge-when-green <番号> --gate-approved user` でマージする。1 回のマージに 2 回の承認を求めない。

## 宛先ごとの判定

| 宛先 | 判定 | NDF が行うマージ |
| --- | --- | --- |
| 本番チャネルでない | 当たらない | そのまま進む |
| 自動反映の本番チャネル | 当たる | 承認を得るまで進めない |
| 本番チャネルで、`delivery` が無い・不明、または宣言が読めない | 決められないため当たるとして扱う | 承認を得るまで進めない。`delivery` を宣言すれば次から判定できる |
| 本番チャネルで、`delivery` に `kind: auto` のその行が無い（手で反映・配布しない） | 当たらない | そのまま進む。本番への反映は `release` の工程で承認を取る |

**判定と止める場所は `merged-steps.py` が持つ**（本体は `lib/delivery.py` の `judge_target`。`gh` を呼ばず、宣言と宛先だけで決める）。
`merge-when-green` は最初の読みで宛先を判定し、当たれば CI を待たずに終了コード 10（`metrics.gate: production-merge`）で止まり、
承認資料を書く。承認を得たら同じコマンドに `--gate-approved user`（`fast` / `auto` の MVV 判定が通したときは `mvv`）を付けて打ち直す。
プランでは `merge-gate` のステップがプランを終え、承認の後に `run <プラン> --from merge-approved` で続ける。
エージェントが直接打つ `gh pr merge` は hook（`workflow-guard.sh`）が同じ判定で拒み、`merge-when-green` へ案内する。
人の手によるマージ（端末・GitHub の画面）は止めない。

**ベースブランチと本番チャネルが同じリポジトリでは、承認ゲート 2 はスプリントで 1 回になる。** 実装 Pull Request は
スプリントブランチへ入り、ベースブランチへ入るのは検査の Pull Request の 1 本だけである。スプリントを通さない
`new impl` / `new fix` は Pull Request ごとに止まる（1 本のマージが 1 回の本番への反映である）。
