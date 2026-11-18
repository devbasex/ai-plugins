"""記録（確定仕様化・引継ぎ・用語集・使用量・MVV）。#1142 の M7（旧い子 N8a #3263・N8b #3264・SF10 #3208）。

工程の終わりに残す記録と、記録を読む集計を持つ。名前が `_decide.py` で終わるモジュールは副作用の無い判断で、
単体で確かめる。ほかのモジュールはファイル・git・GitHub を読み書きする口である。

| 責務 | モジュール |
| --- | --- |
| 用語集の文書をコンテキストごとのファイルと索引に分けて作る・意味の説明の廃止した語 | `glossary_decide`・`glossary` |
| 確定仕様化: 語ごとの SSOT の選び方とコミットに入れるファイル | `spec_decide`・`spec` |
| 引継ぎ文書の大きさ・テンプレートのセクション・書いたパスの実在 | `handoff_decide`・`handoff` |
| 使用量の使用量ログ（応答ごとの数え方・期間・JST の両端・保存先・停止の差） | `usage_decide`・`usage` |
| MVV の記録（検証の再利用・改訂の兆候・判定の材料と指示文） | `mvv_decide`・`mvv_material_decide`・`mvv` |
| 手順 0 の 3 つの確かめのまとめ | `preflight_decide`・`preflight` |
| 工程の終わり（スプリントを閉じる条件・クリーンアップの工程の記録・gws の認証） | `close_decide`・`cleanup_stage`・`gws` |
| 設定のファイルの保存先（表 ndf-files の location と path の解き方） | `files` |
| 記録の JSONL の読み書き | `jsonl` |
| 設定・SSOT の JSON のファイルの読み込み | `jsonfile` |
| サブコマンド `ndf records` | `cli` |
"""
