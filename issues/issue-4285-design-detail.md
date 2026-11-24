# #4285 の詳細設計: 文書の基準を指示に入れ、読み手役の確かめの記録を調べる

設計文書（[issue-4285-design.md](./issue-4285-design.md)）の構成要素を、ファイルと関数の単位にした文書である。

## 地図

```text
plugins/ndf/
  contracts/data/writing-criteria.md *      基準の本文
  contracts/data/review-criteria.json       指摘の基準 5 と指示の文
  contracts/data/ndf-common-principles.md   原則の 1 文
  contracts/hooks/guards.json               guard「reading-check」の行
  contracts/domains/ndf-files.json          記録のファイルの行
  src/ndf/contracts/writing.py *            基準の本文と節を読む
  src/ndf/domains/crossreview/{criteria_decide,drive}.py
  src/ndf/progress/gate/entry.py
  src/ndf/domains/records/reading_decide.py *   記録の判断（純粋な関数）
  src/ndf/domains/records/reading.py *          記録の読み書きとサブコマンド
  src/ndf/domains/records/cli.py
  src/ndf/domains/hooks/guards/reading_check.py *
```

依存の向き: `ndf.contracts.writing` は最も下の層で、cross-review・`progress/gate`・`records` が読む。hook の guard は `records` の判断と読み込みを使い、`records` は hook を読まない。

| パス | 種類 | 役割 | メソッドと最上位の関数 |
| --- | --- | --- | --- |
| `contracts/writing.py` | 新設 | 基準の本文を読む | `text()`・`types_section()` |
| `domains/crossreview/criteria_decide.py` | 変更 | 指示の行を作る | `document_block` を新設 |
| `domains/crossreview/drive.py` | 変更 | 指示に行を加える | `_prepare_round`・`_review_prompt`・`_fix_prompt` |
| `progress/gate/entry.py` | 変更 | worker の指示に本文を入れる | `_skills_of` |
| `domains/records/reading_decide.py` | 新設 | 質問の正規化と、通すか止めるかの判断 | `question_key`・`missing` |
| `domains/records/reading.py` | 新設 | 読み手役の指示・追記・確かめ | `prompt`・`add`・`check`・`rows` |
| `domains/records/cli.py` | 変更 | `ndf records reading` の引数 | `_reading`・`build` |
| `domains/hooks/guards/reading_check.py` | 新設 | 質問の前の確かめ | `check` |

## 基準の本文を指示に入れる

満たす決定: 決定 2・4・5・6、受け入れ条件 7〜11

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `writing.text` | 新設 | `() -> str` | `writing-criteria.md` の全文。読めなければ `ContractError` |
| `writing.types_section` | 新設 | `() -> str` | 4 つの型の節だけ。見出しが見つからなければ `ContractError` |
| `criteria_decide.document_block` | 新設 | `(role: str, documents: bool, round_no: int = 1) -> list[str]` | `documents` が偽なら空。`role` が `reviewer` なら基準 5・書き方・2 ラウンド目以降の制限の文・基準の本文、`fixer` なら直し方の文と基準の本文 |
| `_prepare_round` | 変更 | 変えない | 変わった Markdown の有無を `_Run.documents` に持つ |
| `_review_prompt`・`_fix_prompt` | 変更 | 変えない | `document_block` の行を加える |
| `entry._skills_of` | 変更 | 変えない | `document-style` を読む作業では、Skill の一覧の後に `writing.text()` を続ける |

- 読めないときの扱いは、今の `review-criteria.json` を読めないときと同じにする。指示を作る前に止まり、cross-review は `invalid`、worker は起動しない
- `drive.py` は 998 行あるので、加えるのは 2 行以内にする。1,000 行を超えるなら、変わったファイルを調べる関数（`_regular_blobs` から `changed_sections` まで）を別ファイルへ移す
- 基準 5 の指摘も、`impact` の無い major は minor に下げる今の規則に従う。`review-criteria.json` の `impact.reviewer` に「基準 5 では、どの読み手が何を取り違えるか」を加える

## 読み手役の指示と確かめの記録

満たす決定: 決定 7〜10、受け入れ条件 12〜17

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `reading.prompt` | 新設 | `(document: Path, reader: str) -> str` | 読み手役への指示。読み手の 3 点（`reader`）・4 つの型・返す形・文書のパスを入れる |
| `reading.add` | 新設 | `(row: Mapping[str, Any]) -> None` | 記録を 1 行追記する（`jsonl.append_locked`） |
| `reading.rows` | 新設 | `() -> list[dict[str, Any]]` | 記録を読む。壊れた行は除く |
| `reading.check` | 新設 | `(key: str, prs: Sequence[Pr], root: Path) -> list[str]` | GitHub の head と比べ、足りないものを返す（承認の手順用） |
| `reading_decide.question_key` | 新設 | `(questions: Sequence[Mapping[str, Any]]) -> str` | 質問の文・選択肢の名前と説明だけを順に並べ、前後の空白を除いた文の SHA-256 |
| `reading_decide.missing` | 新設 | `(key: str, gate: bool, rows, heads: Mapping[str, str]) -> list[str]` | 足りないものの説明。空なら通す |

コマンド（どれも内部の扱い）: `ndf records reading prompt --document <パス> --reader <3 点の文>`、`ndf records reading add --file <記録の JSON>`、`ndf records reading check --questions <質問の JSON> [--pr <番号>...]`。`check` は足りないものがあれば `status: stopped`・`precondition` で終わる。

### 内部のデータと定数

記録は `reading-checks.jsonl` に 1 行ずつ追記する。保存先は `gate-rejections.jsonl` と同じ（NDF の状態の保存先で、リポジトリの外）。形は `contracts/schemas/reading-check.schema.json`（新設）で確かめる。

| キー | 値 |
| --- | --- |
| `at` | 記録した時刻 |
| `question_key` | 質問の文の SHA-256（`question_key` の値） |
| `prs` | 承認する Pull Request の番号・ブランチ・head（SHA）。区分が `ndf-ask:` の質問では空でよい |
| `reads` | 読ませたものの並び。名前（パスか「質問の文」など）・中身の SHA-256・立場・回数・結果（`passed` か `left`） |
| `left` | 2 回目にも残った文の引用と、直さなかった理由。結果に `left` があれば 1 件以上必要 |

- `reads` に「質問の文」が無い記録は、`add` が拒む
- 同じ質問に記録が 2 行以上あれば、新しいほうを使う

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `reading.add` | 形が違う | 追記せず、誤りの一覧を返して `invalid` で終わる |
| `reading.rows` | ファイルが無い | 記録なしとして扱う |
| `reading.check` | GitHub を読めない | 止まる（`unknown`）。承認の手順は、確かめられなかったことを承認資料に書いて進む |

## 質問の前の確かめ（hook）

満たす決定: 決定 10・11、受け入れ条件 17〜19

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `reading_check.check` | 新設 | `(ctx, svc) -> Verdict` | 区分を見て、記録を調べ、止めるか通すかを返す |

| 条件 | 結果 |
| --- | --- |
| 区分が `ndf-gate:` でも `ndf-ask:` でも始まらない | 通す |
| 人のいない実行（`NDF_FACTORY_RUN`） | 通す |
| `ndf-ask:` で、質問の文の記録がある | 通す |
| `ndf-gate:` で、質問の文の記録があり、記録したすべての Pull Request の head が `origin/<ブランチ>` と同じ | 通す |
| `ndf-gate:` で、`origin/<ブランチ>` を読めない | 通し、計測に理由を残す |
| 記録のファイルを読めない | 通し、計測に理由を残す |
| 上のどれでもない | 止める。理由に、足りないもの（質問の文の記録が無い・head が変わった）と `ndf records reading add` の打ち方を書く |

- `guards.json` の行: イベント `PreToolUse`・Tool `AskUserQuestion`・`order` 55（質問を転送する `ask-forward` の 60 より前）・`fail_closed` 偽・`deny_key` `reason`・`limits` は `hook.git-rev-parse` と `hook.counter-lock`
- 同じ理由で 2 回止めた後に注意へ変わるのは、今ある `hook.deny-repeat`（`attempts` 2）の働きである。止める理由は質問ごとに同じ文にし、ハッシュ値を入れない（入れると、質問を少し直すたびに数え直しになる）
- git は 1 回だけ起動し（`git for-each-ref refs/remotes/origin/<ブランチ>...`）、上限は `hook.git-rev-parse` の 0.4 秒とする
- Codex には `AskUserQuestion` が無いので、`runtimes.json` の `skipped` に `reading-check` を加える

## 手順書の変更

| ファイル | 変える中身 | 決定 |
| --- | --- | --- |
| `skills/document-style/SKILL.md` | 冒頭の読み手の説明とルール 11 を、基準へのリンクに置き換える。言い換えの表は例として残す。チェックリストの該当行を「文書の読み手に通じる言葉で書いている」に直す | 3 |
| `skills/document-drafting/SKILL.md`・`skills/document-rebuild/references/common.md`・`skills/document-rebuild/references/keyword-check.md`・`skills/pr-review/SKILL.md`・`skills/development-workflow/references/glossary.md` | ルール 11 と読み手を説明する行を、基準へのリンクに置き換える | 2・3 |
| `skills/requirements-design/SKILL.md`・同じ Skill の `references/document-roles.md`・`references/spec-template.md` | 読み手の説明を基準へのリンクに置き換える。「リリース後の仕様書」などの呼び方も合わせる。判断・読み方・見出しの順は変えない | 2・3 |
| `skills/document-rebuild/SKILL.md` | 読み手役に渡すもの・返させるもの・合否・回数・記録の残し方を直す | 7〜9 |
| `skills/development-workflow/references/approval-request.md`・`skills/decision-request/SKILL.md`・`skills/development-workflow/references/asking.md` | 承認の前の確かめ・`reading check` を打つこと・記録が無いと止まることを加える。`asking.md` の「hook は質問を止めない」を直す | 8〜12 |
| `skills/development-workflow/references/agent-layers.md` | conductor が承認の前に読み手役を起動してよいことを加える | 8 |
| `skills/cross-review/docs/01-state-and-review.md`・`skills/fix/SKILL.md` | 指摘の基準の表に基準 5 を加える | 4・5 |
| `docs/glossary/glossary.json`・`docs/glossary.md`・`plugins/ndf/docs/contracts/commands.md`・`docs/ndf-plugin-reference.md` | 「読み手役」の意味・コマンドの一覧・hook の一覧を直す（生成するものは生成する） | 7・10 |

共通原則の「人と AI の対話」のほかの文、プロジェクトの MVV、用語の付け方の仕様（`ndf-ubiquitous-language.md`）は、別のことを定めているので変えない。

## 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `document_block` | 役割・文書の有無・ラウンドの組み合わせで行が変わる | 基準を読めないと例外 |
| `entry._skills_of` | `document-style` を読む作業だけに本文が入る | 基準を読めないと worker を起動しない |
| `question_key` | 選択肢の説明が変わると値が変わる。区分だけが変わっても変わらない | — |
| `missing` | 一致すれば空 | 記録なし・質問の違い・head の違いを別々の理由で返す |
| `reading.add` | 1 行追記する | 「質問の文」の無い記録・`left` の理由の無い記録を拒む |
| `reading_check.check` | 上の表の行ごとに止める・通す | git の失敗・壊れた記録で通す |
