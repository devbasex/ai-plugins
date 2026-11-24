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
  contracts/hooks/guards.json                    guard「reading-check」「reading-clean」を加える
  contracts/hooks/runtimes.json                  Codex で動かさない guard に 2 つを加える
  contracts/domains/ndf-files.json               記録と、作業のディレクトリを登録する
  contracts/domains/locations.json               OS の一時ディレクトリの root（temp）と、その下の保存先を加える
  contracts/limits/hook.reading-check.json *     照合の結果が有効な時間（600 秒）
  contracts/limits/records.reading-keep.json *   記録を残す時間（30 日）
  contracts/limits/records.reading-work.json *   終わらなかった作業のディレクトリを残す時間（1 日）
  src/ndf/contracts/writing.py *
  src/ndf/adapters/{github,git}.py
  src/ndf/core/worker.py
  src/ndf/domains/crossreview/{criteria_decide,drive}.py
  src/ndf/domains/crossreview/changed_docs.py *
  src/ndf/domains/records/{reading_decide,reading}.py *
  src/ndf/domains/records/cli.py
  src/ndf/domains/hooks/guards/{reading_check,reading_clean}.py *
  src/ndf/domains/hooks/strict.py
  tests/ の対応するファイル
```

依存の向き: `ndf.contracts.writing` は最も下の層にあり、cross-review・`core/worker`・`records` から import する。guard は `records` の `reading_decide` と、`reading` の `rows`・`clean` を import し、`records` は hook のモジュールを import しない。

下の表のパスは `plugins/ndf/src/ndf/` から書く。

| パス | 種類 | 役割 | 関数 |
| --- | --- | --- | --- |
| `contracts/writing.py` | 新設 | 基準の本文を読む | `text`・`types_section` |
| `domains/crossreview/criteria_decide.py` | 変更 | 指示の行を作る | `document_block`（新設） |
| `domains/crossreview/changed_docs.py` | 新設 | 変わった Markdown を調べる（`drive.py` から移す）・前のラウンドからの差分を書く | `changed_sections` など・`doc_round`（新設） |
| `domains/crossreview/drive.py` | 変更 | 指示に行を加える | `_prepare_round`・`_review_prompt`・`_fix_prompt` |
| `core/worker.py` | 変更 | worker の指示に本文を入れる | `system_prompt`・`criteria_section`（新設） |
| `adapters/github.py` | 変更 | PR の題も読む | `Pull`・`PULL_FIELDS` |
| `adapters/git.py`・`domains/hooks/strict.py` | 変更 | git を起動せずにリポジトリを探す関数を `strict.py` から `git.py` へ移す | `repo_root`・`common_git_dir` |
| `domains/records/reading_decide.py` | 新設 | 何を読ませるか・質問を通すか止めるかを決める純粋関数 | `question_key`・`to_read`・`missing`・`passable` |
| `domains/records/reading.py` | 新設 | 読み手役への指示を作り、記録を書き、読む | `open_work`・`prompt`・`add`・`rows`・`check`・`repo_key`・`clean` |
| `domains/records/cli.py` | 変更 | `ndf records reading` の引数 | `_reading`（新設）・`build` |
| `domains/hooks/guards/reading_check.py` | 新設 | 質問の前に記録を調べる | `check` |
| `domains/hooks/guards/reading_clean.py` | 新設 | 作業のディレクトリを消す | `check` |

## 基準の本文を指示に入れる

設計書の決定 2・4・5・6 を実装し、受け入れ条件 7〜11 を満たす。

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `writing.text` | 新設 | `() -> str` | `writing-criteria.md` の全文を返す。読めなければ `ContractError` |
| `writing.types_section` | 新設 | `() -> str` | 4 つの型の節だけを返す。見出しが無ければ `ContractError` |
| `criteria_decide.document_block` | 新設 | `(role: str, doc: DocRound) -> list[str]` | 下の表の行を返す。`doc.documents` が偽なら空 |
| `changed_docs.doc_round` | 新設 | `(g, data, cfg, round_no: int, scope, docs, diff: Path, out: Path) -> DocRound` | `DocRound(documents: bool, since: Optional[Path])` を返す。`documents` は変わった Markdown があるか。`since` は前のラウンドからの差分のファイル（下の表） |
| `_prepare_round` | 変更 | 変えない | `doc_round` の値を `_Run.doc` に入れる |
| `_review_prompt`・`_fix_prompt` | 変更 | 変えない | `document_block` の行を指示に加える |
| `worker.criteria_section` | 新設 | `(kind: str) -> str` | `kinds.json` のその作業の `skills` に `document-style` があれば `writing.text()`、無ければ（表に無い作業も）空 |
| `worker.system_prompt` | 変更 | 変えない | 手順書の後に `criteria_section(kind)` を続ける |

| `role` | `document_block` が返すもの（上から順） | 出所 |
| --- | --- | --- |
| `reviewer` | 指摘の基準 5「文書の読み手に通じない文」と、重要度の決め方（誤らせる文は major、読みにくいだけの文は minor） | `review-criteria.json` |
| `reviewer` | 指摘に書かせる項目（引用・型・つまずく理由・書き直した案） | `review-criteria.json` |
| `reviewer` | `doc.since` があるときだけ: 基準 5 を major にしてよい範囲と、前のラウンドからの差分のファイルのパス | `review-criteria.json` |
| `reviewer`・`fixer` | 基準の全文 | `writing-criteria.md` |
| `fixer` | 文や段落を書き直し、前後の段落と一緒に読み直す、という指示 | `review-criteria.json` |

- `writing-criteria.md` を読めないときは、指示を作る前に止める。cross-review は `invalid` で終わり、worker は起動しない
- **worker を起動する経路は 2 つある。** 計画から打つ `ndf answer work` と、直接打つ `ndf worker run` である。前者も `--kind` に `kinds.json` の作業の名前を渡して `ndf worker run` に置き換わるので、どちらも `system_prompt` を通る。本文はここだけで入れ、`progress/gate/entry.py` の起動指示には入れない（2 回入るのを防ぐ）。本文が入る作業は、今の `kinds.json` では design・design-detail・document-sources・implementation-plan・plan-to-spec・requirements・retrospective である
- `drive.py` は 998 行なので、先に `_regular_blobs`・`_local_text`・`_committed_texts`・`_changed`・`_sections`・`changed_sections` を `changed_docs.py` へ移し、呼び出し側（`crossreview/cli.py` とテスト）を直す。そのうえで `drive.py` に `_Run.doc` の定義・設定・2 つの指示への `document_block` の追加を書く

前のラウンドからの差分（`since`）は次のとおり決める。全差分を読み直すラウンドでも、書き換わった文がどれかを相手役が見分けられるようにするためである。

| ラウンド | `since` |
| --- | --- |
| 1 ラウンド目 | 無し |
| 前の head からの差分を読むラウンド（`SINCE`） | 相手役に渡す差分のファイルそのもの。git は起動しない |
| 2 ラウンド目以降で全差分を読むラウンド（`FULL`） | 前の head（`read_by_all_head(data, Basis(None, 相手役, 重点))`。比較基点の変化だけでは捨てない）から HEAD までの、変わった Markdown だけの差分（`git diff <前の head>...HEAD -- <文書>`）を `round<n>-since.diff` に書く。git の起動は、祖先の確認と差分の 2 回 |
| 上のうち、前の head が無い・相手役か重点が変わった・HEAD の祖先でない | 無し。major の範囲を限る文も入れない（前に読んでいない相手役が、全体を初めて読むため） |

- 基準 5 の指摘も、`impact` の無い major は minor に下げる今の規則に従う。`review-criteria.json` の `impact.reviewer` に「基準 5 では、どの読み手が何を取り違えるか」を加える
- `not_written` の「字句や言い回し」を「読み手に通じる文の、好みの言い換え」に変える

## 読み手役の指示と確かめの記録

設計書の決定 7〜10 を実装し、受け入れ条件 12・13・17・18 を満たす。

担当は、セッションで初めて使うときに `open`（作業のディレクトリを作る）を打ち、以後は `prompt`（読ませる内容を固定し、指示を作る）→ 読み手役の起動 → `add`（結果の記録）→ `check`（質問の直前の照合）の順に打つ。何を読ませたかは `prompt` が記録するので、読んでいる間に変わった文書・質問・PR を「読んだ」とは記録できない。

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `reading.open_work` | 新設 | `(session: str) -> Path` | セッションの作業のディレクトリを作り、パスを返す。あればそのまま返す |
| `reading.prompt` | 新設 | `(request: Mapping[str, Any], root: Path) -> list[dict[str, Any]]` | 対象（文書・質問と材料・PR。1 回で複数を受ける）ごとに、原本を 1 度だけ読み、同じバイト列から版・差分・コピーを作り、`to_read` で読ませる範囲を決め、`given` の行を書き、指示の文・`id`・範囲・コピーのパスを返す。指示には、読み手の説明（立場・ふだん使う言葉・知識を書いた文）・4 つの型・返す形・コピーのパスを入れる |
| `reading.add` | 新設 | `(entry: Mapping[str, Any], root: Path) -> dict[str, Any]` | 担当が書いた結果を確かめ、`given` の行から版を引き継いで 1 行追記する。対象のコピーを消し、追記した行と、残った文を承認資料や PR の本文に書くための行（下の「残った文」）を返す。形が違う・対応する `given` が無い・読んだ後に変わっているときは `ReadingInvalid` を送出し、追記しない。GitHub は読まない |
| `reading.check` | 新設 | `(tool_input, prs, material, root) -> list[str]` | 質問の直前の照合。今の値を読んで `missing` に渡し、結果によらず `checked` の行を追記する。空なら `ok`、語があれば `failed`。GitHub を読めず、PR の今の値によらない語（`no_record`・`pr_unmatched`・`material_moved`）に当たらなければ `unknown` とし、`GitHubError` を送出する |
| `reading.rows` | 新設 | `() -> list[dict[str, Any]]` | 記録を古い順に返す。壊れた行は無いものとして扱う。ファイルが無ければ空。ファイルを開けなければ `OSError` を送出する |
| `reading.repo_key` | 新設 | `(cwd: Path) -> str` | `cwd` から親へ `.git` を探し、共通の git ディレクトリの実パスを返す（git を起動しない。worktree からもメインディレクトリと同じ値になる）。git の外では `cwd` の実パス。hook の `strict.py` にある `_repo_root`・`_git_dir` を `adapters/git.py` へ移して両方から使う |
| `reading_decide.question_key` | 新設 | `(questions: Sequence[Mapping[str, Any]]) -> str` | `questions` の並びを正規化した JSON の SHA-256 を返す。質問と選択肢の入れ子・順序・文字列（空白も）・`multiSelect` を含むすべてのキーをそのまま残し、値が `null` のキーを除き、`multiSelect` が無ければ `false` を補う。`json.dumps` の `sort_keys=True`・区切り `,` と `:`・`ensure_ascii=False` で直列化する |
| `reading_decide.to_read` | 新設 | `(rows, repo: str, path: str, role: str, raw: str, kept: frozenset[str]) -> Reading` | 文書と立場の組について、読ませる範囲 `none`・`diff`・`full` と、`diff` のときの前の版を返す（下の表）。`kept` は、残った文の行のうち今も見つかったもののハッシュ |
| `reading_decide.missing` | 新設 | `(rows, scope: Scope, now: Current) -> list[str]` | `check` が使う。足りないものを表す語（下の表）を返す。空なら足りている |
| `reading.clean` | 新設 | `(session: str, ended: bool, now: float) -> None` | `ended` ならそのセッションの作業のディレクトリを消す。ほかのセッションのものは、最終更新から `records.reading-work` を過ぎたものだけを消す |
| `reading_decide.passable` | 新設 | `(rows, scope: Scope, at: datetime, keep: float) -> Optional[str]` | hook が使う。質問を通すなら `None`、止めるなら語（下の表）を返す |

`Scope` は記録を使ってよい範囲で、リポジトリ（`repo_key`）・作業（質問を出すセッションの id）・区分（`metadata.source` の値）・`question_key` の 4 つの組である。4 つがすべて同じ行だけを使う。作業の値の取り方は、下の「セッションの id」で決める。そのため、同じ文の質問でも、別のレーン・リポジトリ・区分の記録は使われず、後から書かれた別の作業の行で上書きもされない。セッションが変わったとき（中継の引き継ぎなど）は、質問だけを読ませ直す。文書の記録は作業によらず、リポジトリ・パス・立場・版で引き継ぐ。

### セッションの id

hook は入力の `session_id`、CLI は環境変数 `CLAUDE_CODE_SESSION_ID` から取る。2026-11-24 に Claude Code 2.1.331 で、hook の入力と、Bash の環境変数を比べた結果は次のとおりである。

| 打つ場所 | 2 つの値 |
| --- | --- |
| 会話の本体の Bash | 同じ |
| サブエージェントの Bash | 同じ（親の会話の id。hook の入力には別に `agent_id` が付く） |
| `--resume` で再開した会話の Bash | 同じ（id は変わらない） |
| 別に起動した `claude -p`（`ndf worker run` など） | その会話の id で、質問を出す会話とは違う |

- 質問と PR の `prompt`・質問を記録する `add`・`check` は、**質問を出す会話か、そのサブエージェントが打つ**。別に起動した `claude -p` の worker には打たせない。文書の記録は `Scope` を使わないので、どの会話から打ってもよい
- Claude Code の下（`CLAUDECODE` が空でない）で環境変数が空なら、`open` と質問・PR を扱うコマンドは `stopped`・`precondition` で止まり、行を書かない（空の値で書くと、hook が必ず `no_record` で止めるため）
- Claude Code 以外では、`AskUserQuestion` の hook が動かないので、担当が `--work <作業名>` で値を渡す。省くと `invalid` で止まる。Claude Code の下での `--work` は `invalid` とする

### 文書の読みを引き継ぐ

文書の原本は、`prompt` がバイト列として 1 度だけ読む。次の値とコピーは、すべてそのバイト列から作る。git が改行やフィルターで変換する内容と原本は違うことがあるので、2 つの値を分けて持つ。

| 値 | 作り方 | 使い道 |
| --- | --- | --- |
| `raw` | バイト列の SHA-256 | 文書の版。読み直しを省くかの判定 |
| `blob` | バイト列を `git hash-object --path <パス> --stdin` に標準入力で渡した id（改行の正規化と clean フィルターを当てた後）。git の外の文書では空 | PR の head の文書（`git rev-parse <head>:<パス>`）との比較と、前の版の取り出し |
| コピー | バイト列を UTF-8 として読み、伏せ字（下の「秘密と個人情報」）を当てたもの | 読み手役に渡す |
| 差分 | 前の版を `git cat-file --filters --path=<パス> <前の blob>` で取り出し、今のバイト列と比べる（ファイルを読み直さない）。取り出した内容の SHA-256 が前の `raw` と違えば、読ませた原文を復元できないので `full` にする | 読ませる範囲 |

`to_read` は、同じリポジトリ・パス・立場の、結果が `passed` か `remained` の最後の行で決める。

| 最後の結果の行 | 読ませる範囲 |
| --- | --- |
| 無い | `full`（全文） |
| `passed` で `raw` が今と同じ | `none`。読み手役を起動しない |
| `remained` で `raw` が今と同じで、残った文の行がすべて見つかる | `none`。見つけた行（引用・対応・理由）を結果に返し、担当が承認資料に書く |
| `passed` で `raw` が違い、前の原文を復元できる | `diff`（前の版から変わった部分と、前後の段落）。指示に変更箇所を並べる |
| 上のどれでもない（`remained` の行が見つからない場合を含む） | `full`。回数は 1 回目から数え直す |

組み直しの担当も同じ `prompt` と `add` を使い、質問の無い結果を記録する。承認を求める担当は、変わっていない文書では読み手役を起動せずに済む。

### 残った文

`remained` の文を、引き継いだ後の承認でも示せるようにする。

- `add` は、残った文ごとに `- <引用> — <直した か 直さない>: <理由>` の 1 行（改行は空白に置き換え、伏せ字を当てる）を作って返し、記録には各行の SHA-256 だけを残す
- 担当は、返された行をそのまま承認資料に書く。文書を含む PR があれば、その本文にも書く（質問の材料は会話とともに消えるが、PR の本文は引き継いだ後も読めるため）。PR の本文を変えるので、PR の `prompt` より先に書く
- 引き継ぎでは、`prompt` の対象に `remained_in`（同じ要求で読む PR の `pr:<番号>` か、承認資料のファイル）を書く。`prompt` はその中の行のハッシュを記録と比べ、すべてあれば `none`、1 つでも無ければ `full` にする。PR の本文は同じ要求で読んだ値を使い、GitHub を別に読まない

### コマンド

AI が手順の中で打つ。コマンドの一覧では「内部」に分類する。

| コマンド | 終わり方 |
| --- | --- |
| `ndf records reading open [--work <作業名>]` | `ok`。作業のディレクトリのパスを返す |
| `ndf records reading prompt --request <ファイル名>` | `ok`。対象ごとの指示の文・`id`・範囲・コピーのパスを結果の `items` で返す。範囲が `none` なら `id` は無い。GitHub を読めなければ `stopped`・`unknown` |
| `ndf records reading add --file <ファイル名>` | 追記したら `ok`。誤りがあれば一覧を結果に入れて `invalid` |
| `ndf records reading check --input <ファイル名> --material <ファイル名> [--pr <番号>...]` | 足りないものが無ければ `ok`、あれば語の一覧を入れて `stopped`・`precondition`。GitHub を読めなければ `stopped`・`unknown` で、担当はそのことを承認資料に書いて進む |

- **質問・材料・結果の本文は、引数にも標準入力の here document にも書かない**（プロセスの引数とコマンドの実行記録に原文が残るため）。担当が、`open` の返したディレクトリへ Write の Tool で書き、コマンドにはファイル名だけを渡す。ディレクトリの外を指す名前・シンボリックリンク・ほかの利用者が所有するファイルは `invalid` とする。CLI は開いた時点で権限を 0600 にする。結果の JSON と誤りの一覧に、質問・材料・引用の値を入れない（`add` の返す「残った文」の行だけが例外で、伏せ字を当てた値である）
- `prompt` の要求は `{"targets": [...], "masks": [...]}` で、`targets` に次の 3 種類を並べる。`role`（立場）と `reader`（読み手の説明）はどれにも書く
  - `{"kind": "document", "path", "remained_in"}`（`remained_in` は任意）
  - `{"kind": "question", "input", "material"}`。`input` は `AskUserQuestion` に渡す入力そのもの（`questions` と `metadata`）を書いたファイルである。区分は、どのコマンドでもこの `metadata.source` から取り、PR の有無では決めない
  - `{"kind": "pr", "prs": [...], "read": true か false}`。今のリポジトリなら番号、ほかのリポジトリなら `<所有者>/<リポジトリ>#<番号>` を、承認する分だけ全部並べる
- `material` は、質問の直前の応答にそのまま載せる本文のファイルである。承認では承認資料、判断の質問では `decision-request` の 5 項目を書く。読み手役には、材料の後に質問を並べた 1 つの文書として渡す。材料を直したら、質問とともに読ませ直す
- 設計 PR の題と本文は読み手役に読ませないが（`read` を偽）、head と題と本文を記録に固定するために `prompt` に並べる。承認の手順の「本文と設計文書の突き合わせ」は、ここで書いたコピーを使い、`gh pr view` を打たない。リリースする PR では `read` を真にし、結果も記録する
- `Pull` に `title` を加え、`PULL_FIELDS` に `title` を加える

### 1 回の承認で増える処理の回数

CLI の起動は次のとおりである。`open` はセッションで 1 回だけ打つ。読み手役の起動は設計書の「影響と危険」にある。

| 経路 | CLI の起動 |
| --- | --- |
| 判断の質問 | `prompt`・`add`・`check` の 3 回（ほかに `open` 1 回） |
| 承認。文書・PR・質問を 1 回で読ませられる（材料に書くことが読む前に決まっている） | 3 回 |
| 承認。文書に「残った文」があり、材料を書いた後に PR と質問を読ませる | 5 回（文書の `prompt`・`add`、PR と質問の `prompt`・`add`、`check`） |
| 不合格で読み直させる | 立場の数によらず 1 回の読み直しごとに 2 回（`prompt`・`add`） |
| 質問・材料・PR が変わって照合をやり直す | `prompt`・`add`・`check` の 3 回 |

GitHub への問い合わせは、`prompt`（`kind` が `pr` の対象があるとき）と `check`（`--pr` があるとき）で 1 度ずつ起きる。`add` と hook は読まない。以下は 1 度あたりで、1 回の承認では 2 倍になる。PR を読む `prompt` や `check` を打ち直すと、その分だけ増える。

| 経路 | 1 度あたりの問い合わせ（リポジトリごとの PR の件数を n とする） |
| --- | --- |
| 通常（GraphQL） | リポジトリごとに `ceil(n / 100)` 回（`github.graphql-batch` は 100）。リポジトリをまたぐ承認では合計する。PR の件数が 100 以下なら、リポジトリの数と同じ |
| GraphQL の一次の上限で、n が 10 以下（`github.rest-fallback`） | リポジトリごとに、失敗した GraphQL の問い合わせに加えて REST が n 回 |
| GraphQL の一次の上限で、n が 11 以上、または二次の上限 | 読み替えず `unknown` で終わる |
| PR を伴わない承認・判断の質問 | 0 回 |

REST へ切り替わる経路では、`prompt` と `check` の両方が切り替わると、対象の PR ごとに最大 2 回（1 回の承認で）の REST が増える。

### 秘密と個人情報

- `prompt` は、対象の本文（`diff` の変更箇所も）に、`run_log.redact`（秘密の環境変数の値とトークンの形を伏せる）と、要求の `masks`（`{"value": <値>, "as": <置き換える語>}` の並び）を当てたコピーを書き、読み手役にはコピーのパスだけを渡す。版とハッシュは、伏せる前の値で求める
- 個人情報は機械では見分けられない。**担当は、文書・PR の題と本文・質問・材料を `prompt` に渡す前に読み、個人情報があれば `masks` に並べる**。質問と材料は、利用者に出す文と同じでなければならないので、値を書かずに確かめられる場所のリンクにする。文書と PR は、原文を変えられないときも `masks` でコピーから除く
- `masks` は作業のディレクトリの `masks.json` に足し、`add` が作る「残った文」の行にも当てる。担当は、その行を承認資料や PR の本文に書く前に、個人情報が残っていないことを確かめ、残っていれば `masks` に足して `add` をやり直す。PR の本文は広く読めるので、見分けに迷う値は伏せる
- 質問・材料・PR を読ませる相手は、質問を出す AI と同じランタイムのサブエージェントに限る（`external-ai` で別の CLI に渡さない）。起動できなければ読ませず、そのことを承認資料に書く
- 記録には、ハッシュ・id・パス・立場・結果だけを書く。質問の文・材料・PR の題と本文・残った文の引用と理由は書かない。引用と理由は、承認を求める文と PR の本文にだけ書く。`redact` を、書く行の値にも当てる
- 記録（0600）は NDF の状態の保存先、作業のディレクトリ（0700。所有者とリンクでないことを確かめる）は OS の一時ディレクトリの `ndf-reading-<uid>/<セッションの id>` に作る。コピー・要求・質問・材料・結果は、すべて作業のディレクトリに置く

| 消す時点 | 消すもの |
| --- | --- |
| `add` が結果を書いた | その対象のコピーと、結果の JSON。読み手役を起動できなかったときは、`result` を `skipped` として打つ。設計 PR のコピーは、その `pr_id` を使う質問の `add` が消す |
| `check` が `ok` か `unknown` | 質問と材料のほかのファイル |
| 会話が終わった（SessionEnd の guard `reading-clean`） | そのセッションの作業のディレクトリ |
| 次の会話が始まった（SessionStart の guard `reading-clean`） | 最終更新から 1 日（`records.reading-work`）を過ぎた、ほかのセッションの作業のディレクトリ。読み手役も `prompt` も要らない |
| 上のどれも動かない（会話が異常に終わり、その後 NDF を使わない） | OS の一時ディレクトリの掃除（再起動など）に任せる。状態の保存先には置かないので、NDF が動かない間に残り続けない |

記録の行は、`prompt` と `add` が、`records.reading-keep`（30 日）より古いものを記録のロックの中で消す。行には原文が無い。

### 内部のデータと定数

記録は `reading-checks.jsonl` で、保存先は `gate-rejections.jsonl` と同じ NDF の状態の保存先（リポジトリの外）である。コピーは作業のディレクトリの `<id>.md` に置く。追記は `jsonl.lock` の中で行う。行の種類は 3 つある。

| `kind` | 誰が書くか | 値 |
| --- | --- | --- |
| `given` | `prompt` | `id`・`at`・`repo`・`target`（`document`・`question`・`pr`）・`role`・範囲。文書は `path`・`raw`・`blob`。質問は `session`・`source`・`question_key`・`material`（材料の SHA-256）。PR は `prs` の並び（リポジトリ・番号・ブランチ・head・題と本文の SHA-256） |
| `read` | `add` | `at`・`reads`（`given` の値を引き継いだものに `round`・`result`・`remained` を足した並び。`remained` は残った文の行の SHA-256 の並び）。質問を含むときは、さらに `Scope` の 4 つ・`material`・`prs`・`no_pr_reason`・`documents` |
| `checked` | `check` | `at`・`Scope` の 4 つ・`result`（`ok`・`failed`・`unknown`） |

`add` に渡す結果の JSON:

| キー | 値 | 拒む条件 |
| --- | --- | --- |
| `reads[].id` | `prompt` が返した `id` | 対応する `given` が無い |
| `reads[].round` | 1 か 2（その立場で何回目の読みか） | 範囲の外 |
| `reads[].result` | `passed`（合格）・`failed`（1 回目の不合格。直して読み直させる）・`remained`（2 回目にも残った）・`skipped`（読み手役を起動できなかった。コピーを消すだけで、読んだことにはしない） | `remained` が 1 回目、`failed` が 2 回目 |
| `reads[].remained` | 残った文ごとの `quote`・`handled`（`fixed` か `kept`）・`reason` の並び | `result` が `remained` なのに空、または `reason` が空 |
| `input` | 質問の入力のファイル名（作業のディレクトリの中）。質問を記録するときだけ書く | 質問と材料を読ませた `id` の `question_key`・`source` と合わない、またはその結果が `failed` か `skipped` |
| `pr_id` | 承認する PR を並べた `prompt` の対象の `id`。質問の読みを引き継ぎ、PR だけを読み直した新しい `pr_id` に替えて `add` してよい。`prs` は `given` から引き継ぐ | `ndf-gate:` の質問で、`pr_id` と `no_pr_reason` のどちらも無い、または両方ある |
| `no_pr_reason` | PR を伴わない承認（設計承認ゲートの委任・運用の操作・制作物承認・PR の無いリリース）の理由。行には「ある」ことだけを書く | 空 |
| `documents` | 承認の根拠にする文書（`path`・`role`・`pr`） | 今の版の `passed` か `remained` の行が無い。`pr` があり、`git rev-parse <head>:<path>` が文書の `blob` と違う（文書の版の `raw` とは比べない。複数の文書は 1 回の git で調べる。head のコミットが手元に無いときも拒み、取得してから打ち直させる） |

### 照合の語

`missing` は、`Scope` が同じ最後の質問の `read` の行を、今の値と比べる。PR を求めるかどうかは、区分ではなく記録の `prs` と `no_pr_reason` で決まる。

| 語 | 条件 |
| --- | --- |
| `no_record` | `Scope` が同じ質問の行が無い |
| `pr_unmatched` | `--pr` の（リポジトリ・番号）の集合が、行の `prs` と同じでない（片方だけにある PR を結果に並べる。`no_pr_reason` の行に `--pr` があるとき、`prs` のある行で `--pr` を省いたときも当たる） |
| `head_moved` | `prs` の head が GitHub の今の head と違う |
| `pr_text_moved` | 行の `prs` のどれかで、題と本文の SHA-256 が今の値と違う。設計 PR も、突き合わせに使った本文なので比べる。当たったら `prompt` で PR を読み直し、設計 PR では本文と設計文書の突き合わせを、題と本文を読ませた PR では読み手役の確かめをやり直す |
| `material_moved` | 材料の SHA-256 が `--material` のファイルと違う |

`passable`（hook）は GitHub も git も使わず、次の語で決める。

| 語 | 条件 |
| --- | --- |
| `no_record` | `missing` と同じ |
| `no_check` | 最後の質問の行より後に、`Scope` が同じ `checked` の行が無いか、その最後の行の `at` から `hook.reading-check`（600 秒）が過ぎている |
| `check_failed` | 最後の質問の行より後の、`Scope` が同じ最後の `checked` の行の `result` が `failed`。それより前の `ok` は使わない |

## 質問の前に記録を調べる（hook）

設計書の決定 10・11 を実装し、受け入れ条件 17〜19 を満たす。`reading_check.check(ctx, svc) -> Verdict` は、上の行から順に調べ、最初に当てはまった行に従う。区分は `AskUserQuestion` の `metadata.source` の値である。

| 順 | 条件 | 結果 |
| ---: | --- | --- |
| 1 | 区分が `ndf-gate:` でも `ndf-ask:` でも始まらない | 通す |
| 2 | 人のいない実行（環境変数 `NDF_FACTORY_RUN` が空でない） | 通す |
| 3 | 記録のファイルを開けない（`OSError`） | 通す。hook の計測の記録に `reading: unreadable` を残す |
| 4 | `passable` が語を返す | 止める |
| 5 | 上のどれでもない | 通す。`checked` の `result` が `unknown` なら、計測の記録に `reading: unknown` を残す |

- 承認も判断の質問も同じ判定で、区分ごとの分岐は無い。PR の要否と PR の今の値は `check` だけが見る。hook は、`check` が質問の直前に合格したことだけを見る。リモートの名前も手元の追跡 ref も使わないので、別名のリモートや、別のマシンからの push で `check` の結果が覆ることはない
- `guards.json` の行: イベント `PreToolUse`・Tool `AskUserQuestion`・`order` 55（質問を転送する `ask-forward` の 60 より前）・`fail_closed` 偽・`deny_key` は `reason`・`limits` は `hook.reading-check` と `hook.counter-lock`
- `reading-clean` の行: イベント `SessionStart`・`SessionEnd`・`fail_closed` 偽・`limits` は `records.reading-work`。消せないときは計測の記録に残して通す
- 止める理由の文は、区分と、足りないものの語だけで決まる固定の文とし、記録を作るコマンドを添える。質問の文やハッシュ値を入れない。同じ理由の拒否を数える今の仕組み（`hook.deny-repeat` の 2 回）が、区分と語の組ごとに数えるようにするためである。2 回止めた後は、同じ区分の別の質問も、同じ理由なら注意に変わって通る

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
| `development-workflow/references/approval-request.md` | 承認の前の確かめの手順と、`reading prompt`・`add`・`check` を打つことを加える。本文と設計文書の突き合わせの `gh pr view` を、`prompt` が書いた PR のコピーを読む形に直す | 8〜10・12 |
| `development-workflow/references/asking.md` | 「hook は質問を止めない」を、記録が足りないと止まることに直す | 10・11 |
| `decision-request/SKILL.md` | 5 項目の材料をファイルに書き、質問と一緒に読み手役に読ませ、`check` に通してから、そのファイルの本文を直前の応答に載せて質問を出すことを加える | 8・10 |
| `development-workflow/references/agent-layers.md` | conductor が承認の前に読み手役を起動してよいことを加える | 8 |
| `cross-review/docs/01-state-and-review.md`・`fix/SKILL.md` | 指摘の基準の表に基準 5 を加える | 4・5 |

文書のほかに、用語集の正（`docs/glossary/glossary.json`）の「読み手役」と、`docs/ndf-plugin-reference.md` の hook の一覧を手で直す。`docs/glossary.md` と `plugins/ndf/docs/contracts/commands.md` は、生成するコマンドで作り直す。共通原則の今ある文・プロジェクトの MVV・用語の付け方の仕様（`ndf-ubiquitous-language.md`）は変えない。

## 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `document_block` | 相手・文書の有無・ラウンドの組み合わせで行が変わる | 基準を読めないと例外 |
| `worker.system_prompt` | `document-style` を読む作業だけに本文が入る。表に無い作業名では入らない | 基準を読めないと worker を起動しない |
| `changed_docs.doc_round` | 全差分の 2 ラウンド目に、前の head からの差分が `since` になる | 前の head が無い・相手役か重点が変わった・祖先でないと `since` が無い |
| `question_key` | 選択肢の説明・`multiSelect` の変更と、質問の末尾の行を選択肢の先頭へ移した質問で値が変わる。`multiSelect` を省いた質問と `false` の質問は同じ。区分だけが変わっても変わらない | — |
| `to_read` | 「文書の読みを引き継ぐ」の表の 5 行を 1 つずつ確かめる。`failed` と `skipped` の行は数えない | 残った文の行が 1 つ見つからなければ `full` |
| `missing` | 一致すれば空。「照合の語」の条件ごとにその語を返す。#10 だけの記録に #10 と #11 を渡すと `pr_unmatched` | `Scope` の 4 つのどれか 1 つが違う行は使わない |
| `passable` | 質問の行より後の、600 秒以内の `checked` で通す（時刻は引数で渡す） | `checked` が質問の行より前・600 秒を過ぎた・別のセッション・最後の `checked` が `failed`（前に `ok` があっても）なら止める |
| `reading.add` | `given` の版を引き継いで 1 行追記し、コピーを消す。質問の無い結果も追記する | 「`add` に渡す結果の JSON」の拒む条件ごとに `ReadingInvalid` |
| `reading.check` | リポジトリごとに 1 回の `pulls` で読む。設計 PR の本文だけが変わると `pr_text_moved` | 読めないと `unknown`、足りないと `failed` の行を書く |
| `reading.prompt` | 改行の変換を持つ文書で、`raw` と `blob` が同じバイト列から作られる（読み取りは 1 度）。読ませた後に PR の head・題・本文が変わると `check` で止まる。記録に質問の文・材料・引用が入らない。`masks` と秘密がコピーに残らない | 作業のディレクトリの外・リンク・環境変数が空・Claude Code での `--work` を拒む。30 日より古い行を消す |
| `reading.clean` | 終わったセッションのディレクトリと、1 日を過ぎたほかのディレクトリだけを消す（時刻は引数で渡す） | リンクや、ほかの利用者のディレクトリは消さない |
| `reading_check.check` | 上の表の順 1〜5 を 1 つずつ確かめる。壊れた行だけの記録は `no_record` で止まる | 開けない記録で通す |

テストは、GitHub と時刻を引数で渡した値に置き換え、実時間を待たない。git を使う関数は、今の fixture のリポジトリで確かめる。
