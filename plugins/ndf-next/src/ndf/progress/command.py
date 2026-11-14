"""副命令 `ndf progress`: スプリントの進行表を作り、読み、conductor の操作で状態を変える。

ndf progress new <スプリント名> --mode <モード> --issues <番号>... [--pace <進め方>] [--disable <工程>=<理由>]...
                 [--requirements <要求と受け入れ条件の出力>] [--parent <親の進行表の ID>]
ndf progress show <進行表の ID>
ndf progress disable|enable|retry <進行表の ID> <工程> --reason <理由>
ndf progress finish <進行表の ID> <工程> --output <出力のファイル> --reason <理由>
ndf progress cancel <進行表の ID> --reason <理由>
ndf progress restore --from <控え>

結果は結果 JSON の 1 行で返す。この副命令を打つのはレーンの conductor で、書く事象の書き手は conductor になる。
工程を流す `next` と、承認ゲートの答えを書く `answer` は P1b（#3092）が足す。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters.git import Git, GitError
from ndf.contracts import causes, facts, result
from ndf.core import durable
from ndf.progress import definition, model, stages, store

# 結果 JSON の tool の名前は副命令の名前（パッケージの名前）と同じ
TOOL = __package__.rpartition(".")[2]
ACTOR = "conductor"
# 番号のぶつかり（同時の追記）で読み直して書き直す回数
APPEND_ATTEMPTS = 3
PROJECT_FILE = identity.FIXED_WORDS["declaration_dir"] + "/project.json"
# 副命令 disable・enable が書く事象の種類
TOGGLE_KINDS = {"disable": "disabled", "enable": "enabled"}
# 進行表の作成の時点で済んでいる工程（要求と受け入れ条件）の終了の理由
DONE_AT_CREATION = "進行表の作成の時点で済んでいる"
REQUIREMENTS_STAGE = "要求と受け入れ条件"  # `--requirements` が終了の印を付ける工程（工程表の行名）


# --- 置き場とリポジトリ ------------------------------------------------------------------


def _root(cwd: Path) -> Path:
    return durable.store_root(identity.load().name, cwd)


def _without_userinfo(url: str) -> str:
    """URL の userinfo（`user:token@`）を外す。トークンを DB へ残さず、トークンの入れ替えで識別を変えない。"""
    parts = urlsplit(url)
    if not parts.scheme or "@" not in parts.netloc:
        # scp の形（git@host:o/r）と userinfo の無い URL はそのまま
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def _repo(cwd: Path, root: Path) -> str:
    """リポジトリの識別。origin の URL（userinfo を外す）、無ければメインディレクトリの絶対パス（置き場の 2 つ上の親）。"""
    try:
        return _without_userinfo(Git(cwd).run("remote", "get-url", "origin").strip())
    except GitError:
        # origin の無いリポジトリはメインディレクトリで識別する
        return str(root.parents[2])


def _read_json(path: Path, failure: str = "を JSON として読めない") -> Any:
    """`path` の JSON。読めなければ `"{path} {failure}: 理由"` の `ContractError`。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise contracts.ContractError(f"{path} {failure}: {exc}") from exc


def _declared(cwd: Path) -> dict[str, Any]:
    """`.ndf/project.json` の `workflow.stages`（工程の名前 → 有効と理由）。ファイルか項目が無ければ空。"""
    top = Path(Git(cwd).run("rev-parse", "--show-toplevel").strip())
    path = top / PROJECT_FILE
    if not path.exists():
        return {}
    raw = _read_json(path, "を読めない")
    workflow = raw.get("workflow") if isinstance(raw, dict) else None
    declared = workflow.get("stages") if isinstance(workflow, dict) else None
    if not isinstance(declared, dict):
        return {}
    errs = [f"workflow.stages.{name}: {e}" for name, value in declared.items() for e in _declaration_errors(value)]
    if errs:
        raise contracts.ContractError(f"{path} の宣言が形に合わない: {'; '.join(errs)}")
    return declared


def _declaration_errors(value: Any) -> list[str]:
    """工程の宣言 1 つの形 `{"enabled": 真偽, "reason": 理由（無効のとき必須）}` を破る理由。"""
    if not isinstance(value, dict):
        return ['{"enabled": 真偽, "reason": 理由} の形でない']
    if not isinstance(value.get("enabled"), bool):
        return ["enabled が真偽でない"]
    reason = value.get("reason")
    if not value["enabled"] and not (isinstance(reason, str) and reason):
        return ["無効にするときの reason が空か文字列でない"]
    return []


# --- 進行表の読み書き ---------------------------------------------------------------------


def _current(conn: Any, table_id: str) -> tuple[int, stages.Snapshot, list[model.Event]]:
    if store.read_table(conn, table_id) is None:
        raise model.Refused(f"進行表 {table_id} は無い")
    rev, raw = store.read_definition(conn, table_id)
    return rev, stages.snapshot_from(raw), store.read_events(conn, table_id)


def _append(
    st: store.Store, table_id: str, build: Callable[[int, stages.Snapshot, list[model.Event]], list[model.EventDraft]]
) -> list[int]:
    """今の改訂と事象を読み、`build` が作った下書きを追記する。番号がぶつかれば読み直して作り直す。"""
    for attempt in range(1, APPEND_ATTEMPTS + 1):
        try:
            with store.transaction(st) as conn:
                rev, snap, events = _current(conn, table_id)
                drafts = build(rev, snap, events)
                return store.append_events(conn, table_id, drafts, events[-1].seq if events else 0)
        except store.Conflict:
            if attempt == APPEND_ATTEMPTS:
                raise
    raise AssertionError("到達しない")


def _rows(snap: stages.Snapshot, events: list[model.Event], rev: int) -> list[dict[str, Any]]:
    folded = model.fold(events, rev)
    out = []
    for s in snap.stages:
        row = folded.get(s.name)
        state = row.state if row else None
        out.append(
            {
                "stage": s.name,
                "phase": s.phase,
                "state": state,
                "label": model.label(state),
                "at": row.at.isoformat() if row and row.at else None,
                "reason": row.reason if row else None,
                "gates": [model.gate_label(st.gate) for st in s.steps if st.gate],
            }
        )
    return out


# --- 副命令 ------------------------------------------------------------------------------


def _new_drafts(snap: stages.Snapshot, enabled: dict[str, stages.Enablement], finished: list[tuple[str, str]]) -> list[model.EventDraft]:
    drafts = [model.EventDraft("created", ACTOR, s.name, rev=1) for s in snap.stages]
    for name, en in enabled.items():
        if not en.enabled:
            drafts.append(model.EventDraft("disabled", ACTOR, name, reason=en.reason, payload={"source": en.source}, rev=1))
    for name, output in finished:
        drafts.append(model.EventDraft("manual_finished", ACTOR, name, reason=DONE_AT_CREATION, payload={"output": output}, rev=1))
    return drafts


def _requirements(snap: stages.Snapshot, path: str | None) -> list[tuple[str, str]]:
    """`--requirements` の出力を要求と受け入れ条件の工程の出力の規約で確かめ、終了の印を付ける工程の一覧にする。

    その工程を持たないモードでは別の工程へ印を付けず、引数の誤りとして断る。"""
    if path is None:
        return []
    found = [s for s in snap.stages if s.name == REQUIREMENTS_STAGE]
    if not found:
        raise definition.WorkflowDefError([f"--requirements: モード {snap.mode} に要求と受け入れ条件の工程が無い"])
    stage = found[0]
    errs = definition.validate_stage_output(stage, _read_json(Path(path)))
    if errs:
        raise definition.WorkflowDefError([f"{stage.name} の出力が規約に合わない: {e}" for e in errs])
    return [(stage.name, path)]


def _prepare_new(a: argparse.Namespace, cwd: Path, snap: stages.Snapshot) -> tuple[dict[str, Any], list[model.EventDraft]]:
    """引数の --disable と文脈を確かめ、保存する定義の改訂の文書と最初の事象の下書きを作る。"""
    disables = dict(item.split("=", 1) for item in a.disable)
    unknown = stages.unknown_stages(snap, disables)
    if unknown:
        raise definition.WorkflowDefError([f"--disable の工程がモード {a.mode} に無い: {', '.join(unknown)}"])
    context = {"issues": a.issues, "mode": a.mode, "pace": a.pace}
    errs = contracts.schema_errors(context, definition.CONTEXT)
    if errs:
        raise definition.WorkflowDefError([f"スプリントの文脈: {e}" for e in errs])
    enabled = stages.resolve_enabled(snap, None, _declared(cwd), disables)
    doc = {**stages.snapshot_dict(snap), "context": context, "enablement": {k: vars(v) for k, v in enabled.items()}}
    return doc, _new_drafts(snap, enabled, _requirements(snap, a.requirements))


def _cmd_new(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    snap = definition.load_definition(a.mode)
    doc, drafts = _prepare_new(a, cwd, snap)
    with store.transaction(st) as conn:
        tid = store.create_table(conn, ("sprint", _repo(cwd, st.root), a.name), a.parent, doc, drafts)
        rev, _, events = _current(conn, tid)
    rows = _rows(snap, events, rev)
    off = [r["stage"] for r in rows if r["state"] == "disabled"]
    summary = f"進行表 {tid} を作った（{len(rows)} 行、無効 {len(off)} 行）"
    return result.result(TOOL, result.OK, summary, items=rows, metrics={"table_id": tid, "rows": len(rows), "disabled": off})


def _cmd_show(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    with store.transaction(st) as conn:
        rev, snap, events = _current(conn, a.table_id)
    rows = _rows(snap, events, rev)
    whole = model.table_state("sprint", events)
    metrics = {"table_id": a.table_id, "mode": snap.mode, "rev": rev, "state": whole, "history": [e.as_dict() for e in events]}
    return result.result(TOOL, result.OK, f"進行表 {a.table_id} は{model.label(whole)}（{len(rows)} 行）", items=rows, metrics=metrics)


def _changed(a: argparse.Namespace, st: store.Store, kind: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    def build(rev: int, snap: stages.Snapshot, _events: list[model.Event]) -> list[model.EventDraft]:
        stage = snap.stage(a.stage)
        if kind == "disabled" and stage.gates:
            raise model.Refused(stages.gate_refusal(stage))
        return [model.EventDraft(kind, ACTOR, a.stage, reason=a.reason, payload=payload, rev=rev)]

    seqs = _append(st, a.table_id, build)
    return result.result(
        TOOL, result.OK, f"進行表 {a.table_id} の {a.stage} に {kind} を書いた", metrics={"table_id": a.table_id, "seqs": seqs}
    )


def _cmd_toggle(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return _changed(a, st, TOGGLE_KINDS[a.action], None)


def _cmd_retry(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    with store.run_lock(st, a.table_id):
        return _changed(a, st, "retry", None)


def _cmd_finish(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    output = _read_json(Path(a.output))
    with store.transaction(st) as conn:
        _, snap, _ = _current(conn, a.table_id)
    stage = snap.stage(a.stage)
    errs = definition.validate_stage_output(stage, output)
    if errs:
        raise model.Refused(f"{a.stage} の出力が規約に合わない: {'; '.join(errs)}")
    dropped: list[str] = []

    def build(rev: int, _snap: stages.Snapshot, _events: list[model.Event]) -> list[model.EventDraft]:
        """終了の印と、工程が流れたときと同じ形で出力を文脈へ書く事象（後の工程の入力になる）。"""
        draft, dropped[:] = stages.outputs_draft(stage, rev, output["outputs"] if "outputs" in output else {}, ACTOR)
        done = model.EventDraft("manual_finished", ACTOR, a.stage, reason=a.reason, payload={"output": a.output}, rev=rev)
        return [done, *([draft] if draft is not None else [])]

    seqs = _append(st, a.table_id, build)
    st.warnings.extend(dropped)
    return result.result(
        TOOL, result.OK, f"進行表 {a.table_id} の {a.stage} に manual_finished を書いた", metrics={"table_id": a.table_id, "seqs": seqs}
    )


def _cmd_cancel(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    def build(rev: int, snap: stages.Snapshot, events: list[model.Event]) -> list[model.EventDraft]:
        folded = model.fold(events, rev)
        live = [s.name for s in snap.stages if s.name in folded and not model.is_terminal(folded[s.name].state)]
        drafts = [model.EventDraft("cancelled", ACTOR, name, reason=a.reason, rev=rev) for name in live]
        return [*drafts, model.EventDraft("cancelled", ACTOR, None, reason=a.reason, rev=rev)]

    with store.run_lock(st, a.table_id):
        seqs = _append(st, a.table_id, build)
    rows = len(seqs) - 1
    return result.result(
        TOOL, result.OK, f"進行表 {a.table_id} を中止した（中止にした行 {rows}）", metrics={"table_id": a.table_id, "cancelled_rows": rows}
    )


def _cmd_restore(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    root = _root(cwd)
    url = store.default_url(root)
    retired = store.restore(url, root, Path(a.source))
    items = [{"restored": str(store.sqlite_path(url)), "from": a.source, "retired": str(retired)}]
    return result.result(TOOL, result.OK, f"進行表の DB を控え {a.source} から戻した", items=items)


Command = Callable[[argparse.Namespace, Path, store.Store], dict[str, Any]]


def _answer_commands(handlers: tuple[Command, ...]) -> dict[str, Command]:
    """止まった後に conductor が選ぶ手の副命令。名前は答えの語彙（表 answers）の行で、`_cmd_<答え>` が受ける。"""
    out = {a: h for h in handlers for a in facts.ids(result.ANSWERS) if h.__name__ == f"_cmd_{a}"}
    if len(out) != len(handlers):
        raise LookupError(
            f"表 {result.ANSWERS} に無い答えの副命令: {sorted({h.__name__ for h in handlers} - {h.__name__ for h in out.values()})}"
        )
    return out


COMMANDS: dict[str, Command] = {
    "new": _cmd_new,
    "show": _cmd_show,
    "disable": _cmd_toggle,
    "enable": _cmd_toggle,
    **_answer_commands((_cmd_retry, _cmd_finish, _cmd_cancel)),
}


# --- エントリポイント --------------------------------------------------------------------------------


def _disable_item(value: str) -> str:
    name, sep, reason = value.partition("=")
    if not sep or not name.strip() or not reason.strip():
        raise argparse.ArgumentTypeError(f"--disable は <工程>=<理由> の形にする: {value}")
    return value


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    new = sub.add_parser("new", help="スプリントの進行表を作る")
    new.add_argument("name", help="スプリント名")
    new.add_argument("--mode", required=True)
    new.add_argument("--issues", type=int, nargs="+", required=True)
    new.add_argument("--pace", default="normal")
    new.add_argument("--disable", type=_disable_item, action="append", default=[], help="<工程>=<理由>")
    new.add_argument("--requirements", help="要求と受け入れ条件の出力（工程の出力の規約に合う JSON）")
    new.add_argument("--parent", help="親の進行表（レーンの進行表）の ID")
    sub.add_parser("show", help="進行表を表示する").add_argument("table_id")
    for name in ("disable", "enable", "retry", "finish"):
        p = sub.add_parser(name, help=f"工程に {name} を書く")
        p.add_argument("table_id")
        p.add_argument("stage", help="工程の名前（工程表の行名）")
        p.add_argument("--reason", required=True)
        if name == "finish":
            p.add_argument("--output", required=True, help="工程の出力の規約に合う JSON のファイル")
    cancel = sub.add_parser("cancel", help="進行表を中止する")
    cancel.add_argument("table_id")
    cancel.add_argument("--reason", required=True)
    sub.add_parser("restore", help="控えから進行表の DB を戻す").add_argument("--from", dest="source", required=True)
    return ap


def _dispatch(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    if a.action == "restore":
        return _cmd_restore(a, cwd)
    root = _root(cwd)
    url = store.default_url(root)
    store.sqlite_path(url)  # 受けない URL の形は、置き場を用意する前に断る
    ready = durable.prepare_store(root, datetime.now(UTC))
    with store.open_store(url, ready.root) as st:
        out = COMMANDS[a.action](a, cwd, st)
        if st.warnings:
            out["warnings"] = list(st.warnings)
        return out


# 止まった理由へ変換する例外の種類（main が受け、_stopped_from が理由へ変える）
STOPPABLE = (
    model.Refused,
    durable.StoreStopped,
    contracts.ContractError,
    GitError,
    identity.IdentityError,
    subprocess.CalledProcessError,
    sa.exc.SQLAlchemyError,
)


def _db_unusable(exc: Exception) -> dict[str, Any]:
    # store が包み直さなかった DB の誤り。本文は SQL・値・接続先を含みうるので種類の名前だけを出す
    return result.stopped(TOOL, store.StoreUnreachable.kind, f"進行表の DB を使えない: {type(exc).__name__}")


def _refused(exc: model.Refused) -> dict[str, Any]:
    metrics = {"current_id": exc.current_id} if exc.current_id else {}
    return result.stopped(TOOL, "refused", exc.reason, metrics=metrics)


def _store_stopped(exc: durable.StoreStopped) -> dict[str, Any]:
    return result.stopped(TOOL, exc.kind, str(exc), items=[{"path": str(p)} for p in exc.paths])


def _workflow_def_error(exc: definition.WorkflowDefError) -> dict[str, Any]:
    return result.stopped(TOOL, "invalid", str(exc), items=[{"error": e} for e in exc.errors])


def _cause_only(cause: str) -> Callable[[Exception], dict[str, Any]]:
    """付加情報を持たず、止まった理由の語だけが決まる例外の変換。"""
    return lambda exc: result.stopped(TOOL, cause, str(exc))


# 例外の種類 → 止まった結果への変換（前から引く）
_STOPPED_FROM: tuple[tuple[type[Exception], Callable[[Any], dict[str, Any]]], ...] = (
    (sa.exc.SQLAlchemyError, _db_unusable),
    (model.Refused, _refused),
    (durable.StoreStopped, _store_stopped),
    # WorkflowDefError は ContractError の子なので、理由の語だけの行より前で受ける
    (definition.WorkflowDefError, _workflow_def_error),
    (contracts.ContractError, _cause_only("invalid")),
    (GitError, _cause_only("invalid")),
    (identity.IdentityError, _cause_only("config_broken")),
)


def _stopped_from(exc: Exception) -> dict[str, Any]:
    """STOPPABLE の種類を受け、結果 JSON の止まった理由にする。表に無い種類は CalledProcessError。"""
    for kind, convert in _STOPPED_FROM:
        if isinstance(exc, kind):
            return convert(exc)
    return result.stopped(TOOL, "precondition", f"git の中で打つ（チェックアウトの置き場を求められない）: {exc}")


def main(argv: list[str]) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as exc:
        # 使い方の誤りは argparse が標準エラーへ書いた。--help は 0
        return causes.exit_code("invalid") if exc.code else 0
    try:
        out = _dispatch(a, Path.cwd())
    except STOPPABLE as exc:
        out = _stopped_from(exc)
    return result.emit(out)
