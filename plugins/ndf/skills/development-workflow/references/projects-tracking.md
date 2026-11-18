# 進捗記録とボード

**11 系の NDF は、工程の進行を GitHub Projects のボードへ書く処理を持たない。** 工程の進行と通過の記録は
スプリントのステートマシン（`ndf progress`）が持ち、`ndf progress show <ステートマシンの ID>` で読む。
セッションが変わっても、ステートマシンの ID から今の工程とモードを引き継げる。

`.ndf/projects.json` があっても NDF は読まない。ボードを使い続けるときは、利用者がボードの側で付け替える。

工程の名前は [SKILL.md](../SKILL.md) の「モードごとに起動する Skill」の表の行名が正である。通過記録の読み方は
[stage-completeness.md](stage-completeness.md) にある。
