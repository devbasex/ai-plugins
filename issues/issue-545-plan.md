# #545: 設計 Pull Request の本文を設計文書の決定から外れさせない — 実装計画

## 関連リンク

- issue: #545
- 要求と受け入れ条件: [issue-545-requirements.md](issue-545-requirements.md)
- 設計: [issue-545-design.md](issue-545-design.md)（設計 Pull Request #578 で承認済み）

## モード

standard（新設のスクリプトと継続的統合の検査を足し、3 つの Skill の手順を変える。設計 Pull Request を通した）

## 修正対象

| ファイル | 新設 / 変更 |
| --- | --- |
| `plugins/ndf/scripts/pr-body-decisions.sh` | 新設 |
| `plugins/ndf/scripts/tests/test_pr_body_decisions.py` | 新設 |
| `.github/workflows/pr-body-decisions.yml` | 新設 |
| `plugins/ndf/skills/pr/SKILL.md` | 変更 |
| `plugins/ndf/skills/fix/SKILL.md` | 変更 |
| `plugins/ndf/skills/design/SKILL.md` | 変更 |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | 変更 |

## タスク分解

### Task 1: 呼び出しの誤りと対象外

- **対象ファイル:** `pr-body-decisions.sh` / テスト
- **変更内容:** 副コマンドと番号の検査（3）、`gh` が無い・読めない（2）、head が `design/` で始まらない（0、`files` と `contents` を読まない）
- **満たす受け入れ条件:** 6 / 8 / 20 / 22
- **進め方:** 差し替えた `gh` が呼び出しを記録する形でテストを先に書く

### Task 2: 突き合わせ（`check`）

- **対象ファイル:** `pr-body-decisions.sh` / テスト
- **変更内容:** 変更したファイルの `.md` を head の SHA から読み、コードフェンスの外の `## 決定の記録` の下の `### ` を集め、期待する節を作って本文の節と比べる
- **満たす受け入れ条件:** 1〜5 / 7
- **進め方:** 失敗するテスト → 実装

### Task 3: 書き直し（`sync`）

- **対象ファイル:** `pr-body-decisions.sh` / テスト
- **変更内容:** 節の差し替え・除去・`## Test plan` の直前か末尾への追加。REST の `PATCH` で本文だけを送り、読み直して突き合わせ直す
- **満たす受け入れ条件:** 9〜12
- **進め方:** 失敗するテスト → 実装

### Task 4: 継続的統合

- **対象ファイル:** `.github/workflows/pr-body-decisions.yml`
- **変更内容:** `opened` / `synchronize` / `edited` / `reopened` で `check` を走らせる。権限は読み取りだけ。必須の検査にはしない（決定 8）
- **満たす受け入れ条件:** 18 / 19
- **進め方:** テスト駆動を適用しない（ワークフローの定義）。この Pull Request 自身で起動と `edited` の再実行を実測する

### Task 5: 手順

- **対象ファイル:** `pr` / `fix` / `design` の `SKILL.md`、`approval-request.md`
- **変更内容:** 設計の「呼ぶ時点」の表のとおりに書き足す
- **満たす受け入れ条件:** 13〜17 / 21
- **進め方:** テスト駆動を適用しない（手順の文書）。文書の検査 3 本の終了コードで確かめる

## 実装で決める 2 件の確かめ方

| 項目 | 確かめ方 |
| --- | --- |
| `PATCH` で本文の改行が保たれるか | この Pull Request 自身の本文へ、複数行と `\r\n` を含む本文を `gh api -X PATCH --input` で書き、読み直してバイト列を比べる。確かめた後に本文を戻す |
| 本文だけの編集で検査が再実行されるか | この Pull Request 自身の本文を編集し、`edited` の実行が増えることを見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `design/SKILL.md` が設計の後に #526 で変わった | 書き足す位置（手順 5 の表の「本文」の行）が残っていることを確かめた。位置は変えない |
| `development-workflow/SKILL.md` が行数の上限ちょうど | `references/approval-request.md` だけを変え、`SKILL.md` には触れない |
| 本文を書き換える実測で本物の Pull Request を壊す | 書き込みの実測はこの Pull Request 自身の本文に限る。他の Pull Request は読むだけにする |

## 切り戻し手順

新設 3 本を消し、Skill 4 本の差分を戻す。データの移行は無い。

## 完了の定義

- [ ] 受け入れ条件 22 件に、検証手段と結果が対応している
- [ ] `uv run --with pytest pytest plugins/ndf/scripts/tests -q` が exit=0
- [ ] 文書の検査 3 本が exit=0
