# 用語集: NDF のスライドの作成（`ndf-slide`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

スライドを生成スクリプトで作り、Google Slides へ変換して確かめるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| トーン＆マナー | — | スライドの書体・色・文字サイズ・著作権表示・ロゴの値の組。プロジェクトごとに差し替える | — | — | `docs/specifications/ndf-slide-generation.md` |
| ページの枠組み | — | 見出し・罫線・ロゴ・著作権表示・ページ番号の位置を固定した、ページを作る共通の関数（page / box / note / tbl / section） | — | — | `docs/specifications/ndf-slide-generation.md` |
| 生成スクリプト | — | pptx を出力するスクリプト（pptxgenjs の build-deck.js）。スライドの SSOT になる | — | — | `docs/specifications/ndf-slide-generation.md` |
| 汎用テンプレート | — | 会社色を持たないトーン＆マナーとページの枠組みを持つ、生成スクリプトの出発点 | — | — | `docs/specifications/ndf-slide-generation.md` |
| 構成メモ | — | スライドのページごとの番号・見出し・役割・根拠の表と、構成の考え方を書いた文書 | — | — | `docs/specifications/ndf-slide-generation.md` |
| 台本 | — | ページごとの見出しと読み上げの原稿。配分秒数は見出しに付けてよい（任意）。スピーカーノートへ入れる | — | — | `docs/specifications/ndf-slide-generation.md` |
| トーン＆マナーのファイル | — | プロジェクトのトーン＆マナーの値を持つ JSON（slides/theme.json）。書いた項目だけが汎用テンプレートの既定値を上書きする | — | — | `docs/specifications/ndf-slide-generation.md` |
| 尺 | — | 台本の読み上げにかかる時間。読み上げの字数を 350 字/分で割って出す | — | — | `docs/specifications/ndf-slide-generation.md` |
