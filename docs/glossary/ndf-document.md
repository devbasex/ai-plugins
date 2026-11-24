# 用語集: NDF の文書の組み直し（`ndf-document`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

書き上げた文書を文書形式と文書の目的に合わせて組み直すときの語。document-rebuild が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 文書形式 | — | 文書を読ませる形。リポジトリの Markdown・文書・ページ・スライド・表計算。保存先（Notion・Confluence・Google Drive などのシステム）とは別の軸 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 文書の目的 | — | 読み手にその文書で何をしてもらうか・何を渡すかの種類（決議リクエスト・議論・アジェンダ・小説・エッセイ・ブログ・作業レポート・論文など） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 共通ルール | — | 文書形式と文書の目的に関わらず、組み直した文書が満たす決まり | — | — | `docs/specifications/ndf-document-rebuild.md` |
| キーワード | — | 文書の題・見出しと、本文で繰り返し使い、読み手がその文書の主題として受け取る語 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 確定事項を伝える文書 | — | 確定した設計・報告書のように、結論が既に決まっている文書 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 議論用の文書 | — | 仕様案・提案書・論点の整理のように、まだ決まっていないことを読み手と決めるための文書 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 形式別ガイド | — | 文書形式 1 つについて、文章量の単位と目安・題と見出しの付け方・呼び出し先を持つ document-rebuild の参照ファイル（references/format-<形式>.md） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 目的別ガイド | — | 文書の目的 1 つについて、構成・文章量・見分ける手がかりを持つ document-rebuild の参照ファイル（references/purpose-<目的>.md） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 読み手役 | — | 文書と、想定した読み手の説明と、通じない文の 4 つの型だけを渡され、会話や経緯を知らずに読む AI（サブエージェントか external-ai で呼ぶ別の CLI）。要点の言い直しと、読み手に通じない文の引用を返す | — | — | `plugins/ndf/skills/document-rebuild/SKILL.md` |
