# #4016: GitHub への書き込みをまとめ、マシン全体で間を空ける（実装計画）

## 関連リンク

- 要求仕様: [issue-4016-requirements.md](./issue-4016-requirements.md)（正は #4016 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4016-design.md](./issue-4016-design.md)・詳細設計 [issue-4016-design-detail.md](./issue-4016-design-detail.md)（PR #4058 でマージ。2026-11-22 に設計承認）
- 後から入った関係する設計: [issue-4059-design.md](./issue-4059-design.md)（エージェントの `gh` も同じ記録で間を空ける）・[issue-4172-design.md](./issue-4172-design.md)（#4059 の直し。決定 3 で `kind_of` を `write_of` に置き換える）。どちらの実装もこの課題の範囲の外
- モード: `standard`（conductor の判定。公開のふるまいを変える機能追加で、設計承認ゲートを通した）

## 設計の後に分かったことと、実装で合わせること

設計の確定（2026-11-22）の後に、lcrfast の #4193 が cross-review のコードを変えた。今の main を正とし、次のとおり合わせる。

| # | 設計の記述 | 今のコード | 実装で合わせること |
| --- | --- | --- | --- |
| A1 | `Journal._raised_again` が再発のときに `occurrence` を書き直す | クラスは `FindingJournal`。再発の処理は `_revive`（`journal.py:113`）にある | `occurrence` の書き直しは `FindingJournal._revive` に置く |
| A2 | 新しい指摘の記録で `occurrence` を書く | 新しい行を作る経路が `enter`・`carry`・`apply_fix` の 3 つある | 3 つの経路がそろって使う `_journal_row` で値を書く |
| A3 | `limit_of`・`Limit`・`reasons_of` を `adapters/github.py` に足す | `github.py` は 1,000 行で、構造の検査の上限（`checks/rules.json` の `max_lines` 1,000）にちょうど達している。`tests/adapters/test_github.py` も 988 行 | 二次レート制限の見分け（`Limit`・`limit_of`・`reasons_of`）を新しいモジュール `adapters/github_limits.py` に置き、テストも `tests/adapters/test_github_limits.py` に分ける。`github.py` へ足す行は、`github.py` の中の既存の処理を新しいモジュールへ移して相殺する |
| A4 | `_post_report` が `write_gate.pr_lock(repo, pr)` の中で状態ファイルを読む | `_post_report` はリポジトリの名前を受けず、状態ファイルを `hub_of()` より前に読む（`cli.py:411-440`） | `cli.Hub` に PR の公開のロックを返す欄 `publishing: Callable[[int], ContextManager]` を足し、`_github_hub` が `pr_lock(slug, pr)` を返す。`_post_report` は `hub_of()` → ロック → 状態ファイルの読み取りの順にする。テストの `FakeHub` は何もしないロックを返す（本物のホームの下へロックのファイルを書かない） |
| A5 | `GitHub` は `gate` の欄で `client()` と同じ `WriteGate` を受ける。渡すのは `_github_hub` | `GitHub(slug, client())` の呼び手が `_github_hub` のほかに 16 か所ある | `GitHub.gate` の既定を `api.config.throttler` から取る（`WriteGate` か、`within` の throttler ならその元の `WriteGate`。どちらでもなければ None）。16 か所の呼び手を変えずに、GraphQL の HTTP 200 の二次レート制限も `block` する（E5） |
| A6 | `GitHub.graphql` が GraphQL の `PrimaryRateLimitExceeded` の `message` も `limit_of` へ渡す | `graphql` は `GraphQLFailed` だけを受ける | `except PrimaryRateLimitExceeded` を足し、`parsed_data.errors` の `message` を `limit_of` に渡す |
| A7 | `_translating` の呼び手は `rest`・`graphql` | 3 つ目の呼び手 `artifact_zip`（GET）がある | 3 か所とも同じ `gate` を渡す |
| A8 | `cross-review.thread-spacing` を消す（地図は `limits.json` と Python だけ） | Skill の文書 `skills/cross-review/docs/02-fix-and-rotation.md:81-89` が方針の名前と、閉じた指摘への返信を書いている | 同じ単位で文書を直す（閉じた理由を最初のコメントへ入れること、間隔は `github.write-spacing`・`github.content-spacing` が持つこと、打ち直しが指数的に延びること） |
| A9 | テストの偽物は今の形のまま使える | `FakePort` に `open_review`・`changed` が無い。偽の例外 `_Limited`・`_Refused` に `secondary`・`hinted`・`reasons` が無い | 偽物と偽の例外へ欄とメソッドを足す。本体は `GitHubError` の欄として読む（`getattr` で逃がさない） |

## 実装で決めたこと

設計が決めていない、または設計の後の設計（#4172）が形を変えた点を、次のとおり決めた。

| # | 問い | 決めたこと | 理由 |
| --- | --- | --- | --- |
| D1 | #4172 の決定 3 の `write_of` の形で作るか | **作る。** `write_gate.write_of(request) -> Optional[Writes]` を新設し、`kind_of` は作らない。`Writes` は `NamedTuple(kind: str, count: int)` で `write_gate.py` に置く。`WriteGate.acquire` は `with self.hold(w.kind, w.count):` で順番を取り、`hold(kind, count)` は予約の間隔を `count` 倍にする。件数は、この課題では GraphQL も REST も 1 を返す（最上位のフィールドの数え方 `mutation_write` と graphql-core の依存は #4059 の PR が足し、`write_rules.py` へ `Writes` を移すときは `write_gate` から読み直せるように名前を残す） | #4172 の設計が「#4016 の実装の PR は `write_of` の形で作る」と書く（`issue-4172-design.md` の影響と危険）。名前と返り値を先に合わせれば、#4059 は件数の数え方を足すだけで、#4016 のテストと呼び手を書き直さない。graphql-core と `write_rules.py` は今のリポジトリに無く、足すのは #4059 の範囲 |
| D2 | 種類の決め方（件数を数えない間） | `/graphql` の POST は本文の JSON の `query` を先頭の空白とコメントを除いて読み、`mutation` で始まらなければ None、最初のフィールドの名前が `create`・`add` で始まれば `CONTENT`、ほかは `WRITE`。本文が JSON でない・`query` が文字列でなければ `Writes(WRITE, 1)` | 詳細設計の `kind_of` の事後条件のまま。#4172 の `write_of` の事後条件とも合う |
| D3 | `github.write-send-lock`・`github.pr-publish-lock` の秒を置く欄 | `lock` の欄に置く（値は設計どおり 60 秒・900 秒） | `limits.schema.json` は `interval` を「問い合わせ・打ち直しの間隔の秒」、`lock` を「錠を待つ上限の秒」と定める。設計の値の意味はロックの待ちの上限で、`relay.key-helper` が同じ欄を使う |
| D4 | `drive.py` の 3 つの関数（`_settle_resolved`・`_carry`・`_detach_revived`）が、`run` の途中で状態ファイルの `threads` の印をロックの外で書く | 変えない。不変条件 I4 の「同じ PR の公開のロックの中でだけ書く」は公開（`_post_report`）の中の書き込みの約束と読む | 3 つは `run` 自身の状態ファイルを書き、公開の重複（I4）を起こさない。同じ PR の `report` と並んだときの上書きは、この課題の書き込みの重複とは別の問題で、範囲外として #4257 に起票した |

## 修正対象

詳細設計の「地図」から引き写し、上の A3・A8・A9 で足すファイルを加えた。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/adapters/write_gate.py`（新設） | `Writes`・`write_of`・`WriteGate`（`acquire`・`async_acquire`・`hold`・`reserve`・`Reservation`・`withdraw`・`within`・`sent`・`block`）・`Busy`・`machine_gate`・`gate_path`・`pr_lock` |
| `plugins/ndf/src/ndf/adapters/github_limits.py`（新設。A3） | `Limit`・`limit_of`・`reasons_of` |
| `plugins/ndf/src/ndf/adapters/github.py` | `client`（`gate`・`within`）・`RetryWithin`（`gate.block`）・`GitHubError`（`secondary`・`hinted`・`reasons`）・`_translated`・`_translating`（`block`・`Busy` の写し）・`GitHub.graphql`・`GitHub.gate`（A5） |
| `plugins/ndf/src/ndf/domains/crossreview/threads.py` | `sync`（処理の流れの 1〜7）・`opening`・`replying`・`finding_of`・`Mark`・`restored`・`refreshed`・`position_refused`・`path_unresolved`・`patient`・`GitHubPort`（`open_review`・`changed`・`Placed`・`THREADS_QUERY`）。`_sync_one`・`Pace`・`GitHubPort._post` の間隔を消す |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | `occurrence` を書く（A1・A2）・`with_occurrences` |
| `plugins/ndf/src/ndf/domains/crossreview/prior_decide.py` | `Thread.replies` |
| `plugins/ndf/src/ndf/domains/crossreview/throttle_decide.py` | `wait_before_retry` を指数的に延ばす。`secondary_limited`・`spacing_wait`・`SPACING` を消す |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | `_github_hub`（`machine_gate`）・`Hub.publishing`・`_post_report`（A4） |
| `plugins/ndf/contracts/limits.json` | `github.write-spacing`・`github.content-spacing`・`github.write-send-lock`・`github.pr-publish-lock` を足す。`cross-review.secondary-rate-limit` を変える。`cross-review.thread-spacing` を消す |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md`（A8） | PR に残る記録の書き方と間隔の説明 |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 「書き込みの順番の記録」「内容を作る書き込み」「二次レート制限」 |
| テスト | `tests/adapters/test_write_gate.py`（新設）・`tests/adapters/test_github_limits.py`（新設。A3）・`tests/adapters/test_github.py`・`tests/domains/crossreview/conftest.py`・`test_crossreview_report.py`・`test_crossreview_throttle.py` |

## タスク分解

機能追加なので、各タスクは今のふるまいを縛るテストか、新しいふるまいの落ちるテストから始める。

### Task 1: 書き込みの順番をマシン全体で取る（WriteGate の本体）

- **対象ファイル:** `adapters/write_gate.py`・`contracts/limits.json`（`github.write-spacing`・`github.content-spacing`・`github.write-send-lock`・`github.pr-publish-lock`）・`tests/adapters/test_write_gate.py`・`docs/glossary/glossary.json`・`docs/glossary.md`
- **変更内容:** `Writes`・`write_of`（D1・D2）、`WriteGate` の `reserve`・`hold`・`acquire`・`async_acquire`・`sent`・`block`、`machine_gate`・`gate_path`・`pr_lock`。I5（記録を読めない・ロックを取れないときも止めない）と壁時計の戻り。テストは `gate_path` を monkeypatch で一時ディレクトリへ向け、時計と眠りを差し替える。`gate_path` の 2 つの子プロセスの試験は、子プロセスで `pwd.getpwuid` を差し替える
- **満たす受け入れ条件:** 3・4（I1・I2・I3・I5・I6）
- **進め方:** 失敗するテスト（詳細設計の「WriteGate」の単体テストの観点のうち `within` 以外）→ 通す最小実装 → 整理

### Task 2: 二次レート制限を 1 か所で見分け、マシン全体へ知らせる

- **対象ファイル:** `adapters/github_limits.py`・`adapters/github.py`・`tests/adapters/test_github_limits.py`・`tests/adapters/test_github.py`
- **変更内容:** `Limit`・`limit_of`・`reasons_of`。`GitHubError` の `secondary`・`hinted`・`reasons`。`RetryWithin` と `_translating` が `gate.block` を呼ぶ。`GitHub.graphql`（A6）・`GitHub.gate`（A5）・`artifact_zip`（A7）。`client(gate=...)` が `Client` の throttler に `WriteGate` を付け、`transport` を渡したときは付けない
- **満たす受け入れ条件:** 5 の見分けの部分（決定 7・8）
- **進め方:** 失敗するテスト（`limit_of`・`reasons_of`・`_translated`・`GitHub.graphql`・`RetryWithin` の観点）→ 通す最小実装 → 整理

### Task 3: 待ちの予算を持つ呼び手の書き込みを予算に収める

- **対象ファイル:** `adapters/write_gate.py`（`within`・`withdraw`・`Busy`・`reserve` の `remaining`・`replacing`）・`adapters/github.py`（`client(remaining=...)`・`_translating` の `Busy` の写し）・`tests/adapters/test_write_gate.py`・`tests/adapters/test_github.py`
- **変更内容:** 決定 10 の手順（どのロックの待ちも `remaining()` で縛り、予算の切れは I5 へ落とさず `Busy`。予約を書いた後なら取り消す）
- **満たす受け入れ条件:** 4（決定 10。予算を持つ呼び手でも間隔を破らない）
- **進め方:** 失敗するテスト（「`within`」「`within`（待ち直し）」の観点）→ 通す最小実装 → 整理

### Task 4: 打ち直しの待ちを指数的に延ばす

- **対象ファイル:** `domains/crossreview/throttle_decide.py`・`domains/crossreview/threads.py`（`patient`）・`contracts/limits.json`（`cross-review.secondary-rate-limit`）・`tests/domains/crossreview/test_crossreview_throttle.py`
- **変更内容:** `wait_before_retry(limited, attempt, retry_after, hinted)`、`patient` が `exc.secondary` で見分けて `block` へ知らせてから眠る。`secondary_limited` を消す
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト（60・120・240 秒の後に 4 回目で例外）→ 通す最小実装 → 整理

### Task 5: 指摘をコミットごとの 1 つのレビューで書き、閉じた理由を最初のコメントへ入れる

- **対象ファイル:** `domains/crossreview/threads.py`・`prior_decide.py`・`journal.py`・`cli.py`・`contracts/limits.json`（`cross-review.thread-spacing` を消す）・`throttle_decide.py`（`spacing_wait`・`SPACING` を消す）・`skills/cross-review/docs/02-fix-and-rotation.md`・`tests/domains/crossreview/conftest.py`・`test_crossreview_report.py`・`test_crossreview_throttle.py`
- **変更内容:** 処理の流れの 1〜7。`occurrence`（A1・A2・`with_occurrences`）、書く前の照合（`restored`・`refreshed`）、行の位置の 422 だけを 1 件ずつに落とす（`position_refused`・`path_unresolved`）、`open_review`・`changed`、同じ PR の公開のロック（A4）。偽物の更新（A9）と Skill の文書（A8）
- **満たす受け入れ条件:** 1・2・6（I4）
- **進め方:** 最初に、今の `sync` の「閉じた指摘ごとに作る・返信・resolve」を縛るテストを、受け入れ条件 2 の期待値（返信 0 回・本文に閉じた理由）へ書き換えて落とす → 通す最小実装 → 整理

## レビューの単位

準備の変更（`WriteGate` と `limits.json` の新しい方針）を含む単位 1 を最初にマージし、単位 2・3 はその後にスプリントブランチから切る。単位 3 は単位 2 の `GitHubError` の欄を使うので、単位 2 のマージの後に始める。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u1 書き込みの順番（WriteGate） | Task 1 | 4016:3・4016:4 | 約 650（本体 300・テスト 350） | 929（追加 926・削除 3。本体 392・テスト 504） |
| u2 二次レート制限の見分けと予算 | Task 2・3・4 | 4016:5 | 約 800（本体 350・テスト 450） | （実装の後に書き足す） |
| u3 指摘のまとめ書き | Task 5 | 4016:1・4016:2・4016:6 | 約 900（本体 400・テスト 500） | （実装の後に書き足す） |

行数は `git diff --shortstat <ベース>...HEAD` で数える。生成するファイルは無い。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルは 8 つ（本体）で、`github.py` 1,000 行の上限は A3 で新しいモジュールへ分けて避ける。`threads.py` は 422 行、`cli.py` は 648 行で上限に余裕がある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `write_gate.py` の `acquire` と `within` の `acquire` の手順の重複、`github_limits.limit_of` と `threads.position_refused`・`path_unresolved` の拒否の語の扱い | 単位 1・2 と単位 2・3 がそれぞれ同じ形の処理を持つ |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| テストが本物のホームの下（`~/.local/state/ndf-github/`）へ記録とロックのファイルを書く（`gate_path` は環境変数を読まない） | `client()` は `transport` を渡したときに `WriteGate` を付けない。`write_gate` のテストは `gate_path` を差し替え、cross-review のテストは `FakeHub` の `publishing` で何もしないロックを使う |
| リリースの経路（`release/` の POST・PATCH）が 1〜7.2 秒ずつ待つ | 設計の代償のとおり。件数は数えていないので、リリースの後の最初のリリースで所要時間を見る |
| 1 つのレビューに載せられるコメントの数の上限が分からない | 設計の「未確認のまま残ること」のとおり、リリースの後の最初の cross-review の終わりで確かめる |
| #4059 が同じ `write_gate.py` を触る | D1 で名前と返り値を合わせ、#4059 は件数の数え方と `gh` の経路だけを足す |

## 切り戻し手順

- 単位ごとの PR の revert で戻せる。データの移行は無い（状態ファイルの指摘に `occurrence` が増えるだけで、前の版はその欄を無視して読む）
- マシンの記録（`~/.local/state/ndf-github/write-gate.json`）は戻した後に残っても読まれない。消してよい

## 完了の定義

- [ ] 受け入れ条件 1〜6 をすべて満たし、条件ごとにテストの名前と CI の結果が対応している
- [ ] 単位 u1〜u3 の PR がスプリントブランチへマージされ、各 PR の `cross-review` が収束している
- [ ] CI の静的解析（ruff・mypy・NDF の lint・構造の検査）とテストが通っている（このリポジトリはテストを手元で走らせない）
