# #2338 の設計: 外部の調査

設計文書の本体は [issue-2338-design.md](issue-2338-design.md) にある。番号 `STD<数>`・`PRD<数>`・`LIB<数>` は、本体と決定の記録からこのファイルの表を指す。

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 構造化ログの行の形・親子の実行のつなぎ方・追記と排他の OS の保証に、標準と仕様がある |
| 主要な製品 | 調べる | 差し替える出力先の代表（CloudWatch Logs）の 1 イベントの上限が、1 行の上限を決める |
| ライブラリとサービス | 調べる | 構造化ログ・出力先の差し替え・排他を、既存のライブラリで得られるかを決める |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | JSON Lines | UTF-8・BOM なし・1 行に 1 つの JSON の値・空行は不可・区切りは `\n` | 記録のファイルの形。空行を書かない | https://jsonlines.org/ （2026-11-09） |
| STD2 | OpenTelemetry Logs Data Model（Stable） | 行は Timestamp・TraceId・SpanId・SeverityText・Body・Attributes などを持つ。TraceId は 1 つの処理に属する行をまとめ、SpanId は処理の中の区間を指す | `run_id`（起点からの区間）と `parent_id` の親子で同じ役割を持たせる。行の鍵は #2335 の形のままにし、OpenTelemetry の形へは変えない（決定 1） | https://opentelemetry.io/docs/specs/otel/logs/data-model/ （2026-11-09） |
| STD3 | OpenTelemetry の環境変数による文脈の伝搬（Release Candidate） | ネットワークを使わない子プロセスへ、親が環境変数へ文脈を入れて渡す。子は起動時に取り出す。秘密を載せない | 親が `NDF_RUN_ID`・`NDF_RUN_LOG` を子へ渡す形（決定 5） | https://opentelemetry.io/docs/specs/otel/context/env-carriers/ （2026-11-09） |
| STD4 | Linux の `flock(2)` | 鍵は開いたファイルの記述に付く。別々に `open` したものは独立に排他になる。NFS では `fcntl` のバイト範囲の鍵として模される | 1 行を書く区間を `flock` で囲む。同じプロセスでも開き直した記述どうしで排他になる点を、シグナルの扱いに反映する（決定 1・決定 7） | https://man7.org/linux/man-pages/man2/flock.2.html （2026-11-09） |
| STD5 | Linux の `write(2)` | 成功した `write` が要求より少ないバイト数を返すことがある（容量不足など）。`O_APPEND` の位置の調整と書き込みは 1 つの原子的な手順 | 戻り値を確かめて残りを書き切る。書き切れないときは長さを戻す（決定 1） | https://man7.org/linux/man-pages/man2/write.2.html （2026-11-09） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | Amazon CloudWatch Logs | 1 イベントの上限 1,024 KB、`PutLogEvents` の 1 回の上限 1 MB（どちらも変えられない） | 1 行の上限 64 KiB は、差し替えた出力先でも 1 イベントで送れる（決定 2）。送る出力先の実装は #2608 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch_limits_cwl.html （2026-11-09） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | Python 標準の `logging`（Handler で出力先を差し替える） | Python 3 の標準ライブラリ | PSF | 標準ライブラリに従う | 無し | 一部。出力先の差し替えはできるが、プロセスの全体で共有するため `argparse` や依存先の出力まで混ざる。排他・全文ファイル・秘密を伏せる処理は自前で足す | `python3 -c "import logging.handlers"`（2026-11-09） |
| LIB2 | structlog | 26.1.0 | MIT OR Apache-2.0 | 2026-06-06 | 無し | 一部。行の形と処理の連鎖は得られるが、extras の無い環境（`deps.py` の診断も書き口を通る）で動かない | https://pypi.org/pypi/structlog/json （2026-11-09） |
| LIB3 | python-json-logger | 4.2.0 | BSD-2-Clause | 2026-08-15 | 無し | 一部。LIB1 の行を JSON にするだけで、LIB1 と同じ不足が残る。extras が要る | https://pypi.org/pypi/python-json-logger/json （2026-11-09） |
| LIB4 | watchtower（LIB1 から CloudWatch Logs へ送る Handler） | 3.4.0 | Apache-2.0 | 2025-02-25 | 無し | この課題では使わない。CloudWatch Logs の出力先の実装（#2608）の候補 | https://pypi.org/pypi/watchtower/json （2026-11-09） |
| LIB5 | opentelemetry-sdk（ログの出力先の差し替え） | 1.45.2 | Apache-2.0 | 2026-10-29 | 無し | 満たさない。送り先（Collector）の運用が前提で、手元の既定のファイルへ 1 行ずつ排他で書く形を持たない。extras が要る | https://pypi.org/pypi/opentelemetry-sdk/json （2026-11-09） |
| LIB6 | filelock（extras の `locks` にある） | 4.2.9 | MIT | 2026-11-04 | 無し | 満たさない。extras の無い環境で動かない。標準の `fcntl.flock` で足りる | https://pypi.org/pypi/filelock/json （2026-11-09） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| CloudWatch Logs が上限を超えたイベントを切るか拒むか | PRD1 の URL | 取得できたが、その頁に記述が無い。#2608 の設計で確かめる |

調べた量: 候補 12 件・取得の試み 11 回。
