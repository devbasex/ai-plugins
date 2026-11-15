# #3569 の設計: 旧い副命令の行き先と新しい副命令の契約

頭の設計文書は [issue-3569-design.md](issue-3569-design.md) にある。この文書は、旧い 10 本の下位の命令ごとの行き先と、足す・変える副命令の引数・結果・終了コードを持つ。決定の番号は頭の設計文書の「決定の記録」を指す。

## 旧い副命令の行き先

「呼ぶ本文」は、`plugins/ndf-next/` の `skills/`・`agents/` の下でその下位の命令を書いたファイルの数である（2026-11-15、このブランチで `grep -rlE '<名前>"? <下位の命令>'` を打った結果）。行き先の種類は次の 4 つである。

| 種類 | 意味 |
| --- | --- |
| 置き換える | 既にある副命令へ呼び出しを書き換える（決定 2） |
| 作る | 旧い実装を新しい副命令へ移す（決定 3） |
| 作らない | 新しい仕組みが役目を持つか、外の仕組みで足りる。本文は代わりの手順へ書き換える（決定 1・2・4） |
| 回す | 開いている子を行き先にした行を対応表へ足し、本文の呼び出しをその旨の 1 文に書き換える（決定 5） |

### `resolve.sh`（行き先 X0。呼ぶ本文 12）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `resolve.sh root`・`scripts [Skill名]`・`skill <Skill名>` | 作らない | `command -v ndf` の確かめ。`$SCRIPTS` は決めない | 決定 1。行は `not_built` |

### `supervise.py`（行き先 C2。呼ぶ本文 28）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `new sprint` | 置き換える | `ndf progress new <スプリント名> --mode <モード> --issues <番号>...` | スプリントは進行表で流す（P1a）。テンプレートに sprint は無い（`ndf route templates`） |
| `new <impl・fix・check・release・close>` | 置き換える | `ndf route render <目的> --out <プラン>` | テンプレートは C3 が持つ（`ndf route templates`） |
| `queue` | 置き換える | `ndf plan queue <プラン>... [--then] [--from] [--resume] [--queue-dir]` | `--max`・`--done`・`--poll` は持ち込まない（決定 2。行 `from-issue:3569-supervise-options`） |
| `run` | 置き換える | `ndf plan next <プラン> [--from] [--state-dir] [--answer]` | `--slow` は持ち込まない（行 `from-issue:3569-supervise-options`） |
| `wait` | 置き換える | `ndf plan wait <プラン>... [--state-dir] [--timeout]` | 渡すのは結果のファイルでなくプランである |
| `advance` | 置き換える | `ndf plan next <プラン>` | 判断の要らない手順を承認ゲートか止まるまで続けて打つのは `next` の役目である |
| `note` | 回す | `from-issue:3569-handoff-note`（`to: M7`） | 引継ぎの記録は M7 #3302 の領分（`ndf records`） |
| `design-results` | 回す | `from-issue:3569-design-results`（`to: P1b`） | スプリントの工程を流すのは P1b #3092 の領分（`ndf progress next`） |

### `test-run.py`（行き先 C2。呼ぶ本文 3）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `scope` | 置き換える | `ndf route test [<コマンド>] [--failed-from] [--result]` | 範囲のコマンドは `ndf.testing.scoped` が宣言から組む |
| `whole` | 置き換える | `ndf ci watch` | 全体テストは CI が持つ。手元の全体テストと落ちの 3 分類（フレーキー・既存の失敗・変更が起こした失敗）は作らない（決定 2。行 `from-issue:3569-test-run-whole`）。落ちたテストの打ち直しは `ndf route test --failed-from` |
| 名前だけの言及（`worktree/references/test-execution.md`） | 置き換える | `ndf route test` | — |

### `project-decl.py`（行き先 D2+ から S1e へ。呼ぶ本文 6）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `check`・`measure`・`write`・`drift`・`schema` | 作る | `ndf project check`・`measure`・`write`・`drift`・`schema` | 新しいプラグインに宣言を判定・測定・書き出す部品が無い。`ndf records preflight` は宣言の有無と読めるかだけを見る（`domains/records/preflight.py`） |

### `context-policy.py`（行き先 M3b。呼ぶ本文 5）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `resolve`・`record`・`calibrate`・`report` | 作る | `ndf relay context resolve`・`record`・`calibrate`・`report` | 新しいプラグインに同じ働きが無い（`src/ndf` に `context_policy`・`calibrate` の語が 0 件） |
| `publish`（SessionStart の hook の本体） | 対象外 | — | 本文は呼ばない。hook の行（`hook:claude:SessionStart`、行き先 M3a）が持つ |

### `relay.py`（行き先 M3b。呼ぶ本文 13）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `run`・`spawn`・`restart`・`ls`・`status`・`install` | 置き換える | `ndf relay run`・`spawn`・`restart`・`ls`・`status`・`install` | `--json` は持ち込まない（どの副命令も結果 JSON を出す） |
| `stop <名前>` | 置き換える | `ndf relay stop <名前>` | — |
| `stop`（名前なし。すべて止める） | 作る | `ndf relay stop --all` | 新しい `stop` は名前を必須にしている |
| `account use <名前>`・`use --clear` | 置き換える | `ndf relay account use <名前>`・`ndf relay account clear` | M3b が作った |
| `account add`・`add-bedrock`・`add-api-key`・`api-key-monthly`・`api-key-use`・`list`・`capacity`・`remove`・`check` | 作る | `ndf relay account <同じ名前>` | 新しい `account` は `use`・`show`・`clear` だけを持つ。人へ残す判断（C1・C2） |
| `notice` | 作る | `ndf relay notice` | 新しいプラグインに無い |
| `is-child` | 作らない | `ndf relay notice` の結果の `metrics.inside` | 判定は `notice` の 1 行目と同じである（行 `from-issue:3569-relay-hook-bodies`） |
| `cap` | 置き換える | `ndf lanes limits <計画> [--max-lanes] [--max-plans] --reason` | 上限は計画ごとに `ndf lanes` が持ち、レーン数の既定は事実の表 `relay.max-named` が持つ |
| `role` | 置き換える | `ndf relay spawn --role <役割> --plan <計画名>` | 役割は起動の時に渡す（`hub.py`） |
| `attach <名前>` | 作らない | `tmux -L ndf-lanes attach -t <名前>` | 端末を tmux のセッションへ結び付ける操作で、結果 JSON を返す副命令の形に合わない。ソケットの名前は `domains/relay/tmux.py` の `SOCKET`（行 `from-issue:3569-relay-attach`） |
| `mark`（Stop の hook の本体） | 作らない | Stop の hook の guard（`hooks/guards/relay_mark.py`） | 本文の言及は guard の説明へ書き換える（行 `from-issue:3569-relay-hook-bodies`） |
| `startup`（SessionStart の hook の本体） | 作らない | `ndf relay install` | 複製を今の版で置き直す働きは `install` が持つ（行 `from-issue:3569-relay-hook-bodies`） |

### `mode-materials.py`（行き先 P1a から S1e へ。呼ぶ本文 1）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `--issue <番号>... [--out]` | 作る | `ndf materials --issue <番号>... [--out <判定の根拠のファイル>]` | 新しいプラグインに無い。下書きは `ndf loop sprint` が受ける |

### `stage-check.sh`（行き先 P1a。呼ぶ本文 4）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `report <課題番号>` | 置き換える | `ndf progress show <進行表の ID>` | 通過記録は進行表が持つ。ID は `ndf progress new` の結果にある |

### `install-official-skills.sh`（行き先 S1b。呼ぶ本文 1）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `<name...>`・`--list`・`--update`・`--scope project` | 作らない | `claude plugin marketplace add anthropics/skills` と `claude plugin install document-skills@anthropic-agent-skills` | 決定 4。行は `not_built` |

### `statusline-switch.sh`（行き先 S1b から S1e へ。呼ぶ本文 1）

| 旧い | 種類 | 新しい | 根拠 |
| --- | --- | --- | --- |
| `ensure`・`status`・`set`・`restore` | 作る | `ndf statusline ensure`・`status`・`set`・`restore` | 新しいプラグインに無い。SessionStart で `ensure` を打つ登録は、hook の行（`hook:claude:SessionStart`、行き先 M3a）が持つ |

## 機能の対応表の変更

| 行 | 変更 |
| --- | --- |
| `entrypoint:scripts/resolve.sh` | `to` を外し、`not_built`: 「`ndf` は名前で見つかる（X0 の `bin/ndf`）。本文は置き場を決めない」 |
| `entrypoint:scripts/install-official-skills.sh` | `to` を外し、`not_built`: 「公式 Skill はプラグインのマーケットプレイスで配られる（anthropics/skills）。導入は Claude Code のプラグインの導入が持つ」 |
| `entrypoint:scripts/project-decl.py`・`entrypoint:scripts/statusline-switch.sh`・`entrypoint:scripts/statusline.sh`・`entrypoint:scripts/mode-materials.py` | `to` を S1e にする（決定 7） |
| `entrypoint:scripts/supervise.py`・`test-run.py`・`context-policy.py`・`relay.py`・`skills/development-workflow/scripts/stage-check.sh` | 変えない（C2・M3b・P1a のまま） |
| `from-issue:3569-handoff-note` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「報告から引継ぎ文書の表へ 1 行足す（旧い supervise.py note）」・`to: M7`・`unowned: true`・`source: "#3569"` |
| `from-issue:3569-design-results` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「マージした設計 PR の設計の結果から実装のプランを書き換える（旧い supervise.py design-results）」・`to: P1b`・`unowned: true`・`source: "#3569"` |
| `from-issue:3569-supervise-options` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「旧い supervise.py の queue の `--max`・`--done`・`--poll` と run の `--slow`」・`not_built`: 「同じ働きを 2 つの名前で持たない。本文の呼び出しは `ndf plan queue`・`ndf plan next` の引数へ書き換える（決定 2）」・`source: "#3569"` |
| `from-issue:3569-test-run-whole` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「旧い test-run.py whole の手元の全体テストと、落ちの 3 分類（フレーキー・既存の失敗・変更が起こした失敗）」・`not_built`: 「全体テストは CI が持つ（`ndf ci watch`）。落ちたテストの打ち直しは `ndf route test --failed-from`」・`source: "#3569"` |
| `from-issue:3569-relay-attach` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「旧い relay.py attach（端末をレーンの tmux のセッションへ結び付ける）」・`not_built`: 「結果 JSON を返す副命令の形に合わない。`tmux -L ndf-lanes attach -t <名前>` で足りる」・`source: "#3569"` |
| `from-issue:3569-relay-hook-bodies` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「旧い relay.py の mark・startup・is-child」・`not_built`: 「役目は Stop の hook の guard・`ndf relay install`・`ndf relay notice` の `metrics.inside` が持つ」・`source: "#3569"` |
| `from-issue:3569-context-report-table` | 足す。`kind: from-issue`・`now: "—"`・`feature`: 「旧い context-policy.py report の `--format table`」・`not_built`: 「結果 JSON の `items` を表として読むのは呼び手である」・`source: "#3569"` |
| `places` | `S1e` を足す（決定 7） |

足す 7 行の `now` は `—` にする（決定 5）。`now` を `scripts/supervise.py` にした場合と `—` にした場合を、今の対応表に回す 2 行を足して判定（`probe.judge`）で比べた結果は次のとおりである（2026-11-15、このブランチ。本文の置き換えの前）。

```text
$ PYTHONPATH=src uv run --frozen --project . python <対応表に 2 行を足して probe.judge を打つスクリプト>
now='scripts/supervise.py'
  from-issue:3569-handoff-note {'result': 'failed', 'reason': 'ndf-next の 28 ファイルがまだ旧い scripts/supervise.py を呼ぶ（例 agents/supervisor-waits.md）。行き先 M7 が新しいエントリポイントの副命令に置き換えていない'}
  from-issue:3569-design-results {'result': 'failed', 'reason': '... 行き先 P1b が新しいエントリポイントの副命令に置き換えていない'}
  agent:supervisor {'result': 'failed', 'reason': '本文が旧いプラグインのスクリプトを呼ぶ: supervise.py（P1b）。...'}
now='—'
  from-issue:3569-handoff-note {'result': 'not_run', 'reason': '試走のスプリントは手順 0 で $SCRIPTS が旧いプラグインの scripts/ へ解決したため、...'}
  from-issue:3569-design-results {'result': 'not_run', ...}
  agent:supervisor {'result': 'failed', 'reason': '本文が旧いプラグインのスクリプトを呼ぶ: supervise.py（C2）。...'}
```

`now` が `—` なら、2 行は本文の呼び出しに依らず `not_run` になり、理由に 10 本の名前が出ない。M7 と P1b の置き場（`src/ndf/domains/records/`・`src/ndf/progress/run.py`・`src/ndf/progress/invoke.py`）は実在するため、置き場が無いことで `failed` にもならない（`probe._missing_places`）。`not_built` を持つ 5 行は、同じ判定で `passed`（理由「作らない行。ndf-next の本文に呼び手が無い」）になり、形の確かめ（`table.shape_errors`）は誤り 0 件である（`from-issue:3569-test-run-whole` を足して打った結果）。上の `agent:supervisor` の `failed` は、本文の置き換えの前の状態であり、置き換えの後に消える。

## 新しい副命令の契約

どの副命令も、結果 JSON を標準出力の最後の 1 行に出し、終了コードは終了コードの表（`contracts/facts/exit-codes.json`）の値を使う。表の名前と値は `ok`（0）・`failed`（1）・`unreadable`（2）・`precondition`（3）である。使い方の誤りは、副命令の表（`cli.py`）が `stopped`（種類 `invalid`）で返す。

**旧い実装と終了コードの意味が食い違う下位の命令は、新しい側の表の名前に揃える。** 旧い `project-decl.py check` の 2（無い・古い）と `context-policy.py` の 2（引数の誤り）は意味が違うため、下の表に新しい側の値を書く。

### `ndf project`

旧い `project-decl.py` の判定・測定・書き出しを移す。宣言はメインディレクトリの `.ndf/project.json`、スキーマは新しいプラグインの `contracts/schemas/declarations/project.schema.json` を正本にする。

| 下位の命令 | 引数 | 結果の `items` | 結果の `metrics` | 終了コード |
| --- | --- | --- | --- | --- |
| `check` | `[--force] [--root <ディレクトリ>]` | 判定の理由 1 件（`kind`: `fresh`・`missing`・`stale`・`broken`） | — | 0 = 新しい / 3 = 無い・古い（`--force` なら新しくても 3）/ 2 = 壊れている / 1 = 判定できない |
| `measure` | `--out <ファイル> [--budget <秒>] [--root <ディレクトリ>]` | 測った項目ごとに 1 件（`name`・`value`・`source`）。不明は `value: null` | `measured`・`unknown`（件数） | 0 = 書けた / 1 = `--out` を書けない |
| `write` | `--measure <ファイル> [--answers <ファイル>] [--dry-run] [--root <ディレクトリ>]` | 書いた・書く予定のキーごとに 1 件 | `written`（件数） | 0 = 書けた・書くものが無い / 1 = 書けない・答えが取り込み方の表に合わない / 2 = 既存の宣言が壊れている |
| `drift` | `--measure <ファイル> [--root <ディレクトリ>]` | ずれた項目ごとに 1 件（`name`・`declared`・`measured`） | `drifted`（件数） | 0 = 比べた / 1 = 測定の結果を読めない / 2 = 宣言が壊れている |
| `schema` | `[--out <ファイル>]` | — | — | 0 = 出した / 1 = 書けない |

`check` の 2 と 3 は、旧い 3（壊れている）と 2（無い・古い）を入れ替えた値である。新しい表で 3 は「前提が満たされない（`precondition`）」、2 は「読めない（`unreadable`）」を指す。`development-workflow` の `decl=` の読み方（`references/project-analysis.md`）は、この値で書き直す。

### `ndf statusline`

旧い `statusline-switch.sh` と表示の本体（旧い `statusline.sh`）を移す。`settings.json` は Claude Code と同じく `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）の下を読み書きし、symlink なら参照先へ書く（旧い #1576 の振る舞い）。表示の本体は `~/.claude/ndf-statusline.sh` へ置き、`statusLine.command` は `bash ~/.claude/ndf-statusline.sh` にする。

| 下位の命令 | 引数 | 結果の `items` | 結果の `metrics` | 終了コード |
| --- | --- | --- | --- | --- |
| `ensure` | — | 行った操作 1 件（`action`: `set`・`updated`・`kept`・`skipped`） | — | 0（`statusLine` が他の設定なら何もしない。`jq` は使わない） |
| `status` | — | `statusLine` の今の値と控えの有無 1 件 | — | 0 / 2 = `settings.json` を読めない |
| `set` | — | 控えた値と書いた値 1 件 | — | 0 / 1 = 書けない / 2 = `settings.json` を読めない |
| `restore` | — | 戻した値 1 件（控えが無ければ `statusLine` を消す） | — | 0 / 1 = 書けない / 2 = `settings.json` を読めない |

旧い実装が使う `jq` は使わず、JSON は Python で読み書きする。控えと排他のファイルの置き場（`.ndf-statusline-backup.json`・`.ndf-statusline.lock`）は旧いと同じにする。旧い版で控えた利用者が新しい版で `restore` できるためである。

### `ndf materials`

旧い `mode-materials.py` を移す。課題の本文は `gh` で読む（`ndf.adapters.github`）。

| 引数 | 結果の `items` | 結果の `metrics` | 終了コード |
| --- | --- | --- | --- |
| `--issue <番号>... [--root <リポジトリ>] [--out <判定の根拠のファイル>]` | 課題ごとに 1 件（`issue`・種類・公開インタフェースの変更の有無・触るファイルの候補・候補ごとのテストのファイル） | `issues`・`gaps`（件数） | 0 = 集めた / 1 = 本文を読めない課題がある（`gaps` に課題と理由） |

`--out` の下書きは、`row`・`mode`・`condition` を空にした判定の根拠のファイルで、conductor が埋めて `ndf loop sprint` に渡す。

### `ndf relay` に足す下位の命令

| 下位の命令 | 引数 | 結果の `items` | 結果の `metrics` | 終了コード |
| --- | --- | --- | --- | --- |
| `notice` | — | カットポイントのアナウンスの 1 文 1 件（`text`。外のときは理由で変わる） | `inside`（ラッパーの直接の子なら `true`） | 0 |
| `stop --all` | `--all`（名前と同時に渡さない） | 停止シグナルファイルを置いたラッパーごとに 1 件 | `stopped`（件数） | 0 / 1 = シグナルファイルを置けないラッパーがある |
| `account add` | `[<名前>] [--code <コード>\|-]` | 登録したアカウント 1 件 | — | 0 / 1 = 登録できない / 3 = 認可コードが要る |
| `account add-bedrock` | `[--profile <名前>] [--region <地域>] [--model <ID>] [--yes]` | 保存した従量の接続の宣言 1 件 | — | 0 / 1 = 呼べない・保存できない |
| `account add-api-key` | `<名前> --account <アカウント> [--key-file <パス>] [--monthly <ドル>]`（キーは標準入力かファイル） | 保存したキーの名前 1 件（キーの値は出さない） | — | 0 / 1 = 確かめに失敗・保存できない |
| `account api-key-monthly` | `<名前> <ドル>` | 宣言した月の額 1 件 | — | 0 / 1 = 書けない |
| `account api-key-use` | `[off\|always\|near-limit] [--weekly-at <百分率>]` | 使い方と閾値 1 件 | — | 0 / 1 = 書けない |
| `account list` | — | アカウント・キーごとに 1 件（残量・枠・月の額） | `accounts`・`keys`（件数） | 0 |
| `account capacity` | `<名前> <5 時間の枠\|-> <週の枠\|->` | 宣言した枠 1 件 | — | 0 / 1 = 書けない |
| `account remove` | `<名前>\|metered\|<API キーの名前>` | 外した登録 1 件 | — | 0 / 1 = 無い・書けない |
| `account check` | `metered\|<API キーの名前>` | 確かめた結果 1 件（状態は記録しない） | — | 0 = 呼べる / 1 = 呼べない |
| `context resolve` | `[--root <ディレクトリ>] [--policy current\|trial]` | 適用値ごとに 1 件（値と出所）。壊れた設定は現行の方式で返し、`config_error` を持つ | — | 0 |
| `context record` | `--verdict 再起動 --reason <理由> --work <識別子>`、または `--event 引継ぎ開始\|復元完了 --work <識別子>` | 追記した行 1 件 | — | 0 / 1 = 書けない |
| `context calibrate` | `[--days <日数>]` | 較正した見込み 1 件 | `samples`（標本の数） | 0 / 1 = 書けない |
| `context report` | `--work <識別子> \| --all [--since <日付>] [--from <時刻>] [--until <時刻>]` | 作業の区分ごとに 1 件（token・秒・費用・再起動・自動圧縮・品質・復元の量。取れない値は `null`） | `works`（件数） | 0 |

**キーの値・認可コード・アクセストークンを、結果 JSON・標準エラー・記録へ書かない。** 旧い実装の取り扱い（保存の置き場・ファイルの権限・表示の伏せ方）をそのまま移す。人へ残す判断（C1・C2）は頭の設計文書にある。

`context record` の理由の値（7 つ）と印の値（2 つ）以外は、使い方の誤りとして argparse の `choices` で拒む（旧いでは終了コード 2）。`context report` の `--format table` は持ち込まない。結果 JSON の `items` を表として読むのは呼び手である（行 `from-issue:3569-context-report-table`）。

旧い `context-policy.py` が書く記録と見込みのファイルの置き場は、旧いと同じ状態の置き場の下にする。1 回目の版の記録を新しい版の `report` と `calibrate` が読めるためである。

## 旧い実装と並ぶ間の状態の書き手

入れ替えの前は、旧いプラグインで動く利用者のセッションと、新しいプラグインだけの起動（試走）が同じ手元で並ぶ。同じ置き場の状態を両方が書くため、状態ごとに書き手と読み手、同時に書いたときの扱いを次のとおりに決める。どの状態も、**新しい副命令は旧い実装と同じ排他の取り方と同じ書き方を使い、形（キー・値の型）を足さない・変えない。** 形を足すと、旧い実装がその値を読めずに捨てる・壊れたものとして扱うためである。

| 状態 | 書き手 | 読み手 | 同時に書いたときの扱い（旧い実装の取り方。新しい副命令も同じにする） |
| --- | --- | --- | --- |
| アカウントの登録（アカウントの置き場のアカウントごとのファイル） | 旧い `relay.py account`・新しい `ndf relay account` | 両方の中継の起動・`account list` | アカウントごとの排他（`<置き場>/.locks/<名前>.lock`。filelock）を取ってから書き、一時ファイルからの置き換え（`os.replace`）で書く。排他のパスが同じため、旧いと新しいの書き手は互いに待つ。待ちの上限を超えたら書かずに失敗として返し（旧い `claude_accounts._with_lock` の `on_fail`）、新しい副命令は終了コード 1 で終わる |
| 再起動の方針の判定の記録（`<状態の置き場>/decisions/<リポジトリ>.decisions.jsonl`） | 旧い `context-policy.py record` と hook・新しい `ndf relay context record` | 両方の `report`・`calibrate` | 追記だけで書く（1 回の書き込みで 1 行）。読み手は読めない行を飛ばす（旧い `context_decisions.append_decision`・`read_decisions`）。追記どうしは行の順が入れ替わってもよく、読み手が `at` で並べ直す |
| 再起動の見込み（`<状態の置き場>/estimates/<リポジトリ>.cost-estimates.json`） | 旧い `context-policy.py calibrate`・新しい `ndf relay context calibrate` | 両方の `resolve` | 一時ファイルからの置き換えで書く（旧い `jsonio.write_atomic`）。後に書いた方が残る。どちらも同じ判定の記録から作るため、後勝ちで値は食い違わない |
| statusline の控え（`.ndf-statusline-backup.json`）と `settings.json` の `statusLine` | 旧い `statusline-switch.sh`・新しい `ndf statusline` | 両方の `status`・`restore` | 排他のファイル（`.ndf-statusline.lock`）に排他のロックを取ってから書く。旧い実装は `flock`（コマンド）で、新しい副命令は同じファイルに filelock（`fcntl.flock`）で取るため、両者は互いに待つ。`settings.json` は一時ファイルからの置き換えで書く |
| 表示の本体（`~/.claude/ndf-statusline.sh`） | 旧い `ensure`・`set`、新しい `ensure`・`set` | Claude Code の statusline | 上の排他の中で置き換える。新しい側は旧い `statusline.sh` を振る舞いを変えずに移すため、後勝ちで表示は変わらない |
| プロジェクトの宣言（`.ndf/project.json`） | 旧い `project-decl.py write`・新しい `ndf project write` | 両方の `check`・`drift`、宣言を読む副命令 | 一時ファイルからの置き換えで書く（旧い `jsonio.write_atomic`）。後に書いた方が残る。どちらも同じ測定と答えの取り込み方の表から書く |

排他の上限の秒数と、filelock と `flock`（コマンド）が同じファイルで互いに待つことは、実装の単体テストで確かめる（未確認）。

## 本文の書き換えの形

| 書き換える行 | 形 |
| --- | --- |
| 冒頭の注記（「今の名前のまま残した」） | 10 本の名前を並びから外す。残る名前が無くなった注記は消す |
| 置き場を探すコマンド（`for R in ... resolve.sh ...` の 12 ファイル） | `command -v ndf >/dev/null 2>&1 \|\| { echo "ndf が PATH に無い: <理由と直し方>" >&2; exit 3; }` の 1 行にする。正本は `development-workflow/references/scripts-lookup.md` で、ほかのファイルはこの節を指す |
| `$SCRIPTS` の後に開いている子の旧いスクリプトを呼ぶ行 | 残す。旧いプラグインを候補に書かない。既定は、`$SCRIPTS` が空なら打たずに飛ばし、「<スクリプト名> は <子の記号> #<番号> が副命令に置き換えるまで新しいプラグインだけでは打てない。飛ばした」の 1 行を出す形（`[ -n "${SCRIPTS:-}" ]` の確かめ）にする。止まる形（理由を標準エラーへ書いて終了コード 3）は、後の手順がそのスクリプトの出力（書くファイル・終了コード）を入力にしていて、飛ばすと誤った入力で進む行だけに使う。止まる形を使う行は、実装のプランに行ごとに理由を書く |
| 10 本の呼び出し | 上の「旧い副命令の行き先」の「新しい」の列の呼び出しへ書き換える |
| 回す 2 機能の呼び出し | 「この機能は <子の記号> #<番号> が `ndf` の副命令に足す。新しいプラグインだけではまだ打てない」の 1 文にする |
| `development-workflow` の手順 0 | `ndf project check` を `$SCRIPTS` の有無に依らず打ち、`decl=` に終了コードを出す。worktree の設定の確かめ（`worktree-setup.sh`）と MVV の確かめ（`project-mvv.py`）は `$SCRIPTS` が空なら飛ばし、`宣言:` の行に理由を書く |
| `official-skills-autoloader` の導入の手順 | 取得元（`anthropics/skills`）を示して同意を得た後に、`claude plugin marketplace add` と `claude plugin install` を打つ |
| `statusline` の使い方 | `ndf statusline status`・`set`・`restore` を名前で打つ |
