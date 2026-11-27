# #3780 の詳細設計: 2 ラウンド目以降は直した箇所を確かめて終わる

[設計文書](./issue-3780-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の理由・不変条件（I1〜I3）・起票の手順は設計文書を、受け入れ条件は [要求仕様](./issue-3780-requirements.md) を指す。

[#4363 の詳細設計](./issue-4363-design-detail.md) の後に実装する。そこで作る `reviewing.py`・`carry.py`・`verify_decide.select`・`journal.apply_checks`（返りの `Checked`）・`journal.fresh`・`drive._confirm`・`criteria_decide.protect_kinds` を前提にする。

| 語 | 意味 |
| --- | --- |
| 起点 | 今回の差分の始まりにする、前に全員が読んだラウンドの head |
| 線引き | 2 ラウンド目以降の指摘を、修正へ進めるものと直さずに残すものに分けること |
| 直さずに残す指摘 | 修正担当へ渡さず、次のラウンドを始める理由にも数えない指摘（行の `parked`） |
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

依存の向き: `cli` が `local_scope`・`leftover`・`drive` を読み、`drive` が `round_scope`・`changes`・`journal` を読み、`journal`・`finish_decide`・`report_decide`・`leftover` が `leftover_decide` を読み、`leftover_decide` は `criteria_decide` と `contracts` だけを読む。`changes` は変えた行の型を `leftover_decide` から受ける（判断のモジュールは外部との接続の層を読めないので、型を判断の側に置く）。`local_scope` は `cli` を読まず、設定の値を Protocol で受ける。輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/crossreview/local_scope.py` | 新設 | `review-scope` の範囲を手元の git と状態の記録から決める | `scope_decide`・`round_scope`・`changes` | モジュール、`ScopeSettings`（Protocol） | 変更（移動）: `decide`・`_local_base`・`_local_mvv`・`_local_sections` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover_decide.py` | 新設 | 指摘が進むか、残すなら起票か記録かを決め、起票の文を組む | `criteria_decide` | モジュール、`Touched`・`Listed` | 新設: `normalized`・`proceeds`・`kinds_of`・`files_issue`・`identity`・`issue_parts`・`trusted`・`matches`・`record_entries`・`record_body`・`result_fields`・`summary_line`・`decisions` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover.py` | 新設 | 残す指摘を起票し、URL を行に書く | `leftover_decide`・`publish`・`github` | `Port`（Protocol）、`GitHubIssues` | 新設: `primary_of`・`file_issues` |
| `plugins/ndf/src/ndf/domains/crossreview/scope_decide.py` | 変更 | 起点の条件と、収束を使い回す条件を分ける | `store` | モジュール | 新設: `origin_record` |
| `plugins/ndf/src/ndf/domains/crossreview/round_scope.py` | 変更 | 起点と線引きの起点を git で確かめる | `scope_decide`・`changes` | モジュール | 新設: `origin`・`line_origin` |
| `plugins/ndf/src/ndf/domains/crossreview/changes.py` | 変更 | 新しく PR の変更になったファイル・渡す差分・変えた行を読む | `git`・`leftover_decide` | モジュール、`Range` | 新設: `new_in_pr`・`since_diff`・`touched` |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | 変更 | 残す指摘に `parked` を付け、進む条件に当たれば外す | `leftover_decide` | `FindingJournal` | 新設: `sort` / 変更: `enter`・`_revive`・`_classify`・`carry`・`handable`・`findings` |
| `plugins/ndf/src/ndf/domains/crossreview/finish_decide.py` | 変更 | `parked` の指摘を欠けから除く | `leftover_decide` | `Finding` | 変更: `_finding_gaps`・`_counts`・`as_findings` |
| `plugins/ndf/src/ndf/domains/crossreview/report_decide.py` | 変更 | 表に残し方と URL を出し、一覧に起票済みを載せる | `leftover_decide` | モジュール | 変更: `_finding_rows`・`open_block` |
| `plugins/ndf/src/ndf/domains/crossreview/prior_decide.py` | 変更 | 記録のコメントの `parked` と `issue` を読む（設計の「データ構造」の引き継ぎの決まり。設計の触るファイルに足した） | `report_decide` | `Carried` | 変更: `_recorded_one` |
| `plugins/ndf/src/ndf/domains/crossreview/threads.py` | 変更 | `parked` の指摘をスレッドにせず、login を渡す | `report_decide` | `GitHubPort` | 新設: `GitHubPort.viewer` / 変更: `_wanted`・`_trusted` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更 | 範囲の起点を目的で分け、線引きを当てる | `round_scope`・`changes`・`journal` | `_Run` | 新設: `_ranges`・`_sort_leftovers` / 変更: `_prepare_round`・`_round` |
| `plugins/ndf/src/ndf/domains/crossreview/settings.py` | 変更 | 目的ごとの上限の既定を持つ | `limits` | `RunConfig` | 新設: `default_max_rounds` |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | 変更 | 起票を記録の公開に組み込み、結果に残し方を載せる | `leftover`・`local_scope` | `Hub` | 新設: `_with_leftovers` / 変更: `_github_hub`・`_post_report`・`_publish`・`_report`・`_run_config`・`cmd_scope`・`_run_parser` |
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

`cli.py` は 848 行で、この課題と #2468 が足す。先に `review-scope` の範囲の判断（`_LocalBase`・`_local_base`・`_local_mvv`・`_local_sections`・`_local_scope`）を振る舞いを変えずに移す。この課題の範囲の変更はこのモジュールに入る。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decide` | `local_scope.py` | 変更 | `decide(wt: Path, base: str, every_section: bool, data: Mapping[str, Any], resolved: ScopeSettings) -> tuple[scope_decide.Scope, list[str]]` | 次のラウンドの範囲と注意を、手元のオブジェクトと状態の記録から決める |

実装の PR では、起点を `round_scope.origin`（下の「範囲の起点」）で選ぶ。起点と比較基点が違えば `changes.new_in_pr` を手元のオブジェクトで呼び、比べられなければ全差分にする。設計の PR は今のとおり `scope_decide.read_by_all_head` を使う。

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
| `origin` | `round_scope.py` | 新設 | `origin(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[changes.Range]` | `origin_record` の head が `head` の祖先なら、その比較基点と head を返す |
| `line_origin` | `round_scope.py` | 新設 | `line_origin(g: git.Git, state: Mapping[str, Any], head: str) -> Optional[changes.Range]` | 線引きの起点（席と PR を照らさず、そのラウンドの全員が読んだ最後のラウンド） |

`line_origin` を `origin` と分けるのは、設計の決定 2 が「範囲が全差分に戻ったラウンドでも、前に全員が読んだ head があれば線引きする」と定めるためである。読んでいない相手役がいて全差分になったラウンドでも、線引きの起点は残る。

- `_reusable`（前回の収束をそのまま返すかの判定）と `read_by_all_head` は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `origin`・`line_origin` | head が祖先でないか、コミットが手元に無い | PR の作り直し | `round_scope._reachable_from` | 既定値で続ける（None。全差分・線引きなし） | 無し | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `origin_record` | `scope_decide.read_by_all_head`・`_seated_read` | `grep -rn "def .*origin\b\|def .*previous_head\|def read_by_all" --include=*.py src/ndf` | 席の照らし合わせは `_seated_read` を使う。`read_by_all_head` は収束の使い回し用の条件のまま残す |
| `origin` | `round_scope.previous_head`・`_reachable_from` | 同上 | 祖先の確かめは `_reachable_from` を使う |
| `line_origin` | 同上 | 同上 | `origin` と同じ中身を、条件の無い `Basis()` で呼ぶ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `origin_record` | 比較基点・重点・単位の指示・MVV・根拠の確認が違っても記録を返す | 一部が読めなかったラウンドを飛ばす | 読んでいない相手役・PR の違い・ラウンドが無いときは None |
| `origin`・`line_origin` | 祖先の head を返す | 読んでいない相手役がいるとき、`origin` は None で `line_origin` は値 | 祖先でない・コミットが無いときは None |

## 新しく PR の変更になったファイルと変えた行（changes）

満たす決定: 決定 1・2、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `new_in_pr` | `changes.py` | 新設 | `new_in_pr(g: git.Git, previous: Range, current: Range) -> Optional[tuple[str, ...]]` | 比較基点が変わったことで、既読の差分にも今回渡す差分にも無い変更を持つファイルを返す。比べられなければ None |
| `since_diff` | `changes.py` | 新設 | `since_diff(g: git.Git, previous: Range, current: Range, added: Sequence[str]) -> str` | 前の head からの差分（PR が変えるファイルに絞る）に、`added` の今の PR の差分を続けて返す |
| `touched` | `changes.py` | 新設 | `touched(g: git.Git, since: str, head: str) -> leftover_decide.Touched` | `since` の後に変わったファイルと、足した行・書き換えた行の番号を返す |

- `new_in_pr`
  - 事前条件: `previous.head` は `current.head` の祖先
  - 副作用: `git diff` と `git diff --raw` を打つ（読むだけ）
- `since_diff`
  - 事前条件: `added` は `new_in_pr` の返り
  - 事後条件: 絞るファイルが 1 つも無ければ、前の head からの分は空にする（パスの指定の無い `git diff` は全差分になるため打たない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `new_in_pr` | 比べられない | 前の比較基点か前の head のコミットが手元に無い | `git.GitError` | 呼び出し元へ返す（None。呼び手は全差分にする） | 呼び手の注意 | 打ち直せる |
| `since_diff` | 差分を読めない | 同上 | `git.GitError` | 呼び出し元へ返す（例外。今の `precondition`） | `git の操作が失敗した` で終了コードは前提の欠け | 打ち直せる |
| `touched` | 差分を読めない | 同上 | `git.GitError` | 呼び出し元へ返す。呼び手は線引きをしない | 注意を 1 行 | 打ち直せる |

### 処理の手順

`new_in_pr` は 3 つの差分を読む。既読の差分 `previous.base...previous.head`、今の PR の差分 `current.base...current.head`、今回渡す差分 `previous.head..current.head` である。

| 確かめる対象 | 当たる（読んだか今回読む）条件 | 当たらなければ |
| --- | --- | --- |
| 今の PR の差分の足した行 | 今回渡す差分が同じ位置で同じ行を足す。または、前の head の行番号に直した位置で、既読の差分が同じ行を足す | そのファイルを返す |
| 今の PR の差分の消した行 | 「次の head の行」の番号を位置にして、同じように比べる | 同上 |
| 変更の種別・リネームの元と先・ファイルのモード | 既読の差分か今回渡す差分の `--raw` に同じ値がある | 同上 |
| 行で比べられないファイル | 今回渡す差分に無く、既読の差分と変更前後の blob が同じ | 同上 |

- 行の位置を前の head の番号に直すのは、今ある `changes._base_lines` を使う
- 計算量の注意: 3 つの差分は 1 回ずつ読み、行は辞書で引く

`touched` の「変えた行」は、`since..head` の差分で足した行と書き換えた行の番号である。行を消しただけの箇所は、前後の行の番号を入れる。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Range(base: str, head: str)` | 型 | `changes.py` | 無し |
| `Touched(files: frozenset[str], lines: Mapping[str, frozenset[int]])` | 型 | `leftover_decide.py` | 空 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `new_in_pr` | `changes._diff_files`・`_base_lines`・`review_diff` | `grep -rn "def .*new_files\|def .*touched\|def .*changed_lines\|def _base_lines\|--raw" --include=*.py src/ndf` | 差分の読み取りと行の対応は 3 つを使う。`git.Git.changed_lines` は行数の和を返すもので使えない |
| `since_diff` | `changes.review_diff`・`git.Git.changed_files` | 同上 | 同じ差分の引数（`_DIFF`）に `--literal-pathspecs` とパスの指定を足して作る |
| `touched` | `crossrefactor.scope.touched`（記号の単位） | 同上 | 返す単位が違うので使えない。`_diff_files` の行を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `new_in_pr` | 分岐したブランチへ比較先を変えると、新しく PR の変更になったファイルが返る。取り込みの競合を解消して前の head の内容に戻したファイルも返る | 競合の無いベースの取り込みは空。head が同じまま比較先を変えて実行権限の変更が現れる。同じ文の追加が同じファイルの別の関数にある。比較先を変えて差分から消えただけのファイルは返らない | コミットが無ければ None |
| `since_diff` | PR に関係の無いベースの変更を含まない。`added` のファイルの今の差分を含む | 絞るファイルが無ければ空 | 無し |
| `touched` | 足した行と書き換えた行 | 行を消しただけの箇所は前後の行。バイナリはファイルだけ | 無し |

## 線引きと残し方（leftover_decide）

満たす決定: 決定 2・3・4・5、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `normalized` | `leftover_decide.py` | 新設 | `normalized(text: str) -> str` | 空白をまとめ大文字小文字をそろえた本文の全体を返す（#2468 も同じ指摘の判定に使う） |
| `proceeds` | `leftover_decide.py` | 新設 | `proceeds(row: Mapping[str, Any], touched: Touched, *, returned: bool = False, unconfirmed: bool = False) -> bool` | 指摘が修正へ進むか |
| `kinds_of` | `leftover_decide.py` | 新設 | `kinds_of(row: Mapping[str, Any]) -> frozenset[str]` | 指摘の種類（行の `protect` と `check.tracks` の和） |
| `files_issue` | `leftover_decide.py` | 新設 | `files_issue(row: Mapping[str, Any]) -> bool` | 残す major のうち起票してよい種類を持つか |
| `identity` | `leftover_decide.py` | 新設 | `identity(origin_pr: int, text: str) -> str` | 元の PR の番号と `normalized(text)` の SHA-256 の先頭 12 字 |
| `issue_parts` | `leftover_decide.py` | 新設 | `issue_parts(text: str, impact: str, place: str, pr_urls: Sequence[str], ident: str) -> tuple[str, str]` | 伏せ終えた値から起票の題と本文を組む |
| `trusted` | `leftover_decide.py` | 新設 | `trusted(author: str, association: str, me: str, logins: Collection[str]) -> bool` | 起票の記録と Issue の作者を信用してよいか |
| `matches` | `leftover_decide.py` | 新設 | `matches(listed: Listed, ident: str, pr_url: str, text: str) -> bool` | Issue がこの指摘を扱うものか（設計の「起票の形」の条件） |
| `record_entries` | `leftover_decide.py` | 新設 | `record_entries(body: str) -> dict[str, dict[str, Any]]` | 起票の記録の本文から、識別値ごとの時刻と URL を読む |
| `record_body` | `leftover_decide.py` | 新設 | `record_body(entries: Mapping[str, Mapping[str, Any]]) -> str` | 起票の記録の本文を組む（マーカー `<!-- ndf:cross-review-leftover-record -->` で始める） |
| `result_fields` | `leftover_decide.py` | 新設 | `result_fields(row: Mapping[str, Any], comment_url: Optional[str]) -> dict[str, str]` | 結果の `items` に足す `disposition` と `url`（残さない行は空） |
| `summary_line` | `leftover_decide.py` | 新設 | `summary_line(rows: Sequence[Mapping[str, Any]]) -> Optional[str]` | 「残した指摘 N（起票 M）」。残した指摘が無ければ None |
| `decisions` | `leftover_decide.py` | 新設 | `decisions(rows: Sequence[Mapping[str, Any]], comment_url: Optional[str]) -> list[dict[str, str]]` | MVV に反する残した指摘を、番号・MVV の項目・URL の判断にする |

- `issue_parts`
  - 事前条件: `text`・`impact`・`place` は、**全文に伏せ字（秘密を伏せる処理）を当てた後の値**である。関数は切り出すだけで伏せない

題を伏せる前の本文から切り出すと、途中で切れた秘密は秘密の形に当たらなくなり、伏せられないまま公開されうる。同じ順の前例は、既存のコメントを字数で切る前に伏せる `drive` の `_remarks`（#4363 の後は `reviewing.remarks`）にある。

### 処理の手順

`proceeds` は次の表を上から当てる。行の `severity` は minor へ下げた後の値である。

| 順 | 条件 | 返り |
| --- | --- | --- |
| 1 | 行が `redline` を持つ（必ず人の承認が要る操作） | 進む（`parked` を付けない。今の扱いで人の判断を待つ） |
| 2 | `by` が NDF | 進む |
| 3 | `round` が 0（前の実行から引き継いだ） | 進む |
| 4 | `returned`（直した後にもう一度出た） | 進む |
| 5 | critical、または major でない（minor・nit は修正へ回さないので残す対象にしない） | 進む |
| 6 | `path` と `line` があり、`touched.lines[path]` に `line` がある | 進む |
| 7 | 行の `protect` か `check.tracks` に保護の種類がある | 進む |
| 8 | 行（`line`）が無くパスがそのファイルの変わった一覧にある。またはパスが無い | 進む |
| 9 | `unconfirmed`（判定を依頼して確認できなかった） | 進む |
| 10 | ほか | 残す |

`path` は先頭の `./` を除いて照らす。順 1 を先頭に置くので、前の確認で `check.tracks` が付いた行や、残した行に後から承認の要る操作が指摘された場合も、必ず進む側になる。

`files_issue` は `severity` が major で、`kinds_of` が `review-criteria.json` の `kinds.filed`（`delivered`・`safety`・`secret`・`user_data`・`irreversible`・`approval`）と重なる場合だけ真である。

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
| `proceeds`・`kinds_of`・`files_issue` | `journal._blocking`・`criteria_decide.protect_kinds` | `journal.py`・`criteria_decide.py` を読んだ | 種類の一覧は `criteria_decide` を使う。線引きの関数は無い |
| `identity` | `journal.citation_key`・`scope_decide.unit_id`（どちらも SHA-256） | `grep -rn "sha256(" --include=*.py src/ndf/domains/crossreview` | 入力と切り出す長さが違うので新しく書く |
| `issue_parts` | `publish.Publisher.create_issue`・`backlog.issues_gh.create_issue` | `grep -rn "def create_issue" src/ndf` | 作成は `Publisher.create_issue` を使い、文の組み立てだけを新しく書く |
| `trusted` | `github.GitHub.trusts` | `grep -rn "author_association\|authorAssociation" --include=*.py src/ndf` | `trusts` は write 権限まで読み、設計の条件（所有者・メンバー・共同作業者・実行の login・`issue_logins`）と違うので使わない |
| `matches`・`record_entries`・`record_body` | `report_decide.open_block`・`open_items` | `report_decide.py` を読んだ | 記録のコメントの一覧とは書き先と中身が違う。HTML のコメントに JSON を埋める形はそろえる |
| `result_fields`・`summary_line`・`decisions` | `finish_decide._counts`・`_limit_decision` | `finish_decide.py` を読んだ | 残した指摘の数え方は無い。`decisions` の形（`decision`・`basis`）は同じにする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `proceeds` | critical・変えた行の major・保護の対象（人の安全を含む）の major は真。変えていない行の major は偽 | 行が無くパスのファイルが変わった・パスも無い・minor・`round` 0・`returned`・`unconfirmed`・`./` 付きのパス | **`redline` の行は、変えていない行の major で起票する種類（`check.tracks`）を持っていても、`parked` が付いた行でも真** |
| `files_issue` | `delivered` を持つ major は真 | `authz` だけは偽。`authz` と `delivered` は真 | minor は偽 |
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
| `comments(pr)`・`comment(id)` | コメント（番号・本文・投稿者） | issue comments の GET |
| `post(pr, body)`・`edit(id, body)` | 番号と応答の更新時刻 | POST・PATCH（`threads.patient` を通す） |
| `search(ident)` | `Listed` の並び | `GET search/issues` |
| `recent(since)` | `since` より後に作られた `Listed` の並び（超えれば `TooMany`） | `GET repos/{repo}/issues?sort=created`。上限は `github.list-pages` |
| `create(title, body)` | Issue の URL | `publish.Publisher.create_issue` |

- `file_issues`
  - 事前条件: 呼び手は記録の公開のロック（`write_gate.pr_lock`）を持つ
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

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ PR の別の保存先の公開 | 起票の記録のコメント | 記録のコメントと同じ PR ごとのロック | 作成の前に記録へ書くので、中断しても候補が分かる |

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
| `file_issues` | 起票して URL を行に書き、同じ指摘は 2 回目に起票しない | 作成を試みていない候補だけなら検索も一覧も呼ばない。`issue_logins` の bot が書いた記録を別の login で読む。読み済みの login を取り直さない | 作成の失敗で行が残る。作成の後の保存の失敗の打ち直しで、同じ保存先・別の保存先・PR を巻き直した後でも起票しない。記録を書けなければ作成しない。検索に反映される前は一覧で見つける。**題の境目をまたぐ偽の秘密を持つ指摘を起票すると、作った Issue の題にも本文にも断片が無い** |
| `primary_of` | `github` | `others` だけが github | 未設定・`unknown`・壊れた JSON は起票しない |

## ジャーナルと引き継ぎ（journal・prior_decide）

満たす決定: 決定 2・4、受け入れ条件 2・5（不変条件 I1・I2）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `sort` | `journal.py` | 新設 | `sort(self, touched: leftover_decide.Touched, unconfirmed: Collection[str] = ()) -> frozenset[str]` | このラウンドで出た行のうち分け直すものに線引きを当て、残した行の指摘のキーを返す |
| `enter` | `journal.py` | 変更 | シグネチャは変えない | このラウンドで出た行の番号を `self.raised_ids` に、直した後に戻った行を `self.returned` に残す |
| `carry` | `journal.py` | 変更 | シグネチャは変えない | 記録の `parked` の項目は行にせず、本文から URL への対応 `self.filed` に残す。`issue` を持つほかの項目は行へ移す |
| `handable` | `journal.py` | 変更 | シグネチャは変えない | `parked` の行を返さない |
| `_classify` | `journal.py` | 変更 | シグネチャは変えない | 必ず人の承認が要る操作と分かった行の `parked` を外す |
| `findings` | `journal.py` | 変更 | シグネチャは変えない | `Finding.parked` を渡す |
| `_recorded_one` | `prior_decide.py` | 変更 | シグネチャは変えない | `Carried` に `parked: bool` と `issue: Optional[str]` を読む |

`sort` が分け直すのは、`raised_ids` のうち `fresh`（新しい・戻った行）か `parked` を持つ行だけである。前から未解決で `parked` の無い行は、出し直されても分け直さない（I2）。`proceeds` が偽なら `parked` を付けて `self.filed` の URL を `issue` に戻し、真なら `parked` を外す。`block` する指摘の集合（`_blocking`）は `parked` の行を除く。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `sort` | `journal.handable`・`restate`、`leftover_decide.proceeds` | `grep -rn "parked\|def sort" src/ndf/domains/crossreview`（該当なし） | 行を残す・外す処理は無い。判断は `proceeds` を使い、`sort` は行への書き込みだけを持つ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sort` | 変えていない行の major に `parked` が付き、`pending` に出ない | 残した行が critical や変えた行の major で出し直されると `parked` が外れる。確認できなかった指摘は `parked` を外し、判定対象外の指摘は残す。進むと決めた行は後のラウンドでも進む | **`redline` を持つ行は `parked` が付かない。残した行に後の出し直しで承認の要る操作が指摘されると `parked` が外れる。`check.tracks` の付いた行に後で承認の要る操作が指摘されても同じ** |
| `carry` | `parked` の項目は未解決の行にならず、同じ指摘が残ると URL が戻る | `issue` を持つ未解決の項目は引き継ぐ | 形の違う `parked`・`issue` は無視する |

## 終わり方・記録・スレッド（finish_decide・report_decide・threads）

満たす決定: 決定 4・5、受け入れ条件 2（不変条件 I1）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | シグネチャは変えない | `parked` の指摘を欠け（MVV の欠けを含む）にしない |
| `_finding_rows` | `report_decide.py` | 変更 | シグネチャは変えない | `parked` の行の終わり方に「直さずに残した（起票: <URL>）」か「直さずに残した（記録のみ）」を出す |
| `open_block` | `report_decide.py` | 変更 | シグネチャは変えない | 一覧の項目に `parked` と `issue` を足す |
| `viewer` | `threads.py` | 新設 | `GitHubPort.viewer(self) -> str` | 今の認証の login を 1 回だけ読んで返す |
| `_wanted` | `threads.py` | 変更 | シグネチャは変えない | スレッドの無い `parked` の指摘にスレッドを作らない |

`Finding` に `parked: bool = False` を足し、`summary_of` と `as_findings` が通す。`_counts` は `parked` を未解決の数に入れない。`_held`（`--stop-on-hold` の欠け）は `parked` の指摘を数えない（`redline` は `parked` にならないので今のまま）。

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
| `_ranges` | `drive.py` | 新設 | `_ranges(run: _Run, merge_base: str, head: str) -> Ranges` | 目的ごとに、起点・足すファイル・線引きの起点を決める |
| `_sort_leftovers` | `drive.py` | 新設 | `_sort_leftovers(run: _Run, prep: Prepared, checked: Checked) -> frozenset[str]` | 線引きの対象のラウンドなら変えた行を読んで `journal.sort` を呼ぶ |
| `default_max_rounds` | `settings.py` | 新設 | `default_max_rounds(every_section: bool) -> int` | `--max-rounds` を省いたときの上限（設計の PR は 3、ほかは 4） |
| `_with_leftovers` | `cli.py` | 新設 | `_with_leftovers(verdict: fin.Verdict, items: list[dict[str, Any]], data: Mapping[str, Any]) -> fin.Verdict` | 結果の `items` に残し方を足し、要約の行と MVV の判断を足す |
| `_prepare_round` | `drive.py` | 変更 | シグネチャは変えない（返りの `Prepared` に `lines: Optional[str]` を足す） | 実装の PR は起点からの差分を `since_diff` で作る |
| `_publish` | `cli.py` | 変更 | `_publish(ref, final, warnings, hub, main: Optional[Path])` | スレッドの後、記録のコメントの前に `leftover.file_issues` を呼ぶ |
| `_post_report` | `cli.py` | 変更 | `_post_report(state_dir, pr, final, warnings, hub_of, main: Optional[Path] = None)` | `main` を `_publish` へ渡す |
| `_report` | `cli.py` | 変更 | シグネチャは変えない | 起票に失敗した行があれば、前回の記録が同じでも書き直す |

`Hub` に `leftovers: Optional[Callable[[int], leftover.Port]] = None` を足し、`_github_hub` が `leftover.GitHubIssues` を作る。`cmd_report` は打った場所からメインディレクトリを決め、`manual=True` で起票する。`--max-rounds` の argparse の既定は None にする。

- `_with_leftovers`
  - 事前条件: 記録の公開（`_report`）の後に呼ぶ。起票の URL は公開の中で行に書かれる

### 処理の手順

| 目的 | `_ranges` の結果 |
| --- | --- |
| 設計の PR（`every_section`） | 今のとおり `round_scope.previous_head`。線引きの起点は無し |
| 実装の PR で `origin` が無い | 全差分。線引きの起点は `line_origin`（あれば） |
| 実装の PR で `origin` があり、比較基点が同じ | 起点は `origin.head`、足すファイル無し |
| 実装の PR で比較基点が違う | `new_in_pr` の結果を足す。None なら全差分で線引きなし |

線引きをするのは、実装の PR で、状態ファイルの通算で 2 ラウンド目以降で、`line_origin` があり、比較基点の変化で足すファイルが無いラウンドである。`_confirm` へは線引きをするかを `use_tracks` として渡す。`run.history` へは、`enter` のキーから `Checked.lowered` と `_sort_leftovers` の返りを除いて足す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_ranges` | 比べられない | コミットが手元に無い | `new_in_pr` が None | 既定値で続ける（全差分） | 注意を 1 行 | 打ち直せる |
| `_sort_leftovers` | 変えた行を読めない | `git.GitError` | `changes.touched` | 既定値で続ける（線引きなし。すべて進む） | 注意を 1 行 | 打ち直せる |

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
| `_sort_leftovers`（`run` を通す） | 2 ラウンド目に変えていない行の major と minor だけなら収束し、起票の種類のある major は起票、ほかは記録の URL が結果に載る | 起票の失敗の後の打ち直しで 1 回だけ起票する | 線引きの順で変えた行の判定を逆にすると落ちる |
| `default_max_rounds` | 省けば `--purpose design` は 3、`code` と省略時は 4 | `--max-rounds` を渡せばその値 | 無し |

## 手順書

満たす決定: 決定 1〜6、受け入れ条件 1・2・3

`skills/cross-review/` の `SKILL.md`・`docs/01-state-and-review.md`・`docs/02-fix-and-rotation.md` の「範囲」「判定」「ラウンドを足す」を、起点の条件・線引き・残し方・上限の既定に書き換える。文言を照合するテストは書かない。
