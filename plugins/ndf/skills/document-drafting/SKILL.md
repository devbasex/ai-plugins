---
name: document-drafting
description: "Write the body of a business document in Markdown, following what its type requires. Use when writing a proposal, approval request, report, metric definition, manual or briefing（執筆・提案資料を書く・稟議書を書く・マニュアルを書く）."
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Bash
---

# ビジネス文書を執筆する

**タイプが決めるのは「何を書くか」である。** どう見せるか（版面）とどこへ置くか（システム）は
別の軸が持つ。

**この Skill が書かせるのは、生成先に依らない Markdown である。**

## この Skill が持たないもの

**「何を書くか」だけを持つ。** 重なる規約を持たない。

| 何を | どこが持つか |
| --- | --- |
| 文章の質（第三者が読める / 識別子を持ち込まない / 検討過程を残さない） | `markdown-writing`（**横断で常時通る**） |
| 図表の記法・横幅の上限 | `markdown-writing` の図表ガイド |
| 版面・書体・1 枚あたりの情報量 | `design` の `layout-<出力の形>.md` |
| 出力の形への生成 | `release` の `form-<出力の形>.md` |
| 章立ての組み直し | `document-restructuring` |
| 数値と引用の出所 | `document-sources` |

**`markdown-writing` は全工程に掛かる。** 工程表には載らない。

## ソースは体裁を持たない

**版面・書体・色をソースへ埋めない。** 埋めると生成の経路が 1 つに固定される。

**特定の変換器の記法を前提にしない。** スライドの 1 枚の区切りは、区切りの記法ではなく
**見出しの階層**で表す（`##` の見出し 1 つが 1 枚）。

## タイプを受け取り、その参照だけを読む

タイプの判定は `development-workflow` の `references/document-types.md` が持つ。
**ここでは判定しない。**

| タイプ | 読む参照 |
| --- | --- |
| 提案・企画 | [references/type-proposal.md](references/type-proposal.md) |
| 決裁・稟議 | [references/type-decision.md](references/type-decision.md) |
| 定例報告 | [references/type-report.md](references/type-report.md) |
| 指標定義 | [references/type-metrics.md](references/type-metrics.md) |
| 運用マニュアル | [references/type-manual.md](references/type-manual.md) |
| 説明・研修 | [references/type-briefing.md](references/type-briefing.md) |

**対象のタイプの参照だけを読む。** 他のタイプの内容はコンテキストに載せない。
**タイプを 1 つ足すときも、他の `type-*.md` は変更しない。**

## どの参照も同じ 4 つの節を持つ

**読み手が探す場所を覚え直さずに済む。**

| 節 | 何を書くか |
| --- | --- |
| 必ず書く節 | その文書が欠かせない章立て |
| 書かない節 | 入れると読み手の判断を鈍らせるもの |
| 読み手が最初に問うこと | 冒頭に置く答え |
| よくある欠落 | 実在の文書で抜けていたもの |

## 手順

### 1. タイプの参照を読む

対象のタイプ 1 つだけを読む。

### 2. 章立てを決める

**構成案（`design` が作ったもの）を起点にする。** 参照の「必ず書く節」と突き合わせ、
欠けている節を足す。**この時点では本文を書かない。**

### 3. 本文を書く

**読み手が最初に問うことへの答えを冒頭に置く。** 検討の順ではなく、読み手が問う順に並べる。

**数値には出典の番号と区別の語を付ける**（`document-sources`）。

### 4. 見直す

参照の「よくある欠落」と突き合わせる。**`markdown-writing` のセルフチェックはここで通す。**

## 範囲外の課題を見つけたとき

執筆の過程で、この文書の範囲外の課題（別の文書の誤り、手順の不備など）に気づいたら、
その場で `/ndf:out-of-scope` が issue にする。

この工程に入ったら `/ndf:progress-tracking <issue番号> "実装"` を呼ぶ（記録の手順はその
Skill が持つ）。

## 関連

- `/ndf:markdown-writing` — 文章と図表の書き方（横断で常時通る）
- `/ndf:document-sources` — 数値と引用の出所
- `/ndf:document-restructuring` — 書き上げた後の章立ての組み直し
- `/ndf:design` — 構成案と体裁設計
- `/ndf:development-workflow` — タイプの判定（`references/document-types.md`）
