"""最終ゲート（Step 7）。**起動のされ方で判定の相手が変わる**（#436 決定 7）。

| 起動のされ方 | 最終ゲート | 見分け方 |
| --- | --- | --- |
| `development-workflow` の 1 工程 | `cross-review` を省き、**全体テスト**で判定 | `--workflow-step` |
| 単独 | `cross-review` を実行 | 既定 |

**引数で受け取る。** 環境変数や記録の読み取りは、起動元が違っても同じ値になりうる。
呼ぶ側が明示する形にすれば、判定が 1 か所で済む。

全体テストの置き場は戦略で決まる（#1334）。`local-full` / `round-only` は手元で走らせ、`local-scoped-ci-whole`（か
`--ci-check`）は push 済みの HEAD のチェックを上限まで待つ。落ちたら JUnit から落ちたテストを取り、フレーキー・既存失敗・
変更起因に分け、変更起因が無ければ通す（I5）。

**時間で打ち切らず、確かめを省かない**（#2457 の原則 1・I11）。落ちたら落ちたテストの材料（ID・ログの置き場・走らせ直す
コマンド。決定 12）を最終ゲート修正へ渡し、落ちたテストの集合の履歴で前進を見る（決定 4）。停滞か振動したら修正担当を
1 度だけ替え、替えた後も停滞か振動したら、打ち消す項目を決められないため止めて人へ渡す（`FINAL_GATE=halt`）。

**テストで見つからない誤りを拾う工程は消えない。** 工程として起動したときに省いた
分は、工程表の「実装レビュー」（`pr` → `cross-review`）が持つ。ここへ軽量なレビューを
足すと、同じ差分を 2 度レビューすることになる。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from typing import Any, Optional

import statefile
import test_strategy as ts
import test_triage

from .. import failure_material, fixloop, gate_lint, info, launch, prepush_lint, progress, publish, timeline, triage
from . import flaky
from ..gitfacts import run_with_timeout, safe_int
from ..gate_ci import ci_checks, ci_gate, ci_mode
from ..paths import head_sha, load_state, work_dir


def cmd_final_gate(args: argparse.Namespace) -> None:
    """最終ゲートを通す（#933 の AC16b・決定 15・決定 16、#1334 F4〜F6、#2457）。

    終了コード: 0 = 通過（または `cross-review` を実行する） / 2 = 落ちた（修正ラウンドへ）・フレーキーの直しへ（`FINAL_GATE=flaky`）・
    止めて人へ渡す（`FINAL_GATE=halt`）/
    4 = 判断できない（CI が終わらない）。**木が起点と同じでも確かめを走らせる**（I11）。

    | 全体テストの置き場 | 見るもの |
    | --- | --- |
    | CI（`local-scoped-ci-whole` か `--ci-check`） | 継続的統合の結果。`pending` の間は `limits.ci_wait_timeout` まで待つ |
    | 手元 | HEAD で全体テストを 1 度。検証の中で通り、取り消しが無く、HEAD が進んでいなければ使い回す |

    単独起動は、通れば `cross-review` へ渡す（`FINAL_GATE=cross-review`）。
    """
    path, state = load_state(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    standalone = not state.get("workflow_step")
    state["phase"] = "final"
    statefile.save(path, state)
    # 検証を通らずに来た実行（残る項目が 0 件）でも、判定の前に 1 度だけ公開する。HEAD が公開した地点と
    # 同じなら何もしない。CI で見る戦略では、この push が無いと読むチェックが動かない
    publish.enter_final_gate(path, state)

    state.pop("launch_failure", None)
    reused = _reusable_failure(state, gate)
    if reused is not None:
        passed, detail = False, reused
    else:
        passed, detail = _run_gate_checks(path, state, gate)
        _remember_failure(state, gate, passed, detail)
        if (state.get("flaky_request") or {}).get("tests"):
            # フレーキーと見分けたテストを先に直し、直した木で確かめ直す（#2457 決定 9）
            statefile.save(path, state)
            info("🔁 フレーキーなテストを直してから最終ゲートを確かめ直します")
            statefile.emit(FINAL_GATE="flaky")
            sys.exit(2)

    if passed and standalone:
        _emit_cross_review(
            path,
            state,
            gate,
            f"✅ 最終ゲートのチェックが通りました（{detail}）。続けて /ndf:cross-review を実行します",
        )
        return
    if passed:
        _gate_passed(path, state, gate, detail)
        return

    halt = _final_fix_stop(state, gate)
    if halt is None:
        _gate_failing(path, state, gate, detail)
        return
    _gate_halt(path, state, gate, detail, halt)


def _gate_failing_set(gate: dict[str, Any]) -> list[str]:
    """最終ゲートの落ちたテストの集合（無関係とフレーキーを除く）。ID が無ければ落ちた検査の鍵か `<whole>`（決定 12）。"""
    triage_rec = gate.get("triage") or {}
    ids = [str(i) for i in triage_rec.get("caused") or []] or [str(i) for i in (gate.get("material") or {}).get("ids") or []]
    lint = [f"lint:{v.get('suite')}" for v in gate.get("lint") or [] if v.get("verdict") == "caused"]
    found = sorted(set(ids + lint))
    return found or [progress.WHOLE]


def _final_fix_stop(state: dict[str, Any], gate: dict[str, Any]) -> Optional[str]:
    """最終ゲート修正を止めて人へ渡す理由。続けられれば `None`（#2457 決定 4）。

    落ちたテストの集合を履歴（`fix_history`）へ積み、前進する限り続ける。停滞か振動したら修正担当を 1 度だけ替え、
    替えた後も停滞か振動したら止める（最終ゲートの失敗は打ち消す項目を決められないため、打ち切らずに人へ渡す）。
    起動し直しても解けない結末（利用上限）も止める。
    """
    if gate.get("no_relaunch"):
        return "修正担当を起動し直しても解けない結末だった"
    history = gate.setdefault("fix_history", [])
    seq = int(gate.get("fix_rounds") or 0)
    if not history or int(history[-1].get("round", -1)) != seq:
        failing = _gate_failing_set(gate)
        verdict = progress.compare([h.get("failing") or [] for h in history] + [failing])
        history.append({"round": seq, "by": _final_fix_impl(state, gate), "failing": failing, "progress": verdict})
    verdict = str(history[-1].get("progress"))
    if verdict not in progress.STOPPING:
        return None
    if gate.get("fixer_switched"):
        return "修正担当を替えた後も最終ゲート修正が停滞か振動した（打ち消す改善項目を決められない）"
    new = fixloop.other_runtime(state, [_final_fix_impl(state, gate)])
    if new is None:
        return "最終ゲート修正が停滞か振動し、替える修正担当が居ない"
    gate["impl"], gate["fixer_switched"] = new, True
    info(f"🔁 最終ゲート修正が{('停滞' if verdict == progress.STALLED else '振動')}したため、修正担当を {new} へ替えます")
    return None


def _gate_halt(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str, why: str) -> None:
    """止めて人へ渡す（変更は残す。#2457 決定 4・5）。"""
    gate["status"] = "halted"
    state["halt"] = {"reason": "final_gate", "detail": f"{why} / {detail}", "at": statefile.now()}
    statefile.save(path, state)
    info(f"⏸ 最終ゲートが通らないまま止めて人へ渡します（{why} / {detail}）。項目は取り消しません")
    statefile.emit(FINAL_GATE="halt", HALT_REASON=why)
    sys.exit(2)


def _run_gate_checks(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, str]:
    """全体テスト（か継続的統合）と静的解析の全体テストを走らせ、合否と記録の 1 行を返す。"""
    whole_started: Optional[float] = None
    gate.pop("triage", None)  # 前回の見分けを残さない（今回のテストが通り静的解析だけが落ちたとき、古い変更起因で取り消さない）
    if _reusable_whole_test(state):
        gate["whole_test_reused"] = True
        passed, detail = True, "検証の中で通った全体テストを使い回しました（HEAD は進んでいません）"
        gate["mode"] = "test"
    else:
        passed, detail, test_seconds = _run_and_record_gate_check(path, state, gate)
        if gate.get("mode") == "test":
            whole_started = time.monotonic() - test_seconds
    lint_passed, lint_detail = gate_lint.lint_gate(path, state, gate, started=whole_started)
    if lint_detail:
        detail = f"{detail} / {lint_detail}"
    return passed and lint_passed, detail


def _fix_marker(gate: dict[str, Any]) -> int:
    """取り込んだ最終ゲート修正の数。判定の後に取り込みがあったかを見分ける。"""
    return len(gate.get("fix_commits") or [])


def _remember_failure(state: dict[str, Any], gate: dict[str, Any], passed: bool, detail: str) -> None:
    """落ちた判定を HEAD と結んで残す（決定 5）。通ったら消す。"""
    if passed:
        gate.pop("last_failing", None)
        return
    gate["last_failing"] = {"head": head_sha(work_dir(state)), "detail": detail, "fix_commits": _fix_marker(gate)}


def _reusable_failure(state: dict[str, Any], gate: dict[str, Any]) -> Optional[str]:
    """差し戻しの後の同じ HEAD なら、前回の失敗の判定（`detail`）を返す（#1693 F3・決定 5）。使い回せなければ `None`。

    HEAD が前回落ちた地点と同じで、その後に取り込みも取り消しも無いときだけ使い回す。取り込めば HEAD が進み、
    取り消しはコミットを積むため、どちらも HEAD が変わる。取り込みの数（`fix_commits`）も合わせて見る。
    """
    last = gate.get("last_failing") or {}
    if not last.get("head") or not gate.get("lint_rejections"):
        return None
    head = head_sha(work_dir(state))
    if head != last["head"] or _fix_marker(gate) != safe_int(last.get("fix_commits")):
        return None
    detail = f"{last.get('detail')}（HEAD が前回の判定と同じため使い回した）"
    _record_gate_check(gate, "", False, detail, 0.0, mode="reused")
    return detail


def _reusable_whole_test(state: dict[str, Any]) -> bool:
    """検証の中の全体テストを使い回せるか（決定 16）。

    CI で見るなら使い回さない。検証の中で走って通り、取り消しが無く（`whole_test.reverted` が偽）、
    その後に HEAD が 1 つも進んでいないときだけ真。生成物の同期のコミットが積まれていれば HEAD が進んでいるので走らせる。
    """
    if ci_mode(state):
        return False
    record = state.get("whole_test") or {}
    if not (record.get("ran") and record.get("status") == "pass") or record.get("reverted"):
        return False
    head = head_sha(work_dir(state))
    return bool(head) and head == record.get("head")


_TRIAGE_KEYS = (
    "failed_tests",
    "flaky",
    "preexisting",
    "caused",
    "caused_output",
    "fallback_reason",
    "rerun_command",
    "rerun_commands",
    "comparison_tree",
    "comparison_fallback",
)


def _run_and_record_gate_check(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, str, float]:
    """最終ゲートのチェックを 1 回走らせ、`checks` へ記録して結果を返す。落ちたら見分け、変更起因が無ければ通す。

    3 つ目はチェックそのものの実行秒数（見分けの時間を含まない）。静的解析の上限の起点に使う。
    """
    # **排他である。** CI で見るなら手元のテストを実行せず継続的統合の結論だけで判定し、無ければ
    # 手元のテストだけで判定する。「どちらか一方が通れば通過」とはしない。
    ci = ci_mode(state)
    gate["mode"] = "ci" if ci else "test"
    started = time.monotonic()
    passed, detail, verdict = ci_gate(state) if ci else _local_gate(path, state)
    seconds = round(time.monotonic() - started, 1)
    if not passed and verdict is not None:
        # 落ちたテストを見分ける。変更起因が無ければ通す（I5・決定 11）。
        classified = triage.classify(state, verdict.get("timed_out", False), verdict.get("ci_xmls"))
        gate["triage"] = {k: classified.get(k) for k in _TRIAGE_KEYS}
        gate["material"] = _material(state, classified, verdict)
        flaky.request(state, classified.get("flaky"), verdict.get("log"))
        if classified.get("fallback_reason"):
            detail += f" / 見分けを全体の走らせ直しに落とした（{classified['fallback_reason']}）"
        else:
            detail += (
                f" / フレーキー {len(classified['flaky'])}・既存失敗 {len(classified['preexisting'])}・変更起因 {len(classified['caused'])}"
            )
            if not classified["caused"]:
                passed = True
                detail += "（変更起因の失敗は無い）"
    _record_gate_check(gate, _gate_command(state), passed, detail, seconds)
    if not ci:
        # 履歴の `whole_test.final`（AC17）。修正の後に走らせ直したときは足し込む。
        gate["whole_test_seconds"] = round(float(gate.get("whole_test_seconds") or 0.0) + seconds, 1)
    statefile.save(path, state)
    return passed, detail, seconds


def _material(state: dict[str, Any], classified: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """落ちたテストの材料（決定 12）。JUnit を読めないときもログか CI の run の置き場と走らせ直すコマンドを渡す（受け入れ条件 13）。"""
    log = verdict.get("log")
    text = ""
    if log:
        try:
            text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    junit_ids = classified.get("caused") or classified.get("failed_tests") or []
    return failure_material.build(
        junit_ids,
        text,
        str(verdict.get("log_place") or log or ""),
        classified.get("rerun_commands") or [],
        timeline.strategy_of(state).whole_commands(ts.TEST),
    )


def _gate_command(state: dict[str, Any]) -> str:
    """記録に残す最終ゲートの相手（チェックの名前か全体テストのコマンド）。"""
    if ci_mode(state):
        return " / ".join(ci_checks(state))
    return " && ".join(timeline.strategy_of(state).whole_commands(ts.TEST))


def _emit_cross_review(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], message: str) -> None:
    """Step 7 を `cross-review` へ委譲する結末。"""
    # **チェックが通ったことを残す。** 履歴へ追記するかは、この目印と `cross-review` の
    # 最終ステータスの両方で決まる（`finalize`）。
    gate["checked_mode"] = gate.get("mode")
    gate["mode"] = "cross-review"
    gate["status"] = "passed"
    statefile.save(path, state)
    info(message)
    statefile.emit(FINAL_GATE="cross-review")


def _record_gate_check(gate: dict[str, Any], command: str, passed: bool, detail: str, seconds: float, mode: Optional[str] = None) -> None:
    """最終ゲートのチェック 1 件を `checks` へ追記する。"""
    gate.setdefault("checks", []).append(
        {
            "at": statefile.now(),
            "mode": mode or gate["mode"],
            "command": command,
            "status": "pass" if passed else "fail",
            "detail": detail,
            "seconds": seconds,
        }
    )


def _gate_passed(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str) -> None:
    gate["status"] = "passed"
    statefile.save(path, state)
    info(f"✅ 最終ゲートを通過しました（{detail}）")
    statefile.emit(FINAL_GATE="passed")


def _gate_failing(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    detail: str,
) -> None:
    gate["fix_rounds"] = safe_int(gate.get("fix_rounds")) + 1
    gate["status"] = "failing"
    # **修正の起点と担当をここで記録する。** 記録しないと `merge-final-fix` が範囲を
    # 確定できず、修正コミットを 1 件も取り込めない。適用ラウンドの記録にある
    # `fix_base_sha` を流用することもできない。あれは最後の群の検証が落ちた地点で
    # あり、そこから HEAD までには**検証を通った正常なコミット**が並ぶ。範囲に含めると
    # 未申告として扱われ、その全部が取り消される。
    gate["fix_base_sha"] = head_sha(work_dir(state))
    # 修正の依頼に載せる内容（落ちた検査と直前の差し戻し）。`launch-cli.sh` が `RF_FINAL_FIX_REQUEST` で渡す（F2）
    request = prepush_lint.fix_request(gate)
    if gate.get("material"):
        # 落ちたテストの材料（ID・ログの置き場・走らせ直すコマンド）。JUnit を読めないときも渡す（受け入れ条件 13）
        request = (request + "\n\n" if request else "") + "落ちたテストの材料:\n" + failure_material.as_markdown(gate["material"])
    if request:
        gate["fix_request"] = request
    else:
        gate.pop("fix_request", None)
    impl = _final_fix_impl(state, gate)
    statefile.save(path, state)
    info(f"❌ 最終ゲートが落ちました（{detail}）。修正ラウンド {gate['fix_rounds']} — 修正担当は {impl} です")
    statefile.emit(FINAL_GATE="failing", FINAL_FIX_IMPL=impl, FINAL_FIX_ROUND=gate["fix_rounds"])
    sys.exit(2)


def _final_fix_impl(state: dict[str, Any], gate: dict[str, Any]) -> str:
    """最終ゲートの修正担当。**実装担当が担う**（#933 の決定 1。輪番は無い）。"""
    impl = str(gate.get("impl") or state.get("implementer") or "")
    gate["impl"] = impl
    return impl


def _local_gate(path: pathlib.Path, state: dict[str, Any]) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """テストの種別の全体テストを手元で実行する（I4: `local-scoped-ci-whole` では呼ばない）。3 つ目は落ちたときの見分けの材料。

    起動の失敗なら止める（#1483 E7）。静的解析は `_lint_gate` が走らせる。
    """
    commands = timeline.strategy_of(state).whole_commands(ts.TEST)
    if not commands:
        return True, "テストの種別の全体テストが無い", None
    work = work_dir(state)
    timeout = timeline.state_whole_timeout(state)
    triage.clear_junit(state)
    started = time.monotonic()
    log = pathlib.Path(state["tmp_dir"]) / "final-gate-whole-test.log" if state.get("tmp_dir") else None
    for command in commands:
        # 上限は suite 群全体で 1 つ（test-run.py の whole と同じ）
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, command=command: run_with_timeout(command, work, left, output=log)
        )
        result = ts.outcome(code, timed_out)
        if result.launch_failed:
            launch.stop(path, state, "gate", command, result)
        if timed_out:
            return False, f"{command} が {timeout} 秒で終わりませんでした", {"timed_out": True, "log": str(log)}
        if code != 0:
            return False, f"{command} / 終了コード {code}", {"timed_out": False, "log": str(log)}
    return True, f"{' && '.join(commands)} / 終了コード 0", None
