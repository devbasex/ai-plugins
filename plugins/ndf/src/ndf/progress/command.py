"""サブコマンド `ndf progress`: スプリントのステートマシンを作り、読み、conductor の操作で状態を変える。

ndf progress new <スプリント名> --mode <モード> --issues <番号>... [--pace <進め方>] [--disable <工程>=<理由>]...
                 [--requirements <要求と受け入れ条件の出力>] [--parent <親のステートマシンの ID>]
ndf progress new <スプリント名> --loop <ループの名前> --parent <レーンのステートマシンの ID>
ndf progress show <ステートマシンの ID>
ndf progress disable|enable|retry <ステートマシンの ID> <工程> --reason <理由>
ndf progress finish <ステートマシンの ID> <工程> --output <出力のファイル> --reason <理由>
ndf progress cancel <ステートマシンの ID> --reason <理由>
ndf progress restore --from <バックアップ>
ndf progress docs-only --base <ベースブランチ> [--remote <リモート名>]

結果は結果 JSON の 1 行で返す。このサブコマンドを打つのはレーンの conductor で、書く事象の書き手は conductor になる。
工程を流す `next` と、承認ゲートの回答を書く `answer` は P1b（#3092）が足す。

`--loop` はレーンの開発ループの行（`kind: loop`）のステートマシンを作る。工程を持たないので、開発ループが止まった後に
`ndf progress next` を打つと完了になり、スプリントと同じ口（`Env.ended`）でレーンの行を終える。計画を止めたときは
スプリントと同じく `ndf progress cancel` で中止にする（#3558）。
"""

from __future__ import annotations

import argparse
import functools
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters.git import Git, GitError
from ndf.contracts import causes, facts, result
from ndf.core import branch_strategy, durable
from ndf.progress import definition, model, stages, store

# 結果 JSON の tool の名前はサブコマンドの名前（パッケージの名前）と同じ
TOOL = __package__.rpartition(".")[2]
ACTOR = "conductor"
# 番号のぶつかり（同時の追記）で読み直して書き直す回数
APPEND_ATTEMPTS = 3
PROJECT_FILE = identity.FIXED_WORDS["ndf_settings_dir"] + "/project.json"
# プロジェクト MVV の設定（表 ndf-files の mvv。スプリントの文脈の mvv_ref の出所）
MVV_FILE = identity.FIXED_WORDS["ndf_settings_dir"] + "/" + facts.value("ndf-files", "mvv", "path")
# サブコマンド disable・enable が書く事象の種類
TOGGLE_KINDS = {"disable": "disabled", "enable": "enabled"}
# ステートマシンの作成の時点で済んでいる工程（要求と受け入れ条件）の終了の理由
DONE_AT_CREATION = "ステートマシンの作成の時点で済んでいる"
REQUIREMENTS_STAGE = "要求と受け入れ条件"  # `--requirements` が終了のマーカーを付ける工程（工程表の行名）
# 開発ループの行のステートマシンの定義（工程を持たない。モードの名前の代わりに置く）
LOOP_SNAPSHOT = stages.Snapshot(mode="loop", version="1.0.0", sha256="", height=0, stages=())


# --- 保存先とリポジトリ ------------------------------------------------------------------


def store_root_of(cwd: Path) -> Path:
    return durable.store_root(identity.load().name, cwd)


def _without_userinfo(url: str) -> str:
    """URL の userinfo（`user:token@`）を外す。トークンを DB へ残さず、トークンの入れ替えで識別を変えない。"""
    parts = urlsplit(url)
    if not parts.scheme or "@" not in parts.netloc:
        # scp の形（git@host:o/r）と userinfo の無い URL はそのまま
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def repo_id(cwd: Path, root: Path) -> str:
    """リポジトリの識別。リモート名のリモートの URL（userinfo を外す）、無ければメインディレクトリの絶対パス（保存先の 2 つ上の親）。"""
    try:
        return _without_userinfo(Git(cwd).remote_url())
    except GitError:
        # リモートの無いリポジトリはメインディレクトリで識別する
        return str(root.parents[2])


def read_json(path: Path, failure: str = "を JSON として読めない") -> Any:
    """`path` の JSON。読めなければ `"{path} {failure}: 理由"` の `ContractError`。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise contracts.ContractError(f"{path} {failure}: {exc}") from exc


def ndf_settings_path(cwd: Path, name: str) -> Path:
    """リポジトリの根の設定のファイルのパス。"""
    return Path(Git(cwd).run("rev-parse", "--show-toplevel").strip()) / name


def _read_ndf_settings(path: Path) -> dict[str, Any]:
    raw = read_json(path, "を読めない") if path.exists() else {}
    return raw if isinstance(raw, dict) else {}


def ndf_settings(cwd: Path, name: str) -> dict[str, Any]:
    """リポジトリの根の設定のファイル。無いか、オブジェクトでなければ空。JSON として読めなければ `ContractError`。"""
    return _read_ndf_settings(ndf_settings_path(cwd, name))


# --- 工程の定義の条件（`when`）の語のうち、ステートマシンの外（git の差分と設定のファイル・スプリントの文脈）から求める語 ---
# `ndf.progress.run` の `Env.facts` の既定（`facts_of`）。ステートマシンから求める語（`run.TABLE_FACTS`）はここに無い。


class FactUnknown(Exception):
    """工程の定義の条件（`when`）の語を、今の材料から求められない。"""

    def __init__(self, word: str) -> None:
        super().__init__(f"条件 {word} を求められない")
        self.word = word


DOC_FILES = ("**/*.md",)  # `.ndf/project.json` の `docs.files` を省いたときの文書のファイル
BEHAVIOR_DOCS: tuple[str, ...] = ()  # 同じく `docs.behavior`（振る舞いを決める文書。省けば無し）
# 差分の語（`diff_kinds` のキー）
DIFF_WORDS = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")


def diff_kinds(files: set[str], docs: set[str], behavior: set[str]) -> dict[str, bool]:
    """差分の語の値。振る舞いを決める文書は文書の部分集合。文書だけは、文書が 1 件以上でコードと振る舞いの文書が 0 件。"""
    code, behavior = files - docs, behavior & docs
    return {
        "code_in_diff": bool(code),
        "docs_only_diff": bool(files) and not code and not behavior,
        "behavior_docs_in_diff": bool(behavior),
    }


def _pathspecs(project: Mapping[str, Any], key: str, default: tuple[str, ...]) -> list[str]:
    """設定のパターン（根からのパス全体に当てる glob。`!` は前が当てたものを外す）を git の pathspec にする。"""
    value = definition.dig(project, "docs", key)
    patterns = value if isinstance(value, list) and all(isinstance(p, str) for p in value) else default
    return [f":(exclude,glob){p[1:]}" if p.startswith("!") else f":(glob){p}" for p in patterns]


def behavior_declared(cwd: Path) -> bool:
    """`.ndf/project.json` が `docs.behavior`（振る舞いを決める文書のパターンの並び）を定義しているか。"""
    value = definition.dig(ndf_settings(cwd, PROJECT_FILE), "docs", "behavior")
    return isinstance(value, list) and all(isinstance(p, str) for p in value)


def strategy(cwd: Path) -> branch_strategy.Strategy:
    """リポジトリの根のブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）。読めなければ `FactUnknown`。"""
    try:
        return branch_strategy.load(Path(Git(cwd).run("rev-parse", "--show-toplevel").strip()))
    except branch_strategy.StrategyError as exc:
        raise FactUnknown(f"ブランチ戦略の設定を読めない: {exc.reason}") from exc


def separate_production(cwd: Path) -> bool:
    """本番ブランチがベースブランチと別か（工程の条件 `prod_branch`。`trunk` では同じ）。"""
    got = strategy(cwd)
    return got.production != got.base


def _sprint_range(context: Mapping[str, Any], cwd: Path) -> tuple[Git, str]:
    """スプリントブランチとベースブランチ（ブランチ戦略の設定のスプリントの PR のマージ先。手元に無ければ `<リモート名>/`）の分岐点からの範囲。"""
    base = strategy(cwd).pr_target("sprint")
    if base.startswith("-"):
        raise FactUnknown(f"差分を求められない: ベースブランチ {base} が - で始まる")
    git = Git(Path(context.get("worktree") or cwd))
    head = str(context.get("branch") or "HEAD")
    try:
        git.rev_parse(base)
        ref = base
    except GitError:
        ref = f"{git.remote()}/{base}"
    return git, f"{git.merge_base(ref, head)}...{head}"


def _diff_names(git: Git, rng: str, specs: list[str] | None) -> set[str]:
    """範囲の差分のパス。`specs` が `None` なら全体、空の並びなら空（`git diff` を打たない。打つと全体が返る）。"""
    if specs is None:
        specs = []
    elif not specs:
        return set()
    return {p for p in git.run("diff", "--name-only", "-z", rng, "--", *specs).split("\0") if p}


def range_diff_kinds(git: Git, rng: str, cwd: Path) -> dict[str, bool]:
    """範囲 `rng` の差分を、`.ndf/project.json` の `docs.files`・`docs.behavior`（省けば既定）で分ける。git が読めなければ `GitError`。"""
    project = ndf_settings(cwd, PROJECT_FILE)
    files = _diff_names(git, rng, None)
    docs = _diff_names(git, rng, _pathspecs(project, "files", DOC_FILES))
    return diff_kinds(files, docs, _diff_names(git, rng, _pathspecs(project, "behavior", BEHAVIOR_DOCS)))


def docs_only(git: Git, rng: str, cwd: Path) -> bool:
    """範囲の差分が文書だけか（工程の条件 `docs_only_diff` と同じ判定。#3617 の I8a）。"""
    return range_diff_kinds(git, rng, cwd)["docs_only_diff"]


def sprint_diff_kinds(context: Mapping[str, Any], cwd: Path) -> dict[str, bool]:
    """スプリントの差分を分ける（`range_diff_kinds`）。"""
    try:
        git, rng = _sprint_range(context, cwd)
        return range_diff_kinds(git, rng, cwd)
    except GitError as exc:
        raise FactUnknown(f"差分を求められない: {exc}") from exc


def facts_of(words: tuple[str, ...], context: Mapping[str, Any], cwd: Path) -> dict[str, bool]:
    """工程の定義の条件（`when`）の語のうち、ステートマシンの外から求める語の値。求められない語は `FactUnknown`。"""
    kinds = functools.cache(lambda: sprint_diff_kinds(context, cwd))
    known: dict[str, Callable[[], bool]] = {
        **{w: (lambda w=w: kinds()[w]) for w in DIFF_WORDS},
        "ci_declared": lambda: definition.dig(ndf_settings(cwd, PROJECT_FILE), "test", "ci", "only") is True,
        "prod_branch": lambda: separate_production(cwd),
        "release_record": lambda: "version" in context,
        "design_pr": lambda: "design_pr" in context,
    }
    unknown = [w for w in words if w not in known]
    if unknown:
        raise FactUnknown(f"条件 {unknown[0]} を求める手段が無い")
    out = {}
    for w in words:
        try:
            out[w] = known[w]()
        except FactUnknown as exc:
            raise FactUnknown(f"条件 {w} を求められない（{exc}）") from exc
    return out


def _declared(cwd: Path) -> dict[str, Any]:
    """`.ndf/project.json` の `workflow.stages`（工程の名前 → 有効と理由）。ファイルか項目が無ければ空。"""
    path = ndf_settings_path(cwd, PROJECT_FILE)
    raw = _read_ndf_settings(path)
    workflow = raw.get("workflow")
    declared = workflow.get("stages") if isinstance(workflow, dict) else None
    if not isinstance(declared, dict):
        return {}
    errs = [f"workflow.stages.{name}: {e}" for name, value in declared.items() for e in _ndf_settings_errors(value)]
    if errs:
        raise contracts.ContractError(f"{path} の設定が形に合わない: {'; '.join(errs)}")
    return declared


def _ndf_settings_errors(value: Any) -> list[str]:
    """工程の設定 1 つの形 `{"enabled": 真偽, "reason": 理由（無効のとき必須）}` を破る理由。"""
    if not isinstance(value, dict):
        return ['{"enabled": 真偽, "reason": 理由} の形でない']
    if not isinstance(value.get("enabled"), bool):
        return ["enabled が真偽でない"]
    reason = value.get("reason")
    if not value["enabled"] and not (isinstance(reason, str) and reason):
        return ["無効にするときの reason が空か文字列でない"]
    return []


# --- ステートマシンの読み書き ---------------------------------------------------------------------


class Current(NamedTuple):
    """同じ改訂で読んだステートマシンの状態の一組。"""

    rev: int  # 最新の改訂
    raw: dict[str, Any]  # 定義の文書
    snapshot: stages.Snapshot  # 定義の文書から組み立てた定義
    events: list[model.Event]  # 事象


def read_current(conn: Any, machine_id: str) -> Current:
    """ステートマシンの最新の改訂・定義の文書・定義・事象。ステートマシンが無ければ `model.Refused`。"""
    if store.read_machine(conn, machine_id) is None:
        raise model.Refused(f"ステートマシン {machine_id} は無い")
    rev, raw = store.read_definition(conn, machine_id)
    return Current(rev, raw, stages.snapshot_from(raw), store.read_events(conn, machine_id))


def retrying_transaction(st: store.Store, write: Callable[[Any], list[int]]) -> list[int]:
    """`write(conn)` を 1 つのトランザクションで打つ。番号がぶつかれば（`store.Conflict`）APPEND_ATTEMPTS 回まで打ち直す。"""
    for attempt in range(1, APPEND_ATTEMPTS + 1):
        try:
            with store.transaction(st) as conn:
                return write(conn)
        except store.Conflict:
            if attempt == APPEND_ATTEMPTS:
                raise
    raise AssertionError("到達しない")


def _append(
    st: store.Store, machine_id: str, build: Callable[[int, stages.Snapshot, list[model.Event]], list[model.EventDraft]]
) -> list[int]:
    """今の改訂と事象を読み、`build` が作った下書きを追記する。番号がぶつかれば読み直して作り直す。"""

    def write(conn: Any) -> list[int]:
        rev, _, snap, events = read_current(conn, machine_id)
        drafts = build(rev, snap, events)
        return store.append_events(conn, machine_id, drafts, events[-1].seq if events else 0)

    return retrying_transaction(st, write)


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


# --- サブコマンド ------------------------------------------------------------------------------


def _new_drafts(snap: stages.Snapshot, enabled: dict[str, stages.Enablement], finished: list[tuple[str, str]]) -> list[model.EventDraft]:
    drafts = [model.EventDraft("created", ACTOR, s.name, rev=1) for s in snap.stages]
    for name, en in enabled.items():
        if not en.enabled:
            drafts.append(model.EventDraft("disabled", ACTOR, name, reason=en.reason, payload={"source": en.source}, rev=1))
    for name, output in finished:
        drafts.append(model.EventDraft("manual_finished", ACTOR, name, reason=DONE_AT_CREATION, payload={"output": output}, rev=1))
    return drafts


def _requirements(snap: stages.Snapshot, path: str | None) -> list[tuple[str, str]]:
    """`--requirements` の出力を要求と受け入れ条件の工程の出力の規約で確かめ、終了のマーカーを付ける工程の一覧にする。

    その工程を持たないモードでは別の工程へマーカーを付けず、引数の誤りとして断る。"""
    if path is None:
        return []
    found = [s for s in snap.stages if s.name == REQUIREMENTS_STAGE]
    if not found:
        raise definition.WorkflowDefError([f"--requirements: モード {snap.mode} に要求と受け入れ条件の工程が無い"])
    stage = found[0]
    errs = definition.validate_stage_output(stage, read_json(Path(path)))
    if errs:
        raise definition.WorkflowDefError([f"{stage.name} の出力が規約に合わない: {e}" for e in errs])
    return [(stage.name, path)]


def mvv_ref(cwd: Path) -> str | None:
    """スプリントが参照する承認済みプロジェクト MVV の sha256（MVV の設定の versions の最後）。設定・承認が無ければ None。"""
    versions = ndf_settings(cwd, MVV_FILE).get("versions")
    last = versions[-1] if isinstance(versions, list) and versions and isinstance(versions[-1], dict) else {}
    sha = last.get("sha256")
    return sha if isinstance(sha, str) else None


def _prepare_new(a: argparse.Namespace, cwd: Path, snap: stages.Snapshot) -> tuple[dict[str, Any], list[model.EventDraft]]:
    """引数の --disable と文脈を確かめ、保存する定義の改訂の文書と最初の事象の下書きを作る。"""
    disables = dict(item.split("=", 1) for item in a.disable)
    unknown = stages.unknown_stages(snap, disables)
    if unknown:
        raise definition.WorkflowDefError([f"--disable の工程がモード {a.mode} に無い: {', '.join(unknown)}"])
    context = {"issues": a.issues, "mode": a.mode, "pace": a.pace, "mvv_ref": mvv_ref(cwd)}
    errs = contracts.schema_errors(context, definition.CONTEXT)
    if errs:
        raise definition.WorkflowDefError([f"スプリントの文脈: {e}" for e in errs])
    enabled = stages.resolve_enabled(snap, None, _declared(cwd), disables)
    doc = {**stages.snapshot_dict(snap), "context": context, "enablement": {k: vars(v) for k, v in enabled.items()}}
    return doc, _new_drafts(snap, enabled, _requirements(snap, a.requirements))


def _loop_new(a: argparse.Namespace) -> tuple[stages.Snapshot, dict[str, Any], list[model.EventDraft]]:
    """開発ループの行のステートマシン（工程なし）。レーンの子にしか作れず、スプリントの引数とは混ぜない。"""
    mixed = [
        flag
        for flag, given in (("--mode", a.mode), ("--issues", a.issues), ("--requirements", a.requirements), ("--disable", a.disable))
        if given
    ]
    if mixed:
        raise definition.WorkflowDefError([f"--loop は {', '.join(mixed)} と一緒に渡せない"])
    if a.parent is None:
        raise definition.WorkflowDefError(["--loop にはレーンのステートマシン（--parent）が要る"])
    doc = {**stages.snapshot_dict(LOOP_SNAPSHOT), "context": {"issues": []}, "enablement": {}}
    return LOOP_SNAPSHOT, doc, []


def _sprint_new(a: argparse.Namespace, cwd: Path) -> tuple[stages.Snapshot, dict[str, Any], list[model.EventDraft]]:
    if a.mode is None or not a.issues:
        raise definition.WorkflowDefError(["--mode と --issues（開発ループの行なら --loop）を渡す"])
    snap = definition.load_definition(a.mode)
    doc, drafts = _prepare_new(a, cwd, snap)
    return snap, doc, drafts


def _cmd_new(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    snap, doc, drafts = _loop_new(a) if a.loop is not None else _sprint_new(a, cwd)
    with store.transaction(st) as conn:
        tid = store.create_machine(conn, ("sprint", repo_id(cwd, st.root), a.name), a.parent, doc, drafts)
        current = read_current(conn, tid)
    rows = _rows(snap, current.events, current.rev)
    off = [r["stage"] for r in rows if r["state"] == "disabled"]
    summary = f"ステートマシン {tid} を作った（{len(rows)} 行、無効 {len(off)} 行）"
    return result.result(TOOL, result.OK, summary, items=rows, metrics={"machine_id": tid, "rows": len(rows), "disabled": off})


def _cmd_show(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    with store.transaction(st) as conn:
        rev, _, snap, events = read_current(conn, a.machine_id)
    rows = _rows(snap, events, rev)
    whole = model.table_state("sprint", events)
    metrics = {"machine_id": a.machine_id, "mode": snap.mode, "rev": rev, "state": whole, "history": [e.as_dict() for e in events]}
    return result.result(
        TOOL, result.OK, f"ステートマシン {a.machine_id} は{model.label(whole)}（{len(rows)} 行）", items=rows, metrics=metrics
    )


def _changed(a: argparse.Namespace, st: store.Store, kind: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    def build(rev: int, snap: stages.Snapshot, _events: list[model.Event]) -> list[model.EventDraft]:
        stage = snap.stage(a.stage)
        if kind == "disabled" and stage.gates:
            raise model.Refused(stages.gate_refusal(stage))
        return [model.EventDraft(kind, ACTOR, a.stage, reason=a.reason, payload=payload, rev=rev)]

    seqs = _append(st, a.machine_id, build)
    return result.result(
        TOOL,
        result.OK,
        f"ステートマシン {a.machine_id} の {a.stage} に {kind} を書いた",
        metrics={"machine_id": a.machine_id, "seqs": seqs},
    )


def _cmd_toggle(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return _changed(a, st, TOGGLE_KINDS[a.action], None)


def _cmd_retry(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    with store.run_lock(st, a.machine_id):
        return _changed(a, st, "retry", None)


def _cmd_finish(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    output = read_json(Path(a.output))
    with store.transaction(st) as conn:
        snap = read_current(conn, a.machine_id).snapshot
    stage = snap.stage(a.stage)
    errs = definition.validate_stage_output(stage, output)
    if errs:
        raise model.Refused(f"{a.stage} の出力が規約に合わない: {'; '.join(errs)}")
    dropped: list[str] = []

    def build(rev: int, _snap: stages.Snapshot, _events: list[model.Event]) -> list[model.EventDraft]:
        """終了のマーカーと、工程が流れたときと同じ形で出力を文脈へ書く事象（後の工程の入力になる）。"""
        draft, dropped[:] = stages.outputs_draft(stage, rev, output["outputs"] if "outputs" in output else {}, ACTOR)
        done = model.EventDraft("manual_finished", ACTOR, a.stage, reason=a.reason, payload={"output": a.output}, rev=rev)
        return [done, *([draft] if draft is not None else [])]

    seqs = _append(st, a.machine_id, build)
    st.warnings.extend(dropped)
    return result.result(
        TOOL,
        result.OK,
        f"ステートマシン {a.machine_id} の {a.stage} に manual_finished を書いた",
        metrics={"machine_id": a.machine_id, "seqs": seqs},
    )


def _cmd_cancel(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    def build(rev: int, snap: stages.Snapshot, events: list[model.Event]) -> list[model.EventDraft]:
        folded = model.fold(events, rev)
        live = [s.name for s in snap.stages if s.name in folded and not model.is_terminal(folded[s.name].state)]
        drafts = [model.EventDraft("cancelled", ACTOR, name, reason=a.reason, rev=rev) for name in live]
        return [*drafts, model.EventDraft("cancelled", ACTOR, None, reason=a.reason, rev=rev)]

    with store.run_lock(st, a.machine_id):
        seqs = _append(st, a.machine_id, build)
    rows = len(seqs) - 1
    return result.result(
        TOOL,
        result.OK,
        f"ステートマシン {a.machine_id} を中止した（中止にした行 {rows}）",
        metrics={"machine_id": a.machine_id, "cancelled_rows": rows},
    )


def _cmd_restore(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    root = store_root_of(cwd)
    url = store.default_url(root)
    retired = store.restore(url, root, Path(a.source))
    items = [{"restored": str(store.sqlite_path(url)), "from": a.source, "retired": str(retired)}]
    return result.result(TOOL, result.OK, f"ステートマシンの DB をバックアップ {a.source} から戻した", items=items)


def _cmd_docs_only(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    """`base` のリモートの参照から HEAD までの差分が文書だけなら `not_applicable`、そうでなければ `ok`。git が読めなければ `precondition`。"""
    if not a.base or a.base.startswith("-"):
        return result.stopped(TOOL, "invalid", f"--base が空か - で始まる: {a.base!r}")
    try:
        git = Git(cwd)
        remote = a.remote if a.remote else git.remote()
        if remote.startswith("-"):
            return result.stopped(TOOL, "invalid", f"--remote が - で始まる: {remote!r}")
        rng = f"{remote}/{a.base}...HEAD"
        kinds = range_diff_kinds(git, rng, cwd)
    except GitError as exc:
        return result.stopped(TOOL, "precondition", f"差分を読めない: {exc}")
    metrics = {k: int(v) for k, v in kinds.items()}
    if kinds["docs_only_diff"]:
        return result.stopped(TOOL, "not_applicable", f"{rng} の差分は文書だけ", metrics=metrics)
    return result.result(TOOL, result.OK, f"{rng} の差分は文書だけでない", metrics=metrics)


Command = Callable[[argparse.Namespace, Path, store.Store], dict[str, Any]]


def _answer_commands(handlers: tuple[Command, ...]) -> dict[str, Command]:
    """止まった後に conductor が選ぶ手のサブコマンド。名前は回答の語彙（表 answers）の行で、`_cmd_<回答>` が受ける。"""
    out = {a: h for h in handlers for a in facts.ids(result.ANSWERS) if h.__name__ == f"_cmd_{a}"}
    if len(out) != len(handlers):
        raise LookupError(
            f"表 {result.ANSWERS} に無い回答のサブコマンド: {sorted({h.__name__ for h in handlers} - {h.__name__ for h in out.values()})}"
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
    new = sub.add_parser("new", help="スプリントのステートマシンを作る")
    new.add_argument("name", help="スプリント名")
    new.add_argument("--mode")
    new.add_argument("--issues", type=int, nargs="+")
    new.add_argument("--loop", help="開発ループの行のステートマシンを作る（工程なし。ループの名前）")
    new.add_argument("--pace", default="normal")
    new.add_argument("--disable", type=_disable_item, action="append", default=[], help="<工程>=<理由>")
    new.add_argument("--requirements", help="要求と受け入れ条件の出力（工程の出力の規約に合う JSON）")
    new.add_argument("--parent", help="親のステートマシン（レーンのステートマシン）の ID")
    sub.add_parser("show", help="ステートマシンを表示する").add_argument("machine_id")
    for name in ("disable", "enable", "retry", "finish"):
        p = sub.add_parser(name, help=f"工程に {name} を書く")
        p.add_argument("machine_id")
        p.add_argument("stage", help="工程の名前（工程表の行名）")
        p.add_argument("--reason", required=True)
        if name == "finish":
            p.add_argument("--output", required=True, help="工程の出力の規約に合う JSON のファイル")
    cancel = sub.add_parser("cancel", help="ステートマシンを中止する")
    cancel.add_argument("machine_id")
    cancel.add_argument("--reason", required=True)
    sub.add_parser("restore", help="バックアップからステートマシンの DB を戻す").add_argument("--from", dest="source", required=True)
    docs = sub.add_parser("docs-only", help="ベースブランチからの差分が文書だけかを返す")
    docs.add_argument("--base", required=True, help="ベースブランチ")
    docs.add_argument("--remote", help="リモート名（省けば worktree の設定の remote か既定）")
    return ap


def with_warnings(out: dict[str, Any], before: Sequence[str] = (), after: Sequence[str] = ()) -> dict[str, Any]:
    """結果の `warnings` の前に `before`、後ろに `after` を足す。足すものが無ければ結果に触れない。"""
    if before or after:
        out["warnings"] = [*before, *(out["warnings"] if "warnings" in out else []), *after]
    return out


def prepare_checkout_store(cwd: Path) -> tuple[Path, str]:
    """チェックアウトの保存先を求めて用意し、（用意した保存先, ステートマシンの DB の URL）を返す。"""
    root = store_root_of(cwd)
    url = store.default_url(root)
    store.sqlite_path(url)  # 受けない URL の形は、保存先を用意する前に断る
    ready = durable.prepare_store(root, datetime.now(UTC))
    return ready.root, url


def with_store(cwd: Path, handler: Command, a: argparse.Namespace) -> dict[str, Any]:
    """チェックアウトの保存先のステートマシンの DB を開いて `handler` を打ち、DB を開いた間の警告を結果の後ろへ足す。"""
    root, url = prepare_checkout_store(cwd)
    with store.open_store(url, root) as st:
        return with_warnings(handler(a, cwd, st), after=st.warnings)


def _dispatch(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    if a.action == "restore":
        return _cmd_restore(a, cwd)
    if a.action == "docs-only":
        return _cmd_docs_only(a, cwd)
    return with_store(cwd, COMMANDS[a.action], a)


# 停止理由へ変換する例外の種類（main が受け、stopped_from が理由へ変える）
STOPPABLE = (
    model.Refused,
    durable.StoreStopped,
    contracts.ContractError,
    GitError,
    identity.IdentityError,
    subprocess.CalledProcessError,
    sa.exc.SQLAlchemyError,
)


def stopped_db(tool: str, exc: Exception) -> dict[str, Any]:
    """store が包み直さなかった DB の誤りの止まった結果。本文は SQL・値・接続先を含みうるので種類の名前だけを出す。"""
    return result.stopped(tool, store.StoreUnreachable.kind, f"ステートマシンの DB を使えない: {type(exc).__name__}")


def stopped_refused(tool: str, exc: model.Refused) -> dict[str, Any]:
    """断った操作の止まった結果。既にあるステートマシンの id があれば metrics へ載せる。"""
    metrics = {"current_id": exc.current_id} if exc.current_id else {}
    return result.stopped(tool, "refused", exc.reason, metrics=metrics)


def _workflow_def_error(exc: definition.WorkflowDefError) -> dict[str, Any]:
    return result.stopped(TOOL, "invalid", str(exc), items=[{"error": e} for e in exc.errors])


def _cause_only(cause: str) -> Callable[[Exception], dict[str, Any]]:
    """付加情報を持たず、停止理由の語だけが決まる例外の変換。"""
    return lambda exc: result.stopped(TOOL, cause, str(exc))


# 例外の種類 → 止まった結果への変換（前から引く）
_STOPPED_FROM: tuple[tuple[type[Exception], Callable[[Any], dict[str, Any]]], ...] = (
    (sa.exc.SQLAlchemyError, functools.partial(stopped_db, TOOL)),
    (model.Refused, functools.partial(stopped_refused, TOOL)),
    (durable.StoreStopped, lambda exc: exc.stopped(TOOL)),
    # WorkflowDefError は ContractError の子なので、理由の語だけの行より前で受ける
    (definition.WorkflowDefError, _workflow_def_error),
    (contracts.ContractError, _cause_only("invalid")),
    (GitError, _cause_only("invalid")),
    (identity.IdentityError, _cause_only("config_broken")),
)


def stopped_from(exc: Exception) -> dict[str, Any]:
    """STOPPABLE の種類を受け、結果 JSON の停止理由にする。表に無い種類は CalledProcessError。"""
    for kind, convert in _STOPPED_FROM:
        if isinstance(exc, kind):
            return convert(exc)
    return result.stopped(TOOL, "precondition", f"git の中で打つ（チェックアウトの保存先を求められない）: {exc}")


def run_main(ap: argparse.ArgumentParser, dispatch: Callable[[argparse.Namespace, Path], dict[str, Any]], argv: list[str]) -> int:
    """引数を読んで `dispatch` を打ち、結果 JSON を出す。STOPPABLE の例外は停止理由にする。"""
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        # 使い方の誤りは argparse が標準エラーへ書いた。--help は 0
        return causes.exit_code("invalid") if exc.code else 0
    try:
        out = dispatch(a, Path.cwd())
    except STOPPABLE as exc:
        out = stopped_from(exc)
    return result.emit(out)


def main(argv: list[str]) -> int:
    return run_main(parser(), _dispatch, argv)
