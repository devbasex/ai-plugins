# #499: 実装計画（版数の扱いを docs へ移し、AGENTS.md を定義へ戻す）

## 関連リンク

- 要求と受け入れ条件: [issue-499-requirements.md](issue-499-requirements.md)
- 設計: [issue-499-design.md](issue-499-design.md)（設計 PR #505 はマージ済み）

## モード

standard（文書の移設に加え、検査スクリプトの読む先とそのテストを変えるため）

## 目的と非目的

達成したい状態:
- `AGENTS.md` がナビゲーションとポリシーだけを持ち、版数と配布の手順・実測・一覧が
  `docs/versioning-and-distribution.md` にまとまる

やらないこと:
- 移す文面の書き換え（決定 6）。版数の例の直しは #566、README の移設は #500 が扱う

## 修正対象

| 区分 | ファイル |
| --- | --- |
| 新設 | `docs/versioning-and-distribution.md` |
| 変更 | `AGENTS.md` / `docs/plugin-development-guide.md` |
| 変更 | `scripts/check-doc-staleness.py` |
| 変更 | `scripts/tests/test_doc_staleness.py` / `scripts/tests/doc_staleness_helpers.py` |
| 参照の張り替え | `SECURITY.md` / `README.md` / `docs/specifications/ndf-skill-inventory/03-version-history.md` / `scripts/tests/test_validate_manifests_version.py` |

## タスク分解

### Task 1: 検査 J の読む先を正本へ移す

- **対象ファイル:** `scripts/check-doc-staleness.py`、テスト 2 本
- **変更内容:** 雛形に正本を足し、J のテストを正本へ向ける。報告先・見出し・終端の深さを変える
- **満たす受け入れ条件:** 検査が終了コード 0 / pytest が通る
- **進め方:** テストを正本へ向けて落ちることを見る → 検査を直して通す

### Task 2: 正本を新設し、AGENTS.md と開発ガイドから移す

- **対象ファイル:** `docs/versioning-and-distribution.md`、`AGENTS.md`、`docs/plugin-development-guide.md`
- **変更内容:** 設計の「移動元と移動先の対応」のとおりに段落を移し、残す判断を 1〜3 行で書く
- **満たす受け入れ条件:** AGENTS.md 300 行以下 / 記述が失われない / 開発ガイドの 2 節がリンク /
  ドキュメントの表に行がある / 章 2・章 7 が移動前の文面のまま / 囲んだ版数が 1 箇所だけ
- **進め方:** テスト駆動は適用しない（文書の移設）。移動の前後を `git diff --color-moved` で突き合わせる

### Task 3: 参照を張り替える

- **対象ファイル:** 設計の「参照を張り替える箇所」の表
- **満たす受け入れ条件:** 参照がすべて正本を指す / リンク検査が終了コード 0
- **進め方:** 設計の検索をやり直し、当たった行を 1 行ずつ分類する

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 移設で文面が変わる | 段落単位で移し、`--color-moved` の差分で変わった行だけを数える |
| 正本が 500 行を超える | 行数を測り、超えた場合だけディレクトリへ分ける（設計の「移す先の章立て」） |

## 完了の定義

- [ ] 受け入れ条件 13 件をすべて満たし、条件ごとにコマンドと終了コードが対応している
