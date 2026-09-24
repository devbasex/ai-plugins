#!/usr/bin/env python3
"""phase-steps.py: supervise.py の run の段から呼ぶ、決まった手順の段（#827）。

各サブコマンドは最後に 1 行の JSON を標準出力へ出す（{"status":"ok|fail", ...}）。
失敗は終了コード 1、引数の誤りは 2。標準ライブラリだけで書く。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"


class StepError(Exception):
    """段の失敗。JSON の reason へ入れて終了コード 1 で終える。"""


def run(cmd, cwd=None, check=True, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise StepError(f"{' '.join(map(str, cmd))} が終了コード {p.returncode}: {p.stderr.strip()[:500]}")
    return p


def git(root, *args, check=True):
    return run(["git", "-C", str(root), *args], check=check)


def git_root(arg):
    if arg:
        return Path(arg).resolve()
    p = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if p.returncode != 0:
        raise StepError("カレントが git の作業ツリーではない（--root を渡す）")
    return Path(p.stdout.strip())


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def commit(root, subject):
    msg = f"{subject}\n\n{CO_AUTHOR}\n"
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD").stdout.strip()


# --- 作業ツリー ------------------------------------------------------------

def list_worktrees(root):
    """git worktree list --porcelain を [{path, branch, detached}] にする。先頭が主ディレクトリ。"""
    out = git(root, "worktree", "list", "--porcelain").stdout
    items, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None, "detached": False}
            items.append(cur)
        elif cur is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
    return items


def common_git_dir(path):
    p = git(path, "rev-parse", "--git-common-dir").stdout.strip()
    d = Path(p)
    if not d.is_absolute():
        d = (Path(path) / d).resolve()
    return d


def evacuate(path, label):
    """未追跡・無視されたファイルを <git-common-dir>/ndf/worktree-trash/ へ移す。移した先を返す。"""
    common = common_git_dir(path)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    trash = common / "ndf" / "worktree-trash" / f"{label.replace('/', '__')}-{stamp}"
    trash.mkdir(parents=True, exist_ok=True)
    out = git(path, "status", "--ignored", "--untracked-files=all", "--porcelain=v1", "-z").stdout
    for ent in out.split("\0"):
        if ent[:3] not in ("?? ", "!! "):
            continue
        rel = ent[3:].rstrip("/")
        if not rel:
            continue
        src, dst = Path(path) / rel, trash / rel
        if not os.path.lexists(src):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
    return str(trash)


def remove_worktree(root, path, label):
    """作業ツリーを外す。拒否されたら退避してから --force で外す。(成否, 理由) を返す。"""
    p = git(root, "worktree", "remove", path, check=False)
    if p.returncode == 0:
        return True, None
    try:
        trash = evacuate(path, label)
    except (StepError, OSError) as e:
        return False, f"退避に失敗: {e}"
    p = git(root, "worktree", "remove", "--force", path, check=False)
    if p.returncode == 0:
        return True, f"退避先 {trash}"
    return False, f"worktree remove --force が失敗: {p.stderr.strip()[:300]}"


def repo_slug(root):
    p = run(["gh", "repo", "view", "--json", "owner,name"], cwd=root, check=False)
    if p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            return f"{d['owner']['login']}--{d['name']}"
        except (ValueError, KeyError, TypeError):
            pass
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}--{m.group(2)}" if m else None


def base_branch(main_dir):
    f = Path(main_dir) / ".ndf" / "worktree.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("base_branch") or "develop"
    except (OSError, ValueError, AttributeError):
        return "develop"


# --- cleanup ---------------------------------------------------------------

def cmd_cleanup(a):
    root = git_root(a.root)
    removed, deleted, kept = [], [], []
    wts = list_worktrees(root)
    main_dir = wts[0]["path"] if wts else str(root)
    slug = repo_slug(root)
    wt_base = Path(os.environ.get("NDF_WORKTREE_BASE") or Path(tempfile.gettempdir()) / "ndf-worktrees")

    for n in a.prs:
        p = run(["gh", "pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root, check=False)
        if p.returncode != 0:
            kept.append({"name": f"#{n}", "reason": f"gh pr view が失敗: {p.stderr.strip()[:200]}"})
            continue
        info = json.loads(p.stdout)
        branch = info.get("headRefName")
        if info.get("state") != "MERGED":
            kept.append({"name": branch or f"#{n}", "reason": f"#{n} が MERGED でない（{info.get('state')}）"})
            continue

        branch_free = True
        for wt in list_worktrees(root):
            if wt["branch"] != branch:
                continue
            if wt["path"] == main_dir:
                kept.append({"name": wt["path"], "reason": "主ディレクトリはこのブランチを checkout しているため外さない"})
                branch_free = False
                continue
            ok, why = remove_worktree(root, wt["path"], branch)
            if ok:
                removed.append(wt["path"])
            else:
                kept.append({"name": wt["path"], "reason": why})
                branch_free = False

        if branch_free and git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0:
            d = git(root, "branch", "-d", branch, check=False)
            if d.returncode == 0:
                deleted.append(branch)
            else:
                kept.append({"name": branch, "reason": f"git branch -d が拒否: {d.stderr.strip()[:300]}"})

        if slug:
            tmp_wt = wt_base / slug / f"pr{n}"
            if tmp_wt.exists():
                listed = {str(Path(w["path"]).resolve()): w for w in list_worktrees(root)}
                w = listed.get(str(tmp_wt.resolve()))
                if w is None:
                    kept.append({"name": str(tmp_wt), "reason": "この repo の作業ツリーとして登録されていない"})
                elif not w["detached"]:
                    kept.append({"name": str(tmp_wt), "reason": "detached でない"})
                else:
                    ok, why = remove_worktree(root, w["path"], f"pr{n}")
                    (removed.append(w["path"]) if ok else kept.append({"name": w["path"], "reason": why}))

    git(root, "worktree", "prune", check=False)
    base = base_branch(main_dir)
    status = "ok"
    result = {"removed_worktrees": removed, "deleted_branches": deleted, "kept": kept}
    cur = git(main_dir, "branch", "--show-current", check=False).stdout.strip()
    if cur != base:
        # 別のブランチへ取り込まないよう、pull はしない
        kept.append({"name": main_dir, "reason": f"主ディレクトリが {base} でなく {cur or 'detached'} のため pull しない"})
        pull = None
    else:
        pull = run(["git", "-C", main_dir, "pull", "--ff-only"], check=False)
    if pull is not None and pull.returncode != 0:
        status = "fail"
        result["reason"] = f"主ディレクトリの git pull --ff-only が失敗: {pull.stderr.strip()[:300]}"
    emit({"status": status, **result})
    return 0 if status == "ok" else 1


# --- spec-finalize ---------------------------------------------------------

def cmd_spec_finalize(a):
    root = git_root(a.root)
    spec = (root / a.spec).resolve() if not Path(a.spec).is_absolute() else Path(a.spec)
    if not spec.is_file():
        raise StepError(f"確定仕様のファイルが無い: {a.spec}")
    spec_rel = spec.relative_to(root).as_posix()

    for d in a.design:
        dp = Path(d)
        rel = dp.resolve().relative_to(root).as_posix() if dp.is_absolute() else dp.as_posix()
        if not (root / rel).exists():
            raise StepError(f"設計のファイルが無い: {d}")
        git(root, "rm", "-q", "--", rel)

    index = root / "docs" / "specifications" / "README.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        link = os.path.relpath(spec, index.parent).replace(os.sep, "/")
        if f"]({link})" not in text and f"]({spec_rel})" not in text and f"](./{link})" not in text:
            update_index(index, text, spec.name, link, a.title)
            git(root, "add", "--", index.relative_to(root).as_posix())

    git(root, "add", "--", spec_rel)
    if git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise StepError("コミットする変更が無い")
    sha = commit(root, f"Docs: {spec.name} を確定仕様にする")
    emit({"status": "ok", "commit": sha})
    return 0


def update_index(index, text, name, link, title):
    """索引の表（| [..](..) | .. |）か一覧（- [..](..)）の最後の行の後へ 1 行を足す。"""
    lines = text.split("\n")
    desc = title or name
    last, kind = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and re.search(r"\]\([^)]+\.md\)", s):
            last, kind = i, "table"
        elif re.match(r"^[-*] \[[^\]]+\]\([^)]+\.md\)", s):
            last, kind = i, "list"
    if last is None:
        new = f"- [{name}]({link}) — {desc}"
        body = text.rstrip("\n") + "\n\n" + new + "\n"
    else:
        if kind == "table":
            new = f"| [{name}]({link}) | {desc} |"
        else:
            sep = " — " if " — " in lines[last] else (": " if ": " in lines[last] else " — ")
            bullet = lines[last].lstrip()[0]
            indent = lines[last][: len(lines[last]) - len(lines[last].lstrip())]
            new = f"{indent}{bullet} [{name}]({link}){sep}{desc}"
        lines.insert(last + 1, new)
        body = "\n".join(lines)
    index.write_text(body, encoding="utf-8")


# --- bump ------------------------------------------------------------------

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


def version_arg(s):
    if not VERSION_RE.match(s):
        raise argparse.ArgumentTypeError(f"版の形が X.Y.Z[-接尾辞] でない: {s}")
    return s


def base_of(v):
    return v.split("-", 1)[0]


def plugin_dir(root, name):
    if name in ("ndf", "playwright-kit"):
        d = root / "plugins" / name
    else:
        d = root / "plugins" / "mcp" / name
    if not (d / ".claude-plugin" / "plugin.json").is_file():
        raise StepError(f"プラグインが無い: {name}（{d.relative_to(root)}/.claude-plugin/plugin.json）")
    return d


def ver_pat(v):
    """版の文字列を、前後に版の続きが無いときだけ当てる正規表現にする。"""
    return r"(?:(?<=v)|(?<![0-9A-Za-z.\-]))" + re.escape(v) + r"(?![0-9A-Za-z\-]|\.[0-9A-Za-z])"


class Editor:
    """行単位で旧版を新版へ直す。書き換えたファイルと、見つからなかった箇所を集める。"""

    def __init__(self, root, old, new):
        self.root, self.old, self.new = root, old, new
        self.files, self.manual = [], []

    def lines(self, path):
        return path.read_text(encoding="utf-8").split("\n")

    def save(self, path, lines):
        path.write_text("\n".join(lines), encoding="utf-8")
        rel = path.relative_to(self.root).as_posix()
        if rel not in self.files:
            self.files.append(rel)

    def sub(self, path, line_re, what, count=1, start=0, stop=None, required=True):
        """line_re に合う行の中の旧版を新版へ。直した行数を返す。"""
        if not path.is_file():
            if required:
                self.manual.append(f"{path.relative_to(self.root).as_posix()} が無い（{what}）")
            return 0
        lines = self.lines(path)
        stop = len(lines) if stop is None else stop
        done, already = 0, 0
        rx = re.compile(line_re)
        for i in range(start, stop):
            if done >= count:
                break
            if not rx.search(lines[i]):
                continue
            new_line = re.sub(ver_pat(self.old), self.new, lines[i])
            if new_line != lines[i]:
                lines[i] = new_line
                done += 1
            elif re.search(ver_pat(self.new), lines[i]):
                already += 1
        if done:
            self.save(path, lines)
        if required and done + already < count:
            self.manual.append(
                f"{path.relative_to(self.root).as_posix()}: {what} の旧版 {self.old} が"
                f" {count} 箇所見つからず {done + already} 箇所だけ（手で直す）")
        return done


def bump_update_heading(ed, readme):
    """README の更新案内の見出しを足す（基底が同じなら書き換える）。"""
    rel = readme.relative_to(ed.root).as_posix()
    if not readme.is_file():
        ed.manual.append(f"{rel} が無い（更新案内の見出し）")
        return
    lines = ed.lines(readme)
    old_h, new_h = f"## v{ed.old} へ更新するとき", f"## v{ed.new} へ更新するとき"
    if new_h in lines:
        return
    if base_of(ed.old) == base_of(ed.new):
        if old_h in lines:
            lines[lines.index(old_h)] = new_h
            ed.save(readme, lines)
        else:
            ed.manual.append(f"{rel}: 見出し「{old_h}」が無い（「{new_h}」を手で足す）")
        return
    if old_h in lines:
        at = lines.index(old_h)
    else:
        rx = re.compile(r"^## (?:以前の版: )?v\S+ へ更新するとき$")
        at = next((i for i, l in enumerate(lines) if rx.match(l)), None)
        if at is None:
            ed.manual.append(f"{rel}: 更新案内の見出しが無い（「{new_h}」を手で足す）")
            return
        ed.manual.append(f"{rel}: 見出し「{old_h}」が無いため、最初の更新案内の見出しの前へ足した")
    lines[at:at] = [new_h, ""]
    ed.save(readme, lines)


def bump_versioning_doc(ed):
    """docs/versioning-and-distribution.md の「版の付け方と開発版の配布」章の正式版の版数（#991 / #978 と同じ位置）。"""
    doc = ed.root / "docs" / "versioning-and-distribution.md"
    rel = "docs/versioning-and-distribution.md"
    ob, nb = base_of(ed.old), base_of(ed.new)
    if ob == nb:
        return
    if not doc.is_file():
        ed.manual.append(f"{rel} が無い（この章は手で直す）")
        return
    lines = ed.lines(doc)
    targets = [
        (re.compile(r"^\| 正式版 \| `([^`]+)` \|"), "正式版の表の行"),
        (re.compile(r"`([^`]+)` の次を開発するなら"), "接尾辞の例"),
    ]
    changed = False
    for rx, what in targets:
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if len(hits) != 1:
            ed.manual.append(f"{rel}: 「版の付け方と開発版の配布」章の{what}が特定できない（この章は手で直す）")
            continue
        i = hits[0]
        cur = rx.search(lines[i]).group(1)
        if cur == nb:
            continue
        if cur != ob:
            ed.manual.append(f"{rel}: {what}の版が {cur} で旧版 {ob} と違う（この章は手で直す）")
            continue
        lines[i] = lines[i].replace(f"`{ob}`", f"`{nb}`", 1)
        changed = True
    if changed:
        ed.save(doc, lines)


def marketplace_range(lines, name):
    """marketplace.json の中で、その plugin の項目の行の範囲（name の行から次の name の行まで）。"""
    rx = re.compile(r'^\s*"name"\s*:\s*"([^"]+)"')
    start = None
    for i, l in enumerate(lines):
        m = rx.match(l)
        if not m:
            continue
        if start is not None:
            return start, i
        if m.group(1) == name:
            start = i
    return (start, len(lines)) if start is not None else (None, None)


def run_staleness(root, expected=()):
    """check-doc-staleness.py を走らせる。expected に合う ERROR だけなら通ったと見なす。"""
    script = root / "scripts" / "check-doc-staleness.py"
    if not script.is_file():
        return None, "scripts/check-doc-staleness.py が無い"
    p = run([sys.executable, str(script), "--root", str(root)], cwd=root, check=False)
    if p.returncode == 0:
        return True, "ok"
    out = (p.stdout + p.stderr).strip().splitlines()
    errors = [l for l in out if l.startswith("ERROR")]
    rest = [l for l in errors if not any(x in l for x in expected)]
    if errors and not rest:
        return True, "ok（後の段で直す分だけ: " + " / ".join(errors)[:800] + "）"
    return False, " / ".join((rest or out)[-10:])[:1000]


def cmd_bump(a):
    root = git_root(a.root)
    pdir = plugin_dir(root, a.plugin)
    try:
        old = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as e:
        raise StepError(f"旧版を plugin.json から読めない: {e}")
    new = a.to
    if old == new:
        raise StepError(f"旧版と新版が同じ: {old}")
    ed = Editor(root, old, new)
    desc_re = r'^\s*"description"\s*:.*\(v' + re.escape(old) + r"\)"

    # plugin の定義
    for rel, required in ((".claude-plugin/plugin.json", True), (".codex-plugin/plugin.json", False),
                          ("dev.agy/plugin.json", False), ("plugin.json", False)):
        f = pdir / rel
        if not f.is_file():
            continue
        ed.sub(f, r'^\s*"version"\s*:', f"{rel} の version")
        text = f.read_text(encoding="utf-8")
        if f"(v{old})" in text:
            ed.sub(f, desc_re, f"{rel} の description")

    # マーケットプレイス
    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        s, e = marketplace_range(ed.lines(mp), a.plugin)
        if s is None:
            ed.manual.append(f".claude-plugin/marketplace.json に {a.plugin} の項目が無い")
        elif any(f"(v{old})" in l for l in ed.lines(mp)[s:e]):
            ed.sub(mp, desc_re, "marketplace.json の description", start=s, stop=e)

    # 根の README
    readme = root / "README.md"
    rows = [l for l in (ed.lines(readme) if readme.is_file() else []) if l.startswith(f"| **{a.plugin}** |")]
    if rows:
        ed.sub(readme, r"^\| \*\*" + re.escape(a.plugin) + r"\*\* \|", "README.md のプラグイン一覧表")
    elif a.plugin in ("ndf", "playwright-kit"):
        ed.manual.append(f"README.md のプラグイン一覧表に {a.plugin} の行が無い")
    if a.plugin == "ndf":
        ed.sub(readme, r"\*\*NDFプラグイン v", "README.md の概要の版")
        ed.sub(root / "AGENTS.md", r"主要プラグインです（v", "AGENTS.md の版")
        nr = pdir / "README.md"
        ed.sub(nr, r"（Kiro CLI用 / v", "plugins/ndf/README.md の Kiro の確認例")
        ed.sub(nr, r"/plugins/cache/ai-plugins/ndf/", "plugins/ndf/README.md の Codex のパス例", count=2)
        ed.sub(nr, r"ndf@ai-plugins\s+installed", "plugins/ndf/README.md の codex plugin list の出力例")
        bump_versioning_doc(ed)

    bump_update_heading(ed, pdir / "README.md")

    # 新しい見出しを古い見出しの前に足すと「更新案内の見出しが 2 個ある」が出る。古い節は本文を書く後の段が片付ける
    expected = []
    if base_of(old) != base_of(new):
        expected.append(f"更新案内の見出しが 2 個ある（v{new} / v{old}）")
        ed.manual.append(f"{pdir.relative_to(root).as_posix()}/README.md: 更新案内の v{old} の節を片付ける（見出しを 1 つにする）")
    ok, summary = run_staleness(root, expected)
    if ok is None:
        ed.manual.append(summary)
    status = "fail" if ok is False else "ok"
    out = {"status": status, "plugin": a.plugin, "from": old, "to": new,
           "files": ed.files, "manual": ed.manual, "staleness": summary}
    if status == "fail":
        out["reason"] = "check-doc-staleness.py が失敗"
    emit(out)
    return 0 if status == "ok" else 1


# --- changelog -------------------------------------------------------------

def pr_titles(root, prs):
    items = []
    for n in prs:
        p = run(["gh", "pr", "view", str(n), "--json", "title"], cwd=root, check=False)
        if p.returncode != 0:
            raise StepError(f"gh pr view {n} が失敗: {p.stderr.strip()[:200]}")
        try:
            title = json.loads(p.stdout)["title"].strip()
        except (ValueError, KeyError, AttributeError):
            raise StepError(f"gh pr view {n} の出力を読めない")
        items.append((n, f"- {title}（#{n}）"))
    return items


def cmd_changelog(a):
    root = git_root(a.root)
    items = pr_titles(root, a.prs)
    sections = []

    # CHANGELOG.md（見出しは基底の版。開発版の接尾辞は載せない）
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        raise StepError("CHANGELOG.md が無い")
    lines = cl.read_text(encoding="utf-8").split("\n")
    ver = base_of(a.version)
    head = f"## [{a.plugin} {ver}]"
    at = next((i for i, l in enumerate(lines) if l == head or l.startswith(head + " ")), None)
    if at is None:
        first = next((i for i, l in enumerate(lines) if l.startswith("## [")), len(lines))
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        block = [f"{head} - {today}", ""] + [b for _, b in items] + [""]
        if first == len(lines) and lines and lines[-1] != "":
            block = [""] + block
        lines[first:first] = block
        sections.append({"file": "CHANGELOG.md", "heading": block[0] if block[0] else block[1],
                         "added": [n for n, _ in items]})
    else:
        end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        body = "\n".join(lines[at:end])
        add = [(n, b) for n, b in items if f"#{n}）" not in body and f"#{n})" not in body]
        ins = end
        while ins - 1 > at and lines[ins - 1].strip() == "":
            ins -= 1
        lines[ins:ins] = [b for _, b in add]
        sections.append({"file": "CHANGELOG.md", "heading": lines[at], "added": [n for n, _ in add]})
    cl.write_text("\n".join(lines), encoding="utf-8")

    # plugin の README の更新案内
    try:
        pdir = plugin_dir(root, a.plugin)
    except StepError:
        pdir = None
    readme = pdir / "README.md" if pdir else None
    h = f"## v{a.version} へ更新するとき"
    if readme and readme.is_file():
        rl = readme.read_text(encoding="utf-8").split("\n")
        if h in rl:
            i = rl.index(h)
            nxt = next((j for j in range(i + 1, len(rl)) if rl[j].strip()), None)
            if nxt is None or rl[nxt].startswith("## "):
                rel = readme.relative_to(root).as_posix()
                rl[i + 1:i + 1] = [""] + [b for _, b in items] + [""]
                # 見出しの直後に空行が重なったら 1 つにする
                j = i + 2 + len(items) + 1
                while j < len(rl) and rl[j] == "" and rl[j - 1] == "":
                    del rl[j]
                readme.write_text("\n".join(rl), encoding="utf-8")
                sections.append({"file": rel, "heading": h, "added": [n for n, _ in items]})

    emit({"status": "ok", "sections": sections})
    return 0


# --- release ---------------------------------------------------------------

def gh_json(root, args, what):
    p = run(["gh", *args], cwd=root, check=False)
    if p.returncode != 0:
        raise StepError(f"{what} が失敗: {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout or "null")
    except ValueError:
        raise StepError(f"{what} の出力を読めない")


def owner_repo(root):
    slug = repo_slug(root)
    if not slug or "--" not in slug:
        raise StepError("リポジトリの owner/name を決められない")
    return slug.replace("--", "/", 1)


def changelog_section(root, version, plugin="ndf"):
    """CHANGELOG.md の `## [<plugin> <基底の版>]` の節の本文（見出しを除く）を返す。"""
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        return ""
    lines = cl.read_text(encoding="utf-8").split("\n")
    head = f"## [{plugin} {base_of(version)}]"
    at = next((i for i, l in enumerate(lines) if l == head or l.startswith(head + " ")), None)
    if at is None:
        return ""
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[at + 1:end]).strip()


def run_checks(root):
    """リリース前の検査を回し、[(名前, 通ったか, 末尾の出力)] を返す。"""
    res = []
    for name, cmd in (("check-doc-staleness", ["python3", "scripts/check-doc-staleness.py", "--root", str(root)]),
                      ("validate-runtime-plugins", ["bash", "scripts/validate-runtime-plugins.sh"])):
        if not (root / cmd[1]).is_file():
            res.append((name, None, "スクリプトが無い"))
            continue
        p = run(cmd, cwd=root, check=False)
        tail = "\n".join((p.stdout + p.stderr).strip().split("\n")[-3:])
        res.append((name, p.returncode == 0, tail))
    return res


def find_pr(root, head, base, states=("OPEN",)):
    """head → base の PR を探す。states の順に最初に見つかった {number, state} を返す。"""
    items = gh_json(root, ["pr", "list", "--head", head, "--base", base, "--state", "all",
                           "--json", "number,state", "--limit", "20"], "gh pr list") or []
    for st in states:
        for it in items:
            if it.get("state") == st:
                return it
    return None


def create_pr(root, base, head, title, body):
    p = run(["gh", "pr", "create", "--base", base, "--head", head, "--title", title, "--body", body],
            cwd=root, check=False)
    if p.returncode != 0:
        raise StepError(f"gh pr create（{head} → {base}）が失敗: {p.stderr.strip()[:300]}")
    m = re.search(r"/pull/(\d+)", p.stdout)
    if not m:
        raise StepError(f"作った PR の番号を読めない: {p.stdout.strip()[:200]}")
    return int(m.group(1))


def pr_check_buckets(root, n):
    p = run(["gh", "pr", "checks", str(n), "--json", "name,bucket"], cwd=root, check=False)
    try:
        return json.loads(p.stdout or "[]") or []
    except ValueError:
        return []


def wait_and_merge(root, n):
    """PR のチェックを待ち、全部 pass ならマージする。落ちたら失敗したチェック名で StepError。"""
    import time
    # 作った直後はチェックがまだ現れないので、現れるまで待つ
    for _ in range(20):
        if pr_check_buckets(root, n):
            break
        time.sleep(15)
    else:
        raise StepError(f"PR #{n} にチェックが現れない")
    run(["gh", "pr", "checks", str(n), "--watch", "-i", "30"], cwd=root, check=False)
    checks = pr_check_buckets(root, n)
    bad = [c.get("name") for c in checks if c.get("bucket") not in ("pass", "skipping")]
    if not checks or bad:
        raise StepError(f"PR #{n} のチェックが通らない: {', '.join(map(str, bad)) or 'チェックが無い'}")
    p = run(["gh", "pr", "merge", str(n), "--admin", "--merge"], cwd=root, check=False)
    if p.returncode != 0:
        raise StepError(f"gh pr merge {n} が失敗: {p.stderr.strip()[:300]}")
    return merge_commit_of(root, n)


def merge_commit_of(root, n):
    d = gh_json(root, ["pr", "view", str(n), "--json", "mergeCommit"], f"gh pr view {n}") or {}
    return ((d.get("mergeCommit") or {}).get("oid")) or None


def cmd_release(a):
    root = git_root(a.root)
    ver = a.version
    plugins = [s.strip() for s in a.plugins.split(",") if s.strip()]
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != f"release/v{ver}":
        raise StepError(f"作業ツリーのブランチが release/v{ver} でない: {branch}")
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StepError("作業ツリーにコミットしていない変更がある（bump と changelog をコミットしてから呼ぶ）")

    git(root, "push", "-q", "-u", "origin", "HEAD")

    # 開発版の PR（release/v<版> → develop）
    pr = find_pr(root, branch, "develop", states=("OPEN", "MERGED"))
    if pr is None:
        section = changelog_section(root, ver)
        checks = run_checks(root)
        mark = {True: "pass", False: "fail", None: "skip"}
        body = "\n".join([
            f"ndf v{ver} のリリース（{a.channel}）。対象の plugin: {', '.join(plugins)}",
            "",
            "## 含む PR",
            "",
            section or "（CHANGELOG.md に該当の節が無い）",
            "",
            "## 検査の結果",
            "",
            *[f"- {name}: {mark[ok]}" + (f"（{tail.splitlines()[-1]}）" if tail else "") for name, ok, tail in checks],
            "",
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        ])
        release_pr = create_pr(root, "develop", branch, f"Release: ndf v{ver}", body)
        pr = {"number": release_pr, "state": "OPEN"}
    release_pr = pr["number"]
    if pr["state"] == "OPEN":
        release_commit = wait_and_merge(root, release_pr)
    else:
        release_commit = merge_commit_of(root, release_pr)

    out = {"status": "ok", "release_pr": release_pr, "main_pr": None, "tag": None,
           "merge_commit": release_commit, "plugins": plugins}
    if a.channel == "dev":
        emit(out)
        return 0

    # 本番: develop → main、タグ、GitHub Release
    tag = f"ndf--v{ver}"
    git(root, "fetch", "-q", "origin", "--tags")
    if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0:
        raise StepError(f"タグ {tag} は既にある")
    mp = find_pr(root, "develop", "main", states=("OPEN",))
    if mp is None:
        main_pr = create_pr(root, "main", "develop", f"Release: ndf v{ver} を main へ",
                            f"ndf v{ver} を main へ出す（開発版の PR #{release_pr}）。\n\n"
                            "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    else:
        main_pr = mp["number"]
    merge = wait_and_merge(root, main_pr)
    git(root, "fetch", "-q", "origin")
    if not merge:
        merge = git(root, "rev-parse", "origin/main").stdout.strip()
    git(root, "tag", "-a", tag, merge, "-m", f"ndf v{ver}")
    git(root, "push", "-q", "origin", tag)

    notes = changelog_section(root, ver) or f"ndf v{ver}"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(notes + "\n")
        notes_file = f.name
    try:
        p = run(["gh", "release", "create", tag, "--title", f"ndf v{ver}", "--notes-file", notes_file,
                 "--latest"], cwd=root, check=False)
    finally:
        os.unlink(notes_file)
    if p.returncode != 0:
        raise StepError(f"gh release create {tag} が失敗: {p.stderr.strip()[:300]}")

    out.update({"main_pr": main_pr, "tag": tag, "merge_commit": merge})
    emit(out)
    return 0


# --- approval-facts --------------------------------------------------------

def cmd_approval_facts(a):
    root = git_root(a.root)
    repo = owner_repo(root)
    git(root, "fetch", "-q", "origin", "--tags")
    cur_tag = f"ndf--v{a.version}"
    prev = a.prev_tag
    if not prev:
        tags = git(root, "tag", "--list", "ndf--v*", "--sort=-v:refname").stdout.split()
        prev = next((t for t in tags if t != cur_tag and "-" not in t[len("ndf--v"):]), None)
        if not prev:
            raise StepError("前のタグを決められない（--prev-tag を渡す）")
    dev = git(root, "rev-parse", "origin/develop").stdout.strip()

    stat = git(root, "diff", "--shortstat", "origin/main...origin/develop").stdout.strip()
    files = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", stat)) else 0
    ins = int(m.group(1)) if (m := re.search(r"(\d+) insertions?", stat)) else 0
    dels = int(m.group(1)) if (m := re.search(r"(\d+) deletions?", stat)) else 0

    out = [
        f"## ndf v{a.version} の本番承認の事実",
        "",
        f"- 版の比較: https://github.com/{repo}/compare/{prev}...{dev}",
        f"- `main...develop` の差分: {files} ファイル / +{ins} / −{dels}",
        "",
        "| PR | タイトル | 状態 | マージコミット | CI |",
        "|---|---|---|---|---|",
    ]
    for n in a.prs:
        d = gh_json(root, ["pr", "view", str(n), "--json", "number,title,state,mergeCommit,url"],
                    f"gh pr view {n}") or {}
        oid = (d.get("mergeCommit") or {}).get("oid") or ""
        checks = pr_check_buckets(root, n)
        passed = sum(1 for c in checks if c.get("bucket") == "pass")
        title = str(d.get("title", "")).replace("|", "\\|")
        url = d.get("url") or f"https://github.com/{repo}/pull/{n}"
        out.append(f"| [#{n}]({url}) | {title} | {d.get('state', '')} | "
                   f"{('`' + oid[:8] + '`') if oid else '—'} | {passed}/{len(checks)} |")
    print("\n".join(out))
    emit({"status": "ok"})
    return 0


# --- 導入確認 ---------------------------------------------------------------

REPO_SLUG = "devbasex/ai-plugins"
MARKET = "ai-plugins"


def sha256_of(path):
    import hashlib
    p = Path(path)
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def user_env_snapshot():
    home = Path(os.path.expanduser("~"))
    link = home / ".local" / "bin" / "claude"
    return {
        ".bashrc": sha256_of(home / ".bashrc"),
        ".zshrc": sha256_of(home / ".zshrc"),
        ".local/bin/claude": os.readlink(link) if link.is_symlink() else (
            "file" if link.exists() else None),
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
    """ref の展開物と導入先とで、変わったファイルを filecmp で比べ、一致しないものを返す。"""
    import filecmp
    out = []
    for f in files:
        rel = Path(f).relative_to(rel_plugin)
        s = Path(src_root) / f
        t = Path(dst) / rel
        if not s.exists():
            continue  # ref で消えたファイル
        if not t.exists() or not filecmp.cmp(s, t, shallow=False):
            out.append(f"{label}: {f}")
    return out


def verify_claude(env, ref, plugins, expect):
    src = f"https://github.com/{REPO_SLUG}.git#{ref}" if ref != "main" else REPO_SLUG
    steps = [["claude", "plugin", "marketplace", "add", src]]
    steps += [["claude", "plugin", "install", f"{p}@{MARKET}"] for p in plugins]
    steps.append(["claude", "plugin", "list"])
    res = {"exit": 0, "version": {}, "log": []}
    out = ""
    for c in steps:
        code, out = run_env_i(c, env)
        res["log"].append({"cmd": " ".join(c), "exit": code})
        if code != 0:
            res["exit"] = code
            res["error"] = out.strip()[-500:]
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
    res = {"exit": 0, "version": {}, "log": []}
    for c in steps:
        code, out = run_env_i(c, env)
        res["log"].append({"cmd": " ".join(c), "exit": code})
        if code != 0:
            res["exit"] = code
            res["error"] = out.strip()[-500:]
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
    import shutil
    root = git_root(a.root)
    plugins = [p.strip() for p in a.plugins.split(",") if p.strip()]
    runtimes = [r.strip() for r in a.runtimes.split(",") if r.strip()]
    bad = [r for r in runtimes if r not in ("claude", "codex", "kiro")]
    if bad:
        print(f"未知の runtime: {','.join(bad)}", file=sys.stderr)
        return 2
    rel = {p: plugin_dir(root, p).relative_to(root).as_posix() for p in plugins}

    git(root, "fetch", "-q", "origin", "--tags")
    ref_rev = git(root, "rev-parse", f"origin/{a.ref}").stdout.strip()
    tags = git(root, "tag", "--list", "ndf--v*", "--sort=-v:refname").stdout.split()
    cur = f"ndf--v{a.expect}"
    prev = next((t for t in tags if t != cur and "-" not in t[len("ndf--v"):]), None)

    before = user_env_snapshot()
    tmp = tempfile.mkdtemp(prefix="ndf-verify-install-")
    result = {"status": "ok", "ref": a.ref, "rev": ref_rev[:8], "prev_tag": prev,
              "runtimes": {}, "mismatch": []}
    try:
        src = Path(tmp) / "src"
        src.mkdir()
        arch = subprocess.run(["git", "-C", str(root), "archive", ref_rev],
                              capture_output=True)
        if arch.returncode != 0:
            raise StepError(f"git archive {ref_rev[:8]} が失敗: {arch.stderr.decode(errors='replace')[:300]}")
        tar = subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, capture_output=True)
        if tar.returncode != 0:
            raise StepError(f"展開が失敗: {tar.stderr.decode(errors='replace')[:300]}")

        changed = {}
        for p, r in rel.items():
            if prev:
                changed[p] = [f for f in git(root, "diff", "--name-only", prev, ref_rev, "--", r)
                              .stdout.split() if f]
            else:
                changed[p] = []

        env = isolated_env(tmp)
        if "claude" in runtimes:
            res, dirs = verify_claude(env, a.ref, plugins, a.expect)
            result["runtimes"]["claude"] = res
            for p, d in dirs.items():
                if d is None:
                    result["mismatch"].append(f"claude: {p} の導入先が無い")
                else:
                    result["mismatch"] += compare_files(src, d, rel[p], changed[p], "claude")
        if "codex" in runtimes:
            res, dirs = verify_codex(env, a.ref, plugins, a.expect)
            result["runtimes"]["codex"] = res
            for p, d in dirs.items():
                if d is None:
                    result["mismatch"].append(f"codex: {p} の導入先が無い")
                else:
                    result["mismatch"] += compare_files(src, d, rel[p], changed[p], "codex")
        if "kiro" in runtimes:
            res, _ = verify_kiro(env, src, tmp, a.expect)
            result["runtimes"]["kiro"] = res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = user_env_snapshot()
    result["user_env_unchanged"] = before == after
    if before != after:
        result["user_env_diff"] = {k: [before[k], after[k]] for k in before if before[k] != after[k]}

    ok = result["user_env_unchanged"] and not result["mismatch"]
    for name, r in result["runtimes"].items():
        if r.get("exit") != 0:
            ok = False
        v = r.get("version")
        if isinstance(v, dict):
            # mcp-serena などは別の版を持つので、ndf があれば ndf だけを --expect と比べる
            vs = [v.get("ndf")] if "ndf" in v else list(v.values())
        else:
            vs = [v]
        if r.get("exit") == 0 and any(x != a.expect for x in vs):
            ok = False
            r["expect_mismatch"] = True
    result["status"] = "ok" if ok else "fail"
    emit(result)
    return 0 if ok else 1


# --- 入口 ------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="phase-steps.py", description=__doc__)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("cleanup", help="マージ済みの PR の作業ツリーとローカルブランチを片付ける")
    p.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("spec-finalize", help="設計を消し、確定仕様を索引へ載せてコミットする")
    p.add_argument("--spec", required=True)
    p.add_argument("--design", nargs="+", required=True)
    p.add_argument("--title")
    p.set_defaults(func=cmd_spec_finalize)

    p = sub.add_parser("bump", help="plugin の版数を持つ箇所を旧版から新版へ上げる")
    p.add_argument("--plugin", required=True, help="ndf / playwright-kit / plugins/mcp の名前（例 mcp-serena）")
    p.add_argument("--to", required=True, type=version_arg)
    p.set_defaults(func=cmd_bump)

    p = sub.add_parser("changelog", help="CHANGELOG.md と plugin の README の更新案内へ PR のタイトルを並べる")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--plugin", default="ndf")
    p.set_defaults(func=cmd_changelog)

    p = sub.add_parser("release", help="release/v<版> を develop へマージし、prod なら main へ出してタグと Release を作る")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--channel", required=True, choices=("dev", "prod"))
    p.add_argument("--plugins", default="ndf", help="カンマ区切り（例 ndf,mcp-serena）")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("approval-facts", help="本番承認の提示物のうち機械で作れる部分を Markdown で出す")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--prev-tag")
    p.set_defaults(func=cmd_approval_facts)

    p = sub.add_parser("verify-install", help="隔離した HOME で ref から導入し、版と中身を確かめる")
    p.add_argument("--ref", required=True, choices=("develop", "main"))
    p.add_argument("--expect", required=True, type=version_arg)
    p.add_argument("--plugins", default="ndf", help="カンマ区切り（例 ndf,mcp-serena）")
    p.add_argument("--runtimes", default="claude,codex,kiro", help="カンマ区切り（claude,codex,kiro）")
    p.set_defaults(func=cmd_verify_install)

    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)  # 引数の誤りは argparse が終了コード 2 で終える
    try:
        return a.func(a)
    except StepError as e:
        emit({"status": "fail", "reason": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
