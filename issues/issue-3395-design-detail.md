# #3395 の詳細設計: 行き先の表の検査・閉じる操作・プロセスの組の停止・選択の記録

設計文書 [issue-3395-design.md](issue-3395-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。書き方は NDF の `design` Skill の `references/detailed-design.md` にある。

**この文書が下ろすのは、#3395 の設計が新しく決めた契約だけである。** 子ごとのほかの項目（行き先の表で子へ割り当てた項目）は、子の受け入れ条件と [issue-1142-design-review-routing.md](issue-1142-design-review-routing.md) が持つ。

| 契約 | 設計文書の節 | 作る子 | 版 |
| --- | --- | --- | --- |
| 規則 `review-items` と `review_items` の生成 | 「`review_items` の形」 | X1+ | 11.x（3395-2） |
| 種類 6 の規則とテストの数の予算 | 「構造の条件の食い違い」「テストと CI の予算」 | X1+ | 11.x（3395-4・3395-10） |
| 選択の記録の JSON Schema | 「目的の物差し」 | D1+ | 11.x（3395-1） |
| プロセスの組の停止の 1 本化 | 「既存の部品と使わない依存」 | T1+ | 11.0.0（3394-9） |
| CI の所要の予算 | 「テストと CI の予算」 | T1+ | 11.x（3395-10） |
| 閉じる操作 `ndf issues settle` | 「閉じる操作」 | M6 | 11.x（3395-6） |
| 今の形と新しい形の両方を読む測り方 | 「目的の物差し」 | M7 | 11.x（3395-1）。ただし `scripts/token-usage.py` の読み手を `scripts/measure/` へ移す分は、入れ替えで切れるため 11.0.0（S2 の 3341-1 と同じ理由） |
| filelock と tenacity への置き換え | 「既存の部品と使わない依存」 | X0+・AD3+・T1+ | 11.x（3395-9） |

版は行き先の表の版の列による（設計の決定 123）。11.x の契約も、この文書の関数の仕様のまま 11.x の版で作る。

この文書のパスは、断りの無い限り `plugins/ndf-next/` からの相対である。今の版の行番号は `origin/develop`（c5ab12e1e、2026-11-14）で読んだ。

## ファイル配置

省く: この設計の差分が触るのは `issues/` と `docs/glossary` の文書だけで、設計文書の「設計の結果」は #3395 を「閉じる」としている（受け入れ条件 20）。下の関数を置くファイルは子の PR が触り、置き場と作る子は「クラスと関数の構造」の表にある。

依存の向きは次のとおりで、輪を作らない。

- 検査（`checks/`）は契約（`contracts/`）と自分のパッケージだけを読み、アダプター（`adapters/`）を読まない。GitHub を読むのは副命令のエントリポイント `checks/review_items.py` の `main` だけで、規則の関数は読んだ本文を引数で受ける
- `process_group.py` はパッケージ直下に置き、`testing/` と `adapters/` の両方から読まれる。自分は `contracts.limits` だけを読む
- 閉じる操作（`issues/`）はアダプター（`adapters/github.py`・`adapters/git.py`）と契約を読む。検査とテスト基盤を読まない
- 測るスクリプト（リポジトリの根の `scripts/measure/`）は ndf-next のモジュールを import しない。新しい形は `bin/ndf` の副命令が出す JSON で受ける

## クラスと関数の構造

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/checks/review_items.py`（X1+。新設） | `checks.review_items` | `extract`・`text_sha`・`read_table`・`generate`・`check`・`main`（新設）。型 `Item`・`TableRow`（新設） |
| `src/ndf/checks/run.py`（X1+。変更） | `checks.run` | `main`（変更: 先頭の引数が `review-items` なら `review_items.main` へ渡す）・`check`（変更: `budget.check` と `smells.duplicate_bodies` を足す） |
| `src/ndf/checks/budget.py`（X1+。新設） | `checks.budget` | `count_tests`・`check`（新設） |
| `src/ndf/checks/smells.py`（X1+。変更） | `checks.smells` | `duplicate_bodies`（新設。規則 NDF601）・`scan`（変更: 規則 NDF602 の呼び出しの一覧を見る） |
| `src/ndf/checks/rules.json`（X1+。変更） | 規則のデータ | `smells` に NDF601・NDF602（種類 6）を足す |
| `src/ndf/checks/children.json`（X1+。変更） | 子の表のコピー | 子の行の `superseded_by`、`review_items` を足す |
| `.github/workflows/ndf-next.yml`（リポジトリの根。X1+。変更） | CI の定義 | `paths` は変えない（正本の表を足さない）。PR と push の実行では、検査のジョブに `bin/ndf check review-items --offline` を打つ手順を足す（GitHub を読まず、`children.json` だけで決まる規則 3・4・5 を当てる。`bin/ndf check` は副命令 `review-items` を通らないため、別の手順にする）。`on.schedule` に週 1 回（`cron: "0 0 * * 1"`）を足し、`schedule` の実行では `bin/ndf check review-items`（規則 1〜6）の手順だけを打つ。この手順だけに `permissions` の `issues: read` と `GH_TOKEN`（`github.token`）を渡す。`schedule` は既定ブランチ `main` の定義と中身で走るため、`schedule` の実行の `actions/checkout` に `ref: develop`（`.ndf/worktree.json` の `base_branch` の値）を渡し、オーガナイザーが生成し直す `develop` の表とコピーを見る。週 1 回の検査は、この変更が本番のリリースで `main` に届いてから走り始める |
| `contracts/limits.json`（X1+・T1+。変更） | 上限の表 | `tests.function-budget`（X1+）・`tests.ci-seconds-budget`・`procs.stop-test`（T1+）を足す |
| `contracts/schemas/route-chosen.schema.json`（D1+。新設） | 選択の記録の JSON Schema | — |
| `src/ndf/process_group.py`（T1+。新設） | `process_group` | `stop_group`・`alive`（新設） |
| `src/ndf/testing/slots.py`（T1+。変更） | `testing.slots` | `_stop_group`・`end_group`・`_end_group`・`_group_alive` を消し、`process_group.stop_group` を呼ぶ。`KILL_GRACE_SECONDS` を消す。`_try_lock` の待ちを tenacity へ（引数・戻り値は変えない） |
| `src/ndf/testing/waits.py`（T1+。変更） | `testing.waits` | `wait_until`（中のループを tenacity の `Retrying` へ。引数・戻り値・`WaitTimeout` は変えない） |
| `tests/conftest.py`（T1+。変更） | pytest の設定 | `pytest_sessionfinish`（新設） |
| `src/ndf/adapters/agents.py`（AD3+。変更） | `adapters.agents` | `stop` と `_group_alive` を消し、呼び出し元を `process_group.stop_group` へ呼び替える。200・263 行の待ちを tenacity へ |
| `src/ndf/adapters/llm.py`（AD3+。変更） | `adapters.llm` | 107 行の打ち直しのループを tenacity へ（回数と待ちの上限は `limits.json` の今の鍵のまま） |
| `src/ndf/adapters/run_log.py`（AD3+。変更） | `adapters.run_log` | `_append_line` のロックを filelock へ（引数・戻り値は変えない） |
| `src/ndf/identity.py`（X0+。変更） | `identity` | `prepare_state_dir` のロックを filelock へ（引数・戻り値・ロックのファイルの置き場は変えない） |
| `src/ndf/adapters/github.py`（M6。変更） | `adapters.github` | `GitHub.merges`（新設。`GitHub.nodes` で `pullRequest` の `number merged baseRefName mergedAt mergeCommit { oid }` を読む。共通の `PULL_FIELDS` と `Pull` は変えない）。型 `Merge`（新設） |
| `src/ndf/issues/settle.py`（M6。新設） | `issues.settle` | `load_input`・`verify`・`close`・`main`（新設）。型 `SettleRow`・`Verdict`（新設） |
| `src/ndf/issues/cli.py`（M6。新設か変更） | `issues.cli` | `main`（副命令 `settle` を `settle.main` へ渡す。M6 のほかの副命令と同じ表に置く） |
| `src/ndf/cli.py`（M6。変更） | `cli` | `COMMANDS` に `"issues": "ndf.issues.cli"` を足す（M6 が先に足していれば変えない） |
| `scripts/measure/ai-branching.py`（リポジトリの根。M7。変更） | 測るスクリプト | `count_choices`（新設）・`main`（変更: `--choices <JSON>` を受ける） |
| `scripts/measure/usage_ledger.py`（リポジトリの根。M7。新設） | 帳簿の読み手 | `plugins/ndf/scripts/lib/usage_ledger.py` の読み取りの関数（`read_usage_ledger`・`cache_writes`・`ledger_dir`・`repo_key`）を移す |
| `scripts/token-usage.py`（リポジトリの根。M7。変更） | 使用量の集計 | `usage_ledger` を `scripts/measure/` から読む（`plugins/ndf/scripts/lib` を import の道から外す） |

`scripts/measure/ai-branching.py` は今も `plugins/ndf` を import しない。`grep -nE "plugins/ndf|scripts/lib|sys\.path" scripts/measure/*.py` の結果は `claude-p-usage.py` の 44 行だけで、`token-usage.py` を読み込む行である。入れ替えで切れるのは `scripts/token-usage.py` の 43・50 行（`plugins/ndf/scripts/lib/usage_ledger.py` を読む）で、移すのはこの読み手だけである。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `review_items.extract` | `src/ndf/checks/review_items.py` | 新設 | `extract(number: int, body: str, quoted: bool) -> list[Item]` | 1 本の Issue の本文から項目を抜き出す | `body` は GitHub から読んだ本文そのまま | 返す並びは本文の上からの順で、`Item.key` は `<number>-<連番>`。連番は取り消し線の無いチェックボックスの行だけを数える。この鍵は本文の順の仮の鍵で、表の鍵は `generate` が `text_sha` の一致で決める（取り消し線を引くと後ろの仮の鍵は 1 つずつ前へずれる）。`quoted` が偽なら `## 進行` より前の引用でない行、真なら引用 `> ` の中の行を数える | 無し |
| `review_items.text_sha` | 同上 | 新設 | `text_sha(line: str) -> str` | 項目の同一性の値を作る | — | チェックボックスの記号（`- [ ] `・`- [x] `）と行頭の空白・引用の印を除いた文の UTF-8 の sha256 の 16 進の先頭 12 文字を返す | 無し |
| `review_items.read_table` | 同上 | 新設 | `read_table(text: str) -> dict[str, TableRow]` | 正本の表の文書から行き先の表の行を読む | `text` は正本の表の文書の本文 | 節「行き先の表」の行ごとに、鍵が項目の番号、値が 6 列（要旨・行き先・順番・新設か追補か・要求の行）の `TableRow`。行き先は `・` で分けた並び、`済み（PR <番号>）` は `済み` に寄せる。順番は先頭の数を読み、直後の全角の括弧の注記（`2（比較の結論まで着手しない）`）は読み捨てる | 無し |
| `review_items.generate` | 同上 | 新設 | `generate(rows: dict[str, TableRow], extracted: dict[int, list[Item]], previous: dict[str, dict] \| None) -> dict[str, dict]` | 正本の表と抜き出しから `review_items.items` を作る | `previous` の `text_sha` の値は互いに重ならない。`rows` の鍵は、下の手順で抜き出しの項目へ割り当てた鍵のどれかである（割り当てに無い鍵と、鍵の割り当たらない項目は、呼ぶ側が規則 1・2 の指摘にする） | 抜き出しの項目ごとに鍵を決める。`text_sha` が `previous` のどれかの値と一致すればその鍵を引き継ぎ、一致しなければ、同じ Issue で `previous` の鍵と既に振った鍵の連番の最大 + 1 を振る（`previous` が `None` なら `Item.key` をそのまま使う）。返す辞書の鍵は `rows` の鍵と同じで、`text_sha` は抜き出しの値。`to`・`order`・`kind`・`req` は表の列から作る（`順番` の `—` は `null`、要求の行の `無し` は `"none"`） | 無し |
| `review_items.check` | 同上 | 新設 | `check(copy: dict, children: dict[str, dict], extracted: dict[int, list[Item]] \| None, rows: dict[str, TableRow] \| None) -> list[Finding]` | 設計文書の規則 1〜6 を当てる | `copy` は `children.json` の `review_items`、`children` は同じファイルの `children`。`extracted` と `rows` は両方そろうか両方 `None`。`None` なら GitHub の本文と正本の表を要る規則 1・2・6 を当てない | 返す指摘は規則ごとに 1 項目 1 件で、`Finding.rule` は `review-items`、`detail` の先頭に規則の番号を置く。指摘が無ければ空 | 無し |
| `review_items.main` | 同上 | 新設 | `main(argv: list[str]) -> int` | 副命令 `ndf check review-items [--offline \| --write]` を動かす | `bin/ndf` から呼ばれる。`--offline` でなければ GitHub のトークンが環境にある | `--offline` なら GitHub と正本の表を読まず、`check` に `extracted` と `rows` を `None` で渡して規則 3・4・5 だけを当てる。`--write` が無ければ結果 JSON を 1 行出し、指摘があれば終了コード 1。`--write` があれば `children.json` の `review_items.items` を `generate` の結果で置き換えて書く | `--offline` でなければ GitHub の GraphQL で `sources` の本文を読む。`--write` のとき `children.json` を書く |
| `budget.count_tests` | `src/ndf/checks/budget.py` | 新設 | `count_tests(sources: list[Source]) -> int` | テストの関数の数を数える | `sources` は `tests/` の下の `.py` | `def test_`・`async def test_` で始まる関数の定義の数（入れ子の関数とクラスのメソッドを含む）。数え方は設計文書の「テストと CI の予算」の `grep` と同じ数になる | `Source.tree` がファイルを読む |
| `budget.check` | 同上 | 新設 | `check(scope: Scope, limit: int) -> list[Finding]` | テストの数が予算を超えたら指摘を返す | `limit` は `limits.json` の `tests.function-budget` の `count` | 数が `limit` 以下なら空。超えたら規則 `test-budget` の指摘 1 件（`path` は `tests`、`detail` に数と上限） | `count_tests` を通してファイルを読む |
| `smells.duplicate_bodies` | `src/ndf/checks/smells.py` | 新設 | `duplicate_bodies(sources: list[Source], params: dict) -> list[Finding]` | 本体の同じ関数が 2 か所以上にあれば指摘を返す（種類 6） | `params` は `rules.json` の NDF601 の `params`（`min_lines`） | 本体が `min_lines` 行以上の関数のうち、名前・docstring・位置を除いた構文木の `ast.dump` が一致する組を、2 か所目以降の定義ごとに 1 件の指摘にする。テストのファイルは見ない | `Source.tree` がファイルを読む |
| `process_group.stop_group` | `src/ndf/process_group.py` | 新設 | `stop_group(proc: subprocess.Popen, *, grace_key: str = "procs.stop-tree", send_first: bool = True) -> None` | `start_new_session` で起こしたプロセスの組を、SIGTERM から猶予の後の SIGKILL まで 1 つの手順で止める | `proc` は起動済み。`grace_key` は `limits.json` にある | 戻ったとき、組の成員は残っていないか SIGKILL を送り終えている。組が自分と同じなら組でなく `proc` へだけ送る。`send_first` が偽なら SIGTERM を送らずに猶予から始める（シグナルを転送した後に呼ぶ形） | シグナルを送る |
| `process_group.alive` | 同上 | 新設 | `alive(proc: subprocess.Popen, pgid: int) -> bool` | 組に生きた成員が残るかを返す | — | 先に `proc.poll()` で直接の子を回収する。`ProcessLookupError` は偽、`PermissionError` は真（送る権限の無い成員が残っている） | シグナル 0 を送る |
| `pytest_sessionfinish` | `tests/conftest.py` | 新設 | `pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None` | CI の上でテストの総所要が予算を超えたら失敗にする | 環境の `CI` が `true`。`--collect-only` でない | 総所要が `tests.ci-seconds-budget` の `seconds` 以下なら何もしない。超えたら `session.exitstatus` を 1 にし、所要と予算を 1 行出す | 標準エラーへ 1 行 |
| `settle.load_input` | `src/ndf/issues/settle.py` | 新設 | `load_input(path: Path) -> list[SettleRow]` | 入力の JSON を読み、形を確かめる | — | 返す行は入力の順。`evidence.kind` は `improved`・`removed`・`not_reproduced` のどれかで、種類ごとの必須の鍵を持つ | ファイルを読む |
| `GitHub.merges` | `src/ndf/adapters/github.py` | 新設 | `merges(self, numbers: Iterable[int]) -> dict[int, Optional[Merge]]` | 閉じる操作が確かめる PR のマージの事実を読む | — | 鍵は渡した番号。値は `Merge(number: int, merged: bool, base_ref: str, merge_oid: str, merged_at: str)` で、未マージなら `merge_oid` と `merged_at` は空文字。PR が無ければ `None` | GitHub の GraphQL（100 件ごとのまとめ） |
| `settle.verify` | `src/ndf/issues/settle.py` | 新設 | `verify(row: SettleRow, gh: GitHub, git: Git, base: str) -> Verdict` | 1 行の証拠を確かめる | `git` は `origin/<base>` を取得済み | `Verdict.ok` が真なのは種類ごとの確かめ方（「処理の手順」）がすべて通ったときだけ。偽なら `Verdict.reason` に通らなかった条件を 1 つ書く | GitHub の GraphQL と `git` を読む |
| `settle.close` | 同上 | 新設 | `close(row: SettleRow, verdict: Verdict, gh: GitHub, repo: str) -> str` | 証拠をコメントに書いてから Issue を閉じる | `verdict.ok` が真 | 戻り値は `closed`・`already_closed` のどちらか。閉じた Issue には印 `<!-- ndf-settle -->` を持つコメントが 1 件だけある | GitHub の REST でコメントを書き、Issue を閉じる |
| `settle.main` | 同上 | 新設 | `main(argv: list[str]) -> int` | 副命令 `ndf issues settle --file <JSON> --base <ブランチ>` を動かす | `bin/ndf` から呼ばれる | 全行が閉じたか既に閉じていれば `ok`。確かめられない行が 1 つでもあれば、その行を閉じずに `items` へ理由を載せて `stopped`（止まった理由 `incomplete`） | 上の 3 つの副作用と、結果 JSON を標準出力へ 1 行 |
| `count_choices` | `scripts/measure/ai-branching.py` | 新設 | `count_choices(records: list[dict], since: str) -> dict[str, Counter]` | 新しい形の選択の記録を規則の番号と選んだ主体ごとに数える | `records` は `bin/ndf` の副命令が出した JSON の `items` | 返す辞書の鍵は `rule`、値は `chooser`（`script`・`conductor`・`human`）ごとの回数。`event` が `route_chosen` でない行と `at` が `since` より前の行は数えない | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `review_items.read_table` | 表の行の列が 6 でない・順番が数（全角の括弧の注記を添えてよい）でも `—` でもない | 正本の表の書き損じ | 行を読むとき | 止める（`CheckError`） | 呼び出し元の `main` が `stopped`・止まった理由 `config_broken`、終了コード 2 | 書き込まない。表を直して打ち直す |
| `review_items.main` | GitHub の本文を読めない | ネットワーク・API の上限・トークンが無い | `GitHub.nodes` の `GitHubError` | 止める（設計文書の「抜き出しは GitHub を読む」） | `stopped`・止まった理由 `infra_wait`、終了コード 75。トークンが無いときは `precondition`、終了コード 3 | 書き込まない。`infra_wait` は `limits.json` の `cause.infra-wait` の打ち直しに乗る |
| `review_items.main` | `children.json` が読めない・`review_items` の鍵が欠ける | ファイルの書き損じ | 読み込みのとき | 止める | `stopped`・`config_broken`、終了コード 2 | 書き込まない |
| `review_items.main` | `sources` の番号の Issue が無い | 番号の書き損じ・Issue の削除 | `GitHub.nodes` が空の辞書を返す | 規則 1 の指摘にせず止める（無い Issue の項目を「表に無い」と数えない） | `stopped`・`config_broken`、終了コード 2。`detail` に番号 | 書き込まない |
| `review_items.main` | `--write` の書き込みに失敗する | ディスク・権限 | 書き込みのとき | 止める | `stopped`・`unknown`、終了コード 1 | 一時ファイルへ書いてから置き換えるので、元のファイルは残る。打ち直せる |
| `budget.count_tests` | テストのファイルが構文の誤りで読めない | 書きかけのテスト | `Source.tree` の `SyntaxError` | 数えない（検査の `unparsable` が同じファイルを `syntax` の違反にする） | `ndf check` の指摘 `syntax`、終了コード 1 | 直して打ち直す |
| `budget.check` | 予算の鍵が `limits.json` に無い | 鍵の書き損じ | `limits` の読み取り | 止める | `stopped`・`config_broken`、終了コード 2 | 書き込まない |
| `smells.duplicate_bodies` | ファイルが構文の誤りで読めない | 書きかけのコード | `Source.tree` の `SyntaxError` | そのファイルを飛ばす（`unparsable` が指摘する） | 指摘 `syntax`、終了コード 1 | 直して打ち直す |
| `process_group.stop_group` | 組が既に無い | 子が先に終わった | `os.getpgid`・`os.killpg` の `ProcessLookupError` | 何もせず戻る | 無し | 何度呼んでもよい |
| `process_group.stop_group` | SIGKILL の後も直接の子を回収できない | カーネルの待ち（D 状態） | `proc.wait` の `TimeoutExpired` | 待たずに戻る（今の `agents.stop` の扱いを引き継ぐ） | 無し | 呼んだ側の次の `poll` で回収される |
| `process_group.stop_group` | 猶予の鍵が `limits.json` に無い | 鍵の書き損じ | `limits.seconds` | 呼び出し元へ返す（`ContractError`） | 呼んだ副命令が `config_broken`、終了コード 2 | 鍵を直して打ち直す |
| `process_group.alive` | 送る権限の無い成員が残る | 別の利用者の権限で動く子孫 | `os.killpg(pgid, 0)` の `PermissionError` | 生きているとみなす | 無し | `stop_group` が SIGKILL を送る（送れなければ猶予の後に戻る） |
| `pytest_sessionfinish` | 予算の鍵が無い | 鍵の書き損じ | `limits.seconds` | テストの結果を失敗にする | `session.exitstatus` を 1 にし、鍵の名前を 1 行出す | 鍵を直して打ち直す |
| `settle.load_input` | 読めない・JSON でない・必須の鍵が欠ける・種類が知らない値 | 入力の書き損じ | 読み込みと形の確かめ | 止める（1 行も閉じない） | `stopped`・`invalid`、終了コード 2。`items` に行の位置と欠けた鍵 | 入力を直して打ち直す |
| `GitHub.merges` | GitHub を読めない | ネットワーク・API の上限・認証が無い・GraphQL の誤り | `GitHub.nodes`（`GitHub.aliased`・`GitHub.graphql`）が投げる `GitHubError` | 捕まえずに呼び出し元へ返す（`GitHub.pulls` と同じ） | 呼び出し元の `settle.verify` がその行を確かめられない行にする | 何も書かない。打ち直せる |
| `GitHub.merges` | 番号が PR でない・無い | 入力の番号の書き損じ | `GitHub.nodes` が空の辞書を返す | その番号の値を `None` にする（例外にしない） | `settle.verify` が「PR が未マージ」の条件で通らない行にする | 何も書かない。入力を直して打ち直す |
| `settle.verify` | GitHub か git を読めない | ネットワーク・API の上限・`origin/<base>` が無い | `GitHubError`・`GitError` | その行を確かめられない行にする | その行を `items` へ理由とともに載せ、結果は `stopped`・`incomplete`、終了コード 6 | 閉じない。打ち直せる |
| `settle.verify` | 証拠が通らない | テストが基底のブランチに無い・PR が未マージ・PR がテストの関数を足していない・パスが残る・記録が無い | 「処理の手順」の判定 | その行を閉じない | 同上。`reason` に通らなかった条件 | 閉じない。証拠を直して打ち直す |
| `settle.close` | コメントは書けたが閉じられない | ネットワーク・権限 | REST の `GitHubError` | その行を閉じられなかった行にする | 同上 | 打ち直すと、印のあるコメントを見つけて書き直さずに閉じるだけを行う |
| `settle.close` | 権限が無い | トークンに Issue の書き込みが無い | REST の 403 | 止める（残りの行も閉じない） | `stopped`・`precondition`、終了コード 3 | 権限を直して打ち直す |
| `settle.main` | `--file`・`--base` が無い | 呼び出しの誤り | 引数の解析 | 止める（1 行も確かめない） | `stopped`・`invalid`、終了コード 2 | 引数を直して打ち直す |
| `settle.main` | `origin/<base>` を取得できない | ネットワーク・ブランチが無い | 最初の `git fetch origin <base>` の `GitError` | 止める（1 行も確かめない） | `stopped`・`infra_wait`、終了コード 75。ブランチが無いときは `precondition`、終了コード 3 | 何も書かない。打ち直せる |
| `count_choices` | 行の `rule`・`chooser` が欠ける | 記録の書き手の誤り | 行を読むとき | その行を数えず、`warnings` に数を載せる | 結果に `warnings`。終了コードは 0 | 何度打ってもよい |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `review_items.extract` | 行がチェックボックスで、取り消し線 `~~` で始まらず、`quoted` が偽で引用でなく、`## 進行` より前 | 数える |
| `review_items.extract` | 行がチェックボックスで、取り消し線で始まる | 数えない（連番を進めない） |
| `review_items.extract` | `quoted` が真で、行が `> - [ ]` か `> - [x]` で始まる | 数える（`## 進行` の位置は見ない） |
| `review_items.extract` | コードブロックの中のチェックボックス | 数えない |
| `review_items.check` | 抜き出しの `text_sha` が `items` の値に無い | 規則 1 |
| `review_items.check` | `items` の `text_sha` が抜き出しに無い | 規則 2 |
| `review_items.check` | `to` の値が予約の値（`#3388`・`#3395 の設計`・`済み`）でなく、`children` に無い | 規則 3 |
| `review_items.check` | `to` の値が `superseded_by` を持つ子 | 規則 3 |
| `review_items.check` | `to` の値が `children` にあり、`superseded_by` を持たない（作業を終えて閉じた子を含む） | 指摘しない |
| `review_items.check` | `kind` が `追補` で、`to` の先頭が `+` で終わらない | 規則 4 |
| `review_items.check` | `req` が整数でも `"none"` でもない | 規則 5 |
| `review_items.check` | `generate(rows, extracted, copy["items"])` の結果が `copy["items"]` と違う | 規則 6（違う鍵ごとに 1 件） |
| `settle.verify` | `improved`: PR がマージ済みで、宛先が `base` で、マージのコミット（`GitHub.merges` の `merge_oid`）の変更ファイル（`git diff --name-only <merge>^1 <merge>`）にテストのノードのファイルがあり、同じ差分（`git diff <merge>^1 <merge> -- <ファイル>`）の足した行に `def <テストの関数名>` があり、そのファイルが `origin/<base>` にあって `def <テストの関数名>` を持つ | `ok` |
| `settle.verify` | `improved`: 上のどれかが通らない | `ok` でない。`reason` は最初に通らなかった条件 |
| `settle.verify` | `removed`: パスが `origin/<base>` に無く（`git cat-file -e` が失敗する）、`commit` が `origin/<base>` の祖先 | `ok` |
| `settle.verify` | `not_reproduced`: `exit` が 0、`record` のファイルが `origin/<base>` にあり、`at` が `after_pr` のマージの日時（`GitHub.merges` の `merged_at`）より後で、今より前 | `ok` |
| `settle.close` | Issue が既に閉じている | `already_closed`。コメントを書かない |
| `settle.close` | Issue が開いていて、印のあるコメントが既にある | コメントを書かずに閉じる |
| `settle.close` | Issue が開いていて、印のあるコメントが無い | コメントを書いてから閉じる |

- 繰り返しの終わる条件: `extract` は本文の行を、`check` は抜き出しの項目と `items` の鍵を、`settle.main` は入力の行を 1 回ずつ読み終えたとき。`stop_group` は組が終わるか猶予が尽きたとき
- 計算量の注意: `review_items.main` は `sources` の 24 本を `GitHub.nodes` の 1 回のクエリで読む（100 件ごとにまとめる既存の上限 `github.graphql-batch`）。`duplicate_bodies` は関数ごとの `ast.dump` を辞書で引くので、関数の数に比例する。テストのノードの確かめは `git show` を行ごとに 1 回打つ（入力は 10 行）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Item` | 型（`NamedTuple`） | `src/ndf/checks/review_items.py` | `key: str`・`text_sha: str`・`line: int`（本文の行番号） |
| `TableRow` | 型（`NamedTuple`） | 同上 | `to: tuple[str, ...]`・`order: int \| None`・`kind: str`・`req: int \| str` |
| `RESERVED` | 定数 | 同上 | `("#3388", "#3395 の設計", "済み")`。表記の正本は正本の表の「列の読み方」で、コードではここだけが持つ |
| `KINDS` | 定数 | 同上 | `("新設", "追補", "既存", "#3395 の設計", "済み", "範囲の外")` |
| `SHA_LENGTH` | 定数 | 同上 | `12` |
| `review_items.sources`・`quoted`・`table` | 設定値 | `src/ndf/checks/children.json` | 設計文書の「`review_items` の形」のとおり。抜き出す Issue の番号はここだけが持つ |
| `superseded_by` | 設定値 | 同上（子の行） | 追補の単位へ置き換えた子（D1・D2・T1・X0・X1・C1・AD1・AD2・AD3）と分けた子（M3・S1）だけが持つ。C1 は `["C1+", "C2"]` |
| `tests.function-budget` | 設定値 | `contracts/limits.json` | `{"count": 4348}`（設計文書の「テストと CI の予算」） |
| `tests.ci-seconds-budget` | 設定値 | 同上 | `{"seconds": 465}`（同上） |
| `procs.stop-tree` | 設定値 | 同上（既存） | `{"seconds": 3, "interval": 0.1}`。外部の CLI の組を止める猶予 |
| `procs.stop-test` | 設定値 | 同上 | `{"seconds": 10, "interval": 0.1}`。テストの組を止める猶予。今の `testing/slots.py` の `KILL_GRACE_SECONDS = 10.0` を移し、コードの定数を消す |
| NDF601 | 規則のデータ | `src/ndf/checks/rules.json` | `{"kind": 6, "mark": "none", "summary": "本体の同じ関数が 2 か所にある", "params": {"min_lines": 6}}` |
| NDF602 | 規則のデータ | 同上 | `{"kind": 6, "mark": "none", "summary": "lock にある部品の再実装", "params": {"calls": {"fcntl.flock": "filelock"}}}` |
| 選択の記録 | 型（JSON Schema） | `contracts/schemas/route-chosen.schema.json` | 必須の鍵 `event`（`route_chosen` 固定）・`at`（ISO 8601）・`run_id`・`rule`（`R` と数）・`chooser`（`script`・`conductor`・`human`）・`from`・`to`。任意の鍵 `cause`（`contracts/causes.json` の `kind`）。追加の鍵を許さない |
| `SettleRow` | 型（`NamedTuple`） | `src/ndf/issues/settle.py` | `issue: int`・`child: str`・`evidence: dict` |
| `Verdict` | 型（`NamedTuple`） | 同上 | `ok: bool`・`reason: str` |
| `MARK` | 定数 | 同上 | `"<!-- ndf-settle -->"` |
| 入力の形 | 型（JSON Schema） | `contracts/schemas/settle.schema.json`（M6。新設） | 設計文書の「閉じる操作」の入力。種類ごとの必須の鍵: `improved` は `pr`・`test`、`removed` は `path`・`commit`、`not_reproduced` は `command`・`exit`・`record`・`at`・`after_pr` |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `ndf check review-items --write` を打つオーガナイザーと、`children.json` を変える子の PR | `src/ndf/checks/children.json` | 排他を取らない。書き手はオーガナイザーだけで（設計文書の「表とコピーを直す時と書き手」）、子の PR の書き換えは週 1 回の検査の規則 6 が落とす（PR の CI は規則 3・4・5 だけを当てる） | 同じ表と本文から同じ `items` を作る（鍵は `text_sha` の一致で既にある鍵を引き継ぐ）。途中で止まっても元のファイルは残る |
| 週 1 回の検査と、本文に取り消し線を引くオーガナイザー | 入力の Issue の本文 | 排他を取らない | 引いた直後の検査は規則 2 で落ちる。生成し直した後の検査は通る（設計文書のとおり、残ったずれは週 1 回の検査が落とす） |
| `ndf issues settle` の 2 回の実行 | 解消する Issue のコメントと状態 | 印 `<!-- ndf-settle -->` のコメントを読んでから書く | 閉じた Issue は `already_closed` で飛ばす。コメントの後で止まっても、次の実行は閉じるだけを行う |
| テストの枠の停止と外部の CLI の停止 | プロセスの組 | 組ごとに 1 つの `stop_group` が送る。同じ組を 2 か所から止めても、2 回目は `ProcessLookupError` で戻る | 何度呼んでもよい |
| 状態の置き場を用意する 2 つの処理（X0+） | `identity.lock_path` のロックのファイル | filelock の `FileLock` で待つ（今の `fcntl.flock` の `LOCK_EX` と同じ、待ちの上限なし） | `_prepare_locked` は今のまま冪等 |
| 実行の記録へ書く並行の子（AD3+） | 記録のファイル | filelock の `FileLock` をファイルの隣の `.lock` で取る | 1 行を書き切る今の振る舞いのまま |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `review_items.main` | 規則ごとの指摘の数・抜き出した項目の数・`--write` で書き換えた鍵の数 | 結果 JSON を標準出力へ 1 行。指摘は 1 件 1 行で標準エラーへ（`ndf check` と同じ） | Issue の本文の全文（項目の番号と `text_sha` だけ）。トークン |
| `budget.check` | テストの関数の数と予算 | `ndf check` の結果 JSON の `metrics.tests` | 無し |
| `process_group.stop_group` | SIGKILL を送ったこと | ログの `warning` | 無し |
| `pytest_sessionfinish` | 総所要と予算 | 標準エラーへ 1 行 | 無し |
| `settle.main` | 行ごとの `closed`・`already_closed`・通らなかった理由 | 結果 JSON の `items` を標準出力へ 1 行 | トークン |
| `settle.close` | 証拠の種類と中身（PR の番号・テストのノード・パス・コマンド・終了コード・記録のパス・日時） | 閉じる Issue のコメント | 記録のファイルの中身（パスだけ） |
| `count_choices` | 規則と主体ごとの回数、数えなかった行の数 | 測るスクリプトの出力（今の形と同じ表） | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `review_items.extract` | 正本の表の文書の抜き出しのコマンド・`adapters/markdown.py` の `code_lines` | [issue-1142-design-review-routing.md](issue-1142-design-review-routing.md) の「抜き出しのコマンドと突き合わせ」・`git show origin/develop:plugins/ndf-next/src/ndf/adapters/markdown.py` | 規則は抜き出しのコマンドと同じにする。`code_lines` は検査（`checks/`）からアダプターを読む向きになるため使わず、フェンスの開閉だけを数える |
| `review_items.text_sha` | `hashlib.sha256` | 標準ライブラリ | 使う |
| `review_items.read_table` | `adapters/markdown.py`（markdown-it-py） | 同上 | 使えない。検査からアダプターを読む向きになる。表の行は先頭が `\| <数 4 桁>-<数> \|` の行だけで、正本の表の抜き出しのコマンドの `grep` と同じ形で読む |
| `review_items.generate` | `checks/child_deps.py` の `load_children`（X1 の #3347 の分） | [issue-3347-design-detail.md](issue-3347-design-detail.md) の「関数の仕様」 | 使う。`children.json` の読み込みを作り直さない |
| `review_items.check` | `checks/model.py` の `Finding`・`CheckError` | `git show origin/develop:plugins/ndf-next/src/ndf/checks/model.py` | 使う |
| `review_items.main` | `adapters/github.py` の `GitHub.nodes`（`issue` の節点・100 件ごとのまとめ） | `git show origin/develop:plugins/ndf-next/src/ndf/adapters/github.py` の 263 行 | 使う |
| `budget.count_tests` | `checks/model.py` の `Source.tree` | 同上 | 使う。構文木を作り直さない |
| `budget.check` | `contracts/limits.py` | `git show origin/develop:plugins/ndf-next/src/ndf/contracts/limits.py` | 使う |
| `smells.duplicate_bodies` | ruff 0.16.9 の規則・pylint の `duplicate-code`（R0801） | `ruff rule --all --output-format json` で名前に `duplicate` を含む規則を引いた（10 件。関数の本体の重複を見る規則は無い）。`grep '^name = "pylint"' plugins/ndf-next/uv.lock` は 0 件 | 使えない。ruff は持たず、pylint は lock に無い。依存を足すより `Source.tree` の `ast.dump` で足りる |
| `process_group.stop_group` | `testing/slots.py` の `_end_group`・`adapters/agents.py` の `stop` | `git show origin/develop:…/testing/slots.py` の 254〜306 行・`…/adapters/agents.py` の 177〜209 行 | 2 つを 1 つにまとめる（設計文書の 3394-9）。自分の組への送り方と「先に送るか」は `_end_group`、送った後の `proc.wait` は `stop` から引き継ぐ |
| `process_group.alive` | 同上の `_group_alive`（2 つ） | 同上 | まとめる。`PermissionError` を生きているとみなす `slots.py` の扱いに揃える |
| `pytest_sessionfinish` | pytest の hook `pytest_sessionfinish`・pytest-xdist | pytest の hook の一覧・`plugins/ndf-next/uv.lock` | 使う。xdist の子の側では総所要を持たないため、`session.config.workerinput` がある側では何もしない |
| `settle.load_input` | `contracts` の `require_schema` | `git show origin/develop:plugins/ndf-next/src/ndf/contracts/__init__.py` の 126 行 | 使う。形を手で確かめない |
| `GitHub.merges` | `GitHub.pulls`・`GitHub.nodes`（`pullRequest` の節点・100 件ごとのまとめ） | `…/adapters/github.py` の 263・271 行 | `GitHub.nodes` を使い、`pullRequest` の節点から `number merged baseRefName mergeCommit { oid } mergedAt` を読む。`GitHub.pulls` は使えない（取得の項目 `PULL_FIELDS` と `Pull` にマージのコミットとマージの日時が無い）。`PULL_FIELDS` と `Pull` へ項目を足すと既存の呼び出し元すべての取得が増えるため、足さずに別の関数にする |
| `settle.verify` | `GitHub.pulls`・`GitHub.nodes`・`Git.run`・`Git.is_ancestor` | `…/adapters/github.py` の 32・263・271 行・`…/adapters/git.py` の `Git` | `GitHub.pulls` は使えない。共通の取得の項目 `PULL_FIELDS`（`number state merged headRefOid headRefName baseRefName body mergeStateStatus`）はマージのコミットとマージの日時を取らず、`Pull` にも項目が無い。`GitHub.nodes` の上に `GitHub.merges` を足して使う（「クラスと関数の構造」の `adapters/github.py` の行）。変更ファイルは GitHub の `files`（ページ送りの接続）を読まず、マージのコミットの差分 `git diff --name-only <merge>^1 <merge>` で求める。`Git.run`・`Git.is_ancestor` は使う |
| `settle.close` | `GitHub.rest`・`GitHub.nodes`（`issue` の `state` とコメント） | `…/adapters/github.py` の 231・263 行 | 使う |
| `settle.main` | `contracts/result.py` の `result`・`stopped`・`emit` | `git show origin/develop:plugins/ndf-next/src/ndf/contracts/result.py` | 使う |
| `count_choices` | `collections.Counter`・今の形の `count_progress` | `scripts/measure/ai-branching.py` の 111 行 | `Counter` を使う。`count_progress` は今の形の記録の読み手で、新しい形の行を読まない |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_items.extract` | チェックボックス 3 行の本文から `<番号>-1`〜`-3` | 取り消し線の行を挟むと連番が飛ばない。`## 進行` の後の行は数えない。`quoted` が真なら引用の中だけを数える。コードブロックの中は数えない | — |
| `review_items.text_sha` | 同じ文で同じ値 | `- [ ]` と `- [x]` の違い・行頭の空白・引用の印では値が変わらない | — |
| `review_items.read_table` | 正本の表の 182 行を読む | `済み（PR 3360）` が `済み` に寄る。順番の `—` が `None`。`2（比較の結論まで着手しない）` が `2` | 列が 5 の行・順番が `二` の行で `CheckError` |
| `review_items.generate` | 表と抜き出しから `items` を作る | 本文の途中の項目に取り消し線を引き、表からその行を消すと、後ろの項目は `previous` の鍵を引き継ぎ、規則 1・2・6 の指摘が 0 件。本文の末尾に足した項目は、その Issue の既存の最大の連番 + 1 の鍵になる | — |
| `review_items.check` | 生成した直後のコピーで指摘 0 件 | 閉じた子（`superseded_by` なし）と予約の値 3 つを指す行では落ちない | 規則 1〜6 をそれぞれ 1 つずつ壊した入力で、その規則の指摘だけが出る（設計文書の「テスト設計」の 3〜5） |
| `review_items.main` | 偽の GitHub で `ok`、`--write` で `children.json` が生成の結果になる。`--offline` は GitHub を 1 回も呼ばずに規則 3・4・5 だけを返す | 指摘があると終了コード 1 | 本文を読めないと `infra_wait`・75。無い Issue で `config_broken`・2。表の書き損じで `config_broken`・2。書き込みの失敗で元のファイルが残る |
| `budget.count_tests` | 関数とメソッドと `async def` を数える | 入れ子の関数を数える。`test` で始まらない関数を数えない | 構文の誤りのファイルは数えず、`syntax` の違反が出る |
| `budget.check` | 予算ちょうどで指摘なし | 予算 + 1 で指摘 1 件 | 鍵が無いと `config_broken` |
| `smells.duplicate_bodies` | 本体の同じ 2 関数で 1 件 | `min_lines` 未満の関数は数えない。名前と docstring だけが違う関数は重複とみなす。テストのファイルは見ない | 構文の誤りのファイルを飛ばす |
| `process_group.stop_group` | SIGTERM で終わる組は SIGKILL を送らない | SIGTERM を無視する子孫が残る組は猶予の後に SIGKILL で終わる。自分と同じ組では `proc` にだけ送る。`send_first` が偽なら SIGTERM を送らない | 既に終わった組で例外を出さない。鍵が無いと `ContractError` |
| `process_group.alive` | 生きた組で真 | 先頭が終わり子孫が残る組で真 | 成員の無い組で偽。`PermissionError` で真 |
| `pytest_sessionfinish` | 予算以下で終了の状態を変えない | `CI` が無いと何もしない。xdist の子では何もしない | 予算を超えると 1。鍵が無いと 1 |
| `settle.load_input` | 3 種類の行を読む | 空の `settle` は 0 行 | 鍵の欠け・知らない種類で `invalid`・2、1 行も閉じない |
| `settle.verify` | 3 種類のそれぞれが通る | `not_reproduced` の `at` が `after_pr` のマージの日時と同じなら通らない | テストが基底のブランチに無い・PR が未マージ・PR の差分がテストの関数を足していない（既存のテストを指す）・パスが残る・`exit` が 0 でない・記録が無い、それぞれで `ok` が偽になり `reason` に条件が出る。GitHub を読めないと確かめられない行になる |
| `settle.close` | コメントを書いてから閉じる | 既に閉じた Issue は `already_closed` でコメントを書かない | 閉じるのに失敗した後の打ち直しでコメントが 2 件にならない。403 で `precondition`・3 |
| `settle.main` | 全行が閉じて `ok` | 既に閉じた行だけでも `ok` | 1 行が通らないと、その行を閉じずに `stopped`・`incomplete`・6 で、ほかの行は閉じる |
| `count_choices` | 規則と主体ごとに数える | `since` より前の行と `route_chosen` でない行を数えない | `rule` の欠けた行を数えず `warnings` に数が出る |
