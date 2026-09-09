# #515 / #511 / #514: ドキュメンテーションシステムと、生成・提出・取り込み

## 関連リンク

- 課題: #515（`system-<名前>.md` で分割管理）/ #511（生成と提出を `release` の形として足す）/
  #514（外部の文書を取り込んで差分でレビューする）
- 親: #506 / マイルストーン 10「ビジネス文書ワークフロー」
- 設計: [milestone-10-design.md](milestone-10-design.md) /
  [milestone-10-design-decisions.md](milestone-10-design-decisions.md)
- 前提となる実装: #517（`documentation` モードと工程表）

## モード

`standard`。Skill を 1 個新設し、`release` へ出力の形を 4 つ足す。公開インタフェース
（Skill と参照）の追加である。

## 目的と非目的

達成したい状態:

- ドキュメンテーションシステムが 1 システム 1 ファイルで分かれ、システムを 1 つ足すときに
  他の `system-*.md` を変更しない
- 生成と提出が `release` の形として足され、**新しい Skill が増えない**
- 外部にある文書を取り込む手順が決まり、内容のレビューが git の差分に載る

やらないこと:

- **取得と投稿のクライアント実装。** 手順の記述だけを持ち、Confluence / SharePoint の
  スクリプトは書かない
- **既存の実装（別リポジトリの `.claude/skills/`）の移植**
- `google-drive` と `notion-writing` の内容を写すこと。**指すだけにする**
- 執筆・体裁・素材の収集（実装 3 / 4 が行う）
- 版を上げること

## 前提

- 前提 1: **新設する Skill は `document-systems` の 1 個である。** 取り込み（#514）は
  この Skill の参照が持ち、独立した Skill を作らない。3 つの工程（配布 / 素材の収集 /
  体裁レビュー）が同じシステムの知識を読むため、置き場所は 1 つにする
- 前提 2: 出力の形は 4 つ（スライド / 文書 / 表計算 / ページ）で、**システムの名前を形の
  名前に混ぜない**（決定 1）。`form-notion.md` のような名前は作らない
- 前提 3: **往復の実測（#514 の受け入れ条件）は、生成の手段がそろってから行う。**
  取り込みは MCP で動くことを確認済みだが、Markdown から Google スライドや Notion への
  生成の手段はまだ無い。実測できない場合は「未確認」として残し、`release-verification`
  へ引き継ぐ
- 前提 4: Confluence と SharePoint は、このリポジトリで実行して確かめていない。既存実装の
  知識から書き、実測していないことを各ファイルへ明記する

## 受け入れ条件

- [ ] 条件 1: `document-systems` が 1 個で、5 システムの参照（`system-gdrive.md` /
      `system-notion.md` / `system-confluence.md` / `system-sharepoint.md` /
      `system-repo.md`）を持つ
- [ ] 条件 2: `SKILL.md` が対象のシステムの参照だけを読ませる
- [ ] 条件 3: 各 `system-<名前>.md` が 8 項目（認証 / 取り込みの手段と取れないもの /
      投稿の手段 / 本文の表現 / 版の扱い / 図の扱い / 描画して見る手段 / 既知の失敗）を持つ
- [ ] 条件 4: `release/references/form-slide.md` / `form-document.md` /
      `form-spreadsheet.md` / `form-page.md` があり、**新しい Skill を作っていない**
- [ ] 条件 5: 形の参照がシステム固有の手順を持たず、`document-systems` を指している
- [ ] 条件 6: `release/SKILL.md` の「配布の形」の表へ 4 つの形が載り、
      `distribution-forms.md` の一覧にも載る
- [ ] 条件 7: 生成の経路が複数あることが書かれ、**特定の変換器を既定にしていない**
- [ ] 条件 8: 生成物を保存するかが**経路ごとに**決まっている（非決定的な経路では保存する）
- [ ] 条件 9: 置き場所・命名・公開範囲・索引への登録の 4 つについて決め方が書かれている
- [ ] 条件 10: 公開範囲を設定した後に確かめる手順がある
- [ ] 条件 11: 取り消しの手段とその限界（既に見た人には効かない）が完了報告に含まれる
- [ ] 条件 12: 5 システムそれぞれについて、取り込みで取れるものと取れないものが書かれている
- [ ] 条件 13: 正規化が決定的である（同じ対象を 2 回取り込むと同じ結果になる）
- [ ] 条件 14: 3 つの用途それぞれについて、取り込みの向きと実行の時期が決まっている
- [ ] 条件 15: 正本の Markdown と取り込んだ Markdown が別のファイルである
- [ ] 条件 16: CI で動かす必要があるかが決まっており、必要なら認証の前提が書かれている
- [ ] 条件 17: `document-systems` が 4 つの manifest すべてに載る
- [ ] 条件 18: `README.md` の公開 Skill 数とカテゴリ内訳が更新され、
      `check-doc-staleness.py` が通る
- [ ] 条件 19: `check-skill-repo-assumptions.py` が通る（自リポジトリ前提を持たない）
- [ ] 条件 20: `check-cross-skill-refs.py` が通る（境界をまたぐ参照の扱い）
- [ ] 条件 21: すべての `SKILL.md` が 500 行以下
- [ ] 条件 22: 既存のテストが 1 件も壊れていない

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 取り込みを `document-systems` の参照へ入れる | 採用 | 3 つの工程が同じシステムの知識を読む。置き場所を 1 つにすれば写しが生まれない |
| B | 取り込み専用の Skill を新設する | 不採用 | 取り込みの手段はシステムごとに違い、`system-*.md` と同じ内容を 2 か所に持つことになる |
| C | 形の参照をシステムごとに切る（`form-notion.md` ほか） | 不採用 | 形とシステムが混ざる。#515 が指摘した軸の潰れが再発する |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 正本 | リポジトリに置く Markdown。生成の入力になる |
| 取り込み | 外部のシステムにある文書をリポジトリへ持ち込むこと |
| 正規化 | 取り込んだ内容を、同じ対象なら毎回同じ形になるよう整えること |
| 決定的な経路 | 変換器を使う生成。同じ入力から同じ生成物が出る |
| 非決定的な経路 | AI エージェントが組む生成。同じ入力から同じ生成物が出ない |

## 不変条件

- システムを 1 つ足すときに、他の `system-*.md` を変更しない
- 形の参照はシステム固有の手順を持たない
- 正本と取り込んだファイルは別である

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `release` の形 | 4 つ増える | **追加のみ。** 既存 5 形の手順は変えない |
| 配布 Skill の数 | 1 つ増える | 追加のみ。4 ランタイムすべてへ配る |
| `google-drive` / `notion-writing` | 変更しない | 指すだけにする |

## 修正対象

```text
plugins/ndf/skills/document-systems/SKILL.md                    （新規）
plugins/ndf/skills/document-systems/references/system-gdrive.md      （新規）
plugins/ndf/skills/document-systems/references/system-notion.md      （新規）
plugins/ndf/skills/document-systems/references/system-confluence.md  （新規）
plugins/ndf/skills/document-systems/references/system-sharepoint.md  （新規）
plugins/ndf/skills/document-systems/references/system-repo.md        （新規）
plugins/ndf/skills/document-systems/references/import.md             （新規。#514）
plugins/ndf/skills/release/SKILL.md
plugins/ndf/skills/release/references/distribution-forms.md
plugins/ndf/skills/release/references/form-slide.md         （新規）
plugins/ndf/skills/release/references/form-document.md      （新規）
plugins/ndf/skills/release/references/form-spreadsheet.md   （新規）
plugins/ndf/skills/release/references/form-page.md          （新規）
plugins/ndf/manifests/{claude,kiro,codex,agy}-skills.txt
README.md
```

## タスク分解

### Task 1: `document-systems` を新設し、5 システムの参照を置く

- **対象ファイル:** `document-systems/SKILL.md` と `references/system-*.md` 5 本
- **変更内容:** 対象のシステムを受け取り、該当する参照だけを読ませる。各参照は 8 項目を持つ
- **満たす受け入れ条件:** 1 / 2 / 3 / 12
- **進め方:** 文書のみ。テスト駆動を適用しない（手順の記述であり、機械で確かめる振る舞いが
  無い）。frontmatter とリンクは検査が見る

### Task 2: 取り込みの手順と正規化を置く

- **対象ファイル:** `document-systems/references/import.md`
- **変更内容:** 3 つの用途・向き・時期、正規化の規則、格納先、往復で保たれるものを書く
- **満たす受け入れ条件:** 13 / 14 / 15 / 16
- **進め方:** 文書のみ。**正規化が決定的であることは、同じ対象を 2 回取り込んで差分が
  出ないことで確かめる**（実測できる範囲で行う）

### Task 3: `release` へ出力の形を 4 つ足す

- **対象ファイル:** `release/SKILL.md` / `references/distribution-forms.md` /
  `references/form-{slide,document,spreadsheet,page}.md`
- **変更内容:** 「配布の形」の表と一覧へ 4 行、形ごとのファイルを 4 本。生成の経路・
  生成物の保存・置き場所・命名・公開範囲・索引・取り消しを書く
- **満たす受け入れ条件:** 4 / 5 / 6 / 7 / 8 / 9 / 10 / 11
- **進め方:** 文書のみ

### Task 4: 配布物へ載せ、説明文書の数を合わせる

- **対象ファイル:** `manifests/*-skills.txt` / `README.md`
- **変更内容:** `document-systems` を 4 つの manifest へ足し、`README.md` の公開 Skill 数と
  カテゴリ内訳を更新する
- **満たす受け入れ条件:** 17 / 18 / 19 / 20 / 21 / 22
- **進め方:** `check-doc-staleness.py` が落ちることを先に確かめてから直す

## 影響範囲

- `release` を呼ぶすべての配布（形が 4 つ増えるが、既存の形の手順は変わらない）
- 配布 Skill の数（41 / 40 / 39 / 39 → 42 / 41 / 40 / 40）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `release/SKILL.md` が 500 行を超える（現在 293 行） | 形ごとの記述は `form-*.md` へ置き、`SKILL.md` へは表の 4 行だけ足す |
| 既存実装の知識を写すときに自リポジトリ前提が混ざる | `check-skill-repo-assumptions.py` が機械で検査する。サイト名・組織名・認証情報の識別子を書かない |
| 往復の実測ができない | 前提 3 のとおり未確認として残し、`release-verification` へ引き継ぐ |
| 5 システム分を 1 度に書くと、形が揃わないまま増える | **Google Drive と Notion を先に書き、その形を他の 3 つへ当てる**（実測済みの 2 つを基準にする） |

## 切り戻し手順

差分は Markdown と manifest の行だけで、データ移行を含まない。Pull Request を revert すれば
元へ戻る。

## 完了の定義

- [ ] 受け入れ条件 22 件すべてに検証手段と結果が対応している
- [ ] `standard` の必須の段（1 → 2 → 3 → 4）を通す
