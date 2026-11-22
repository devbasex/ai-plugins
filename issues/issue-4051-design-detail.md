# #4051 の詳細設計: 実行中の行を持つ計画を、耐久ワークフローがどう終わっても索引から数える

設計文書 [issue-4051-design.md](./issue-4051-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ（`machine.unindex_idle`・`machine.is_indexed`）・シグネチャを変える関数が 1 つ（`run._indexed_until_done`）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ）:

```text
plugins/ndf/
├── src/ndf/progress/lane/machine.py    unindex_idle と is_indexed を足す。モジュールの説明文の索引の段落を直す
├── src/ndf/progress/plan/run.py        _indexed_until_done の引数と消す条件を変える
└── tests/progress/
    ├── test_lane_machine.py            unindex_idle と残った索引の数え方のテスト
    └── test_plan_run.py                advance の終わり方ごとの索引のテスト
docs/specifications/ndf-parallel-machine-plan-limit.md   稼働中の数の数え方・マシンの計画の索引・構成要素の表の run.py の行
```

依存の向き: `run.py` → `machine.py` の一方向で、今と変わらない。`unindex_idle` は `machine.py` の中の `read_lanes`・`unindex_plan` だけを呼ぶ。`is_indexed` は `_index_file` だけを使う。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可・計画の索引 | `ndf.progress.command`・`store`・`model` | モジュール | `unindex_idle`（新設）・`is_indexed`（新設） |
| `src/ndf/progress/plan/run.py` | 変更 | 計画を進める耐久ワークフローの起動と終わり | `ndf.progress.lane.machine`・`ndf.core.durable` | モジュール | `_indexed_until_done`（変更）・`advance`（呼び方と完了済みの早期 return を変更） |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` | 変更 | 確定仕様 | — | — | — |

## 索引を消す条件（`machine.py`）

満たす決定: 決定 1・決定 3・決定 5、受け入れ条件 1・4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `unindex_idle` | `src/ndf/progress/lane/machine.py` | 新設 | `unindex_idle(where: PlanPlace) -> bool` | 計画を起動の許可と同じ読み手で読み、数える行が無いときだけ索引から消し、消したら真を返す |
| `is_indexed` | `src/ndf/progress/lane/machine.py` | 新設 | `is_indexed(plan_id: str) -> bool` | 計画の索引のファイルがあるかを返す（中身は読まない） |

- `unindex_idle`
  - 事前条件: 呼ぶのはその計画の耐久ワークフロー（実行のキーのロックを持つ）だけである（決定 2）
  - 事後条件: `read_lanes(Path(where.cwd), where.plan)` が `readable` で、`key` が `where.plan_id` に等しく、`lanes` に `running_row` が真のものが無いとき、索引のファイルが無い。それ以外は索引のファイルを変えない
  - 副作用: 計画のステートマシンを 1 回読む。索引のファイルを消すことがある

- `is_indexed`
  - 事後条件: `_index_file(plan_id)` がファイルとしてあれば真、無ければ偽。ロックを取らず、ファイルを書かない
  - 副作用: 無し（ファイルの有無を 1 回見るだけ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `unindex_idle` | 計画を読めない | 保存先の誤り・DB が無い・チェックアウトが無い（`read_lanes` が `readable` の偽を返す） | `read_lanes` | 消さずに偽を返す（決定 3） | 無し | 次に耐久ワークフローが終わるときに確かめ直す |
| `unindex_idle` | 読んだ計画の ID が索引と違う | 作業ディレクトリが別のリポジトリを指すようになった | `key` と `where.plan_id` の比較 | 消さずに偽を返す | 無し | 同上 |
| `unindex_idle` | 索引のファイルを消せない | 権限・ファイルシステムの誤り（`OSError`） | `unindex_plan` | 呼び出し元へ返す | 呼び出し元の扱い（下の `_indexed_until_done`） | 残った索引は数え方が正しく数える（受け入れ条件 2） |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `unindex_idle` | 読めた・ID が同じ・数える行が無い | 消して真 |
| `unindex_idle` | 読めた・ID が同じ・数える行がある | 残して偽 |
| `unindex_idle` | 読めない、または ID が違う | 残して偽 |

## 耐久ワークフローの終わり（`run.py`）

満たす決定: 決定 1・決定 2、受け入れ条件 1・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_indexed_until_done` | `src/ndf/progress/plan/run.py` | 変更 | `_indexed_until_done(where: machine.PlanPlace) -> Iterator[None]`（今は `plan_id: str`） | 耐久ワークフローが終わったら（終わり方を問わず）、`machine.unindex_idle(where)` を打つ |
| `advance` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | `_indexed_until_done` へ `machine.PlanPlace(plan_id, str(where.cwd), plan)` を渡す。計画が `completed` のときの早期 return は、索引が無ければ今のとおり即座に返し、索引があるときだけ実行のキーのロックを取って（持ち主がいれば放すまで待って）`_indexed_until_done` を通してから返す |

- `_indexed_until_done`
  - 事前条件: 実行のキーのロックを持って入る。耐久ワークフローを流す経路は今の `with durable.session(...), _indexed_until_done(...)` の並びのまま、完了済みの早期 return は索引があるときだけ `durable.run_lock(...)`（待つ）の中で入り、どちらもロックを放す前に抜ける
  - 事後条件: 抜けた時点で計画が数える行を持たず読めれば、索引のファイルが無い。持つか読めなければ残る
  - 副作用: 抜けるときに計画のステートマシンを 1 回読む

- `advance` の完了済みの早期 return
  - 今は `durable.session` より前で `view.state == completed` を見て返すので、全行の完了と計画の `completed` が保存された後・索引を消す前にプロセスが落ちると、打ち直しても `_indexed_until_done` を通らず索引が残り続ける（決定 2 の「打ち直して回復した耐久ワークフローが終わるときに消える」が成り立たない）
  - 直した形: `completed` のときは次の順で決め、どの場合も今と同じ結果（`OUTCOME_COMPLETED`）を即座に返す
    1. `machine.is_indexed(plan_id)` が偽なら、ロックを取らずにそのまま返す（正常に終わって索引が片付いた計画の打ち直しは今と同じ手間で、ロックファイルへの書き込みもステートマシンの再読込も足さない）
    2. 真なら `with durable.run_lock(where.root, run_key(plan)):`（`wait` は既定の真）でロックを取り、その中で `with _indexed_until_done(place):` を何もせずに抜けてから返す。ロックが取れないときに諦めて返すことはしない
  - 待って取る理由: 実行のキーのロックを持つのは、同じ計画の耐久ワークフローだけではない。別の計画の起動時に走る `durable_sweep._retire_finished` も同じロックを `wait=False` で取ってキーのファイルを retired/ へ移し、索引の後始末はしない。取れないときに返すと、掃除と重なった打ち直しは `OUTCOME_COMPLETED` を返しても索引を残し、以後の起動許可のたびにその計画を読ませ続ける
  - 待ちが短い理由: 計画は `completed` なので、ロックの持ち主はどれもすぐ放す。掃除はキーのファイルを 1 つ移す間だけ持つ。元の耐久ワークフローは `completed` を保存した後で、`_indexed_until_done` の後始末を通って閉じる途中である。同じ計画の別の打ち直しも、この手順を通るだけである。`durable.session` を通らないので `StoreInUse` にはならない
  - 待って取った後は、持ち主が先に消していれば `unindex_idle` は計画を 1 回読むだけで、`unindex_plan` は何もしない
  - `durable.session` は DBOS を起動するので使わず、キーのロックだけを取る（ロックの持ち主だけが消す決定 2 を保ち、完了済みの計画の打ち直しに DBOS の起動を足さない）
  - 副作用: 索引が無い完了済みの計画の打ち直しでは、ファイルの有無を 1 回見るだけ。索引が残った計画の打ち直しでは、ロックを 1 回取り（持ち主がいれば放すまで待つ）、ステートマシンを 1 回多く読む

`PlanPlace` の `cwd` は、`launch_lane` が `admitted` に渡すもの（`spec["cwd"]`）と同じ `str(where.cwd)` にする。書く側と確かめる側が同じ所在を読むためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_indexed_until_done` | 本体（耐久ワークフロー）が例外で落ちた | 耐久ステップの誤り | `finally` | `unindex_idle` を打ってから例外をそのまま外へ出す | 今のとおり `ndf lanes run` の失敗 | 打ち直すと回復して続く |
| `_indexed_until_done` | `unindex_idle` が `OSError` を出した | 索引を消せない | `finally` | 本体が例外で落ちたときは本体の例外を優先して外へ出し、本体が終わったときは `OSError` を外へ出す（今の `finally` は後始末の例外が本体の例外を置き換えるので、本体が落ちたときは後始末の `OSError` を握って本体の例外を出し直す形へ変える） | 今のとおり | 残った索引は数え方が正しく数える |

## 確定仕様（`docs/specifications/ndf-parallel-machine-plan-limit.md`）

満たす決定: 決定 4、受け入れ条件 2・3

| 節 | 書き直す中身 |
| --- | --- |
| 構成要素と責務の `run.py` の行 | 「耐久ワークフローが終わるときの索引の削除」を「耐久ワークフローが終わるとき、計画が数える行を持たなければ索引を消す」へ |
| 構成要素と責務の `machine.py` の行 | 計画の索引の関数に `unindex_idle`・`is_indexed` を足す |
| 稼働中の数の数え方の索引の段落 | 書く時点（今のまま）・消す時点と条件（決定 1）・消す担い手（決定 2）・読めないときに残すこと（決定 3）。「異常終了で残ったファイルは、実行中の行が無い計画として 0 本と数える」を「残ったファイルの計画もステートマシンを読み、数える行があれば数え、無ければ 0 本と数える」へ |
| マシンの計画の索引 | 消す時点と条件（索引の寿命。上の段落と同じ中身）だけを直す。ファイルの形（版と項目）の文には触らない。形は #4044 が持ち、版 2（`plan_id`・`cwd`・`plan`・`store_root`・`db`）と旧版の版 1 の扱いを書く（[issue-4044-design-detail.md](./issue-4044-design-detail.md) の「確定仕様の書き直し」）。この課題が #4044 の後に入るときも、版 2 の形を版 1 へ戻さない。`unindex_idle` は版によらず `plan_id`・`cwd`・`plan` だけを読む |
| テスト観点 | 受け入れ条件 1・2・4・5 の観点を 1 行ずつ足す |

モジュールの説明文（`machine.py` の頭の docstring の「索引は…耐久ワークフローが終わるときに消す」）も同じ中身へ直す。

## 単体テストの観点

| 観点 | 置き場 |
| --- | --- |
| 実行中の行を持つ計画で `unindex_idle` が偽を返し索引が残る。行を終えると真を返し索引が消える | `tests/progress/test_lane_machine.py`（`other_repo` の計画を使う） |
| 読めない所在（DB の無いチェックアウト）の `unindex_idle` が偽を返し索引が残る | 同上 |
| 読んだ計画の ID が索引の `plan_id` と違うとき、`unindex_idle` が偽を返し索引が残る | 同上 |
| 本体が例外で落ち、`unindex_idle` も `OSError` を出すとき、`_indexed_until_done` が本体の例外を外へ出す。本体が終わったときは `OSError` を外へ出す | `tests/progress/test_plan_run.py` |
| 索引だけが残った計画を、行があれば `machine_plans`（`machine_count` 経由）が 1 本、無ければ 0 本と数える | 同上 |
| 区切りで止めた計画が実行中の行を持ったまま耐久ワークフローが例外で終わった後、索引が残り、登録簿の行が無くても別の計画の上限 1 の `admitted` が待つ。`cancel_failed` で終わったときも同じ | `tests/progress/test_plan_run.py`（`where` の計画を使う） |
| 完了・中止（行が無くなった）・区切り（行が無くなった）で `advance` が終わった後、索引が無い | 同上 |
| 計画が `completed` で保存済みのまま索引だけが残った状態（完了の保存の後・索引を消す前に落ちた）から `advance` を打ち直すと、`OUTCOME_COMPLETED` を返し、索引が無い。DBOS を起動しない（`durable.session` を通らない） | 同上 |
| 計画が `completed` で索引が無いときの打ち直しは `durable.run_lock` を呼ばずに `OUTCOME_COMPLETED` を返す。索引が残り、掃除（`durable_sweep._retire_finished`）が実行のキーのロックを持つ間に打ち直すと、掃除がロックを放した後に `OUTCOME_COMPLETED` を返し、索引が無い（掃除の側のロックはテストの中で別のスレッドが `locks.file_run_lock` を持ち、少し後に放して作る） | 同上 |
