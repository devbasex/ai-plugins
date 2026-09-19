# cross-review: 利用上限で止まった担当が「結果ファイル無し」と報告されて空振りの起動し直しで待たされ、止めた担当が後から結果を書く → 上限を理由に報告して同じラウンドで起動し直さず、止めた後は書かせない（要求と受け入れ条件 / #729 #619 #584）

## 目的

**起きていること。** 担当の CLI（kiro / claude）が月間の利用上限に当たると、監視はその文言を読めない。結果なしの理由は「結果ファイル無し」に畳まれる。進行側は同じ担当を同じラウンドで起動し直し、監視の上限 1 回分（レビューで 1200 秒）を待ってから全体を誤りで終える（#619。#647 では 3729 回の空振り）。また、監視が止めた担当の子プロセスが、止めた後に結果ファイルを書く（#584）。

**困る人。** 収束ループを回す進行側と、結果を待つ利用者。届く理由が「結果ファイル無し」のため、上限に当たったのか、監視の上限で打ち切られたのかを判別できない。

**直すと成り立つこと。** 理由が「利用上限」として進行側と利用者に届き、同じラウンドで同じ担当を起動し直さない。結果ファイルが無いときの「監視が打ち切った」「CLI が自分の上限で終わった」「終わったが結果を書かなかった」を、結末の語彙 1 つで区別できる。結果なしの判断と起動し直しの可否は共通層の 1 か所が持つ。cross-review と cross-refactoring がその値を読む（両 Skill が同じ判断を別々に書かない）。監視が止めた担当の子プロセスは、止めた後に結果ファイルを書かない。

## 用語

本文はこの表の用語で書く。受け入れ条件は検査で突き合わせる値を持つため、識別子の列の語で書く。

| 用語 | 意味 | 識別子 |
| --- | --- | --- |
| 担当 | レビュー・反証・適用・修正を行う CLI（codex / agy / kiro / claude） | — |
| 起動 1 回 | 起動の手順が担当を 1 度起動し、監視がそれを見終わるまで | `launch-cli.sh` / `monitor.py` |
| 結末 | 起動 1 回の終わり方。監視の状態と理由、結果ファイルの有無と読めるかを合わせたもの | 監視の `status` と `reason` |
| 理由 | 結末の語彙の 1 語 | `monitor_outcome.REASONS` |
| 起動し直しの可否 | 同じ担当を同じ条件で起動し直せば解ける結末か（偽なら、起動し直しても同じ結末になる） | `relaunch_same_agent` |
| 使える結果 | 結果ファイルがあり、JSON オブジェクトとして読める | `payload` |
| 結果ファイル | 担当が一時ディレクトリへ書く JSON | `<stem>-result.json` |
| 監視の結果ファイル | 担当 1 者の最後の監視の結果（P1） | `<stem>-monitor.json` |
| 監視の記録 | 監視の結果を追記だけで積む（P1） | `monitor-outcomes.jsonl` |
| 利用上限 | 担当の CLI の月間・週間の利用枠に達し、起動し直しても解けない状態 | 理由 `usage_limit`。監視の状態は早期の致命 `EARLY_ERROR`（終了コード 4） |
| CLI の上限 | 担当の CLI 自身の実行時間の上限（agy の `--print-timeout`） | 理由 `cli_timeout`。監視の状態は結果なし `NO_RESULT`（終了コード 3） |
| 結果ファイル無し | 結果ファイルが無く、理由の文言も無い | 理由 `missing` |
| 読めない結果 | 結果ファイルがあるが JSON オブジェクトとして読めない | 理由 `unparsable` |
| 誤りの終わり | 収束ループ全体を誤りとして終える | 状態ファイルの `final=error`。判定の終了コード 1 |
| 結果の取り込み / 判定 / 報告の表 | cross-review の状態の操作の 3 コマンド | `state.py read-result` / `judge` / `report` |
| 結末の共通層 | 2 つ以上の Skill が使う部品の置き場所にある、結末の語彙と読み取り | `plugins/ndf/scripts/lib/monitor_outcome.py` |

## 対象範囲

変えるのは共通層の 3 ファイルと cross-review で、cross-refactoring は契約だけを決める。

含む:

| 場所 | 扱うもの |
| --- | --- |
| 共通層 `plugins/ndf/scripts/lib/monitor_outcome.py` | 理由の語彙 2 つの追加、結末を 1 つの値として読む関数、起動し直しの可否 |
| 共通層 `plugins/ndf/scripts/lib/monitor.py` | 利用上限と CLI の上限の文言の検知、理由を持つ結末、プロセスグループへの停止 |
| 共通層 `plugins/ndf/scripts/lib/launch-cli.sh` | CLI を独立したプロセスグループで起動する |
| cross-review `scripts/state.py` | `read-result` の結果なしの理由と `monitor_detail`、`judge` の理由の出力と利用上限での停止、`report` の表 |
| cross-review `docs/` | 理由の表、上限に当たった場合の見分け方 |
| テスト | 共通層と cross-review のテスト |

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| cross-refactoring の `gitfacts.read_result` と 3 つの取り込み（`merge-apply` / `merge-fix` / `merge-final-fix`）の実装 | #728（G4）。この文書は契約だけを決める（設計文書の「両 Skill が従う契約」） |
| 投稿の後に打ち切られた担当の記録と重ねての投稿（`prior_review_url`、記録だけの起動） | #583 → #730（G5）。既存の設計の決定 18・AC63〜AC67 はそちらへ移る |
| 起動し直した担当を初回と同じ経路（証拠集約を含む）に通す骨組みの変更 | 既存の設計の決定 19・AC67。投稿の重なりと同じ骨組みの行を触るため #730（G5）へ渡す |
| 利用上限の担当を外して残りの担当で回す | #478（D-B） |
| 反証の取り直し（`critique-round.sh`）で理由を見ること | 既存の設計の「未確認のまま残ること」10。直さない |
| codex のモデルの 404 を理由として区別すること | #461（マイルストーン 06）。この語彙では `early_error` か `missing` に落ちる |
| 監視の終了コードと標準出力の変更 | 前提 6 |
| 型・クラスの新設のうち、画面・永続データベース・OpenAPI に当たるもの | この変更に画面と API は無い。永続データは状態ファイルと監視の結果ファイル（JSON）で、設計文書の「データ構造」が表で持つ |
| `CHANGELOG.md` と版数 | 配布の工程が書く |

## 前提

利用上限の文言の実物は 2 つで、配布済みの P1 / P2 の上に載せる。

| # | 前提 |
| --- | --- |
| 1 | claude の利用上限の文言の実物は `"api_error_status":429` を含む行である（#647 の本文）。err.log と stdout.log のどちらに出るかは未確認のため、両方を見る |
| 2 | kiro の利用上限の文言の実物は err.log の `Monthly request limit reached` である（#619 の本文、PR #601 の round 2） |
| 3 | P1（監視の結果ファイル `<stem>-monitor.json` と `monitor_outcome.read_outcome`）と P2（`limits.py`、`--phase`）は v10.13.0 で `develop` に入っている。この変更はその上に載せる |
| 4 | cross-refactoring の側の実装（`gitfacts.read_result` と 3 つの取り込み）は #728（G4）が行う。この変更が決めるのは `read_result` が従う契約だけである |
| 5 | 利用上限で止まった担当を外して残りの担当で回す判断は #478（D-B）が持つ。この変更は「同じ担当を起動し直さずに止めて理由を報告する」までである |
| 6 | 監視の終了コード 0〜6 と標準出力の 13 個のキーは変えない（既存の設計の決定 16。G4 の骨組みがこれを前提にする） |

## 影響

監視の終了コードと標準出力は変わらず、増えるのは理由の値と状態ファイルの鍵である。

| 対象 | 影響 |
| --- | --- |
| `monitor.py` の終了コードと標準出力 | 変わらない。監視の結果ファイルと記録の `reason` に 2 つの値が増える |
| `monitor_outcome.REASONS` | 6 語から 9 語になる。読む側（`run_metrics.py` の `--by reason`）は値を集計するだけで、語彙の一覧を持たないため変更なし |
| 状態ファイル | `rounds[-1].<担当>` に `monitor_detail` が増える（結果なしのときだけ）。`no_result_reason` の値が 3 種類から 10 種類になる |
| `state.py judge` | `NO_RESULT_REASONS` の行が増える。利用上限では起動し直さず 1 で終わる。0 / 2 / 7 / 8 の意味は変わらない |
| `state.py read-result` | 終了コードは変わらない。`no_result_reason` の値が増える |
| `gitfacts.read_result` | この変更では触らない。契約（結果なしを値で返す）を G4 が実装する |
| 担当の CLI のプロセス | 独立したプロセスグループで動く。監視の停止がグループへ届く |
| 待ち時間の最悪値 | 利用上限では起動し直さないため、1 ラウンドあたり監視の上限 1 回分（レビュー 1200 秒）短くなる |

## 非機能の条件

運用・保守性、セキュリティ、システム環境の 3 つを条件にする。

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 結果なしの理由が、状態ファイル（`no_result_reason`）・監視の結果ファイル・監視の記録・実行の要約の `launches[]` の 4 か所で同じ語彙で読める。理由の語彙を足すときに変える場所は `monitor_outcome.py` の 1 か所である |
| セキュリティ | `monitor_detail` は err.log の抜粋（最大 200 文字）で、作業ツリーの中の状態ファイルにだけ残る。実行の要約には入れない（既存の設計の決定 7 のまま） |
| システム環境 | macOS の bash 3.2 でも AC19 が成り立つこと（未確認。設計文書の「未確認のまま残ること」） |

## 前提とする取り決め

プロジェクトの規約のうち、この変更が従うものを 3 つ挙げる。

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 2 つ以上の Skill が使う部品は `plugins/ndf/scripts/lib/` に置く（`plugins/ndf/scripts/lib/README.md` の「置いてよいもの・いけないもの」）。結末を読む関数は両 Skill が使うため共通層に置く |
| コーディング規約 | 外部コマンドとシグナルの挙動、正規表現の一致範囲は書く前に実行して確かめる（`AGENTS.md` の DO）。状態ファイルの鍵は追加だけで、既存の鍵の意味を変えない |
| テスト戦略 | 監視と起動は、PATH へ置いた偽の CLI を実プロセスとして動かす既存の形（`cross-review/tests/test_monitor_*.py`）。結末の読み取りは一時ディレクトリに監視の結果ファイルと結果ファイルを置いて単体に試す。`judge` / `report` は状態ファイルを作って呼ぶ |

## 境界

常に行うこと、確認してから行うこと、行わないことを分ける。

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、配布物の同期の検査、新しい文言を実物のログの形で試すこと |
| 確認してから行う | 利用上限で起動し直さない判断（AC15）。理由の語彙の追加（G4 が読む） |
| 行わない | cross-refactoring の取り込みの実装（G4）、投稿の重なりと骨組みの経路の変更（G5）、担当を外して回す判断（D-B）、監視の終了コードの変更 |

## 検証手段

テスト・配布物の同期・定義の検査と、2 つの issue の再現の手動確認で確かめる。

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 定義の検査 | `claude plugin validate .` と `python3 scripts/check-skill-frontmatter.py` |
| 手動確認（#619 の再現） | err.log に `Monthly request limit reached` を書く偽の kiro を PATH に置いて cross-review を 1 ラウンド回し、`judge` が 7 を返さず `NO_RESULT_REASONS='kiro=usage_limit'` を出して 1 で終わる |
| 手動確認（#584 の再現） | #584 の本文の再現スクリプト（3 秒後に子が書く `bash -c`）を `launch-cli.sh` 経由で起動し、`_kill_pid` の後に `late-result.json` が無い |

## 受け入れ条件

24 件を 5 つの群に分ける。文言の一覧は設計文書の「入出力の契約」の「検知の文言」にある。

語彙と検知（監視、#619）:

- [ ] AC1: `monitor_outcome.REASONS` は次の 9 語である。`reason_for(status)` の 6 つの状態に対する返り値は変更前と同じである

  | 既存の 6 語 | 足す 3 語 |
  | --- | --- |
  | `ok` / `timeout` / `stalled` / `early_error` / `missing` / `pidfile_bad` | `usage_limit` / `cli_timeout` / `unparsable` |

- [ ] AC2: err.log に `Monthly request limit reached` の行が出ると、監視は担当を止めて `EARLY_ERROR`（終了コード 4）を返す。監視の結果ファイルの `reason` は `usage_limit` である
- [ ] AC3: err.log（全担当）か stdout.log（claude だけ）に、`"api_error_status"` と `429` を `:` で結んだ行が出たときも AC2 と同じである。`reason` は `usage_limit` になる。`:` の前後の空白の有無は問わない
- [ ] AC4: 既存の一致のうち `quota exceeded` / `rate limit exceeded` / `HTTP/<版> 429` の `reason` は `usage_limit` になる。`HTTP/<版> 401` / `HTTP/<版> 403` とそれ以外の致命の一致は `early_error` のままである
- [ ] AC5: 担当が結果ファイル無しで終わり、err.log に `print timeout after <時間> with turn in progress` がある場合を扱う。監視は `NO_RESULT`（終了コード 3）を返し、`reason` は `cli_timeout` である。同じ文言があっても結果ファイルがあれば `OK` / `ok` である
- [ ] AC6: AC2〜AC5 の文言が err.log で markdown の表の行・引用・バッククォート・grep 形式の引用の中にあるときは一致しない。claude の stdout.log は JSON 向けの照合で見るため、この除外を掛けない
- [ ] AC7: `usage_limit` / `cli_timeout` は監視の結果ファイルと監視の記録の `reason` に入る。監視の標準出力の 13 個のキーと値の型、終了コードは変更前と同じである

結末を 1 つの値として読む（共通層、#729）:

- [ ] AC8: `monitor_outcome.read_launch_outcome(tmp_dir, stem, result_path)` を呼ぶ。結果ファイルが JSON オブジェクトとして読めるとき、返り値はその辞書を `payload` に持つ。`reason` は `None`、`relaunch_same_agent` は `True` である。監視の結果ファイルの `reason` が何であっても同じである
- [ ] AC9: 使える結果が無いとき、`reason` は次の表で決まる

  | 監視の結果ファイルの `reason` | 結果ファイル | `reason` |
  | --- | --- | --- |
  | `timeout` / `stalled` / `early_error` / `usage_limit` / `cli_timeout` / `pidfile_bad` | 問わない | その値 |
  | `ok` / `missing` / ファイルが無い・読めない | 無い、または空 | `missing` |
  | `ok` / `missing` / ファイルが無い・読めない | あるが JSON として読めない、または JSON オブジェクトでない | `unparsable` |

- [ ] AC10: `relaunch_same_agent` は `reason` が `usage_limit` のときだけ `False` で、それ以外の理由では `True` である。`monitor_outcome.relaunch_same_agent(reason)` も同じ値を返す
- [ ] AC11: `read_launch_outcome` は `SystemExit` を投げず、標準出力・標準エラーへ何も書かない。監視の結果ファイルがあれば `monitor` にその辞書、無ければ `None` を持ち、`detail` に監視の `detail`（無ければ読めなかった理由の 1 文）を持つ
- [ ] AC12: 理由の一覧と起動し直しの可否の表を持つのは `plugins/ndf/scripts/lib/monitor_outcome.py` だけである。`plugins/ndf/skills/` の下に `usage_limit` を含む条件分岐（`== "usage_limit"` / `in (...)` の形）は無い

cross-review が値を読む（#619）:

- [ ] AC13: `read-result` は使える結果が無いとき、`no_result_reason` に AC9 の `reason` を記録する。監視の結果ファイルがあれば `rounds[-1].<担当>.monitor_detail` に監視の `detail` を残す。終了コードは変更前と同じ（`unparsable` は 3、それ以外は 1）である
- [ ] AC14: `judge` は結果なしの担当があると、標準出力に `NO_RESULT_REASONS='<担当>=<理由> ...'` の 1 行を出す
- [ ] AC15: 結果なしの担当の理由に `relaunch_same_agent` が偽のもの（`usage_limit`）があるとき、`judge` は起動し直さない。`final=error` として終了コード 1 で終わり、標準エラーに担当・理由・`monitor_detail` が出る
- [ ] AC16: 理由がすべて起動し直してよいものなら、`judge` は変更前と同じく終了コード 7 で `RELAUNCH_AGENTS` を返し、2 度目の結果なしで `final=error` になる。終了コード 0 / 2 / 8 の枝は変更前と同じである
- [ ] AC17: `state.py report` のラウンド表で、結果なしの担当は `kiro=NO_RESULT(usage_limit)` の形で出る
- [ ] AC18: `docs/01-state-and-review.md` の理由の表に 10 個の理由が載る。10 個は AC1 の 9 語から `ok` を除いた 8 語に、`no_verdict` / `not_posted` を足したものである。`docs/03-review-output.md` の「monitor.py が誤って kill する場合の手順」に、上限に当たった場合の見分け方が載る。見分け方は `reason` と `monitor-outcomes.jsonl` の読み方である

止めた後に書かせない（#584）:

- [ ] AC19: `launch-cli.sh` で起動した CLI のプロセスは、自分の pid をプロセスグループの番号に持つ
- [ ] AC20: 3 秒後に子プロセスが結果ファイルを書く CLI を監視の上限で止めると、4 秒待っても結果ファイルが無い
- [ ] AC21: 対象の pid がプロセスグループの先頭でないとき、監視はその pid だけを止め、監視自身のプロセスグループへシグナルを送らない

退行しないこと:

- [ ] AC22: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る。既存の `test_monitor_*.py` と `test_monitor_outcome_unit.py` は変更せずに通る
- [ ] AC23: 「検証手段」の配布物の同期と定義の検査の 3 つのコマンドが、終了コード 0 で終わる
- [ ] AC24: cross-review の `SKILL.md` の骨組みの行は変えない。骨組みとは、レビューの起動 → 待ち → `read-result` → `judge` → 7 で起動し直し → 8 で `flush` の並びである。判定の終了コードの分岐が増えない

## 未決

1 件。決めるのは G4 の設計である。

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `gitfacts.read_result` を共通の関数の薄い包みとして残すか、呼び出し側が共通の関数を直接呼ぶか | G4（#728）の設計 | G4 の設計 Pull Request |

## 既存の受け入れ条件との対応

既存の設計（PR #666）の P3 の受け入れ条件を、この文書のどこが引き継ぐかを示す。

| 既存 | この文書 | 変わったこと |
| --- | --- | --- |
| AC50 | AC2 | 同じ |
| AC51 | AC3 | 同じ |
| AC52 | AC4 | 同じ |
| AC53 | AC5 | 結果ファイルがあれば `ok` になることを明記 |
| AC54 | AC6 | 同じ |
| AC55 | AC9 + AC13 | 理由の表を `state.py` の規則から共通層の関数の規則へ移した。`unparsable` を共通の語彙に入れた |
| AC56 | AC14 | 同じ |
| AC57 | AC15 | 判定の条件を「`usage_limit` を含む」から「起動し直しの可否が偽」へ変えた。値は同じ |
| AC58 | AC16 | 同じ |
| AC59 | AC17 | 同じ |
| AC60〜AC62 | AC19〜AC21 | 同じ |
| AC63〜AC67 | — | #730（G5）へ |
| AC68 | AC18 | 理由が 10 個になった（`unparsable` を足し、`no_verdict` / `not_posted` を残す） |
| AC69 | AC18 | 同じ |
| — | AC1、AC8、AC10〜AC12 | 新設（結末を 1 つの値として読む契約） |
| — | AC24 | 新設（骨組みを変えない） |

## 依頼（原文）

3 つの issue の本文を、書かれたままの形で引く。

### #729（根本原因の親）

> **担当 1 回の起動の結末（結果ファイルの有無と、監視が打ち切った理由）を読み、起動し直してよいかを返す契約。**
>
> - 結末の語彙は `plugins/ndf/scripts/lib/monitor_outcome.py`、早期の致命の検知は `monitor.py` の `EARLY_ERROR_FATAL` にある
> - cross-review の `state.py`（`_read_review_result_file`）も cross-refactoring の `refactor_lib/gitfacts.py`（`read_result`）も語彙を読まず、結果ファイルの有無だけで判断する（Skill 側で `monitor_outcome` を読むコードは 0 件）
> - `EARLY_ERROR_FATAL` は `Monthly request limit reached` や `"api_error_status":429` の行に一致しない
>
> `read_result` が `die` で進行を決める向きは #728 が持つ。
>
> ## 採る手
>
> - 移動（`move_responsibility`）: 結果なしの判断を、各 Skill の結果ファイルの読み取りから共通層の結末へ移す
> - 新設: 利用上限（`usage_limit`）の語彙と検知の文言
>
> ## 完了条件
>
> - 両 Skill が共通層の結末を読み、利用上限を理由として出し、同じラウンドでの起動し直しを止める
> - 利用上限の実際の出力（上の 2 形式）を検知することを検査が確かめる
> - 各子 issue の再現手順を実行し、現象が出ないことを確かめる

### #619

> **監視の結果（状態と `detail`）を担当ごとにファイルへ残し、`read-result` が `NO_RESULT` の理由に使う。**
> 理由は、監視の上限（timeout）・CLI 自身の上限（cli_timeout）・利用上限（usage_limit）・早期エラー（early_error）・未投稿（not_posted）・結果ファイル無し（missing）を区別する。
>
> - 利用上限は起動し直しても解けないため、理由が `usage_limit` のときは起動し直さずに止めて報告する判断もここに置ける（cross-refactoring の #647 と共通）

### #584

> 移動（`move_responsibility`）。停止の単位を pid からプロセスグループへ移す。`launch-cli.sh` は CLI を新しいプロセスグループとして起動し、`_kill_pid` はそのグループへ SIGTERM / SIGKILL を送る。
>
> 止めた理由を読む側（結果なしの理由の語彙）は、担当 1 回の起動の結末を共通の語彙で読む #729 が持つ。

## この文書の位置づけ

この文書は「何を満たすか」だけを扱う。設計は [issue-729-619-584-design.md](issue-729-619-584-design.md) にある。

**既存の要求 [issue-662-598-537-619-584-583-requirements.md](issue-662-598-537-619-584-583-requirements.md) の P3 を置き換える。** 置き換える受け入れ条件は AC50〜AC62 と AC68〜AC69 で、対応は「既存の受け入れ条件との対応」にある。P1（#662）と P2（#598 #537）は v10.13.0 で配布済みで、この文書は触らない。#583 は #730 の設計が持つ。
