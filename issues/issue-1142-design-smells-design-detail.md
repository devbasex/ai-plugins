# #1142 の詳細設計: AI が陥りやすい作りを数え、増やす変更を落としながら減らす

設計文書 [issue-1142-design-smells.md](issue-1142-design-smells.md) の「構成要素」と決定 36〜44 を、ファイルと関数の粒度へ下ろした文書である。
書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。
全体の設計は [issue-1142-design.md](issue-1142-design.md) 、要求は [issue-1142-requirements.md](issue-1142-requirements.md) にある。

**移行ステップは設計文書の「直す順序と触るファイル」の表が持つ**（A0・A1a・A1b・A2・A3a〜A3g・E9）。
この文書は移行ステップごとに、新設する関数と、シグネチャ・責務・失敗の扱いを変える関数を書く。
A3a〜A3g は数百の箇所を同じ規則で直すため、関数を 1 つずつは挙げず、「処理の手順」に直し方の判定表を置く。

**後方互換の経路は作らない。** 検査の結果 JSON・基準ファイル・規則のデータは最初の形から今の形だけを持つ。
古い形を読む既存の経路は A1a・A1b で消す（決定 40）。
テストは各移行ステップの PR で「単体テストの観点」から作り、置き換えた範囲の古いテストを同じ PR で消す。

用語の対応（本文は左の語で書き、識別子は括弧で添える）:

| 語 | 識別子 | 意味 |
| --- | --- | --- |
| 検査 | `scripts/check-ai-smells.py` | 兆候を数え、基準と merge-base と比べて合否を出すエントリポイント |
| 規則のデータ | `scripts/ai-smells-rules.json` | 採る規則・しきい値・印を許す規則・境界の種類・例外の型の分類 |
| 自作の規則 | `scripts/structure_checks/smells_rules.py` | ruff に無い規則を構文木で数えるモジュール |
| 兆候の基準ファイル | `scripts/ai-smells-baseline/<名前>.json` | 元のファイル 1 本の規則ごとの許す数 |
| 境界の印 | `# noqa: <規則> <境界の種類>: <理由>` | 兆候を外す行末の印。`NDF201` は `上限: <件数と根拠>` も使える |
| ヒット | `Hit` | 1 つの規則が 1 つの行で見つけた兆候 |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除）:

```text
（リポジトリのルート）
├── .github/workflows/script-structure.yml
├── .ndf/project.json
└── scripts/
    ├── check-ai-smells.py *
    ├── ai-smells-rules.json *
    ├── ai-smells-baseline/ *            （元のファイル 1 本に 1 つ。A0 の --write-baseline が書く）
    ├── contracts_lib/ratchet.py
    ├── lib/allowlist.py                 （読むだけ。allow_file_name を使う）
    ├── structure_checks/smells_rules.py *
    └── tests/
        ├── test_check_ai_smells.py *
        └── test_smells_rules.py *
plugins/ndf/
├── contracts/entrypoints/
│   ├── plugins__ndf__scripts__bundle-close.py.json -
│   ├── plugins__ndf__scripts__mission-close.py.json -
│   ├── plugins__ndf__scripts__mission-state.py.json -
│   └── plugins__ndf__scripts__phase-steps.py.json -
└── scripts/
    ├── bundle-close.py -  mission-close.py -  mission-state.py -  phase-steps.py -
    ├── data/legacy-values.json -
    ├── lib/legacy_names.py -  lib/legacy_values.py -
    ├── lib/gh_rest.py                   （numbered_nodes を足す）
    ├── merged-steps.py  release-verification-steps.py  sprint_close_lib/collect.py
    ├── wait-notify.py  lib/gate_answer.py  lib/dist_record.py  supervise_lib/sprint_waves.py
    └── （A1a・A1b・A2・A3 の呼び出し側。下の表）
```

**依存は一方向で、循環しない。**

| モジュール | import するもの |
| --- | --- |
| 検査 | 自作の規則・`scripts/lib/allowlist.py`・`scripts/contracts_lib/ratchet.py` |
| 自作の規則 | 標準ライブラリ（`ast`・`tokenize`・`json`）だけ。検査を import しない |
| `ratchet.py` | 新しいモジュールを import しない |
| `gh_rest.py`（プラグインの側） | `gh_call`・`gh_quota`・`gh_fields` だけ（今の向きを変えない）。`numbered_nodes` は `gh_call.gh` だけを使う |

A1a・A1b は import を消すだけで、新しい向きを足さない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `scripts/check-ai-smells.py` | 新設 | 兆候を数えて基準と merge-base と比べ、結果 JSON を 1 行出す（A0） | `scripts/structure_checks/smells_rules.py`・`scripts/lib/allowlist.py`・`scripts/contracts_lib/ratchet.py`・uv の `lint` グループの ruff・git |
| `scripts/structure_checks/smells_rules.py` | 新設 | 規則のデータを読み、自作の規則と境界の印を構文木と字句で読む（A0） | 無し |
| `scripts/ai-smells-rules.json` | 新設 | 規則・しきい値・印を許す規則・境界の種類・例外の型の分類（A0） | 無し |
| `scripts/ai-smells-baseline/` | 新設 | 兆候の基準ファイルの置き場（A0。以後は各 PR が `--write-baseline` で書き換える） | 無し |
| `scripts/contracts_lib/ratchet.py` | 変更 | 初回の導入の判定 `initial` に検査の名前を渡せるようにする（A0） | git |
| `scripts/tests/test_check_ai_smells.py` | 新設 | 検査の振る舞いを一時の git リポジトリで確かめる（A0） | `scripts/check-ai-smells.py`・uv |
| `scripts/tests/test_smells_rules.py` | 新設 | 自作の規則と印の読み取りを確かめる（A0） | `scripts/structure_checks/smells_rules.py` |
| `.github/workflows/script-structure.yml` | 変更 | `script-structure-check` に検査の手順を足し、履歴をすべて取る（A0） | `scripts/check-ai-smells.py` |
| `.ndf/project.json` | 変更 | 静的解析の suite `ai-smells` を宣言する（A0） | 無し |
| `plugins/ndf/scripts/lib/legacy_names.py` | 削除 | 旧名の読み替え（A1a） | — |
| `plugins/ndf/scripts/bundle-close.py` | 削除 | 旧名のエントリポイント（A1a） | — |
| `plugins/ndf/scripts/mission-close.py` | 削除 | 旧名のエントリポイント（A1a） | — |
| `plugins/ndf/scripts/mission-state.py` | 削除 | 旧名のエントリポイント（A1a） | — |
| `plugins/ndf/scripts/phase-steps.py` | 削除 | 移した先へ渡すだけの古いエントリポイント（A1a） | — |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__bundle-close.py.json` | 削除 | 消したエントリポイントの契約（A1a） | — |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__mission-close.py.json` | 削除 | 同上 | — |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__mission-state.py.json` | 削除 | 同上 | — |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__phase-steps.py.json` | 削除 | 同上 | — |
| `plugins/ndf/scripts/wait-notify.py` | 変更 | 古いエントリポイント `--runtime` を消し、通知の送信だけを持つ（A1a） | `plugins/ndf/scripts/lib/notify.py` |
| `plugins/ndf/scripts/lib/gate_answer.py` | 変更 | 承認ゲートの名前を今の名前だけで比べる（A1a） | 無し |
| `plugins/ndf/scripts/lib/dist_record.py` | 変更 | リリース記録を今の見出しだけで読む（A1a・A1b） | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | スプリントブランチを今の接頭辞だけで 1 回で探す（A1a） | git |
| `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | リリースプランのフェーズの名前を、プランを組む側の定数から引く（A1b） | `plugins/ndf/scripts/supervise_lib/delivery_templates.py` |
| `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | 変更 | リリースプランのフェーズの名前の組 `RELEASE_PHASES` を持つ（A1b） | 無し |
| `plugins/ndf/scripts/lib/legacy_values.py` | 削除 | 古い値の読み替え（A1b） | — |
| `plugins/ndf/scripts/data/legacy-values.json` | 削除 | 古い値の表（A1b） | — |
| `plugins/ndf/scripts/lib/gh_rest.py` | 変更 | 番号ごとの GraphQL のエイリアスのクエリ `numbered_nodes` を持つ（A2） | `plugins/ndf/scripts/lib/gh_call.py` |
| `plugins/ndf/scripts/merged-steps.py` | 変更 | 調べる PR を開いた PR の一覧 1 回から選ぶ（A2） | `plugins/ndf/scripts/lib/gh_rest.py` |
| `plugins/ndf/scripts/release-verification-steps.py` | 変更 | プラグインごとの変更を 1 回の `git diff` から分ける（A2） | git |
| `plugins/ndf/scripts/sprint_close_lib/collect.py` | 変更 | 参照だけの Issue の状態を 1 回のクエリで読む（A2） | `plugins/ndf/scripts/lib/gh_rest.py` |

次のファイルは、設計文書の「直す順序と触るファイル」の表が持つ。ここでは関数の責務を変えるものだけを上に挙げた。

- A1a・A1b の呼び出し側（import と読み替えの呼び出しを消すだけのファイル）
- A2 の残り 18 本
- A3a〜A3g のファイル

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `scripts/check-ai-smells.py` | モジュール | `main`・`list_targets`・`run_ruff`・`apply_marks`・`load_baselines`・`compare`・`base_totals`・`ratchet_items`・`write_baselines`・`baseline_name`・`emit`（すべて新設） |
| `scripts/structure_checks/smells_rules.py` | モジュール。型 `Rules`・`Rule`・`Mark`・`FileScan`・`Hit`（新設） | `load_rules`・`scan_file`・`read_marks`・`mark_problem`・`loop_calls`・`elif_chains`・`quiet_handlers`・`suppress_calls`・`fallbacks`・`long_functions`（すべて新設） |
| `scripts/contracts_lib/ratchet.py` | モジュール | `initial`（変更） |
| `plugins/ndf/scripts/wait-notify.py` | モジュール | `main`（変更）・`run_legacy_hook`（削除） |
| `plugins/ndf/scripts/lib/gate_answer.py` | モジュール | `replaces`（変更）・`canonical`・`accept`・`_CANONICAL`（削除） |
| `plugins/ndf/scripts/lib/dist_record.py` | モジュール | `parse_record`（変更）・`DIST_HEADINGS`（今の見出し 1 つの組へ） |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | モジュール | `_branch_of`（変更） |
| `plugins/ndf/scripts/supervise_lib/queue.py` | モジュール | `RELEASE_PHASES` を `delivery_templates` から引く（名前だけ）・フェーズを読む関数から読み替えを外す（名前だけ） |
| `plugins/ndf/scripts/supervise.py`・`plugins/ndf/scripts/mvv-gate.py`・`plugins/ndf/scripts/project-mvv.py` | モジュール | `main`（旧名の引数の読み替えを外す。名前だけ） |
| `plugins/ndf/scripts/sprint-state.py` | モジュール | `plan_kind`（旧キー `持ち場` と古い値の読み替えを外す）・承認ゲートの引数の型（`gate_answer.accept` を外す）（名前だけ） |
| `plugins/ndf/scripts/supervise_lib/state.py` | クラス `RunState` | `sprint_mvv_of`（計画の `スプリント状態` だけを読む。名前だけ） |
| `plugins/ndf/scripts/lib/` の A1a・A1b の呼び出し側 | モジュール | `estimate_records`・`slow_step`・`stage_skills`・`transcript_agents`・`pr_mode`・`project_mvv_signals`・`parallel_repo`・`parallel_status`・`parallel_external`・`sprint_loop_record`・`sprint_loop_gate`・`sprint_loop_gate2`・`sprint_mvv`・`gate_rejections` の読み替えの呼び出しを外す（名前だけ） |
| `plugins/ndf/scripts/lib/gh_rest.py` | モジュール | `numbered_nodes`（新設）・`pr_head_branches`（`numbered_nodes` を呼ぶ形へ。名前だけ） |
| `plugins/ndf/scripts/merged-steps.py` | モジュール | `probe_prs`（変更）・`cmd_probe`（`probe_prs` の理由を結果へ出す。名前だけ） |
| `plugins/ndf/scripts/release-verification-steps.py` | モジュール | `_verify_runtimes`（`git diff` を 1 回へ。シグネチャと失敗の扱いは変えない。名前だけ） |
| `plugins/ndf/scripts/sprint_close_lib/collect.py` | モジュール | `referenced_items`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `check-ai-smells.main` | `scripts/check-ai-smells.py` | 新設 | `(argv: list[str] \| None = None) -> int` | 引数（`--root`・`--base`・`--write-baseline`・`--allowed`）を解き、数える・比べる・基準を書く・印の一覧を出すのどれかを行う | `--root` が git の worktree のルートで、uv が使える | 結果 JSON がちょうど 1 行、標準出力の最後の行にある。終了コードは 0 合格・1 違反・2 読めない・3 起点が決まらない（CI の中だけ） | ファイル（規則のデータと基準ファイルを読む。`--write-baseline` で基準ファイルを書き消す）・外部コマンド（`uv run ... ruff`・`git`） |
| `smells_rules.load_rules` | `scripts/structure_checks/smells_rules.py` | 新設 | `(path: Path) -> Rules` | 規則のデータを読み、形を確かめて型へ直す | 無し | 返した `Rules` の規則はすべて `kind`・`by`・`mark` を持ち、`mark` が `none` / `boundary` / `boundary-or-limit` のどれかで、例外の型は 3 つの分類のどれか 1 つだけに属する | ファイル（読む） |
| `check-ai-smells.list_targets` | `scripts/check-ai-smells.py` | 新設 | `(root: Path) -> list[str]` | 見るファイル（`plugins/ndf` の git が追跡する `.py` のうち `tests/`・`experimental/` の下でないもの）をルートからの相対パスで返す | `root` が git の worktree | 返した一覧は昇順で重複が無く、各パスはworktree に実在する | 外部コマンド（`git ls-files`） |
| `check-ai-smells.run_ruff` | `scripts/check-ai-smells.py` | 新設 | `(root: Path, files: list[str], rules: Rules) -> list[Hit]` | 規則のデータが `by: ruff` とする規則だけを、印を無視して ruff に数えさせる | `files` が空でない。uv とルートの `uv.lock` がある | 返したヒットはすべて `rules` の ruff の規則で、行は ruff の `noqa_row`。印の付いた行も含む | 外部コマンド（`uv run --frozen --project <root> --only-group lint ruff check --isolated --ignore-noqa --output-format json ...`） |
| `smells_rules.scan_file` | `scripts/structure_checks/smells_rules.py` | 新設 | `(rel: str, text: str, rules: Rules) -> FileScan` | 1 ファイルの自作の規則のヒットと、行ごとの印と、行ごとの `except` の受ける型を 1 回の解析で集める | `text` が UTF-8 で読めた本文 | `FileScan.hits` は自作の規則のヒットだけで、同じ（行・規則）を 2 度持たない。`FileScan.marks` は規則のデータが知る規則の印だけを持つ | 無し |
| `smells_rules.read_marks` | `scripts/structure_checks/smells_rules.py` | 新設 | `(text: str, codes: frozenset[str]) -> dict[int, list[Mark]]` | コメントの字句から境界の印を読み、行ごとに返す | `text` が字句に分けられる | 返した印はすべて `codes` の規則を 1 つ以上持つ。形の誤った印も `Mark.label` か `Mark.reason` を空にして返す（捨てない） | 無し |
| `smells_rules.mark_problem` | `scripts/structure_checks/smells_rules.py` | 新設 | `(mark: Mark, code: str, caught: tuple[str, ...] \| None, rules: Rules) -> str \| None` | 印がその規則の兆候を外せるかを判定し、外せないなら理由の文を返す | `code` が `mark.codes` に含まれる | 戻り値が `None` のときだけ、印はその規則の兆候を外す | 無し |
| `smells_rules.loop_calls` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module, calls: frozenset[str], modules: frozenset[str]) -> list[int]` | ループの中の外部呼び出し（`NDF201`）を、呼び出しを直に含む最も内側のループの行で返す | 無し | 1 つのループにつき高々 1 行。入れ子のループで同じ呼び出しを 2 度数えない。入れ子の関数・`lambda` の中は数えない | 無し |
| `smells_rules.elif_chains` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module, min_elif: int) -> list[int]` | `elif` が `min_elif` 本以上続く連鎖（`NDF301`）を、連鎖の先頭の `if` の行で返す | 無し | 1 つの連鎖につき 1 行。連鎖の途中の `if` から数えない | 無し |
| `smells_rules.quiet_handlers` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module) -> tuple[list[int], dict[int, tuple[str, ...]]]` | 本体が定数の `return` を含み、`pass`・`continue`・定数の `return` だけでできた `except`（`NDF401`）の行と、すべての `except` の行ごとの受ける型の名前を返す | 無し | 型の名前は属性の最後の名前（`json.JSONDecodeError` は `JSONDecodeError`）。素の `except` は `("BaseException",)` | 無し |
| `smells_rules.suppress_calls` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module) -> list[int]` | `contextlib.suppress(...)` と `suppress(...)` の呼び出し（`NDF402`）の行を返す | 無し | 呼び出し 1 つにつき 1 行 | 無し |
| `smells_rules.fallbacks` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module) -> list[tuple[int, str]]` | 空の既定への `or`（`NDF501`。右端が `{}`・`[]`・`""`・`0`・`None`）と既定値つきの `.get(k, 既定)`（`NDF502`）を返す | 無し | `.get` は位置引数がちょうど 2 つの呼び出しだけ | 無し |
| `smells_rules.long_functions` | `scripts/structure_checks/smells_rules.py` | 新設 | `(tree: ast.Module, max_lines: int) -> list[int]` | 行数（`def` の行から最後の行まで）が `max_lines` を超える関数（`NDF701`）の `def` の行を返す | 無し | 入れ子の関数もそれぞれ 1 つの関数として数える | 無し |
| `check-ai-smells.apply_marks` | `scripts/check-ai-smells.py` | 新設 | `(hits: list[Hit], scans: dict[str, FileScan], rules: Rules) -> Applied` | ヒットに印を当て、数に入るヒット・許した箇所・要らない印に分ける | `hits` のパスがすべて `scans` にある | ヒットは「数に入る」か「許した」のどちらか一方にだけ入る。数に入るヒットのうち、外せない印の付いたものは理由を持つ | 無し |
| `check-ai-smells.load_baselines` | `scripts/check-ai-smells.py` | 新設 | `(base_dir: Path) -> dict[str, dict[str, int]]` | 兆候の基準ファイルを読み、元のパス → 規則ごとの数を返す | 無し | 返した数はすべて 1 以上の整数で、元のパスごとに 1 つ | ファイル（読む） |
| `check-ai-smells.compare` | `scripts/check-ai-smells.py` | 新設 | `(counts: dict[str, Counter], baselines: dict[str, dict[str, int]], targets: set[str], applied: Applied) -> list[dict]` | ファイルごと・規則ごとの今の数と基準を比べ、超えた・下回った・元のファイルが無い基準を違反の行にする（IA1・IA2） | `counts` のパスは `targets` の部分集合 | 違反の行が無いときに限り、`targets` のすべてのファイルとすべての規則で今の数と基準が等しい | 無し |
| `check-ai-smells.base_totals` | `scripts/check-ai-smells.py` | 新設 | `(root: Path, sha: str, base_rel: str) -> dict[str, int]` | merge-base のツリーの基準ファイルを 1 回で取り出し、規則ごとの合計を返す | `sha` が root のリポジトリにある | 基準ファイルが 1 つも無いツリーでは空の辞書 | 外部コマンド（`git ls-tree`・`git archive`） |
| `check-ai-smells.ratchet_items` | `scripts/check-ai-smells.py` | 新設 | `(root: Path, base_arg: str \| None, baselines: dict[str, dict[str, int]], base_rel: str) -> tuple[list[dict], str \| None, str]` | 規則ごとの基準の合計を merge-base の合計と比べる（IA3）。状態は `checked` / `initial` / `skipped` | 無し | 状態が `checked` で違反の行が無いとき、すべての規則で今の合計が merge-base の合計以下 | 外部コマンド（`git merge-base` と `base_totals` の 2 つ） |
| `check-ai-smells.write_baselines` | `scripts/check-ai-smells.py` | 新設 | `(base_dir: Path, counts: dict[str, Counter]) -> tuple[int, int]` | 今の数で兆候の基準ファイルを書き、数が 0 になったファイルの基準ファイルを消す（書いた数, 消した数） | `counts` は印を当てた後の数 | 置き場の基準ファイルの組が、数が 1 以上のファイルの組と一致し、中身が今の数と等しい | ファイル（書く・消す） |
| `check-ai-smells.baseline_name` | `scripts/check-ai-smells.py` | 新設 | `(rel: str) -> str` | 元のパスから基準ファイルの名前を決める（`/` を `__` に替え `.json` を付ける） | `rel` がルートからの相対パス | `allow_file_name({"path": rel, "name": "", "kind": ""})` と同じ値 | 無し |
| `check-ai-smells.emit` | `scripts/check-ai-smells.py` | 新設 | `(status: str, summary: str, items: list[dict], metrics: dict) -> None` | 結果 JSON（`tool`・`status`・`summary`・`items`・`metrics`）を 1 行出す | `status` が `ok` か `stopped` | 標準出力に 1 行の JSON | 無し |
| `ratchet.initial` | `scripts/contracts_lib/ratchet.py` | 変更 | `(root: Path, sha: str, checker: str = CHECKER) -> bool` | merge-base のワークフローが `checker` を走らせていなければ初回の導入とする | `sha` が root のリポジトリにある | 既定の引数のとき、今の契約の検査の判定と同じ値 | 外部コマンド（`git show`） |
| `wait-notify.main` | `plugins/ndf/scripts/wait-notify.py` | 変更 | `(argv: list[str]) -> int` | 引数が `--send <JSON>` のときだけ通知を送り、それ以外は送らずに 1 行残す | 無し | 常に 0 を返す（hook と切り離した子を失敗させない）。`--runtime` は知らない引数として扱う | ネットワーク（通知の送信）・ファイル（ログへの追記） |
| `gate_answer.replaces` | `plugins/ndf/scripts/lib/gate_answer.py` | 変更 | `(old: dict, name: str, pr: int \| None) -> bool` | 同じ承認ゲートの名前と同じ `pr` の記録だけを置き換える対象にする | `name` が今の名前 | 名前は文字列のまま比べる。旧名（`関門 1` など）の記録は置き換えの対象にならない | 無し |
| `dist_record.parse_record` | `plugins/ndf/scripts/lib/dist_record.py` | 変更 | `(text, parts=None)`（変えない） | 本文から今の見出し（`## リリース記録`）と今の行の見出し（`スプリント: `）の記録だけを読む | 無し | 古い見出し（`## 配布の記録`・`ミッション: `・`まとまり: `）の節は記録として返さない | 無し |
| `sprint_waves._branch_of` | `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | `(repo: str, name: str) -> str \| None`（変えない） | `sprint/<名前>` のブランチが手元か origin にあるかを `git for-each-ref` 1 回で確かめる | `repo` が git の worktree | 戻り値は `sprint/<名前>` か `None`。旧名の接頭辞（`mission/`）は探さず、標準エラーへ案内を出さない | 外部コマンド（`git for-each-ref`） |
| `gh_rest.numbered_nodes` | `plugins/ndf/scripts/lib/gh_rest.py` | 新設 | `(numbers: list[int], node: str, fields: str, repo: str \| None = None, cwd: str \| None = None) -> tuple[dict[int, dict] \| None, str]` | 番号ごとの `node(number: N) { fields }` を GraphQL のエイリアスで 100 件ずつまとめて読む | `node` が `pullRequest` / `issue` / `issueOrPullRequest` のどれか。`fields` は GraphQL の選択セット | 読めたとき、`numbers` のすべての番号が鍵にあり、その種類で無い番号の値は空の辞書。読めなければ `(None, 理由)` で、一部だけの結果を返さない | ネットワーク（`gh api graphql`） |
| `merged-steps.probe_prs` | `plugins/ndf/scripts/merged-steps.py` | 変更 | `(root, a) -> tuple[list[str], str]` | `--pr` はそのまま、`--head` は開いた PR の一覧を 1 回読んで head の名前で選ぶ | `a.pr` か `a.head` のどちらかがある | 読めたとき `(番号の一覧, "")`。一覧を読めなければ `([], 理由)` で、head ごとの読めない失敗を黙って飛ばさない | ネットワーク（`gh_rest.pr_list` の 1 回） |
| `collect.referenced_items` | `plugins/ndf/scripts/sprint_close_lib/collect.py` | 変更 | `(root, repo, refs, issues) -> list[dict]`（変えない） | 参照だけの Issue の状態と種類を `numbered_nodes` の 1 回で読み、PR と閉じた Issue を除いた行を返す | `repo` が `owner/name` | 読めなければ参照のすべてを「状態を読めない」の行にする（番号ごとに読み直さない） | ネットワーク（`gh_rest.numbered_nodes` の 1 回） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check-ai-smells.main` | 規則のデータ・基準ファイルが読めない | JSON の誤り・形の誤り・名前の不一致 | `smells_rules.load_rules`・`check-ai-smells.load_baselines` が送出する `SmellsError` | 止める | `status: stopped`、`summary` にファイルと理由。終了コード 2 | 書き換えていない。直して実行し直せる |
| `check-ai-smells.main` | 見るファイルが構文の誤りで読めない | 壊れた `.py` | `smells_rules.scan_file` の `SyntaxError`・ruff の `invalid-syntax` | 止める | `items` にパスと行。終了コード 2 | 同上 |
| `check-ai-smells.main` | 違反がある | IA1〜IA4 | `compare`・`ratchet_items`・`apply_marks` | 呼び出し元へ返す | 違反ごとに標準エラーへ 1 行、`status: stopped`。終了コード 1 | 直すか `--write-baseline` を実行して実行し直す |
| `check-ai-smells.main` | 起点が決まらない | CI の中で `GITHUB_BASE_REF` も `--base` も無い・merge-base が無い | `ratchet.base_ref` の `RatchetError` | 止める（CI の中）/ 既定値で続ける（手元。状態 `skipped`） | CI: `status: stopped`。終了コード 3。手元: `metrics.ratchet = "skipped"` で続ける | 履歴を取り直して（`fetch-depth: 0`）実行し直せる |
| `smells_rules.load_rules` | 規則のデータが無い・JSON でない・キーが欠ける・`mark` の値が知らない値・型が 2 つの分類に重なる | 規則のデータの書き誤り | 読んだ直後の形の確かめ | 呼び出し元へ返す（`SmellsError`） | `main` が終了コード 2 で出す | 無し |
| `check-ai-smells.list_targets` | git が使えない | worktree の外・git が無い | `git ls-files` の終了コードが 0 でない | 呼び出し元へ返す（`SmellsError`） | `main` が終了コード 2 で出す（「git の worktree で実行する」） | 無し |
| `check-ai-smells.run_ruff` | uv か ruff を起動できない | uv が無い・`uv.lock` の版を取れない | `FileNotFoundError`・終了コード 2 以上 | 呼び出し元へ返す（`SmellsError`） | `main` が終了コード 2 で出す。標準エラーの先頭 300 字を `summary` に入れる | 無し。uv を用意して実行し直せる |
| `check-ai-smells.run_ruff` | 出力が JSON でない | ruff の異常 | `json.loads` の `ValueError` | 呼び出し元へ返す（`SmellsError`） | 同上 | 同上 |
| `check-ai-smells.load_baselines` | 基準ファイルが読めない・形が違う・名前が `baseline_name(path)` と違う・数が 1 未満 | 手で書いた・壊れた | 1 ファイルずつ読む所 | 呼び出し元へ返す（`SmellsError`） | `main` が終了コード 2 で出す（ファイル名と、`--write-baseline` で書き直す案内） | 無し |
| `check-ai-smells.base_totals` | merge-base の基準ファイルが読めない | `git archive` の失敗・JSON でない | `git` の終了コード・`json.loads` | 呼び出し元へ返す（`SmellsError`） | `main` が終了コード 2 で出す | 無し |
| `check-ai-smells.ratchet_items` | 起点が決まらない | 上の `main` の行と同じ | `ratchet.base_ref` | CI の中は呼び出し元へ返す（`RatchetError`）、手元は既定値で続ける | 上の `main` の行と同じ | 同上 |
| `check-ai-smells.write_baselines` | 書けない・消せない | 権限・ディスク | `OSError` | 止める | `status: stopped`、終了コード 2 | 書いた分は今の数と一致するため、実行し直せば残りも書ける（冪等） |
| `ratchet.initial` | merge-base にワークフローが無い | 履歴の浅い取得 | `git show` の失敗（空の文字列） | 既定値で続ける（初回として扱う。今と同じ） | 無し | 無し |
| `wait-notify.main` | 通知を送れない | ネットワーク・設定 | `send_notice` の例外 | 既定値で続ける（ログへ 1 行。hook と切り離した子を失敗させない） | 標準出力に無し。終了コード 0 | 無し |
| `wait-notify.main` | 知らない引数（`--runtime` を含む） | 古い hook の設定から呼ばれた | 引数の判定 | 既定値で続ける（`skip: bad arguments` をログへ） | 終了コード 0 | 無し。hook の設定を今の `hook.py` の形へ導入し直す |
| `sprint_waves._branch_of` | `git for-each-ref` が失敗する | worktree でない | 終了コード | 呼び出し元へ返す（`None`。今と同じ） | 呼び出し元が新しいブランチを切る | 無し |
| `gh_rest.numbered_nodes` | 応答が読めない・GraphQL のエラー・上限 | ネットワーク・権限・上限 | `gh_call.gh` の終了コードと `data` の欠け | 呼び出し元へ返す（`(None, 理由)`。途中のチャンクの結果も捨てる） | 呼び出し元が理由を出す | 無し。読み取りだけなので実行し直せる |
| `merged-steps.probe_prs` | 開いた PR の一覧が読めない | ネットワーク・上限 | `gh_rest.pr_list` の `Attempt.error` | 呼び出し元へ返す | `cmd_probe` が `status: stopped`、`summary` に理由、終了コード 1 | 読み取りだけなので実行し直せる |
| `collect.referenced_items` | 状態が読めない | 同上 | `numbered_nodes` の `None` | 既定値で続ける（すべての参照を「状態を読めない」の行にする。今と同じ扱い） | 締めの結果の `items` に行が出る | 同上 |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check-ai-smells.main` | `--write-baseline` あり | 数えて `write_baselines` だけを行い、比べない。`status: ok`・終了コード 0（`metrics.written`・`metrics.removed`） |
| `check-ai-smells.main` | `--allowed` あり | 数えて許した箇所の一覧を `items` に出す。比べない。終了コード 0 |
| `check-ai-smells.main` | どちらも無し、`compare`・`ratchet_items`・要らない印のどれかに行がある | 終了コード 1 |
| `check-ai-smells.main` | どちらも無し、違反の行が無い | 終了コード 0 |
| `check-ai-smells.main` | `--write-baseline` と `--allowed` の両方 | 引数の誤りとして終了コード 2 |
| `smells_rules.mark_problem` | 印の `label` か `reason` が空 | 「印の形が `<規則> <境界の種類>: <理由>` でない」 |
| `smells_rules.mark_problem` | 規則の `mark` が `none`（分岐・長さ・引数） | 「この規則は印で外せない。直す」 |
| `smells_rules.mark_problem` | `label` が `上限`、規則が `NDF201` でない | 「`上限:` はループの中の呼び出しだけ」 |
| `smells_rules.mark_problem` | `label` が境界の種類にも `上限` にも無い | 「境界の種類は 外部コマンド / ネットワーク / 利用者の入力 / ファイル」 |
| `smells_rules.mark_problem` | 規則が `except` の規則（S110・S112・BLE001・NDF401）、`caught` の型がすべて「内部」 | 「内部の型だけを受ける `except` は直す（握りつぶさずに送出する）」 |
| `smells_rules.mark_problem` | 上のどれにも当たらない | `None`（兆候を外す） |
| `check-ai-smells.apply_marks` | ヒットの行に、その規則を持ち `mark_problem` が `None` の印がある | 許した箇所へ入れる |
| `check-ai-smells.apply_marks` | ヒットの行に、その規則を持つ印があり `mark_problem` が理由を返す | 数に入れ、理由をヒットに付ける |
| `check-ai-smells.apply_marks` | ヒットの行に、その規則の印が無い | 数に入れる |
| `check-ai-smells.apply_marks` | 正しい形の印の行に、その規則のヒットが無い | 要らない印の違反（`mark-unused`） |
| `check-ai-smells.compare` | 今の数 > 基準 | `over` の違反。数に入った行の一覧と、付いた印の理由を添える |
| `check-ai-smells.compare` | 今の数 < 基準 | `stale` の違反（「`--write-baseline` で基準を下げる」） |
| `check-ai-smells.compare` | 基準ファイルがあり、元のパスが見るファイルに無い | `orphan` の違反（「基準ファイルを消す」） |
| `check-ai-smells.ratchet_items` | 起点が決まらない、CI の中 | `RatchetError` を送出する |
| `check-ai-smells.ratchet_items` | 起点が決まらない、手元 | 行なし・状態 `skipped` |
| `check-ai-smells.ratchet_items` | `ratchet.initial(root, sha, "check-ai-smells.py")` が真 | 行なし・状態 `initial`（A0 の PR） |
| `check-ai-smells.ratchet_items` | 上のどれでもない | 規則ごとに今の基準の合計と `base_totals` を比べ、超えた規則ごとに `total-over` の違反。状態 `checked` |
| `merged-steps.probe_prs` | `--pr` あり | その番号をそのまま返す（呼び出し無し） |
| `merged-steps.probe_prs` | `--head` あり、一覧が読めた | 一覧の `headRefName` が `--head` のどれかに一致する番号を、`--head` の順で重複なく返す |
| A2 の各 PR が直す箇所 | ループの各回が読み取りで、番号か名前の組で引ける | 1 回のクエリへまとめる（GitHub は `numbered_nodes` か一覧 1 回、git はパスの組を渡した 1 回と手元の振り分け） |
| A2 の各 PR が直す箇所 | ループの各回が書き込み（コメント・ラベル・再実行） | 直さず `# noqa: NDF201 外部コマンド: <なぜ 1 回にできないか>` |
| A2 の各 PR が直す箇所 | 回数がデータで固定の小さい数（ランタイムの 2 つ・100 件ごとのチャンク） | 直さず `# noqa: NDF201 上限: <件数と根拠>` |
| A3 の各 PR が直す箇所 | `except` が内部の型だけを受ける | `try` を外して例外を送出する。呼び出し元が境界なら境界で 1 回だけ受ける |
| A3 の各 PR が直す箇所 | `except` が境界の型を受け、既定値へ倒す | 失敗を理由つきで呼び出し元へ返す形へ直す。hook のように止めてはならない境界は、境界の種類と理由の印を付ける |
| A3 の各 PR が直す箇所 | `.get(k, 既定)`・`x or {}` が、境界で確かめた後の値を読む | 境界で `lib/schema.py` の `load_shape` を通した型の属性を読む形へ直す |
| A3 の各 PR が直す箇所 | `.get(k, 既定)` が環境変数・利用者の引数・外部の応答を直に読む | 境界の種類（利用者の入力 / ネットワーク / ファイル / 外部コマンド）と理由の印を付ける |
| A3 の各 PR が直す箇所 | `.get`・`or` が古い形のキー・値を読み替える | 消す（決定 40。古い形は読めない入力として止める） |
| A3 の各 PR が直す箇所 | 分岐の多い関数（`NDF301`・`PLR0912`・`PLR1702`） | 判定データと、データを引く 1 つの関数へ直す（決定 42） |
| A3 の各 PR が直す箇所 | 60 行を超える関数・引数 7 個以上の関数（`NDF701`・`PLR0913`） | 責務ごとの関数へ分ける。引数は関係する組を `dataclass` 1 つにまとめる |

- 繰り返しの終わる条件: 見るファイルの一覧（有限）を 1 回ずつ走査し、各ファイルの構文木を 1 回たどる。`numbered_nodes` は番号を 100 件ずつのチャンクにしてチャンクの数だけクエリを送る
- 計算量の注意: 構文木の走査は 529 ファイルで 1 回ずつ（`ai-smells.py` の同じ走査で約 3 秒）。ruff の起動は 1 回。merge-base の基準ファイルは `git ls-tree` と `git archive` の 2 回で読み、ファイルごとの `git show` をしない（基準ファイルは約 450 本の見込み）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Rules` | 型（`rules: dict[str, Rule]`・`boundaries: tuple[str, ...]`・`types: dict[str, str]`（型の名前 → `boundary` / `internal` / `either`）・`ruff_config: dict[str, object]`） | `scripts/structure_checks/smells_rules.py` | 規則のデータから作る |
| `Rule` | 型（`code`・`kind`（1〜7）・`by`（`ruff` / `own`）・`mark`（`none` / `boundary` / `boundary-or-limit`）・`preview: bool`・`params: dict`） | 同上 | 同上 |
| `Mark` | 型（`line`・`codes: tuple[str, ...]`・`label`・`reason`） | 同上 | — |
| `Hit` | 型（`path`・`line`・`rule`） | 同上 | — |
| `FileScan` | 型（`hits: list[Hit]`・`marks: dict[int, list[Mark]]`・`caught: dict[int, tuple[str, ...]]`） | 同上 | — |
| `Applied` | 型（`counted: list[tuple[Hit, str \| None]]`・`allowed: list[tuple[Hit, Mark]]`・`unused: list[tuple[str, Mark]]`） | `scripts/check-ai-smells.py` | — |
| 規則の表 | 設定値 | `scripts/ai-smells-rules.json` の `rules` | S110・S112・BLE001（種類 4・ruff・`boundary`）、PLR1702（種類 3・ruff・`none`・preview）、PLR0912（種類 3・ruff・`none`）、PLR0913（種類 7・ruff・`none`）、NDF201（種類 2・`boundary-or-limit`）、NDF301（種類 3・`none`・`min_elif` 4）、NDF401・NDF402（種類 4・`boundary`）、NDF501・NDF502（種類 5・`boundary`）、NDF701（種類 7・`none`・`max_lines` 60） |
| ruff のしきい値 | 設定値 | `scripts/ai-smells-rules.json` の `ruff_config` | `lint.flake8-bandit.check-typed-exception = true`・`lint.pylint.max-nested-blocks = 3`・`lint.pylint.max-branches = 9`・`lint.pylint.max-args = 6`（決定 36）。`--config` で 1 つずつ渡す |
| 外部呼び出しの名前 | 設定値 | `scripts/ai-smells-rules.json` の `NDF201.params` | `calls`: `run`・`check_output`・`check_call`・`Popen`・`gh`・`gh_json`・`git`・`_git`・`gh_api`・`call_gh`（`ai-smells.py` の `CALL_IO` と同じ組）。`modules`: 属性の呼び出しで外部呼び出しと見るレシーバの名前（`subprocess`・`proc`・`step_result`・`gh_call`・`gh_parts`・`gh_rest`）。レシーバが名前でない `x.run()` は数えない |
| 境界の種類 | 設定値 | `scripts/ai-smells-rules.json` の `boundaries` | `外部コマンド`・`ネットワーク`・`利用者の入力`・`ファイル` |
| 例外の型の分類 | 設定値 | `scripts/ai-smells-rules.json` の `exception_types` | 境界: `OSError`・`FileNotFoundError`・`PermissionError`・`SubprocessError`・`CalledProcessError`・`TimeoutExpired`・`JSONDecodeError`・`UnicodeDecodeError`・`URLError`・`HTTPError`。内部: `TypeError`・`AttributeError`・`KeyError`・`IndexError`・`NameError`・`AssertionError`。どちらも: `ValueError`・`LookupError`・`RuntimeError`・`Exception`・`BaseException`。表に無い型（`StepError` など自作の型）は「どちらも」として扱う |
| 見る範囲 | 定数 | `scripts/check-ai-smells.py` の `SCAN`・`SKIP_PARTS` | `plugins/ndf`・`("tests", "experimental")` |
| 基準の置き場 | 定数 | `scripts/check-ai-smells.py` の `BASELINE_DIR` | `scripts/ai-smells-baseline` |
| 規則のデータの置き場 | 定数 | `scripts/check-ai-smells.py` の `RULES_PATH` | `scripts/ai-smells-rules.json` |
| 検査の名前（初回の判定に渡す） | 定数 | `scripts/check-ai-smells.py` の `SELF` | `check-ai-smells.py`（`ratchet.initial` の `checker`） |
| 兆候の基準ファイルの形 | 型 | `scripts/ai-smells-baseline/<baseline_name>` | `{"path": "<元のパス>", "counts": {"<規則>": <1 以上の整数>}}`。0 の規則は持たない |
| 結果 JSON の違反の行 | 型 | `scripts/check-ai-smells.py` | `{"kind": "over" / "stale" / "orphan" / "total-over" / "mark-unused", "path", "rule", "result": "violation", "detail"}`。`--allowed` の行は `{"kind": "allowed", "path", "line", "rule", "label", "reason"}` |
| チャンクの大きさ | 定数 | `plugins/ndf/scripts/lib/gh_rest.py` の `PER_PAGE`（既存） | 100 |
| リリースプランのフェーズ | 定数 | `plugins/ndf/scripts/supervise_lib/delivery_templates.py` の `RELEASE_PHASES` | `delivery_templates.py` が組むリリースプランのフェーズの名前の組。`queue.py` はここから引く。「定数からデータへ」の DT6 がフェーズの表へ移すまでの唯一の置き場 |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| CI の `script-structure-check` が PR ごとに検査を実行する | worktree（読むだけ） | 取らない（書かない） | 何度実行しても同じ結果 |
| 並列の PR がそれぞれ `--write-baseline` を実行する | 兆候の基準ファイル（元のファイル 1 本に 1 つ） | 取らない。触る元のファイルが重ならない PR の組を同時に流す（先頭の設計文書の規則 2・3）。重なったときは git のマージの衝突で現れる | 後からマージする PR がベースブランチを取り込み、`--write-baseline` を実行し直せば解ける（今の数で上書きするため冪等） |
| 手元で `--write-baseline` を 2 つ同時に実行する | 同じ基準ファイル | 取らない（同じツリーからは同じ中身を書く） | 途中で止まっても、実行し直せば残りを書き、要らないファイルを消す |
| A1a・A1b と、子の Issue（#2963・#2993・#2994・#2992）・DT6 | `supervise_lib/`・`lib/sprint_loop*`・`lib/parallel_*`・`lib/legacy_names.py` | A1a・A1b を先に流し、重なる子の Issueは後に流す（設計文書の「直す順序と触るファイル」） | — |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check-ai-smells.main` | 違反 1 件につき `<kind>: <path>: <detail>` | 標準エラー | ソースの行の本文（行番号だけを出す） |
| `check-ai-smells.main` | `tool`・`status`・`summary`・`items`・`metrics`（`files`・規則ごとの `counted`・`allowed`・`base`・`ratchet`・`written`・`removed`） | 結果 JSON（標準出力の最後の 1 行） | 環境変数の値・ruff の起動の環境 |
| `check-ai-smells.run_ruff` | ruff の失敗時の標準エラーの先頭 300 字 | 結果 JSON の `summary` | 300 字を超える部分 |
| `wait-notify.main` | `skip: bad arguments` と受けた引数・通知の失敗の型と文 | 既存のログ（`log`） | 通知の宛先の URL と秘密 |
| `merged-steps.probe_prs` | 一覧を読めない理由 | `cmd_probe` の結果 JSON の `summary` | トークン |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `check-ai-smells.main` | `scripts/check-script-structure.py` の `main`・`scripts/check-ndf-contracts.py` の `main` | `grep -n '^def ' scripts/check-script-structure.py`・`sed -n 416,470p scripts/check-ndf-contracts.py` | 使えない理由: どちらもファイル名にハイフンを持つエントリポイントで import できない。引数・結果 JSON・終了コード 3 の形はそろえる |
| `smells_rules.load_rules` | `scripts/lib/allowlist.py` の `load_allow`・`lib/schema.py` の `load_shape`・`jsonschema` | `cat scripts/lib/allowlist.py`・`grep -n '^def \|^class ' plugins/ndf/scripts/lib/schema.py`・`grep -n jsonschema issues/issue-1142-design-data.md` | 使えない理由: `load_allow` は 1 項目 1 ファイルの 4 つのキーの形に固定。`lib/schema.py` はプラグインの側で pydantic を要し、ルートのスクリプトは標準ライブラリで動く（`check-script-structure.py` と同じ）。`jsonschema` は DT1 が足す依存で A0 の時点に無い。標準ライブラリで形を確かめる |
| `check-ai-smells.list_targets` | `scripts/check-script-structure.py` の `list_files` | `sed -n 126,146p scripts/check-script-structure.py` | 使えない理由: `.sh` も返し `experimental/` を除かず、ハイフンの名前で import できない。#2996 が構文木の解析を 1 回にまとめるときに 1 つにする |
| `check-ai-smells.run_ruff` | `scripts/check-lint.sh` の `tool`（`uv run --frozen --project <ルート> --only-group lint`） | `sed -n 1,80p scripts/check-lint.sh` | 使う（起動の形だけ）。シェルの関数は呼べないため同じ引数の並びを書く。ruff の `--isolated`・`--ignore-noqa`・`--config` の組は手元で起動して確かめた（ruff 0.16.9。`--isolated` でも `--config 'lint.pylint.max-args = 6'` が効き、`--ignore-noqa` で印の付いた行も返り、JSON の各行が `noqa_row` を持つ） |
| `check-ai-smells.apply_marks` | ruff の印の扱い（`RUF100`）・`ruff check --add-noqa` | `ruff check --isolated --select S110,RUF100` を印の付いたファイルへ実行した | 使えない理由: `RUF100` は選んでいない規則の印（`E402`）も指摘し、印の形（境界の種類と理由）を見ない。印の判定は自作する |
| `check-ai-smells.load_baselines` | `scripts/lib/allowlist.py` の `load_allow`・`allow_file_name` | `cat scripts/lib/allowlist.py` | `allow_file_name` は使う（`baseline_name` から）。`load_allow` は使えない理由: キーが `path`・`name`・`kind`・`reason` に固定で、規則ごとの数を持てない |
| `check-ai-smells.compare` | `scripts/check-script-structure.py` の `check`（`lines` のラチェット） | `sed -n 832,872p scripts/check-script-structure.py` | 使えない理由: 1 ファイル 1 つの行数を比べる形で、規則ごとの数と下回りの違反を持たない |
| `check-ai-smells.base_totals` | `scripts/contracts_lib/ratchet.py` の `_names_at`・`_row_at` | `cat scripts/contracts_lib/ratchet.py` | 使えない理由: 1 ファイルにつき `git show` を 1 回実行し、約 450 本の基準ファイルで 450 回になる（この主題が数えるループの中の外部呼び出しそのもの）。`git archive` の 1 回を `tarfile` で読む（`git archive --format=tar HEAD -- scripts/script-structure-allow/` が 185 ファイルを 1 回で返し、無いパスでは終了コード 128 になることを確かめた。そのため先に `git ls-tree` で有無を見る） |
| `check-ai-smells.ratchet_items` | `scripts/contracts_lib/ratchet.py` の `base_ref`・`initial`・`RatchetError` | `grep -n 'ratchet\.' scripts/check-ndf-contracts.py` | 使う。`initial` は検査の名前を固定で持つため、引数 `checker` を足す（既定は今の値） |
| `check-ai-smells.write_baselines` | 例外リストを書く既存の関数 | `grep -rn 'write_text' scripts/check-script-structure.py scripts/check-ndf-contracts.py scripts/lib/allowlist.py` | 使えない理由: 例外リストは手で書き、書く関数が無い。基準ファイルは手で書かない（集約の持ち主は検査） |
| `check-ai-smells.baseline_name` | `scripts/lib/allowlist.py` の `allow_file_name` | `cat scripts/lib/allowlist.py` | 使う（`{"path": rel, "name": "", "kind": ""}` を渡すと空の部を省き `<パスの / を __ に>.json` になる） |
| `check-ai-smells.emit` | `scripts/check-script-structure.py` の `emit`・`plugins/ndf/scripts/lib/step_result.py` の `emit` | `sed -n 875,881p scripts/check-script-structure.py`・`grep -n '^def emit' plugins/ndf/scripts/lib/step_result.py` | 使えない理由: 前者は import できない。後者はプラグインの側の部品で、ルートの検査はプラグインのモジュールを import しない（`check-script-structure.py` と同じ）。形は前者にそろえる |
| `smells_rules.scan_file` | `scripts/measure/ai-smells.py` の `Tally.file` | `sed -n 1,240p scripts/measure/ai-smells.py` | 使えない理由: 計測は数え方を E9 まで変えない（IA5）。入れ子のループで同じ呼び出しを 2 度数え、`elif` の連鎖を途中からも数える。判定の述語（`is_swallow`・`is_empty_fallback`）は同じ考え方で書き直す |
| `smells_rules.read_marks` | ruff の `noqa` の読み取り・`tokenize` | ruff の公式の一覧（`https://docs.astral.sh/ruff/linter/#error-suppression`）と `ruff check --help` | 使えない理由: ruff は印の読み取りを外へ出さない。標準ライブラリの `tokenize` でコメントの字句だけを読む（文字列の中の `# noqa` を拾わない） |
| `smells_rules.mark_problem` | 無し（印の形の規則はこの主題で決めた） | `grep -rn 'noqa' scripts/*.py scripts/lib/*.py` | 使えない理由: 既存の検査に印の形を確かめる部品が無い |
| `smells_rules.loop_calls` | `scripts/measure/ai-smells.py` の `Tally.loop`・`callee` | 同上 | 使えない理由: 同上（IA5）。最も内側のループに 1 回だけ数える形に直す |
| `smells_rules.elif_chains` | `scripts/measure/ai-smells.py` の `elif_len` | 同上 | 使えない理由: 同上。連鎖の先頭だけで数える |
| `smells_rules.quiet_handlers` | ruff の S110・S112 | `ruff rule --all` の一覧を `except` で探した（決定 36） | 一部を使う: `pass`・`continue` は ruff が数える。定数の `return` を数える規則が無いため、その分だけ自作する |
| `smells_rules.suppress_calls` | ruff の SIM105 | 決定 36 の表 | 使えない理由: SIM105 は逆向き（`suppress` へ書き換えさせる） |
| `smells_rules.fallbacks` | ruff の規則の一覧（`default`・`or` を名前に持つ規則） | 決定 36 の表の後の段落 | 使えない理由: 空のフォールバックを見る規則が無い |
| `smells_rules.long_functions` | ruff の PLR0915 | 決定 36 の表 | 使えない理由: 文の数を見て行数を見ない |
| `gh_rest.numbered_nodes` | `plugins/ndf/scripts/lib/gh_rest.py` の `pr_head_branches`・`plugins/ndf/scripts/lib/gh_graphql.py` の `graphql` | `sed -n 95,116p plugins/ndf/scripts/lib/gh_rest.py`・`sed -n 87,120p plugins/ndf/scripts/lib/gh_graphql.py` | 使う: `pr_head_branches` のエイリアスの組み立てと 100 件のチャンクをそのまま一般化し、`pr_head_branches` を `numbered_nodes` の呼び出しへ置き換える。`graphql` は作業ディレクトリのリポジトリを `{owner}`・`{repo}` で解けないため使わない |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check-ai-smells.main` | 基準 0 の一時のリポジトリで、規則ごとに兆候を 1 つ持つファイルを置くと終了コード 1、`--write-baseline` の後は 0 | 兆候の無いツリーで基準ファイルが 0 本、終了コード 0。`--allowed` は印の無いツリーで `items` が空 | 規則のデータの壊れ・基準ファイルの名前の不一致・構文の誤りのファイルで 2。CI の環境変数で起点が無いと 3。`--write-baseline` と `--allowed` の両方で 2 |
| `smells_rules.load_rules` | A0 の規則のデータが読め、13 の規則を持つ | `ruff_config` が空でも読める | キーの欠け・`mark` の知らない値・型の分類の重なり・JSON でない、のそれぞれで `SmellsError` |
| `check-ai-smells.list_targets` | `tests/`・`experimental/` の下と `.sh` を除いた `.py` を昇順で返す | 追跡されているがworktree から消したファイルを返さない | git の worktree の外で `SmellsError` |
| `check-ai-smells.run_ruff` | 入れ子の深さ 4 の関数で PLR1702 が当たり 3 では当たらない。引数 7 個で PLR0913 が当たり 6 個では当たらない。`if` 10 個で PLR0912 が当たり 9 個では当たらない。型を指定した `except OSError: pass` で S110 が当たる | 印の付いた行もヒットとして返る（`--ignore-noqa`） | uv を見つけられない `PATH` で `SmellsError`。ruff が JSON でない出力を返すと `SmellsError` |
| `smells_rules.scan_file` | 7 つの自作の規則の兆候を 1 つずつ持つ本文で、規則ごとに 1 つのヒット | 兆候の無い本文でヒット 0 | 構文の誤りで `SyntaxError` を送出する |
| `smells_rules.read_marks` | `# noqa: S110 ファイル: 理由` を読み、`codes`・`label`・`reason` を持つ | `# noqa: S110, NDF401 ファイル: 理由` の 2 つの規則・全角のコロン・文字列の中の `# noqa` は拾わない・`# noqa: E402` は規則のデータに無いので返さない | `# noqa: BLE001 — 理由`（境界の種類が無い）は `label` を空にして返す |
| `smells_rules.mark_problem` | 境界の型を受ける `except` の正しい印で `None` | `NDF201` の `上限:` の印で `None` | 判定表の 5 つの理由をそれぞれ返す（形・許さない規則・`上限:` の誤用・知らない境界の種類・内部の型） |
| `smells_rules.loop_calls` | `for` の本体の `subprocess.run` で 1 行 | 内包表記の中の `gh(...)` で 1 行。入れ子のループで内側だけに呼び出しがあると内側の 1 行だけ。同じループに呼び出し 2 つで 1 行。ループの中の入れ子の関数の中の呼び出しは数えない。`engine.run()` は数えない | 無し（構文木を受けるだけ） |
| `smells_rules.elif_chains` | `if` と `elif` 4 本の連鎖で 1 行（先頭の行） | `elif` 3 本で 0。連鎖の途中の `if` から数えない | 無し |
| `smells_rules.quiet_handlers` | 本体が `return None` の `except KeyError` で 1 行と、その行の型 `("KeyError",)` | 本体が `pass` だけの `except` はヒットにしない（ruff の S110 が数える）が型は返す。`except (OSError, json.JSONDecodeError)` の型は `("OSError", "JSONDecodeError")`。素の `except` は `("BaseException",)` | 無し |
| `smells_rules.suppress_calls` | `with contextlib.suppress(OSError):` で 1 行 | `from contextlib import suppress` の後の `suppress(...)` も 1 行 | 無し |
| `smells_rules.fallbacks` | `x or {}` で NDF501、`d.get("k", 0)` で NDF502 | `x or default_value`（空でない既定）は数えない。`d.get("k")` は数えない | 無し |
| `smells_rules.long_functions` | 61 行の関数で 1 行 | 60 行の関数で 0 | 無し |
| `check-ai-smells.apply_marks` | 正しい印の行のヒットは許した箇所へ入り数に入らない | 1 つの行に 2 つの規則のヒットがあり、印が片方だけを持つと、もう片方は数に入る | 形の誤った印の行のヒットは数に入り理由を持つ。ヒットの無い行の正しい印は `mark-unused` |
| `check-ai-smells.load_baselines` | 2 本の基準ファイルを読み、元のパスごとの数を返す | 置き場が無ければ空 | 名前の不一致・数が 0・数が整数でない・JSON でない、のそれぞれで `SmellsError` |
| `check-ai-smells.compare` | 今の数と基準が等しいと行なし | 基準ファイルの無いファイルに兆候が 1 つで `over` | 1 つ多いと `over`、1 つ少ないと `stale`、元のファイルが無い基準で `orphan` |
| `check-ai-smells.base_totals` | 一時のリポジトリの 1 つ前のコミットの基準の合計を返す | 基準ファイルの無いコミットで空の辞書 | 壊れた JSON の基準ファイルを持つコミットで `SmellsError` |
| `check-ai-smells.ratchet_items` | 兆候を持つ関数を新しいファイルへ移し、両方の基準を書き直すと行なし | merge-base のワークフローが検査を持たないツリーで、0 でない基準を足すと状態 `initial`・行なし | merge-base が検査を持つツリーで、新しいファイルの基準だけを足すと `total-over`。CI の環境変数で起点が無いと `RatchetError` |
| `check-ai-smells.write_baselines` | 今の数で基準ファイルを書く | 数が 0 になったファイルの基準ファイルを消す。2 回実行しても中身が変わらない | 書けない置き場（読み取りだけのディレクトリ）で `OSError` |
| `check-ai-smells.baseline_name` | `plugins/ndf/scripts/lib/x.py` から `plugins__ndf__scripts__lib__x.py.json` | 先頭の `./` を持たないルートからの相対パスだけを受ける | 無し |
| `check-ai-smells.emit` | 1 行の JSON で `tool` が `check-ai-smells` | `items` が空でも `metrics` を出す | 無し |
| `ratchet.initial` | 既定の引数で、今の契約の検査の判定と同じ値 | `checker` に `check-ai-smells.py` を渡すと、ワークフローにその名前が無い merge-base で真 | ワークフローの無い merge-base で真 |
| `wait-notify.main` | `--send <JSON>` で送信の部品を 1 回呼ぶ | 引数 0 個で送らずに 0 | `--runtime claude` で送らずに 0、ログに `skip: bad arguments`。送信の部品が例外を送出しても 0 |
| `gate_answer.replaces` | 同じ名前と同じ `pr` で真 | `pr` がどちらも `None` で真。別の `pr` で偽 | 旧名（`関門 1`）の記録は今の名前（設計の承認ゲート）の置き換えの対象にならない |
| `dist_record.parse_record` | `## リリース記録` の節の `スプリント: ` の行を読む | 記録の無い本文で空 | `## 配布の記録`・`ミッション: ` の節は記録として返さない |
| `sprint_waves._branch_of` | 手元に `sprint/<名前>` があれば返す | origin にだけあるときも返す | `mission/<名前>` だけがあると `None`。`git` の呼び出しが 1 回 |
| `gh_rest.numbered_nodes` | 3 つの番号を 1 回のクエリで読み、番号 → フィールドの辞書を返す | 101 件で 2 回。PR でない番号の値は空の辞書。`repo` を渡すと `owner`・`name` にその値を使う | 2 つ目のチャンクで失敗すると `(None, 理由)` で、1 つ目のチャンクの結果も返さない |
| `merged-steps.probe_prs` | `--head` 2 つで一覧の 1 回から 2 つの番号を `--head` の順で返す | `--pr` だけなら GitHub を呼ばない。同じ番号を 2 度返さない | 一覧が読めないと `([], 理由)` で、`cmd_probe` が終了コード 1 |
| `collect.referenced_items` | 開いた Issue を 1 行、PR と閉じた Issue は行にしない。GitHub の呼び出しが 1 回 | `issues` にある番号はクエリに含めない | クエリが読めないと、すべての参照が「状態を読めない」の行になる |

A1a・A1b・A2・A3 の各 PR が消す古いテスト（置き換えた振る舞いを期待値に持つもの）:

| 移行ステップ | 消すテスト | 書き直すテスト |
| --- | --- | --- |
| A0 | 無し（新しい検査） | 無し |
| A1a | `plugins/ndf/scripts/tests/test_legacy_names.py`・`plugins/ndf/scripts/tests/test_gate_names.py` | `plugins/ndf/scripts/tests/test_resolve.py` の旧名の Skill の場合、`plugins/ndf/scripts/tests/test_step_scripts.py` の `phase-steps.py` の節、`plugins/ndf/scripts/tests/test_wait_notify.py` の `--runtime` の場合（上の観点で置き換える） |
| A1b | `plugins/ndf/scripts/tests/test_legacy_values.py` | 古い値を入力に持つ各モジュールのテストの場合（着手の時点で `grep -rln '配布（\|配布待ち\|## 配布の記録' plugins/ndf/scripts/tests` で洗い出す） |
| A2 | `plugins/ndf/scripts/tests/test_merged_probe.py` のうち head ごとに `gh pr list --head` を呼ぶことを期待する場合 | 22 本の関数のテストのうち、呼び出しの回数と順序を期待値に持つもの（着手の時点で関数名を `grep -rln` で引いて洗い出す） |
| A3a〜A3g | 握りつぶし・既定値への倒れを期待値に持つ場合（例: 壊れた JSON で空の辞書を返す） | 直した関数ごとに上の判定表の結果（送出する・理由を返す・境界で止める）を期待値にして書き直す |
