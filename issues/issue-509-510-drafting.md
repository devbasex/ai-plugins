# #509 / #510: 素材の収集と、執筆

## 関連リンク

- 課題: #509（素材の収集を新設し、事実確認を `quality-gates` へ足す）/
  #510（執筆の Skill を新設し、タイプ別の中身を参照で持つ）
- 親: #506 / マイルストーン 10「ビジネス文書ワークフロー」
- 設計: [milestone-10-design.md](milestone-10-design.md) /
  [milestone-10-design-decisions.md](milestone-10-design-decisions.md)
- 前提となる実装: #517（工程表）

## モード

`standard`。Skill を 2 個新設し、`quality-gates` と `requirements-design` へ足す。

## 目的と非目的

達成したい状態:

- 使う数値・図・引用の出所が残り、**後から同じ値へ戻れる**
- 事実確認が完了判定の段になり、**書いた本人だけでは完了しない**
- 執筆の Skill が 6 タイプの参照を持ち、**生成先に依らない Markdown** を書かせる

やらないこと:

- **取得の手段を作ること。** 出所の残し方だけを決める（決定 17）
- **文章の質の規約。** `markdown-writing` が持つ（横断で常時通る）
- **版面・生成・章立ての規約。** それぞれ実装 4 / #519 / `document-restructuring` が持つ
- 体裁レビュー（実装 4 が行う）
- 版を上げること

## 前提

- 前提 1: **新設は 2 個**（`document-sources` / `document-drafting`）。事実確認は
  `quality-gates` の段であり、新しい Skill を作らない（決定 18）
- 前提 2: **`document-drafting` は 1 個で、6 タイプの参照を持つ。** タイプごとに Skill を
  分けない（決定 19）
- 前提 3: 配布数は #519 の後で 42 / 41 / 40 / 40 になる。この実装で **44 / 43 / 42 / 42**
  になる。#519 がマージされた後に起点を取り直す
- 前提 4: 決裁・稟議に Markdown の実例は無い。**Markdown を正本にするのはこのワークフローの
  提案であり、生成の側が既存の形（`.docx` / `.xlsx` / `.pdf`）へ出せることが前提になる**

## 受け入れ条件

- [ ] 条件 1: `document-sources` が新設され、出所として残す 4 項目が決まっている
      （後から同じ値へ戻れる）
- [ ] 条件 2: 出所の無い値の扱いが決まっている（**拒否せず、実績 / 見込み / 概算で区別する**）
- [ ] 条件 3: 出所の置き場所が**ソースの `## 出典` の節**であり、生成物から消えない
- [ ] 条件 4: 事実確認が `quality-gates` の段として書かれ、**新しい Skill を作っていない**
- [ ] 条件 5: `quality-gates` が `documentation` で通す段と通さない段を持つ（必須は 3）
- [ ] 条件 6: 一致しなかったときに工程をどこへ戻すかが決まっている
- [ ] 条件 7: 事実確認の担当が決まっており、**書いた本人だけで完了しない**
- [ ] 条件 8: `document-drafting` が 1 個で、6 タイプの参照（`type-proposal.md` /
      `type-decision.md` / `type-report.md` / `type-metrics.md` / `type-manual.md` /
      `type-briefing.md`）を持つ
- [ ] 条件 9: `SKILL.md` が対象のタイプの参照だけを読ませる
- [ ] 条件 10: `markdown-writing` と重なる規約を持たない
- [ ] 条件 11: 版面・生成・章立てに関する規約を持たない
- [ ] 条件 12: 書かせる Markdown が**特定の変換器の記法を前提にしない**
- [ ] 条件 13: マニュアルの初版と改訂の違いが参照に書かれている
- [ ] 条件 14: `requirements-design` が読み手・目的・読み手に求める判断を扱う参照を持つ
- [ ] 条件 15: 新設 2 個が 4 つの manifest すべてに載る
- [ ] 条件 16: `README.md` の数とカテゴリ内訳が更新され、`check-doc-staleness.py` が通る
- [ ] 条件 17: `check-skill-frontmatter.py` / `check-skill-repo-assumptions.py` /
      `check-cross-skill-refs.py` / `validate-runtime-plugins.sh` が通る
- [ ] 条件 18: すべての `SKILL.md` が 500 行以下
- [ ] 条件 19: 既存のテストが 1 件も壊れていない

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 執筆を 1 個の Skill にし、タイプ別の参照を読ませる | 採用 | `refactoring` の `lang-*` と `design` の領域別に前例がある。タイプを足すとき他を変えない |
| B | タイプごとに 6 個の Skill を作る | 不採用 | 起動の時点でどれを使うかの判断が要る。配布数も 6 増える |
| C | 事実確認を新しい Skill にする | 不採用 | `quality-gates` は既にモードごとに段を変える。同じ形が既にある |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 出典 | 使った数値・図・引用の出所。ソースの `## 出典` の節に置く |
| 実績 | 出所を持つ確定した値 |
| 見込み | 将来の値。出所の代わりに前提を書く |
| 概算 | 精度を落とした値。出所の代わりに前提を書く |
| 事実確認 | 書かれた値を出所と突き合わせること |

## 不変条件

- 出所は生成物に残る場所へ置く
- 実績には出所が要る
- 事実確認は書いた本人だけでは完了しない

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `quality-gates` のモード別の表 | 行が 1 つ増える | **追加のみ。** 既存 4 モードの段は変えない |
| 配布 Skill の数 | 2 つ増える | 追加のみ |
| `requirements-design` | 参照が 1 本増える | 追加のみ |

## 修正対象

```text
plugins/ndf/skills/document-sources/SKILL.md                       （新規）
plugins/ndf/skills/document-sources/references/source-record.md     （新規）
plugins/ndf/skills/document-drafting/SKILL.md                       （新規）
plugins/ndf/skills/document-drafting/references/type-proposal.md    （新規）
plugins/ndf/skills/document-drafting/references/type-decision.md    （新規）
plugins/ndf/skills/document-drafting/references/type-report.md      （新規）
plugins/ndf/skills/document-drafting/references/type-metrics.md     （新規）
plugins/ndf/skills/document-drafting/references/type-manual.md      （新規）
plugins/ndf/skills/document-drafting/references/type-briefing.md    （新規）
plugins/ndf/skills/quality-gates/SKILL.md
plugins/ndf/skills/quality-gates/references/definition-of-done.md
plugins/ndf/skills/requirements-design/SKILL.md
plugins/ndf/skills/requirements-design/references/document-requirements.md  （新規）
plugins/ndf/manifests/{claude,kiro,codex,agy}-skills.txt
README.md / plugins/ndf/README.md / plugin.json 3 本 / marketplace.json
```

## タスク分解

### Task 1: `document-sources` を新設する

- **対象ファイル:** `document-sources/SKILL.md` と `references/source-record.md`
- **変更内容:** 出所として残す 4 項目、`## 出典` の節の形、実績 / 見込み / 概算の区別、
  取得の手段を持たないこと
- **満たす受け入れ条件:** 1 / 2 / 3
- **進め方:** 文書のみ。テスト駆動を適用しない（手順の記述であり、機械で確かめる振る舞いが
  無い）

### Task 2: 事実確認を `quality-gates` の段として足す

- **対象ファイル:** `quality-gates/SKILL.md` / `references/definition-of-done.md`
- **変更内容:** モード別の表へ `documentation` の行（必須の段は 3、追加で事実確認）。
  一致しなかったときの戻り先と、担当（`cross-review` のレビュワー）を書く
- **満たす受け入れ条件:** 4 / 5 / 6 / 7
- **進め方:** 文書のみ

### Task 3: `document-drafting` を新設し、6 タイプの参照を置く

- **対象ファイル:** `document-drafting/SKILL.md` と `references/type-*.md` 6 本
- **変更内容:** タイプを受け取り、該当する参照だけを読ませる。各参照は
  「必ず書く節 / 書かない節 / 読み手が最初に問うこと / よくある欠落」を持つ
- **満たす受け入れ条件:** 8 / 9 / 10 / 11 / 12 / 13
- **進め方:** 文書のみ

### Task 4: `requirements-design` へ文書向けの参照を足す

- **対象ファイル:** `requirements-design/SKILL.md` /
  `references/document-requirements.md`
- **変更内容:** 読み手・目的・読み手に求める判断を、受け入れ条件として書く形
- **満たす受け入れ条件:** 14
- **進め方:** 文書のみ

### Task 5: 配布物へ載せ、説明文書の数を合わせる

- **対象ファイル:** `manifests/*-skills.txt` / `README.md` / `plugins/ndf/README.md` /
  `plugin.json` 3 本 / `.claude-plugin/marketplace.json` / `scripts/tests/test_agy_distribution.py`
- **変更内容:** 新設 2 個を 4 つの manifest へ足し、数を持つ箇所をすべて更新する
- **満たす受け入れ条件:** 15 / 16 / 17 / 18 / 19
- **進め方:** `check-doc-staleness.py` と `validate-runtime-plugins.sh` が落ちることを
  先に確かめてから直す

## 影響範囲

- `quality-gates` を呼ぶすべての完了判定（行が 1 つ増えるが、既存 4 モードは変わらない）
- 配布 Skill の数

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `document-drafting` の参照が `markdown-writing` と重なる | **「何を書くか」だけを持つ。** 文章の質・図表の記法・語の選び方は書かない |
| 6 タイプの参照の粒度が揃わない | **4 つの節（必ず書く / 書かない / 最初に問うこと / よくある欠落）を全タイプで同じにする** |
| 配布数を持つ箇所の取りこぼし | `check-doc-staleness.py` と `validate-runtime-plugins.sh` が機械で見る。#519 で踏んだ箇所（`plugin.json` の英語の description）も同じ形で直す |
| #519 のマージ前に起点を取ると数が合わない | **#519 がマージされてから起点を取り直す** |

## 切り戻し手順

差分は Markdown と manifest の行だけ。Pull Request を revert すれば元へ戻る。

## 完了の定義

- [ ] 受け入れ条件 19 件すべてに検証手段と結果が対応している
- [ ] `standard` の必須の段（1 → 2 → 3 → 4）を通す
