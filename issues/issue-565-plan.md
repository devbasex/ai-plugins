# #565: 語の分割に、コマンドの区切りを持たせる（実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-565-requirements.md](issue-565-requirements.md)
- 設計: [issue-565-design.md](issue-565-design.md)（設計 PR #582 はマージ済み）

## モード

standard（hook の判定ロジックの変更で、設計 PR を先に通したため）

## 修正対象

以下、`plugins/ndf/skills/development-workflow/` からの相対で書く。

| ファイル | 変更 |
| --- | --- |
| `scripts/lib/workflow-common.sh` | `wf_split` / `wf_parse_sync` / `_wf_pr_create_body`。あわせて `wf_is_candidate`（下の「設計からの差分」） |
| `scripts/lib/workflow-merge.sh` | `wf_merge_target` |
| `tests/test_workflow_guard.py` | 受け入れ条件ごとのテスト |
| `references/stage-completeness.md` | 「通過工程の控え」に、どの書き方の記録を読むかを 1 段落 |

触らない: `SKILL.md` / `scripts/workflow-guard.sh` / `tests/workflow_helpers.py`（#527 の実装と並行しているため）。

## 設計からの差分

**`wf_is_candidate` にも手を入れる。** AC13 の `gh pr \⏎merge 268` は、読み手の
`wf_merge_target` がマージと判定できても、手前の安い見分け（`grep -E 'pr[[:space:]]+merge'`）が
行単位で当たらず、hook は読み手を呼ばずに通す。関門が外れたままになるため、見分けの前に
行末の `\` と改行の組を空白へ置き換える（bash の置換 1 回。起動は増えない）。

## タスク分解

### Task 1: `wf_split` が区切りを出す

- **変更内容:** 引用の外の `;` `|` `(` `)` と、リダイレクトでない `&` で空の語を出す。
  改行は 2 行目以降の行頭で区切りを出し、行末の `\` は継続として捨てる
- **満たす受け入れ条件:** AC11 と、他のすべての前提
- **進め方:** `wf_split` の出力を比べるテスト（設計の出力例 6 件 + リダイレクト 3 件 + 性能）→ 実装

### Task 2: 進行の記録を 1 つ目のコマンドの終わりまでで読む

- **変更内容:** `wf_parse_sync` が区切りの前では読み飛ばし、見つけた後の区切りか 3 語で止まる
- **満たす受け入れ条件:** AC1〜AC5、AC13（3 つ目）
- **進め方:** `guard` で控えを読むテスト → 実装

### Task 3: マージの判定を 1 つ目のマージの終わりまでで読む

- **変更内容:** `wf_merge_target` が区切りで状態を 0 へ戻し、見つけた後の区切りで止まる。`wf_is_candidate` の継続の扱い
- **満たす受け入れ条件:** AC6〜AC8、AC13（1・2 つ目）
- **進め方:** `run_lib` と `guard`（`stub_gh`）のテスト → 実装

### Task 4: Pull Request 作成の本文を作成のコマンドの終わりまでで読む

- **変更内容:** `_wf_pr_create_body` が区切りで状態を 0 へ戻し、見つけた後の区切りで止まる
- **満たす受け入れ条件:** AC9、AC10
- **進め方:** `wf_parse_pr_create` のテスト → 実装

### Task 5: 参照文書

- **変更内容:** `stage-completeness.md` の「通過工程の控え」に 1 段落
- **進め方:** 文書のみのためテスト駆動は適用しない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 区切りの追加で、今まで積まれていた書き方の控えが変わる | AC5 のテストで既存の書き方を固定し、テスト一式（AC12）を通す |
| awk の実装差（mawk / gawk / BSD awk）で NUL の出力が変わる | 既存と同じ `printf "%s%c", "", 0` の形だけを使う |

## 切り戻し手順

hook の判定だけの変更で、控えの形式も公開の契約も変えない。コミットを revert すれば戻る。

## 完了の定義

- [ ] AC1〜AC13 のテストが通り、条件ごとに検証手段と結果が対応している
- [ ] 親の会話で踏んだ形（`; echo "exit=$?"` 付きの記録と `cd x&&gh pr merge 268`）を hook の入力として流し、控えの置き場所を隔離して結果を示す
- [ ] `bash scripts/build-runtime-plugins.sh` と `bash scripts/validate-runtime-plugins.sh` と `claude plugin validate .` が通る
