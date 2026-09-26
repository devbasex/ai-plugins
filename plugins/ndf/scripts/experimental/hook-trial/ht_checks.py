"""hook-trial.py の timing（2）・passthrough（3）を持つ。sdk と bump（4）は ht_sdk.py・ht_bump.py。"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from step_result import emit, result

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1]
REPO = HERE.parents[4]
GUARD = SCRIPTS / "worktree-guard.sh"
HOOK = HERE / "ht_hook.py"
TOOL = "hook-trial"
# hooks/*.json の command の形の案: 環境の python が無ければ sh が 0 で抜ける（決定 20 の、判定をせずに通す形）
LAUNCH = '[ -x "$0" ] || exit 0; exec "$0" "$1"'


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(work: Path) -> Path:
    repo = work / "timing" / "repo"
    if (repo / ".git").is_dir():
        return repo
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("a\n", encoding="utf-8")
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "worktree.json").write_bytes((REPO / ".ndf" / "worktree.json").read_bytes())
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t2", "-c", "user.email=t2@example.invalid", "commit", "-q", "-m", "init")
    git(repo, "worktree", "add", "-q", ".worktrees/x", "-b", "x")
    return repo


def payloads(repo: Path) -> dict:
    base = {"hook_event_name": "PreToolUse", "session_id": "t2-timing", "cwd": str(repo)}
    return {
        "bash_write": {**base, "tool_name": "Bash",
                       "tool_input": {"command": "cd sub 2>/dev/null; sed -i 's/a/b/' README.md && echo ok > out.txt"}},
        "bash_read": {**base, "tool_name": "Bash", "tool_input": {"command": "git status --short | head -5"}},
        "edit": {**base, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "README.md"),
                                                               "old_string": "a", "new_string": "b"}},
    }


def once(argv, data: bytes, env) -> tuple[float, subprocess.CompletedProcess]:
    t = time.perf_counter()
    p = subprocess.run(argv, input=data, capture_output=True, env=env)
    return (time.perf_counter() - t) * 1000, p


def cmd_timing(a) -> None:
    work = Path(a.work)
    repo = make_repo(work)
    tmp = work / "timing" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE="1")
    py = sys.executable
    variants = {
        "worktree-guard.sh": ["bash", str(GUARD)],
        "python 直": [py, str(HOOK)],
        "sh + python": ["sh", "-c", LAUNCH, py, str(HOOK)],
    }
    bases = {"python -c pass": [py, "-c", "pass"],
             "python + import tree_sitter_bash": [py, "-c", "import tree_sitter, tree_sitter_bash"]}
    rows, items = {}, []
    # steady: 同じセッションで打ち直す（状態ファイルが温まり、今の guard は同じパスの案内を 2 回目から出さない）
    # fresh: 1 回ごとに新しいセッション（状態を解決し直し、案内まで出す）
    keys = []
    for mode in ("steady", "fresh"):
        for pname, pl in payloads(repo).items():
            key = f"{mode} / {pname}"
            keys.append(key)
            outs = {}
            for name, argv in variants.items():
                for _ in range(3):  # 温める
                    once(argv, json.dumps(pl).encode(), env)
            samples: dict[str, list[float]] = {k: [] for k in variants}
            for i in range(a.runs):
                data = json.dumps({**pl, "session_id": f"t2-{mode}-{i}"} if mode == "fresh" else pl).encode()
                for name, argv in variants.items():  # 交互に流して、機械の揺れを両方へ等しく載せる
                    ms, p = once(argv, data, env)
                    samples[name].append(ms)
                    outs[name] = (p.returncode, bool(p.stdout.strip()))
            for name, xs in samples.items():
                rows[f"{key} / {name}"] = {"median_ms": round(statistics.median(xs), 1),
                                          "p90_ms": round(sorted(xs)[int(len(xs) * 0.9) - 1], 1),
                                          "runs": len(xs), "exit": outs[name][0], "notice": outs[name][1]}
    for name, argv in bases.items():
        xs = [once(argv, b"", env)[0] for _ in range(a.runs)]
        rows[name] = {"median_ms": round(statistics.median(xs), 1), "runs": len(xs)}
    worse = []
    for pname in keys:
        now = rows[f"{pname} / worktree-guard.sh"]["median_ms"]
        for name in ("python 直", "sh + python"):
            new = rows[f"{pname} / {name}"]["median_ms"]
            items.append({"kind": "timing", "name": f"{pname} / {name}", "result": "faster" if new <= now else "slower",
                          "now_ms": now, "new_ms": new})
            if new > now:
                worse.append(f"{pname} / {name}")
        # 案内の有無が同じか（同じ仕事をしているか）。steady の今の guard は 2 回目から案内を出さないので比べない
        n0 = rows[f"{pname} / worktree-guard.sh"]["notice"]
        for name in (("python 直", "sh + python") if pname.startswith("fresh") else ()):
            if rows[f"{pname} / {name}"]["notice"] != n0:
                worse.append(f"{pname} / {name} の案内の有無が今と違う")
    (work / "timing.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    status = "stopped" if worse else "ok"
    emit(result(TOOL, status, f"hook 1 回の所要の中央値（{a.runs} 回ずつ）" + ("。悪くなった: " + " / ".join(worse) if worse else ""),
                items, rows), 0 if status == "ok" else 1)


def cmd_passthrough(a) -> None:
    work = Path(a.work)
    repo = make_repo(work)
    data = json.dumps(payloads(repo)["bash_write"]).encode()
    env = dict(os.environ, TMPDIR=str(work / "timing" / "tmp"))
    missing = str(work / "no-such-venv" / "bin" / "python")
    sys_py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"
    probe = subprocess.run([sys_py, "-I", "-c", "import tree_sitter_bash"], capture_output=True)
    cases = {
        "環境が無い（sh + python の形）": ["sh", "-c", LAUNCH, missing, str(HOOK)],
        "環境が無い（python 直の形）": [missing, str(HOOK)],
        "tree-sitter を import できない python": [sys_py, "-I", str(HOOK)] if probe.returncode else None,
        "環境がある（対照）": [sys.executable, str(HOOK)],
    }
    items, bad = [], []
    for name, argv in cases.items():
        if argv is None:
            items.append({"kind": "passthrough", "name": name, "result": "skipped",
                          "reason": f"{sys_py} が tree_sitter_bash を import できる"})
            continue
        try:
            p = subprocess.run(argv, input=data, capture_output=True, env=env)
            rc, out, err = p.returncode, p.stdout.decode().strip(), p.stderr.decode().strip()
        except OSError as e:
            rc, out, err = 127, "", str(e)
        passed = rc == 0 and not out
        want = name != "環境がある（対照）"
        items.append({"kind": "passthrough", "name": name, "result": "passthrough" if passed else "not_passthrough",
                      "exit": rc, "stdout": out[:120], "stderr": err[:120]})
        if want and not passed and "python 直" not in name:
            bad.append(name)
        if not want and passed:
            bad.append(name + "（案内が出ない）")
    direct = next(i for i in items if "python 直" in i["name"])
    status = "stopped" if bad else "ok"
    summary = ("環境が無いときも壊れているときも判定をせずに通す（hook の command は sh で環境の有無を見る形にする。"
               f"python を直に指す形は終了コード {direct['exit']} で終わる）") if not bad else "判定をせずに通す形にならない: " + " / ".join(bad)
    emit(result(TOOL, status, summary, items), 0 if status == "ok" else 1)
