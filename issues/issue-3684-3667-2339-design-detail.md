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
│   └── schemas/
│       ├── limits.schema.json                read_by を必須にする
│       ├── result.schema.json                items の要素へ任意の欄を 4 つ
│       └── internal/
│           ├── record.schema.json           * 記録の 1 行の形と版
│           └── state.schema.json            * 実行の状態の形と版
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
│   │   └── internal.py                      * 内部データの読み書きの核
│   ├── adapters/notify.py                    KIND_LABELS をスキーマから
│   ├── progress/model.py                     MVV_PACES をスキーマから
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
│   ├── domains/records/__init__.py           記録の書き込みを核へ通す
│   └── domains/loop/store.py                 実行の状態の読み書きを核へ通す
├── skills/refactoring/SKILL.md               値の置き場の基準
└── tests/                                    下の「単体テストの観点」の表のファイル
```

依存の向き: 新しい規則（`checks/`）は `ndf.contracts` の読み手と `checks/model.py` の `Finding` だけを読み、互いを読まない。

**規則のモジュールが持つ入口の関数は、既にある規則と同じ `check(scope, rules)` にそろえる。** 既存の `structure.py:76`・`facts.py:231` と同じ形で、`run.py` が規則ごとに同じ形で呼ぶ。この同名は役割が同じことを示すものなので、規則 `copy-dup` の対象から外す（`rules.json` の `copy_dup.allow_names` に `check` を置く）。`ratchet.py` は規則を読まず、規則の名前と件数だけを受け取る。内部データの核（`contracts/internal.py`）は `ndf.contracts` の層にあり、`domains/` の書き手がそれを読む。層の向きは下から `ndf.contracts`・`ndf.adapters`・`ndf.core`・`ndf.progress`・`ndf.domains` で、規則 `layer` が守る。型の関係は設計文書の「構成要素」の表にあり、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/checks/limits_readers.py` | 新設 | 上限の設定の読み手の宣言と、実際に方針を引く import を突き合わせる | `ndf.contracts.limits`、`checks.model` | モジュール | `declared`・`actual`・`check`（新設） |
| `src/ndf/checks/copy_dup.py` | 新設 | 2 つ以上のファイルの同じ名前の最上位の関数と、4 つ以上のファイルに出る文字列を数える | `checks.model` | モジュール | `top_functions`・`repeated_strings`・`check`（新設） |
| `src/ndf/checks/internal_names.py` | 新設 | 内部データのスキーマと書き手が、名前の対応表に無い別名を使っていないかを見る | `ndf.contracts`、`checks.model` | モジュール | `allowed`・`check`（新設） |
| `src/ndf/checks/ratchet.py` | 新設 | 規則ごとの基準の数を読み、超えた規則を違反にし、下回ったら基準を書き直す | `ndf.contracts`（読み込みのみ） | モジュール | `baseline`・`verdicts`・`lower`（新設） |
| `src/ndf/checks/facts.py` | 変更 | `const-fact` の照合の相手に、スキーマの `enum`・上限の設定の数・辞書の値を足す | `ndf.contracts.limits`、`ndf.contracts.facts` | モジュール | `lit`（変更）・`enum_sets`・`limit_numbers`・`_enum_dups`・`_limit_dups`（新設） |
| `src/ndf/contracts/internal.py` | 新設 | 内部データを種類ごとのスキーマで読み書きし、古い版を移行の関数で受ける | `ndf.contracts` | モジュール | `read_record`・`write_record`・`migrate`・`VERSION`（新設） |
| `src/ndf/checks/run.py` | 変更 | 新しい規則を規則の並びへ足し、ラチェットのある規則の件数を照らす | 上の 4 つ | モジュール | `_rules`（変更） |
| `src/ndf/core/worker.py` | 変更 | worker の上限を上限の設定の新しい方針 `supervise.worker-run`（3,600 秒。値は今の `WORK_TIMEOUT` と同じ）から読む | `ndf.contracts.limits` | モジュール | `WORK_TIMEOUT`（削除）・`work_timeout`（新設） |

## 上限の設定の読み手の宣言（規則 `limit-readers`）

満たす決定: 決定 1、受け入れ条件 #3684 の 1・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `declared` | `src/ndf/checks/limits_readers.py` | 新設 | `declared() -> dict[str, frozenset[str]]` | 上限の設定の方針ごとの、宣言された読み手のモジュールの名前 |
| `actual` | `src/ndf/checks/limits_readers.py` | 新設 | `actual(trees: Mapping[str, ast.Module]) -> dict[str, frozenset[str]]` | 構文木から、方針の名前を引く呼び出しを探し、方針ごとに引いたモジュールの名前を集める |
| `check` | `src/ndf/checks/limits_readers.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 宣言と実際の食い違いを指摘にする（既存の規則と同じ入口の形） |

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
    4. 属性（`machine.CAP_POLICY`）・import した名前: import の先のモジュールの最上位の代入を 2.・3. で解く（1 段だけ。先がさらに別のモジュールを指すときは解けないものにする）
    5. 上のどれでも解けない（引数・関数の戻り値・f 文字列）: 「動的な読み口」の宣言で解く（次の項）
  - 動的な読み口: 名前を実行時に組み立てる読み手は、`rules.json` の `limits.dynamic` に `{"module": "<モジュール>", "template": "<名前の型>", "vocabulary": "<契約のファイル>#<JSON Pointer>", "reason": "<理由>"}` を宣言する。`template` の `{}` に `vocabulary` の値を 1 つずつ入れた名前だけを引いたものとして数える（接頭辞で始まる方針をすべて数えることはしない）。今の実例は `ndf.domains.hooks.budget` の 2 つの型で、`hook.budget.{}` の語彙は `contracts/hooks/runtimes.json` の各ランタイムの `events.*.budget`、`hook.budget.{}.{}` は同じ語彙とランタイムの名前の組である。`budget.py` は `tables.policies()` の辞書を名前で引くので、上の表の関数を通らない
  - 解けない読み口: 宣言の無い動的な読み口は `unresolved` の指摘にする（保存先は呼び出しの行）。黙って数えない・黙って捨てる、のどちらもしない
  - 副作用: 無し（構文木を読むだけ）
- `check`
  - 事後条件: 指摘は 3 種類で、宣言した読み手が引いていない方針（`unread`）と、宣言の無い方針を引いているモジュール（`undeclared`）と、解けない読み口（`unresolved`）。どれも方針の名前（解けたとき）とモジュールの名前を理由に含む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `declared` | 上限の設定を読めない | ファイルが無い・JSON が壊れている・`read_by` がスキーマに合わない | `ndf.contracts.limits.table` | 呼び出し元へ返す（`LimitsError`） | `ndf check` が `stopped`・`cause.kind` は `config_broken`・終了コード 2 | 直してから打ち直す |
| `actual` | 構文木を読めない | 構文の誤り | `ndf check` の木を作る所（既存） | 呼び出し元へ返す | `ndf check` が `stopped`・`invalid`・終了コード 2 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 宣言した読み手が、その方針を引く呼び出しを持たない | `unread` の指摘（保存先は上限の設定のその方針の行） |
| `check` | あるモジュールが方針を引くが、その方針の `read_by` にそのモジュールが無い | `undeclared` の指摘（保存先は呼び出しの行） |
| `check` | 読み口の引数が解けず、`limits.dynamic` にそのモジュールの宣言も無い | `unresolved` の指摘（保存先は呼び出しの行） |
| `check` | 宣言と実際が一致する | 指摘なし |

- 繰り返しの終わる条件: 方針の数（223）× 読み手の数で有限
- 計算量の注意: 動的な読み口の名前は、語彙から組み立てた名前を集合にして方針の表と突き合わせる。方針ごとに全ファイルを走査しない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `read_by` | データ（方針の項目） | `contracts/limits.json` | 無し（必須） |
| `limits.readers` | 設定値（読み口の関数の完全な名前と、方針の名前の引数の位置） | `src/ndf/checks/rules.json` | 上の「読み口」の表の 6 行 |
| `limits.dynamic` | 設定値（動的な読み口の宣言） | 同上 | 上の「動的な読み口」の `ndf.domains.hooks.budget` の 2 行 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `actual` | `src/ndf/checks/imports.py`（規則 `layer`・`cycle` が使う import の収集） | `grep -n "def " src/ndf/checks/imports.py` | 使う（モジュールの名前への変換を再利用し、呼び出しの引数の読み取りだけを足す） |
| `declared` | `ndf.contracts.limits.table` | `src/ndf/contracts/limits.py` を読んだ | 使う（`Policy` へ `read_by` を足す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `declared` | 方針ごとに宣言の集まりを返す | `read_by` が 1 つだけの方針 | 上限の設定を読めないときに `LimitsError` |
| `actual` | 実コードの呼び出しの形をそのまま写した合成のモジュールで集める: `limits.seconds(LOCK_POLICY)`（最上位の定数）・`limits.value(*POLICY)`（組の定数）・`limits_mod.value("handoff.lines", "count")`（別名の import）・`from ndf.contracts.limits import seconds` の `seconds("x")`・`limits.from_env(env, "N", "p", "seconds")`（3 番目）・`tables.policy_value("hook.pushback", "attempts")` | `ndf.testing.limits` を `limits_mod` の別名で読むモジュール（集めない）・`machine.CAP_POLICY` のように別のモジュールの定数を指す引数（1 段だけ解く） | 構文の誤りで木が無いファイルは渡されない |
| `actual`（動的な読み口） | 宣言した語彙の値から組み立てた名前だけを集める | 語彙に無い名前の方針 `hook.budget.unused` を足した設定（集めない。`check` で `unread`） | 宣言の無い f 文字列の読み口（集めずに `unresolved`） |
| `check` | 一致すれば 0 件 | 宣言が 1 つで実際が 0 件（`unread` 1 件） | 宣言の無い方針を引くモジュール（`undeclared` 1 件）・解けない読み口（`unresolved` 1 件） |

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
| `verdicts` | `src/ndf/checks/ratchet.py` | 新設 | `verdicts(counts: Mapping[str, int]) -> list[str]` | 基準を超えた規則の名前と数を文にする（空なら通る） |
| `lower` | `src/ndf/checks/ratchet.py` | 新設 | `lower(counts: Mapping[str, int], commit: str, day: str) -> list[str]` | 基準を下回った規則の基準を書き直し、書いた規則の名前を返す |

- `top_functions`
  - 事前条件: `trees` は `src/` の下の `.py` だけ
  - 事後条件: 名前が `_` で始まる関数（モジュールの内側の助けの関数）も数える。1 つの名前につき 1 件で、辞書順の先頭のファイルの行に付ける
  - 副作用: 無し
- `repeated_strings`
  - 事後条件: docstring を除く。4 ファイル以上に出る文字列 1 つにつき 1 件
- `lower`
  - 事前条件: `ndf check --write-spec` のときだけ呼ばれる（検査だけのときは基準を書き換えない）。CI は `--write-spec` を付けずに打つため、基準が下がるのは手元で打ち直して生成物をコミットしたときである。減らした変更がそのまま通るのは `verdicts` が基準以下を通すためで、基準の数が下がるのは次に `--write-spec` を打ったときになる
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
| `verdicts` | 基準と同じ件数なら通る | 基準 + 1（落ちる）・基準 − 1（通る） | 基準に無い規則の件数（止まる） |
| `lower` | 下回った規則の基準が今の件数になる | 下回った規則が 0 個（書かない） | 書けないときに例外 |

テストのファイル: `tests/checks/test_copy_dup.py`・`tests/checks/test_ratchet.py`（新設）

## `const-fact` の照合の拡張

満たす決定: 決定 2・8、受け入れ条件 #3667 の 4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `lit` | `src/ndf/checks/facts.py` | 変更 | `lit(node: ast.expr) -> object`（変えない） | 辞書の定数で、キーだけでなく値の集まりも読む |
| `enum_sets` | `src/ndf/checks/facts.py` | 新設 | `enum_sets() -> dict[str, frozenset[str]]` | 手書きのスキーマの `enum` の集まり（スキーマのパスと JSON Pointer をキーにする） |
| `limit_numbers` | `src/ndf/checks/facts.py` | 新設 | `limit_numbers() -> dict[float, list[str]]` | 上限の設定の数と、その数を持つ方針の名前 |
| `_enum_dups` | `src/ndf/checks/facts.py` | 新設 | `_enum_dups(sets: list[Const]) -> list[Finding]` | スキーマの `enum` と同じか部分集合の定数を指摘にする |
| `_limit_dups` | `src/ndf/checks/facts.py` | 新設 | `_limit_dups(nums: list[Const]) -> list[Finding]` | 上限の設定の数と同じで、名前が上限・回数・待ちを表す定数を指摘にする |

- `lit`
  - 事後条件: 辞書の定数は、キーの集まりと値の集まりの両方を返す（`frozenset` の組）。値に文字列でない要素が混ざる辞書はキーだけを返す（今の振る舞い）
- `enum_sets`
  - 事前条件: 生成されるスキーマ（`*.args.schema.json`）を除く
  - 事後条件: 要素が 2 つ以上の `enum` だけを返す（1 つの `enum` は定数と重なっても事実の重複と言えない）
  - 副作用: 無し（契約の読み手を通して読むので、1 プロセス 1 回）
- `_limit_dups`
  - 事後条件: 名前が上限・回数・待ちを表す（`rules.json` の `facts.limit_name_pattern` に当たる）定数だけを照らす。当たらない名前の定数は、同じ数でも指摘しない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `enum_sets` | スキーマを読めない | ファイルが無い・JSON が壊れている | `ndf.contracts.load_json` | 呼び出し元へ返す | `ndf check` が `stopped`・`config_broken`・終了コード 2 | 直してから打ち直す |
| `limit_numbers` | 上限の設定を読めない | 同上 | `ndf.contracts.limits.table` | 同上 | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `facts.limit_name_pattern` | 設定値（上限・回数・待ちを表す名前） | `src/ndf/checks/rules.json` | `MAX\|LIMIT\|TIMEOUT\|WAIT\|RETRY\|DAYS\|SECONDS\|INTERVAL\|ATTEMPT\|GRACE\|KEEP\|BUDGET\|CAP` |
| `facts.generated_schemas` | 設定値（照合から外すスキーマ） | 同上 | `["schemas/io/*.args.schema.json"]` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `lit` | 辞書の定数で値の集まりも返す | 値に数が混ざる辞書（キーだけ） | キーが `None` を含む辞書（`None` を返す。今のまま） |
| `_enum_dups` | `enum` と同じ集まりの定数で 1 件、部分集合で 1 件 | 要素 2 つの `enum`（照合する）・1 つ（照合しない）・関係の無い集まり（0 件） | スキーマを読めないときに例外 |
| `_limit_dups` | 合成した上限の設定の方針の数（例: 1,800）と同じ値を持つ、名前が上限を表す定数で 1 件 | 名前が当たらない定数（0 件）・同じ数の表示の行数（0 件） | 上限の設定を読めないときに例外 |

テストのファイル: `tests/checks/test_facts_enum.py`・`test_facts_limits.py`・`test_facts_dict.py`（新設）

## 内部データの型と版（`contracts/internal.py`）

満たす決定: 決定 9・10・11、受け入れ条件 #2339 の 1・3・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_record` | `src/ndf/contracts/internal.py` | 新設 | `read_record(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]` | 1 行を読み、古い版は移行してから種類のスキーマで確かめる |
| `write_record` | `src/ndf/contracts/internal.py` | 新設 | `write_record(kind: str, row: Mapping[str, Any]) -> dict[str, Any]` | 書く前に今の版の形で確かめ、`version` と `at` を埋めて返す |
| `migrate` | `src/ndf/contracts/internal.py` | 新設 | `migrate(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]` | 古い版の行を今の版の形へ移す（種類ごとに 1 か所） |

- `read_record`
  - 事前条件: `kind` は `contracts/schemas/internal/` にスキーマを持つ種類
  - 事後条件: 返る行は今の版の形。`version` を持たない行は版 1 として移行する
  - 副作用: 無し
- `write_record`
  - 事前条件: `row` は名前の対応表に載る名前だけを持つ
  - 事後条件: 形に合わない行は書かずに `InternalError` を投げる（呼び出し元が `stopped` にする）。`at` が無ければ UTC の今の時刻を埋める
  - 副作用: 無し（ファイルへ書くのは呼び出し元）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_record` | 形に合わない行 | 知らない版・項目の欠け | `read_record` | 呼び出し元へ返す（`InternalError`） | 呼び出し元のサブコマンドが `stopped`・`invalid`・終了コード 2 | 行を直してから打ち直す |
| `read_record` | 知らない種類 | `kind` の綴りの誤り | 同上 | 止める | `stopped`・`config_broken`・終了コード 2 | 綴りを直す |
| `write_record` | 形に合わない行 | 名前の対応表に無い名前・型の違い | `write_record` | 書かずに呼び出し元へ返す | `stopped`・`invalid`・終了コード 2 | 呼び出し元を直す |
| `migrate` | 移せない版 | 今の版より新しい版 | `migrate` | 止める（新しい版を古い読み手が読まない。#1706 と同じ止まり方だが、理由を「新しい版の記録を古い NDF が読んだ」と出す） | `stopped`・`precondition`・終了コード 3 | プラグインを更新する |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `read_record` | `version` が無い | 版 1 として `migrate` を通す |
| `read_record` | `version` が今の版 | そのままスキーマで確かめる |
| `read_record` | `version` が今の版より小さい | `migrate` を通してから確かめる（今の版は 1 なので、この経路は版 2 を足したときに初めて通る。合成したスキーマでテストする） |
| `read_record` | `version` が今の版より大きい | `precondition` で止める |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VERSION` | 定数（今の版） | `src/ndf/contracts/internal.py` | 1 |
| 読める版 | データ | `contracts/schemas/internal/<種類>.schema.json` の `properties.version.enum` | `[1]` |
| 名前の対応表 | データ | `contracts/data/internal-names.json` | 時刻は `at`、結果は `status` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read_record` | 今の版の行をそのまま返す | `version` の無い行（版 1 として読む） | 今の版 + 1 の行（`precondition`）・知らない種類（`config_broken`） |
| `write_record` | `version` と `at` を埋めて返す | `at` を呼び出し元が渡した行（上書きしない） | 対応表に無い名前を持つ行（書かずに `InternalError`）・時差の無い時刻 |
| `migrate` | 合成した版 1 → 2 の移行で項目が移る（今の版は 1 なので、移行の経路は合成したスキーマで確かめる） | 移す項目が無い版（そのまま） | 新しい版（止まる） |

テストのファイル: `tests/contracts/test_internal.py`（新設）

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

## 結果 JSON の `items` の欄

満たす決定: 決定 6、受け入れ条件 #3684 の 5

既存の関数のシグネチャは変えない。`contracts/schemas/result.schema.json` の `items.items` に `properties` を足す。`severity` の語彙は、重さの表（`contracts/domains/severities.json`）の `id` から組み立てる。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| 結果のスキーマ | 4 つの欄を持つ結果 JSON が通る | 欄を 1 つも持たない結果 JSON（通る）・`items` が空（通る） | `severity` が語彙の外（通らない）・`acceptance` が文字列（並びでない。通らない） |

テストのファイル: `tests/contracts/test_result_items.py`（新設）

## 退行を止める確かめ

満たす決定: —、受け入れ条件 #3684 の 2・3

既存の振る舞いを縛るだけで、関数は足さない。

| 確かめ | 振る舞い | テストのファイル |
| --- | --- | --- |
| 契約の読み手の検証が 1 回 | `limits.table()` を 3 回呼んでも検証の関数の呼び出しが 1 回 | `tests/contracts/test_limits_cached.py`（新設） |
| 生成物の差分が落ちる | `docs/contracts/` の生成物を 1 文字変えると `spec-docs` の指摘が出る | 既存の `tests/checks/` の規約の規則のテストへ 1 件足す |
