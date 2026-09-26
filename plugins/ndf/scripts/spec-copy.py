#!/usr/bin/env python3
"""spec-copy.py: 課題の本文にある要求の写しを作り、本文と写しの食い違いを返す。

    python3 spec-copy.py write <課題> <ファイル> [--repo OWNER/REPO]
    python3 spec-copy.py check <課題> <ファイル> [--repo OWNER/REPO]

要求の正は課題の本文で、`issues/` のファイルは設計 PR と一緒にコミットする写しである。
`write` は先頭の見出しと「正は課題の本文」の 1 行に続けて、本文の `## 進行` より前の全文を書く。
`check` は本文の `##` の節（`## 進行` を除く）ごとに、写しに同じ中身の節があるかを見る。写しにしか
無い節（計画など）は許す。行末の空白と節の後ろの空行は比べない。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "spec-copy"`）。終了コードは
0 = 書いた・一致した / 1 = 無い節か中身の違う節がある / 2 = 本文か写しを読めない。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, main_with, result  # noqa: E402
import gh_parts  # noqa: E402

TOOL = "spec-copy"
PROGRESS = "進行"
MARKER = "正は課題の本文"
SECTION = re.compile(r"^##\s+(.*?)\s*$")


def fetch_issue(number: str, repo: str | None) -> dict:
    """課題の題と本文。GraphQL が上限なら `gh_parts.view_json` が REST で読み直す。"""
    if not str(number).isdigit():
        raise StepError(f"#{number} の本文を読めない: 課題の番号でない", EXIT_UNREADABLE)
    r = gh_parts.view_json("issue", int(number), "title,body", repo=repo)
    if r.returncode == 127:
        raise StepError(f"gh を起動できない: {r.stderr.strip()[:300]}", EXIT_UNREADABLE)
    if r.returncode != 0:
        raise StepError(f"#{number} の本文を読めない: {r.stderr.strip()[:300]}", EXIT_UNREADABLE)
    try:
        data = json.loads(r.stdout)
    except ValueError as e:
        raise StepError(f"#{number} の本文を読めない: {e}", EXIT_UNREADABLE)
    if not isinstance(data, dict) or not isinstance(data.get("body"), str):
        raise StepError(f"#{number} の本文が無い", EXIT_UNREADABLE)
    return data


def before_progress(body: str) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        m = SECTION.match(line)
        if m and m.group(1) == PROGRESS:
            lines = lines[:i]
            break
    return "\n".join(lines).rstrip() + "\n"


def sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """`##` の節ごとに行を分ける。見出しより前の行は先頭の分として返す。"""
    head: list[str] = []
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = SECTION.match(line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        (out[cur] if cur is not None else head).append(line)
    return head, out


def normalize(lines: list[str]) -> list[str]:
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def cmd_write(a):
    data = fetch_issue(a.issue, a.repo)
    title = str(data.get("title") or "").strip()
    text = f"# #{a.issue}: {title}\n\n{MARKER}（#{a.issue}）で、この文書はその写しである。" \
           f"`spec-copy.py write` で作り直す。手で直さない。\n\n" + before_progress(data["body"]).lstrip("\n")
    path = Path(a.file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise StepError(f"{a.file} を書けない: {e}", EXIT_UNREADABLE)
    emit(result(TOOL, "ok", f"#{a.issue} の本文から {a.file} を書いた", [{"name": a.file, "result": "written"}]))


def cmd_check(a):
    data = fetch_issue(a.issue, a.repo)
    try:
        copy = Path(a.file).read_text(encoding="utf-8")
    except OSError as e:
        raise StepError(f"{a.file} を読めない: {e}", EXIT_UNREADABLE)
    b_head, b_secs = sections(before_progress(data["body"]))
    c_head, c_secs = sections(copy)
    c_head = [ln for ln in c_head if not ln.startswith("# ") and MARKER not in ln]
    items = []
    pairs = [("", b_head, c_head)] if normalize(b_head) else []
    pairs += [(name, lines, c_secs.get(name)) for name, lines in b_secs.items() if name != PROGRESS]
    for name, want, have in pairs:
        label = name or "（見出しより前）"
        if have is None:
            items.append({"name": label, "result": "missing", "diff": ""})
            continue
        w, h = normalize(want), normalize(have)
        if w != h:
            diff = "\n".join(difflib.unified_diff(w, h, "本文", "写し", lineterm="", n=1))
            items.append({"name": label, "result": "differs", "diff": diff})
    if items:
        emit(result(TOOL, "stopped", f"{len(items)} 節が本文と食い違う。本文に合わせて write で作り直す: "
                                     + " / ".join(it["name"] for it in items), items), EXIT_VIOLATION)
    emit(result(TOOL, "ok", f"{a.file} は #{a.issue} の本文と一致する", [], {"sections": len(pairs)}))


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func in (("write", cmd_write), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("issue")
        p.add_argument("file")
        p.add_argument("--repo")
        p.set_defaults(func=func)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
