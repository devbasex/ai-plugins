# 用語集の形式

プロジェクトのユビキタス言語は、プロジェクトのリポジトリに置く 3 つのファイルで持つ。
NDF の `scripts/glossary.py` はこの形でだけ読み、語はすべて用語集から読む。

| ファイル | 書き手 | 役割 |
| --- | --- | --- |
| 用語集の設定（`.ndf/glossary.json`） | `glossary.py init` が初めの 1 回。後はプロジェクトの変更 | 用語集の置き場・形式・検査の対象 |
| 用語集（設定の `source`） | プロジェクトの変更（Pull Request） | 語・意味・コンテキスト・廃止した語・識別子・正本。**正** |
| 人が読む文書（設定の `document`） | `glossary.py render` だけ | 用語集から作る写し。手で直さない |

## 例

用語集の無いプロジェクトで `glossary.py init` を打つと、次の 3 つができる（置き場は既定の値）。

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
    {"term": "注文", "context": "shipping", "meaning": "倉庫へ出す配送の依頼"}
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
| `check.term_sections` | 文字列の配列 | 許す | 未登録の語とみなす表を持つ節の見出し。無いときは `["用語"]` |
| `check.source_paths` | 文字列の配列 | 許す | 語の正本（`terms[].source`）に認めるパスの glob。確定した文書（確定仕様・リリースした手順）だけを書く。空は「正本の置き場を見ない」。当たらないパスは `unconfirmed_source` で落とす（URL は見ない） |

**当たった規則を止める項目は持たない。** 当たった規則は必ず落とす。プロジェクトが変えられるのは、
どの文書を見るか（`check.paths`）と、どの表を語とみなすか（`check.term_sections`）である。

**パスはリポジトリの内側に限る。** `source` / `document` / `check.paths` に、絶対パス・`..` を含むもの・
シンボリックリンクを解いてリポジトリの外を指すものを書くと、どの副命令も何も書かずに終了コード 2 で止まる。

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
| `terms[].source` | 文字列 | 許す | 正本（その語の意味を決めている確定した文書のパスか URL）。空は「用語集そのものが正本」 |
| `terms[].pending_source` | 文字列 | 許す | 確定前の出所（語を決めた設計文書のパス）。`plan-to-spec` の `spec-finalize` がその設計を消すときに、`source` を確定仕様へ移してこの項目を消す。指す文書が無ければ `check` の `schema` に当たる |
| `terms[].code` | 文字列 | 許す | コードで使う識別子の基本形。英小文字の snake_case（例: `approval_gate`）。コードに現れない語は項目を書かない |
| `terms[].deprecated_code` | 文字列の配列 | 許す | 廃止した識別子の基本形（例: `gate`）。形は `code` と同じ |

**設計の工程で足す語は `source` を空にし、`pending_source` に設計文書を書く。** 設計文書は確定前の
仕様で、承認の後も確定仕様になるまで書き換わる。正本にすると、確定仕様へ移った後も消えた文書を指す。

- **ほぼ空の用語集**は `contexts` と `terms` が空の配列のものである。設計の工程へ進める
- 語の履歴は git が持つ。意味を上書きしても前の意味はコミットに残り、`glossary.py diff` が取り出す
- 並びは書いた順のまま文書に出る。並べ替えると、文書の差分が用語集の差分と対応しなくなる

### 識別子の書き方

**識別子は基本形（snake_case）を 1 つだけ持ち、ほかの書き方は `glossary.py` の `spellings` が導く。**

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

`render` が、コンテキストごとの見出しと 6 列の表（語・識別子・意味・廃止した語・廃止した識別子・正本）を書く。セルの中の
`|` は `\|` に、改行は空白に置き換える。用語集を変えて `render` を打っていない変更は、`check` が
`stale_document` で落とす。

## 用語チェックの規則

`glossary.py check` の規則。`--rules structure` は上の 3 つだけ、`all`（既定）は 6 つを見る。

| 規則 | 見るもの | 当たる条件 |
| --- | --- | --- |
| `schema` | 用語集 | 必須の項目が無い・廃止した語が同じコンテキストの生きた語と重なる・`context` が宣言されていない・1 文字の廃止した語・`code` / `deprecated_code` が snake_case でない・同じコンテキストで同じ `code` が 2 回ある・廃止した識別子が同じコンテキストの生きた識別子と重なる |
| `duplicate` | 用語集 | 同じコンテキストで同じ語が 2 回ある |
| `stale_document` | 文書 | 用語集から `render` した内容と一致しない |
| `deprecated` | 追加した行（`--file` ならファイル全体） | 廃止した語が現れ、その語がどのコンテキストの生きた語にも無い |
| `unregistered` | 追加した行のうち `term_sections` の節の表の 1 列目 | 生きた語としてどのコンテキストにも無い |
| `deprecated_code` | コード（`.py` / `.sh` / `.js` / `.ts`）の追加した行（`--file` ならファイル全体） | 廃止した識別子か、その書き方を変えた形が現れ、その形がどの生きた識別子の書き方にも無い |

- **未登録の語とみなすのは、見出しが `term_sections` の節の表の 1 列目だけである。** 要求の「用語」の節と、
  設計のドメインモデルの節の「用語」の小見出しが当たる。本文や別の表でだけ使った語は当てない。
  新しい語を使うときは、同じ変更でこの表に書き、用語集へ足す（`pending_source` にこの文書を書く）
- 照合から外すもの: コードブロック・インラインコード・設定の `document` の文書。廃止した語を説明の
  ために書くときはインラインコードで囲む
- 廃止した語の出現が生きた語の出現の内側にあるとき（廃止した `カート` と生きた `カートン`）は当てない
- 英数字だけの語は単語の境界で照合する（`cart` は `cartridge` に当たらない）
- 識別子は英数字と `_` の境界で照合する。廃止した `gate` は `approval_gate` にも `ApprovalGate` にも当たらない。
  生きた識別子の出現の内側（`approval-gate` の `gate`）も当てない。コードは `check.paths` の外でも見る。
  コードに `deprecated` と `unregistered` は当てない
- `--diff BASE` は `BASE` と `HEAD` の分岐点からの追加した行を見る。`BASE` が先へ進んでも、ほかの変更が
  足した行は当たらない。追跡していないファイルは全行を見る。見るのは `check.paths` に当たるファイルだけである
- 当たりは標準エラーへも `ERROR: <path>:<line>: <rule>: <term>` の 1 行ずつで出す

## 副命令

結果は `lib/step_result.py` の形の 1 行の JSON（`tool: "glossary"`）で出す。`--root` の既定は `.`。

| 副命令 | すること | 終了コード |
| --- | --- | --- |
| `gate --mode M` | 設計の工程の入口の検査。止めたときは作る手順の 3 行を `items` に出す | 0 = 通す（`M` が `standard` / `legacy-refactor` 以外、または揃っている）。1 = 用語集の設定・用語集・文書のどれかが無い。2 = 読めない |
| `init [--source P] [--document P]` | 3 つのうち欠けたものだけを作る。在るファイルは読めなくても書き換えない | 0 = 作った・揃っている。2 = 書けない・読めない・境界を越える |
| `candidates [--limit N]` | 追跡しているファイルから語の候補を集める（`table`: 用語の表の 1 列目・`bold`: 12 字以下の太字で 3 回以上・`type`: 型の名前）。用語集にある語は除く | 0。0 件なら `summary` に「スクラッチ」 |
| `render [--check]` | 文書を作る。`--check` は書かずに一致を見る | 0 = 書いた・一致した。1 = 一致しない。2 = 読めない |
| `check [--diff BASE \| --file P...] [--rules all\|structure]` | 上の規則 | 0 = 当たりなし（用語集の設定が無いときも 0）。1 = 当たりあり。2 = 読めない |
| `diff --base REF [--head REF]` | 2 つの版の間の `added` / `meaning_changed` / `deprecated` / `removed` を返す。`--head` の既定はワーキングツリー | 0。無い版は空の用語集として比べる |
