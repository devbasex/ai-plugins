# プレゼンテーション資料

社内勉強会などで使うスライド資料を Marp 形式で管理します。

## 資料一覧

| ファイル | 内容 | 想定時間 |
|---|---|---|
| [2026-08-06-ai-plugins-intro.md](2026-08-06-ai-plugins-intro.md) | ai-plugins / NDF v4.20.1 の個別機能紹介。PR・レビューワークフローと設計・仕様ドキュメント系スキルが中心 | 15分 / 12枚 |

配布・閲覧用に、書き出し済みの PDF も同じディレクトリに置いています（[2026-08-06-ai-plugins-intro.pdf](2026-08-06-ai-plugins-intro.pdf)）。しおりから各スライドに移動できます。**Markdown を編集したら PDF も再生成してコミットしてください。**

各スライドの HTML コメントには発表用の台本と時間配分を記載しています。Marp のプレゼンターモードで参照できます。PDF には台本は出力されません。

## ディレクトリ構成

```
docs/presentations/
├── README.md
├── 2026-08-06-ai-plugins-intro.md   # スライド本体（Marp Markdown）
├── 2026-08-06-ai-plugins-intro.pdf  # 上記から書き出した配布用 PDF
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
cd docs/presentations

npx @marp-team/marp-cli@4 --pdf --pdf-outlines --allow-local-files 2026-08-06-ai-plugins-intro.md
npx @marp-team/marp-cli@4 --html --allow-local-files 2026-08-06-ai-plugins-intro.md
```

`--allow-local-files` は `images/` のローカル PNG を埋め込むために必要です。`--pdf-outlines` は PDF にしおりを付けます。コミットする PDF はこの指定で生成してください。

台本も配る場合は `--pdf-notes` を付けると、各スライドの HTML コメントが PDF の注釈として埋め込まれます。

PDF 出力には Chromium が必要です。見つからない場合は取得してパスを渡します。

```bash
npx playwright@1.49 install chromium
export CHROME_PATH="$HOME/.cache/ms-playwright/chromium-1148/chrome-linux/chrome"
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
