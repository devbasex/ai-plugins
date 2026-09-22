# 04: 状態ファイルと入出力の契約

`cross-review` が持つ 2 つの契約を一次資料として残す。状態ファイル（`state.json`）の形式と、
レビューを行う CLI へ渡す入出力の取り決めである。**手順の途中では読まず、形式を確かめる
ときだけ開く。** 手順は [01-state-and-review.md](01-state-and-review.md) にある。

## 状態ファイル

`$TMP_DIR/cross-review-pr<番号>-state.json`:

```json
{
  "started_at": "2026-05-12T...",
  "max_rounds": 12,
  "rotate_after": 8,
  "only": null,
  "current_pr": 123,
  "worktree_path": "/tmp/ndf-worktrees/owner--name/pr123",
  "repo": "owner/name",
  "head_branch": "feature/foo",
  "base_branch": "main",
  "pr_author": "someone",
  "is_own_pr": false,
  "event_downgrade": false,
  "participants": {
    "pool": ["codex", "agy", "kiro"],
    "included": [], "excluded": ["agy"],
    "available": ["codex"],
    "unavailable": {"kiro": "kiro-cli が見つかりません"},
    "probe_skipped": false, "require_all": false,
    "fallback": ["claude"]
  },
  "resume_changes": [
    {"at": "...", "field": "max_rounds", "from": 12, "to": 4}
  ],
  "pr_history": [
    {"pr": 123, "opened_at": "...", "closed_at": null, "rounds": 2}
  ],
  "carried_over": {
    "detected_at": "...",
    "count": 3,
    "thread_ids": ["PRRT_kwDO..."],
    "fixed_in_round": null
  },
  "sweep": {
    "declared_remaining_open": 0,
    "remaining_open": 0,
    "remaining_reason": null,
    "verified": true
  },
  "rounds": [
    {
      "round": 1,
      "pr": 123,
      "started_at": "...",
      "verdict": "changes_requested",
      "reviewers": ["codex", "claude-2"],
      "codex":  {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 5, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0}},
      "claude-2": {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 3, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 2, "minor": 1, "nit": 0}},
      "fix":    {"commit": "abc1234", "fixed": 6, "deferred": 2, "rejected": 0,
                 "resolved_threads": 4, "resolved_thread_ids": ["PRRT_kwDO..."],
                 "resolved_thread_positions": [
                   {"thread_id": "PRRT_kwDO...", "path": "src/foo.py", "line": 42}],
                 "ci": "SUCCESS", "ci_note": null},
      "ended_at": "..."
    }
  ],
  "deferred_nits": [
    {"pr": 123, "round": 1, "path": "src/foo.py", "line": 42, "severity": "nit",
     "summary": "...", "comment_url": "..."}
  ],
  "review_findings": [
    {"finding_id": "agy-r1-0", "pr": 123, "round": 1, "agent": "agy",
     "path": "src/foo.py", "line": 42,
     "severity": "major", "body": "...", "evidence": "...", "falsification": "...",
     "suggested_check": "pytest tests/test_foo.py::test_x", "posted_to": "body",
     "has_evidence": true,
     "origin_runtimes": ["agy", "kiro"], "merged_from": ["kiro-r1-2"],
     "verification": {"command": "...", "exit_code": 1, "result": "reproduced",
                      "finding_id": "agy-r1-0", "ran_at": "..."},
     "critiques": [{"agent": "codex", "verdict": "support", "reason": "..."}],
     "classification": "verified_blocking"}
  ],
  "unmatched_critiques": [],
  "evidence_rounds": [1],
  "rejected_findings": [
    {"pr": 123, "round": 1, "path": "src/foo.py", "line": 42, "severity": "minor",
     "comment_id": 3222849090, "summary": "...", "reason_for_rejection": "..."}
  ],
  "final": null
}
```

`final` 値: `approved` / `max_rounds` / `oscillation` / `error`

### 席の名前

**担当の単位は席の名前である。** 形は `<ランタイム名>` か `<ランタイム名>-<2〜9>` で、
正規表現にすると `^(claude|codex|agy|kiro)(-[2-9])?$`（共通層の `assignment.SEAT_PATTERN`）。
接尾辞の付いた名前は、使える者が足りないラウンドで立てる**同じランタイムの 2 つ目**を指す。

| 現れる場所 | 値の例 |
| --- | --- |
| `rounds[].reviewers` | `["codex", "claude-2"]` |
| `rounds[].<席の名前>` の鍵 | `claude-2` |
| `review_findings[].agent` と `finding_id` の接頭 | `claude-2` / `claude-2-r1-0` |
| 結果ファイルの stem | `<席の名前>-review-pr<番号>` |

起動する CLI はハイフンの手前を取って選ぶ（シェルは `${SEAT%%-*}`、Python は
`assignment.seat_runtime`）。ランタイム名にハイフンを含むものが無いため、両者は同じ
規則になる。**1 つ目の席の名前はランタイム名そのままである**ため、埋め合わせが要らない
実行ではこの変更の前と同じ名前しか現れない。

### 重要なフィールド

- `host` — 確定したホスト名（`claude` / `codex` / `agy` / `kiro`）。母集合から外れる
- `review_findings` — 取り込んだ指摘を **per-item** で蓄積する（#156）。各要素は
  `finding_id`（`<担当>-r<ラウンド>-<索引>`）を持つ。**取り込みの時点で採番し、統合・
  反証・実行検証の記録がどの指摘を指すかをこの値で結ぶ。** 担当とラウンドを含めるため、
  別の担当が同じ索引を持っても衝突しない。要素は
  `payload.json` の 1 件に `pr` / `round` / `agent` と `has_evidence` を添えた形である。
  **`has_evidence` は `evidence` と `falsification` の両方が空でないときだけ真になる**
  （片方だけでは、別の担当がその指摘を確かめられない）
- `review_findings[].verification` — 実行検証の結果（#156）。`result` は
  `reproduced` / `not_reproduced` / `not_run` の 3 つで、**`not_run` は実行できなかった
  ことを表す**（再現しなかったことと同じにしない）。`finding_id` は結果の出所で、
  統合した組では代表と違う値になりうる。**`ran_at` は実行した記録にだけ入り、実行
  しなかった記録では `exit_code` とともに `null` である**（実行していない記録に時刻が
  残ると、実行済みと見分けられない）
- `review_findings[].critiques` — 反証の結果（#156）。**提案者以外の担当だけが載る。**
  値は `support` / `refute` / `insufficient_evidence` / `duplicate` / `out_of_scope`。
  **1 つの `(ラウンド, finding_id, 担当)` が持つ値は 1 つである。** 取り直した反証は
  古い値へ積まず置き換える（積むと、`refute` を `support` へ訂正しても両方が並び、
  区分の順で `refute` が先に当たって指摘が `rejected` のままになる）
- `review_findings[].classification` — 6 つの区分（#156、#732）。値は `verified_blocking` /
  `verified_non_blocking` / `rejected` / `needs_human_judgment` / `unrefuted` /
  `insufficient_evidence`。**収束の判定が数えるのは `verified_blocking` と
  `needs_human_judgment` と `unrefuted` の 3 つである。** 数えないのは、誤りだと示された
  棄却と、承認を妨げない `minor` 以下だけである
- `review_findings[].unrefuted_reason` — 未反証の理由（#732）。**`classification` が
  `unrefuted` のときだけ持つ。** 値は `no_critique`（反証を返した担当が 0 者）/
  `not_supported`（反証はあるが支持も否定も無い）。区分が変わると消える（`rejection_reason`
  と同じ扱い）
- `unmatched_critiques` — 結び先の無い反証（#156）。**捨てない**（反証 0 件のラウンドと、
  結び先を誤ったラウンドを区別するため）
- `evidence_rounds` — 証拠集約（統合・実行検証・反証）を通ったラウンドの番号（#156）。
  **収束の判定はこの印で母集合を決める。** 印を持つラウンドだけを数える 3 区分へ絞り、
  持たないラウンドは従来どおり全件を数える。印の役割は、取り込みだけを済ませた旧いラウンドと、
  反証が届いていないラウンドを、棄却と `minor` 以下も含めて全件を数える側に置くことである
  （`major` は誤りを示されていなければ `unrefuted` として数えられるが、否定が届いていない
  かもしれないラウンドでは全件を数える側が安全である）。**`review_findings` の有無では判定
  しない**（取り込みは印より前から要素を積むため、区分も `verification` も持たない旧い
  ラウンドが絞り込みに掛かる）。印を書くのは経路の最後（`collect-critiques`）で、**対象
  ごとに有効な反証が揃ったときだけである**。揃わないときは付けないだけでなく、**先に付いて
  いたそのラウンドの印を外す**（取り直しの後も印が残ると、出力と実際の数え方が食い違う）
- `rounds[].critique_relaunched` — 反証を取り直した担当（#549 レビュー対応）。
  **同じラウンドで 1 度だけ取り直す**ための控えである
- `rejected_findings` — 却下した指摘を **per-item** で蓄積する。`rounds[].fix.rejected` は
  ラウンドごとの件数で、こちらは理由と位置を持つ。**両方を持つのは、件数だけが返る劣化表現
  （`fix` が int を返す経路）があるためである。** そのときは記録が空になり、件数だけが残る。
  **項目が欠けた要素も落とさない**（落とすと却下そのものが記録から消える）
- `host_source` — `explicit`（`--host`）または `env`（環境変数からの推定）
- `participants` — 使える者の解決の結果（#727）。`pool`（母集合の既定）/ `included` /
  `excluded` / `available`（使える者）/ `unavailable`（名前 → 確認が通らなかった理由）/
  `probe_skipped`（確認を飛ばしたか）/ `require_all` / `fallback`（席の埋め合わせに使える
  相手）の 8 項目。**この項目を持たない状態ファイルは、この変更の前に始めた実行である**
  （読み方は `05-pool-and-convergence.md`）。`unavailable` が空である理由は 2 つあり、
  `probe_skipped` がそれを分ける（全員が通った / 確認を飛ばした）
- `resume_changes` — 再開で変えた値の記録（#727）。要素は `at` / `field` / `to` / `from` で、
  `field` は状態ファイルの鍵である。**追記だけを行う。** 参加者の記録を作り直したときは
  `participants` の 1 件として積む（中の項目ごとには積まない）
- `rounds[].reviewers` — そのラウンドのレビュー担当 2 席。**ラウンドを開くときに決めて残す**
- `worktree_path` — 並行セッションとの分離。サブエージェントへの cwd 指示にも使う
- `is_own_pr` / `event_downgrade` — 自分の PR の場合 `REQUEST_CHANGES → COMMENT` 強制ダウングレード
- `rounds[].<担当>.intent` — AI の本来判定。**ループ判定はこれを見る**。担当ごとのキーの
  名前は担当名で、取りうる名前は 4 つ（`codex` / `agy` / `claude` / `kiro`）
- `rounds[].codex.intent` — 上の形の例。`host` を持たない状態ファイルでは `codex` / `agy`
  の 2 つだけが現れる
- `rounds[].codex.posted_as` — GitHub に実際に送った event。`is_own_pr=true` なら `COMMENT` になる
- `rounds[].fix.resolved_threads` — `resolveReviewThread` で resolve した**件数(int)**。
  ここ (state.json 側) は int だが、fix サブエージェントが返す戻り値ファイル
  (`fix-pr<PR>-result.json`) 側の `resolved_threads` は **list**（[02-fix-and-rotation.md](02-fix-and-rotation.md) の戻り値スキーマ参照）。
  `state.py merge-fix` が fix結果の list を `len()` して state.json に int で保存する。
  混同して fix結果側に int を書くと過去 `merge-fix` が落ちていたため、現在は int/list 両受理
- `rounds[].fix.ci_note` — コード無関係の CI 失敗時に「Assignees 未設定」等の理由を残す
- `rounds[].verdict` — そのラウンドの判定（`approved` / `changes_requested`）。
  次のラウンドの開始時に、修正の記録の有無を突き合わせるために使う
- `rounds[].fix.resolved_thread_ids` — 修正サブエージェントが Resolve したと申告した
  thread ID の一覧。次のラウンドの開始時に GitHub 側の未解決集合と突き合わせる
- `rounds[].fix.resolved_thread_positions` — Resolve したと申告したスレッドの位置
  （#156）。要素は `{"thread_id", "path", "line"}` で、fix の戻り値の
  `resolved_threads[]` から写す。**読むのは効果の測定（`scripts/measure.py`）だけで、
  収束ループはこの値を見ない。** 上限の方式が、この位置と `review_findings[].path` /
  `line` を結んで「修正された指摘」を決める。**位置の欠けた要素も落とさない**
  （`path` / `line` は `null` になりうる）。件数だけが返る劣化表現（`resolved_threads`
  が int）では空の一覧になる。**この項目を持たない過去の状態ファイルでは、上限の方式を
  計算できない**
- `carried_over` — 再開の時点で残っていた未解決の指摘。`fixed_in_round` が `null` の
  あいだは、両者が承認しても収束させない（[01-state-and-review.md](01-state-and-review.md) の Step 3 参照）
- `viewer_login` — 自分のログイン名。一度取って持つ控えで、待ち行列の冪等の照合が
  「投稿者が自分か」を見るために使う
- `rounds[].codex.queued` — その結果の投稿を待ち行列へ積んだかどうか。真のあいだは
  届いたことの照会を飛ばす（[01-state-and-review.md](01-state-and-review.md) の待ち行列の節参照）
- `rounds[].verdict` の `queued` — 通ったが待ち行列に投稿が残っているラウンド。収束させない
- `sweep` — 最終スイープ後の検証結果。`remaining_open` は GitHub 側で数え直した実数で、
  `declared_remaining_open` は結果ファイルの申告値。両者が食い違う場合は実数を採る

## 再開で渡した引数の扱い

**黙って捨てる引数は無い。** 渡さなかった引数は状態ファイルの値のまま残り、`--worktree` /
`--focus` / `--extra-instructions-file` は状態に載らないため毎回の指定が使われる。

| 扱い | 引数 | 何が起きるか |
| --- | --- | --- |
| 反映する | `--max-rounds` / `--rotate-after` / `--verify-command` / `--verify-exit-code` | 状態を書き換え、`resume_changes` へ 1 件積み、`↻ <項目>: <旧> → <新>` を出す |
| 反映し、参加者を作り直す | `--only` | 状態を書き換えて記録へ積んだうえで、認証の確認をやり直して `participants` を置き換える。`none` を渡すと 1 者指定を外す |
| 参加者を作り直す | `--exclude` / `--include` / `--require-all` | 使える者を解決し直して `participants` を置き換える。失敗したら状態を書き換えずに終了コード 1 |
| 反映しない | `--host` | 状態と違うときだけ `ℹ --host は再開では反映しません` を出す |

**1 者指定は 2 行にまたがる。** 1 者指定（`--only`）は状態ファイルに載る項目であると同時に、
参加する実行主体を決め直す引数でもある（`PARTICIPANT_ARGS`）。渡した再開は、指定した 1 者の
認証の確認をやり直し、通らなければ状態を書き換えずに終了コード 1 で止まる。

## AI への入出力契約（両 launcher 共通）

launcher が生成するプロンプトに以下を強制している:

- **headRefOid (commit_id) を明示**: AI が自前で取得すると baseRefOid を誤って入れる事故が多発
- **作業 worktree の絶対パス**: 「ファイル読み取りは必ず worktree 配下の絶対パスを使う」（実 path は state.json の `worktree_path` を参照。`<worktree-base>` は `NDF_WORKTREE_BASE` env > `<システム tmpdir>/ndf-worktrees` の優先順で解決）
- **event ダウングレード警告**: `event_downgrade=true` のときは payload の `event` を `COMMENT` に
- **既存コメント差分**: `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt` を読んで重複指摘禁止
- **自動レビュー観点**: GitHub API の `pulls/<PR>/files --paginate` で変更ファイルを全件取得して分類し、`common` / `docs_only` / `code` / `db_migration` / `test` / `dependency` / `config_ci` / `api_contract` / `auth_security` / `frontend` / `performance` / `deletion_rename` / `generated` / `i18n` / `infra` の該当テンプレートを state.json の `auto_review_instructions` に保存する
- **手動追加レビュー観点**: `--focus` / `--extra-instructions-file` が指定されていれば state.json の `manual_extra_review_instructions` に保存し、自動テンプレートの後ろに連結した `review_instructions` を codex / agy 両 launcher が同じ「追加レビュー観点」セクションとしてプロンプトに差し込む
- **進捗マーカー**: agy には `$TMP_DIR/agy-review-pr<PR>-progress.log` へ短いフェーズ名を追記させ、monitor の heartbeat で表示する。内部推論や長文説明は書かせない
- **review body 先頭 prefix**:
  ```
  ## 🤖 cross-review | round <N> | <agent> | <event(intent)>
  ```
  `<event>` は **本来の intent**（`posted_as` ではない）。
  例: 自分PR で REQUEST_CHANGES を COMMENT にダウングロードしても、prefix は `REQUEST_CHANGES` のまま。
- **出力禁止事項**（SKILL.md「レビュー出力の制約」と一致）:
  - 「良い点」「Strengths」などの褒めセクションを body に書かない
  - 修正アクションを伴わないインラインコメントは作らない（nit はインライン化しない）
  - コード引用のみで指摘内容が無いコメント禁止
  - 雑感だけの `event=COMMENT` 投稿禁止（直すべき点が無ければ `APPROVE`）

## AI が書き出すファイル契約

各 launcher は AI に以下 2 ファイルの書き出しを指示する:

| ファイル | 内容 |
|---|---|
| `$TMP_DIR/<agent>-review-pr<PR>-result.json` | `{event, posted_as, comments_count, review_url, by_severity}` のサマリ |
| `$TMP_DIR/<agent>-review-pr<PR>-round<R>-payload.json` | `{comments: [{path, line, body, severity, evidence, falsification, suggested_check, posted_to}, ...]}` |

**`comments[]` が持つのは、その担当が出した指摘の全件である**（#156）。投稿した
インラインの写しではない。**差分の外を指すために総評へ書いた指摘も、`HTTP 422` で総評へ
移した指摘も載る。** そのため `result.json` の `comments_count`（投稿したインラインの数）
とは一致しない。

| 項目 | 何を書くか | 無いときの扱い |
| --- | --- | --- |
| `evidence` | 根拠。対象のコードと到達経路 | 空。`has_evidence` が偽になる |
| `falsification` | 反証条件。これが成り立てば棄却できる | 同上 |
| `suggested_check` | 実行できる検証手順 | 空 |
| `posted_to` | `inline` / `body` のどちらへ投稿したか | `inline` として扱う |

**4 項目を持たない指摘も捨てない。** 捨てると、対応していない担当の指摘が記録から消える。

### payload の形が違うとき

**取り込みは止めず、警告を出して 0 件にする。** `payload` が dict でないとき、
`comments` が list でないとき、要素に dict でないものがあるときが対象である。

**止めないのは、そのラウンドのレビュー結果を失わないためである。** 記録の保存は
取り込みの後にあり、ここで止めると `rounds[].<agent>`（判定・投稿先・件数）ごと消える。
**判定は止まる。** 同じファイルを判定の直前に読む `_finding_keys` が `die(code=3)` で
中断するため、形の不正が見逃されることはない。警告は、記録が空である理由が payload の
形にあることを読み取れるようにする。

### 同じラウンドを取り込み直したとき

**同じ `(pr, round, agent)` の記録は入れ替える。** 中断からの再実行で取り込みが 2 度
走ることがあり、追記のままでは同じ指摘が件数だけ増える。**入れ替えであって、追記の
抑止ではない**（前回より減った指摘は記録からも消える）。

**落とすのは、書き込む中身が確定した後である。** 読めなかった再実行が、一度取り込めて
いた記録を消さないようにする。

`/ndf:pr-review` の result.json 出力規約に `posted_as` フィールドを含むこと
（自分PR ダウングレード時に GitHub に実際送った event。デフォルトは `event` と同値）。

## 監視と計測が残すファイル

監視（`monitor.py`）と状態の保存が、AI の書き出しとは別に残す（#662）。

| ファイル | 置き場所 | 中身 | いつ書くか |
|---|---|---|---|
| `<stem>-monitor.json` | `$TMP_DIR` | その担当の**最後の**監視の結果（`status` / `reason` / 時刻と、`--phase` の値の `phase`（省いたときは `null`）など 15 個のキー） | 担当 1 者の監視を終えたとき。起動（`launch-cli.sh`）の前に消す |
| `monitor-outcomes.jsonl` | `$TMP_DIR` | 監視の結果を 1 行 1 つで**追記だけ**で積む | 同上。消さない |
| `cross-review-pr<PR>-<開始時刻の UTC>.json` | 要約の置き場所の `<owner>--<repo>/` | 実行の要約（所要・結末・ラウンド・起動と `measure`）。**本文・`detail` を含まない** | 状態を保存するたび（同じ実行は上書き） |

`reason` は既定では `status` から決まる（`OK`→`ok` / `TIMEOUT`→`timeout` / `STALLED`→`stalled` /
`EARLY_ERROR`→`early_error` / `NO_RESULT`→`missing` / `PIDFILE_BAD`→`pidfile_bad`）。監視が
文言で区別した 2 つだけが状態から決まらない: 利用上限は `EARLY_ERROR` のまま `usage_limit`、
CLI 自身の上限で結果を書かずに終わったときは `NO_RESULT` のまま `cli_timeout`（#729）。
監視の標準出力と終了コードは変わらない。

結果の取り込みは結果ファイルを自前で開かず、共通層の `monitor_outcome.read_launch_outcome(tmp_dir,
"<agent>-review-pr<PR>", result_path)` が返す値（使える結果 `payload` / 理由 `reason` / 監視の詳細
`detail` / 起動し直しの可否 `relaunch_same_agent`）を読む。結果なしのときは
`rounds[-1].<agent>` に `intent: "NO_RESULT"`、`no_result_reason: <reason>`、監視の結果ファイルが
あれば `monitor_detail: <detail>` を書く（**鍵が無い** = 監視の結果ファイルが無かった。空文字は
書かない）。理由の一覧は [01-state-and-review.md](01-state-and-review.md) の「結果を残さなかった
レビュアーの扱い」にある。

**要約の置き場所は作業ツリーの外である。** `NDF_METRICS_DIR` → `$XDG_STATE_HOME/ndf/metrics` →
`$HOME/.local/state/ndf/metrics` の順に決まり、`NDF_METRICS=0` のときは書かない。`state.py report`
の最後の行が、書いた要約のパスか書かなかった理由を出す。束ねるのは共通層の `run_metrics.py aggregate`
（`--since` / `--until` / `--repo` / `--kind` / `--version` / `--by total|round-count|reason`）である。

## `<worktree-base>` の解決順

`state.py init` は worktree の親ディレクトリを以下の優先順で解決する:

1. `NDF_WORKTREE_BASE` 環境変数（明示オーバーライド）
2. `<システム tmpdir>/ndf-worktrees`（Python `tempfile.gettempdir()`。非永続領域のため
   コンテナ再作成で自動消滅し、共有 volume を消費しない）

worktree の実パスは `<base>/<owner>--<repo>/pr<PR>` 形式で、リポジトリ slug を含める
ことで**他リポジトリの同一 PR 番号と衝突しない**。永続 volume（旧 `/work/worktrees`）を
使っていた頃は別プロジェクトの残骸 worktree を誤って流用する事故があったため、
パスが存在しても `git worktree list` に登録されていなければ `.stale-<timestamp>` に
退避して作り直すガードも入っている。

解決した実パスは `state.json` の `worktree_path` に書かれるため、後続スクリプトや
サブエージェント prompt は state.json から読めば追従できる。

