"""リリース（段階ごとのモジュール。#1142 の M2。旧い子 RF8 #2995）。

ブランチの運用は GitHub flow を土台にする（#3572）。スプリントの PR（main 宛て）が本番の PR を兼ね、承認した先端との照合付きの
main へのマージがリリースになる。版数とタグは CD（`.github/workflows/ndf-next-release.yml`）がマージの後に版上げの PR で作り、
PR の検査と検証の ref は `.github/workflows/ndf-next-pr.yml` が持つ。会話のセッションは承認資料の提示と、答えを証跡のコメントに
書くことだけを持つ（#3395 の決定 128）。承認の正本は本番の PR の証跡と、人の GitHub でのマージである。

段階と持ち主:

| 段階 | モジュール |
| --- | --- |
| 材料を集める | `plugins`（マーケットプレイスと版）・`declaration`（宣言と宣言の手順）・`gather` |
| 承認資料 | `assemble`（本番の PR の本文の承認資料の節。ブランチを作らない） |
| 提示と承認 | `materials_decide`（承認資料）・`approval_decide`（証跡の判定）・`trail`（証跡・人のマージ・作った版の記録の読み書き）・`merge_decide`（承認の経路） |
| マージ | `land`（証跡の後の照合付きのマージ。同期の PR を作らない）・`required_checks`（宛先の必須のチェックの解決）・`cleanup`（worktree の後片付け） |
| 本番のプランの形 | `release_plan`（承認の後に直しのステップを置かない・`merge` の部品の前の `release-guard`） |
| 版上げ（CD） | `bump_decide`（上げ幅・起点・動き）・`bump`（版上げの PR）・`confirm`（版上げの PR のマージ・タグ・GitHub Release の確かめ） |
| PR の検査と検証の ref | `pr_check`（版数・上げ幅のラベル・検証の ref）・`verify_decide`・`verify_ref`（2 つ目のマーケットプレイスが指す検証の ref） |
| 共有の補助 | `shapes`（形の決まらない JSON の値の読み） |
| リリース記録 | `record_decide`（記録の本文と読み方）・`record`（含めた PR の各々へ書く・同じ PR へリリース後テストの記録を書く） |
| タグと GitHub Release | `tagging` |
| リリース後テスト | `verify`（ベースブランチのそろえと利用者の設定の控え） |

名前が `_decide.py` で終わるモジュールは副作用の無い判断（版・変化・CHANGELOG・CI の数え方・証跡・承認の経路・版上げ・
検証の ref・同期の手・設定の控えの行・含む PR・記録の本文）で、単体で確かめる。副命令は `cli`。
"""
