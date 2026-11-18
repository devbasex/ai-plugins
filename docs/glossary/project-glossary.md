# 用語集: プロジェクトの用語集（`project-glossary`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

各プロジェクトが持つユビキタス言語。語・意味・コンテキスト・廃止した語・SSOT

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| ユビキタス言語 | — | プロジェクトの関係者とエージェントが、要求・設計・コードで同じ意味に使う語の集まり | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 用語集 | `glossary` | ユビキタス言語を持つ構造化ファイル（正）と、そこから作る人が読む Markdown の文書。どちらもプロジェクトのリポジトリに置く | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| コンテキスト | `context` | 語の意味が 1 つに決まる範囲（境界づけられたコンテキスト） | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| ドメインモデルのセクション | — | 設計文書の先頭に置くセクション。変更が属するコンテキスト・変える集約とそのオーナー・不変条件・ドメインイベントを書く | ドメインモデルの節 | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 廃止した語 | `deprecated` | 用語集で別の語へ置き換えた語。文書の追加した行と、用語集自身の意味の説明に出たら落とす | — | — | `docs/specifications/ndf-glossary-deprecated-meaning.md` |
| 単独でマッチする廃止した語 | `deprecated_standalone` | 前後が漢字の出現には当てない廃止した語。1 文字の語と、長い語の一部に現れる語を登録するために使う | — | — | `docs/specifications/ndf-glossary-deprecated-meaning.md` |
| 不変条件 | — | 集約がいつも満たす条件。設計のドメインモデルのセクションに書き、実装の前にテストにする | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 未登録の語 | `unregistered` | 用語として書かれているのに用語集に無い語。見出しが「用語」のセクションの表の 1 列目に書いた語を指す | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 用語集のマージ | — | ベースブランチのマージで衝突した用語集を、共通の祖先・自分の側・相手の側の 3 つのバージョンから語の単位で合わせ、文書を作り直してマージを終えること。`glossary.py merge` が行う | — | — | `docs/specifications/ndf-glossary-merge.md` |
| 語の衝突 | — | 用語集のマージで、両側が同じ語（コンテキストと語の組）を違う中身にしたこと、または片側が消して片側が書き換えたこと。コンテキストの `id` でも同じ。スクリプトは解かずに止まる | — | — | `docs/specifications/ndf-glossary-merge.md` |
| 用語集の設定 | `glossary_config` | `.ndf/glossary.json`。用語集の保存先・形式・チェックの対象を持つ | 用語集の宣言 | `declaration` | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 用語チェック | — | `glossary.py check`。用語集の形と意味の説明の廃止した語、文書の追加した行の語を見る | 語のチェック | — | `docs/specifications/ndf-glossary-deprecated-meaning.md` |
| agy に配る定義 | — | `plugins/ndf/dev.agy/agents/` の下にあり agy が読むエージェント定義。`plugins/ndf/agents/` から生成した写しで、`model` だけが agy の tier 名に変わる。手で直さない | — | — | `docs/specifications/runtime-plugin-distribution.md` |
| model の対応表 | — | Claude Code のエージェント定義の `model` の値（型の名前）から agy の tier 名への対応。`scripts/lib/agy_agents.py` が持つ | — | — | `docs/specifications/runtime-plugin-distribution.md` |
| 禁止語 | — | 用語集の設定が、検査する範囲で新しく書くことを禁じた語 | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
