# #4285 の詳細設計: 文書の基準を AI への指示に入れ、読み手役の確かめの記録を質問の前に調べる

設計書（[issue-4285-design.md](./issue-4285-design.md)）の構成要素を、ファイルと関数ごとに分けた文書である。受け入れ条件の番号は要求仕様（[issue-4285-requirements.md](./issue-4285-requirements.md)）のものを指す。

## 地図

`*` は新設するファイルである。

```text
plugins/ndf/
  contracts/data/writing-criteria.md *           文書の基準の本文
  contracts/data/review-criteria.json            指摘の基準 5 と、指示の文
  contracts/data/ndf-common-principles.md        「文書は、想定した読み手に通じる言葉で書く」の 1 文を加える
  contracts/schemas/reading-check.schema.json *  記録の形
  contracts/hooks/guards.json                    guard「reading-check」を加える
  contracts/hooks/runtimes.json                  Codex で動かさない guard に reading-check を加える
  contracts/domains/ndf-files.json               記録のファイルを登録する
  src/ndf/contracts/writing.py *
  src/ndf/domains/crossreview/{criteria_decide,drive}.py
  src/ndf/progress/gate/entry.py
  src/ndf/domains/records/{reading_decide,reading}.py *
  src/ndf/domains/records/cli.py
  src/ndf/domains/hooks/guards/reading_check.py *
  tests/ の対応するファイル
```

依存の向き: `ndf.contracts.writing` は最も下の層にあり、cross-review・`progress/gate`・`records` から import する。guard は `records` の `reading_decide.missing` と `reading.rows` を import し、`records` は hook のモジュールを import しない。

下の表のパスは `plugins/ndf/src/ndf/` から書く。

| パス | 種類 | 役割 | 関数 |
| --- | --- | --- | --- |
| `contracts/writing.py` | 新設 | 基準の本文を読む | `text`・`types_section` |
| `domains/crossreview/criteria_decide.py` | 変更 | 指示の行を作る | `document_block`（新設） |
| `domains/crossreview/drive.py` | 変更 | 指示に行を加える | `_prepare_round`・`_review_prompt`・`_fix_prompt` |
| `progress/gate/entry.py` | 変更 | worker の指示に本文を入れる | `_skills_of` |
| `domains/records/reading_decide.py` | 新設 | 質問を通すか止めるかを決める純粋関数 | `question_key`・`missing` |
| `domains/records/reading.py` | 新設 | 読み手役への指示を作り、記録を書き、読む | `prompt`・`add`・`rows`・`check` |
| `domains/records/cli.py` | 変更 | `ndf records reading` の引数 | `_reading`（新設）・`build` |
| `domains/hooks/guards/reading_check.py` | 新設 | 質問の前に記録を調べる | `check` |

## 基準の本文を指示に入れる

設計書の決定 2・4・5・6 を実装し、受け入れ条件 7〜11 を満たす。

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `writing.text` | 新設 | `() -> str` | `writing-criteria.md` の全文を返す。読めなければ `ContractError` |
| `writing.types_section` | 新設 | `() -> str` | 4 つの型の節だけを返す。見出しが無ければ `ContractError` |
| `criteria_decide.document_block` | 新設 | `(role: str, documents: bool, round_no: int = 1) -> list[str]` | 下の表の行を返す。`documents` が偽なら空 |
| `_prepare_round` | 変更 | 変えない | 変わった Markdown があるかを `_Run.documents` に入れる |
| `_review_prompt`・`_fix_prompt` | 変更 | 変えない | `document_block` の行を指示に加える |
| `entry._skills_of` | 変更 | 変えない | `document-style` を読む作業では、Skill の一覧の後に `writing.text()` を続ける |

| `role` | `document_block` が返すもの（上から順） | 出所 |
| --- | --- | --- |
| `reviewer` | 指摘の基準 5「文書の読み手に通じない文」と、重要度の決め方（誤らせる文は major、読みにくいだけの文は minor） | `review-criteria.json` |
| `reviewer` | 指摘に書かせる項目（引用・型・つまずく理由・書き直した案） | `review-criteria.json` |
| `reviewer` | 2 ラウンド目以降だけ: 基準 5 を major にしてよい範囲 | `review-criteria.json` |
| `reviewer`・`fixer` | 基準の全文 | `writing-criteria.md` |
| `fixer` | 文や段落を書き直し、前後の段落と一緒に読み直す、という指示 | `review-criteria.json` |

- `writing-criteria.md` を読めないときは、指示を作る前に止める。cross-review は `invalid` で終わり、worker は起動しない
- `drive.py` に加える行は、`_Run.documents` の定義と、それを設定する 1 行の、合わせて 2 行である。2 つの指示に行を加える変更は、今ある行に `+ document_block(...)` を続けるので、行数は増えない。1,000 行を超えるときは、先に `_regular_blobs`・`_local_text`・`_committed_texts`・`_changed`・`_sections`・`changed_sections` を、新しいファイル `changed_docs.py` へ移す
- 基準 5 の指摘も、`impact` の無い major は minor に下げる今の規則に従う。`review-criteria.json` の `impact.reviewer` に「基準 5 では、どの読み手が何を取り違えるか」を加える
- `not_written` の「字句や言い回し」を「読み手に通じる文の、好みの言い換え」に変える

## 読み手役の指示と確かめの記録

設計書の決定 7〜10 を実装し、受け入れ条件 12・13・17・18 を満たす。

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `reading.prompt` | 新設 | `(document: Path, reader: str) -> str` | 読み手役への指示を返す。読み手の説明（立場・ふだん使う言葉・知識を書いた文）・4 つの型・返す形・文書のパスを入れる |
| `reading.add` | 新設 | `(entry: Mapping[str, Any], root: Path) -> dict[str, Any]` | 担当が書いた記録を確かめ、足りない値を補って 1 行追記し、追記した行を返す。形が違えば `ReadingInvalid` を送出し、追記しない |
| `reading.rows` | 新設 | `() -> list[dict[str, Any]]` | 記録を古い順に返す。壊れた行は無いものとして扱う。ファイルが無ければ空。ファイルを開けなければ `OSError` を送出する |
| `reading.check` | 新設 | `(questions, prs: Sequence[int], root: Path) -> list[str]` | 承認の手順用。`prs` があれば GitHub から各 PR の head を読み、`missing` に承認として渡す。`prs` が無ければ、判断の質問として渡す。GitHub を読めなければ `GitHubError` を送出する |
| `reading_decide.question_key` | 新設 | `(questions: Sequence[Mapping[str, Any]]) -> str` | 質問の文・選択肢の名前・選択肢の説明を、それぞれ前後の空白を除いて出現順に改行でつなぎ、その SHA-256 を返す。区分などは含めない |
| `reading_decide.missing` | 新設 | `(key: str, gate: bool, rows, heads: Mapping[str, str]) -> list[str]` | 足りないものを表す語（下の表）を返す。空なら通す。`gate` は区分が `ndf-gate:` のとき真。`heads` はブランチ名から今の head（SHA）への対応 |

| 語 | 条件 | 承認（`gate`） | 判断の質問 |
| --- | --- | --- | --- |
| `no_record` | `question_key` が一致する行が 1 つも無い（記録が 1 件も無いときも、別の質問の記録だけがあるときも同じ） | 止める | 止める |
| `no_pr` | 一致する最後の行の `prs` が空 | 止める | 当たらない |
| `head_moved` | 一致する最後の行の PR のうち、`heads` にあって値が違うものがある | 止める | 当たらない |

`heads` に無いブランチは調べられなかったものとして扱い、`head_moved` に数えない。

コマンド（AI が手順の中で打つ。コマンドの一覧では「内部」に分類する）:

| コマンド | 終わり方 |
| --- | --- |
| `ndf records reading prompt --document <パス> --reader <読み手の説明>` | `ok`。指示の文を結果の `metrics` で返す |
| `ndf records reading add --file <記録の JSON>` | 追記したら `ok`。形が違えば誤りの一覧を結果に入れて `invalid` |
| `ndf records reading check --questions <質問の JSON> [--pr <番号>...]` | 足りないものが無ければ `ok`、あれば `stopped`・`precondition`。GitHub を読めなければ `stopped`・`unknown` で、担当はそのことを承認資料に書いて進む |

### 内部のデータと定数

記録は `reading-checks.jsonl` に 1 行ずつ追記する。保存先は `gate-rejections.jsonl` と同じ、NDF の状態の保存先（リポジトリの外）である。`question_key` が同じ行が複数あれば、最後の行を使う。

| キー | 型 | 誰が書くか | 値 |
| --- | --- | --- | --- |
| `questions` | 配列 | 担当 | `AskUserQuestion` に渡す `questions` と同じ形。行には書かず、`question_key` の計算に使う |
| `prs` | 番号の配列 → オブジェクトの配列 | 担当は番号だけ。`add` がブランチと head（GitHub の値）を補う | 承認する PR。判断の質問では空でよい |
| `reads[].name` | 文字列 | 担当 | 文書のパスか、固定の文字列 `question`（質問の文）・`material`（承認資料）・`pr`（PR の題と本文）のどれか。`question` が 1 つも無い記録は拒む |
| `reads[].reader` | 文字列 | 担当 | 読み手の立場 |
| `reads[].round` | 1 か 2 | 担当 | その立場で何回目の読みか |
| `reads[].result` | `passed` か `remained` | 担当 | 合格か、2 回目にも残ったか |
| `remained` | 配列 | 担当 | 残った文の引用・直したか・理由。`result` に `remained` があれば 1 件以上必要 |
| `at`・`question_key` | 文字列 | `add` | 記録した時刻と、質問の SHA-256 |

## 質問の前に記録を調べる（hook）

設計書の決定 10・11 を実装し、受け入れ条件 17〜19 を満たす。`reading_check.check(ctx, svc) -> Verdict` は、上の行から順に調べ、最初に当てはまった行に従う。区分は `AskUserQuestion` の `metadata.source` の値である。

| 順 | 条件 | 結果 |
| ---: | --- | --- |
| 1 | 区分が `ndf-gate:` でも `ndf-ask:` でも始まらない | 通す |
| 2 | 人のいない実行（環境変数 `NDF_FACTORY_RUN` が空でない） | 通す |
| 3 | 記録のファイルを開けない（`OSError`） | 通す。hook の計測の記録に `reading: unreadable` を残す |
| 4 | `missing` が空でない | 止める |
| 5 | `ndf-gate:` で、git が失敗したか時間切れになった、または手元に `origin/<ブランチ>` が無い PR がある | 通す。hook の計測の記録に `reading: no_head` を残す |
| 6 | 上のどれでもない | 通す |

- 承認の質問では、`missing` の前に git を 1 回だけ起動し、記録の PR のブランチの `refs/remotes/origin/<ブランチ>` をまとめて読む（`git for-each-ref`）。読めたものだけを `heads` に入れる。時間の上限は `hook.git-rev-parse` の 0.4 秒とする
- `guards.json` の行: イベント `PreToolUse`・Tool `AskUserQuestion`・`order` 55（質問を転送する `ask-forward` の 60 より前）・`fail_closed` 偽・`deny_key` は `reason`・`limits` は `hook.git-rev-parse` と `hook.counter-lock`
- 止める理由の文は、区分と、足りないものの語（`no_record` など）だけで決まる固定の文とし、記録を作るコマンドを添える。質問の文やハッシュ値を入れない。同じ理由の拒否を数える今の仕組み（`hook.deny-repeat` の 2 回）が、区分と語の組ごとに数えるようにするためである。2 回止めた後は、同じ区分の別の質問も、同じ理由なら注意に変わって通る

## 手順書の変更

どのファイルも `plugins/ndf/skills/` の下にある。

| ファイル | 変える中身 | 決定 |
| --- | --- | --- |
| `document-style/SKILL.md` | 冒頭の読み手の説明とルール 11 を、基準へのリンクに置き換える。言い換えの表は例として残す。チェックリストの直訳の行を「文書の読み手に通じる言葉で書いている」に直す | 3 |
| `document-drafting/SKILL.md` | 「語の選び方」の冒頭を基準へのリンクに置き換え、表は例として残す | 3 |
| `document-rebuild/references/common.md`・`keyword-check.md` | K5・K6 と、社内の読み手の行を、基準へのリンクに置き換える | 2・3 |
| `pr-review/SKILL.md`・`development-workflow/references/glossary.md` | ルール 11 への参照を、基準へのリンクに置き換える | 3 |
| `requirements-design/SKILL.md`・同じ Skill の `references/document-roles.md`・`spec-template.md` | 読み手を説明する文と、`document-roles.md` の表の「読み手」の列を、基準へのリンクに置き換える。ほかの列と見出しの順は変えない | 2・3 |
| `document-rebuild/SKILL.md` | 「読み手役に読ませる」の、渡すもの・返させるもの・合否・回数・記録を直す | 7〜9 |
| `development-workflow/references/approval-request.md` | 承認の前の確かめの手順と、`reading add`・`reading check` を打つことを加える | 8〜10・12 |
| `development-workflow/references/asking.md` | 「hook は質問を止めない」を、記録が足りないと止まることに直す | 10・11 |
| `decision-request/SKILL.md` | 質問の文を読み手役に読ませてから出すことを加える | 8 |
| `development-workflow/references/agent-layers.md` | conductor が承認の前に読み手役を起動してよいことを加える | 8 |
| `cross-review/docs/01-state-and-review.md`・`fix/SKILL.md` | 指摘の基準の表に基準 5 を加える | 4・5 |

文書のほかに、用語集の正（`docs/glossary/glossary.json`）の「読み手役」と、`docs/ndf-plugin-reference.md` の hook の一覧を手で直す。`docs/glossary.md` と `plugins/ndf/docs/contracts/commands.md` は、生成するコマンドで作り直す。共通原則の今ある文・プロジェクトの MVV・用語の付け方の仕様（`ndf-ubiquitous-language.md`）は変えない。

## 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `document_block` | 相手・文書の有無・ラウンドの組み合わせで行が変わる | 基準を読めないと例外 |
| `entry._skills_of` | `document-style` を読む作業だけに本文が入る | 基準を読めないと worker を起動しない |
| `question_key` | 選択肢の説明が変わると値が変わる。区分だけが変わっても変わらない | — |
| `missing` | 一致すれば空。`no_record`・`no_pr`・`head_moved` の条件ごとにその語を返す | `heads` に無いブランチでは `head_moved` を返さない |
| `reading.add` | 値を補って 1 行追記する | `question` の無い記録・`remained` の理由の無い記録で `ReadingInvalid` |
| `reading_check.check` | 上の表の順 1〜6 を 1 つずつ確かめる。壊れた行だけの記録は `no_record` で止まる | 開けない記録・git の失敗で通す |
