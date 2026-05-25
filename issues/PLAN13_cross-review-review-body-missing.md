# PLAN13: cross-review — PR review body の指摘見落とし修正

- 起票日: 2026-05-25
- 対象 plugin: `ndf` v4.7.5
- 対象 skill: `ndf:cross-review`, `ndf:fix`, `ndf:review-pr-comments`
- 関連 issue: [GitHub Issue #13](https://github.com/devbasex/ai-plugins/issues/13)
- 報告者: takemi-ohama
- 実際のケース: [carmo-system-console PR #14137](https://github.com/volareinc/carmo-system-console/pull/14137) で人間レビュアーの `CHANGES_REQUESTED` review body 指摘が cross-review 6 ラウンド通じて検出されなかった

## 背景・課題

### 現状

PR コメント取得時に **インラインコメント (`pulls/{pr}/comments`)** のみを取得している。GitHub の PR コメントは 3 つのソースに分かれるが、うち 2 つが見落とされている:

| ソース | API | 取得状況 | 内容 |
|---|---|---|---|
| インラインコメント | `pulls/{pr}/comments` | ✅ 取得済み | diff の特定行に紐づくコメント |
| レビュー body | `pulls/{pr}/reviews` の `body` フィールド | ❌ **未取得** | レビュー投稿時の総評テキスト |
| PR レベルコメント | `issues/{pr}/comments` | ❌ **未取得** | Conversation タブの通常コメント |

### 影響

- 人間レビュアーが review body にのみ指摘を書いた場合、cross-review ループ全体で検出されない
- `/ndf:fix` が review body の指摘を修正対象として認識しない
- `/ndf:review-pr-comments` が review body/PR レベルコメントを分類対象に含めない

### 影響箇所

| ファイル | 修正内容 |
|---|---|
| `plugins/ndf/skills/fix/scripts/fetch-pr-comments.sh` | **新規作成** — 3 ソース一括取得の共有スクリプト |
| `plugins/ndf/skills/cross-review/scripts/state.py` (L306-324) | 既存の `gh api` 直接呼び出しを `fetch-pr-comments.sh` 呼び出しに差し替え |
| `plugins/ndf/skills/fix/SKILL.md` (L184) | スクリプト参照と使い方を追記 |
| `plugins/ndf/skills/review-pr-comments/SKILL.md` (L49) | コメント取得を共有スクリプト参照に変更 |
| `plugins/ndf/skills/cross-review/SKILL.md` (L87) | Step 4 の説明を共有スクリプト経由に更新 |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` (L98) | 既存コメント差分の説明を共有スクリプト参照に更新 |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` (L76) | fix prompt 内のコメント取得手順を共有スクリプト参照に更新 |

## 修正方針

### 設計方針: コメント取得の共有スクリプト化

3 つのスキル (cross-review, fix, review-pr-comments) が同じ 3 ソースの `gh api` 呼び出しを必要とするため、共有スクリプトに切り出す。

**配置場所**: `plugins/ndf/skills/fix/scripts/fetch-pr-comments.sh`

cross-review は既に fix をサブエージェント経由で呼ぶ依存関係にあるため、fix 側にスクリプトを置けば依存方向が一致する。review-pr-comments も fix の前段（分類→修正）の関係。

### 1. `fix/scripts/fetch-pr-comments.sh` — 共有スクリプト新規作成 (コア)

3 ソースを一括取得し、タグ付き行単位で stdout に出力するシェルスクリプト。

```bash
#!/usr/bin/env bash
# Usage: fetch-pr-comments.sh <owner/repo> <pr_number>
set -uo pipefail  # -e は意図的に外す (0件ソースで後続が止まるのを防止)

REPO="$1"
PR="$2"

# 1. インラインコメント (diff の特定行に紐づく)
gh api "repos/${REPO}/pulls/${PR}/comments" --paginate --jq \
  '.[] | "\(.path // "?"):\(.line // .original_line // "?") [\(.user.login)] \(.body // "" | split("\n")[0])"' \
  || true

# 2. レビュー body (CHANGES_REQUESTED / COMMENTED 等の総評)
gh api "repos/${REPO}/pulls/${PR}/reviews" --paginate --jq \
  '.[] | select(.body != null and .body != "") | "[REVIEW-BODY] [\(.user.login)] state=\(.state) \(.body | split("\n")[0])"' \
  || true

# 3. PR レベルコメント (Conversation タブの通常コメント)
gh api "repos/${REPO}/issues/${PR}/comments" --paginate --jq \
  '.[] | "[PR-COMMENT] [\(.user.login)] \(.body // "" | split("\n")[0])"' \
  || true
```

出力フォーマット:
- インラインコメント: `path:line [user] body`
- review body: `[REVIEW-BODY] [user] state=CHANGES_REQUESTED body`
- PR コメント: `[PR-COMMENT] [user] body`

### 2. `state.py` — 既存コメント収集を共有スクリプト呼び出しに差し替え

`init()` 内の L306-324 (インラインコメント取得 + ファイル書き出し) を `fetch-pr-comments.sh` の呼び出しに置き換え:

```python
fetch_script = Path(__file__).resolve().parent.parent.parent / "fix" / "scripts" / "fetch-pr-comments.sh"
r = subprocess.run(
    [str(fetch_script), repo, str(pr)],
    capture_output=True, text=True,
)
existing_path = tmp_dir / f"cross-review-pr{pr}-existing-comments.txt"
if r.returncode == 0:
    existing_path.write_text(r.stdout)
else:
    info(f"⚠ 既存コメント取得失敗: {r.stderr.strip()[:200]}")
    existing_path.write_text("")
```

既存の `jq_filter` 変数と `subprocess.run(["gh", "api", ...])` ブロックは削除。

### 3. `fix/SKILL.md` — スクリプト参照とコマンド例の更新

gh コマンド例セクションに `fetch-pr-comments.sh` の使い方を追記:

```markdown
### PR コメント一括取得 (3 ソース)

```bash
# 共有スクリプトで全ソース一括取得
"$(dirname "$0")/scripts/fetch-pr-comments.sh" <owner/repo> <pr_number>
```

### 4. `review-pr-comments/SKILL.md` — コメント取得セクション更新

Step 2 のコメント取得で `fix/scripts/fetch-pr-comments.sh` を参照:

```markdown
### 2. PRコメント取得

fix skill の共有スクリプトで 3 ソース (インラインコメント / レビュー body / PR レベルコメント) を一括取得:

```bash
PLUGIN_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
"$PLUGIN_DIR/skills/fix/scripts/fetch-pr-comments.sh" "$REPO" "$PR_NUMBER"
```

### 5. ドキュメント整合

| ファイル | 更新内容 |
|---|---|
| `cross-review/SKILL.md` (L87) | Step 4 の説明を「3 ソース (fetch-pr-comments.sh 経由)」に更新 |
| `cross-review/docs/01-state-and-review.md` (L98) | 既存コメント差分の説明を共有スクリプト参照に更新 |
| `cross-review/docs/02-fix-and-rotation.md` (L76) | fix prompt 内のコメント取得手順を共有スクリプト参照に更新 |

## 単一 PR 判定

- 変更ファイル: 7 ファイル (新規 1 + 既存 6)
- 差分: 推定 80-120 行 (共有スクリプト化で各ファイルの変更量は減少)
- すべて同一目的 (コメント取得ソースの拡張 + 共有スクリプト化) で結合度が高い
- 依存関係のある複数タスクなし

→ **単一 PR で対応**。release ブランチ不要。

## テスト計画

- [ ] `state.py` の変更後、実 PR に対して `state.py init` を実行し、`existing-comments.txt` に 3 ソースの内容が含まれることを確認
- [ ] review body にのみ指摘がある PR で `/ndf:cross-review` を実行し、指摘が検出されることを確認
- [ ] `claude plugin validate` が通ることを確認
