# 設計工程の規約

開発ループに設計工程を置き、その成果物を規約化した経緯と決定を残す。

**手順と表は [`design` の SKILL.md](../../plugins/ndf/skills/design/SKILL.md) が正である。**
ここに書き写さない。この文書が扱うのは、そこに書かない決定の理由と、工程表での位置である。

## 概要

設計工程は `design` Skill が担う。モードごとに作る文書を決め、変更が触る領域に対応する参照
だけを読ませる。`standard` では設計を実装より先に組み直してからレビューへ通し、設計だけを
載せた Pull Request をマージしてから実装へ進む。

## 用語

| 用語 | 意味 |
| --- | --- |
| 設計文書 | `design` が作る成果物。`issues/` 配下に置く Markdown |
| 設計 Pull Request | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request |
| 記述標準 | 機械が読める形式で契約を書く外部の取り決め（OpenAPI・Open Data Contract Standard・Design Tokens Format Module） |

## 背景

設計工程には、何という文書を作り何を書くかの規約が無かった。そのため成果物の構成が担当した
AI ごとに変わり、文書の分割単位・記載項目・粒度をその場で決めることになっていた。

仕様駆動開発の主要なツールは、要求・設計・タスクの 3 段構成を採る。NDF は要求
（`requirements-design`）とタスク（`implementation-plan`）を持ち、その間が空いていた。

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 設計の Skill を 1 個にする | 過去の計画にあった 3 分割（設計レビュー・ドメインモデリング・クラス設計）は、起動の時点でどれを使うかの判断が要る。設計の観点の切れ目がモードや領域と一致しない |
| 触る領域ごとに参照を分け、入出力は API と画面で 2 本にする | 読ませる分量を、実際に触る領域まで絞る。片方だけを触る変更が多い |
| 記述標準は対象がある場合にだけ必須にする | モードで一律に必須とすると、画面を持たないリポジトリでも画面の節を求めることになる |
| 成果物は `issues/` 配下へ置く | 仕様と同じ場所に置き、完了後に `plan-to-spec` が `docs/` へ移す既存の流れに乗せる |
| 時系列を保護する手法に既定を置かない | どれを採るかは対象の性質で変わる。既定を置くと合わない対象へも同じ手法が選ばれる。代わりに、過去を失う構造を選んだときは理由を残すことを求める |
| 画面は策定中の標準を待たず、当面の書き方を定める | 画面要素の構造を記述する標準は策定の途中にある。確定を待つとその間の設計に規約が無い |
| 決定の記録を設計文書の 1 節に置く | 決定を別ファイルにすると、設計を読む人が理由へたどり着けない |
| 図はコンテナ階層までを求める | クラスやコードの階層はコードが唯一の正であり、図は書いた時点で古くなる |
| 設計 Pull Request で課題を閉じない | 実装が終わっていない段階でマージするため、閉じると残りの工程が追えなくなる |
| ドキュメントレビューに新しい Skill を作らない | `pr` → `cross-review` → `merged` と手順が同じであり、作ると同じ内容が 2 箇所に増える |

## 工程表での位置

| 工程 | `light` | `legacy-refactor` | `standard` |
| --- | --- | --- | --- |
| 設計 | — | `design` | `design` |
| ドキュメント再構成 | — | 設計 Pull Request を分けた場合 | `document-restructuring` |
| ドキュメントレビュー | — | 設計 Pull Request を分けた場合 | `pr` → `cross-review` → `merged` |

`legacy-refactor` でこの 2 つを条件付きにしたのは、このモードが要求と受け入れ条件を通らず、
レビューの軸が現状固定テストの通過に寄るためである。**2 つは同じ条件で同時に発動する。**

設計 Pull Request をマージした後は、`worktree` を実装用のブランチ名で呼び直す。`merged` が
設計のブランチと作業ツリーを消すため、そのまま実装を続けられない。

前後の Skill との受け渡し:

| 相手 | 受け渡すもの |
| --- | --- |
| `requirements-design` | 受け入れ条件と対象範囲を受け取る（`legacy-refactor` を除く） |
| `implementation-plan` | 設計文書の節からタスクを導く |
| `plan-to-spec` | 設計文書を確定仕様へ取り込む |

## データ・設定

`design` は永続データを持たない。**この Skill が記録する値は `設計` と
`ドキュメントレビュー` の 2 つ**で、`.ndf/projects.json` を持つリポジトリでだけ働く。

**盤面の単一選択へ足す値は、この 2 つに限らない。** 設計の工程の前後には
`ドキュメント再構成`（`document-restructuring` が記録する）も入る。**盤面へ足すのは
工程表の行すべてであり、その操作は盤面を持つリポジトリ側が行う。**

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| frontmatter が執筆規約を満たす | `python3 scripts/check-skill-frontmatter.py` |
| 4 ランタイムのマニフェストと配布物が一致する | `bash scripts/build-runtime-plugins.sh --check` / `bash scripts/validate-runtime-plugins.sh` |
| 説明文書の Skill 数が実体と一致する | `python3 scripts/check-doc-staleness.py` |
| 参照のリンクが解決できる | `python3 scripts/check-markdown-links.py` |
| 4 ランタイムで Skill が読み込める | `bash scripts/runtime-smoke-test.sh` |

## 運用

`FRONTMATTER_TOTAL_MAX`（`scripts/check-skill-frontmatter.py`）の引き上げは、実測を取り直して
から行う。手順と根拠は
[Skill 執筆規約の「上限値」](../../plugins/ndf/skills/README.md#上限値)にある。

## 関連リンク

- [issue #161](https://github.com/devbasex/ai-plugins/issues/161)
- [PR #212](https://github.com/devbasex/ai-plugins/pull/212) — 要求仕様と設計
- [PR #218](https://github.com/devbasex/ai-plugins/pull/218) — 実装
- [`design` Skill](../../plugins/ndf/skills/design/SKILL.md)
- [開発ワークフローの振り分け](../../plugins/ndf/skills/development-workflow/SKILL.md)
