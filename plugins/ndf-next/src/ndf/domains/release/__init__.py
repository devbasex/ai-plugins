"""リリース（段階ごとのモジュール。#1142 の M2。旧い子 RF8 #2995）。

リリースは GitHub Actions の workflow（`.github/workflows/ndf-next-release.yml`）が行い、会話のセッションは承認資料の
提示と、回答を証跡のコメントに書くことだけを持つ（#3395 の決定 128）。承認の SSOT はリリースの PR の証跡だけである。

段階とオーナー:

| 段階 | モジュール |
| --- | --- |
| 材料を集める | `plugins`（マーケットプレイスとバージョン）・`ndf_settings`（設定と設定の手順）・`gather` |
| 組み立て | `assemble`（版上げ・CHANGELOG・設定の手順・PR） |
| 提示と承認 | `materials_decide`（承認資料）・`approval_decide`（証跡の判定）・`trail`（証跡の読み書き） |
| マージ | `land`（証跡の後のマージとベースブランチへ戻す PR）・`cleanup`（worktree の後片付け） |
| リリース記録 | `record_decide`（記録の本文と読み方）・`record`（含めた PR の各々へ書く・同じ PR へリリース後テストの記録を書く） |
| タグと GitHub Release | `tagging` |
| リリース後テスト | `verify`（ベースブランチのそろえと利用者の設定のスナップショット） |

名前が `_decide.py` で終わるモジュールは副作用の無い判断（バージョン・変化・CHANGELOG・CI の数え方・証跡・同期の手・
設定のスナップショットの行・含む PR・記録の本文）で、単体で確かめる。サブコマンドは `cli`。
"""
