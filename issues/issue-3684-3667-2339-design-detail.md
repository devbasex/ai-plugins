# #3684 #3667 #2339 の詳細設計: 契約の検査の規則と、内部データの型と版

設計文書 [issue-3684-3667-2339-design.md](issue-3684-3667-2339-design.md) を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── limits.json                           方針へ read_by を足す・supervise.worker-run（3,600 秒）を足し supervise.worker-step を消す
│   ├── data/
│   │   └── internal-names.json              * 名前の対応表
│   ├── workflow/
│   │   ├── context.schema.json               pace を $defs の plain_pace と mvv_pace の 2 つに分ける・version を形の版に・product_version を足す
│   │   ├── stage-io.schema.json              release-result の outputs.version を product_version へ
│   │   └── stages/                           deploy.json（出力）・release-verification.json・layout-review.json（入力）の version を product_version へ
│   └── schemas/
│       ├── limits.schema.json                read_by を必須にする
│       ├── result.schema.json                items の要素へ任意の欄を 4 つ・任意の version・$defs/outputs の version を product_version へ
│       └── internal/
│           ├── common.schema.json           * 共通の欄（version・at・status）の $defs
│           ├── usage.schema.json            * 使用量の記録の 1 行
│           ├── mvv-signals.schema.json      * MVV の兆候の 1 行
│           ├── mvv-validation.schema.json   * MVV の検証の 1 行
│           ├── context-records.schema.json  * 文脈の記録の 1 行
│           └── loop-event.schema.json       * 開発ループのイベントの 1 行（実行の状態）
├── src/ndf/
│   ├── checks/
│   │   ├── limits_readers.py                * 規則 limit-readers
│   │   ├── copy_dup.py                      * 規則 copy-dup
│   │   ├── internal_names.py                * 規則 internal-names
│   │   ├── ratchet.py                       * ラチェット
│   │   ├── ratchet.json                     * 規則ごとの基準の数
│   │   ├── facts.py                          照合の相手を 3 つ足す
│   │   ├── rules.json                        新しい規則のしきい値
│   │   ├── run.py                            新しい規則を並びへ足す
│   │   └── README.md                         規則の表とラチェットの節
│   ├── contracts/
│   │   ├── internal.py                      * 内部データの読み書きの核
│   │   └── chosen.py                         CHOOSERS をスキーマから
│   ├── adapters/notify.py                    KIND_LABELS をスキーマから
│   ├── progress/model.py                     MVV_PACES をスキーマの $defs/mvv_pace から
│   ├── domains/loop/cli.py                   pace の選択肢を $defs の 2 つから組み立てる
│   ├── core/worker.py                        WORK_TIMEOUT を上限の設定から
│   ├── core/plan/model.py                    LIMIT_PER_STEP を上限の設定から
│   ├── core/plan/runner.py                   ANSWER_WAIT_SECONDS を上限の設定から
│   ├── core/durable_sweep.py                 KEEP_DAYS を上限の設定から
│   ├── testing/records.py                    KEEP_DAYS・KEEP_ROWS を上限の設定から
│   ├── domains/gws/thumbnails.py             SIZES をスキーマから
│   ├── domains/materials/collect.py          INTERFACE_VALUES をスキーマから
│   ├── domains/relay/context_thresholds.py   語の集まりを StrEnum へ
│   ├── domains/crossreview/drive.py          DEFAULT_MAX_ROUNDS を上限の設定から
│   ├── domains/crossrefactor/flaky_decide.py MAX_ATTEMPTS を上限の設定から
│   ├── domains/records/jsonl.py              記録の読み書き（read_rows・append・append_locked）に種類を渡し核へ通す
│   ├── domains/records/usage.py              jsonl へ種類 usage を渡す
│   ├── domains/records/mvv.py                jsonl へ種類 mvv-signals・mvv-validation を渡す・兆候の version（MVV の設定の版）を mvv_version へ
│   ├── domains/relay/context_records.py      jsonl へ種類を渡す
│   ├── domains/relay/context_report.py       同上
│   ├── domains/loop/store.py                 LoopStore.read・append を核へ通す
│   ├── domains/loop/record_decide.py         at を時刻の文字列で書き、読むときに秒へ直す・version-shipped の version を product_version へ
│   ├── contracts/result.py                   emit が version を確かめる（欄は任意）
│   ├── progress/plan/__init__.py             read_doc が今の版より大きい版で止まる
│   ├── progress/command.py                   条件 release_record が product_version を見る・machine_context を compose_context へ・_prepare_new が version: 1 を書く
│   ├── progress/run.py                       View.context を compose_context へ
│   ├── progress/stages.py                    compose_context を新設・snapshot_from の前に stage-snapshot の移行・snapshot_dict が版 1 で書く
│   ├── domains/release/cli.py                assembled・cmd_record の outputs の version を product_version へ
│   ├── progress/gate/asking.py               リリースのゲートが product_version を読む
│   └── progress/invoke.py                    read_report・_call_skill が結果の版を見る
├── skills/refactoring/SKILL.md               値の保存先の基準
└── tests/                                    下の「単体テストの観点」の表のファイル
```

依存の向き: 新しい規則（`checks/`）は `ndf.contracts` の読み手と `checks/model.py` の `Finding` だけを読み、互いを読まない。

**規則のモジュールが持つ入口の関数は、既にある規則と同じ `check(scope, rules)` にそろえる。** 既存の `structure.py:76`・`facts.py:231` と同じ形で、`run.py` が規則ごとに同じ形で呼ぶ。この同名は役割が同じことを示すものなので、規則 `copy-dup` の対象から外す（`rules.json` の `copy_dup.allow_names` に `check` を置く）。`ratchet.py` は規則を読まず、規則の名前と件数だけを受け取る。内部データの核（`contracts/internal.py`）は `ndf.contracts` の層にあり、`domains/` の書き手がそれを読む。層の向きは下から `ndf.contracts`・`ndf.adapters`・`ndf.core`・`ndf.progress`・`ndf.domains` で、規則 `layer` が守る。型の関係は設計文書の「構成要素」の表にあり、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/checks/limits_readers.py` | 新設 | 上限の設定の読み手の指定と、実際に方針を引く import を突き合わせる | `ndf.contracts.limits`、`checks.model` | モジュール | `declared`・`actual`・`check`（新設） |
| `src/ndf/checks/copy_dup.py` | 新設 | 2 つ以上のファイルの同じ名前の最上位の関数と、4 つ以上のファイルに出る文字列を数える | `checks.model` | モジュール | `top_functions`・`repeated_strings`・`check`（新設） |
| `src/ndf/checks/internal_names.py` | 新設 | 内部データのスキーマと書き手が、名前の対応表に無い別名を使っていないかを見る | `ndf.contracts`、`checks.model` | モジュール | `allowed`・`check`（新設） |
| `src/ndf/checks/ratchet.py` | 新設 | 規則ごとの基準の数を読み、超えた規則を違反にし、下回ったら基準を書き直す | `ndf.contracts`（読み込みのみ） | モジュール | `baseline`・`verdicts`・`lower`（新設） |
| `src/ndf/checks/facts.py` | 変更 | `const-fact` の照合の相手に、スキーマの `enum`・上限の設定の数・辞書の値を足す | `ndf.contracts.limits`、`ndf.contracts.facts` | モジュール | `consts`（変更）・`dict_values`・`enum_sets`・`limit_numbers`・`_enum_dups`・`_limit_dups`（新設） |
| `src/ndf/contracts/internal.py` | 新設 | 内部データを種類ごとのスキーマで読み書きし、古い版を移行の関数で受ける | `ndf.contracts` | モジュール | `read_record`・`write_record`・`migrate`・`readable_version`・`VERSION`（新設） |
| `src/ndf/checks/spec_render.py` | 変更 | 設計レビューの項目の表から、項目ごとの行き先の並び（`review_items`）を組み立てる | `checks.spec_model` | モジュール | `review_items`（新設） |
| `src/ndf/checks/spec_docs.py` | 変更 | 生成した `review_items` と `children.json` の差分と、行き先の無い項目を指摘にする | `checks.spec_render` | モジュール | `check`（変更） |
| `src/ndf/checks/run.py` | 変更 | 新しい規則を規則の並びへ足し、ラチェットのある規則の件数を照らす | 上の 4 つ | モジュール | `_rules`（変更） |
| `src/ndf/core/worker.py` | 変更 | worker の上限を上限の設定の新しい方針 `supervise.worker-run`（3,600 秒。値は今の `WORK_TIMEOUT` と同じ）から読む | `ndf.contracts.limits` | モジュール | `WORK_TIMEOUT`（削除）・`work_timeout`（新設） |

## 上限の設定の読み手の指定（規則 `limit-readers`）

満たす決定: 決定 1、受け入れ条件 #3684 の 1・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `declared` | `src/ndf/checks/limits_readers.py` | 新設 | `declared(raw: object) -> dict[str, frozenset[str]]` | 上限の設定の方針ごとの、指定された読み手のモジュールの名前 |
| `actual` | `src/ndf/checks/limits_readers.py` | 新設 | `actual(trees: Mapping[str, ast.Module]) -> dict[str, frozenset[str]]` | 構文木から、方針の名前を引く呼び出しを探し、方針ごとに引いたモジュールの名前を集める |
| `check` | `src/ndf/checks/limits_readers.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 指定と実際の食い違いを指摘にする（既存の規則と同じ入口の形） |

- `actual`
  - 事前条件: `trees` のキーはプラグインの根からの相対パスで、`src/` の下の `.py` だけを含む
  - 事後条件: 返す名前はモジュールの名前（`src/ndf/core/worker.py` → `ndf.core.worker`）である。下の「読み口」に当たる呼び出しの、方針の名前の引数を「引数の解き方」で解いたものを集める
  - 読み口（方針を引く関数。`rules.json` の `limits.readers` が持つ。実コードの呼び出しの形は右の列）:

    | 関数（完全な名前） | 方針の名前の引数 | 実コードの例 |
    | --- | --- | --- |
    | `ndf.contracts.limits.policy` | 1 番目 | `limits.policy(name)` |
    | `ndf.contracts.limits.value` | 1 番目 | `limits.value(*POLICY)`（`src/ndf/contracts/named_cap.py:80`）・`limits_mod.value("handoff.lines", "count")`（`src/ndf/domains/records/handoff.py:17`） |
    | `ndf.contracts.limits.seconds` | 1 番目 | `limits.seconds(LOCK_POLICY)`（`src/ndf/domains/loop/store.py:79`）・`limits.seconds("locks.append")`（`src/ndf/domains/records/jsonl.py:53`） |
    | `ndf.contracts.limits.scaled` | 1 番目 | `limits.scaled(name, measured)` |
    | `ndf.contracts.limits.from_env` | 3 番目（`policy_name`） | `limits.from_env(environ, name, policy_name, key)` |
    | `ndf.domains.hooks.tables.policy_value` | 1 番目 | `tables.policy_value("hook.pushback", "attempts")`（`src/ndf/domains/hooks/counter.py:89`） |

  - 関数の名前の解き方: モジュールの import を読み、呼び出しの先を完全な名前へ直してから上の表と照らす。`from ndf.contracts import limits`・`from ndf.contracts import limits as limits_mod`・`from ndf.contracts.limits import seconds` のどれも同じ `ndf.contracts.limits.seconds` になる。名前だけで照らさない（`ndf.testing.limits` は上限の設定ではない別のモジュールで、`limits_mod` の別名も持つ）
  - 引数の解き方（上から順に試す）:
    1. 文字列の定数: その文字列
    2. 名前（`LOCK_POLICY`）: 同じモジュールの最上位の代入の右辺を 1. で解く
    3. 星付きの名前（`*POLICY`）: 最上位の代入の右辺が組（`("relay.max-named", "count")`）のとき、表の引数の位置の要素を 1. で解く
    4. 属性（`machine.CAP_POLICY`）・import した名前: import の先のモジュールの最上位の代入を 2.・3. で解く（1 階層だけ。先がさらに別のモジュールを指すときは解けないものにする）
    5. 上のどれでも解けない（引数・関数の戻り値・f 文字列）: 「動的な読み口」の指定で解く（次の項）
  - 動的な読み口: 名前を実行時に組み立てる読み手は、`rules.json` の `limits.dynamic` に `{"at": "<関数の完全な名前>", "expr": "<名前を組み立てる式の綴り>", "template": "<名前の型>", "vocabulary": "<契約のファイル>#<JSON Pointer>", "reason": "<理由>"}` を指定する。`expr` は読み口の引数の式で、引数が関数の中の名前なら、その名前に入る式（`for` の変数なら並びの式）を書く。関数 `at` の式が `expr` か `template` の形と違えば、その読み口は解けないものにする。`template` の `{}` に `vocabulary` の値を 1 つずつ、`{1}`・`{2}` … に、その値へたどる JSON Pointer の `*` が当たったキー（左から 1 番目の `*` が `{1}`。並びなら添字）を入れた名前だけを引いたものとして数える（接頭辞で始まる方針をすべて数えることはしない）。例えば `ndf.domains.hooks.budget.budget_for` の 2 つの型 `hook.budget.{}` と `hook.budget.{}.{1}` は、どちらも語彙が `hooks/runtimes.json#/runtimes/*/events/*/budget` で、`{}` は予算のキー、`{1}` は 1 番目の `*` が当たったランタイムの名前である（`codex` の `stop` なら `hook.budget.stop.codex`）。`budget.py` は読み口 `ndf.domains.hooks.tables.policy_row` で 1 行ずつ引く。表を丸ごと参照する読み（`tables.policies()`）は動的な読み口の指定で解かず、`unresolved` にする
  - 解けない読み口: 指定の無い動的な読み口は `unresolved` の指摘にする（保存先は呼び出しの行）。黙って数えない・黙って捨てる、のどちらもしない
  - 副作用: 無し（構文木を読むだけ）
- `check`
  - 事後条件: 指摘は 3 種類で、指定した読み手が引いていない方針（`unread`）と、指定の無い方針を引いているモジュール（`undeclared`）と、解けない読み口（`unresolved`）。どれも方針の名前（解けたとき）とモジュールの名前を理由に含む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `declared` | 上限の設定を読めない | ファイルが無い・JSON が壊れている・`read_by` がスキーマに合わない | `ndf.contracts.limits.table` | 呼び出し元へ返す（`LimitsError`） | `ndf check` が `stopped`・`cause.kind` は `config_broken`・終了コード 2 | 直してから打ち直す |
| `actual` | 構文木を読めない | 構文の誤り | `ndf check` の木を作る所（既存） | 呼び出し元へ返す | `ndf check` が `stopped`・`invalid`・終了コード 2 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 指定した読み手が、その方針を引く呼び出しを持たない | `unread` の指摘（保存先は上限の設定のその方針の行） |
| `check` | あるモジュールが方針を引くが、その方針の `read_by` にそのモジュールが無い | `undeclared` の指摘（保存先は呼び出しの行） |
| `check` | 読み口の引数が解けず、`limits.dynamic` にその関数の式の指定も無い | `unresolved` の指摘（保存先は呼び出しの行） |
| `check` | 指定と実際が一致する | 指摘なし |

- 繰り返しの終わる条件: 方針の数（223）× 読み手の数で有限
- 計算量の注意: 動的な読み口の名前は、語彙から組み立てた名前を集合にして方針の表と突き合わせる。方針ごとに全ファイルを走査しない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `read_by` | データ（方針の項目） | `contracts/limits.json` | 無し（必須） |
| `limits.readers` | 設定値（読み口の関数の完全な名前と、方針の名前の引数の位置） | `src/ndf/checks/rules.json` | 上の「読み口」の表の 6 行 |
| `limits.dynamic` | 設定値（動的な読み口の指定） | 同上 | 上の「動的な読み口」の `ndf.domains.hooks.budget` の 2 行 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `actual` | `src/ndf/checks/imports.py`（規則 `layer`・`cycle` が使う import の収集） | `grep -n "def " src/ndf/checks/imports.py` | 使う（モジュールの名前への変換を再利用し、呼び出しの引数の読み取りだけを足す） |
| `declared` | `ndf.contracts.limits.table` | `src/ndf/contracts/limits.py` を読んだ | 使う（`Policy` へ `read_by` を足す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `declared` | 方針ごとに指定の集まりを返す | `read_by` が 1 つだけの方針 | 上限の設定を読めないときに `LimitsError` |
| `actual` | 実コードの呼び出しの形をそのまま写した合成のモジュールで集める: `limits.seconds(LOCK_POLICY)`（最上位の定数）・`limits.value(*POLICY)`（組の定数）・`limits_mod.value("handoff.lines", "count")`（別名の import）・`from ndf.contracts.limits import seconds` の `seconds("x")`・`limits.from_env(env, "N", "p", "seconds")`（3 番目）・`tables.policy_value("hook.pushback", "attempts")` | `ndf.testing.limits` を `limits_mod` の別名で読むモジュール（集めない）・`machine.CAP_POLICY` のように別のモジュールの定数を指す引数（1 階層だけ解く） | 構文の誤りで木が無いファイルは渡されない |
| `actual`（動的な読み口） | 指定した語彙の値から組み立てた名前だけを集める | 語彙に無い名前の方針 `hook.budget.unused` を足した設定（集めない。`check` で `unread`） | 指定の無い f 文字列の読み口（集めずに `unresolved`） |
| `check` | 一致すれば 0 件 | 指定が 1 つで実際が 0 件（`unread` 1 件） | 指定の無い方針を引くモジュール（`undeclared` 1 件）・解けない読み口（`unresolved` 1 件） |

テストのファイル: `tests/checks/test_limits_readers.py`（新設）

## コピーで生まれた重複（規則 `copy-dup`）とラチェット

満たす決定: 決定 4・5・12、受け入れ条件 #3684 の 4・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `top_functions` | `src/ndf/checks/copy_dup.py` | 新設 | `top_functions(trees: Mapping[str, ast.Module]) -> list[Finding]` | 2 つ以上のファイルに同じ名前の最上位の関数がある組を指摘にする |
| `repeated_strings` | `src/ndf/checks/copy_dup.py` | 新設 | `repeated_strings(trees: Mapping[str, ast.Module]) -> list[Finding]` | 4 つ以上のファイルに出る 8〜80 字の文字列を指摘にする |
| `check` | `src/ndf/checks/copy_dup.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 上の 2 つを並べて返す（既存の規則と同じ入口の形） |
| `baseline` | `src/ndf/checks/ratchet.py` | 新設 | `baseline() -> dict[str, int]` | 規則ごとの基準の数 |
| `verdicts` | `src/ndf/checks/ratchet.py` | 新設 | `verdicts(counts: Mapping[str, int], base: Mapping[str, int] \| None) -> list[str]` | 今の件数が基準と違う規則と、基準を前の値より上げた規則を文にする（空なら通る） |
| `below` | `src/ndf/checks/ratchet.py` | 新設 | `below(counts: Mapping[str, int], current: Mapping[str, Baseline]) -> list[str]` | 基準を下回った規則の名前を返す（空なら `lower` も `HEAD` の取得もしない） |
| `lower` | `src/ndf/checks/ratchet.py` | 新設 | `lower(path: Path, counts: Mapping[str, int], current: Mapping[str, Baseline], commit: str, day: str) -> dict[str, Baseline]` | 基準を下回った規則の基準を書き直し、書いた後の基準を返す |

- `top_functions`
  - 事前条件: `trees` は `src/` の下の `.py` だけ
  - 事後条件: 名前が `_` で始まる関数（モジュールの内側の助けの関数）も数える。1 つの名前につき 1 件で、辞書順の先頭のファイルの行に付ける
  - 副作用: 無し
- `repeated_strings`
  - 事後条件: docstring を除く。4 ファイル以上に出る文字列 1 つにつき 1 件
- `verdicts`
  - 事後条件: 3 つの場合を違反にする。(1) 今の件数が基準の `max` を超える（増やした）。(2) 今の件数が基準の `max` を下回る（減らしたのに基準を据え置いた。理由に「`ndf check --write-spec` で基準を下げてコミットする」を出す）。(3) `base`（変更の前の `ratchet.json` の `max`）があり、今の `max` がそれを上回る（基準を引き上げた）。つまり CI を通るのは「件数 = 基準 ≤ 変更の前の基準」のときだけで、重複を減らす PR は基準の更新を同じ PR に含めることになり、基準を上げて増加を通す PR は落ちる
  - `base` の出所: CI のジョブ `script-structure-check` は 2 世代を取っている（Pull Request と merge queue では `HEAD^1` が変更の前の先頭）。push では push の前の先頭（`github.event.before`）を取得して渡す。`ndf check --ratchet-base <変更の前>` を打ち、`git show HEAD^1:plugins/ndf/src/ndf/checks/ratchet.json` を読む（git の呼び出しは 1 回）。宛先に `ratchet.json` が無い（規則を足す PR 自身）ときと、手元で `--ratchet-base` を付けないときは `base` を `None` にし、(3) だけを見ない
  - 基準を上げる経路は持たない（決定 4。規則の数え方を変えて件数が増えるときは、数え方を変える PR で重複も減らすか、規則の `rules.json` の除外を足す）
- `lower`
  - 事前条件: `ndf check --write-spec` のときだけ呼ばれる（検査だけのときは基準を書き換えない）。CI は `--write-spec` を付けずに打ち、基準を据え置いた PR は `verdicts` の (2) で落ちるので、重複を減らした人は手元で `--write-spec` を打って `ratchet.json` を同じ PR にコミットする
  - 事後条件: 書き直した `ratchet.json` は、下回った規則の `max` を今の件数にし、`measured` と `commit` を渡された値にする
  - 副作用: `src/ndf/checks/ratchet.json` を書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 基準のファイルを読めない | ファイルが無い・JSON が壊れている・`version` が知らない値 | `ratchet.baseline` | 止める | `ndf check` が `stopped`・`config_broken`・終了コード 2 | 直してから打ち直す |
| `verdicts` | 基準に無い規則の件数が来た | 規則を足して基準を書き忘れた | `ratchet.verdicts` | 止める（基準の無い規則をラチェットで通さない） | 同上 | 基準を足してから打ち直す |

**`counts` に渡すのは、ラチェットを当てる規則の件数だけである**（今は `copy-dup` の 1 つ）。0 件を保つ規則の件数は渡さず、`run.py` が規則の名前で分ける。
| `lower` | 基準のファイルを書けない | 権限・ディスク | `ratchet.lower` | 呼び出し元へ返す | `ndf check` が `stopped`・`failed`・終了コード 1 | 権限を直してから打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `copy_dup.min_files` | 設定値（文字列が何ファイルに出たら数えるか） | `src/ndf/checks/rules.json` | 4 |
| `copy_dup.min_chars`・`max_chars` | 設定値（数える文字列の長さ） | 同上 | 8・80 |
| `copy_dup.allow_names` | 設定値（同名でも数えない関数の名前） | 同上 | `["check", "main"]`（規則の入口と、サブコマンドの入口） |
| `ratchet.rules` | データ（規則ごとの基準の数） | `src/ndf/checks/ratchet.json` | 実装の単位 3 の最後に測る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `top_functions` | 同じ名前の関数が 2 ファイルにあれば 1 件 | 同じファイルの中の 2 つの同名（入れ子の関数。数えない） | 木が空のとき 0 件 |
| `repeated_strings` | 4 ファイルに出る 20 字の文字列で 1 件 | 3 ファイル（0 件）・4 ファイル（1 件）・7 字（0 件）・8 字（1 件）・81 字（0 件） | docstring だけのファイル（0 件） |
| `verdicts` | 基準と同じ件数なら通る | 基準 + 1（落ちる）・基準 − 1 で基準を据え置き（落ちる）・基準 − 1 で基準も − 1（通る）・`base` より `max` を + 1 した基準と同じ件数（落ちる）・`base` が `None`（(3) を見ない） | 基準に無い規則の件数（止まる） |
| `lower` | 下回った規則の基準が今の件数になる | 下回った規則が 0 個（書かない） | 書けないときに例外 |

テストのファイル: `tests/checks/test_copy_dup.py`・`tests/checks/test_ratchet.py`（新設）

## スキーマから組み立てる定数（5 個）

満たす決定: 決定 7、受け入れ条件 #3667 の 1・2

5 個の定数の右辺を、スキーマの読み込みを共有する契約の読み手へ置き換える。

**スキーマの読み込みは `ndf.contracts.load_schema` の 1 つに集め、定数の初期化と検証器が同じ読み込みを使う。** 今の `load_json` はキャッシュを持たず、`_schema_validator` は `@cached_reader` で検証器だけを 1 回作る。定数の初期化が `load_json` を直に呼ぶと、同じプロセスで `notice.schema.json` を 2 回読んで解析する（通知の解析では従来の 1 回から 2 回に増える）。次の 2 つを足す。

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load_schema` | `src/ndf/contracts/__init__.py` | 新設 | `load_schema(path: str) -> Mapping[str, Any]` | スキーマのファイルを `@cached_reader` で 1 プロセス 1 回だけ読む。返す値は書き換えない（呼び手は写してから変える） |
| `schema_enum` | 同上 | 新設 | `schema_enum(path: str, pointer: str) -> tuple[str, ...]` | `load_schema` の結果から JSON Pointer の先の `enum` を並びのまま返す |
| `_schema_validator` | 同上 | 変更 | 変えない | 内側の `load_json(path)` を `load_schema(path)` に変える（`$defs` を引く形は今も新しい辞書を組むので、共有した値を書き換えない） |

`src/ndf/domains/loop/cli.py:164` の `contracts.load_json(definition.CONTEXT)` も `load_schema` へ変える。

| 定数 | 保存先 | 読む所 |
| --- | --- | --- |
| `KIND_LABELS` | `src/ndf/adapters/notify.py` | `contracts/schemas/notice.schema.json` の `properties.kind`。見出し（`回答待ち` など）はスキーマに無いので、`kind` を `enum` から `oneOf` の `{"const": "question", "title": "回答待ち"}` の並びへ変え、値と見出しを同じ行に持つ。`KIND_LABELS` は `{c["const"]: c["title"]}` で組む。見出しを持たない行（`title` の無い `const`）は値そのものを見出しにする（`Notice.text` の組み立て（`notify.py:135`）は `KIND_LABELS.get(kind, kind)` で引き、`KeyError` で止めない） |
| `MVV_PACES` | `src/ndf/progress/model.py` | `contracts/workflow/context.schema.json` の `$defs/mvv_pace/enum`（下の項） |
| `SIZES` | `src/ndf/domains/gws/thumbnails.py` | `contracts/schemas/io/gws.result.schema.json` の `metrics.size` の `enum` |
| `INTERFACE_VALUES` | `src/ndf/domains/materials/collect.py` | `contracts/schemas/io/materials.result.schema.json` の `public_interface.value` の `enum` から `NOT_STATED`（`記載なし`）を除いたもの。書き出しで当てるので、長い順に並べる（`変わらない` を `変わる` より先に。今の並びと同じ） |
| `CHOOSERS` | `src/ndf/contracts/chosen.py` | `contracts/schemas/route-chosen.schema.json` の `chooser` の `enum`。`SCRIPT`・`CONDUCTOR`・`HUMAN` の 3 つの名前は `StrEnum` の members にする（決定 7）。`tests/contracts/test_chosen.py:54` の一致の縛りは、写しが無くなるので消す |

**`MVV_PACES` は `pace` の語彙の部分集合で、`pace` の `enum` 全体からは作らない。** `MVV_PACES` は「承認ゲートを MVV 判定で省ける進め方」（`fast`・`auto`）で、`normal` は含まない（`sprint_decide.closing_pace` と `verify_needed` が読む）。部分集合を契約で 1 か所に持つため、`context.schema.json` の `pace` を次の形に変える。

```json
{
  "$defs": {
    "plain_pace": {"enum": ["normal"], "description": "利用者の承認でゲートを通る進め方"},
    "mvv_pace": {"enum": ["fast", "auto"], "description": "承認ゲートを MVV 判定で省ける進め方"}
  },
  "properties": {
    "pace": {"anyOf": [{"$ref": "#/$defs/plain_pace"}, {"$ref": "#/$defs/mvv_pace"}], "description": "進め方。モード判定が書く"}
  }
}
```

`pace` の語彙全体を読む `src/ndf/domains/loop/cli.py:164`（`["properties"]["pace"]["enum"]`）は、`$defs` の 2 つの `enum` をつないだ並びへ変える。

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `MVV_PACES` | `("fast", "auto")` を `$defs/mvv_pace` から読む | `normal` を含まない・`closing_pace("normal")` が `normal`・`verify_needed("normal", True, True, True)` が偽 | — |
| `KIND_LABELS` | 今の 3 つの見出し（`回答待ち`・`承認待ち`・`完了`）を `oneOf` の `title` から読む | 合成したスキーマへ `title` の無い `{"const": "later"}` を足すと、通知の本文の見出しが `later` になる（止まらない） | — |
| `load_schema` | 同じスキーマを定数の初期化と検証で読んでも、ファイルの読み込みと解析が 1 回 | — | — |
| `CHOOSERS` | スキーマの `chooser` の `enum` と同じ並び | 合成したスキーマへ値を 1 つ足すと `CHOOSERS` も増える | — |
| `context.schema.json` | `pace` が `normal`・`fast`・`auto` の文脈が通る | — | `pace` が語彙の外（通らない） |

テストのファイル: `tests/contracts/test_chosen.py`（一致の縛りを消し、スキーマだけを触るテストへ）・`tests/domains/loop/test_loop_sprint_decide.py`（`normal` の 2 件を足す）・`tests/adapters/test_notify_sender.py`

## `const-fact` の照合の拡張

満たす決定: 決定 2・8、受け入れ条件 #3667 の 4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `lit` | `src/ndf/checks/facts.py` | 変えない | `lit(node: ast.expr) -> object` | 辞書の定数はキーの集まり（`frozenset`）を返す。今のまま |
| `dict_values` | `src/ndf/checks/facts.py` | 新設 | `dict_values(node: ast.expr) -> frozenset[str] \| None` | 辞書の定数の値の集まり。値がすべて文字列で 2 つ以上のときだけ返す |
| `consts` | `src/ndf/checks/facts.py` | 変更 | `consts(tree: ast.Module, rel: str) -> list[Const]`（変えない） | 辞書の定数から、キーの `Const` に加えて値の `Const` も作る |
| `enum_sets` | `src/ndf/checks/facts.py` | 新設 | `enum_sets() -> dict[str, frozenset[str]]` | 手書きのスキーマの `enum` の集まり（スキーマのパスと JSON Pointer をキーにする） |
| `limit_numbers` | `src/ndf/checks/facts.py` | 新設 | `limit_numbers() -> dict[float, list[str]]` | 上限の設定の数と、その数を持つ方針の名前 |
| `_enum_dups` | `src/ndf/checks/facts.py` | 新設 | `_enum_dups(sets: list[Const]) -> list[Finding]` | スキーマの `enum` と同じか部分集合の定数を指摘にする |
| `_limit_dups` | `src/ndf/checks/facts.py` | 新設 | `_limit_dups(nums: list[Const]) -> list[Finding]` | 上限の設定の数と同じで、名前が上限・回数・待ちを表す定数を指摘にする |

- `lit`・`consts`
  - 方針: `Const.value` に組を載せない。既存の `groups`（`same-set`・`subset`・`member`）と `table_hits`（`table-set`）は `isinstance(c.value, frozenset)` で集まりを拾うので、組を載せるとキーの検査まで消える
  - 事後条件（`consts`）: 辞書の定数 `NAME = {...}` は、キーの集まりを持つ `Const(name="NAME")`（今のまま）と、`dict_values` が返す値の集まりを持つ `Const(name="NAME.values")` の 2 つになる。どちらも `frozenset` なので、既存の集合の照合へそのまま入る。値に文字列でない要素が混ざる辞書は値の `Const` を作らない（キーの `Const` だけ。今の振る舞い）
- `enum_sets`
  - 事前条件: 生成されるスキーマ（`*.args.schema.json`）を除く
  - 事後条件: 要素が 2 つ以上の `enum` だけを返す（1 つの `enum` は定数と重なっても事実の重複と言えない）
  - 副作用: 無し（契約の読み手を通して読むので、1 プロセス 1 回）
- `_limit_dups`
  - 事後条件: 次の 3 つをすべて満たす定数だけを指摘する。数が偶然そろっただけの定数を落とさないため、名前と値の一致だけでは指摘しない
    1. 名前が `rules.json` の `facts.limit_kinds` のどれかの語に当たる
    2. 当たった語に対応する方針の値のキー（下の表）を持つ方針の、そのキーの値と等しい（ほかのキーの値とは照らさない。`ATTEMPT = 3` は `attempts` だけと照らし、`seconds: 3` とは照らさない）
    3. `rules.json` の `facts.limit_constants` に載っていない
  - 定数のまま置く値（決定 8 の外部の仕様の値と実装の都合の値）は、`facts.limit_constants` に `{"name": "<モジュール>.<定数>", "kind": "external" | "implementation", "reason": "<理由>"}` で 1 つずつ載せる。名前の型や値の範囲でまとめて外す書き方は持たない（広い除外で方針の写しが通るのを防ぐ）。`reason` は 1 字以上を必須とする。この表が #3667 の受け入れ条件 3 の「分けた一覧」の正本で、実装の PR の本文はこの表を写す
  - 載せた定数が無くなった・照合に当たらなくなった行は `stale` の指摘にする（除外が残り続けて、後から足した同名の写しを通さないため）
  - 語と方針の値のキーの対応（`facts.limit_kinds`）:

    | 名前の語 | 照らす値のキー |
    | --- | --- |
    | `TIMEOUT`・`WAIT`・`SECONDS`・`GRACE` | `seconds`・`timeout`・`deadline` |
    | `INTERVAL` | `interval`・`max_interval` |
    | `MAX`・`LIMIT`・`CAP`・`KEEP`・`BUDGET` | `count` |
    | `RETRY`・`ATTEMPT` | `attempts` |
    | `DAYS` | `count`（上限の設定は日数を `count` で持つ） |

  - 今の除外の例（実装の単位 3 で全件を載せる）:

    | 定数 | 照合に当たる方針 | 種類 | 理由 |
    | --- | --- | --- | --- |
    | `ndf.domains.relay.account_login.POLL_INTERVAL`（0.1） | `procs.stop-tree`・`procs.stop-test` の `interval`（0.1） | `implementation` | ログインの締め切りまで条件を見直す間隔で、プロセスを止める猶予の方針とは別の事実。値が同じなのは偶然 |
    | `ndf.domains.gws.thumbnails.CURL_TIMEOUT_EXIT`（28） | 名前の語 `TIMEOUT` が当たる方針のうち値 28 を持つもの（あれば） | `external` | curl の終了コード（curl の仕様）。変えると判定が壊れる |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `enum_sets` | スキーマを読めない | ファイルが無い・JSON が壊れている | `ndf.contracts.load_json` | 呼び出し元へ返す | `ndf check` が `stopped`・`config_broken`・終了コード 2 | 直してから打ち直す |
| `limit_numbers` | 上限の設定を読めない | 同上 | `ndf.contracts.limits.table` | 同上 | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `facts.limit_kinds` | 設定値（上限・回数・待ちを表す名前の語と、照らす方針の値のキー） | `src/ndf/checks/rules.json` | 上の「語と方針の値のキーの対応」の表 |
| `facts.limit_constants` | 設定値（定数のまま置く値の 1 つずつの除外と理由） | 同上 | 上の「今の除外の例」を含む全件（実装の単位 3 で測る） |
| `facts.generated_schemas` | 設定値（照合から外すスキーマ） | 同上 | `["schemas/io/*.args.schema.json"]` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `consts`・`dict_values` | 辞書の定数から、キーの `Const` と `NAME.values` の `Const` の 2 つができる | 値に数が混ざる辞書（キーの `Const` だけ） | キーが `None` を含む辞書（キーの `Const` を作らない。今のまま） |
| `groups`・`table_hits`（変えない関数の退行） | 辞書のキーを別のファイルの集まりへ写した定数に、今までどおり `same-set` と `table-set` の指摘が出る | 辞書の値だけが別のファイルの集まりと同じ（`same-set` 1 件。名前は `NAME.values`） | — |
| `_enum_dups` | `enum` と同じ集まりの定数で 1 件、部分集合で 1 件 | 要素 2 つの `enum`（照合する）・1 つ（照合しない）・関係の無い集まり（0 件） | スキーマを読めないときに例外 |
| `_limit_dups` | 合成した上限の設定の方針の `seconds`（例: 1,800）と同じ値を持つ `X_TIMEOUT` で 1 件 | 名前が当たらない定数（0 件）・同じ数の表示の行数（0 件）・語に対応しないキーだけが同じ値（`ATTEMPTS = 3` と `seconds: 3`。0 件）・実例の組 `POLL_INTERVAL = 0.1` と `procs.stop-tree`・`procs.stop-test` の `interval: 0.1` を `facts.limit_constants` に載せた設定（0 件）・同じ組で除外を消した設定（1 件） | 上限の設定を読めないときに例外・除外の行の定数が無い（`stale` 1 件）・`reason` が空の除外（設定の検証で止まる） |

テストのファイル: `tests/checks/test_facts_enum.py`・`test_facts_limits.py`・`test_facts_dict.py`（新設）

## 内部データの型と版（`contracts/internal.py`）

満たす決定: 決定 9・10・11、受け入れ条件 #2339 の 1・3・5

### 種類ごとの入口

要求の 5 種類を、実際に読み書きしている関数へ当てる。核（`contracts/internal.py`）を通すのはこの表の入口だけで、入口の外に古い形の分岐を書かない（決定 10）。

| 種類 | 核での種類の名前 | スキーマ | 書く入口 | 読む入口 |
| --- | --- | --- | --- | --- |
| 記録（使用量） | `usage` | `internal/usage.schema.json`（新設） | `domains/records/jsonl.py` の `append`・`append_locked`（呼び手は `domains/records/usage.py:80・92`） | `jsonl.read_rows`（呼び手は `usage.py:77`（`record_subagent` の差分の計算。錠の内側で読み、形はその `agent_id` の行だけ確かめる）・`usage.py:110`・`domains/relay/context_report.py:57`） |
| 記録（MVV の兆候・検証） | `mvv-signals`・`mvv-validation` | `internal/mvv-signals.schema.json`・`internal/mvv-validation.schema.json`（新設） | `jsonl.append_locked`（呼び手は `domains/records/mvv.py:122・142`） | `jsonl.read_rows`（`mvv.py:90・135`） |
| 記録（文脈の記録） | `context-records` | `internal/context-records.schema.json`（新設） | 書き手のモジュール（実装の単位 4 で `jsonl` へ寄せる） | `jsonl.read_rows`（`domains/relay/context_records.py:168`） |
| 実行の状態（開発ループ） | `loop-event` | `internal/loop-event.schema.json`（新設） | `domains/loop/store.py` の `LoopStore.append`。`version-shipped` のイベントを組む 2 か所（`control_decide.py:284` の出荷の取り込みと `:321` の明示の出荷）はどちらも `version=` を `product_version=` に変える。旧い欄 `version`（文字列）を `product_version` へ移すのは、既存の行を読む `migrate` だけで、書き手に読み替えを置かない | `LoopStore.read`（核の後に今の `record_decide.check` を通す） |
| 実行（工程の文脈） | `context` | `workflow/context.schema.json`（既存。`version` を形の版に、製品の版を `product_version` へ） | 下の「工程の文脈の版」 | 同左 |
| 実行（工程の入出力） | — | `workflow/stage-io.schema.json`（既存。`release-result` の `outputs.version` を `product_version` へ） | 版を足さない。工程の入出力は 1 回の実行の中で書いて読む値で、版をまたいで残らない | 同左 |
| プラン | — | `workflow/parallel-plan.schema.json`（既存の `version: const 1`） | 人が書く | `progress/plan/__init__.py` の `read_doc`。今の版より大きい `version` は `precondition` で止める（今はスキーマの `const` で `invalid` になる） |
| 工程の表 | — | `workflow/progress.schema.json`（既存の `version: const 1`） | 契約の一部（人が書く） | `progress/model.py` の `require_schema`（変えない） |
| 結果 | `result` | `schemas/result.schema.json`（任意の `version` を足す） | `contracts/result.py` の `emit`（下の「結果の版と、形の違う結果」） | `progress/invoke.py` の `read_report`（子プロセスの標準出力の最後の行）と `_call_skill`（`invoke.py:780`。Skill の回答）。どちらも `result.errors` で確かめている入口で、版の扱いを下の項に置く |

`jsonl` の 3 つの関数（`read_rows`・`append`・`append_locked`）は種類（`kind`）を必須の引数に持つ。表の呼び手はすべて種類を渡す（`usage.py:77` の読み戻しも `kind="usage"`）。`jsonl.parse_lines` は種類を持たない行の分解だけを残す。今の呼び手（`usage.py:51` の `transcript_usage` と `relay/context_records.py:267`）は Claude の会話ログ（外部の形）を読むので、核を通さない。

**`read_rows` の不合格の行の扱い: 止めずに、理由つきで数えて返し、呼び手が結果に出す。** 追記だけの記録（使用量・MVV・文脈の記録）は、1 行の欠けで集計や判断の全体を止めると、これまで動いていた読み出しが止まる。一方、数えるだけで呼び手が捨てると、壊れた行が黙って欠ける。そこで次のように分ける。

| 行の状態 | `read_rows` の扱い |
| --- | --- |
| JSON として壊れている・辞書でない（書きかけを含む） | 除いて、`Broken(line, reason)` を返す（今の `broken` の数を、行番号と理由の並びにする。MVV 判定の再利用が古い判定へさかのぼらないよう、前にある読めた行の数 `before` と辞書として読めた中身 `raw` も持つ） |
| `read_record` が `InternalError`（形に合わない・項目の欠け・時刻の書式） | 同上（理由は `InternalError` の文） |
| `read_record` が `precondition`（今の版より新しい版） | 止める（呼び手へ伝える）。新しい NDF が書いた記録を古い NDF が部分的に読んで判断しない |
| 知らない種類（`config_broken`） | 止める（呼び手の綴りの誤り） |

`read_rows` の戻り値は `tuple[list[dict[str, Any]], list[Broken]]` にする。呼び手は、除いた行を結果 JSON に出す: 使用量の集計（`usage.py:110`）は今の `broken_rows` に数を、文脈の記録（`context_records.py:168`）は今の `skipped` に足し（集計と較正の両方）、MVV（`mvv.py:90・135`）と使用量の差分（`usage.py:77`）は今は捨てているので、結果の `items` の警告（`{"kind": "broken_rows", "path": ..., "lines": [...]}`）に出す。

### 工程の文脈の版

工程の文脈は 1 つのファイルではなく、作成の時点の値（ステートマシンの文書の `context`）に、最新の改訂の `outputs_recorded` のイベントの `outputs` を順に重ねて作る。重ねる所が今 2 つある（`progress/run.py:105` の `View.context` と `progress/command.py:489` の `machine_context`）。

- **形の版は、作成の時点の値にだけ持たせる。** `_prepare_new`（`progress/command.py:539`）が `version: 1` を書く。`outputs_recorded` の `outputs` は定義した出力の部分の辞書で、形の版を持たせない（`invoke.finished` の `value_errors`（`invoke.py:277`）に渡るのも部分の辞書で、そこへ版を足さない）
- **重ねる入口を 1 つにする。** `progress/stages.py` に `compose_context(initial: Mapping[str, Any], outputs: Iterable[Mapping[str, Any]]) -> dict[str, Any]` を置き、`View.context` と `machine_context` の 2 つはこれを呼ぶ。`outputs_recorded` を書く 3 つの書き手（通常の実行と `ndf progress finish` が通る `stages.py:289`、ゲートの回答の `gate/answering.py:122・188・300`）はどれも同じイベントを書くので、読む側の 1 か所で受ければ全経路が通る
- **`compose_context` の順序:** (1) 作成の時点の値を `internal.read_record("context", initial)` に通す。版 0（`version` が無いか文字列）は文字列の `version` を `product_version` へ移して版 1 にし、今の版より大きい整数の版は `precondition` の `InternalError` で止める（新しい NDF が保存した文脈を古い NDF が版 1 として読んで工程を再開しない）。(2) 各 `outputs` を古い順に重ねる前に、文字列の `version`（旧 `deploy` の出力）を `product_version` へ移す。(3) 最後に `version` を (1) で確かめた版で置き直す（出力が形の版を上書きしない）。この順序なので、更新の前に出荷した既存のスプリントを再開しても `release_record` は `product_version` で真になる
- **`precondition` の伝え方:** `compose_context` の `InternalError` は 2 つの呼び手（`View.context` と `machine_context`）のどちらも捕まえずに上へ通し、`ndf progress` の各サブコマンドの入口が今の例外の表で `stopped`・`precondition`・終了コード 3（「新しい版の文脈を古い NDF が読んだ。プラグインを更新する」）にする。2 つの呼び手に別々の受け方を書かない
- **保存済みの工程の定義（スナップショット）も移す:** `read_current`（`progress/command.py:395`）は、作成の時点に保存した工程の定義を `stages.snapshot_from` で復元する。配布する `stages/*.json` を直しても、既存のスプリントの `StageDef` の `inputs`・`outputs` は `version` のまま残り、`release-verification` に文脈の形の版 `1` が渡る。そこでスナップショットを内部データの種類 `stage-snapshot` とし、`snapshot_from` の前に `read_record("stage-snapshot", raw)` を通す。スナップショット自身の `version` は工程の定義の文書の版の文字列（`"1.0.0"`。`definition.py:382`）なので、版 0 の見分けは文脈と同じ（整数でなければ版 0）。版 0 → 1 の移し方: スナップショットの文字列の `version` を `definition_version` へ移し、各工程の `inputs`・`outputs` の `"version"` を `"product_version"` に置き換え、`version: 1` を足す。`snapshot_dict` は版 1 の形で書く（`definition_version` と `version: 1`）
- **工程の定義の `version` の点検:** `contracts/workflow/stages/*.json` の `metadata.ndf.inputs`・`outputs` の `version` を製品の版として `product_version` へ改名する。今あるのは `deploy.json`（出力）・`release-verification.json`（入力）・`layout-review.json`（入力）の 3 つ。各ファイルの先頭の `"version": "1.0.0"` は工程の定義の書式（Serverless Workflow の文書の版）で、別の意味なので変えない。コードで文脈の `version` を読む所（`progress/command.py:337` の `release_record`・`progress/gate/asking.py:219` のリリースのゲート）も `product_version` へ変える。点検の手段は `git grep -n '"version"' plugins/ndf/contracts/workflow/stages` と `git grep -n 'ctx.get("version")\|context\["version"\]\|"version" in context' plugins/ndf/src`

### リリースの結果の製品の版（出力元から後の工程の入力まで）

製品の版の改名は、文脈と工程の定義だけでなく、版を出す側と結果のスキーマにも当てる。どれかが旧い `version` のままだと、`invoke.finished` が工程の出力の定義に無いキーを捨てる（または結果のスキーマが拒む）ので、出荷の後の工程へ版が渡らない。

| 箇所 | 今 | 変えた後 |
| --- | --- | --- |
| `contracts/schemas/result.schema.json` の `$defs/outputs`（`additionalProperties: false`） | `version`（文字列）を持つ | `version` を消し、`product_version`（文字列）を持つ。残すと形の版と同じ名前になり、消さずに足すだけだと旧い名前の出力が通り続ける |
| `domains/release/cli.py` の `assembled`（101 行）と `cmd_record`（199 行） | `outputs` に `"version"` を出す | `"product_version"` を出す |
| `stages/deploy.json` の出力・`release-verification.json`・`layout-review.json` の入力 | `version` | `product_version`（上の「工程の文脈の版」） |
| 保存済みのスナップショットの入出力 | `version` | `read_record("stage-snapshot", ...)` が移す（同上） |

子プロセスまたは Skill がその場で出した結果は、旧い `version` を `outputs` に持つなら受けない（結果のスキーマが拒み、`bad_report` になる）。出す側と読む側の版がそろうためである。ただし結果は 1 回限りの値ではなく、プランの状態の保存先にも残り、プランの再開と `ref: version` が更新の後に読む。旧い名前が残る保存済みの値と、今の形へ移す所は次のとおり。

| 保存済みの値 | 移す所 |
| --- | --- |
| `outputs_recorded` | `compose_context` |
| スナップショット | `read_record("stage-snapshot", ...)` |
| プランの出力の記録 `outputs.json` | `core/plan/records.py` の `_stored_outputs`（`recorded_outputs`・`_add_outputs` が通す） |
| プランの結果のバックアップ `results/<回>.json` の `outputs` | `core/plan/runner.py` の `stored_result`（`_stored`・`RepoSources.plan_record`・`invoke._previous`・`ndf plan wait` が通す） |
| 工程の入力の写し `values.json` の `inputs` | `contracts/worker_kinds.py` の `values_of`（`read_values`・`ndf answer work` の `values_in` が通す） |

後の 3 つの移し方は `internal.stage_values` だけが持つ。プランの出力と工程の入力は形の版を持たず、`version` を持てば旧い形と見る（`product_version` もあればそちらを残す）。プランの参照（`refs`）が明示した `key: "version"` は、`refs._output` が同じ対応（`internal.stage_value_key`）で `product_version` を引く。

`stored_result` は、移す前に保存済みの結果の形の版を確かめる。今の版より大きければ `InternalError`（`precondition`）を投げ、`routes` も `outputs` も返さない（プラグインを戻した後に、新しい版の `routes` で後続のステップを流さない）。`ndf plan next` は `ndf.cli` が、`ndf plan wait` は `_watched` が、参照は `RepoSources.plan_record`（`Unreadable`）が、工程は `invoke.plan_failure` が `precondition` の停止にする。

確かめ（単体テスト）: `cmd_record` の結果 → `invoke.finished`（`deploy` の出力の定義で拾う）→ `outputs_recorded` → `compose_context` → `release-verification` の入力の解決、を 1 本の流れで通し、入力に `product_version` の文字列が渡ることを見る。旧いスナップショット（入力が `version`）と旧い `deploy` の出力を持つ、出荷済みのスプリントを再開した場合も同じ値が渡ることを見る。

### 結果の版と、形の違う結果

- **書く側（`result.emit`）:** 今は形が違うと標準エラーへ書いて終了コードだけを返し、結果 JSON を出さない。呼び手（工程を流す `invoke`）は結果の欠けを `bad_report` にするので、受け入れ条件 #2339 の 1 の「書かずに `stopped` を返す」にならない。`emit` は形が違うとき、元の結果を出さずに、代わりの `stopped` の結果（`tool` は固定の `ndf`・`cause.kind` は `invalid`。元の `tool` は形が合っても秘密を含みうるので引き継がない。`result.refused_newer` の代わりの結果も同じ）を 1 行で出し、`invalid` の終了コードを返す。**代わりの結果の `detail` には、確かめた値も `ValidationError.message` も入れない。** `schema_errors` の文は値やオブジェクト全体を含みうるので、それを標準出力へ移すと、元の結果を出さない措置が効かず、不正な結果に混ざった秘密や利用者のデータが工程や閲覧者へ渡る。`detail` は固定の文「結果 JSON の形が違う（<件数> 件）」に、誤りごとのスキーマの側の場所（`absolute_schema_path`。スキーマのキーと番号だけで値を含まない）と規則の名前（`validator`。例 `required`・`enum`）を足して組む。値を含む誤りの文は、今と同じく標準エラーにだけ書く。代わりの結果も形が合わない（`causes` の表が読めない）ときだけ、今の `emit_unchecked` の経路で出す
- **読む側（プランのステップ。`core/plan/steps.py` の `last_json`）:** 部分的な結果 JSON も受けるので `result.errors` では確かめず、今の版より大きい `version` の結果だけを代わりの `stopped`・`precondition`（`result.refused_newer`）に替える。`outputs`・`routes` を使う前に替わるので、後続のステップへ進まない。ステップの結果に `newer` を付け、`transition_decide` がプランの `on_cause`・`on_fail`・`retry` より前にそのステップで止める（ふつうの `precondition` と同じルートに載せると、`end`・別のステップ・打ち直しへ進む）。代わりの `stopped` は `cause.instance` に `result-newer`（`result.NEWER_INSTANCE`）を持ち、ステップのコマンドの中（`ndf worker run`・別のプランの `ndf plan next`）で受けて止まった結果も同じ値を持つので、`steps.outcome_of` はそれも `newer` と見る（`result.stopped_by_newer`）
- **読む側（worker の結果ファイル。`core/worker.py` の `_deliver`）:** `status`・`outputs`・`routes` を読む前に今の版より大きい `version` を見て、`precondition`（`cause.instance` は `result-newer`）で止める。探した記録の欠けでの再起動（`_missing_record`）もしない
- **読む側（`invoke.read_report`・`_call_skill`）:** 結果の `version` が無ければ版 1、1 なら今の版として `result.errors` で確かめる（今と同じ）。今の版より大きい版は形の違い（`bad_report`）と分け、`stopped`・`precondition`（「新しい版の結果を古い NDF が受け取った。プラグインを更新する」）の `StageResult` にする。整数でない `version` は形の違いとして今の `bad_report` に入れる

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_record` | `src/ndf/contracts/internal.py` | 新設 | `read_record(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]` | 1 行を読み、古い版は移行してから種類のスキーマで確かめる |
| `write_record` | `src/ndf/contracts/internal.py` | 新設 | `write_record(kind: str, row: Mapping[str, Any]) -> dict[str, Any]` | 書く前に今の版の形で確かめ、`version` と `at` を埋めて返す |
| `migrate` | `src/ndf/contracts/internal.py` | 新設 | `migrate(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]` | 古い版の行を今の版の形へ移す（種類ごとに 1 か所） |
| `readable_version` | `src/ndf/contracts/internal.py` | 新設 | `readable_version(kind: str, raw: Mapping[str, Any]) -> int` | 行の版だけを返す（移さず、形も確かめない。新しい版なら止める）。対象外の行を読み飛ばす `jsonl.read_rows` が使う |
| `stamp`・`epoch` | `src/ndf/contracts/internal.py` | 新設 | `stamp(seconds: float) -> str`・`epoch(at: str) -> float` | 秒（`time.time()`）と `at` の文字列（UTC・マイクロ秒まで）を行き来する |

- `read_record`
  - 事前条件: `kind` は上の表の「核での種類の名前」のどれか
  - 事後条件: 返る行は今の版の形。**版の見分け: `version` が整数ならその版、`version` が無いか整数でない（文字列の製品の版）なら版 0（旧形）** として `migrate` を通す。`mvv-signals` は旧形の `version` も整数（MVV の設定の版）なので、`mvv_version` の欄が無い行を版 0 とする。版 0 は「版を持つ前の形」で、今の版 1 とは別の版である
  - 副作用: 無し
- `write_record`
  - 事前条件: `row` の欄は、その種類のスキーマの `properties` に載る名前だけ（スキーマは `additionalProperties: false`）
  - 事後条件: 形に合わない行は書かずに `InternalError` を投げる（呼び出し元が `stopped` にする）。`version` を今の版で埋め、`at` が無ければ UTC の今の時刻を埋める
  - 副作用: 無し（ファイルへ書くのは呼び出し元）
- `migrate`（版 0 → 1）: 種類ごとの移し方を下の表に置く。どの種類も最後に `version: 1` を足す

| 種類 | 実際の版 0 の行（サンプル） | 移し方 | 版 1 の行 |
| --- | --- | --- | --- |
| `loop-event` | `{"at": 1795400000.123456, "kind": "gate-answered", "lap": 2, "pr": 4301, "answer": "approved", "head": "abc", "note": ""}` | `at` が数なら `stamp(at)` で文字列にする。`status` は足さない（イベントは結果を持たない） | `{"version": 1, "at": "2026-11-23T02:13:20.123456+00:00", "kind": "gate-answered", ...}` |
| `loop-event`（出荷） | `{"at": 1795400000.0, "kind": "version-shipped", "lap": 2, "version": "11.0.23"}` | 上に加え、文字列の `version` を `product_version` へ移す（形の版と衝突させない） | `{"version": 1, "at": "...", "kind": "version-shipped", "lap": 2, "product_version": "11.0.23"}` |
| `loop-event`（貸し出し） | `{"at": 1795400000.0, "kind": "lease-taken", "session": "s1", "until": 1795400600.0}` | `at` だけを直す。`until` は書いた時刻ではなく期限（名前の対応表で別の意味）なので秒のまま | `{"version": 1, "at": "...", "kind": "lease-taken", "session": "s1", "until": 1795400600.0}` |
| `usage` | `{"at": "2026-11-23T01:00:00+00:00", "caller": "claude-cli", "usage": {...}}` | `version` を足すだけ（`at` は既に `jsonl.now()` の UTC の文字列） | `{"version": 1, ...}` |
| `mvv-signals` | `{"at": "...", "kind": "override_pass", "version": 2, "repo": "...", "state": "...", "gate": "design", "pr": 1}` | `version` は MVV の設定の版（整数か null。製品の版ではない）なので、`mvv_version` へ移す（無ければ null） | `{"version": 1, "at": "...", "kind": "override_pass", "mvv_version": 2, "repo": "...", "state": "...", "gate": "design", "pr": 1}` |
| `mvv-validation`・`context-records` | 今の書き手の行 | `version` を足すだけ（時刻の欄が `at` でない行は、名前の対応表の消した別名から `at` へ移す） | 同左 |
| `context` | `{"pace": "auto", "version": "11.0.23", ...}` | 文字列の `version` を `product_version` へ移す。`version` が無い文脈はそのまま版 1 にする（未出荷のまま。`release_record` は `product_version` を見るので満たさない） | `{"version": 1, "pace": "auto", "product_version": "11.0.23", ...}` |

- 読み手の変更: `record_decide` の `float(ev["at"])`（`record_decide.py:221・240・242`）は `epoch(ev["at"])` へ、`rec.event(kind, at: float, ...)` は `at` を `stamp(at)` で書く。`until` を読む `float(ev["until"])` は変えない。`record_decide` の `version-shipped` の要るキー（`VERSION_SHIPPED: ("lap", "version")`）と `ev["version"]` の読み（`record_decide.py:224`・`control_decide.py:286`）は `product_version` へ変える。時刻を秒へ直して比べるので、`stamp` はマイクロ秒まで残す（同じ秒の 2 つのイベントの前後を保つ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_record` | 形に合わない行 | 項目の欠け・型の違い | `read_record` | 呼び出し元へ返す（`InternalError`）。`jsonl.read_rows` から呼ばれたときは `read_rows` が受けて、その行を `Broken` に入れて読み進める（上の「`read_rows` の不合格の行の扱い」）。`LoopStore.read` から呼ばれたときは今の `StoreError` と同じく止める（開発ループのイベントは 1 件欠けると状態を再生できない） | `read_rows` の経路は結果の `broken_rows` か警告に行番号が出る。`LoopStore.read` の経路は `stopped`・`invalid`・終了コード 2 | 行を直してから打ち直す |
| `read_record` | 知らない種類 | `kind` の綴りの誤り | 同上 | 止める | `stopped`・`config_broken`・終了コード 2 | 綴りを直す |
| `write_record` | 形に合わない行 | スキーマに無い欄・型の違い・時刻の書式 | `write_record` | 書かずに呼び出し元へ返す | `stopped`・`invalid`・終了コード 2 | 呼び出し元を直す |
| `migrate` | 移せない版 | 今の版より新しい版 | `migrate` | 止める（新しい版を古い読み手が読まない。#1706 と同じ止まり方だが、理由を「新しい版の記録を古い NDF が読んだ」と出す） | `stopped`・`precondition`・終了コード 3 | プラグインを更新する |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `read_record` | `version` が無いか整数でない（`mvv-signals` は `mvv_version` の欄が無い） | 版 0 として `migrate` を通し、版 1 のスキーマで確かめる |
| `read_record` | `version` が今の版 | そのままスキーマで確かめる |
| `read_record` | `version` が 1 以上で今の版より小さい | `migrate` を通してから確かめる（今の版は 1 なので、この経路は版 2 を足したときに初めて通る。合成したスキーマでテストする） |
| `read_record` | `version` が今の版より大きい | `precondition` で止める |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VERSION` | 定数（今の版） | `src/ndf/contracts/internal.py` | 1 |
| 書式の検査器 | 関数（`date-time` の検査。`datetime.fromisoformat` で読めて時差が 0） | `src/ndf/contracts/internal.py` | `jsonschema.FormatChecker()` に登録し、核の検証器だけに渡す（`ndf.contracts.schema_errors` は変えない） |
| 読める版 | データ | `contracts/schemas/internal/<種類>.schema.json` の `properties.version.enum` | `[1]` |
| 名前の対応表 | データ | `contracts/data/internal-names.json` | 時刻は `at`、結果は `status`、形の版は `version`、製品の版は `product_version`、MVV の設定の版は `mvv_version`、工程の定義の文書の版は `definition_version`（別の意味）、期限は `until`（別の意味） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read_record` | 今の版の行をそのまま返す | 上の移し方の表のサンプルの 7 行が、それぞれ右の列の版 1 の行になる（数の `at`・文字列の `version`・`status` を持たないイベント・`until` を秒のまま） | 今の版 + 1 の行（`precondition`）・知らない種類（`config_broken`） |
| `write_record` | `version` と `at` を埋めて返す | `at` を呼び出し元が渡した行（上書きしない）・`+00:00` の時刻（通る）・`status` を持たない種類の行（通る） | スキーマに無い欄を持つ行（書かずに `InternalError`）・固有の欄の型の違い（同）・時差の無い時刻・`+09:00` の時刻・UTC の接尾辞を持つ不正な日付（`2026-02-30T00:00:00Z`。書式の検査器で落ちる） |
| `migrate` | 合成した版 1 → 2 の移行で項目が移る（今の版は 1 なので、移行の経路は合成したスキーマで確かめる） | 移す項目が無い版（そのまま） | 新しい版（止まる） |
| `jsonl.read_rows` | 合う行だけを返し、`Broken` が空 | 書きかけの最後の行（`Broken` 1 件・行番号つき）・項目の欠けた行（`Broken` 1 件・止まらない） | 今の版より新しい版の行（止まる） |
| `compose_context` | 作成の時点の値に出力を重ね、`version` が 1 | 文字列の `version` を持つ旧い作成の時点の値と旧い `deploy` の出力（どちらも `product_version` へ移り、`release_record` が真）・出力に `version` が無い通常の工程（形の版が 1 のまま） | — |
| `result.emit` | 合う結果をそのまま出す | — | 形の違う結果で、代わりの `stopped`（`invalid`）が 1 行出て終了コード 2・形の違う値に秘密に見立てた文字列（`"sk-test-secret"`）を入れた結果で、標準出力の行にその文字列が無い |
| `compose_context` の版 | — | — | 作成の時点の値が `version: 2`（`precondition` で止まり、`View.context` と `machine_context` のどちらも工程を再開しない） |
| `read_record("stage-snapshot")` | 版 1 のスナップショットをそのまま返す | 入力が `version` の旧いスナップショット（`product_version` へ移り、文字列の `version` は `definition_version` へ） | `version: 2`（`precondition`） |
| `invoke.read_report` | `version` の無い結果を版 1 として受ける | `version: 1` | `version: 2` の結果（`stopped`・`precondition`。`bad_report` にしない）・`version: "1"`（`bad_report`） |
| `stamp`・`epoch` | `epoch(stamp(t)) == t`（マイクロ秒まで） | 同じ秒の 2 つの時刻の前後が保たれる | 時差つきの文字列を `epoch` に渡す（`InternalError`） |
| 開発ループの再生 | 版 0 のイベントだけの `record.jsonl` と、版 0 と版 1 が混ざった `record.jsonl` から、今と同じ状態（回答の時刻・停止の時刻・出した版）が再生できる | 出荷のイベントの `product_version` が `released` に入る | — |
| 条件 `release_record` | `product_version` を持つ文脈で真 | 形の版 `version: 1` だけを持つ未出荷の文脈で偽 | — |

テストのファイル: `tests/contracts/test_internal.py`（新設）・`tests/domains/loop/` の再生のテスト（版 0 のイベントを足す）・`tests/progress/` の条件のテスト

## 名前の対応表の規則（`internal-names`）

満たす決定: 決定 11、受け入れ条件 #2339 の 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `allowed` | `src/ndf/checks/internal_names.py` | 新設 | `allowed() -> dict[str, frozenset[str]]` | 種類ごとに、使ってよい名前の集まり |
| `check` | `src/ndf/checks/internal_names.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 内部データのスキーマと書き手が、対応表に無い別名を使っていれば指摘にする（既存の規則と同じ入口の形） |

- `check`
  - 事前条件: 見るのは内部データのスキーマ（`contracts/schemas/internal/`）と、`write_record` を呼ぶモジュール
  - 事後条件: 対応表の `where` にその種類が無い名前だけを指摘する（意味の違う名前は、対応表に別の意味の行として載せる）
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `allowed` | 対応表を読めない | ファイルが無い・JSON が壊れている | `ndf.contracts.load_json` | 止める | `ndf check` が `stopped`・`config_broken`・終了コード 2 | 直してから打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `allowed` | 種類ごとの名前の集まりを返す | `where` が 1 つの種類だけの名前 | 対応表を読めないときに例外 |
| `check` | 対応表どおりなら 0 件 | 別の意味として載っている名前（0 件）・`where` にその種類が無い名前（1 件） | 内部データのスキーマが 1 つも無いとき 0 件 |

テストのファイル: `tests/checks/test_internal_names.py`（新設）

## 設計レビューの項目の行き先（規則 `review-items`）

満たす決定: 決定 1（正本の使われ方を機械で確かめる）、受け入れ条件 #3684 の 7（対応表の 3395-2・要求 332）

**#1142 の設計は「子の表のコピーの `review_items` は、項目の表と入力の Issue の本文から生成する（手で写さない）」と決めたが、11.0.0 では入らなかった。** 今の `src/ndf/checks/children.json` に `review_items` のキーは無く、規則も無い（`python3 -c` で読んで確かめた）。そのため、項目に行き先があるかを誰も確かめていない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `review_items` | `src/ndf/checks/spec_render.py` | 新設 | `review_items(source: Path, columns: Mapping[str, str]) -> list[dict[str, Any]]` | 項目の表（`issues/issue-1142-design-review-routing.md`）の行から、項目ごとの行き先の並びを組み立てる |
| `check` | `src/ndf/checks/spec_docs.py` | 変更 | `check(scope, rules, write_spec, written)`（変えない） | 生成した `review_items` と、`children.json` に書かれた中身の差分を指摘にする。行き先の無い項目も指摘にする |

- `review_items`
  - 事前条件: 項目の表が「列の読み方」の 7 列（項目・要旨・行き先の子・順番・新設か追補か・要求の行・版）を持つ
  - 事後条件: 1 項目につき `{"id", "text_sha", "routed_to", "order", "kind", "requirement_line", "release"}` の 1 行（項目の配列）。`text_sha` は表の要旨のセル（前後の空白を除いた UTF-8）の sha256 の 16 進の先頭 12 文字で、Issue の本文は使わない。`routed_to` が空か仮の `—` の項目は、値を空のまま返す（落とすのは `check` の役目）
  - 副作用: 無し（表を読むだけ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `review_items` | 項目の表を読めない | ファイルが無い・列が足りない | `review_items` | 止める | `ndf check` が `stopped`・`config_broken`・終了コード 2 | 表を直してから打ち直す |
| `check` | 行き先の無い項目がある | 表の「行き先の子」の列が空 | `spec_docs.check` | 違反にする（項目の番号を挙げる） | `ndf check` が `stopped`・`check_failed`・終了コード 1 | 表に行き先を書いてから打ち直す |
| `check` | 11.x へ回した項目の対応表の行き先が、子の記号（`Z<数>`）・`#<Issue 番号>`・`作らない` のどれでもない | 対応表の「11.x の行き先」の列が空・仮の `—`・別の語 | 同上 | 違反にする（項目の番号を挙げる） | 同上 | 対応表に行き先を書いてから打ち直す |
| `check` | 生成物と `children.json` が食い違う | 手で写した・表を直して生成し直していない | 同上 | 違反にする | 同上 | `ndf check --write-spec` で生成し直す |
| `check` | 項目の表・対応表・`children.json`・書き込み先がリポジトリ（プラグイン）の外を指すか、シンボリックリンクを通る | リンクの書き換え | `model.contained` | 止める（読み書きしない） | `ndf check` が `stopped`・`config_broken`・終了コード 2 | リンクを直してから打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `spec.review_items_source` | 設定値（項目の表のパス） | `src/ndf/checks/rules.json` | `issues/issue-1142-design-review-routing.md` |
| `review_items` | データ（生成物） | `src/ndf/checks/children.json` | 生成する（手で書かない） |

**項目の表と対応表はリリース対象の外（`issues/`）にあるため、ワークフローの `REVIEW_ITEMS_PATHS` に置き、表だけを変える PR では `ndf check --only review-items` だけを打つ。** 置かないと CI が表の変更で動かず、`NDF_PATHS` へ足すと表だけの PR で全体の検査が走る。`--only review-items` だけのときは、項目の表・対応表・`children.json` だけを読む。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_items` | 合成した表から項目ごとの行を組み立てる | 行き先が 2 つ以上の項目（`・` で並ぶ。先頭が持ち主）・版が `11.x` の項目（行き先は対応表を指す） | 列が足りない表（止まる） |
| `check` | 生成物と `children.json` が同じなら 0 件 | 行き先が空の項目 1 件（1 件の違反）・`children.json` に `review_items` が無い（1 件の違反） | 表を読めないとき（止まる） |

テストのファイル: `tests/checks/test_review_items.py`（新設）

## 結果 JSON の `items` の欄

満たす決定: 決定 6、受け入れ条件 #3684 の 5

既存の関数のシグネチャは変えない。`contracts/schemas/result.schema.json` の `items.items` に `properties` を足す。`severity` の語彙は、重さの表（`contracts/domains/severities.json`）の `id` から組み立てる。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| 結果のスキーマ | 4 つの欄を持つ結果 JSON が通る・重さの表の 4 つ（`critical`・`major`・`minor`・`nit`）のどれを `severity` に持つ項目も通る・既存の cross-review の結果 JSON（`severity` 付きの `items`）をそのまま通す | 欄を 1 つも持たない結果 JSON（通る）・`items` が空（通る） | `severity` が語彙の外（`blocker`・`info`。通らない）・`acceptance` が文字列（並びでない。通らない） |

テストのファイル: `tests/contracts/test_result_items.py`（新設）

## 退行を止める確かめ

満たす決定: —、受け入れ条件 #3684 の 2・3

既存の振る舞いを縛るだけで、関数は足さない。

| 確かめ | 振る舞い | テストのファイル |
| --- | --- | --- |
| 契約の読み手の検証が 1 回 | `limits.table()` を 3 回呼んでも検証の関数の呼び出しが 1 回 | `tests/contracts/test_limits_cached.py`（新設） |
| 生成物の差分が落ちる | `docs/contracts/` の生成物を 1 文字変えると `spec-docs` の指摘が出る | 既存の `tests/checks/` の規約の規則のテストへ 1 件足す |
