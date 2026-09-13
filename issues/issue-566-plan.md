# #566: 版の形の表と次の開発の例を、例どうしで突き合わせる（実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-566-requirements.md](issue-566-requirements.md)
- 設計文書: [issue-566-design.md](issue-566-design.md)
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/604 （マージ済み）
- 課題: #566

## モード

`standard`。継続的統合が走らせる検査（`scripts/check-doc-staleness.py`）の判定規則が増えるため。

## 目的と非目的

達成したい状態:

- 正本の章 2 の版の形の表と「次を開発するなら」の例が崩れたとき、検査が行番号付きで落ちる

やらないこと:

- 章 2 の例の値の書き換え（要求の前提 3）
- 正本の章 6 の重複段落と敬体の段落（#603）
- 根の `README.md` の記述を正本へ移すこと（#500）

## 前提

- 前提 1: 起点は `origin/develop` の `854c55b`。設計の確認に使った `55d94f9` から、正本の章 2・章 6・章 7 と
  検査 J のコードは変わっていない
- 前提 2: 設計は `check_version_examples` を `check_version_section` の中から呼ぶ形を描いている。
  その形では、`check_version_section` を直接呼ぶ既存の単体テスト 2 件（章に表の行を置かない本文で
  `report.errors` の全体を比べる）が R1・R4 の報告で落ち、AC8（既存の J のテストの期待値を変えない）に反する。
  **呼び出しは `main` に置き、`check_version_section` が章を読めたときだけ呼ぶ。** 判定の規則と文言は設計のまま

## 受け入れ条件

**9 件は [issue-566-requirements.md](issue-566-requirements.md) の「受け入れ条件」にある。** ここでは写さない。

## 修正対象

- `scripts/check-doc-staleness.py`
- `scripts/tests/doc_staleness_helpers.py`
- `scripts/tests/test_doc_staleness.py`
- `docs/versioning-and-distribution.md`

## タスク分解

### Task 1: テスト用の雛形を正本の章 2 と同じ形へ揃える

- **対象ファイル:** `scripts/tests/doc_staleness_helpers.py` / `scripts/tests/test_doc_staleness.py`
- **変更内容:** 雛形の開発版の行を `9.4.0-dev.1` へ直し、公開前の確認版の行 `9.4.0-rc.1` を足す。
  `retarget_version` を新しい雛形に合わせる。行番号の期待値 `L17` を `L18` へ直す
- **満たす受け入れ条件:** AC6, AC8
- **進め方:** 雛形を直した時点でテスト一式が通ることを確かめる（規則を足す前の基準）

### Task 2: 規則 R1〜R5 を足す

- **対象ファイル:** `scripts/check-doc-staleness.py` / `scripts/tests/test_doc_staleness.py`
- **変更内容:** `section_lines` を新設して `scan_section_versions` をその上に載せ替え、
  `check_version_examples` を新設して `main` から呼ぶ
- **満たす受け入れ条件:** AC1〜AC5, AC7
- **進め方:** AC1〜AC5 のテストを先に書いて落ちることを確かめ、規則を足して通す

### Task 3: 正本の章 6・章 7

- **対象ファイル:** `docs/versioning-and-distribution.md`
- **変更内容:** 章 6 に規則と位置を決める語を書く。章 7 の前置きの件数を表の行数（3）へ揃える
- **満たす受け入れ条件:** AC9
- **進め方:** テスト駆動を適用しない（文書）。差分を読み、前置きの件数と表の行数を数える

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 章 2 の走査を 2 つの関数が別々に持つと、区間の終わりの判定が食い違う | `section_lines` へ 1 か所にまとめ、既存の J のテストを変えずに通す |
| #500 が章 2・章 7 を先に書き換える | 取り込み直した後に AC7 を再実行する |

## 切り戻し手順

- 4 ファイルの変更を戻せば元の検査に戻る。データの移行は無い

## 完了の定義

- [ ] 受け入れ条件 9 件に、検証手段と結果が対応している
- [ ] issue の 2 つの崩れた形を一時コピーで作り、検査が終了コード 1 で行番号付きで落ちる。`develop` の木で終了コード 0
