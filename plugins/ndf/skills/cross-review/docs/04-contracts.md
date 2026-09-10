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
      "codex":  {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 5, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0}},
      "agy": {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 3, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 2, "minor": 1, "nit": 0}},
      "fix":    {"commit": "abc1234", "fixed": 6, "deferred": 2, "rejected": 0,
                 "resolved_threads": 4, "resolved_thread_ids": ["PRRT_kwDO..."],
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
- `review_findings[].classification` — 5 つの区分（#156）。**収束の判定が数えるのは
  `verified_blocking` と `needs_human_judgment` の 2 つだけである**
- `unmatched_critiques` — 結び先の無い反証（#156）。**捨てない**（反証 0 件のラウンドと、
  結び先を誤ったラウンドを区別するため）
- `evidence_rounds` — 証拠集約（統合・実行検証・反証）を通ったラウンドの番号（#156）。
  **収束の判定はこの印で母集合を決める。** 印を持つラウンドだけを区分の 2 つへ絞り、
  持たないラウンドは従来どおり全件を数える。**`review_findings` の有無では判定しない**
  （取り込みはこの変更より前から要素を積むため、区分も `verification` も持たない旧い
  ラウンドが絞り込みに掛かり、修正必須の `major` が `insufficient_evidence` へ落ちて
  新規 0 件で収束する）。印を書くのは経路の最後（`collect-critiques`）で、**対象ごとに
  有効な反証が揃ったときだけである**
- `rounds[].critique_relaunched` — 反証を取り直した担当（#549 レビュー対応）。
  **同じラウンドで 1 度だけ取り直す**ための控えである
- `rejected_findings` — 却下した指摘を **per-item** で蓄積する。`rounds[].fix.rejected` は
  ラウンドごとの件数で、こちらは理由と位置を持つ。**両方を持つのは、件数だけが返る劣化表現
  （`fix` が int を返す経路）があるためである。** そのときは記録が空になり、件数だけが残る。
  **項目が欠けた要素も落とさない**（落とすと却下そのものが記録から消える）
- `host_source` — `explicit`（`--host`）または `env`（環境変数からの推定）
- `rounds[].reviewers` — そのラウンドのレビュー担当 2 者。**ラウンドを開くときに決めて残す**
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
- `carried_over` — 再開の時点で残っていた未解決の指摘。`fixed_in_round` が `null` の
  あいだは、両者が承認しても収束させない（[01-state-and-review.md](01-state-and-review.md) の Step 3 参照）
- `viewer_login` — 自分のログイン名。一度取って持つ控えで、待ち行列の冪等の照合が
  「投稿者が自分か」を見るために使う
- `rounds[].codex.queued` — その結果の投稿を待ち行列へ積んだかどうか。真のあいだは
  届いたことの照会を飛ばす（[01-state-and-review.md](01-state-and-review.md) の待ち行列の節参照）
- `rounds[].verdict` の `queued` — 通ったが待ち行列に投稿が残っているラウンド。収束させない
- `sweep` — 最終スイープ後の検証結果。`remaining_open` は GitHub 側で数え直した実数で、
  `declared_remaining_open` は結果ファイルの申告値。両者が食い違う場合は実数を採る

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

