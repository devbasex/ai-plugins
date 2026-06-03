# 02: 修正 (Step 5) + PR ローテーション (Step 6) + 終了処理 (Step 8)

主要処理は `scripts/` 配下に切り出し済み:

| script | 役割 |
|---|---|
| (Agent) | Step 5 — 修正サブエージェント起動（メインからの責務） |
| `scripts/state.py merge-fix` | Step 5 後段 — fix 戻り値マージ + CI 分類 |
| `scripts/state.py should-rotate` | Step 6 — rotate 要否判定 |
| `scripts/rotate-pr.sh prepare` | Step 6a — 旧 PR の素材を `rotate-pr<STATE_PR>-prepare.json` に dump |
| (Agent) | Step 6b — light モードのみ。新 PR の title/body を再生成して `rotate-pr<STATE_PR>-newtext.json` に書き出し |
| `scripts/rotate-pr.sh execute` | Step 6c — 旧 PR close + 新 PR 作成 (light は同ブランチ / squash は新ブランチ) |
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
   （`$TMP_DIR` は env `CROSS_REVIEW_TMP_DIR` > `<worktree>/.cross_review/` の順で解決。
   詳細は `scripts/state.py _tmp_dir()` 参照。`/tmp/` 直書きでも `state.py merge-fix` は legacy fallback で拾う）

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

1. PR コメント取得 (3 ソース): `fix/scripts/fetch-pr-comments.sh {OWNER_REPO} {PR}` でインラインコメント / レビュー body / PR レベルコメントを一括取得
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

## Step 6: PR ローテーション (prepare → Agent → execute の 3 段)

`rotate-pr.sh` は **light モード (default) と squash モード (opt-in)** を持つ。
両者ともメインからは `prepare → (light のみ Agent) → execute` の 3 段で呼ぶ。

```bash
if "$SCRIPTS/state.py" should-rotate "$STATE_PR"; then
  # Step 6a: 旧 PR の素材 dump (title / body / isDraft / git log / git diff --stat)
  eval "$("$SCRIPTS/rotate-pr.sh" prepare "$STATE_PR")"

  # Step 6b: light モードのみ。Agent(subagent_type="general-purpose") で
  # 現状の差分・実装を反映した新 title/body を生成し、
  # $TMP_DIR/rotate-pr<STATE_PR>-newtext.json に書き出させる。
  # (squash モードでは Step 6b は不要)

  # Step 6c: 実行 (NEW_PR / NEW_PR_URL / NEW_BRANCH を取り込む)
  eval "$("$SCRIPTS/rotate-pr.sh" execute "$STATE_PR" --mode "$ROTATE_MODE")"

  "$SCRIPTS/state.py" set-current-pr "$STATE_PR" "$NEW_PR"
  # NOTE: STATE_PR は **絶対に変えない**。次ループの scripts も $STATE_PR で呼ぶ。
fi
```

`should-rotate` は `round_in_pr >= rotate_after && total_rounds < max_rounds` で
exit 0 を返す（rotate 要）。それ以外は exit 2（keep）。

### Step 6a: `rotate-pr.sh prepare <STATE_PR>`

state.json から旧 PR / worktree を解決し、以下の素材を 1 つの JSON に dump する:

```json
{
  "state_pr": 217,
  "old_pr": 217,
  "old_pr_url": "https://github.com/.../pull/217",
  "worktree_path": "/work/worktrees/pr217",
  "head_branch": "feature/...",
  "base_branch": "release/...",
  "is_draft": true,
  "round_in_pr": 5,
  "old_title": "...",
  "old_body": "...",
  "git_log": "abc1234 メッセージ\n...",
  "git_diff_stat": " path/to/file | 12 +-\n ..."
}
```

ファイル: `$TMP_DIR/rotate-pr<STATE_PR>-prepare.json`。
stdout にも `OLD_PR=` / `HEAD_BRANCH=` / `BASE_BRANCH=` / `IS_DRAFT=` / `PREPARE_JSON=` を出すので
`eval` で取り込める。

### Step 6b: Agent (general-purpose) で新 title/body を生成 (light モードのみ)

メインセッションから以下のように Agent を起動する。プロンプトは
**書いて良いこと / 禁止事項** を必ず明示する
(外側の prompt フェンスは内側に ```json を含むため 4 連バッククォートで囲む):

````python
Agent(
    subagent_type="general-purpose",
    description=f"Generate light-rotation PR text for PR #{OLD_PR}",
    prompt=f"""
PR rotation の light モードで作成する新 PR の title / body を生成してください。

## 素材
- prepare.json: $TMP_DIR/rotate-pr{STATE_PR}-prepare.json
  - 元 PR の title / body / git log $BASE..HEAD / git diff --stat
- 必要なら worktree 内のファイルを直接読んで実装内容を確認してよい
  (worktree: {WORKTREE_PATH})

## 出力ファイル
$TMP_DIR/rotate-pr{STATE_PR}-newtext.json に JSON で書き出してください:

```json
{{
  "title": "新 PR の title (元 PR の title をそのままコピーしない。現状の実装を反映)",
  "body":  "新 PR の body (Markdown)。以下のセクションを含む:\\n## 何のために\\n## 何を\\n## Test plan"
}}
```

## 書いて **良い** こと
- 何のために (背景・動機) — 元 PR の背景セクションは再利用可
- 何を (変更内容) — 現在のブランチの実態を git log / git diff から反映
- Test plan — 元 PR から継承可

## 書いて **はいけない** こと (内部用語の漏洩防止)
- 「round N で〜」「cross-review で〜」「レビュー指摘で〜」
- 「(rotated)」のような automated suffix
- 「fix された問題」の列挙 / レビューサイクルの存在自体への言及
- 「旧 PR」「巻き直し」等の rotation 内部用語

PR を読む人は cross-review の存在を意識しないため、最終 PR を初めて見る読者向けに
書く。元 title / body をそのままコピーするのは **禁止** (現状の実装を反映)。
""",
)
````

> ⚠ `rotate-pr.sh` 内部から `claude` / `codex` / `gemini` CLI を直接呼んで生成
> させてはならない (環境依存・コスト管理外)。**メイン側の Agent tool で行う**。

### Step 6c: `rotate-pr.sh execute <STATE_PR> --mode light|squash`

`--mode` で実際の rotation を実行する:

| mode | 振る舞い |
|---|---|
| `light` (default) | prepare.json と newtext.json を読み、**同ブランチ・同 base** で旧 PR を close → 新 PR を作成。`is_draft=true` なら新 PR も Draft で作る。title/body は newtext から流す |
| `squash` (opt-in) | `<branch>-rHHMMSS` の新ブランチを作って `git reset --soft origin/$BASE` で squash 統合 → 旧 PR close → 新 PR (`(rotated)` suffix + automated body) |

stdout には両モードとも以下を KEY=VALUE で出す:

- `NEW_PR=<number>`
- `NEW_PR_URL=<url>`
- `NEW_BRANCH=<branch>`  (light モードでは元ブランチと同じ)

`state.py set-current-pr` が `state.json` の `current_pr` / `pr_history` を更新する。
state.json の **キーは元 PR 番号 (STATE_PR) のまま** なので、light/squash どちらでも
後続スクリプトへの第 1 引数は `$STATE_PR` を渡し続ければよい。

### 後方互換: 旧 1 引数形式

`rotate-pr.sh <STATE_PR>` (引数 1 つ) は `execute --mode squash` 相当として動くが、
stderr に deprecation warning を出す。新規呼び出しは必ず prepare → execute 形式へ移行。

> ⚠ **重要**: state.json のファイル名は **最初に init した PR 番号** がキー
> (`$STATE_PR`)。rotation 後も全 scripts の **第 1 引数には常に `$STATE_PR`** を渡す。
> 内部的に `state.json.current_pr` を読んで「現在の PR」を解決する設計。
> `PR=$NEW_PR` 等で shell 変数の側を切り替えると、次ループの `state.py start-round`
> が `$TMP_DIR/cross-review-pr<NEW_PR>-state.json` を探して `state.json not found` で
> 止まる。

## Step 7: 次ラウンドへ

Step 1 に戻る。

## Step 7.5: 最終スイープ（必須）— 取りこぼし防止

**目的**: ループを抜けた時点で PR 上に **未解決 (open) のレビュースレッドを 1 件も残さない**。

### なぜ必要か

ループ内の修正フェーズ (Step 5) は「一方でも REQUEST_CHANGES」のラウンドでしか走らない。
そのため以下が取りこぼされる:

- **最終 APPROVE ラウンドのインラインコメント**: skill のレビュー方針上、`minor` 以下しか
  無ければ `APPROVE` で良い。つまり **APPROVE でも minor/nit のインラインコメントが
  投稿されている**ことがあり、両者 APPROVE でループを抜けるとこれらが未対応のまま残る。
- **ループ中に deferred 記録した nit**: `state.deferred_nits` に積まれたまま reply のみで
  Resolve されていないスレッド。

これらを放置すると、PR レビュー画面に「Unresolved」スレッドが残り、人間のレビュアーや
後続作業者が「未対応の指摘がある」と誤認する。

### 実行（メインが Agent を駆動）

ループを抜けたら（`final` がどの値でも）、**メインが `Agent(subagent_type="general-purpose")`
を起動**し、`/ndf:fix <current_pr>` を再実行させる。bash 単体では Agent を呼べないため、
while ループ脱出後にメインが以下のプロンプトでサブエージェントを起動する。

> **対象 PR**: `<current_pr>`（state.json の `current_pr`。rotation していれば最新 PR）
> **worktree**: state.json の `worktree_path`
>
> PR の **全 open review thread**（インライン / レビュー body / PR レベルコメント）を
> `gh api` で洗い出し、cross-review の codex/gemini が残したものを中心に **すべて解消**せよ:
> 1. 修正可能な `minor`/`nit` → コード修正 + push（同ブランチ、main へは push しない）し、
>    reply + GraphQL `resolveReviewThread` で Resolve。
> 2. 修正しない（好み・判断保留）`nit` → 「[deferred / nit] 対応見送り: <理由>」を日本語で
>    reply した上で **Resolve まで実行**（スレッドを open のまま残さない）。
> 3. bot 誤指摘 → 却下理由を reply して Resolve。
> 修正で push した場合は `claude plugin validate` を通すこと。
> 完了後、`$TMP_DIR/sweep-pr<PR>-result.json` に
> `{"resolved": N, "fixed_in_sweep": M, "commit": "<SHA|null>", "remaining_open": K,
>   "items": ["<1行要約>", ...]}` を書き出し、最終メッセージで内訳を日本語報告せよ。
> **`remaining_open` は 0 を目標**とし、0 にできない場合は理由を明記すること。

> ⚠ 最終スイープは「修正の追加」ではなく **後始末**。新しい設計変更や大きな
> リファクタは行わない（行う必要があれば deferred として report に残す）。
> push が走った場合でも、それに対する再レビューはループ終了後のため行わない
> （次回 cross-review か通常レビューに委ねる）。

### 再開性

sweep 中にメインが落ちても、`sweep-pr<PR>-result.json` が無ければ Step 7.5 から
再実行すれば良い（Resolve は冪等。既 Resolve スレッドは skip される）。

## Step 8: 終了処理 — ラウンドサマリ + 残 deferred の参考列挙

最終スイープ (Step 7.5) 完了後、ラウンドサマリを表示:

```bash
"$SCRIPTS/state.py" report "$STATE_PR"
```

`report` は以下を Markdown で吐く:

- 最終ステータス（`approved` / `max_rounds` / `oscillation` / `error`）
- PR 履歴
- ラウンドサマリ表
- 残 deferred nit 一覧

メインは `report` の出力に **Step 7.5 の sweep 結果**（`sweep-pr<PR>-result.json` の
`resolved` / `fixed_in_sweep` / `remaining_open`）を折り込んで最終報告する。

> **方針変更（v4.11.0）**: 従来は deferred nit を「AskUserQuestion で 1 回問い合わせ」て
> いたが、未解決スレッドを残さない方針に変更。**Step 7.5 で nit も含め全 open thread を
> Resolve する**ため、Step 8 のユーザ問い合わせは原則不要。deferred nit は「対応見送りの
> 記録」として report に **参考列挙**するに留める（再対応が要るものがあればユーザが
> その場で指示できる）。`remaining_open > 0` の場合のみ、残った理由を添えて報告する。
