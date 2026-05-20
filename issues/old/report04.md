了解です。Claude Code の **カスタムスラッシュコマンド**として、

1. **中期 memory の自動レビュー運用**（/mem-review）
2. **task 終了時に memory write を促す**（/mem-capture）

を **そのまま置ける .md ファイル**で作ります。

> 仕様根拠：Claude Code は `.claude/commands/*.md` に Markdown を置くとスラッシュコマンド化でき、frontmatter で `allowed-tools` などを指定できます。([Claude Code][1])
> また `disable-model-invocation: true` を使うと自動発火を抑制できます。([クラスメソッド発「やってみた」系技術メディア | DevelopersIO][2])

---

## 1) /mem-review（中期 memory の自動レビュー運用）

**狙い**

* `.serena/memories/` を走査して `review_at` 期限の来た中期記憶を検出
* 期限超過/期限間近をまとめて提示
* 1件ずつ「延長/長期化/アーカイブ/削除/更新」を提案し、必要ならファイルを編集

> Serena の memory が `.serena/memories/` に置かれる運用は一般に定着しています（Serena workflow/usage系の情報）。([クラスメソッド発「やってみた」系技術メディア | DevelopersIO][3])
> ※あなたの環境でパスが違う場合は、コマンド内の `MEM_DIR` を変更してください。

**保存先**：`.claude/commands/mem-review.md`

```md
---
description: "中期Serena memory（review_at付き）を自動検出してレビューする"
argument-hint: "[--days N] [--dir PATH] 例: /mem-review --days 14"
allowed-tools: Bash(date:*), Bash(find:*), Bash(rg:*), Bash(ls:*), Bash(pwd:*), Read, Write
disable-model-invocation: true
---

あなたは「中期/長期の記憶戦略」を運用するエージェントです。
このコマンドは `.serena/memories/` の **中期 memory**（`review_at` を持つもの）をレビューします。

# 入力
$ARGUMENTS

# 期待するmemory形式（例）
先頭付近に以下のようなメタ情報がある（YAMLでなくても "key: value" を本文に含めば可）:
- type: decision | assumption | experiment
- review_at: YYYY-MM-DD
- confidence: low | medium | high
- project: <name>

# 実行手順

## 0) パラメータ解釈
- --days N : 「期限間近」とみなす日数（デフォルト14日）
- --dir PATH : memoryディレクトリ（デフォルト `.serena/memories`）

まず Bash で今日の日付（YYYY-MM-DD）を取得する:
- `date +%F`

次に、対象ディレクトリを決定する（存在確認も行う）。

## 1) 期限超過・期限間近の抽出
Bash で以下を行う:
1. memoryファイル一覧を取得（*.md想定）
2. 各ファイルから `review_at: YYYY-MM-DD` を抽出
3. 今日の日付と比較して:
   - overdue: review_at < today
   - due_soon: today <= review_at <= today + N日
4. 結果を「overdue」「due_soon」に分けてリストアップ

比較は Bash で `date -d` が使えない環境があるので、**ISO日付の文字列比較**を基本とする。
（YYYY-MM-DDなら辞書順で比較可能）

## 2) レポート表示（必須）
以下を必ず出力:
- 今日の日付
- 設定（dir, days）
- overdue一覧（ファイル名・review_at・type・project・冒頭1〜2行の要約）
- due_soon一覧（同上）

要約のために必要なファイルだけ Read する。

## 3) 1件ずつレビュー処理
各対象memoryについて、次の選択肢を提示し、ユーザーが指示しなくても「推奨」を1つ示す:
A. 延長（review_at を未来に更新）
B. 長期化（type=principle/constraint/policyへ移行、expires: none 付与、review_at削除）
C. 更新（内容修正 + review_at更新）
D. アーカイブ（ファイル末尾に `status: archived` を追記、または `ARCHIVE/` に移動）
E. 削除

ユーザーが指示しない場合は、以下で自動推奨:
- 実験結果が確定して「原則」になっている → B
- まだ暫定だが有効 → A
- 内容が古い/前提が変わった → C or D
- 明らかに不要 → E

編集が必要なら Write でファイルを更新する。

## 4) 変更サマリ
最後に、実行した変更を一覧で出力:
- 更新したファイル
- 変更内容（review_at変更/長期化/アーカイブ/削除）
- 次回のレビュー推奨日

# 注意
- このコマンド自体は「記憶戦略の運用」だけを行う。
- 新しい意思決定を作るのは /mem-capture に誘導する。
```

---

## 2) /mem-capture（task終了時の memory write を促す）

**狙い**

* タスク完了後に、**記憶化すべきものだけ**を短いフォームで回収
* 既存memoryに追記 or 新規作成
* 中期なら `review_at` をデフォルトで **60日後**（変更可）
* 長期化フラグ（`--long`）で principle/constraint/policy として保存

**保存先**：`.claude/commands/mem-capture.md`

```md
---
description: "タスク終了時に、再利用価値のある知見をSerena memoryとして保存する"
argument-hint: "[--project NAME] [--type decision|assumption|experiment|principle|constraint|policy] [--review-at YYYY-MM-DD] [--long] [--append FILE]"
allowed-tools: Bash(date:*), Bash(pwd:*), Bash(ls:*), Bash(mkdir:*), Bash(test:*), Read, Write
disable-model-invocation: true
---

あなたは「中期/長期の記憶戦略」を運用するエージェントです。
このコマンドは **タスク終了時**に、記憶化すべき内容をフォームで回収し、
`.serena/memories/` に保存します。

# 入力
$ARGUMENTS

# 目標
- Skillを肥大化させないため、判断・前提・制約は memory に保存する
- ただし「手順」「実装詳細」は保存しない

# 手順

## 0) デフォルト設定
- memory dir: `.serena/memories`
- 今日: `date +%F`
- デフォルト review_at: 今日 + 60日（Bashで日付計算できない環境があるので、ユーザーに日付入力を促しても良い）

## 1) まずユーザーにフォームで回答してもらう（必須）
以下をそのまま提示し、ユーザーの入力を待たずに「推奨の埋め方」も例示してよいが、最終的にはユーザー入力を使う。

### Memory Capture Form
1. project: (例 carloc / mdx / global)
2. type: (decision / assumption / experiment / principle / constraint / policy)
3. confidence: (low / medium / high)
4. title: (短いタイトル)
5. context: (何が起きた？ 1-2文)
6. decision_or_fact: (確定事項を1-3点)
7. why: (理由があれば1-2文)
8. next_action: (必要なら)
9. review_at: (中期のみ YYYY-MM-DD, 長期は none)
10. related_files: (任意。パスやPR番号など)

## 2) 保存先の決定
- `--append FILE` があれば、そのファイルを Read して追記する
- それ以外は新規作成:
  - ファイル名: `{today}-{project}-{slug(title)}.md`
  - 保存先: `.serena/memories/`

必要なら mkdir を Bash で実行。

## 3) 出力フォーマット
新規作成の場合、以下のテンプレを使う（内容はフォームから埋める）:

---
# <title>
type: <type>
confidence: <confidence>
project: <project>
review_at: <YYYY-MM-DD or none>
created_at: <today>
status: active
---

## Context
<context>

## Decision / Facts
- <decision_or_fact 1>
- <decision_or_fact 2>

## Why
<why>

## Next action
<next_action>

## Related
- <related_files>

## Notes
- This memory intentionally excludes procedures and implementation details.

## 4) 中期→長期化の自動提案
- type が decision/assumption/experiment でも、内容が「不変の原則」なら
  長期化（principle/constraint/policy）を提案する
- `--long` 指定があれば review_at は none にする

## 5) 完了サマリ
保存/追記したファイルパスを必ず表示し、次の推奨コマンドを提示:
- 次回レビュー: `/mem-review`
```

---

## 置き方（最小）

```bash
mkdir -p .claude/commands
# 上の2ファイルをそれぞれ .claude/commands/mem-review.md と mem-capture.md に保存
```

---

## 使い方例

* 期限チェック（14日以内を「間近」扱い）

  * `/mem-review --days 14`

* タスク完了の記憶化（中期）

  * `/mem-capture --project carloc --type decision --review-at 2026-04-01`

* 長期化（原則として保存）

  * `/mem-capture --project global --type principle --long`

---

## 追加で「より自動」にしたい場合（選択肢）

Claude Code 側だけで“完全自動（フック）”は難しいことが多いので、実務的には

* PR作成前に `/mem-capture`
* 週1で `/mem-review`
  が堅いです。

もし「Git hook / CI で週次レビューを半自動化」までやりたいなら、あなたの運用（GitHub Actions有無、CI権限）に合わせて *hook/ジョブ* 版も作れます。

[1]: https://code.claude.com/docs/ja/slash-commands?utm_source=chatgpt.com "スラッシュコマンド - Claude Code Docs"
[2]: https://dev.classmethod.jp/en/articles/disable-model-invocation-claude-code/?utm_source=chatgpt.com "For skills that are only executed manually (slash commands), I ..."
[3]: https://dev.classmethod.jp/articles/20250806-tuning-project-configuration-for-serena-efficiency/?utm_source=chatgpt.com "プロジェクト構成をSerena効率化に向けたチューニング"
