# #4106 の詳細設計: 省いた回答を消費の直前に判定の対象と照合する

設計文書 [issue-4106-design.md](./issue-4106-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。別ファイルにしたのは、新設する関数と型が 9 つ（`mvv_targets`・`mvv_vouch`・`git_base_tip`・`rewait`・`Unread`・`run_git_within`・`shared_remote`・`Deadline`・`DeadlineTransport`）あり、シグネチャを変える関数が 6 つ（`gate_outcome`・`run_git`・`client`・`token`・`RetryWithin`・`github_observe`）あるためである。パスは `plugins/ndf/` からの相対で書く。#3959 の実装の後の形を前提にする。#4105 の実装はどちらが先でもよく、先後で変わるところは各節に書く（設計文書の決定 7）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── limits.json                            … progress.mvv-vouch（照合の 1 回の呼び出しの上限と照合全体の期限）
│   └── workflow/context.schema.json           … $defs.mvv_gate に pr・base_ref・base_sha
├── src/ndf/adapters/
│   ├── deadline.py *                          … Deadline（単調時計の期限）
│   ├── git.py                                 … run_git に timeout・run_git_within
│   └── github.py                              … client に timeout・deadline、token に deadline・per_call、RetryWithin に deadline、DeadlineTransport
├── src/ndf/domains/records/
│   ├── mvv_material_decide.py                 … gate_outcome に pr・base_ref・base_sha（#4105 の scope と並ぶ）
│   └── mvv_gate.py                            … _judged が読んだ PR と snap.commit を渡す
├── src/ndf/progress/
│   ├── model.py                               … mvv_skip・mvv_answer_void（判定の対象の欄・区分の表）
│   ├── run.py                                 … Vouch・Env.vouch・rewait・_consume・StepProgress._answered・_running_move
│   └── gate/
│       ├── asking.py                          … Target.base_ref
│       ├── answering.py                       … Unread・shared_remote・github_observe・mvv_vouch・git_base_tip・recheck
│       ├── entry.py                           … _progress が Env.vouch を差し込む・present が読めない PR でも質問を返す
│       └── records.py                         … answers・approved_head
└── tests/
    ├── adapters/test_git.py                   … run_git_within
    ├── adapters/test_github.py                … Deadline・DeadlineTransport・EnvRoutedTransport・RetryWithin・token
    ├── progress/gate/test_gate_mvv_answer.py *
    └── progress/test_run.py
```

依存の向き: `progress/gate/*` → `progress/run` → `progress/model` の一方向で、今と変わらない。`run.py` は照合関数の型（`Vouch`）だけを持ち、照合関数の中身（GitHub と git を読む）は `gate/answering.py` が作って `gate/entry.py` が `Env` へ渡す。`domains/records/*` は `progress` を読まない（判定の出力の形は `context.schema.json` が持つ）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/limits.json` | 変更 | 上限の宣言 | — | — | キー `progress.mvv-vouch`（新設） |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | `$defs.mvv_gate` | キー `pr`・`base_ref`・`base_sha`（新設） |
| `src/ndf/adapters/deadline.py` | 新設 | 単調時計の期限 | — | `Deadline` | `within`・`left`・`passed`・`active` |
| `src/ndf/adapters/git.py` | 変更 | git の起動 | `deadline` | モジュール | `run_git`（変更）・`run_git_within`（新設） |
| `src/ndf/adapters/github.py` | 変更 | GitHub の接続 | `deadline` | `RetryWithin`（変更）・`DeadlineTransport`・`EnvRoutedTransport`（新設）、モジュール | `client`・`token`（変更） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力の形 | — | モジュール | `gate_outcome`（変更） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの MVV 判定 | `mvv_material_decide` | モジュール | `_judged`（変更） |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_TARGET_KEYS`（新設）・`mvv_targets`（新設）・`mvv_skip`・`mvv_answer_void`（変更か新設） |
| `src/ndf/progress/run.py` | 変更 | ステートマシンの駆動 | `model`・`store` | `Env`（欄 `vouch`）・`StepProgress`（欄 `voided`）、モジュール | 型 `Vouch`（新設）・`rewait`（新設）・`_consume`・`_running_move`・`StepProgress._answered`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問 | `run`・`model` | `Target`（欄 `base_ref`） | — |
| `src/ndf/progress/gate/answering.py` | 変更 | 承認ゲートの回答の検証 | `asking`・`run`・`model` | `Unread`（新設）、モジュール | `github_observe`・`recheck`（変更）・`mvv_vouch`・`git_base_tip`・`shared_remote`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf progress` のエントリポイント | `answering`・`run` | モジュール | `_progress`・`present`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 回答の読み出し | `run`・`model` | モジュール | `answers`・`approved_head`（変更） |

## 判定の対象を出力に持たせる

満たす決定: 決定 3・6・7、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_outcome` | `domains/records/mvv_material_decide.py` | 変更 | `gate_outcome(gate, verdict, skip, why, head=None, holds=(), *, scope: Optional[Mapping[str, Any]] = None, pr: Optional[int] = None, base_ref: Optional[str] = None, base_sha: Optional[str] = None) -> dict`（`holds` は #3959、`scope` は #4105 が足す。この Issue はキーワードだけの `pr`・`base_ref`・`base_sha` を足す。#4105 が先に入っていれば `scope` は既にあり、この Issue は消さずに並べる。この Issue が先なら `scope` は無く、#4105 が同じ位置にキーワードだけで足す） | 値があるキーだけを出力に置く（無いキーは置かない。今の出力の形と同じ規則）。`scope` と 3 つのキーは独立に置き、片方を他方から作らない |
| `_judged` | `domains/records/mvv_gate.py` | 変更 | シグネチャは同じ | #3959 の手順 (4) 以降のすべての `gate_outcome` に、(1) で読んだ PR の番号と `pull.base_ref`、(2) の `snap.commit` を渡す。(3)（承認済みの MVV が無い）は渡さない（`skip` が真になりうるのは (8) だけなので照合には足りる）。#4105 の後のリリース承認ゲートでは、同じ呼び出しに `scope=scope` も渡す。`scope.pr` と `pr`、`scope.base` と `base_sha` は同じ変数（(1) の PR の番号・(2) の `snap.commit`）から入れ、別に読み直さない |
| `$defs.mvv_gate` | `contracts/workflow/context.schema.json` | 変更 | 任意のキー `pr`（整数、1 以上）・`base_ref`（文字列、1 文字以上）・`base_sha`（`^[0-9a-f]{40}$`） | `additionalProperties: false` のまま 3 つを足す（#4105 の `scope` と並ぶ） |

## 省く条件と読まない回答

満たす決定: 決定 6・7、受け入れ条件 5、不変条件 I2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_TARGET_KEYS` | `progress/model.py` | 新設 | `tuple[str, ...] = ("pr", "base_ref", "base_sha", "head")` | 判定の対象の欄の名前 |
| `mvv_targets` | `progress/model.py` | 新設 | `mvv_targets(held: Mapping[str, Any]) -> Optional[str]` | 判定の出力 `held` の対象の欄が形どおりか（`pr` が整数・`base_ref` と `head` が空でない文字列・`base_sha` が 40 桁の 16 進）。外れれば欠けた欄の名前を並べた理由、そろえば None |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | #3959・#4105 の条件に、`mvv_targets(held)` が None であることを足す。理由があれば None（承認ゲートで人を待つ） |
| `mvv_answer_void` | `progress/model.py` | 変更（#4105 が先なら）か新設（この Issue が先なら） | `mvv_answer_void(payload: Mapping[str, Any]) -> Optional[str]` | 回答した者が `MVV_SERVICE` の回答で、次のどれかなら理由を返す。(a) 回答の区分 `payload["gate"]` が #3959 の `MVV_SKIP_GATES` に無い（None を含む）→「省いてよい区分でない承認ゲート <区分> の省いた回答」。(b) `payload[MVV_KEY]` が辞書でない、か `mvv_targets` が理由を返す →「省いた回答が判定の対象（<欠けた欄>）を持たない」。(c) #4105 の印の条件（#4105 が足す）。先に当たった理由を返す。ほかは None |
| `StepProgress._answered` | `progress/run.py` | 変更（#4105 が先なら欄 `voided` だけを足す） | シグネチャは同じ。欄 `voided: bool = False` を足す | 変更要求でない回答のうち `model.mvv_answer_void(p)` が理由を返すものは `answer` に残さず、数え始め（`anchor`）より後なら `voided = True` にする（事象は消さない）。`voided` は `_started`・`_recorded`・変更要求の回答で偽へ戻す（数え始めが動くところ）。読める回答が後から来れば `answer` は今どおりそれを持つ |
| `_running_move` | `progress/run.py` | 変更 | シグネチャは同じ | 省く条件 `skip` に `not prog.voided` を足す。今の承認ゲートに読まない省いた回答があれば、判定の出力が欄をそろえていても省かずに `GATE` を返し、2 つ目の省いた回答を書かない |
| `answers` | `progress/gate/records.py` | 変更（#4105 が先なら済み） | シグネチャは同じ | `mvv_answer_void` が理由を返す回答を一覧から外す（#4105 の決定と同じ。一覧は読める回答だけ） |

- 読む側の配線（`StepProgress._answered`・`records.answers`・`approved_head`）は、#4105 と先に入る方が持つ。この Issue が先に入るなら、関数だけを作って読む側を #4105 に残すことはしない（関数だけでは誰も呼ばず、不変条件 I2 と受け入れ条件 5 が成り立たない）。後に入る方は、先の方が作った配線に条件を足すだけにする
- (a) の条件により、#3959 の `MVV_SKIP_GATES` に無い区分（#4105 の前の `release`）の省いた回答は、どこから書かれても読まれず消費されない（`mvv_skip` が書かないことに加えて、読む側でも落とす）
- `voided` が要るのは、判定の出力は欄をそろえ、回答へ写した `mvv_gate` だけが欄を欠く（書き損ねた・書き換えた）場合である。`_answered` が読まないだけでは `prog.answer` と `prog.waiting` がともに空のままになり、`_running_move` が `mvv_skip` を通って次の省いた回答を書く。`voided` でそれを止め、人の回答を待つ

## 照合関数

満たす決定: 決定 1・2・4・8、受け入れ条件 1・2・3、不変条件 I1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `progress.mvv-vouch` | `contracts/limits.json` | 新設 | `{"summary": …, "seconds": 60, "per_call": 30}` | 既存の欄だけを使う（`limits.schema.json`・`limits.Policy`・`limits.value` は変えない。`supervise.design-stage-read` と同じく `seconds` を合計、`per_call` を 1 回の上限に使う）。`per_call` は外部の呼び出し 1 回（REST か GraphQL の 1 要求・git か gh の 1 起動）の上限の秒、`seconds` は照合 1 回（`mvv_vouch` が作る関数の 1 回の呼び出し。接続の初期化を含む）の実経過時間の期限の秒で、コードでは `total` の名前で渡す。各呼び出しの上限は `min(per_call, 期限までの残り)` |
| `Deadline` | `adapters/deadline.py` | 新設 | `class Deadline`。`Deadline(clock: Callable[[], float] = time.monotonic)`、`within(seconds: float) -> ContextManager[None]`・`left(cap: Optional[float]) -> Optional[float]`・`passed() -> bool`・属性 `active: bool` | 単調時計の期限。`within` の中だけ期限 `clock() + seconds` を持ち（入れ子にしない。抜けると期限なしへ戻す）、`left` は期限なしなら `cap`、期限があれば `min(cap, 残り)`（0 未満は 0）、`passed` は期限があり過ぎたときだけ真。git・HTTP・gh の起動が同じ 1 つを読む。テストは `clock` を差し替える |
| `run_git` | `adapters/git.py` | 変更 | `run_git(args, cwd, *, timeout: Optional[float] = None) -> Completed` | `timeout` を `subprocess.run` へ渡す。`subprocess.TimeoutExpired` は `GitError("git <サブコマンド> が <秒> 秒で戻らない")` にする（`subprocess.run` が子を止めてから投げる）。None なら今どおり上限なし |
| `run_git_within` | `adapters/git.py` | 新設 | `run_git_within(per_call: float, deadline: Deadline) -> Runner` | 起動ごとに `deadline.passed()` なら起動せず `GitError("期限を過ぎたので git <サブコマンド> を起動しない")`、でなければ `run_git(args, cwd, timeout=deadline.left(per_call))` を打つ `Runner`（`Git(cwd, runner=…)` に渡す）。期限の外では `per_call` だけが掛かる |
| `token` | `adapters/github.py` | 変更 | `token(deadline: Optional[Deadline] = None, per_call: Optional[float] = None) -> str` | 環境変数（`GH_TOKEN`・`GITHUB_TOKEN`）があれば今どおり起動しない。無ければ `cli_floor.refuse_old` の `gh --version`（プロセスで 1 回だけ。`cli_floor` の固定の上限 `VERSION_SECONDS` 5 秒）を打ち、その直後に期限を確かめ直す。`deadline` があって `deadline.passed()` なら `gh auth token` を起動せず `GitHubError("期限を過ぎたので gh auth token を起動しない", unreached=True)`。でなければ、その時点の `deadline.left(per_call)`（`deadline` が None なら `per_call`）を `gh auth token` の `subprocess.run` に `timeout` で渡す（版確認の前に計算した残りを渡さない）。`TimeoutExpired` は `GitHubError("gh auth token が <秒> 秒で戻らない", unreached=True)`。どちらも None なら今どおり上限なし |
| `RetryWithin` | `adapters/github.py` | 変更 | `RetryWithin(remaining, deadline: Optional[Deadline] = None)` | `deadline` が `active`（照合の中）なら、5xx・上限を含むすべての失敗で `RetryOption(False)` を返し、githubkit に再試行も待ちもさせない（githubkit の既定は 5xx を最大 3 回、1・4・9 秒眠って打ち直す。2026-11-22 に `githubkit/retry.py` の `RetryServerError` で確かめた）。期限の外は今どおり（5xx は既定の再試行、上限は `remaining` で判断） |
| `DeadlineTransport` | `adapters/github.py` | 新設 | `class DeadlineTransport(httpx.BaseTransport)`。`DeadlineTransport(inner: httpx.BaseTransport, deadline: Deadline, per_call: Optional[float])` | `deadline.active` が偽（照合の外）なら、段階の待ちを `per_call` にして `inner` へそのまま渡す（今どおり。本文も今どおりストリームで返す）。期限の中では要求ごとに、(1) `deadline.passed()` なら送らず `httpx.ConnectTimeout` を投げる。(2) その要求の絶対期限 `due = 今 + deadline.left(per_call)`（`min(per_call, 照合の残り)`）を立てる。全体に余裕があっても 1 要求は `per_call` を超えない。(3) `inner.handle_request` と本文の読み切り（`response.iter_raw()`）を 1 本の daemon のスレッドで打ち、呼んだスレッドは `due` までだけ待つ（`threading.Event.wait(due までの残り)`）。接続・応答ヘッダー・次の本文の塊のどれを待っている最中でも、呼んだスレッドは `due` で戻る。(4) `due` までに終われば、読み切った本文で `httpx.Response(status_code, headers=…, content=…, extensions=…)` を作って返し、スレッドで起きた例外はそのまま投げ直す。終わらなければスレッドを待たずに `httpx.ReadTimeout` を投げる。(5) 置いていったスレッドが残り続けないよう、スレッドの中では `request.extensions["timeout"]` の connect・read・write・pool を `due` までの残りにして `inner` へ渡し、本文の塊を 1 つ受けるごとに `due` を過ぎていれば応答を閉じて止める。よってスレッドも遅くとも `due` ＋ 段階の待ち 1 つ（`due` までの残り以下）で終わる。`due` は実時間の待ちなので `Deadline` の `clock` は単調時計のまま使う。githubkit の `_request` が `httpx.TimeoutException` を `RequestTimeout` に写し（2026-11-22 に `githubkit/core.py` で確かめた）、今の `_translated` が `GitHubError(unreached=True)` にする |
| `client` | `adapters/github.py` | 変更 | 引数 `timeout: Optional[float] = None`・`deadline: Optional[Deadline] = None` を足す | githubkit の `Client(..., timeout=timeout)` へ渡す。githubkit の既定は `httpx.Timeout(None)`（上限なし。2026-11-22 に `githubkit/config.py` の `build_timeout` で確かめた）。`deadline` があれば、`transport`（テストの差し替えか、無ければ `EnvRoutedTransport()`）を `DeadlineTransport(transport, deadline, timeout)` で包み、`auto_retry=RetryWithin(budget, deadline)` にし、認証は `deadline.passed()` なら `GitHubError(unreached=True)`、でなければ `token(deadline=deadline, per_call=timeout)` で読む（残りは `token` が版確認の後に計算する） |
| `EnvRoutedTransport` | `adapters/github.py` | 新設 | `class EnvRoutedTransport(httpx.BaseTransport)`。`EnvRoutedTransport()` | 既定の接続。httpx は `Client` に `transport` を明示すると環境変数のプロキシ（`HTTP_PROXY`・`HTTPS_PROXY`・`ALL_PROXY`・`NO_PROXY`）を読まない（2026-11-22 に `httpx/_client.py` の `allow_env_proxies = trust_env and transport is None` で確かめた）ので、`httpx.HTTPTransport()` を直に包むと、プロキシが要る環境で PR を読めず、同じ接続の `recheck`・`present` も落ちる。そこで内側に `httpx.Client(trust_env=True, follow_redirects=False)` を 1 つ持ち、`handle_request(request)` は `self._client.send(request, stream=True)` を返す（経路の選択は httpx の `Client` の公開の振る舞いに任せ、`NO_PROXY` を含めて今と同じ経路になる。本文はストリームのまま返すので、`DeadlineTransport` が期限の中でも外でも同じ接続を包める）。`close` は内側の `Client` を閉じる。`deadline` の無い `client` は今どおり `transport` を明示しない（githubkit が `trust_env` で経路を選ぶ） |
| `Unread` | `progress/gate/answering.py` | 新設 | `class Unread(model.Refused)`。欄 `pr: int`・`kind: str` | PR を読めない失敗。文字列は「PR #<pr> を読めない（<kind>）」だけで、原因の例外の文字列を持たない。`kind` は `上限`（`GitHubError.quota` が真）・`届かない`（`unreached`。時間切れ・期限切れ・`gh auth token` の時間切れを含む）・`状態 <code>`（HTTP の状態の整数。照合の中は 5xx も再試行せずここへ来る）・`認証`（ほかの `GitHubError`。`token` が値を得られない）・`リモートの設定`（`GitError`。期限切れで git を起動しなかったときを含む） |
| `Target` | `progress/gate/asking.py` | 変更 | 欄 `base_ref: Optional[str] = None` を足す | 観た PR の宛先。`differs`・`as_dict`・`from_dict` は変えない（人の回答の `seen` の形と比べ方は今のまま） |
| `shared_remote` | `progress/gate/answering.py` | 新設 | `shared_remote(cwd: Path, runner: Runner) -> Callable[[], str]` | リモート名を最初の呼び出しのときだけ `Git(cwd, runner=runner).remote()`（`git worktree list` を 1 回）で解いて覚える関数を返す。失敗（`GitError`）は覚えず投げる。`github_observe` と `git_base_tip` が同じ 1 つを受け、1 回の CLI の呼び出しで `git worktree list` を 1 回にする |
| `github_observe` | `progress/gate/answering.py` | 変更 | `github_observe(cwd, root, hub=None, *, remote: Optional[Callable[[], str]] = None, runner: Runner = run_git, timeout: Optional[float] = None, deadline: Optional[Deadline] = None)` | `hub.pull(q.pr)` の 1 回の結果から `head_sha` に加えて `base_ref` を読み、`Target(head, material, base_ref=base_ref)` を返す。接続は最初の読み取りのときだけ `GitHub(Git(cwd, runner=runner).remote_repo(remote()), client(timeout=timeout, deadline=deadline))` で作る（`remote_repo` に名前を渡すので、git は `remote get-url` の 1 起動だけ足す）。`GitHubError`・`GitError` は `Unread(q.pr, kind)` で投げ（例外の文字列を載せない）、`remote_repo` が投げる URL 入りの `GitError` も同じく区分だけにする。REST の呼び出しは増えない |
| `git_base_tip` | `progress/gate/answering.py` | 新設 | `git_base_tip(cwd: Path, remote: Callable[[], str], runner: Runner) -> run.BaseTip`（`BaseTip = Callable[[str], Optional[str]]` は `run.py` に置く） | 宛先のブランチ名を受け、`Git(cwd, runner=runner)` で、`remote()`（`shared_remote` の値。`github_observe` が先に解いていれば git を打たない）のリモートへ `git ls-remote -- <リモート> refs/heads/<ブランチ>` を 1 回打ち、参照の名前がちょうど `refs/heads/<ブランチ>` の行の SHA を返す（ls-remote の照合は末尾の一致なので、`refs/heads/x/refs/heads/<ブランチ>` のような行を採らない）。終了コードが 0 でない・行が無い・`GitError`（時間切れ・起動できない・リモートの設定）は None（ブランチが無いと終了コード 0 で出力が空になる。2026-11-22 に `git ls-remote -- origin refs/heads/<無いブランチ>` で確かめた）。関数を作るだけでは git を打たない |
| `mvv_vouch` | `progress/gate/answering.py` | 新設 | `mvv_vouch(observe: Observe, base_tip: run.BaseTip, deadline: Deadline, total: float) -> Vouched`。`Vouched` は呼べる物（`run.Vouch` の形）で、欄 `seen: dict[int, Optional[Target]]` を持つ | 照合関数を作る。作る関数は `(v, m)` を受け、`with deadline.within(total):` の中で、`m.event` の payload の `MVV_KEY`（判定のコピー `held`）を今の PR と宛先の先端に照らし、照合が通らない理由か None を返す（下の手順）。t2・t3 の前に `deadline.passed()` なら打たずに理由「照合の期限 <total> 秒を過ぎた」を返す。PR を読んだら最後の結果を `seen[q.pr]` に残す（読めた `Target`、読めなければ None。待ちの表示 `present` が読み直さないため） |
| `recheck` | `progress/gate/answering.py` | 変更 | シグネチャは同じ | 読む回答の payload が `by: MVV_SERVICE` なら照合せずに None を返す（照合は `_consume` が排他の中で行う。決定 1）。人の回答は今どおり。待ちへ戻す事象の組み立ては `run.rewait` を使う |

- `mvv_vouch` が作る関数
  - 事前条件: 呼ぶ側（`_consume`）が駆動の排他を持ち、`m.action` が `CONSUME`、`m.event` が `by: MVV_SERVICE` の回答
  - 事後条件: 理由が None のときだけ、PR の 1 回目の読み取り（t1）・宛先の先端の読み取り（t2）・PR の 2 回目の読み取り（t3）がすべて判定のコピーと一致している。つまり t1 から t3 の間で、PR の番号・宛先・head が判定のときのままと 2 回観え、その間の t2 に宛先の先端が判定の `base_sha` だった
  - 副作用: 下の「呼び出しの数」の表。事象を書かない

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `mvv_vouch` が作る関数 | `held` が辞書でない・`model.mvv_targets(held)` が理由を返す | 理由「省いた回答が判定の対象を持たない: <欠けた欄>」（外部を呼ばない） |
| 〃 | `q = question(v, replace(m, action=GATE))` の `q.pr` が None か `held["pr"]` と違う | 理由「判定した PR #<held.pr> と承認ゲートの PR #<q.pr> が違う」（外部を呼ばない） |
| 〃 | (t1) `now = observe(q)` が `model.Refused` を投げる | 理由は `Unread` なら `str(exc)`（「PR #<番号> を読めない（<区分>）」）、ほかの `Refused` なら「PR #<番号> を読めない」（例外を外へ出さない。原因の文字列を載せない） |
| 〃 | `now.base_ref != held["base_ref"]` | 理由「判定した宛先 <held.base_ref> と今の宛先 <now.base_ref> が違う」 |
| 〃 | `now.head != held["head"]` | 理由「判定した head <held.head> と今の head <now.head> が違う」 |
| 〃 | t2 か t3 の前に `deadline.passed()` | 理由「照合の期限 <total> 秒を過ぎた」（以降を打たない） |
| 〃 | (t2) `base_tip(held["base_ref"])` が None か `held["base_sha"]` と違う | 理由「判定が規則を読んだベース <held.base_sha> と今の <宛先> の先端 <SHA か 読めない> が違う」 |
| 〃 | (t3) `again = observe(q)` が `model.Refused`、か `again.base_ref`・`again.head` が `held` と違う | t1 の 3 行と同じ理由（どちらの読み取りで違ったかを「先端を読んだ後に」と添える） |
| 〃 | ほか | None |

- 順序の約束: PR の読み取り（t1）→ 宛先の先端（t2）→ PR の読み取り（t3）の順に打ち、前で違えば後を打たない。t1 の間に宛先へマージが入っても t2 の先端に入る（指摘 r6-1）。t1 の後、`ls-remote` の応答を待つ間に PR の head を進める・同じ head で宛先を変える変更は t3 に入る（指摘 r1-1）
- 残る競合（同じ排他でも閉じないもの）: (1) t3 の後から消費の事象を書くまでの、ローカルの事象 1 つの書き込みの間に GitHub で起きた変更。(2) t1 と t3 の間に変えて戻した変更（宛先を別のブランチへ変え、t3 の前に戻すなど）。(3) t2 の後から事象を書くまでの宛先へのマージ。どれも別々の読み取りでは閉じられない（GitHub に条件つきの読み取りの約束が無い）。消費の後の変更と同じく次の判定が読み、マージの側の `--expect-head` が head の変化を止める（設計文書の「残る窓」）
- 理由の先頭には「MVV 判定で省いた回答を読まない: 」を付ける（呼ぶ側の `_consume` が付ける）

### 呼び出しの数

省いた回答を 1 つ消費する駆動 1 回あたり（`R` は REST の 1 要求、`G` は GraphQL の 1 要求）。`recheck` は省いた回答を読み飛ばすので、省いた回答だけを消費する通常の `next` では、`GitHub` の接続とリモートの解決が照合の t1 で初めて起きる（遅延の初期化）。表はその初期化を含めた数である。

初期化（1 回の CLI の呼び出しで 1 回だけ。同じ呼び出しで先に `recheck`・`_took`・`present` が PR を読んでいれば 0）:

| 何を | git | gh | 起こす所 |
| --- | --- | --- | --- |
| リモート名（`shared_remote`） | `worktree list` 1 | — | t1 の接続の作成（`git_base_tip` は同じ値を使い、打たない） |
| リポジトリ名（`remote_repo(remote())`） | `remote get-url` 1 | — | t1 の接続の作成 |
| 認証（`token`） | — | 環境変数があれば 0。無ければ `gh --version` 1（プロセスで初めての gh のときだけ）・`gh auth token` 1 | t1 の接続の作成 |

照合の分（初期化を除く）:

| 場合 | GitHub | git | 待ちの表示（`present`）の PR の読み直し |
| --- | --- | --- | --- |
| 欄を欠く・質問の PR が違う | 0（初期化もしない） | 0 | 1 R（照合で読んでいないので今どおり読む。初期化はここで起きる） |
| 一致して消費する | 2 R | `ls-remote` 1 | 無し（消費して待たない） |
| 初期化で失敗する（リモートの設定・認証・期限） | 0 | 初期化の分まで | 0（`Vouched.seen` に None。読み直さずに head 無しの質問） |
| t1 で宛先か head が違う・読めない | 1 R | 0 | 0（`Vouched.seen` の t1 の結果を使う。読めなかったなら読み直さずに head 無しの質問） |
| t2 で先端が違う・読めない | 1 R | `ls-remote` 1 | 0（t1 の値を使う） |
| t3 で違う・読めない | 2 R | `ls-remote` 1 | 0（t3 の値を使う） |
| REST のスロットが上限 | 上の各 R が「R 1（上限で断られる）＋ G 1」になる（`GitHub.pull` の読み替え） | 同じ | 同じ |
| GitHub が 5xx を返す | 上の各 R が 1 要求のまま（照合の中は再試行しない）で、そこで `Unread(状態 5xx)` | 同じ | 同じ |

- 一致して消費するときの合計は、初期化が要るとき GitHub 2 R・git 3 起動（`worktree list`・`remote get-url`・`ls-remote`）・gh 0〜2 起動、初期化が済んでいるとき GitHub 2 R・git 1 起動である。前の版の設計（`git_base_tip` が自分でリモート名を解く）より `worktree list` が 1 起動少ない
- 排他を持つ最長の時間: 照合の外部の呼び出しは、初期化も REST から GraphQL への読み替えも含めて、すべて 1 つの `Deadline`（`total`、既定 60 秒）の中で打つ。各呼び出しの上限は `min(per_call, 残り)` で、git と `gh auth token` は `subprocess.run` の全体の上限、HTTP は `DeadlineTransport` が要求ごとに `min(per_call, 残り)` の絶対期限を立て、接続・応答ヘッダー・本文の塊を待っている最中でもその期限で戻るので、段階の待ちが積み重なる場合も、少しずつ届く応答も、期限の直前に本文の一部が届いてから止まる場合も、期限で切れる。再試行は照合の中ではしない（`RetryWithin` が止める）ので、再試行の待ちは予算に入らない。期限を過ぎた後の呼び出しは打たない。よって照合が排他を持つ最長は `total` に、子を止める時間と、期限の直前に始まった `gh --version`（`cli_floor` の固定の 5 秒。環境変数のトークンが無く、プロセスで初めて gh を起動するときだけ）を足した値で、既定で 65 秒である
- 待ちの表示が `Vouched.seen` を使うのは、同じ CLI の呼び出しで `_consume` が照合して待ちへ戻したときだけで、`seen` の値は排他の中の直前の読み取りである。ほかの経路の待ちの表示は今どおり `observe` で読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `mvv_vouch` が作る関数 | PR を読めない | GitHub が回答しない・権限・上限 | `observe` の `Unread` | 理由を返す | 承認ゲートの待ち（`gate`）。警告に理由（PR の番号と区分だけ） | 人が回答する |
| `mvv_vouch` が作る関数 | PR の読み取りが戻らない・少しずつしか届かない | 応答の止まった接続・遅いサーバー | `DeadlineTransport` の要求ごとの絶対期限 `due`（接続・応答ヘッダー・本文のどれを待っていても `due` で戻る） → `httpx.TimeoutException` → githubkit の `RequestTimeout` → `GitHubError(unreached)` → `Unread(kind="届かない")` | 理由を返す | 同上 | 同上。`_consume` が `rewait` を書いて戻り、`advance` の `with store.run_lock` を抜けて排他を放す |
| `mvv_vouch` が作る関数 | GitHub が 5xx を返す | GitHub の一時的な障害 | `RetryWithin` が照合の中では再試行しない → `GitHubError(status)` → `Unread(kind="状態 <code>")` | 理由を返す | 同上 | 同上（人が回答するか、次の判定が省く） |
| `mvv_vouch` が作る関数 | 接続の初期化が戻らない | `git worktree list`・`git remote get-url`・`gh auth token` の停止 | `run_git_within` と `token(timeout=…)` の時間切れ → `GitError`・`GitHubError(unreached)` → `Unread` | 理由を返す | 同上 | 同上 |
| `mvv_vouch` が作る関数 | 照合の期限を過ぎた | 呼び出しの合計が `total` を超えた | `deadline.passed()`（t2・t3 の前・各起動の前・`DeadlineTransport`） | 理由を返す（以降を打たない） | 同上（「照合の期限 <total> 秒を過ぎた」か「届かない」） | 同上 |
| `mvv_vouch` が作る関数 | 宛先の先端を読めない | `git ls-remote` の失敗・ブランチが無い | `base_tip` が None | 理由を返す | 同上（「読めない」） | 同上 |
| `git_base_tip` | git が戻らない | 応答の止まったリモート | `run_git` の `TimeoutExpired` → `GitError` | None を返す（例外を外へ出さない） | — | 排他は呼ぶ側の `with` を抜けて放す |
| `git_base_tip` | git が失敗する | ネットワーク・リモートの設定 | `GitError` か終了コード | None を返す（例外を外へ出さない。stderr を理由に載せない） | — | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `git_base_tip` | `Git.run`・`Git.attempt`・`Git.remote`（`adapters/git.py`）、`ls-remote` の既存の呼び出し（`domains/release/gather.py`・`assemble.py`） | `grep -rn "ls-remote" src/ndf` | 使う（`Git.attempt("ls-remote", "--", remote, ref)` の出力の 1 列目）。上限は `Git` の `runner` に `run_git_within` を渡して掛け、新しい git の関数は足さない |
| `run_git_within` | `limits.json` の `per_call`（`supervise.design-stage-read`）、`Runner` の型 | `limits.json` と `adapters/git.py` を読んだ | 上限の宣言は `limits.json` の形に倣う。git の起動に上限を掛ける部品は今は無い（`run_git` は `subprocess.run` に timeout を渡さない）ので足す |
| `Deadline`・`DeadlineTransport` | githubkit の `timeout`（`httpx.Timeout`）・`auto_retry`、httpx の `BaseTransport` と `request.extensions["timeout"]`、`client` の `transport` 引数 | `githubkit/config.py`・`retry.py`・`core.py` と `adapters/github.py` を読んだ（2026-11-22） | githubkit と httpx には要求全体の実経過時間の上限が無い（`httpx.Timeout` は段階ごとで、読み取りの待ちは受信 1 回ごとに数え直す）ので、既存の `transport` の差し替え口に包む transport を足す。待ちの最中に打ち切るのは、要求をスレッドで打って呼んだ側が `due` で戻る形にする（httpcore の接続の部品（`network_backend`）を差し替える形は、`httpx.HTTPTransport` が公開の引数で受けないので採らない）。再試行は既存の `RetryWithin` に条件を足して止め、新しい再試行の部品は作らない |
| `shared_remote` | `Git.remote()`・`Git.remote_repo(remote)` | `adapters/git.py` を読んだ | 使う。`remote_repo` は名前を受けられるので、名前の解決だけを覚えて共有する |
| `mvv_vouch` | `recheck` の対象の読み方（`question`・`observe`・`payload_of`） | `answering.py` を読んだ | 使う。`Target.differs` は使わない（人の回答の比べ方を変えないため） |

## 消費の直前の照合

満たす決定: 決定 1・2・8、受け入れ条件 1・2・3・6、不変条件 I1・I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Vouch`・`BaseTip` | `progress/run.py` | 新設（型） | `Vouch = Callable[[View, Move], Optional[str]]`・`BaseTip = Callable[[str], Optional[str]]` | 照合関数と宛先の先端を読む関数の型 |
| `Env` | `progress/run.py` | 変更 | 欄 `vouch: Optional[Vouch] = None` を足す | 省いた回答の照合関数。`ndf progress` のエントリポイント（`entry._progress`）だけが置く |
| `rewait` | `progress/run.py` | 新設 | `rewait(v: View, m: Move, why: str) -> model.EventDraft` | 承認ゲートのステップを待ちへ戻す事象（`STEP_EVENT`・`status: waiting`・`report: None`・今の `generation`・`reason: why`）。今の `recheck` の中の組み立てを移し、`recheck` と `_consume` が使う |
| `_consume` | `progress/run.py` | 変更 | シグネチャは同じ | 回答（`m.event`）が `by: MVV_SERVICE` なら、`d.env.vouch` が None なら理由「省いた回答を照合する関数が無い（ndf progress のエントリポイントを通らない駆動）」、あれば `d.env.vouch(v, m)` を 1 回呼ぶ。理由があれば `d.write(rewait(v, m, "MVV 判定で省いた回答を読まない: " + 理由))` を書き、警告に理由を足して None を返す（駆動を続け、次の手は `GATE`）。理由が無ければ、続けて今の承認の読み方で消費する（`finished` を書く）。照合と消費の間に別の読み取りを挟まない。人の回答は今どおりで、照合関数を呼ばない。回答が無いときに省いた回答を書いて `next` を返す今の手は変えない |
| `_progress` | `progress/gate/entry.py` | 変更 | シグネチャは同じ | `per_call = limits.value("progress.mvv-vouch", "per_call")`・`total = limits.seconds("progress.mvv-vouch")` を読み（新しい欄を足さないので、スキーマが方針を拒まず、照合の要否より前に読んでも通常の進行操作を落とさない）、`deadline = Deadline()`、`runner = run_git_within(per_call, deadline)`、`remote = shared_remote(cwd, runner)`、`seen = github_observe(cwd, st.root, remote=remote, runner=runner, timeout=per_call, deadline=deadline)`、`vouched = mvv_vouch(seen, git_base_tip(cwd, remote, runner), deadline, total)` を作り（どれも作るだけで外部を呼ばない）、`run.Env(facts=…, vouch=vouched)` を `lane_env` で包んで駆動へ渡す。`seen` はエントリポイントの `recheck`・`_took`・`present` と共有し（`GitHub` の接続は 1 つ）、`present` には `vouched.seen` も渡す |
| `present` | `progress/gate/entry.py` | 変更 | 引数 `known: Mapping[int, Optional[Target]] = {}` を足す | 承認ゲートの待ちの質問の対象を、`asked.pr` が `known` にあり値が `Target` ならその head・`base_ref` に今の承認資料の sha256 を合わせて作り、値が None（照合で読めなかった）なら読み直さずに下の読めないときと同じにし、`known` に無ければ今どおり `observe(asked)` で読む。`observe` が `model.Refused` を投げたら、例外を外へ出さず `target` を `Target(None, 承認資料の sha256)` にして質問と回答ごとのルートを返し、警告「<Unread の文字列>。質問に今の head を載せていない。PR を読めるようになってから ndf progress next で質問を出し直すか、PR の head を見て ndf answer で回答する（回答も head を照らす）」を足す。結果は承認ゲートの待ち（`gate`）のままで、ステートマシンの待ちは変えない |

- 照合関数を呼ぶのは `_drive` を通る `next`（`run.advance`）と `work`（`run.work`）だけである（2026-11-22 に `run.py` の `COMMANDS` と `_drive` の呼び出し元で確かめた）。`show`・`wait`・`resync`・`answer` は `_drive` を通らないので、`Env.vouch` を持っても PR の読み取りも `git ls-remote` も増えない（指摘 r6-3）。`next` が起動する `ndf progress work` の子は `progress_main` → `_progress` を通り、自分の `Env.vouch` を持つ（指摘 r6-2）。エントリポイントを通らずに `run.work` を直接呼ぶ駆動は `Env.vouch` が None で、省いた回答を待ちへ戻す
- 照合が通らないときの `rewait` は `_consume` が書くので、PR を読めないときも例外を外へ出さずに人の承認待ちへ移る（指摘 r6-4）。同じ CLI の呼び出しの待ちの表示（`present`）は、照合の読み取りの値（`known`）を使うか、読めなければ head 無しの質問と警告を返すので、PR の取得が続けて失敗しても結果は停止（`stopped`）でなく承認ゲートの待ち（`gate`）になる（指摘 r1-2）

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じステートマシンへの `next` と `work` の子 | ステートマシンの事象の表 | `_consume` は `store.run_lock` の中で照合と消費（か待ちへ戻す事象）を続けて書く。照合の外部の呼び出し（接続の初期化・読み替え・REST・git を含む）はどれも 1 つの `Deadline`（`total`）の中で打ち、期限を過ぎた後は打たず、照合の中は再試行しないので、排他を持つ時間には上限がある（既定で 65 秒。「呼び出しの数」） | 照合の途中で落ちたら事象は書かれず、次の駆動が同じ回答を照合し直す。待ちへ戻した後は `prog.waiting` があり、同じ回答を 2 度照合しない |
| `next` と `ndf answer`（人の回答） | 同上 | `ndf answer` は今どおり。人の回答が書かれた後の `decide` は人の回答を読むので照合関数を呼ばない | — |
| 照合と GitHub の PR・宛先の変更 | GitHub の PR と宛先のブランチ | 排他では止められない。t1・t2・t3 の読み取りで照合の間の変更を見る（「処理の手順」の順序の約束と残る競合） | 消費の後の変更は次の判定と `--expect-head` が読む |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_consume` | 待ちへ戻した理由 | 事象の `reason`・結果 JSON の `warnings` | 例外の文字列・git の stderr・リモートの URL（理由に載るのは PR の番号・失敗の区分・ブランチ名・SHA だけ。`Unread` と `git_base_tip` が原因の文字列を捨てる） |
| `present` | PR を読めないときの警告 | 結果 JSON の `warnings` | 同上 |

## 承認済みの head

満たす決定: 決定 5、受け入れ条件 4、不変条件 I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `approved_head` | `progress/gate/records.py` | 変更 | シグネチャは同じ | (1) 事象の表の `answered` を `mvv_answer_void` で外さずに読み、区分 `kind` の最後の回答（生の最新の回答）を決める。(2) その回答が承認でない・`mvv_answer_void` が理由を返す・`by: MVV_SERVICE` でその回答の事象番号を `report.metrics.seq` に持つ `STEP_EVENT` の `finished`（`gate_report` が今も書く）が無い、のどれかなら None。前の回答へさかのぼらない。(3) 人の回答は今どおり（文書だけの差で進めた head の扱いを含む） |

- `answers`（一覧）は読めない回答を外すので、`approved_head` は `answers` の結果から最後の行を選ばない（外された最新の回答の前の承認が、マージ用の head として戻ってしまう）。生の回答の行の組み立ては `answers` と共有の内部関数 `_rows(v)` に置き、`answers` はその結果から読めない回答を外す
- `answers` が返す行には事象番号（`seq`）が今もある

## 単体テストの観点

`tests/progress/gate/test_gate_mvv_answer.py`（新設）で、実際のステートマシン（`store` と `run`）に事象を書き、照合関数・宛先の先端を読む関数のスタブを渡して縛る。`Deadline`・`DeadlineTransport`・`RetryWithin`・`token` は `tests/adapters/test_github.py`、`run_git_within` は `tests/adapters/test_git.py` に足す。`tests/progress/test_run.py` の今の省いた回答のテスト（`service:mvv` で工程が進む）は、一致を返す照合関数を `Env` に渡す形へ直す。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_targets`・`mvv_skip` | 4 つの欄がそろう `skip: true` → 省く | `pr` が文字列・`base_sha` が 39 桁・`base_ref` が無い → None | — |
| `mvv_answer_void` | 欄のそろう設計承認ゲートの省いた回答 → None。人の回答 → None | 欄を 1 つ欠く省いた回答 → 理由。`MVV_SKIP_GATES` に無い区分（`release`、#4105 の前）か区分 None の省いた回答 → 理由 | `mvv_gate` が辞書でない → 理由 |
| `StepProgress._answered`・`_running_move` | 欄のそろう省いた回答 → `answer` に残り `CONSUME` | 判定の出力は 4 つの欄をそろえ、回答の `mvv_gate` だけが欄を欠く事象の列 → `voided` が真、`decide` が `GATE`、駆動しても 2 つ目の `service:mvv` の回答を書かない。その後に人の承認を `ndf answer` で書く → 受けられ、消費して工程が進む | 許されない区分の省いた回答の事象 → 読まれず `GATE` |
| `mvv_vouch` が作る関数 | 判定と同じ PR・宛先・head・先端 → None。`observe` は 2 回、`base_tip` は 1 回 | 同じ head で宛先だけが違う → 理由。質問の PR が違う → 理由で `observe` を呼ばない。`observe` の 1 回目のスタブの中で先端を進める → 理由。`base_tip` のスタブの中で PR の head を進める・同じ head で宛先を変える → t3 で理由 | `observe` が `Unread` → 理由に PR の番号と区分だけ（原因の文字列を載せたスタブの例外の文字列が理由に無い）。`base_tip` が None → 理由 |
| `_consume` | 一致を返す照合関数 → 1 回の駆動で消費し、工程が進む | 理由を返す照合関数 → 待ちへ戻り次の手が `GATE`、`finished` が無い。エントリポイントを通った後に別の駆動が省いた回答を書いた状態で `advance` → 照合関数が 1 回呼ばれてから消費か待ち | `Env.vouch` が None の `run.advance`・`run.work` → 待ちへ戻り `next` を返し続けない |
| `_consume`（時間切れ） | — | — | `base_tip` が上限で None を返す（スタブの runner が `TimeoutExpired`）→ 待ちへ戻り、続けて別の `run.advance` が排他を取れる（`is_live` が偽）。HTTP の側: `client(transport=…)` に、本文の一部を返してから `threading.Event` で止まるスタブの transport と実時間の短い期限を渡す → t1 の読み取りが要求ごとの期限で戻って `Unread(届かない)` で待ちへ戻り、続けて別の `run.advance` が排他を取れる |
| `_consume`（期限と再試行） | — | 期限が t1 の後に切れる時計 → t2・t3 を打たず（`base_tip` と 2 回目の `observe` が呼ばれない）理由「照合の期限」 | 5xx を返すスタブの transport → 照合の中の要求は 1 回だけで `Unread(状態 502)`、期限の外（同じ接続での `present`）では今どおり再試行する。REST が上限を返し GraphQL の読み替えの前に期限が切れる → GraphQL の要求を送らず `Unread(届かない)` |
| `_consume`（初期化） | 環境変数のトークンが無く、`recheck` が PR を読まない `progress_main(["next", …])` → 起動を数えるスタブで、git は `worktree list`・`remote get-url`・`ls-remote` の 3 起動（`worktree list` は 1 回）・gh は `gh auth token` に timeout が渡る | 環境変数のトークンがある → gh を起動しない | `gh auth token` か `git remote get-url` が戻らないスタブ → `Unread` で待ちへ戻り、排他を放す |
| `_consume`（人の回答） | 人の承認 → 照合関数を呼ばずに消費 | — | — |
| `recheck` | 人の回答で head が違う → 待ちへ戻す（今と同じ）。文書だけの差 → 出し直さずに進む（#3020） | 省いた回答 → `observe` を呼ばずに None | — |
| `_progress`・`present`（CLI の結果） | `show`・`wait`・`resync` で省いた回答があっても照合関数を呼ばない（呼ぶと落ちるスタブ） | `ndf progress work` の子のエントリポイントで作った `Env` が照合関数を持つ。宛先の違いで待ちへ戻した `progress_main(["next", …])` → `observe` の呼び出しは照合の 1 回だけで、結果は `gate` | `observe` が常に `Unread` を投げる `progress_main(["next", …])` → 結果は `gate` で、質問・回答ごとのルート・PR を読めない警告があり、`stopped` にならない。警告と事象の理由に原因の文字列（URL を含むスタブの文字列）が無い |
| `approved_head` | 消費された省いた回答 → その head | 待ちへ戻された・まだ消費されていない省いた回答が最後 → None。人の承認の後に、同じ head を持つ読めない省いた回答（欄を欠く）が最後 → None（前の人の承認へさかのぼらない） | — |
| `gate_outcome`・`_judged` | 手順 (8) の出力が `pr`・`base_ref`・`base_sha`（= `snap.commit`）を持ち、`context.schema.json` に通る。#4105 の後のリリース判定の出力が `scope` と 3 つを持ち、`scope.pr == pr`・`scope.base == base_sha` | 手順 (3) の出力は 3 つを持たない | — |
| `git_base_tip`・`run_git`・`shared_remote` | 一時のリモートのリポジトリでブランチの先端の SHA を返す。`github_observe` と `git_base_tip` が同じ `shared_remote` を使うと、リモート名の解決（`git worktree list`）は合わせて 1 回 | ブランチが無い → None | リモートが無い → None で例外を出さない。`run_git(…, timeout=0.01)` で戻らない git（スタブの runner）→ `GitError`。期限を過ぎた `run_git_within` → 起動せず `GitError` |
| `token` | 環境変数のトークンがある → gh を起動しない | 時計を進めるスタブで `gh --version` の間に残りが 10 秒から 3 秒へ減る → `gh auth token` の timeout は 3 秒（版確認の前の 10 秒でない） | `gh --version` の間に期限が切れる → `gh auth token` を起動せず `GitHubError(unreached)` |
| `EnvRoutedTransport`・`client` | `HTTP_PROXY` を手元のスタブのプロキシ（受けた要求を記録する HTTP サーバー）へ向け、`DeadlineTransport(EnvRoutedTransport(), …)` で http の宛先（手元の別のスタブのサーバー）へ要求を送る → 期限の中でも外でも要求がプロキシを通る。`client(deadline=…)` に `transport` を渡さない → 包む内側が `EnvRoutedTransport` | `NO_PROXY` に宛先を足す → プロキシを通らず宛先へ直接届く | — |
| `Deadline`・`DeadlineTransport`・`RetryWithin` | 期限の外では `left(cap)` が `cap`、`RetryWithin` が今どおり 5xx を再試行する | `within` の中で `left` が残りに縮み、0 未満にならない | 期限を過ぎた要求を送らず `httpx.ConnectTimeout`。期限の中の 5xx・上限 → 再試行しない。次の 3 つは `threading.Event` で止まるスタブの `inner` と実時間の短い期限（`total` 0.3 秒・`per_call` 0.2 秒）で、呼び出しが `due` の直後に `httpx.ReadTimeout` で戻り、スタブが止まったままでも戻ることを確かめる（終わりにスタブの Event を立てて放す）: (a) 接続を待つ段階と応答ヘッダーを待つ段階がそれぞれ残りの大半を使う（待ちが積み重なって期限をまたぐ）、(b) 期限の直前に本文の塊を 1 つ返してから止まる、(c) 照合の残りは十分（`total` 10 秒）で、本文を `per_call` より短い間隔で少しずつ返し続ける → `per_call` で切れる。期限の外の要求はスレッドを使わず `inner` へそのまま渡る |
| `github_observe` | — | — | `remote_repo` が URL 入りの `GitError` → `Unread(kind="リモートの設定")` で文字列に URL が無い |
