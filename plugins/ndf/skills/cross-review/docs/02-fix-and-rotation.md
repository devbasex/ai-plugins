# 02: 修正 (Step 5) + PR ローテーション (Step 6) + 終了処理 (Step 8)

主要処理は `scripts/` 配下に切り出し済み:

| script | 役割 |
|---|---|
| (Agent) | Step 5 — 修正サブエージェント起動（メインからの責務） |
| `scripts/state.py merge-fix` | Step 5 後段 — fix 戻り値マージ + CI 分類 |
| `scripts/state.py should-rotate` | Step 6 — rotate 要否判定 |
| `scripts/rotate-pr.sh` | Step 6 — PR rotation 実行 |
| `scripts/state.py set-current-pr` | Step 6 — rotation 後の state 更新 |
| `scripts/state.py report` | Step 8 — deferred nit + ラウンドサマリ |

## Step 5: 修正 — **必ずサブエージェント経由**

**メインセッションでは修正コードを書かない。** `/ndf:fix` を
`general-purpose` サブエージェントで起動する。

**サブエージェントの責務（必須 6 点）**:

1. critical / major / minor の修正コミット
2. 修正テストの追加・実行
3. 修正対象の thread に **reply 投稿** + **`resolveReviewThread` で Resolve**
4. nit / 判断が割れる minor は **修正せず deferred 記録**（reply は `[deferred / nit]` ラベル付き、Resolve しない）
5. **PR レベルの Summary コメントを `gh pr comment` で投稿**（対応件数 / 重要度別 / deferred 件数 / rejected 件数 / commit SHA を含む）
6. 戻り値ファイル `$TMP_DIR/fix-pr<PR>-result.json` を必ず書き出す

> ⚠ inline thread への reply + Resolve **だけでは不十分**。PR ページの
> conversation タブに表示される **PR レベルコメント** がレビュアーへの
> サマリ通知として必須（`/ndf:fix` SKILL.md の手順 7 で規定）。
> サブエージェント起動プロンプトでも明示的に指示すること。

### サブエージェント起動例

```python
Agent(
    subagent_type="general-purpose",
    description=f"Fix PR #{PR} (round {ROUND})",
    prompt=f"""
/ndf:fix {PR} --defer-nit を実行してください。

**作業ディレクトリ厳守**: cd {WORKTREE_PATH} で作業すること。
別セッションが /work/<repo-root> 側で並行作業している可能性があり、
worktree 外を触ると競合します。

## コンテキスト
- リポジトリ: {OWNER_REPO}
- PR: #{PR} (round {ROUND_IN_PR}/{ROTATE_AFTER})
- worktree: {WORKTREE_PATH}
- ブランチ: {HEAD_BRANCH}
- ベース: {BASE_BRANCH}
- headRefOid: {HEAD_OID}
- 前ラウンドのレビュー結果:
  - codex review: {CODEX_REVIEW_URL}
    (intent={CODEX_INTENT}, posted_as={CODEX_POSTED_AS}, {CODEX_COMMENT_COUNT}件)
  - gemini review: {GEMINI_REVIEW_URL}
    (intent={GEMINI_INTENT}, posted_as={GEMINI_POSTED_AS}, {GEMINI_COMMENT_COUNT}件)
- 既存コメントスナップショット: $TMP_DIR/cross-review-pr{PR}-existing-comments.txt

## ポリシー
- 重要度ラベルは **AI agent の付与を鵜呑みにせず**、コードを読んで独自に再判定する
- critical / major は自動修正
- minor / nit のうち **パフォーマンス・可読性・重複コード排除** に該当するものは修正
  （特にトータル行数が減る方向は積極実施 / +30 行を超えそうなら deferred + ユーザ問い合わせ）
- それ以外の nit は deferred として記録のみ（修正しない、Resolve しない）
- bot 指摘が誤読していたら修正せず reply で説明（rejected として記録、Resolve しない）
- **重複指摘（codex/gemini が同じ箇所を別 thread で指摘）は全 thread に reply + Resolve**
- PR テスト範囲外の **flaky テストも見つけ次第このループで修正**（放置はリポジトリ品質を劣化させる）

## 必須実行手順（順序厳守）

1. PR コメント取得: `gh api "repos/{OWNER_REPO}/pulls/{PR}/comments" --paginate`
2. 重要度を独自再判定（AI agent のラベルは参考値）
3. CI 状態スナップショット: `gh pr checks {PR} --json name,state` （**完了待ちはしない**、PENDING は無視して FAILURE のみ修正対象に取り込む）
4. critical/major + 該当 minor/nit の修正コミット（worktree 内のみ）
5. `./pint-changed.sh && ./larastan-changed.sh` 等の品質チェック
6. push: `git push origin {HEAD_BRANCH}` （--force / --no-verify 禁止）
7. **CI 再実行は待たない**（push 後の `--watch` 等は行わない、`ci_status` は push 時点での既知失敗のみ反映）
8. **各 thread に reply 投稿**:
   - 修正済み: 「対応しました — <ファイル>:<行> で〇〇 (commit <SHA>)」
   - deferred: 「[deferred / nit] 後続 PR で対応予定」
   - rejected: 「bot 指摘は誤読です — 理由: ...」
9. **修正済み thread を `resolveReviewThread` で Resolve**:
   ```bash
   # thread_id は GraphQL で取得
   gh api graphql -f query='
     query {{ repository(owner:"...", name:"...") {{
       pullRequest(number: {PR}) {{ reviewThreads(first:100) {{
         nodes {{ id isResolved path line }}
       }} }}
     }} }}'
   # 修正済みのみ resolve
   gh api graphql -f query='
     mutation($id: ID!) {{
       resolveReviewThread(input: {{threadId: $id}}) {{ thread {{ isResolved }} }}
     }}' -f id="$THREAD_ID"
   ```
   - deferred / rejected の thread は **Resolve しない**
10. **PR レベル Summary コメントを投稿**（必須・inline reply とは別物）:
    ```bash
    gh pr comment {PR} --body "$(cat <<'EOMD'
    ## 🔧 /ndf:fix サマリ (round N)

    対応件数: critical=X / major=Y / minor=Z (合計 N 件)
    deferred: D 件 / rejected: R 件
    commit: <SHA>
    CI: SUCCESS | FAILURE | NONE

    ### 詳細
    - 各 thread の対応概要（行リンク付き）
    EOMD
    )"
    ```
    - inline reply + Resolve だけでは「PR ページの Conversation タブ」に
      まとめが出ず、レビュアー視点で見落とされる。**必ず投稿する**
11. 戻り値ファイル書き出し（下記フォーマット）。`summary_comment_url` には
    手順 10 の URL を入れる

## 戻り値ファイル $TMP_DIR/fix-pr{PR}-result.json

```json
{{
  "pr": {PR},
  "fix_commit": "abc1234",
  "ci_status": "SUCCESS" | "FAILURE" | "PENDING",
  "ci_failed_checks": [],
  "fixed_count": 6,
  "by_severity": {{"critical": 0, "major": 4, "minor": 2, "nit": 0}},
  "resolved_threads": [
    {{"thread_id": "PRRT_...", "comment_id": 123, "path": "...", "line": 42}}
  ],
  "deferred": [
    {{"thread_id": "...", "path": "...", "line": 31, "severity": "nit",
      "summary": "...", "comment_url": "..."}}
  ],
  "rejected": [
    {{"thread_id": "...", "summary": "...", "reason_for_rejection": "..."}}
  ]
}}
```
""",
)
```

### Step 5 後段: fix 戻り値マージ + CI 分類

```bash
if "$SCRIPTS/state.py" merge-fix "$STATE_PR"; then
  : # exit 0 = continue
elif [ $? -eq 3 ]; then
  exit 3  # final=error（コード関連 CI 失敗 or fix 戻り値ファイル欠落）
fi
```

`state.py merge-fix` が内部で行う処理:

1. `$TMP_DIR/fix-pr<PR>-result.json` を読んで `state.rounds[-1].fix` にマージ
2. `deferred` を `state.deferred_nits` に追記
3. **CI 失敗の分類**:
   - code-fail (`pint` / `larastan` / `phpstan` / `test` / `lint` / `type` / `build` / `ruff` / `eslint` / `tsc` / `mypy`): `final=error` で中断 (exit 3)
   - meta-only (`check_pr_requirements` / `assignees` / `reviewers` / `labels` / `meta`): `ci_note` に記録して継続
   - 不明: 保守的に code-fail 扱い

**例**: `check_pr_requirements`（Assignees 未設定）はループ継続、
`laravel/pint` や `phpstan` の失敗は即中断してユーザ判断。

## Step 6: PR ローテーション判定

```bash
if "$SCRIPTS/state.py" should-rotate "$STATE_PR"; then
  eval "$("$SCRIPTS/rotate-pr.sh" "$STATE_PR")"   # NEW_PR / NEW_PR_URL / NEW_BRANCH を取り込む
  "$SCRIPTS/state.py" set-current-pr "$STATE_PR" "$NEW_PR"
  # NOTE: STATE_PR は **絶対に変えない**。次ループの scripts も $STATE_PR で呼ぶ。
fi
```

`should-rotate` は `round_in_pr >= rotate_after && total_rounds < max_rounds` で
exit 0 を返す（rotate 要）。それ以外は exit 2（keep）。

`rotate-pr.sh` が内部で行う処理:

1. state.json から `current_pr` (= 旧 PR) と `worktree_path` を読む
2. 既存ブランチを **squash 統合** した新ブランチ作成
3. 旧 PR に「ローテーションのため close」コメント + close
4. 新 PR 作成（タイトル末尾に `(rotated)` 付与）
5. 新 PR 番号 / URL / ブランチ名を stdout に KEY=VALUE で吐く

`state.py set-current-pr` が `state.json` の `current_pr` / `pr_history` を更新。

> ⚠ **重要**: state.json のファイル名は **最初に init した PR 番号** がキー
> (`$STATE_PR`)。rotation 後も全 scripts の **第 1 引数には常に `$STATE_PR`** を渡す。
> 内部的に `state.json.current_pr` を読んで「現在の PR」を解決する設計。
> `PR=$NEW_PR` 等で shell 変数の側を切り替えると、次ループの `state.py start-round`
> が `$TMP_DIR/cross-review-pr<NEW_PR>-state.json` を探して `state.json not found` で
> 止まる。

## Step 7: 次ラウンドへ

Step 1 に戻る。

## Step 8: 終了処理 — deferred nit のバッチ問い合わせ

ループ終了時（`final` 確定後）、ラウンドサマリと残 deferred nit を表示:

```bash
"$SCRIPTS/state.py" report "$STATE_PR"
```

`report` は以下を Markdown で吐く:

- 最終ステータス（`approved` / `max_rounds` / `oscillation` / `error`）
- PR 履歴
- ラウンドサマリ表
- 残 deferred nit 一覧

UI 上は **AskUserQuestion で 1 回だけ** 「nit 一括対応する / しない /
個別選択」を選ばせるのが望ましい。
