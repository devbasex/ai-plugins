# #1959 の詳細設計: 起動の設定・承認と、full の起動の絞り込み

設計文書 [issue-1959-design.md](./issue-1959-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは断りが無ければ `plugins/ndf/` からの相対で書く。決定と受け入れ条件の中身は設計書と要求仕様（[issue-1959-requirements.md](./issue-1959-requirements.md)）が持つ。この文書の「full の起動」は、利用者の設定・プラグイン・hook をそのまま読み込み、Skill とサブエージェントが使える `claude -p` の起動である（要求仕様の「全部入り」）。「相手役」はレビューなどを頼むほかの AI の CLI である。「工程」の名前は、worker の作業の名前か Skill の名前である。「安全装置」を外す指定の対象と条件は、設計書の「安全装置を外す指定」にある。

## 地図

パッケージ・モジュール構成（触るファイルのうちコードと表。`*` は新設）:

```text
src/ndf/
  __main__.py                    使用量ログの書き込み処理を差し込む
  cli.py                         サブコマンド launch-config・delegate-run の行
  adapters/
    claude.py                    full の引数・結果の欄・起動記録のインターフェース
    agents.py                    相手役の子の環境
    launch_settings.py *         起動の設定の読み取り・指定の解決・安全装置の引数
    launch_approval.py *         ハッシュ・承認の記録・依頼と答え
    launch_candidates.py *       候補のディレクトリの読み取り・所有の確かめ・退避と中断の後の片付け
    secret_forms.py *            秘密の形の表と伏せる処理（domains/project/secret.py から移す）
  core/
    worker.py                    指定の取得・子プロセスごとの記録
    converge/participants.py     安全装置の引数の取得
    plan/runner.py・steps.py・transition_decide.py  実行の回数と子へ渡す環境変数
  progress/invoke.py             Skill の起動の指定と記録
  domains/
    records/usage.py・usage_decide.py   行の書き込み・費用の計算
    settings/launch_config_cli.py *     ndf launch-config
    settings/delegate_run_cli.py *      ndf delegate-run
    hooks/guards/launch_answer.py *     承認の問いの答えの記録
    crossreview/seats.py         読めなかった担当の直し方の文
```

依存の向き: 層は下から contracts・adapters・core・progress・domains の順で、下の層は上の層を読み込まない。`launch_settings` は `launch_approval`・`agents`・`secret_forms` を読み、`launch_approval` は `launch_settings` を読まない（承認の対象は、読んだ値を引数で受ける）。`claude` は `launch_settings` の型を読み、`launch_settings` は `claude` を読まない。使用量ログへの書き込みは、`claude.py` が持つ差し込みのインターフェースを `__main__.py` が埋める。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | 新設（N）と変更（C） |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/launch_settings.py` | 新設 | 起動の設定を読んで検証し、起動 1 回の指定を返す | launch_approval・agents・git・contracts | `Settings`・`StepSpec`・`Grant` | N: `load`・`step_spec`・`check_definitions`・`approval_target`・`prepared`・`safety_args` |
| `src/ndf/adapters/launch_approval.py` | 新設 | 承認のハッシュと記録を持つ | identity・git | モジュール | N: `repo_key`・`digest`・`approved`・`append`・`put_question`・`record_answer`・`approve_by_answer`・`pending_request`・`new_candidate`・`reflect`・`recover`・`reflected` |
| `src/ndf/adapters/launch_candidates.py` | 新設 | 候補のディレクトリ内の記録を読む。読む対象は通常ファイルに限り、シンボリックリンクはたどらない。候補の中が NDF の作ったものだけかを確かめ、退避・消去・元の名前へ戻す。作成途中の候補のディレクトリは、作る前から隣に残す記録で NDF が作ったと確かめたものだけを片付ける。どの候補を片付けるかは `launch_approval` が履歴から決める | — | `Read` | N: `write_launch_record`・`raw_record`・`record_object`・`ownership`・`stamped_version`・`regular_version`・`same_stamp`・`set_aside`・`settle_aside`・`restore_aside`（`launch_approval.py` から分ける）・`start_draft`・`finish_draft`・`reclaim_drafts` |
| `src/ndf/adapters/secret_forms.py` | 新設 | 秘密の形の表と、当たる文字列を伏せる処理 | — | モジュール | N: `SECRET_VALUE`・`ASSIGNED_SECRET`・`MASK`・`redact`（`domains/project/secret.py` から移す） |
| `src/ndf/domains/project/secret.py` | 変更 | 移した表と処理を `secret_forms` から読み込む | secret_forms | モジュール | C: 定義の移動 |
| `src/ndf/adapters/claude.py` | 変更 | 起動の引数と結果 | launch_settings・claude_precheck | `ClaudeCall`・`ClaudeReply`・`LaunchOptions`・`LaunchRecorder` | C: `permission_args`・`_full_args`・`call_claude`・`_parse_reply`・`context_window`・`_prefaced`・`_launch_once`（止めた確かめを `PRECHECK_FAILURES` で失敗の理由へ直す）。N: `main_model`・`set_launch_recorder`・`LaunchLog`・`PRECHECK_FAILURES` |
| `src/ndf/adapters/claude_precheck.py` | 新設 | 起動の直前の確かめ（中継の確かめ直し・承認した起動の設定の照らし直し・CLI の最低バージョン）。止めた確かめと理由を返し、回答と失敗の理由への直しは `claude` が持つ。`claude` を読まない（読むと import の輪になる） | cli_floor | `Check`・`Stop` | N: `precheck_claude`・`require_claude`（`claude.py` の `_precheck`・`require_claude` から分ける） |
| `src/ndf/adapters/cli_floor.py` | 変更 | CLI の最低バージョンの確認が、確かめの間に待ったかを返す。自分で `--version` の終了を待った場合と、同じ実行ファイルのバージョン取得を別スレッドに任せて、その終了を実行ファイルごとの錠で待った場合は真を返す。待った後にキャッシュを使う場合も真である。バージョン取得を待たずにキャッシュを使った場合は偽を返す。呼び出し元が、待った間の取り消しを確かめ直すために使う | — | モジュール | C: `require`（戻り値を `bool` にする）。N: `_version_read` |
| `src/ndf/adapters/agents.py` | 変更 | 相手役の起動 | identity | `Launch` | C: `_start`・`_guarded_run`・`Launch`（`verify`） |
| `src/ndf/core/worker.py` | 変更 | worker の起動 | launch_settings・claude | `Caller` | C: `delegate`・`_args_for`・`_resume_until_written` |
| `src/ndf/progress/invoke.py` | 変更 | Skill を回す起動 | launch_settings・claude | `SkillCaller` | C: `_call_skill` |
| `src/ndf/core/plan/transition_decide.py` | 変更 | ステップごとの実行の回数を数える | — | `RunStep`・`StepState` | C: `_run`・`decide` |
| `src/ndf/core/plan/runner.py` | 変更 | ステップの子へ渡す値を組む | steps | モジュール | C: `run_step`・`_perform` |
| `src/ndf/core/plan/steps.py` | 変更 | 子の環境を重ねて起動する | run_log | `StepContext` | C: `_launch` |
| `src/ndf/core/converge/participants.py` | 変更 | 相手役の引数 | launch_settings | モジュール | C: `_sandbox_args`・`_review_args` |
| `src/ndf/domains/records/usage.py` | 変更 | `claude -p` の行を書く | jsonl | モジュール | C: `record_cli` |
| `src/ndf/domains/records/usage_decide.py` | 変更 | 起動 1 回の費用を求める | — | モジュール | N: `launch_costs` |
| `src/ndf/domains/settings/launch_config_cli.py` | 新設 | `ndf launch-config` の 4 つのサブコマンド | launch_settings・launch_approval | モジュール | N: `main`・`_draft`・`_show`・`_approve`・`_confirm_terminal`・`_revoke` |
| `src/ndf/domains/settings/delegate_run_cli.py` | 新設 | `ndf delegate-run`（委譲の起動） | launch_settings | モジュール | N: `main`・`build_command` |
| `src/ndf/domains/hooks/guards/launch_answer.py` | 新設 | 承認の問いの答えを記録する | launch_approval | モジュール | N: `check` |
| `src/ndf/domains/crossreview/seats.py` | 変更 | サンドボックスのためにファイルを読めなかったレビュー担当向けの直し方の文を、承認の手順にする | — | モジュール | C: `_unread_hint` |
| `src/ndf/cli.py`・`src/ndf/__main__.py` | 変更 | サブコマンドの登録と差し込み | usage | `COMMANDS` | C: 行の追加 |
| `contracts/domains/runtimes.json` | 変更 | `delegate_sandbox_args`・`sandbox_needs_userns` の列と、kiro・agy の引数 | — | 表 | — |
| `contracts/domains/env.json` | 変更 | 子へ渡す環境変数の 4 行 | — | 表 | — |
| `contracts/domains/ndf-files.json`・`locations.json` | 変更 | 起動の設定・承認の記録・問いと答え・候補・反映の完了の記録・MCP の定義のコピーの保存先 | — | 表 | — |
| `contracts/domains/entrypoints.json` | 変更 | 承認の答えの hook の行（読む環境変数は `NDF_FACTORY_RUN`） | — | 表 | — |
| `contracts/domains/cli-floors.json` | 変更 | 最低バージョンの確認で通らない場合に上げる | — | 表 | — |
| `contracts/domains/skill-io.json`・`manifests/skills.json`・`.codex-plugin/plugin.json` | 変更 | Skill `launch-config` の追加（`plugin.json` は `ndf manifests render` が作る） | — | 表 | — |
| `contracts/hooks/guards.json` | 変更 | guard の行（PostToolUse、`AskUserQuestion`） | — | 表 | — |
| `contracts/schemas/ndf-settings/launch.schema.json` | 新設 | 起動の設定の形 | — | スキーマ | — |
| `contracts/schemas/internal/usage.schema.json` | 変更 | `name`・`session_seq`・`session_cost_usd` の欄 | — | スキーマ | — |
| `contracts/schemas/io/launch-config.args.schema.json`・`launch-config.result.schema.json`・`docs/contracts/*.md` | 新設 / 変更 | 引数のスキーマと規約のページは生成し、結果のスキーマは手で書く | — | スキーマ | — |

手順書の変更（`skills/launch-config/SKILL.md`・`skills/external-ai/`・`skills/qa-security-scan/03-report-template.md`・`agents/corder.md`・`skills/cross-review/SKILL.md`・`skills/worktree/references/ndf-settings.md`）は、「手順書」のセクションにまとめる。

## 起動の設定の読み取り

満たす決定: 決定 1・2・4・5・8・9、受け入れ条件 5・8・11・12・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load` | `adapters/launch_settings.py` | 新設 | `load(root: Path, source: Optional[Path] = None) -> Settings` | `<root>/.ndf/launch.json`（`source` があればそのファイル）を読み、ファイル全体を検証して、`permissions` と定義ファイルのパスを返す |
| `step_spec` | 同 | 新設 | `step_spec(settings: Settings, name: str) -> StepSpec` | `*` の指定に工程の指定をキーごとに重ねる |
| `check_definitions` | 同 | 新設 | `check_definitions(settings: Settings, paths: Sequence[str]) -> dict[str, Any]` | 定義ファイルを読み、MCP サーバの定義の形を検証して、解いた中身を返す |
| `approval_target` | 同 | 新設 | `approval_target(settings: Settings) -> Target` | 承認の対象（`permissions`・全工程の定義ファイル・`inherit` があれば `.mcp.json`）のハッシュと、出力してよい一覧を返す |
| `prepared` | 同 | 新設 | `prepared(name: str, cwd: Path) -> ContextManager[Grant]` | 起動 1 回の指定を解き、承認を確かめ、MCP の定義のコピーを作り、抜けるときに消す |
| `safety_args` | 同 | 新設 | `safety_args(runtime: str, use: str, cwd: Path) -> SafetyArgs` | 設計書の「安全装置を外す指定」の表のとおりに引数を返す。外す引数には、起動の直前の確認を添える |

- `Settings` は `root`・`permissions`（辞書。ファイルが無ければ空）を持つ。`StepSpec` は `load: tuple[str, ...]`・`allow: tuple[str, ...]`・`mcp: None | "inherit" | tuple[str, ...]`。`Target` は `sha256`・`listing`（サーバ名・コマンド名・定義ファイルの場所・ハッシュ・`load`・`allow`・`unsandboxed`）。`listing`・`sha256`・`Grant.allow` はどれも書かれたままの指定子から作る。秘密の形の指定子は `load` が先に止めるので、ここには届かない。停止の文も `listing` から作る
- `SafetyArgs` は `args: list[str]` と `verify: Optional[Callable[[], Optional[str]]]` を持つ。`verify` は外す引数（`unsandboxed_args`）のときだけ持ち、承認の対象を読み直して、違うか取り消されていれば理由の 1 行を返す
- `Grant` は `load`・`allow`・`inherit: bool`・`mcp_files: tuple[Path, ...]`・`sha256: Optional[str]` を持つ。`verify() -> Optional[str]` も持ち、内容を読み直して、違うか取り消されていれば理由の 1 行を返す
- `prepared`
  - 事前条件: `cwd` は起動する作業場所。リポジトリの根は、`cwd` から親へたどって最初に `.git`（ディレクトリかファイル）を持つディレクトリ、無ければ `cwd`。git のプロセスは起動しない。`safety_args` も同じ決め方にする
  - git の起動: 指定が空でない場合の `repo_key`（`git rev-parse --git-common-dir`）の 1 回だけ
  - 事後条件: 指定が空（`load`・`allow`・`mcp` が無い）なら承認を調べず、`sha256` が None の `Grant` を返す。空でなければ承認済みのハッシュを持つ。抜けた後、コピーのディレクトリは無い
  - 副作用: 状態の保存先の `launch-mcp/<PID>-<一意な値>/`（0700、中のファイルは 0600）を作って消す。隣に管理ファイル（`<id>.owner`）を置き、抜けるまでその錠（`filelock`）を取り続ける。錠の取得は、初回を含めて最大 3 回試す。別の起動の掃除に錠を先に取られて取得できなければ、残りの試行では別の `<id>` を使う。管理ファイルには `row`・`id`・ディレクトリの `dev`・`ino`・これから書くコピーの名前（`files`）の記録を JSON で書き、終わりに改行を付ける。作る前に、錠を待たずに取れる管理ファイルを調べ、設計書の「消し残り」の表のとおりに消す。ディレクトリの中で消すのは記録の `files` の普通のファイルだけで、ディレクトリは再帰的に消さない。管理ファイルは最後に消す
- `safety_args`
  - 事前条件: `use` は `review` か `delegate`
  - 副作用: サンドボックスの確認（`agents.user_namespaces`）と、承認の記録の読み取り

### 異常系の処理

どれも `internal.InternalError("precondition", <文>)` を上げる。文にはファイルとキーの位置だけを載せ、定義の値を載せない。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 読めない・形が違う | JSON でない、スキーマに合わない、`load` が英字だけの名前でない、`allow` の形か指定子が秘密の形（`secret_forms.redact` が伏せる値。代入の値が `$` で始まる参照は除く） | 読んだ直後 | 呼び出し元へ返す | `stopped`・`precondition` | 後始末なし。直して再実行できる |
| `load` | 反映の途中 | 反映先が無く `launch.json.reflecting` がある | 読む前 | 呼び出し元へ返す | `stopped`・`precondition`。`approve` の打ち直しを載せる | 打ち直しが退避を片付けてから照らす |
| `check_definitions` | 定義ファイルの誤り | 無い、JSON のオブジェクトでない、`mcpServers` の形、`command` も `url` も無い | 読んだ直後 | 呼び出し元へ返す | 同上 | 同上 |
| `approval_target` | ハッシュを計算できない | どれかの工程の `mcp` が絶対パスか根の外、またはほかの工程が指す定義ファイルが読めない | 定義を読むとき（`load` では見ない。追加の指定が無い工程の `prepared` は定義を読まないので止まらない） | 呼び出し元へ返す | 同上（パスの書かれた位置か、そのファイルを示す） | 同上 |
| `prepared` | 未承認 | 書き換え、このマシンで未承認、取り消し | `launch_approval.approved` | 止める | `precondition`。承認の対象の一覧と `/ndf:launch-config` を載せる | コピーを作らない。承認して再実行 |
| `prepared` | コピーを書けない | 状態の保存先の権限・容量 | 書き込み | 呼び出し元へ返す | `precondition` | 作りかけのディレクトリを消す |
| `safety_args` | 起動できない状況 | サンドボックスが使えず承認が無い、agy の `review` | 表の判定 | 止める | `precondition`。承認の手順を載せる | 再実行できる |

### 処理の手順

`safety_args` の判定表:

| runtime / use | 条件 | 結果 |
| --- | --- | --- |
| claude / どちらも | — | `[]` |
| agy / review | — | 停止 |
| `sandbox_needs_userns` が偽で `sandbox_args` を持つ行 / review（kiro） | — | `sandbox_args` |
| codex / review・delegate | 名前空間を作れる | `sandbox_args`（review）・`delegate_sandbox_args`（delegate） |
| codex / review・delegate | 作れず、`permissions.unsandboxed` にそのランタイムがあり承認済み | `unsandboxed_args` |
| kiro・agy / delegate | `permissions.unsandboxed` にそのランタイムがあり承認済み（サンドボックスの有無を見ない） | `unsandboxed_args` |
| 上のどれでもない | — | 停止 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load`・`step_spec` | 工程の指定が `*` をキーごとに置き換える | ファイルが無い、`permissions` が空 | `load` に `Bash(...)`・`mcp__` の名前・カンマ、秘密の形の指定子（文に値が出ない。`token=$API_TOKEN` は通る） |
| `check_definitions` | `command` のサーバと `url` のサーバ | `mcpServers` が空 | 形の誤りの文に値が出ない |
| `approval_target` | 字下げとキーの順の違いで同じ値 | `inherit` で `.mcp.json` が無い | 定義の中身を変えると値が変わる。引数・`env`・`headers`・URL が一覧に出ない。`mcp` の根の外へ出るシンボリックリンクで止まる |
| `prepared` | コピーの権限と、抜けた後の削除、掃除と重なったときの取り直し | 指定が空なら記録が無くても通る | 例外でも削除される。設計書の「消し残り」の表の各行（管理ファイルだけが残った途中の消し残り、記録を書く前の空の残り、改行で終わらない書きかけの記録の残り、使用中、管理ファイルの無いもの、記録の形でないもの、置き換わったディレクトリ、記録のコピーのほかに利用者のファイルがあるもの） |
| `safety_args` | 判定表の各行 | 承認があっても名前空間を作れれば外さない（`verify` は None） | 承認が無ければ停止。引数を返した後の取り消しで `verify` が理由を返す |

## 起動の承認

満たす決定: 決定 7・8、受け入れ条件 9・10・11・14

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `repo_key` | `adapters/launch_approval.py` | 新設 | `repo_key(root: Path) -> str` | git の共通ディレクトリの実体の絶対パス。git の外は `root` の実体 |
| `digest` | 同 | 新設 | `digest(permissions: Mapping, files: Mapping[str, Any]) -> str` | キーの順にそろえた JSON の SHA-256 |
| `approved` | 同 | 新設 | `approved(repo: str, sha256: str) -> bool` | 同じ組の最後の行が `approve` か |
| `append` | 同 | 新設 | `append(repo: str, sha256: str, action: str, approver: Optional[str] = None, request_id: Optional[str] = None) -> dict` | 承認の記録へ 1 行足す |
| `put_question` | 同 | 新設 | `put_question(request: Mapping, make_question: Callable[[], Mapping], candidate: Optional[Path] = None) -> Mapping` | 承認の依頼（識別子・リポジトリのキー・操作の種類・対象のハッシュ・反映先・反映元の版・候補・会話の id・日時）と、問いの中身のハッシュを残し、作った問いの中身を返す。候補があれば、候補のロックを取ってから、退避した候補を元の名前へ戻し、`make_question` を呼んで問いの中身を作り、依頼を追記する。`make_question` は `{"sha256": 承認の対象のハッシュ, "questions": 問いの中身}` を返し、依頼の対象のハッシュにはこの `sha256` を使う（候補を読んで求めるので、ロックの外では決めない）。この 3 つを同じロックの中で行うので、問いが示す候補は依頼を残すまで消えない。戻せる候補が無ければ、`make_question` を呼ばず、依頼も残さずに `precondition` で止める |
| `record_answer` | 同 | 新設 | `record_answer(questions: Any, answers: Any, question_id: str, session: str) -> bool` | 残した問いと一致し、会話の id が依頼と同じ場合だけ、依頼の識別子を持つ答えを足す |
| `approve_by_answer` | 同 | 新設 | `approve_by_answer(repo: str, sha256: str, request_id: str) -> Optional[str]` | `reflect` か自分が取ったロックの中で、依頼の答えを設計書の「人の確認」の条件の表で照らし、承認の行を足して承認者を返す。同じ依頼の承認の行があり承認済みなら、行を足さずに承認者を返す |
| `pending_request` | 同 | 新設 | `pending_request(repo: str, target: Path) -> Optional[Mapping]` | その反映先で「承認する」と答えがあり、反映が済んでいない使える依頼を返す |
| `new_candidate` | 同 | 新設 | `new_candidate(repo: str, target: Path) -> Draft` | `launch-candidates/<キーのハッシュ>/<一意な値>/` に候補（反映先の写し）と、反映先・反映元の版の記録を作る。その前に、設計書の「候補の掃除」のとおり、前の掃除が退避した候補を消すか元の名前へ戻し、片付けの条件（「承認しない」の依頼、反映先の worktree が無い、反映先が反映元の版から変わった）に当たる候補を退避し、反映先の worktree が無い完了の記録を消す。返す `Draft` は、作った候補のパス（`candidate`）と、掃除の後も元のパスに残った片付けの対象の一覧（`cleanup_targets`。形は `cleanup_targets` 関数と同じ）を持つ。一覧は掃除で求めた判定の結果から作り、依頼と回答の記録と候補の一覧を読み直さない |
| `cleanup_targets` | 同 | 新設 | `cleanup_targets(repo: str) -> list[dict]` | 片付けの条件に当たるが元のパスに残っている候補のうち、利用者が消しても設定案と利用者の項目を失わないものを返す。対象は、比べる版のどれかと中身が一致し、ディレクトリの中に NDF が作ったファイルしか無い候補である。`new_candidate` が返す一覧は、同じ掃除で開いたままのファイルを見つけて元の名前へ戻した候補も外す。過去の反映先か `candidate.json` が普通のファイルでなく読めない候補は、その候補だけを判定から外し、ほかの候補の判定と呼び手の処理を続ける。依頼の否認、反映先の worktree の有無、反映先の版から、片付けの対象になり得る候補を絞り、当たらない候補のディレクトリは調べない。候補の中身が必要な判定では、先にディレクトリが NDF の作ったものだけかを確かめ、候補を読んでから、まだ使える依頼か、どの理由で片付けるかを確定する。履歴と反映先の版だけでまだ使える依頼と決まる候補は読まない。一覧を返す前に、候補のディレクトリの項目と、各項目の inode・大きさ・更新時刻が判定の時と同じかを照らし、変わった候補は載せない。判定に使った `source.json` と `candidate.json` は、リンクをたどらず、FIFO で待たずに開き、開いた記述子で普通のファイルと確かめてから読む。読んだファイルの inode・大きさ・更新時刻もこの照合に含める。設計書の「まだ使える依頼」がある候補は載せない（人が答えた後の `approve --candidate` がこの候補で承認を済ませるため）。答えが使えるかは `approve_by_answer` と同じ条件（`_lapsed`）で判定するので、取り消した依頼の候補は載る。答えで書いた承認の行がある依頼は、反映先が依頼の反映元の版か候補の今の中身と同じときだけ、まだ使える依頼とみなす。依頼の履歴と承認の記録は 1 回ずつ走査して索引を作り、承認の記録は照らす候補があるときにだけ読む。索引のキーに使う欄（依頼の識別子・反映先・候補・リポジトリのキー・ハッシュ）に、文字列でも null でもない値（配列など）がある行は、壊れた行として索引から外し、ほかの行で判定を続ける。各要素は `candidate`（候補ファイルのパス）と `reason`（片付けの理由。設計書の「候補の掃除」の `denied`・`worktree_gone`・`reflected`・`outdated`）を持つ。対象が無ければ空のリストを返す。何も書かない。`/proc` の無い環境では掃除が候補を退避しないので、`_show` がこの一覧を結果に載せる |
| `reflect` | 同 | 新設 | `reflect(candidate: Path, record: Callable[[], None]) -> bool` | 承認の記録のロックの中で、`recover` を呼んでから、反映先を反映元の版と候補に照らす。版が同じなら `record` を呼び、設計書の「置き換えの手順」で置き、完了の記録を書いてから使えなくなった候補を消す。候補と同じなら完了の記録を書いて真、どちらでもないか途中で保存を見つけたら偽 |
| `recover` | 同 | 新設 | `recover(target: Path) -> Optional[Path]` | 承認の記録のロックの中で、設計書の「退避が残っている場合の判定」の表のとおりに退避と反映の途中の記録を片付ける。利用者の保存を別の名前で残した場合はそのパスを返す |
| `reflected` | 同 | 新設 | `reflected(candidate: Path, sha256: str, request_id: Optional[str]) -> bool` | 候補が無い打ち直しで、完了の記録と反映先の中身を照らす。何も書かない |

- このモジュールは hook から読むので、モジュールの頭では標準ライブラリと `identity` だけを読み込む
- `append`・`put_question`・`record_answer`
  - 事後条件: 追記だけで、ファイルは所有者だけが読める（0600）
  - 副作用: 状態の保存先の `launch-approvals.jsonl`・`launch-questions.jsonl`・`launch-answers.jsonl`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `approved` | 記録が読めない・壊れた行 | 書きかけ | 行の解析 | 壊れた行を飛ばす。ファイルを読めなければ未承認 | 呼び出し元の `precondition` | 再実行できる |
| `append` | 書けない | 権限・容量・ロックの待ちの超過 | 書き込み | 呼び出し元へ返す | `precondition` | 承認は成立しない。再実行 |
| `put_question` | 書けない | 同上 | 書き込み | 呼び出し元へ返す | `precondition` | 問いを出さない。再実行 |
| `record_answer` | 一致する問いが無い | ほかの問い、中身の書き換え、別の会話の id | 照合 | 既定値（記録しない）で続ける | 出力なし | — |
| `approve_by_answer` | 使える答えが無い | 未回答、「承認しない」、別のリポジトリ・操作・ハッシュ、同じ反映先により新しい依頼がある、反映先が変わった、依頼の後の取り消し | ロックの中の照合 | None を返す | 呼び出し元が拒む | 問いを出し直せる |
| `repo_key` | git を打てない | git の外 | `git.GitError` | 既定値（`root` の実体）で続ける | 出力なし | — |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つの `approve`、`approve` と `revoke` | 承認の記録と答えの記録 | 承認の記録の 1 つのロック（`launch-approvals.jsonl.lock`）の中で、答えの照合と承認の行の追記を続けて行う | 使用済みは、同じ依頼の識別子を持つ承認の行で表し、別の行を書かない。行を書く前に落ちれば答えは未使用で残り、書いた後に落ちれば打ち直しが行を足さずに続ける |
| 起動の直前の照らし直し（`Grant.verify`）と承認の書き込み | 承認の記録 | 読む側はロックを取らず、改行で終わる行だけを読む | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `append`・`record_answer` | 設計書の承認の記録の欄 | 状態の保存先の JSONL | MCP の定義の中身、引数、`env`、`headers`、URL |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `repo_key` | `git.Git.common_dir` | `grep -n "def common_dir" src/ndf/adapters/git.py` | 使う |
| `append`・`approve_by_answer` | `domains/records/jsonl.py` | 層の順序の確認（adapters は domains を読めない） | 使えない。`filelock` と `identity.load().state_dir` を直接使う |
| `load` | `contracts.require_schema` | `core/converge/participants.py` の使い方 | 使う |
| `check_definitions`・`approval_target` | `participants.setting_present` | 同ファイル | 使えない（core の層）。`Path` の確認を持つ |
| `safety_args` | `agents.user_namespaces` | `grep -n user_namespaces src/ndf` | 使う |
| `load` の秘密の検査 | `domains/project/secret.py` の `redact` | 層の順序の確認（adapters は domains を読めない） | 表と処理を `adapters/secret_forms.py` へ移し、2 つの層が同じ表を使う |
| `prepared` | `tempfile.mkdtemp` | 標準ライブラリ | 使う（状態の保存先の下に作る） |
| `launch_costs`・`main_model` | `claude.context_window` の主モデルの選び方 | `adapters/claude.py` の 449 行の付近 | `main_model` に切り出して両方が使う |
| `LaunchLog`・`set_launch_recorder` | `set_relay_checks` | `adapters/claude.py` | 同じ差し込みの形にする |
| `step_spec`・`digest`・`approved`・`new_candidate`・`reflect`・`recover`・`pending_request` | 起動の設定と承認を扱う関数 | `grep -rn "approval\|launch.json" src/ndf` | 同じ役割は無い（マージの承認の証跡 `core/merge_approval.py` は PR のコメントが対象）。新設する |
| `put_question`・`record_answer`・`check` | `guards/ask.py`・`ask_clear.py` | `ls src/ndf/domains/hooks/guards` | 質問の転送と後片付けで、答えは残さない。guard の形だけ使う |
| `approve_by_answer` | `filelock.FileLock` | `domains/records/jsonl.py` の使い方 | 使う |
| `main`・`_show`・`_approve`・`_confirm_terminal`・`_revoke`・`_draft` | `contracts.result`・`ndf.cli` のサブコマンドの形 | `src/ndf/cli.py` | 結果 JSON の組み立てを使う |

## `ndf launch-config` と承認の答えの hook

満たす決定: 決定 7・9、受け入れ条件 9・10・14

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `domains/settings/launch_config_cli.py` | 新設 | `main(argv: list[str]) -> int` | `draft`・`show`・`approve`・`revoke` を振り分ける |
| `_draft` | 同 | 新設 | `_draft(a: Namespace) -> int` | `NDF_AI_CHILD` が無ければ `new_candidate` を呼び、候補のパスと、利用者が手で片付けてよい候補の一覧を結果 JSON で出す。一覧は `new_candidate` が返す `Draft.cleanup_targets` を使い、`cleanup_targets` 関数は呼ばない（掃除と同じ判定をもう一度走らせないため）。結果 JSON の形は下の「`draft` と `show` の結果の `cleanup_targets`」のとおり |
| `_show` | 同 | 新設 | `_show(a: Namespace) -> int` | 承認の対象と状態を結果 JSON で出す。反映が済んでいない依頼があれば続きの `approve` のコマンドを載せる。`--question approve` は、候補を読んで承認の対象・ハッシュ・問いの中身（`unsandboxed` があれば警告の 1 文を含む）を組み立てる関数を `put_question` へ渡し、返った問いの中身を結果に載せる。`cleanup_targets` 関数が返す一覧も、下の「`draft` と `show` の結果の `cleanup_targets`」の形で載せる。`--candidate` を付けた呼び出しでは、`put_question` を呼ぶ前に候補を読まない（読むと、退避した候補を元の名前へ戻す前に読むことになる）。答えが記録されない会話では端末で打つコマンドを返す |
| `_approve` | 同 | 新設 | `_approve(a: Namespace) -> int` | 設計書の「人の確認」（端末での入力か、`--request` の依頼への人の答え）を満たす場合だけ記録し、`--candidate` を反映する |
| `_confirm_terminal` | 同 | 新設 | `_confirm_terminal(target: Target) -> Optional[str]` | 端末で名前とハッシュの先頭 8 文字を受ける |
| `_revoke` | 同 | 新設 | `_revoke(a: Namespace) -> int` | `NDF_AI_CHILD` が無ければ取り消しの行を足す |
| `main` | `domains/settings/delegate_run_cli.py` | 新設 | `main(argv: list[str]) -> int` | `safety_args(runtime, "delegate", cwd)` を取り、`verify` を打ってから、`NDF_AI_CHILD=1` を足して `os.execvpe` で CLI に置き換える。失敗は標準エラーだけに出す |
| `build_command` | 同 | 新設 | `build_command(runtime: str, command: Sequence[str], args: Sequence[str]) -> list[str]` | 先頭の語がランタイムの CLI で、`{args}` が単独の引数として 1 つだけあることを確かめ、そこを `args` に広げる |
| `check` | `domains/hooks/guards/launch_answer.py` | 新設 | `check(ctx, svc) -> Verdict` | `AskUserQuestion` の答えを `record_answer` へ渡し、常に通す |

- `draft` と `show` の結果の `cleanup_targets`: 利用者が手で片付けてよい候補を、結果 JSON の `cleanup_targets` 配列で出す。`/proc` の無い環境では掃除が候補を退避しないので、利用者はこの一覧を見て候補のディレクトリを消す
  - 各要素は `candidate`（候補ファイル `candidate.json` の絶対パスの文字列）と `reason`（片付けの理由の文字列）の 2 つのキーを持つ
  - `reason` の値は `denied`（最新の依頼に「承認しない」と回答した）・`worktree_gone`（反映先の worktree が無くなった）・`reflected`（反映先が反映元の版から変わり、候補の中身が反映先の今の中身と一致する。反映の完了の記録とは照らさないので、この候補を承認して反映したことを表さない）・`outdated`（反映先が反映元の版から変わり、候補の中身が反映先の今の中身と違うので、この候補は反映できない）のどれか
  - 対象が無ければ空配列 `[]` を出す。キーを省かない
  - 結果の文には、候補を消す前にその候補を開いているエディタでの編集を終えて閉じることを添える。開いたまま消すと、その後の保存で設定案が失われる
  - 例: `"cleanup_targets": [{"candidate": "/home/u/.local/state/ndf/launch-candidates/3f2a…/9c1e…/candidate.json", "reason": "reflected"}]`
- `_approve`
  - 事前条件: `--sha256` が必須
  - 事後条件: 候補（`--candidate`）があるときは `reflect` の中で承認の行を足し、設計書の「置き換えの手順」で反映する。反映先が変わっていれば、行を足さず、置き換えずに拒む。候補の承認の対象が空か承認済みなら、問いを求めず、承認の行も書かずに同じ照らし方で置き換える。`NDF_AI_CHILD` があれば、この場合も拒む。`--candidate` があるときは、今の `.ndf/launch.json` を設定として解析せず、版のハッシュだけを取る。候補のファイルが無ければ、`NDF_AI_CHILD` が無いことを確かめて `reflected` で判断し、人の確認を求めず、何も書かない。`--candidate` が無いときも、承認の対象を読む前に `recover` を呼ぶ
  - 副作用: 承認の記録、`.ndf/launch.json`、候補の削除、標準入力の読み取り（端末のときだけ）
- `delegate_run_cli.main`
  - 事後条件: 成功すると戻らず、CLI がこのプロセスを置き換える。失敗は `InternalError` をここで受け、標準エラーへ書いて 0 以外で終える（`ndf.cli` の結果 JSON が標準出力へ出ないようにする）
- `check`
  - 副作用: `NDF_AI_CHILD`・`NDF_FACTORY_RUN`・`NDF_RELAY_PLAN`・`NDF_ORGANIZER_DIR` のどれかがあれば何も書かない。どれも無ければ `record_answer` を呼ぶ（記録しない理由は設計書の「人の確認」）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_show` | 設定を読めない | 起動の設定の誤り | `load` | 呼び出し元へ返す | `stopped`・`precondition` | 直して再実行。`--candidate` を付けた呼び出しは候補だけを読む |
| `_approve` | 内容が違う | `--sha256` といまの対象の不一致 | 計算の直後 | 止める | `stopped`・`refused` | 記録も反映もしない |
| `_approve` | 人の確認が無い | `NDF_AI_CHILD`、端末でなく答えも無い | 確認の順 | 止める | `stopped`・`refused` | 同上 |
| `_approve` | 反映先が変わった | 候補を作った後の別の作業の保存 | `reflect` | 止める | `stopped`・`refused`。候補を作り直すよう載せる | 行も反映もしない。`draft` からやり直す |
| `_approve` | 反映できない | `.ndf/launch.json` の名前の変更か書き込みの失敗 | `reflect` | `recover` で片付けて呼び出し元へ返す | `stopped`・`precondition`。承認の行は残る | 同じコマンドを打ち直すと、承認済みなので置き換える |
| `_approve` | 置き換えの間に保存された | 利用者の手の保存 | `reflect` | 保存された中身を残して止める | `stopped`・`refused`。別の名前で残した場合はそのパスを載せる | 承認の行は残る。`draft` からやり直す |
| `_approve` | 候補が無く、反映も確かめられない | 完了の記録が無いか、反映の後に設定が変わった | `reflected` | 止める | `stopped`・`refused` | 何も書かない。`draft` からやり直す |
| `_confirm_terminal` | 打ち間違い・中断 | ハッシュの不一致、EOF | 入力の比較 | None を返す | 呼び出し元が拒む | 打ち直せる |
| `_revoke` | AI の中で呼ばれた | `NDF_AI_CHILD` | 頭 | 止める | `stopped`・`refused` | 記録しない |
| `delegate_run_cli.main` | 起動できない状況・使い方の誤り・読み直しの食い違い | `safety_args` の停止、`build_command`、`verify` | 呼び出し | CLI を起動せずに止める | 標準出力は空、理由は標準エラー、終了コードは停止理由 `precondition` のもの | 再実行できる |
| `check` | 記録の失敗 | 権限・容量 | 書き込み | 通す（dispatch が例外を `pass` にする） | 出力なし。`approve` が拒む | 問いを出し直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_approve` | 端末の確認と、答えの確認 | 候補の対象が空 | 「承認しない」・使用済み・別のリポジトリ。拒んだ後に `.ndf/launch.json` が変わらない。同じ依頼で 2 回打つと行は 1 つで、2 回目も成功する。候補を作った後に反映先が変わると拒み、反映先を変えない |
| `check` | 一致する問いの答えを記録 | 問いが無い | 4 つの環境変数のどれかがあれば記録しない |
| `_draft`・`reflect` | 2 つの候補は別のファイル | 反映先のファイルが無い | 版が違う反映先は上書きしない |
| `reflect`・`recover`・`reflected` | 反映して候補を消した後の打ち直しが成功する。候補を置いた直後の中断の後の打ち直しが成功する | 退避だけが残った中断、反映の途中の記録だけが残った中断 | 照らした後の保存（名前の変更の前後に差し込む）が残る。置いた直後の中断で退避の側が書き換わっていれば拒んで戻す。反映先と退避の両方が利用者の中身なら両方残る。反映の後に変えた設定の打ち直しは拒む |
| `_show` | 続きのコマンドが出る | 依頼から長くたった答え | 4 つの環境変数があれば問いでなく端末のコマンド |
| `delegate_run_cli.main`・`build_command` | `{args}` に引数が入り、子の環境に `NDF_AI_CHILD` がある（`exec` は差し替える） | 引数が 0 個 | 停止と読み直しの食い違いで `exec` を呼ばない。`{args}` が無い・別の CLI は拒む |

## 起動の引数の組み立てと起動の結果

満たす決定: 決定 1・4・5・6、受け入れ条件 2・3・6・7・8・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `permission_args` | `adapters/claude.py` | 変更 | `permission_args(permission, tools=None, readable=(), grant: Optional[Grant] = None) -> list[str]` | full は `--tools`・`--allowed-tools <1 件 1 引数>`・`--strict-mcp-config`・`--mcp-config` を組む |
| `precheck_claude` | `adapters/claude_precheck.py` | 変更 | `precheck_claude(run, cwd, options) -> Optional[Stop]`（`claude.py` の `_precheck` から分ける） | `options.verify` が理由を返せば `Check.VERIFY` で止め、`claude` が `Failure.LAUNCH` にする |
| `call_claude` | `adapters/claude.py` | 変更 | 変えない | 子の環境に `NDF_AI_CHILD=1` を足す |
| `_parse_reply` | 同 | 変更 | 変えない | 成功でも誤りでも、`session_id`・`usage`・`modelUsage`・`total_cost_usd` を回答に持たせる |
| `_prefaced` | 同 | 変更 | 変えない | 起動の前置きと起動を打ち、起動した子が終わったその場で `options.log` へ回答を渡す。アカウントを替えて打ち直す場合も、前の子の行は替え先の起動より先に渡る |
| `main_model` | 同 | 新設 | `main_model(models: Mapping) -> Optional[tuple[str, Mapping]]` | トークンの和が最大（同じなら窓の小さい方）のモデルを返す |
| `context_window` | 同 | 変更 | 変えない | 主モデルの選び方を `main_model` にする |
| `set_launch_recorder` | 同 | 新設 | `set_launch_recorder(recorder: Optional[LaunchRecorder]) -> None` | 書き込み処理を差し込む |
| `LaunchLog` | 同 | 新設 | `LaunchLog(meta: Mapping)`、`report(child: ClaudeReply) -> None` | 1 回の委譲か Skill の呼び出しの記録先。起動した子 1 つにつき 1 行を書き込み処理へ渡す。`session_seq` は会話の id（結果の `session_id`、無ければ `meta` の `session_id`）ごとに 0 から数える。書き込み処理が無ければ渡さない |
| `launch_meta` | 同 | 新設（A4） | `launch_meta(name: str, repo: Optional[str] = None) -> dict` | 行の `name`・`repo`（呼び出し元が解決済みのときだけ）と、環境変数の `run_id`・`step`・`attempt`。worker と Skill を回す起動が同じ組み立てを使う |

- `permission_args` の full の並び: `--permission-mode acceptEdits --tools <同梱の読み込む一覧 + grant.load をカンマで連結> --allowed-tools <同梱の許可 13 個> <grant.allow の各件> [--strict-mcp-config] [--mcp-config <コピー>...]`。`inherit` は後ろの 2 つを付けない。`--allowed-tools` は複数の値を取る。後に続くプロンプトを値として読ませないよう、`plugins.args` が空のときも、後ろに `--append-system-prompt` を置く（今の `_full_args` の並び。`plugins.args` は空になりうるので、終端として数えない）。`grant` が None なら指定なしと同じ
- 定数: `LOAD_TOOLS`（16 個）を足し、`FULL_TOOLS`（許可の 13 個）は変えない。`load` の名前は `--allowed-tools` に入れない
- `ClaudeCall` に `grant`（full 以外で渡すと `ValueError`）、`ClaudeReply` に `session_id`・`usage`・`model_usage`・`session_cost_usd`・`launched: bool`・`launched_at`・`seconds`、`LaunchOptions` に `verify: Optional[Callable[[], Optional[str]]]` と `log: Optional[LaunchLog]` を足す。どれも省略できる
- `LaunchRecorder` は `record_cli(usage, meta)` を持つ
- `LaunchLog.report`
  - 事後条件: `launched` が偽の子（CLI が古い、同時起動のスロットが取れない、起動できない、起動の直前の照らし直し（`Grant.verify`）で停止）は行を渡さない。`launch_id` は子ごとに作る
  - 副作用: 差し込まれた書き込み処理の呼び出し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `precheck_claude` | 承認の対象が変わった | 起動の直前の書き換え・取り消し。CLI の最低バージョンの確認（`claude --version` の終了を待つ）の間の取り消しを含む | `options.verify`（最低バージョンの確認の前と、確認で `claude --version` の終了を待った起動では後にも打つ。別の起動の `--version` を錠で待った起動も含む） | 呼び出し元へ返す | 呼び出し元の `precondition`。承認の案内のコマンドには、読み直した現在のハッシュを `--sha256` に載せる | 子を起動しない。承認して再実行 |
| `LaunchLog.report` | 書き込み処理が無い | エントリポイントを通らない呼び出し | None の判定 | 既定値（書かない）で続ける | 出力なし | — |
| `_parse_reply` | 欄が無い・形が違う | CLI の出力の変化、JSON でない | 読み取り | 欄を None にして続ける | 行の `usage` が `unknown` | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `LaunchLog.report` | 設計書の「使用量ログの行」の欄 | 使用量ログ | 質問の本文、回答の本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `permission_args` | 指定なし、定義ファイルの指定、`inherit` | `load` と `allow` が空 | `load` の名前が `--allowed-tools` に入らない。`--setting-sources` が無い |
| `precheck_claude` | `verify` が None を返せば起動 | — | 理由を返せば子を起動しない |
| `LaunchLog.report` | 子 1 つで 1 行、打ち直しで 2 行（前の子の行が打ち直しより先） | `usage` が無い。同じ id のままアカウントを替えた子は番号が続き、会話の id が変わった子は番号が 0。会話の id が分からない子は常に 0 | `launched` が偽なら 0 行 |
| `main_model` | 和が最大 | 同じ和は窓の小さい方 | `modelUsage` が空 |

## worker の起動と Skill を回す起動

満たす決定: 決定 3・6・8、受け入れ条件 3・4・5・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `delegate` | `core/worker.py` | 変更 | 変えない | `prepared(handoff.kind, handoff.cwd)` の中で引数を組み、再開まで流す |
| `_args_for` | 同 | 変更 | `_args_for(system, root, cwd, grant) -> claude.PluginArgs` | 指定を持つ引数を組む |
| `_resume_until_written` | 同 | 変更 | `_resume_until_written(handoff, wd, out, base, call, options)` | 子ごとに `verify` と、委譲の間で共通の `LaunchLog` を渡す |
| `_call_skill` | `progress/invoke.py` | 変更 | 変えない | `prepared(run.name, cwd)` の中で起動し、記録する。`session_seq` は 0 から |

- `Caller` と `SkillCaller` は 5 つ目の引数 `options: claude.LaunchOptions` を受ける形にする。テストの偽の呼び出しも直す
- 行の `name` は作業の名前か Skill の名前。worker と Skill を回す起動はリポジトリ名を解決済みで持たないので、`repo` の欄を省く（リポジトリ名を得るためだけに git を起動しない）
- `run_id`・`step`・`attempt` は、環境変数 `NDF_PLAN_RUN_ID`・`NDF_STEP_ID`・`NDF_STEP_ATTEMPT` があれば書く
- 実装する worker は、PR を作る前に、工程が MCP の Tool を呼んだ件数を数え直し、結果を PR の本文へ書く。目的は、MCP を読み込まなくする前に、使っている工程が無いかを確かめることである。呼んだ工程があれば、このリポジトリの `.ndf/launch.json` に指定を足す。数えるのは、2026-11-24 から数える日までの、変更を入れる前のバージョンで起動した非対話の会話の記録である。起動の見分け方は設計書の決定 3 の表にある

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `delegate` | 指定が壊れているか未承認 | `prepared` の停止 | 起動の前 | 呼び出し元へ返す（`ndf.cli` が結果 JSON にする） | `stopped`・`precondition` | 起動しない。再実行できる |
| `_resume_until_written` | 再開の直前の停止 | `verify` | `reply.failure` | 止める | `stopped`・`precondition` | コピーは `delegate` を抜けるときに消える |
| `_call_skill` | 指定の停止 | `prepared` | `InternalError` を受ける | 呼び出し元へ返す | `StageResult` の `stopped`・`precondition` | 起動しない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `delegate` | 指定の引数が起動に渡る。再開した子の数だけ行が増える | 指定が無いリポジトリ | 未承認で呼び出しが 0 回。JSON の無い失敗は `unknown` の行 |
| `_call_skill` | 工程の名前が Skill の名前 | プランの外では `run_id` が無い | 停止で呼び出しが 0 回。使用量ログへ書けなくても結果は同じ |

## プランの実行

満たす決定: 決定 6、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_run` | `core/plan/transition_decide.py` | 変更 | 変えない | ステップを実行する指示（`RunStep`）に、そのステップの何回目かを持たせる |
| `decide` | 同 | 変更 | 変えない | 打ち直し（待ちの後）の指示でも回数を 1 増やす |
| `run_step` | `core/plan/runner.py` | 変更 | `run_step(spec, step_id, attempt, nth: int = 1)` | `run_id`・ステップの id・回数を子の環境にする |
| `plan_step_env` | 同 | 新設（A4） | `plan_step_env(run_id: Optional[str], step_id: str, nth: int) -> dict[str, str]` | 子へ渡す 3 つの環境変数を、名前を環境変数の表から引いて組む |
| `_launch` | `core/plan/steps.py` | 変更 | 変えない | `StepContext.env` を子の環境に重ねる |

- `RunStep` に `nth: int = 1`、`StepState` に `runs: Mapping[str, int]`、`StepContext` に `env: Mapping[str, str]` を足す。`attempt`（打ち直しの回数）の意味は変えない
- `NDF_RUN_ID`・`NDF_RUN_LOG` は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_launch` | 起動できない | 実行ファイルが無い | `OSError` | 今までと同じ（`precondition`） | 変えない | 変えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide`・`_run` | 遷移で戻ったステップと打ち直しで回数が増える | 最初は 1 | — |
| `run_step`・`_launch` | 子の環境に 3 つの値がある | 実行ログを開いた実行でも `run_id` はプランの値 | — |

## 相手役の起動の引数

満たす決定: 決定 9、受け入れ条件 13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_sandbox_args` | `core/converge/participants.py` | 変更 | `_sandbox_args(agent: str, worktree: Path) -> SafetyArgs` | `safety_args(agent, "review", worktree)` を返す |
| `_review_args` | 同 | 変更 | `_review_args(agent, row, worktree) -> tuple[tuple[str, ...], Optional[Callable]]` | `{sandbox}` を上の引数に広げ、起動の直前の確認を返す。`review_launch` は確認を `Launch.verify` に入れる |
| `_start` | `adapters/agents.py` | 変更 | 変えない | 相手役と修正担当の子の環境に `NDF_AI_CHILD=1` を足す |
| `_guarded_run` | 同 | 変更 | 変えない | `Launch.verify`（`Optional[Callable[[], Optional[str]]]`、省けば None）があれば、同時起動の枠と CLI のバージョンの確認の後、`_start` の直前に呼ぶ。理由を返せば起動せず `AgentState.REFUSED` |

- `runtimes.json`: codex に `sandbox_needs_userns: true` と `delegate_sandbox_args: ["-s", "workspace-write"]`。kiro は `sandbox_args: ["--trust-tools=fs_read"]` を持たせ、`review_args` に書いてある同じ値は `{sandbox}` に置き換える。kiro の `unsandboxed_args: ["--trust-all-tools"]`。agy は `unsandboxed_args: ["--dangerously-skip-permissions"]`
- `_unread_hint`（`domains/crossreview/seats.py`）のサンドボックスの失敗の文は「NDF がサンドボックスを外して起動する」をやめ、`/ndf:launch-config` で承認する手順にする

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_guarded_run` | 引数を組んだ後の書き換え・取り消し | 待っている間の `revoke` や指定の変更 | `Launch.verify` | 呼び出し元へ返す | その担当は結果の無い担当（`refused`）。理由に `/ndf:launch-config` を載せる | 子を起動しない。承認して打ち直す |
| `_review_args` | 停止の状況 | `safety_args` | 起動の組み立て | 呼び出し元へ返す（cross-review と cross-refactoring は `ndf.cli` が結果 JSON にする） | `stopped`・`precondition` | どの担当も起動しないうちに止まる。承認して再実行 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_review_args` | codex は `-s read-only`、kiro は `--trust-tools=fs_read` | 承認があっても名前空間を作れれば外さない | 作れず承認が無ければ停止 |
| `_start` | 子の環境に `NDF_AI_CHILD` | — | — |
| `_guarded_run` | `verify` が None を返せば起動 | `verify` が無い起動は変わらない | 理由を返せば子を起動しない |

## 使用量ログの書き込みと費用

満たす決定: 決定 6、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_cli` | `domains/records/usage.py` | 変更 | 変えない | `meta` の足した欄を書く。値が None の欄は置かない |
| `launch_costs` | `domains/records/usage_decide.py` | 新設 | `launch_costs(rows: Iterable[Mapping]) -> dict[str, Optional[float]]` | 同じ会話の累計の差から、`launch_id` ごとの費用を返す。不明は None |

- `__main__.py` は `claude.set_launch_recorder(usage)` を呼ぶ
- `launch_costs` は設計書の「起動の費用の求め方」の表と、同じ `launch_id` の行の扱いに従う。期間で絞る前の全部の行を受ける

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_cli` | 書けない | 権限・容量・ロックの待ち・行の形の誤り | 追記 | 既定値（None）で続ける | 標準エラーに警告 1 行。工程の結果は変えない | 行は残らない |
| `launch_costs` | 値の欠け・番号の飛び・差が負 | 書けなかった起動 | 差の計算 | 不明（None） | 集計が件数を数える | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `record_cli` | 足した欄がスキーマに合う | `usage` が無い行は `unknown` | 書けなくても例外が出ない |
| `launch_costs` | 再開は累計の差 | `session_seq` 0、`session_id` の無い行 | 番号の飛び、差が負、同じ `launch_id` の重なり |

## 手順書

満たす決定: 決定 7・9、受け入れ条件 9・13

| ファイル | 変える中身 |
| --- | --- |
| `skills/launch-config/SKILL.md`（新設） | 設計書の「承認を取る」の 4 手順（端末で承認する会話の分岐を含む）。`show` が返した問いは変えずに出す（`unsandboxed` の警告の文はコマンドが入れる）。壊れた設定を直す場合の進め方。`allow` の指定子に資格情報を書かないこと。MCP の定義ファイルと `.mcp.json` を読まず、作らず、編集しない |
| `skills/external-ai/SKILL.md`・`references/cli-codex.md`・`cli-agy.md`・`cli-kiro.md` | 決め打ちの安全装置の指定をやめ、起動の例を `ndf delegate-run --runtime <名前> -- <CLI のコマンド>` にする |
| `skills/qa-security-scan/03-report-template.md`・`agents/corder.md` | 同上 |
| `skills/cross-review/SKILL.md` | サンドボックスを自動で外す説明を、承認がある場合だけにする |
| `skills/worktree/references/ndf-settings.md` | `.ndf/launch.json` の行 |

起動の例の形は設計書の「安全装置を外す指定」にある。書いた例は実行して確かめる。
