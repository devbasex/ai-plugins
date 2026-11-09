# #2338 の設計: 外部の調査

設計文書の本体は [issue-2338-design.md](issue-2338-design.md) にある。番号 `STD<数>`・`PRD<数>`・`LIB<数>` は、本体と決定の記録からこのファイルの表を指す。

## 外部の調査

調べた主題は「複数のプロセスに対応した堅牢なログの出力・収集・保管」である。出力（プロセスの中でどう書くか）・形式と相関（行の形と親子のつなぎ方）・
収集と保管（書いた行をどこへ集め、いつまで持つか）の 3 つに分けて、既存の仕組みを比べた。

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 構造化ログの行の形・親子の実行のつなぎ方・追記と排他の OS の保証に、標準と仕様がある |
| 主要な製品 | 調べる | 収集と保管の代表（Fluent Bit・Vector・journald・CloudWatch Logs）の行の上限・中断への強さ・寿命が、行の上限と出力先の形を決める |
| ライブラリとサービス | 調べる | 複数のプロセスから 1 ファイルへ書く出力・構造化ログ・出力先の差し替えを、既存のライブラリで得られるかを決める |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | JSON Lines | UTF-8・BOM なし・1 行に 1 つの JSON の値・空行は不可・区切りは `\n` | 記録のファイルの形。空行を書かない | https://jsonlines.org/ （2026-11-09） |
| STD2 | OpenTelemetry Logs Data Model（Stable） | 行は Timestamp・TraceId・SpanId・SeverityText・Body・Attributes などを持つ。TraceId は 1 つの処理に属する行をまとめ、SpanId は処理の中の区間を指す | `run_id`（起点からの区間）と `parent_id` の親子で同じ役割を持たせる（決定 1） | https://opentelemetry.io/docs/specs/otel/logs/data-model/ （2026-11-09） |
| STD3 | OpenTelemetry の環境変数による文脈の伝搬（Release Candidate） | ネットワークを使わない子プロセスへ、親が環境変数へ文脈を入れて渡す。子は起動時に取り出す。秘密を載せない | 親が `NDF_RUN_ID`・`NDF_RUN_LOG` を子へ渡す形（決定 5） | https://opentelemetry.io/docs/specs/otel/context/env-carriers/ （2026-11-09） |
| STD4 | Linux の `flock(2)` | 鍵は開いたファイルの記述に付く。別々に `open` したものは独立に排他になる。NFS では `fcntl` のバイト範囲の鍵として模される | 1 行を書く区間を `flock` で囲む。開き直した記述どうしで排他になる点をシグナルの扱いに反映する（決定 1・決定 7） | https://man7.org/linux/man-pages/man2/flock.2.html （2026-11-09） |
| STD5 | Linux の `write(2)` | 成功した `write` が要求より少ないバイト数を返すことがある（容量不足など）。`O_APPEND` の位置の調整と書き込みは 1 つの原子的な手順 | 戻り値を確かめて残りを書き切る。書き切れないときは長さを戻す（決定 1） | https://man7.org/linux/man-pages/man2/write.2.html （2026-11-09） |
| STD6 | OpenTelemetry の trace context in non-OTLP Log Formats | OTLP 以外の行の形では、TraceId を鍵 `trace_id`、SpanId を `span_id`（どちらも小文字の 16 進）で持つ。3 つの鍵はどれも任意 | 行の鍵は #2335 の `run_id`・`parent_id` のまま。OpenTelemetry へ送る出力先が送るときに `trace_id`・`span_id` へ写す（決定 1） | https://opentelemetry.io/docs/specs/otel/compatibility/logging_trace_context/ （2026-11-09） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | Amazon CloudWatch Logs（ECS の `awslogs` ログドライバー・FireLens） | 1 イベントの上限 1,024 KB、`PutLogEvents` の 1 回の上限 1 MB。ECS の `awslogs` はコンテナの標準出力と標準エラーをそのまま送る。FireLens は Fluent Bit か Fluentd のコンテナをタスクに足して振り分ける | Factory の出力先は #2608 が決める。標準出力は結果 JSON、標準エラーは人へ見せる診断に使っているため、記録の行をこの 2 つへ混ぜない（決定 4） | https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch_limits_cwl.html ・ https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html ・ https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_firelens.html （2026-11-09） |
| PRD2 | Fluent Bit（入力 `tail`） | ファイルの inode と読んだ位置を DB に持ち、SIGTERM では位置を同期する。SIGKILL・電源断では少なくとも 1 回の配送（重複はあり得る）。1 ファイルの読み込みの領域 `buffer_max_size` の既定は 32k で、超える行に当たると既定ではそのファイルの監視をやめる（`skip_long_lines` で飛ばせる） | 1 行の上限を、既定の設定のままでも監視が止まらない 32,000 バイトに置く（決定 2） | https://docs.fluentbit.io/manual/data-pipeline/inputs/tail （2026-11-09） |
| PRD3 | Vector（入力 `file`） | `data_dir` に読んだ位置のチェックポイントを持つ。区切りは `line_delimiter`（既定 `\n`）、1 行の上限 `max_line_bytes` の既定は 102,400 バイト | 1 行の上限 32,000 バイトは既定のまま読める。JSON Lines のファイルを後から集める形が成り立つ（決定 2・決定 4） | https://vector.dev/docs/reference/configuration/sources/file/ （2026-11-09） |
| PRD4 | systemd-journald | systemd の上で動くサービスのログを集め、名前空間ごとに保管する。並行のログの流れは 4,096 本まで | 採らない。手元のコンテナに journald が無い（`ls /run/systemd/journal` が「No such file or directory」、2026-11-09） | https://www.freedesktop.org/software/systemd/man/latest/systemd-journald.service.html （2026-11-09） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | Python 標準の `logging`（`Handler` による出力先の差し替え、`QueueHandler` / `QueueListener`） | Python 3 の標準ライブラリ | PSF | 標準ライブラリに従う | 無し | 一部。出力先を `Handler` で差し替える枠組みと、OpenTelemetry・CloudWatch Logs へ送る既存の `Handler` が使える。ただし公式の手引きは「複数のプロセスから 1 ファイルへのログは、プロセスをまたいで直列化する標準の方法が無いためサポートしない」と書く。`QueueHandler` / `QueueListener` は同じプロセスのスレッドか、`multiprocessing` の `Queue` を共有するプロセスの間だけで働く | https://docs.python.org/3/howto/logging-cookbook.html ・ https://docs.python.org/3/library/logging.handlers.html （2026-11-09） |
| LIB2 | structlog | 26.1.0 | MIT OR Apache-2.0 | 2026-06-06 | 無し | 一部。行の形を処理の連鎖で組むが、書き出しは LIB1 の `Handler` へ任せる（`ProcessorFormatter`）。複数のプロセスの排他は持たない。extras が要る | https://pypi.org/pypi/structlog/json ・ https://www.structlog.org/en/stable/standard-library.html （2026-11-09） |
| LIB3 | loguru（`enqueue=True`） | 0.7.3 | 未確認（PyPI の `license` は空） | 2024-12-06 | 無し | 満たさない。`enqueue` は `multiprocessing` の安全なキューを通すもので、`multiprocessing` で起こした子に logger を渡す前提である。`subprocess` で起こした別のエントリポイントの間は守らない | https://loguru.readthedocs.io/en/stable/api/logger.html ・ https://loguru.readthedocs.io/en/stable/resources/recipes.html （2026-11-09） |
| LIB4 | concurrent-log-handler（portalocker を使う） | 0.9.31（portalocker 4.4.2） | Apache-2.0（portalocker は BSD-3-Clause） | 2026-10-21 | 無し | 一部。LIB1 の `Handler` として、別々に起動したプロセスからも 1 ファイルへ排他で書ける（別のロックファイルに `flock`）。0.9.29 で fork の後の排他の取り違えを直している。ただし extras が要り、書き切れなかった行を戻す処理と、強制終了で残った書きかけの行の扱いを持たない。主な用途はファイルの回転（大きさ・時刻） | https://pypi.org/pypi/concurrent-log-handler/json ・ https://pypi.org/pypi/portalocker/json （2026-11-09） |
| LIB5 | opentelemetry-sdk（`LoggingHandler` で LIB1 の行を送る） | 1.45.2 | Apache-2.0 | 2026-10-29 | 無し | この課題では使わない。送り先（Collector か OTLP の受け手）の運用が前提である。LIB1 の `Handler` として足せるため、送る出力先の候補になる | https://pypi.org/pypi/opentelemetry-sdk/json （2026-11-09） |
| LIB6 | watchtower（LIB1 から CloudWatch Logs へ送る `Handler`） | 3.4.0 | Apache-2.0 | 2025-02-25 | 無し | この課題では使わない。CloudWatch Logs の出力先（#2608）の候補。LIB1 の `Handler` として足せる | https://pypi.org/pypi/watchtower/json （2026-11-09） |

### 観点ごとの比べ

依頼の 8 つの観点で、出力と収集・保管の候補を比べた。「この課題で」の列は、この課題の書き口に採るかを示す。

| # | 並行の書き込み | 親子と別のコンテナ | 中断に強いか | 依存の重さ | 出力先の切り替え | 保管と寿命 | 秘密の伏せ方 | 運用の実績 | この課題で |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | スレッドは安全。プロセスをまたぐ 1 ファイルは公式に非対応 | `subprocess` の子へは何も渡らない | 書きかけの行を戻さない | 無し | `Handler` で切り替えられる | 持たない | `Filter` で伏せられる | 標準ライブラリ | **枠組みに採る**（決定 1・決定 4） |
| LIB2 | LIB1 に従う | LIB1 に従う | LIB1 に従う | extras | LIB1 に従う | 持たない | 処理の連鎖で伏せられる | 2026-06-06 に 26.1.0 | 採らない |
| LIB3 | `multiprocessing` の子だけ | `subprocess` の子・別のコンテナは対象外 | キューの残りは終わりの処理に頼る | extras | 書き先（sink）を関数で足す | 回転と保持を持つ | `patch` で伏せられる | 最後の版が 2024-12-06 | 採らない |
| LIB4 | 別々のプロセスから `flock` で排他 | 同じファイルシステムなら別のコンテナも排他 | 書きかけの行を戻さない・強制終了の後の行の連結を防がない | extras（portalocker） | LIB1 の `Handler` の 1 つ | 大きさ・時刻で回転 | LIB1 の `Filter` | 2026-10-21 に 0.9.31 | 採らない（決定 1） |
| LIB5 | 送り先へ任せる | 文脈の伝搬は STD3 | 送り先とバッファへ任せる | extras（SDK・API・規約） | LIB1 の `Handler` の 1 つ | 送り先が持つ | 送る前の処理 | 2026-10-29 に 1.45.2 | #2608 の候補 |
| PRD2 | 読む側のため対象外 | ファイルを読むだけ | SIGKILL で少なくとも 1 回の配送 | 別のプロセス | 出力のプラグインで切り替える | 送り先が持つ | フィルタで伏せられる | AWS が ECS 向けのイメージを配る（PRD1） | 採らない。後から集める道具として残る（決定 4） |
| PRD3 | 読む側のため対象外 | ファイルを読むだけ | チェックポイントで再開 | 別のプロセス | 出力（sink）で切り替える | 送り先が持つ | 変換で伏せられる | 1 行の既定 102,400 バイト | 採らない。同上 |
| PRD4 | journald が直列化する | 同じホストの systemd が前提 | journald が持つ | systemd | 無し（journald の転送） | 名前空間ごとに持つ | 持たない | systemd の一部 | 採らない（手元のコンテナに無い） |
| PRD1 | イベントの順は送る側が決める | ECS ではタスクのコンテナごと | ドライバーに任せる | AWS の権限 | `awslogs` か FireLens | ロググループの保持期間 | 送る前に伏せる | ECS の標準 | #2608 で決める |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| CloudWatch Logs が上限を超えたイベントを切るか拒むか | PRD1 の 1 つ目の URL | 取得できたが、その頁に記述が無い。#2608 の設計で確かめる |
| python-json-logger・filelock・Fluentd | 試していない | 上限（種類ごとの候補 6 件）。python-json-logger は LIB2 と、filelock は LIB4 と、Fluentd は PRD2 と同じ位置に立つ |
| loguru のライセンス・loguru と concurrent-log-handler の利用の規模（ダウンロード数・利用するリポジトリの数） | 試していない | PyPI の JSON の `license` が空で、数も持たない。「運用の実績」の列は最後のリリースの日付と、配る者で示した |

調べた量: 候補 16 件・取得の試み 27 回。
