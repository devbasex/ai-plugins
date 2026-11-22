# #4051 の詳細設計: 実行中の行を持つ計画を、耐久ワークフローがどう終わっても索引から数える

設計文書 [issue-4051-design.md](./issue-4051-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 1 つ（`machine.unindex_idle`）・シグネチャを変える関数が 1 つ（`run._indexed_until_done`）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ）:

```text
plugins/ndf/
├── src/ndf/progress/lane/machine.py    unindex_idle を足す。モジュールの説明文の索引の段落を直す
├── src/ndf/progress/plan/run.py        _indexed_until_done の引数と消す条件を変える
└── tests/progress/
    ├── test_lane_machine.py            unindex_idle と残った索引の数え方のテスト
    └── test_plan_run.py                advance の終わり方ごとの索引のテスト
docs/specifications/ndf-parallel-machine-plan-limit.md   稼働中の数の数え方・マシンの計画の索引・構成要素の表の run.py の行
```

依存の向き: `run.py` → `machine.py` の一方向で、今と変わらない。`unindex_idle` は `machine.py` の中の `read_lanes`・`unindex_plan` だけを呼ぶ。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可・計画の索引 | `ndf.progress.command`・`store`・`model` | モジュール | `unindex_idle`（新設） |
| `src/ndf/progress/plan/run.py` | 変更 | 計画を進める耐久ワークフローの起動と終わり | `ndf.progress.lane.machine` | モジュール | `_indexed_until_done`（変更）・`advance`（呼び方だけ変更） |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` | 変更 | 確定仕様 | — | — | — |

## 索引を消す条件（`machine.py`）

満たす決定: 決定 1・決定 3・決定 5、受け入れ条件 1・4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `unindex_idle` | `src/ndf/progress/lane/machine.py` | 新設 | `unindex_idle(where: PlanPlace) -> bool` | 計画を起動の許可と同じ読み手で読み、数える行が無いときだけ索引から消し、消したら真を返す |

- `unindex_idle`
  - 事前条件: 呼ぶのはその計画の耐久ワークフロー（実行のキーのロックを持つ）だけである（決定 2）
  - 事後条件: `read_lanes(Path(where.cwd), where.plan)` が `readable` で、`key` が `where.plan_id` に等しく、`lanes` に `running_row` が真のものが無いとき、索引のファイルが無い。それ以外は索引のファイルを変えない
  - 副作用: 計画のステートマシンを 1 回読む。索引のファイルを消すことがある

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
| `advance` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | `_indexed_until_done` へ `machine.PlanPlace(plan_id, str(where.cwd), plan)` を渡す |

- `_indexed_until_done`
  - 事前条件: `durable.session` の中（実行のキーのロックを持つ）で入る。今の `with durable.session(...), _indexed_until_done(...)` の並びのままにし、ロックを放す前に抜ける
  - 事後条件: 抜けた時点で計画が数える行を持たず読めれば、索引のファイルが無い。持つか読めなければ残る
  - 副作用: 抜けるときに計画のステートマシンを 1 回読む

`PlanPlace` の `cwd` は、`launch_lane` が `admitted` に渡すもの（`spec["cwd"]`）と同じ `str(where.cwd)` にする。書く側と確かめる側が同じ所在を読むためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_indexed_until_done` | 本体（耐久ワークフロー）が例外で落ちた | 耐久ステップの誤り | `finally` | `unindex_idle` を打ってから例外をそのまま外へ出す | 今のとおり `ndf lanes run` の失敗 | 打ち直すと回復して続く |
| `_indexed_until_done` | `unindex_idle` が `OSError` を出した | 索引を消せない | `finally` | 本体が例外で落ちたときは本体の例外を優先して外へ出し、本体が終わったときは `OSError` を外へ出す（今の `unindex_plan` と同じ扱い） | 今のとおり | 残った索引は数え方が正しく数える |

## 確定仕様（`docs/specifications/ndf-parallel-machine-plan-limit.md`）

満たす決定: 決定 4、受け入れ条件 2・3

| 節 | 書き直す中身 |
| --- | --- |
| 構成要素と責務の `run.py` の行 | 「耐久ワークフローが終わるときの索引の削除」を「耐久ワークフローが終わるとき、計画が数える行を持たなければ索引を消す」へ |
| 構成要素と責務の `machine.py` の行 | 計画の索引の関数に `unindex_idle` を足す |
| 稼働中の数の数え方の索引の段落 | 書く時点（今のまま）・消す時点と条件（決定 1）・消す担い手（決定 2）・読めないときに残すこと（決定 3）。「異常終了で残ったファイルは、実行中の行が無い計画として 0 本と数える」を「残ったファイルの計画もステートマシンを読み、数える行があれば数え、無ければ 0 本と数える」へ |
| マシンの計画の索引 | ファイルの形を `{"version": 1, "plan_id": <計画のステートマシンの ID>, "cwd": <作業ディレクトリ>, "plan": <計画の名前>}` へ（実装の `index_plan` が書く形） |
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
