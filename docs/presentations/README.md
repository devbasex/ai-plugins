# プレゼンテーション資料

発表用のスライド資料を Marp 形式で管理します。

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

| ファイル | 内容 | 想定時間 |
|---|---|---|
| [2026-08-06-ai-plugins-intro.md](2026-08-06-ai-plugins-intro.md) | ai-plugins / NDF v5.0.0 の個別機能紹介。各スキルに何が書いてあり、何を重視しているかを扱う | 15分 / 15枚 |

書き出し済みの成果物も同じディレクトリに置いています。**Markdown を編集したら `build.sh` で両方を再生成してコミットしてください。**

| 成果物 | 用途 | 特徴 |
|---|---|---|
| [2026-08-06-ai-plugins-intro.pdf](2026-08-06-ai-plugins-intro.pdf) | 配布・共有ドライブへの掲載 | しおり付き。どこでも開ける。台本は出力されない |
| [2026-08-06-ai-plugins-intro.html](2026-08-06-ai-plugins-intro.html) | 発表本番 | 単一ファイル。**プレゼンタービューで台本が読める** |

HTML は画像を data URI として埋め込み、絵文字も文字に戻してあるため、このファイル1つで完結します。外部への通信は発生しません。ブラウザで開いたときの操作は以下のとおりです。

| キー | 動作 |
|---|---|
| `→` / `←` / スペース | スライド送り・戻し |
| `f` | フルスクリーン |
| `p` | プレゼンタービュー（次スライド・台本・タイマー）を別ウィンドウで開く |
| `o` | 一覧表示 |

各スライドの HTML コメントに発表台本と時間配分を書いており、プレゼンタービューに表示されます。

## ディレクトリ構成

```
docs/presentations/
├── README.md
├── index.html                       # 公開ページの入口（資料一覧）
├── 2026-08-06-ai-plugins-intro.md   # スライド本体（Marp Markdown）
├── 2026-08-06-ai-plugins-intro.pdf  # 配布用 PDF（build.sh が生成）
├── 2026-08-06-ai-plugins-intro.html # 発表用の単一ファイル HTML（build.sh が生成）
├── build.sh                         # PDF と HTML を書き出す
├── diagrams/                        # 図版のソース（Mermaid）
│   ├── overview.mmd
│   ├── pr-flow.mmd
│   └── cross-review.mmd
└── images/                          # diagrams/ から生成した PNG
    ├── overview.png
    ├── pr-flow.png
    └── cross-review.png
```

図版は Mermaid で書き、PNG に変換したものをスライドから参照します。Marp は Mermaid を直接描画しないため、`.mmd` を編集したら PNG を再生成してください。

## ビルド

### スライドを表示する

VS Code の [Marp for VS Code](https://marketplace.visualstudio.com/items?itemName=marp-team.marp-vscode) 拡張を入れると、Markdown をプレビューするだけでスライドとして表示されます。

### PDF / HTML に書き出す

```bash
bash docs/presentations/build.sh
```

PDF と、単一ファイル化した HTML の両方を書き出します。

`build.sh` が単一ファイル化まで行うのは、Marp の HTML 出力が `images/` を相対パスで参照し、絵文字を CDN 上の SVG に置き換えるためです。そのまま配ると画像が表示されず、オフラインでは絵文字も欠けます。`build.sh` は画像を data URI として埋め込み、絵文字を文字へ戻したうえで、外部アセット参照が残っていないことを検証します。

Chromium が必要です。見つからない場合は取得してからもう一度実行してください。

```bash
npx playwright@1.49 install chromium
```

台本も配る場合は、`--pdf-notes` を付けると各スライドの HTML コメントが PDF の注釈として埋め込まれます。

```bash
npx @marp-team/marp-cli@4 --pdf --pdf-notes --allow-local-files 2026-08-06-ai-plugins-intro.md
```

### 図版を再生成する

```bash
cd docs/presentations

cat > /tmp/puppeteer.json <<'EOF'
{"executablePath":"/home/user/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
 "args":["--no-sandbox","--disable-dev-shm-usage"]}
EOF

for d in overview pr-flow cross-review; do
  npx @mermaid-js/mermaid-cli@11 \
    -i "diagrams/$d.mmd" -o "images/$d.png" \
    -p /tmp/puppeteer.json -b transparent -s 3
done
```

`executablePath` は自分の環境の Chromium パスに置き換えてください。`-s 3` は 3 倍解像度でのレンダリング指定で、スライドに拡大表示しても文字が潰れないようにするためのものです。

## 新しい資料を追加するとき

- ファイル名は `YYYY-MM-DD-{topic}.md` とし、日本語は含めない
- 図版は Mermaid で書いて `diagrams/` にソースを残す。PNG だけをコミットしない
- 発表台本は各スライド末尾の HTML コメントに書く
- 書き出しは `build.sh` を使う（引数にスライドの Markdown を渡せる）
- 公開ページに載せる場合は `index.html` の資料一覧にも追加する
