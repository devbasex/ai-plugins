"""4a: claude-agent-sdk が supervise_lib/claude.py の claude_cmd・call_claude の今の契約を満たすか（hook-trial.py sdk）。

確かめる 4 つ:
- 引数: claude_cmd の 3 つの形（最小構成・work の Tool と Serena・Skill を回す full と --resume）を SDK の
  ClaudeAgentOptions で組み、SDK が CLI へ渡す引数（cli_path を引数を控える偽物にして読む）が今の引数を含むか
- 結果の読み取り: ResultMessage が call_claude の読む値（result・is_error・usage・modelUsage・total_cost_usd・
  num_turns・session_id と所要）を持つか。--live なら同じ依頼を CLI と SDK で --runs 回ずつ流して値を比べる
- 費用: SDK の下の CLI は CLAUDE_AGENT_SDK_VERSION（SDK が必ず立てる）を見て会話のタイトルを作る呼び出し
  （source=generate_session_title）を 1 回足す。usage は本体の呼び出しだけ、modelUsage と費用は両方を数える。
  env の CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 で止まるかを、立てない形と並べて測る
- 打ち切り: 応答しない CLI を asyncio の打ち切りで止めたとき、子のプロセスが残らないか
- 差し替え: 今のテストの偽物（NDF_SUPERVISE_CLAUDE="<python> <偽物>"。標準入力を EOF まで読んで JSON を 1 つ書く）は、
  SDK の initialize の制御要求に答えないため双方が待ち合って止まる。stream-json の往復を話す偽物（cli_path）と
  query(transport=...) の差し替えが動くかを確かめる
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from step_result import emit, result

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
TOOL = "hook-trial"
RECORDER = '''#!/bin/sh
printf '%s\\n' "$@" > "{out}"
exit 1
'''
SLOW = '''#!/bin/sh
exec sleep 120
'''
NO_TITLE = {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}
FAKE_STREAM = '''import json, sys
if "-v" in sys.argv:
    print("2.1.283 (Claude Code)")
    sys.exit(0)
for line in sys.stdin:
    m = json.loads(line)
    if m.get("type") == "control_request":
        r = {"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"], "response": {}}}
    elif m.get("type") == "user":
        r = {"type": "result", "subtype": "success", "result": "ok", "is_error": False, "usage": {}, "total_cost_usd": 0,
             "num_turns": 1, "session_id": "s", "duration_ms": 1, "duration_api_ms": 1}
    else:
        continue
    print(json.dumps(r), flush=True)
    if r["type"] == "result":
        break
'''
FAKE = '''import json, sys
sys.stdin.read()
print(json.dumps({"result": "ok", "is_error": False, "usage": {}, "total_cost_usd": 0, "num_turns": 1, "session_id": "s"}))
'''


def options(kind: str, system: str, cwd: str, cli: str | None, resume: str | None = None, env: dict | None = None):
    from claude_agent_sdk import ClaudeAgentOptions
    from supervise_lib import claude as cl

    common = {"cwd": cwd, "cli_path": cli, "env": env or {}}
    if os.environ.get("NDF_SUPERVISE_MODEL"):
        common["model"] = os.environ["NDF_SUPERVISE_MODEL"]
    if kind == "full":
        common.pop("model", None)
        return ClaudeAgentOptions(system_prompt={"type": "preset", "preset": "claude_code", "append": system},
                                  allowed_tools=cl.FULL_TOOLS.split(","), permission_mode="acceptEdits",
                                  resume=resume, setting_sources=["user", "project", "local"], **common)
    base = dict(system_prompt=system, setting_sources=[], strict_mcp_config=True,
                extra_args={"no-session-persistence": None, "disable-slash-commands": None}, **common)
    if kind == "minimal":
        return ClaudeAgentOptions(tools=[], **base)
    tools = cl.WORK_TOOLS.split(",")
    return ClaudeAgentOptions(tools=tools, allowed_tools=tools + ["mcp__serena"], permission_mode="acceptEdits",
                              add_dirs=[cwd], mcp_servers=cl.SERENA_MCP["mcpServers"], **base)


async def drain(prompt: str, opts, timeout: float, transport=None):
    from claude_agent_sdk import query

    async def go():
        last = None
        async for m in query(prompt=prompt, options=opts, transport=transport):
            last = m
        return last
    return await asyncio.wait_for(go(), timeout)


def pairs(argv: list[str]) -> dict:
    """引数の並びを {フラグ: 値} にする（値の無いフラグは True）。"""
    out, i = {}, 0
    alias = {"--allowedTools": "--allowed-tools"}  # CLI はどちらの綴りも受ける
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and "=" in a:  # SDK は `--setting-sources=` `--resume=<id>` の形で渡す
            k, v = a.split("=", 1)
            out[alias.get(k, k)], i = v, i + 1
            continue
        a = alias.get(a, a)
        if a.startswith("--"):
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[a], i = argv[i + 1], i + 2
                continue
            out[a] = True
        i += 1
    return out


def check_args(work: Path, items: list, bad: list) -> None:
    from supervise_lib import claude as cl

    for kind in ("minimal", "work", "full"):
        rec = work / f"argv-{kind}.txt"
        cli = work / f"rec-{kind}.sh"
        cli.write_text(RECORDER.format(out=rec), encoding="utf-8")
        cli.chmod(0o755)
        try:
            asyncio.run(drain("hi", options(kind, "SYS", str(work), str(cli), "sess-1" if kind == "full" else None), 20))
        except Exception:  # 偽物は応答しない。引数だけを読む
            pass
        sdk = pairs(rec.read_text(encoding="utf-8").splitlines()) if rec.exists() else {}
        now = pairs(cl.claude_cmd("SYS", cl.WORK_TOOLS if kind == "work" else None, str(work), full=kind == "full",
                                  serena=kind == "work", resume="sess-1" if kind == "full" else None)[1:])
        missing, differ = [], []
        for k, v in now.items():
            if k in ("--output-format", "--print"):
                continue  # SDK は stream-json で話す（結果は ResultMessage で読む）
            if k not in sdk:
                missing.append(k)
            elif k in ("--mcp-config",):
                if json.loads(sdk[k]).get("mcpServers") != json.loads(v).get("mcpServers"):
                    differ.append(k)
            elif k in ("--tools", "--allowed-tools"):
                if set(str(sdk[k]).split(",")) != set(str(v).split(",")):
                    differ.append(f"{k}（{v} / {sdk[k]}）")
            elif sdk[k] != v:
                differ.append(f"{k}（{v!r} / {sdk[k]!r}）")
        extra = sorted(k for k in sdk if k not in now)
        ok = not missing and not differ
        items.append({"kind": "sdk_args", "name": kind, "result": "same" if ok else "differs",
                      "missing": missing, "differs": differ, "sdk_only": extra})
        if not ok:
            bad.append(f"引数（{kind}）")


def check_timeout(work: Path, items: list, bad: list) -> None:
    cli = work / "slow.sh"
    cli.write_text(SLOW, encoding="utf-8")
    cli.chmod(0o755)
    t = time.time()
    try:
        asyncio.run(drain("hi", options("minimal", "SYS", str(work), str(cli)), 2))
        how = "終わった"
    except asyncio.TimeoutError:
        how = "打ち切り"
    except Exception as e:
        how = type(e).__name__
    time.sleep(0.5)
    left = subprocess.run(["pgrep", "-f", "exec sleep 120|sleep 120"], capture_output=True, text=True).stdout.split()
    for pid in left:
        subprocess.run(["kill", pid], capture_output=True)
    ok = how == "打ち切り" and not left
    items.append({"kind": "sdk_timeout", "name": "応答しない CLI を 2 秒で打ち切る", "result": "ok" if ok else "left",
                  "how": how, "seconds": round(time.time() - t, 1), "left_processes": len(left)})
    if not ok:
        bad.append("打ち切り")


def check_fake(work: Path, items: list, bad: list) -> None:
    from claude_agent_sdk._internal.transport import Transport

    class ReplyTransport(Transport):
        """プロセスを起こさずに initialize へ答え、user を 1 つ受けて result を 1 つ返す。"""

        def __init__(self):
            self.box: asyncio.Queue = asyncio.Queue()
            self.ready = False

        async def connect(self):
            self.ready = True

        async def write(self, data):
            for line in data.splitlines():
                m = json.loads(line)
                if m.get("type") == "control_request":
                    await self.box.put({"type": "control_response", "response": {
                        "subtype": "success", "request_id": m["request_id"], "response": {}}})
                elif m.get("type") == "user":
                    await self.box.put({"type": "result", "subtype": "success", "result": "ok", "is_error": False,
                                        "usage": {}, "total_cost_usd": 0, "num_turns": 1, "session_id": "s",
                                        "duration_ms": 1, "duration_api_ms": 1})
                    await self.box.put(None)

        async def read_messages(self):
            while (m := await self.box.get()) is not None:
                yield m

        async def close(self):
            self.ready = False

        def is_ready(self):
            return self.ready

        async def end_input(self):
            pass

    def wrap(name: str, body: str) -> str:
        py = work / f"{name}.py"
        py.write_text(body, encoding="utf-8")
        sh = work / f"{name}.sh"
        sh.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(py))} \"$@\"\n", encoding="utf-8")
        sh.chmod(0o755)
        return str(sh)

    def attempt(cli: str | None, transport=None, timeout: float = 20) -> str:
        try:
            m = asyncio.run(drain("hi", options("minimal", "SYS", str(work), cli), timeout, transport))
            return type(m).__name__ if m is not None else "応答なし"
        except asyncio.TimeoutError:
            return f"TimeoutError（{timeout:g} 秒）"
        except Exception as e:
            return f"{type(e).__name__}: {str(e)[:120]}"

    got = {"now": attempt(wrap("fake_claude", FAKE), timeout=10),
           "stream_json": attempt(wrap("fake_stream", FAKE_STREAM)),
           "transport": attempt(None, ReplyTransport())}
    for name, how in got.items():
        ok = how == "ResultMessage"
        items.append({"kind": "sdk_fake", "name": name, "result": "works" if ok else "fails", "got": how})
    if got["stream_json"] != "ResultMessage" and got["transport"] != "ResultMessage":
        bad.append("テストの偽物の CLI（stream-json の偽物も transport の差し替えも動かない）")


def check_live(work: Path, items: list, bad: list, runs: int) -> None:
    from statistics import median

    from supervise_lib import claude as cl

    model = os.environ.get("NDF_SUPERVISE_MODEL") or "haiku"
    os.environ["NDF_SUPERVISE_MODEL"] = model
    prompt, system = "Reply with exactly one word: ok", "You answer tersely."

    def model_in(mu):
        return sum((v or {}).get("inputTokens", 0) for v in (mu or {}).values())

    def cli_once() -> dict:
        t = time.time()
        now = cl.call_claude(system, prompt, None, str(work), 120)
        return {"ok": now["ok"], "text": now["text"], "usage": now["usage"], "model_usage": now["model_usage"],
                "cost": now["cost"], "turns": now["turns"], "session": now["session"], "seconds": round(time.time() - t, 1)}

    def sdk_once(env: dict | None) -> dict:
        t = time.time()
        m = asyncio.run(drain(prompt, options("minimal", system, str(work), None, env=env), 120))
        return {"ok": not m.is_error, "text": m.result, "usage": m.usage, "model_usage": m.model_usage,
                "cost": m.total_cost_usd, "turns": m.num_turns, "session": m.session_id, "seconds": round(time.time() - t, 1)}

    rows: dict[str, list] = {"cli": [], "sdk": [], "sdk_no_title": []}
    try:
        for _ in range(runs):
            rows["cli"].append(cli_once())
            rows["sdk"].append(sdk_once(None))
            rows["sdk_no_title"].append(sdk_once(NO_TITLE))
    except Exception as e:
        items.append({"kind": "sdk_live", "name": model, "result": "fails", "error": str(e)[:200]})
        bad.append("実際の呼び出し")
        return
    keys = ("ok", "text", "usage", "model_usage", "cost", "turns", "session")
    ref, got = rows["cli"][0], rows["sdk_no_title"][0]
    same_shape = all(bool(got[k]) == bool(ref[k]) for k in keys)
    items.append({"kind": "sdk_live", "name": model, "result": "same_shape" if same_shape else "differs",
                  "now": {k: bool(ref[k]) for k in keys}, "sdk": {k: bool(got[k]) for k in keys},
                  "sdk_usage_keys": sorted(got["usage"] or {})})
    if not same_shape:
        bad.append("結果の読み取り")
    # 費用: usage（帳簿が読む値）と modelUsage の入力が合うか・CLI と同じ依頼で費用が変わらないか（中央値）
    base = median(r["cost"] or 0 for r in rows["cli"])
    for name in ("cli", "sdk", "sdk_no_title"):
        rs = rows[name]
        tokens = {"runs": len(rs), "usage_in": [(r["usage"] or {}).get("input_tokens") for r in rs],
                  "model_in": [model_in(r["model_usage"]) for r in rs], "cost": [r["cost"] for r in rs],
                  "cost_ratio": round(median(r["cost"] or 0 for r in rs) / base, 2) if base else None}
        ok = tokens["usage_in"] == tokens["model_in"] and (tokens["cost_ratio"] or 0) <= 1.2
        items.append({"kind": "sdk_cost", "name": f"{model}・{name}", "result": "same" if ok else "differs",
                      "env": NO_TITLE if name == "sdk_no_title" else {}, **tokens})
        if name == "sdk_no_title" and not ok:
            bad.append(f"費用（タイトルを止めても入力 {tokens['model_in']} トークン・費用 {tokens['cost_ratio']} 倍）")


def cmd_sdk(a) -> None:
    work = Path(a.work) / "sdk"
    work.mkdir(parents=True, exist_ok=True)
    import claude_agent_sdk
    items: list = []
    bad: list = []
    check_args(work, items, bad)
    check_timeout(work, items, bad)
    check_fake(work, items, bad)
    if a.live:
        check_live(work, items, bad, a.runs)
    status = "stopped" if bad else "ok"
    emit(result(TOOL, status, (f"claude-agent-sdk {claude_agent_sdk.__version__}: " +
                               ("今の契約を満たす" if not bad else "満たさない: " + " / ".join(bad))), items,
                {"live": bool(a.live)}), 0 if status == "ok" else 1)
