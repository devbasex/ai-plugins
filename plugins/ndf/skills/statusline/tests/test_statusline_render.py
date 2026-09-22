"""statusline.sh の表示（メインとサブエージェントの使用量）を検証する。

擬似の transcript（<tmp>/sess.jsonl）とサブエージェントの記録
（<tmp>/sess/subagents/agent-<id>.jsonl / .meta.json）を作り、標準入力へ JSON を渡して
`bash statusline.sh` の出力を突き合わせる。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

for _cmd in ("bash", "jq"):
    if shutil.which(_cmd) is None:
        pytest.skip(f"{_cmd} not available", allow_module_level=True)

STATUSLINE = Path(__file__).resolve().parents[3] / "scripts" / "statusline.sh"
RED = "\033[0;31m"
ANSI = re.compile(r"\033\[[0-9;]*m")


def _usage(tokens: int) -> dict:
    return {"input_tokens": tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def write_agent(root: Path, agent_id: str, *, tokens: int = 10_000, end: str = "tool_use",
                model: str = "claude-opus-5", description: str | None = None,
                age: float = 0) -> Path:
    """end: tool_use（実行中）/ end_turn（終了）/ text（stop_reason 無しの text で終わる）"""
    sub = root / "sess" / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    content = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}] if end == "tool_use" \
        else [{"type": "text", "text": "done"}]
    stop = "end_turn" if end == "end_turn" else None
    lines = [
        {"type": "user", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "message": {"model": model, "content": content,
                                          "stop_reason": stop, "usage": _usage(tokens)}},
    ]
    f = sub / f"agent-{agent_id}.jsonl"
    f.write_text("".join(json.dumps(line) + "\n" for line in lines))
    if description is not None:
        (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps({"description": description}))
    if age:
        t = time.time() - age
        os.utime(f, (t, t))
    return f


def render(root: Path, total: int = 100_000, size: int | None = None) -> str:
    cw: dict = {"total_input_tokens": total}
    if size is not None:
        cw["context_window_size"] = size
    payload = {"transcript_path": str(root / "sess.jsonl"),
               "model": {"display_name": "Opus 5 (1M context)"}, "context_window": cw}
    r = subprocess.run(["bash", str(STATUSLINE)], input=json.dumps(payload),
                       capture_output=True, text=True, check=True)
    return r.stdout


def plain(out: str) -> str:
    return ANSI.sub("", out)


def test_tool_use_is_shown_and_end_turn_is_not(tmp_path):
    write_agent(tmp_path, "aaaa1111", tokens=42_000, description="実行中の担当")
    write_agent(tmp_path, "bbbb2222", tokens=77_000, end="end_turn", description="終わった担当")
    out = plain(render(tmp_path))
    assert "実行中の 42k" in out
    assert "終わった" not in out


def test_text_older_than_30s_is_hidden(tmp_path):
    write_agent(tmp_path, "aaaa1111", end="text", age=60, description="古いtext")
    write_agent(tmp_path, "bbbb2222", end="text", description="新しいtext")
    out = plain(render(tmp_path))
    assert "古いte" not in out
    assert "新しいt" in out


def test_top3_and_rest_count(tmp_path):
    for i, tok in enumerate([10_000, 20_000, 30_000, 40_000, 50_000]):
        write_agent(tmp_path, f"id{i}xxxx", tokens=tok, description=f"担当{i}号機")
    out = plain(render(tmp_path))
    assert "│ 担当4号 50k · 担当3号 40k · 担当2号 30k +2]" in out


def test_haiku_warns_at_150k_but_opus_does_not(tmp_path):
    write_agent(tmp_path, "aaaa1111", tokens=160_000, model="claude-haiku-4-5", description="はいく")
    out = render(tmp_path)
    assert f"{RED}はいく 160k" in out
    shutil.rmtree(tmp_path / "sess")
    write_agent(tmp_path, "aaaa1111", tokens=160_000, model="claude-opus-5", description="おーぱす")
    assert RED not in render(tmp_path)


def test_main_warns_over_500k(tmp_path):
    assert RED + "553k" in render(tmp_path, total=553_000)
    assert RED not in render(tmp_path, total=160_000)


def test_main_with_200k_window_warns_at_150k(tmp_path):
    assert RED + "160k" in render(tmp_path, total=160_000, size=200_000)
    assert RED not in render(tmp_path, total=160_000, size=1_000_000)


def test_label_from_description_or_id(tmp_path):
    write_agent(tmp_path, "abcd9999", tokens=12_000, description="Fix PR comments")
    write_agent(tmp_path, "wxyz8888", tokens=11_000)
    out = plain(render(tmp_path))
    assert "FixP 12k" in out
    assert "wxyz 11k" in out


def test_control_chars_in_description_are_dropped(tmp_path):
    # 説明に ESC などの制御文字があっても端末へ出さない（画面消去などを実行させない）
    write_agent(tmp_path, "aaaa1111", tokens=21_000, description="\u001b[2J\u009b画面消去")
    out = render(tmp_path)
    assert "\u001b[2J" not in out
    assert "\u009b" not in out
    assert "[2J画 21k" in plain(out)

def test_path_with_spaces(tmp_path):
    root = tmp_path / "my project dir"
    write_agent(root, "aaaa1111", tokens=33_000, description="空白下")
    assert "空白下 33k" in plain(render(root))
