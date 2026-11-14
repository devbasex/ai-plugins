# Google Drive

**このリポジトリで実行して確かめた。** 取得は MCP の `read_file_content` で動く。

## 認証

| 経路 | 出所 |
| --- | --- |
| MCP | 利用者のセッションが持つ。設定は要らない |
| gws（`google-workspace`） | gws が自分の設定ディレクトリに持つ。導入と認証の案内は `google-workspace` が持つ |

**値は設定に書かない。** `.ndf/document.json` の `auth` に書くのは環境変数の名前だけである。

**gws の経路は認証が済んでいないと動かない。** 利用者が `gws auth login` でブラウザから承認する。
継続的統合で動かすなら、gws にトークンを渡す前提を先に用意する。

## 取り込みの手段と取れないもの

| 対象 | 手段 |
| --- | --- |
| Google スライド | MCP の `read_file_content` |
| Google ドキュメント | 同上 |
| Google スプレッドシート | 同上 |
| PDF へ変換して取得 | `google-workspace` の対応表のエクスポート（`mimeType` は `application/pdf`） |

**取れないもの。**

| 取れないもの | 影響 |
| --- | --- |
| スライドの区切り | 10 枚が 1 続きのテキストになる。**ページ番号が本文へ連結する**（`© … / Confidential2` のような形） |
| 改行 | 段落の区切りが失われ、全体がほぼ 1 行になる |
| 版面・図形の位置・大きさ・色 | **体裁は取れない。** 描画して見る |
| 図そのもの | 参照だけを残す |
| コメント | `includeComments` を渡しても出なかった。**未確認**（対象にコメントが無かった可能性がある） |

正規化は [import.md](import.md) が定める。

## 投稿の手段

**Markdown から Google スライド / ドキュメントを作る手段は、この Skill には無い。** 生成の経路は
`release` の `form-<出力の形>.md` が決める。

**アップロード・変換・共有のコマンドは `google-workspace` の対応表が持つ。** 内容をここへコピーしない。

**Google スライドは、生成スクリプトが作った pptx を変換して作る**（`release` の `form-slide.md`）。

| 場合 | 順 |
| --- | --- |
| 新しく作る | pptx をフォルダを指定せず `ignoreDefaultVisibility` を付けて上げ（非公開。`google-workspace` の対応表の変換の 1 手目）、応答の `id` を `mimeType` を Google Slides に指定して複製する。複製が成果物である |
| 既にある Google スライドを直す | 同じファイルの ID へ pptx を上げ直す。**URL と共有の設定はそのまま残る。** pptx のあるディレクトリで打つ |

フォルダへ入れるときは、先にフォルダの共有範囲を確かめる（`google-workspace` の手順）。

## 本文の表現

Google のネイティブ形式（スライド / ドキュメント / スプレッドシート）である。
**Markdown をそのまま受け取らない。** 変換した結果を上げる。

## 版の扱い

**Drive は版を保持する。** 同じファイルを更新すると版が増え、前の版へ戻せる。
**ただし「その状態を見た人」は戻らない。**

## 図の扱い

図は画像として貼る。**Mermaid をそのまま描画しない。** 画像へ変換してから貼る。

## 描画して見る手段

| 対象 | 手段 |
| --- | --- |
| Google スライド | **ページごとのサムネイル。** `google-workspace` の `slides-thumbnails.py` が全ページを `p<番号>.png` で保存する。Google Slides の描画をそのまま見られる |
| Google ドキュメント | `google-workspace` の対応表のエクスポート（`mimeType` は `application/pdf`）で PDF にしてから、ページを画像へ変換する |

**体裁レビューはこの経路を使う。**

## 既知の失敗

| 事象 | 対処 |
| --- | --- |
| gws が未認証で失敗する | `google-workspace` の手順で確かめ、利用者に `gws auth login` を頼む（問うときの `metadata.source` は `ndf-ask:human-action`） |
| 取り込んだテキストがほぼ 1 行になる | 正規化する（[import.md](import.md)） |
| ページ番号が本文へ連結する | 同上 |
| pptx で差し替えた Google スライドの本文の書体が、pptx の指定でなく Arial になる（同じ pptx の差し替え 4 回のうち 1 回で起きた） | サムネイルで書体が変わっていないかを見る。疑わしければ `gws slides presentations get --params '{"presentationId":"<ID>","fields":"slides(pageElements(shape(text(textElements(textRun(style(fontFamily)))))))"}'` で書体を数え、トーン＆マナーの書体でなければ差し替えを打ち直す（空白だけの文字列は既定の書体のまま残ってよい） |
