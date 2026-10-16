"""`waiting.md` の待ちの雛形を `timeout` / `gtimeout` の無い `PATH` で打つ（#1297）。

macOS の標準の状態には `timeout` が無く、雛形が 127 で終わっていた。雛形は上限を bash の `SECONDS` で数え、
条件が成り立てば 0、上限に達すれば 124 で終わり、最後に `exit=<終了コード>` を出す。
"""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from waiting_templates import attention_template, done_template

ATTENTION = '{"kind": "attention", "text": "x"}\n'
SHORT = 2  # 条件を作らないときの上限
LONG = 8  # 1 秒後に条件を作るときの上限


@pytest.fixture()
def bare_bin(tmp_path):
    """雛形が使う bash / cat / grep / sleep だけを置いた PATH。"""
    d = tmp_path / "bin"
    d.mkdir()
    for name in ("bash", "cat", "grep", "sleep"):
        (d / name).symlink_to(shutil.which(name))
    return d


def start(cmd, bin_dir):
    env = {"PATH": str(bin_dir)}
    assert shutil.which("timeout", path=env["PATH"]) is None
    assert shutil.which("gtimeout", path=env["PATH"]) is None
    return subprocess.Popen(
        [str(bin_dir / "bash"), "-c", cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ), time.monotonic()


def finish(proc, t0, limit):
    out, err = proc.communicate(timeout=limit + 10)
    took = time.monotonic() - t0
    assert proc.returncode in (0, 124), (proc.returncode, err)
    assert out.strip().splitlines()[-1] == f"exit={proc.returncode}"
    assert took <= limit + 10
    return proc.returncode, took


def test_done_template_ends_0_when_marker_appears(tmp_path, bare_bin):
    place = tmp_path / "report.md"
    proc, t0 = start(done_template(str(place), LONG), bare_bin)
    time.sleep(1)
    (tmp_path / "report.md.done").write_text("")
    rc, _ = finish(proc, t0, LONG)
    assert rc == 0


def test_done_template_ends_124_at_limit(tmp_path, bare_bin):
    proc, t0 = start(done_template(str(tmp_path / "report.md"), SHORT), bare_bin)
    rc, took = finish(proc, t0, SHORT)
    assert rc == 124
    assert took >= SHORT


def _state(tmp_path, lines=0):
    s = tmp_path / "plan-state"
    s.mkdir()
    (s / "progress.jsonl").write_text('{"kind": "step", "text": "s"}\n' + ATTENTION * lines)
    return s


def test_attention_template_ends_0_on_report(tmp_path, bare_bin):
    s = _state(tmp_path)
    proc, t0 = start(attention_template(str(s), LONG), bare_bin)
    time.sleep(1)
    (s / "report.md").write_text("done\n")
    assert finish(proc, t0, LONG)[0] == 0


def test_attention_template_ends_0_on_new_attention(tmp_path, bare_bin):
    s = _state(tmp_path)
    proc, t0 = start(attention_template(str(s), LONG), bare_bin)
    time.sleep(1)
    with (s / "progress.jsonl").open("a") as f:
        f.write(ATTENTION)
    assert finish(proc, t0, LONG)[0] == 0


@pytest.mark.parametrize("existing", [0, 2])
def test_attention_template_ends_124_without_news(tmp_path, bare_bin, existing):
    # 起動の時点で既にある attention の行は新しい知らせと読まない
    s = _state(tmp_path, existing)
    proc, t0 = start(attention_template(str(s), SHORT), bare_bin)
    rc, took = finish(proc, t0, SHORT)
    assert rc == 124
    assert took >= SHORT
