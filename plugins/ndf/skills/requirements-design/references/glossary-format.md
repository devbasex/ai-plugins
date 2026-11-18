# 用語集の形式

プロジェクトのユビキタス言語は、プロジェクトのリポジトリに置く 3 つのファイルで持つ。
NDF（`ndf records glossary`・`ndf records spec`）はこの形でだけ読み、語はすべて用語集から読む。

| ファイル | 書き手 | 役割 |
| --- | --- | --- |
| 用語集の設定（`.ndf/glossary.json`） | 初めの 1 回は `requirements-design` の手順 0 で手で書く。後はプロジェクトの変更 | 用語集の保存先・形式・検査の対象 |
| 用語集（設定の `source`） | プロジェクトの変更（Pull Request） | 語・意味・コンテキスト・廃止した語・識別子・SSOT。**正** |
| 人が読む文書（設定の `document`） | `ndf records glossary render` だけ | 用語集から作るコピー。手で直さない |

## 例

用語集の無いプロジェクトでは、用語集の設定とほぼ空の用語集を次の形で書き（保存先は既定の値）、`ndf records glossary render` で人が読む文書を作る。

```json
{
  "version": 1,
  "format": "json",
  "source": "docs/glossary/glossary.json",
  "document": "docs/glossary.md",
  "check": {
    "paths": ["issues/*-requirements.md", "issues/*-design.md", "issues/*-design-decisions.md"],
    "term_sections": ["用語"]
  }
}
```

採った語を足した用語集は次の形になる。

```json
{
  "version": 1,
  "contexts": [
    {"id": "ordering", "name": "受注", "meaning": "注文を受けて確定するまで"},
    {"id": "shipping", "name": "配送", "meaning": "確定した注文を届けるまで"}
  ],
  "terms": [
    {"term": "注文", "context": "ordering", "meaning": "顧客が買うと決めた品の組",
     "deprecated": ["オーダー"], "code": "order", "deprecated_code": ["purchase_order"],
     "source": "docs/ordering.md"},
    {"term": "注文", "context": "shipping", "meaning": "倉庫へ出す配送のリクエスト"}
  ]
}
```

`注文` は 2 つのコンテキストで別の意味を持つ。1 語 1 意味はコンテキストごとに守る。受注の `注文` は
コードで `order` と書き、前の `purchase_order` は廃止した識別子として `check` が拾う。

## 用語集の設定（`.ndf/glossary.json`）

| 項目 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `version` | 整数 | 許さない | `1`。ほかの値は終了コード 2 |
| `format` | 文字列 | 許さない | 用語集の形式。`json` だけ。ほかの値は終了コード 2 |
| `source` | 文字列 | 許さない | 用語集のファイルのパス（リポジトリの根から） |
| `document` | 文字列 | 許さない | 人が読む文書のパス |
| `check.paths` | 文字列の配列 | 許す | 追加した行を見る文書の glob。空は「どの文書も語を見ない」（用語集の形と文書の一致は見る） |
| `check.term_sections` | 文字列の配列 | 許す | 未登録の語とみなす表を持つセクションの見出し。無いときは `["用語"]` |
| `check.source_paths` | 文字列の配列 | 許す | 語の SSOT（`terms[].source`）に認めるパスの glob。確定した文書（確定仕様・リリースした手順）だけを書く。空は「SSOT の保存先を見ない」。当たらないパスは `unconfirmed_source` で落とす（URL は見ない） |
| `check.banned` | オブジェクト | 許す（無ければ規則 `banned` を走らせない） | 禁止語の設定。`words`（禁止語の配列）・`paths`（検査する範囲の glob）・`exclude`（外す範囲の glob。無くてよい） |

**当たった規則を止める項目は持たない。** 当たった規則は必ず落とす。プロジェクトが変えられるのは、
どの文書を見るか（`check.paths`）と、どの表を語とみなすか（`check.term_sections`）である。

**パスはリポジトリの内側に限る。** `source` / `document` / `check.paths` に、絶対パス・`..` を含むもの・
シンボリックリンクを解いてリポジトリの外を指すものを書くと、どのサブコマンドも何も書かずに終了コード 2 で止まる。

## 用語集（`source`）

| 項目 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `version` | 整数 | 許さない | `1` |
| `contexts[].id` | 文字列 | 許さない | コンテキストの識別子。用語集の中で一意 |
| `contexts[].name` | 文字列 | 許さない | 文書の見出しに使う名前 |
| `contexts[].meaning` | 文字列 | 許す | 何の語が 1 つの意味に決まる範囲か。空は「まだ書いていない」 |
| `terms[].term` | 文字列 | 許さない | 語 |
| `terms[].context` | 文字列 | 許さない | `contexts[].id` のどれか |
| `terms[].meaning` | 文字列 | 許さない | 意味 |
| `terms[].deprecated` | 文字列の配列 | 許す | 廃止した語（旧称）。2 文字以上 |
| `terms[].deprecated_standalone` | 文字列の配列 | 許す | 単独でマッチする廃止した語。1 文字でよい。各語の先頭か末尾が漢字である。先頭が漢字なら直前が漢字の出現に、末尾が漢字なら直後が漢字の出現に当てない（`札` は `改札`・`札束` に、`型で持` は `原型で持つ` に当たらない） |
| `terms[].source` | 文字列 | 許す | SSOT（その語の意味を決めている確定した文書のパスか URL）。空は「用語集そのものが SSOT」 |
| `terms[].pending_source` | 文字列 | 許す | 確定前の出所（語を決めた設計文書のパス）。`plan-to-spec` の `spec-finalize` がその設計を消すときに、`source` を確定仕様へ移してこの項目を消す。指す文書が無ければ `check` の `schema` に当たる |
| `terms[].code` | 文字列 | 許す | コードで使う識別子の基本形。英小文字の snake_case（例: `approval_gate`）。コードに現れない語は項目を書かない |
| `terms[].deprecated_code` | 文字列の配列 | 許す | 廃止した識別子の基本形（例: `gate`）。形は `code` と同じ |

**設計の工程で足す語は `source` を空にし、`pending_source` に設計文書を書く。** 設計文書は確定前の
仕様で、承認の後も確定仕様になるまで書き換わる。SSOT にすると、確定仕様へ移った後も消えた文書を指す。

- **ほぼ空の用語集**は `contexts` と `terms` が空の配列のものである。設計の工程へ進める
- 語の履歴は git が持つ。意味を上書きしても前の意味はコミットに残り、`git diff <前のバージョン> -- <source>` で取り出す
- 並びは書いた順のまま文書に出る。並べ替えると、文書の差分が用語集の差分と対応しなくなる

### 識別子の書き方

**識別子は基本形（snake_case）を 1 つだけ持ち、ほかの書き方は下の表の規則で導く。**

| 使う場所 | 書き方 | `approval_gate` の例 |
| --- | --- | --- |
| 変数・関数・JSON のキー | 基本形のまま | `approval_gate` |
| クラス | PascalCase（`_` で区切った各部の頭を大文字にしてつなぐ） | `ApprovalGate` |
| 定数 | 大文字 | `APPROVAL_GATE` |
| CLI の引数・ファイル名 | kebab-case（`_` を `-` にする） | `approval-gate` |

- 1 つのコンテキストで同じ識別子を 2 つの語に使わない（1 語 1 意味と同じ）
- 業界の英語に定着した名前があれば採る。日本語の語の直訳にしない
- 今のコードで名前がばらけている語は 1 つを `code` に決め、ほかを `deprecated_code` に入れる

## 人が読む文書（`document`）

`render` が、コンテキストごとの見出しと 6 列の表（語・識別子・意味・廃止した語・廃止した識別子・SSOT）を書く。廃止した語の列は、
`deprecated` の語の後に単独でマッチする廃止した語を `札（単独）` の形で並べる。セルの中の
`|` は `\|` に、改行は空白に置き換える。用語集を変えて `render` を打っていない変更は、`ndf records glossary render --check` が
食い違いとして止める。

## 用語チェックの規則

用語集と文書を照らすときの規則である。**機械が確かめるのは `stale_document`（`ndf records glossary render --check`）と
`deprecated_meaning`（`ndf records glossary meanings`）の 2 つだけで、ほかは書いた側が用語集（設定の `source`）を読んで照らす。**
照らす範囲は、追加した行（`git diff <ベース>...HEAD`）か、渡されたファイル全体である。

| 規則 | 見るもの | 当たる条件 |
| --- | --- | --- |
| `schema` | 用語集 | 必須の項目が無い・廃止した語が同じコンテキストの生きた語と重なる・`context` が定義されていない・1 文字の廃止した語・`deprecated_standalone` が空でない文字列の配列でない・単独でマッチする廃止した語の先頭も末尾も漢字でない・単独でマッチする廃止した語が同じコンテキストの生きた語と重なる・`code` / `deprecated_code` が snake_case でない・同じコンテキストで同じ `code` が 2 回ある・廃止した識別子が同じコンテキストの生きた識別子と重なる |
| `duplicate` | 用語集 | 同じコンテキストで同じ語が 2 回ある |
| `stale_document` | 文書 | 用語集から `render` した内容と一致しない |
| `deprecated_meaning` | 用語集の意味の説明（`terms[].meaning`・`contexts[].meaning`） | どれかの項目の廃止した語（`deprecated`・`deprecated_standalone`）が現れる。除き方は `deprecated` と同じ。当たりの `detail` が見つけた語と置き換え先を示す。置き換え先のどの語の意味にも当たらない出現は、置き換え先へ写さず中身で書く |
| `deprecated` | 追加した行（ファイルを照らすときはファイル全体） | 廃止した語が現れ、その語がどのコンテキストの生きた語にも無い |
| `unregistered` | 追加した行のうち `term_sections` のセクションの表の 1 列目 | 生きた語としてどのコンテキストにも無い |
| `deprecated_code` | コード（`.py` / `.sh` / `.js` / `.ts`）の追加した行（ファイルを照らすときはファイル全体） | 廃止した識別子か、その書き方を変えた形が現れ、その形がどの生きた識別子の書き方にも無い |

- **未登録の語とみなすのは、見出しが `term_sections` のセクションの表の 1 列目だけである。** 要求の「用語」のセクションと、
  設計のドメインモデルのセクションの「用語」の小見出しが当たる。本文や別の表でだけ使った語は当てない。
  新しい語を使うときは、同じ変更でこの表に書き、用語集へ足す（`pending_source` にこの文書を書く）
- 検証から外すもの: コードブロック・インラインコード・設定の `document` の文書。廃止した語を説明の
  ために書くときはインラインコードで囲む
- 廃止した語の出現が生きた語の出現の内側にあるとき（廃止した `カート` と生きた `カートン`）は当てない
- 英数字だけの語は単語の境界でマッチする（`cart` は `cartridge` に当たらない）
- 識別子は英数字と `_` の境界でマッチする。廃止した `gate` は `approval_gate` にも `ApprovalGate` にも当たらない。
  生きた識別子の出現の内側（`approval-gate` の `gate`）も当てない。コードは `check.paths` の外でも見る。
  コードに `deprecated` と `unregistered` は当てない
- コードのコメント（`.py` / `.sh` の `#`、`.js` / `.ts` の `//` と `/* */`）の中は当てない。古い名前を説明する
  ために書けるようにする。文字列の中（`"--gate"`）はコードの一部として当てる
- 追加した行は、ベースと `HEAD` の分岐点からの差分で見る。ベースが先へ進んでも、ほかの変更が
  足した行は当たらない。追跡していないファイルは全行を見る。見るのは `check.paths` に当たるファイルだけである
- `banned`（禁止語）は `check.banned.paths` に当たり `exclude` に当たらないファイルを、ファイルの種類を問わず部分一致で
  照らす。コードのコメント・文字列・Markdown のコードブロックとインラインのコードも見る。
  用語集の語と意味の欄の禁止語も照らす（廃止した語の欄は見ない）。置き換え先は廃止した語の対応から出す

## サブコマンド

結果は 1 行の結果 JSON（`tool: "records"`）で出す。

| サブコマンド | すること | 結果 |
| --- | --- | --- |
| `ndf records glossary render [--check]` | 用語集から人が読む文書（コンテキストごとのファイルと索引）を作る。`--check` は書かずに一致を見る | `ok` = 書いた・一致した。設定か用語集が無ければ `precondition` で止まる |
| `ndf records glossary meanings` | 意味の説明（`terms[].meaning`・`contexts[].meaning`）に残った廃止した語を並べる（規則 `deprecated_meaning`） | `ok` = 当たりなし。当たりは `items` に語と見つけた語が載る |

用語集の衝突（`git merge` の途中）を解くサブコマンドは無い。語の単位で両側の変更を残して手で解き、`ndf records glossary render`
で文書を作り直す。
