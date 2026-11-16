"""ランタイムの契約表と本体の版ごとの見本（M3-H11・IH10）。"""

from __future__ import annotations

import io
import json

import pytest
from jsonschema import Draft202012Validator

from ndf.domains.hooks import boot, cli, context, tables
from ndf.domains.hooks.verdict import KINDS

from ._hooks import SAMPLES, call_entry, read_sample, records, sample_paths, stop


def _shape_schema(template):
    """出力の形の JSON Schema（鍵の集まりと、置き換えない値は同じ値）。"""
    if isinstance(template, dict):
        return {
            "type": "object",
            "required": sorted(template),
            "additionalProperties": False,
            "properties": {k: _shape_schema(v) for k, v in template.items()},
        }
    if template in ("{reason}", "{context}"):
        return {"type": "string", "minLength": 1}
    return {"const": template}


def test_samples_cover_the_declared_versions():
    for runtime, rt in tables.runtimes().items():
        for version in rt["samples"]:
            assert sorted((SAMPLES / runtime / version).glob("*.json")), (runtime, version)


def test_contract_table_names_known_verdicts_and_runtime_skips_known_guards():
    names = {g["name"] for g in tables.guards()}
    for rt in tables.runtimes().values():
        assert set(rt["skipped"]) <= names
        for spec in rt["events"].values():
            assert set(spec["outputs"]) <= set(KINDS) - {"pass"}
            assert f"hook.budget.{spec['budget']}" in tables.policies()


@pytest.mark.parametrize("path", sample_paths(), ids=lambda p: "/".join(p.parts[-3:]))
def test_every_sample_is_read_and_answered_in_the_contract_shape(path, tmp_path):
    runtime, event = path.parts[-3], path.stem
    payload = read_sample(path, tmp_path)
    spec = tables.event_spec(runtime, event)
    assert spec is not None, "見本の事象が契約表に無い"
    assert not [k for k in spec["reads"] if k not in payload], "見本に読む鍵が無い"
    out = call_entry(runtime, event, payload)
    rec = records()[-1]
    assert rec["error"] == "" and rec["event"] == event
    if out is not None:
        kinds = [k for k, t in spec["outputs"].items() if not list(Draft202012Validator(_shape_schema(t)).iter_errors(out))]
        assert kinds, f"出力が契約表の形に合わない: {out}"


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_stop_pushback_has_the_runtime_shape(runtime, tmp_path):
    out = call_entry(runtime, "Stop", stop(tmp_path, "どちらにしますか？", runtime=runtime))
    assert out == {"decision": "block", "reason": out["reason"]}
    # Codex の Stop の出力は hookSpecificOutput を許さない（HK6）
    assert "hookSpecificOutput" not in out


def test_missing_read_keys_pass_as_input_shape(tmp_path):
    payload = read_sample(next(SAMPLES.glob("claude/*/PreToolUse.json")), tmp_path)
    del payload["tool_input"]
    assert call_entry("claude", "PreToolUse", payload) is None
    assert records()[-1]["error"] == "input_shape"


@pytest.mark.parametrize("args", [("claude", "Unknown"), ("kiro", "PreToolUse")])
def test_unknown_events_and_runtimes_pass(args, tmp_path):
    assert call_entry(*args, read_sample(next(SAMPLES.glob("claude/*/PreToolUse.json")), tmp_path)) is None
    assert records()[-1]["error"] == "unknown_event"


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "null"])
def test_non_object_input_passes(text):
    assert call_entry("claude", "PreToolUse", text) is None
    assert records()[-1]["error"] == "input_shape"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("   \n", None),
        ("not json", None),
        ("[1, 2]", None),
        ("null", None),
        ('"文字列"', None),
        ("3", None),
        ('{"a": 1', None),
        ("{}", {}),
        (' {"tool_name": "Bash", "n": [1]} \n', {"tool_name": "Bash", "n": [1]}),
    ],
)
def test_boot_reads_the_payload_like_the_context(text, expected):
    # 環境が無いときのエントリポイントも、オブジェクトの JSON だけを入力として読む（context の解析と同じ答え）
    assert tables.json_object(text) == expected
    assert context.parse_payload(text) == expected


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "null"])
def test_boot_passes_non_object_input_as_input_shape(text):
    out = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO(text), out) == 0
    assert out.getvalue() == ""
    assert records()[-1]["error"] == "input_shape" and records()[-1]["path"] == "boot"


def test_capture_redacts_and_writes_a_sample(tmp_path):
    raw = {
        "session_id": "ab12cd34-0000-4000-8000-000000000001",
        "cwd": "/home/me/secret-project",
        "transcript_path": "/home/me/.claude/x.jsonl",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "export TOKEN=abc && gh pr merge 1", "reason": "secret-reason", "source": "secret-src"},
        "tool_response": [{"model": "user text", "session_id": "ab12"}],
    }
    path = cli.capture("claude", "9.9.9", "PreToolUse", raw, root=tmp_path)
    got = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "claude" / "9.9.9" / "PreToolUse.json"
    assert got["session_id"] == "00000000-0000-0000-0000-000000000000"
    assert got["cwd"] == "<cwd>" and got["transcript_path"] == "<transcript>"
    assert got["tool_input"]["command"] == "<text>" and got["tool_name"] == "Bash"
    # 入れ子の中の同名の鍵は伏せる（原文を残すのはトップレベルの既知のメタデータだけ）
    assert got["tool_input"]["reason"] == "<text>" and got["tool_input"]["source"] == "<text>"
    assert got["tool_response"] == [{"model": "<text>", "session_id": "<text>"}]


def test_capture_command_refuses_unknown_events(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["capture", "claude", "2.1.325", "Nope"]) != 0
    assert '"invalid"' in capsys.readouterr().out
