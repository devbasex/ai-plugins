# #3908 の詳細設計: 工程のプランが作業のプロンプトを組んで worker を起動する

設計文書 [issue-3908-design.md](./issue-3908-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── templates/params.json                        差し込み worker の既定
│   ├── worker/kinds.json *                          作業の名前ごとの定義（唯一の基準）
│   ├── worker/prompt.md *                           起動指示の共通のテンプレート
│   └── workflow/context.schema.json                 文脈のキーごとの x-source
├── docs/contracts/commands.md                       サブコマンドの仕様（生成物）
├── src/ndf/progress/
│   ├── invoke.py                                    values.json を書く・差し込みを先に確かめる
│   ├── run.py                                       started の前に確かめて止まる
│   ├── command.py                                   ndf progress check の結果
│   ├── definition.py                                欠けた文脈のキーに取り場所を添える
│   └── gate/entry.py                                プロンプトを組んで書き、worker へ置き換わる
└── tests/progress/
    ├── test_invoke.py                               values.json と決められない差し込み
    ├── test_sprint_templates.py                     worker の既定と退行
    └── gate/test_gate_work.py                       プロンプトの組み立て
```

依存の向き: `contracts/`（データ）→ `progress/invoke.py`（工程がプランを組む層）→ `progress/gate/entry.py`（プランのステップが打つサブコマンド）の一方向で、逆向きの import は足さない。`progress/gate/entry.py` は `values.json` の読み手として `progress/invoke.py` を読み（今も `from ndf.progress import command, invoke, model, run, store` で読んでいる）、パスは `ndf.contracts.worker_paths.report_paths`（#3877）で組む。`core/plan`（プランの実行の層）は触らない。`progress/run.py` は `progress/invoke.py` を呼ぶ既存の向きのままである。循環は無い。要素どうしの順序は設計文書の「処理の流れ」にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/contracts/templates/params.json` | 変更 | 差し込み `worker` に既定を持たせる | 無し（データ） | `params` | 無し |
| `plugins/ndf/contracts/worker/kinds.json` | 新設 | 作業の名前 → 作業種別・作業の 1 行・読む Skill・手順の抜粋・読む差し込み | 無し（データ） | `kinds` | 無し |
| `plugins/ndf/contracts/worker/prompt.md` | 新設 | 起動指示の 7 項目と規則 7 個の共通のテンプレート | 無し（データ） | 無し | 無し |
| `plugins/ndf/contracts/workflow/context.schema.json` | 変更 | 文脈のキーごとに値の取り場所（`x-source`）を持つ | 無し（データ） | `properties` | 無し |
| `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | プランを書き出すときに差し込みの値を残し、決められない差し込みを先に返す | `ndf.core.route`・`ndf.core.plan.files` | モジュール `ndf.progress.invoke` | `write_values`（新設）・`read_values`（新設）・`missing_values`（新設）・`plan_path`（変更） |
| `plugins/ndf/src/ndf/progress/run.py` | 変更 | `started` を書く前に差し込みを確かめる | `ndf.progress.invoke` | モジュール `ndf.progress.run` | `_start`（変更） |
| `plugins/ndf/src/ndf/progress/definition.py` | 変更 | 欠けた文脈のキーの行に取り場所を添える | `ndf.contracts` | モジュール `ndf.progress.definition` | `context_source`（新設）・`_missing_keys`（変更） |
| `plugins/ndf/src/ndf/progress/command.py` | 変更 | `ndf progress check` の結果に取り場所の付いた行を出す | `ndf.progress.definition` | モジュール `ndf.progress.command` | `_cmd_check`（変更） |
| `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | プロンプトを組んで書き、`ndf worker run` へ置き換わる | `ndf.progress.invoke`・`ndf.contracts.worker_paths`・`ndf.contracts`・`string` | モジュール `ndf.progress.gate.entry` | `work_kinds`（新設）・`work_prompt`（新設）・`_write_prompt`（新設）・`work_argv`（変更）・`work_parser`（変更）・`_cmd_work`（変更） |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf answer work` の節（生成物） | 無し（生成物） | 無し | 無し |
| `plugins/ndf/tests/progress/test_invoke.py`・`test_sprint_templates.py`・`gate/test_gate_work.py` | 変更 | 差し込みの既定・`values.json`・決められない工程・プロンプトの組み立て | `ndf.progress` | モジュール | テスト関数 |

## worker の起動の引数の既定（`params.json`）

満たす決定: 決定 1、受け入れ条件 1、受け入れ条件 5

関数を持たない構成要素である。`params.json` の `worker` に `"default": ["ndf", "answer", "work", "--state-dir", "{state_dir}", "--cwd", "{repo}"]` を足し、`summary` から「既定は持たない」の但し書きと作業の名前の列挙を外して、作業の名前の唯一の基準が `contracts/worker/kinds.json` であることを書く。

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `worker` | 差し込みの定義 | `plugins/ndf/contracts/templates/params.json` | `["ndf", "answer", "work", "--state-dir", "{state_dir}", "--cwd", "{repo}"]`（`list` が真） |

`{state_dir}` と `{repo}` はプランの実行が埋める決まった名前の値で、`core/plan/runner.py:242-250` の `_base_values` が持つ（このファイルは触らない）。既定を持つ差し込みは `invoke._missing`（`src/ndf/progress/invoke.py:578-582`）が「決まらない」に数えないので、`FILL_SOURCES` の `worker` の行は残したまま、17 工程が差し込みで止まらなくなる。`merged.json` と `fix.json` は `worker` を使わないため、クリーンアップの工程は引数が変わらない（受け入れ条件 5）。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `params.json` の `worker` | `standard` のすべてのテンプレートを `route.render` で組むとき、`worker` が差し込みの不足に挙がらない | `worker` を使わない 2 件のテンプレートは引数が変わらない | 既定を消すと、工程「要求と受け入れ条件」の起動が差し込みの不足で止まる |

## 作業ごとの定義（`contracts/worker/kinds.json`）

満たす決定: 決定 3、受け入れ条件 1、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `work_kinds` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 新設 | `work_kinds() -> dict[str, dict[str, Any]]` | `contracts/worker/kinds.json` の `kinds` を返す。`WORK_KINDS` の定数を置き換える |

- `work_kinds`
  - 事前条件: 無し
  - 事後条件: 返るキーは、テンプレートが `{{worker}}` の後ろに置く作業の名前 18 件をすべて含む。値の形は設計文書の「データ構造」にある
  - 副作用: コントラクトのファイルを 1 度読む（`contracts.cached_reader` で回ごとに 1 回）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `work_kinds` | 定義を読めない | ファイルが無い・JSON が壊れている | `contracts.load_json` | 呼び出し元へ返す（`contracts.ContractError`） | `ndf` のエントリポイントが `stopped`（`config_broken`）の結果 JSON を出す（`src/ndf/cli.py` の `main`） | 後始末は無い。プラグインを入れ直せば直る |
| `work_kinds` の呼び手（`work_argv`） | 作業の名前が定義に無い | テンプレートが定義に無い名前を使う・打ち間違い | `work_argv` | 呼び出し元へ返す（`model.Refused`） | `stopped`（`invalid`）。`summary` に名前と、定義にある名前を並べる | ファイルを触らずに返す。名前を直せば打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KINDS_FILE` | 定数 | `plugins/ndf/src/ndf/progress/gate/entry.py` | `"worker/kinds.json"`（`contracts.load_json` に渡す相対パス） |
| 作業の名前 18 件と定義 | データ | `plugins/ndf/contracts/worker/kinds.json` | `impl`・`fix`・`ci-fix`・`fix-base`・`fix-push`・`design`・`plan-to-spec`・`requirements`・`worktree`・`design-detail`・`document-sources`・`implementation-plan`・`quality-gates`・`release`・`release-verification`・`layout-review`・`retrospective`・`backlog-refinement` |

`WORK_KINDS` の定数（`src/ndf/progress/gate/entry.py:77`）は消し、`ndf.progress.gate.__init__` の再公開（`:73`・`:84`）も `work_kinds` へ置き換える。作業の名前を 2 か所に持たない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `work_kinds` | `contracts.load_json` と `contracts.cached_reader`（`src/ndf/contracts/__init__.py:49,68`） | `grep -n "^def " plugins/ndf/src/ndf/contracts/__init__.py` | 使う。コントラクトの表を読む経路はこの 2 つが唯一の保存先で、`route.params`（`core/route/__init__.py:129`）も同じ形を使う |
| `work_kinds` | `route.templates()`・`route.params()` | `grep -n "def templates\|def params" plugins/ndf/src/ndf/core/route/__init__.py` | 使えない。テンプレートと差し込みの定義を読む関数で、作業の名前の定義を持たない。読み方（`cached_reader` + `load_json`）だけ踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `work_kinds` | 18 件のキーが返り、どれも `work`・`summary`・`skills`・`excerpt`・`inputs` を持つ | テンプレートが `{{worker}}` の後ろに置く名前の集合と、キーの集合が一致する（受け入れ条件 1・決定 3） | 定義に無い名前を `ndf answer work` に渡すと `stopped`（`invalid`） |

## 起動指示の共通のテンプレート（`contracts/worker/prompt.md`）

満たす決定: 決定 1、決定 3、決定 6

関数を持たない構成要素である。`agent-layers.md` の「supervisor → worker」が定める 7 項目（作業・入力・手順・読む Skill・返す形・置く場所・作業の結果ファイル）の見出しと、worker が守る規則 7 個を本文に持ち、差し込みだけを `string.Template` の形（`$名前`・`${名前}`）にする。本文に `$` の字を書くときは `$$` で逃がす（決定 6 の代償）。

| 差し込み | 入る値 |
| --- | --- |
| `$work` | 作業種別（`kinds.json` の `work`） |
| `$summary` | 作業の 1 行（`kinds.json` の `summary`） |
| `$inputs` | 作業が読む差し込みの値を 1 行 1 件で並べた字面（`values.json` から組む） |
| `$steps` | 手順の抜粋（`kinds.json` の `excerpt` のファイルの中身。無ければ「手順の抜粋は無い」の 1 行） |
| `$skills` | 読む Skill の名前と `SKILL.md` の絶対パス（無ければ「無し」） |
| `$report`・`$done`・`$result` | 置き場所の 3 つの絶対パス（`contracts.worker_paths.report_paths`。#3877） |

本文に書く 7 項目と規則 7 個の文面は、worker を起動指示で起動するときはこのファイルが、supervisor がサブエージェントで起動するときは `agent-layers.md` が持つ。どちらも同じ 7 項目と規則 7 個である。

## 差し込みの値の受け渡し（`values.json`）

満たす決定: 決定 2、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_values` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `write_values(state_dir: Path, plan: str, values: Mapping[str, Any]) -> None` | プランの名前・差し込みの値・書いた時刻を `<保存先>/values.json` へ書く |
| `read_values` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `read_values(state_dir: Path) -> Optional[dict[str, Any]]` | `<保存先>/values.json` を読む。無い・壊れていれば `None` |
| `plan_path` | `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | `plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path`（変わらない） | 埋めたプランを書き出すとき、同じ保存先へ `values.json` も書く |

- `write_values`
  - 事前条件: `state_dir` が存在する（`plan_path` を呼ぶ `_call_plan` が `work_dir.mkdir(parents=True, exist_ok=True)` を済ませている。`src/ndf/progress/invoke.py:726`）
  - 事後条件: `<保存先>/values.json` が設計文書の「データ構造」の形で存在する。同じ工程を打ち直しても最後の中身になる
  - 副作用: ファイルの書き込み 1 つ
- `read_values`
  - 事前条件: 無し
  - 事後条件: 返る辞書は `plan`・`values`・`at` を持つ。形が違えば `None`
  - 副作用: 無し（読み取りだけ）
- `plan_path`
  - 事後条件: 書き出したプランの `{state_dir}`（= `work_dir`）に `values.json` があり、その `values` がそのプランを組むのに使った値と一致する
  - 副作用: 今までのプランの書き出しに、`values.json` の書き込みが 1 つ増える

書くのは `template_values` の後、`route.render` の前後どちらでもよいが、プランを書き出す行（`out.write_text(...)`）と同じ関数の中に置く。プランと値が同じ呼び出しで保存先へ並ぶので、片方だけが古くなることがない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_values` | 書けない | 保存先の権限・容量（`OSError`） | `files.write_json` | 呼び出し元へ返す（`OSError` をそのまま上げる） | `ndf progress next` が止まる（プランを起動しない） | プランを起動していないので後始末は無い。打ち直せる |
| `plan_path` | 値を残せない | 上の行と同じ | `write_values` の呼び出し | 呼び出し元へ返す（捕まえない） | 同上 | 同上 |
| `read_values` | 読めない・形が違う | ファイルが無い（工程を通さずに手で打ったプラン）・JSON が壊れている | `files.read_json` の返り | 既定値で続ける（`None` を返す） | 呼び手（`_cmd_work`）が `stopped`（`precondition`）にする | 後始末は無い |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VALUES_FILE` | 定数 | `plugins/ndf/src/ndf/progress/invoke.py` | `"values.json"` |

`values` に入るのは `template_values` が返す差し込みの値そのもので、工程の入力・課題の題と本文・リポジトリの設定・打った場所（`route.derived`）・起動の補い（`route.launched`。`acceptance` など）をすべて含む。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ工程の `ndf progress next`（打ち直し・承認の回答） | `<保存先>/values.json` | 取らない（工程の行の排他（`store.run_lock`）の中で 1 つの駆動だけがプランを組む） | 同じ工程・同じ入力なら中身は同じで、何度書いても結果が変わらない |
| プランのステップ（`ndf answer work`） | 同上 | 排他を取らずに読むだけ | 読み取りだけで、読めなければ `stopped`（`precondition`）で止まる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `write_values`・`read_values` | `ndf.core.plan.files.write_json`・`read_json`（`invoke.py` が `plan_files` として既に読んでいる） | `grep -n "plan_files\|def write_json\|def read_json" plugins/ndf/src/ndf/progress/invoke.py plugins/ndf/src/ndf/core/plan/files.py` | 使う。保存先の JSON を読み書きする既存の関数で、`invoke.py` に新しい依存を足さない |
| `write_values`・`read_values` | `ndf.core.plan.records`（`prs.json`・`outputs.json` の読み書き。`src/ndf/core/plan/records.py:17-22`） | `grep -n "^def \|^[A-Z_]* = " plugins/ndf/src/ndf/core/plan/records.py` | 使えない。プランの実行の層が回をまたいで書く記録で、工程がプランを組むときの値を持たない（決定 2 で書き込み元を工程の層にした） |
| `write_values` | `invoke._issue_values` がコピーを置く形（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE` を同じ `work_dir` へ書く。`invoke.py:386-412`） | `grep -n "ISSUE_BODY_FILE\|ISSUE_TITLE_FILE" plugins/ndf/src/ndf/progress/invoke.py` | 使う（置き方を踏襲する）。工程の保存先へ材料を残す形が既にある |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_path` | プランを書き出した保存先に `values.json` があり、`plan`・`values`・`at` を持つ。`values` に `acceptance`・`title`・`body_file` の補いが入る | 値が空の工程でも `values` は空の辞書で書かれる | 保存先が読み取り専用のとき `OSError` が上がり、プランを起動しない |
| `read_values` | 書いた中身をそのまま読める | 打ち直した工程でも最後の値を読める | ファイルが無い・壊れているとき `None` |

## 作業のプロンプトを組んで worker を起動する（`ndf answer work`）

満たす決定: 決定 1、決定 2、決定 3、決定 6、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `work_prompt` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 新設 | `work_prompt(kind: str, values: Mapping[str, Any], paths: worker_paths.ReportPaths) -> str` | 共通のテンプレート（`contracts/worker/prompt.md`）と作業の定義と差し込みの値から、起動指示の本文を組む |
| `_write_prompt` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 新設 | `_write_prompt(kind: str, state_dir: Path) -> Path` | `values.json` を読んで `work_prompt` を呼び、`report_paths(...).prompt`（`<作業名>.prompt.md`）へ書いてそのパスを返す |
| `work_argv` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `work_argv(kind: str, state_dir: Path, cwd: Path) -> list[str]`（変わらない） | 作業の名前を `work_kinds()` で確かめ、プロンプトと結果ファイルのパスを `worker_paths.report_paths` から取る（`WORK_KINDS` の定数と `<作業名>-prompt.md`・`<作業名>-result.json` の字面をやめる） |
| `work_parser` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `work_parser() -> argparse.ArgumentParser`（変わらない） | 作業の名前の説明を `work_kinds()` のキーから組む |
| `_cmd_work` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `_cmd_work(argv: list[str], exec_: Callable[[str, list[str]], Any] = os.execv) -> int` | プロンプトを書いてから `ndf worker run` へ置き換わる |

- `work_prompt`
  - 事前条件: `kind` が `work_kinds()` のキーである。`values` は `values.json` の `values`
  - 事後条件: 返る本文に、起動指示の 7 項目の見出しと規則 7 個がそろう。差し込みは 1 つも残らない
  - 副作用: コントラクトのファイル（テンプレートと手順の抜粋）を読む
- `_write_prompt`
  - 事前条件: `state_dir` に `values.json` がある
  - 事後条件: `<保存先>/<作業名>.prompt.md` が存在し、中身が `work_prompt` の返り
  - 副作用: ファイルの読み取り 1 つと書き込み 1 つ
- `_cmd_work`
  - 事前条件: 今までどおり（`--state-dir`・`--cwd`・作業の名前）
  - 事後条件: プロンプトのファイルが書かれた後に、プロセスが `ndf worker run` へ置き換わる。置き換わる前に戻る経路は失敗のときだけ
  - 副作用: ファイルの書き込みとプロセスの置き換え（`os.execv`）

引数は変わらないが、作業の名前の説明が `work_kinds()` のキーから組まれるため、サブコマンドの仕様の文書（`plugins/ndf/docs/contracts/commands.md`）の `ndf answer work` の節が変わる。この節は `ndf check --write-spec` が生成する。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `work_argv` | 作業の名前が定義に無い | テンプレートの名前と `kinds.json` の食い違い | `work_argv` の先頭 | 呼び出し元へ返す（`model.Refused`） | `stopped`（`invalid`）。今の振る舞いと同じ | ファイルを触らずに返す |
| `work_argv` | 名前がパスの形に合わない | `[A-Za-z0-9._-]{1,64}` でない名前 | `report_paths` が上げる `worker_paths.NameInvalid` | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`invalid`） | ファイルを触らずに返す |
| `_write_prompt` | `values.json` が無い・読めない | 工程を通さずに手で打ったプラン・保存先が違う | `invoke.read_values` の返り | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。`summary` に保存先のパスと理由を書く（決定 2） | 何も書いていない。工程から打ち直せる |
| `work_prompt` | 差し込みの値が足りない | 作業の定義の `inputs` の名前が `values.json` に無い | `string.Template.substitute` の `KeyError` | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。足りない名前を `summary` に書く | 何も書いていない。工程の入力か設定を足して打ち直せる |
| `work_prompt` | 手順の抜粋を読めない | `kinds.json` の `excerpt` のパスが無い | 抜粋の読み取り | 呼び出し元へ返す（`model.Refused`） | `stopped`（`precondition`） | 手順を渡せない worker を起動しない（`core/worker.py` の既存の方針と同じ） |
| `_cmd_work` | プロンプトを書けない | 保存先の権限・容量（`OSError`） | `Path.write_text` | 呼び出し元へ返す | `stopped`（`precondition`） | 置き換えの前に返るので、worker は起動しない |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_work` | 作業の名前が定義にある・`values.json` が読める・差し込みがそろう | プロンプトを書き、`ndf worker run` へ置き換わる |
| `_cmd_work` | 作業の名前が定義に無い | `stopped`（`invalid`）。プロンプトを書かない |
| `_cmd_work` | `values.json` が無い・読めない | `stopped`（`precondition`）。プロンプトを書かない |
| `_cmd_work` | 差し込みが足りない | `stopped`（`precondition`）。プロンプトを書かない |

繰り返しは作業の定義の `inputs` の数（多くて数件）で終わる。計算量の注意は無い。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `PROMPT_FILE` | 定数 | `plugins/ndf/src/ndf/progress/gate/entry.py` | `"worker/prompt.md"`（コントラクトの保存先からの相対パス） |
| 4 つのファイルの名前 | 定数 | `plugins/ndf/src/ndf/contracts/worker_paths.py`（#3877） | `<作業名>.prompt.md`・`<作業名>.report.md`・`<作業名>.report.md.done`・`<作業名>.result.json`。`entry.py` は字面を持たない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_work` | 失敗したときだけ `stopped` の結果 JSON（標準出力 1 行）。成功したときは置き換わるので出力しない | 結果 JSON | プロンプトの本文（長く、課題の本文を含みうる。ファイルにだけ置く）。課題の本文・認証情報 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `work_prompt` | `string.Template`（Python 標準ライブラリ。設計文書の LIB1・決定 6） | 設計文書の「ライブラリとサービス」の表 | 使う。`$名前`・`${名前}` の差し込みで足り、欠けた値は `substitute()` の `KeyError` で止まる |
| `work_prompt` | `core/route/__init__.py` の `_fill`（テンプレートの `{{名前}}` を埋める） | `grep -n "def _fill\|def resolve" plugins/ndf/src/ndf/core/route/__init__.py` | 使えない。プランの JSON の構造（配列の要素の展開）を埋める処理で、Markdown の本文を埋める用途と形が合わない |
| `work_prompt` | `core/worker.py` の `system_prompt`・`skills_section`（`src/ndf/core/worker.py:207-233`） | `grep -n "def system_prompt" -A 10 plugins/ndf/src/ndf/core/worker.py` | 使えない。`ndf worker run` が `claude -p` へ渡す規則文を組む処理で、起動指示の 7 項目を組む処理ではない。読む Skill の本文を載せる側はこちらが持ち続ける |
| `_write_prompt` | `invoke.read_values`（この設計で新設） | 同じ文書の「差し込みの値の受け渡し」 | 使う |
| `_write_prompt` | `contracts.worker_paths.report_paths`（#3877 で新設） | 設計文書の「文脈と制約」（置き場所のパスの決め方は #3877 が持つ） | 使う。プロンプト・報告・完了マーカー・結果ファイルの 4 つはこの関数から取り、別の決め方を作らない。`contracts` の層なので `entry.py:95` の IG1 に触れない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `work_prompt` | 組んだ本文に 7 項目の見出しと規則 7 個がそろう（決定 1） | 読む Skill が無い作業は「無し」、手順の抜粋が無い作業は抜粋の無い本文になる。本文の `$$` が `$` 1 つになる | 差し込みが足りないと `model.Refused` |
| `_cmd_work` | `<保存先>/<作業名>.prompt.md` を書いてから `ndf worker run` の argv へ置き換わる（置き換えは差し替えた `exec_` で確かめる） | 2 回打つとプロンプトを書き直す | `values.json` の無い保存先で `stopped`（`precondition`）になり、置き換えが呼ばれない（決定 2） |
| `work_argv` | 定義にある 18 件の名前すべてで argv を組める | `--prompt-file` と `--result-file` が `report_paths` の返りと一致する（区切りが `.`） | 定義に無い名前で `stopped`（`invalid`）（決定 3） |

## 決められない差し込みを起動の前に見つける

満たす決定: 決定 4、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `missing_values` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `missing_values(run: StageRun, work_dir: Path, cwd: Path) -> dict[str, str]` | 工程のプランの差し込みのうち決まらないものの、名前 → 出所の人が読む文を返す。埋められるなら空 |
| `_start` | `plugins/ndf/src/ndf/progress/run.py` | 変更 | `_start(d: Drive, v: View, s: stages.StageDef) -> Optional[dict[str, Any]]`（変わらない） | 入力の検査（`invoke.resolve_inputs`）の後、`started` を書く前に `missing_values` を呼び、空でなければ書かずに止まる |

- `missing_values`
  - 事前条件: `run` は `invoke.stage_run` が組んだ呼び出しで、`kind` がプラン（`PLAN`）である。プラン以外のステップでは呼ばない
  - 事後条件: 返る辞書が空なら、同じ入力で `plan_path` が `TemplateValuesMissing` を上げない
  - 副作用: 既存の `template_values` と同じ（課題の題と本文の書き出し・リファクタリングの候補の書き出し・`git` と GitHub の読み取り）。`values.json` は書かない（書くのは `plan_path`）。2 回呼んでも同じ値を書き直すだけで、起動の前と後で結果が変わらない
- `_start`
  - 事後条件: 差し込みを決められない工程では、ステートマシンに `started` も `failed` も増えず、行は未着手のままである（受け入れ条件 3）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `missing_values` | 差し込みを決められない | 工程の入力・設定・課題のどこにも値が無い | `template_values` が上げる `TemplateValuesMissing` を捕まえる | 呼び出し元へ返す（例外にせず辞書で返す） | `_start` が `stopped`（`precondition`）。`summary` は「工程 \<名前\> のプラン \<テンプレート\> の差し込み \<名前\>… を決められない」、`items` は差し込みごとの `{"key": 名前, "source": 出所}` | ステートマシンに何も書いていない。値を足して `ndf progress next` を打ち直せる |
| `missing_values` | 課題を読めない | GitHub の不達・認証（`github.GitHubError`） | `template_values` の中 | 呼び出し元へ上げる（捕まえない） | 今までどおり、プランの呼び出しの層（`_call_plan`）と同じ `transient`・`precondition` の扱いにする | 打ち直せる |
| `missing_values` | テンプレートが無い | 工程の定義の `plan` の名前が違う | `template()` の返りが `None` | 空の辞書を返す（起動の層の既存の `precondition` に任せる） | `_call_plan` の今までの結果 | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_start` | 工程の条件が偽 | 今までどおり `skipped` を書く |
| `_start` | 工程の入力が欠ける・形が違う | 今までどおり `stopped`（入力の種類） |
| `_start` | プランのステップがあり、差し込みを決められない | `started` を書かずに `stopped`（`precondition`）。決まらない名前と出所を `items` に並べる |
| `_start` | 上のどれでもない | 今までどおり `started` を書いて起動する |

工程に含まれるプランのステップの数（多くて数件）だけ繰り返す。計算量の注意は無い。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `missing_values` | `invoke.template_values` と `TemplateValuesMissing`（`src/ndf/progress/invoke.py:115-126,432`） | `grep -n "TemplateValuesMissing\|def template_values" plugins/ndf/src/ndf/progress/invoke.py` | 使う。決まらない差し込みの名前 → 出所は例外の `missing` が既に持ち、`items` の形（`{"key", "source"}`）も `items` プロパティが持つ |
| `missing_values` | `invoke._missing`（`:578`）・`FILL_SOURCES`（`:313`） | 同上 | 使う（`template_values` 経由）。直に呼ばない（既定の有無と補いの順序を 2 か所に持たない） |
| `missing_values` | `invoke.resolve_inputs`・`StageInputError`（`:86-112`） | `grep -n "class StageInput\|def resolve_inputs" plugins/ndf/src/ndf/progress/invoke.py` | 使えない。工程の入力が文脈にあるかの検査で、プランの差し込みを見ない。`_start` が止まる形（`kind` と `items` を持つ例外）だけそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `missing_values` | 差し込みのそろう工程で空の辞書 | 手元のテストのステップを外す設定（`test.ci.only`）のとき、そこだけが使う差し込みを求めない | 決まらない工程で、名前 → 出所の辞書が返る |
| `_start` | 決められない工程を `ndf progress next` で起動すると、事象に `started` も `failed` も増えない（受け入れ条件 3） | 決まる工程では今までどおり `started` が 1 件増える | 結果が `stopped`（`precondition`）で、`items` に差し込みごとの名前と出所が並ぶ |

## 出力の値の取り場所（`x-source`）

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `context_source` | `plugins/ndf/src/ndf/progress/definition.py` | 新設 | `context_source(key: str) -> Optional[str]` | 文脈のスキーマのキーの `x-source`（値の取り場所の 1 行）。無ければ `None` |
| `_missing_keys` | `plugins/ndf/src/ndf/progress/definition.py` | 変更 | `_missing_keys(obj: Any, part: str, keys: tuple[str, ...]) -> list[str]`（変わらない） | 欠けたキーの行に取り場所を添える |
| `_cmd_check` | `plugins/ndf/src/ndf/progress/command.py` | 変更 | `_cmd_check(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]`（変わらない） | 取り場所の付いた行を `summary` と `items` に出す |

- `context_source`
  - 事前条件: 無し
  - 事後条件: 文脈のスキーマ（`contracts/workflow/context.schema.json`）の `properties` にあるキーで、`x-source` を持つものはその文を返す
  - 副作用: コントラクトのファイルを 1 度読む（`contracts.load_json`。`definition.CONTEXT` の既存の読み取りと同じ）
- `_cmd_check`
  - 事後条件: 欠けたキーがあるとき、`items` に `{"kind": "missing", "key": <キー>, "source": <取り場所か null>}` が並ぶ。`summary` は今までどおり誤りをつないだ 1 文
  - 副作用: 無し（書かない。今までどおり）

欠けたキーの行は「`outputs: 文脈のキー <キー> が無い（取り場所: <x-source>）`」になり、`x-source` が無いキーでは今までどおり括弧を付けない。この行は `ndf progress check` の `summary`（`src/ndf/progress/command.py:674-684`）と `ndf progress finish` の拒否の理由にそのまま載る。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `context_source` | スキーマを読めない | ファイルが無い・壊れている | `contracts.load_json` | 呼び出し元へ返す（`contracts.ContractError`） | エントリポイントが `stopped`（`config_broken`） | 後始末は無い |
| `context_source` | キーが `x-source` を持たない | スキーマに書き足していないキー | 返り | 既定値で続ける（`None`。今までどおりの行） | 取り場所の無い行 | 後始末は無い |
| `_cmd_check` | 出力が規約に合わない | 工程の出力のキーの欠け・形の違い | `_read_stage_output` | 今までどおり `stopped`（`refused`） | `stopped`（`refused`）。欠けたキーの行に取り場所が付く | 出力を直して打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `X_SOURCE` | 定数 | `plugins/ndf/src/ndf/progress/definition.py` | `"x-source"` |
| キーごとの取り場所 | データ | `plugins/ndf/contracts/workflow/context.schema.json` の各 `properties` | 例: `delivery` は「`.ndf/project.json` の `branch_strategy.release.deliveries` の 1 行に `result` を足す」、`mvv_gate` は「ステートマシンの `release-mvv` のステップの outputs」 |

`x-source` は JSON Schema の語彙に無い接頭辞付きのキーで、検証では無視される（決定 5 の代償）。値の取り場所の唯一の保存先はこのスキーマで、手順書には書かない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `context_source` | `definition._context_keys()`（`src/ndf/progress/definition.py:196`。`contracts.load_json(CONTEXT)["properties"]` を読む） | `grep -n "CONTEXT\b" plugins/ndf/src/ndf/progress/definition.py` | 使う。同じ読み取りの経路を共有し、スキーマを 2 か所から読まない |
| `context_source` | `definition.dig`（`:147`） | 同上 | 使う。入れ子の辞書を安全にたどる既存の関数 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `context_source` | `delivery`・`mvv_gate` で取り場所の文が返る（受け入れ条件 4） | `x-source` を持たないキーで `None` | スキーマに無いキーで `None` |
| `_missing_keys` | 欠けた `delivery` の行に取り場所が添う | 取り場所の無いキーの行は今までどおり | 出力が辞書でないときも今までどおり欠けとして並ぶ |
| `_cmd_check` | 欠けたキーのある出力で `items` に取り場所が載る（受け入れ条件 4） | 欠けが無い出力では `items` が空 | 出力のファイルが読めないときは今までどおり |
