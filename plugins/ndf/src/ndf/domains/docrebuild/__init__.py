"""文書の組み直し（`ndf document-rebuild run`。#4337）。

判断は名前が `_decide.py` で終わる純粋なモジュールが持ち、git・ファイル・外部のコマンドを読む部分は殻が持つ。

| モジュール | 中身 |
| --- | --- |
| `select_decide` | 差分のパスと課題の番号から候補を挙げ、通常ファイルと実装計画の頭で対象を決める |
| `drive` | 殻: 起点を決め、差分を読み、worker を 1 回起動し、結果を確かめて結果 JSON を組む |
| `cli` | サブコマンド `document-rebuild` |

工程とモードから文書の種類を決める対応は呼ぶ側（`ndf.progress.rebuild_values`）が持ち、ここは `ndf.progress` を読まない。
"""
