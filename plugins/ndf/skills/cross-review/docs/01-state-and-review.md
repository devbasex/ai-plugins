# 01: 状態管理 + レビュー実行 (Step 0〜4)

`SKILL.md` 本体から呼び出される **状態ファイル初期化 / ラウンド開始 /
並列レビュー / 判定 / 振動検知** までの詳細手順。

主要処理は `scripts/` 配下のコマンドに切り出し済み:

| script | 役割 |
|---|---|
| `scripts/state.py init` | Step 0 — state 初期化 / 再開 + プリチェック |
| `scripts/state.py start-round` | Step 1 — round 開始判定 |
| `scripts/launch-codex.sh` / `scripts/launch-gemini.sh` | Step 2 — review launcher |
| `scripts/monitor.py` | Step 2 — codex/gemini プロセス多軸監視 |
| `scripts/wait-review.sh` | Step 2 — `monitor.py` の薄ラッパ（互換用） |
| `scripts/state.py read-result` | Step 2.5 — result.json マージ |
| `scripts/state.py judge` | Step 3 — intent ベース pass 判定 |
| `scripts/state.py check-oscillation` | Step 4 — 振動検知 |

このドキュメントは **state.json スキーマと AI への入出力契約** を一次資料として残す。
スクリプト側の挙動はソースを直接参照のこと。

## 状態ファイル

`$TMP_DIR/cross-review-pr<番号>-state.json`:

```json
{
  "started_at": "2026-05-12T...",
  "max_rounds": 6,
  "rotate_after": 5,
  "only": null,
  "current_pr": 123,
  "worktree_path": "/work/worktrees/pr123",
  "repo": "owner/name",
  "head_branch": "feature/foo",
  "base_branch": "main",
  "pr_author": "someone",
  "is_own_pr": false,
  "event_downgrade": false,
  "pr_history": [
    {"pr": 123, "opened_at": "...", "closed_at": null, "rounds": 2}
  ],
  "rounds": [
    {
      "round": 1,
      "pr": 123,
      "started_at": "...",
      "codex":  {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 5, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0}},
      "gemini": {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
                 "comments": 3, "review_url": "...",
                 "by_severity": {"critical": 0, "major": 2, "minor": 1, "nit": 0}},
      "fix":    {"commit": "abc1234", "fixed": 6, "deferred": 2, "rejected": 0,
                 "resolved_threads": 4, "ci": "SUCCESS", "ci_note": null},
      "ended_at": "..."
    }
  ],
  "deferred_nits": [
    {"pr": 123, "round": 1, "path": "src/foo.py", "line": 42, "severity": "nit",
     "summary": "...", "comment_url": "..."}
  ],
  "final": null
}
```

`final` 値: `approved` / `max_rounds` / `oscillation` / `error`

### 重要なフィールド

- `worktree_path` — 並行セッションとの分離。サブエージェントへの cwd 指示にも使う
- `is_own_pr` / `event_downgrade` — 自分の PR の場合 `REQUEST_CHANGES → COMMENT` 強制ダウングレード
- `rounds[].codex.intent` — AI の本来判定。**ループ判定はこれを見る**
- `rounds[].codex.posted_as` — GitHub に実際に送った event。`is_own_pr=true` なら `COMMENT` になる
- `rounds[].fix.resolved_threads` — fix サブエージェントが `resolveReviewThread` で resolve した件数
- `rounds[].fix.ci_note` — コード無関係の CI 失敗時に「Assignees 未設定」等の理由を残す

## Step 0: 準備 + 既存 state 引き継ぎ

```bash
SCRIPTS="$CLAUDE_PLUGIN_ROOT/skills/cross-review/scripts"  # or 直接の絶対パス

# state 初期化 / 再開（プリチェック・worktree 作成・既存コメントスナップショットを内部実行）
eval "$("$SCRIPTS/state.py" init "$STATE_PR" \
          --max-rounds "$MAX_ROUNDS" --rotate-after "$ROTATE_AFTER" \
          ${ONLY:+--only "$ONLY"})"

# eval で取り込まれる変数: PR, WORKTREE, REPO, HEAD_BRANCH, BASE_BRANCH,
#                        IS_OWN_PR, EVENT_DOWNGRADE, RESUMED
cd "$WORKTREE"
```

`state.py init` が内部で行う処理:

1. 既存 state.json があり `final == null` なら再開
2. 自分の PR 判定（`gh api user` と `gh pr view --json author` を比較）
3. worktree 作成（`<worktree-base>/pr<PR>`。`<worktree-base>` は `NDF_WORKTREE_BASE` env > `/work/worktrees` > `$HOME/work/worktrees` の優先順で解決。実 path は state.json の `worktree_path` を参照）
4. 既存コメントスナップショット (`fix/scripts/fetch-pr-comments.sh` で 3 ソース一括取得) → `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt`
5. state.json 書き出し

**重要**: 以降の全ステップで `cd $WORKTREE` を強制。
サブエージェント（fix）を起動するときも、prompt 内で worktree path を明示する。

## Step 1: Round 開始判定

```bash
eval "$("$SCRIPTS/state.py" start-round "$STATE_PR")"
# eval で取り込まれる変数: ROUND, ROUND_IN_PR, PR, MAX_ROUNDS, ROTATE_AFTER
```

`state.py start-round` は `max_rounds` 超過なら `final=max_rounds` を書いて exit 1。
それ以外は新しい round エントリを state.rounds に push して KEY=VALUE を吐く。

## Step 2: codex / gemini 並列レビュー（AI 直接投稿）

**要点**: メインは launcher を **並列バックグラウンド** で起動するだけ。
各 AI が `gh api` で投稿し `$TMP_DIR/<agent>-review-pr<PR>-result.json` に
サマリを書く。**ペイロード本体はメイン context に載せない**。

### 2.1 launcher 起動 + monitor

```bash
[ "$ONLY" != "gemini" ] && "$SCRIPTS/launch-codex.sh"  "$STATE_PR" "$ROUND"
[ "$ONLY" != "codex"  ] && "$SCRIPTS/launch-gemini.sh" "$STATE_PR" "$ROUND"

# monitor.py が多軸で完了判定。exit code で失敗種別を分岐。
if ! "$SCRIPTS/monitor.py" "$STATE_PR" "${ONLY:-both}"; then
  case $? in
    2) echo "❌ timeout"      ;;  # hard timeout 超過
    3) echo "❌ no result"    ;;  # プロセス終了したが result.json 未生成
    4) echo "💥 early error"  ;;  # err.log に致命的パターン
    5) echo "🛑 stalled"      ;;  # 進捗ログ更新なし
    6) echo "❓ pidfile bad"  ;;  # 起動失敗 / 不正
  esac
  # ラウンドを失敗マークしてリトライ or 中断（state.py side で判断）
fi
```

#### `monitor.py` の多軸監視

| 軸 | 内容 |
|---|---|
| pidfile + `kill -0` | プロセス生存確認。alive 確認後に `/proc/<pid>/cmdline` で agent 名一致も検証 (PID 再利用対策)。**プロセスが既に死んでいる場合は result.json の有無のみで OK 判定**する (死亡直後 cmdline 不一致で誤検知しないため) |
| codex sentinel | err.log に `^tokens used$` 出現で正常完了マーク |
| early-error | **行頭限定** で `^Error:` / `^FATAL:` / `^panic:` / `^Traceback ` / `^HTTP/1.1 401\|403\|429` / `^Approval mode overridden to "default"` / `^Authentication failed` / 「quota exceeded」「rate limit exceeded」「API key not found/missing/invalid」「sandbox error」を含む行を検出 (diff/doc 引用文中の同語句は誤検知しないよう anchor + benign フィルタ併用) |
| stall timeout | err.log + stdout.log の合計サイズが一定時間変化しなければ STALLED で中断。既定は **agent 別** (codex=**180s** / gemini=**480s**)。gemini は err.log がほぼ無音のため大きめに取る。codex 側既定は不変。上書き方法: CLI `--stall-timeout` (明示優先) > env `MONITOR_STALL_<AGENT>` (per-agent) > env `MONITOR_STALL` (両 agent 共通) > agent 別ビルトイン |
| hard timeout | 既定 **7 分**。`--timeout` or `MONITOR_TIMEOUT` env で上書き |
| result.json 存在 | プロセス終了後、result.json が無ければ NO_RESULT (exit 3) |
| **失敗時 kill** | TIMEOUT / STALLED / EARLY_ERROR / PIDFILE_BAD で返るときは対象プロセスに SIGTERM → 3 秒後 SIGKILL。残存プロセスが後から `gh api` 投稿や result.json 書き込みを行うのを防ぐ |

> ⚠ **罠**: `nohup ... &` でラッパーシェルは即終了し、ハーネスから
> 「タスク完了」通知が飛んでくる。これに惑わされず、`monitor.py` で
> 実プロセスの完了を pidfile / sentinel で確認すること。
>
> ⚠ **`pgrep -fa <prompt>` で完了判定しない**: gemini は long `-p` プロンプトを
> 引数に持つため、`grep` のキーワード選定で誤検知する。**pidfile 必須**。
>
> ⚠ **sentinel 単独で完了判定しない**: codex がクラッシュすると `tokens used` が
> 永遠に出ない。`monitor.py` は sentinel と pidfile/result.json/err.log を併用する。

### 2.2 AI への入出力契約（両 launcher 共通）

launcher が生成するプロンプトに以下を強制している:

- **headRefOid (commit_id) を明示**: AI が自前で取得すると baseRefOid を誤って入れる事故が多発
- **作業 worktree の絶対パス**: 「ファイル読み取りは必ず worktree 配下の絶対パスを使う」（実 path は state.json の `worktree_path` を参照。`<worktree-base>` は `NDF_WORKTREE_BASE` env > `/work/worktrees` > `$HOME/work/worktrees` の優先順で解決）
- **event ダウングレード警告**: `event_downgrade=true` のときは payload の `event` を `COMMENT` に
- **既存コメント差分**: `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt` を読んで重複指摘禁止
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

### 2.3 AI が書き出すファイル契約

各 launcher は AI に以下 2 ファイルの書き出しを指示する:

| ファイル | 内容 |
|---|---|
| `$TMP_DIR/<agent>-review-pr<PR>-result.json` | `{event, posted_as, comments_count, review_url, by_severity}` のサマリ |
| `$TMP_DIR/<agent>-review-pr<PR>-round<R>-payload.json` | `{comments: [{path, line, body, severity}, ...]}` 振動検知用 |

`/ndf:review` の result.json 出力規約に `posted_as` フィールドを含むこと
（自分PR ダウングレード時に GitHub に実際送った event。デフォルトは `event` と同値）。

### 2.4 result.json を state にマージ

```bash
[ "$ONLY" != "gemini" ] && "$SCRIPTS/state.py" read-result "$STATE_PR" codex
[ "$ONLY" != "codex"  ] && "$SCRIPTS/state.py" read-result "$STATE_PR" gemini
```

`state.rounds[-1].<agent>` に `intent / posted_as / comments / review_url / by_severity` を分離保存する。

## Step 3: 判定（intent ベース）

```bash
if "$SCRIPTS/state.py" judge "$STATE_PR"; then
  : # exit 0 = approved。ループ終了。
elif [ $? -eq 2 ]; then
  : # exit 2 = continue → Step 5 (fix)
else
  exit 1
fi
```

**判定ロジック**:

- `APPROVE` / `SKIP` は pass
- `COMMENT` は `by_severity.critical == 0 && major == 0` のみ pass（軽微な指摘のみなら通す）
- `--only` 指定時は反対側を SKIP 扱い
- ループ収束判定は **必ず `intent`** を見る（`posted_as` ではない）

自分の PR で `REQUEST_CHANGES → COMMENT` にダウングレード投稿していても、
intent が `REQUEST_CHANGES` なら継続する。

## Step 4: 振動検知

```bash
if "$SCRIPTS/state.py" check-oscillation "$STATE_PR"; then
  : # ここには来ない（成功は exit 2 = continue）
elif [ $? -eq 4 ]; then
  exit 4  # final=oscillation で中断
fi
```

各ラウンドの `$TMP_DIR/<agent>-review-pr<PR>-round<R>-payload.json` から
`path:line` を抽出し、前ラウンドとの重複率を計算。**50% 以上重複で中断**。

PR ローテーション直後 (`round_in_pr < 2`) はスキップ。
