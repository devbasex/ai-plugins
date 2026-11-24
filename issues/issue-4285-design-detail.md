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
  contracts/domains/ndf-files.json               記録と、読ませるためのコピーを登録する
  contracts/limits/hook.reading-check.json *     照合の結果が有効な時間（600 秒）
  contracts/limits/records.reading-keep.json *   記録を残す時間（30 日）
  src/ndf/contracts/writing.py *
  src/ndf/adapters/{github,git}.py
  src/ndf/core/worker.py
  src/ndf/domains/crossreview/{criteria_decide,drive}.py
  src/ndf/domains/crossreview/changed_docs.py *
  src/ndf/domains/records/{reading_decide,reading}.py *
  src/ndf/domains/records/cli.py
  src/ndf/domains/hooks/guards/reading_check.py *
  src/ndf/domains/hooks/strict.py
  tests/ の対応するファイル
```

依存の向き: `ndf.contracts.writing` は最も下の層にあり、cross-review・`core/worker`・`records` から import する。guard は `records` の `reading_decide` と `reading.rows` を import し、`records` は hook のモジュールを import しない。

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
| `domains/records/reading.py` | 新設 | 読み手役への指示を作り、記録を書き、読む | `prompt`・`add`・`rows`・`check`・`repo_key` |
| `domains/records/cli.py` | 変更 | `ndf records reading` の引数 | `_reading`（新設）・`build` |
| `domains/hooks/guards/reading_check.py` | 新設 | 質問の前に記録を調べる | `check` |

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

担当は `prompt`（読ませる内容を固定し、指示を作る）→ 読み手役の起動 → `add`（結果の記録）→ `check`（質問の直前の照合）の順に打つ。何を読ませたかは `prompt` が記録するので、読んでいる間に変わった文書・質問・PR を「読んだ」とは記録できない。

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `reading.prompt` | 新設 | `(target: Target, role: str, reader: str, root: Path) -> dict[str, Any]` | 対象（文書・質問と材料・PR）を読んで版を求め、`to_read` で読ませる範囲を決め、コピーと `given` の行を書き、指示の文・`id`・範囲・コピーのパスを返す。指示には、読み手の説明（立場・ふだん使う言葉・知識を書いた文）・4 つの型・返す形・コピーのパスを入れる |
| `reading.add` | 新設 | `(entry: Mapping[str, Any], root: Path) -> dict[str, Any]` | 担当が書いた結果を確かめ、`given` の行から版を引き継いで 1 行追記し、その行を返す。形が違う・対応する `given` が無い・読んだ後に変わっているときは `ReadingInvalid` を送出し、追記しない。GitHub は読まない |
| `reading.check` | 新設 | `(tool_input, prs, material, root) -> list[str]` | 質問の直前の照合。今の値を読んで `missing` に渡し、空なら `checked` の行を追記する。GitHub を読めず、PR の今の値によらない語（`no_record`・`pr_unmatched`・`material_moved`）に当たらなければ、`result` を `unknown` とした `checked` の行を追記し、`GitHubError` を送出する（当たればその語を返す） |
| `reading.rows` | 新設 | `() -> list[dict[str, Any]]` | 記録を古い順に返す。壊れた行は無いものとして扱う。ファイルが無ければ空。ファイルを開けなければ `OSError` を送出する |
| `reading.repo_key` | 新設 | `(cwd: Path) -> str` | `cwd` から親へ `.git` を探し、共通の git ディレクトリの実パスを返す（git を起動しない。worktree からもメインディレクトリと同じ値になる）。git の外では `cwd` の実パス。hook の `strict.py` にある `_repo_root`・`_git_dir` を `adapters/git.py` へ移して両方から使う |
| `reading_decide.question_key` | 新設 | `(questions: Sequence[Mapping[str, Any]]) -> str` | 質問の文・選択肢の名前・選択肢の説明を、それぞれ前後の空白を除いて出現順に改行でつなぎ、その SHA-256 を返す |
| `reading_decide.to_read` | 新設 | `(rows, repo: str, path: str, role: str, blob: str) -> Reading` | 文書と立場の組について、読ませる範囲 `none`・`diff`・`full` と、`diff` のときの前の版を返す（下の表） |
| `reading_decide.missing` | 新設 | `(rows, scope: Scope, now: Current) -> list[str]` | `check` が使う。足りないものを表す語（下の表）を返す。空なら足りている |
| `reading_decide.passable` | 新設 | `(rows, scope: Scope, at: datetime, keep: float) -> Optional[str]` | hook が使う。質問を通すなら `None`、止めるなら語（下の表）を返す |

`Scope` は記録を使ってよい範囲で、リポジトリ（`repo_key`）・作業（質問を出すセッションの id）・区分（`metadata.source` の値）・`question_key` の 4 つの組である。CLI はセッションの id を環境変数 `CLAUDE_CODE_SESSION_ID`、hook は入力の `session_id` から取り、4 つがすべて同じ行だけを使う。そのため、同じ文の質問でも、別のレーン・リポジトリ・区分の記録は使われず、後から書かれた別の作業の行で上書きもされない。セッションが変わったとき（中継の引き継ぎなど）は、質問だけを読ませ直す。文書の記録は作業によらず、リポジトリ・パス・立場・版で引き継ぐ。

### 文書の読みを引き継ぐ

文書の版は git の blob の id（`git hash-object <パス>`）とする。`to_read` は、同じリポジトリ・パス・立場の、`failed` でない最後の結果の行で決める。

| 最後の結果の行 | 読ませる範囲 |
| --- | --- |
| 無い | `full`（全文） |
| 版が今と同じ（`passed` か `remained`） | `none`。読み手役を起動しない |
| `passed` で版が違い、前の版を `git cat-file` で読める | `diff`（前の版から変わった部分と、前後の段落）。指示に変更箇所を並べる |
| 上のどれでもない | `full` |

組み直しの担当も同じ `prompt` と `add` を使い、質問の無い結果を記録する。承認を求める担当は、変わっていない文書では読み手役を起動せずに済む。

### コマンド

AI が手順の中で打つ。コマンドの一覧では「内部」に分類する。

| コマンド | 終わり方 |
| --- | --- |
| `ndf records reading prompt --role <立場> --reader <読み手の説明>` に、`--document <パス>`・`--input <AskUserQuestion の入力の JSON> --material <材料のファイル>`・`--pr <番号>...` のどれか 1 つを添える | `ok`。指示の文・`id`・範囲・コピーのパスを結果の `metrics` で返す。範囲が `none` なら `id` は無い。GitHub を読めなければ `stopped`・`unknown` |
| `ndf records reading add --file <結果の JSON>` | 追記したら `ok`。誤りがあれば一覧を結果に入れて `invalid` |
| `ndf records reading check --input <AskUserQuestion の入力の JSON> --material <材料のファイル> [--pr <番号>...]` | 足りないものが無ければ `ok`、あれば語の一覧を入れて `stopped`・`precondition`。GitHub を読めなければ `stopped`・`unknown` で、担当はそのことを承認資料に書いて進む |

- `--input` は `AskUserQuestion` に渡す入力そのもの（`questions` と `metadata`）である。質問の区分は、どのコマンドでも `metadata.source` から取る。`--pr` の有無では決めない
- `--material` は、質問の直前の応答にそのまま載せる本文のファイルである。承認では承認資料、判断の質問では `decision-request` の 5 項目を書く。読み手役には、材料の後に質問を並べた 1 つの文書として渡す。材料を直したら、質問とともに読ませ直す
- `--pr` は、今のリポジトリなら番号、ほかのリポジトリなら `<所有者>/<リポジトリ>#<番号>` と書く。承認する PR を全部並べる
- 設計 PR の題と本文は読み手役に読ませないが、head を記録に固定するために `prompt --pr` を打つ（読み手役は起動しない）。リリースする PR では、題と本文を読ませて結果も記録する
- PR は `GitHub.pulls` でリポジトリごとにまとめて読む（100 件ごとに 1 クエリ）。1 回の承認での呼び出しは、`prompt --pr` と `check` の各 1 回で、PR の件数では増えない。`add` は `prompt --pr` の値を引き継ぐので呼び出さない。承認の手順の「本文と設計文書の突き合わせ」は、`prompt --pr` が書いたコピーを使い、`gh pr view` を打たない。`check` だけは、変わっていないことを確かめるために読み直す
- `Pull` に `title` を加え、`PULL_FIELDS` に `title` を加える

### 秘密と個人情報

- `prompt` は、対象の本文（`diff` の変更箇所も）に `run_log.redact`（秘密の環境変数の値とトークンの形を伏せる）を当てたコピーを書き、読み手役にはコピーのパスだけを渡す。版とハッシュは、伏せる前の値で求める
- 個人情報は機械では見分けられないので、担当が質問と材料に値を書かず、確かめられる場所のリンクにしてから `prompt` に渡す。質問・材料・PR を読ませる相手は、質問を出す AI と同じランタイムのサブエージェントに限る（`external-ai` で別の CLI に渡さない）。起動できなければ読ませず、そのことを承認資料に書く
- 記録には、ハッシュ・id・パス・立場・結果・件数だけを書く。質問の文・材料・PR の題と本文・残った文の引用と理由は書かない。引用と理由は、承認を求める文（判断を求めない文書では PR の本文）にだけ書く。`redact` を、書く行の値にも当てる
- 記録とコピーは権限 0600、コピーのディレクトリは 0700 で作る。コピーは `add` が結果を書いたときに消す。`prompt` は、記録の行と残ったコピーのうち `records.reading-keep`（30 日）より古いものを、記録のロックの中で消す

### 内部のデータと定数

記録は `reading-checks.jsonl`、コピーは `reading/<id>.md` で、保存先は `gate-rejections.jsonl` と同じ NDF の状態の保存先（リポジトリの外）である。追記は `jsonl.lock` の中で行う。行の種類は 3 つある。

| `kind` | 誰が書くか | 値 |
| --- | --- | --- |
| `given` | `prompt` | `id`・`at`・`repo`・`target`（`document`・`question`・`pr`）・`role`・範囲。文書は `path` と `blob`。質問は `session`・`source`・`question_key`・`material`（材料の SHA-256）。PR は `prs` の並び（リポジトリ・番号・ブランチ・head・題と本文の SHA-256） |
| `read` | `add` | `at`・`reads`（`given` の値を引き継いだものに `round`・`result`・`remained` の件数を足した並び）。質問を含むときは、さらに `Scope` の 4 つ・`material`・`prs`・`no_pr_reason`・`documents` |
| `checked` | `check` | `at`・`Scope` の 4 つ・`result`（`ok` か `unknown`） |

`add` に渡す結果の JSON:

| キー | 値 | 拒む条件 |
| --- | --- | --- |
| `reads[].id` | `prompt` が返した `id` | 対応する `given` が無い |
| `reads[].round` | 1 か 2（その立場で何回目の読みか） | 範囲の外 |
| `reads[].result` | `passed`（合格）・`failed`（1 回目の不合格。直して読み直させる）・`remained`（2 回目にも残った） | `remained` が 1 回目、`failed` が 2 回目 |
| `reads[].remained` | 残った文の件数 | `result` が `remained` なのに 0 |
| `input` | 質問の入力の JSON。質問を記録するときだけ書く | 質問と材料を読ませた `id` の `question_key`・`source` と合わない、またはその結果が `failed` |
| `pr_id` | 承認する PR の `prompt --pr` の `id`。`prs` は `given` から引き継ぐ | `ndf-gate:` の質問で、`pr_id` と `no_pr_reason` のどちらも無い、または両方ある |
| `no_pr_reason` | PR を伴わない承認（設計承認ゲートの委任・運用の操作・制作物承認・PR の無いリリース）の理由。行には「ある」ことだけを書く | 空 |
| `documents` | 承認の根拠にする文書（`path`・`role`・`pr`） | 今の版の `passed` か `remained` の行が無い。`pr` があり、`git rev-parse <head>:<path>` が文書の版と違う（複数の文書は 1 回の git で調べる。head のコミットが手元に無いときも拒み、取得してから打ち直させる） |

### 照合の語

`missing` は、`Scope` が同じ最後の質問の `read` の行を、今の値と比べる。PR を求めるかどうかは、区分ではなく記録の `prs` と `no_pr_reason` で決まる。

| 語 | 条件 |
| --- | --- |
| `no_record` | `Scope` が同じ質問の行が無い |
| `pr_unmatched` | `--pr` の（リポジトリ・番号）の集合が、行の `prs` と同じでない（片方だけにある PR を結果に並べる。`no_pr_reason` の行に `--pr` があるとき、`prs` のある行で `--pr` を省いたときも当たる） |
| `head_moved` | `prs` の head が GitHub の今の head と違う |
| `pr_text_moved` | 題と本文を読ませた PR（`pr_id` の結果が `reads` にある）で、題と本文の SHA-256 が今の値と違う |
| `material_moved` | 材料の SHA-256 が `--material` のファイルと違う |

`passable`（hook）は GitHub も git も使わず、次の語で決める。

| 語 | 条件 |
| --- | --- |
| `no_record` | `missing` と同じ |
| `no_check` | 最後の質問の行より後に、`Scope` が同じ `checked` の行が無いか、その `at` から `hook.reading-check`（600 秒）が過ぎている |

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
| `development-workflow/references/approval-request.md` | 承認の前の確かめの手順と、`reading prompt`・`add`・`check` を打つことを加える。本文と設計文書の突き合わせの `gh pr view` を、`prompt --pr` のコピーを読む形に直す | 8〜10・12 |
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
| `question_key` | 選択肢の説明が変わると値が変わる。区分だけが変わっても変わらない | — |
| `to_read` | 「文書の読みを引き継ぐ」の表の 4 行を 1 つずつ確かめる。`failed` の行は数えない | 前の版を読めなければ `full` |
| `missing` | 一致すれば空。「照合の語」の条件ごとにその語を返す。#10 だけの記録に #10 と #11 を渡すと `pr_unmatched` | `Scope` の 4 つのどれか 1 つが違う行は使わない |
| `passable` | 質問の行より後の、600 秒以内の `checked` で通す（時刻は引数で渡す） | `checked` が質問の行より前・600 秒を過ぎた・別のセッションなら止める |
| `reading.add` | `given` の版を引き継いで 1 行追記し、コピーを消す。質問の無い結果も追記する | 「`add` に渡す結果の JSON」の拒む条件ごとに `ReadingInvalid` |
| `reading.check` | 複数の PR を 1 回の `pulls` で読む | 読めないと `unknown` の行を書く |
| `reading.prompt` | 読ませた後に PR の head・題・本文が変わると `check` で止まる。記録に質問の文・材料・引用が入らない | 30 日より古い行とコピーを消す |
| `reading_check.check` | 上の表の順 1〜5 を 1 つずつ確かめる。壊れた行だけの記録は `no_record` で止まる | 開けない記録で通す |

テストは、GitHub と時刻を引数で渡した値に置き換え、実時間を待たない。git を使う関数は、今の fixture のリポジトリで確かめる。
