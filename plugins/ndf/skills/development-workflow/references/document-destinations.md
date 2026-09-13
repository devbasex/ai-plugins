# 文書の提出先の宣言

**文書の提出先は git の外にある。** `.ndf/worktree.json` の `production_branch` は git の
ブランチしか指せないため、**制作物承認の関門はこの宣言を読む**。

**宣言が無ければ何も起きない。** `worktree.json` と `projects.json` に続く 3 つ目の宣言
ファイルで、無いリポジトリでは提出の工程が「提出先が宣言されていない」と伝えて止まる。
承認を求める対象そのものが無いためである。

## 置き場所と形

`.ndf/document.json` に置く。

```json
{
  "version": 1,
  "source_root": "docs/documents",
  "destinations": [
    {
      "name": "proposal-drive",
      "system": "gdrive",
      "location": "https://drive.google.com/drive/folders/<識別子>",
      "visibility": "internal",
      "production": true,
      "draft": "proposal-drive-draft",
      "auth": { "kind": "env", "keys": ["GOOGLE_APPLICATION_CREDENTIALS"] }
    },
    {
      "name": "proposal-drive-draft",
      "system": "gdrive",
      "location": "https://drive.google.com/drive/folders/<識別子>",
      "visibility": "internal",
      "production": false,
      "auth": { "kind": "env", "keys": ["GOOGLE_APPLICATION_CREDENTIALS"] }
    }
  ],
  "index": { "system": "notion", "location": "https://www.notion.so/<識別子>" }
}
```

| キー | 何を書くか |
| --- | --- |
| `source_root` | Markdown の正本を置く根。リポジトリの根からの相対パス |
| `destinations[].name` | 提出先の名前。`draft` が指す先になる |
| `destinations[].system` | `gdrive` / `notion` / `confluence` / `sharepoint` / `repo` |
| `destinations[].location` | 提出先の場所（フォルダ・データベース・空間の URL） |
| `destinations[].visibility` | 期待する公開範囲。提出の後にこの値と突き合わせる |
| `destinations[].production` | **真なら本番の系。** 届く操作が制作物承認の関門になる |
| `destinations[].draft` | 本番の提出先が指す、対になる下書き先の `name` |
| `destinations[].auth` | 認証の**出所**。環境変数の名前や秘密情報の管理系の識別子 |
| `index` | 索引への登録先。無ければ索引への登録を飛ばす |

**認証情報そのものは書かない。** `auth` が持つのは出所だけである。

## 本番の提出先には対の下書き先が要る

**承認の前に書き込んでよいのは `production` が偽の提出先だけである。** 生成・取り込みと
内容照合・体裁レビューはすべて下書き先で行い、真の提出先へは承認した生成物だけが届く。
既存の正式な文書を改訂する場合も、真の提出先を直接書き換えない。

**対の下書き先は `system` が同じでなければならない。** 系が違うと、承認した生成物をそのまま
移せず作り直しになり、承認したものと届くものが別になる。

**「偽の提出先がどこかに 1 つある」では足りない。** 本番の提出先ごとに 1 つ要る。

### 解決できないときは止める

| 状態 | 扱い |
| --- | --- |
| `draft` の記載が無い | その提出先は使えない |
| `draft` が指す先が無い | 同上 |
| `draft` が指す先の `production` が真 | 同上 |
| `draft` が指す先の `system` が違う | 同上 |

**いずれも提出の工程で止める。** 本番へ出してから戻す形は採らない。取り消しても、その状態を
見た人は戻らない。

## 検証への提出にあたるもの

`production` が偽の提出先への操作は、開発側の「検証への配布」に当たる。**承認を求めない。**

## 関連

- [approval-request.md](approval-request.md) — 承認を求めるときに提示するもの
- `document-systems` — システムごとの認証・取得・投稿・描画の手順
