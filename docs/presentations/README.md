# プレゼンテーション資料

発表用のスライド資料を置きます。新しい資料は、NDF の `release` Skill の
[form-slide.md](../../plugins/ndf/skills/release/references/form-slide.md) の生成スクリプトの経路（pptxgenjs）で作ります。

公開先: **https://devbasex.github.io/ai-plugins/presentations/**

`main` にマージすると GitHub Pages が更新されます。Pages は `main` ブランチの `docs/` を配信するよう設定されています。Jekyll は無効（`docs/.nojekyll`）で、ファイルはそのまま配信されます。サイトのルート（`docs/index.html`）は資料一覧へ転送します。

## この資料の位置づけ

各スライドは**発表日時点の記録**です。発表後にリポジトリの構成やプラグインの内容が変わっても
追随させません。日付と、ヘッダに書いた版数の時点の内容として読んでください。

現在の構成・導入手順は次を参照してください。

| 知りたいこと | 参照先 |
|---|---|
| 配布構成と導入手順 | [README.md](../../README.md) |
| プラグインの詳細 | [plugins/ndf/README.md](../../plugins/ndf/README.md) / [plugins/playwright-kit/README.md](../../plugins/playwright-kit/README.md) |
| 配布の仕様 | [docs/specifications/runtime-plugin-distribution.md](../specifications/runtime-plugin-distribution.md) |

たとえば `2026-08-06-ai-plugins-intro` は NDF v5.0.0 時点のもので、当時の
`plugins/ndf-shared/` や `plugins/ndf-kiro/install.sh` といったパス、当時の Skill 数を含みます。
これらは v9.0.0 の単一ディレクトリ構成では別の場所にあります。

## 資料一覧

| 資料 | 内容 | 想定時間 |
|---|---|---|
| [2026-10-17-skill-plugin-development/](2026-10-17-skill-plugin-development/README.md) | 独自プラグイン開発の薦め。NDF の機能を例に、自分用のスキルを作り、プラグインにして配り、育てることを薦める（社外の勉強会・登壇向け） | 30〜35分 / 23ページ |
| [2026-08-06-ai-plugins-intro.md](2026-08-06-ai-plugins-intro.md) | ai-plugins / NDF v5.0.0 の個別機能紹介。各スキルに何が書いてあり、何を重視しているかを扱う | 15分 / 15枚 |

`2026-10-17-skill-plugin-development` の成果物は Google Slides で、pptx は生成スクリプトから作り直せるためコミットしません。

`2026-08-06-ai-plugins-intro` は、当時の Marp（Markdown からスライドを作る変換器）で作った記録です。書き出した PDF と HTML
（[pdf](2026-08-06-ai-plugins-intro.pdf) / [html](2026-08-06-ai-plugins-intro.html)）を同じディレクトリに残しています。
HTML は画像を data URI として埋め込んだ単一ファイルで、ブラウザで開いて `p` を押すと台本の読めるプレゼンタービューになります。

## ディレクトリ構成

```
docs/presentations/
├── README.md
├── index.html                       # 公開ページの入口（資料一覧）
├── 2026-10-17-skill-plugin-development/
│   ├── README.md                    # 成果物・トーン＆マナー・生成と確認の手順
│   ├── notes.md                     # 構成メモ（ページごとの見出し・役割・根拠と尺）
│   ├── script.md                    # 台本（スピーカーノートの正本）
│   └── slides/                      # 生成スクリプト・theme.json・写した枠組み（lib/）
├── 2026-08-06-ai-plugins-intro.md   # 2026-08-06 の資料（記録）
├── 2026-08-06-ai-plugins-intro.pdf
├── 2026-08-06-ai-plugins-intro.html
├── diagrams/                        # 2026-08-06 の資料の図版のソース（Mermaid）
└── images/                          # diagrams/ から作った PNG
```

## 新しい資料を追加するとき

- `YYYY-MM-DD-{topic}/` のディレクトリを作り、日本語は含めない
- 中身は `form-slide.md` の「生成スクリプトの経路」の並び（README・構成メモ・台本・`slides/`）にする。汎用テンプレートは `plugins/ndf/skills/release/assets/slides/` から写す
- 生成は `node slides/build-deck.js <出力>.pptx`、尺は `node slides/lib/script-time.js script.md` で測る
- Google Slides への変換とページごとのサムネイルでの確認は `form-slide.md` の「変換と確認」の順に行う
- `slides/lib/` は書き換えない。トーン＆マナーは `slides/theme.json` に書く
- 生成した pptx と `node_modules/` はコミットしない（`.gitignore`）
- 資料の一覧（この README の表）へ足す。公開ページに載せる場合は `index.html` の資料一覧にも追加する
