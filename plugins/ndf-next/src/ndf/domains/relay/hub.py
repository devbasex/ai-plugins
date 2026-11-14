"""リレーハブ: 名前つきのラッパーの起動（spawn）・起こし直し（restart）・停止（stop）・一覧（ls）。

頼む側は依頼を `relay/hub/requests/<id>.json` へ書き、リレーハブの排他（`hub.lock`）を取れたら自分がリレーハブに
なって、溜まった依頼を古い順に 1 本ずつ処理する（常駐のプロセスを持たない）。結果は `results/<id>.json` へ書き、
頼んだ側が読んで消す。登録簿 `registry.json` を書くのは排他を持つリレーハブだけである。

- 起こし直しは、止める前に上限を判定し、通ったときだけ止めてから起こす（#2753。`hub_decide.restart`）
- 起動が「起動した」を返すのは、ラッパーの錠が取られ、tmux のセッションが残っているときだけ（#2563）
- ラッパーの置き場（プラグインの `bin/ndf`）が無ければ、理由と打ち直しのコマンドを返す（#2583）
- 版の違うラッパーが動いていれば起動しない（3341-2。`hub_decide.mixed`）
- 起こし直しの結果に、待ちを抱えた実行の一覧（`ndf plan pending`）を載せる（#3344 の決定 86）
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ndf.contracts import facts
from ndf.domains.relay import hub_decide, registry, session_env, signals
from ndf.domains.relay.tmux import Tmux

LOCK_FILE = "hub.lock"
STATE_FILE = "hub.json"
REGISTRY_FILE = "registry.json"
LOG_FILE = "hub-log.jsonl"


class Outcome(NamedTuple):
    """依頼 1 本の結果。`cause` は断った・失敗した理由の種類（`contracts/causes.json`）。"""

    ok: bool
    summary: str
    cause: str = ""
    items: list = []


class Wait(NamedTuple):
    seconds: float
    interval: float


@dataclass
class Context:
    """リレーハブが読む事実と部品。時計・眠り・日付はテストで差し替える。"""

    state_dir: Path
    plugin_root: Path
    version: str
    environ: Mapping[str, str]
    tmux: Tmux
    max_named: int
    max_starts: int
    started: Wait
    stop_wait: Wait
    hub_wait: Wait
    pending: Callable[[], Mapping[str, Any]] = field(default=lambda: {"runs": [], "note": ""})
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    today: Callable[[], date] = date.today


def hub_dir(state_dir: Path) -> Path:
    return state_dir / facts.value("places", "relay-hub", "path")


def submit(ctx: Context, request: Mapping[str, Any]) -> Outcome:
    """依頼を書き、結果が出るまで待つ（排他が空けば自分で処理する）。"""
    base = hub_dir(ctx.state_dir)
    rid = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
    signals.replace_json(base / "requests" / f"{rid}.json", dict(request))
    result = base / "results" / f"{rid}.json"

    def answered() -> bool:
        drain(ctx)
        return result.is_file()

    if not _wait_until(ctx, ctx.hub_wait, answered):
        (base / "requests" / f"{rid}.json").unlink(missing_ok=True)
        return Outcome(False, f"リレーハブの答えを {ctx.hub_wait.seconds:g} 秒待っても出ない", "wait_timeout")
    data = json.loads(result.read_text(encoding="utf-8"))
    result.unlink()
    return Outcome(data["ok"], data["summary"], data["cause"], data["items"])


def _wait_until(ctx: Context, wait: Wait, done: Callable[[], bool]) -> bool:
    """`done` が真になれば真、`wait.seconds` 秒を過ぎれば偽。各回の判定の後に期限を見てから `wait.interval` 秒眠る。"""
    deadline = ctx.monotonic() + wait.seconds
    while not done():
        if ctx.monotonic() >= deadline:
            return False
        ctx.sleep(wait.interval)
    return True


def drain(ctx: Context) -> bool:
    """排他を取れたら溜まった依頼を古い順に処理する。取れなければ偽。"""
    base = hub_dir(ctx.state_dir)
    base.mkdir(parents=True, exist_ok=True)
    with signals.held(base / LOCK_FILE, 0) as owned:
        if not owned:
            return False
        signals.replace_json(base / STATE_FILE, {"pid": os.getpid(), "version": ctx.version, "at": signals.now_iso()})
        requests = base / "requests"
        for path in sorted(requests.glob("*.json")) if requests.is_dir() else []:
            got = _handle_file(ctx, path)
            if got is None:
                continue
            _log(base, path.stem, got)
            if not path.is_file():
                continue  # 処理の間に頼む側が期限切れで消した。誰も読まない結果を書かない
            signals.replace_json(base / "results" / path.name, got._asdict())
            path.unlink(missing_ok=True)
        return True


def _handle_file(ctx: Context, path: Path) -> Optional[Outcome]:
    """依頼のファイル 1 本を処理する。読めない依頼・処理が送出した例外も結果にして返す（後ろの依頼を止めない）。

    依頼のファイルが既に無い（頼む側が submit の期限切れで消した）ときは None を返す。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 頼む側が期限切れで消した依頼は処理しない
        return None
    try:
        req = json.loads(text)
        if not isinstance(req, dict):
            return Outcome(False, f"依頼の形が違う（{path.name}）", "invalid")
        return handle(ctx, req)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Outcome(False, f"依頼を処理できない（{path.name}）: {type(exc).__name__}: {exc}", "invalid")


def handle(ctx: Context, req: Mapping[str, Any]) -> Outcome:
    """依頼 1 本を処理する。"""
    kind = req.get("kind")
    try:
        if kind == "spawn":
            return spawn(ctx, req)
        if kind == "restart":
            return restart(ctx, str(req["name"]))
        if kind == "stop":
            return stop(ctx, str(req["name"]))
    except registry.RegistryError as exc:
        return Outcome(False, str(exc), "invalid")
    return Outcome(False, f"知らない依頼: {kind}", "invalid")


def read_registry(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = hub_dir(state_dir) / REGISTRY_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise registry.RegistryError(f"登録簿を読めない（{path}）: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise registry.RegistryError(f"登録簿の形が違う（{path}）")
    return entries


def _save_registry(state_dir: Path, entries: Mapping[str, Any]) -> None:
    signals.replace_json(hub_dir(state_dir) / REGISTRY_FILE, {"version": 1, "entries": dict(entries)})


def _next_name(taken: set[str]) -> str:
    n = 1
    while f"t{n}" in taken:
        n += 1
    return f"t{n}"


def spawn(ctx: Context, req: Mapping[str, Any]) -> Outcome:
    """新しく起動する。"""
    entries = read_registry(ctx.state_dir)
    running = registry.all_running(ctx.state_dir, ctx.environ)
    refused = _refused(hub_decide.mixed(ctx.version, running), "precondition")
    if refused is not None:
        return refused
    names = {r.name for r in running}
    name = str(req.get("name") or _next_name(names | set(entries)))
    registry.wrapper_dir(ctx.state_dir, name)  # 名前の形を tmux を起動する前に確かめる（違えば RegistryError）
    if name in names or ctx.tmux.has(name):
        return Outcome(False, f"{name} は既に動いている", "refused")
    lanes = hub_decide.Lanes(_own_count(running), ctx.max_named, False, 0, ctx.max_starts)
    refused = _refused(hub_decide.spawn(lanes), "refused")
    if refused is not None:
        return refused
    entry = {k: req.get(k) for k in ("cwd", "args", "role", "plan")}
    entry["starts"] = registry.Starts(ctx.today().isoformat(), 0).as_json()
    got = _launch(ctx, name, entry)
    if got.ok:
        entries[name] = {**entry, "version": ctx.version}
        _save_registry(ctx.state_dir, entries)
    return got


def restart(ctx: Context, name: str) -> Outcome:
    """止める前に上限と版を判定し、通ったときだけ止めてから起こす。"""
    entries = read_registry(ctx.state_dir)
    entry = entries.get(name)
    if entry is None:
        return Outcome(False, f"{name} は登録簿に無い。spawn で起動する", "invalid")
    running = registry.all_running(ctx.state_dir, ctx.environ)
    others = [r for r in running if r.name != name]
    refused = _refused(hub_decide.mixed(ctx.version, others), "precondition")
    if refused is not None:
        return refused
    starts = registry.Starts.today(entry.get("starts"), ctx.today)
    target = any(r.name == name for r in running)
    lanes = hub_decide.Lanes(_own_count(running), ctx.max_named, target, starts.count, ctx.max_starts)
    refused = _refused(hub_decide.restart(lanes), "refused")
    if refused is not None:
        return refused
    pending = ctx.pending()
    if target:
        stopped = stop(ctx, name)
        if not stopped.ok:
            return stopped
    entry = {**entry, "starts": starts.bumped().as_json(), "version": ctx.version}
    got = _launch(ctx, name, entry)
    entries[name] = entry
    _save_registry(ctx.state_dir, entries)
    return _with_pending(got, pending)


def _refused(d: hub_decide.Decision, cause: str) -> Optional[Outcome]:
    """断る判断なら、その理由と `cause` の結果。進めるなら None。"""
    return None if d.ok else Outcome(False, d.reason, cause)


def _own_count(running: list[hub_decide.Running]) -> int:
    """動いているラッパーのうち、この NDF のもの（版を持つもの）の数。"""
    return len([r for r in running if r.version is not None])


def _with_pending(got: Outcome, pending: Mapping[str, Any]) -> Outcome:
    """起こし直しの結果へ、待ちを抱えた実行の件数（読めなければその理由）と一覧を足す。"""
    note = pending.get("note")
    if note:
        summary = f"{got.summary}。待ちの一覧を読めない: {note}"
    else:
        summary = f"{got.summary}。待ちを抱えた実行 {len(pending.get('runs') or [])} 件"  # noqa: NDF501 外部コマンド: 読めなかった一覧は空
    return Outcome(got.ok, summary, got.cause, [*got.items, {"pending": pending.get("runs")}])


def stop(ctx: Context, name: str) -> Outcome:
    """tmux のセッションを止め、ラッパーの錠が空くまで待つ。"""
    lock = registry.wrapper_dir(ctx.state_dir, name) / registry.LOCK_FILE
    if not registry.lock_busy(lock) and not ctx.tmux.has(name):
        return Outcome(True, f"{name} は動いていない")
    killed = ctx.tmux.kill(name)
    if not _wait_until(ctx, ctx.stop_wait, lambda: not registry.lock_busy(lock)):
        return Outcome(False, f"{name} が {ctx.stop_wait.seconds:g} 秒で終わらない（tmux: {killed.detail}）", "wait_timeout")
    return Outcome(True, f"{name} を止めた")


def _launch(ctx: Context, name: str, entry: Mapping[str, Any]) -> Outcome:
    ndf = ctx.plugin_root / "bin" / "ndf"
    if not ndf.is_file():
        facts = hub_decide.Started(False, False, False)
        return Outcome(False, hub_decide.started(facts, name).reason, "precondition")
    env = session_env.child_env(ctx.environ, ctx.state_dir)
    extra = {"NDF_RELAY_NAME": name, "NDF_RELAY_SPAWNED": "1", "NDF_RELAY_ROLE": entry.get("role"), "NDF_RELAY_PLAN": entry.get("plan")}
    env.update({k: str(v) for k, v in extra.items() if v})
    cwd = Path(str(entry.get("cwd") or ctx.state_dir))
    args = [str(a) for a in entry.get("args") or []]  # noqa: NDF501 利用者の入力: 引数が無ければ claude の既定
    started = ctx.tmux.start(name, cwd, [str(ndf), "relay", "run", "--name", name, "--", *args], env)
    if not started.ok:
        return Outcome(False, f"{name} の tmux のセッションを作れない: {started.detail}", "precondition")
    return _confirm(ctx, name)


def _confirm(ctx: Context, name: str) -> Outcome:
    """ラッパーが錠を取り、tmux のセッションが残るまで待つ。"""
    lock = registry.wrapper_dir(ctx.state_dir, name) / registry.LOCK_FILE
    _wait_until(ctx, ctx.started, lambda: registry.lock_busy(lock))
    facts = hub_decide.Started(True, ctx.tmux.has(name), registry.lock_busy(lock))
    got = hub_decide.started(facts, name)
    return Outcome(got.ok, got.reason, "" if got.ok else "precondition", [{"name": name}])


def listing(ctx: Context) -> list[dict[str, Any]]:
    """登録簿の各行に、動いているか・ラッパーの状態（`stalled` など。#3243）と理由を足す。"""
    running = {r.name: r for r in registry.running(ctx.state_dir)}
    out = []
    for name, entry in sorted(read_registry(ctx.state_dir).items()):
        state = registry.read_state(registry.wrapper_dir(ctx.state_dir, name))
        status = (state or {}).get("state") if name in running else "stopped"  # noqa: NDF501 ファイル: 状態が無ければ起動の途中
        out.append({"name": name, "running": name in running, "state": status, "reason": (state or {}).get("reason"), **entry})  # noqa: NDF501 ファイル: 状態が無ければ理由も無い
    return out


def _log(base: Path, rid: str, got: Outcome) -> None:
    signals.append_jsonl(base / LOG_FILE, {"request": rid, "ok": got.ok, "summary": got.summary, "cause": got.cause})
