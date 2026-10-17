# 独自スキルプラグイン開発の薦め

社外の勉強会・登壇で話す 23 ページのスライド。NDF の機能を例に、自分用のスキルプラグインを作って育てることを薦める。
`release` の `form-slide.md` の生成スクリプトの経路で作った。

| ファイル | 中身 |
| --- | --- |
| [script.md](script.md) | 台本（第 7 稿の写し）。ページごとの見出しと読み上げ。スピーカーノートの正本 |
| [notes.md](notes.md) | 構成メモ。ページごとの見出し・役割・根拠の表と、尺 |
| [slides/build-deck.js](slides/build-deck.js) | 生成スクリプト。スライドの正本 |
| [slides/theme.json](slides/theme.json) | トーン＆マナーのファイル |
| `slides/lib/` | NDF の汎用テンプレートの枠組み（`release` の `assets/slides/lib/`）の写し。書き換えない |

## 成果物

Google Slides を成果物にする。pptx は生成スクリプトから作り直せるため、コミットしない。Google Slides の URL は
リポジトリに書かず、作った Pull Request の記録に残す。

## トーン＆マナー

汎用テンプレートの既定値（書体 Noto Sans JP、強調の色 `2B6CB0`）を使い、`theme.json` では著作権表示の文言だけを
足した。ロゴは置かない。

| 項目 | 値 |
| --- | --- |
| 版面 | 16:9（10 × 5.625 インチ） |
| 書体 | 既定値 |
| 色 | 既定値 |
| 著作権表示 | `NDF — github.com/devbasex/ai-plugins` |
| ロゴ | なし |
| 写した時点の `lib/` の内容のハッシュ | `86c3acf6f8b74c80bb92ffa3a1dfe17cde6ee17d199e6f76d029744f7d6a9133`（`cat slides/lib/*.js \| sha256sum`） |

## 生成と確認の手順

```bash
cd docs/presentations/2026-10-17-skill-plugin-development
(cd slides && npm ci)
node slides/lib/script-time.js script.md --limit 35   # 尺（出力は notes.md の「尺」）
node slides/build-deck.js /tmp/skill-plugin-development.pptx
```

Google Slides への変換と、ページごとのサムネイルでの確認は `release` の `form-slide.md` の「変換と確認」の順に行う。

## 発表日時点の記録

発表日時点の記録で、以後の NDF の変更に追随しない（[../README.md](../README.md) の「この資料の位置づけ」）。
数値は 2026-10-17 時点の実測。
