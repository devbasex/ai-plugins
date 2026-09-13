# Confluence

**このリポジトリでは実行して確かめていない。** 別のリポジトリにある実装の知識から書いた。
手順どおりに動かないときは、実測した結果でこのファイルを直す。

## 認証

| 項目 | 内容 |
| --- | --- |
| 方式 | Basic 認証（利用者の識別子とトークンを組にする） |
| 出所 | `.ndf/document.json` の `auth` が指す環境変数、または秘密情報の管理系の識別子 |

**MCP は認証で失敗しやすい。** `Authentication credentials are missing` を返す事象が
報告されている。**REST を直接呼ぶ経路を既定にする。**

**サイトの URL はリポジトリごとに違う。** 宣言の `location` が持つ。この Skill には書かない。

## 取り込みの手段と取れないもの

| 対象 | 手段 |
| --- | --- |
| ページの本文 | REST v2 の `GET /wiki/api/v2/pages/{id}?body-format=storage` |

**取れるのは storage 形式（XHTML）である。** Markdown ではない。

**取れないもの。**

| 取れないもの | 影響 |
| --- | --- |
| 版面 | **体裁は取れない。** 描画して見る |
| マクロの描画結果 | マクロは記述のまま返る |

**差分の取り方に既存の形がある。** 正規化して Markdown へ寄せるのではなく、**以前投稿した
storage をそのまま控えておき、次に取得した storage と比べる**方法が実装されている。
どちらを採るかは [import.md](import.md) が定める。

## 投稿の手段

| 段階 | 手段 |
| --- | --- |
| 変換 | Wiki マークアップから storage へ変換する（v1 の API） |
| 投稿 | ページの更新（`PUT`） |
| 添付 | 添付の API（v1） |

**マクロとコードブロックは、変換の前に差し替える。** そのまま変換すると
`UnknownMacroMigrationException` で失敗する。置き換え用の印を入れてから変換し、変換後に
戻す。

## 本文の表現

`storage`（XHTML）が既定である。`atlas_doc_format` と `wiki`（変換のみ）もある。

## 版の扱い

**下書きと公開中で扱いが違う。**

| 状態 | 版 |
| --- | --- |
| 下書き | **版は 1 に固定される** |
| 公開中 | 更新のたびに増える |

**混同すると `DRAFT pages do not support multiple versions` で失敗する。**

## 図の扱い

**PlantUML のマクロは、クラウド版では `Migration Required` と表示されて描画されない。**
図は画像へ変換して添付し、添付を参照する形（`<ac:image><ri:attachment>`）で載せる。

**Mermaid も同じ扱いになる可能性がある。未確認。**

## 描画して見る手段

**未確認である。** 公開したページの URL を `playwright-kit` の `playwright-evidence` で撮る
案がある。手段が無いときの倒し方は `layout-review` が持つ。

## 既知の失敗

| 事象 | 対処 |
| --- | --- |
| MCP が認証で失敗する | REST を直接呼ぶ |
| `DRAFT pages do not support multiple versions` | 下書きと公開中で版の扱いを分ける |
| `UnknownMacroMigrationException` | マクロを印へ差し替えてから変換する |
| PlantUML が `Migration Required` になる | 画像へ変換して添付する |
