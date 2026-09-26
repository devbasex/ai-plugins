#!/usr/bin/env python3
"""release-verification-steps.py: リリース後テストの導入確認（#862。試作は #827 の phase-steps.py）。

    python3 release-verification-steps.py verify-install --ref develop|main --expect <版>
        [--plugins ndf,...] [--runtimes claude,codex,kiro] [--root <dir>]

隔離した HOME で ref から導入し、導入された版と、前の正式版のタグから変わったファイルの中身が
ref と一致するかを確かめる。利用者の HOME の設定が変わっていないことも確かめる。
結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 1 = 不一致か導入の失敗 /
2 = 呼び出しの誤り（未知の runtime）/ 3 = plugin が無い。
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (EXIT_UNREADABLE, StepError, common_parser, emit, git, git_root,  # noqa: E402
                         main_with, plugin_dir, result, version_arg)

TOOL = "release-verification"
REPO_SLUG = "devbasex/ai-plugins"
MARKET = "ai-plugins"


def rc_digest(path):
    """rc ファイルの sha256。無ければ None（導入の前後で利用者の rc が変わらないことを比べる）。"""
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def user_env_snapshot():
    home = Path(os.path.expanduser("~"))
    link = home / ".local" / "bin" / "claude"
    return {
        ".bashrc": rc_digest(home / ".bashrc"),
        ".zshrc": rc_digest(home / ".zshrc"),
        ".local/bin/claude": os.readlink(link) if link.is_symlink() else ("file" if link.exists() else None),
    }


def isolated_env(tmp):
    """利用者の HOME と設定を触らないよう、すべてを一時ディレクトリの下へ向けた環境変数。"""
    h = Path(tmp) / "home"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(h),
        "XDG_CONFIG_HOME": str(h / ".config"),
        "XDG_DATA_HOME": str(h / ".local" / "share"),
        "XDG_CACHE_HOME": str(h / ".cache"),
        "XDG_STATE_HOME": str(h / ".local" / "state"),
        "CLAUDE_CONFIG_DIR": str(h / ".claude"),
        "CODEX_HOME": str(h / ".codex"),
        "NPM_CONFIG_PREFIX": str(h / ".npm-global"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
              "CLAUDE_CONFIG_DIR", "CODEX_HOME", "NPM_CONFIG_PREFIX"):
        Path(env[k]).mkdir(parents=True, exist_ok=True)
    return env


def run_env_i(cmd, env, cwd=None, timeout=600):
    """env -i で環境を空にし、渡した変数だけで動かす。"""
    full = ["env", "-i", *[f"{k}={v}" for k, v in env.items()], *cmd]
    try:
        p = subprocess.run(full, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)} が {timeout} 秒で終わらない"


def installed_dir(cache_root, p, expect):
    """<設定>/plugins/cache/ai-plugins/<p>/<版> を探す。期待の版が無ければ最新のものを返す。"""
    base = Path(cache_root) / "plugins" / "cache" / MARKET / p
    if not base.is_dir():
        return None
    if (base / expect).is_dir():
        return base / expect
    dirs = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)
    return dirs[-1] if dirs else None


def manifest_version(d):
    for m in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        f = Path(d) / m
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError):
                pass
    return None


def compare_files(src_root, dst, rel_plugin, files, label):
    """ref の展開物と導入先とで、変わったファイルを比べ、一致しないものを返す。"""
    out = []
    for f in files:
        s = Path(src_root) / f
        t = Path(dst) / Path(f).relative_to(rel_plugin)
        if not s.exists():
            continue  # ref で消えたファイル
        if not t.exists() or not filecmp.cmp(s, t, shallow=False):
            out.append(f"{label}: {f}")
    return out


def _run_steps(env, steps):
    res = {"exit": 0, "version": {}, "log": []}
    out = ""
    for c in steps:
        code, out = run_env_i(c, env)
        res["log"].append({"cmd": " ".join(c), "exit": code})
        if code != 0:
            res["exit"] = code
            res["error"] = out.strip()[-500:]
            return res, out, False
    return res, out, True


def verify_claude(env, ref, plugins, expect):
    src = f"https://github.com/{REPO_SLUG}.git#{ref}" if ref != "main" else REPO_SLUG
    steps = [["claude", "plugin", "marketplace", "add", src]]
    steps += [["claude", "plugin", "install", f"{p}@{MARKET}"] for p in plugins]
    steps.append(["claude", "plugin", "list"])
    res, out, ok = _run_steps(env, steps)
    if not ok:
        return res, {}
    dirs = {}
    for p in plugins:
        d = installed_dir(env["CLAUDE_CONFIG_DIR"], p, expect)
        dirs[p] = d
        v = manifest_version(d) if d else None
        m = re.search(rf"{re.escape(p)}@{MARKET}\S*\s+(?:.*?)?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", out)
        res["version"][p] = v or (m.group(1) if m else None)
    return res, dirs


def verify_codex(env, ref, plugins, expect):
    add = ["codex", "plugin", "marketplace", "add", REPO_SLUG]
    if ref != "main":
        add += ["--ref", ref]
    steps = [add] + [["codex", "plugin", "add", f"{p}@{MARKET}"] for p in plugins]
    steps.append(["codex", "plugin", "list"])
    res, _, ok = _run_steps(env, steps)
    if not ok:
        return res, {}
    dirs = {}
    for p in plugins:
        d = installed_dir(env["CODEX_HOME"], p, expect)
        dirs[p] = d
        res["version"][p] = manifest_version(d) if d else None
    return res, dirs


def verify_kiro(env, src, tmp, expect):
    proj = Path(tmp) / "kiro-project"
    proj.mkdir()
    res = {"exit": 0, "version": None}
    code, out = run_env_i(["bash", str(src / "plugins/ndf/dev.kiro/install.sh"),
                           "--project", str(proj), "--yes"], env, cwd=str(src))
    res["exit"] = code
    if code != 0:
        res["error"] = out.strip()[-500:]
        return res, None
    agent = proj / ".kiro" / "agents" / "ndf.json"
    if agent.is_file():
        try:
            desc = json.loads(agent.read_text(encoding="utf-8")).get("description", "")
        except (OSError, ValueError):
            desc = ""
        m = re.search(r"v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", desc)
        res["version"] = m.group(1) if m else None
    return res, proj


def cmd_verify_install(a):
    root = git_root(a.root)
    plugins = [p.strip() for p in a.plugins.split(",") if p.strip()]
    runtimes = [r.strip() for r in a.runtimes.split(",") if r.strip()]
    bad = [r for r in runtimes if r not in ("claude", "codex", "kiro")]
    if bad:
        raise StepError(f"未知の runtime: {','.join(bad)}", EXIT_UNREADABLE)
    rel = {p: plugin_dir(root, p).relative_to(root).as_posix() for p in plugins}

    git(root, "fetch", "-q", "origin", "--tags")
    ref_rev = git(root, "rev-parse", f"origin/{a.ref}").stdout.strip()
    tags = git(root, "tag", "--list", "ndf--v*", "--sort=-v:refname").stdout.split()
    cur = f"ndf--v{a.expect}"
    prev = next((t for t in tags if t != cur and "-" not in t[len("ndf--v"):]), None)

    before = user_env_snapshot()
    tmp = tempfile.mkdtemp(prefix="ndf-verify-install-")
    runtimes_res, mismatch = {}, []
    try:
        src = Path(tmp) / "src"
        src.mkdir()
        arch = subprocess.run(["git", "-C", str(root), "archive", ref_rev], capture_output=True)
        if arch.returncode != 0:
            raise StepError(f"git archive {ref_rev[:8]} が失敗: {arch.stderr.decode(errors='replace')[:300]}")
        tar = subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, capture_output=True)
        if tar.returncode != 0:
            raise StepError(f"展開が失敗: {tar.stderr.decode(errors='replace')[:300]}")

        changed = {p: ([f for f in git(root, "diff", "--name-only", prev, ref_rev, "--", r).stdout.split() if f]
                       if prev else []) for p, r in rel.items()}
        env = isolated_env(tmp)
        for name, fn in (("claude", verify_claude), ("codex", verify_codex)):
            if name not in runtimes:
                continue
            res, dirs = fn(env, a.ref, plugins, a.expect)
            runtimes_res[name] = res
            for p, d in dirs.items():
                if d is None:
                    mismatch.append(f"{name}: {p} の導入先が無い")
                else:
                    mismatch += compare_files(src, d, rel[p], changed[p], name)
        if "kiro" in runtimes:
            runtimes_res["kiro"], _ = verify_kiro(env, src, tmp, a.expect)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = user_env_snapshot()
    items = []
    env_same = before == after
    if not env_same:
        for k in before:
            if before[k] != after[k]:
                items.append({"kind": "user_env", "name": k, "result": "changed", "before": before[k],
                              "after": after[k]})
    ok = env_same and not mismatch
    for name, r in runtimes_res.items():
        v = r.get("version")
        # mcp-serena などは別の版を持つので、ndf があれば ndf だけを --expect と比べる
        vs = ([v.get("ndf")] if "ndf" in v else list(v.values())) if isinstance(v, dict) else [v]
        res = "ok"
        if r.get("exit") != 0:
            res = "failed"
        elif any(x != a.expect for x in vs):
            res = "version_mismatch"
        if res != "ok":
            ok = False
        items.append({"kind": "runtime", "name": name, "result": res, **r})
    items += [{"kind": "file", "name": m, "result": "mismatch"} for m in mismatch]
    metrics = {"ref": a.ref, "rev": ref_rev[:8], "prev_tag": prev, "expect": a.expect,
               "user_env_unchanged": env_same, "mismatch": len(mismatch)}
    bad_rt = [i["name"] for i in items if i["kind"] == "runtime" and i["result"] != "ok"]
    summary = (f"{a.ref}（{ref_rev[:8]}）から {', '.join(runtimes_res)} へ導入し v{a.expect} を確かめた"
               if ok else
               f"導入の確認が通らない（runtime: {', '.join(bad_rt) or 'なし'} / 中身の不一致 {len(mismatch)} 件"
               f" / 利用者の環境が{'変わらない' if env_same else '変わった'}）")
    emit(result(TOOL, "ok" if ok else "stopped", summary, items, metrics))


def build_parser():
    ap = argparse.ArgumentParser(prog="release-verification-steps.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify-install", parents=[common_parser()], help="隔離した HOME で ref から導入し、版と中身を確かめる")
    p.add_argument("--ref", required=True, choices=("develop", "main"))
    p.add_argument("--expect", required=True, type=version_arg)
    p.add_argument("--plugins", default="ndf", help="カンマ区切り（例 ndf,mcp-serena）")
    p.add_argument("--runtimes", default="claude,codex,kiro", help="カンマ区切り（claude,codex,kiro）")
    p.set_defaults(func=cmd_verify_install)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
