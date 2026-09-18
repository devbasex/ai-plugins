from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "skill-stats.py"


def _fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, str]]:
    plugin_root = tmp_path / "plugin"
    skill_dir = plugin_root / "skills" / "sample"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Use for samples（サンプル）.\n---\n",
        encoding="utf-8",
    )

    transcript_dir = tmp_path / ".claude" / "projects" / "-work-demo"
    transcript_dir.mkdir(parents=True)
    events = [
        {"type": "user", "cwd": "/work/demo", "message": {"content": "サンプルを使う"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "ndf:sample"}}
                ]
            },
        },
    ]
    (transcript_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    return plugin_root, env


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_markdown_output_is_preserved(tmp_path: pathlib.Path) -> None:
    plugin_root, env = _fixture(tmp_path)

    done = _run("--plugin-root", str(plugin_root), "--days", "0", env=env)

    assert done.returncode == 0
    assert done.stdout == (
        "| skill | triggers源 | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|\n"
        "| ndf:sample | explicit | 1 | 1 | 0 | 1 | 1 | 100.0% |\n"
        "| **合計** | | **1** | **1** | **0** | **1** | **1** | **100.0%** |\n"
    )
    assert "all time / transcript 1件" in done.stderr


def test_json_output_is_preserved(tmp_path: pathlib.Path) -> None:
    plugin_root, env = _fixture(tmp_path)

    done = _run("--plugin-root", str(plugin_root), "--days", "0", "--format", "json", env=env)

    assert done.returncode == 0
    payload = json.loads(done.stdout)
    assert payload["total"] == {
        "invocations": 1,
        "auto": 1,
        "explicit": 0,
        "triggers": 1,
        "hits": 1,
        "hit_rate_pct": 100.0,
    }
    assert payload["projects"][0]["project"] == "demo"
    assert payload["meta"]["transcripts"] == 1
    assert "all time / transcript 1件" in done.stderr


def test_unmatched_project_keeps_empty_stdout(tmp_path: pathlib.Path) -> None:
    plugin_root, env = _fixture(tmp_path)

    done = _run(
        "--plugin-root", str(plugin_root), "--days", "0", "--project", "missing", env=env
    )

    assert done.returncode == 0
    assert done.stdout == ""
    assert done.stderr.endswith("[skill-stats] no projects matched: missing\n")


def test_invalid_plugin_root_reports_error(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing"

    done = _run("--plugin-root", str(missing))

    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr == f"[skill-stats] plugin root not found: {missing}\n"
