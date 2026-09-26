"""spec-copy.py: 課題の本文（正）から写しを作り、食い違いを返す。gh は偽物に差し替える。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "spec-copy.py"

BODY = """## 依頼（原文）

> 店を開きたい

## 受け入れ条件

- [ ] 注文を受けられる
- [ ] 荷物を送れる

## 進行

- [x] 要求 — 2026-09-25
"""


def fake_gh(tmp_path, body, title="店の開設", code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "view.json").write_text(json.dumps({"title": title, "body": body}, ensure_ascii=False))
    (bindir / "gh").write_text(f'#!/bin/bash\n[ "$2" = view ] && cat {tmp_path / "view.json"}\nexit {code}\n')
    (bindir / "gh").chmod(0o755)
    return dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")


def run(env, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)
    lines = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(lines[-1]) if lines else None)


def test_write_copies_body_before_progress(tmp_path):
    env = fake_gh(tmp_path, BODY)
    out_file = tmp_path / "issues" / "issue-7-requirements.md"
    code, out = run(env, "write", "7", str(out_file))
    assert code == 0 and out["tool"] == "spec-copy"
    text = out_file.read_text()
    head, _, rest = text.partition("\n\n")
    assert head == "# #7: 店の開設"
    marker, _, copied = rest.partition("\n\n")
    assert "正は課題の本文" in marker
    assert copied == BODY.split("## 進行")[0].rstrip() + "\n"
    assert run(env, "check", "7", str(out_file))[0] == 0


def test_check_allows_sections_only_in_copy(tmp_path):
    env = fake_gh(tmp_path, BODY)
    out_file = tmp_path / "copy.md"
    run(env, "write", "7", str(out_file))
    out_file.write_text(out_file.read_text() + "\n## 計画\n\n- 手順 1  \n\n\n")
    assert run(env, "check", "7", str(out_file))[0] == 0


def test_check_fails_when_body_section_changes_or_is_missing(tmp_path):
    env = fake_gh(tmp_path, BODY)
    out_file = tmp_path / "copy.md"
    run(env, "write", "7", str(out_file))
    env = fake_gh(tmp_path, BODY.replace("荷物を送れる", "荷物を翌日に送れる")
                   .replace("## 進行", "## 用語\n\n| 語 | 意味 |\n\n## 進行"))
    code, out = run(env, "check", "7", str(out_file))
    assert code == 1
    assert {(it["name"], it["result"]) for it in out["items"]} == {("受け入れ条件", "differs"), ("用語", "missing")}


def test_progress_section_is_not_compared(tmp_path):
    env = fake_gh(tmp_path, BODY)
    out_file = tmp_path / "copy.md"
    run(env, "write", "7", str(out_file))
    env = fake_gh(tmp_path, BODY + "- [x] 設計 — 2026-09-26\n")
    assert run(env, "check", "7", str(out_file))[0] == 0


@pytest.mark.parametrize("sub", ["write", "check"])
def test_unreadable_body_is_2(tmp_path, sub):
    env = fake_gh(tmp_path, BODY, code=1)
    out_file = tmp_path / "copy.md"
    out_file.write_text("x")
    assert run(env, sub, "7", str(out_file))[0] == 2


def test_heading_inside_fence_is_not_a_section(tmp_path):
    """囲みの中の `## 進行` は節の見出しにしない（lib/md.py。行の字面で分けていた頃は、そこで本文を切った）。"""
    body = BODY.replace("## 受け入れ条件", "```md\n## 進行\n```\n\n## 受け入れ条件")
    env = fake_gh(tmp_path, body)
    out_file = tmp_path / "copy.md"
    run(env, "write", "7", str(out_file))
    assert "注文を受けられる" in out_file.read_text()
