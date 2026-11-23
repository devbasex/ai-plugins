# レーンの生死: 何もしていない conductor を止まりとして知らせる（#4036）

レーンの生死は conductor のラッパーのプロセスが動いているかだけで決まる。そのため、背景の作業を持たないまま来ない通知を待つ conductor が「動いている」と判定され、約 3 時間が誰にも気づかれなかった。ラッパーの状態ファイルから会話の記録をたどって最後の応答の時刻を測り、背景の作業の件数を Stop の hook が書き残す形にして、両方がそろったときだけ止まりとする。閾値は `contracts/limits.json` に置く。採らなかった案は「決定の記録」にある。

## 今の姿

何が起きたか・目的・受け入れ条件は要求仕様（[issue-4036-requirements.md](./issue-4036-requirements.md)、正は #4036 の本文）にある。この文書は「どう解くか」と「なぜその解き方か」だけを書く。

```mermaid
flowchart TB
    J["liveness.judge<br/>（progress/lane/liveness.py:92-111）"]
    J --> Q1{"実行中の行が無い"} -->|はい| IDLE["idle"]
    Q1 -->|いいえ| Q2{"回答の後の next 待ち"} -->|はい| RES["resuming"]
    Q2 -->|いいえ| Q3{"スプリントの待ちが gate"} -->|はい| WA["waiting_answer"]
    Q3 -->|いいえ| Q4{"ラッパーを読めない"} -->|はい| UNK["unknown"]
    Q4 -->|いいえ| Q5{"ラッパーが stalled"} -->|はい| ST["stalled（止まり）"]
    Q5 -->|いいえ| Q6{"ラッパーが動いている"} -->|はい| AL["alive ← #4036 はここ"]
    Q6 -->|いいえ| Q7{"スプリントが working"} -->|はい| WK["working"]
    Q7 -->|いいえ| EN["ended（止まり）"]
```

- 判定は 8 つの値（`idle`・`resuming`・`waiting_answer`・`unknown`・`stalled`・`alive`・`working`・`ended`）を返す。止まりとして知らせるのは `stalled` と `ended` の 2 つだけである（`liveness.py:38-49`）
- 順 6 の `alive` は `ndf relay ls` の行の `running` だけを見る（`liveness.py:87-89`）。conductor が何をしているかは見ない
- 材料を読むインターフェースは `Probes` の 4 つ（`wrapper_name`・`wrappers`・`sprint`・`account_ok`。`liveness.py:124-133`）で、実装は `progress/plan/run.py:120-130` にある
- `liveness.py` は `contracts/limits.json` を 1 つも読んでいない。判定はすべて状態の値だけで決まる
- `lane_stalled` のリクエストの `moved`（最後に動いた時刻）は、ステートマシンの事象の時刻である（`progress/plan/run.py:171-181`）。**conductor の会話の動きは見ていない**ため、工程が進まない間はこの値も更新されない

会話の記録と背景の作業を読む材料は、別の場所に既にある。

| 材料 | 場所 | 読めるもの |
| --- | --- | --- |
| 会話の記録のパス | `domains/relay/observe.py:55-57`（`transcript_path(environ, cwd, session_id)`） | 記録のファイルのパス |
| 最後の応答の時刻 | `domains/relay/observe.py:196-208`（`turn_done(path, size)`） | 最後の記録が終わった応答なら `(True, その時刻)`、途中なら `(False, None)` |
| `session_id` と `cwd` | ラッパーの状態ファイル `wrapper.json`（`domains/relay/registry.py:28`・`wrapper.py:950-968`） | 会話の記録のパスを組む材料 |
| 背景の作業の件数 | Stop の hook の入力の `background_tasks`（`domains/hooks/context.py:609-611`、`guards/relay_mark.py:22`） | conductor が応答を終えた時点の背景の作業の件数 |

`ndf relay ls` の行（`domains/relay/hub.py:498-510`）には `session_id` と `cwd` が載っていない。

## 文脈と制約

- 判定（`liveness.judge`）は DB もプロセスも読まない純粋な関数である。材料は `Probes` の呼び出しが集める。この形は変えない
- 背景の作業の有無は、Stop の hook の入力でしか読めない。計画の耐久ワークフローは hook の入力を受け取らない
- `wrapper.json` の `at` は「状態が変わった時刻」で、同じ状態では書き直さない（`wrapper.py:952-955`）。最後に動いた時刻としては使えない
- 承認ゲート・質問の回答を待つ行は、今の判定の順 2・順 3 が先に拾う。新しい条件はその後ろに置けば、今までどおり止まりにしない
- 時間で区切った試験は行わない。閾値の判定は、時刻を与えるテストで確かめる
- 閾値は `contracts/limits.json` に置き、コードに同じ値を書かない
- hook から見たラッパーの保存先は環境変数 `NDF_RELAY_DIR` で渡る。`domains/relay/wrapper.py:740` が `signals.launch_env(self.dir, …)` で渡し、`self.dir` は `registry.wrapper_dir(...)` である（`wrapper.py:278`）。`activity.json` はこの保存先へ書く
- `ndf lanes status` も判定を同じ `run.probes` を通して呼ぶ（`progress/plan/cli.py:103`）ので、活動を読む項目を 1 か所に足せば両方に当たる

## 解き方の全体像

```mermaid
flowchart LR
    SH["Stop の hook<br/>relay-marker guard"] -->|"背景の作業の件数を書く"| AF[("&lt;ラッパーの保存先&gt;/activity.json")]
    WJ[("wrapper.json<br/>session_id・cwd")] --> PR["Probes.activity（新設）"]
    AF --> PR
    TR[("会話の記録<br/>&lt;session_id&gt;.jsonl")] -->|"observe.turn_done"| PR
    PR --> J["liveness.judge"]
    L[("contracts/limits.json<br/>parallel-run.idle")] --> J
    J -->|"応答が終わり・背景 0 件・閾値を過ぎた"| S["idle_conductor（止まり）"]
    S --> RQ["lane_stalled のリクエスト<br/>moved・background を載せる"]
```

Stop の hook が、conductor が応答を終えた時点の背景の作業の件数をラッパーの保存先へ書く。生死の判定は、ラッパーの状態ファイルから `session_id` と `cwd` を取って会話の記録をたどり、最後の応答が終わっているかとその時刻を読む。応答が終わっていて、背景の作業が 0 件で、最後の応答から閾値を過ぎていれば、新しい判定 `idle_conductor` を返して止まりとする。`lane_stalled` のリクエストには、最後に動いた時刻と背景の作業が 0 件であることを載せる。

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `activity.json`（新設。ラッパーの保存先） | conductor が応答を終えた時点の、背景の作業の件数と書いた時刻 |
| `relay-marker` guard（`domains/hooks/guards/relay_mark.py`。変更） | Stop のたびに `activity.json` を書く |
| `Probes.activity`（新設。`progress/lane/liveness.py` のインターフェースと `progress/plan/run.py` の実装） | レーンの名前から、最後の応答が終わっているか・その時刻・背景の作業の件数を返す |
| `liveness.judge`（変更） | 順 6 の前に、新しい条件を置いて `idle_conductor` を返す |
| `contracts/limits.json`（変更） | `parallel-run.idle` の閾値 |
| `_stall_report`（`progress/plan/run_decide.py:463-468`。変更） | `lane_stalled` のリクエストに `moved` と `background` を載せる |

```mermaid
flowchart TB
    subgraph hooks["hooks（conductor の中）"]
        RM["relay_mark guard"]
    end
    subgraph relay["relay（ラッパーの保存先）"]
        WJ["wrapper.json"]
        AJ["activity.json（新設）"]
    end
    subgraph lane["progress/lane"]
        PB["Probes.activity（新設）"]
        LJ["liveness.judge"]
    end
    subgraph plan["progress/plan"]
        RUN["run.probes"]
        RD["run_decide._stall_report"]
    end
    RM --> AJ
    AJ --> PB
    WJ --> PB
    PB --> LJ
    RUN --> PB
    LJ --> RD
```

## データ構造

`<ラッパーの保存先>/activity.json`:

| キー | 型 | 中身 |
| --- | --- | --- |
| `at` | string | 書いた時刻（ISO 8601）。conductor が応答を終えた時刻に当たる |
| `background` | integer | その時点の背景の作業の件数（hook の入力の `background_tasks` の数） |

`contracts/limits.json` に足す方針:

| 名前 | 値 | 説明 |
| --- | --- | --- |
| `parallel-run.idle` | `seconds: 600` | conductor が応答を終えて背景の作業を持たないまま、何もしていないとみなすまでの秒 |

`judge` は時刻を引数（`now`）で受け取る。判定を純粋な関数のままにし、閾値の境目を時刻を与えるテストで確かめるためである。

`Probes.activity(lane) -> Activity | None` が返す値:

| キー | 型 | 中身 |
| --- | --- | --- |
| `turn_done` | bool | 会話の記録の最後が、終わった応答か |
| `moved` | float / None | 最後の応答の時刻（UNIX 時刻）。読めなければ `None` |
| `background` | integer / None | `activity.json` の件数。無ければ `None` |

読めない（会話の記録が無い・`session_id` が取れない・`activity.json` が無い）ときは `None` を返し、判定は今までどおり `alive` にする。

## 入出力のコントラクト

`liveness` の返す値に `idle_conductor` を 1 つ足し、`STALL_KINDS` に入れる。理由の文は「conductor が応答を終えてから \<分\> 分、背景の作業が無いまま動いていない」にする。

`lane_stalled` のリクエストの中身に 2 つ足す。

| キー | 型 | 中身 |
| --- | --- | --- |
| `moved` | string / null | 今までどおり。`idle_conductor` のときは conductor の最後の応答の時刻を入れる |
| `background` | integer / null | 背景の作業の件数。`idle_conductor` のときは 0 |

`ndf relay ls` の行に `session_id` と `cwd` を足す（会話の記録のパスを組むため）。既存のキーは変えない。

## 処理の流れ

```mermaid
sequenceDiagram
    participant C as conductor
    participant H as Stop の hook
    participant A as activity.json
    participant D as 耐久ワークフロー
    participant J as liveness.judge
    C->>H: 応答を終える
    H->>A: at と background を書く
    loop 読み直しの間隔
        D->>D: ndf relay ls（session_id・cwd つき）
        D->>A: activity.json を読む
        D->>D: 会話の記録を turn_done で読む
        D->>J: 行・スプリントの待ち・ラッパー・活動
        alt 応答が終わり・background が 0・閾値を過ぎた
            J-->>D: idle_conductor（止まり）
            D->>D: lane_stalled のリクエストを書く
        else ほか
            J-->>D: 今までどおりの判定
        end
    end
```

判定の順序は、今の順 5（ラッパーが `stalled`）と順 6（`alive`）の間に新しい条件を置く。順 2（回答の後の `next` 待ち）と順 3（スプリントの待ちが `gate`）はその前にあるので、承認ゲートと質問の回答を待つ行は今までどおり止まりにならない。

## 決定の記録

### 決定 1: 最後の動きは、会話の記録の最後の応答の時刻で測る

- **質問**: conductor の「最後の動き」を何で測るか
- **選んだ案**: ラッパーの状態ファイルの `session_id` と `cwd` から会話の記録のパスを組み、`observe.turn_done` が返す最後の応答の時刻を使う
- 採らなかった案:
  - ラッパーの状態ファイルの `at`（同じ状態では書き直さないため、動いていても古いままになる）
  - ステートマシンの事象の時刻（今の `moved` がこれで、工程が進まない間は更新されない。まさに #4036 が見落とした値）
  - 画面（`tmux capture-pane`）を読む（コードに読む処理が無く、サジェストの文字を動きと読む危険がある）
- **代償**: 会話の記録のファイルを読むため、読み直しのたびに入出力が増える。読む大きさは `limits.json` の `hook.transcript-tail` に合わせる
- 作り方: 自前で作る（既存の `observe.turn_done` を呼ぶ）
- 比べた候補: STD1・STD2・PRD1・PRD2
- 根拠: Value 5「決められた範囲の資源はできるだけ使われる。…待つ間は別の作業を進めるか、待っていることをオーガナイザーや conductor へ伝え、伝えられた側が別のレーンか supervisor を起動する」（MVV バージョン 1）

### 決定 2: 背景の作業の有無は、Stop の hook が書き残す

- **質問**: 背景の作業（シェル・サブエージェント）の有無を、耐久ワークフローからどう知るか
- **選んだ案**: Stop の hook（`relay-marker` guard）が、入力の `background_tasks` の件数をラッパーの保存先の `activity.json` へ書き、判定がそれを読む
- 採らなかった案:
  - 会話の記録から読む（`observe.tool_pending` は「Tool の実行の途中か」を返すだけで、背景の作業が残っているかとは別である）
  - conductor の子プロセスを数える（サブエージェントは同じプロセスの中で動くので、プロセスの数に現れない）
  - 背景の作業を見ずに、時間だけで止まりとする（受け入れ条件 2 を満たさず、長い cross-review を回している間の行まで止まりとして知らせる）
- **代償**: hook が呼ばれない実行（中継を使わない conductor）では `activity.json` が無く、判定は今までどおり `alive` になる
- 作り方: 自前で作る（既存の guard に書き込みを足す）
- 比べた候補: PRD1・PRD2・PRD3
- 根拠: Value 3「判断の要らない手順は仕組みが進める」（MVV バージョン 1）

### 決定 3: 閾値は 10 分とし、`contracts/limits.json` に置く

- **質問**: 何もしていないとみなすまでの時間をいくつにするか
- **選んだ案**: `parallel-run.idle` を `seconds: 600`（10 分）にする。出力が止まったことを直接測る仕組みの一次情報は CircleCI の `no_output_timeout` の既定（10 分）だけで（PRD3）、同じ相場に合わせる
- 採らなかった案:
  - 15 分・30 分（`relay.stall` と同じ）・60 分（`parallel-repo.stall` と同じ）（背景の作業が 0 件であることを条件にしているので、長く待つ理由が無い。lracct では 60 分にすると 3 時間のうち最初の 1 時間が失われる）
  - 5 分（conductor が応答を終えてから次の通知が届くまでの間に、正常な行を止まりとして知らせうる。一次情報の相場より短い）
  - 既定を持たず、設定したときだけ働かせる（systemd の `WatchdogSec=` の形（STD2）。知らせが要るのは設定を忘れたレーンなので、既定を置く）
- **代償**: 値の妥当さは、知らせの実績を見るまで分からない。合わなければ設定の 1 か所を直す
- 根拠: Value 5「速く届く」・Value 2「役に立たない繰り返しに費用を使わない」（MVV バージョン 1）

### 決定 4: 新しい判定の値を足し、既存の `stalled` に混ぜない

- **質問**: 新しい止まりを、既存の `stalled` として返すか、新しい値にするか
- **選んだ案**: `idle_conductor` を足し、`STALL_KINDS` に入れる
- 採らなかった案: 既存の `stalled`（ラッパーが止まったときの判定）として返す（知らせを受けたオーガナイザーが、ラッパーの異常と conductor の待ちを見分けられない）
- **代償**: 判定の値が 8 から 9 に増え、値ごとに分岐する既存の規則を見直す対象が増える
- 根拠: Value 1「承認の場には、決めたこと・まだ確かめていないこと・MVV に照らした判定が中身の分かる語でそろって届く」（MVV バージョン 1）

## 影響と危険

- **働く範囲**: `plugins/ndf` のリリース対象。並列の計画を使うリポジトリで働く。単独の実行（レーンを持たない conductor）には当たらない
- **プロジェクトごとに違うもの**: 閾値だけで、`contracts/limits.json` の 1 行で受ける。引数では受けない
- **当たるモード**: すべてのモード（判定はモードを見ない）

### 代表のリポジトリでの振る舞い

| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |
| --- | --- | --- | --- |
| Web サービス | 並列の計画でレーンを流す | 何もしていない conductor が 10 分で `lane_stalled` に出る | 無し |
| モバイルアプリ | 同上 | 同上 | 無し |
| 業務文書だけ | 並列の計画を使うかはリポジトリによる。使わなければ判定そのものが働かない | 使うときは同じ。使わなければ変わらない | 無し |

### ai-plugins だけの事情

- 無し（閾値は `contracts/limits.json` の値で、リポジトリの運用の値を持たない）

危険:

- 判定の値を 8 から 9 に増やすため、値ごとに分岐する既存の規則が新しい値に当てはまるかを 1 つずつ見る。当てはまらない規則は実装で変える。集める経路は次の 2 つである
  - `liveness` の値の名前で `plugins/ndf` を検索する
  - 値を名指さずすべての判定に当たる規則（`STALL_KINDS` を読む側、知らせのキーを組む `notice_key`、リクエストを書く `write_report`）を読む
- `activity.json` を書くのは Stop の hook で、hook が落ちても conductor の進行は止めない（今の guard と同じ。書けなければ判定は `alive` のままになる）
- `relay_mark.py` と `hub.py` は中継の本流で、並行するほかのスプリントも触りうる

## 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 活動 | conductor が最後に応答を終えた時刻と、その時点の背景の作業の件数の組 | 追加 |

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1（止まりになる） | 応答が終わり・背景 0 件・最後の応答から閾値を過ぎた実行中の行が `idle_conductor` になり、`lane_stalled` のリクエストになる | 閾値の比較を落とすと落ちる |
| 2（背景の作業がある行） | 背景の作業が 1 件以上の行は、閾値を過ぎても `alive` のまま | 件数を見ずに時刻だけで判定すると落ちる |
| 3（回答を待つ行） | スプリントの待ちが `gate` の行と、回答の後の `next` 待ちの行は、閾値を過ぎても止まりにならない | 新しい条件を順 2・順 3 より前に置くと落ちる |
| 4（リクエストの中身） | `idle_conductor` の `lane_stalled` に、最後に動いた時刻と背景の作業が 0 件であることが載る | `background` を載せずに書くと落ちる |
| 5（閾値を設定で変える） | `contracts/limits.json` の `parallel-run.idle` を変えると、判定の境目が変わる。コードに秒の値を書かない | 判定のコードに 600 を書くと落ちる |
| 決定 1（読めないとき） | 会話の記録を読めない・`session_id` が取れない行は、今までどおり `alive` になる | 読めないときに止まりとすると落ちる |
| 決定 2（hook が書く） | Stop の hook が、入力の `background_tasks` の件数と時刻を `activity.json` へ書く。書けなくても conductor の進行を止めない | 書き込みの失敗で guard が例外を上げると落ちる |

詳細設計は [issue-4036-design-detail.md](./issue-4036-design-detail.md) にある（新設する関数が 2 つ以上のため別ファイル）。

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #4036 | 実装する | — | `plugins/ndf/contracts/limits.json`、`plugins/ndf/src/ndf/progress/lane/liveness.py`、`plugins/ndf/src/ndf/progress/plan/run.py`、`plugins/ndf/src/ndf/progress/plan/run_decide.py`、`plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py`、`plugins/ndf/src/ndf/domains/relay/registry.py`、`plugins/ndf/src/ndf/domains/relay/hub.py`、`plugins/ndf/src/ndf/progress/plan/cli.py`、`plugins/ndf/tests/progress/test_lane_liveness.py`、`plugins/ndf/tests/domains/hooks/test_relay_mark.py`（新設）、`plugins/ndf/tests/progress/test_plan_run_stall.py`（新設） |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 閾値の妥当さ | 10 分が短すぎて正常な行を止まりと知らせるか、長すぎて気づきが遅れるかは、知らせの実績を見るまで分からない。合わなければ `contracts/limits.json` の 1 行を直す |
| Stop の hook が呼ばれる頻度 | conductor が応答を終えるたびに呼ばれると見ているが、背景の作業を持ったまま終えた応答でも呼ばれるかは実測していない。実装で 1 回確かめる |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 「生きている」と「仕事をしている」を分けて測る形に事実上の標準があるか（決定 1・決定 4） |
| 主要な製品 | 調べる | 出力が止まった実行をどう打ち切るか、閾値の相場はいくつか（決定 3） |
| ライブラリとサービス | 調べない | 判定は既存の `liveness.judge` に条件を 1 つ足すだけで、外から持ち込む部品が無い |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | Kubernetes の Probe（liveness / readiness / startup の分離） | 「Liveness probes determine when to restart a container.」「Readiness probes determine when a container is ready to accept traffic.」。生きているかと仕事を受けられるかを別の probe で測り、失敗時の処置も再起動と切り離しで分ける。`periodSeconds` の既定は 10 秒 | 決定 4。既存の `stalled`（ラッパーの異常）と新しい `idle_conductor`（何もしていない）を別の値にする根拠 | https://kubernetes.io/docs/concepts/workloads/pods/probes/ （2026-11-23） |
| STD2 | systemd の `WatchdogSec=` | サービス自身が `WATCHDOG=1` を打ち続け、間隔が設定を超えたら失敗とする。既定は 0 で無効 | 決定 1・決定 3。プロセスが居ることでなく「ハートビートが来続けること」を測る形の実績。既定を無効にせず置く理由は決定 3 にある | https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html （2026-11-23） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | Temporal（Activity の heartbeat） | 「Each ping informs the Temporal Service that the Activity Execution is making progress and the Worker has not crashed.」。閾値（Heartbeat Timeout）に既定は無く、未設定は無効。送信の絞りは 30 秒・60 秒 | 決定 1。進んでいるかをハートビートで測る形。既定を持たない形は採らない（決定 3） | https://docs.temporal.io/encyclopedia/detecting-activity-failures （2026-11-23） |
| PRD2 | GitHub Actions・Buildkite | 時間の上限だけを持ち、出力の停止は測らない。GitHub Actions のジョブの `timeout-minutes` の既定は 360 分 | 決定 1。時間の上限だけでは、長い作業をしている行と何もしていない行を分けられない | https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax （2026-11-23）、https://buildkite.com/docs/pipelines/configure/build-timeouts （2026-11-23） |
| PRD3 | CircleCI（`no_output_timeout`） | 「Elapsed time the command can run without output.」で「The default is 10 minutes」。出力が止まったことを直接測る | 決定 3。閾値を 10 分にする根拠。出力の停止を測る仕組みの一次情報はこの 1 件だけだった | https://circleci.com/docs/reference/configuration-reference/ （2026-11-23） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| OpenTelemetry の heartbeat の標準 | 試していない | 取得の試みの上限（30 回）に達したため着手していない |
| GitHub Actions に「出力が止まったジョブを打ち切る」仕組みがあるか | 制限のページと workflow-syntax を 4 回取得 | 取得した範囲（237,191 文字のうち 200,000 文字）に記載が無かった。無いとは断定しない。理由: 上限 |
| CircleCI の `no_output_timeout` 超過時の処置の原文 | 設定の一覧のページを取得 | 既定の 10 分までは一次情報で確認したが、超過時に何が起きるかはサポート記事（二次）でしか確認できていない。理由: 原文に無い |
| Buildkite の「Default Command Step Timeout」の出荷時の値 | コマンドステップと打ち切りのページを 2 回取得 | 設定項目の存在は確認したが、出荷時の分数の記載が見つからなかった。理由: 原文に無い |

調べた量: 候補 5 件・取得の試み 10 回。
