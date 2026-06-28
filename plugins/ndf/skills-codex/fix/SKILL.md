---
name: fix
description: "Fix actionable PR review comments."
when_to_use: "PRレビューコメント (codex/gemini/人間) の指摘を実際にコード修正で対応したいとき。review-pr-comments で分類した後の修正フェーズに使う。Triggers: 'PRコメント対応', 'PRレビュー修正', 'PR fix', 'review feedback fix', 'コメントに対応して修正'"
argument-hint: "[PR番号] [--defer-nit] [--severity-min critical|major|minor]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# PR修正コマンド

直前PR、または引数で指定されたPRのreview comment確認・修正対応実行。

## 起動モード

このスキルは **メインセッション直接実行** と **サブエージェント (`general-purpose`) 起動** の両方に対応する。
長丁場のクロスレビューループ（`/ndf:cross-review`）からは **必ずサブエージェント経由で起動** されることを想定:

```python
# メインからの起動例（cross-review が内部でこれを行う）
Agent(
    subagent_type="general-purpose",
    description="Fix PR review comments (sub-agent)",
    prompt="""
/ndf:fix <PR番号> --defer-nit を実行してください。

PR: <PR番号>
リポジトリ: <owner/repo>
重要度ポリシー: critical/major/minor は修正、nit は deferred として残す
完了後の戻り値: 件数サマリ + 修正コミット SHA + 残 nit リスト
"""
)
```

サブエージェント側ではこの SKILL.md を読み込んで、自己完結で
**修正 → コミット → push → reply → Resolve Conversation** まで実行する。
メインへの戻り値は最小限のサマリのみ。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | 対象 PR | 直前 PR |
| `--defer-nit` | nit 指摘は修正せず deferred としてリスト出力 | OFF |
| `--severity-min LEVEL` | 指定重要度未満は無視（`critical` / `major` / `minor`） | `minor` (= minor 以上を修正) |

## 重要度ベースの自動修正ポリシー

`[重要度 / カテゴリ]` プレフィックス（`/ndf:review` 出力規約）で分類。
**ただし重要度ラベルを鵜呑みにしない** — 各指摘ごとにコード/仕様を独自に調査し、
本来の重要度を判定し直してから下表の動作を適用する（bot のラベリングは参考値に過ぎない）。

| 重要度 | 動作 | ユーザ問い合わせ |
|---|---|---|
| `critical` | **必ず自動修正** | なし |
| `major` | **必ず自動修正** | なし |
| `minor` / `nit` (パフォーマンス・可読性・重複コード排除) | **このPRで修正対応**。特にトータル行数が減る方向の修正は積極的に実施 | なし |
| `minor` / `nit` (上記カテゴリ、修正範囲が +30 行を超えそう) | ユーザ問い合わせ | あり |
| `minor` (その他) | 自動修正（明らかな改善のみ）。判断が割れるなら `nit` として deferred 扱い | なし |
| `nit` (その他) | `--defer-nit` 指定時は **修正せず deferred リスト** に追加。最後にまとめてユーザ問い合わせ | あり（最後に1回） |

**重要度の独自判定**:
- AI agent (CodeRabbit / Copilot 等) が `nit` と付けていても、実体がパフォーマンス改善や重複排除なら **minor/nit カテゴリ修正対象** として扱う
- 逆に AI agent が `critical` と付けていても、実害がないスタイル指摘なら `nit` 相当に格下げして deferred 化してよい
- 重要度はカテゴリ（performance/readability/duplication/security/style/etc）と合わせて、コード本体を読んだ上で判定する

**指摘の正否判断**:
- ロジック・仕様逸脱・セキュリティ: コード/仕様を確認してから修正可否判断
- bot 指摘で **明らかに誤読** している場合（例: 意図的な変数展開を「クオート不足」と指摘する等）: 修正しない、reply で理由説明
- 仕様判断が必要な指摘（API 変更、互換性破壊など）: ユーザ問い合わせ対象（critical でもエスカレーション）

**自動判断できない場合の取り扱い** （context 節約のため安易に user に投げない）:
- 仕様文書（docs/, README）を読んで判断する
- 既存テストを読んで挙動を確認する
- 関連する他コードの慣例を確認する
- それでも不明なら deferred リストに「要ユーザ判断」として記録、最後にまとめて問い合わせ

## 手順

1. review comment取得 + 重要度を**独自に再判定**（AI agent のラベルは参考値）
2. **CIエラー確認**（`gh pr checks <PR>` で **現時点の** 失敗ジョブを検出）
   - **完了待ちはしない**。実行中(PENDING/IN_PROGRESS)のチェックは無視して次ステップへ進む
   - 直近で失敗(FAILURE)状態のジョブのみを修正対象に取り込む
3. 修正対象を確定:
   - `critical` / `major` → 全件修正対象
   - `minor` / `nit` (パフォーマンス・可読性・重複排除) → 修正対象。+30行超なら **deferred + ユーザ問い合わせ**
   - `minor` (その他) → 修正対象（明らかでないものは `deferred[]` へ）
   - `nit` (その他、`--defer-nit` 時) → `deferred[]` のみ、修正しない
   - CIエラー → 全件修正対象（PRテスト範囲外の **flaky テストも見つけ次第修正**）
4. 問題点修正
   - **コード行数が減る方向の修正は積極的に実施**（重複排除、不要分岐除去 等）
5. **コミット前の再確認**（修正作業中に状況が変わっている可能性への対応）
   - **review comment再取得**: 作業中に新しいコメントが追加されていないか確認
   - **CI状態再確認**: 現時点の状態だけ確認（完了待ちはしない）。新しい失敗が出ていれば対象に取り込む
   - 新しい指摘/失敗があれば手順3に戻る
6. コミット・プッシュ
7. PRにSummaryコメントを追加（対応した件数 + deferred 件数を明記）
8. 対応したインラインコメントに個別に返信
9. **deferred スレッドには `[deferred / nit]` のラベル付き返信** を投稿（resolve はしない）
10. reviewerに再レビューを依頼
11. 対応完了したインラインコメントを「Resolve Conversation」にする（`resolveReviewThread` mutation）
    - resolve した thread_id / comment_id / path / line を `resolved_threads[]` に記録
    - `deferred` / `rejected` の thread は Resolve しない（次ラウンドで再評価するため）
12. **戻り値ファイルを書き出す**: `/tmp/fix-pr<番号>-result.json` （後述「戻り値フォーマット」参照）
    - `ci_failed_checks` には `gh pr checks <PR> --json name,state` から `state=FAILURE` の name を抽出して列挙
    - push 直後の CI 再実行結果は**待たない**ため、戻り値の `ci_status` は push 時点での既知失敗のみを反映する

- 4〜6はgit、1〜2/5と7以降はgithub mcpまたはghを利用

**flakyテストの扱い**: PR の変更範囲外で発生している flaky テストも、見つけ次第このPRで修正する。
flaky を放置するとリポジトリ全体のコード品質が下がり、後続 PR の CI 信頼性も損なわれるため。

## CIエラーチェック

### 失敗ジョブの検出

```bash
# PRの全チェック状態を確認（FAIL/PASS/PENDING）
gh pr checks <PR番号>

# JSON形式で詳細取得
gh pr checks <PR番号> --json name,state,link,completedAt

# 失敗ジョブのみ抽出
gh pr checks <PR番号> --json name,state | \
  python3 -c "import json,sys; [print(c['name']) for c in json.load(sys.stdin) if c['state']=='FAILURE']"

# 実行中ジョブのみ抽出（状態スナップショット用。完了は待たない）
gh pr checks <PR番号> --json name,state | \
  python3 -c "import json,sys; [print(c['name']) for c in json.load(sys.stdin) if c['state'] in ('PENDING','IN_PROGRESS','QUEUED')]"
```

### CI完了待ちはしない

このスキルでは **CI 完了待ちは行わない**（`gh pr checks --watch` 等は使わない）。
- 各チェックポイントでは「現時点で FAILURE のジョブ」のみを取り込んで修正する
- push 後の CI 再実行結果も待たない（待機中に context を消費しないため）
- ただし `gh pr checks <PR> --json name,state` での **状態スナップショット取得は実施**
  し、戻り値の `ci_status` / `ci_failed_checks` に反映する

### 失敗ログの取得

```bash
# ワークフロー実行ID取得
RUN_ID=$(gh run list --branch <branch-name> --limit 1 --json databaseId --jq '.[0].databaseId // empty')
[ -z "$RUN_ID" ] && { echo "No CI run found for this branch"; exit 0; }

# 失敗ステップのログだけ表示（効率的）
gh run view $RUN_ID --log-failed

# 特定ジョブのログ
gh run view $RUN_ID --job <job-id> --log
```

### CIエラーの分類と対応方針

| エラー種別 | 対応方針 |
|---|---|
| **lint/format** | 自動修正ツール実行（`ruff`, `prettier`, `eslint --fix` 等）→ コミット |
| **型チェック** | 型定義・アノテーションを修正。無視コメントは原則禁止（根本対応） |
| **テスト失敗** | 失敗テストを読み、実装/テストどちらが正しいか判断してから修正。テスト側の問題なら仕様確認 |
| **ビルドエラー** | 依存関係・構文・設定ファイルを確認 |
| **依存脆弱性** | 可能ならバージョン更新、無理なら除外ルール追加（理由明記） |
| **タイムアウト/flaky** | retry設定、テスト分割、リトライ追加。**PR範囲外の flaky も見つけ次第修正**（放置でリポジトリ全体の品質劣化を招くため） |
| **インフラ一時障害** | 再実行で解消することがあるため `gh run rerun $RUN_ID` を先に試す |

### review指摘との統合

review指摘とCIエラーは**同じPRで一緒に修正**する:
- 同じファイル・機能に関する指摘とCIエラーは1コミットにまとめる
- 独立しているなら別コミットに分離（git log で追いやすい）

## ghコマンド例

### PR コメント一括取得 (3 ソース)

```bash
# インラインコメント / レビュー body / PR レベルコメントを一括取得
FETCH_SCRIPT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}}/skills-codex/fix/scripts/fetch-pr-comments.sh"
"$FETCH_SCRIPT" <owner/repo> <pr_number>
```

### コメントへの返信

```bash
# PRのレビューコメント一覧を取得 (インラインコメントのみ)
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments

# 特定のコメントに返信（in_reply_to にコメントIDを指定）
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \
  -f body="修正しました。" \
  -F in_reply_to={comment_id}
```

### Resolve Conversation

```bash
# GraphQL APIでスレッドをresolveする
gh api graphql -f query='
  mutation {
    resolveReviewThread(input: {threadId: "{thread_node_id}"}) {
      thread { isResolved }
    }
  }
'
```

### thread_node_idの取得方法

```bash
# PRのレビュースレッド一覧を取得（node_id含む）
gh api graphql -f query='
  query {
    repository(owner: "{owner}", name: "{repo}") {
      pullRequest(number: {pr_number}) {
        reviewThreads(first: 100) {
          nodes {
            id
            isResolved
            comments(first: 1) {
              nodes { body }
            }
          }
        }
      }
    }
  }
'
```

**方針**:
- 品質・可読性・セキュリティ向上、既存機能影響なし
- 指摘がすべて正しいとは限らない。修正前に仕様を調査し、実施の可否を判断すること
- 未対応の場合はその理由をコメントに書き込む

## 戻り値フォーマット（必須）

サブエージェント呼び出し時の context 節約のため、**実行結果は `/tmp/fix-pr<番号>-result.json` に書き出す**:

```json
{
  "pr": 67,
  "fix_commit": "abc1234",
  "ci_status": "SUCCESS" | "FAILURE" | "PENDING" | "NONE",
  "ci_failed_checks": [],
  "ci_note": null,
  "fixed_count": 5,
  "by_severity": {"critical": 1, "major": 2, "minor": 2, "nit": 0},
  "resolved_threads": [
    {
      "thread_id": "PRRT_...",
      "comment_id": 3222849090,
      "path": "src/foo.py",
      "line": 42
    }
  ],
  "deferred": [
    {
      "comment_id": 3222849090,
      "thread_id": "PRRT_...",
      "path": "src/foo.py",
      "line": 42,
      "severity": "nit",
      "category": "style",
      "summary": "末尾セミコロンの有無",
      "reason_for_deferral": "好みの範囲。プロジェクト規約と齟齬なし"
    }
  ],
  "rejected": [
    {
      "comment_id": 3222849090,
      "summary": "heredoc を <<'JSON' にせよ",
      "reason_for_rejection": "$SHA を意図的に展開する必要があり、クオート化すると逆に壊れる"
    }
  ],
  "summary_comment_url": "https://github.com/.../pull/67#issuecomment-..."
}
```

**フィールド説明**:

- `ci_failed_checks` — `ci_status = FAILURE` のとき、失敗した check 名の配列。`/ndf:cross-review` 側で code-related (`pint/larastan/test/build/lint/type`) と meta-only (`check_pr_requirements/assignees/reviewers/labels`) を分類し、メタチェックのみ失敗ならループ継続する
- `ci_note` — code-related ではない CI 失敗の補足。例: `"メタチェックのみ失敗: check_pr_requirements — Assignees 未設定"`
- `resolved_threads` — 手順 11 で `resolveReviewThread` mutation を実行したスレッド一覧。`deferred` / `rejected` の thread は **Resolve しない**（再評価のため）

サブエージェントとして起動された場合は、この JSON をメインに返すサマリの基礎とする。

## 作業完了報告（必須）

メイン or PR への報告内容（戻り値ファイルから抽出）:
- 対応した指摘の件数（重要度別: critical/major/minor/nit）
- **deferred 件数**（主に nit、最後にユーザ問い合わせ予定）
- **rejected 件数**（bot 指摘が不適切で修正しなかった件、各々理由付き）
- **対応したCIエラーの一覧**（ジョブ名、エラー内容、修正方法）
- **対応した flaky テストの一覧**（PR範囲外も含む）
- 修正コミット SHA / 修正ファイル一覧
- 戻り値ファイルパス: `/tmp/fix-pr<番号>-result.json`
- **PR URL を最後に必ず記載**（例: `https://github.com/<owner>/<repo>/pull/<番号>`）
