# 用語集: NDF の指示書チェック（`ndf-instructions`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

instructions-check.py が見る指示書・スコープ・読み込み量・リリース済みバージョンの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 指示書 | — | エージェントが読む規約の文書。既定の名前は AGENTS.md / CLAUDE.md / KIRO.md | — | — | `docs/specifications/ndf-instruction-files-check.md` |
| ルート指示書 | — | スコープの根に置かれた指示書。セッションのたびに読まれる | 根の指示書 | — | `docs/specifications/ndf-instruction-files-check.md` |
| サブディレクトリ指示書 | — | サブディレクトリに置かれた指示書。その場所で作業したときに読まれる | 配下の指示書 | — | `docs/specifications/ndf-instruction-files-check.md` |
| @インポート | — | コードの外に書いた @<パス>。参照先の内容がそのまま読み込まれる | 即時読み込みの参照、即時読み込み | — | `docs/specifications/ndf-instruction-files-check.md` |
| リリース済みバージョン | — | リポジトリがリリース済みとして定義したバージョン。取り出し方（変更履歴の文書・タグ）は設定が決める | 出た版 | — | `docs/specifications/ndf-instruction-files-check.md` |
| 読み込み量 | — | ルート指示書と、そこから @ でたどった先の合計バイト数 | 読み込みの量 | — | `docs/specifications/ndf-instruction-files-check.md` |
| 指示書スコープ | — | 指示書の置く場所の区分。プロジェクト / ユーザー / プラグイン | — | — | `docs/specifications/ndf-instruction-files-check.md` |
