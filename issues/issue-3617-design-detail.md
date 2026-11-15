# #3617 の詳細設計: ndf-next の ai-plugins の値を設定で受け、ハードコードと選べる値の減少を CI で止める

設計文書 [issue-3617-design.md](issue-3617-design.md) の「構成要素」と「ドメインモデル」を、ファイルと関数の粒度へ下ろした文書である。決定の番号（決定 1 など）は [issue-3617-design-decisions.md](issue-3617-design-decisions.md) を指す。不変条件の番号（I1 など）は設計文書の「不変条件」を指す。

関数は `<モジュール>.<関数>` の形で書く（例: `git.remote_of` は `plugins/ndf-next/src/ndf/adapters/git.py` の `remote_of`）。モジュールの名前が 2 つの置き場で重なるときは、置き場の列で区別する。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`→` は移す先）:

```text
scripts/
├── check-skill-repo-assumptions.py
├── check-ndf-choices.py *
├── ndf-choices.json *
├── repo-assumptions-allow/          （ndf-next の項目を足す）
├── ndf-next-parity/ *               （plugins/ndf-next/src/ndf/parity/ → ここへ移す）
│   ├── README.md
│   ├── parity/
│   │   ├── __init__.py
│   │   ├── table.py
│   │   ├── features.json / fixes.json / schema.json
│   │   └── trial/ (__init__.py・probe.py・2026-11-15.json・2026-11-16.json)
│   └── tests/
│       ├── conftest.py *
│       ├── test_table.py            （plugins/ndf-next/tests/parity/ → ここへ）
│       ├── test_trial_probe.py      （同上）
│       └── test_skill_table.py      （plugins/ndf-next/tests/domains/manifests/ → ここへ）
└── tests/
    ├── test_check_skill_repo_assumptions.py
    └── test_check_ndf_choices.py *
plugins/ndf-next/
├── contracts/
│   ├── domains/ndf-files.json
│   ├── schemas/declarations/{worktree,project,estimate}.schema.json
│   └── templates/{params.json, check.json, parts/steps.json}
├── skills/cross-refactoring/data/allocation-defaults.json
└── src/ndf/
    ├── cli.py
    ├── adapters/{git.py, claude.py, cli.py}
    ├── core/{route/__init__.py, converge/worktree.py}
    ├── progress/command.py
    ├── checks/children.json
    └── domains/
        ├── crossrefactor/{allocation_decide.py *, final_gate.py}
        ├── crossreview/{drive.py, repo.py, push.py}
        ├── hooks/strict.py
        ├── project/{check.py, cli.py, measure_flow.py, measure_repo.py}
        ├── records/mvv.py
        ├── relay/context_records.py
        ├── release/verify.py
        └── worktree/{cleanup.py, create.py, declaration.py, follow.py, merged.py, repo.py}
```

依存の向き: リモート名の事実は `adapters/git.py` の `remote_of` だけが読む。`core/`・`progress/`・`domains/` はそこから引く（下の層のアダプタを上の層が読む 1 方向）。`adapters/git.py` は標準ライブラリと `ndf.identity` だけを読み、`ndf.contracts`（`jsonschema` を読み込む）を読まない。hook（`domains/hooks/strict.py`）が `adapters/git.py` を読んでも起動の時間を延ばさないためである。根の `scripts/` の 2 つの検査は ndf-next のファイルを読むだけで、ndf-next のモジュールを import しない。移した機能の対応表（`scripts/ndf-next-parity/`）は ndf-next のモジュール（`ndf.domains.manifests`）を import する。ndf-next の側からは読まれない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `scripts/check-skill-repo-assumptions.py` | 変更 | ndf-next の形のプラグインの Skill・エージェント・`src/`・`contracts/`・`hooks/*.py` を走査し、追加の語を当てる | `scripts/lib/allowlist.py` |
| `scripts/repo-assumptions-allow/` | 変更 | ndf-next の今の当たりを `pending` と `not-a-violation` の項目として足す | 無し |
| `scripts/check-ndf-choices.py` | 新設 | 選べる値の基準と今のコントラクトを照らし、I4〜I6 を判定する | `scripts/ndf-choices.json` |
| `scripts/ndf-choices.json` | 新設 | 選べる値の集合ごとの出所・値・減らす例外を持つ | 無し |
| `scripts/tests/test_check_skill_repo_assumptions.py` | 変更 | 走査を広げた後の件数・語・既定の除外を確かめる | `scripts/check-skill-repo-assumptions.py` |
| `scripts/tests/test_check_ndf_choices.py` | 新設 | 選べる値の検査の判定と終了コードを確かめる | `scripts/check-ndf-choices.py` |
| `scripts/ndf-next-parity/` | 新設 | 機能の対応表・読み手・試走の判定・そのテストを、リリース対象の外に持つ | ndf-next の依存の環境（`jsonschema`）と `ndf.domains.manifests` |
| `plugins/ndf-next/src/ndf/parity/table.py` | 削除 | `scripts/ndf-next-parity/parity/table.py` へ移す | — |
| `plugins/ndf-next/src/ndf/parity/trial/probe.py` | 削除 | `scripts/ndf-next-parity/parity/trial/probe.py` へ移す | — |
| `plugins/ndf-next/tests/parity/test_table.py` | 削除 | `scripts/ndf-next-parity/tests/` へ移す | — |
| `plugins/ndf-next/tests/domains/manifests/test_skill_table.py` | 削除 | `scripts/ndf-next-parity/tests/` へ移す | — |
| `plugins/ndf-next/src/ndf/adapters/git.py` | 変更 | リモート名を宣言かスキーマの既定から決め、`Git` の取得・照合・送信に渡す | `ndf.identity` |
| `plugins/ndf-next/src/ndf/adapters/claude.py` | 変更 | 導入済みの登録の一覧から、外す旧い NDF の登録を決める | `ndf.identity`・`ndf.contracts.facts`・`claude` の CLI |
| `plugins/ndf-next/src/ndf/adapters/cli.py` | 変更 | `ndf launch-args` が、一覧を読めなかった知らせを `items` に載せる | `adapters/claude.py` |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | 使い方の表示から NDF 自身の開発の副命令を外す | 無し |
| `plugins/ndf-next/src/ndf/core/route/__init__.py` | 変更 | 差し込み `remote` を補い、既定ブランチをリモート名から読む | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/core/converge/worktree.py` | 変更 | 収束の取得をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | 文書だけの判定の下位の命令 `docs-only` を足し、振る舞いの文書の既定を空にする | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/allocation_decide.py` | 新設 | 初期値と宣言を重ねた配分の値を返す（純粋な関数） | 無し |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/final_gate.py` | 変更 | 送信をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/crossreview/drive.py` | 変更 | 送り直しの照合をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/crossreview/repo.py` | 変更 | 対象のリポジトリをリモート名から決める | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/crossreview/push.py` | 変更 | 送信をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/hooks/strict.py` | 変更 | マージの宛先の照合を、リモート名の URL で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/project/check.py` | 変更 | ブランチの状態をリモート名で読む | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | 変更 | GitHub のリポジトリをリモート名の URL から読む | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/project/measure_flow.py` | 変更 | スプリントの流れの計測をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/project/measure_repo.py` | 変更 | 既定ブランチの計測をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/records/mvv.py` | 変更 | MVV の兆候の基準をリモート名で読む | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/relay/context_records.py` | 変更 | リポジトリの鍵をリモート名の URL から作る | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/release/verify.py` | 変更 | ベースブランチの同期をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/cleanup.py` | 変更 | スプリントブランチを残すかの判定にリモート名を渡す | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/create.py` | 変更 | worktree の作成の取得をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 変更 | 個人設定で `remote` を上書きさせない（`LOCAL_SILENT` に足さず、受けない鍵として扱う） | 無し |
| `plugins/ndf-next/src/ndf/domains/worktree/follow.py` | 変更 | 追従の取得をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/merged.py` | 変更 | マージ後の同期をリモート名で行う | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/repo.py` | 変更 | 既定ブランチと本番ブランチへの包含をリモート名で読む | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | `X3` の `packages` から `ndf.parity` を外し、`X4` の行と `S2` の `needs` を足す | 無し |
| `plugins/ndf-next/contracts/templates/params.json` | 変更 | 差し込み `remote` を足す | 無し |
| `plugins/ndf-next/contracts/templates/check.json` | 変更 | `assess` を `ndf progress docs-only` の呼び出しにする | 無し |
| `plugins/ndf-next/contracts/templates/parts/steps.json` | 変更 | `push`・`base-sync` を `{{remote}}` で書く | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` | 変更 | `remote` を足す（`default` はここだけ） | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` | 変更 | `docs.behavior` の説明を「省くと振る舞いの文書は無し」にする | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/estimate.schema.json` | 変更 | `allocation_minutes` を足す | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | `worktree` に `remote`、`estimate` に `allocation_minutes` の鍵を足す | 無し |
| `plugins/ndf-next/skills/cross-refactoring/data/allocation-defaults.json` | 変更 | `initial` と上書きの置き場を書いた `summary` を足す | 無し |
| `.github/workflows/ndf-next-checks.yml` | 変更 | 2 つの検査を打つステップと、それらのファイルの paths を足す | 根の `scripts/` |
| `.github/workflows/ndf-next.yml` | 変更 | 移した対応表のテストを ndf-next の依存の環境で流す | `scripts/ndf-next-parity/` |
| `.github/workflows/ndf-next-release.yml` | 変更 | 宣言に無いブランチを GitHub の既定ブランチで補う | `gh api` |
| `.ndf/project.json` | 変更 | ai-plugins の振る舞いの文書のパスを `docs.behavior` に書く | 無し |

表に挙げないファイルは、設計文書の「設計の結果」の触るファイルのとおりに直す。Skill とエージェントの本文の 104 ファイル・`docs/glossary/glossary.json`・`docs/glossary.md`・ndf-next のテストのファイルである。Skill とエージェントの本文の直しは関数を持たないため、下の節には現れない。直す中身は次の 4 つである。

- 冒頭の機能の対応表の注記を消す（決定 10）
- 「（ai-plugins）」の表記を外す
- `devbasex` の例を `<owner>`・`<owner>/<repo>` にする
- `--admin` を、迂回の宣言と ruleset の両方がそろうときの扱いとして書く

## クラスと関数の構造

型の関係は設計文書の「ドメインモデル」の集約の表を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `scripts/check-skill-repo-assumptions.py` | モジュール | `is_next_family`（新設）・`collect_code`（新設）・`scan_contracts`（新設）・`remote_defaults`（新設）・`default_problems`（新設）・`load_manifest_union`（変更）・`owning_skills_dir`（変更）・`scan_family`（変更）・`print_outside`（変更）・`main`（変更）・`ScanError`（新設） |
| `scripts/check-ndf-choices.py` | モジュール | `load_baseline`・`read_values`・`judge`・`main`・`BaselineError`・`SourceError`・`Verdict`（すべて新設） |
| `plugins/ndf-next/src/ndf/adapters/git.py` | モジュールと `Git` | `default_remote`（新設）・`remote_of`（新設）・`RemoteError`（新設）・`__getattr__`（新設。`ORIGIN` を遅れて解決）・`Git.remote`（新設）・`Git.remote_repo`（変更） |
| `plugins/ndf-next/src/ndf/adapters/claude.py` | モジュール | `list_plugins`（新設）・`disabled_plugins`（新設）・`PluginArgs`（新設）・`plugin_args`（変更）・`_full_args`（変更。`.args` を読むだけ） |
| `plugins/ndf-next/src/ndf/adapters/cli.py` | モジュール | `main`（変更） |
| `plugins/ndf-next/src/ndf/cli.py` | モジュール | `DEVELOPER_COMMANDS`（新設）・`_usage`（変更） |
| `plugins/ndf-next/src/ndf/core/route/__init__.py` | モジュール | `default_base`（変更）・`derived`（変更） |
| `plugins/ndf-next/src/ndf/progress/command.py` | モジュール | `docs_only`（新設）・`_cmd_docs_only`（新設）・`_diff_names`（変更）・`range_diff_kinds`（変更）・`_sprint_range`（変更）・`repo_id`（変更）・`parser`（変更）・`_dispatch`（変更） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/allocation_decide.py` | モジュール | `decide`・`AllocationError`（新設） |
| `plugins/ndf-next/src/ndf/domains/hooks/strict.py` | モジュール | `_remote_slug`（変更。`_origin_slug` の名前を替える） |
| `plugins/ndf-next/src/ndf/domains/relay/context_records.py` | モジュール | `repo_key`（変更） |
| `plugins/ndf-next/src/ndf/domains/project/measure_repo.py` | `Tree` | `Tree.__init__`（変更。`remote` を持つ）・`default_branch`（変更）・`measure_branches`（変更） |
| `plugins/ndf-next/src/ndf/domains/project/measure_flow.py` | モジュール | `sprint_start`・`collect_flow`・`measure`（変更。`tree.remote` を読むだけ） |
| `plugins/ndf-next/src/ndf/domains/project/check.py` | モジュール | `branch_state`（変更。`repo.remote()` を読むだけ） |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | モジュール | `_gh_repo`（変更。`repo.remote()` を読むだけ） |
| `plugins/ndf-next/src/ndf/domains/worktree/` | `cleanup`・`create`・`follow`・`merged`・`repo` | `cleanup._sprint_keep`・`follow.resolve`・`merged.sync_main`・`merged` の片付け・`repo.default_branch`・`repo.contained_in_production`・`create` の取得（変更。`ORIGIN` と `REMOTE` を `Git.remote()` に替えるだけ） |
| `plugins/ndf-next/src/ndf/domains/crossreview/` | `drive`・`repo`・`push` | `drive._resend`・`repo._from_origin`・`push._push`（変更。`Git.remote()` を読むだけ） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/final_gate.py`・`core/converge/worktree.py`・`domains/records/mvv.py`・`domains/release/verify.py` | モジュール | `final_gate.send`・`worktree.fetch_head`・`mvv.signals`・`verify.sync_base`・`verify._apply`（変更。`Git.remote()` を読むだけ） |
| `scripts/ndf-next-parity/parity/trial/probe.py` | モジュール | `texts`・`judge`・`main`（変更。プラグインの根を引数で受ける） |
| `scripts/ndf-next-parity/parity/table.py` | モジュール | 変えない（置き場からの相対で読むため、移しても形が変わらない） |

「`Git.remote()` を読むだけ」の関数は、`ORIGIN`・`"origin"`・`origin/` を `Git.remote()` の戻り値に替えるだけで、シグネチャ・責務・失敗の扱いを変えない。下の「関数の仕様」には書かない。ただし `Git.remote()` が上げうる `RemoteError` は `GitError` の子なので、今 `GitError` を受けている所で同じく受かる（「異常系の処理」）。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `check_skill_repo_assumptions.is_next_family` | `scripts/check-skill-repo-assumptions.py` | 新設 | `(skills_dir: Path) -> bool` | プラグインが ndf-next の形（Skill の一覧を `manifests/skills.json` に持つ）かを返す | `skills_dir` は `plugins/<名前>/skills` | 真は `skills_dir.parent / "manifests/skills.json"` がファイルであることと同値 | ファイル（有無を見る） |
| `check_skill_repo_assumptions.load_manifest_union` | `scripts/check-skill-repo-assumptions.py` | 変更 | `(skills_dir: Path) -> set[str]` | `manifests/*-skills.txt` の和集合に、`manifests/skills.json` の `skills[].name` を足して返す | 同上 | 戻り値は 2 つの形の名前の和。`skills.json` が無ければ今と同じ値 | ファイル（読む） |
| `check_skill_repo_assumptions.collect_code` | `scripts/check-skill-repo-assumptions.py` | 新設 | `(skills_dir: Path) -> tuple[list[str], list[str], list[str]]` | ndf-next の形のプラグインの `src/**/*.py`・`contracts/**/*.json`・`hooks/*.py` を、プラグインの根からの相対パスで返す | `is_next_family(skills_dir)` が真 | 3 つの並びは辞書順で、`tests`・`experimental` の下を含まない | ファイル（一覧を読む） |
| `check_skill_repo_assumptions.scan_contracts` | `scripts/check-skill-repo-assumptions.py` | 新設 | `(base: Path, rels: list[str], matchers: list[tuple[str, Pattern[str]]]) -> list[Hit]` | JSON のファイルを `scan` と同じく走査し、語 `remote-origin` の当たりのうち `"default": "<値>"` の値の中にあるものを除いて返す | `rels` は JSON のファイル | `remote-origin` 以外の語の当たりは `scan` と同じ。`default` 以外の鍵の値の当たりは除かない | ファイル（読む） |
| `check_skill_repo_assumptions.remote_defaults` | `scripts/check-skill-repo-assumptions.py` | 新設 | `(base: Path, rels: list[str]) -> list[tuple[str, int]]` | `scan_contracts` が除いた `default` の値の当たりを（ファイル・行）で返す | 同上 | 戻り値の件数は、除いた当たりの件数と等しい | ファイル（読む） |
| `check_skill_repo_assumptions.default_problems` | `scripts/check-skill-repo-assumptions.py` | 新設 | `(defaults: list[tuple[str, int]], plugin_root: Path) -> list[str]` | リモート名の既定が `worktree.schema.json` の `/properties/remote/default` の 1 か所だけかを確かめ、外れた理由を返す（I7a） | `defaults` は `remote_defaults` の戻り値 | 空の戻り値は、件数が 1 で、そのファイルが `contracts/schemas/declarations/worktree.schema.json` で、JSON として読んだその位置に値があることと同値 | ファイル（読む） |
| `check_skill_repo_assumptions.owning_skills_dir` | `scripts/check-skill-repo-assumptions.py` | 変更 | `(key: str, skills_dirs: list[Path]) -> Path \| None` | 項目のパスの持ち主のプラグインを返す。根の候補に `src/` と `contracts/` を足す | 今と同じ | `src/`・`contracts/` の下の項目も、そのプラグインに属する | 無し |
| `check_skill_repo_assumptions.scan_family` | `scripts/check-skill-repo-assumptions.py` | 変更 | `(skills_dir: Path) -> tuple[set[str], list[Hit], str, list[Path], list[str]]` | 1 つのプラグインを走査し、5 つ目の戻り値に既定の問題（`default_problems`）を返す | 今と同じ | ndf-next の形でないプラグインの走査の対象・当たり・報告行は今と同じ（I3）。ndf-next の形のプラグインは追加の語と `src/`・`contracts/`・`hooks/*.py` も走査し、報告行に `src`・`contracts` の本数を足す | ファイル（読む） |
| `check_skill_repo_assumptions.print_outside` | `scripts/check-skill-repo-assumptions.py` | 変更 | `(outside: list[Hit]) -> None` | 宣言の外の当たりと、足せば通る項目のファイル名（`allow_file_name`）を標準エラーへ出す | 今と同じ | 当たりの組ごとに 1 行、足す項目のファイル名を出す | 標準エラー |
| `check_skill_repo_assumptions.main` | `scripts/check-skill-repo-assumptions.py` | 変更 | `() -> int` | 走査・既定の問題・例外リストを判定し、0・1・2 を返す | 根で打つ | 2 は 1 より先に返す。既定の問題か `ScanError` があれば 2 | 標準出力・標準エラー・ファイル（読む） |
| `check_ndf_choices.load_baseline` | `scripts/check-ndf-choices.py` | 新設 | `(path: Path) -> dict[str, Any]` | 選べる値の基準を読み、形を確かめて返す | 無し | 戻り値は `version` が 1、`sets` の `id` が一意、`source` が 3 種のどれか、`removed` の各行が `set`・`value`・`issue`（1 以上の整数）・`reason`（空でない文字列）を持ち、`set` が `sets` の `id` にある | ファイル（読む） |
| `check_ndf_choices.read_values` | `scripts/check-ndf-choices.py` | 新設 | `(source: Mapping[str, Any], root: Path) -> set[str]` | 出所の種類ごとに今の値の集合を読む | `source` は `load_baseline` が確かめた形 | `rows` は表の `rows[].<key>`、`enum` は JSON Pointer の先の列挙、`files` は glob に当たるファイルの名前の拡張子を外して `prefix` を外したもの | ファイル（読む） |
| `check_ndf_choices.judge` | `scripts/check-ndf-choices.py` | 新設 | `(baseline: Mapping[str, Any], current: Mapping[str, set[str]]) -> Verdict` | 基準と今の値を照らし、減った値（I4）・誤った例外（I5）・足した値（I6）を分ける | `current` の鍵は `sets` の `id` と同じ | `Verdict.removed` は基準にあり今に無く例外にも無い値、`Verdict.stale` は例外に載り今もある値、`Verdict.added` は今にあり基準に無い値 | 無し |
| `check_ndf_choices.main` | `scripts/check-ndf-choices.py` | 新設 | `(argv: list[str] \| None = None) -> int` | 基準を読み、集合ごとに今の値を読み、判定を出して 0・1・2 を返す | 根で打つ（`--root` で変えられる） | 2 は 1 より先に返す | 標準出力・標準エラー・ファイル（読む） |
| `git.default_remote` | `plugins/ndf-next/src/ndf/adapters/git.py` | 新設 | `() -> str` | `worktree.schema.json` の `/properties/remote/default` を読んで返す（1 回だけ読む） | プラグインの導入物にスキーマがある | 戻り値は空でない文字列 | ファイル（読む） |
| `git.remote_of` | `plugins/ndf-next/src/ndf/adapters/git.py` | 新設 | `(main_dir: Path) -> str` | `<main_dir>/.ndf/worktree.json` の `remote`、無ければ `default_remote()` を返す | `main_dir` はメインディレクトリかその clone の根 | 戻り値は空でなく `-` で始まらない文字列（I7） | ファイル（読む） |
| `git.__getattr__` | `plugins/ndf-next/src/ndf/adapters/git.py` | 新設 | `(name: str) -> str` | 名前 `ORIGIN` を引いたときだけ `default_remote()` を返す | 無し | `ORIGIN` 以外の名前は `AttributeError` | ファイル（`default_remote` を通して読む） |
| `git.Git.remote` | `plugins/ndf-next/src/ndf/adapters/git.py` | 新設 | `(self) -> str` | この作業場所のメインディレクトリ（`worktree list` の先頭）を求め、`remote_of` を返す | `cwd` が git の中 | 戻り値は `remote_of(<メインディレクトリ>)` と等しい | 外部コマンド（`git worktree list`）・ファイル（読む） |
| `git.Git.remote_repo` | `plugins/ndf-next/src/ndf/adapters/git.py` | 変更 | `(self, remote: Optional[str] = None) -> str` | リモートの `<所有者>/<リポジトリ>` を返す。`remote` を省けば `self.remote()` を使う | 今と同じ | 宣言の `remote` を書いたリポジトリでは、そのリモートの URL から決まる | 外部コマンド（`git remote get-url`） |
| `claude.list_plugins` | `plugins/ndf-next/src/ndf/adapters/claude.py` | 新設 | `(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Optional[list[dict[str, Any]]]` | `claude plugin list --json` を打ち、要素の並びを返す。読めなければ `None` | 無し | 戻り値は `None` か、各要素が `id`（文字列）と `enabled`（真偽）を持つ並び | 外部コマンド（`claude plugin list --json`） |
| `claude.disabled_plugins` | `plugins/ndf-next/src/ndf/adapters/claude.py` | 新設 | `(listed: Sequence[Mapping[str, Any]], own: Optional[str]) -> dict[str, bool]` | 一覧のうち外す登録を `{<id>: False}` で返す（I10） | `listed` は `list_plugins` の戻り値（`None` でない） | 鍵は、`enabled` が真で、`id` の `@` の前が `identity.FINAL_NAME` で、`own` と違う登録だけ | 無し |
| `claude.plugin_args` | `plugins/ndf-next/src/ndf/adapters/claude.py` | 変更 | `(root: Path, listed: Callable[[], Optional[list[dict[str, Any]]]] = list_plugins) -> PluginArgs` | 外す登録を一覧から決め、`root` を読む `claude` の引数と、一覧を読めなかった知らせを返す | `root` はプラグインの根 | 一覧を読めれば `notice` は `None`。読めなければ外す登録を空にし、`notice` に理由を入れる（I10a）。外す登録が空なら `--settings` を付けない | 外部コマンド（`listed` を通して） |
| `cli.main` | `plugins/ndf-next/src/ndf/adapters/cli.py` | 変更 | `(argv: list[str]) -> int` | `ndf launch-args` の結果に引数を載せ、知らせがあれば種類 `notice` の行を 1 件足す | 無し | `status` は知らせの有無によらず `ok` | 標準出力・外部コマンド（`plugin_args` を通して） |
| `cli._usage` | `plugins/ndf-next/src/ndf/cli.py` | 変更 | `(name: str) -> str` | 使い方の表示に、`COMMANDS` から `DEVELOPER_COMMANDS` を除いた副命令だけを並べる | 無し | 戻り値に `check`・`manifests` が無い（I11）。`COMMANDS` は変えないため、名前で呼べば動く（I11a） | 無し |
| `route.default_base` | `plugins/ndf-next/src/ndf/core/route/__init__.py` | 変更 | `(repo: Path) -> Optional[str]` | 宣言の `base_branch`、無ければリモート名のリモートの既定ブランチを返す | 今と同じ | 既定ブランチは `refs/remotes/<リモート名>/HEAD` から読み、接頭辞 `<リモート名>/` を外す | 外部コマンド（`git symbolic-ref`）・ファイル（読む） |
| `route.derived` | `plugins/ndf-next/src/ndf/core/route/__init__.py` | 変更 | `(t: Template, given: Mapping[str, Value], cwd: Path) -> dict[str, Value]` | 省いた差し込みに `remote` を足して決める | 今と同じ | テンプレートが `remote` を使い、省かれていて `repo` が決まれば、`remote` は `git.remote_of(<repo>)` | 外部コマンド・ファイル（今と同じ） |
| `command.docs_only` | `plugins/ndf-next/src/ndf/progress/command.py` | 新設 | `(git: Git, rng: str, cwd: Path) -> bool` | 範囲の差分が文書だけかを、工程の条件と同じ判定（`range_diff_kinds` の `docs_only_diff`）で返す（I8a） | `rng` は `<a>...<b>` の形 | 戻り値は `range_diff_kinds(git, rng, cwd)["docs_only_diff"]` と等しい | 外部コマンド（`git diff`）・ファイル（読む） |
| `command._cmd_docs_only` | `plugins/ndf-next/src/ndf/progress/command.py` | 新設 | `(a: argparse.Namespace, cwd: Path) -> dict[str, Any]` | `ndf progress docs-only --base <b> [--remote <r>]` の結果 JSON を作る | `a.base` は空でなく `-` で始まらない | 文書だけなら止まった理由 `not_applicable`、そうでなければ `ok`、git が読めなければ `precondition` | 外部コマンド（`git diff`・`git worktree list`）・ファイル（読む） |
| `command._diff_names` | `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `(git: Git, rng: str, specs: Optional[list[str]]) -> set[str]` | 範囲の差分のパスを返す。`specs` が `None` なら全体、空の並びなら空の集合を返す | 今と同じ | 空の並びで `git diff` を打たない（打つと全体が返り、振る舞いの文書がすべてに当たる） | 外部コマンド（`git diff`） |
| `command.range_diff_kinds` | `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `(git: Git, rng: str, cwd: Path) -> dict[str, bool]` | 差分を宣言の `docs.files`・`docs.behavior` で分ける。`docs.behavior` を省けば振る舞いの文書は無し | 今と同じ | 宣言が無いとき、`.md` だけの差分は Skill の `.md` を含んでも `docs_only_diff` が真（受け入れ条件 11） | 外部コマンド（`git diff`）・ファイル（読む） |
| `command._sprint_range` | `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `(context: Mapping[str, Any], cwd: Path) -> tuple[Git, str]` | ベースブランチが手元に無いとき、`<リモート名>/<ベースブランチ>` を使う | 今と同じ | 宣言の `remote` を書いたリポジトリでは、そのリモートの参照から範囲を作る | 外部コマンド（`git rev-parse`・`git merge-base`） |
| `command.repo_id` | `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `(cwd: Path, root: Path) -> str` | リモート名のリモートの URL（userinfo を外す）、無ければメインディレクトリで識別する | 今と同じ | 宣言が無いリポジトリでは今と同じ値 | 外部コマンド（`git remote get-url`） |
| `allocation_decide.decide` | `plugins/ndf-next/src/ndf/domains/crossrefactor/allocation_decide.py` | 新設 | `(defaults: Mapping[str, Any], declared: Optional[Mapping[str, Any]]) -> dict[str, float]` | 初期値のファイルの `values` に、宣言の `allocation_minutes` を種類ごとに重ねた分を返す（I9） | `defaults` は初期値のファイルを読んだ値、`declared` は `.ndf/estimate.json` を読んだ値か `None` | 戻り値の鍵は初期値のファイルの種類と同じ。宣言にある種類は宣言の値、無い種類は初期値 | 無し |
| `strict._remote_slug` | `plugins/ndf-next/src/ndf/domains/hooks/strict.py` | 変更 | `(repo_root: Path) -> Optional[str]` | git の設定ファイルの `[remote "<リモート名>"]` の URL から `owner/repo` を返す | 今と同じ | リモート名は `git.remote_of(repo_root)`。git を起動しない | ファイル（読む） |
| `context_records.repo_key` | `plugins/ndf-next/src/ndf/domains/relay/context_records.py` | 変更 | `(root: Optional[Path]) -> str` | `remote.<リモート名>.url` から `<owner>__<repo>` を作る | 今と同じ | 宣言が無いリポジトリでは今と同じ値 | 外部コマンド（`git config`）・ファイル（読む） |
| `measure_repo.Tree.__init__` | `plugins/ndf-next/src/ndf/domains/project/measure_repo.py` | 変更 | `(self, root: Path, deadline: float) -> None` | 木の読み取りに加え、`self.remote` に `git.remote_of(root)` を持つ | 今と同じ | `measure_flow` と `measure_repo` の関数は `tree.remote` を読み、`origin` を書かない | ファイル（読む）・外部コマンド（今と同じ） |
| `probe.texts` | `scripts/ndf-next-parity/` | 変更 | `(places: Mapping[str, Any], root: Path) -> dict[str, str]` | プラグインの根 `root` の下の置き場の本文を読む | `root` はプラグインの根 | 既定値を持たない（呼び手が渡す） | ファイル（読む） |
| `probe.main` | `scripts/ndf-next-parity/` | 変更 | `(argv: list[str] \| None = None) -> int` | `--plugin-root`（既定はリポジトリの根の `plugins/ndf-next`）を受け、試走の判定を書く | リポジトリの根で打つ | 置き場を決め打たない | ファイル（読む・書く） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_skill_repo_assumptions.is_next_family` | 一覧の有無を読めない | 権限 | `Path.is_file` | 既定値で続ける（偽。今の NDF と同じ走査になる） | 無し。`load_manifest_union` の読みで落ちる | 後始末なし。再実行できる |
| `check_skill_repo_assumptions.load_manifest_union` | `skills.json` を読めない・形が違う | JSON の誤り・`skills` が並びでない・`name` が文字列でない | `json.loads` と形の確かめ | `ScanError` を上げて止める | `[check-skill-repo-assumptions] <パス> を読めない: <理由>`、終了コード 2 | 後始末なし。直して再実行できる |
| `check_skill_repo_assumptions.collect_code` | ディレクトリが無い | ndf-next の形だが `src/` か `contracts/` が無い | `is_dir` | `ScanError` を上げて止める（走査の範囲の欠けを 0 件と読まない） | `走査の対象が無い: <パス>`、終了コード 2 | 同上 |
| `check_skill_repo_assumptions.scan_contracts` | 本文を読めない | UTF-8 でない | `read_text` | `ScanError` を上げて止める | `<パス> を読めない`、終了コード 2 | 同上 |
| `check_skill_repo_assumptions.remote_defaults` | 本文を読めない | 同上 | 同上 | 同上 | 同上 | 同上 |
| `check_skill_repo_assumptions.default_problems` | 既定が 0 か所か 2 か所以上、または位置が違う | スキーマの `default` を消した・ほかの鍵へ書き足した | 件数と JSON の位置の照合 | 呼び出し元へ返す（`main` が 2 で止める） | `リモート名の既定が <件数> か所にある: <ファイル:行>...（worktree.schema.json の /properties/remote/default の 1 か所にする）`、終了コード 2 | 後始末なし。直して再実行できる |
| `check_skill_repo_assumptions.scan_family` | 上の 5 行のどれか | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `check_skill_repo_assumptions.print_outside` | 出力に失敗 | 標準エラーが閉じている | `print` | 止める（Python の例外のまま） | 終了コード 1 以外になりうる | 再実行できる |
| `check_skill_repo_assumptions.main` | 走査・既定・例外リストの誤りと、宣言の外の当たり | 上の各行と、例外リストの外で語が増えた | 各関数の戻り値 | 2 を先に返し、無ければ 1 | 2: 誤りの一覧。1: 当たりの `path:line: 'word'` と足す項目のファイル名 | 後始末なし。再実行できる |
| `check_ndf_choices.load_baseline` | 基準を読めない・形が違う | JSON の誤り・鍵の欠け・`issue` が整数でない・`reason` が空・`set` が無い集合を指す | 読みと形の確かめ | `BaselineError` を上げて止める | `[check-ndf-choices] <理由>`、終了コード 2 | 後始末なし。直して再実行できる |
| `check_ndf_choices.read_values` | 出所を読めない | 表かスキーマのファイルが無い・JSON Pointer の先が列挙でない・glob が 0 件 | 読みと形の確かめ | `SourceError` を上げて止める（0 件を「全部減った」と読まない） | `集合 <id> の出所を読めない: <理由>`、終了コード 2 | 同上 |
| `check_ndf_choices.main` | 判定に誤りか違反がある | `Verdict.stale`（I5）・`Verdict.removed`（I4）・`Verdict.added`（I6） | `judge` の戻り値 | `stale` があれば 2、無ければ `removed`・`added` で 1 | 2: `例外 <set>/<value> の値がまだある（removed から外す）`。1: `集合 <set> から <value> が減った。減らすなら removed に {"set": "<set>", "value": "<value>", "issue": <番号>, "reason": "<理由>"} を足す`・`集合 <set> に <value> が増えた。sets の values に足す` | 同上 |
| `git.default_remote` | スキーマを読めない・位置に値が無い | 導入物の欠け | `json.loads` と位置の照合 | `RemoteError` を上げる（呼び出し元の `GitError` の受け口で止まる） | 副命令の止まった結果（理由はスキーマのパスと位置） | 導入し直す。後始末なし |
| `git.remote_of` | 宣言の値が違う | `remote` が文字列でない・空・`-` で始まる、または宣言が JSON として読めない | 読みと値の確かめ | `RemoteError` を上げる。宣言のファイルが無いか `remote` が無ければ既定値で続ける | 副命令の止まった結果（`.ndf/worktree.json の remote を直す: <値>`） | 宣言を直して再実行できる |
| `git.__getattr__` | 同上（`default_remote` の失敗） | 同上 | 同上 | `RemoteError` を上げる | `ORIGIN` を import する部品（リリースの 2 ファイル）の起動が止まる | 同上 |
| `git.Git.remote` | メインディレクトリを求められない・宣言の値が違う | git の外・`worktree list` の失敗・`remote_of` の失敗 | `self.run` と `remote_of` | `GitError`（`RemoteError` を含む）を呼び出し元へ返す | 呼び出し元の今の `GitError` の扱いのまま | 再実行できる |
| `git.Git.remote_repo` | リモートが無い・GitHub でない | 宣言の名前のリモートが無い | `git remote get-url` の終了コード | `GitError` を呼び出し元へ返す（今と同じ） | 呼び出し元の今の扱いのまま | 同上 |
| `claude.list_plugins` | 一覧を読めない | `claude` が無い・終了コードが 0 でない・時間切れ・JSON でない・要素の形が違う | `run` の戻り値と `json.loads` | 既定値で続ける（`None` を返す） | 無し（`plugin_args` が知らせにする） | 後始末なし。次の起動で読み直す |
| `claude.plugin_args` | 一覧を読めない | `list_plugins` が `None` | 戻り値 | 既定値で続ける（外す登録を空にし、`notice` に理由を入れる） | `ndf launch-args` の `items` に `{"kind": "notice", "reason": "導入済みのプラグインの一覧を読めず、旧い NDF を外さずに起動する"}`。終了コード 0 | 後始末なし |
| `cli.main` | 引数を作れない | `identity.load` の失敗 | `identity.IdentityError` | 止める（今と同じ） | 止まった結果、`config_broken` の終了コード | 今と同じ |
| `route.default_base` | 既定ブランチを読めない | リモートの HEAD が無い・宣言の `remote` が違う | `git.GitError` | 既定値で続ける（`None`。今と同じ。宣言の誤りもここでは `None` になり、後の手順の `remote` の解決で止まる） | 無し（後の手順が「base が決まらない」で止まる） | 宣言を直して再実行できる |
| `route.derived` | `remote` を決められない | 宣言の `remote` が違う | `git.RemoteError` | 止める（`RouteError` に替える） | `ndf route` の止まった結果（`.ndf/worktree.json の remote を直す`） | 同上 |
| `command.docs_only` | git が差分を読めない | 範囲の参照が無い | `GitError` | 呼び出し元へ返す | 無し（`_cmd_docs_only` が扱う） | 同上 |
| `command._cmd_docs_only` | 差分を読めない・ベースブランチが無い・宣言の `remote` が違う | 取得していない・宣言の誤り | `GitError`（`RemoteError` を含む） | 止める（止まった理由 `precondition`。`invalid` にしない。今の `assess` の終了コード 2 が `precondition` を指すため） | 止まった結果 `precondition`、`exit-codes.json` の `precondition` の終了コード | 取得するか宣言を直して再実行できる |
| `command._diff_names` | git が差分を読めない | 同上 | `GitError` | 呼び出し元へ返す（今と同じ） | 呼び出し元の扱い | 同上 |
| `command.range_diff_kinds` | 同上・宣言を読めない | 同上・`.ndf/project.json` の誤り | `GitError`・`ContractError` | 呼び出し元へ返す（今と同じ） | 呼び出し元の扱い | 同上 |
| `command._sprint_range` | 範囲を作れない | ベースブランチがどこにも無い・宣言の `remote` が違う | `GitError`・`FactUnknown` | 呼び出し元へ返す（今と同じ） | 工程の条件の止まった結果 | 同上 |
| `command.repo_id` | リモートの URL を読めない | リモートが無い・宣言の `remote` が違う | `GitError` | 既定値で続ける（メインディレクトリで識別。今と同じ） | 無し | 後始末なし |
| `strict._remote_slug` | 設定か宣言を読めない | 権限・宣言の誤り | `OSError`・`git.RemoteError` | 既定値で続ける（`None`。hook はマージを拒む側へ倒れる。今と同じ） | hook の拒否の文（リポジトリ「不明」） | 宣言を直して再実行できる |
| `context_records.repo_key` | URL を読めない | リモートが無い・宣言の誤り | `git.GitError`（`RemoteError` を含む） | 既定値で続ける（`"unknown"`。今と同じ） | 無し | 後始末なし |
| `measure_repo.Tree.__init__` | 宣言の `remote` が違う | 宣言の誤り | `git.RemoteError` | 呼び出し元へ返す（`ndf project measure` が止まった結果にする） | 止まった結果（`.ndf/worktree.json の remote を直す`） | 宣言を直して再実行できる |
| `probe.texts` | 置き場を読めない | 根の誤り | `OSError` | 呼び出し元へ返す（今と同じ） | テストか試走が落ちる | 再実行できる |
| `probe.main` | 根が無い | `--plugin-root` の誤り | `is_dir` | 止める | `プラグインの根が無い: <パス>`、終了コード 2 | 再実行できる |

`allocation_decide.decide` は副作用を持たないため上の表に無い。形の誤り（初期値のファイルに `initial` が無い・`values` の分が 0 以下・宣言に初期値に無い種類がある）は `AllocationError` を上げる。呼び手（M4 の配分の表の部品）が止まった結果にする。

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check_skill_repo_assumptions.scan_family` | ndf-next の形でない | 今と同じ走査（Markdown・スクリプト・`hooks/*.json`・エージェント）。追加の語を当てない |
| `check_skill_repo_assumptions.scan_family` | ndf-next の形で、Skill の Markdown かエージェント | 今の文書の語に、`repo-name`・`org-name`・`marketplace-entry`・`parity-table` を足して当てる |
| `check_skill_repo_assumptions.scan_family` | ndf-next の形で、`src/**/*.py` か `hooks/*.py` | 今のスクリプトの語に、上の 4 語と `remote-origin` を足して当てる |
| `check_skill_repo_assumptions.scan_family` | ndf-next の形で、`contracts/**/*.json` | `hooks/*.py` と同じ語を `scan_contracts` で当てる（`remote-origin` の `default` の値を除く） |
| `check_skill_repo_assumptions.main` | 走査の範囲の欠け・`ScanError`・既定の問題・例外リストの誤り | この順に見て、最初に当たったもので 2 |
| `check_skill_repo_assumptions.main` | 上が無く、宣言の外の当たりがある | 1 |
| `check_skill_repo_assumptions.main` | どれも無い | 0 |
| `check_ndf_choices.judge` | 値が基準にあり、今にあり | 何もしない |
| `check_ndf_choices.judge` | 値が基準にあり、今に無く、例外に無い | `removed`（I4） |
| `check_ndf_choices.judge` | 値が基準にあり、今に無く、例外にある | 何もしない（減らす例外が受ける） |
| `check_ndf_choices.judge` | 値が例外にあり、今にある | `stale`（I5） |
| `check_ndf_choices.judge` | 値が今にあり、基準に無い | `added`（I6） |
| `git.remote_of` | 宣言のファイルが無い・`remote` が無い | `default_remote()` |
| `git.remote_of` | `remote` が空でない文字列で `-` で始まらない | その値 |
| `git.remote_of` | 上のどれでもない（形の誤り・JSON の誤り） | `RemoteError` |
| `claude.disabled_plugins` | `enabled` が真・`@` の前が `FINAL_NAME`・`own` と違う | 外す |
| `claude.disabled_plugins` | `@` の前が `own` と同じ（入れ替えの後の自分） | 外さない |
| `claude.disabled_plugins` | `enabled` が偽か無い・`@` の前が別の名前 | 外さない |
| `command._cmd_docs_only` | 変えたファイルが 0 件 | `ok`（文書だけとしない。`diff_kinds` の `bool(files)`） |
| `command._cmd_docs_only` | すべて `docs.files` に当たり、`docs.behavior` に 1 件も当たらない | 止まった理由 `not_applicable`（テンプレートの `on_cause` が `review` へ進める） |
| `command._cmd_docs_only` | 上のどちらでもない | `ok`（`test-all` へ進む） |
| `allocation_decide.decide` | `declared` が `None`・`allocation_minutes` が無い | 初期値のまま |
| `allocation_decide.decide` | `allocation_minutes` の種類が初期値にある | その種類だけ宣言の値 |
| `allocation_decide.decide` | `allocation_minutes` に初期値に無い種類がある | `AllocationError` |

- 繰り返しの終わる条件: 2 つの検査はファイルの一覧と集合の要素を 1 回ずつ回るだけで、打ち直しを持たない。`claude.list_plugins` は打ち直さない（1 回で読めなければ知らせにする）
- 計算量の注意: ハードコードの検査は、ndf-next の `src/` の Python と `contracts/` の JSON を足しても、語の和で行を先に絞る今の形のまま 1 ファイル 1 回読む。走査を広げる前と後の所要を `time` で測って PR に載せる（設計文書の「非機能の実現方式」）。`Git.remote()` は呼ぶたびに `git worktree list` を 1 回打つ。1 つの関数で 2 回以上使うときは、戻り値を変数に持って打ち直さない

テンプレート `check` の `assess` の終了コード: 設計文書の入出力の契約は、文書だけを 1、それ以外を 0、git の失敗を 2 と書く。ndf の副命令は結果 JSON を出し、プランのランナーは結果 JSON があれば `cause.kind` で行き先を決める（`core/plan/steps.py`）。このため `ndf progress docs-only` は結果 JSON の `not_applicable`・`ok`・`precondition` で 3 つを表す。`assess` の `exit_cause` は外す。行き先（文書だけなら `review`、git の失敗なら人へ戻す）は今と同じである。

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `NEXT_DOC_WORDS` | 定数 | `scripts/check-skill-repo-assumptions.py` | `repo-name`: `\bai-plugins\b`、`org-name`: `\bdevbasex\b`、`marketplace-entry`: `ndf@`、`parity-table`: `parity/features\.json\|機能の対応表\|ndf-next-parity` |
| `NEXT_CODE_WORDS` | 定数 | `scripts/check-skill-repo-assumptions.py` | `NEXT_DOC_WORDS` に、`remote-origin`: `remote "origin"\|"origin"\|\borigin/\|remotes/origin\b\|remote\.origin\b` と、`parity-table` の `ndf\.parity` を足したもの |
| `KNOWN_WORDS` | 定数 | `scripts/check-skill-repo-assumptions.py` | 今の 3 つの語の表と `NEXT_CODE_WORDS` の鍵の和（例外リストの `name` の検査に使う） |
| `NEXT_MANIFEST` | 定数 | `scripts/check-skill-repo-assumptions.py` | `manifests/skills.json` |
| `REMOTE_DEFAULT_AT` | 定数 | `scripts/check-skill-repo-assumptions.py` | `("contracts/schemas/declarations/worktree.schema.json", "/properties/remote/default")`。スキーマの置き場を検査が知る唯一の所 |
| `scripts/ndf-choices.json` | 設定値 | `scripts/ndf-choices.json` | 設計文書の「選べる値の基準の初期の中身」の 3 集合、`removed` は空 |
| `Verdict` | 型 | `scripts/check-ndf-choices.py` | `removed`・`stale`・`added` の 3 つの `list[tuple[str, str]]`（集合の `id` と値） |
| `remote` の既定 | 設定値 | `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` の `/properties/remote/default` | `origin`（I7a。コードに定数で持たない） |
| `REMOTE_SCHEMA` | 定数 | `plugins/ndf-next/src/ndf/adapters/git.py` | `identity.ROOT` からの相対 `contracts/schemas/declarations/worktree.schema.json` と、位置 `/properties/remote/default`。リモート名の値は持たない |
| `ORIGIN` | 定数（遅れて解決） | `plugins/ndf-next/src/ndf/adapters/git.py` | `default_remote()` の値。#3615 がリリースの 2 ファイルを直すときに消す（決定 5） |
| `PluginArgs` | 型 | `plugins/ndf-next/src/ndf/adapters/claude.py` | `NamedTuple(args: list[str], notice: Optional[str])` |
| `PLUGIN_LIST_TIMEOUT` | 定数 | `plugins/ndf-next/src/ndf/adapters/claude.py` | 30 秒。所要の実測で見直す（設計文書の「未確認のまま残ること」） |
| `DISABLED_PLUGINS` | 定数 | `plugins/ndf-next/src/ndf/adapters/claude.py` | 消す（登録名の決め打ちをやめる） |
| `DEVELOPER_COMMANDS` | 定数 | `plugins/ndf-next/src/ndf/cli.py` | `frozenset({"check", "manifests"})` |
| `BEHAVIOR_DOCS` | 定数 | `plugins/ndf-next/src/ndf/progress/command.py` | `()`（空。ai-plugins の値は `.ndf/project.json` の `docs.behavior` へ移す） |
| `docs.behavior` | 設定値 | `.ndf/project.json` | `["**/skills/**/*.md", "**/agents/*.md"]` |
| `allocation_minutes` | 設定値 | `plugins/ndf-next/contracts/schemas/declarations/estimate.schema.json` | 鍵は種類、値は 0 より大きい数。種類の名前はスキーマに並べず（`additionalProperties` で数だけを受ける）、初期値のファイルの `values` の鍵だけが持つ。初期値に無い種類は `allocation_decide.decide` が止める |
| `initial` | 設定値 | `plugins/ndf-next/skills/cross-refactoring/data/allocation-defaults.json` | `true` |
| `X4` | 設定値 | `plugins/ndf-next/src/ndf/checks/children.json` | `{"issue": 3617, "packages": [], "needs": [], "order": 9, "lane": "lgeneric", "closed": false}`（決定 12） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| CI の 2 つの検査と、ほかのジョブ | リポジトリのファイル（読むだけ） | 要らない（どちらも書かない） | 何度打っても同じ結果。中断したら打ち直す |
| 並列のレーンの `ndf` の副命令が `remote_of` を読む | `.ndf/worktree.json`（読むだけ） | 要らない | 読むだけで状態を持たない |
| 並列のレーンの `Skill` の起動が `claude plugin list --json` を打つ | Claude Code の導入の記録（読むだけ） | 要らない（`claude` の CLI が読むだけ） | 読めなければ知らせにして起動する。次の起動で読み直す |
| hook（`strict`）と副命令 | `.git/config`・`.ndf/worktree.json`（読むだけ） | 要らない | 読むだけ |
| 機能の対応表の試走（`probe.main`） | `scripts/ndf-next-parity/parity/trial/<日付>.json` | 1 人で打つ（試走は入れ替えの前に 1 回。今と同じ） | 同じ日付のファイルを書き直す。中断したら打ち直す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check_skill_repo_assumptions.main` | `--report` の走査の規模（プラグインごとに Skill・Markdown・スクリプト・`src`・`contracts`・hook・エージェントの本数と当たりの件数） | 標準出力 | 無し（走査するのは同梱物で、秘密を含まない） |
| `check_skill_repo_assumptions.print_outside` | 当たりの `path:line: 'word'`・行・足す項目のファイル名 | 標準エラー | 同上 |
| `check_ndf_choices.main` | 集合ごとの今の値の件数と、減った値・増えた値・誤った例外と、足す 1 行の JSON | 標準出力（件数）・標準エラー（違反） | 無し |
| `cli.main` | 起動の引数と知らせ | 結果 JSON の `items` | `claude plugin list` の出力そのもの（導入先のパスを含むため、理由の 1 文だけを出す） |
| `command._cmd_docs_only` | 範囲・変えたファイルの件数・文書の件数・振る舞いの文書の件数 | 結果 JSON の `summary` と `metrics` | ファイルの名前の一覧（件数だけ） |
| `command.repo_id` | リポジトリの識別 | 進行表の DB | URL の userinfo（今と同じく外す） |
| `strict._remote_slug` | `owner/repo` | hook の拒否の文 | URL の userinfo（`slug_of_url` が `owner/repo` だけを返す） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `check_skill_repo_assumptions.is_next_family` | `scripts/lib/plugin_families.py` | `grep -n 'def ' scripts/lib/plugin_families.py` | 使えない理由: プラグインの一覧を返す部品で、Skill の一覧の形（`*-skills.txt` か `skills.json`）を見分けない |
| `check_skill_repo_assumptions.collect_code` | 同じファイルの `collect_scripts` | 同じファイルを読んだ | 使う（`.py` は `collect_scripts(root / "src")` の戻り値を `.py` に絞る。JSON は同じ形の 1 行を足す） |
| `check_skill_repo_assumptions.scan_contracts` | 同じファイルの `scan`・`iter_matching_lines` | 同じファイルを読んだ | 使う（`scan` の戻り値から `default` の値の中の `remote-origin` を除くだけにする） |
| `check_skill_repo_assumptions.remote_defaults` | 同上 | 同上 | 使う（`scan_contracts` が除いたものを返す） |
| `check_skill_repo_assumptions.default_problems` | 今の NDF の `plugins/ndf/scripts/lib/json_pick.py` の `resolve_pointer` | `grep -rn 'def .*pointer' scripts/lib plugins/ndf/scripts/lib`（当たりは `json_pick.py` の `parse_pointer`・`resolve_pointer` と、別の役割の `hook_python.py`・`state_get.py`。根の `scripts/lib` には無い） | 使えない理由: 根の検査は今の NDF のリリース対象を import しない（入れ替え S2 で今の NDF の置き場が消える）。位置は固定の 3 階層なので辞書を 3 回引く |
| `check_skill_repo_assumptions.ScanError` | `scripts/lib/allowlist.py` の `AllowError` | 同じファイルを読んだ | 使えない理由: 例外リストの誤りを表す型で、走査の範囲の誤りと分けて出す |
| `check_ndf_choices.load_baseline` | `scripts/lib/allowlist.py` の `load_allow` | 同じファイルを読んだ | 使えない理由: 1 項目 1 ファイルの読み手で、1 つの JSON を読む形と違う |
| `check_ndf_choices.read_values` | ndf-next の `ndf.contracts.load_json`・今の NDF の `json_pick.resolve_pointer` | `grep -n 'def load_json' plugins/ndf-next/src/ndf/contracts/__init__.py`・上の行の検索 | 使えない理由: 根の検査は ndf-next の依存の環境を要らないようにし（決定 4）、今の NDF のリリース対象も import しない。JSON Pointer（RFC 6901。`~0`・`~1` の戻しを含む）の解決は、このファイルに数行で持つ |
| `check_ndf_choices.judge` | `jsonschema`・semgrep・betterer | 設計文書の「外部の調査」（LIB2・LIB3） | 使えない理由: 値の集合の後退を見る部品が無い（決定 4） |
| `check_ndf_choices.main` | 同じ根の `scripts/check-skill-repo-assumptions.py` の `main` | 同じファイルを読んだ | 使えない理由: 終了コードの順（2 を 1 より先）だけを倣い、判定は別 |
| `check_ndf_choices.BaselineError` | `AllowError` | 同上 | 使えない理由: 同上 |
| `check_ndf_choices.SourceError` | 同上 | 同上 | 使えない理由: 同上 |
| `check_ndf_choices.Verdict` | 無し | — | 新しく持つ |
| `git.default_remote` | `ndf.contracts.load_json` | `grep -n '^from\|^import' plugins/ndf-next/src/ndf/contracts/__init__.py` | 使えない理由: `jsonschema` を読み込み、hook の起動を延ばす。`json` と `identity.ROOT` で読む |
| `git.remote_of` | `domains/worktree/declaration.py` の `load`・`read_object` | `grep -n '^def ' plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 使えない理由: 領域の層の部品で、アダプタから読むと依存が逆向きになる。個人設定を重ねる読み方も、リモート名には当てない（決定 5） |
| `git.__getattr__` | 無し | — | 新しく持つ（PEP 562 のモジュールの `__getattr__`） |
| `git.Git.remote` | `Git.worktrees`・`Git.common_dir` | 同じファイルを読んだ | 使う（`worktrees()[0].path` をメインディレクトリにする。`common_dir().parent` はサブモジュールで外れる） |
| `git.RemoteError` | `GitError` | 同上 | 使う（子の型にし、今の受け口で受かるようにする） |
| `claude.list_plugins` | `adapters/agents.py` の起動の部品・`_run_claude` | `grep -n '^def ' plugins/ndf-next/src/ndf/adapters/agents.py plugins/ndf-next/src/ndf/adapters/claude.py` | 使えない理由: 起動の枠（`launch_slot`）と結果の読み手はプロンプトを渡す起動のもので、一覧を 1 回読む CLI には重い。`subprocess.run` を差し替えられる引数で受ける |
| `claude.disabled_plugins` | `identity.name_of`・`identity.FINAL_NAME` | `grep -n 'FINAL_NAME\|def name_of' plugins/ndf-next/src/ndf/identity.py` | 使う（旧い NDF の名前と自分の名前をここから読む） |
| `claude.PluginArgs` | 無し | — | 新しく持つ |
| `command.docs_only` | `range_diff_kinds`・`diff_kinds` | 同じファイルを読んだ | 使う（判定を別に持たない。I8a） |
| `command._cmd_docs_only` | `_cmd_restore` の store を使わない分岐 | 同じファイルの `_dispatch` を読んだ | 使う（`restore` と同じく store を開かずに受ける） |
| `allocation_decide.decide` | `domains/crossrefactor/budget_decide.py` | `grep -n '^def ' plugins/ndf-next/src/ndf/domains/crossrefactor/*_decide.py` | 使えない理由: 予算の判断で、種類ごとの分を重ねる関数が無い |
| `allocation_decide.AllocationError` | 無し | 同上 | 新しく持つ |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check_skill_repo_assumptions.load_manifest_union` | `skills.json` の名前を返す（受け入れ条件 1） | `*-skills.txt` と `skills.json` の両方があれば和 | 形の違う `skills.json` で 2 |
| `check_skill_repo_assumptions.scan_family` | ndf-next の Skill の件数がディレクトリの数と等しい | `plugins/ndf/skills`・`plugins/playwright-kit/skills` の件数と当たりが広げる前と同じ（I3） | `src/` が無い ndf-next の形で 2 |
| `check_skill_repo_assumptions.scan_contracts` | `description` の `"origin"` は当たる | `"default": "origin"` は当たらない。同じ行の別の鍵の値は当たる | 読めない JSON で 2 |
| `check_skill_repo_assumptions.default_problems` | 1 か所で空 | 0 か所・2 か所で理由を返す | 別のファイルの 1 か所で理由を返す |
| `check_skill_repo_assumptions.main` | 例外リストどおりで 0 | 件数どおりで 0・1 超えで 1（I1）・1 少ないで 2（I2） | 理由の無い項目・`pending` に Issue の番号が無い項目で 2 |
| `check_skill_repo_assumptions.print_outside` | 足す項目のファイル名が出る | 同じ組の 2 件で 1 行 | — |
| `check_ndf_choices.judge` | 基準と今が等しいと空 | 例外が受ける減少は空 | 減少で `removed`（I4）・例外の値が残ると `stale`（I5）・増加で `added`（I6） |
| `check_ndf_choices.read_values` | 3 種の出所の値（受け入れ条件 7） | `files` の接頭辞と拡張子を外す | 出所が無い・glob が 0 件で `SourceError` |
| `check_ndf_choices.load_baseline` | 初期の中身を読める | `removed` が空 | Issue の番号の無い例外・空の理由・無い集合を指す例外で `BaselineError` |
| `check_ndf_choices.main` | 0 | 例外を足すと 0（受け入れ条件 6） | 取り込み方の表の行を外すと 1（受け入れ条件 5）、誤った例外で 2 |
| `git.remote_of` | 宣言の `upstream` を返す（受け入れ条件 8） | 宣言が無い・`remote` が無いと `origin`（受け入れ条件 9） | 空・`-` で始まる・数で `RemoteError` |
| `git.default_remote` | スキーマの値を返す | — | スキーマの位置に値が無いと `RemoteError` |
| `git.Git.remote` | linked worktree からでもメインディレクトリの宣言を読む | — | git の外で `GitError` |
| `git.Git.remote_repo` | 省くと宣言のリモートの URL | 引数で渡せばその名前 | 無いリモートで `GitError` |
| `claude.list_plugins` | JSON の並びを返す | 空の並び | 終了コードが 0 でない・JSON でない・時間切れで `None` |
| `claude.disabled_plugins` | `ndf@<別の名前>`（有効）を外す（受け入れ条件 13） | 自分の名前と同じ・無効の登録は外さない | — |
| `claude.plugin_args` | 外す登録があれば `--settings` を付ける | 外す登録が空なら付けない | 一覧が `None` なら外さず `notice` を入れる（I10a） |
| `cli.main` | 知らせが無ければ `items` は引数の 1 行 | — | 知らせがあれば `notice` の行が 1 件増え、`status` は `ok` |
| `cli._usage` | `check`・`manifests` が無い（I11） | — | `ndf check` は今と同じ結果（I11a） |
| `route.derived` | `remote` を宣言から補う | テンプレートが `remote` を使わなければ足さない | 宣言の誤りで `RouteError` |
| `route.default_base` | `refs/remotes/<リモート名>/HEAD` から読む | 宣言の `base_branch` が先 | HEAD が無いと `None` |
| `command.docs_only` | `docs.files` を書いたリポジトリで、すべて当たると真（受け入れ条件 10） | 1 つ外れると偽・変えたファイルが 0 件で偽 | git の失敗で `GitError` |
| `command._cmd_docs_only` | 文書だけで `not_applicable`、そうでなければ `ok` | `--remote` を省くと宣言のリモート | 参照が無いと `precondition` |
| `command._diff_names` | パターンに当たるパス | 空の並びで空の集合（`git diff` を打たない） | git の失敗で `GitError` |
| `command.range_diff_kinds` | 宣言が無いと Skill の `.md` だけの差分も文書だけ（受け入れ条件 11） | 宣言の `docs.behavior` に当たると文書だけでない | 宣言の誤りで `ContractError` |
| `command._sprint_range` | 手元に無いベースブランチを宣言のリモートから読む | 手元にあれば手元 | どこにも無いと `GitError` |
| `command.repo_id` | 宣言のリモートの URL | userinfo を外す | リモートが無いとメインディレクトリ |
| `allocation_decide.decide` | 宣言の値が初期値より先に効く（受け入れ条件 12） | 省いた種類は初期値・宣言が `None` なら初期値のまま | 初期値に無い種類・`initial` の無い初期値で `AllocationError` |
| `strict._remote_slug` | 宣言のリモートの URL の `owner/repo` | 宣言が無いと `origin` の節 | 設定を読めないと `None` |
| `context_records.repo_key` | 宣言のリモートの URL の鍵 | git の外で `_local` | 読めないと `unknown` |
| `measure_repo.Tree.__init__` | `tree.remote` が宣言の値 | 宣言が無いと `origin` | 宣言の誤りで `RemoteError` |
| `probe.texts` | 渡した根の下を読む（受け入れ条件 19） | — | 無い置き場で `OSError` |
| `probe.main` | `--plugin-root` を省くとリポジトリの根の `plugins/ndf-next` | — | 無い根で 2 |
