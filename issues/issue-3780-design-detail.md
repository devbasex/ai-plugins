# #3780 の詳細設計: 2 ラウンド目以降は直した箇所を確かめて終わる

[設計文書](./issue-3780-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の理由・不変条件（I1〜I3）・起票の手順は設計文書を、受け入れ条件は [要求仕様](./issue-3780-requirements.md) を指す。

[#4363 の詳細設計](./issue-4363-design-detail.md) の後に実装する。そこで作る `reviewing.py`・`carry.py`・`verify_decide.select`・`journal.apply_checks`（返りの `Checked`）・`journal.fresh`・`drive._confirm_heavy`・`criteria_decide.protect_kinds` を前提にする。

| 語 | 意味 |
| --- | --- |
| 起点 | 今回の差分の始まりにする、前に全員が読んだラウンドの head |
| 線引き | 2 ラウンド目以降の指摘を、修正へ進めるものと直さずに残すものに分けること |
| 直さずに残す指摘 | 修正担当へ渡さず、次のラウンドを始める理由にも数えない指摘（指摘の行の `parked`） |
| 修正へ進む | `proceeds` が真であること。`parked` を付けない（付いていれば外す）という意味である。修正担当へ渡すかは別に重要度で決まり、minor・nit は真でも渡さない |
| 指摘の行・ソースの行 | 「指摘の行」は状態ファイルの指摘 1 件分の記録。「ソースの行」はコードや文書の行で、「変えた行」「足した行」「消した行」はソースの行を指す |
| 判定対象外の指摘 | 判定役（指摘を確かめる読むだけの AI）へ渡さないと決めた指摘（`verify_decide.select` が外したもの） |
| 確認できなかった指摘 | 判定役へ渡したのに、有効な回答が無い指摘（`Checked.unconfirmed`） |
| PR の巻き直し | Pull Request を閉じて、新しい番号で出し直すこと。状態ファイルは元の番号（`pr`）と今の番号（`current_pr`）を持つ |
| `issue_logins` | 起票に使った GitHub の login の並び（状態ファイルに持つ）。別の login で打ち直したときに、前の login が書いた起票の記録と Issue を信用して読むために使う |
| 起票の記録 | 起票を試みた指摘と Issue の URL を持つ、元の PR の専用のコメント |

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/crossreview/
│   ├── local_scope.py *       review-scope の範囲の判断（cli.py から移す）
│   ├── leftover_decide.py *   線引き・残し方・起票の判断
│   ├── leftover.py *          起票と起票の記録
│   ├── scope_decide.py
│   ├── round_scope.py
│   ├── changes.py
│   ├── journal.py
│   ├── finish_decide.py
│   ├── report_decide.py
│   ├── prior_decide.py
│   ├── threads.py
│   ├── drive.py
│   ├── settings.py
│   └── cli.py
├── contracts/
│   ├── data/review-criteria.json
│   ├── limits/ cross-review.max-rounds.json, cross-review.max-rounds.design.json *, github.list-pages.json
│   └── schemas/io/ cross-review.result.schema.json, cross-review.args.schema.json
├── docs/contracts/commands.md
├── skills/cross-review/ SKILL.md, docs/01-*.md, docs/02-*.md
└── tests/domains/crossreview/
```

依存の向き: `cli` が `local_scope`・`leftover`・`leftover_decide`・`drive` を読み、`drive` が `round_scope`・`changes`・`journal` を読み、`leftover` が `leftover_decide`・`store`・`threads`・`settings` と、GitHub と公開のアダプター（`github`・`publish`）を読み、`journal`・`finish_decide`・`report_decide` が `leftover_decide` を読み、`leftover_decide` は `criteria_decide` と `contracts` だけを読む。`changes` は変えた行の型を `leftover_decide` から受ける（判断のモジュールは外部との接続の層を読めないので、型を判断の側に置く）。`local_scope` は `cli` を読まず、設定の値を Protocol で受ける。輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/crossreview/local_scope.py` | 新設 | `review-scope` の範囲を手元の git と状態の記録から決める | `scope_decide`・`round_scope`・`changes` | モジュール、`ScopeSettings`（Protocol）、`_LocalBase` | 変更（移動）: `decide`（今の `_local_scope`）・`_local_base`・`_local_mvv`・`_local_sections` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover_decide.py` | 新設 | 指摘が進むか、残すなら起票か記録かを決め、起票の文を組む | `criteria_decide` | モジュール、`Touched`・`Listed` | 新設: `normalized`・`proceeds`・`finding_kinds`・`files_issue`・`identity`・`issue_parts`・`trusted`・`matches`・`record_entries`・`record_body`・`result_fields`・`summary_line`・`decisions` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover.py` | 新設 | 残す指摘を起票し、URL を行に書く | `leftover_decide`・`store`・`threads`・`settings`・`publish`・`github` | `Port`（Protocol）、`GitHubIssues` | 新設: `primary_of`・`file_issues` |
| `plugins/ndf/src/ndf/domains/crossreview/scope_decide.py` | 変更 | 起点の条件と、収束を使い回す条件を分ける | `store` | モジュール | 新設: `origin_record` |
| `plugins/ndf/src/ndf/domains/crossreview/round_scope.py` | 変更 | 起点と線引きの起点を git で確かめる | `scope_decide`・`changes` | モジュール | 新設: `read_origin`・`line_origin` |
| `plugins/ndf/src/ndf/domains/crossreview/changes.py` | 変更 | 新しく PR の変更になったファイル・渡す差分・変えた行を読む | `git`・`leftover_decide` | モジュール、`Range` | 新設: `new_in_pr`・`since_diff`・`touched_since` |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | 変更 | 残す指摘に `parked` を付け、進む条件に当たれば外す | `leftover_decide` | `FindingJournal` | 新設: `sort` / 変更: `enter`・`_revive`・`_classify`・`carry`・`handable`・`findings` |
| `plugins/ndf/src/ndf/domains/crossreview/finish_decide.py` | 変更 | `parked` の指摘を欠けから除く | `leftover_decide` | `Finding` | 変更: `_finding_gaps`・`_counts`・`as_findings` |
| `plugins/ndf/src/ndf/domains/crossreview/report_decide.py` | 変更 | 表に残し方と URL を出し、一覧に起票済みを載せる | `leftover_decide` | モジュール | 変更: `_finding_rows`・`open_block` |
| `plugins/ndf/src/ndf/domains/crossreview/prior_decide.py` | 変更 | 記録のコメントの `parked` と `issue` を読む（設計の「データ構造」の引き継ぎの決まり。設計の触るファイルに足した） | `report_decide` | `Carried` | 変更: `_recorded_one` |
| `plugins/ndf/src/ndf/domains/crossreview/threads.py` | 変更 | `parked` の指摘をスレッドにせず、login を渡す | `report_decide` | `GitHubPort` | 新設: `GitHubPort.viewer` / 変更: `_wanted`・`_trusted` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更 | 範囲の起点を目的で分け、線引きを当てる | `round_scope`・`changes`・`journal` | `_Run` | 新設: `_ranges`・`_sort_leftovers` / 変更: `_prepare_round`・`_round` |
| `plugins/ndf/src/ndf/domains/crossreview/settings.py` | 変更 | 目的ごとの上限の既定を持つ | `limits` | `RunConfig` | 新設: `default_max_rounds` |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | 変更 | 起票を記録の公開に組み込み、結果に残し方を載せる | `leftover`・`leftover_decide`・`local_scope`・`drive` | `Hub` | 新設: `_with_leftovers` / 変更: `_github_hub`・`_post_report`・`_publish`・`_report`・`_run_config`・`_run_parser`・`cmd_scope`・`cmd_report` |
| `plugins/ndf/contracts/data/review-criteria.json` | 変更 | 起票する指摘の種類 `kinds.filed` を足す（設計の触るファイルに足した） | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.max-rounds.json` | 変更 | 実装の PR の既定を 4 にする | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.max-rounds.design.json` | 新設 | 設計の PR の既定 3 を持つ | 無し | データ | — |
| `plugins/ndf/contracts/limits/github.list-pages.json` | 変更 | 読み手に `leftover` を足す（`ndf check` の `limit-readers` が落ちるため。設計の触るファイルに足した） | 無し | データ | — |
| `plugins/ndf/contracts/schemas/io/cross-review.result.schema.json` | 変更 | `items` に `disposition` を足す | 無し | スキーマ | — |
| `plugins/ndf/contracts/schemas/io/cross-review.args.schema.json` | 変更 | `--max-rounds` の既定が消える（生成物） | 無し | 生成物 | — |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | 同上（`ndf check --write-spec` の生成物。設計の触るファイルに足した） | 無し | 生成物 | — |
| `plugins/ndf/skills/cross-review/SKILL.md` | 変更 | 範囲・判定・上限の書き換え | 無し | 文書 | — |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` | 変更 | 同上 | 無し | 文書 | — |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 同上 | 無し | 文書 | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_scope.py` | 変更 | 範囲の起点と新しいファイル | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_leftover.py` | 新設 | 線引き・起票・redline の指摘 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_run.py` | 変更 | 通しの収束と上限の既定 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_finish.py` | 変更 | 欠けと要約 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_report.py` | 変更 | 表と一覧 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_prior.py` | 変更 | 起票済みの引き継ぎ（設計の触るファイルに足した） | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/conftest.py` | 変更 | PR へ書く偽物に起票先の偽物を足す（設計の触るファイルに足した） | — | テスト | — |

## review-scope の判断の分離（local_scope）

満たす決定: 決定 1、受け入れ条件 1

`cli.py` は 848 行で、この課題と #2468 が足す。このモジュールは 2 段階で作る。

1. 移動（実装計画の Task 1）: `review-scope` の範囲の判断（`_LocalBase`・`_local_base`・`_local_mvv`・`_local_sections`・`_local_scope`）を、振る舞いを変えずに移す。`_local_scope` はほかのモジュールから呼ぶので `decide` に名前を変える。残りの名前は変えない
2. 起点の変更（Task 5）: 移した `decide` に、下の実装の PR の起点の選び方を入れる

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decide` | `local_scope.py` | 変更 | `decide(wt: Path, base: str, every_section: bool, data: Mapping[str, Any], resolved: ScopeSettings) -> tuple[scope_decide.Scope, list[str]]` | 次のラウンドの範囲と注意を、手元のオブジェクトと状態の記録から決める |

実装の PR では、起点を `round_scope.read_origin`（下の「範囲の起点」）で選ぶ。起点と比較基点が違えば `changes.new_in_pr` を手元のオブジェクトで呼び、比べられなければ全差分にする。設計の PR は今のとおり `scope_decide.read_by_all_head` を使う。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `decide` | 比べるコミットや blob が手元に無い | 部分クローン・浅い履歴 | `git.GitError` | 既定値で続ける（全差分） | 今の `warnings` と同じ形で理由を 1 行 | 読むだけなので打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide` | 移動の後も今の `review-scope` のテストが通る | 実装の PR で比較基点だけが違えば `since` | 比べられなければ `full` と注意 |

## 範囲の起点（scope_decide・round_scope）

満たす決定: 決定 1・2、受け入れ条件 1・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `origin_record` | `scope_decide.py` | 新設 | `origin_record(state: Mapping[str, Any], basis: Basis) -> Optional[Mapping[str, Any]]` | 相手役の全員が読んだ最後のラウンドの記録を返す。今回の席と読んだ PR だけを照らし、比較基点・重点・単位の指示・MVV は見ない |
| `read_origin` | `round_scope.py` | 新設 | `read_origin(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[changes.Range]` | `origin_record` の head が `head` の祖先なら、その比較基点と head を返す |
| `line_origin` | `round_scope.py` | 新設 | `line_origin(g: git.Git, state: Mapping[str, Any], head: str) -> Optional[changes.Range]` | 線引きの起点（担当した相手役が全員結果を返した最後のラウンドの、比較基点と head）を返す |

`line_origin` が探すのは、そのラウンドを担当した相手役が全員結果を返した（`incomplete_reviewers` で終わっていない）最後のラウンドである。そのラウンドを読んだ顔ぶれが今回の相手役と同じか、読んだ PR が今回と同じかは問わない。`origin_record` に条件の無い `Basis()` を渡すと、この 2 つの照合をしない。

`line_origin` を `read_origin` と分けるのは、設計の決定 2 が「範囲が全差分に戻ったラウンドでも、前に全員が読んだ head があれば線引きする」と定めるためである。読んでいない相手役がいて全差分になったラウンドでも、線引きの起点は残る。

- `_reusable`（前回の収束をそのまま返すかの判定）と `read_by_all_head` は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_origin`・`line_origin` | head が祖先でないか、コミットが手元に無い | PR の作り直し | `round_scope._reachable_from` | 既定値で続ける（None。全差分・線引きなし） | 無し | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `origin_record` | `scope_decide.read_by_all_head`・`_seated_read` | `grep -rn "def .*origin\b\|def .*previous_head\|def read_by_all" --include=*.py src/ndf` | 席の照らし合わせは `_seated_read` を使う。`read_by_all_head` は収束の使い回し用の条件のまま残す |
| `read_origin` | `round_scope.previous_head`・`_reachable_from` | 同上 | 祖先の確かめは `_reachable_from` を使う |
| `line_origin` | 同上 | 同上 | `read_origin` と同じ中身を、条件の無い `Basis()` で呼ぶ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `origin_record` | 比較基点・重点・単位の指示・MVV・根拠の確認が違っても記録を返す | 一部が読めなかったラウンドを飛ばす | 読んでいない相手役・PR の違い・ラウンドが無いときは None |
| `read_origin`・`line_origin` | 祖先の head を返す | 読んでいない相手役がいるとき、`read_origin` は None で `line_origin` は値 | 祖先でない・コミットが無いときは None |

## 新しく PR の変更になったファイルと変えた行（changes）

満たす決定: 決定 1・2、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `new_in_pr` | `changes.py` | 新設 | `new_in_pr(g: git.Git, previous: Range, current: Range, listed: Listings) -> Optional[tuple[str, ...]]` | 比較基点が変わったことで、既読の差分にも今回渡す差分にも無い変更を持つファイルを返す。比べられなければ None |
| `since_diff` | `changes.py` | 新設 | `since_diff(g: git.Git, previous: Range, current: Range, added: Sequence[str], listed: Listings) -> str` | 前の head からの差分（比較基点が違えば PR が変えるファイルに絞る）に、`added` の今の PR の差分を続けて返す |
| `since_files` | `changes.py` | 新設 | `since_files(g: git.Git, previous: Range, current: Range, added: Sequence[str], listed: Listings) -> tuple[str, ...]` | `since_diff` が渡すファイルのパスを返す。足して渡す PR の差分に改名があれば、`since_diff` と同じく、改名前と改名後の両方のパスを返す。PR に既にあるインラインのコメントを選ぶ `reviewing.remarks` が使う |
| `touched_since` | `changes.py` | 新設 | `touched_since(g: git.Git, since: str, head: str, listed: Listings) -> leftover_decide.Touched` | `since` の後に変わったファイルと、足した行・書き換えた行の番号を返す。`since` に渡すのは線引きの起点（`line_origin` の head）で、差分を渡す起点（`read_origin`）ではない |

- `new_in_pr`
  - 事前条件: `previous.head` は `current.head` の祖先
  - `listed`: 呼び手が空の辞書を作って渡す。読んだ一覧が入るので、同じ辞書を `since_diff` へ渡す
  - 副作用: `git diff` と `git diff --raw` を打つ（読むだけ）
- `since_diff`
  - 比較基点が前のラウンドと違う: 先に `new_in_pr` を呼び、その返りを `added` に、同じ辞書を `listed` に渡す。返りが None のときは全差分にするので、`since_diff` は呼ばない
  - 比較基点が前のラウンドと同じ: `new_in_pr` は呼ばない。`added=()` と空の辞書の `listed` を渡す。前の head からの差分に出るファイルはどれも PR の変更なので、一覧を読まず、絞らない
  - 事後条件: 比較基点が違い、絞るファイル（PR が前のラウンドの時点か今に変えるファイル）が 1 つも無ければ、空文字列を返す。`added` は今の PR が変えるファイルの一部なので、このとき足す分も無い。パスの指定の無い `git diff` は全差分になるため打たない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `new_in_pr` | 比べられない | 前の比較基点か前の head のコミットが手元に無い | `git.GitError` | 呼び出し元へ返す（None。呼び手は全差分にする） | 呼び手の注意 | 打ち直せる |
| `since_diff` | 差分を読めない | 同上 | `git.GitError` | 呼び出し元へ返す（例外。今の `precondition`） | `git の操作が失敗した` で終了コードは前提の欠け | 打ち直せる |
| `touched_since` | 差分を読めない | 同上 | `git.GitError` | 呼び出し元へ返す。呼び手は線引きをしない | 注意を 1 行 | 打ち直せる |

### 処理の手順

`new_in_pr` は 3 つの差分を読む。既読の差分 `previous.base...previous.head`、今の PR の差分 `current.base...current.head`、今回渡す差分 `previous.head..current.head` である。

今の PR の差分にある変更を 1 つずつ確かめ、どれか 1 つでも「読んだか、今回読む」に当たらないファイルを返す。

同じソースの行でも、どの head の内容かで行番号がずれる。比べる位置は次の 2 つの番号にそろえる。今回渡す差分との比較は今の head の行番号で行う。既読の差分との比較は、その番号を今回渡す差分の対応（変えずに残した行の前後の番号の対応）で前の head の行番号に直して行う。

| 確かめる対象 | 位置の取り方 | 読んだか今回読むに当たる条件 |
| --- | --- | --- |
| 今の PR の差分が足したソースの行 | その行の、今の head での行番号 | 今回渡す差分が同じ位置で同じ内容を足している。または、前の head の番号に直した位置で、既読の差分が同じ内容を足している |
| 今の PR の差分が消したソースの行 | 消したソースの行は今の head に無いので、消した箇所のすぐ後ろに残る行の、今の head での行番号 | 区間内の既読の削除と今回の削除を、下の手順で 1 つの列に並べる。今の PR の差分の削除がすべて、その列に同じ順で現れる |
| 変更の種別・リネームの元と先・ファイルのモード | 行を使わない | 既読の差分か今回渡す差分の `git diff --raw` に同じ値がある |
| ソースの行で比べられないファイル（バイナリなど） | 行を使わない | 今の PR の差分の変更前の blob が、既読の差分の変更前の blob か、今回渡す差分の変更前の blob（今回渡す差分が中身を変える場合）と同じ |

行の内容は、末尾の改行の有無も含めて比べる。改行の無い `x` と改行のある `x` は別の行である。

バイナリは差分に中身が出ない。相手役が確かめる材料は、差分のファイルごとの見出しにある変更前後の blob ID（`index` の行）である。相手役は作業場所のリポジトリから、その ID の中身を読める。`--binary` を付けて符号化した中身を渡す案は、相手役が読めず token が増えるので採らない。今の PR の差分の変更後の blob は今の head の内容なので、今回渡す差分か既読の差分に必ず現れる。そこで照らすのは変更前の blob である。例えば今の PR が `B` → `C`、今回渡す差分が `A` → `C` で、既読の差分の変更前も `B` でなければ、`B` はどこにも渡っていないので、今の PR の差分を足して渡す。

通常のファイルとシンボリックリンクを入れ替えると、差分は削除と追加の 2 つの hunk になる。間に残る行が無いので、1 つの変更のまとまりとして照らす。

足した行の例: 既読の差分が、前の head の 10 行目に `x = 1` を足していた。今回渡す差分は、その上に 2 行を足した。今の PR の差分は `x = 1` を今の head の 12 行目に足している。12 は前の head の番号に直すと 10 になり、既読の差分と一致するので、読んだ行である。

消した行の例: 既読の差分が `y = 0` を消し、すぐ後ろに残る行は前の head の 20 行目だった。今回渡す差分は、その上に 2 行を足した。今の PR の差分では、`y = 0` を消した箇所のすぐ後ろの行は今の head の 22 行目になる。22 は 20 に直るので一致する。

最後の表の行は、設計の「範囲の起点の条件」の手順 3 を、当たる側から書いたものである。今回渡す差分がファイルの中身を変えていれば、このラウンドで読む。今回渡す差分にファイルが無いときと、実行権限や名前だけを変えるときは、中身はこのラウンドで渡らない。この場合は、変更前後の blob が既読の差分と同じことを求める。ファイルの名前があるだけでは、中身が後で変わったかが分からないためである。今回渡す差分が名前を変えたファイルは、元の名前で既読の差分を引く。

足したソースの行は、番号を直せない（位置の行が今回渡す差分で足された行である）場合、既読の差分とは一致しないとみなす。

消したソースの行は、1 つの行番号ではなく区間で比べる。区間は前の head の行番号で定め、直前の対応行の次の行から、直後の対応行までである。対応行とは、今回渡す差分が変えずに残した行である。直前の対応行が無ければ 1 行目から、直後の対応行が無ければ末尾の次の行までとする。消した行の位置はすぐ後ろの行の番号で表すので、直後の対応行の番号も区間に含む。

区間の中では順序も比べる。まず、区間の削除を前の head の行の順に並べる。行番号ごとに、既読の差分がその行の直前で消した行を置き、続けて、今回渡す差分がその行を消していれば置く。今の PR の差分がこの区間で消した行が、すべてこの並びに同じ順序で現れれば、読んだか今回読む行とする。1 つでも現れなければ当たらないとする。並びのうち今回渡す差分の削除に当てるには、内容に加えて、今の head 上の位置（すぐ後ろに残る行の番号）も同じであることを求める。既読の差分の削除は、区間の中なら位置を問わない。消したファイルには残る行が無いので、順序は比べず、本文と件数だけを比べる。

区間で比べるのは、読んだ削除の跡に行を足したり、読んだ置き換えを書き直したりしただけで、そのファイルをもう一度渡さないためである。順序を比べるのは、ベースが行を移して生じた削除を、移す前の位置の削除と取り違えないためである。例えば、並びが「`X`（読んだ削除）、`A`（今回消す行）」の区間で、今の PR の差分が `A`、`X` の順に消していれば、`X` は読んでいない。

今回渡す差分の削除に位置も求めるのは、同じ区間の別の位置で同じ内容を消した変更が、相手役に渡らないためである。例えば、前の head が `P / A / Z`、今の head が `P / R1 / R2 / Z`、今のベースが `P / R1 / A / R2 / Z`（`/` は改行）とする。今回渡す差分は `R1` の前の `A` を消し、今の PR の差分は `R2` の前の `A` を消す。内容は同じでも位置が違うので、当たらないとする。

行番号の対応はファイルごとに 1 回だけ索引にする。

#### git を起動する回数

1 つの差分は、一覧（`git diff --raw --numstat -z`）と行の差分（`git diff`）の 2 回の起動で読む。一覧はパス・モード・blob ID・行で比べられるかを、区切りのあいまいでない形で返す。行の差分はそれを返さない。

| ラウンド | 呼ぶ関数 | 起動の回数 |
| --- | --- | --- |
| 比較基点が変わった | `new_in_pr`（3 つの差分）と `since_diff` | `new_in_pr` が 6 回。`since_diff` は行の差分だけで 1 回、足すファイルがあれば 2 回 |
| 比較基点が同じ | `since_diff` だけ | 行の差分 1 回 |

同じ範囲の一覧は、`listed` で 2 つの関数が共有する。`listed` は git が返した一覧を 1 ラウンドの間だけメモリに持つ写しで、共有しない場合より起動が 2 回少ない。

省ける 2 回の起動（既読の差分と今の PR の差分の一覧）の時間は、このリポジトリで次のとおり測った。条件は git 2.53.0、x86_64 の 8 CPU、2 回の一覧の読み取りを 21 回繰り返した値である。どちらの測定も、前の head は `2c508370b`、今の head は `c5ba10c90` である。

| 比較基点 | 変わったファイル | 省ける時間の中央値（最小〜最大） |
| --- | --- | --- |
| `7e7fcce8f`（PR 1 件の大きさの差分） | 17 | 23 ミリ秒（19〜28） |
| `f5587e8e2`（大きい差分） | 1842 | 1.5 秒（1.3〜1.7） |

PR 1 件の大きさでは短縮はわずかで、効くのは変わったファイルが多い PR である。写しはラウンドの中で作って捨て、コミットの範囲が同じなら一覧は変わらないので、古い値を読むことは無い。そのため共有する。

ほかの起動を分ける理由と、採らなかった案は次のとおりである。どれもローカルの git の起動で、token・API・CI は増えない。

| 分けているもの | 理由 | 採らなかった案 |
| --- | --- | --- |
| 一覧と行の差分 | NUL 区切りの一覧と行の差分を 1 つの出力にすると、両方を切り分ける読み取りを新しく作ることになる | `git diff --raw --numstat -p -z` で 1 回にまとめる（`new_in_pr` の起動が 3 回減る） |
| `since_diff` の行の差分 | 比較基点が違うときに渡す差分は PR が変えるファイルに絞る。`new_in_pr` が読んだ差分は絞っていない。比較基点が同じラウンドは `new_in_pr` を呼ばない | `new_in_pr` が読んだ差分の文字列を、ファイルごとに切り分けて組み立てる（起動が 1〜2 回減る） |

`touched_since` の「変えた行」は、`since..head` の差分で足した行と書き換えた行の番号である。行を消しただけの箇所は、前後の行の番号を入れる。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Range(base: str, head: str)` | 型 | `changes.py` | 無し |
| `Touched(files: frozenset[str], lines: Mapping[str, frozenset[int]])` | 型 | `leftover_decide.py` | 空 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `new_in_pr` | `changes._diff_files`・`_base_lines`・`review_diff` | `grep -rn "def .*new_files\|def .*touched\|def .*changed_lines\|def _base_lines\|--raw" --include=*.py src/ndf` | 差分の読み取りは `_diff_files` と `review_diff` の中身を使う。行の対応は、`_base_lines` が呼ぶたびに hunk を先頭からたどるので、1 回だけ作る索引を別に持つ。`git.Git.changed_lines` は行数の和を返すもので使えない |
| `since_diff` | `changes.review_diff`・`git.Git.changed_files` | 同上 | 同じ差分の引数（`_DIFF`）に `--literal-pathspecs` とパスの指定を足して作る |
| `touched_since` | `crossrefactor.scope.touched`（記号の単位） | 同上 | 返す単位が違うので使えない。`_diff_files` の行を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `new_in_pr` | 分岐したブランチへ比較先を変えると、新しく PR の変更になったファイルが返る。取り込みの競合を解消して前の head の内容に戻したファイルも返る | 競合の無いベースの取り込みは空。head が同じまま比較先を変えて実行権限の変更が現れる。同じ文の追加が同じファイルの別の関数にある。比較先を変えて差分から消えただけのファイルは返らない。末尾の改行の有無だけが違う行は別の行にし、種別の変更の削除と追加は 1 つにまとめる。バイナリは変更前の blob が渡っていなければ返す（この 3 つは差分を入力にして確かめる）。消した行は、今回渡す差分と同じ内容でも位置が違えば未読にする（上の `R1`・`R2` の例。差分を入力にして確かめ、git を起動しない） | コミットが無ければ None |
| `since_diff` | PR に関係の無いベースの変更を含まない。`added` のファイルの今の差分を含む | 比較基点が違い、絞るファイルが無ければ空文字列 | コミットが手元に無ければ `git.GitError` |
| `touched_since` | 足したソースの行と書き換えたソースの行 | 行を消しただけの箇所は前後の行。バイナリはファイルだけ | コミットが手元に無ければ `git.GitError` |

## 線引きと残し方（leftover_decide）

満たす決定: 決定 2・3・4・5、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `normalized` | `leftover_decide.py` | 新設 | `normalized(text: str) -> str` | 空白をまとめ大文字小文字をそろえた本文の全体を返す（#2468 も同じ指摘の判定に使う） |
| `proceeds` | `leftover_decide.py` | 新設 | `proceeds(row: Mapping[str, Any], touched: Touched, *, returned: bool = False, unconfirmed: bool = False) -> bool` | 指摘を直さずに残さないか（真なら `parked` を付けない） |
| `finding_kinds` | `leftover_decide.py` | 新設 | `finding_kinds(row: Mapping[str, Any]) -> frozenset[str]` | 指摘の種類（行の `protect` と `check.tracks` の和） |
| `files_issue` | `leftover_decide.py` | 新設 | `files_issue(row: Mapping[str, Any]) -> bool` | 重い指摘（critical・major）が起票してよい種類を持つか |
| `identity` | `leftover_decide.py` | 新設 | `identity(origin_pr: int, text: str) -> str` | 元の PR の番号と `normalized(text)` の SHA-256 の先頭 12 字 |
| `issue_parts` | `leftover_decide.py` | 新設 | `issue_parts(text: str, impact: str, place: str, pr_urls: Sequence[str], ident: str) -> tuple[str, str]` | 伏せ終えた値から起票の題と本文を組む |
| `trusted` | `leftover_decide.py` | 新設 | `trusted(author: str, association: str, me: str, logins: Collection[str]) -> bool` | 起票の記録と Issue の作者を信用してよいか |
| `matches` | `leftover_decide.py` | 新設 | `matches(listed: Listed, ident: str, pr_url: str, text: str) -> bool` | Issue がこの指摘を扱うものか（設計の「起票の形」の条件） |
| `record_entries` | `leftover_decide.py` | 新設 | `record_entries(body: str) -> dict[str, dict[str, Any]]` | 起票の記録の本文から、識別値ごとの `at` と `url`（どちらも無いことがある）を読む |
| `record_body` | `leftover_decide.py` | 新設 | `record_body(entries: Mapping[str, Mapping[str, Any]]) -> str` | 起票の記録の本文を組む（マーカー `<!-- ndf:cross-review-leftover-record -->` で始める） |
| `result_fields` | `leftover_decide.py` | 新設 | `result_fields(row: Mapping[str, Any], comment_url: Optional[str]) -> dict[str, str]` | 結果の `items` に足す `disposition` と `url`（残さない行は空） |
| `summary_line` | `leftover_decide.py` | 新設 | `summary_line(rows: Sequence[Mapping[str, Any]]) -> Optional[str]` | 「残した指摘 N（起票 M）」。残した指摘が無ければ None |
| `decisions` | `leftover_decide.py` | 新設 | `decisions(rows: Sequence[Mapping[str, Any]], comment_url: Optional[str]) -> list[dict[str, str]]` | MVV に反する残した指摘を、番号・MVV の項目・URL の判断にする |

- `issue_parts`
  - 事前条件: `text`・`impact`・`place` は、**全文に伏せ字（秘密を伏せる処理）を当てた後の値**である。関数は切り出すだけで伏せない

題を伏せる前の本文から切り出すと、途中で切れた秘密は秘密の形に当たらなくなり、伏せられないまま公開されうる。同じ順の前例は、既存のコメントを字数で切る前に伏せる `drive` の `_remarks`（#4363 の後は `reviewing.remarks`）にある。

### 処理の手順

`proceeds` は次の表を上から当てる。指摘の行の `severity` は、minor へ下げた後の値である。

| 順 | 条件 | 返り |
| --- | --- | --- |
| 1 | 行が `redline` を持つ（必ず人の承認が要る操作） | 進む（`parked` を付けない。今の扱いで人の判断を待つ） |
| 2 | `by` が NDF | 進む |
| 3 | `round` が 0（前の実行から引き継いだ） | 進む |
| 4 | `returned`（直した後にもう一度出た） | 進む |
| 5 | critical、または major でない（minor・nit は `parked` の対象にしない。収束を止める重さでないので、真でも修正担当へは渡らない） | 進む |
| 6 | `path` と `line` があり、`touched.lines[path]` に `line` がある | 進む |
| 7 | 行の `protect` か `check.tracks` に保護の種類がある | 進む |
| 8 | 指摘の `line` が無く、かつ（`path` が無い、または `path` が `touched.files` にある） | 進む |
| 9 | `unconfirmed`（判定を依頼して確認できなかった） | 進む |
| 10 | ほか | 残す |

`path` は先頭の `./` を除いて照らす。順 1 を先頭に置くので、前の確認で `check.tracks` が付いた指摘や、直さずに残した指摘に後から承認の要る操作が指摘された場合も、必ず進む側になる。

`files_issue` は `severity` が critical か major で、`finding_kinds` が `review-criteria.json` の `kinds.filed`（`delivered`・`safety`・`secret`・`user_data`・`irreversible`・`approval`）と重なる場合だけ真である。この課題の線引きで残るのは major だけだが（critical は `proceeds` の順 5 で進む）、#2468 は上限で残った critical も同じ関数で起票するかを決めるためである。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `TITLE_CHARS` | 定数 | `leftover_decide.py` | 80（題にする本文の先頭の字数） |
| `Listed(url, body, author, association, created_at, is_pull)` | 型 | `leftover_decide.py` | 無し |
| `kinds.filed` | 定数 | `contracts/data/review-criteria.json` | 上の 6 つ |
| `PARKED = "parked"`・`ISSUE = "issue"`・`ISSUE_ERROR = "issue_error"` | 行のキー | `leftover_decide.py` | 無し |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `normalized` | `oscillation.finding_key` | `grep -rn "def finding_key" src/ndf` | `finding_key` は先頭だけを返すので使えない。そろえ方（空白と大文字小文字）は同じにする |
| `proceeds`・`finding_kinds`・`files_issue` | `journal._blocking`・`criteria_decide.protect_kinds` | `journal.py`・`criteria_decide.py` を読んだ | 種類の一覧は `criteria_decide` を使う。線引きの関数は無い |
| `identity` | `journal.citation_key`・`scope_decide.unit_id`（どちらも SHA-256） | `grep -rn "sha256(" --include=*.py src/ndf/domains/crossreview` | 入力と切り出す長さが違うので新しく書く |
| `issue_parts` | `publish.Publisher.create_issue`・`backlog.issues_gh.create_issue` | `grep -rn "def create_issue" src/ndf` | 作成は `Publisher.create_issue` を使い、文の組み立てだけを新しく書く |
| `trusted` | `github.GitHub.trusts` | `grep -rn "author_association\|authorAssociation" --include=*.py src/ndf` | `trusts` は write 権限まで読み、設計の条件（所有者・メンバー・共同作業者・実行の login・`issue_logins`）と違うので使わない |
| `matches`・`record_entries`・`record_body` | `report_decide.open_block`・`open_items` | `report_decide.py` を読んだ | 記録のコメントの一覧とは書き先と中身が違う。HTML のコメントに JSON を埋める形はそろえる |
| `result_fields`・`summary_line`・`decisions` | `finish_decide._counts`・`_limit_decision` | `finish_decide.py` を読んだ | 残した指摘の数え方は無い。`decisions` の形（`decision`・`basis`）は同じにする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `proceeds` | critical・変えたソースの行への major・保護の対象（人の安全を含む）の major は真。変えていないソースの行への major は偽 | `line` が無くパスのファイルが変わった指摘・`path` も無い指摘・minor（真だが修正担当へは渡らない）・`round` 0・`returned`・`unconfirmed`・`./` 付きのパス | **`redline` を持つ指摘は、変えていないソースの行への major で起票する種類（`check.tracks`）を持っていても、`parked` が付いていても真** |
| `files_issue` | `delivered` を持つ major は真。`user_data` を持つ critical も真 | `authz` だけは偽。`authz` と `delivered` は真 | minor は偽 |
| `identity` | 同じ本文は同じ値 | 空白と大文字小文字の違いは同じ値。先頭が同じで続きが違えば違う値 | 無し |
| `issue_parts` | 題・本文・目印の行が最後 | **題の切り出しの長さの境目をまたぐ偽の秘密を、伏せた後の値で渡すと、題にも本文にも断片が無い**（テストは偽の秘密を実行時に連結して作る。秘密の検査 `check-repo-secrets.py` に当たらないようにする） | 本文が空の 1 文・場所 |
| `matches`・`trusted` | 条件をすべて満たす Issue は真 | login 自身・`issue_logins` の bot | 引用の中の目印・目印が最後でない・Pull Request・第三者 |
| `record_entries`・`record_body` | 書いた形を読み戻せる | 空の記録 | 壊れた本文は空 |

## 起票（leftover）

満たす決定: 決定 5、受け入れ条件 2（不変条件 I3）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `primary_of` | `leftover.py` | 新設 | `primary_of(main: Optional[Path]) -> tuple[Optional[str], Optional[str]]` | メインディレクトリの `.ndf/project.json` の `issues.primary` と、注意（未設定のとき） |
| `file_issues` | `leftover.py` | 新設 | `file_issues(data: dict[str, Any], ref: store.StateRef, port: Port, save: Callable[[], None], primary: Optional[str], *, manual: bool = False) -> list[str]` | 起票の候補を設計の「起票の形」の順で扱い、行の `issue`・`issue_error` と状態の `issue_logins`・`issue_record_id`・`issue_list_full` を書いて注意を返す |

`Port`（テストは偽物）は次のメソッドを持ち、`GitHubIssues` が GitHub の REST で実装する。

| メソッド | 返り | 使う API |
| --- | --- | --- |
| `viewer()` | login | `threads.GitHubPort.viewer`（1 回だけ読む） |
| `pr_url(pr)` | PR の URL | 呼ばない（リポジトリ名から組む） |
| `redact(*texts)` | 伏せた値の並び | `publish.Publisher.guarded` |
| `comments(pr)`・`comment(id)` | コメント（番号・本文・投稿者・更新時刻） | issue comments の GET |
| `post(pr, body)`・`edit(id, body)` | 番号と応答の更新時刻 | POST・PATCH（`threads.patient` を通す） |
| `search(ident)` | `Listed` の並び | `GET search/issues` |
| `recent(since)` | 作成時刻が `since` 以後（同じ時刻を含む）の `Listed` の並び（超えれば `TooMany`） | `GET repos/{repo}/issues?sort=created`。上限は `github.list-pages` |
| `create(title, body)` | Issue の URL | `publish.Publisher.create_issue` |

- `file_issues`
  - 事前条件: 呼び手は、記録の公開のロック（`write_gate.pr_lock` の今の PR 番号）に加えて、元の PR 番号のロックを持つ。2 つの番号が同じなら 1 回だけ取る（同じ番号を 2 回取ると、2 回目が待ち切れで失敗する）。順は今の番号が先である
  - 事後条件: 起票の候補の行は `issue` か `issue_error` を持つ。Issue を作る前に `issue_logins` と起票の記録を保存している
  - 副作用: 元の PR の起票の記録のコメントと、Issue を GitHub に書く。状態ファイルを `save` で書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `primary_of` | 設定を読めない・未設定・`unknown` | ファイルが無い・壊れている | JSON の読み取り | 既定値で続ける（起票しない） | `github` を設定するよう注意 | 設定すれば次の公開で起票する |
| `file_issues` | 起票の記録を読めない・書けない | 通信・権限・二次レート制限の上限 | `GitHubError` | 止めない。どの候補も起票せず `issue_error` | 注意と記録のコメントの表。終了コードは変えない | 次の起動か `ndf cross-review report` で打ち直す |
| `file_issues` | 検索か一覧を読めない・無いと確かめられない | 検索の上限・`TooMany` | `GitHubError`・`TooMany` | その候補を起票せず `issue_error`。`TooMany` なら `issue_list_full` に上限の値 | 同上 | `issue_list_full` がある間、自動の公開は一覧を読まない。`github.list-pages` を上げるか `report` で打ち直す |
| `file_issues` | GitHub が作成をはっきり拒む | 4xx | `GitHubError.status` | 起票の記録からその識別値を消し、`issue_error` | 同上 | 打ち直せる |
| `file_issues` | 作成の結果が分からない（5xx・不達・保存の前の中断） | 通信の障害 | 次の起動で記録に URL が無い識別値 | 起票の記録を残し、次の公開で検索と一覧で確かめる | 同上 | 打ち直しても 2 件作らない |

### 処理の手順

`file_issues` は、設計の「起票の形」の順で進む。指摘の行に `issue` があるものは起票しない。起票の記録は元の PR の専用のコメントで、本文に識別値ごとの `at`（試みた時刻）と `url` を持つ。コメントの番号は状態ファイルの `issue_record_id` に置く。1 回の公開で記録を書くのは、作成の前の 1 回と結果の 1 回である。

1. 記録を読む（本文とコメントの更新時刻）。`url` も `at` も無い識別値には、読んだ更新時刻を `at` として当てる
2. `url` のある識別値は起票せず、その URL を使う
3. `url` の無い識別値（作成の結果が分からないもの）は、識別値で検索し、無ければ作成時刻が `at` 以後の Issue の一覧を読む
4. 1 回目の書き込み: 記録に無い識別値と、順 3 でどちらにも無いと確認できた識別値を、`at` と `url` の無い項目として書く。試みた時刻はこの書き込みの応答の更新時刻なので、この本文には入れられない。書けなければ作らない
5. 順 4 で書いた識別値の Issue を作る
6. 2 回目の書き込み: 作れた識別値は `url` と `at` を、結果が分からない識別値は `at` を書き、GitHub がはっきり拒んだ（4xx）識別値は消す。作れた URL は指摘の行にも書く

`url` も `at` も無い項目が次の起動に残るのは、順 4 の後、順 6 の前に中断したか、順 6 の書き込みに失敗した場合である。このときコメントの更新時刻は順 4 の応答の値のままなので、順 1 がそれを試みた時刻にする。記録を書く処理は書く前に必ず読んで `at` を当てるので、後の書き込みで時刻が動いても、先の項目の時刻は失われない。人がコメントを編集して更新時刻が後ろへ動いた場合の扱いは、設計書が定めていない。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ PR の別の公開（別の保存先と、巻き直しの前後を含む） | 起票の記録のコメント | 起票の記録を置く元の PR 番号の公開のロック | 作成の前に記録へ書くので、中断しても候補が分かる |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `file_issues` | 起票した URL・失敗の理由 | 行の `issue`・`issue_error`、結果の `warnings` | 伏せる前の指摘の本文。起票の記録には識別値・時刻・URL だけを書く |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `primary_of` | `settings.settings_path(main, "project")` | `grep -rn "issues.*primary\|\[\"primary\"\]" --include=*.py src/ndf` | パスは `settings_path` を使う。`issues.primary` を起票の判断に読む関数は無い |
| `file_issues` | `threads.sync`・`threads.patient`・`publish.Publisher`・`write_gate.pr_lock` | `grep -rn "def create_issue\|search/issues\|sort=created" --include=*.py src/ndf` | 打ち直しと伏せ字とロックは既存を使う。起票の記録と重複の確かめは無いので新しく書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `file_issues` | 起票して URL を行に書き、同じ指摘は 2 回目に起票しない | 作成を試みていない候補だけなら検索も一覧も呼ばない。`issue_logins` の bot が書いた記録を別の login で読む。読み済みの login を取り直さない | 作成の失敗で行が残る。作成の後の保存の失敗の打ち直しで、同じ保存先・別の保存先・PR を巻き直した後でも起票しない。記録を書けなければ作成しない。検索に反映される前は一覧で見つける。作成時刻が `at` と同じ Issue も一覧で見つける。**題の境目をまたぐ偽の秘密を持つ指摘を起票すると、作った Issue の題にも本文にも断片が無い** |
| `primary_of` | `primary` が `github` なら `("github", None)` を返し、起票する | `primary` が `markdown` で `others` に `github` がある場合は `("markdown", None)` を返し、起票しない | ファイルが無い・`issues` が無いか null・`unknown`・壊れた JSON は、値が None で注意の文を返し、起票しない |

## ジャーナルと引き継ぎ（journal・prior_decide）

満たす決定: 決定 2・4、受け入れ条件 2・5（不変条件 I1・I2）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `sort` | `journal.py` | 新設 | `sort(self, touched: leftover_decide.Touched, unconfirmed: Collection[str] = ()) -> frozenset[str]` | このラウンドで出た指摘のうち分け直すものに線引きを当て、残した指摘のキーを返す |
| `enter` | `journal.py` | 変更 | シグネチャは変えない | このラウンドで出た指摘の番号を `self.raised_ids` に残し、`self.returned`（直した後に戻った指摘の番号）を呼び出しごとに空にする |
| `carry` | `journal.py` | 変更 | シグネチャは変えない | 記録のコメントの `parked` の項目は指摘の行にせず、本文から URL への対応 `self.filed` に残す。`issue` を持つほかの項目は指摘の行へ移す |
| `_revive` | `journal.py` | 変更 | シグネチャは変えない | 直した（`fixed`）指摘が戻ったときに、番号を `self.returned` に足す |
| `handable` | `journal.py` | 変更 | シグネチャは変えない | `parked` の指摘を返さない |
| `_classify` | `journal.py` | 変更 | シグネチャは変えない | 必ず人の承認が要る操作と分かった指摘の `parked` を外す |
| `findings` | `journal.py` | 変更 | シグネチャは変えない | `Finding.parked` を渡す |
| `_recorded_one` | `prior_decide.py` | 変更 | シグネチャは変えない | `Carried` に `parked: bool` と `issue: Optional[str]` を読む |

`sort` が分け直すのは、このラウンドで出た指摘（`raised_ids`）のうち、新しく出たか戻った指摘と、`parked` を持つ指摘だけである。新しく出たか戻った指摘は、判定役へ渡す候補の `fresh` ではなく、絞る前の一覧を使う。内容の違う重い報告が重なって判定役へ渡さない新しい指摘も、線引きするためである。前から未解決で `parked` の無い指摘は、出し直されても分け直さない（I2: 一度修正へ進むと決めた指摘は、直すか見送るまで進む）。

`proceeds` に渡す位置は、今回の出し直しのものを 1 件ずつ当てる。直さずに残した指摘の行の位置は、前の head の行番号だからである。どれか 1 件が真なら進め、行の位置をその出し直しのものに置き換える（新しい指摘で、その出し直しに位置が無ければ、元の位置を残す）。どれも偽なら `parked` を付けて `self.filed` の URL を `issue` に戻す。

`parked` の指摘を修正担当へ渡さないのは `handable` である。重さの判定（`_blocking`）は変えない。`enter` が返すキーには残していた指摘も含め、駆動が `sort` の返りを除く。出し直しで進むことになった指摘のキーが、振動の判定の履歴から抜けないためである。

ただし、直さずに残した指摘が critical か保護の対象の指摘として出し直されたときは、`enter` が取り込む時点で `parked` を外し、行の位置をその出し直しのものに置き換える。この条件は今の差分と照らさなくても決まるので、`sort` を呼ばないラウンドでも修正担当へ渡すためである。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `sort` | `journal.handable`・`restate`、`leftover_decide.proceeds` | `grep -rn "parked\|def sort" src/ndf/domains/crossreview`（該当なし） | 行を残す・外す処理は無い。判断は `proceeds` を使い、`sort` は行への書き込みだけを持つ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sort` | 変えていないソースの行への major に `parked` が付き、`pending` に出ない | 直さずに残した指摘が critical か変えた行の major で出し直されると `parked` が外れる。直さずに残した指摘が出し直され、確認できなかった場合も `parked` が外れる。判定対象外の指摘は、回答が無いことを理由に `parked` を外さず、直さずに残したままにする。修正へ進むと決めた指摘は後のラウンドでも進む | **`redline` を持つ指摘は `parked` が付かない。直さずに残した指摘に後の出し直しで承認の要る操作が指摘されると `parked` が外れる。`check.tracks` の付いた指摘に後で承認の要る操作が指摘されても同じ** |
| `carry` | `parked` の項目は未解決の行にならず、同じ指摘が残ると URL が戻る | `issue` を持つ未解決の項目は引き継ぐ | 形の違う `parked`・`issue` は無視する |

## 終わり方・記録・スレッド（finish_decide・report_decide・threads）

満たす決定: 決定 4・5、受け入れ条件 2（不変条件 I1）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | シグネチャは変えない | `parked` の指摘を欠け（MVV の欠けを含む）にしない |
| `_counts` | `finish_decide.py` | 変更 | シグネチャは変えない | `parked` の指摘を未解決の数に入れない |
| `as_findings` | `finish_decide.py` | 変更 | シグネチャは変えない | 要約の指摘の `parked` を読む |
| `_finding_rows` | `report_decide.py` | 変更 | シグネチャは変えない | `parked` の行の終わり方に「直さずに残した（起票: <URL>）」か「直さずに残した（記録のみ）」を出す |
| `open_block` | `report_decide.py` | 変更 | シグネチャは変えない | 一覧の項目に `parked` と `issue` を足す |
| `viewer` | `threads.py` | 新設 | `GitHubPort.viewer(self) -> str` | 今の認証の login を 1 回だけ読んで返す |
| `_wanted` | `threads.py` | 変更 | シグネチャは変えない | スレッドの無い `parked` の指摘にスレッドを作らない |

`Finding` に `parked: bool = False` を足す。`summary_of` は `Finding` の全項目を書くので変えない。`--stop-on-hold` の欠けを作る `_held` も変えない。`parked` の指摘は未解決で `redline` でないので、今の条件で数えないためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `viewer` | login を読めない | 認証・通信 | `GitHubError` | 呼び出し元へ返す（起票は `issue_error`、スレッドは今の注意） | 注意 | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `viewer` | `GitHubPort._trusted` の中の `VIEWER` の照会 | `threads.py` を読んだ | 同じ照会を公開のメソッドに切り出し、`_trusted` もそれを呼ぶ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_finding_gaps` | 残した指摘だけなら欠けが無い | MVV の残した指摘は `outputs.mvv.findings` に未解決で載り、欠けにならない | 無し |
| `_finding_rows`・`open_block` | 残し方と URL が出る | 起票に失敗した行は記録のみ | 無し（文言は照合せず、URL と番号が現れることを見る） |
| `_wanted`・`viewer` | `parked` の指摘にスレッドを作らない | 2 回目は照会しない | 無し |

## 駆動とサブコマンド（drive・cli・settings）

満たす決定: 決定 1・2・3・6、受け入れ条件 1・2・3・4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_ranges` | `drive.py` | 新設 | `_ranges(run: _Run, merge_base: str, head: str) -> Ranges` | 目的ごとに、差分を渡す起点（`read_origin`）・足すファイル・線引きの起点（`line_origin`）を決める |
| `_sort_leftovers` | `drive.py` | 新設 | `_sort_leftovers(run: _Run, touched: Optional[leftover_decide.Touched], checked: Checked) -> frozenset[str]` | `_confirm_heavy` の前に読んだ変えた行を受け取り、線引きの対象のラウンドなら `journal.sort` に渡す |
| `default_max_rounds` | `settings.py` | 新設 | `default_max_rounds(every_section: bool) -> int` | `--max-rounds` を省いたときの上限（設計の PR は 3、ほかは 4） |
| `_with_leftovers` | `cli.py` | 新設 | `_with_leftovers(verdict: fin.Verdict, items: list[dict[str, Any]], data: Mapping[str, Any]) -> fin.Verdict` | 結果の `items` に残し方を足し、要約の行と MVV の判断を足す |
| `_prepare_round` | `drive.py` | 変更 | シグネチャは変えない（返りの `Prepared` に `lines: Optional[str]` と `listed: changes.Listings` を足す） | 実装の PR は起点からの差分を `since_diff` で作る。`lines` には線引きの起点（`line_origin` の head）を入れ、線引きをしないラウンドは None にする |
| `_publish` | `cli.py` | 変更 | `_publish(ref, final, warnings, hub, main: Optional[Path])` | スレッドの後、記録のコメントの前に `leftover.file_issues` を呼ぶ。元の PR 番号が今の番号と違えば、その呼び出しを元の番号の公開のロックで囲む |
| `_post_report` | `cli.py` | 変更 | `_post_report(state_dir, pr, final, warnings, hub_of, main: Optional[Path] = None)` | `main` を `_publish` へ渡す |
| `_report` | `cli.py` | 変更 | シグネチャは変えない | 起票に失敗した指摘があれば、前回の記録が同じでも書き直す |
| `_github_hub` | `cli.py` | 変更 | シグネチャは変えない | `Hub.leftovers` に、`leftover.GitHubIssues` を作る関数を入れる |
| `_run_config` | `cli.py` | 変更 | シグネチャは変えない | `--max-rounds` が無ければ `default_max_rounds` で上限を決める |
| `_run_parser` | `cli.py` | 変更 | シグネチャは変えない | `--max-rounds` の既定を None にする |
| `cmd_scope` | `cli.py` | 変更 | シグネチャは変えない | 範囲を `local_scope.decide` に決めさせる |
| `cmd_report` | `cli.py` | 変更 | シグネチャは変えない | 打った場所からメインディレクトリを決め、`manual=True` で起票する |
| `_round` | `drive.py` | 変更 | シグネチャは変えない | 判定役へ指摘の種類を求めるか（`use_tracks`）を `_confirm_heavy` へ渡し、`_sort_leftovers` を呼び、振動の判定の履歴から残した指摘を除く |

`Hub` に `leftovers: Optional[Callable[[int], leftover.Port]] = None` を足す。

- `_with_leftovers`
  - 事前条件: 記録の公開（`_report`）の後に呼ぶ。起票の URL は公開の中で行に書かれる

### 処理の手順

| 目的 | `_ranges` の結果 |
| --- | --- |
| 設計の PR（`every_section`） | 今のとおり `round_scope.previous_head`。線引きの起点は無し |
| 実装の PR で `read_origin` が無い | 全差分。線引きの起点は `line_origin`（あれば） |
| 実装の PR で `read_origin` があり、比較基点が同じ | 起点は `read_origin.head`、足すファイル無し |
| 実装の PR で比較基点が違う | `new_in_pr` の結果を足す。None なら全差分で線引きなし |

線引きをするのは、実装の PR で、状態ファイルの通算で 2 ラウンド目以降で、`line_origin` があり、比較基点の変化で足すファイルが無いラウンドである。足すファイルの有無は、`read_origin` が無くて全差分になるラウンドでも、`line_origin` の比較基点が今と違えば `new_in_pr` で確かめる。`_confirm_heavy` の `use_tracks` を真にするのは、線引きをするラウンドで、このラウンドの新しい重い指摘に、MVV の項目を持ち `proceeds` が偽のものがある場合だけである。その指摘は、回答の指摘の種類によって、進むか（保護の種類が付く）、起票するかが変わる。

保護の対象の指摘は `proceeds` の順 7 で進むと決まっていて、起票もしない。critical や変えた行の MVV の指摘も進むと決まっている。これらだけのラウンドでは、判定役を起動しない（ほかの理由で起動するラウンドでは、今のとおり一緒に渡る）。`proceeds` に渡す `touched` は、線引きをするラウンドで `_confirm_heavy` の前に `changes.touched_since(g, prep.lines, prep.head, listed)` で 1 回読み、`_sort_leftovers` にも渡す（線引きをしないラウンドは None）。`listed` は `prep.listed`（`_ranges` が読んだ差分の一覧。読んでいなければ空）で、一覧を読んだ範囲は読み直さない。

`run.history`（振動の判定に使う、ラウンドごとの指摘のキーの集合）へは、`enter` が返したキーから、下げた指摘（`Checked.lowered`）と残した指摘（`_sort_leftovers` の返り）を除いて足す。どちらも修正担当へ渡らないので、後でもう一度出ても、直しては戻す往復には当たらないためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_ranges` | 比べられない | コミットが手元に無い | `new_in_pr` が None | 既定値で続ける（全差分） | 注意を 1 行 | 打ち直せる |
| `_sort_leftovers` | 変えた行を読めない | `git.GitError` | `changes.touched_since` | 既定値で続ける（線引きなし。すべて進む） | 注意を 1 行 | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_ranges`・`_sort_leftovers` | `drive._prepare_round`・`round_scope.previous_head` | `drive.py` を読んだ | `_prepare_round` の中に書くと 60 行の上限（`NDF701`）を超えるので分ける |
| `default_max_rounds` | `settings.RunConfig` の `default_factory` | `grep -rn "max-rounds\|MAX_ROUNDS" src/ndf` | 今の読み口は 1 つの値だけなので、目的で選ぶ関数にする |
| `_with_leftovers` | `cli._emit_verdict` | `cli.py` を読んだ | 出力の前に足すだけの関数にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_ranges`（`run` を通す） | 比較基点・重点・指示・MVV・根拠の確認が違っても範囲は前の head から | 比較先を変えて足したラウンドは線引きしない。1 ラウンド目は全差分で線引きしない。設計の PR は今のまま | 読んでいない相手役・PR の違い・祖先でない head は全差分 |
| `_sort_leftovers`（`run` を通す） | 2 ラウンド目に変えていない行の major と minor だけなら収束し、起票の種類のある major は起票、ほかは記録の URL が結果に載る。変えていない行の、保護の対象の major だけのラウンドは、判定役を起動せずに修正へ進む | 起票の失敗の後の打ち直しで 1 回だけ起票する | 線引きの順で変えた行の判定を逆にすると落ちる |
| `default_max_rounds` | 省けば `--purpose design` は 3、`code` と省略時は 4 | `--max-rounds` を渡せばその値 | 無し |

## 手順書

満たす決定: 決定 1〜6、受け入れ条件 1・2・3

`skills/cross-review/` の `SKILL.md`・`docs/01-state-and-review.md`・`docs/02-fix-and-rotation.md` の「範囲」「判定」「ラウンドを足す」を、起点の条件・線引き・残し方・上限の既定に書き換える。文言を照合するテストは書かない。
