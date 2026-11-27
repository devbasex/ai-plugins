# #4521 の詳細設計: 読み手のある項目だけを測って設定に書く

設計文書 [issue-4521-design.md](./issue-4521-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。以下の「決定 N」は設計文書の決定、「受け入れ条件 N」は要求仕様 [issue-4521-requirements.md](./issue-4521-requirements.md) の番号を指す。新設する関数が 2 つ以上あり、シグネチャを変える関数もあるので、別ファイルにする。

現状の記述は、`main` のコミット `7504f88a3`（NDF 11.0.50。#4580 のマージを含む）のコードに合わせる。実装はこのコミット以降の `main` から始める。

## 地図

パッケージ・モジュール構成（`*` は新設）。この文書のパスの根は、`docs/specifications/` と `.ndf/` を除き `plugins/ndf/` である。

```text
src/ndf/domains/project/
  cli.py  check.py  write.py  measure_repo.py  measure_ci.py
  analysis_record.py  items.py  secret.py
  answer_decide.py *   held.py *
contracts/
  schemas/ndf-settings/project.schema.json
  schemas/internal/project-held-answers.schema.json *
  domains/ndf-files.json  domains/languages.json
  data/internal-names.json
```

依存の向きは `cli → write / check → held → answer_decide → analysis_record / secret / items` の一方向で、循環は無い。`held.py` は `write.py` と `check.py` を import しない。`check.py` が `held.py` を読み、`write.py` が `check.py` を読むので、逆向きの import は循環になる。このため、git の追跡と無視を確かめる処理は、今と同じく `cli.py` に置く。`answer_decide.py` はファイルと git に触れない判断だけを持つ（名前の末尾 `_decide` の決まりを `ndf check` が確かめる）。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/project/measure_repo.py` | 変更 | git の木から `test`・`materials` を測る | `secret`・`items` | `measure_repo` | 変更: `compose_services`・`measure`。新設: `service_names`・`instruction_notes` |
| `src/ndf/domains/project/measure_ci.py` | 変更 | 必須のチェックだけを測る | `measure_repo` | `measure_ci` | 変更: `measure` |
| `src/ndf/domains/project/cli.py` | 変更 | 測定の組み立て、書き込みの実行と表示 | 下の全部 | `cli`・`MeasurementContext` | 変更: `_ci_items`・`measure_project`・`_measure`・`_inputs`・`_write`・`_commit`・`_print_write`。新設: `_ask_context`・`_guard_store`。削除: `_instruction_notes` |
| `src/ndf/domains/project/write.py` | 変更 | 設定の値と、書くファイル・保管の変更の計画を作る（何も書かない） | `check`・`held`・`answer_decide` | `Inputs`・`Plan`・`WriteError` | 変更: `merge_project`・`plan`・`p_number`・`row_line`・`_build_record`・`_project_file`・`_declare_ci_tests`。新設: `probe_inside`・`suites_note`・`removed_by_hand`・`veil_rows`・`check_measure`・`cleanup_plan` |
| `src/ndf/domains/project/answer_decide.py` | 新設 | `current` の形、回答の順序表、回答の秘密の判定 | `analysis_record`・`secret`・`items` | `Case`・`Ruling` | `compare_value`・`current_entry`・`measure_problem`・`answer_tainted`・`judge_answer` |
| `src/ndf/domains/project/held.py` | 新設 | 控えと、保管した回答の読み書き | `answer_decide`・`secret`・`items` | `Event`・`Changes` | `context_of`・`place_of`・`replace_owner_only`・`read_place`・`awaits_resume`・`pick_held`・`plan_changes`・`gone_places`・`store_adds`・`store_drops` |
| `src/ndf/domains/project/analysis_record.py` | 変更 | 旧い形のキー | `items` | `analysis_record` | 変更: `strip_item`・`strip_legacy`。削除: `_old_keys` の配列の呼び出し |
| `src/ndf/domains/project/items.py` | 変更 | 項目の値の共通の形 | — | `items` | 移す: `is_unknown`（`write.py` から） |
| `src/ndf/domains/project/secret.py` | 変更 | 秘密の形の判定 | `adapters.secret_lines` | `secret` | 新設: `shows_secret` |
| `src/ndf/domains/project/check.py` | 変更 | 設定が新しいかの判定 | `held`・`analysis_record` | `check`・`RepoDirs` | 変更: `repo_dirs`・`_stale_reasons`・`judge`。新設: `_held_reasons` |
| `contracts/schemas/ndf-settings/project.schema.json` と `skills/development-workflow/schemas/project.schema.json` | 変更 | 設定の形（後者は `ndf project schema` の生成物） | — | — | — |
| `contracts/schemas/internal/project-held-answers.schema.json` | 新設 | 保管した回答と場所の形 | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | NDF が書くファイルの表 | — | — | — |
| `contracts/domains/languages.json` | 変更 | 要約の文 | — | — | — |
| `contracts/data/internal-names.json` | 変更 | 欄の名前 `at` を使える種類に `project-held-answers` を足す | — | — | — |
| `contracts/schemas/io/project.args.schema.json`・`contracts/schemas/io/project.result.schema.json` | 変更 | 引数の説明（生成物）と、行の `result` の説明 | — | — | — |
| `docs/contracts/ndf-settings.md`・`docs/contracts/commands.md` | 変更 | 生成物（`ndf check --write-spec`） | — | — | — |
| `skills/development-workflow/references/project-analysis.md`、`docs/specifications/ndf-project-analysis-record.md`、`.ndf/project.json` | 変更 | 手順書・確定仕様・このリポジトリの設定 | — | — | — |
| `tests/domains/project/` の `test_project_measure.py`・`test_project_cli.py`・`test_analysis_record.py`・`test_measure_project_branch_state.py`・`test_write_plan.py` | 変更 | 今のテスト | — | — | — |
| `tests/domains/project/test_answer_decide.py`・`tests/domains/project/test_project_held.py` | 新設 | 順序表と、保管・控えのテスト | — | — | — |

## 設定の形の定義とファイルの表

満たす決定: 決定 1・5・8、受け入れ条件 3・4・13

- `project.schema.json` の `properties` から 6 項目を消し、それだけが参照する `$defs`（`Language`・`Services`・`Issues`・`Checks`・`Tool`・`NdfPolicies`・`Instructions`・`Import`）を消す。最上位の `description` から言語と課題の保存先を外す
- `ndf-files.json`: 行 `project` の `keys` から 6 項目を外す。行を 2 つ足す。どちらも `location: analysis`・`kind: record`・`tracked: false`・`ends: kept` である

| `id` | `path` | `writer` |
| --- | --- | --- |
| `project-held` | `held` | 解析（M7） |
| `project-removed` | `removed` | 解析（M7） |

- `project-held-answers.schema.json`: 回答 1 件の形（設計の決定 8 の「保管する中身」の欄。すべて必須で、ほかの欄は許さない）を本体にし、`context.json` の形（`root`・`branch`・`commit`・`worktree`）を `$defs/Place` に置く
- `languages.json` は要約（`summary`）の「言語の計測・」だけを消す

## リポジトリと CI の測定

満たす決定: 決定 2・3、受け入れ条件 2・3・5・6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `compose_services` | `measure_repo.py` | 変更 | `(text: str) -> list[str]` | `services:` の直下の名前を返す（image は読まない） |
| `service_names` | `measure_repo.py` | 新設 | `(tree: Tree) -> list[str]` | 根と 1 段下の compose のファイルのサービス名を、重複なく整列して返す |
| `instruction_notes` | `measure_repo.py` | 新設 | `(tree: Tree) -> list[str]` | 指示書の案内（古い案内のマーカー、指示書が無い）を、今の `measure_instructions` の `notes` と同じ文と順で返す |
| `measure` | `measure_repo.py` | 変更 | `(tree: Tree, step: Step, items: dict) -> list[str]` | `test`・`materials` を `items` へ入れ、指示書の注記を返す |
| `measure` | `measure_ci.py` | 変更 | `(tree, repo, head, deadline) -> dict`（返すキーは `ci`・`notes`） | 必須のチェックだけを測る |
| `_ci_items` | `cli.py` | 変更 | `(tree, step, ctx) -> list[str]` | `ci` の項目を入れ、CI の注記だけを返す |

- `measure`（`measure_repo.py`）
  - 事後条件: `items` に `test`・`materials` のほかの項目の値が無い。依存・サービス名・指示書の確かめが失敗しても残りを測る
  - 手順の名前（`step` のキー）は `dependencies`・`services`・`instructions` で、失敗の記録は `items` に入っても、`cli.measure_project` が `write.ITEM_KEYS` で絞るので結果に出ない
- `measure_project` の注記の順は、指示書の注記、CI の注記、開かなかった秘密の名前のファイル、の順で今と同じである
- `MeasurementContext` から `repo_issues` を消す

消すもの（読み手が無くなる）: `LANG_EXT`・`FRAMEWORKS`・`DB_WORDS`・`CHECK_TOOLS`・`PYPROJECT_TOOLS`・`POLICY_WORDS`・`_major`・`_first_number`・`_versions`・`measure_languages`・`_workflow_services`・`measure_services`・`measure_issues_repo`・`url_hosts`・`measure_checks`・`measure_policies`・`measure_instructions`・`measure_ci._measure_merges`・`measure_ci.measure_issues`・`cli._instruction_notes`。`measure_ci` の `Gh` と `GhUnavailable` は `strategy_measure.py` も使うので残す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `service_names`・`instruction_notes` | 読み取りの失敗・時間切れ | git の失敗、締め切り | `cli._Steps` | 既定値で続ける（空） | 出力に出ない。終了コード 0 | 打ち直せる |
| `measure`（`measure_ci.py`） | 必須のチェックを取れない | 今と同じ（未認証・ネットワーク・時間切れ・既定ブランチが不明） | `measure` | 既定値で続ける（`ci` は不明） | 注記「CI を測れない: <理由>」。終了コード 0 | 打ち直せる |

## 旧い形のキーと控え

満たす決定: 決定 1、受け入れ条件 9・10・11・12・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `strip_item` | `analysis_record.py` | 変更 | `(key, value) -> tuple[Any, list[str]]` | `ci` の中の旧いキーを取り除く（配列の行の中を見る経路を消す） |
| `strip_legacy` | `analysis_record.py` | 変更 | `(doc) -> tuple[dict, list[str]]` | 6 項目を項目ごと取り除く |
| `removed_by_hand` | `write.py` | 新設 | `(existing: Optional[dict], written_before: dict) -> dict[str, Any]` | 6 項目のうち、指紋が一致しない（指紋が無いものを含む）値を返す |
| `_project_file` | `write.py` | 変更 | `(inp, doc) -> Optional[File]` | 6 項目が残る設定では、差分の前の側を、6 項目の値を `（伏せた）` に置き換えて整形し直した文にする |
| `_measure` | `cli.py` | 変更 | シグネチャは今のまま | `--out` は親のディレクトリだけを解決し（末尾のリンクはたどらない）、リポジトリの中かを確かめて `replace_owner_only` で書く |
| `p_number` | `write.py` | 変更 | `(key, default) -> str` | 番号の表を `test`（P2）・`ci`（P4）だけにする |
| `replace_owner_only` | `held.py` | 新設 | `(path: Path, text: str) -> None` | 同じディレクトリに `tempfile.mkstemp` で作り、`os.replace` で置き換える（0600） |
| `_guard_store` | `cli.py` | 新設 | `(main: Path, folder: str, rels: list[str]) -> None` | 保管か控えの置き場に追跡されたファイルが無く、書くパスがすべて git に無視されることを確かめる |

- `analysis_record` の定数は、`UNREAD_ITEMS`（6 項目）を足し、`OLD_ITEMS` を `test_duration` と `UNREAD_ITEMS` にする。`_OLD_IN_ITEMS` から `languages[].files` を消す
- 控えは 1 回の書き出しにつき 1 ファイル `.ndf/analysis/removed/<uuid4 の hex>.json` で、中身は `{キー: 値}` である。`plan` が名前を決め、`cli._commit` が書く。`--dry-run` は案内の行だけで、名前を決めない
- `_guard_store`
  - 手順: `git ls-files -- <folder>`（出力があれば止める）→ `<folder>/.gitignore` が無ければ `*` を書く → `write.check_ignored(main, rels)` の結果で、`rels` がすべて `rules` にあることを確かめる。終了コードでは判定しない
  - 副作用: git の起動 2 回、`.gitignore` の作成
  - `.gitignore` を今回作り、その後の確かめで止める場合は、作った `.gitignore` を消す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_guard_store` | 追跡されている・無視されない・確かめられない | 利用者の git の状態 | `_guard_store` | 止める（`WriteError`） | 理由とパス。終了コード 1 | 控え・回答・設定を書いていない。原因を除いて打ち直せる |
| `replace_owner_only` | 書けない | 権限・容量 | 呼び出し元 | 呼び出し元へ返す（`OSError`） | `write` は終了コード 1、`measure` は `--out を書けない` | 一時ファイルを消す。打ち直せる |

## 書き出し（test の再解析）

満たす決定: 決定 6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `probe_inside` | `write.py` | 新設 | `(root: Path, rel: str) -> str`（`file`・`absent`・`outside`） | `root` の中の相対パスだけを、ディレクトリの参照をたどって調べる |
| `suites_note` | `write.py` | 新設 | `(root: Path, suites: list) -> dict` | 残した suite の案内の行（残した理由・無くなったファイル・未確認のパス・確かめていないこと）を作る |
| `_declare_ci_tests` | `write.py` | 変更 | シグネチャは今のまま | `ci_jobs` の存在を `probe_inside(root, p) == "file"` で判定する |

- 空の測定値の扱いは `merge_project` の中で分ける。条件は「`key` が `test`、測定が `measured` で `suites` が空、今の値の `suites` が 1 件以上、今の値が書かない値の形でない」である
  - 解析が書いた値（指紋が一致）: 新しい値を `{"unknown": "テストの手がかりが無い"}` にして `merge_item` へ渡す（`kept_previous` の行になる）
  - 手で書いた値: 新しい値を空の一覧のまま渡す（`mismatch` の行になる）。不明に置き換えると `kept` の行になるためである
  - どちらも `suites_note` の行を足す

### 処理の手順

| `probe_inside` の条件（上から） | 結果 |
| --- | --- |
| 絶対パス、`..` を含む、NUL を含む | `outside` |
| 途中の要素がシンボリックリンク（`os.stat(part, dir_fd=…, follow_symlinks=False)`） | `outside` |
| 途中の要素が無い、ディレクトリでない | `absent` |
| 途中の要素を開く（`O_RDONLY | O_DIRECTORY | O_NOFOLLOW`）ことに失敗 | `outside` |
| 末尾の要素がシンボリックリンク | `outside` |
| 末尾の要素が通常のファイル | `file` |
| それ以外（無い、ディレクトリ） | `absent` |

- 繰り返しの終わる条件: パスの要素を使い切る。開いた参照は、次の参照を開いた時点と関数を抜ける時点で閉じる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `probe_inside` | `stat` か `open` の `OSError` | 権限・途中の差し替え | `probe_inside` | 既定値で続ける（`stat` は `absent`、`open` は `outside`） | 出力は指す先の有無で変えない | 開いた参照を閉じる |

## 回答の照合と秘密

満たす決定: 決定 4・7、受け入れ条件 8・14・15（照合と秘密の部分）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `shows_secret` | `secret.py` | 新設 | `(value: Any) -> bool` | 全階層の辞書のキーと文字列の値が、出力で伏せる形に当たるかを返す |
| `compare_value` | `answer_decide.py` | 新設 | `(key: str, value: Any) -> Any` | 比べる値（旧い形のキーと、`test.ci.only` を除く） |
| `current_entry` | `answer_decide.py` | 新設 | `(key: str, has: bool, value: Any) -> dict` | `current` の 3 つの形のどれかを作る |
| `measure_problem` | `answer_decide.py` | 新設 | `(measure: dict, keys: tuple[str, ...]) -> Optional[str]` | 測定の結果の `analyzer`・`branch_state`・`context`・`current` の欠けと形の誤りを返す |
| `answer_tainted` | `answer_decide.py` | 新設 | `(name: str, answer: Any) -> bool` | 項目の名前と回答の中身のどれかが秘密を含むか |
| `judge_answer` | `answer_decide.py` | 新設 | `(case: Case) -> Ruling` | 決定 7 の順序表の何番に当たるかと、扱いを返す |
| `veil_rows` | `write.py` | 新設 | `(rows: list[dict], key: str, tainted: bool) -> list[dict]` | 行から値と回答の内容を外し、理由を固定の文にする |
| `check_measure` | `write.py` | 新設 | `(inp: Inputs) -> None` | 何かを書く前に測定の結果と場所を確かめる |
| `merge_project` | `write.py` | 変更 | `(existing, measure, answers, written_before) -> tuple[dict, list[dict], dict[str, Outcome]]` | 項目ごとの回答の扱い（`Outcome`: 順の番号・決着した比べる値の指紋・保管する理由）も返す |
| `_build_record` | `write.py` | 変更 | シグネチャは今のまま | `analyzer` に測定の結果の `analyzer` を書く |
| `measure_project` | `cli.py` | 変更 | シグネチャは今のまま | 結果に `context` を足し、質問の項目に `current`（と `held`）を載せる |
| `_ask_context` | `cli.py` | 新設 | `(dirs: RepoDirs, decl: Optional[dict], data: dict) -> None` | `current`・`held`・保管の読み取りの注記を `data` に足す |
| `_inputs` | `cli.py` | 変更 | シグネチャは今のまま | 測定の結果を読めない場合は `result: remeasure` の行を持つ `WriteError`、回答を読めない場合は行の無い `WriteError` にする |

- `WriteError` は `rows: list[dict]` を持つ（既定は空）。`cli._write` は `result.stopped` の `items` に、伏せ字を当てて渡す
- `check_measure` の順: `measure_problem`（`remeasure`）→ `analyzer` が 5 以上なら場所の比較（`context_mismatch`）
  - 測定の結果は `measure` が伏せ字を当てた後の値なので、今の場所の値にも `secret.mask` を当ててから比べる
- ブランチの構成は、#4580 の `_measured_branches` と `_record_branches` をそのまま使う。測定の `branch_state` と今の構成が違えば、何も書かずに終了コード 1 で終わる。同じ場合に記録する値は、測定の構成と同じである（決定 4）。足す関数は無い
- `ignored` の行は、回答のキーのうち `discard`・`unresume` でなく、`write.ITEM_KEYS` の質問でもないものに出す
- `RepoDirs` に `worktree: Optional[str] = None`（git の管理名）を足す。`repo_dirs` は今の `rev-parse --show-toplevel` に `--absolute-git-dir` を足し、2 行目の親が `<git の共通ディレクトリ>/worktrees` のときだけ末尾の名前を入れる

### 処理の手順

`judge_answer` は、次の順に並べた述語の表を上から当てる。述語と扱いはコードの中の 1 つの並びに持つ。

| 順（決定 7） | 扱い（`Ruling.effect`） | `merge_project` がすること |
| --- | --- | --- |
| 1・2・3・6・7・12 | `pass` | 回答をそのまま今の `resolve_item`・`merge_item` へ渡す |
| 4 | `keep_secret` | 今の値を残し、`kept`（`reason` あり）の行にする |
| 5 | `drop_secret` | 回答を `{"unknown": …}` として渡し、行を `kept`（キーが無ければ `unknown`）と `result: answer_secret` に置き換える |
| 8 | `keep_old` | 今の値を残し、`mismatch` と `result: remeasure_v4` の案内の行 |
| 9・10 | `keep_stale` | 今の値を残し、`mismatch` と `result: answer_held` の案内の行。保管の理由は `remeasure` |
| 11 | `keep_hand` | 今の値を残し、`mismatch` の行。保管の理由は `manual` |

- 順 3 のうち形に合わない `value` は、保管の理由が `invalid` になる（今の値が出力で伏せる形なら保管しない）
- `answer_tainted` が真の項目は、`veil_rows` が行を置き換える。順 8〜11 は `kind: kept` にして案内の行を出さない。保管の理由は消す。回答の `value` の適用は順序表のとおりである
- 今の値が出力で伏せる形の項目は、回答の有無によらず `veil_rows` を通し、`declared`・`analyzed` を持つ行を出さない
- `row_line` は、`kept` の行が `reason` を持てば理由を添える

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_measure` | 測定の結果の欠け・形の誤り | 旧い・壊れた測定の結果 | `measure_problem` | 止める | `result: remeasure` の行。終了コード 1 | 何も書いていない。測り直す |
| `check_measure` | 場所が違う | 測った後の切り替え | 場所の値の比較 | 止める | `result: context_mismatch` の行（測った場所と今の場所）。終了コード 1 | 何も書いていない。設計の 2 つの経路 |
| `_ask_context` | 保管のファイルを読めない | 壊れた JSON・権限 | `held.read_place` | 既定値で続ける | 注記にパスと理由。終了コード 0 | ファイルは変えない |
| `_inputs` | 読めない | ファイルが無い・JSON でない | `jsonfile.read_object` | 止める | 終了コード 1 | 打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Case`（有無・今の値・解析の値か・回答・`current`・`analyzer`・形の誤り） | 型 | `answer_decide.py` | — |
| `Ruling`（順の番号・`effect`・保管の理由） | 型 | `answer_decide.py` | — |
| `Outcome`（順の番号・決着した指紋・保管の理由・保管する値） | 型 | `answer_decide.py` | — |
| `ANALYZER` | 定数 | `check.py` | 5 |
| 版 4 以前の境（`current` を持つ最初の版） | 定数 | `answer_decide.py` | 5 |
| `SECRET_REASON` と、足す固定の理由の文 | 定数 | `write.py` | — |

保管の理由の 3 つの値と行の `result` の値は、使う場所の文字列で書く。保管の形のスキーマの `enum` と同じ集まりを定数にすると、`ndf check` の `const-fact` が落とすためである。

## 保管と再開

満たす決定: 決定 8、受け入れ条件 15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `context_of` | `held.py` | 新設 | `(repo: git.Git, worktree: Optional[str]) -> dict` | 測定の `context`（`root`・`branch`・`commit`・`worktree`・`measure_id`） |
| `place_of` | `held.py` | 新設 | `(context: dict) -> dict` | 場所の値 |
| `read_place` | `held.py` | 新設 | `(main: Path, context: dict) -> tuple[list[dict], list[tuple[Path, str]]]` | 今の場所の回答と、読めないファイル |
| `awaits_resume` | `held.py` | 新設 | `(entry: dict, inputs_digest: str, branches: dict, decl: Optional[dict]) -> bool` | `check` と `measure` が使う再開の条件 |
| `pick_held` | `held.py` | 新設 | `(entries: list[dict], ready: set[str]) -> Optional[dict]` | AI に渡す 1 件（`id`・`value`・`reason`・`others`） |
| `plan_changes` | `held.py` | 新設 | `(main: Path, entries: list[dict], event: Event) -> Changes` | 足す・消す・`resume` を下げるファイルと、案内の行を決める（何も書かない） |
| `gone_places` | `held.py` | 新設 | `(main: Path, common: Path) -> list[Path]` | 作業場所が削除された場所のディレクトリ |
| `store_adds` | `held.py` | 新設 | `(changes: Changes) -> None` | `context.json` と足す回答を書く |
| `store_drops` | `held.py` | 新設 | `(changes: Changes) -> list[dict]` | 回答を消し、`resume` を下げる。失敗した `id` の行を返す |
| `cleanup_plan` | `write.py` | 新設 | `(inp: Inputs) -> Plan` | `discard`・`unresume` だけの回答の計画（設定と記録を書かない） |
| `plan` | `write.py` | 変更 | `(inp: Inputs) -> Plan` | `check_measure` を先に打ち、控えと保管の変更を `Plan` に載せる |
| `_commit` | `cli.py` | 変更 | `(main: Path, planned: Plan) -> list[dict]` | 決定 8 の書き込みの順で書き、後処理の失敗の行を返す |
| `_write` | `cli.py` | 変更 | シグネチャは今のまま | `_commit` が返す行を出力に足す |
| `_stale_reasons` | `check.py` | 変更 | `(repo, record, decl) -> tuple[list[str], dict, dict]` | 判定で取った入力とブランチの構成も返す |
| `_held_reasons` | `check.py` | 新設 | `(dirs: RepoDirs, decl, inputs: dict, branches: dict) -> list[str]` | 「保管した回答がある: <キー>」の理由 |
| `judge` | `check.py` | 変更 | シグネチャは今のまま | ほかの理由が無いときだけ保管を読む。順は、古い理由、保管、`--force` |

- `Inputs` に `common: Optional[Path] = None` と `worktree: Optional[str] = None` を末尾に足す。`Plan` に `held: Optional[held.Changes] = None` を足す。今のテストの組み立て方は変わらない
- `context_of`
  - 事前条件: `repo` は作業場所の根
  - 副作用: `git rev-parse HEAD --symbolic-full-name HEAD`（1 行目がコミット、2 行目が `refs/heads/<名前>` か `HEAD`）。コミットが無いリポジトリでは失敗するので、`git symbolic-ref -q HEAD` でブランチを取り、`commit` は `null` にする
  - `branch` は `refs/heads/` を外した名前、`measure_id` は `uuid4` の hex
- `read_place`
  - 副作用: 場所のディレクトリ 1 つの一覧と読み取り。`context.json` と、`.` で始まる一時ファイルは回答に数えない。一覧の後に消えたファイルは読めないファイルに数えない
- `plan_changes`
  - `Event`: 測定の `context`・`inputs` の指紋・`branch_state`、項目ごとの `Outcome`、回答の `resolves`・`discard`・`unresume`、測定の項目の `status`・`current`・`held.id`、`--answers` の有無
  - 事後条件: 受け取った `id` からパスを組み立てない。`entries` の `id` と一致するものだけを対象にし、ほかは案内の行にする。同じ値かは `compare_value` の指紋で比べる
- `gone_places`
  - 副作用: `root` と管理名の git ディレクトリの両方が無いブランチの場所があるときだけ、`git for-each-ref --format=%(refname) refs/heads` を 1 回打つ
- `store_drops` が消す場所のディレクトリは、回答と `context.json` を消して空になったときだけ消す。読めないファイルがあれば残す
- 保管の欄の名前は設計の決定 8 の表のとおりで、スキーマに合わないファイルは「読めないファイル」である

### 処理の手順

`_commit` の順は次のとおりである。

| 順 | すること | 失敗の扱い |
| --- | --- | --- |
| 1 | 今の `_guard_record`、続けて控えと保管の置き場の `_guard_store`（足すか書き換える場合だけ） | 何も書かず終了コード 1 |
| 2 | 控え、`store_adds` | 終了コード 1。足した回答は残る |
| 3 | 今の順の `.gitignore`・記録・設定 | 終了コード 1。回答は消していない |
| 4 | `store_drops`（作業場所が削除された回答を含む） | 終了コード 0。失敗した `id` の行を返す |

`resume` を決める順は、設計の決定 8 の「`resume` の決め方」の表を、そのまま述語の並びにする。

`plan` の分岐は次のとおりである。

| 条件 | 結果 |
| --- | --- |
| `check_measure` が失敗 | `WriteError` |
| 回答のキーが空でなく、`discard`・`unresume` だけ | `cleanup_plan`。`analyzer` が 4 以下なら保管の変更は無い |
| `analyzer` が 4 以下 | 設定の計画だけ。保管の追加と削除はしない |
| 一覧を取れない場所のディレクトリがある | `WriteError`（何も書かない） |
| それ以外 | 設定の計画と保管の変更 |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `context_of` | git が失敗する | 壊れたリポジトリ | `context_of` | 呼び出し元へ返す（`CheckError` に直す） | `測れない: <理由>`。終了コード 1 | 打ち直せる |
| `read_place` | 一覧を取れない | 権限 | `read_place` | 呼び出し元へ返す（`OSError`） | `write` は終了コード 1。`check` は理由にしない。`measure` は注記 | ファイルは変えない |
| `read_place` | 1 件を読めない | 壊れた JSON・形の誤り | `read_place` | 既定値で続ける | パスと理由の案内の行か注記 | ファイルは変えない |
| `gone_places` | `context.json` かブランチの一覧を読めない | 権限・git の失敗 | `gone_places` | 既定値で続ける（消さない） | 出力に出ない | 次の `write` がもう一度確かめる |
| `store_adds` | 書けない | 権限・容量 | `_commit` | 止める | 終了コード 1 | 打ち直しても回答は増えない |
| `store_drops` | 削除か更新に失敗 | 権限 | `store_drops` | 呼び出し元へ返す（行） | `result: held_delete_failed` か `held_resume_failed`、`name` に `id`。終了コード 0 | 後処理だけの打ち直し |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別の worktree の `write` どうし | 保管・控え・設定・記録 | 今の書き出しのロック（`cleanup_plan` も含む。`--dry-run` は取らず、書かない） | 同じ回答の打ち直しは `measure_ids` で見分け、増やさない |
| `write` と `check`・`measure` | 保管のファイル | ロックを取らない。書き込みは `os.replace` なので、読む側は書きかけを見ない | 消えたファイルは無いものとして扱う |

## 手順書・確定仕様・このリポジトリの設定

満たす決定: 決定 1・6・7・8、受け入れ条件 13

- `cli.py` の `--answers` の説明（`_parser`）に、設計の入出力のコントラクトの最後の行の内容を書き、`ndf check --write-spec` で `project.args.schema.json` と `commands.md` を作り直す
- `project.result.schema.json` の `result` の説明に、足す値（`remeasure`・`context_mismatch`・`answer_held`・`remeasure_v4`・`answer_secret`・`held_delete_failed`・`held_resume_failed`）を書く
- `project-analysis.md`・`ndf-project-analysis-record.md` は、設計の構成要素の表の「手順書と確定仕様」の行の内容を書く
- `.ndf/project.json` は、6 項目と `analysis.written` の該当の指紋を手で消す

## ログと記録の出力

満たす決定: 決定 1・7・8、受け入れ条件 7・15・16

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `plan` | 控えの案内・照合と保管の案内・`suites_note` の行 | 結果 JSON の `items` と標準出力 | 取り除く値、秘密を含む回答の内容、出力で伏せる形の値 |
| `_ask_context` | `current`・`held`・読めないファイルの注記 | `--out`（0600） | 伏せる形の値と、その指紋 |

## 既存の部品の再利用

満たす決定: 決定 1・6・7・8（新設する関数の全部）

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `replace_owner_only` | `signals.replace_text`、`records.mvv.write_private`、`crossreview.store.write_object` | `grep -rn "mkstemp\|def write_private" plugins/ndf/src/ndf` | 使えない。`replace_text` は一時ファイルの名前が予測できる。`write_private` は MVV のモジュールにあり、設計が定める `mkstemp` を使わない。`write_object` は cross-review の JSON 用である |
| `probe_inside` | `Path.resolve` と `is_relative_to`、`ndf.contracts.worker_paths` | 設計の「外部の調査」と `grep -rn "dir_fd" plugins/ndf/src/ndf` | 使えない。`worker_paths` は利用者が共有しないディレクトリの用意で、存在の確かめではない |
| `shows_secret` | `secret.has_secret`・`secret.mask` | `secret.py` を読んだ | 使えない。前者は書かない値の形だけ、後者は辞書のキーを見ない。形の定義（`secret_lines`）は使う |
| `compare_value` | `analysis_record.strip_item`・`items.value_digest` | `grep -rn "value_digest\|strip_item" plugins/ndf/src` | 2 つとも使う |
| `current_entry`・`measure_problem`・`answer_tainted`・`judge_answer` | `write.resolve_item`・`write.item_problem` | `write.py` を読んだ | 値の解決と形の確かめは使う。照合の判定は今は無い |
| `context_of`・`place_of` | `check.repo_dirs`・`adapters.git.Git` | `git.py` の `rev-parse` を使う関数を読んだ | `Git.attempt` を使う |
| `read_place`・`plan_changes`・`store_adds`・`store_drops`・`gone_places`・`pick_held`・`awaits_resume` | `jsonfile.read_object`・`contracts.schema_errors`・`items.value_digest` | `grep -rn "held" plugins/ndf/src/ndf/domains` | 読み取りと形の確かめは使う。保管の仕組みは今は無い |
| `service_names`・`instruction_notes` | `compose_services`・`_old_guide_notes`・`instruction_files` | `measure_repo.py` を読んだ | 使う |
| `removed_by_hand`・`veil_rows`・`suites_note`・`check_measure`・`cleanup_plan`・`_ask_context`・`_guard_store`・`_held_reasons` | `_owned_decision`・`ignored_notes`・`_untracked`・`_ignored`・`check_ignored` | `write.py`・`cli.py` を読んだ | `check_ignored` と `_untracked` を使う。ほかは対象が違う |

新しい最上位の関数の名前は、`ndf check` の `copy-dup`（2 つ以上のファイルに同じ名前の関数）に当たらない名前を選んでいる。`write_private`・`resumable` は別のモジュールにあるので使わない。

## 単体テストの観点

満たす決定: 決定 1・2・3・6・7・8、受け入れ条件 6・7・15・16

受け入れ条件の粒度と落ちるべき壊し方は、設計の「テスト設計」が持つ。ここは関数の境界だけを並べる。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `service_names` | 2 ファイルの名前が整列して 1 回ずつ出る | compose が無ければ空 | 読めないファイルは数えない |
| `instruction_notes` | 古い案内の注記 | 指示書が無い | — |
| `probe_inside` | `file` | ディレクトリは `absent`、`.` を含む相対パス | 絶対パス・`..`・途中と末尾のリンクは `outside` |
| `removed_by_hand` | 指紋が違う値を返す | 指紋が無い値を含む。すべて一致なら空 | — |
| `shows_secret` | 辞書のキーの代入の形を見つける | 空の値は偽 | — |
| `current_entry`・`measure_problem` | 3 つの形 | `digest: null`、`analyzer` 4 | 余分なキー、16 進数でない指紋、指紋の食い違い |
| `judge_answer` | 順 1〜12 の 1 行ずつ | 版 4 でキーが無い・不明、`withheld` で同じ値 | 形に合わない `value` |
| `context_of`・`place_of` | ブランチ | detached HEAD、コミットが無い | — |
| `plan_changes` | 追加・同値の削除・`resolves`・`discard` | 同じ `measure_id` の打ち直し | `../` を含む `id`、別の場所の `id` |
| `gone_places` | worktree とブランチの削除 | 移動だけ・改名だけは残す | `context.json` が壊れている |
| `_guard_store` | 無視される | `.gitignore` を作る回 | 追跡済み、親と子の両方の指定を外して無視されないパス |
