# 承認ゲートの回答を記録する

[approval-request.md](approval-request.md) の「回答を記録する」の手順である。

**利用者が承認ゲートで回答したら、承認も変更要求も、利用者に示した時点の結果の `routes` にある、回答に対応するコマンドで記録する。**
pace と MVV 判定の有無を問わない。利用者へ新しい確認は求めない。`routes` を返すのは、スプリントのステートマシン（`ndf progress`）か、
プラン自身（`ndf plan`）のどちらかで、返るコマンドと補う引数が違う。

- `ndf progress` が回答を待つときの `routes` は `ndf answer` である。`--reason`・`--by human`・`--key` を補って打つ。
  コマンドに含まれる `--head`（見た PR の HEAD）・`--material-sha256`（承認資料の sha256）・`--presentation`（資料の提示の識別子）は
  そのまま渡す。これらを消すか、資料を出し直した後に古い `routes` を打つと、回答は断られ、承認ゲートは待ちのまま残る。
  書いた回答は `ndf answer list <ステートマシン>` で古い順に読める
- `ndf plan` が回答を待つときの `routes` は、回答ごとの `ndf plan next ... --answer <回答> --by human` である。変更要求も含め、
  該当の回答のコマンドを引数を足さずにそのまま打つ。
  コマンドに含まれる `--waiting`（回答する承認待ち。回の id・ステップ・待ちの番号）と `--presented`（資料の提示の識別子）は、利用者に示した承認待ちと資料を指す。
  提示の識別子が返る承認待ちでは、人の回答に `--waiting` と `--presented` の両方が要る。今の承認待ちや提示と照らして合わない回答は、記録されずに `approval_stale` で止まる。
  前の回や前の承認待ちで返った古い `routes` を打った場合も、同じく止まる。
  提示の識別子が返らない承認待ちでも、結果にある回答コマンドを引数を削らずにそのまま打つ。`--waiting` を削ると、前の回への回答と見分けられないまま記録される。
  止まったら、`ndf plan wait` で今の承認待ちと資料を読み直して回答する。
  `ndf plan wait` の結果では、承認待ちのプランの `items` の要素が、その `routes` と同じ提示の承認資料のパス（`presentation_path`）と、
  承認でマージが本番へ反映され得るか（`production`）を持つ。利用者には、この資料で本番への反映を説明する
- `ndf plan queue` か `ndf plan wait` の結果に複数のプランが含まれる場合は、提示した件数にかかわらず、結果の直下ではなく、
  回答の対象のプランに当たる `items` の要素の `routes` を使う。結果の直下の `routes` は回答用のコマンドを持つ最初のプランのもので、
  実行先のプランが指定されているため、別のプランへの回答に使うと最初のプランに記録される。対象は提示時の `items` の `name` で確かめる

下の表の `<…>` は `routes` が埋めない値である。

| 回答を待っているもの | 承認 | 変更要求 |
| --- | --- | --- |
| スプリントのステートマシン（`ndf progress`） | 提示時の `routes.approved` の `ndf answer` に `--reason <要約> --by human --key <冪等のキー>` を補って打つ | 提示時の `routes.request_changes` の `ndf answer` に `--reason <要約> --by human --key <冪等のキー> [--notes <指摘ファイル>]` を補って打つ |
| プラン自身（`ndf plan`） | 提示時の `routes.approved` の `ndf plan next` をそのまま打つ | 提示時の `routes` にある変更要求に当たる回答（テンプレートのプランでは `rejected`）の `ndf plan next` をそのまま打つ |
| ステートマシンが無い（単発の変更など） | 記録しない。回答と要約を conductor の報告に書く | 同じ |

`ndf answer` の `--gate` は承認ゲートの結果の `metrics.gate.id`、`--result` はその結果の `routes` のキーから写す。`--key` は回答ごとに決め、
同じキーで打ち直しても 2 行にならない。終了コードが 0 以外（記録を書けない）なら、書けなかったことと `summary` を conductor の報告に出して先へ進む。
設計承認ゲートで起票者の推定が変更を要求されたときの仕様の直し方は `requirements-design` の手順 1a にある。
