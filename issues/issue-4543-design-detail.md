# #4543 の詳細設計: 現状と改善の案の書き分け

設計文書 [issue-4543-design.md](./issue-4543-design.md) の「どこを変えるか」を、ファイルと関数の粒度へ下ろした文書である。「決定」は設計文書の「決定の記録」の番号、「受け入れ条件」は[要求仕様](./issue-4543-requirements.md)の番号である。断りの無いパスは `plugins/ndf/` からの相対パスである。

新しい関数は 3 つで、シグネチャを変える既存の関数は無い。設計文書が挙げた「小見出しを外す関数」のほかに、要求仕様のコピー（課題の本文から作る `issues/issue-<番号>-requirements.md`）を名前で見分ける関数と、修正担当へ足す文を読む関数を足す。後の 2 つを足すのは、次の 2 つの決定を、ほかのモジュールの内部に触れずに満たすためである。決定 6 の「名前の形は `issue_docs.py` だけに置く」と、決定 7 の「指示の文は `review-criteria.json` に置く」である。

## 地図

パッケージ・モジュール構成（触るプログラムと定義だけ）:

```text
plugins/ndf/
├── contracts/
│   ├── data/{writing-criteria.md, review-criteria.json}
│   └── worker/kinds.json
└── src/ndf/
    ├── contracts/writing.py
    ├── core/{issue_docs.py, worker.py}
    └── domains/
        ├── records/reading.py
        ├── docrebuild/drive.py
        └── crossreview/{criteria_decide.py, drive.py, fixer.py}
```

依存の向き: `domains` と `core` が `contracts` を読み、`domains` が `core` を読む一方向で、循環は無い。`domains/crossreview/drive.py` と `fixer.py` は `src/ndf/contracts/writing.py` を直接読む。外す条件は呼ぶ側が持ち、`writing.py` は条件を知らない（決定 6）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/data/writing-criteria.md` | 変更 | 書き分けの小見出しと目印の正本 | — | — | — |
| `src/ndf/contracts/writing.py` | 変更 | 基準の本文を読み、目印の範囲を外す | `ndf.contracts` | `writing` | `without_distinction`（新設） |
| `src/ndf/domains/records/reading.py` | 変更 | 読み手役（会話を知らずに読み手の立場で読む AI）への指示を組む | `writing` | `reading` | `prompt`・`_require_prompt`（変更） |
| `contracts/worker/kinds.json` | 変更 | 小見出しを渡す作業を持つ | — | — | — |
| `src/ndf/core/worker.py` | 変更 | worker の規則文を組む | `writing`・`worker_kinds` | `worker` | `system_prompt`（変更） |
| `src/ndf/core/issue_docs.py` | 変更 | 課題の文書の名前の形を持つ | — | `issue_docs` | `requirements_issues`（新設） |
| `src/ndf/domains/docrebuild/drive.py` | 変更 | 要求仕様のコピーの名前を `issue_docs` から読む | `issue_docs` | `drive` | `requirements_of`（参照先の定数だけ変える） |
| `src/ndf/domains/docrebuild/requirements_copy.py` | 新設 | 要求仕様のコピーのうち直してよいセクションを決める | `adapters.markdown`・`adapters.github` | `requirements_copy` | `editable_sections`・`protected`（新設） |
| `src/ndf/domains/docrebuild/gate.py` | 変更 | 決まったセクションだけを直させ、ほかの範囲の変更を push せずに止める | `requirements_copy` | `gate` | `_allow_fix`・`_outside_sections`（新設） |
| `contracts/data/review-criteria.json` | 変更 | 修正担当へ足す扱いの文を持つ | — | — | — |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 指摘の基準の文を表から読む | 表 | `criteria_decide` | `fixer_requirements_copy`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 変更したファイルから小見出しを渡すかを決め、レビュー担当の指示を組む | `issue_docs`・`writing`・`criteria_decide` | `_Run` | `_prepare_round`・`_review_prompt`（変更） |
| `src/ndf/domains/crossreview/fixer.py` | 変更 | 修正担当の指示を組む | `writing`・`criteria_decide` | `FixRun` | `_fix_prompt`（変更） |
| `skills/design/references/design-template.md` | 変更 | 要約・全体像・目的を書く指示 | 基準の小見出し | — | — |
| `skills/pr/SKILL.md` | 変更 | PR の概要を書く指示 | 基準の小見出し | — | — |
| `skills/requirements-design/SKILL.md` | 変更 | 課題の本文の概要を書く指示 | 基準の小見出し | — | — |
| `skills/development-workflow/references/approval-request.md` | 変更 | 承認の文を書く指示 | 基準の小見出し | — | — |
| `skills/decision-request/SKILL.md` | 変更 | 質問の文を書く指示 | 基準の小見出し | — | — |
| `skills/document-style/SKILL.md` | 変更 | 現状を過去形で書くのは文書を直した経緯ではないと示す | 基準の小見出し | — | — |
| `skills/document-rebuild/SKILL.md` | 変更 | 指示の JSON で `distinction` を `false` にする文書を示す | — | — | — |

## 基準の小見出しと手順書

満たす決定: 決定 1・2・3・4・5、受け入れ条件 1・2・4・5

プログラムの関数は無い。足す文は設計文書の「どこを変えるか」の 2 つの表のとおりである。目印は `<!-- ndf-distinction-begin -->` と `<!-- ndf-distinction-end -->` で、それぞれ 1 行を単独で使う。置くのは、`### 現状と改善の案の書き分け` の前と、小見出しの最後（`## 4. 書き方` の前）である。3 章の中に収まるので、`writing.types_section` の切り出しにも入る。

## 小見出しを外す処理

満たす決定: 決定 6、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `without_distinction` | `src/ndf/contracts/writing.py` | 新設 | `(text: str) -> str` | 目印の行で囲まれた範囲を、目印の行ごと除いて返す |

- `without_distinction`
  - 事後条件: 目印の行が無い本文は、そのまま返る。目印の組が複数あれば、すべて外れる。範囲の外の行と改行は変わらない
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `without_distinction` | 目印が対にならない（始まりだけ、終わりだけ、入れ子） | 基準の本文の編集の誤り | 行を読む中と読み終わり | 呼び出し元へ `CriteriaBroken` を送出する | 呼び出し元の各経路は、基準を読めないときと同じ扱いにする（下の各セクションの「異常系の処理」） | 書き込みが無いので、基準を直して打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `DISTINCTION_BEGIN` | 定数 | `src/ndf/contracts/writing.py` | `<!-- ndf-distinction-begin -->` |
| `DISTINCTION_END` | 定数 | `src/ndf/contracts/writing.py` | `<!-- ndf-distinction-end -->` |

目印は、前後の空白を除いた行の全体が定数と一致するときだけ数える。文の中で目印に触れた行は数えない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `without_distinction` | `writing.types_section`、`core/durable_sweep.py` の `_drop_markers` | `src/ndf` を `def .*(strip\|remove\|drop\|without\|cut\|section)` と `ndf:…(begin\|start)` で検索 | 使えない。前者は見出しで切り出し、後者は本文の範囲を扱わない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `without_distinction` | 範囲が外れ、前後が残る。見出しの文を変えた本文でも外れる。配っている基準では結果が元と異なる | 目印が無い。組が 2 つ。文の中の目印 | 始まりだけ、終わりだけ、入れ子で `CriteriaBroken` |

## 読み手役への指示

満たす決定: 決定 6、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `prompt` | `src/ndf/domains/records/reading.py` | 変更 | `(entry: Mapping[str, Any]) -> str`（変えない） | `distinction` が `false` なら、型の章から小見出しを外して指示を組む |
| `_require_prompt` | `src/ndf/domains/records/reading.py` | 変更 | `(entry: Any) -> None`（変えない） | 必須のキーに加えて、任意の `distinction` を真偽として受け付ける |

`_require_keys` はキーの集合の完全な一致を見て、ほかの入力の確かめも使うので変えない。`_require_prompt` が、`entry` がオブジェクトのときに `distinction` を除いた写しを渡す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_require_prompt` | `distinction` が真偽でない（`null`・文字列・数） | 指示の JSON の誤り | キーの確かめの後 | 呼び出し元へ `ReadingInvalid` を送出する | 今の「入力の形が違う」の結果（`invalid`）と、その終了コード | 書き込みが無いので打ち直せる |
| `prompt` | 基準が読めない・目印が対にならない | 配布物の欠け・編集の誤り | `writing` | 呼び出し元へ `CriteriaBroken` を送出する | 基準を読めないときの今の扱いと同じ（この変更で扱いを足さない） | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prompt` | `false` で小見出しが無く、型の表は残る | キーが無い・`true` は小見出しがある | 目印が対にならない |
| `_require_prompt` | `distinction` のある入力を受け付ける | ほかの余分なキーは今どおり拒む | `"false"`・`null`・`0` を拒む |

## 作業の AI への指示

満たす決定: 決定 6、受け入れ条件 1・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `system_prompt` | `src/ndf/core/worker.py` | 変更 | `(kind: str, state_dir: Path, skills_dir: Path, sources: Sequence[Source] = ()) -> str`（変えない） | 基準の全文を足すとき、作業の定義が小見出しを求めなければ外す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `system_prompt` | 基準が読めない・目印が対にならない | 配布物の欠け・編集の誤り | 今の `CriteriaBroken` の `try` の中で外す処理も呼ぶ | 止める（`WorkerError` の `precondition`） | 基準を読めないときの今の扱いと同じ。worker を起動しない | 直して打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `distinction` | 作業の定義の任意のキー（真偽） | `contracts/worker/kinds.json` の `design`・`design-detail`・`design-check-fix`・`requirements` に `true` | 無ければ偽（小見出しを外す） |

`kinds.json` の `summary` と、`worker_kinds.work_kinds` の docstring のキーの並びに `distinction` を足す。読み手役の同じ名前のキーは省くと真で、作業の定義は省くと偽である。どちらも、省いたときに多くの入力が望むほうの値を既定にしている。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `system_prompt` | 4 つの作業は全文、基準を読むほかの作業は小見出しを除いた本文を持つ | 基準を読まない作業と、定義に無い作業（`sweep`）はどちらも持たない | 目印が対にならない基準で `precondition` |

`tests/worker/test_rules.py` の「基準を読む作業は全文を持つ」の確かめは、この分け方に合わせて直す。

## 課題の文書の名前

満たす決定: 決定 6・7、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `requirements_issues` | `src/ndf/core/issue_docs.py` | 新設 | `(path: str) -> frozenset[int]` | パスが要求仕様のコピーの形なら課題の番号 1 つの集合を返し、違えば空集合を返す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `REQUIREMENTS_FILE` | 定数（書く側の書式） | `src/ndf/core/issue_docs.py`（`docrebuild/drive.py` から移し、定義を残さない） | `issues/issue-{number}-requirements.md` |
| `REQUIREMENTS_PATH` | 定数（読む側の正規表現） | `src/ndf/core/issue_docs.py` | `^issues/issue-(\d+)-requirements\.md$` |

`docrebuild/drive.py` の `requirements_of` は `issue_docs.REQUIREMENTS_FILE` を読む。振る舞いは変えない。

要求仕様のコピーは課題ごとに 1 本で、名前の番号は 1 つである（`requirements-design` の手順 7）。複数の課題をまとめた設計書でも、コピーは番号ごとに分かれる。読む側の形も番号 1 つに限り、名前の番号をコピーの直し先の課題にする（決定 7）。手順と違う、番号をまとめたコピーが過去の文書に 1 本ある（`issues/issue-4061-4062-4064-requirements.md`）。これは直し先の課題が名前から 1 つに決まらないので、対象にしない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `requirements_issues` | `issue_docs` の `_name_numbers`・`design_issues`・`plan_issues` | `issue_docs.py` の全体を読み、`src/ndf` を `-requirements` で検索 | `_name_numbers` を使う。要求仕様を読む側の形は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `requirements_issues` | 番号 1 つのコピー | 番号の一部が一致するだけの課題を取り違えない | 番号が複数の `issue-7-8-requirements.md`、設計書、実装計画、`issues/` の外のパスは空集合 |

## cross-review の指示

満たす決定: 決定 6・7、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `fixer_requirements_copy` | `src/ndf/domains/crossreview/criteria_decide.py` | 新設 | `(repo: str \| None) -> str` | リポジトリがあれば要求仕様のコピーへの指摘の扱いの全文、無ければ短い制約を表から返す |
| `_prepare_round` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | 変更したファイルの名前から `run.distinction` と `run.requirements_copy` を決める |
| `_review_prompt` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | `run.distinction` が偽なら、文書の基準のセクションから小見出しを外す |
| `_fix_prompt` | `src/ndf/domains/crossreview/fixer.py` | 変更 | 変えない | 同じ条件で小見出しを外し、コピーがあれば扱いの文を足す |

- `_prepare_round`
  - 事後条件: `run.distinction` は、次の条件で真になる。変更したファイルのどれかで、`issue_docs.design_issues` か `issue_docs.requirements_issues` の結果が空でない。`run.requirements_copy` は後者に当たるファイルがあるとき真になる
- `_review_prompt`・`_fix_prompt`
  - `run.distinction` が偽の場合だけ、`document_block` の返す各要素に `writing.without_distinction` を掛ける。真の場合は呼ばない。目印の無い要素は変わらない。`document_block` は変えない
  - 指摘の文や単位の指示など、基準でない部分には掛けない（このリポジトリでは目印の文字列がそこへ現れうる）

扱いの全文を渡すのは、渡す指摘のどれかがコピーに向くとき（場所の無い指摘と「足すべき内容」を含む）だけである。ほかの指摘だけのときは、短い制約（`document.requirements_copy_keep`）を渡す。

扱いの文は `fixer_ci_only` と同じく修正担当への指示だけに入れ、基準の全文より前（`unit_block` の後）に置く。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_review_prompt`・`_fix_prompt` | 目印が対にならない | 編集の誤り | `without_distinction` | `CriteriaBroken` を呼び出し元へそのまま送出する | `crossreview/cli.py` の今の `CriteriaBroken` の扱い（`invalid` で止める） | 指示を作る前なので担当を起動しない。直して打ち直せる |

修正担当が扱いの文のとおりに動いたかは、プログラムでは検出しない。修正担当は、下の「内部のデータと定数」の扱いに従って指摘を直すか、`deferred`・`defer: needs_user` で返す。結果の形は今のままである。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `_Run.distinction` | 内部の値（真偽）。`FixRun` にも足す | `domains/crossreview/drive.py` | 偽 |
| `_Run.requirements_copy` | 内部の値（真偽）。`FixRun` にも足す | `domains/crossreview/drive.py` | 偽 |
| `document.requirements_copy` | 修正担当へ足す文 | `contracts/data/review-criteria.json` | — |

`document.requirements_copy` の文には、要求仕様のコピー（`issues/issue-<番号>-requirements.md`）への指摘の扱いを書く。`summary` にもこのキーの説明を足す。

修正担当は、コピーへの指摘があれば、コピーの名前の番号の課題の本文を `gh issue view <番号> --json body --jq .body` で読む。読むだけで、書かない。読んだ本文の `## 進行` より前（`## 進行` が無ければ全文）を、下の表では「要求の範囲」と呼ぶ。扱いは、指摘された場所で分ける。

まず現在の本文の見出しで分ける。要求の範囲にある見出しは、写した範囲と決まる。要求の範囲に無い見出しは、コピーにだけ足したセクションとは限らない。コピーした後に本文から消したセクションもあるためである。そのような見出しへの指摘がある課題に限り、本文の編集履歴（GraphQL の `userContentEdits`。版ごとの本文の全文が返る）を 1 回読む。どの版の要求の範囲にも無かった見出しだけを、コピーにだけあるセクションとする。

| 指摘された場所 | 扱い |
| --- | --- |
| 課題の本文から写した範囲（要求の範囲の `## ` の見出しと同じ見出しのセクションと、最初の `## ` より前） | 課題の本文、コピー、PR の本文を書き替えない。直す案を見送りの理由に書き、`deferred`・`defer: needs_user` にする |
| コピーにだけあるセクション（見出しが、現在の本文と編集履歴のどの版の要求の範囲にも無い。実装計画など） | そのセクションを直接直してコミットする。課題の本文と PR の本文は変えない |
| 見分けられない場合（本文か編集履歴を読み切れないか、要求の範囲の `## ` の見出しがコピーにそろっていない） | 写した範囲と同じ扱いにする |

直す案には、直し先の課題の番号、セクションの見出し、直す前の文、直した後の文を書く。利用者がこの理由だけを読んで、直すかを決められるようにするためである。コピーの写した範囲は、ほかの文書への指摘を直すときも書き替えない。

本文と編集履歴を読むコマンドには、cross-review が PR へ書くときと同じ順で決めた対象のリポジトリを指示の文に埋め込む。順は、`--repo`、状態ファイル、作業場所のリモートである。2 つの読み取りが別のリポジトリを指すと、写した範囲をコピーにだけあるセクションと取り違えるためである。リポジトリを決められないときは、短い制約だけを渡す。

承認の前の確認（`ndf document-rebuild gate`）も同じ見分け方を使う。判定は `domains/docrebuild/requirements_copy.py` が行い、AI には任せない。コピーにだけあるセクションがあるときだけ、コピーを直してよいファイルに入れ、起動指示にそのセクションを挙げる。worker がほかの範囲を変えた場合は、push せずに止める。

2 つの値はラウンドごとに決め直し、状態ファイルへは書かない（`documents` と同じ）。

### ログと記録の出力

足すものは無く、指示の中身だけが変わる。`drive.py` は 1,000 行の上限に近いので、足すのは値の 2 つと分岐の数行にとどめる。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `fixer_requirements_copy` | `criteria_decide.fixer_ci_only`・`document_block` | `criteria_decide.py` の該当の関数を読んだ | `fixer_ci_only` と同じ形で `_table()` から読む。`document_block` に足すにはシグネチャの変更が要るので使わない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fixer_requirements_copy` | 表の文を返す | — | — |
| `_prepare_round`・`_review_prompt` | 設計書がある変更で小見出しがある | 要求仕様のコピーだけでもある。どちらも無い Markdown の変更では、小見出しが無く基準の残りがある | — |
| `_fix_prompt` | コピーがあれば扱いの文と小見出しがある | 設計書だけなら扱いの文が無い。どちらも無ければ両方無い | 目印が対にならない基準で `invalid` |

`tests/domains/crossreview/test_crossreview_documents.py` の「指示に基準の全文がある」の確かめは、変更したファイルの名前に合わせて直す。
