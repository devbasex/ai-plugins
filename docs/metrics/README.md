# 計測の記録

**消える記録から集計値だけを残す置き場である。** 生の記録・会話の本文・リポジトリ名・会話の ID は
置かない。各ファイルは集計した日時点の値で、以後の記録の増減には追随しない。

| 置き場所 | 中身 | 作り方 |
| --- | --- | --- |
| [ndf-token-usage/](ndf-token-usage/2026-09-23.md) | ndf の版ごとのトークン消費と所要時間（#893）。`<集計日>.md` が版の軸の表、同名の `.json` が版・モード・モデル・Claude Code の版・cross-review の担当で分けた値 | `python3 scripts/token-usage.py`（引数はファイルの「作り方」の節） |
| [ndf-cost-and-lead-time/](ndf-cost-and-lead-time/2026-11-10.md) | 開発の費用（層・起動の形・工程・Skill 別の token と USD）と速さ（リリース・課題とスプリントのリードタイム・テストの時間）の週次の集計と、無駄の仮説の検証。`<集計日>.md` が結論、同名のディレクトリが CSV | transcript と使用量の帳簿（`plugins/ndf/scripts/usage-report.py`）、`gh`・`git` の集計（手順はファイルの「作り方」の節） |
