"""`scripts/token-usage-snapshot.py` を小さな合成の記録で確かめる（#893）。

会話は conductor だけの最小の形にし、呼び出しの回数で換算の値を決める（1 回 = U_COST）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "token-usage-snapshot.py"
WT = "/tmp/ndf-worktrees/acme--secret-repo/pr7"
UNTIL = "2026-09-10T12:00:00Z"
U = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 100,
     "cache_creation_input_tokens": 200, "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 0}}


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _ts(minute: int) -> str:
    return f"2026-09-10T{minute // 60:02d}:{minute % 60:02d}:00.000Z"


def session(claude: Path, sid: str, version: str, start: int, calls: int, pr: int) -> None:
    """版 `version` の会話を 1 件作る。`start` 分から 1 分おきに `calls` 回の応答を置き、PR を 1 本作る。"""
    rows = [{"type": "user", "isMeta": True, "timestamp": _ts(start), "cwd": "/work/secret-repo",
             "message": {"content": [{"type": "text", "text":
                                      f"Base directory for this skill: /h/.claude/plugins/cache/ai-plugins/ndf/{version}/skills/x"}]}}]
    for i in range(calls):
        content = [{"type": "tool_use", "id": f"t{sid}", "name": "Bash", "input": {"command": f"cd {WT} && gh pr create"}}] if i == 0 else []
        rows.append({"type": "assistant", "timestamp": _ts(start + 1 + i), "version": "2.1.200", "cwd": "/work/secret-repo",
                     "message": {"id": f"{sid}-m{i}", "model": "claude-opus-5", "usage": U, "content": content}})
        if i == 0:
            rows.append({"type": "user", "timestamp": _ts(start + 1), "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{sid}",
                 "content": f"https://github.com/acme/secret-repo/pull/{pr}"}]}})
    _jsonl(claude / "-work-secret-repo" / f"{sid}.jsonl", rows)


def record(out: Path, name: str, released: str, until: str | None, versions: list[str]) -> None:
    meta = {"by": ["version"], "released": released, "sessions": 0}
    if until is not None:
        meta["until"] = until
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps({"per_pr": [{"version": v} for v in versions], "per_role": [],
                                                   "external": [], "meta": meta}), encoding="utf-8")
    (out / f"{name}.md").write_text("# 前の記録\n", encoding="utf-8")


def build(tmp: Path) -> dict:
    claude = tmp / "claude"
    session(claude, "aaaaaaaa-0000-0000-0000-000000000001", "10.0.0", 0, 2, 1)
    session(claude, "aaaaaaaa-0000-0000-0000-000000000002", "10.1.0", 10, 2, 2)
    session(claude, "aaaaaaaa-0000-0000-0000-000000000003", "10.2.0", 20, 4, 3)  # 前の行の 2 倍
    session(claude, "aaaaaaaa-0000-0000-0000-000000000004", "10.3.0", 690, 5, 4)  # 1.25 倍・打ち切りの 30 分以内
    _jsonl(tmp / "codex" / "2026/09/10/rollout-a.jsonl", [
        {"timestamp": _ts(12), "type": "session_meta", "payload": {"cwd": WT}},
        {"timestamp": _ts(13), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 50}}}},
    ])
    (tmp / "kiro").mkdir()
    out = tmp / "out"
    record(out, "2026-08-01", "9.0.0", None, ["9.0.0", "10.0.0"])  # 打ち切りの時刻を持たない記録は最も古い
    record(out, "2026-09-05", "10.1.0", "2026-09-05T00:00:00Z", ["10.0.0", "10.1.0-dev.1", "10.1.0"])
    record(out, "2026-09-20", "10.9.0", "2026-09-20T00:00:00Z", ["10.9.0"])  # 打ち切りより後の記録は取らない
    changelog = tmp / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [ndf 10.3.0] - x\n\n## [mcp-x 1.0.0] - x\n\n## [ndf 10.2.0] - x\n\n"
                         "## [ndf 10.1.5] - x\n\n## [ndf 10.1.0] - x\n\n## [ndf 10.0.0] - x\n", encoding="utf-8")
    return {"claude": tmp / "claude", "codex": tmp / "codex", "kiro": tmp / "kiro", "out": out, "changelog": changelog}


def run(env: dict, *args: str, until: str | None = UNTIL) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--claude-root", str(env["claude"]), "--codex-root", str(env["codex"]),
           "--kiro-root", str(env["kiro"]), "--out", str(env["out"]), "--changelog", str(env["changelog"]), *args]
    if until:
        cmd += ["--until", until]
    return subprocess.run(cmd, capture_output=True, text=True)


def ok(env: dict, *args: str, until: str | None = UNTIL) -> subprocess.CompletedProcess:
    p = run(env, *args, until=until)
    assert p.returncode == 0, p.stdout + p.stderr
    return p


def section(md: str, head: str) -> str:
    part = md.split(f"## {head}", 1)[1]
    return part.split("\n## ", 1)[0]


# --- AC6: 2 つのファイルと名前 ---------------------------------------------------

def test_writes_md_and_json_named_by_until_date(tmp_path):
    env = build(tmp_path)
    ok(env, "--released", "10.3.0")
    data = json.loads((env["out"] / "2026-09-10.json").read_text(encoding="utf-8"))
    assert (env["out"] / "2026-09-10.md").exists()
    assert data["meta"]["released"] == "10.3.0"
    assert data["meta"]["previous"] == "2026-09-05.json"
    assert data["meta"]["until"] == UNTIL
    assert data["meta"]["by"] == ["version", "mode", "model", "cc", "reviewers"]
    assert set(data) == {"per_pr", "per_role", "external", "meta"}


def test_other_version_on_same_date_gets_suffix(tmp_path):
    env = build(tmp_path)
    record(env["out"], "2026-09-10", "10.2.5", "2026-09-10T01:00:00Z", ["10.1.0", "10.2.0"])
    ok(env, "--released", "10.3.0")
    assert json.loads((env["out"] / "2026-09-10.json").read_text())["meta"]["released"] == "10.2.5"
    assert json.loads((env["out"] / "2026-09-10-2.json").read_text())["meta"]["released"] == "10.3.0"


def test_without_previous_and_min_version_is_two(tmp_path):
    env = build(tmp_path)
    for f in env["out"].iterdir():
        f.unlink()
    assert run(env, "--released", "10.3.0").returncode == 2
    ok(env, "--released", "10.3.0", "--min-version", "10.2.0")
    data = json.loads((env["out"] / "2026-09-10.json").read_text())
    assert data["meta"]["previous"] is None
    assert {r["version"] for r in data["per_pr"]} == {"10.2.0", "10.3.0"}


# --- AC7: 表と注意 ---------------------------------------------------------------

def test_table_starts_at_last_version_of_previous_record(tmp_path):
    env = build(tmp_path)
    ok(env, "--released", "10.3.0")
    data = json.loads((env["out"] / "2026-09-10.json").read_text())
    assert [r["version"] for r in data["per_pr"]] == ["10.1.0", "10.2.0", "10.3.0"]
    table = section((env["out"] / "2026-09-10.md").read_text(), "版ごとの差")
    rows = [line for line in table.splitlines() if line.startswith("| 10.")]
    assert [r.split(" | ")[0] for r in rows] == ["| 10.1.0", "| 10.2.0", "| 10.3.0"]
    assert " - |" in rows[0] and "+100%" in rows[1] and "+25%" in rows[2]


def test_md_has_layer_table_and_rebuild_command(tmp_path):
    env = build(tmp_path)
    ok(env, "--released", "10.3.0")
    md = (env["out"] / "2026-09-10.md").read_text()
    assert "| 10.2.0 | conductor | 1 |" in section(md, "版と層ごとの呼び出しとキャッシュ")
    assert f"--until {UNTIL}" in section(md, "作り方")
    assert "--min-version 10.1.0" in section(md, "作り方")


def test_notes_list_each_mechanical_case(tmp_path):
    env = build(tmp_path)
    session(env["claude"], "aaaaaaaa-0000-0000-0000-000000000005", "10.2.0", 40, 4, 5)
    session(env["claude"], "aaaaaaaa-0000-0000-0000-000000000006", "10.2.0", 60, 4, 6)
    ok(env, "--released", "10.3.0")
    lines = section((env["out"] / "2026-09-10.md").read_text(), "比べるときの注意").splitlines()
    few = next(line for line in lines if "2 件以下" in line)
    assert "10.1.0" in few and "10.3.0" in few and "10.2.0" not in few
    missing = next(line for line in lines if "CHANGELOG.md" in line)
    assert "10.1.5" in missing and "10.2.0" not in missing and "10.0.0" not in missing
    live = next(line for line in lines if "進行中" in line)
    assert "10.3.0" in live and "10.1.0" not in live and "10.2.0" not in live


# --- AC8: 差の大きい版 -----------------------------------------------------------

def test_only_versions_beyond_30_percent_are_listed(tmp_path):
    env = build(tmp_path)
    p = ok(env, "--released", "10.3.0")
    [line] = [x for x in p.stdout.splitlines() if x.startswith("差の大きい版:")]
    assert "10.2.0" in line and "10.3.0" not in line and "10.1.0" not in line


def test_no_large_difference_prints_none(tmp_path):
    env = build(tmp_path)
    p = ok(env, "--released", "10.3.0", "--min-version", "10.3.0")
    assert "差の大きい版: 無し" in p.stdout.splitlines()


# --- AC9: 走らせ直し -------------------------------------------------------------

def test_rerun_with_same_until_rewrites_same_name_identically(tmp_path):
    env = build(tmp_path)
    ok(env, "--released", "10.3.0")
    first = (env["out"] / "2026-09-10.json").read_text()
    names = sorted(f.name for f in env["out"].iterdir())
    ok(env, "--released", "10.3.0")
    assert (env["out"] / "2026-09-10.json").read_text() == first
    assert sorted(f.name for f in env["out"].iterdir()) == names
    assert json.loads(first)["meta"]["previous"] == "2026-09-05.json"


def test_rerun_with_other_until_same_version_uses_same_name(tmp_path):
    env = build(tmp_path)
    ok(env, "--released", "10.3.0")
    ok(env, "--released", "10.3.0", until="2026-09-10T13:00:00Z")
    assert not (env["out"] / "2026-09-10-2.json").exists()
    data = json.loads((env["out"] / "2026-09-10.json").read_text())
    assert data["meta"]["until"] == "2026-09-10T13:00:00Z"
    assert data["meta"]["previous"] == "2026-09-05.json"


# --- AC10: 秘匿 ------------------------------------------------------------------

def test_outputs_carry_no_body_path_repository_or_session_id(tmp_path):
    env = build(tmp_path)
    p = ok(env, "--released", "10.3.0")
    for text in ((env["out"] / "2026-09-10.json").read_text(), (env["out"] / "2026-09-10.md").read_text(), p.stdout):
        for secret in ("secret-repo", "acme", "aaaaaaaa-0000", "/work/", "/tmp/ndf-worktrees", "Base directory", str(tmp_path)):
            assert secret not in text, secret
