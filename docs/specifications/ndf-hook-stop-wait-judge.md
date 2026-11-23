# stop-wait: 本文で人の回答を待って終えた応答を、ルールと軽いモデルで見分ける

hook の guard `stop-wait`（Stop で動く）は、最後の応答が `AskUserQuestion` を使わずに本文で人の回答を待って終えたら、
変更を要求して `AskUserQuestion` で問わせる。判定は 2 つの手順で行う。**先に 10 系から移したルール（`reply_wait`）を当て**、
ルールが待ちと読んだ本文は軽いモデルを呼ばずに止める。ルールで決まらない本文だけを、Claude Code のセッションに限って
軽いモデル（思考を切った haiku の最小構成の `claude -p`）に 1 回だけ問い、`wait` なら止め、`report` なら通す。

例: conductor が報告の最後を「どちらにするか決めていただければ進めます。」で終えた（#4000 の 1 回目）。

| 順 | 起きること |
| ---: | --- |
| 1 | Stop の hook が `stop-wait` を呼ぶ。本文があり、最後の利用者の入力の後に `AskUserQuestion` を使っていない |
| 2 | ルールが「いただければ」（依頼の語）で待ちの文と読み、種類 `prose_wait` を返す |
| 3 | 軽いモデルを呼ばずに変更要求を返す。会話へ返す文は当たった文（「どちらにするか決めていただければ進めます。」）を添え、承認ゲートと `asking.md` の区分に当たれば `AskUserQuestion` で出し直し、当たらなければ推奨の案で決めて続けるよう伝える |
| 4 | hook の計測の行に `reply=prose_wait`・`judge=rule`・`judge_ms=0`・`judge_calls=0` が残る。本文は残らない |

同じ conductor が「どちらにするかの指示をください。」の後に箇条書きでない報告の行を 9 行続けた本文では、ルールは最後の 3 行
だけを見て `report` を返す。そのときは軽いモデルにコードブロックを除いた本文（末尾 1,600 字まで）を渡し、`wait` の回答で止める。

| 何を読むか | 正本 |
| --- | --- |
| guard の定義（イベント・順・`limits`・`summary`） | [`contracts/hooks/guards.json`](../../plugins/ndf/contracts/hooks/guards.json) の `stop-wait` |
| 判定の順と変更要求の文 | [`guards/stop_wait.py`](../../plugins/ndf/src/ndf/domains/hooks/guards/stop_wait.py) の `check` と `REASON` |
| ルールの語と文の分け方 | [`reply_wait.py`](../../plugins/ndf/src/ndf/domains/hooks/reply_wait.py) の定数と `read_prose` |
| 判定のリクエスト（hook の側）・送信先の確かめ・休みのマーカー | [`reply_judge.py`](../../plugins/ndf/src/ndf/domains/hooks/reply_judge.py) |
| 判定の子・質問の文・判定の例の組の確かめ | [`judge_child.py`](../../plugins/ndf/src/ndf/domains/hooks/judge_child.py) の `SYSTEM`・`QUESTION`・`run_cases` |
| 値（モデル・秒・字数・休む間・周の数・送信先の確かめ） | [`contracts/limits/`](../../plugins/ndf/contracts/limits/) の `hook.stop-wait.judge`・`hook.budget.stop`・`hook.budget.stop.codex`・`hook.pushback` |
| 判定の例の組 | [`contracts/hooks/reply-judge-cases.json`](../../plugins/ndf/contracts/hooks/reply-judge-cases.json) |
| 問いの区分と出し方 | `development-workflow` の [`references/asking.md`](../../plugins/ndf/skills/development-workflow/references/asking.md) |

## 背景

11 系の作り直し（#1142）で、10 系の文の単位の判定（`scripts/lib/wait_notice.py` の `classify_reply_wait`）は、言い回しの列と
末尾 4 行の一致（11.0.22 の `classify_reply`）へ置き換わった。2026-11-21〜23 に本文で判断を求めた応答を 5 回通し、質問を
引用した報告を 1 回止めた。10 系の例の組 225 件（利用者の入力の直前に終わった実際の応答）では、待ちの 79 件のうち 10 系は
74 件、11.0.22 は 23 件しか拾わなかった。文字列の一致は、見逃さないよう広げるほど引用を誤って止め、絞るほど言い回しの数だけ
見逃す。

そこで、見ればわかる待ちは 10 系のルールで止め（モデルの費用と待ちをかけない）、ルールで決まらない本文だけを軽いモデルに
問う形にした。

## 対象範囲

| 対象 | 扱い |
| --- | --- |
| Claude Code の Stop | ルールの後に軽いモデルを呼びうる |
| Codex の Stop | ルールだけで決める（`judge=skip:runtime`） |
| SubagentStop（worker・supervisor の応答） | 対象外。サブエージェントは人へ問えず、待ちを伴う終え方は上の層の報告の規約が受け止める |
| guard を止める | 環境変数 `NDF_HOOK_OFF` に `stop-wait` を含める（ほかの guard と同じ切り方） |

## 決定と理由

| # | 決定 | 理由 |
| ---: | --- | --- |
| 1 | 10 系の `classify_reply_wait` を書き直さずに移し、軽いモデルの前に置く | 10 系が実際の応答で測った正誤を保つ。見ればわかる待ちにモデルの費用と待ちをかけない（利用者の決定） |
| 2 | ルールはかぎ括弧の引用の中身を見ず、待ちの文がどれも別の場所（セッション・レーン・計画・オーガナイザー）の待ちの報告なら通す | 10 系は引用と別の場所の待ちを待ちと読む。2 つを外しても 225 件の正誤（74 件・誤検出 5 件）は変わらない |
| 3 | 判定は今の Stop の hook の中で同期に待つ。Claude Code の Stop の予算は締め切り 10 秒・余白 1.5 秒・登録の timeout 12 秒・p95 5,000 ms、判定の上限は 8 秒 | 呼ばない条件・変更要求の上限・上限の後の待ちの知らせ・計測をそのまま使える。prompt 型の hook と背景で再開させる hook はそれらを使えず、Codex に同じ仕組みが無い。8 秒は実装の確かめで 1 回 2.9〜5.5 秒かかった実測から決めた（conductor の決定・案 A） |
| 4 | 軽いモデルの起動は hook の閉包の外の子プロセス（`judge_child`）が既存の `call_claude` で行う | hook の import の表は hook を軽く保つために `ndf.adapters.claude` を禁じる。子が学習に使わない設定の確かめなどの前置きを `call_claude` と共有する |
| 5 | 1 つの Stop で 1 回だけ打ち、通すべき報告の形を質問の文に例として置く | 同じ文への回答は独立に揺れず、2 回打っても外れが減らなかった。残る揺れは変更要求の上限（入力ごと 2 回）が受け止める |
| 6 | 回答が得られないとき（時間切れ・起動できない・認証の失敗・形の違い）はルールの判定で決める | 止める側へ倒すと認証の切れた環境で毎回止まり、通す側へ倒すと見逃しが増える |
| 7 | 質問へ渡すのはコードブロックの行と空行を除いた本文の末尾 1,600 字 | 直近 1,317 件の応答の 99% 点が 1,595 字で、質問を含む 15 件のどれも末尾 1,600 字に収まった（800 字では 2 件が外れた） |
| 8 | 判定の子は重い起動のスロットを待たない | テストが並ぶ間の Stop をすべて上限まで待たせない |
| 9 | 判定の例の組を実際のモデルで確かめるコマンド（`ndf hooks judge-cases`）を置き、CI では偽の判定器で振る舞いだけを縛る | CI に認証情報を置かず、揺れで CI を落とさない。質問の文の良し悪しは実際のモデルでしか測れない |
| 10 | 起動の失敗と送信先の不一致の後は、休む間（既定 600 秒）だけ子を起動しない。時間切れとスロットの空き待ちでは休まない | 使えない環境で Stop ごとに起動し直さない。時間切れと混み具合は環境の性質でない |
| 11 | 本文を送るのは、送信先がセッション自身の Claude の API と同じと確かめられる Stop だけ | 利用者のデータをセッションの外の接続先へ送らない |
| 12 | 判定の `claude` はプロジェクトの外の空の作業ディレクトリで起動する | プロジェクトの中で起動すると `--setting-sources ""` でも上の `CLAUDE.md` を読み、入力が 910 → 6,650 token に増えて引用の例を `wait` と読んだ |
| 13 | 変更要求の文は 10 系の `WAIT_REASON` を出発点にし、区分の一覧と出し方は写さず `asking.md` を指す | 経路を hook の文の中で分けない。ルールで止めたときだけ当たった文を添え、なぜ止めたかを AI が読めるようにする |

## 仕様

### 構成要素

| 要素 | ファイル | 責務 |
| --- | --- | --- |
| ルールの判定 | `ndf.domains.hooks.reply_wait`（hook の閉包の中・標準ライブラリと `ndf.adapters.markdown` だけ） | 本文の行（`prose_lines`）を文に分け、文の終わりの形・依頼の語・条件の添え書き・操作の案内・見出しを見て、本文の種類（下の表）を返す |
| guard `stop-wait` | `ndf.domains.hooks.guards.stop_wait` | 下の「判定の順」で変更要求か通過を返し、計測の notes を書く |
| 判定のリクエスト | `ndf.domains.hooks.reply_judge`（hook の閉包の中・標準ライブラリだけ） | 質問へ渡す本文を作る（`judge_text`）。送信先の hook の側の確かめ（`parent_launch`）。子を新しいプロセスの組で起動して秒の上限で待ち（`ask`）、どの終わり方でも組を止めて回収する。休みのマーカーの読み書き |
| 判定の子 | `ndf.domains.hooks.judge_child`（hook の閉包の外） | 送信先の子の側の確かめ（`endpoint_refusal`）と親の実行ファイルの同一性の確かめの後、`call_claude` で 1 回だけ打ち、`wait` / `report` か失敗の種類を 1 行の JSON で返す |
| hook の文脈 | `HookContext`（`context.py`） | `has_reply_prose`・`asked_this_turn`（最後の利用者の入力の後に `AskUserQuestion` の `tool_use` があるか）・`reply_reading`（`read_prose` の返り）・`last_reply_kind` |
| 変更要求の数え手 | `Counter`（`counter.py`） | 上限の手前か（`pushback_left`。数えない）と、今の入力の中でモデルが `wait` と回答したか（`mark_model_wait`・`model_waited`） |
| 締め切り | `main.py`・`dispatch.py` の `GuardServices.deadline` | SIGALRM と同じ絶対時刻の締め切りを guard へ渡す。余白は guard が 1 回だけ引く |

### ルールの判定の種類

| 種類 | 意味 | guard の扱い |
| --- | --- | --- |
| `none` | コードブロック・引用・表のほかに本文が無い | 通す（`skip:empty`） |
| `prose_wait` | 本人への待ちの文（回答待ち・承認待ち）がある。かぎ括弧の中は見ない | 変更要求（`rule`）。モデルを呼ばない |
| `elsewhere_wait` | 待ちの文がどれも別の場所の待ちの報告（`ELSEWHERE_RE`） | 通す（`skip:elsewhere`） |
| `closing_report` | 待ちの文が無く、定型の結び（「引き続きよろしくお願いします。」）の前が完了の報告（「ました」「でした」の言い切り）だけ | 通す（`skip:closing`）。モデルを呼ばない |
| `heading_wait` | 見出しで承認・選択を明示的に求め、後に本文が続く | モデルの判定に従う。モデルを呼ばない Stop・回答しない Stop では変更要求 |
| `report` | 上のどれでもない | モデルの判定に従う。モデルを呼ばない Stop・回答しない Stop では通す |

### 判定の順

上から順に見て、最初に当たった 1 つを notes の `judge` に書く。

| 条件 | 結果（`judge`） |
| --- | --- |
| 本文が無い | 通す（`skip:empty`）。ルールを当てない |
| 最後の利用者の入力の後に `AskUserQuestion` を使った | 通す（`skip:asked`）。ルールを当てない（選択肢の案内の文をルールは待ちと読むが、問いは既に届いている） |
| ルールが `elsewhere_wait` / `closing_report` / `prose_wait` | それぞれ上の表のとおり（`skip:elsewhere` / `skip:closing` / `rule`）。`prose_wait` は変更要求の上限に達していても返す |
| 質問へ渡す本文（`judge_text`）が空 | `skip:no_prose` |
| 背景の作業（Stop の入力の `background_tasks`）が残る | `skip:background` |
| ランタイムが `hook.stop-wait.judge` の `runtimes`（`claude`）に無い | `skip:runtime` |
| 今の入力の後の変更要求が上限（`hook.pushback` の 2 回）に達した | `skip:limit` |
| 表の前の guard が変更要求か拒否を既に返した | `skip:decided` |
| 起動の失敗の休みのうち | `skip:rest` |
| 残りの秒（締め切り − 余白 − 今 と設定の `seconds` の小さい方）が 0.5 秒を切る | `skip:time` |
| 親の起動を確かめられない（`parent_launch` が None） | `skip:endpoint` |
| この起動の形の送信先の不一致の休みのうち | `skip:rest` |
| 上のどれでもない | 判定の子に問う。`wait` → 変更要求（`model`。数え手にモデルの `wait` を置く）、`report` → 通す（`model`）、回答なし → 休みのマーカーを書いたうえで通す（`fallback:<種類>`） |

`skip:*` と `fallback:*` の行は、ルールが `heading_wait` なら変更要求に読み替える。`skip:no_prose` と `skip:limit` の行は、
上限に達していて今の入力の中でモデルが `wait` と回答していれば変更要求を返す。dispatch はそれを上限で通し、待ちの知らせを
送信待ちへ書く（上限の後に変更要求を返すのは待ちの知らせへ渡すためだけである）。

### 質問へ渡す本文

- 本文を `markdown.code_lines` と同じ数え方で行に分け、コードブロックの行と空行を除いて改行でつなぎ、`max_chars`（1,600）を超えれば末尾を渡す
- 本文が上限を超えたときは、モデルへ判定を委ねた見出し（`ProseReading.delegated`）のうち末尾に入らないものを `## ` を付けて先頭に残す
- コードブロックの行の数え方が行の数と合わない本文は空として扱い、送らない
- 質問の文（`QUESTION`）は本文を `<reply>` と `</reply>` で囲み、`wait` か `report` の 1 語を求める。`report` の例として、他人の質問を「」で引用した経緯の説明・ほかのセッションやレーンの承認待ちの伝達・承認を求める資料の用意や作業の結果の報告の 3 つを並べる

### 判定の子の起動

| 項目 | 値 |
| --- | --- |
| 子の起動 | `[sys.executable, "-P", "-m", "ndf.domains.hooks.judge_child"]`、新しいプロセスの組。標準入力に `text`・`seconds`・`parent_argv`・`project_dir`・`parent_exe`・`parent_exe_id` |
| 子へ渡す環境 | hook の環境から環境変数の表（`contracts/domains/env.json`）の `child` が `drop` の行（`CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` など。接続と認証の変数を含まない）を外し、`PYTHONPATH` と `MAX_THINKING_TOKENS=0` を入れる |
| `claude` の起動 | 親の実行ファイルの実パス（PATH で引き直さない）。最小構成（`--setting-sources ""`・`--strict-mcp-config`・`--tools ""`）・`--model haiku`・判断の権限・会話を残さない。プロジェクトの外の空の一時ディレクトリで起動する（候補の親は一時領域・`/tmp`・`/var/tmp`・`/dev/shm` のうち、自身と祖先に `CLAUDE.md`・`.git` などが無いもの。無ければ `launch`） |
| 前置き | `LaunchOptions(slot_wait=0, new_group=False, cli_floor=False, ensure_training=False)`。スロットを待たない・`claude` を子と同じ組に置く・CLI の下限を確かめない（親が今動いている同じ実体なので満たしている）・学習に使わない設定の記録が無いか期限切れなら確かめ直さずに `refused` |
| 後始末 | 時間切れ・割り込みでは組へ SIGTERM、0.2 秒おいて SIGKILL を送ってから回収する。正常に終えたときも組へ SIGKILL を送ってから回収する |

### 送信先の確かめ

安い順に見て、どこかで確かめられなければ呼ばない（決定 11）。値は `hook.stop-wait.judge` の `endpoint` にある。

| 手順 | 呼ぶ条件 |
| --- | --- |
| hook の側・ランタイム | Claude Code のセッション |
| hook の側・親の起動 | `CLAUDE_CODE_ENTRYPOINT` が `entrypoints`（`cli`・`sdk-cli`）のどれか。`CLAUDE_PROJECT_DIR` が絶対パス。`CLAUDE_CONFIG_DIR` が無いか絶対パス。hook のプロセスから `ancestors`（8）代のうちで最も近い Claude Code の実プロセス（`/proc/<pid>/exe` の実パスが `cli_exe` に当たる）が見つかり、その前に `script_hosts`（`node*`）の祖先が無く、実行ファイルのリンクの先が ` (deleted)` で終わらない。`/proc` が無い環境（macOS）では呼ばない |
| 判定の子・実効の設定 | 親の起動引数に `flags`（`--settings`・`--setting-sources`）が無い。`CLAUDE_CONFIG_DIR`・`ANTHROPIC_CONFIG_DIR` が相対パスでない。`account_dir.effective_env` の返りの `unresolved` が空で、`apiKeyHelper` が無く、`keys`（`awsAuthRefresh`・`awsCredentialExport`・`gcpAuthRefresh`）をどの層も定義せず、設定の層が出所の変数に `env_names`（`ANTHROPIC_`・`CLAUDE_CODE_USE_`・`CLAUDE_CODE_SKIP_`・`AWS_`・プロキシの変数など）で始まるものが無い |
| 判定の子・実行ファイル | `stat(parent_exe)` の `[st_dev, st_ino]` が、hook の側が親から読んだ `parent_exe_id` と同じ |

子の側で外れたら `claude` を起動せずに `failure="endpoint"` を返す。理由の項目名は計測にも hook の出力にも載せない。

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| ---: | --- | --- |
| I1 | 1 つの Stop で判定の子を起動するのは高々 1 回で、子が打つ `claude -p` も 1 回 | — |
| I2 | ルールが `prose_wait` と読んだ本文では、モデルを呼ばない | — |
| I3 | 本文が空・`AskUserQuestion` を使ったターンの Stop では、ルールを当てない | — |
| I4 | 判定の子と `claude` は、どの終わり方でも止めて回収してから hook が戻る | 割り込みの例外は回収の後に投げ直す |
| I5 | 計測とマーカーに応答の本文・API キー・起動引数の値を書かない。本文は会話へ返す変更要求の文に当たった文として 120 字まで入るだけ | — |
| I6 | 回答が得られないときは、ルールの判定（`heading_wait` なら変更要求、ほかは通す）で決める | — |
| I7 | 判定の上限（`seconds`）≤ Claude Code の Stop の締め切り − 余白 ≤ 登録の timeout | `ndf hooks check-budget` が落ちる |
| I8 | hook の閉包は `ndf.adapters.claude` を含まない（`judge_child` を hook の側から import しない） | hook の import の表の検査が落ちる |

## データ・設定

`contracts/limits.json`:

| 行 | 値 |
| --- | --- |
| `hook.stop-wait.judge` | `model` `haiku`・`runtimes` `["claude"]`・`seconds` 8・`max_chars` 1600・`rest_seconds` 600・`lock_seconds` 0.2・`rounds` 3・`auth_markers` `["Not logged in", "Invalid API key"]`・`endpoint`（上の「送信先の確かめ」） |
| `hook.budget.stop`（Claude Code） | `p95_ms` 5000・`deadline` 10・`margin` 1.5・`timeout` 12・`inject_chars` 2000 |
| `hook.budget.stop.codex` | `p95_ms` 300・`deadline` 3.5・`margin` 1.5・`timeout` 6・`inject_chars` 2000（モデルを呼ばないので判定の秒を足さない） |

登録の timeout（`hooks/claude.json`・`hooks/codex.json`）は `ndf hooks render` が予算から作り直す。手で書かない。

計測の行（`<状態の保存先>/hooks/<日付>.jsonl`）の `stop-wait` の notes:

```json
{"reply": "report", "judge": "model", "judge_ms": 3412.5, "judge_calls": 1}
```

`reply` はルールの種類（ルールを当てない `skip:asked` では入らない）、`judge` は `rule` / `model` / `fallback:<種類>` / `skip:<理由>`。
失敗の種類は `timeout`・`launch`・`exit`・`shape`・`no_slot`・`refused`・`usage_limit`・`auth`・`endpoint` のどれかである。
`auth` は判定の子が付ける（`call_claude` の失敗の種類が無く、本文が `auth_markers` を含むとき）。

休みのマーカー（`<状態の保存先>/hooks/judge-rest.json`。所有者だけが読める 0600）:

```json
{"launch": {"at": 1795400000.0, "failure": "auth"}, "endpoint": {"3f2a9c0d1e4b5a67": 1795400000.0}}
```

- `launch` は起動の失敗（`launch`・`refused`・`usage_limit`・`auth`）で書き、どの起動の形も休む
- `endpoint` は送信先の不一致で書き、送信先の確かめの対象（`endpoint_scope`。接続を変えるオプションの名前・プロジェクトの根・設定ディレクトリの値・接続の変数の名前・親の実行ファイルのパスと同一性の SHA-256 の先頭 16 桁）ごとに休む。プロンプトや値は材料に入れない
- 更新は `.judge-rest.lock` の `flock`（`lock_seconds` まで待つ。取れなければ書かない）の中で、一時ファイルからの置き換えで行う

判定の例の組（`reply-judge-cases.json`）は `{"version": 1, "cases": [{"id", "text", "expect", "source"}]}` の形で、#4000 の見逃しの
文（4 回目の 2 文を分けて 6 件）・引用・別の場所の待ち・作業の報告・承認資料の報告の 10 件を持つ。

## 運用

### 判定の例の組を確かめる

```bash
ndf hooks judge-cases [--cases <パス>] [--rounds <周の数>]
```

各例を周の数（既定は設定の `rounds` の 3）だけ PATH の `claude` で打ち、1 周でも外れた例を結果 JSON の `items` に出して
`stopped`（終了コード 1）で終える。**質問の文（`QUESTION`）か判定の例の組を変える Pull Request では、マージの前に打ち、外れ 0 件の
出力を Pull Request に貼る。** 外れたら質問の文の例を直して打ち直す。学習に使わない設定の記録が無い・CLI が古いなどで断られたら、
残りを打たずに `refused` で止まり、理由の文が対処を示す。

### 判定を直すとき

直すのは質問の文と判定の例の組の 2 つで、言い回しの列を足して直さない。ルールの語（`reply_wait`）を変えるときは、
10 系から移した例の組（`tests/domains/hooks/fixtures/wait-corpus.json`、225 件）の正誤を下回らないことをテストが縛る。

### 費用と遅れ

ルールで決まらない Claude Code の Stop では、会話の終わりが子の起動と 1 回の回答の分（実測 2.9〜5.5 秒）遅れ、1 回あたり
0.00006〜0.0008 USD かかる。上限の 8 秒に当たる Stop は 8 秒遅れ、Stop の最悪の待ちは締め切りの 10 秒である。

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| モデルを呼ばない環境 | Codex のセッション・起動の引数や設定で接続を選ぶセッション・SDK に埋め込んだ起動・相対の `CLAUDE_CONFIG_DIR`・`/proc` の無い環境（macOS）・node で動く Claude Code（npm の導入）では、ルールの判定だけで決まる |
| 回答の揺れ | 待ちでない応答を `wait` と読むことがある。そのとき AI は応答を 1 つ足し、2 回目の変更要求の後は上限で通る |
| 末尾 1,600 字の外の質問 | 見出しで委ねたもの以外は見えない |
| ルールの誤検出 | 225 件のうち待ちでない 146 件の 5 件をルールが待ちと読む。引用の記号を使わずに他人の質問をそのまま載せた本文も待ちと読む |
| 背景の作業が残る応答 | ルールの判定でしか見えない |
| 認証の失敗の文言 | `auth_markers` の 2 つのほかは `exit` になり休まない。計測の `fallback:exit` の多さで分かる |
| 見出しの問いのルール | `heading_wait` の見分け方の作り直しは [#4310](https://github.com/devbasex/ai-plugins/issues/4310) |
| Codex と人のいない実行の区分付きの質問 | 本文で出す経路は [#4316](https://github.com/devbasex/ai-plugins/issues/4316) |

## テスト観点

テストは `plugins/ndf/tests/domains/hooks/` の `test_reply_wait.py`・`test_stop_wait.py`・`test_reply_judge.py`・`test_judge_child.py`・
`test_judge_child_read.py`・`test_judge_cases_cli.py`・`test_context.py`・`test_counter.py`・`test_budget.py` にある。軽いモデルは偽の
判定器に、`/proc`・時計・`Popen`・`killpg` は偽物に置き換え、実プロセスと実時間を仕様にしない。

| 観点 | 満たすこと |
| --- | --- |
| ルールの正誤 | 10 系の例の組 225 件で、待ち 79 件のうち 74 件以上を拾い、待ちでない 146 件のうち待ちと読むのが 5 件以下であること。#4000 の見逃しの 5 文はルールだけで止まり、判定器が呼ばれないこと |
| 通す形 | 引用の誤検出の例はルールが `report` を返し判定器の `report` で通ること。「別のレーンは承認を待っています。」は `skip:elsewhere`、`AskUserQuestion` を使ったターンの選択肢の案内は `skip:asked` で、判定器を呼ばないこと。別の場所の待ちに本人の質問が続けば `rule` で止まること |
| 本文全体の回答 | ルールが `report` を返す長文（質問の後に報告の行が 9 行）で判定器に質問の行が渡り、コードブロックの中の質問は渡らないこと。判定器の `wait` で変更要求、`report` で通過になること |
| 呼ばない条件 | 判定の順の表の `skip:*` のそれぞれで判定器が呼ばれず、どれでもない Stop で 1 回だけ呼ばれること |
| 回答なし | 時間切れ・起動の失敗・認証の失敗・形の違いでルールの判定と同じ結果（`heading_wait` は変更要求）になり、時間切れと割り込みでも組へ送ってから回収すること |
| 休み | 起動の失敗と送信先の不一致の後は休む間だけ呼ばず、時間切れとスロットの空き待ちでは休まないこと。送信先の不一致の休みは同じ起動の形だけに効くこと |
| 上限の後 | 前にモデルが `wait` と回答した入力で上限に達し、本文がルールで `report` のとき、変更要求を返して待ちの知らせが送信待ちに入ること |
| 送信先 | Codex・エントリポイント `sdk-ts`・`CLAUDE_PROJECT_DIR` の欠け・相対の `CLAUDE_CONFIG_DIR`・`/proc` の読めない環境・node の祖先・`--settings` 付きの近い方の Claude Code・設定の層の `ANTHROPIC_BASE_URL` や `apiKeyHelper`・差し替わった実行ファイルのどれでも `claude` を起動しないこと。子の環境が出所の `ANTHROPIC_BASE_URL` では呼ぶこと |
| 最小構成 | 起動の引数が最小構成と設定のモデルで、`MAX_THINKING_TOKENS=0` を渡し、作業ディレクトリがプロジェクトの外の空のディレクトリであること |
| 変更要求の文 | ルールで止めたときだけ当たった文（120 字まで）を添え、モデルで止めたときは本文を引かないこと。文言は照合しない |
| 設定と予算 | `limits.json` の値を替えると起動の引数と渡す字数が替わること。`ndf hooks check-budget` が通ること |
| 実際のモデル | `ndf hooks judge-cases` が 3 周のうち 1 周だけ外す偽の判定器で外れを出して 1 で終わること。マージの前に実際のモデルで外れ 0 件であること（手動） |
| 手動 | リリース後、本文で質問を書いて応答を終え、`stop-wait` が変更を要求し計測に `judge` が残ること |

## 関連リンク

- [ndf-relay-goal-skill-and-prose-wait.md](ndf-relay-goal-skill-and-prose-wait.md)（10 系の本文の待ちの差し戻しの経緯）
- [ndf-relay-pending-question-guard.md](ndf-relay-pending-question-guard.md)（中継の下の質問の守り）
- [ndf-ask-guard.md](ndf-ask-guard.md)（問いの区分を判定する guard）
- 課題 [#4000](https://github.com/devbasex/ai-plugins/issues/4000)・スプリントの Pull Request [#4312](https://github.com/devbasex/ai-plugins/pull/4312)
- 設計の記録（消す前の版）: [要求仕様](https://github.com/devbasex/ai-plugins/blob/d6788cfce3c8e6e12392533156f4bad3d228f130/issues/issue-4000-requirements.md)・[設計](https://github.com/devbasex/ai-plugins/blob/d6788cfce3c8e6e12392533156f4bad3d228f130/issues/issue-4000-design.md)・[詳細設計](https://github.com/devbasex/ai-plugins/blob/d6788cfce3c8e6e12392533156f4bad3d228f130/issues/issue-4000-design-detail.md)
