#!/usr/bin/env python3
"""リポジトリが宣言した配布の段を、配布の段階に合わせて走らせる（#893）。

宣言はリポジトリの `.ndf/release.json`。形は `skills/release/schemas/release.schema.json` が定め、
書き方は `skills/release/references/release-steps.md` にある。

    python3 release-steps.py run   --root <dir> --stage production|verification --version <版> [--dry-run]
    python3 release-steps.py check --root <dir>

終了コード:

    0  宣言が無い（run は何も出力しない）、段階に合う段が無い、またはすべての段が 0 で終わり
       書いてよい場所の中だけが変わった
    1  段が 0 以外で終わった・時間切れ・書いてよい場所の外が変わった。最初に落ちた段で止める
    2  check だけが返す。宣言が無い
    3  宣言が読めない。どの項目かを標準エラーに出す

段はシェルを通さずに `--root` を作業ディレクトリにして実行する。段が変えたパスは、段の前後の
`git status --porcelain -uall` に出たパスの内容の要約（`git hash-object`）を比べて決める。

配布の決まった手順（#862。試作は #827 の phase-steps.py）も同じスクリプトに置く。結果は
`lib/step_result.py` の形の 1 行の JSON で、終了コードは 0 = ok / 1 = 失敗（stopped）/
2 = 読めない / 3 = 前提が無い / 10 = 本番への配布の承認が要る（approval-facts）。

    python3 release-steps.py bump           --plugin <名前> --to <版> [--root <dir>]
    python3 release-steps.py changelog      --version <版> --prs <PR番号>... [--plugin ndf] [--root <dir>]
    python3 release-steps.py release        --version <版> --channel dev|prod [--plugins ndf,...] [--root <dir>]
    python3 release-steps.py approval-facts --version <版> --prs <PR番号>... [--prev-tag <タグ>] [--root <dir>]
    python3 release-steps.py notes          --version <版> --prs <PR番号>... [--approval <提示物>]
                                            [--verified claude,codex,kiro] [--ref develop] [--plugin ndf] [--root <dir>]

notes は PR 本文の `## 利用者向けの変化` の節（無い・「無し」の PR は題名）から、CHANGELOG.md の版の節と
plugin の README の `## v<版> へ更新するとき` の節を組み直す。`--approval` を渡すと、代わりに本番承認の提示物の
「配る中身」「検証への配布で確かめたこと」の欄と、PR 本文の `## 未検証・残る危険` を集めた節を書く。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (EXIT_GATE, EXIT_PRECONDITION, StepError, approval_present, base_of,  # noqa: E402
                         common_parser, emit, gh_json, git, git_root, main_with, plugin_dir,
                         repo_slug, result, run, today, version_arg)

DECLARATION = ".ndf/release.json"
SUPPORTED_VERSIONS = (1,)
STAGES = ("production", "verification", "any")
DEFAULT_TIMEOUT = 600
STEP_KEYS = {"name", "stage", "command", "writes", "guide", "timeout_seconds"}


class DeclarationError(Exception):
    pass


@dataclass
class Step:
    name: str
    stage: str
    command: list[str]
    writes: list[str]
    guide: str | None
    timeout: int


def _relative(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeclarationError(f"{where}: 空でない文字列で書く")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise DeclarationError(f"{where}: リポジトリの根からの相対パスで書く（絶対パスと .. は使えない）: {value!r}")
    return value


def parse(raw) -> list[Step]:
    if not isinstance(raw, dict):
        raise DeclarationError("release.json: 最上位はオブジェクトで書く")
    unknown = set(raw) - {"$schema", "version", "steps"}
    if unknown:
        raise DeclarationError(f"release.json: 知らない項目: {', '.join(sorted(unknown))}")
    version = raw.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise DeclarationError(f"version: 無いか未対応である: {version!r}（読めるのは {SUPPORTED_VERSIONS}）")
    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise DeclarationError("steps: 段の配列で書く（必須）")
    out = []
    for i, s in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(s, dict):
            raise DeclarationError(f"{where}: オブジェクトで書く")
        unknown = set(s) - STEP_KEYS
        if unknown:
            raise DeclarationError(f"{where}: 知らない項目: {', '.join(sorted(unknown))}")
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeclarationError(f"{where}.name: 空でない文字列で書く（必須）")
        if s.get("stage") not in STAGES:
            raise DeclarationError(f"{where}.stage: {' / '.join(STAGES)} のどれかで書く（必須）: {s.get('stage')!r}")
        command = s.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
            raise DeclarationError(f"{where}.command: 空でない文字列の配列で書く（必須）")
        writes = s.get("writes")
        if not isinstance(writes, list):
            raise DeclarationError(f"{where}.writes: パスの前置きの配列で書く（必須。何も書かない段は []）")
        writes = [_relative(w, f"{where}.writes[{j}]") for j, w in enumerate(writes)]
        guide = s.get("guide")
        if guide is not None:
            guide = _relative(guide, f"{where}.guide")
        timeout = s.get("timeout_seconds", DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise DeclarationError(f"{where}.timeout_seconds: 1 以上の整数で書く: {timeout!r}")
        out.append(Step(name, s["stage"], command, writes, guide, timeout))
    return out


def load(root: Path) -> list[Step] | None:
    path = root / DECLARATION
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise DeclarationError(f"release.json: JSON として読めない: {e}") from e
    return parse(raw)


# ---------- 変更の判定 ----------

def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          input=stdin).stdout


def status_paths(root: Path) -> set[str]:
    """`git status` が挙げたパス。名前の変更は元と先の両方を返す。"""
    tokens = _git(root, "status", "--porcelain=v1", "-z", "-uall").split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            paths.add(tokens[i])
            i += 1
    return paths


def digests(root: Path, paths: set[str]) -> dict[str, str | None]:
    """パスごとの内容の要約。無いパスは None。"""
    present = sorted(p for p in paths if (root / p).is_file() or (root / p).is_symlink())
    out: dict[str, str | None] = {p: None for p in paths}
    if present:
        hashes = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(present) + "\n").split()
        out.update(zip(present, hashes))
    return out


def allowed(path: str, writes: list[str]) -> bool:
    for w in writes:
        prefix = w.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# ---------- 実行 ----------

def selected(steps: list[Step], stage: str) -> list[Step]:
    return [s for s in steps if s.stage in (stage, "any")]


def expand(step: Step, version: str) -> list[str]:
    return [c.replace("{version}", version) for c in step.command]


def run_steps(root: Path, stage: str, version: str, dry_run: bool) -> int:
    steps = load(root)
    if steps is None:
        return 0
    for step in selected(steps, stage):
        command = expand(step, version)
        if dry_run:
            print(f"段: {step.name}（{step.stage}）")
            print(f"  command: {' '.join(command)}")
            print(f"  writes: {', '.join(step.writes) or '（何も書かない）'}")
            if step.guide:
                print(f"  guide: {step.guide}")
            continue
        try:
            before_paths = status_paths(root)
            before = digests(root, before_paths)
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"段: {step.name} → 実行しない（git status を取れない: {e}）", file=sys.stderr)
            return 1
        sys.stdout.flush()
        try:
            rc = subprocess.run(command, cwd=str(root), timeout=step.timeout).returncode
        except subprocess.TimeoutExpired:
            print(f"段: {step.name} → 時間切れ（{step.timeout} 秒）")
            return 1
        except OSError as e:
            print(f"段: {step.name} → 起動できない: {e}")
            return 1
        after_paths = status_paths(root)
        union = before_paths | after_paths
        after = digests(root, union)
        for p in after_paths - before_paths:  # 段の前は変更が無かった = HEAD の内容
            before[p] = _head_digest(root, p)
        changed = sorted(p for p in union if before[p] != after[p])
        outside = [p for p in changed if not allowed(p, step.writes)]
        print(f"段: {step.name} → {rc}")
        for p in changed:
            print(f"  変えたパス: {p}{'（書いてよい場所の外）' if p in outside else ''}")
        if rc != 0:
            return 1
        if outside:
            print(f"段: {step.name} が書いてよい場所（{', '.join(step.writes) or 'なし'}）の外を変えた", file=sys.stderr)
            return 1
        if step.guide:
            print(f"guide: {step.guide}")
    return 0


def _head_digest(root: Path, path: str) -> str | None:
    """段の前に変更の無かったパスの要約（HEAD の内容。HEAD に無ければ None）。"""
    try:
        return _git(root, "rev-parse", "--verify", "-q", f"HEAD:{path}").strip() or None
    except subprocess.CalledProcessError:
        return None


# ---------- 配布の決まった手順（#862） ----------

TOOL = "release"


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
    """README の更新案内の見出しを新しい版へ書き換える（検査は見出しを現行の版の 1 つだけに求める）。"""
    rel = readme.relative_to(ed.root).as_posix()
    if not readme.is_file():
        ed.manual.append(f"{rel} が無い（更新案内の見出し）")
        return
    lines = ed.lines(readme)
    new_h = f"## v{ed.new} へ更新するとき"
    if new_h in lines:
        return
    rx = re.compile(r"^## v\S+ へ更新するとき$")
    at = next((i for i, l in enumerate(lines) if rx.match(l)), None)
    if at is None:
        ed.manual.append(f"{rel}: 更新案内の見出しが無い（「{new_h}」を手で足す）")
        return
    lines[at] = new_h
    ed.save(readme, lines)
    if base_of(ed.old) != base_of(ed.new):
        ed.manual.append(f"{rel}: 更新案内の本文を v{ed.new} の変更へ書き直す（changelog が PR の一覧へ差し替える）")


def bump_versioning_doc(ed):
    """docs/versioning-and-distribution.md の「版の付け方と開発版の配布」章の正式版の版数。"""
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
        raise StepError(f"旧版を plugin.json から読めない: {e}", 2)
    new = a.to
    if old == new:
        raise StepError(f"旧版と新版が同じ: {old}")
    ed = Editor(root, old, new)
    desc_re = r'^\s*"description"\s*:.*\(v' + re.escape(old) + r"\)"

    for rel, _ in ((".claude-plugin/plugin.json", True), (".codex-plugin/plugin.json", False),
                   ("dev.agy/plugin.json", False), ("plugin.json", False)):
        f = pdir / rel
        if not f.is_file():
            continue
        ed.sub(f, r'^\s*"version"\s*:', f"{rel} の version")
        if f"(v{old})" in f.read_text(encoding="utf-8"):
            ed.sub(f, desc_re, f"{rel} の description")

    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        s, e = marketplace_range(ed.lines(mp), a.plugin)
        if s is None:
            ed.manual.append(f".claude-plugin/marketplace.json に {a.plugin} の項目が無い")
        elif any(f"(v{old})" in l for l in ed.lines(mp)[s:e]):
            ed.sub(mp, desc_re, "marketplace.json の description", start=s, stop=e)

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

    expected = []
    if base_of(old) != base_of(new):
        expected.append(f"更新案内の見出しが 2 個ある（v{new} / v{old}）")
        ed.manual.append(f"{pdir.relative_to(root).as_posix()}/README.md: 更新案内の v{old} の節を片付ける（見出しを 1 つにする）")
    ok, summary = run_staleness(root, expected)
    if ok is None:
        ed.manual.append(summary)
    items = ([{"kind": "file", "name": f, "result": "updated"} for f in ed.files]
             + [{"kind": "manual", "name": m, "result": "manual"} for m in ed.manual])
    metrics = {"plugin": a.plugin, "from": old, "to": new, "staleness": summary}
    if ok is False:
        emit(result(TOOL, "stopped", f"{old} → {new} の後に check-doc-staleness.py が失敗", items, metrics))
    emit(result(TOOL, "ok", f"{a.plugin} を {old} → {new} へ上げた（{len(ed.files)} ファイル・手で直す {len(ed.manual)} 件）",
                items, metrics, next="items の manual を手で直す" if ed.manual else None))


def pr_titles(root, prs):
    items = []
    for n in prs:
        d = gh_json(root, ["pr", "view", str(n), "--json", "title"], f"gh pr view {n}")
        try:
            title = d["title"].strip()
        except (TypeError, KeyError, AttributeError):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        items.append((n, f"- {title}（#{n}）"))
    return items


def cmd_changelog(a):
    root = git_root(a.root)
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        raise StepError("CHANGELOG.md が無い", EXIT_PRECONDITION)
    items = pr_titles(root, a.prs)
    sections = []

    # CHANGELOG.md（見出しは基底の版。開発版の接尾辞は載せない）
    lines = cl.read_text(encoding="utf-8").split("\n")
    head = f"## [{a.plugin} {base_of(a.version)}]"
    at = next((i for i, l in enumerate(lines) if l == head or l.startswith(head + " ")), None)
    if at is None:
        first = next((i for i, l in enumerate(lines) if l.startswith("## [")), len(lines))
        block = [f"{head} - {today()}", ""] + [b for _, b in items] + [""]
        if first == len(lines) and lines and lines[-1] != "":
            block = [""] + block
        lines[first:first] = block
        sections.append({"kind": "section", "name": "CHANGELOG.md", "result": "added",
                         "heading": block[0] or block[1], "added": [n for n, _ in items]})
    else:
        end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        body = "\n".join(lines[at:end])
        add = [(n, b) for n, b in items if f"#{n}）" not in body and f"#{n})" not in body]
        ins = end
        while ins - 1 > at and lines[ins - 1].strip() == "":
            ins -= 1
        lines[ins:ins] = [b for _, b in add]
        sections.append({"kind": "section", "name": "CHANGELOG.md", "result": "added",
                         "heading": lines[at], "added": [n for n, _ in add]})
    cl.write_text("\n".join(lines), encoding="utf-8")

    # plugin の README の更新案内: 見出しから次の同じ深さの見出しまでを、この版の PR の一覧へ差し替える
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
            end = next((j for j in range(i + 1, len(rl)) if rl[j].startswith("## ")), len(rl))
            if not any(f"（#{n}）" in l for l in rl[i:end] for n, _ in items):
                rl[i + 1:end] = [""] + [b for _, b in items] + [""]
                readme.write_text("\n".join(rl), encoding="utf-8")
                sections.append({"kind": "section", "name": readme.relative_to(root).as_posix(),
                                 "result": "replaced", "heading": h, "added": [n for n, _ in items]})

    readme_done = any(s["result"] == "replaced" for s in sections)
    emit(result(TOOL, "ok", f"{len(a.prs)} 件の PR を {len(sections)} 箇所へ並べた", sections,
                {"version": a.version, "prs": len(a.prs)},
                next="更新案内の本文を利用者向けの説明へ書き直す" if readme_done else None))


def owner_repo(root):
    slug = repo_slug(root)
    if not slug or "--" not in slug:
        raise StepError("リポジトリの owner/name を決められない", EXIT_PRECONDITION)
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
        res.append((name, p.returncode == 0, "\n".join((p.stdout + p.stderr).strip().split("\n")[-3:])))
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
        raise StepError(f"作った PR の番号を読めない: {p.stdout.strip()[:200]}", 2)
    return int(m.group(1))


def pr_check_buckets(root, n):
    p = run(["gh", "pr", "checks", str(n), "--json", "name,bucket"], cwd=root, check=False)
    try:
        return json.loads(p.stdout or "[]") or []
    except ValueError:
        return []


def wait_and_merge(root, n):
    """PR のチェックを merge-when-green で待ってマージする。止まったらその summary で止める。

    merge-when-green は待ちの上限を持ち、実行が終わったのに pending のまま取り残されたチェックを
    1 度だけ再実行する（`gh pr checks --watch` は上限が無く、取り残されたチェックを待ち続けた）。
    後片付けは配布の手順が持つので行わせない。
    """
    script = Path(__file__).resolve().parent / "merged-steps.py"
    p = run([sys.executable, str(script), "merge-when-green", str(n), "--no-cleanup", "--interval", "5"],
            cwd=root, check=False)
    try:
        out = json.loads((p.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {}
    if p.returncode != 0:
        raise StepError(f"PR #{n} をマージできない: {out.get('summary') or p.stderr.strip()[:300]}")
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
        raise StepError(f"作業ツリーのブランチが release/v{ver} でない: {branch}", EXIT_PRECONDITION)
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StepError("作業ツリーにコミットしていない変更がある（bump と changelog をコミットしてから呼ぶ）",
                        EXIT_PRECONDITION)

    git(root, "push", "-q", "-u", "origin", "HEAD")

    # 開発版の PR（release/v<版> → develop）
    pr = find_pr(root, branch, "develop", states=("OPEN", "MERGED"))
    if pr is None:
        section = changelog_section(root, ver)
        mark = {True: "pass", False: "fail", None: "skip"}
        body = "\n".join([
            f"ndf v{ver} のリリース（{a.channel}）。対象の plugin: {', '.join(plugins)}",
            "", "## 含む PR", "", section or "（CHANGELOG.md に該当の節が無い）", "", "## 検査の結果", "",
            *[f"- {name}: {mark[ok]}" + (f"（{tail.splitlines()[-1]}）" if tail else "")
              for name, ok, tail in run_checks(root)],
            "", "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        ])
        pr = {"number": create_pr(root, "develop", branch, f"Release: ndf v{ver}", body), "state": "OPEN"}
    release_pr = pr["number"]
    release_commit = wait_and_merge(root, release_pr) if pr["state"] == "OPEN" else merge_commit_of(root, release_pr)

    metrics = {"channel": a.channel, "version": ver, "release_pr": release_pr, "main_pr": None, "tag": None,
               "merge_commit": release_commit, "plugins": ",".join(plugins)}
    items = [{"kind": "pr", "name": f"#{release_pr}", "result": "merged", "base": "develop"}]
    if a.channel == "dev":
        emit(result(TOOL, "ok", f"ndf v{ver} を develop へ出した（#{release_pr}）", items, metrics))

    # 本番: develop → main、タグ、GitHub Release（利用者の承認を得てから呼ぶ）
    tag = f"ndf--v{ver}"
    git(root, "fetch", "-q", "origin", "--tags")
    if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0:
        raise StepError(f"タグ {tag} は既にある")
    mp = find_pr(root, "develop", "main", states=("OPEN",))
    main_pr = mp["number"] if mp else create_pr(
        root, "main", "develop", f"Release: ndf v{ver} を main へ",
        f"ndf v{ver} を main へ出す（開発版の PR #{release_pr}）。\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
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

    metrics.update({"main_pr": main_pr, "tag": tag, "merge_commit": merge})
    items += [{"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": "main"},
              {"kind": "tag", "name": tag, "result": "pushed"},
              {"kind": "release", "name": tag, "result": "created"}]
    emit(result(TOOL, "ok", f"ndf v{ver} を main へ出し、{tag} と GitHub Release を作った", items, metrics))


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
            raise StepError("前のタグを決められない（--prev-tag を渡す）", EXIT_PRECONDITION)
    dev = git(root, "rev-parse", "origin/develop").stdout.strip()

    stat = git(root, "diff", "--shortstat", "origin/main...origin/develop").stdout.strip()
    files = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", stat)) else 0
    ins = int(m.group(1)) if (m := re.search(r"(\d+) insertions?", stat)) else 0
    dels = int(m.group(1)) if (m := re.search(r"(\d+) deletions?", stat)) else 0

    items, rows = [], []
    for n in a.prs:
        d = gh_json(root, ["pr", "view", str(n), "--json", "number,title,state,mergeCommit,url"],
                    f"gh pr view {n}") or {}
        oid = (d.get("mergeCommit") or {}).get("oid") or ""
        checks = pr_check_buckets(root, n)
        passed = sum(1 for c in checks if c.get("bucket") == "pass")
        url = d.get("url") or f"https://github.com/{repo}/pull/{n}"
        items.append({"kind": "pr", "name": f"#{n}", "result": str(d.get("state", "")).lower() or "unknown",
                      "url": url, "title": d.get("title", ""), "merge_commit": oid,
                      "checks_passed": passed, "checks": len(checks)})
        rows.append(f"#{n} {d.get('title', '')}（{d.get('state', '')}・"
                    f"{oid[:8] if oid else '—'}・CI {passed}/{len(checks)}）")

    compare = f"https://github.com/{repo}/compare/{prev}...{dev}"
    path = approval_present(
        TOOL, f"v{a.version}", title=f"ndf v{a.version} の本番への配布",
        targets=[{"url": compare, "title": f"{prev} → develop（{dev[:8]}）", "base_head": "main ← develop"}],
        change=f"`main...develop` の差分 {files} ファイル / +{ins} / −{dels}",
        judge=[("版数", a.version), ("含む PR", "\n".join(rows) or "—"),
               ("配る中身", NOTES_PENDING),
               ("検証への配布で確かめたこと", NOTES_PENDING)],
        consent=[f"ndf v{a.version} を main へ出し、タグ {cur_tag} と GitHub Release を作る"],
        rollback=(f"タグ {cur_tag} と GitHub Release を消し、main を {prev} の内容へ戻す PR を出す。"
                  "導入済みの利用者の環境は利用者の側の操作（前の版の導入し直し）でしか戻せない。"))
    emit(result(TOOL, "gate", f"ndf v{a.version} の本番承認の提示物を書いた（PR {len(a.prs)} 件）", items,
                {"files": files, "insertions": ins, "deletions": dels, "prev_tag": prev, "compare": compare},
                path, f"利用者の承認を得たら release-steps.py release --version {a.version} --channel prod"),
         EXIT_GATE)


CHANGES_HEADING = "## 利用者向けの変化"
RISKS_HEADING = "## 未検証・残る危険"
NOTES_PENDING = "（release-steps.py notes --approval が PR 本文の「利用者向けの変化」から書く）"
RUNTIME_NAMES = {"claude": "Claude Code", "codex": "Codex", "kiro": "Kiro", "agy": "Antigravity"}


def body_section(body, heading):
    """Markdown の本文から heading の節の中身の行（空行を除く）を返す。"""
    out, inside = [], False
    for line in (body or "").splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside and line.strip():
            out.append(line.rstrip())
    return out


def change_items(lines, n):
    """節の行を「- 本文（#n）」の箇条へ直す。続きの行（字下げ）は前の項目へつなぐ。「無し」だけなら空。"""
    items = []
    for line in lines:
        text = line.strip()
        bullet = re.match(r"^[-*]\s+(.*)$", text)
        if bullet or not items or not line[:1].isspace():
            items.append((bullet.group(1) if bullet else text).strip())
        else:
            items[-1] += " " + text
    items = [i for i in items if i and i not in ("無し", "なし")]
    return [i if f"#{n}" in i else f"{i}（#{n}）" for i in items]


def pr_notes(root, prs):
    """PR ごとに (番号, 利用者向けの変化の箇条, 未検証・残る危険の箇条, 題名で代えたか) を返す。"""
    out = []
    for n in prs:
        d = gh_json(root, ["pr", "view", str(n), "--json", "title,body"], f"gh pr view {n}")
        if not isinstance(d, dict) or not isinstance(d.get("title"), str):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        body = d.get("body") or ""
        items = change_items(body_section(body, CHANGES_HEADING), n)
        risks = change_items(body_section(body, RISKS_HEADING), n)
        out.append((n, items or [f"{d['title'].strip()}（#{n}）"], risks, not items))
    return out


def replace_section(lines, at, block):
    """lines[at] の見出しから次の `## ` までの中身を block に差し替える。"""
    end = next((j for j in range(at + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    lines[at + 1:end] = [""] + block + [""]


def write_notes(root, version, plugin, bullets):
    items = []
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        raise StepError("CHANGELOG.md が無い", EXIT_PRECONDITION)
    lines = cl.read_text(encoding="utf-8").split("\n")
    head = f"## [{plugin} {base_of(version)}]"
    at = next((i for i, l in enumerate(lines) if l == head or l.startswith(head + " ")), None)
    if at is None:
        raise StepError(f"CHANGELOG.md に {head} の節が無い（先に changelog を走らせる）", EXIT_PRECONDITION)
    replace_section(lines, at, bullets)
    cl.write_text("\n".join(lines), encoding="utf-8")
    items.append({"kind": "section", "name": "CHANGELOG.md", "result": "replaced", "heading": lines[at],
                  "lines": len(bullets)})
    try:
        pdir = plugin_dir(root, plugin)
    except StepError:
        pdir = None
    readme = pdir / "README.md" if pdir else None
    h = f"## v{version} へ更新するとき"
    if readme and readme.is_file():
        rl = readme.read_text(encoding="utf-8").split("\n")
        if h in rl:
            replace_section(rl, rl.index(h), bullets)
            readme.write_text("\n".join(rl), encoding="utf-8")
            items.append({"kind": "section", "name": readme.relative_to(root).as_posix(), "result": "replaced",
                          "heading": h, "lines": len(bullets)})
    return items


def cell(text):
    return text.replace("|", "\\|").replace("\n", "<br>")


def write_approval(path, version, bullets, risks, verified, ref):
    if not path.is_file():
        raise StepError(f"提示物 {path} が無い（先に approval-facts を走らせる）", EXIT_PRECONDITION)
    names = "・".join(RUNTIME_NAMES.get(r, r) for r in verified)
    check = (f"{names} の {len(verified)} 経路で {ref} から ndf {version} を導入し、版と中身が一致した"
             f"（release-verification-steps.py verify-install）" if verified else "—")
    rows = {"配る中身": "\n".join(bullets) or "—", "検証への配布で確かめたこと": check}
    lines = path.read_text(encoding="utf-8").split("\n")
    done = []
    for i, line in enumerate(lines):
        for key, value in rows.items():
            if line.startswith(f"| {key} |"):
                lines[i] = f"| {key} | {cell(value)} |"
                done.append(key)
    missing = [k for k in rows if k not in done]
    if missing:
        raise StepError(f"提示物に欄が無い: {', '.join(missing)}", EXIT_PRECONDITION)
    if RISKS_HEADING not in lines:
        at = next((i for i, l in enumerate(lines) if l == "## 同意を求めること"), len(lines))
        lines[at:at] = [RISKS_HEADING, "", *(risks or ["- PR の本文に記載が無い"]), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return [{"kind": "cell", "name": k, "result": "written"} for k in rows] + [
        {"kind": "section", "name": RISKS_HEADING, "result": "written", "lines": len(risks)}]


def cmd_notes(a):
    root = git_root(a.root)
    notes = pr_notes(root, a.prs)
    bullets = [f"- {i}" for _, items, _, _ in notes for i in items]
    risks = [f"- {i}" for _, _, rs, _ in notes for i in rs]
    fallback = sum(1 for *_, by_title in notes if by_title)
    if a.approval:
        path = Path(a.approval)
        path = path if path.is_absolute() else root / path
        verified = [r for r in (a.verified or "").split(",") if r]
        items = write_approval(path, a.version, bullets, risks, verified, a.ref)
        emit(result(TOOL, "ok", f"提示物の欄を {len(a.prs)} 件の PR から書いた", items,
                    {"version": a.version, "prs": len(a.prs), "lines": len(bullets), "approval": str(path)}))
        return
    items = write_notes(root, a.version, a.plugin, bullets)
    emit(result(TOOL, "ok", f"{len(a.prs)} 件の PR の利用者向けの変化を {len(items)} 箇所へ書いた", items,
                {"version": a.version, "prs": len(a.prs), "lines": len(bullets), "fallback": fallback}))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="リポジトリが宣言した配布の段と、配布の決まった手順を走らせる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="段階に合う段を順に実行する")
    r.add_argument("--root", type=Path, default=Path("."))
    r.add_argument("--stage", choices=("production", "verification"), required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check", help="宣言を読むだけ")
    c.add_argument("--root", type=Path, default=Path("."))

    common = common_parser()
    p = sub.add_parser("bump", parents=[common], help="plugin の版数を持つ箇所を旧版から新版へ上げる")
    p.add_argument("--plugin", required=True, help="ndf / playwright-kit / plugins/mcp の名前（例 mcp-serena）")
    p.add_argument("--to", required=True, type=version_arg)
    p.set_defaults(func=cmd_bump)

    p = sub.add_parser("changelog", parents=[common], help="CHANGELOG.md と plugin の README の更新案内へ PR のタイトルを並べる")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--plugin", default="ndf")
    p.set_defaults(func=cmd_changelog)

    p = sub.add_parser("release", parents=[common], help="release/v<版> を develop へマージし、prod なら main へ出してタグと Release を作る")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--channel", required=True, choices=("dev", "prod"))
    p.add_argument("--plugins", default="ndf", help="カンマ区切り（例 ndf,mcp-serena）")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("approval-facts", parents=[common], help="本番承認の提示物のうち機械で作れる部分を書き出す")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--prev-tag")
    p.set_defaults(func=cmd_approval_facts)

    p = sub.add_parser("notes", parents=[common],
                       help="PR 本文の「利用者向けの変化」から CHANGELOG と更新案内（--approval なら提示物の欄）を組む")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--plugin", default="ndf")
    p.add_argument("--approval", help="本番承認の提示物。渡すと「配る中身」「検証への配布で確かめたこと」の欄を書く")
    p.add_argument("--verified", default="", help="--approval: 導入を確かめた経路（カンマ区切り。例 claude,codex,kiro）")
    p.add_argument("--ref", default="develop", help="--approval: 検証への配布で導入した ref")
    p.set_defaults(func=cmd_notes)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd not in ("run", "check"):
        return main_with(ap, lambda a: TOOL, argv)
    root = args.root.resolve()
    try:
        if args.cmd == "check":
            return 0 if load(root) is not None else 2
        return run_steps(root, args.stage, args.version, args.dry_run)
    except DeclarationError as e:
        print(f"宣言を読めない（{DECLARATION}）: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
