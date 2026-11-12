"""雛形のテストとマージのステップ（#1334 AC7）。

範囲テストと全体テストは `test-run.py` が宣言の戦略で走らせる。ステップの `timeout` と `merge-when-green` の
`--timeout` は、`apply_decls` が宣言から出した上限（`test_limits`。`lib/test_strategy.limits`）に余裕を足したもので、
固定の秒は持たない。所要が宣言に無いときだけ「所要が不明のときの値」を使い、計画の `テストの時間` にそう書く。
"""

from __future__ import annotations

import math
import shlex

import delivery
import test_strategy as ts
import limits
from step_result import EXIT_BASE_CONFLICT
from supervise_lib.decl import scope_paths, test_kind
from supervise_lib.paths import CHECK_PY, CI_PEEK_CMD, MERGE_CMD, MERGE_GATE_CMD, MERGE_PROBE, READY, SPRINT_STATE_PY, STEPS_PY, TEST_RUN_PY
from supervise_lib.procedures import DESIGN_MATCH_MODES, base_sync_steps, design_diff_on_pr, design_match_steps, pr_step, push_fix_steps

# merge-when-green と release が CI の基盤待ち（75。lib/step_result.py の EXIT_INFRA_WAIT）で止まったら、judge を通らず
# プランを止める（修正へ回さない。#1645 の決定 6）
INFRA_WAIT_STOP = {"75": "stop"}

# ステップの `timeout` に足す余裕（上限の 1 割。下限は監視の 1 周期の 2 倍）
MARGIN_SHARE = 0.1
MARGIN_FLOOR = limits.seconds("supervise.verify-margin-floor")

# マージに続く後片付けのテスト環境の回収の上限（秒）。`lib/container_reach.py` の RECLAIM_LIMIT と同じ値である。
# `lib/container_reach.py` はプランを作る側が読まない環境変数と設定を読むため、ここへは import しない（#2660 の決定 8）
RECLAIM_LIMIT = 330


def plan_strategy(a) -> dict | None:
    """宣言から解いた戦略（`apply_decls` が載せる）。無ければ `--test-cmd` の雛形から解く。"""
    found = getattr(a, "strategy", None)
    if isinstance(found, dict):
        return found
    template = getattr(a, "test_cmd", None)
    if not template:
        return None
    return ts.resolve({}, baseline_test=template, template_kind=test_kind(a), scope_paths=scope_paths(a)).as_state()


def plan_limits(a) -> dict:
    """テストと CI の待ちの上限（`apply_decls` が載せる `test_limits`。無ければ所要が不明のときの値）。"""
    found = getattr(a, "test_limits", None)
    if isinstance(found, dict):
        return found
    strategy = plan_strategy(a)
    return ts.limits(ts.Strategy.from_state(strategy) if strategy else ts.Strategy(ts.LOCAL_FULL, "args"), None)


def with_margin(seconds: int) -> int:
    """ステップの `timeout` = 上限 + 余裕。"""
    return int(seconds) + max(MARGIN_FLOOR, math.ceil(int(seconds) * MARGIN_SHARE))


CD_RESULT_FILE = "{state_dir}/work/cd-result.json"  # cd-result が書き record が読む結果のファイル（#2623）
CD_FAILED_STOP = {"5": "stop"}  # CD の結果が失敗（record が記録を書いた後の 5）なら judge-record を通らずに止める


def cd_result_step(repo: str | None, base: str, head: str | None, prs: str | None = None) -> dict:
    """本番チャネルへのマージの後に CD の結果を読むステップ。宣言に依らず常に置き、CD の行が無ければ `none` を書く（#2623 の決定 8）。
    結果は 0 で返り、record が書いた後に失敗を止める。`prs` があれば最後の PR のマージのコミットを読む（record --merged と同じリビジョン）。"""
    head_arg = (f" --head {shlex.quote(head)}" if head else "") + (f" --prs {prs}" if prs else "")
    return {
        "id": "cd-result",
        "type": "run",
        "stage": "デプロイ",
        "timeout": with_margin(delivery.RESULT_TOTAL_LIMIT),
        **({"cwd": repo} if repo else {}),
        "cmd": f"{STEPS_PY} cd-result --base {shlex.quote(base)}{head_arg} --out {CD_RESULT_FILE}",
        "next": "record",
    }


IMPL_STALL_DEFAULT = limits.seconds("supervise.impl-stall")


def _template_arg(a) -> str:
    """`--template` と、静的解析の雛形なら `--test-kind lint`（種別は `test-run.py` へそのまま渡す）。"""
    template = getattr(a, "test_cmd", None)
    if not template:
        return ""
    kind = test_kind(a)
    return f" --template {shlex.quote(template)}" + (f" --test-kind {kind}" if kind != ts.TEST else "")


def limit_arg(key: str, seconds: int) -> str:
    """テストのステップの `--limit`。run のステップが開始の時点で上限を決め直して置き換える（`steps.fill_test_limit`。#1631）。

    `{test_limit:<上限の表の鍵>:<プランの作成の時点の秒>}` の形で、決め直せなければ作成の時点の秒になる。"""
    return f" --limit {{test_limit:{key}:{int(seconds)}}}"


def scope_cmd(a, tests: str) -> str:
    """範囲テストのステップのコマンド（`test-run.py scope`）。"""
    limit = limit_arg("test_timeout", plan_limits(a)["test_timeout"])
    return f"{TEST_RUN_PY} scope --paths {tests}{_template_arg(a)}{limit}"


def _lint_paths_arg(a) -> str:
    """静的解析の雛形を渡すときの ` --paths`（雛形の `{paths}` へ入る対象。計画時の `plan_strategy` と同じ範囲）。"""
    paths = (getattr(a, "test_paths", None) or scope_paths(a)) if getattr(a, "test_cmd", None) and test_kind(a) == ts.LINT else []
    return " --paths " + " ".join(shlex.quote(p) for p in paths) if paths else ""


def whole_cmd(a) -> str:
    """全体テストのステップのコマンド（`test-run.py whole`。CI に任せる戦略は Pull Request のチェックを待つ）。"""
    extra = _lint_paths_arg(a)
    strategy = plan_strategy(a)
    # CI に任せる戦略のステップの timeout は CI の待ちの上限で決まるため、手元の上限を渡さない
    if not (strategy and strategy.get("name") == ts.LOCAL_SCOPED_CI_WHOLE):
        extra += limit_arg("whole_timeout", plan_limits(a)["whole_timeout"])
    return f"{TEST_RUN_PY} whole --pr {{pr}}{_template_arg(a)}{extra}"


def scope_timeout(a) -> int:
    return with_margin(plan_limits(a)["test_timeout"])


def ci_only(a) -> bool:
    """CI テストの宣言（`test.ci.only: true`）のあるリポジトリか（#3056）。手元でテストを走らせず、PR の CI の結果で合否を決める。"""
    strategy = plan_strategy(a)
    return bool(strategy and strategy.get("ci_only"))


def whole_on_ci(a) -> bool:
    """全体テストを CI に任せる戦略（`local-scoped-ci-whole`）か、CI テストの宣言があるか。"""
    strategy = plan_strategy(a)
    return bool(strategy and (strategy.get("name") == ts.LOCAL_SCOPED_CI_WHOLE or strategy.get("ci_only")))


def whole_timeout(a) -> int:
    """全体テストのステップの `timeout`。手元なら `whole_timeout`、CI なら `ci_wait_timeout` に余裕を足す。"""
    limits = plan_limits(a)
    return with_margin(limits["ci_wait_timeout"] if whole_on_ci(a) else limits["whole_timeout"])


def without_ci_wait(a, steps: list[dict], after_whole: str, drop: tuple[str, ...] = (), merge_fail: str | None = None) -> list[dict]:
    """全体テストを CI に任せる戦略なら、全体テストのステップ（test-all。PR の CI を待つ）と `drop` を外し、test-all へ
    向かう遷移を `after_whole` へ、入力から test-all を外す。CI の結果を待つのはマージのステップ（merge-when-green）だけで、
    落ちればマージしない（2026-11-12、利用者の指示）。手元で走らせる戦略ならそのまま返す。

    `merge_fail` を渡すと、マージ（merge・merge-approved）が落ちたときにそのステップ（judge）へ戻し、そこと fix の入力へ
    merge と merge-approved を足す（CI の失敗を直しの輪へ戻す。test-all が持っていた行き先の代わり）。承認の後は
    merge-approved だけが流れるため、両方を足す。流れていない方は入力に「まだ流れていない」と載るだけである。"""
    if not whole_on_ci(a):
        return steps
    gone = {"test-all", *drop}
    out = []
    for step in (s for s in steps if s["id"] not in gone):
        step = {k: (after_whole if k in ("next", "on_fail", "skip_to") and val == "test-all" else val) for k, val in step.items()}
        if "inputs" in step:
            step["inputs"] = [i for i in step["inputs"] if i not in gone]
        if merge_fail and step["id"] in ("merge", "merge-approved"):
            step["on_fail"] = merge_fail
        if merge_fail and step["id"] in (merge_fail, "fix"):
            step["inputs"] = [*step.get("inputs", []), "merge", "merge-approved"]
        out.append(step)
    return out


def trim_check_steps(a, steps: list[dict], after_whole: str, refactor_off: bool) -> list[dict]:
    """検査のプランのステップを外す。`refactor_off` なら構造改善（assess・refactor）を外す。全体テストを CI に任せる戦略なら、
    レビューの後の全体テスト（test-all と、それだけを入力に持つ judge・fix）を外す（`without_ci_wait`）。"""
    kept = [s for s in steps if not (refactor_off and s["id"] in ("assess", "refactor"))]
    return without_ci_wait(a, kept, after_whole, drop=("judge", "fix"))


CI_FIX_PROMPT = (
    "入力は CI の結果の監視（ci-peek）の出力で、落ちた CI のテストのチェックと失敗したジョブのログの末尾がある。"
    "落ちたテストを直してコミットする（テストを手元で打たない。合否は push の後の CI で読む）。変更に起因しない失敗は直さない。"
)
CI_FIX_PUSH = "直した後に git push する。"
CI_PEEK_IDS = ("ci-peek", "ci-peek-2", "ci-peek-merge")


def ci_watch_steps(peeks: list[tuple[str, str]], fix_next: str, stop: str = "stop", push: bool = False) -> list[dict]:
    """CI の結果の監視のステップ（#3056 の決定 4〜6）。`peeks` は (ステップの id, 成功の行き先) の並び（`ci-peek`・`ci-peek-2`）。

    - `ci-peek*`: PR の CI のテストのチェックを待たずに 1 回だけ読む。0 なら次へ、1（落ちた）なら `ci-fix`、2（改修の回数が
      上限に当たった）なら `stop`
    - `ci-peek-merge`: マージが落ちた後に読み直す（`--after-merge`）。CI の失敗なら `ci-fix`、上限（2）か CI 以外の理由（3）なら `stop`
    - `ci-fix`: 修正の worker。直してコミットし `fix_next` へ戻る。`push` なら自分で push する（検査のプランは pr のステップを持たない）"""
    stop_codes = {"2": stop}
    steps = [
        {
            "id": sid,
            "type": "run",
            "stage": "完了判定",
            "timeout": 300,
            "cmd": CI_PEEK_CMD,
            "on_fail": "ci-fix",
            "on_exit": stop_codes,
            "next": nxt,
        }
        for sid, nxt in peeks
    ]
    steps.append(
        {
            "id": "ci-peek-merge",
            "type": "run",
            "stage": "完了判定",
            "timeout": 300,
            "cmd": CI_PEEK_CMD + " --after-merge",
            "on_fail": "ci-fix",
            "on_exit": {**stop_codes, "3": stop},
            "next": "end",
        }
    )
    prompt = CI_FIX_PROMPT + (CI_FIX_PUSH if push else "push しない。")
    steps.append(
        {
            "id": "ci-fix",
            "type": "work",
            "kind": "修正",
            "stage": "完了判定",
            "inputs": list(CI_PEEK_IDS),
            "prompt": prompt,
            "next": fix_next,
        }
    )
    return steps


def merge_to_ci_peek(steps: list[dict]) -> list[dict]:
    """マージ（merge・merge-approved）が落ちたら `ci-peek-merge` で読み直す（改修の回数を監視の記録で数える。決定 6）。"""
    return [{**s, "on_fail": "ci-peek-merge"} if s["id"] in ("merge", "merge-approved") else s for s in steps]


def without_local_tests(steps: list[dict], after_tests: str) -> list[dict]:
    """手元のテストのステップ（test-limited・test-all）と、それだけを入力に持つ judge・fix を外し、そこへ向かう遷移を
    `after_tests` へ向ける（#3056 の I2）。"""
    gone = {"test-limited", "test-all"}
    gone |= {s["id"] for s in steps if s.get("inputs") and set(s["inputs"]) <= gone}
    out = []
    for step in (s for s in steps if s["id"] not in gone):
        step = {k: (after_tests if k in ("next", "on_fail", "skip_to") and v in gone else v) for k, v in step.items()}
        if "inputs" in step:
            step["inputs"] = [i for i in step["inputs"] if i not in gone]
        out.append(step)
    return out


def refactor_template_arg(a) -> str:
    """`--test-cmd` を渡したときだけ、cross-refactoring へ同じ雛形を `--baseline-test` で渡す（宣言があれば渡さない）。"""
    template = getattr(a, "test_cmd", None)
    if not template:
        return ""
    kind = test_kind(a)
    return f" --baseline-test {shlex.quote(template)}" + (f" --test-kind {kind}" if kind != ts.TEST else "")


def merge_steps(a, on_conflict: str | None = None, **extra) -> list[dict]:
    """マージの 3 ステップ（#1336 の決定 1）。

    - `merge-gate`: 宛先がリリース承認ゲート（自動反映の本番チャネル）に当たるかだけを判定する。当たればプランを終える（`gate_next: end`）
    - `merge`: CI を待ってマージする。CI の待ちの上限を `--timeout` で渡し、ステップの `timeout` はそれに余裕と、
      マージに続く後片付けのテスト環境の回収の上限を足す
    - `merge-approved`: `merge` に `--gate-approved user` を足したもの。通常の流れからは入らず、承認の後に `--from merge-approved` でだけ入る

    `merge` と `merge-approved` は CI の基盤待ち（75）で judge を通らずに止まる（`on_exit`）。`on_conflict` を渡すと、
    PR がベースブランチと衝突したとき（4。`EXIT_BASE_CONFLICT`）にそのステップへ進む（#1917）。渡さなければ今の失敗と同じ。

    `extra` の `next`（と `on_fail`）は `merge` と `merge-approved` に付ける。`next` が無いと `merge` が並びの次の
    `merge-approved` へ流れるため、必ず渡す。
    """
    if "next" not in extra:
        raise ValueError("merge_steps には next が要る（merge が merge-approved へ流れないように）")
    ci_wait = int(plan_limits(a)["ci_wait_timeout"])
    merge = {
        "id": "merge",
        "type": "run",
        "timeout": with_margin(ci_wait) + RECLAIM_LIMIT,
        "cmd": f"{MERGE_CMD} --timeout {ci_wait}",
        "probe": MERGE_PROBE,
        "on_exit": {**INFRA_WAIT_STOP, **({str(EXIT_BASE_CONFLICT): on_conflict} if on_conflict else {})},
        **extra,
    }
    gate = {"id": "merge-gate", "type": "run", "timeout": 120, "cmd": MERGE_GATE_CMD, "gate_next": "end", "next": "merge"}
    if extra.get("on_fail"):
        gate["on_fail"] = extra["on_fail"]
    approved = {**merge, "id": "merge-approved", "cmd": f"{MERGE_CMD} --timeout {ci_wait} --gate-approved user"}
    return [gate, merge, approved]


def handoff_step(state: str, gate: str, what: str) -> dict:
    """MVV 判定で通した後のステップ（ラベル・マージ・判定のコメント）が落ちたときに承認ゲートへ落とすステップ（#1370 の I8）。
    `mvv` のステップが書いた `by: mvv` の記録を外し、理由を 1 行出して終了コード 10（承認ゲート）を返す。外せなくても 10 を返す。"""
    st, g = shlex.quote(state), shlex.quote(gate)
    why = shlex.quote(f"{what}が失敗した。{gate}は自動で通さず、利用者の承認を求める")
    cmd = (
        f'sh -c \'{SPRINT_STATE_PY} gate "$1" "$2" --withdraw >/dev/null || echo "$2 の MVV 判定の記録を外せなかった"; '
        f'echo "$3"; exit 10\' handoff {st} {g} {why}'
    )
    return {"id": "handoff", "type": "run", "timeout": 120, "cmd": cmd, "gate_next": "end", "next": "end"}


REJECT_STEP = "reject"


def reject_step(state: str, gate: str, next_id: str, root: str = ".", pr: str | None = None) -> dict:
    """承認ゲートで差し戻された後の続き（#2183）。直した後に `run <計画> --from reject` で流すと、差し戻しを差し戻しの記録
    （`gate-rejections.jsonl`）へ 1 行書いてから `next_id`（再提示のステップ）へ進む。記録を書けなければ止まる。
    conductor は `sprint-state.py gate ... --outcome rejected` を手で打たない。"""
    what = shlex.quote(f"{gate}で差し戻された（直して再提示する）")
    cmd = (
        f"{SPRINT_STATE_PY} gate {shlex.quote(state)} {shlex.quote(gate)} --what {what} --by user --outcome rejected "
        f"--root {shlex.quote(root)}" + (f" --pr {pr}" if pr else "")
    )
    return {"id": REJECT_STEP, "type": "run", "timeout": 120, "cmd": cmd, "next": next_id, "from_only": True}


def test_meta(plan: dict, a) -> dict:
    """計画の最上位に `テストの戦略` と `テストの時間` を書く。"""
    strategy = plan_strategy(a)
    if strategy:
        plan["テストの戦略"] = {"name": strategy["name"], "source": strategy["source"], "note": getattr(a, "test_note", None)}
        if strategy.get("ci_only"):
            plan["テストの戦略"]["ci_only"] = True  # worker の起動指示がテストを手元で打たない行を持つ（#3056）
    plan["テストの時間"] = dict(plan_limits(a))
    return plan


def pre_test_steps(a, fix_prompt: str, after: str = "test-limited") -> list[dict]:
    """範囲テストの前のステップ: 整形（format）→ 同期とチェック（sync）→ 範囲の静的解析（lint-limited）。宣言の無いものは
    置かない（format は supervise.json の format、sync は sync_checks、lint-limited は範囲の雛形を持つ kind: lint の suite が
    あり戦略が round-only でないとき。#1651 の決定 5・6）。最後のステップの次は `after`（既定は範囲テスト）。
    fix-format・fix-sync・fix-lint（修正の worker。`fix_prompt`）は並びの先頭へ戻る
    （sync は静的解析も打つため、fix-sync の直しも整形を通し直す）。
    実装のプランの雛形（`templates.plan_to_merge`）が起点の取り込みの後に置く。"""
    st = plan_strategy(a) or {}
    lint = st.get("name") != ts.ROUND_ONLY and any(s.get("scope_command") and s.get("kind") == ts.LINT for s in st.get("suites") or [])
    lint_cmd = scope_cmd(a, " ".join(a.tests)) + " --only-kind lint"
    wanted = [
        (getattr(a, "format", None), {"id": "format", "type": "run", "preset": "format", "stage": "実装", "on_exit": {"2": "stop"}}),
        (getattr(a, "sync_checks", None), {"id": "sync", "type": "run", "preset": "sync-check", "stage": "実装"}),
        (
            lint,
            {
                "id": "lint-limited",
                "type": "run",
                "stage": "完了判定",
                "timeout": scope_timeout(a),
                "cmd": lint_cmd,
                "on_exit": {"2": "stop"},
            },
        ),
    ]
    found = [step for want, step in wanted if want]
    out: list[dict] = []
    for i, step in enumerate(found):
        fix = "fix-" + step["id"].split("-")[0]
        nxt = found[i + 1]["id"] if i + 1 < len(found) else after
        out += [
            {**step, "on_fail": fix, "next": nxt},
            {"id": fix, "type": "work", "kind": "修正", "inputs": [step["id"]], "prompt": fix_prompt, "next": found[0]["id"]},
        ]
    return out


def check_ci_watch(a, steps: list[dict], before: str, stop: str, *, fix_push: bool) -> list[dict]:
    """CI テストの宣言のある検査のプランへ、`before`（ready か finish）の前の `ci-peek` と、マージが落ちた後の `ci-peek-merge`・
    `ci-fix` を足す（#3056）。手元のテストのステップは外す。宣言の無いリポジトリはそのまま返す。"""
    if not ci_only(a):
        return steps
    steps = merge_to_ci_peek(without_local_tests(steps, "ci-peek"))
    peek, *rest = ci_watch_steps([("ci-peek", before)], "ci-peek", stop=stop, push=fix_push)
    at = next(i for i, s in enumerate(steps) if s["id"] == before)
    end = next(i for i, s in enumerate(steps) if s["id"] == "merge-approved") + 1
    return [*steps[:at], peek, *steps[at:end], *rest, *steps[end:]]


def with_escape(a, steps: list[dict]) -> list[dict]:
    """`--escape-of` なら、マージの後に「逃げた不具合」を記録するステップを足す（検査のトリガーの材料。#1078）。"""
    if getattr(a, "escape_of", None) is None:
        return steps
    steps = [{**s, "next": "escape"} if s["id"] in ("merge", "merge-approved") else s for s in steps]
    return [*steps, {"id": "escape", "type": "run", "cmd": f"{CHECK_PY} escape --pr {{pr}} --of {a.escape_of}", "next": "end"}]


def lint_whole_steps(a, fix_prompt: str, after: str, back_to: str) -> list[dict]:
    """CI テストの宣言のある実装のプランで、`command` を持つ kind: lint の suite を手元で全体に打つステップ（`lint-all`）と、
    その修正（`fix-lint-all`。直して `back_to` へ戻る）。テストを CI に任せても静的解析の不合格を見逃さない（全体テストの
    test-all を外した代わり。round-only の戦略の範囲の静的解析もここで打つ）。該当する suite が無ければ置かない。"""
    st = plan_strategy(a) or {}
    if not any(s.get("kind") == ts.LINT and s.get("command") for s in st.get("suites") or []):
        return []
    limit = limit_arg("whole_timeout", plan_limits(a)["whole_timeout"])
    return [
        {
            "id": "lint-all",
            "type": "run",
            "stage": "完了判定",
            "timeout": with_margin(plan_limits(a)["whole_timeout"]),
            "cmd": f"{TEST_RUN_PY} whole --only-kind lint{_template_arg(a)}{_lint_paths_arg(a)}{limit}",
            "on_fail": "fix-lint-all",
            "on_exit": {"2": "stop"},
            "next": after,
        },
        {"id": "fix-lint-all", "type": "work", "kind": "修正", "inputs": ["lint-all"], "prompt": fix_prompt, "next": back_to},
    ]


def ci_only_to_merge(a, head: list[dict], fix_prompt: str) -> tuple[list[dict], str]:
    """CI テストの宣言のあるリポジトリの実装のプランのステップ（#3056 の決定 4・6・7）。戻りは（ステップ, 規則）。

    head の直後に Draft の PR を出し（`pr-draft`。本文は機械のテンプレートで LLM を呼ばない）、取り込み → 整形・同期・範囲の
    静的解析 → 静的解析の全体（`lint-all`。`command` を持つ kind: lint の suite があるとき）→（standard なら）設計との突き合わせ →
    pr と進む。手元でテストを走らせず、pr の後と doc-lint の後に CI の結果を
    待たずに 1 回だけ読み（`ci-peek`・`ci-peek-2`）、落ちていれば `ci-fix` が直して整形から流れ直す。マージが落ちたら
    `ci-peek-merge` が読み直す。"""
    match = design_match_steps(a.issue, a.base, "pr") if a.mode in DESIGN_MATCH_MODES and a.issue else []
    after_lint = match[0]["id"] if match else "pr"
    lint_all = lint_whole_steps(a, fix_prompt, after=after_lint, back_to="")
    after_pre = lint_all[0]["id"] if lint_all else after_lint
    pre = pre_test_steps(a, fix_prompt, after=after_pre)
    first = pre[0]["id"] if pre else after_pre
    if lint_all:
        lint_all[-1]["next"] = first
    if head:
        head[-1]["next"] = "pr-draft"
    *base, back = base_sync_steps(f"--base {shlex.quote(a.base or '')}", first, push=False, on_error=None, returns=True, stage="実装")
    texts = (a.base, a.title, a.summary or "", getattr(a, "changes", None) or "")
    draft = {
        **pr_step(*texts, "base-sync", stage="実装", body="template"),
        "id": "pr-draft",
        # push 前の検査で拒まれたら、整形と静的解析を通した後の pr のステップが push する
        "on_fail": "base-sync",
        "on_fail_only": "push-check",
    }
    pr = pr_step(*texts, "ci-peek", {"manual": bool(getattr(a, "manual", True)), "migration": True})
    judge = []
    if match:
        design_diff_on_pr(pr, "judge")
        question = "PR が「設計と違う点」の節を欠いて止まったなら突き合わせをやり直すか（design-tests）、止めるか（stop）"
        judge = [{"id": "judge", "type": "judge", "inputs": ["pr"], "question": question, "choices": ["design-tests", "stop"]}]
    peek, peek2, *after_merge = ci_watch_steps([("ci-peek", "doc-lint"), ("ci-peek-2", "ready")], first)
    steps = [
        *head,
        draft,
        *base,
        *pre,
        *lint_all,
        *judge,
        *match,
        pr,
        *push_fix_steps(pr),
        peek,
        {"id": "doc-lint", "type": "run", "preset": "doc-lint", "stage": "完了判定", "on_fail": "fix-doc", "next": "ci-peek-2"},
        {
            "id": "fix-doc",
            "type": "work",
            "kind": "修正",
            "stage": "完了判定",
            "inputs": ["doc-lint"],
            "prompt": "ヒットした行を今の決まりだけを書く形へ直してコミットする（push しない）。",
            "next": "doc-lint",
        },
        peek2,
        {"id": "ready", "type": "run", "cmd": READY, "next": "merge-gate"},
        *merge_to_ci_peek(merge_steps(a, next="end", on_conflict="base-return")),
        *after_merge,
    ]
    return [*with_escape(a, steps), back], RULE_CI_ONLY


RULE_CI_ONLY = (
    "テストは手元で走らせず、PR の CI の結果で合否を決める（CI テストの宣言）。CI の結果の監視（ci-peek）が落ちたテストを見つけたら"
    " ci-fix が直す。改修の回数が上限に当たったら止める。"
)


def judge_step(after_tests: str, match: bool, ci_merge: bool = False) -> dict:
    """実装のプランの judge。範囲テストの失敗が変更に無関係なら `after_tests`（突き合わせがあれば design-tests、無ければ pr）へ
    進む。突き合わせがあれば PR の本文の節が欠けて pr が止まったとき、`ci_merge` ならマージが落ちたときもここへ戻る。"""
    to_next = "設計との突き合わせへ（design-tests）" if match else "PR へ（pr）"
    pr_missing = "、PR が「設計と違う点」の節を欠いて止まったなら突き合わせをやり直すか（design-tests）" if match else ""
    return {
        "id": "judge",
        "type": "judge",
        "inputs": ["test-limited", "test-all", *(["pr"] if match else [])],
        "question": f"テストの失敗を直すか（fix）、範囲テストの失敗が変更に無関係なら{to_next}{pr_missing}、"
        f"全体テストの失敗が変更に無関係なら文書のチェックへ（doc-lint）{'、マージ（merge）が CI で落ちたなら直すか（fix）' if ci_merge else ''}、止めるか（stop）",
        "choices": ["fix", after_tests, "doc-lint", "stop"],
    }
