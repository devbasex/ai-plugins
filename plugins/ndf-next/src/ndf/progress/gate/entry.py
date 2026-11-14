"""副命令 `ndf answer`（`main`）と `ndf progress`（`progress_main`）のエントリポイント（`ndf.progress.gate` の部品）。

ndf answer <進行表> --gate <問いの id> --result <答え> --reason <理由> --by <答えた者> --key <冪等の鍵>
           [--notes <指摘ファイル>] [--head <見た head の SHA>] [--material-sha256 <見た承認資料の sha256>]
           [--drop <課題の番号>...]
ndf answer ask [--gate design|release]   # プランの中の承認ゲート（雛形の {{gate}}）。答えを待つ結果 JSON を返す
ndf answer list <進行表>                 # 書いた答え（選択肢・指摘・理由・答えた者・時刻）を古い順に返す
ndf answer head <進行表> [--gate release] # 承認で見た PR の head（マージの側が --expect-head へ渡して照らす。#2147）
ndf answer work --state-dir <置き場> [--cwd <作業場所>] <作業名>
                                         # 雛形の {{worker}} の既定。作業名を ndf worker run の必須の引数へ合わせて打つ
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.progress import command, invoke, model, run, store
from ndf.progress.gate.answering import Answer, Observe, answer, git_docs_only, github_observe, lane_env, recheck
from ndf.progress.gate.asking import RELEASE_GATE, TOOL, Target, gate_result, question, write_material
from ndf.progress.gate.records import answers, approved_head
from ndf.progress.gate.release import request_release, take_reply
from ndf.progress.plan import external


def present(
    st: store.Store, table_id: str, cwd: Path, out: dict[str, Any], observe: Observe, env: Optional[Mapping[str, str]] = None
) -> dict[str, Any]:
    """`ndf progress` の結果（next・wait・work）が承認ゲート（`ndf.listen`）の待ちなら、問い・承認資料・答えごとの行き先の結果に
    差し替える。リリース承認ゲートならレーンのリリースの依頼も出す（`request_release`）。"""
    v = run.read(st, table_id)
    m = run.decide(v)
    if out["status"] != result.GATE or m.action != run.Action.GATE or m.step is None or not invoke.is_listen(m.step):
        return out
    try:
        repo: Optional[str] = Git(cwd).remote_repo()
    except GitError:
        repo = None
    asked = question(v, m, repo=repo)
    path = write_material(st.root, v, asked)
    q = replace(asked, target=observe(asked))
    metrics = dict(out["metrics"]) if isinstance(out.get("metrics"), dict) else {}
    warned = list(out["warnings"]) if "warnings" in out else []
    try:
        sent = request_release(st, v, q, env if env is not None else os.environ)
    except (model.Refused, external.ExchangeError, OSError) as exc:
        sent, warned = None, [*warned, f"リリースの依頼を出せなかった（人が ndf answer で答える）: {exc}"]
    if sent is not None:
        metrics["release_request"] = sent
    return command.with_warnings(gate_result(q, metrics, path), before=warned)


# --- 副命令 ndf answer ----------------------------------------------------------------------------


def ask(gate: Optional[str]) -> dict[str, Any]:
    """プランの中の承認ゲート（雛形の `{{gate}}`）: 答えを待つ結果 JSON。答えはプランの行き先が受ける。

    答えの語（routes の鍵）だけを返し、値は null（このステップは何も打たない）にする。このステップは進行表と工程を
    知らず、答えを書くコマンドはプランの実行（`ndf plan next ... --answer`）と工程の流れ（`ndf progress answer
    <進行表> <工程>`）がそれぞれ自分の routes で組むためである。"""
    if gate is not None and gate not in model.table()["gates"]:
        raise model.Refused(f"承認ゲートの区分 {gate} は無い（{', '.join(model.table()['gates'])}）")
    label = model.gate_label(gate) if gate else "プランの中の承認ゲート"
    out: dict[str, Any] = {a: None for a in (run.APPROVED, run.REJECTED)}
    return result.result(TOOL, result.GATE, f"{label}の答えを待つ", metrics={"gate": {"kind": gate, "label": label}}, routes=out)


# 雛形の `{{worker}}` の既定が渡す作業名（雛形の `["{{worker}}", "<作業名>"]` の末尾）
WORK_KINDS = ("impl", "fix", "ci-fix", "fix-base", "fix-push", "design", "plan-to-spec")


def work_argv(kind: str, state_dir: Path, cwd: Path) -> list[str]:
    """作業名を `ndf worker run` の必須の引数へ合わせる。プロンプトは `<置き場>/<作業名>-prompt.md`（conductor が書く）、
    結果は `<置き場>/<作業名>-result.json` に置く。"""
    if kind not in WORK_KINDS:
        raise model.Refused(f"作業名 {kind} は雛形の作業名（{', '.join(WORK_KINDS)}）のどれでもない")
    files = ("--prompt-file", str(state_dir / f"{kind}-prompt.md"), "--result-file", str(state_dir / f"{kind}-result.json"))
    return ["run", "--kind", kind, *files, "--cwd", str(cwd), "--state-dir", str(state_dir)]


def work_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} work")
    ap.add_argument("--state-dir", required=True, help="プランの状態の置き場（雛形の {state_dir}）")
    ap.add_argument("--cwd", default=".", help="worker が動く作業場所")
    ap.add_argument("kind", help="作業名（" + "・".join(WORK_KINDS) + "）")
    return ap


def _cmd_work(argv: list[str], exec_: Callable[[str, list[str]], Any] = os.execv) -> int:
    """`ndf worker run` を同じプロセスの置き換えで打つ（worker の層は読まず、副命令の契約だけを使う。IG1）。"""
    a = work_parser().parse_args(argv)
    try:
        args = work_argv(a.kind, Path(a.state_dir), Path(a.cwd))
    except model.Refused as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    argv = invoke.ndf_command("worker", *args)  # -P: 作業場所の ndf.py・ndf/ を配布したパッケージより先に読まない
    exec_(argv[0], argv)
    return 0


def _cmd_give(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    seen = Target(a.head, a.material_sha256)
    given = Answer(a.gate, a.result, a.reason, a.by, a.key, Path(a.notes) if a.notes else None, seen, tuple(a.drop))
    return answer(st, a.table_id, given, github_observe(cwd, st.root))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("table_id", help="答える進行表の ID")
    ap.add_argument("--gate", required=True, help="問いの id（承認ゲートの結果の metrics.gate.id）")
    ap.add_argument("--result", required=True, help="答えの語（承認ゲートの結果の routes のキー）")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--by", required=True, help="答えた者（human・conductor・organizer か service:<主体>）")
    ap.add_argument("--key", required=True, help="冪等の鍵（同じ鍵の答え直しは書かない）")
    ap.add_argument("--notes", help="差し戻し（revise）の指摘ファイル")
    ap.add_argument("--head", help="見た PR の head の SHA")
    ap.add_argument("--material-sha256", help="見た承認資料の sha256")
    ap.add_argument("--drop", type=int, nargs="*", default=[], help="設計承認ゲートで外す課題の番号")
    return ap


def _cmd_head(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    head = approved_head(run.read(st, a.table_id), a.gate, st.root)
    if head is None:
        raise model.Refused(f"進行表 {a.table_id} に、{model.gate_label(a.gate)}で head を見て承認した答えが無い")
    summary = f"{model.gate_label(a.gate)}で承認した head は {head}"
    return result.result(TOOL, result.OK, summary, metrics={"table_id": a.table_id, "gate": a.gate, "head": head})


def head_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} head")
    ap.add_argument("table_id")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]), default=RELEASE_GATE)
    return ap


def _cmd_list(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    got = answers(run.read(st, a.table_id))
    return result.result(TOOL, result.OK, f"進行表 {a.table_id} の答え {len(got)} 件", items=got, metrics={"table_id": a.table_id})


def list_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} list")
    ap.add_argument("table_id")
    return ap


def ask_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} ask")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]))
    return ap


def _progress(a: argparse.Namespace, cwd: Path, st: store.Store, observe: Optional[Observe] = None) -> dict[str, Any]:
    """`ndf progress` の工程を流す副命令。レーンの行を終える `Env` で流し、答えを読む前に対象を見直し、承認ゲートの
    待ちは問いと答えごとの行き先の結果にする。"""
    seen = observe if observe is not None else github_observe(cwd, st.root)
    took = _took(st, a.table_id, seen)
    why = recheck(st, a.table_id, seen, git_docs_only(cwd))
    out = run.COMMANDS[a.action](run.with_env(a, lane_env(run.Env(facts=command.facts_of))), cwd, st)
    return command.with_warnings(present(st, a.table_id, cwd, out, seen), before=[w for w in (took, why) if w])


def _took(st: store.Store, table_id: str, observe: Observe) -> Optional[str]:
    """リリースの依頼への答えを承認ゲートの答えとして書く。書けなければ理由を警告に返す（待ちは残る）。"""
    try:
        got = take_reply(st, table_id, os.environ, observe)
    except (model.Refused, external.ExchangeError) as exc:
        return f"リリースの依頼への答えを承認ゲートの答えにできなかった: {exc}"
    return got["summary"] if got is not None else None


Dispatch = Callable[[argparse.Namespace, Path], dict[str, Any]]
# `ndf answer` の副命令の名前 → （引数の解析器を作る関数, 受ける関数）。`work` は exec するので表の外で受ける
ANSWER_VERBS: dict[str, tuple[Callable[[], argparse.ArgumentParser], Dispatch]] = {
    "ask": (ask_parser, lambda a, _cwd: ask(a.gate)),
    "head": (head_parser, lambda a, cwd: command.with_store(cwd, _cmd_head, a)),
    "list": (list_parser, lambda a, cwd: command.with_store(cwd, _cmd_list, a)),
}


def main(argv: list[str]) -> int:
    """副命令 `ndf answer`（副命令の表の `answer` の行がこのパッケージを指す）。

    `ask` はプランの中の承認ゲート、`list` は書いた答え、`head` は承認で見た head、`work` は雛形の `{{worker}}` の既定、
    進行表の ID で始まれば承認ゲートへの答え。`ndf progress` は `progress_main` が受ける（`work` の名前が両方にあるため、
    入口の副命令で分ける）
    """
    if argv and argv[0] in ANSWER_VERBS:
        make, dispatch = ANSWER_VERBS[argv[0]]
        return command.run_main(make(), dispatch, argv[1:])
    if argv and argv[0] == "work":
        return _cmd_work(argv[1:])
    return command.run_main(parser(), lambda a, cwd: command.with_store(cwd, _cmd_give, a), argv)


def progress_main(argv: list[str]) -> int:
    """副命令 `ndf progress`（副命令の表の `progress` の行が `ndf.progress.gate.progress_cli` を指す）。

    工程を流す副命令（next・wait・answer・resync・work・show）は、スプリントの完了と同じトランザクションでレーンの行を
    終える `Env`（`lane_env`。決定 17）で流す。ほか（new・disable など）は `command` へ渡す。P1b（`run`）はレーンを読まない
    """
    if argv and argv[0] in run.COMMANDS:
        return command.run_main(run.parser(), lambda a, cwd: command.with_store(cwd, _progress, a), argv)
    return command.main(argv)
