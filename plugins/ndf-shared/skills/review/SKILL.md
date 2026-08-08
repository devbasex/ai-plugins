---
name: review
description: "Review a PR diff, or the current branch diff with --branch, and post an approve or request-changes verdict."
when_to_use: "PR をレビューするとき、および PR 作成前にローカルブランチをセルフレビューするとき (--branch)。Triggers: 'レビューして', 'PRレビュー', 'マージ前チェック', 'ブランチをレビュー', 'セルフレビュー', 'PR前にレビュー', 'review my branch', 'self review', 'pre-PR review'"
argument-hint: "[PR番号 | --branch] [AIエージェント(codex|gemini)] [--focus AREA]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# コードレビューコマンド

PR 差分、または `--branch` 指定時は現在のブランチの差分を、専門家としてレビューする。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | レビュー対象の PR | 直前の PR |
| `--branch` | PR ではなく **ローカルブランチの差分**をレビューする（PR 作成前のセルフレビュー） | OFF |
| `[AIエージェント]` | `codex` / `gemini` に委譲。省略時は Claude 自身 | Claude |
| `--focus AREA` | 重点観点（`security` / `performance` / `tests` / 任意の文字列） | なし |

```
/ndf:review                       # 直前 PR をレビュー
/ndf:review 9352                  # PR 番号を指定
/ndf:review 9352 codex            # Codex CLI に委譲
/ndf:review --branch              # ローカルブランチをセルフレビュー
/ndf:review --branch security     # セキュリティに焦点を当ててセルフレビュー
```

## 2 つのモード

| 観点 | PR モード（既定） | `--branch` モード |
|---|---|---|
| 対象 | GitHub 上の PR 差分 | `git diff <既定ブランチ>` の差分 |
| 出力先 | **PR 上にインラインコメント + 総評を投稿** | セッション上の報告のみ（投稿しない） |
| 判定 | `APPROVE` / `REQUEST_CHANGES` | 判定を出さず改善提案を返す |
| 用途 | PR 作成後のレビュー | PR 作成前のセルフレビュー |

**どちらのモードでもコード修正は行わない**（分析と指摘のみ。修正は `/ndf:fix`）。

## 観点

言語慣用性（Idiomatic）・可読性・コード品質・保守性・セキュリティ・テストカバレッジ。
上から順に優先して指摘する。

### 具体的なチェックポイント

- **その言語らしい記述方式**: イディオム・標準ライブラリ・言語機能の活用
- **メモリ効率・演算性能**
  - キャッシュ利用、不要なループ・コピーの排除
  - Python: numpy 利用、内包表記、ジェネレータ
  - PHP: switch 文の map（連想配列）化
  - N+1 クエリ、不要なデータベースアクセス、インデックスの活用
- **関数・メソッド・ファイル行数の適正化**
  - 目安: 関数/メソッド 50 行、ファイル 300 行。ただしプロジェクトの慣例に従う
  - 単一責任原則から外れていないか
- **重複・冗長コードの排除**
  - PR 範囲にこだわらず積極的にまとめるよう指摘
  - 逆に過剰な抽象化（YAGNI 違反）も指摘する
- **柔軟性を損なう定数化の排除**
  - 数字をそのまま定数にするような硬直化を避ける
  - 定数よりも DB の master テーブル、または json/yaml による外部化を検討
- **セキュリティ**
  - SQL インジェクション / XSS / CSRF 対策、入力値バリデーション
  - 認証・認可の適切性、機密情報（トークン、キー、個人情報）の取り扱い
- **エラーハンドリング**
  - 例外が適切に捕捉されているか、リトライ / タイムアウトの設計
  - ログ出力の妥当性（詳細は `/ndf:logging-guidelines`）

`--focus` が指定された場合は、該当する観点を優先し、他の観点は重大なもののみ指摘する。

## `--branch` モードの手順

### 1. 変更の把握

```bash
git diff main --name-only          # 変更ファイル一覧
git diff main --stat               # 差分の統計
git log main..HEAD --oneline       # コミット履歴
```

### 2. 分析

各変更ファイルについて、追加・変更されたロジックの意図が明確か、テストが追加されて
いるか、プロジェクトの規約に準拠しているかを、上記「観点」に沿って確認する。

### 3. 報告

```markdown
## レビュー結果

### 概要
- 変更ファイル数 / 追加行数 / 削除行数

### Issues（要修正）
- `path/to/file.ext:456` — 問題点と修正方針

### Suggestions（改善提案）
- `path/to/file.ext:123` — 提案内容
```

指摘は重要度の高いものから並べる。良い点の列挙は行わない。

### 注意事項

- 大量の変更がある場合、重要な変更から優先的にレビューする
- 自動品質チェック（linter, formatter, type checker）は事前実行済みを前提とする
- レビュー結果は提案であり、最終判断は開発者が行う

## PR モードの手順

レビュー結果は **GitHub の PR レビュー機能** を使って必ず PR 上に書き込む。
個別指摘は **コード行に紐付くインラインコメント** が原則。総評（review body）にだけ
書くのは避ける。

- 問題点・改善点あり → `REQUEST_CHANGES`
- 指摘なし → `APPROVE`

### 指摘の振り分け

| 指摘の種類 | 投稿先 |
|---|---|
| 特定ファイル・特定行への指摘 | **インラインコメント** (`comments[].path` + `line`) |
| 複数ファイルにまたがる設計指摘 | 代表箇所にインラインコメント + review body に補足 |
| 設計レベル・PR全体の所見 | review body（総評） |
| ファイル単位の指摘（行を絞れない） | そのファイルの代表行にインラインコメント |

### 投稿フロー（推奨: 1 リクエストで一括投稿）

`gh api` の Reviews API を使い、**総評 + 複数のインラインコメント + 判定（event）を
1 回で送信** する。

```bash
PR=<PR番号>
OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)

# インラインコメントを JSON 配列で組み立て
#   （path / line / side / body の 4 つが必須。複数行レンジは start_line を併用）
SUMMARY=$'## 総評\n\n... 全体所見をここに ...'
jq -n \
  --arg sha "$SHA" \
  --arg event "REQUEST_CHANGES" \
  --arg body "$SUMMARY" \
  '{
    commit_id: $sha,
    event: $event,
    body: $body,
    comments: [
      {path: "src/foo.py", line: 42, side: "RIGHT",
       body: "[major / 可読性] この関数は 70 行ある。〇〇 と △△ に分割を推奨。"},
      {path: "src/bar.py", start_line: 10, line: 25, side: "RIGHT",
       body: "[minor / 性能] このループは内包表記化できる。"}
    ]
  }' > /tmp/review-payload.json

gh api -X POST "repos/$OWNER_REPO/pulls/$PR/reviews" --input /tmp/review-payload.json
```

> 💡 **JSON 組み立てに heredoc (`<<JSON`) は使わない**: 変数展開は必要だが、`$SHA` 等に
> 特殊文字が混入した場合 JSON が壊れる（あるいはクオート未エスケープで JSON injection に
> なる）。`jq -n --arg` 経由なら値が自動で JSON エスケープされるため安全。クオート付き
> heredoc (`<<'JSON'`) は逆に `$SHA` が展開されず使えない。

**`event` の値**:
- `APPROVE` — 指摘なし
- `REQUEST_CHANGES` — 修正必須の指摘あり
- `COMMENT` — 任意の指摘のみ（マージブロックしない）

### インラインコメント本文の書式

各 `comments[].body` の先頭に **`[重要度 / カテゴリ]`** を付けて視認性を上げる:

```
[critical / セキュリティ] SQL がエスケープなしで連結されている。プレースホルダ必須。
[major / 可読性] 70 行関数。〇〇 と △△ に分割を推奨。
[minor / 言語慣用性] Python なら内包表記で 1 行化可能。
[nit / スタイル] スペースが揃っていない。
```

### 重要度の運用ガイド（auto-fix 判定に直結）

| 重要度 | 定義 | 後段（`/ndf:fix`）の扱い |
|---|---|---|
| `critical` | セキュリティ・データ破損・本番障害につながる | **必ず自動修正** |
| `major` | 保守性・性能・仕様逸脱の重要問題 | **必ず自動修正** |
| `minor` | 改善推奨だがブロッカーではない | **自動修正対象**（明らかな改善のみ。判断要なら nit に格下げ） |
| `nit` | 好み・スタイル | **修正しない**、最後にユーザ判断にまとめる |

過剰な nit 量産は避ける。critical / major で対応すべき真の問題に集中すること。

### 既存コメントがある場合の重複防止

同じ箇所への二重指摘を避けるため、投稿前に既存コメントを確認する:

```bash
gh api "repos/$OWNER_REPO/pulls/$PR/comments" --paginate \
  | jq -r '.[] | "\(.path):\(.line) \(.body | split("\n")[0])"'
```

すでに同種の指摘があれば、その指摘は省くか、reply（既存コメントへの返信）にする。

### 補助コマンド

```bash
# review body 単体（インラインなし）で投稿したい場合
gh pr review "$PR" --request-changes --body "..."
gh pr review "$PR" --approve --body "..."

# 会話タブへの普通のコメント（行に紐付かない）
gh pr comment "$PR" --body "..."

# 1 件だけインラインコメントを追加（既存 review に含めない）
gh api -X POST "repos/$OWNER_REPO/pulls/$PR/comments" \
  -F commit_id="$SHA" -F path="src/foo.py" -F line=42 -F side=RIGHT -F body="..."
```

## 外部 AI への委譲

第二引数が指定された場合、上記「観点」「具体的なチェックポイント」「PR モードの手順」の
内容を **レビュー指示プロンプト** として組み立て、指定された CLI に渡す。
呼び出し手順の詳細は `/ndf:codex` / `/ndf:gemini` を参照。

### プロンプト組み立て

1. `gh pr view <PR> --json title,body,baseRefName,headRefName,url,headRefOid` でメタ情報を取得
2. `gh pr diff <PR>` で差分を取得
3. 上記「観点」「PR モードの手順」をそのままプロンプトに転記
4. PR タイトル・URL・差分を **対象情報** として明記
5. **出力は Reviews API のペイロード形式（JSON）で出させ、外部 AI 自身に投稿させる**

### 外部 AI に必須化する出力形式と直接投稿

**外部 AI はペイロードを組み立てた後、自分自身で `gh api` を呼んで PR に投稿する。**
メインに返すのは「投稿が成功したか」「最終 verdict」「review URL」「件数」の小さな
結果サマリのみ。

プロンプトに必ず含める指示:

- 個別指摘は必ず `comments[]` のインラインコメントにする（行を絞れない場合はファイル代表行）
- `body`（総評）には設計・横断的な所見のみ書く。個別指摘の繰り返しは禁止
- 各 `comments[].body` の先頭に `[重要度 / カテゴリ]` を付ける
- `path` は **PR 差分に登場するファイルのみ**（`gh pr diff <PR> --name-only` の一覧から選ぶ）
- `line` は **差分に含まれる行**（追加行・コンテキスト行）に限る。`side=RIGHT` が既定
- `commit_id` は `gh pr view <PR> --json headRefOid -q .headRefOid` の値を使う
- 投稿後 `/tmp/<agent>-review-pr<番号>-result.json` に結果サマリを書き出す

結果サマリの形式:

```json
{
  "status": "posted",
  "event": "REQUEST_CHANGES",
  "posted_as": "COMMENT",
  "review_url": "https://github.com/.../pull/<PR>#pullrequestreview-...",
  "comments_count": 5,
  "by_severity": {"critical": 0, "major": 2, "minor": 2, "nit": 1},
  "payload_path": "/tmp/<agent>-review-pr<番号>-payload.json",
  "error": null
}
```

投稿失敗時は `status: "failed"`、`error` にエラーメッセージを入れ、`payload_path` に
payload を残す（メイン側のフォールバック投稿で使う）。

### `event` と `posted_as` の使い分け

- `event` — **AI 本来の判定 (intent)**。ループ収束判定（`/ndf:cross-review`）はこれを見る
- `posted_as` — **GitHub に実際投稿した event**。既定は `event` と同じ値

GitHub は **自分の PR には `REQUEST_CHANGES` で投稿できない**
（`HTTP 422: Can not request changes on your own pull request`）。自分の PR をレビュー
する場合は次のダウングレードを行う。

- `event = "REQUEST_CHANGES"` のままにしておく（intent 保持）
- ペイロードの `event` だけ `"COMMENT"` にして投稿
- `posted_as = "COMMENT"` を結果サマリに記録

判定にあたっては事前に `gh api user --jq .login` と
`gh pr view <PR> --json author --jq .author.login` を比較する。

### メイン側の検証とフォールバック

メインエージェントの責務は **結果サマリの読み込みと検証のみ**。

```bash
AGENT=codex   # or gemini
RESULT=/tmp/$AGENT-review-pr$PR-result.json

if [ ! -s "$RESULT" ]; then
  echo "❌ $AGENT: 結果サマリ未生成。完了検知 or プロンプト指示に問題あり" >&2
  exit 1
fi

if [ "$(jq -r '.status' "$RESULT")" = "failed" ]; then
  echo "⚠️ $AGENT: 投稿失敗。payload からメインがフォールバック投稿します" >&2
  PAYLOAD=$(jq -r '.payload_path' "$RESULT")
  OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
  SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
  jq --arg sha "$SHA" '.commit_id = $sha' "$PAYLOAD" > /tmp/review-fallback.json
  gh api -X POST "repos/$OWNER_REPO/pulls/$PR/reviews" --input /tmp/review-fallback.json
fi
```

**Claude 自身による追加判定は行わず**、外部 AI の判定（`event`）と指摘内容をそのまま採用する。

## 作業完了報告（必須）

PR モードではレビュー結果が **PR 上に投稿済み** であることが前提。報告は以下に絞る。

- 利用エージェント（claude / codex / gemini）
- 投稿結果（review URL、event）
- 件数サマリ（インラインコメント数、重要度別内訳）
- 総評の要約 / PR URL

詳細な指摘内容は PR 上のインラインコメントに残っているため、報告では繰り返さない。
`--branch` モードでは投稿先がないため、上記「報告」の書式でセッション上に出力する。

## 関連

- `/ndf:fix` — レビュー指摘の分類と修正対応
- `/ndf:cross-review` — codex + gemini の収束レビュー
- `/ndf:codex` — Codex CLI の呼び出し手順
- `/ndf:gemini` — Gemini CLI の呼び出し手順
- `/ndf:logging-guidelines` — ログ設計
