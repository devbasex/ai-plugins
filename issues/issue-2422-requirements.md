# #2422: クロスリファクタリングが時間の予算で見送った 24 件（スプリント lflow-1）

正は課題の本文（#2422）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

スプリント lflow-1 の検査で、クロスリファクタリングは改善の候補を 30 件出した。そのうち 24 件は時間の予算に入らず見送った（理由 `budget`。残り 2 件は同じ変更の重複）。見送った 24 件は次のとおり。

| ファイル | シンボル | 兆候 | 手法 | 重大度 |
| --- | --- | --- | --- | --- |
| `plugins/ndf/scripts/context-policy.py` | `ledger_calls` | duplication | consolidate_duplication | major |
| `plugins/ndf/scripts/context-policy.py` | `report_work` | long_method | extract_method | major |
| `plugins/ndf/scripts/context-policy.py` | `session_parts` | long_method | extract_method | major |
| `plugins/ndf/skills/skill-stats/scripts/skill-stats.py` | `aggregate_by_project` | deep_nesting | extract_method | major |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | `_trial_cause` | magic_value | introduce_named_constant | major |
| `plugins/ndf/scripts/lib/context_decisions.py` | `parse_ts` | duplication | consolidate_duplication | major |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_research.py` | `run_research` | long_method | extract_method | minor |
| `plugins/ndf/scripts/context-policy.py` | `IDLE_CAP` | duplication | centralize_configuration | minor |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_research.py` | `research_one` | deep_nesting | flatten_conditional | minor |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | `plan_hint` | duplication | consolidate_duplication | minor |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` | `diffs_since` | duplication | consolidate_duplication | minor |
| `scripts/token-usage.py` | `read_codex` | long_method | extract_method | major |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_research.py` | `run_research` | long_parameter_list | introduce_parameter_object | major |
| `plugins/ndf/skills/skill-stats/scripts/skill-stats.py` | `emit_json` | data_clump | introduce_parameter_object | major |
| `plugins/ndf/scripts/lib/context_decisions.py` | `read_calls` | duplication | consolidate_duplication | major |
| `plugins/ndf/scripts/lib/context_decisions.py` | `read_calls` | long_method | extract_method | minor |
| `plugins/ndf/scripts/lib/context_policy.py` | `_positive_int` | duplication | extract_method | minor |
| `plugins/ndf/scripts/lib/sprint_loop_select.py` | `run_selection` | long_method | extract_method | minor |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_overhaul.py` | `count_types` | divergent_change | extract_method | minor |
| `plugins/ndf/scripts/relay_lib/terminal.py` | `Terminal` | hidden_dependency | change_signature | minor |
| `plugins/ndf/scripts/context-policy.py` | `cmd_record` | duplication | consolidate_duplication | minor |
| `plugins/ndf/scripts/lib/context_decisions.py` | `append_decision` | duplication | consolidate_duplication | minor |
| `plugins/ndf/scripts/lib/context_decisions.py` | `attach_marks` | duplication | consolidate_duplication | minor |
| `plugins/ndf/scripts/lib/context_decisions.py` | `write_applied` | duplication | consolidate_duplication | minor |

## どこで見つけたか

スプリントの Pull Request https://github.com/devbasex/ai-plugins/pull/2414 のリファクタリング計画（コメント `cross-refactoring plan rf2414`）。

## なぜこの変更の範囲外なのか

開発ループは、品質を上げる工程を上限で打ち切り、残りを次の周回の棚卸しへ回す（`development-workflow` の `references/loop.md`）。この周回の受け入れ条件は、#2346・#2315・#2262 の機能そのものである。根拠: Value 10（MVV 版はプロジェクトの MVV）。

## 直さないと何が起きるか

#2262（context-policy.py・context_decisions.py）、#2315（upkeep_depth.py）、skill-stats の新しいコードに、長いメソッドと重複が残る。次に触るときに読みにくく、同じ修正を 2 か所へ入れることになる。

## 起票者の推定
- 無し

## 由来

PR #2414

## 依頼（原文）

> クロスリファクタリングが時間の予算で見送った 24 件（スプリント lflow-1）

> スプリント lflow-1 の検査で、クロスリファクタリングは改善の候補を 30 件出した。そのうち 24 件は時間の予算に入らず見送った（理由 `budget`。残り 2 件は同じ変更の重複）。

> #2262（context-policy.py・context_decisions.py）、#2315（upkeep_depth.py）、skill-stats の新しいコードに、長いメソッドと重複が残る。次に触るときに読みにくく、同じ修正を 2 か所へ入れることになる。

（書き手: 起票者（開発ループが `out-of-scope` で起こした本文）、日付: 2026-11-02。利用者の指示の原文は無い）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-04）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 見送った 24 件を、次の周回で改修する対象として扱う | 開発ループの手順（`development-workflow` の `references/loop.md` の「品質を上げる工程は今の上限で打ち切る」） | 未決 1（進めるかどうかと量は承認ゲート 1 で決める） |
| S2 | 24 件を直さないと、次に触るときに同じ修正を 2 か所へ入れることになる | 本文の「直さないと何が起きるか」 | 目的 |

## 目的

- 見送った提案 24 件（関数・定数 22 個。同じ関数に別の兆候が 2 行ある関数が 2 個）について、振る舞いを変えずに長い関数と重複を減らす。次にこれらのファイルを直すとき、1 か所の修正で済む状態にする
- 改修しない行は、理由を残して終える。理由の無い見送りを次の周回へ持ち越さない

## 前提

- 前提 1: この課題はリファクタリングである。公開インタフェース（コマンドの引数・終了コード・標準出力の結果 JSON・`plugins/ndf/contracts/` が定めるファイルと環境変数の形）は変えない
- 前提 2: 24 行は今の `develop`（2026-11-05 の `729dcbe40`）にすべて残っている。22 個の関数・定数の定義をファイルごとに検索し、すべて見つかった（検索の結果は下の「確認した事実」）
- 前提 3: 配布物（`plugins/`）は、リポジトリだけの道具（`scripts/`）を import しない。`parse_ts` と `IDLE_CAP` は `scripts/token-usage.py` にも同じものがある。この 2 行の重複は `plugins/` の中で 1 か所にまとめる範囲で解き、`scripts/` 側との共有は扱わない
- 前提 4: 改修の前後で振る舞いが変わらないことは、改修の前の出力をそのまま期待値にしたテスト（現状固定テスト。`refactoring` の Skill の語）で確かめる。改修すると公開インタフェースが変わる行、または現状固定テストを書けない行は、改修せずに「採らない」と理由を記録する
- 前提 5: モードは `standard` とする（複数のファイルの構造を変えるため）

## 確認した事実

各行のファイルで `def <名前>` / `class <名前>` / `<名前> =` を検索した結果（2026-11-04、`729dcbe40`）。22 個すべてが見つかった。

| ファイル | 見つかった定義（行） |
| --- | --- |
| `plugins/ndf/scripts/context-policy.py` | `IDLE_CAP`（39）・`ledger_calls`（59）・`session_parts`（134）・`report_work`（191）・`cmd_record`（286） |
| `plugins/ndf/scripts/lib/context_decisions.py` | `parse_ts`（97）・`append_decision`（157）・`write_applied`（247）・`read_calls`（270）・`attach_marks`（348） |
| `plugins/ndf/scripts/lib/context_policy.py` | `_positive_int`（133） |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | `_trial_cause`（331）・`plan_hint`（448） |
| `plugins/ndf/scripts/lib/sprint_loop_select.py` | `run_selection`（247） |
| `plugins/ndf/scripts/relay_lib/terminal.py` | `Terminal`（49） |
| `plugins/ndf/skills/skill-stats/scripts/skill-stats.py` | `aggregate_by_project`（378）・`emit_json`（750） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_research.py` | `research_one`（294）・`run_research`（321） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` | `diffs_since`（109） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_overhaul.py` | `count_types`（65） |
| `scripts/token-usage.py` | `read_codex`（472）・`parse_ts`（91）・`IDLE_CAP`（60） |

## 対象範囲

含む:
- 「何を見つけたか」の表の 24 行それぞれの、改修するか採らないかの判定と、改修する行の改修
- 改修する行で、対象の関数を通るテストが無い場合の現状固定テストの追加

含まない:
- PR #2414 のリファクタリング計画で「未確認」になった 4 件（`calibrate` など。最終ゲートを経ずに残った改修）の確かめ直し
- 表に無い関数・ファイルのリファクタリング
- `scripts/token-usage.py` と `plugins/` の間で関数・定数を共有する仕組みの導入（前提 3）
- 開発ループが見送った提案を Issue に起こす手順そのものの見直し

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 各行を今のコードと突き合わせ、改修するか採らないかを決めた | 承認ゲート 1 を通った | 判定できない行は「採らない」と理由を書く（前提 4） | — |
| E2 | 改修する行の関数を通る現状固定テストを足した | E1 で改修すると決めた行に、通るテストが無い | テストを書けない行は E1 へ戻して「採らない」にする | E1 |
| E3 | 1 行（または同じ重複を解く複数行）ごとに改修を適用した | E2 のテストが改修前に通った | テストが落ちたら、その改修だけを取り消し、行を「採らない」にする | E2 |
| E4 | 全体テストと静的解析が通った | すべての行の E3 が終わった | 落ちた原因の改修を取り消し、E3 へ戻す | E3 |
| E5 | 24 行の判定と理由を Pull Request の本文に記録した | E4 が通った | — | E4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 見送った提案 | クロスリファクタリングが計画に入れなかった提案と、見送った改善項目。この課題では「何を見つけたか」の表の 24 行で、理由はどれも時間の予算（`budget`） |
| 全体テスト | リポジトリのすべてのテスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`） |

## 受け入れ条件

- [ ] Pull Request の本文に 24 行の表があり、各行に「改修した」か「採らない」と、採らない行には理由が 1 つ書いてある
- [ ] 「改修した」の行の関数には、その関数を通るテストがあり、改修の前のコミットと後のコミットの両方で通る
- [ ] 重複（兆候 duplication）を「改修した」の行では、同じ処理の本体が `plugins/` の中で 1 か所だけに定義されている（関数名か処理の検索で 1 件）
- [ ] 長い関数（兆候 long_method）と深い入れ子（兆候 deep_nesting）を「改修した」の行では、対象の関数の行数か入れ子の深さが改修の前より小さい（数値を本文の表に書く）
- [ ] 名前の無い値（兆候 magic_value）を「改修した」の行では、その値が名前の付いた定数を通して参照されている
- [ ] `uv run --frozen --project . --all-extras pytest . -q -n 4` が通る
- [ ] `python3 scripts/check-script-structure.py`・`python3 scripts/check-ndf-contracts.py`・`bash scripts/check-lint.sh` が 0 で終わる
- [ ] `plugins/ndf/contracts/` に差分が無い（公開インタフェースが変わっていない。退行しない）
- [ ] `plugins/` の Python が `scripts/` のモジュールを新たに import していない（`git diff` に該当の import の行が無い）
- [ ] `plugins/ndf/.claude-plugin/plugin.json` の `version` が変わっていない（版はスプリントの後に上げる）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 対象のスクリプトのコマンドの引数・終了コード・出力は変わらない。利用者の手元での操作は増えない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| データ | 変わらない（記録のファイルの形を変えない） |
| 既存の振る舞い | 変わらない。内部の関数の分け方と定義の場所だけが変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-script-structure.py` / `python3 scripts/check-ndf-contracts.py` / `bash scripts/check-lint.sh` |
| 手動確認 | 無し（振る舞いを変えないため、リリース後テストは自動テストの結果で足りる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。配布物（`plugins/`）から `scripts/` と `factory/` を参照しない |
| コーディング規約 | `plugins/ndf/skills/refactoring/SKILL.md`（テストで守ってから構造を変える）。確認は上の静的解析 |
| テスト戦略 | 既存の単体テストを現状固定テストとして使い、関数を通るテストが無い行だけ現状固定テストを足す。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 改修の前に対象の関数を通るテストを確かめる。全体テストと静的解析を流す |
| 確認してから行う | 公開インタフェースに触れる変更（行わずに「採らない」とし、承認ゲート 2 で人が見る） |
| 行わない | 表に無い関数の改修、`scripts/` と `plugins/` の共有の仕組みの導入、版数の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 未決 1: この課題を進めるか、進めるなら 24 行すべてか重大度 major の 10 行に絞るか。Value 2（人が 1 回の承認で読み切れる量）と Value 3（上限に当たったら縮めるを既定にする）に照らし、分量で分けるかもここで決める | 利用者（承認ゲート 1 の先頭の要否） | 承認ゲート 1 |
| 未決 2: Value 3 は Issue に起こすものを利用者へ届いた不具合などに限るが、開発ループの手順（`loop.md`）は見送った提案を Issue に起こす。この食い違いの扱い（この課題の範囲外。前提とせず記録だけする） | 利用者 | 承認ゲート 1 |
