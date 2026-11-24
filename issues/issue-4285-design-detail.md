# #4285 の詳細設計: 文書の基準を AI への指示に入れ、読み手役が確認した記録を質問の前に調べる

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
  src/ndf/core/worker.py
  src/ndf/domains/records/{reading_decide,reading}.py *
  src/ndf/domains/records/cli.py
  src/ndf/domains/hooks/guards/reading_check.py *
  tests/ の対応するファイル
```

依存の向き: `ndf.contracts.writing` は最も下の層にあり、cross-review・`core/worker`・`records` から import する。guard は `records` の `reading_decide.missing` と `reading.rows` を import し、`records` は hook のモジュールを import しない。

下の表のパスは `plugins/ndf/src/ndf/` から書く。

| パス | 種類 | 役割 | 関数 |
| --- | --- | --- | --- |
| `contracts/writing.py` | 新設 | 基準の本文を読む | `text`・`types_section` |
| `domains/crossreview/criteria_decide.py` | 変更 | 指示の行を作る | `document_block`（新設） |
| `domains/crossreview/drive.py` | 変更 | 指示に行を加える | `_prepare_round`・`_review_prompt`・`_fix_prompt` |
| `core/worker.py` | 変更 | worker の指示に本文を入れる | `system_prompt` |
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
| `criteria_decide.document_block` | 新設 | `(role: str, documents: bool, since: str = "") -> list[str]` | 下の表の行を返す。`documents` が偽なら空。`since` は前のラウンドの head で、1 ラウンド目は空 |
| `_prepare_round` | 変更 | 変えない | Pull Request の全体の差分（`cfg.base...HEAD`）に Markdown のファイルがあるかを `_Run.documents` に入れる（ラウンドごとの差分では決めない） |
| `_review_prompt`・`_fix_prompt` | 変更 | 変えない | `document_block` の行を指示に加える |
| `worker.system_prompt` | 変更 | 変えない | `document-style` を読む作業（`kinds.json`）では、手順書の節の後に `writing.text()` を続ける |

| `role` | `document_block` が返すもの（上から順） | 出所 |
| --- | --- | --- |
| `reviewer` | 指摘の基準 5「文書の読み手に通じない文」と、重要度の決め方（誤らせる文は major、読みにくいだけの文は minor） | `review-criteria.json` |
| `reviewer` | 指摘に書かせる項目（引用・型・つまずく理由・書き直した案） | `review-criteria.json` |
| `reviewer` | 2 ラウンド目以降だけ: 基準 5 を major にしてよい範囲と、前のラウンドの head（SHA） | `review-criteria.json` |
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
| `reading.prompt` | 新設 | `(entry: Mapping[str, Any]) -> str` | 読み手役への指示を返す。`files`・`reader`・`questions_kind` と、4 つの型・2 つの区分の見分け方・返す形を入れる。文書は加工しない。「エンジニア向けの補足」を外した文書や、直した部分だけの文書は、担当が先に別のファイルへ作る |
| `reading.add` | 新設 | `(entry: Mapping[str, Any], root: Path) -> dict[str, Any]` | 担当が書いた記録を確かめ、足りない値を補って 1 行追記し、追記した行を返す。形が違えば `ReadingInvalid` を送出し、追記しない。GitHub を読まない |
| `reading.rows` | 新設 | `() -> list[dict[str, Any]]` | 記録を古い順に返す。壊れた行は無いものとして扱う。ファイルが無ければ空。ファイルを開けなければ `OSError` を送出する |
| `reading.check` | 新設 | `(questions, prs: Sequence[int], root: Path) -> list[str]` | 承認の手順用。後で動く hook が新しい head と比べられるように、先に `git fetch` を打つ（失敗しても続ける）。その後、`GitHub.pulls` で PR の head をまとめて読み、`missing` に渡す。GitHub を読めなければ `GitHubError` を送出する |
| `reading_decide.question_key` | 新設 | `(questions: Sequence[Mapping[str, Any]]) -> str` | 質問ごとの「質問の文・複数選択か・選択肢の名前と説明」だけを残した JSON（キーの順を固定し、文字列の前後の空白を除く）の SHA-256。区分などは含めない |
| `reading_decide.missing` | 新設 | `(key: str, repo: str, rows, heads: Mapping[int, str], prs: Sequence[int] = ()) -> list[str]` | 足りないものを表す語（下の表）を返す。空なら通す。`heads` は PR の番号から今の head（SHA）への対応、`prs` は承認の手順で渡す、承認する PR の番号 |

| 語 | 条件 |
| --- | --- |
| `no_record` | `repo` と `question_key` が一致する行が 1 つも無い |
| `no_pr` | 承認する PR の番号（`prs`）に、使う記録の行（`repo` と `question_key` が一致する最後の行）に書かれていないものがある。hook は `prs` を渡さないので、承認の手順でだけ出る |
| `head_moved` | 使う記録の行の PR のうち、`heads` にあって記録した head と違うものがある |

`heads` に無い PR は調べられなかったものとして扱い、`head_moved` に数えない。

コマンド（AI が手順の中で打つ。コマンドの一覧では「内部」に分類する）。どれも入力は JSON のファイル 1 つで受け、複数行の文を引数に載せない。キーは、`prompt` が `files`（読ませるファイルのパスの配列）・`reader`（立場・ふだん使う言葉・知識）・`questions_kind`（`requirements`・`design`・`none`。言い直しに含めさせる問いの種類）、`add` が下の表の「担当」のキー、`check` が `questions` と `prs`（番号の配列）である。

| コマンド | 終わり方 |
| --- | --- |
| `ndf records reading prompt --file <JSON>` | `ok`。指示の文を結果の `metrics` で返す |
| `ndf records reading add --file <JSON>` | 追記したら `ok`。形が違えば誤りの一覧を結果に入れて `invalid` |
| `ndf records reading check --file <JSON>` | 足りないものが無ければ `ok`、あれば `stopped`・`precondition`。GitHub を読めなければ `stopped`・`unknown` で、担当はそのことを承認資料に書いて進む |

### 内部のデータと定数

記録は `reading-checks.jsonl` に 1 行ずつ追記する。保存先は `gate-rejections.jsonl` と同じ、NDF の状態の保存先（リポジトリの外）である。`repo` と `question_key` が同じ行が複数あれば、最後の行を使う。文書の本文・質問の文・残った文の引用は入れない。

| キー | 型 | 誰が書くか | 値 |
| --- | --- | --- | --- |
| `questions` | 配列 | 担当 | `AskUserQuestion` に渡す `questions` と同じ形。行には書かず、`question_key` の計算に使う |
| `prs[].number`・`prs[].branch`・`prs[].head` | 整数・文字列・文字列 | 担当 | 承認する PR と、読み手役の確認を終えた時点の head。判断の質問と、PR の無い承認では `prs` を空にする |
| `reads[].name` | 文字列 | 担当 | 文書のパスか、固定の文字列 `question`（質問の文）・`material`（質問の直前に示す材料か承認資料）。`question` が 1 つも無い記録は拒む |
| `reads[].reader` | 文字列 | 担当 | 読み手の立場 |
| `reads[].round` | 1 か 2 | 担当 | その立場で何回目の読みか |
| `reads[].result` | `passed` か `remained` | 担当 | 合格か、2 回目にも残ったか |
| `at`・`repo`・`question_key` | 文字列 | `add` | 記録した時刻・リポジトリ（`owner/name`）・質問の SHA-256 |

## 質問の前に記録を調べる（hook）

設計書の決定 10・11 を実装し、受け入れ条件 17〜19 を満たす。`reading_check.check(ctx, svc) -> Verdict` は、上の行から順に調べ、最初に当てはまった行に従う。区分は、`AskUserQuestion` の `metadata.source` の値の先頭にある `ndf-gate:` か `ndf-ask:` である（その後に続く文字列は含めない）。

| 順 | 条件 | 結果 |
| ---: | --- | --- |
| 1 | 区分が無い（どちらでも始まらない） | 通す |
| 2 | 人のいない実行（環境変数 `NDF_FACTORY_RUN` が空でない） | 通す |
| 3 | 記録のファイルを開けない（`OSError`）・リポジトリの名前が決まらない | 通す。hook の計測の記録に `reading: unreadable` を残す |
| 4 | `missing` が空でない | 止める |
| 5 | 上のどれでもない（git が失敗したときと、PR のリモート追跡ブランチが手元に無いときも、ここに当たる） | 通す。head を読めない PR があれば、hook の計測の記録に `reading: no_head` を残す |

- `ndf-gate:` の質問で、一致する記録に PR があるときだけ、git を 1 回起動し、その PR のブランチの `refs/remotes/<リモート>/<ブランチ>` をまとめて読む（`git for-each-ref`）。リモートの名前は `git.remote_of`（`.ndf/worktree.json` の設定）で決める。読めたものだけを `heads` に入れる。時間の上限は `hook.git-rev-parse` の 0.4 秒とする
- `guards.json` の行: イベント `PreToolUse`・Tool `AskUserQuestion`・`order` 55（質問を転送する `ask-forward` の 60 より前）・`fail_closed` 偽・`deny_key` は `reason`・`limits` は `hook.git-rev-parse` と `hook.counter-lock`
- 止める理由の文は、区分と、足りないものの語（`no_record` など）だけで決まる固定の文とし、記録を作るコマンドを添える。質問の文やハッシュ値を入れない。同じ理由の拒否を数える今の仕組み（`hook.deny-repeat` の 2 回）が、区分と語の組ごとに数えるようにするためである。2 回止めた後は、同じ区分の別の質問でも、同じ理由であれば、止めずに注意を添えて通す

## 手順書の変更

どのファイルも `plugins/ndf/skills/` の下にある。

| ファイル | 変える中身 | 決定 |
| --- | --- | --- |
| `document-style/SKILL.md` | 冒頭の読み手の説明とルール 11 を、基準へのリンクに置き換える。言い換えの表は例として残す。チェックリストのうち、直訳を避けているかを確かめる項目を、「文書の読み手に通じる言葉で書いている」に書き換える | 3 |
| `document-drafting/SKILL.md` | 「語の選び方」の冒頭を基準へのリンクに置き換え、表は例として残す | 3 |
| `document-rebuild/references/common.md`・`keyword-check.md` | `common.md` の K5（書き手の文脈を持たずに読める）と K6（初めて見ても意味が通じる語で書く）、`keyword-check.md` の表の「社内・リポジトリ」の行を、基準へのリンクに置き換える | 2・3 |
| `pr-review/SKILL.md`・`development-workflow/references/glossary.md` | ルール 11 への参照を、基準へのリンクに置き換える | 3 |
| `requirements-design/SKILL.md`・同じ Skill の `references/document-roles.md`・`spec-template.md` | 読み手を説明する文と、`document-roles.md` の表の「読み手」の列を、基準へのリンクに置き換える。ほかの列と見出しの順は変えない | 2・3 |
| `document-rebuild/SKILL.md` | 「読み手役に読ませる」の、渡すもの・返させるもの・合否・回数・記録を直す | 7〜9 |
| `development-workflow/references/approval-request.md` | 承認の前の確認の手順と、`reading add`・`reading check` を打つことを加える | 8〜10・12 |
| `development-workflow/references/asking.md` | 「hook は質問を止めない」を、記録が足りないと止まることに直す | 10・11 |
| `decision-request/SKILL.md` | 質問の文を読み手役に読ませてから出すことを加える | 8 |
| `development-workflow/references/agent-layers.md` | conductor が承認の前に読み手役を起動してよいことを加える | 8 |
| `cross-review/docs/01-state-and-review.md`・`fix/SKILL.md` | 指摘の基準の表に基準 5 を加える | 4・5 |

上の手順書のほかに、用語集の元データ（`docs/glossary/glossary.json`）の「読み手役」の項目と、`docs/ndf-plugin-reference.md` の hook の一覧を手で直す。`docs/glossary.md` と `plugins/ndf/docs/contracts/commands.md` は、生成するコマンドで作り直す。共通原則の今ある文・プロジェクトの MVV・用語の付け方の仕様（`ndf-ubiquitous-language.md`）は変えない。

## 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `document_block` | `role`・`documents`・`since` の組み合わせごとに、返す行が変わる | 基準を読めないと例外 |
| `worker.system_prompt` | `document-style` を読む作業だけに本文が入る | 基準を読めないと worker を起動しない |
| `question_key` | 選択肢の説明・複数選択かどうか・文の境界が変わると値が変わる。区分だけが変わっても変わらない | — |
| `missing` | 一致すれば空。`no_record`・`no_pr`・`head_moved` の条件ごとにその語を返す。別のリポジトリの記録は数えない | `heads` に無い PR では `head_moved` を返さない |
| `reading.add` | 値を補って 1 行追記する | `question` の無い記録で `ReadingInvalid`。複数行と引用符を含む値をそのまま受ける |
| `reading_check.check` | 「質問の前に記録を調べる（hook）」の表の順 1〜5 を 1 つずつ確かめる。壊れた行だけの記録は `no_record` で止まる | 開けない記録・git の失敗で通す |
