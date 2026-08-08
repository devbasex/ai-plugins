---
name: fix
description: "Classify PR review comments, fix the actionable ones, then reply and resolve each thread. Use when responding to review feedback from codex, gemini, bots, or humans on a PR. Triggers: 'PRコメント対応', 'PRレビュー修正', 'PRコメントを分類', 'コメントに対応して修正', 'Resolveして'"
argument-hint: "[PR番号] [--classify-only] [--defer-nit] [--severity-min critical|major|minor]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# PR コメント対応コマンド

指定 PR（省略時は直前 PR）のレビューコメントを **分類 → 修正 → 返信 → Resolve** まで
一貫して処理する。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | 対象 PR | 直前 PR |
| `--classify-only` | **分類・優先度判定のみ**で終了する（読み取り専用）。修正・返信・Resolve は行わない | OFF |
| `--defer-nit` | nit 指摘は修正せず deferred としてリスト出力 | OFF |
| `--severity-min LEVEL` | 指定重要度未満は無視（`critical` / `major` / `minor`） | `minor` |

```
/ndf:fix                        # 直前 PR のコメントに対応
/ndf:fix 9352                   # PR 番号を指定
/ndf:fix 9352 --classify-only   # まず全体像を把握したいとき
/ndf:fix 9352 --defer-nit       # nit を残して critical/major/minor だけ修正
```

大量のコメントがある PR では、`--classify-only` で全体像と優先度を確認してから修正へ
進むと、修正範囲の判断を誤りにくい。

## 起動モード

このスキルは **メインセッション直接実行** と **サブエージェント (`general-purpose`) 起動**
の両方に対応する。長丁場のクロスレビューループ（`/ndf:cross-review`）からは
**必ずサブエージェント経由で起動** されることを想定する。

```python
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

## コメントの取得（3 ソース）

インラインコメント / レビュー body / PR レベルコメントを一括取得する。
どれか 1 つでも欠けると指摘を取りこぼす。

`$ARGUMENTS` には PR 番号とオプションが混在するため、**そのまま PR 番号として扱わない**。
数値トークンだけを PR 番号として取り出し、`--` で始まるトークンはオプションとして解釈する。

```bash
# PR 番号 = 最初の数値トークン。無ければ直前 PR
PR_NUMBER=$(printf '%s\n' "$ARGUMENTS" | tr ' ' '\n' | grep -m1 -E '^[0-9]+$' || true)
PR_NUMBER="${PR_NUMBER:-$(gh pr view --json number --jq .number)}"

# オプションは $ARGUMENTS から個別に判定する
case " $ARGUMENTS " in *" --classify-only "*) CLASSIFY_ONLY=1 ;; esac
case " $ARGUMENTS " in *" --defer-nit "*) DEFER_NIT=1 ;; esac
SEVERITY_MIN=$(printf '%s\n' "$ARGUMENTS" | sed -n 's/.*--severity-min[ =]\([a-z]*\).*/\1/p')
SEVERITY_MIN="${SEVERITY_MIN:-minor}"

FETCH_SCRIPT="${PLUGIN_ROOT:-plugins/ndf-kiro}/skills/fix/scripts/fetch-pr-comments.sh"
"$FETCH_SCRIPT" "$(gh repo view --json nameWithOwner -q .nameWithOwner)" "$PR_NUMBER"

# 補助情報
gh pr view "$PR_NUMBER" --json reviewDecision,body
```

GitHub MCP を使う場合は `mcp__github__get_pull_request_comments` を利用する。

**PR 本文を必ず読む**。「やらないこと」「別 PR 対応」セクションに記載された内容への
指摘は、この PR では対応しない（分類は「別 PR 対応」）。

## 重要度の判定

`[重要度 / カテゴリ]` プレフィックス（`/ndf:review` の出力規約）を手がかりにするが、
**重要度ラベルを鵜呑みにしない**。各指摘ごとにコード・仕様を独自に調査し、本来の重要度を
判定し直してから下表の動作を適用する。bot のラベリングは参考値に過ぎない。

| 重要度 | 動作 | ユーザ問い合わせ |
|---|---|---|
| `critical` | **必ず自動修正** | なし |
| `major` | **必ず自動修正** | なし |
| `minor` / `nit`（パフォーマンス・可読性・重複コード排除） | **この PR で修正**。特にトータル行数が減る方向の修正は積極的に実施 | なし |
| `minor` / `nit`（上記カテゴリ、修正範囲が +30 行を超えそう） | deferred | あり |
| `minor`（その他） | 自動修正（明らかな改善のみ）。判断が割れるなら `nit` として deferred | なし |
| `nit`（その他） | `--defer-nit` 指定時は **修正せず deferred リスト** に追加 | あり（最後に 1 回） |

**重要度の独自判定**:
- AI agent（CodeRabbit / Copilot 等）が `nit` と付けていても、実体がパフォーマンス改善や
  重複排除なら修正対象として扱う
- 逆に `critical` と付いていても、実害がないスタイル指摘なら `nit` 相当に格下げしてよい
- 重要度はカテゴリ（performance / readability / duplication / security / style 等）と
  合わせて、コード本体を読んだ上で判定する

**指摘の正否判断**:
- ロジック・仕様逸脱・セキュリティ: コード / 仕様を確認してから修正可否を判断
- bot 指摘が **明らかに誤読** している場合（例: 意図的な変数展開を「クオート不足」と指摘）:
  修正せず reply で理由を説明（`rejected` として記録、Resolve しない）
- 仕様判断が必要な指摘（API 変更、互換性破壊など）: ユーザ問い合わせ対象

**自動判断できない場合**（context 節約のため安易にユーザへ投げない）:
仕様文書（`docs/`, `README`）を読む → 既存テストを読んで挙動を確認する → 関連する他コードの
慣例を確認する。それでも不明なら deferred リストに「要ユーザ判断」として記録し、最後に
まとめて問い合わせる。

## `--classify-only` の出力

修正は一切行わず、次の形式で分類結果だけを報告する。

| カテゴリ | 説明 | 対応判断 |
|---|---|---|
| 🔴 重大 | セキュリティ、データ整合性、クラッシュの可能性 | **対応必須** |
| 🟡 改善推奨 | コード品質、保守性、ベストプラクティス | **対応推奨** |
| 🟢 軽微 | タイポ、フォーマット、命名規則 | **対応すべき** |
| ⚪ 参考 | 提案、質問、情報共有 | **対応任意** |
| 🔵 別 PR 対応 | PR 本文で別 PR 対応と明記されている内容 | **対応不要** |

```markdown
## PR #XXXX コメント分類結果

### サマリー
- 総コメント数 / 対応必須 / 対応推奨 / 対応すべき / 対応任意・不要

### 詳細
| # | ファイル | 行 | 指摘内容 | 分類 | 対応判断 |
|---|---|---|---|---|---|
| 1 | path/to/file.ext | 123 | 指摘の要約 | 🔴 重大 | **対応必須** |

### 推奨アクション
1. 対応すべき項目（重大 + 軽微）
2. 対応推奨項目
3. 別 PR で対応（コメントで返信推奨）
```

分類の根拠（なぜその分類になったか）を簡潔に添える。

## 修正手順

1. コメント取得（上記 3 ソース）+ 重要度を**独自に再判定**
2. **CI エラー確認**（`gh pr checks <PR>` で **現時点の** 失敗ジョブを検出）
   - **完了待ちはしない**。実行中（PENDING / IN_PROGRESS）は無視して次へ進む
   - 直近で失敗（FAILURE）状態のジョブのみを修正対象に取り込む
3. 修正対象を確定（「重要度の判定」の表に従う）。CI エラーは全件修正対象
4. 問題点を修正。**コード行数が減る方向の修正は積極的に実施**（重複排除、不要分岐除去）
5. **コミット前の再確認** — 作業中に新しいコメントが追加されていないか再取得し、CI 状態も
   現時点だけ確認する（完了待ちはしない）。新しい指摘・失敗があれば手順 3 に戻る
6. コミット・プッシュ
7. **PR レベルの Summary コメントを投稿**（対応件数 + deferred 件数を明記）
8. 対応したインラインコメントに個別に返信
9. **deferred スレッドには `[deferred / nit]` ラベル付き返信** を投稿（Resolve はしない）
10. reviewer に再レビューを依頼
11. 対応完了したスレッドを **Resolve Conversation** にする
12. **戻り値ファイルを書き出す**（後述）

**flaky テストの扱い**: PR の変更範囲外で発生している flaky テストも、見つけ次第この PR で
修正する。放置するとリポジトリ全体のコード品質が下がり、後続 PR の CI 信頼性も損なわれる。

## CI エラーチェック

```bash
gh pr checks <PR番号>                              # 全チェック状態
gh pr checks <PR番号> --json name,state,link       # JSON 形式
gh pr checks <PR番号> --json name,state | \
  python3 -c "import json,sys; [print(c['name']) for c in json.load(sys.stdin) if c['state']=='FAILURE']"
```

**CI 完了待ちはしない**（`gh pr checks --watch` 等は使わない）。各チェックポイントでは
「現時点で FAILURE のジョブ」のみを取り込む。push 後の CI 再実行結果も待たない。
ただし状態スナップショットの取得は行い、戻り値の `ci_status` / `ci_failed_checks` に反映する。

失敗ログの取得:

```bash
RUN_ID=$(gh run list --branch <branch-name> --limit 1 --json databaseId --jq '.[0].databaseId // empty')
[ -z "$RUN_ID" ] && { echo "No CI run found for this branch"; exit 0; }
gh run view $RUN_ID --log-failed        # 失敗ステップのログだけ
```

| エラー種別 | 対応方針 |
|---|---|
| **lint/format** | 自動修正ツール実行（`ruff`, `prettier`, `eslint --fix` 等）→ コミット |
| **型チェック** | 型定義・アノテーションを修正。無視コメントは原則禁止（根本対応） |
| **テスト失敗** | 失敗テストを読み、実装 / テストどちらが正しいか判断してから修正 |
| **ビルドエラー** | 依存関係・構文・設定ファイルを確認 |
| **依存脆弱性** | 可能ならバージョン更新、無理なら除外ルール追加（理由明記） |
| **タイムアウト/flaky** | retry 設定、テスト分割。**PR 範囲外の flaky も見つけ次第修正** |
| **インフラ一時障害** | `gh run rerun $RUN_ID` を先に試す |

review 指摘と CI エラーは**同じ PR で一緒に修正**する。同じファイル・機能に関するものは
1 コミットにまとめ、独立しているなら別コミットに分離する。

## 返信と Resolve

### 返信の書き分け

| 状況 | 返信の型 |
|---|---|
| 修正した | `対応しました — <ファイル>:<行> で〇〇 (commit <SHA>)` |
| 別 PR で対応 | `別 PR で対応予定です。PR 説明の「やらないこと」に記載のとおり、<理由>` |
| deferred | `[deferred / nit] 後続 PR で対応予定` |
| rejected | `bot 指摘は誤読です — 理由: ...` |
| 対応不要 | `確認しました。<対応不要と判断した理由>` |

```bash
# 特定のコメントに返信（in_reply_to にコメント ID を指定）
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \
  -f body="対応しました。" -F in_reply_to={comment_id}
```

### Resolve Conversation

**修正済みのスレッドのみ** Resolve する。`deferred` / `rejected` は次ラウンドで再評価する
ため Resolve しない。

`resolveReviewThread` が要求するのは **review thread** の ID（`PRRT_...`）であり、
レビューコメントの `node_id`（`PRRT_` ではなく `PRRC_...`）ではない。
`repos/{owner}/{repo}/pulls/comments/<comment_id>` から引ける `node_id` はコメント側の ID
なので **Resolve には使えない**。必ず下記 query の `nodes[].id` を使い、
`comments.nodes[].databaseId`（返信に使ったコメント ID）または本文と突き合わせて特定する。

```bash
# スレッド一覧を thread ID (PRRT_...) 付きで取得
gh api graphql -f query='
  query {
    repository(owner: "{owner}", name: "{repo}") {
      pullRequest(number: {pr_number}) {
        reviewThreads(first: 100) {
          nodes {
            id isResolved path line
            comments(first: 1) { nodes { databaseId body } }
          }
        }
      }
    }
  }' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
           | select(.isResolved == false)
           | {thread_id: .id, path, line, comment_id: .comments.nodes[0].databaseId}'

# 上で得た thread_id（PRRT_...）を THREAD_ID に入れて Resolve
gh api graphql -f query='
  mutation($id: ID!) {
    resolveReviewThread(input: {threadId: $id}) { thread { isResolved } }
  }' -f id="$THREAD_ID"
```

### PR レベル Summary コメント（必須）

インラインへの返信と Resolve **だけでは不十分**。PR ページの Conversation タブに
まとめが出ないと、レビュアー視点で見落とされる。

```bash
gh pr comment <PR> --body "$(cat <<'EOMD'
## 🔧 /ndf:fix サマリ

対応件数: critical=X / major=Y / minor=Z (合計 N 件)
deferred: D 件 / rejected: R 件
commit: <SHA>
CI: SUCCESS | FAILURE | NONE

### 詳細
- 各 thread の対応概要（行リンク付き）
EOMD
)"
```

## 戻り値フォーマット（必須）

サブエージェント呼び出し時の context 節約のため、**実行結果を
`$TMP_DIR/fix-pr<番号>-result.json` に書き出す**（`$TMP_DIR` は環境変数
`CROSS_REVIEW_TMP_DIR` があればそれ、なければ `/tmp`）。

```json
{
  "pr": 67,
  "fix_commit": "abc1234",
  "ci_status": "SUCCESS",
  "ci_failed_checks": [],
  "ci_note": null,
  "fixed_count": 5,
  "by_severity": {"critical": 1, "major": 2, "minor": 2, "nit": 0},
  "resolved_threads": [
    {"thread_id": "PRRT_...", "comment_id": 3222849090, "path": "src/foo.py", "line": 42}
  ],
  "deferred": [
    {"comment_id": 3222849090, "thread_id": "PRRT_...", "path": "src/foo.py", "line": 42,
     "severity": "nit", "category": "style", "summary": "末尾セミコロンの有無",
     "reason_for_deferral": "好みの範囲。プロジェクト規約と齟齬なし"}
  ],
  "rejected": [
    {"comment_id": 3222849090, "summary": "heredoc を <<'JSON' にせよ",
     "reason_for_rejection": "$SHA を意図的に展開する必要があり、クオート化すると逆に壊れる"}
  ],
  "summary_comment_url": "https://github.com/.../pull/67#issuecomment-..."
}
```

- `resolved_threads` / `deferred` / `rejected` は **必ず配列**で返す（件数の int は誤り）。
  該当が無ければ空配列
- `ci_failed_checks` — `ci_status = FAILURE` のとき、失敗した check 名の配列。
  `/ndf:cross-review` 側で code-related（`pint` / `larastan` / `test` / `build` / `lint` /
  `type`）と meta-only（`check_pr_requirements` / `assignees` / `reviewers` / `labels`）を
  分類し、メタチェックのみ失敗ならループを継続する
- `ci_note` — code-related ではない CI 失敗の補足

## 方針

- 品質・可読性・セキュリティ向上を目的とし、既存機能に影響を与えない
- 指摘がすべて正しいとは限らない。修正前に仕様を調査し、実施の可否を判断する
- 未対応の場合はその理由をコメントに書き込む

## 作業完了報告（必須）

- 対応した指摘の件数（重要度別）/ deferred 件数 / rejected 件数（各々理由付き）
- 対応した CI エラーの一覧（ジョブ名、エラー内容、修正方法）
- 対応した flaky テストの一覧（PR 範囲外も含む）
- 修正コミット SHA / 修正ファイル一覧 / 戻り値ファイルパス
- **PR URL を最後に必ず記載**

## 関連

- `/ndf:review` — PR / ブランチのレビュー（Approve / Request Changes 判定）
- `/ndf:cross-review` — codex + gemini の収束レビュー。内部からこの Skill を呼ぶ
