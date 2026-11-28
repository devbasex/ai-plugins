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
| `src/ndf/domains/records/requirements_copy.py` | 新設 | 要求仕様のコピーのうち直してよいセクションを決める | `adapters.markdown`・`adapters.github`・`issue_docs` | `requirements_copy` | `editable_sections`・`editable_of`・`unchanged_outside`・`all_outside`（新設） |
| `src/ndf/domains/docrebuild/gate.py` | 変更 | 決まったセクションだけを直させ、ほかの範囲の変更を push せずに止める | `requirements_copy` | `gate` | `_allow_fix`・`_outside_sections`・`_only_protected_remain`（新設） |
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
| `fixer_requirements_copy` | `src/ndf/domains/crossreview/criteria_decide.py` | 新設 | `(scope: str \| None) -> str` | 直してよいセクションの行があれば要求仕様のコピーへの指摘の扱いの全文、無ければ短い制約を表から返す |
| `_prepare_round` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | 変更したファイルの名前から `run.distinction` と `run.requirements_copies` を決める |
| `_review_prompt` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | `run.distinction` が偽なら、文書の基準のセクションから小見出しを外す |
| `_fix_prompt` | `src/ndf/domains/crossreview/fixer.py` | 変更 | 変えない | 同じ条件で小見出しを外し、コピーがあれば扱いの文を足す |

- `_prepare_round`
  - 事後条件: `run.distinction` は、次の条件で真になる。変更したファイルのどれかで、`issue_docs.design_issues` か `issue_docs.requirements_issues` の結果が空でない。`run.requirements_copies` は後者に当たるファイルの並びになる
- `_review_prompt`・`_fix_prompt`
  - `run.distinction` が偽の場合だけ、`document_block` の返す各要素に `writing.without_distinction` を掛ける。真の場合は呼ばない。目印の無い要素は変わらない。`document_block` は変えない
  - 指摘の文や単位の指示など、基準でない部分には掛けない（このリポジトリでは目印の文字列がそこへ現れうる）

扱いの全文を渡すのは、渡す指摘のどれかがコピーに向くとき（場所の無い指摘と「足すべき内容」を含む）だけである。ほかの指摘だけのときは、短い制約（`document.requirements_copy_keep`）を渡し、GitHub を読まない。

扱いの文は `fixer_ci_only` と同じく修正担当への指示だけに入れ、基準の全文より前（`unit_block` の後）に置く。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_review_prompt`・`_fix_prompt` | 目印が対にならない | 編集の誤り | `without_distinction` | `CriteriaBroken` を呼び出し元へそのまま送出する | `crossreview/cli.py` の今の `CriteriaBroken` の扱い（`invalid` で止める） | 指示を作る前なので担当を起動しない。直して打ち直せる |

cross-review では、修正担当が扱いの文のとおりに動いたかをプログラムで検出しない。修正担当は、下の表に従って指摘を直すか、`deferred`・`defer: needs_user` で返す。結果の形は今のままである。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `_Run.distinction` | 内部の値（真偽）。`FixRun` にも足す | `domains/crossreview/drive.py` | 偽 |
| `_Run.requirements_copies` | 内部の値（変える要求仕様のコピーのパスの並び）。`FixRun` にも足す | `domains/crossreview/drive.py` | 空 |
| `RunConfig.repo_arg` | 引数 `--repo` の値 | `domains/crossreview/settings.py` | 無し |
| `document.requirements_copy` | 修正担当へ足す文 | `contracts/data/review-criteria.json` | — |

`_Run.distinction` と `_Run.requirements_copies` はラウンドごとに決め直し、状態ファイルへは書かない（`documents` と同じ）。

`document.requirements_copy` の文には、要求仕様のコピー（`issues/issue-<番号>-requirements.md`）への指摘の扱いを書く。`summary` にもこのキーの説明を足す。

#### 直してよい範囲の判定

直してよい範囲は `domains/records/requirements_copy.py` だけが決め、AI には判定させない。修正担当への指示と承認の前の確認の 2 つがこの判定を使う。

1. 課題の本文を読む。`## 進行` より前（`## 進行` が無ければ全文）を、下では「要求の範囲」と呼ぶ
2. コピーの `## ` の見出しのうち、要求の範囲に無いものを候補にする。候補が無いか、要求の範囲の見出しがコピーにそろっていなければ、直してよいものは無い
3. 候補があるときだけ、本文の編集履歴（GraphQL の `userContentEdits`）を 1 回読む。コピーした後に本文から消したセクションを見分けるためである
4. どの版の要求の範囲にも無かった候補だけを、直してよいセクションにする

GitHub の説明では、版の `diff` は編集の要約である。実際の応答は版の本文の全文だった（2026-11-28 に、このリポジトリの 7 件の課題で、現在の本文と同じ版があることを確かめた）。履歴が空（本文を一度も編集していない）なら、現在の本文だけで判定する。履歴が 1 件以上あり、その中に現在の本文と一致する版が無ければ、直してよいセクションは無い。本文か履歴を読み切れない場合も、直してよいセクションは無い。

| 指摘された場所 | 扱い |
| --- | --- |
| 直してよいセクションの中 | 直してコミットする。`## ` の見出しは足さない・消さない・動かさない・変えない。課題の本文と PR の本文は変えない |
| それ以外（本文から写した範囲と、見分けられない範囲） | 課題の本文、コピー、PR の本文を書き替えない。直す案を返し、利用者の判断を待つ |

直す案には、直し先の課題の番号、セクションの見出し、直す前の文、直した後の文を書く。利用者がこの理由だけを読んで、直すかを決められるようにするためである。

経路ごとの動きは次のとおりである。

| 経路 | 判定する時点 | 読むリポジトリ | 範囲の外を変えた場合 |
| --- | --- | --- | --- |
| cross-review の修正担当 | 指摘がコピーに向くときに、指示を組むプログラムが判定し、結果を指示の文に入れる | `--repo`、状態ファイル、作業場所のリモートの順で決める。決められなければ直してよいものは無い | 検出しない |
| `ndf document-rebuild gate`（承認の前の確認） | コピーを直してよいファイルに入れるときに、worker の起動の前に判定し、結果を起動指示に書く | PR のリポジトリ | push せずに止める |

`gate` は、見出しの並びが同じで、直してよいセクションの外の中身と、直してよいセクションの見出しの行が同じことを確かめる。コピーを 1 回の不合格で終えてよいのは、残った文の引用がすべて直してよい範囲の外にあるときだけである。直してよいセクションでも、`## ` の見出しの行は範囲の外に数える。

`gate` は、worker の起動の前に判定する。直す側の worker が判定を書き換えられないようにするためである。判定はファイルに書き残さず、`gate` が持つ同じ値を、起動指示と worker の後の確かめの両方に使う。

この方式では、指摘が無くて直さないコピーでも課題を読む。それでも採るのは、起動の前に増える時間が下の実測の範囲に収まり、次の 3 つの条件を満たすからである。

- worker の起動も、LLM の往復も増えない
- 起動指示と worker の後の確かめが同じ判定を使うので、途中で課題の本文が変わっても 2 つが食い違わない
- worker と `gate` の間に、新しい通信の仕組みが要らない

方式ごとの費用の合計は比べていない。コピーを実際に直す割合と、下の表の採らない案で増える時間と token を測っていないためである。そのため、この方式の費用が最も小さいかは分からない。どの案でも、範囲の外を変えていないかは `gate` が自分の判定で確かめるので、保護は変わらない。

| 案 | 直さない場合に増えるもの | 直す場合にかかるもの | 採否 |
| --- | --- | --- | --- |
| `gate` が worker の起動の前に読む | 本文の読み取り 1 回と、候補があれば編集履歴の読み取り 1 回 | 左と同じ読み取りだけ | 採る |
| worker が直す前にコマンドで範囲を問い、`gate` は worker の後に自分で読み直す | 無し | 本文と編集履歴の読み取りが 2 回ずつと、コマンドの起動 1 回、その結果を読む LLM の往復。読む間に本文が変わると、2 つの判定が食い違う | 採らない |
| 先に読み手役の確かめだけを行い、コピーを直す必要があるときに、`gate` が課題を読んで 2 つ目の worker を起動する | 無し | 読み取りに加えて、worker の起動がもう 1 回 | 採らない |
| worker の実行中の問い合わせに `gate` が答える | 無し | 読み取り 1 回ずつ。ただし、worker と `gate` の間の通信の仕組みを新しく作る必要がある | 採らない |

起動の前に読むことで増える時間は、コピー 1 件につき約 0.9 秒である。2026-11-28 にこのリポジトリの 7 件の課題で測った中央値は、本文の読み取りが 0.30 秒、編集履歴の読み取りが 0.58 秒だった。承認の前の確認の worker は、手元に記録が残る 28 回の中央値が 407 秒、最短が 152 秒である（起動指示と結果ファイルの更新時刻の差）。同じ記録の設計の確認 26 回のうち、要求仕様のコピーを読ませたのは 21 回で、19 回は 1 件、2 回は 3 件だった。コピーは順に読むので、3 件では約 2.7 秒になる。LLM の token は、起動指示に足す数行だけが増える。

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
