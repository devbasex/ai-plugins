---
name: document-systems
description: "Hold the per-system steps for reading, posting and rendering documents in Google Drive, Notion, Confluence, SharePoint or the repository itself. Use when a document must be fetched from or published to one of them（ドキュメンテーションシステム・Confluenceへ投稿・SharePointから取り込む・システムごとの手順）."
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Bash
---

# ドキュメンテーションシステムごとの手順

**文書を置く先ごとに、認証・取得・投稿・描画の手順が違う。** この Skill はその違いを
1 システム 1 ファイルで持ち、**対象のシステムの参照だけを読ませる**。

## 3 つの工程がここを読む

**同じシステムの知識を 3 か所へ書かない。** 同じ規約が複数箇所にあると片方だけが更新される。

| 工程 | 読むもの |
| --- | --- |
| 配布（生成・提出） | そのシステムへの投稿の手順 |
| 素材の収集と出典の確定 | そのシステムからの取得と、取れないもの |
| 体裁レビュー | そのシステムが描画できる形式と、描画して見る手段 |

## 出力の形とは別の軸である

**形は「どう見えるか」、システムは「どこへ置くか」である。** 同じ形が複数のシステムに載る。

| 軸 | 値 | 持つ場所 |
| --- | --- | --- |
| 出力の形 | スライド / 文書 / 表計算 / ページ | `release` の `form-<出力の形>.md` |
| ドキュメンテーションシステム | Google Drive / Notion / Confluence / SharePoint / リポジトリ自身 | この Skill |

**形の参照に投稿の手順を書かない。** ページは Notion にも Confluence にも SharePoint にも
あり、投稿の手順だけが違う。

## 対象のシステムを決める

**この Skill が受け取るのは、システム名と用途である**（取得 / 投稿 / 描画）。
**用途によって、どのシステムを選ぶかが変わる。**

| 用途 | 選ぶシステム | 何が決めるか |
| --- | --- | --- |
| 投稿 | 文書を置く先 | `.ndf/document.json` の `destinations[].system`。**宣言が無ければ投稿へ進まない**（形と書き方は `development-workflow` の `references/document-destinations.md`） |
| 取得 | **素材や既存の文書がある側** | 呼び出し側が渡したシステム名。渡されないときは取得元の URL から見分ける |
| 描画 | 生成物が載っている先 | 生成した先。本番の提出先ではなく、対になる下書き先である |

**取得で提出先の宣言を要求しない。** 素材が Google Drive にあり、提出先が Notion である構成が
ある。提出先だけを見ると、取得の参照を取り違える。**提出先がまだ決まっていない既存文書の
取り込み（[references/import.md](references/import.md) の用途 2）も、宣言の有無によらず行う。**

取得元の URL から見分けるときの手掛かりは次のとおり。

| URL の形 | `system` の値 |
| --- | --- |
| `drive.google.com` / `docs.google.com` | `gdrive` |
| `notion.so` | `notion` |
| Confluence の空間（`/wiki/spaces/`） | `confluence` |
| SharePoint / OneDrive（`sharepoint.com`） | `sharepoint` |
| 対象のリポジトリの中のパス | `repo` |

**見分けが付かないときは推測せず、呼び出し側にシステム名を確かめる。** 別のシステムの参照を
読むと、取れないものの一覧が実際と食い違う。

| `system` の値 | 読む参照 |
| --- | --- |
| `gdrive` | [references/system-gdrive.md](references/system-gdrive.md) |
| `notion` | [references/system-notion.md](references/system-notion.md) |
| `confluence` | [references/system-confluence.md](references/system-confluence.md) |
| `sharepoint` | [references/system-sharepoint.md](references/system-sharepoint.md) |
| `repo` | [references/system-repo.md](references/system-repo.md) |

**対象のシステムの参照だけを読む。** 他のシステムの内容はコンテキストに載せない。
**システムを 1 つ足すときも、他の `system-*.md` は変更しない。**

## 各参照が持つ 8 項目

**どのシステムのファイルも同じ並びを持つ。** 読み手が探す場所を覚え直さずに済む。

| 項目 | 何を書くか |
| --- | --- |
| 認証 | 何で認証するか。**出所だけを書き、値は書かない** |
| 取り込みの手段と取れないもの | 何で取得するか。**取れないものを必ず書く** |
| 投稿の手段 | 何で書き込むか。作成と更新の違い |
| 本文の表現 | そのシステムが受け取る本文の形式 |
| 版の扱い | 版が増えるか、上書きか |
| 図の扱い | 図をどう載せるか。描画されない形式 |
| 描画して見る手段 | 生成物を画像にする手段。**無ければ「無い」と書く** |
| 既知の失敗 | 実測で踏んだもの |

**「取れないもの」を必ず書く。** 取得できたものだけを書くと、読み手は取れなかったものを
「取れた」と読む。取り込みの限界は `references/import.md` が扱う。

## 取り込みは共通の手順を持つ

**何を取り込めるかはシステムで違うが、取り込んだ後の扱いは共通である。**
用途・向き・時期・正規化・格納先は [references/import.md](references/import.md) にある。

## 既にある Skill は指すだけにする

**内容を写さない。** Google Drive の取得と Notion の記法は既に Skill がある。

| 既存の Skill | 持っているもの |
| --- | --- |
| `google-drive` | Google Drive / Docs の取得・アップロード・共有 |
| `google-auth` | Google API の OAuth2 の設定 |
| `notion-writing` | Notion のページの書き方（表・子ページの扱い） |

## 実測していないシステムがある

**このリポジトリで実行して確かめたのは Google Drive と Notion だけである。** Confluence と
SharePoint は別のリポジトリにある実装の知識から書いており、**各ファイルの冒頭にその旨を
書いてある**。手順どおりに動かないときは、実測した結果でそのファイルを直す。

## 関連

- `/ndf:release` — 生成と提出（形ごとの手順は `form-<出力の形>.md`）
- `/ndf:google-drive` — Google Drive / Docs の取得とアップロード
- `/ndf:notion-writing` — Notion のページの書き方
- `/ndf:development-workflow` — 提出先の宣言（`references/document-destinations.md`）
