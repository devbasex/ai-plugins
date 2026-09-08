# #444 / #443: 呼び名の表の移送と、テストの扱いの規約

## 関連リンク

| 種類 | 場所 |
| --- | --- |
| 課題 | #444（呼び名の表の移送）/ #443（テストの扱い） |
| 要求と受け入れ条件 | [issue-443-444-442-methodology.md](issue-443-444-442-methodology.md) |
| 設計 | [design-refactoring-methodology.md](design-refactoring-methodology.md)（PR #468 でマージ済み） |

## モード

`standard`。本番コードの構造を変え、対象にテストが十分にある（579 件）。

## 目的と非目的

達成したい状態:

- **兆候と手法の呼び名を `refactoring` が持ち、`cross-refactoring` は読むだけになる**
- **テストをどこまで変えてよいかが規約として書かれている**

やらないこと:

- #442（実装前の構造改善の判断）。別の Pull Request で行う
- 兆候・手法そのものの追加や削除（テストの兆候 3 つを除く）
- `cross-refactoring` の進行の制御の変更

## 実測（2026-09-07、着手の時点で数え直したもの）

| 対象 | 実測 |
| --- | ---: |
| `vocabulary.py` の行数 | 209 |
| `SMELLS` | 17 組 |
| `TECHNIQUES` | 18 組 |
| `TEST_CASES` / `TEST_LEVELS` | テスト整備ラウンドの語。**移送の対象外**（方法論ではなく枠組みの区分） |
| 差分予算の倍率 | `DIFF_BUDGET_FACTOR` = 2 / `EXTRACTION_DIFF_BUDGET_FACTOR` と `EXTRACTION_TECHNIQUES`（7 手法） |
| `tests/` の収集 | 579 件 |

## 受け入れ条件

- [ ] 1. `refactoring/references/vocabulary.md` が、兆候 17 組・手法 18 組の識別子と日本語の
      名前、および手法ごとの差分予算の倍率を持つ
- [ ] 2. **表と `SMELLS` / `TECHNIQUES` が一致することを機械で確かめる検査がある**
- [ ] 3. `vocabulary.py` が表を読み込み、自分では持たない
- [ ] 4. **読めないときは止まる**（`init` の時点で確かめる）
- [ ] 5. `code-smells.md` と `refactoring-catalog.md` に識別子の列がある
- [ ] 6. `check-cross-skill-refs.py` の走査が `"<Skill 名>" / "references"` を拾い、この参照が
      例外に登録されている
- [ ] 7. **4 つの manifest が両方の Skill を載せることを固定するテストがある**
- [ ] 8. `refactoring/references/test-changes.md` が、テストの変更の 3 分類・判定の 3 段・
      現状固定テストの扱い・段階の進め方を持つ
- [ ] 9. 兆候にテストの問題 3 つが入っている（識別子・日本語の名前・主な手法）
- [ ] 10. `verify.py` が、機械で判定できない差分を AI エージェントへ渡す
- [ ] 11. **`cross-refactoring` の振る舞いが変わっていない**（生きている経路のテストが通る）
- [ ] 12. 検査 8 本が終了コード 0
- [ ] 13. `uv run --with pytest pytest scripts/tests plugins/ndf -q` が終了コード 0

## タスク分解

### Task 1: 呼び名の表を作り、一致を見る検査を置く

- **対象ファイル:** `refactoring/references/vocabulary.md`（新設）/
  `scripts/tests/test_vocabulary_single_source.py`（新設）
- **変更内容:** `vocabulary.py` の現在の値から表を起こす。**値は変えない**
- **満たす受け入れ条件:** 1 / 2
- **進め方:** 失敗する検査を先に置く → 表を作る → 検査が通ることを見る

### Task 2: `vocabulary.py` を読み込む側にする

- **対象ファイル:** `refactor_lib/vocabulary.py` / `commands/setup.py`（`init` の確認）
- **変更内容:** 表を読んで `SMELLS` / `TECHNIQUES` / 倍率を組み立てる。読めなければ止める
- **満たす受け入れ条件:** 3 / 4 / 11
- **進め方:** 読み込みのテストを先に書く → 実装 → 既存のテストが通ることを見る

### Task 3: 検査の走査を広げ、配布の条件を固定する

- **対象ファイル:** `scripts/check-cross-skill-refs.py` / `scripts/tests/`（新設）
- **変更内容:** 走査へ `references` を足し、例外へ登録する。4 manifest の同梱を固定する
- **満たす受け入れ条件:** 6 / 7 / 12

### Task 4: 説明の表へ識別子の列を足す

- **対象ファイル:** `refactoring/references/code-smells.md` / `refactoring-catalog.md`
- **変更内容:** 「識別子」の列を足す。説明は変えない
- **満たす受け入れ条件:** 5 / 9（兆候の側）

### Task 5: テストの扱いの規約を書く

- **対象ファイル:** `refactoring/references/test-changes.md`（新設）/ `refactoring/SKILL.md`
- **変更内容:** 3 分類・判定の 3 段・現状固定テストの扱い・段階の進め方を書く
- **満たす受け入れ条件:** 8

### Task 6: 検証が判定できない差分を AI へ渡す

- **対象ファイル:** `refactor_lib/verify.py` / `prompts/`（判定のプロンプト）
- **変更内容:** 機械で判定できない差分を集め、AI エージェントへ 3 択で判定させる
- **満たす受け入れ条件:** 10 / 11

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 表の読み取りが列の並びに依存する | **見出しの名前で列を決める**。並びが変わっても壊れない |
| 読み込みで起動が遅くなる | 1 ファイルの読み取りである。`init` の時点で 1 度だけ行い、結果を持ち回る |
| AI の判定の費用が読めない | 対象は段 1 が拾えなかった差分に限る。最初の実行で件数を測り、計画へ残す |
| `TEST_CASES` / `TEST_LEVELS` まで移すと枠組みの区分が方法論へ混ざる | **移送の対象外**とし、`vocabulary.py` に残す |

## 切り戻し手順

コミットを戻すだけで元へ戻る。データ移行も外部の系への操作も含まない。

## 完了の定義

- [ ] 受け入れ条件 13 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `standard` の検証の段階（限定的な検証 → 全体テスト → 静的解析）を通している
