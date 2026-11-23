# レーンの生死: 背景の作業を持たないまま何もしていない conductor を止まりとして知らせる

## 概要

**レーンの生死の判定に、「conductor が応答を終え、背景の作業が 1 つも残っていないまま 10 分が過ぎた」を止まりとして足した。** 判定の種類は `idle_conductor` で、止まりとして知らせる 3 つのうちの 1 つである。

例: 並列の計画 p12 のレーン lracct で、conductor が 04:26 に「Pull Request の CI と cross-review がそろうのを待つ」と書いて応答を終えた。待っていた背景の作業は 04:50 までに終わったが、完了の通知が conductor に届かなかった。耐久ワークフローは 05:00 の周で、conductor の会話の記録の最後の応答が終わっていること・終わった背景の作業を除くと残りが 0 件であること・最後に動いた時刻（04:50）から 10 分が過ぎたことを読み、計画のステートマシンへ `lane_stalled` のリクエストを開く。リクエストの中身には最後に動いた時刻（`moved`）と背景の作業の数（`background: 0`）が載り、オーガナイザーは `ndf lanes wait <計画>` でそれを受け取る。

**この判定が無かったときは、conductor のラッパーのプロセスが動いていれば `alive` だった。** lracct では 04:26 から 07:20 までの約 3 時間、人が画面を見るまで誰も気づかなかった。

止まった conductor を自動で起こすことはしない。まず知らせるだけである。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 活動 | conductor の最後の応答が終わっているか・最後に動いた時刻・まだ走っているとみなす背景の作業の数の 3 つの組（`liveness.Activity`） |
| 背景の作業 | conductor が応答を終えた後も動き続ける作業。Stop の hook の入力の `background_tasks` の要素で、`type` は `shell`（Bash の Tool の背景の呼び出し）・`subagent`（背景のサブエージェント）・`monitor` などがある |
| 周 | 耐久ワークフローが計画とレーンのステートマシンを読み直す 1 回（上限の設定 `parallel-run.poll` の秒ごと） |
| ラッパー | レーンの conductor（`claude`）を擬似端末で起動して見守るプロセス（`ndf relay`）。状態を `wrapper.json` に書く |

「レーン」「オーガナイザー」「conductor」「耐久ワークフロー」の定義は [docs/ndf-parallel-model.md](../ndf-parallel-model.md) が持つ。

## 背景

レーンの生死の判定（`ndf.progress.lane.liveness`）は、実行中の行を持つレーンが動いているか・正常に待っているか・止まったかを 1 つに決める。判定が止まりを返すと、耐久ワークフローが計画のステートマシンへ `lane_stalled` のリクエストを開き、オーガナイザーが手を打つきっかけになる。

**ラッパーの一覧とスプリントの待ちだけでは、何もしていない conductor を見分けられない。** ラッパーのプロセスが動いていれば `alive` になるため、conductor が背景の作業を持たないまま来ない通知を待っていても「動いている」と読まれる。`lane_stalled` のリクエストに載る「最後に動いた時刻」も、ステートマシンの記録の時刻は工程が進まない間は更新されない。そこで conductor の活動を材料に足した。

**最後の動きは、conductor の会話の記録の最後の応答の時刻で測る。** ラッパーの状態ファイルの時刻は状態が変わらない限り書き直されず、ステートマシンの記録の時刻は工程が進まない間は動かない。画面（`tmux capture-pane`）は、入力欄のサジェストの文字を人の入力と読み違える。

**背景の作業は、Stop の hook が応答を終えた時点の一覧を書き残し、読む側がそれぞれ終わったかを確かめる。** 件数だけを書き残すと lracct の現象を捕まえられない。応答を終えた時点では待っていた作業が走っており件数は 1 で、その作業が終わっても完了の通知が届かなければ次の Stop は来ないため、件数は 1 のまま残る。

**閾値は 10 分とした。** 出力が止まったことを直接測る仕組みの一次情報は CircleCI の `no_output_timeout` の既定（10 分）で、同じ相場に合わせた。背景の作業が 0 件であることを条件にしているので、それより長く待つ理由が無い。5 分では、conductor が応答を終えてから人やオーガナイザーの次の入力が届くまでの数分を止まりと知らせうる。

**新しい判定は既存の `stalled` に混ぜず、別の値にした。** `stalled` はラッパー自身が止まったこと（認証の失敗で `/goal` が外れた など）を表す。混ぜると、知らせを受けたオーガナイザーがラッパーの異常と conductor の待ちを見分けられない。

## 対象範囲

含む:

- 生死の判定に `idle_conductor` を足すこと（`ndf.progress.lane.liveness`）と、その材料を読む経路
- Stop の hook（`relay-marker` guard）が書く活動の記録（`activity.json`）
- `ndf relay ls --activity` が行に載せる活動（`turn_done`・`moved`・`background`）と、ラッパーが `wrapper.json` に書く会話の記録の場所
- `lane_stalled` のリクエストに載る最後に動いた時刻と背景の作業の数
- 閾値の設定（`plugins/ndf/contracts/limits.json` の `parallel-run.idle`）

含まない:

- 止まった conductor を自動で起こすこと
- 入力待ちのダイアログで止まったレーンを知らせること
- ほかの 8 つの判定の条件（この変更で変えていない）

## 仕様

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| `plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py` | Stop のたびに、中継の下なら活動の記録を書く。どの早い戻りより前に書く |
| `plugins/ndf/src/ndf/domains/hooks/context.py` | `HookContext.last_reply_id`。会話の記録の末尾から選んだ最後の `assistant` の記録の `uuid` |
| `plugins/ndf/src/ndf/domains/relay/signals.py` | 活動の記録の読み書き（`write_activity`・`read_activity`・`ACTIVITY_FILE`）。Stop の hook が読めるモジュールに置く |
| `plugins/ndf/src/ndf/domains/relay/observe.py` | 活動の観測。`conductor_activity`（手順の本体）・`reply_id`・`subagent_transcript_path`・`process_children`・`live_shells`・`Look`・`NO_ACTIVITY` |
| `plugins/ndf/src/ndf/domains/relay/hub.py` | `listing(ctx, activity=False)`。行に `session_id`・`cwd`・`pid` を載せ、`--activity` のときは動いている行に活動も載せる |
| `plugins/ndf/src/ndf/domains/relay/wrapper.py` | 会話の記録の場所を、子に渡した環境変数で組んで `wrapper.json` の `transcript` に書く（`_recorded_transcript`） |
| `plugins/ndf/src/ndf/progress/lane/liveness.py` | 判定。`Activity`・`idle_after`・`IDLE_CONDUCTOR`・`STALL_KINDS`・`Probes.activity`・`judge`・`judge_lanes`・`Liveness` の `moved_at`・`background` |
| `plugins/ndf/src/ndf/progress/plan/run.py` | `lane_activity`（一覧の行の値を `Activity` へコピーする）・`probes`（活動つきの一覧に結ぶ）・`_judge`・`_moved` |
| `plugins/ndf/src/ndf/progress/plan/run_decide.py` | `Stall`・`_stall_report`。最後に動いた時刻を ISO 8601 へ直し、背景の作業の数をリクエストの中身に載せる |
| `plugins/ndf/contracts/limits.json` | 閾値 `parallel-run.idle`（600 秒）の唯一の保存先 |

活動を観測する処理は `ndf.domains.relay` に置き、`ndf relay ls --activity` の行を通して `ndf.progress` へ渡す。層の規則（`plugins/ndf/src/ndf/checks/rules.json` の `layers`）では `progress` から `domains` を読めず、子の表（`checks/children.json`）でも `ndf.progress.plan` の先に `ndf.domains.relay` は無い。`progress` は以前からラッパーの一覧を `ndf relay ls` の出力で読んでいるので、同じ経路に載せた。

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 止まりとして知らせる判定は `stalled`・`ended`・`idle_conductor` の 3 つで、`STALL_KINDS` が唯一の保存先である。値を名指さずに `STALL_KINDS` で判定する側（`Liveness.stall`・`_judge`・`ndf lanes status` の警告）は、定数に足すだけで当たる | テストが落とす |
| 承認ゲートや質問の回答を待つ行と、回答が載った後に `next` を待つ行は、閾値を過ぎても止まりにならない | 新しい条件は判定の 6 番目にあり、2 番目（`resuming`）・3 番目（`waiting_answer`）が先に当たる。テストが落とす |
| 背景の作業が 1 件以上の行は、閾値を過ぎても `alive` である | テストが落とす |
| 活動を読めない行（会話の記録が無い・活動の記録が今の応答のものと確かめられない・シェルの生死を確かめられない）は、今までどおり `alive` である。止まりと知らせる側へ倒さない | テストが落とす |
| 閾値の秒は `contracts/limits.json` だけが持ち、判定のコードは方針の名前（`parallel-run.idle`）だけを持つ | テストが落とす |
| 判定は DB もプロセスもファイルも読まない純粋な関数で、時刻を引数（`now`）で受け取る | テストが落とす。時間で区切った試験は行わず、閾値の境目は時刻を与えるテストで確かめる |
| 活動の記録を書けなくても conductor の応答は止まらない | `write_activity` は例外を外へ出さずに偽を返し、hook はそれまでの判定を続ける |
| 活動を読むのは `ndf relay ls --activity` のときだけである。起動の許可（`machine.admitted`）など稼働の本数しか使わない一覧は活動を読まない | テストが落とす |

### 判定の順序

上から先に当たったものが判定になる。

| 順 | 条件 | 判定 |
| --- | --- | --- |
| 1 | 実行中の行が無い | `idle` |
| 2 | 回答が載った後に `next` が打たれていない | `resuming` |
| 3 | スプリントが承認ゲートか質問の回答を待っている | `waiting_answer` |
| 4 | ラッパーの一覧かスプリントの待ちを読めない | `unknown` |
| 5 | ラッパーが動いていて状態が `stalled` | `stalled`（止まり） |
| 6 | ラッパーが動いていて、応答が終わり・背景の作業が 0 件・最後に動いた時刻から `parallel-run.idle` の秒が過ぎた | `idle_conductor`（止まり） |
| 7 | ラッパーが動いている | `alive` |
| 8 | スプリントの工程を流す作業が動いている | `working` |
| 9 | ほか | `ended`（止まり） |

6 番目に当たるのは、活動（`Activity`）が読めて `turn_done` が真・`background` が 0・`moved` があり、`now - moved >= idle_after()` のときだけである。閾値ちょうどは止まり、1 秒手前は `alive` である。理由の文は「conductor が応答を終えてから \<分\> 分、背景の作業が無いまま動いていない」で、分は経過した秒から組む。

活動は、活動を見なければ `alive` と決まる行（`judge_lanes` が 7 番目まで進んだ行）だけ読む。ほかの行では読まない。

### 活動の読み取り（`observe.conductor_activity`）

ラッパーの一覧の行（`session_id`・`transcript`・`pid`）とラッパーの保存先から、`turn_done`・`moved`・`background` の 3 つを読む。どの失敗でも例外を上げず、読めない値を `null` にして返す。

1. 行に `session_id` と `transcript` が無ければ、3 つとも `null` で返す
2. `transcript`（ラッパーが `wrapper.json` に書いた絶対パス）の末尾を 1 回読み、最後の応答が終わっているか・その時刻・最後の `assistant` の記録の `uuid` を得る。読めなければ 3 つとも `null`。応答の途中か時刻が無ければ、活動の記録を読まずに `background` を `null` で返す
3. ラッパーの保存先の活動の記録（`activity.json`）を読む。無い・`session_id` が行と違う・`reply` が手順 2 の `uuid` と違う（どちらかが無いときも違うとみなす）・`tasks` が `null` のときは、`background` を `null` で返す
4. `tasks` のうち `type` が `subagent` の要素は、その会話の記録（`<会話の記録から .jsonl を除いたもの>/subagents/agent-<id>.jsonl`）の最後の応答が終わっていれば除き、終わった時刻を集める。記録を読めない・応答の途中の要素は走っているとみなす。`subagent` でも `shell` でもない `type`（`monitor` など）は走っているとみなす
5. シェルは一覧に頼らず、行の `pid` から `/proc` でラッパーの子の `claude` の子を引き、Bash の Tool のシェルを数える。一覧の `shell` より生きているシェルが少なければ、終わりを最初に確かめた時刻を集める。`pid` が無い・`/proc` を読めない・`claude` が見つからない・`cmdline` を読めないときは、一覧の `shell` の数によらず（一覧が空でも）`background` を `null` にする
6. 手順 2 の時刻と、手順 4・5 で集めた時刻のうち最も遅いものを `moved` にする。`background` は、手順 4 の走っているとみなす数と手順 5 の生きているシェルの数の和である

**今の応答の記録かは、時刻でなく応答の `uuid` で確かめる。** `activity.json` の `at` は秒で書かれるため、前の Stop と今の応答が同じ秒に入ると時刻では区別できず、前の Stop の空の一覧を今の応答のものとして使ってしまう。Stop の hook が書き残す `reply`（会話の記録の最後の `assistant` の記録の `uuid`）と、読む側が同じ会話の記録から同じ規則で選んだ `uuid` を比べる。

**最後に動いた時刻は、conductor の最後の応答だけでなく、終わったとみなして除いた背景の作業の時刻も含めた最も遅いものにする。** これがないと、conductor の最後の応答が 30 分前・背景のサブエージェントが 1 分前に終わったレーンを、完了の通知が conductor を起こす前に止まりと知らせてしまう。

**終わったシェルの時刻は、終わりを最初に確かめた時刻を使う。** プロセスが残らないので終わった時刻は分からない。出力のファイルの更新時刻は最後に出力した時刻で、出力の後に黙って動く作業では終わった時刻より古く、終わった直後のレーンを止まりと読む。最初に確かめた時刻は `shells-ended.json` に残し、次の周からはその時刻を読む（周ごとに読む時刻へ戻すと、無活動の時間が毎回 0 になって止まりにならない）。

**シェルは Bash の Tool の形で見分ける。** `claude` の直接の子で、`cmdline` の実行ファイルの名前が `bash`、引数に `/shell-snapshots/snapshot-` を含むものである。設定ディレクトリの名前（既定の `~/.claude` か、登録済みアカウントの `CLAUDE_CONFIG_DIR`）には依らない。応答が終わった後に生きている Bash の Tool のシェルは背景のシェルだけなので、この数を使うのは `turn_done` が真のときだけである。

### 読む量

| 読むもの | 1 周あたりの量 |
| --- | --- |
| conductor の会話の記録 | レーンごとに末尾 262,144 バイト（`limits.json` の `hook.transcript-tail` の `count`）。記録が伸びても読む量は増えない |
| 活動の記録 | レーンごとに 1 ファイル |
| サブエージェントの会話の記録 | 一覧の `subagent` の要素ごとに末尾を 1 回 |
| `/proc/<pid>/stat` | シェルの生死を確かめる行に届いた周で 1 度だけ全体を組み、全行で共有する（レーンの数を掛けない） |
| `shells-ended.json` | シェルが終わっている行だけ |

レーンの数の上限は `relay.max-named`（既定 6）である。`--activity` を付けない `ndf relay ls` はどれも読まない。

## データ・設定

### 活動の記録（`<ラッパーの保存先>/activity.json`）

Stop の hook が書き、`ndf relay ls --activity` が読む。

| キー | 型 | 中身 |
| --- | --- | --- |
| `at` | string | 書いた時刻（ISO 8601）。conductor が応答を終えた時刻に当たる |
| `session_id` | string / null | 書いた Stop の入力の `session_id` |
| `reply` | string / null | この Stop が終えた応答（会話の記録の最後の `assistant` の記録の `uuid`）。背景の作業の一覧がリストのときだけ読み、ほかは `null` |
| `tasks` | array / null | 背景の作業。要素は `{"id", "type"}` だけで、`status`・`description` は写さない。入力に `background_tasks` が無い・リストでないときは `null`（観測できなかったことを表し、空のリストと区別する）。要素の `id`・`type` が文字列でなければ空の文字列にして要素の数を保つ |

**明示の空のリストと `null` を区別する。** `HookContext.background_tasks` は欠けや型の違いを空のリストへそろえるため、そのまま書くと「背景の作業が無いと確かめた記録」と「観測できなかった記録」が同じになる。hook は正規化の前の入力の値を渡す。

書き込みは一時ファイルからの置き換えで行う。排他は取らない（ラッパー 1 つにつき conductor は 1 つなので書き込み元は 1 つで、読む側は中途半端な中身を読まない）。

### 終わったシェルの記録（`<ラッパーの保存先>/shells-ended.json`）

`ndf relay ls --activity` が書く。キーは `session_id`・`stop_at`（活動の記録の `at`）・`ended`（終わったシェルの数）で、値は `seen`（終わりを最初に確かめた時刻の UNIX 時刻）である。キーのどれかが変われば書き直す。終わったシェルの数をキーに入れるのは、1 つ目が終わった時刻を 2 つ目の終わりに使い回さないためである。

### ラッパーの状態（`<ラッパーの保存先>/wrapper.json`）

`transcript` に、子の会話の記録の絶対パスを書く。ラッパーが子に渡した環境変数（従量接続・登録済みキーでは共有の `CLAUDE_CONFIG_DIR` に切り替わる）で組み、相対のパスは子の作業ディレクトリを基準に絶対化する。**一覧を読む側は、自分の環境変数や `session_id`・作業ディレクトリからパスを組み直さない。** 耐久ワークフローと起動先の設定ディレクトリが違うレーンでも、起動先の記録を読むためである。

### 閾値（`plugins/ndf/contracts/limits.json`）

| 名前 | 値 | 説明 |
| --- | --- | --- |
| `parallel-run.idle` | `seconds: 600` | conductor が応答を終えて背景の作業を持たないまま、何もしていないとみなすまでの秒 |

この秒の唯一の保存先である。判定は `liveness.idle_after()` で毎回読むので、値を書き換えれば動いている耐久ワークフローも次の判定から新しい値を使う。

## 外部連携

### `ndf relay ls --activity`

動いている行に 3 つのキーを足す。既存のキーは変えない。`--activity` を付けなければ 3 つとも `null` である。

| キー | 型 | 中身 |
| --- | --- | --- |
| `turn_done` | boolean / null | conductor の最後の応答が終わったか |
| `moved` | number / null | 最後の応答と、終わった背景の作業の時刻のうち最も遅いもの（UNIX 時刻） |
| `background` | integer / null | まだ走っているとみなす背景の作業の数。確かめられなければ `null` |

あわせて、すべての行に `session_id`・`cwd`・`pid`（ラッパーのプロセス）を載せる。`cwd` は `claude` を起動した場所（ラッパーの状態の値）を優先し、状態の無い行では登録簿の値を使う。

### `lane_stalled` のリクエスト

計画のステートマシンのリクエストの中身に 2 つ足す。既存の `moved`・`launches`・`last_launch` は残る。

| キー | 型 | 中身 |
| --- | --- | --- |
| `moved` | string / null | 最後に動いた時刻（ISO 8601）。`idle_conductor` では判定が見た活動の時刻を UTC の ISO 8601 へ直したもの、ほかの止まりでは今までどおりステートマシンの記録の時刻 |
| `background` | integer / null | 背景の作業の数。`idle_conductor` では 0、ほかの止まりでは `null` |

知らせのキーには判定の種類が入るため、同じレーン・同じ行でも `stalled` の知らせと `idle_conductor` の知らせは別のリクエストになる。リクエストの種類（`kind`）は `lane_stalled` のままで、判定の種類は本文の理由に入る。レーンが動き出して判定が止まりでなくなれば、リクエストは閉じる。

### `ndf lanes status`

レーンの行の `liveness` に `moved_at`（UNIX 時刻）と `background` が増え、`kind` が `idle_conductor` を取りうる。`warnings` には理由の文が並び、`metrics.stalled` が数に入る。活動を読む経路は耐久ワークフローと同じ `run.probes` なので、どちらにも同じ条件で当たる。

## エラー処理

| 失敗 | 扱い |
| --- | --- |
| 活動の記録を書けない（権限・容量） | `write_activity` が偽を返し、hook はそれまでの判定を続ける。次の Stop が書き直す |
| 活動の記録が無い・壊れている | `read_activity` が `null`。`background` が `null` で判定は `alive`。中継を使わない conductor では hook が呼ばれないため、常にこの経路になる |
| 活動の記録が今の応答のものと確かめられない | `background` が `null` で判定は `alive`。前の Stop の空の一覧を根拠に止まりとしない |
| conductor の会話の記録が無い・壊れている | 3 つとも `null` で判定は `alive` |
| サブエージェントの会話の記録を読めない・`id` がファイル名に使えない形 | その要素を走っているとみなす。判定は `alive` |
| `/proc` が無い・行に `pid` が無い・`claude` が見つからない・`cmdline` を読めない | `background` が `null` で判定は `alive`。一覧が空でも 0 と数えない（一覧に載っていないシェルが走っていることがある） |
| `shells-ended.json` を書けない | その周は読む時刻を使う（止まりにならない）。次の周が書き直す |
| 閾値を読めない（`limits.json` が壊れている） | `ContractError` で、呼び手のサブコマンドが原因 `config_broken` で止まる |
| 活動の記録のパスを組めない（ラッパーの名前の形が違う） | 今までどおり `RegistryError`。この変更で増やさない |

## セキュリティ

活動の記録とリクエストに、会話の記録の中身は入れない。入るのは時刻・`session_id`・背景の作業の `id` と `type`・応答の `uuid`・件数だけである。

サブエージェントの会話の記録のパスは、`id` が英数字・`-`・`_` だけのときにしか組まない。それ以外（空・`/` や `.` を含む）では組まず、記録の保存先の外を指すパスを作らない。

`/proc` の走査では、他の利用者のプロセスの `stat` を読めないマシン（`hidepid=1`）とプロセスの終了を失敗として扱わず、そのプロセスを数えずに続ける。シグナルは送らない。

## 運用

**閾値を変える手段は `plugins/ndf/contracts/limits.json` の `parallel-run.idle` の 1 行である。** 引数でも環境変数でもリポジトリの設定でも受けない。

**この判定が増えたことで、これまで `alive` と出ていた行の一部が止まりになる。** conductor が `AskUserQuestion` を使わずに本文で問いかけて応答を終えた行は、背景の作業が 0 件なら閾値を過ぎて `idle_conductor` になる。人の答えを待って止まっていることに変わりはないので、知らせとして正しい。`AskUserQuestion` の質問とスプリントの承認ゲートを待つ行は、今までどおり止まりにならない。

**`idle_conductor` のレーンを耐久ワークフローは再起動しない。** `/goal` を入れ直して再起動するのは `stalled`（ラッパー自身が止まった）のレーンだけである。

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 閾値の妥当さ | 10 分が短すぎて正常な行を止まりと知らせるか、長すぎて気づきが遅れるかは、知らせの実績を見るまで分からない |
| 確かめられない背景の作業 | `subagent` と `shell` 以外の `type` は終わったかを確かめられないので、走っているとみなす。その行は止まりにならない |
| シェルの生死を確かめられないマシン | `/proc` が無いマシンでは `background` が `null` になり、この判定は働かない |
| 活動の記録のファイルの台帳 | `shells-ended.json` はファイルの台帳（`plugins/ndf/contracts/domains/ndf-files.json`）に `relay-shells-ended` として載っているが、`activity.json` の行は無い |

## テスト観点

| 観点 | 何で縛るか |
| --- | --- |
| 応答が終わり・背景の作業が 0 件・最後に動いた時刻から閾値を過ぎた実行中の行が `idle_conductor` になり、`lane_stalled` のリクエストになる | 閾値の比較を落とすと落ちる |
| 背景の作業が 1 件以上の行は、閾値を過ぎても `alive` | 件数を見ずに時刻だけで判定すると落ちる |
| 承認ゲートを待つ行と、回答が載った後に `next` を待つ行は、閾値を過ぎても止まりにならない | 新しい条件を 2 番目・3 番目より前に置くと落ちる |
| 活動を渡さない呼び手では、ほかの 8 つの判定が変わらない | 既定の判定を変えると落ちる |
| `idle_conductor` の `lane_stalled` のリクエストに、ISO 8601 へ直した最後に動いた時刻と背景の作業の数 0 が載る | 背景の作業の数を載せずに書くと落ちる。UNIX 時刻をそのまま載せても落ちる |
| 閾値は `contracts/limits.json` の `parallel-run.idle` から来る | 判定のコードに秒を書くと落ちる |
| 会話の記録を読めない行・活動の記録が今の応答のものと確かめられない行は `alive` | 読めないときに止まりとすると落ちる |
| Stop の hook が、入力の背景の作業の一覧の `id`・`type` と `session_id`・応答の `uuid` を書く。明示の空のリストと観測できなかった値を書き分ける | 正規化の後の値を書くと落ちる |
| Stop の hook は早い戻りより前に書き、書けなくても今までの判定を返す | 書き込みの失敗で例外を上げると落ちる |
| 終わったサブエージェント 1 件だけが残る行は背景の作業 0 件とみなし、サブエージェントの最後の応答から閾値を過ぎれば `idle_conductor` になる | 会話の記録を読まずに件数だけで判定すると落ちる |
| conductor の最後の応答が閾値よりずっと前で、背景の作業が直前に終わった行は `alive` | 終わった作業の時刻を最後に動いた時刻へ重ねないと落ちる |
| 生きている Bash の Tool のシェルが 1 つある行は、一覧が空でも `alive` | 生きているシェルを数えずに一覧だけで判定すると落ちる |
| 一覧の `shell` が終わった行は、終わりを最初に確かめた時刻から閾値を過ぎれば `idle_conductor` になり、その時刻は周をまたいで変わらない | 周ごとに読む時刻へ戻すと落ちる |
| `/proc` を読めない・`pid` が無い行は、一覧の `shell` の数によらず `background` が `null` で `alive` | 確かめられないシェルを 0 件と数えると落ちる |
| 同じ秒に書かれた前の Stop の記録は使わず、応答の `uuid` が同じときだけ使う | 時刻で照合すると落ちる |
| `ndf lanes status` の行の `liveness.kind` が `idle_conductor` になり、`stall` が真・`metrics.stalled` が 1 になる | 活動を読む経路を耐久ワークフローだけに結ぶと落ちる |

テストの保存先は `plugins/ndf/tests/progress/test_lane_liveness.py`・`tests/progress/test_plan_run_stall.py`・`tests/domains/relay/test_relay_observe.py`・`tests/domains/hooks/test_relay_mark.py` である。時間で区切った試験は行わず、時刻はテストが引数で渡す。

## 関連リンク

- [ndf-parallel-organizer.md](ndf-parallel-organizer.md) — 並列の計画の全体
- [ndf-parallel-machine-plan-limit.md](ndf-parallel-machine-plan-limit.md) — レーンの同時実行の制御と `ndf relay ls`・`ndf lanes status`
- [ndf-parallel-durable-lane-launch.md](ndf-parallel-durable-lane-launch.md) — 耐久ワークフローがレーンの起動と再起動を打つ分担
- [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) — ラッパーのセグメントの切り替えと Stop の hook
- [docs/ndf-parallel-model.md](../ndf-parallel-model.md) — レーン・オーガナイザー・conductor の関係
