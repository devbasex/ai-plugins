"""4a: claude-agent-sdk が supervise_lib/claude.py の claude_cmd・call_claude の今の契約を満たすか（hook-trial.py sdk）。

確かめる 4 つ:
- 引数: claude_cmd の 3 つの形（最小構成・work の Tool と Serena・Skill を回す full と --resume）を SDK の
  ClaudeAgentOptions で組み、SDK が CLI へ渡す引数（cli_path を引数を控える偽物にして読む）が今の引数を含むか
- 結果の読み取り: ResultMessage が call_claude の読む値（result・is_error・usage・modelUsage・total_cost_usd・
  num_turns・session_id と所要）を持つか。--live なら同じ依頼を CLI と SDK で 1 回ずつ流して値を比べる
- 打ち切り: 応答しない CLI を asyncio の打ち切りで止めたとき、子のプロセスが残らないか
- 差し替え: 今のテストの偽物（NDF_SUPERVISE_CLAUDE="<python> <偽物>"。標準入力を読んで JSON を 1 つ書く）が SDK の下で動くか
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
FAKE = '''import json, sys
sys.stdin.read()
print(json.dumps({"result": "ok", "is_error": False, "usage": {}, "total_cost_usd": 0, "num_turns": 1, "session_id": "s"}))
'''


def options(kind: str, system: str, cwd: str, cli: str | None, resume: str | None = None):
    from claude_agent_sdk import ClaudeAgentOptions
    from supervise_lib import claude as cl

    common = {"cwd": cwd, "cli_path": cli}
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


async def drain(prompt: str, opts, timeout: float):
    from claude_agent_sdk import query

    async def go():
        last = None
        async for m in query(prompt=prompt, options=opts):
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
    fake = work / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    wrapper = work / "fake_claude.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    try:
        m = asyncio.run(drain("hi", options("minimal", "SYS", str(work), str(wrapper)), 20))
        got = type(m).__name__ if m is not None else "応答なし"
    except Exception as e:
        got = f"{type(e).__name__}: {str(e)[:120]}"
    ok = got == "ResultMessage"
    items.append({"kind": "sdk_fake", "name": "今のテストの偽物の CLI（JSON を 1 つ書く）", "result": "works" if ok else "fails",
                  "got": got})
    if not ok:
        bad.append("テストの偽物の CLI が動かない（偽物を SDK の testing か stream-json を話す形へ直す）")


def check_live(work: Path, items: list, bad: list) -> None:
    from supervise_lib import claude as cl

    model = os.environ.get("NDF_SUPERVISE_MODEL") or "haiku"
    os.environ["NDF_SUPERVISE_MODEL"] = model
    prompt = "Reply with exactly one word: ok"
    t = time.time()
    now = cl.call_claude("You answer tersely.", prompt, None, str(work), 120)
    now_s = round(time.time() - t, 1)
    t = time.time()
    try:
        m = asyncio.run(drain(prompt, options("minimal", "You answer tersely.", str(work), None), 120))
    except Exception as e:
        items.append({"kind": "sdk_live", "name": model, "result": "fails", "error": str(e)[:200]})
        bad.append("実際の呼び出し")
        return
    sdk_s = round(time.time() - t, 1)
    got = {"ok": not m.is_error, "text": m.result, "usage": bool(m.usage), "model_usage": bool(m.model_usage),
           "cost": m.total_cost_usd, "turns": m.num_turns, "session": bool(m.session_id),
           "duration_ms": m.duration_ms, "seconds": sdk_s}
    ref = {"ok": now["ok"], "text": now["text"], "usage": bool(now["usage"]), "model_usage": bool(now["model_usage"]),
           "cost": now["cost"], "turns": now["turns"], "session": bool(now["session"]), "seconds": now_s}
    same_shape = all(bool(got[k]) == bool(ref[k]) for k in ("ok", "text", "usage", "model_usage", "cost", "turns", "session"))
    items.append({"kind": "sdk_live", "name": model, "result": "same_shape" if same_shape else "differs",
                  "now": ref, "sdk": got, "sdk_usage_keys": sorted(m.usage or {})})
    if not same_shape:
        bad.append("結果の読み取り")
    # 費用: usage（帳簿が読む値）と modelUsage の入力の合計が合うか・CLI と同じ依頼で費用が変わらないか
    def model_in(mu):
        return sum((v or {}).get("inputTokens", 0) for v in (mu or {}).values())
    tokens = {"now_usage_in": (now["usage"] or {}).get("input_tokens"), "now_model_in": model_in(now["model_usage"]),
              "sdk_usage_in": (m.usage or {}).get("input_tokens"), "sdk_model_in": model_in(m.model_usage),
              "cost_ratio": round((m.total_cost_usd or 0) / now["cost"], 2) if now["cost"] else None}
    cost_ok = tokens["sdk_usage_in"] == tokens["sdk_model_in"] and (tokens["cost_ratio"] or 0) <= 1.2
    items.append({"kind": "sdk_cost", "name": model, "result": "same" if cost_ok else "differs", **tokens})
    if not cost_ok:
        bad.append(f"費用（同じ依頼で入力が {tokens['now_model_in']} → {tokens['sdk_model_in']} トークン・"
                   f"費用 {tokens['cost_ratio']} 倍。usage の入力 {tokens['sdk_usage_in']} が modelUsage と合わない）")


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
        check_live(work, items, bad)
    status = "stopped" if bad else "ok"
    emit(result(TOOL, status, (f"claude-agent-sdk {claude_agent_sdk.__version__}: " +
                               ("今の契約を満たす" if not bad else "満たさない: " + " / ".join(bad))), items,
                {"live": bool(a.live)}), 0 if status == "ok" else 1)
