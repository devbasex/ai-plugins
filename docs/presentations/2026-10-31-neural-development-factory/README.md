# AI が回すソフトウェア工場 — NDF から Neural Development Factory へ

社外の勉強会・登壇で話すスライド（台本の 31 ページと、章扉 3 つ・中締めの扉 1 つの 35 ページ）。Software Factory の考え方を冒頭に置き、
前半で NDF（工場のラインをプラグインにしたもの）を、後半で Neural Development Factory（そのラインを Web から回す
サービス）の構想と画面モックを紹介する。前身は [2026-10-17-skill-plugin-development](../2026-10-17-skill-plugin-development/README.md)
で、NDF の紹介のページはそこから流用して並べ直した。`release` の `form-slide.md` の生成スクリプトの経路で作った。

| ファイル | 中身 |
| --- | --- |
| [script.md](script.md) | 台本（第 1 稿）。ページごとの見出しと読み上げ。スピーカーノートの正本。1 文 1 行で書き、`>` だけの行で段落を分ける。改行と空行はそのままノートに入る（`build-deck.js` が読み直す） |
| [notes.md](notes.md) | 構成メモ。章立てと、ページごとの見出し・役割・根拠 |
| [slides/build-deck.js](slides/build-deck.js) | 生成スクリプト。スライドの正本 |
| [slides/theme.json](slides/theme.json) | トーン＆マナーのファイル |
| `slides/lib/` | NDF の汎用テンプレートの枠組み（`release` の `assets/slides/lib/`）の写し。書き換えない |

## 成果物

Google Slides を成果物にする。2026-10-17 の資料の Google Slides を同じ URL のまま差し替えた（前の版は Google の版の
履歴に残る）。pptx は生成スクリプトから作り直せるため、コミットしない。Google Slides の URL はリポジトリに書かない。

## トーン＆マナー

暗い地（`0A0E1A`）にシアン（`22D3EE`）とバイオレット（`7C5CFF`）を差し色にした。承認など人が判断する箇所は琥珀
（`FFB547`）の地に暗い字で示す。書体は既定値（Noto Sans JP）。ロゴは置かない。章扉の大きな章番号・表紙と章扉の
半透明の円・見出しの下のアクセントの線は、`lib/` を変えずに生成スクリプトが描き足す。

| 項目 | 値 |
| --- | --- |
| 版面 | 16:9（10 × 5.625 インチ） |
| 書体 | 既定値 |
| 色 | `theme.json` の `colors` |
| 著作権表示 | `NDF — github.com/devbasex/ai-plugins` |
| ロゴ | なし |
| 写した時点の `lib/` の内容のハッシュ | `e1fbac1642aef0fe49a57b00d7a2575eaf363bbfd5c55b324d53aa778f632800`（`cat slides/lib/*.js \| sha256sum`） |

## 画面モック（25〜29 ページ）

Neural Development Factory の画面モック（claude.ai の Design のキャンバス「Neural Development Factory 画面モック」。
#2172・#2173 の本文から辿れる）のうち、スプリント一覧・スプリント詳細・承認ゲート・判断・スプリントを始めるの 5 画面を、
スライドの図形で描き直した。スクリーンショットにしないのは、スライドの大きさで文字が読めるように要素を絞るためと、
Google Slides の上で直せるようにするためである。出ている数値・リポジトリ名は例。

## 生成と確認の手順

```bash
cd docs/presentations/2026-10-31-neural-development-factory
(cd slides && npm ci)
node slides/lib/script-time.js script.md --limit 45   # 尺（約 21 分の読み上げ）
node slides/build-deck.js /tmp/ndf-factory.pptx
```

Google Slides への差し替えと、ページごとのサムネイルでの確認は `release` の `form-slide.md` の「変換と確認」の順に行う。

## 発表日時点の記録

発表日時点の記録で、以後の NDF と Factory の変更に追随しない（[../README.md](../README.md) の「この資料の位置づけ」）。
数値は 2026-10-31 時点の実測（事例は 2026-10-17 時点）。
