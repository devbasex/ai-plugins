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
| `scripts/state.py unresolved-threads` | PR 上の未解決の指摘を数える（順序を持たない補助） |
| `scripts/state.py judge` | Step 3 — intent + 引き継いだ指摘の判定 |
| `scripts/state.py check-oscillation` | Step 4 — 振動検知 |

このドキュメントは **state.json スキーマと AI への入出力契約** を一次資料として残す。
スクリプト側の挙動はソースを直接参照のこと。

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
      "gemini": {"intent": "REQUEST_CHANGES", "posted_as": "COMMENT",
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
  "final": null
}
```

`final` 値: `approved` / `max_rounds` / `oscillation` / `error`

### 重要なフィールド

- `worktree_path` — 並行セッションとの分離。サブエージェントへの cwd 指示にも使う
- `is_own_pr` / `event_downgrade` — 自分の PR の場合 `REQUEST_CHANGES → COMMENT` 強制ダウングレード
- `rounds[].codex.intent` — AI の本来判定。**ループ判定はこれを見る**
- `rounds[].codex.posted_as` — GitHub に実際に送った event。`is_own_pr=true` なら `COMMENT` になる
- `rounds[].fix.resolved_threads` — `resolveReviewThread` で resolve した**件数(int)**。
  ここ (state.json 側) は int だが、fix サブエージェントが返す戻り値ファイル
  (`fix-pr<PR>-result.json`) 側の `resolved_threads` は **list**（`docs/02` の戻り値スキーマ参照）。
  `state.py merge-fix` が fix結果の list を `len()` して state.json に int で保存する。
  混同して fix結果側に int を書くと過去 `merge-fix` が落ちていたため、現在は int/list 両受理
- `rounds[].fix.ci_note` — コード無関係の CI 失敗時に「Assignees 未設定」等の理由を残す
- `rounds[].verdict` — そのラウンドの判定（`approved` / `changes_requested`）。
  次のラウンドの開始時に、修正の記録の有無を突き合わせるために使う
- `rounds[].fix.resolved_thread_ids` — 修正サブエージェントが Resolve したと申告した
  thread ID の一覧。次のラウンドの開始時に GitHub 側の未解決集合と突き合わせる
- `carried_over` — 再開の時点で残っていた未解決の指摘。`fixed_in_round` が `null` の
  あいだは、両者が承認しても収束させない（Step 3 参照）
- `sweep` — 最終スイープ後の検証結果。`remaining_open` は GitHub 側で数え直した実数で、
  `declared_remaining_open` は結果ファイルの申告値。両者が食い違う場合は実数を採る

## Step 0: 準備 + 既存 state 引き継ぎ

```bash
# この Skill のディレクトリを決める。候補を順に試し、最初に当たったものを絶対パスで採る。
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である（未定義の変数を読まないので `set -u` でも落ちない）。Codex と Kiro CLI は置き換えず、
# プラグインルートを示す環境変数も置かない（Codex は実測、Kiro CLI は未確認）。置き換えない
# runtime では、
# **この bash を実行する前に `<この Skill のディレクトリ>` をランタイムから渡された実際の
# パスへ置き換えること**。置き換えないまま実行しても、その候補が外れるだけで別の場所を
# 読むことはない。Kiro CLI は installer が `.kiro/skills/` へ symlink を張るため、置き換え
# なくてもその位置で当たる。
SKILL_NAME=cross-review
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
SKILL_DIR=
# 明示的に渡されたディレクトリを `.kiro` より先に見る。逆にすると、Kiro の設定を持つ
# リポジトリで Codex や Claude Code を動かしたときに別 runtime の Skill を選ぶ。
for candidate in \
  ${PLUGIN_ROOT:+"$PLUGIN_ROOT/skills/$SKILL_NAME"} \
  "<この Skill のディレクトリ>" \
  ".kiro/skills/$SKILL_NAME" \
  "$HOME/.kiro/skills/$SKILL_NAME"
do
  [ -d "$candidate/scripts" ] || continue
  # 相対パスのまま持ち回ると、この後 worktree へ移ったときに外れる。ここで絶対パスにする。
  SKILL_DIR="$(cd "$candidate" && pwd)"
  break
done
[ -n "$SKILL_DIR" ] || { echo "この Skill のディレクトリを解決できない" >&2; exit 1; }
SCRIPTS="$SKILL_DIR/scripts"

# state 初期化 / 再開（プリチェック・worktree 作成・既存コメントスナップショットを内部実行）
# ⚠ `eval "$(スクリプト)"` は、スクリプトが異常終了しても出力が空なら終了コード 0 に
# なる。コマンド置換の終了コードは eval 自身の終了コードにならないため、止まるべき
# 場面で止まらない。**必ず変数で受け、終了コードを見てから eval する。**
INIT_VARS=$("$SCRIPTS/state.py" init "$STATE_PR" \
          --max-rounds "$MAX_ROUNDS" --rotate-after "$ROTATE_AFTER" \
          ${ONLY:+--only "$ONLY"} \
          ${FOCUS:+--focus "$FOCUS"} \
          ${EXTRA_INSTRUCTIONS_FILE:+--extra-instructions-file "$EXTRA_INSTRUCTIONS_FILE"}) || exit $?
eval "$INIT_VARS"

# eval で取り込まれる変数: PR, WORKTREE, REPO, HEAD_BRANCH, BASE_BRANCH,
#                        IS_OWN_PR, EVENT_DOWNGRADE, CARRIED_OVER_THREADS, RESUMED
cd "$WORKTREE"
```

`state.py init` が内部で行う処理:

1. 既存 state.json があり `final == null` なら再開。**再開のときは PR 上の未解決の指摘を
   数え、`carried_over` に記録する**（Step 3 の判定へ入る。取得できなければ記録は変えない）
2. 自分の PR 判定（`gh api user` と `gh pr view --json author` を比較）
3. worktree 作成（`<worktree-base>/<owner>--<repo>/pr<PR>`。`<worktree-base>` は `NDF_WORKTREE_BASE` env > `<システム tmpdir>/ndf-worktrees` の優先順で解決。既存パスが現リポジトリの登録済み worktree でなければ `.stale-<ts>` に退避して作り直す。実 path は state.json の `worktree_path` を参照）

   **既存の worktree を流用するときは、必ず PR の head へ同期する。** `git fetch origin <head>`
   の後に worktree の中で `git reset --hard origin/<head>` を実行し、
   `git clean -fd -e .cross_review` で前回の実行が残した追跡対象外のファイルを消す。
   フォーク PR で origin に head branch が無いときは `gh pr checkout <PR> --detach` へ
   落とす。同期できないときは止める。

   同期しないと、**レビュー担当は前回の実行が残した古い差分を読む**。指摘は現在の Pull Request
   に無い行へ出るか、直したはずの箇所へ再び出る。どちらも投稿されるため、読む側からは
   見分けが付かない。実測では 8 コミット古い worktree がそのまま流用された。

   追跡対象外のファイルを消すのは、fix 担当が `git add -A` を使ったときに Pull Request へ
   混ざるためである。`-x` は付けず、tmp ディレクトリは `-e` で除外する。`.gitignore` に
   `.cross_review/` を持たないリポジトリでも、state.json と result.json が残る。

   **再開の経路でも同じ同期を行う。** 中断から再開するまでの間に head が進んでいることがある。
   **ラウンドの開始時（Step 1）にも同期する。** そちらは失われるものがあるときに止める。
4. 既存コメントスナップショット (`fix/scripts/fetch-pr-comments.sh` で 3 ソース一括取得) → `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt`
5. state.json 書き出し

**重要**: 以降の全ステップで `cd $WORKTREE` を強制。
サブエージェント（fix）を起動するときも、prompt 内で worktree path を明示する。

## Step 1: Round 開始判定

```bash
ROUND_VARS=$("$SCRIPTS/state.py" start-round "$STATE_PR") || {
  RC=$?
  [ "$RC" -eq 1 ] && break   # max_rounds 到達 → ループを抜けて最終スイープへ
  exit "$RC"                 # 5=後始末が未了 / 8=作業ツリーを同期できない。その場で止める
}
eval "$ROUND_VARS"
# eval で取り込まれる変数: ROUND, ROUND_IN_PR, PR, MAX_ROUNDS, ROTATE_AFTER
```

`state.py start-round` は `max_rounds` 超過なら `final=max_rounds` を書いて exit 1。
それ以外は新しい round エントリを state.rounds に push して KEY=VALUE を吐く。

前のラウンドの後始末（返信と Resolve）が終わっていなければ **exit 5** で止まる。
条件は Step 3 の「Step 1 の開始時に行う後始末の検査」にある。

### ラウンドの開始時の同期

**round エントリを開く前に、作業ツリーを PR の head へ揃える。** 修正を作業ツリーの外で
行って push すると、次のラウンドは 1 つ前の内容をレビューする。実測（PR #212）では、
ラウンド 4 で対応済みの指摘 2 件がラウンド 5 で再び投稿された。エントリを開く前に行うのは、
途中で止まったときにラウンドが半端に開かれず、原因を取り除いた後に同じ番号から再開できる
ようにするためである。

| 状態 | 扱い |
| --- | --- |
| head より古い | `git fetch` と `git reset --hard <headRefOid>` で揃える |
| head と一致していて変更が無い | 何もしない。`git clean` も発行しない |
| 追跡対象のファイルに変更がある | **exit 8** で止める |
| 基準に含まれないローカルのコミットがある | **exit 8** で止める |
| 基準を取り込めない / head を解決できない | **exit 8** で止める |
| `worktree_path` が無い、または登録済みの作業ツリーでない | 同期せず、警告して続ける |

追跡対象の変更と未 push のコミットで止めるのは、それが**修正の工程が push を終えていない
証拠**だからである。捨てると修正そのものが失われ、しかも失われたことが誰にも見えない。
`init` の側で同じものを捨てているのは、そこで見つかる残骸が前回の実行のものだからで、
意味が違う。

**同期の失敗で exit 1 を返さない。** 1 はループを抜ける値であり、返すと同期できない原因が
残ったまま最終スイープへ進む。

比較の基準は `gh pr view --json headRefOid` が返すコミットで、`origin/<head>` ではない。
フォークの PR では head branch が base のリポジトリに無く、`origin/<head>` を基準に据えると
一致判定も未 push のコミットの検出もフォークのときだけ行えない。取り込みの宛先だけを
`refs/pull/<PR>/head` へ変える。

同期先のブランチ名は毎ラウンド取り直し、`state.json` の `head_branch` へ書き戻す。
`squash` の巻き直しは `<branch>-r<HHMMSS>` を作るが `head_branch` は更新されないため、
state の値をそのまま使うと巻き直し前のブランチへ戻すことになる。

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
| stall timeout | err.log + stdout.log + progress.log の合計サイズが一定時間変化しなければ STALLED で中断。既定は **agent 別** (codex=**180s** / gemini=**480s**)。gemini は err.log がほぼ無音のため大きめに取る。codex 側既定は不変。上書き方法: CLI `--stall-timeout` (明示優先) > env `MONITOR_STALL_<AGENT>` (per-agent) > env `MONITOR_STALL` (両 agent 共通) > agent 別ビルトイン |
| hard timeout | 既定 **7 分**。`--timeout` or `MONITOR_TIMEOUT` env で上書き |
| progress.log heartbeat | launcher が任意で `<agent>-review-pr<PR>-progress.log` への短いフェーズマーカー出力を要求し、monitor が最終行を stderr の heartbeat に表示する。内部推論ではなく `scan` / `analyze` / `post` / `done` などの監視用ステータスだけを出す |
| result.json 存在 | プロセス終了後、result.json が無ければ NO_RESULT (exit 3) |
| **result.json + age fallback** | sentinel を持たない agent (gemini) 向け。プロセスが alive のまま result.json の mtime が **30 秒以上前**なら完了とみなし kill → OK。gemini が MCP 切断待ち等でハングするケースに対応 (codex は sentinel チェックが先に発火するため影響なし) |
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
>
> ⚠ **Docker 環境ではゾンビプロセスに注意**: `nohup ... & disown` で起動した
> プロセスは、終了後にゾンビ化する (PID 1 が proper init でない場合)。
> `monitor.py` は `/proc/<pid>/status` でゾンビを検出して dead 扱いする。
> **推奨: Docker 実行時に `--init` フラグを付ける** (tini が PID 1 になりゾンビを reap する)。

### 2.2 AI への入出力契約（両 launcher 共通）

launcher が生成するプロンプトに以下を強制している:

- **headRefOid (commit_id) を明示**: AI が自前で取得すると baseRefOid を誤って入れる事故が多発
- **作業 worktree の絶対パス**: 「ファイル読み取りは必ず worktree 配下の絶対パスを使う」（実 path は state.json の `worktree_path` を参照。`<worktree-base>` は `NDF_WORKTREE_BASE` env > `<システム tmpdir>/ndf-worktrees` の優先順で解決）
- **event ダウングレード警告**: `event_downgrade=true` のときは payload の `event` を `COMMENT` に
- **既存コメント差分**: `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt` を読んで重複指摘禁止
- **自動レビュー観点**: GitHub API の `pulls/<PR>/files --paginate` で変更ファイルを全件取得して分類し、`common` / `docs_only` / `code` / `db_migration` / `test` / `dependency` / `config_ci` / `api_contract` / `auth_security` / `frontend` / `performance` / `deletion_rename` / `generated` / `i18n` / `infra` の該当テンプレートを state.json の `auto_review_instructions` に保存する
- **手動追加レビュー観点**: `--focus` / `--extra-instructions-file` が指定されていれば state.json の `manual_extra_review_instructions` に保存し、自動テンプレートの後ろに連結した `review_instructions` を codex / gemini 両 launcher が同じ「追加レビュー観点」セクションとしてプロンプトに差し込む
- **進捗マーカー**: Gemini には `$TMP_DIR/gemini-review-pr<PR>-progress.log` へ短いフェーズ名を追記させ、monitor の heartbeat で表示する。内部推論や長文説明は書かせない
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

`/ndf:pr-review` の result.json 出力規約に `posted_as` フィールドを含むこと
（自分PR ダウングレード時に GitHub に実際送った event。デフォルトは `event` と同値）。

### 2.4 result.json を state にマージ

```bash
[ "$ONLY" != "gemini" ] && "$SCRIPTS/state.py" read-result "$STATE_PR" codex
[ "$ONLY" != "codex"  ] && "$SCRIPTS/state.py" read-result "$STATE_PR" gemini
```

`state.rounds[-1].<agent>` に `intent / posted_as / comments / review_url / by_severity` を分離保存する。

#### 申告されたコメント数を GitHub 側と突き合わせる

投稿は **AI 自身が `gh api` で行う**ため、失敗しても結果ファイルの申告だけは残る。
申告のまま進むと、修正担当が読むべき指摘が GitHub 上に存在しないまま収束判定まで走る。
実測では、2 件の申告に対しスレッドが 1 つも作られていなかった。

`read-result` は申告が 1 件以上のとき、`review_url` の識別子から
`repos/<repo>/pulls/<PR>/reviews/<id>/comments` を数えて突き合わせる。

| 申告 | GitHub 側 | 扱い |
| --- | --- | --- |
| 0 件 | 見に行かない | 突き合わせる相手がいない |
| n 件 | n 件以上 | 採用する。人の追記など申告以外の経路で増えうる |
| n 件 | n 件未満 | **中断する。** 投稿が届いていない |
| n 件 | 取得できない | 申告を採用し、確認できなかったことを出力へ残す |

**「取得できなかった」と「0 件」を区別する。** 取得の失敗で止めると、GitHub 側の
一時的な不調でループが進まなくなる。

## Step 3: 判定（intent ベース + 引き継いだ指摘 + 結果なし）

```bash
JUDGE_VARS=$("$SCRIPTS/state.py" judge "$STATE_PR"); JUDGE_RC=$?
eval "$JUDGE_VARS"
# コマンド置換の終了コードは変数で受けてから読む。
# `eval "$(...)"` の形は置換の終了コードを飲むため、7 で分岐できない。
```

### 判定の出口

出口は 4 つある。**結果を取り込めていないラウンドは、収束も修正も決められない。**
そのため結果なしの検査を、通ったかどうかの判定より先に置く。

| ラウンドの状態 | 出口 | 終了コード | `verdict` |
| --- | --- | --- | --- |
| 結果なしがあり、そのレビュアーをまだ起動し直していない | 起動し直す | 7 | `no_result` |
| 結果なしがあり、既に起動し直している | 中断（`final = error`） | 1 | `no_result` |
| 通った、かつ引き継いだ指摘なし | 収束（`final = approved`） | 0 | `approved` |
| それ以外 | 修正へ | 2 | `changes_requested` |

### 結果を残さなかったレビュアーの扱い

**起動したのに使える結果が残らなかったレビュアーは、収束の側へ数えない。** レビューが
行われていないのに、行われて承認されたのと同じ出口へ進むためである。打ち切りは負荷が
高いときや対象が大きいときに起きやすく、レビューが最も要る場面で承認が水増しされる。

`--only` で外したレビュアーと、結果を残さなかったレビュアーは別の値で持つ。

| 値 | 意味 | 収束の判定 |
| --- | --- | --- |
| `SKIP` | `--only` の指定で起動しなかった | 判定へ届かない（短絡する） |
| `NO_RESULT` | 起動したが、使える結果が残らなかった | 通らない |

`NO_RESULT` は `read-result` が書き込む。理由は `no_result_reason` に残る。

| 理由 | 何が起きたか | `read-result` の終了コード |
| --- | --- | --- |
| `missing` | 結果ファイルが無い、または空 | 1 |
| `unparsable` | JSON として読めない、または dict ではない | 3 |
| `no_verdict` | `event` も `intent` も無い | 1 |

**記録が無いラウンドも結果なしとして読む。** 骨組みが取り込みを呼び忘れても、判定は
収束しない。

終了コード 7 のとき、判定は次の 2 行を追加で出力し、対象を `rounds[-1].relaunched` へ
記録する。**2 度目の判定は記録を見て中断へ回る。**

```text
RELAUNCH_AGENTS='gemini'
RELAUNCH_TARGET=gemini
```

`RELAUNCH_TARGET` は `codex` / `gemini` / `both` のいずれかで、`monitor.py` へそのまま
渡せる値である。起動し直しはラウンドの中で完結するため、**ラウンドの数え方と上限の
意味は変わらない。** 回数を 1 度に限るのは、待ち時間の上限をラウンドあたり 2 回分に
収めるためである。

### 判定へ入れる対象

判定は 2 つの対象を別々に見る。**投稿数と未解決の指摘の数は一致しない。**
投稿数はそのラウンドで外部の AI が新しく投稿した件数で、未解決の指摘は前のラウンドの
分も含む Pull Request 上の総数である。

| 対象 | 判定への入れ方 | 数え方 |
|---|---|---|
| そのラウンドで新しく投稿された指摘 | 外部の AI が返した `intent` と重要度で見る | `result.json` の `comments_count` |
| 引き継いだ指摘（再開の時点で残っていた未解決の指摘） | 修正の工程（Step 5）を 1 度通すまで収束させない | GraphQL の `reviewThreads` を数え直す |

```mermaid
graph TD
    A[ラウンドの判定] --> G{結果を残さなかった<br/>レビュアーがいるか}
    G -->|"いる (初回)"| R[そのレビュアーだけ<br/>起動し直す]
    G -->|"いる (2 度目)"| X[中断<br/>final=error]
    R --> A
    G -->|いない| B{新しく投稿された<br/>指摘は通ったか}
    B -->|通らない| E[修正の工程<br/>Step 5]
    B -->|通った| C{引き継いだ指摘が<br/>残っているか}
    C -->|残っている| E
    C -->|無い| D[収束<br/>final=approved]
    E --> F[次のラウンド]
```

すべてのラウンドで未解決の指摘が 0 件になるまで収束させる形は採らない。承認された
ラウンドに軽微な指摘が乗るのは通常の経路であり、そこを収束の条件にするとラウンドが
増え続ける。収束を止めるのは修正の工程を 1 度通すまでで、**増えるラウンドは最大 1 回**に
収まる。1 度通した後に残る指摘は最終スイープ（Step 7.5）が受け持つ。

**判定ロジック**（新しく投稿された指摘の側）:

- `APPROVE` は pass
- `COMMENT` は `by_severity.critical == 0 && major == 0` のみ pass（軽微な指摘のみなら通す）
- `NO_RESULT` は pass にしない（結果なしの検査が先に効くため、ここへは届かない）
- `--only` 指定時は反対側を SKIP 扱いとし、pass 判定を短絡する
- ループ収束判定は **必ず `intent`** を見る（`posted_as` ではない）

自分の PR で `REQUEST_CHANGES → COMMENT` にダウングレード投稿していても、
intent が `REQUEST_CHANGES` なら継続する。

### 引き継いだ指摘の記録と解除

| 段階 | 起きること |
|---|---|
| `init` の再開 | 未解決の指摘を数え、`carried_over` に件数と thread ID を記録。出力に `CARRIED_OVER_THREADS=N` |
| `judge` | `carried_over.fixed_in_round` が `null` のあいだは、両者が承認しても exit 2 |
| `merge-fix` | 修正の工程を通したラウンド番号を `carried_over.fixed_in_round` に入れる |
| 以降の `judge` | 引き継ぎは判定から外れ、収束の振る舞いは現行に戻る |

新規に開始したレビューでは記録しない。引き継ぎは**再開の時点**で決まる。
未解決の指摘を取得できなかったときは記録を書き換えない（0 件として扱わない）。

修正の工程を通した後にもう一度再開したときは、**そのとき残っている指摘が前回の記録に
無い識別子を含むかどうか**で分ける。含まなければ deferred / rejected と最終スイープ待ちの
残りなので、`fixed_in_round` をそのまま残して収束を抑止しない。含むなら再開の後に増えた
指摘があるため、それを含めて数え直し、もう 1 度修正の工程へ通す。前者を未処理として
数え直すと、収束が再開のたびに 1 ラウンドずつ先送りされる。

### 未解決の指摘を単独で数える

```bash
UNRESOLVED_VARS=$("$SCRIPTS/state.py" unresolved-threads "$STATE_PR") || exit $?
eval "$UNRESOLVED_VARS"
# UNRESOLVED_COUNT / UNRESOLVED_THREAD_IDS を取り込む。
# exit 1 = 取得できなかった（0 件と区別する）
```

### Step 1 の開始時に行う後始末の検査

`start-round` は、前のラウンドの後始末が終わっているかを次の 2 点で確かめ、
どちらかに当たれば **exit 5** で止まる。進行側が手で修正して次のラウンドへ進めると、
Step 5 が担う返信と Resolve が飛ばされるため。

| 状態 | 扱い |
|---|---|
| 前のラウンドが修正必須の判定で、`fix` の記録が無い | exit 5 |
| 前のラウンドで Resolve したと申告された thread が GitHub 側で未解決のまま | exit 5 |
| 前のラウンドが結果なし（`verdict = no_result`）| 修正の記録を求めない |
| 未解決の指摘を取得できない | 検査を飛ばし、確認できなかったことを stderr へ残して続行 |

判定の結果（`rounds[].verdict`）を持たない古い state.json では、保存された `intent` と
重要度から判定し直して同じ検査へ通す。

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
