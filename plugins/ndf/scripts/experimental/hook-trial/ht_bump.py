"""4b: bump-my-version が release-steps.py の cmd_bump の書き換え（版数を持つ 15 箇所。
docs/versioning-and-distribution.md）を満たすか（hook-trial.py bump）。

HEAD の複製を 2 つ作り、片方で今の `release-steps.py bump --plugin ndf`、もう片方で bump-my-version の
`replace`（下の BUMPCFG の 15 箇所）を流して、2 つの木の差を比べる。基底が同じ上げ方（-dev.N の連番）と
基底が変わる上げ方（PATCH）の 2 つを見る。cmd_bump の手で直す箇所の報告と check-doc-staleness.py の実行は
NDF の側に残る部分なので比べない。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from step_result import emit, result

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
RELEASE = HERE.parents[1] / "release-steps.py"
TOOL = "hook-trial"
# 15 箇所（定義ファイルと更新案内の見出し 8・説明文書の本文 7）
BUMPCFG = r'''[tool.bumpversion]
current_version = "{old}"
parse = "(?P<major>\\d+)\\.(?P<minor>\\d+)\\.(?P<patch>\\d+)(?:-(?P<pre_l>dev|rc)\\.(?P<pre_n>\\d+))?"
serialize = ["{{major}}.{{minor}}.{{patch}}-{{pre_l}}.{{pre_n}}", "{{major}}.{{minor}}.{{patch}}"]
commit = false
tag = false

[[tool.bumpversion.files]]
filename = "plugins/ndf/.claude-plugin/plugin.json"
search = '"version": "{{current_version}}"'
replace = '"version": "{{new_version}}"'
[[tool.bumpversion.files]]
filename = "plugins/ndf/.claude-plugin/plugin.json"
search = "(v{{current_version}})"
replace = "(v{{new_version}})"
[[tool.bumpversion.files]]
filename = "plugins/ndf/.codex-plugin/plugin.json"
search = '"version": "{{current_version}}"'
replace = '"version": "{{new_version}}"'
[[tool.bumpversion.files]]
filename = "plugins/ndf/.codex-plugin/plugin.json"
search = "(v{{current_version}})"
replace = "(v{{new_version}})"
[[tool.bumpversion.files]]
filename = "plugins/ndf/dev.agy/plugin.json"
search = '"version": "{{current_version}}"'
replace = '"version": "{{new_version}}"'
[[tool.bumpversion.files]]
filename = "plugins/ndf/dev.agy/plugin.json"
search = "(v{{current_version}})"
replace = "(v{{new_version}})"
[[tool.bumpversion.files]]
filename = ".claude-plugin/marketplace.json"
search = "Claude Code plugin (v{{current_version}}): 8 specialized agents"
replace = "Claude Code plugin (v{{new_version}}): 8 specialized agents"
[[tool.bumpversion.files]]
filename = "plugins/ndf/README.md"
search = "## v{{current_version}} へ更新するとき"
replace = "## v{{new_version}} へ更新するとき"
[[tool.bumpversion.files]]
filename = "README.md"
search = "**NDFプラグイン v{{current_version}}**"
replace = "**NDFプラグイン v{{new_version}}**"
[[tool.bumpversion.files]]
filename = "README.md"
search = "| **ndf** | {{current_version}} |"
replace = "| **ndf** | {{new_version}} |"
[[tool.bumpversion.files]]
filename = "AGENTS.md"
search = "主要プラグインです（v{{current_version}}）"
replace = "主要プラグインです（v{{new_version}}）"
[[tool.bumpversion.files]]
filename = "plugins/ndf/README.md"
search = "（Kiro CLI用 / v{{current_version}}）"
replace = "（Kiro CLI用 / v{{new_version}}）"
[[tool.bumpversion.files]]
filename = "plugins/ndf/README.md"
search = "/plugins/cache/ai-plugins/ndf/{{current_version}}/"
replace = "/plugins/cache/ai-plugins/ndf/{{new_version}}/"
[[tool.bumpversion.files]]
filename = "plugins/ndf/README.md"
regex = true
search = "(ndf@ai-plugins\\s+installed, enabled\\s+){{current_version}}"
replace = "\\g<1>{{new_version}}"
[[tool.bumpversion.files]]
filename = "docs/versioning-and-distribution.md"
search = "| 正式版 | `{{current_major}}.{{current_minor}}.{{current_patch}}` |"
replace = "| 正式版 | `{{new_major}}.{{new_minor}}.{{new_patch}}` |"
[[tool.bumpversion.files]]
filename = "docs/versioning-and-distribution.md"
search = "`{{current_major}}.{{current_minor}}.{{current_patch}}` の次を開発するなら"
replace = "`{{new_major}}.{{new_minor}}.{{new_patch}}` の次を開発するなら"
'''


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def snapshot(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    arc = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arc, check=True)
    sh(["git", "init", "-q", "-b", "develop"], dest, check=True)
    sh(["git", "add", "-A"], dest, check=True)
    sh(["git", "-c", "user.name=t2", "-c", "user.email=t2@example.invalid", "commit", "-q", "-m", "snap"], dest, check=True)


def changed(root: Path) -> dict[str, str]:
    """HEAD からの差（ファイルごとの unified diff の +/- の行）。"""
    out = {}
    names = sh(["git", "diff", "--name-only"], root).stdout.split()
    for n in names:
        d = sh(["git", "diff", "-U0", "--", n], root).stdout
        out[n] = "\n".join(l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    return out


def case(work: Path, old: str, new: str) -> dict:
    a, b = work / "cmd_bump", work / "bump-my-version"
    snapshot(a)
    snapshot(b)
    p = sh([sys.executable, str(RELEASE), "bump", "--plugin", "ndf", "--to", new, "--root", str(a)], a)
    try:
        now = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        now = {"status": "error", "summary": (p.stdout + p.stderr)[-300:]}
    cfg = work / "bumpversion.toml"
    cfg.write_text(BUMPCFG.format(old=old), encoding="utf-8")
    q = sh([str(Path(sys.executable).parent / "bump-my-version"), "replace", "--config-file", str(cfg),
            "--current-version", old, "--new-version", new, "--allow-dirty"], b)
    ca, cb = changed(a), changed(b)
    differ = sorted(n for n in set(ca) | set(cb) if ca.get(n) != cb.get(n))
    return {"from": old, "to": new, "cmd_bump": now.get("status"), "cmd_bump_summary": now.get("summary"),
            "cmd_bump_files": sorted(ca), "bump_my_version_exit": q.returncode,
            "bump_my_version_err": (q.stderr or "")[-400:] if q.returncode else "",
            "bump_my_version_files": sorted(cb), "differs": differ,
            "diff": {n: {"cmd_bump": ca.get(n, ""), "bump_my_version": cb.get(n, "")} for n in differ},
            "lines": sum(len([l for l in v.splitlines() if l.startswith("+")]) for v in ca.values())}


def cmd_bump(a) -> None:
    work = Path(a.work) / "bump"
    work.mkdir(parents=True, exist_ok=True)
    old = json.loads((REPO / "plugins/ndf/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(dev|rc)\.(\d+))?", old)
    if not m:
        emit(result(TOOL, "stopped", f"今の版 {old} を読めない"), 3)
    major, minor, patch, pre, n = m.groups()
    news = [f"{major}.{minor}.{patch}-{pre}.{int(n) + 1}" if pre else f"{major}.{minor}.{int(patch) + 1}-dev.1",
            f"{major}.{minor}.{int(patch) + 1}"]
    items, bad = [], []
    for new in news:
        r = case(work / new, old, new)
        ok = r["bump_my_version_exit"] == 0 and not r["differs"]
        items.append({"kind": "bump", "name": f"{old} → {new}", "result": "same" if ok else "differs", **r})
        if not ok:
            bad.append(f"{old} → {new}")
    status = "stopped" if bad else "ok"
    emit(result(TOOL, status, "bump-my-version の書き換えが cmd_bump と" + ("同じ" if not bad else "違う: " + " / ".join(bad)),
                items), 0 if status == "ok" else 1)
