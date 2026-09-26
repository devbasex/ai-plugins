#!/usr/bin/env python3
"""hook-trial.py: #1142 の決定 20 の試行 T2。hook を 1 本の Python のエントリポイントへまとめる前に、成り立ちと退行を確かめる（実験版）。

    python3 hook-trial.py collect [--work DIR]    # hook のテストを流し、hook へ渡るシェルの入力を集める
    python3 hook-trial.py parity [--work DIR]     # 1: 集めた入力のすべてを tree-sitter-bash で判定し、今の判定と比べる
    python3 hook-trial.py timing [--work DIR] [--runs N]  # 2: 今の worktree-guard.sh と 1 本のエントリポイントの所要の中央値
    python3 hook-trial.py passthrough [--work DIR]  # 3: 環境が無いとき・壊れているときに判定をせずに通す（終了コード 0・出力なし）か
    python3 hook-trial.py sdk [--work DIR] [--live]  # 4a: claude-agent-sdk が supervise_lib/claude.py の契約を満たすか
    python3 hook-trial.py bump [--work DIR]       # 4b: bump-my-version が release-steps.py の cmd_bump の書き換えを満たすか

依存は runner-trial.py と同じ形（uv の環境へ起動し直す）で解決し、宣言と lock は隣の hook-trial/ にある（extra は
hook・sdk・bump）。環境は ~/.cache/ndf/venv/hook-trial-<extra> に置く（NDF_DEPS_VENV を接頭辞に変えられる）。
作業ファイルは --work（既定 ~/.cache/ndf/hook-trial）に置く。
結果は lib/step_result.py の形の 1 行の JSON。終了コード 0 = ok / 1 = 確かめたことが成り立たない / 3 = 前提が無い。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parent / "hook-trial"
SCRIPTS = HERE.parents[1]
REPO = HERE.parents[4]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(PROJECT))
from step_result import emit, result  # noqa: E402

TOOL = "hook-trial"
EXTRA = {"parity": "hook", "timing": "hook", "passthrough": "hook", "sdk": "sdk", "bump": "bump"}
TOP = {"hook": "tree_sitter_bash", "sdk": "claude_agent_sdk", "bump": "bumpversion"}


def venv_of(extra: str) -> str:
    return (os.environ.get("NDF_DEPS_VENV") or str(Path.home() / ".cache/ndf/venv/hook-trial")) + f"-{extra}"


def deps_trial():
    spec = importlib.util.spec_from_file_location("deps_trial", HERE.parent / "deps-trial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reexec_into(extra: str) -> None:
    """extra のパッケージを import できる環境で動いていることを保証する。無ければ uv の環境へ起動し直す。"""
    if importlib.util.find_spec(TOP[extra]):
        return
    if os.environ.get("NDF_DEPS_REEXEC"):
        emit(result(TOOL, "stopped", f"uv の環境へ起動し直したが {TOP[extra]} を import できない"), 3)
    dt = deps_trial()
    uv = dt.find_uv() or dt.install_uv()
    if not uv:
        emit(result(TOOL, "stopped", f"uv を入れられない（{dt.UV_VERSION}）"), 3)
    env = dict(os.environ, NDF_DEPS_REEXEC="1", UV_PROJECT_ENVIRONMENT=venv_of(extra))
    argv = [uv, "run", "--quiet", "--frozen", "--project", str(PROJECT), "--extra", extra,
            "python", str(HERE), *sys.argv[1:]]
    os.execve(uv, argv, env)


# --- collect ---------------------------------------------------------------

TEST_DIRS = ["plugins/ndf/skills/worktree/tests", "plugins/ndf/scripts/tests", "scripts/tests/test_agy_install_hooks.py"]
CAPTURE_WRAPPER = r'''
# --- T2 の入力の採取（作業ディレクトリの複製だけに足す） ---
eval "$(declare -f wt_extract_write_target | sed '1s/^wt_extract_write_target/__t2_orig_wewt/')"
wt_extract_write_target() {
  if [ -n "${NDF_T2_CAP:-}" ]; then
    { printf 'R\0%s\0%s\0' "$PWD" "$#"; printf '%s\0' "$@"; } >> "$NDF_T2_CAP"
  fi
  __t2_orig_wewt "$@"
}
'''


def cmd_collect(a) -> None:
    import shutil
    work = Path(a.work)
    copy = work / "scripts-copy"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.rmtree(work / "cap", ignore_errors=True)
    shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("tests", "experimental", "__pycache__"), symlinks=True)
    with open(copy / "lib" / "worktree-common.sh", "a", encoding="utf-8") as f:
        f.write(CAPTURE_WRAPPER)
    (work / "cap").mkdir(parents=True)
    env = dict(os.environ, NDF_T2_REPO=str(REPO), NDF_T2_WORK=str(work),
               PYTHONPATH=os.pathsep.join([str(PROJECT), os.environ.get("PYTHONPATH", "")]))
    cmd = ["uv", "run", "--project", "plugins/playwright-kit/skills/playwright-kit-ops", "--with", "pytest",
           "--with", "pytest-xdist", "pytest", "-p", "ht_capture", *TEST_DIRS, "-q", "-n", "4", "-p", "no:cacheprovider"]
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True)
    tail = (p.stdout or p.stderr).strip().splitlines()[-1:] or [""]
    inputs = gather(work)
    (work / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=1), encoding="utf-8")
    status = "ok" if p.returncode == 0 else "stopped"
    emit(result(TOOL, status, f"hook のテストの入力を集めた（{tail[0]}）", [],
                {"write_target": len(inputs["write_target"]), "bash": len(inputs["bash"]),
                 "inputs": str(work / "inputs.json")}), 0 if status == "ok" else 1)


def gather(work: Path) -> dict:
    wt: dict = {}
    for p in sorted((work / "cap").glob("wt.*.cap")):
        parts = p.read_bytes().split(b"\0")
        i = 0
        while i < len(parts) - 1 and parts[i] == b"R":
            n = int(parts[i + 2])
            args = [x.decode(errors="replace") for x in parts[i + 3:i + 3 + n]]
            i += 3 + n
            wt.setdefault((args[0] if args else "", args[1] if len(args) > 1 else ""), True)
    tok: dict = {}
    for p in sorted((work / "cap").glob("token.*.jsonl")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            try:
                d = json.loads(r.get("input") or "")
            except ValueError:
                continue
            ti = d.get("tool_input") if isinstance(d, dict) else None
            if d.get("tool_name") != "Bash" or not isinstance(ti, dict) or not isinstance(ti.get("command"), str):
                continue
            tok.setdefault((ti["command"], r.get("max") or "5"), bool(ti.get("run_in_background")))
    return {"write_target": [{"command": c, "base": b} for (c, b) in sorted(wt)],
            "bash": [{"command": c, "max": m, "background": bg} for (c, m), bg in sorted(tok.items())]}


# --- parity ----------------------------------------------------------------

LIB = SCRIPTS / "lib" / "worktree-common.sh"
BATCH_WT = r'''set -uo pipefail
. "$1"
while IFS= read -r -d '' cmd && IFS= read -r -d '' base; do
  out=$(wt_extract_write_target "$cmd" "$base"); rc=$?
  printf '%s\0%s\0' "$rc" "$out"
done
'''
BATCH_PLAN = r'''source <(sed -n '/^split_commands() {/,/^}/p;/^plan_command() {/,/^}/p' "$1")
while IFS= read -r -d '' cmd; do
  if plan_command "$cmd"; then printf '1\0'; else printf '0\0'; fi
done
'''


def now_write_targets(pairs: list[tuple[str, str]]) -> list[list[str]]:
    data = b"".join(c.encode() + b"\0" + b.encode() + b"\0" for c, b in pairs)
    p = subprocess.run(["bash", "-c", BATCH_WT, "batch", str(LIB)], input=data, capture_output=True,
                       env=dict(os.environ, LC_ALL="C"))
    parts = p.stdout.split(b"\0")
    return [[ln for ln in parts[2 * k + 1].decode(errors="replace").splitlines() if ln] for k in range(len(pairs))]


def now_plan(cmds: list[str]) -> list[bool]:
    data = b"".join(c.encode() + b"\0" for c in cmds)
    p = subprocess.run(["bash", "-c", BATCH_PLAN, "batch", str(SCRIPTS / "token-guard.sh")], input=data,
                       capture_output=True)
    return [x == b"1" for x in p.stdout.split(b"\0")[:len(cmds)]]


def uniq(xs):
    return sorted(set(xs))


def cmd_parity(a) -> None:
    import ht_shparse as sp
    import ht_shsleep as ss
    spec = importlib.util.spec_from_file_location("token_guard_sleep", SCRIPTS / "lib" / "token_guard_sleep.py")
    tgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tgs)
    work = Path(a.work)
    try:
        inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
    except OSError:
        emit(result(TOOL, "stopped", "入力が無い（先に collect を流す）"), 3)
    pairs = [(r["command"], r["base"]) for r in inputs["write_target"]]
    pairs += [(r["command"], "") for r in inputs["bash"] if (r["command"], "") not in set(pairs)]
    cmds = sorted({c for c, _ in pairs})
    limits = {r["command"]: float(r["max"]) for r in inputs["bash"]}
    items, errors = [], 0
    for (c, b), got in zip(pairs, now_write_targets(pairs)):
        new = sp.write_targets(c, b)
        errors += sp.has_error(c)
        if uniq(got) != uniq(new):
            items.append({"kind": "write_target", "name": c, "result": "differs", "base": b,
                          "now": uniq(got), "tree_sitter": uniq(new)})
    for c, got in zip(cmds, now_plan(cmds)):
        new = ss.plan_command(c)
        if got != new:
            items.append({"kind": "plan_command", "name": c, "result": "differs", "now": got, "tree_sitter": new})
    for c in cmds:
        lim = limits.get(c, 5.0)
        try:
            got = tgs.should_deny(c, lim)
        except Exception:  # 今の実装も読めないコマンドは通す
            got = False
        try:
            new = ss.sleep_deny(c, lim)
        except Exception:
            new = False
        if got != new:
            items.append({"kind": "sleep_deny", "name": c, "result": "differs", "limit": lim,
                          "now": got, "tree_sitter": new})
    out = work / "parity.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {k: sum(1 for i in items if i["kind"] == k) for k in ("write_target", "plan_command", "sleep_deny")}
    metrics = {"write_target_inputs": len(pairs), "commands": len(cmds), "parse_errors": errors,
               "differs": kinds, "list": str(out)}
    status = "ok" if not items else "stopped"
    emit(result(TOOL, status, f"入力 {len(pairs)} 件・コマンド {len(cmds)} 件のうち食い違い {len(items)} 件",
                items, metrics), 0 if status == "ok" else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "parity", "timing", "passthrough", "sdk", "bump"):
        s = sub.add_parser(name)
        s.add_argument("--work", default=str(Path.home() / ".cache/ndf/hook-trial"))
        if name == "timing":
            s.add_argument("--runs", type=int, default=40)
        if name == "sdk":
            s.add_argument("--live", action="store_true", help="claude を実際に 1 回ずつ呼んで結果の形と費用を比べる")
    a = ap.parse_args()
    Path(a.work).mkdir(parents=True, exist_ok=True)
    if a.cmd in EXTRA:
        reexec_into(EXTRA[a.cmd])
    mod = {"timing": "ht_checks", "passthrough": "ht_checks", "sdk": "ht_sdk", "bump": "ht_bump"}.get(a.cmd)
    if mod:
        getattr(importlib.import_module(mod), "cmd_" + a.cmd)(a)
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
