"""サブコマンド `ndf answer`（`main`）と `ndf progress`（`progress_main`）のエントリポイント（`ndf.progress.gate` の部品）。

ndf answer <ステートマシン> --gate <質問の id> --result <回答> --reason <理由> --by <回答した者> --key <冪等のキー>
           [--notes <指摘ファイル>] [--head <見た head の SHA>] [--material-sha256 <見た承認資料の sha256>]
           [--drop <課題の番号>...]
ndf answer ask [--gate design|release]   # プランの中の承認ゲート（テンプレートの {{gate}}）。回答を待つ結果 JSON を返す
ndf answer list <ステートマシン>                 # 書いた回答（選択肢・指摘・理由・回答した者・時刻）を古い順に返す
ndf answer head <ステートマシン> [--gate release] # 承認で見た PR の head（マージの側が --expect-head へ渡して照らす。#2147）
ndf answer work --state-dir <保存先> [--cwd <作業場所>] <作業名>
                                         # 作業名を ndf worker run の必須の引数へ合わせて打つ（プロンプトは呼ぶ側が先に書く）
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
from ndf.progress.worker_kinds import work_kinds


def present(
    st: store.Store, machine_id: str, cwd: Path, out: dict[str, Any], observe: Observe, env: Optional[Mapping[str, str]] = None
) -> dict[str, Any]:
    """`ndf progress` の結果（next・wait・work）が承認ゲート（`ndf.listen`）の待ちなら、質問・承認資料・回答ごとのルートの結果に
    差し替える。リリース承認ゲートならレーンのリリースのリクエストも出す（`request_release`）。"""
    v = run.read(st, machine_id)
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
        sent, warned = None, [*warned, f"リリースのリクエストを出せなかった（人が ndf answer で回答する）: {exc}"]
    if sent is not None:
        metrics["release_request"] = sent
    return command.with_warnings(gate_result(q, metrics, path), before=warned)


# --- サブコマンド ndf answer ----------------------------------------------------------------------------


def ask(gate: Optional[str]) -> dict[str, Any]:
    """プランの中の承認ゲート（テンプレートの `{{gate}}`）: 回答を待つ結果 JSON。回答はプランのルートが受ける。

    回答の語（routes のキー）だけを返し、値は null（このステップは何も打たない）にする。このステップはステートマシンと工程を
    知らず、回答を書くコマンドはプランの実行（`ndf plan next ... --answer`）と工程の流れ（`ndf progress answer
    <ステートマシン> <工程>`）がそれぞれ自分の routes で組むためである。"""
    if gate is not None and gate not in model.table()["gates"]:
        raise model.Refused(f"承認ゲートの区分 {gate} は無い（{', '.join(model.table()['gates'])}）")
    label = model.gate_label(gate) if gate else "プランの中の承認ゲート"
    out: dict[str, Any] = {a: None for a in (run.APPROVED, run.REJECTED)}
    return result.result(TOOL, result.GATE, f"{label}の回答を待つ", metrics={"gate": {"kind": gate, "label": label}}, routes=out)


def work_argv(kind: str, state_dir: Path, cwd: Path) -> list[str]:
    """作業名を `ndf worker run` の必須の引数へ合わせる。プロンプトは `<保存先>/<作業名>-prompt.md`（呼ぶ側が先に書く）、
    結果は `<保存先>/<作業名>-result.json` に置く。"""
    # 受ける作業名は作業の定義（`contracts/worker/kinds.json`）のキーだけ（テンプレートの `["{{worker}}", "<作業名>"]` の末尾）
    if kind not in work_kinds():
        raise model.Refused(f"作業名 {kind} はテンプレートの作業名（{', '.join(work_kinds())}）のどれでもない")
    files = ("--prompt-file", str(state_dir / f"{kind}-prompt.md"), "--result-file", str(state_dir / f"{kind}-result.json"))
    return ["run", "--kind", kind, *files, "--cwd", str(cwd), "--state-dir", str(state_dir)]


def work_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} work")
    ap.add_argument("--state-dir", required=True, help="プランの状態の保存先（テンプレートの {state_dir}）")
    ap.add_argument("--cwd", default=".", help="worker が動く作業場所")
    ap.add_argument("kind", help="作業名（" + "・".join(work_kinds()) + "）")
    return ap


def _cmd_work(argv: list[str], exec_: Callable[[str, list[str]], Any] = os.execv) -> int:
    """`ndf worker run` を同じプロセスの置き換えで打つ（worker の層は読まず、サブコマンドのコントラクトだけを使う。IG1）。"""
    a = work_parser().parse_args(argv)
    try:
        args = work_argv(a.kind, Path(a.state_dir), Path(a.cwd))
    except model.Refused as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    # -I: 作業場所と呼び出し元の PYTHONPATH の ndf.py・ndf/ を NDF の src より先に読まない。置き換えた先も、呼び出し元が読む
    # 今の標準出力・標準エラーの文字コードで書く（プランのステップはロケールで読む。#4029）
    argv = invoke.ndf_command("worker", *args, keep_streams=True)
    exec_(argv[0], argv)
    return 0


def _cmd_give(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    seen = Target(a.head, a.material_sha256)
    given = Answer(a.gate, a.result, a.reason, a.by, a.key, Path(a.notes) if a.notes else None, seen, tuple(a.drop))
    return answer(st, a.machine_id, given, github_observe(cwd, st.root))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("machine_id", help="回答するステートマシンの ID")
    ap.add_argument("--gate", required=True, help="質問の id（承認ゲートの結果の metrics.gate.id）")
    ap.add_argument("--result", required=True, help="回答の語（承認ゲートの結果の routes のキー）")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--by", required=True, help="回答した者（human・conductor・organizer か service:<主体>）")
    ap.add_argument("--key", required=True, help="冪等のキー（同じキーの回答し直しは書かない）")
    ap.add_argument("--notes", help="変更要求（request_changes）の指摘ファイル")
    ap.add_argument("--head", help="見た PR の head の SHA")
    ap.add_argument("--material-sha256", help="見た承認資料の sha256")
    ap.add_argument("--drop", type=int, nargs="*", default=[], help="設計承認ゲートで外す課題の番号")
    return ap


def _cmd_head(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    head = approved_head(run.read(st, a.machine_id), a.gate, st.root)
    if head is None:
        raise model.Refused(f"ステートマシン {a.machine_id} に、{model.gate_label(a.gate)}で head を見て承認した回答が無い")
    summary = f"{model.gate_label(a.gate)}で承認済みのコミットは {head}"
    return result.result(TOOL, result.OK, summary, metrics={"machine_id": a.machine_id, "gate": a.gate, "head": head})


def head_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} head")
    ap.add_argument("machine_id")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]), default=RELEASE_GATE)
    return ap


def _cmd_list(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    got = answers(run.read(st, a.machine_id))
    return result.result(
        TOOL, result.OK, f"ステートマシン {a.machine_id} の回答 {len(got)} 件", items=got, metrics={"machine_id": a.machine_id}
    )


def list_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} list")
    ap.add_argument("machine_id")
    return ap


def ask_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} ask")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]))
    return ap


def _progress(a: argparse.Namespace, cwd: Path, st: store.Store, observe: Optional[Observe] = None) -> dict[str, Any]:
    """`ndf progress` の工程を流すサブコマンド。レーンの行を終える `Env` で流し、回答を読む前に対象を見直し、承認ゲートの
    待ちは質問と回答ごとのルートの結果にする。"""
    seen = observe if observe is not None else github_observe(cwd, st.root)
    took = _took(st, a.machine_id, seen)
    why = recheck(st, a.machine_id, seen, git_docs_only(cwd))
    out = run.COMMANDS[a.action](run.with_env(a, lane_env(run.Env(facts=command.facts_of))), cwd, st)
    return command.with_warnings(present(st, a.machine_id, cwd, out, seen), before=[w for w in (took, why) if w])


def _took(st: store.Store, machine_id: str, observe: Observe) -> Optional[str]:
    """リリースのリクエストへの回答を承認ゲートの回答として書く。書けなければ理由を警告に返す（待ちは残る）。"""
    try:
        got = take_reply(st, machine_id, os.environ, observe)
    except (model.Refused, external.ExchangeError) as exc:
        return f"リリースのリクエストへの回答を承認ゲートの回答にできなかった: {exc}"
    return got["summary"] if got is not None else None


Dispatch = Callable[[argparse.Namespace, Path], dict[str, Any]]
# `ndf answer` のサブコマンドの名前 → （引数の解析器を作る関数, 受ける関数）。`work` は exec するので表の外で受ける
ANSWER_VERBS: dict[str, tuple[Callable[[], argparse.ArgumentParser], Dispatch]] = {
    "ask": (ask_parser, lambda a, _cwd: ask(a.gate)),
    "head": (head_parser, lambda a, cwd: command.with_store(cwd, _cmd_head, a)),
    "list": (list_parser, lambda a, cwd: command.with_store(cwd, _cmd_list, a)),
}


def main(argv: list[str]) -> int:
    """サブコマンド `ndf answer`（サブコマンドの表の `answer` の行がこのパッケージを指す）。

    `ask` はプランの中の承認ゲート、`list` は書いた回答、`head` は承認で見た head、`work` は作業名から `ndf worker run` を打つ、
    ステートマシンの ID で始まれば承認ゲートへの回答。`ndf progress` は `progress_main` が受ける（`work` の名前が両方にあるため、
    エントリポイントのサブコマンドで分ける）
    """
    if argv and argv[0] in ANSWER_VERBS:
        make, dispatch = ANSWER_VERBS[argv[0]]
        return command.run_main(make(), dispatch, argv[1:])
    if argv and argv[0] == "work":
        return _cmd_work(argv[1:])
    return command.run_main(parser(), lambda a, cwd: command.with_store(cwd, _cmd_give, a), argv)


def progress_main(argv: list[str]) -> int:
    """サブコマンド `ndf progress`（サブコマンドの表の `progress` の行が `ndf.progress.gate.progress_cli` を指す）。

    工程を流すサブコマンド（next・wait・answer・resync・work・show）は、スプリントの完了と同じトランザクションでレーンの行を
    終える `Env`（`lane_env`。決定 17）で流す。ほか（new・disable など）は `command` へ渡す。P1b（`run`）はレーンを読まない
    """
    if argv and argv[0] in run.COMMANDS:
        return command.run_main(run.parser(), lambda a, cwd: command.with_store(cwd, _progress, a), argv)
    return command.main(argv)
