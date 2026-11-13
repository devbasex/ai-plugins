# 待ち方

**待つ間に状態を問い合わせる呼び出しを繰り返さない。** 待ち方の規約はこの文書だけが持ち、
他の文書はコピーせずにここを指す。

## 待ちの費用

**呼び出しは 1 回ごとに、その時点の会話の文脈の全体を読み直す。** `sleep 60 && tail -5 x.log`
を 30 回繰り返すと、30 回とも文脈の全体を読む。バックグラウンドで待って通知を 1 回受けるなら、待つ時間が
長くても呼び出しは増えない。

#827 の実測では、待つ間の繰り返しの問い合わせ（ポーリング）が全体の費用の 16%（2026-09-20
以降）、ai-plugins の 30 日間では 19% を占めた。

| 待ち方 | 待つ間の呼び出し | 費用 |
| --- | --- | --- |
| フォアグラウンドの `sleep` を挟んで状態を問い合わせ直す | 待つ時間 ÷ 間隔 | 回数 × その時点の文脈 |
| 出力ファイルを読み直す | 読み直した回数 | 同上 |
| `run_in_background` で起動し、完了通知を待つ | 0（通知が 1 回） | 待つ時間に依らない |
| `Monitor` で出来事を 1 つずつ受ける | 出来事の数 | 出来事の数 × 文脈 |

**待ちの後の最初の呼び出しは、キャッシュが切れていれば文脈の全体を書き直す。** バックグラウンドで待っても
呼び出しは増えないが、待ちがキャッシュの寿命（サブエージェントは既定で 5 分）を超えると、戻った
呼び出しが文脈の全体を書き込みの単価（入力の 1.25 倍）で払う。そのため収束ループを通す supervisor は
寿命 1 時間の定義で起動し、寿命 5 分の supervisor が長い文脈のまま収束ループへ入るのを hook が止める
（[context-window.md](context-window.md) の「フェーズの中のスイッチポイント」と「supervisor の定義を選ぶ」）。

## 許す待ち方

| ランタイム | 待ち方 |
| --- | --- |
| Claude Code | **条件の until ループを Bash の `run_in_background: true` で起動し、完了通知を 1 回受ける。** 出来事を 1 つずつ受けるなら `Monitor`。サブエージェントは完了通知を待つ |
| Codex / Kiro / agy | 1 回のフォアグラウンドの until ループ。600 秒を超えるなら共通ライブラリの `scripts/lib/bg-wait.sh`（`run` でバックグラウンドに起動し、`wait` を 124 のあいだ別の呼び出しとして打ち直す） |

**1 回で足りる待ちは `Monitor` ではなく `run_in_background` にする。** `Monitor` は出来事の
たびに通知が届き、その都度文脈を読む。終わりだけを知りたい待ちでは通知が 1 回で済む
`run_in_background` のほうが安い。

## 禁じる待ち方

- **`sleep` を挟んだ呼び出しの繰り返し。** `sleep 30 && tail x.log` を何度も打つ形と、フォアグラウンドの
  `while` / `until` のループの本体で `sleep` する形
- **出力ファイルの繰り返しの読み直し。** 変わっていないファイルの同じ範囲を続けて読む形
- **サブエージェントの `tasks/*.output` を読むこと。** 会話の記録の全体で、読むと文脈を埋める。
  完了通知を待つ
- **プロセス名で待つこと（`pgrep -f` / `pkill -f`）。** 待つ側のコマンド行にも同じ文字列が入るので、
  自分に一致して終わらない。Claude Code はバックグラウンドの Bash を `bash -c … eval '…'` で包み、単引用符を
  `'"'"'` に置き換えるため、元のコマンドの文字列での一致もあてにならない。終わりはファイルで待つ

## 待つ相手ごとの手

| 待つ相手 | 手（Claude Code） |
| --- | --- |
| サブエージェント | 完了通知を待つ。途中の出力を読まない |
| バックグラウンドで動かす CLI（`codex exec` など） | CLI そのものを `run_in_background: true` で起動し、完了通知を待つ |
| 既に起動したプロセス・書き終わりを待つファイル | 終わりを待つ until ループ（例: `until [ -s out.md ]; do sleep 5; done`）を `run_in_background: true` で起動する |
| Pull Request のチェック | 今の状態は `lib/gh_parts.py pr-status <番号>` を 1 回打って読む。`metrics.action` が次の 1 手（`wait` 待つ・`fix` 直す・`merge` マージへ進める・`escalate` 人へ戻す）で、`next` に打つコマンドが載る。待つときは下の `merge-when-green` を `run_in_background: true` で起動する。PR の表示とチェックの照会を別々に続けて打たない |
| 新しいコメントを 1 件ずつ | `Monitor` |
| `supervise.py run` | `report.md` が揃うか、`progress.jsonl` に `attention` の行が足されるまでの until ループを `run_in_background: true` で起動する（下の節） |
| `supervise.py queue` | `supervise.py wait <done のパス>` を `run_in_background: true` で 1 回起動する。キューの終わり（done）か、キューが流すプランの `attention` の行で終わる（下の節）。done のパスは `queue --done` で渡した所（省けば最初のプランの `<プラン>-state/queue-done.json`） |
| Pull Request の CI とマージ | `merged-steps.py merge-when-green <PR 番号>` を `run_in_background: true` で 1 回起動する。CI がまだ現れない間も待ち、緑になればマージする。マージの承認を得た後に限る。起動の前に `gh_parts.py pr-status <PR 番号>` で、直すもの（失敗したチェック・未解決のスレッド）と衝突が無いかを 1 回で確かめる。チェックの照会と `gh pr merge` を手でつながない |
| 判断の要らない NDF の手順を続けて打つ（`queue` の後の `wait` など） | 1 行ずつ打たず、`supervise.py advance --step "<コマンド>" --step "<コマンド>"...` を 1 回打つ。結果 JSON が `ok` の間だけ次を打ち、`gate` か `stopped` で止まって、理由と続きのコマンド（`--from <番号>`）を `next` に返す。打てるのは NDF の `scripts/` と `skills/*/scripts/` だけ。長く待つ手順を含むなら `run_in_background: true` で起動する |
| キューの後に続けるプラン（リリースなど） | 手でパイプラインを組まず、`queue <実装のプラン>... --then <後続のプラン>` で渡す。後続は前のプランがすべて `完了` のときだけ流れ、1 本でも `止まった` / `関門` なら `流さなかった` と理由が結果に残る。リリースプランを `new release --prs-from-queue` で作れば、実装の PR の番号を知らずに渡せる |

### supervise.py の進捗ログ

**フェーズを `supervise.py run` / `queue` で回すとき、conductor は `report.md` が揃うか、
`progress.jsonl` に conductor 向けの行が足されるまでを 1 回のバックグラウンドの待ちで待つ。**
`progress.jsonl` はプランの状態ディレクトリ（`<プラン>-state/`）に置かれ、1 行が 1 つの JSON である。
書くのは supervise.py と worker で、LLM は使わない。

| `kind` | 書く時点 | 中身 | conductor の読み方 |
| --- | --- | --- | --- |
| `step` | ステップの切り替わりごと | `step`・`type`・`exit`・`seconds`・`cost`・`next`・`summary`・`host`（負荷とメモリの値。下の注） | 起きたときに末尾を読む。これだけでは起きない |
| `alive` | 最後の行からプランの `"report_interval"`（既定 600 秒）動きが無いとき | `step`・`elapsed`・`worker`（worker の最後の報告）・`host` | 同上 |
| `worker` | work のステップの worker が区切り（課題を読み終えた・テストを足した・実装を 1 つ終えた・コミットした）ごと | `text`（1 行の要約） | 同上 |
| `slow` | run・work・drive のステップの経過が想定時間を超え、一次の調査を流すたび | `step`・`round`・`elapsed`・`expected`・`basis`・`probe`（`name`・`class`・`action`・`summary`）・`act`・`by`（`rule` / `llm`）・`llm`・`next_check` | `step` と同じ |
| `attention` | conductor の判断が要るとき | `step`・`reason`（`止まった` / `関門` / `同じ失敗の繰り返し` / `judge のステップで stop が出そう` / `遅れ`）・`text` | **この行が足されたら起きて読む** |

- `host` は負荷とメモリの値とマシンの上の pytest の数である（`load`（1・5・15 分の load average）・`mem_available_mib`・
  `swap_total_mib`・`swap_free_mib`・`pytest`（本体の数）・`pytest_workers`（xdist の作業プロセスの数））。並列の上限を決め直す
  材料で、待ちの判断には使わない。読めない値は鍵ごと省く
- **conductor 向けの行は `attention` だけである。** supervise.py が worker の行の語（`止まった`・`関門`・
  `失敗`）と繰り返し、ステップの結果からスクリプトで分ける。`step` / `alive` / `worker` / `slow` は起こさない
  （起こすたびに conductor の文脈の全体を読み直すため）
- `queue` は `attention` の行を標準出力の 1 行 `{"tool": "supervise-queue", "event": "attention", ...}`
  で知らせる。最後の行は結果の JSON のままである
- `attention` で起きたら、その行と `progress.jsonl` の末尾だけを読み、止めるか続けるかを決める。
  続けるなら同じ待ちを起動し直す。フェーズレポートは `report.md` で読む
- **遅れの監視は supervise.py がステップの待ちの中で行う。** 想定は同じステップ（フェーズ, ステップの id）の直近 10 回の
  所要の中央値 × 3（下限 300 秒。履歴が 3 回未満なら 900 秒）。超えたら一次の調査を流し、決まった手
  （待ち直し・取り残しの再実行）で解けなければ Tool なしの `claude -p` が retry / fix / stop / wait を選ぶ。
  `attention`（reason `遅れ`）は、調査が手を打ったとき・判定へ回したとき・ステップを打ち切ったとき・判定の
  回数が上限に達したときだけ書く。打ち切ったステップの終了コードは 125
- フェーズレポート（`## フェーズの報告`）の `途中の報告` の欄が、行の種類ごとの数（`遅れの調査` を含む）と、LLM へ回した
  回数・費用を持つ。手を打ったステップは `- 遅れ:` の行に並ぶ
- 待ちのコマンドは下のとおり

**conductor は `report.md` と、`progress.jsonl` の `attention` の行の数を 1 つの until ループで待つ。**
`step` / `alive` / `worker` の行では起きない。

```bash
# Bash の run_in_background: true で起動する（フォアグラウンドで打たない）。S はプランの状態ディレクトリ、最後の 3600 が上限の秒数
S="<プラン>-state"; n=$(cat "$S/progress.jsonl" 2>/dev/null | grep -c '"kind": "attention"')
bash -c 'c() { cat "$1/progress.jsonl" 2>/dev/null | grep -c "\"kind\": \"attention\""; }
until [ -s "$1/report.md" ] || [ "$(c "$1")" -gt "$2" ]; do [ "$SECONDS" -lt "$3" ] || exit 124; sleep 5; done' _ "$S" "$n" 3600; rc=$?; echo "exit=$rc"; exit "$rc"
```

- `queue` で流すときは、この until ループの代わりに `supervise.py wait` で待つ（次の節）
- 起きたら `attention` の行と `progress.jsonl` の末尾だけを読む。続けるなら数を取り直して同じ待ちを起動する
- 124（上限の 3600 秒）で終わったら、`progress.jsonl` の最後の行（`alive` ならステップと経過）を 1 度読み、
  同じ待ちを起動し直す

### キューの待ち（supervise.py wait）

**conductor はキューをバックグラウンドで起動し、`supervise.py wait <done のパス>` を `run_in_background: true` で
1 回起動する。** 待ちの監視（until ループ・`attention` の読み取り・上限）を手で書かない。

```bash
# どちらも Bash の run_in_background: true で起動する。queue を起動した後に wait を打つ
python3 plugins/ndf/scripts/supervise.py queue plan-1101.json plan-1102.json --then plan-release-dev.json --done q/done.json
python3 plugins/ndf/scripts/supervise.py wait q/done.json
```

| 終わり方 | 終了コード | 次にすること |
| --- | --- | --- |
| キューが終わった（done に結果の JSON が書かれた） | 0 | 結果の JSON の `items[0]`（キューの結果）の `status` を見る。`gate` なら承認ゲートを返したプランの `report.md` を読んで提示する |
| キューが流すプランの `progress.jsonl` に `attention` の行が足された | 20 | その行（結果の `items`）と `progress.jsonl` の末尾だけを読み、止めるか続けるかを決める。続けるなら同じ `wait` を打つ。知らせた行の続きから待つ |
| 上限（`--timeout`、既定 10800 秒）に達した | 3 | 流れているプランの `progress.jsonl` の最後の行を 1 度読み、同じ `wait` を打つ |

- 出力は要約の 1 行と結果の JSON の 1 行だけである
- 開発ループでは `loop sprint` の `metrics.research`（`upkeep.py research run --until <done> ...`）も `wait` と並べて背景で起動する。
  並列の上限に空きがあるときだけ調査の項目を 1 つずつ調べ、done が出たら新しい項目を始めずに終わる。結果を待たない
  （`backlog-refinement` の `references/research.md`）
- キューは始めに流すプランの一覧（`--then` を含む）と読み始める所を done の隣の `<done>.plans.json` へ書き、
  wait はそれを読む。知らせた `attention` の続きは `<done>.wait.json` に残る

### スプリントを流すコマンド

**スプリントは工程の進行表で流す。conductor が打つのは、進行表を作る 1 回と、止まるたびの `next` 1 回である。** 次に流す工程・
再開する位置・まとめの工程は進行表が決めるため、プランの名前やステップを組み立てない。例はスプリント `m6`（課題 1052・1053、
モード `standard`）で、`pg() { python3 "$SCRIPTS/progress.py" "$@"; }` とする。`next` はバックグラウンドで起動し、リポジトリの根から起動する。

1. 進行表を作る: `pg new m6 --mode standard --issues 1052 1053 --requirements <要求の文書> --judgment <モード判定の結果>`。
   結果の `metrics` に進行表の ID が載る。並列の計画のレーンでは `--lane <レーンの進行表の ID> --row <行>` を足す（最初の入力が示す）
2. 流す: `pg next <ID>`。未着手で有効な最初の工程から流し、工程ごとに出力を入出力規約で検査して終了の印を付ける
3. 終了コード 10（待ち）: `items` に待っている行と `next_step` が載る。承認ゲートなら承認資料を添えて利用者の答えを取り、
   `pg answer <ID> <行> --approved|--rejected` を打ってから 2 へ戻る。レーンのリリースの依頼なら `next_step` の
   `parallel.py await <計画名> --from <依頼の記録>` を打つ（答えを進行表の行へ届ける）。答えがまだ無ければ（3）待ってから打ち直す
4. 終了コード 1（工程の失敗）: 原因を直して `pg retry <ID> <工程> --reason <理由>` の後に 2 へ戻る。手で済ませた工程は
   `pg finish <ID> <工程> --output <出力> --reason <理由>`、外す工程は `pg disable <ID> <工程> --reason <理由>` で進行表を変える
5. 終了コード 0: 有効な最後の工程まで終わった。`metrics.next_row` があれば、同じレーンの次の行を 1 から始める

- 状態は `pg show <ID>` で読む。課題の工程表とスプリント PR の「通した工程」は進行表の終了の印から作られるため、手で書かない
- 確定仕様化・リリース後テスト・振り返り・組み換え・棚卸しも進行表の行で、`next` が続けて流す
- `pace: fast` と `pace: auto` の並びは [pace.md](pace.md)、本番の後に続けるコマンドは [relay.md](relay.md) にある

**サブエージェントは、バックグラウンドの処理を残したまま応答を終えない。** 完了通知で再開はされるが、
**親には応答を終えた時点で 1 度「終わった」と通知が届き、途中の文面が結果として渡る**
（Claude Code 2.1.280 で実測。`codex exec` をバックグラウンドで起動して応答を終えたサブエージェントは、
約 2 秒後の完了通知で再開して報告を出し直し、親には通知が 2 回届いた）。親が 1 回目を
結果と読むと、報告の無いフェーズを受け取る。supervisor と worker は待ちで応答を終えない
（[agent-layers.md](agent-layers.md) の規則）。バックグラウンドの処理を起動した後は、同じ応答の中で
他の作業を進め、通知を受けてから次の作業へ進む。他の作業が無いまま待つときの手は #656 が扱う。
親の側の手は、受け取る層ごとに次の節が持つ。

### 中間通知を受けたとき

**中間通知**は、バックグラウンドの処理を残したまま応答を終えたサブエージェントについて届く 1 回目の
通知である。見分けは通知の注記（「background work of its own still running」「may be
interim」）で行う。

| 受け取る層 | 手 |
| --- | --- |
| conductor | **2 回目の通知を待ってから報告を読む。** conductor は応答を終えても次の通知で起こされる |
| supervisor | **応答を終える前に、自分のバックグラウンドの処理として報告コピーの待ちを起動する。** 下のコマンドを `run_in_background: true` で起動し、完了通知で再開する |

**supervisor が「2 回目の通知を待つ」で応答を終えると止まる。** supervisor が起動した worker
は supervisor のバックグラウンドの子に数えられず、worker が後で終わっても、応答を終えた supervisor は
起こされない。自分で起動したバックグラウンドの Bash の完了通知なら、supervisor は再開する。

**worker の規則 5（バックグラウンドの処理を残したまま応答を終えない）は保つ。** 規則を守る worker では
中間通知は起きない。この手は、守れなかった worker（`Monitor` やバックグラウンドの待ちを残して応答を
終えた worker）への備えである。

**supervisor は worker を起動する前に、`置き場所` のファイルを worker ごとに新しいパスで空に
作り、完了マーカーを消す**（`: > <置き場所>; rm -f <置き場所>.done`）。前の worker の報告や完了マーカーが
残ったパスを渡すと、コピーの待ちがその完了マーカーに即座に反応し、今の worker の報告を待たずに終わる。

**待つのは完了マーカーのファイル `<置き場所>.done` である。** worker
は報告コピーを `置き場所` の末尾へ書き終えた後に、空のファイル `<置き場所>.done` を作る
（[agent-layers.md](agent-layers.md) の起動指示の `置き場所`）。

```bash
# Bash の run_in_background: true で起動する（フォアグラウンドで打たない）。最後の 3600 が上限の秒数
bash -c 'until [ -e "$1.done" ]; do [ "$SECONDS" -lt "$2" ] || exit 124; sleep 5; done' _ "<置き場所>" 3600; rc=$?; echo "exit=$rc"; exit "$rc"
```

| 終了コード | supervisor の動き |
| --- | --- |
| 0（`<置き場所>.done` が現れた） | `置き場所` の最後の `## 作業の報告` から末尾までを読み、フェーズを進める |
| 124（上限の 3600 秒に達した） | [interrupt-resume.md](interrupt-resume.md) の「supervisor の worker の点検」を 1 回行う。レートリミット中断でなければ「報告が無いまま終わったとき」の規則で `SendMessage` を送る |

- **worker の 2 回目の通知は、コピーを読んだ後に届いても読み直さない。** 同じ報告である
- 中間通知でない通知（報告の見出しが無く、バックグラウンドの処理も残っていない）は、今のまま
  「報告が無いまま終わったとき」の規則で扱う
- 待つ間に問い合わせを繰り返さない。起動は 1 回で、通知も 1 回である（「許す待ち方」）

## バックグラウンドの作業を止める

**バックグラウンドの作業を止めるのは `TaskStop <task id>`。** task id は起動したときの応答と完了通知にある。
`pkill -f` / `pgrep -f` で止めたり、止まったかを確かめたりしない（上の「禁じる待ち方」と同じ理由で
一致しない。確かめる側の `grep -v pgrep` は、`pgrep` を含む待ちのコマンド行ごと結果から除く）。
止まったかは完了通知（`failed` / `killed`）で確かめる。

バックグラウンドの作業が残っていると、ラッパーの Stop hook（`relay.py mark`）は `ndf-next` のシグナルファイルを書かない。そのときは
Stop を 1 度だけ止め、動いている作業を並べて知らせる。supervisor や `supervise.py queue` のように
止めてはいけない作業なら、止めずに終わりを待ってから `ndf-next` を出し直す。

## hook

**Claude Code では、禁じる待ち方を hook が止める**（`scripts/hook.py` の token の guard。PreToolUse の
`Bash` と `Read` で動く）。止めたときは理由の欄に代わりの待ち方が出る。

| 判定 | 止める条件 | 止め方 | 上限を変える |
| --- | --- | --- | --- |
| sleep | フォアグラウンド Bash で、コマンドの位置（先頭の代入語 `X=1` と、`timeout 590` / `nohup` / `env` などの前置きの後ろを含む）の `sleep` が `while` / `until` のループの本体にある（秒数が変数でも止める）か、秒数が上限を超える。コメント・引用・ヒアドキュメントの本文は見ず、コマンドの位置にある `bash -c` / `sh -c` / `zsh -c` / `dash -c` / `eval` の中身は見る（`echo bash -c ...` のような引数の中の語は見ない） | `NDF_SLEEP_GUARD=0` | `NDF_SLEEP_MAX_SEC`（既定 5） |
| 連続 Read | 同じ `file_path`・`offset`・`limit` の Read が、ファイルの大きさ・更新時刻・inode が変わらないまま上限の回数に達する | `NDF_READ_REPEAT_GUARD=0` | `NDF_READ_REPEAT_LIMIT`（既定 3） |
| スイッチポイント | 寿命 5 分の supervisor（入力の `agent_type` が `ndf:supervisor`）が `cross-review` / `cross-refactoring` の Skill を起動し、自身の記録の今の文脈が最初の呼び出しの文脈の比以上ある（やり直しても止め続ける）。PreToolUse の `Skill` で動く | `NDF_SUPERVISOR_CUT_GUARD=0` | `NDF_SUPERVISOR_CUT_RATIO`（既定 1.5） |

- **止めないもの:** `run_in_background: true` の Bash、`Monitor` の中の `sleep`、ループの本体の
  外の上限以下の `sleep`、`for` のループの中の上限以下の `sleep`、末尾の `&` でバックグラウンドになる `sleep`（`sleep 30 >/tmp/x &` のようにリダイレクトを挟んでもよい。`sleep 30 && echo x &` のようなリストや、`(sleep 30) &`・`{ sleep 30; } &`・`while ...; do sleep 1; done &` のように sleep を囲む複合コマンドの全体がバックグラウンドになる形も含む。`bash -c 'sleep 30' &`・`eval 'sleep 30' &` のように `bash -c` / `eval` の外側がバックグラウンドになる形も含む。`2>&1` / `&>` の `&` はバックグラウンドと読まない）
- **判定が失敗したときは止めない**（入力が読めない・`jq` や `python3` が無い・記録を書けない）
- 同じ hook が、文脈が上限を超えた conductor の工程の起動も止める（[context-window.md](context-window.md)
  の「上限を超えたら hook が止める」）と、寿命 5 分の supervisor が長い文脈のまま収束ループを
  始めるのも止める（同じ文書の「フェーズの中のスイッチポイント」）

**hook を置くのは Claude Code だけである。**

| ランタイム | 待ち方（#829） | 会話を切る（#830） | 理由 |
| --- | --- | --- | --- |
| Claude Code | hook ＋ この規約 | hook ＋ 再開コマンド | 代わりの待ち方（`Monitor` / `run_in_background` の通知）と会話の記録の場所を持つ |
| Codex | この規約だけ | 再開コマンドだけ | バックグラウンドの起動と完了通知が無く、1 回のフォアグラウンドのループが待ち方になる |
| Kiro | この規約だけ | 再開コマンドだけ | 実行前の hook は拒否しか返せず、既存の設計も実行前の hook を置いていない |
| agy | この規約だけ | 再開コマンドだけ | 実行前の hook は案内を記録へ積む形で、拒否の仕組みを使っていない |
