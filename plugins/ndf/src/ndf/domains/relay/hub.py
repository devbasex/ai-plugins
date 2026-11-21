"""リレーハブ: 名前つきのラッパーの起動（spawn）・再起動（restart）・停止（stop）・一覧（ls）。

頼む側はリクエストを `relay/hub/requests-v2/<id>.json` へ書き、リレーハブの排他（`hub.lock`）を取れたら自分がリレーハブに
なって、溜まったリクエストを古い順に 1 本ずつ処理する（常駐のプロセスを持たない）。結果は `results/<id>.json` へ書き、
頼んだ側が読んで消す。登録簿 `registry.json` を書くのは排他を持つリレーハブだけである。

- 再起動は、止める前に上限を判定し、通ったときだけ止めてから起動する（#2753。`hub_decide.restart`）
- 本数の上限は判定のたびにマシンの設定 → 頼む側が要求に載せた値（頼む側の環境変数 → 上限の設定）の順で読む（#3989 の決定 4）。
  上限で断った結果の `items` に `{"refusal": "max-named", "running", "cap"}` の項目を載せる
- 置き換えの起動（`spawn --replace --seat <席>`。要求の種別は `replace`）は、止める前に対象を除いた本数で判定する。本数には、
  起動の許可が数えたラッパーの止まった稼働中のレーン（要求の `held`）も数える。続きの起動（`--seated`）は、行を始める起動が
  登録簿に書いた席と同じ席なら本数を判定しない。席を書く前のバージョンが書いた行（`seat` のキーが無い）は、役と計画が同じなら初回の
  続きの起動で席を書く（#3989 の決定 3・7）
- 置き換えの種別を知らない旧版のハブが処理すると「知らないリクエスト」で返るので、頼む側は同じ中身を新しい要求 ID で書き直し（旧版は結果を
  書いてから要求を消すので、同じパスへの書き直しはその削除に消される）、対応版のハブが
  処理するまで待つ（旧版が通常の起動として席を書かずに起動しない）
- 一覧は登録簿の行に加え、登録簿に無く錠を持つラッパーも `registered: false` で載せる（ハブが数えるラッパーをすべて載せる）
- 起動が「起動した」を返すのは、ラッパーの錠が取られ、tmux のセッションが残っているときだけ（#2563）
- ラッパーの保存先（プラグインの `bin/ndf`）が無ければ、理由と打ち直しのコマンドを返す（#2583）
- バージョンの違うラッパーが動いていれば起動しない（3341-2。`hub_decide.mixed`）
- 再起動の結果に、待ちを抱えた実行の一覧（`ndf plan pending`）を載せる（#3344 の決定 86）
- リクエストには頼んだ側の導入（プラグインの根とバージョン）を載せ、起動・再起動はリレーハブ自身ではなくその導入の
  `bin/ndf` とバージョンで行う（#3909。更新前に起動したコマンドがリレーハブでも、新しい導入を選んだ頼みを旧版で起動しない）。
  受付箱は導入を読まない旧版の `requests/` と分け（`REQUESTS_DIR`）、旧版のリレーハブには新しいリクエストを見せない
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Union

from ndf import identity
from ndf.contracts import facts
from ndf.contracts import named_cap
from ndf.domains.relay import hub_decide, registry, session_env, signals
from ndf.domains.relay.tmux import Tmux

LOCK_FILE = "hub.lock"
STATE_FILE = "hub.json"
LOG_FILE = "hub-log.jsonl"
# 受付箱。導入（plugin_root・version）を読まない旧版は `requests/` だけを読むので、別の名前にして旧版に処理させない（#3909）。
# 旧版の頼む側は自分の drain で `requests/` を処理する（排他は共通の hub.lock）
REQUESTS_DIR = "requests-v2"
# 置き換えの起動の要求の種別と、知らない種別への回答の頭（旧版のハブも同じ文で返す）
REPLACE = "replace"
UNKNOWN_KIND = "知らないリクエスト"


class Outcome(NamedTuple):
    """リクエスト 1 本の結果。`cause` は断った・失敗した理由の種類（`contracts/causes.json`）。"""

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
    max_starts: int
    started: Wait
    stop_wait: Wait
    hub_wait: Wait
    pending: Callable[[], Mapping[str, Any]] = field(default=lambda: {"runs": [], "note": ""})
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    today: Callable[[], date] = date.today
    # 要求に載った頼む側の上限（マシンの設定が無いときに使う。`requester` が載せる）
    fallback_cap: Optional[Union[named_cap.Cap, named_cap.NamedCapError]] = None


def hub_dir(state_dir: Path) -> Path:
    return state_dir / facts.value("locations", "relay-hub", "path")


def submit(ctx: Context, request: Mapping[str, Any]) -> Outcome:
    """リクエストを書き、結果が出るまで待つ（排他が空けば自分で処理する）。

    リクエストには頼んだ側の導入（`plugin_root`・`version`）を載せる。別のプロセスがリレーハブでも、その導入で処理させる（#3909）。
    """
    base = hub_dir(ctx.state_dir)
    payload = {**request, "plugin_root": str(ctx.plugin_root), "version": ctx.version}
    if _carries_cap(request):
        payload["cap"] = _requested_cap(ctx.environ)
    # 書いた要求のパスと、その結果のパス（再送のたびに新しい要求 ID へ切り替える）
    paths = [_write_request(base, payload)]

    got: list[dict[str, Any]] = []

    def answered() -> bool:
        drain(ctx)
        result = paths[0][1]
        if not result.is_file():
            return False
        data = json.loads(result.read_text(encoding="utf-8"))
        result.unlink()
        if _unknown_to_older_hub(request, data):
            # 旧版のハブが処理して断った。対応版のハブ（自分の drain を含む）が処理するまで要求を書き直す。旧版のハブは結果を
            # 書いてから要求のファイルを消すので、同じパスへ書き直すとその削除が再送分を消す。新しい要求 ID で書く
            paths[0] = _write_request(base, payload)
            return False
        got.append(data)
        return True

    if not _wait_until(ctx, ctx.hub_wait, answered):
        paths[0][0].unlink(missing_ok=True)
        return Outcome(False, f"リレーハブの回答を {ctx.hub_wait.seconds:g} 秒待っても出ない", "wait_timeout")
    data = got[0]
    return Outcome(data["ok"], data["summary"], data["cause"], data["items"])


def _write_request(base: Path, payload: Mapping[str, Any]) -> tuple[Path, Path]:
    """新しい要求 ID で要求を書き、（要求のパス, 結果のパス）を返す。"""
    rid = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
    request_path = base / REQUESTS_DIR / f"{rid}.json"
    signals.replace_json(request_path, payload)
    return request_path, base / "results" / f"{rid}.json"


def _unknown_to_older_hub(request: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    """置き換えの要求を、その種別を知らない旧版のハブが断った回答か。"""
    kind = request.get("kind")
    return kind == REPLACE and data.get("cause") == "invalid" and data.get("summary") == f"{UNKNOWN_KIND}: {kind}"


def _carries_cap(request: Mapping[str, Any]) -> bool:
    """要求に頼む側の上限を載せるか（再起動と、席を指定しない起動）。"""
    return request.get("kind") in ("restart", "spawn") or (request.get("kind") == REPLACE and not request.get("seated"))


def _requested_cap(environ: Mapping[str, str]) -> dict[str, Any]:
    """要求に載せる頼む側の上限（マシンの設定を読まない。解けなければ誤りの文。ハブはマシンの設定が無いときだけ使う）。"""
    try:
        got = named_cap.fallback(environ)
    except named_cap.NamedCapError as exc:
        return {"error": str(exc)}
    return {"value": got.value, "source": got.source}


def _wait_until(ctx: Context, wait: Wait, done: Callable[[], bool]) -> bool:
    """`done` が真になれば真、`wait.seconds` 秒を過ぎれば偽。各回の判定の後に期限を見てから `wait.interval` 秒眠る。"""
    deadline = ctx.monotonic() + wait.seconds
    while not done():
        if ctx.monotonic() >= deadline:
            return False
        ctx.sleep(wait.interval)
    return True


def drain(ctx: Context) -> bool:
    """排他を取れたら溜まったリクエストを古い順に処理する。取れなければ偽。"""
    base = hub_dir(ctx.state_dir)
    base.mkdir(parents=True, exist_ok=True)
    with signals.held(base / LOCK_FILE, 0) as owned:
        if not owned:
            return False
        signals.replace_json(base / STATE_FILE, {"pid": os.getpid(), "version": ctx.version, "at": signals.now_iso()})
        requests = base / REQUESTS_DIR
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
    """リクエストのファイル 1 本を処理する。読めないリクエスト・処理が送出した例外も結果にして返す（後ろのリクエストを止めない）。

    リクエストのファイルが既に無い（頼む側が submit の期限切れで消した）ときは None を返す。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 頼む側が期限切れで消したリクエストは処理しない
        return None
    try:
        req = json.loads(text)
        if not isinstance(req, dict):
            return Outcome(False, f"リクエストの形が違う（{path.name}）", "invalid")
        return handle(ctx, req)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Outcome(False, f"リクエストを処理できない（{path.name}）: {type(exc).__name__}: {exc}", "invalid")


def handle(ctx: Context, req: Mapping[str, Any]) -> Outcome:
    """リクエスト 1 本を処理する。起動・再起動は頼んだ側の導入で行う（`requester`）。"""
    kind = req.get("kind")
    try:
        if kind in ("spawn", REPLACE):
            return spawn(requester(ctx, req), req)
        if kind == "restart":
            return restart(requester(ctx, req), str(req["name"]))
        if kind == "stop":
            return stop(ctx, str(req["name"]))
    except (registry.RegistryError, named_cap.NamedCapError) as exc:
        return Outcome(False, str(exc), "invalid")
    return Outcome(False, f"{UNKNOWN_KIND}: {kind}", "invalid")


def requester(ctx: Context, req: Mapping[str, Any]) -> Context:
    """頼んだ側の導入（`plugin_root`・`version`）と上限（`cap`）に差し替えた `ctx`。リクエストに無い・形が違えばリレーハブ自身の値。"""
    root, version = req.get("plugin_root"), req.get("version")
    if isinstance(root, str) and root and isinstance(version, str) and version:
        ctx = dataclasses.replace(ctx, plugin_root=Path(root), version=version)
    return dataclasses.replace(ctx, fallback_cap=_cap_of(req.get("cap")))


def _cap_of(raw: object) -> Optional[Union[named_cap.Cap, named_cap.NamedCapError]]:
    """要求の `cap`（`{"value", "source"}` か `{"error"}`）。無い・形が違えば None（ハブ自身の環境で読む）。"""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("error"), str):
        return named_cap.NamedCapError(raw["error"])
    value, source = raw.get("value"), raw.get("source")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1 and isinstance(source, str):
        return named_cap.Cap(value, source)
    return None


def _max_named(ctx: Context) -> int:
    """判定のたびに読む本数の上限（マシンの設定 → 要求に載った頼む側の値。要求に無ければハブ自身の環境 → 上限の設定）。"""
    return named_cap.read(ctx.environ, ctx.state_dir, ctx.fallback_cap).value


def _next_name(taken: set[str]) -> str:
    n = 1
    while f"t{n}" in taken:
        n += 1
    return f"t{n}"


def spawn(ctx: Context, req: Mapping[str, Any]) -> Outcome:
    """新しく起動する。`replace` なら同じ名前のラッパーを、止める前に上限を判定してから置き換える（#3989 の決定 7）。"""
    entries = registry.read_registry(hub_dir(ctx.state_dir))
    running = registry.all_running(ctx.state_dir, ctx.environ)
    replacing, seated, seat = req.get("kind") == REPLACE, bool(req.get("seated")), req.get("seat")
    names = {r.name for r in running}
    name = str(req.get("name") or _next_name(names | set(entries)))
    registry.wrapper_dir(ctx.state_dir, name)  # 名前の形を tmux を起動する前に確かめる（違えば RegistryError）
    target = name in names or ctx.tmux.has(name)
    # 置き換えでは対象を除いてバージョンの混在を判定する（対象だけが旧版なら置き換えで直る）
    others = [r for r in running if r.name != name] if replacing else running
    refused = _refused(hub_decide.mixed(ctx.version, others), "precondition")
    if refused is not None:
        return refused
    if target and not replacing:
        return Outcome(False, f"{name} は既に動いている", "refused")
    if seated:
        refused = _seated_ok(entries, name, str(seat), req)
    else:
        refused = _over_cap(ctx, running, name if replacing else None, _held(req) if replacing else ())
    if refused is not None:
        return refused
    entry: dict[str, Any] = {k: req.get(k) for k in ("cwd", "args", "role", "plan")}
    entry["starts"] = registry.Starts(ctx.today().isoformat(), 0).as_json()
    # 席を持たない起動も `seat` のキーを書く（キーの無い行は席を書く前のバージョンの行として扱う。_seated_ok）
    entry["seat"] = seat if replacing else None
    if replacing:
        refused = _preflight(ctx, name, entry)
        if refused is not None:
            return refused
        unstopped = _await_exit(ctx, name, target)
        if unstopped is not None:
            return unstopped
    got = _launch(ctx, name, entry)
    if got.ok:
        entries[name] = {**entry, "version": ctx.version}
        registry.save_registry(hub_dir(ctx.state_dir), entries)
    return got


def _held(req: Mapping[str, Any]) -> tuple[str, ...]:
    """要求の `held`（起動の許可が数えた、ラッパーの止まった稼働中のレーンの名前）。無い・形が違えば空。"""
    raw = req.get("held")
    return tuple(h for h in raw if isinstance(h, str) and h) if isinstance(raw, list) else ()


def _over_cap(ctx: Context, running: list[hub_decide.Running], target: Optional[str], held: tuple[str, ...] = ()) -> Optional[Outcome]:
    """本数の上限の判定。`target` があれば置き換える対象を除いて数える。`held` の名前も 1 本ずつ数える（動いているラッパーと
    同じ名前は 1 回）。断るなら `items` に上限の断りの項目を載せた結果。"""
    limit = _max_named(ctx)
    own = {r.name for r in running if r.version is not None}
    counted = len(own | (set(held) - {target}))
    own_target = target is not None and any(r.name == target and r.version is not None for r in running)
    lanes = hub_decide.Lanes(counted, limit, own_target, 0, ctx.max_starts)
    got = hub_decide.replace(lanes) if target is not None else hub_decide.spawn(lanes)
    return None if got.ok else _cap_refusal(got.reason, hub_decide.others_running(lanes), limit)


def _cap_refusal(reason: str, running: int, limit: int) -> Outcome:
    return Outcome(False, reason, "refused", [{"refusal": named_cap.REFUSAL, "running": running, "cap": limit}])


def _seated_ok(entries: Mapping[str, Mapping[str, Any]], name: str, seat: str, req: Mapping[str, Any]) -> Optional[Outcome]:
    """続きの起動の席を確かめる。登録簿の `name` の行の席が要求と同じなら None（本数を判定しない）。ラッパーの生死は見ない。

    席を書く前のバージョンが書いた行（`seat` のキーが無い）は、役と計画が要求と同じなら通し、起動の後に席を書く。更新の前から実行中の行の
    続き（回答の後の next・/goal の入れ直し・中止）を断らないためである。頼む側は実行中の行を読んでから続きの起動を打つ。
    """
    entry = entries.get(name)
    if entry is not None and "seat" not in entry and all(entry.get(k) == req.get(k) for k in ("role", "plan")):
        return None
    if entry is None or entry.get("seat") != seat:
        held = None if entry is None else entry.get("seat")
        return Outcome(False, f"{name} の席 {seat} は登録簿に無い（登録簿の席: {held}）。--seated を外して上限の判定を受ける", "refused")
    return None


def _preflight(ctx: Context, name: str, entry: Mapping[str, Any]) -> Optional[Outcome]:
    """対象を止める前に確かめられる起動の条件（導入のエントリポイントと作業ディレクトリ）。通れば None。"""
    if not identity.entrypoint(ctx.plugin_root).is_file():
        return Outcome(False, hub_decide.started(hub_decide.Started(False, False, False), name).reason, "precondition")
    cwd = _workdir(ctx, entry)
    if not cwd.is_dir():
        return Outcome(False, f"作業ディレクトリ {cwd} が無い。正しい --cwd で打ち直す", "precondition")
    return None


def _workdir(ctx: Context, entry: Mapping[str, Any]) -> Path:
    """起動する作業ディレクトリ。登録簿の行の `cwd`、無ければ状態の保存先。"""
    return Path(str(entry.get("cwd") or ctx.state_dir))


def restart(ctx: Context, name: str) -> Outcome:
    """止める前に上限とバージョンを判定し、通ったときだけ止めてから起動する。"""
    entries = registry.read_registry(hub_dir(ctx.state_dir))
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
    limit = _max_named(ctx)
    lanes = hub_decide.Lanes(_own_count(running), limit, target, starts.count, ctx.max_starts)
    decided = hub_decide.restart(lanes)
    if not decided.ok:
        rest = hub_decide.others_running(lanes)
        return _cap_refusal(decided.reason, rest, limit) if rest + 1 > limit else Outcome(False, decided.reason, "refused")
    pending = ctx.pending()
    unstopped = _await_exit(ctx, name, target)
    if unstopped is not None:
        return unstopped
    entry = {**entry, "starts": starts.bumped().as_json(), "version": ctx.version}
    got = _launch(ctx, name, entry)
    entries[name] = entry
    registry.save_registry(hub_dir(ctx.state_dir), entries)
    return _with_pending(got, pending)


def _await_exit(ctx: Context, name: str, running: bool) -> Optional[Outcome]:
    """動いていれば止めて終わるまで待つ。止められなければその結果、止めた・動いていなければ None。"""
    if not running:
        return None
    stopped = stop(ctx, name)
    return None if stopped.ok else stopped


def _refused(d: hub_decide.Decision, cause: str) -> Optional[Outcome]:
    """断る判断なら、その理由と `cause` の結果。進めるなら None。"""
    return None if d.ok else Outcome(False, d.reason, cause)


def _own_count(running: list[hub_decide.Running]) -> int:
    """動いているラッパーのうち、この NDF のもの（バージョンを持つもの）の数。"""
    return len([r for r in running if r.version is not None])


def _with_pending(got: Outcome, pending: Mapping[str, Any]) -> Outcome:
    """再起動の結果へ、待ちを抱えた実行の件数（読めなければその理由）と一覧を足す。"""
    note = pending.get("note")
    if note:
        summary = f"{got.summary}。待ちの一覧を読めない: {note}"
    else:
        summary = f"{got.summary}。待ちを抱えた実行 {len(pending.get('runs') or [])} 件"  # noqa: NDF501 外部コマンド: 読めなかった一覧は空
    return Outcome(got.ok, summary, got.cause, [*got.items, {"pending": pending.get("runs")}])


def stop(ctx: Context, name: str) -> Outcome:
    """tmux のセッションを止め、ラッパーの錠が空くまで待つ。"""
    lock = registry.wrapper_dir(ctx.state_dir, name) / registry.LOCK_FILE
    if not identity.lock_held(lock) and not ctx.tmux.has(name):
        return Outcome(True, f"{name} は動いていない")
    killed = ctx.tmux.kill(name)
    if not _wait_until(ctx, ctx.stop_wait, lambda: not identity.lock_held(lock)):
        return Outcome(False, f"{name} が {ctx.stop_wait.seconds:g} 秒で終わらない（tmux: {killed.detail}）", "wait_timeout")
    return Outcome(True, f"{name} を止めた")


def spawn_env(environ: Mapping[str, str], state_dir: Path, name: str, entry: Mapping[str, Any]) -> dict[str, str]:
    """名前つきのラッパーへ渡す環境（子のセッションの環境に、名前・役割・計画の名前を足す）。

    計画の名前（`NDF_RELAY_PLAN`）があれば、ラッパーは子の質問を計画のリクエストとして載せる（`lane_question`。#3035）。
    """
    env = session_env.child_env(environ, state_dir)
    extra = {"NDF_RELAY_NAME": name, "NDF_RELAY_SPAWNED": "1", "NDF_RELAY_ROLE": entry.get("role"), "NDF_RELAY_PLAN": entry.get("plan")}
    env.update({k: str(v) for k, v in extra.items() if v})
    return env


def _launch(ctx: Context, name: str, entry: Mapping[str, Any]) -> Outcome:
    refused = _preflight(ctx, name, entry)
    if refused is not None:
        return refused
    ndf = identity.entrypoint(ctx.plugin_root)
    env = spawn_env(ctx.environ, ctx.state_dir, name, entry)
    cwd = _workdir(ctx, entry)
    args = [str(a) for a in entry.get("args") or []]  # noqa: NDF501 利用者の入力: 引数が無ければ claude の既定
    started = ctx.tmux.start(name, cwd, [str(ndf), "relay", "run", "--name", name, "--", *args], env)
    if not started.ok:
        return Outcome(False, f"{name} の tmux のセッションを作れない: {started.detail}", "precondition")
    return _confirm(ctx, name)


def _confirm(ctx: Context, name: str) -> Outcome:
    """ラッパーが錠を取り、tmux のセッションが残るまで待つ。"""
    lock = registry.wrapper_dir(ctx.state_dir, name) / registry.LOCK_FILE
    _wait_until(ctx, ctx.started, lambda: identity.lock_held(lock))
    facts = hub_decide.Started(True, ctx.tmux.has(name), identity.lock_held(lock))
    got = hub_decide.started(facts, name)
    return Outcome(got.ok, got.reason, "" if got.ok else "precondition", [{"name": name}])


def listing(ctx: Context) -> list[dict[str, Any]]:
    """登録簿の各行に、動いているか・ラッパーの状態（`stalled` など。#3243）と理由を足す。"""
    running = set(registry.busy_names(ctx.state_dir))
    entries = registry.read_registry(hub_dir(ctx.state_dir))
    out = []
    # 登録簿の行と、登録簿に無く錠を持つラッパー（ハブが数えるラッパーをすべて載せる。#3989 の決定 3）
    for name in sorted(set(entries) | set(running)):
        state = registry.read_state(registry.wrapper_dir(ctx.state_dir, name))
        status = (state or {}).get("state") if name in running else "stopped"  # noqa: NDF501 ファイル: 状態が無ければ起動の途中
        row = {"name": name, "running": name in running, "state": status, "reason": (state or {}).get("reason")}  # noqa: NDF501 ファイル: 状態が無ければ理由も無い
        out.append({**row, **entries[name], "registered": True} if name in entries else {**row, "registered": False})
    return out


def _log(base: Path, rid: str, got: Outcome) -> None:
    signals.append_jsonl(base / LOG_FILE, {"request": rid, "ok": got.ok, "summary": got.summary, "cause": got.cause})
