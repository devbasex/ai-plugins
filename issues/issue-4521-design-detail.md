# #4521 の詳細設計: 読み手のある項目だけを測って設定に書く

設計文書 [issue-4521-design.md](./issue-4521-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。以下の「決定 N」は設計文書の決定、「受け入れ条件 N」は要求仕様 [issue-4521-requirements.md](./issue-4521-requirements.md) の番号を指す。新設する関数が 2 つ以上あり、シグネチャを変える関数もあるので、別ファイルにする。

この文書で使う呼び名は次のとおりである。

- **6 項目**: `.ndf/project.json` の `languages`・`services`・`issues`・`checks`・`ndf_policies`・`instructions`
- **読み手**: 設定に書かれた値を使うプログラム・手順書・利用者（数え方は要求仕様）
- **指紋**: 解析が書いた値の SHA-256。項目ごとに設定の `analysis.written` に持ち、解析が書いたままの値か人が直した値かを見分ける

現状の記述は、`main` のコミット `7504f88a3`（#4580 のマージを含む）のコードに合わせる。実装はこのコミット以降の `main` から始める。

## 地図

パッケージ・モジュール構成。新設するモジュールは無い。この文書のパスの根は、`docs/specifications/` と `.ndf/` を除き `plugins/ndf/` である。

```text
src/ndf/domains/project/
  cli.py  check.py  write.py  measure_repo.py  measure_ci.py  analysis_record.py
contracts/
  schemas/ndf-settings/project.schema.json
  domains/ndf-files.json  domains/languages.json
```

依存の向きは今と同じで、足す依存は `write.py` から `adapters.secret_lines` の伏せ字の文字列（`MASK`。今も `secret.py` が読む）を使うことだけである。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/project/measure_repo.py` | 変更 | git の木から `test`・`materials` を測る | `secret`・`items` | `measure_repo` | 変更: `compose_services`・`measure`。新設: `service_names`・`instruction_notes` |
| `src/ndf/domains/project/measure_ci.py` | 変更 | 必須のチェックだけを測る | `measure_repo` | `measure_ci` | 変更: `measure` |
| `src/ndf/domains/project/cli.py` | 変更 | 測定の組み立て | 下の全部 | `cli`・`MeasurementContext` | 変更: `_ci_items`・`measure_project`。削除: `_instruction_notes` |
| `src/ndf/domains/project/write.py` | 変更 | 設定の値と、書くファイルの計画を作る | `check`・`analysis_record` | `write` | 変更: `plan`・`p_number`・`_build_record`・`_project_file` |
| `src/ndf/domains/project/analysis_record.py` | 変更 | 旧い形のキー | `items` | `analysis_record` | 変更: `strip_item`・`strip_legacy`。削除: 配列の各行の中の旧いキーを探す分岐 |
| `src/ndf/domains/project/check.py` | 変更 | 解析器のバージョン | — | `check` | 変更: 定数 `ANALYZER` |
| `contracts/schemas/ndf-settings/project.schema.json` と `skills/development-workflow/schemas/project.schema.json` | 変更 | 設定の形（後者は `ndf project schema` の生成物） | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | NDF が書くファイルの表 | — | — | — |
| `contracts/domains/languages.json` | 変更 | 要約の文 | — | — | — |
| `docs/contracts/ndf-settings.md` | 変更 | 生成物（`ndf check --write-spec`） | — | — | — |
| `skills/development-workflow/references/project-analysis.md`、`docs/specifications/ndf-project-analysis-record.md`、`.ndf/project.json` | 変更 | 手順書・確定仕様・このリポジトリの設定 | — | — | — |
| `tests/domains/project/` の `test_project_measure.py`・`test_project_cli.py`・`test_analysis_record.py`・`test_measure_project_branch_state.py`・`test_write_plan.py` | 変更 | 今のテスト | — | — | — |

## 設定の形の定義とファイルの表

満たす決定: 決定 1・5、受け入れ条件 3・4・13

- `project.schema.json` の `properties` から 6 項目を消し、それだけが参照する `$defs`（`Language`・`Services`・`Issues`・`Checks`・`Tool`・`NdfPolicies`・`Instructions`・`Import`）を消す。最上位の `description` から言語と課題の保存先を外す
- `ndf-files.json`: 行 `project` の `keys` から 6 項目を外す。行は足さない
- `languages.json` は要約（`summary`）の「言語の計測・」だけを消す

## リポジトリと CI の測定

満たす決定: 決定 2・3、受け入れ条件 2・3・5・6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `compose_services` | `measure_repo.py` | 変更 | `(text: str) -> list[str]` | `services:` の直下の名前を返す（image は読まない） |
| `service_names` | `measure_repo.py` | 新設 | `(tree: Tree) -> list[str]` | 根と 1 段下の compose のファイルのサービス名を、重複なく整列して返す |
| `instruction_notes` | `measure_repo.py` | 新設 | `(tree: Tree) -> list[str]` | 指示書（`AGENTS.md`・`CLAUDE.md` など、AI への指示を書くファイル）の注意書きを返す。対象は、指示書か、指示書が読み込むファイルの先頭 5 行に、古い NDF の案内の目印（`NDF_PLUGIN_GUIDE_START`）がある場合と、指示書が無い場合の 2 つである。今の `measure_instructions` の `notes` と同じ文と順で返す |
| `measure` | `measure_repo.py` | 変更 | `(tree: Tree, step: Step, items: dict) -> list[str]` | `test`・`materials` を `items` へ入れ、指示書の注記を返す |
| `measure` | `measure_ci.py` | 変更 | `(tree, repo, head, deadline) -> dict`（返すキーは `ci`・`notes`） | 必須のチェックだけを測る |
| `_ci_items` | `cli.py` | 変更 | `(tree, step, ctx) -> list[str]` | `ci` の項目を入れ、CI の注記だけを返す |

- `measure`（`measure_repo.py`）
  - 事後条件: `items` に `test`・`materials` のほかの項目の値が無い。依存・サービス名・指示書の確かめが失敗しても残りを測る
  - `step` に渡す処理の名前は `dependencies`・`services`・`instructions` である。どれかが失敗すると、失敗の記録がその名前で `items` に入る。`cli.measure_project` は `write.ITEM_KEYS` にあるキーだけを残すので、この記録は結果に出ない
- `measure_project` の注記の順は、指示書の注記、CI の注記、開かなかった秘密の名前のファイル、の順で今と同じである
- `MeasurementContext` から `repo_issues` を消す

消すもの（読み手が無くなる）: `LANG_EXT`・`FRAMEWORKS`・`DB_WORDS`・`CHECK_TOOLS`・`PYPROJECT_TOOLS`・`POLICY_WORDS`・`_major`・`_first_number`・`_versions`・`measure_languages`・`_workflow_services`・`measure_services`・`measure_issues_repo`・`url_hosts`・`measure_checks`・`measure_policies`・`measure_instructions`・`measure_ci._measure_merges`・`measure_ci.measure_issues`・`cli._instruction_notes`。`measure_ci` の `Gh` と `GhUnavailable` は `strategy_measure.py` も使うので残す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `service_names`・`instruction_notes` | 読み取りの失敗・時間切れ | git の失敗、締め切り | `cli._Steps` | 既定値で続ける（空） | 出力に出ない。終了コード 0 | 打ち直せる |
| `measure`（`measure_ci.py`） | 必須のチェックを取れない | 今と同じ（未認証・ネットワーク・時間切れ・既定ブランチが不明） | `measure` | 既定値で続ける（`ci` は不明） | 注記「CI を測れない: <理由>」。終了コード 0 | 打ち直せる |

## 旧い形のキーと書き出し

満たす決定: 決定 1・4、受け入れ条件 8・9・10・11・12・14

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `strip_item` | `analysis_record.py` | 変更 | `(key, value) -> tuple[Any, list[str]]` | `ci` の中の旧いキーを取り除く。値が配列のときに各行の中の旧いキーを取り除く分岐は消す（対象は `languages[].files` だけで、`languages` は項目ごと消える） |
| `strip_legacy` | `analysis_record.py` | 変更 | `(doc) -> tuple[dict, list[str]]` | 6 項目を項目ごと取り除く |
| `plan` | `write.py` | 変更 | シグネチャは今のまま | 回答できる項目を、`status: question` のうち `ITEM_KEYS` にあるものに絞る。ほかの回答は `ignored` の行にする |
| `p_number` | `write.py` | 変更 | `(key, default) -> str` | 項目と番号（`measure`・`write` の出力で項目を指す `P2` などの表示）の対応表を、`test`（P2）・`ci`（P4）だけにする |
| `_build_record` | `write.py` | 変更 | シグネチャは今のまま | `analyzer` に測定の結果の `analyzer` を書く。1 以上の整数でない値は、記録の形の確かめより前に止める |
| `_project_file` | `write.py` | 変更 | `(inp, doc) -> Optional[File]` | 6 項目が残る設定では、差分の前の側を、6 項目の値を `（伏せた）` に置き換えて整形し直した文にする |

- `analysis_record` の定数は、6 項目の並び `UNREAD_ITEMS` を足し、`OLD_ITEMS` を `test_duration` と `UNREAD_ITEMS` にする。`_OLD_IN_ITEMS` から `languages[].files` を消す
- 6 項目の指紋は、`merge_project` が `analysis.written` を `ITEM_KEYS` の項目だけで作り直すので消える。足す処理は無い
- `_project_file` が置き換えるのは差分の前の側（`File.old`）だけで、書く中身（`File.new`）は 6 項目を持たない
- 不正な `analyzer` を記録の形の確かめ（`contracts.schema_errors`）に渡さない。そのエラー文は検証した値を含み、`write` は `WriteError` の文を出力と結果 JSON にそのまま載せるためである
- ブランチの構成は、#4580 の `_measured_branches` と `_record_branches` をそのまま使う
- `ANALYZER`（`check.py`）は 5 にする

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_build_record` | 測定の結果の `analyzer` が無いか 1 以上の整数でない | 壊れた測定の結果 | `_build_record` の先頭の型の確かめ（真偽値と小数も不正） | 止める（`WriteError`） | 「測定の結果の analyzer は 1 以上の整数が必要（ndf project measure からやり直す）」。入力された値は出さない。終了コード 1 | 何も書いていない。測り直す |

## 手順書・確定仕様・このリポジトリの設定

満たす決定: 決定 1、受け入れ条件 13

- `project-analysis.md`
  - 「回答方」の表と例から `issues`・`ndf_policies` を消し、`gh` で呼ぶ API と質問の説明を今の項目に合わせる
  - 旧い形の設定の説明に 6 項目を足し、次を書く
    - `ndf project write` は、人が直した値も取り除く。取り除く前の値の控えは自動で作らない
    - `ndf project write` が出す差分は、取り除く値を伏せる。Git の差分（`git diff` など）には値がそのまま表示される
    - コミットしていない手の編集は戻せない。打つ前に、利用者本人がメインディレクトリで `git diff --cached -- .ndf/project.json`（ステージした変更）と `git diff -- .ndf/project.json`（ステージしていない変更）の両方を確かめる
    - 設定を git で追跡していなければ、どの値も戻せない。残したい値があれば、打つ前にファイルをコピーする
    - 旧い値には認証情報が入っていることがある。値の入った Git の差分を見るのは、利用者本人が自分だけが見る端末で打つ場合に限り、AI には打たせない。書き写した値やファイルのコピーは、リポジトリの外の本人だけが読める場所に置く
  - 「移行の後」の手順 1（メインディレクトリで差分を確かめる）を、値を表示しない確かめに書き直す。変わった内容は `write` が出した差分で確かめる。旧いキーが残っていないことは `ndf project check`、ファイルが変わったことは `git diff --stat HEAD -- .ndf/project.json` で確かめる
  - 「手順 0 での扱い」の「書き出した設定はコミットしない限り取り消せるので、承認を求めない」を書き直す。移行の説明に注意を足すだけでは、手順 0 だけを読む担当者が、コミット前ならすべて取り消せると判断するためである。書き直した後の内容は次の 3 つである。承認は今と同じく求めない。書き出した設定は、コミットしない限りチームに届かない。ただし、取り除く 6 項目などの旧い項目に手で加えてコミットしていない編集は、書き出すと取り消せない（設定を git で追跡していなければ、取り除いた値はどれも戻せない）
- `ndf-project-analysis-record.md`: 解析が書く項目、解析器のバージョンの記録の仕方、上の取り除き方と理由を書く
- `.ndf/project.json` は、6 項目と `analysis.written` の該当の指紋を手で消す

## 既存の部品の再利用

満たす決定: 決定 2・3

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `service_names`・`instruction_notes` | `compose_services`・`_old_guide_notes`・`instruction_files` | `measure_repo.py` を読んだ | 使う |

## 単体テストの観点

満たす決定: 決定 1・2・3・4、受け入れ条件 6・7・8・10・14

受け入れ条件の粒度と落ちるべき壊し方は、設計の「テスト設計」が持つ。ここは関数の境界だけを並べる。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `service_names` | 2 ファイルの名前が整列して 1 回ずつ出る | compose が無ければ空 | 読めないファイルは数えない |
| `instruction_notes` | 古い案内の注記 | 指示書が無い | — |
| `strip_legacy` | 6 項目を取り除き、キーの名前を返す | 6 項目の無い設定は変わらない | 値がスキーマに合わない 6 項目も取り除く |
| `plan` | `test` の回答は適用される | 旧版の測定の結果の `issues` の回答は `ignored` | — |
| `_build_record` | `analyzer` に測定の結果の値を書く | 1 は通る | 文字列・真偽値・小数・0・欠落は `WriteError`。文に入力された値が無い |
| `_project_file` | 取り除くキーが差分に出て、値は伏せる | 6 項目の無い設定の差分は今と同じ | — |
