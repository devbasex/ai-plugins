"""依頼の口（#2346）: 副命令 `spawn`・`ls`・`attach`・`restart` と名前つきの `stop`、端末の 2 本目の依頼、
Stop hook の起動のブロック。

頼む側は受付箱へ依頼を置いて結果を待つだけで、登録簿を書かない（I4）。一覧は作業ディレクトリと登録簿と tmux を
読んで組み立てる（読むだけ）。結果は `lib/step_result.py` の形（`tool`・`status`・`summary`・`items`・`metrics`）の
1 行の JSON（`--json`）か、人の読む 1 行・表で出す。step_result.py はバージョンディレクトリへ写さないため import せず、
同じ形を組む（I2）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time

from . import hub as hb
from . import tmux
from .common import PID_FILE, STOP_FILE, _write_file, env_num, read_text, relay_running, remove, stamp, write_json_atomic

import md  # noqa: E402,I001  common が lib/ を sys.path に置く

EXIT_NO_HUB = 1  # 統括役が居ない・答えない


# ---------------------------------------------------------------- 依頼


def put_request(kind: str, name: str | None, cwd: str, args: list[str], by: str) -> str:
    """受付箱へ依頼を置き、依頼の id を返す（ファイルは 0600。I8）。環境変数は載せない（決定 2）。"""
    hb.ensure_dirs()
    rid = hb.new_id()
    write_json_atomic(
        hb.hub_path(hb.REQUESTS, f"{rid}.json"),
        {"kind": kind, "name": name, "cwd": cwd, "args": args, "by": by, "at": stamp()},
    )
    return rid


def _wait_result(rid: str, seconds: float) -> dict | None:
    path = hb.hub_path(hb.RESULTS, f"{rid}.json")
    end = time.time() + seconds
    while True:
        got = hb.load_json(path)
        if isinstance(got, dict):
            remove(path)
            return got
        if time.time() >= end:
            return None
        time.sleep(0.1)


def request(kind: str, name: str | None, cwd: str, args: list[str], by: str) -> dict | None:
    """依頼を置いて結果を待つ。`NDF_RELAY_HUB_WAIT` 秒で来なければ依頼を消して None。消せなければ統括役が
    受け取った後なので、もう 1 度同じ秒数だけ待つ（決定 14）。"""
    rid = put_request(kind, name, cwd, args, by)
    seconds = hb.wait_seconds()
    got = _wait_result(rid, seconds)
    if got is not None:
        return got
    try:
        os.unlink(hb.hub_path(hb.REQUESTS, f"{rid}.json"))
        return None
    except OSError:
        return _wait_result(rid, seconds)


def code_of(res: dict) -> int:
    code = (res.get("metrics") or {}).get("exit_code")
    return code if isinstance(code, int) else (0 if res.get("status") == "ok" else 1)


def first_name(res: dict) -> str | None:
    items = res.get("items") or []
    return items[0].get("name") if items and isinstance(items[0], dict) else None


def finish(res: dict, as_json: bool) -> int:
    """結果を出して終了コードを返す。"""
    code = code_of(res)
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"relay.py: {res.get('summary', '')}", file=sys.stdout if code == 0 else sys.stderr)
    return code


def no_hub(kind: str, name, why: str) -> dict:
    return hb.outcome(kind, "stopped", why, name, "no-hub", EXIT_NO_HUB)


# ---------------------------------------------------------------- 端末の 2 本目（run から呼ぶ）


def terminal_request(args: list[str]) -> tuple[str, dict | None]:
    """統括役へ端末の 2 本目の起動を頼む。(`attach` / `refused` / `timeout`, 結果)。"""
    name = os.environ.get("NDF_RELAY_NAME") or None
    res = request("spawn", name, os.getcwd(), list(args), "terminal")
    if res is None:
        return "timeout", None
    return ("attach" if res.get("status") == "ok" else "refused"), res


# ---------------------------------------------------------------- 起動のブロック（Stop hook の mark から呼ぶ）

SPAWN_INFO = "ndf-spawn"


def spawn_blocks(text: str) -> list[tuple[str, str]]:
    """情報文字列が `ndf-spawn <名前>` の囲みの (名前, 中身)。囲みは `ndf-next` と同じ包み（`lib/md.py`）で読む（決定 6）。"""
    out = []
    for t in md.md_tokens(text):
        if t.type != "fence" or t.markup != "```":
            continue
        parts = t.info.split()
        if len(parts) == 2 and parts[0] == SPAWN_INFO:
            out.append((parts[1], t.content.strip("\n")))
    return out


def place_blocks(text: str, cwd: str, relay_dir: str) -> int:
    """応答の起動のブロックごとに依頼を置く（待たない。統括役が居なければ次の統括役が処理する）。置いた数。"""
    n = 0
    for name, body in spawn_blocks(text):
        put_request("spawn", name, cwd or os.getcwd(), hb.prompt_args(body), f"block:{relay_dir}")
        n += 1
    return n


# ---------------------------------------------------------------- spawn


def _split_dashdash(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def cmd_spawn(argv: list[str]) -> int:
    opts, rest = _split_dashdash(argv)
    as_json = "--json" in opts
    name = None
    if "--name" in opts:
        i = opts.index("--name")
        name = opts[i + 1] if i + 1 < len(opts) else None
    if not name:
        print("usage: relay.py spawn --name <名前> [--json] -- <最初の入力>", file=sys.stderr)
        return 2
    if not hb.valid_name(name):
        return finish(hb.outcome("spawn", "stopped", f"名前の形が違う（{hb.NAME_RE.pattern}）: {name}", name, hb.BAD_NAME, 3), as_json)
    if not hb.hub_running():
        return finish(no_hub("spawn", name, "統括役が居ない（端末で claude を起動すると統括役になる）"), as_json)
    res = request("spawn", name, os.getcwd(), hb.prompt_args(" ".join(rest)) if rest else [], "spawn")
    return finish(res or no_hub("spawn", name, "統括役が答えない"), as_json)


# ---------------------------------------------------------------- ls


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(name: str, w: hb.Wrapper | None, session: dict | None, pending: bool, hub_dir: str | None) -> dict:
    running = bool(w and w.running) or pending
    waiting = bool(w and w.running and w.question)
    state = "awaiting_approval" if waiting else "running" if running else "stopped"
    start = (w.start if w else None) or {}
    at = _iso(session["activity"]) if session and session.get("activity") else (w.last_at if w else None)
    return {
        "name": name,
        "state": state,
        "awaiting_approval": waiting,
        "sections": start.get("section") if isinstance(start.get("section"), int) else None,
        "cost_usd": None,  # ラッパーは費用を記録しない。0 と書かない（要求の前提 5）
        "at": at,
        "account": start.get("account") if isinstance(start.get("account"), str) else None,
        "ndf_version": start.get("plugin_version") if isinstance(start.get("plugin_version"), str) else None,
        "attached": session["attached"] if session else None,
        "hub": bool(w and w.running and hub_dir and w.dir == hub_dir),
    }


def list_items() -> list[dict]:
    ws = hb.scan()
    registry = hb.read_registry()
    sessions = tmux.sessions()
    hub_dir = hb.hub_relay_dir()
    named = hb.by_name(ws)
    names = sorted(set(registry) | {n for n, w in named.items() if w.running})
    items = []
    for n in names:
        w = named.get(n)
        session = sessions.get((registry.get(n) or {}).get("session", n))
        pending = session is not None and not (w and w.running) and n in registry
        items.append(_item(n, w, session, pending, hub_dir))
    items += [_item("-", w, None, False, hub_dir) for w in ws if w.running and not w.name]
    return items


COLUMNS = (
    ("name", "名前"),
    ("state", "状態"),
    ("awaiting_approval", "承認待ち"),
    ("sections", "区間"),
    ("cost_usd", "費用"),
    ("at", "時刻"),
    ("account", "アカウント"),
    ("attached", "結び付き"),
    ("hub", "統括役"),
)


def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def cmd_ls(argv: list[str]) -> int:
    try:
        items = list_items()
    except OSError as e:
        print(f"relay.py: 状態ディレクトリを読めない（{e}）", file=sys.stderr)
        return 2
    states = [it["state"] for it in items]
    metrics = {
        "running": states.count("running"),
        "awaiting_approval": states.count("awaiting_approval"),
        "stopped": states.count("stopped"),
        "limit": hb.max_named(),
    }
    if "--json" in argv:
        summary = f"ラッパー {len(items)} 本（動いている {metrics['running']}・承認待ち {metrics['awaiting_approval']}・止まった {metrics['stopped']}）"
        res = {"tool": "relay-ls", "status": "ok", "summary": summary, "items": items, "metrics": metrics}
        print(json.dumps(res, ensure_ascii=False))
        return 0
    rows = [[h for _, h in COLUMNS]] + [[_cell(it[k]) for k, _ in COLUMNS] for it in items]
    widths = [max(len(r[i]) for r in rows) for i in range(len(COLUMNS))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    return 0


# ---------------------------------------------------------------- stop / restart / attach


def _target(name: str) -> tuple[hb.Wrapper | None, bool, dict]:
    """(選んだ作業ディレクトリ, tmux のセッションが在るか, 登録簿の行)。"""
    registry = hb.read_registry()
    row = registry.get(name) or {}
    w = hb.by_name(hb.scan()).get(name)
    return w, tmux.has_session(row.get("session", name)), row


def stop_named(name: str) -> bool:
    """1 本を止める。停止シグナルファイルを置き、tmux のセッションを終え、5 秒で `relay.lock` が放されなければ
    プロセスの木を止める（決定 9）。止める対象が無ければ偽。"""
    w, has, row = _target(name)
    running = bool(w and w.running)
    if not running and not has:
        return False
    if running:
        _write_file(os.path.join(w.dir, STOP_FILE), b"", 0o600)
    if has:
        tmux.kill_session(row.get("session", name))
    if running:
        end = time.time() + env_num("NDF_RELAY_STOP_WAIT", 5)
        while relay_running(w.dir) and time.time() < end:
            time.sleep(0.1)
        if relay_running(w.dir):
            import procs  # 外部パッケージ（psutil）を使う。読み込みのときには import しない

            pid = (read_text(os.path.join(w.dir, PID_FILE)) or "").strip()
            if pid.isdigit():
                procs.stop_tree(int(pid))
    hb.hub_log("stopped", name, "stop")
    return True


def _not_found(kind: str, name: str) -> dict:
    return hb.outcome(kind, "stopped", f"その名前は無い: {name}", name, hb.NOT_FOUND, 3)


def cmd_stop_named(name: str, as_json: bool) -> int:
    if not stop_named(name):
        return finish(_not_found("stop", name), as_json)
    res = hb.outcome("stop", "ok", f"{name} を止めた", name, "stopped", 0)
    res["tool"] = "relay-stop"
    return finish(res, as_json)


def cmd_restart(argv: list[str]) -> int:
    as_json = "--json" in argv
    names = [a for a in argv if not a.startswith("--")]
    if len(names) != 1:
        print("usage: relay.py restart <名前> [--json]", file=sys.stderr)
        return 2
    name = names[0]
    w, has, row = _target(name)
    if not row and not (w and w.running):
        return finish(_not_found("restart", name), as_json)
    if not hb.hub_running():
        return finish(no_hub("restart", name, "統括役が居ない（端末で claude を起動すると統括役になる）"), as_json)
    stop_named(name)
    res = request("restart", name, os.getcwd(), [], "restart")
    return finish(res or no_hub("restart", name, "統括役が答えない"), as_json)


def cmd_attach(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("--")]
    if len(names) != 1:
        print("usage: relay.py attach <名前>", file=sys.stderr)
        return 2
    name = names[0]
    row = hb.read_registry().get(name) or {}
    if not tmux.has_session(row.get("session", name)):
        print(f"relay.py: その名前は無い: {name}", file=sys.stderr)
        return 3
    if not (os.isatty(0) and os.isatty(1)):
        print("relay.py: 端末でないため結び付けない", file=sys.stderr)
        return 2
    tmux.exec_attach(row.get("session", name))
    return 1
