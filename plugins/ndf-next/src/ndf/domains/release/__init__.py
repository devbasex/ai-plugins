"""リリース（段階ごとのモジュール。#1142 の M2。旧い子 RF8 #2995）。

リリースは GitHub Actions の workflow（`.github/workflows/ndf-next-release.yml`）が行い、会話のセッションは承認資料の
提示と、答えを証跡のコメントに書くことだけを持つ（#3395 の決定 128）。承認の正本はリリースの PR の証跡だけである。

段階と持ち主:

| 段階 | モジュール |
| --- | --- |
| 材料を集める | `plugins`（マーケットプレイスと版）・`declaration`（宣言と宣言の手順）・`gather` |
| 組み立て | `assemble`（版上げ・CHANGELOG・宣言の手順・PR） |
| 提示と承認 | `materials_decide`（承認資料）・`approval_decide`（証跡の判定）・`trail`（証跡の読み書き） |
| マージ | `land`（証跡の後のマージとベースブランチへ戻す PR）・`required_checks`（宛先の必須のチェックの解決）・`cleanup`（worktree の後片付け） |
| リリース記録 | `record_decide`（記録の本文と読み方）・`record`（含めた PR の各々へ書く・同じ PR へリリース後テストの記録を書く） |
| タグと GitHub Release | `tagging` |
| リリース後テスト | `verify`（ベースブランチのそろえと利用者の設定の控え） |

名前が `_decide.py` で終わるモジュールは副作用の無い判断（版・変化・CHANGELOG・CI の数え方・証跡・同期の手・
設定の控えの行・含む PR・記録の本文）で、単体で確かめる。副命令は `cli`。
"""
