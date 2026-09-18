"""会話の記録を層の単位で読む部品（#550 の AC20〜AC28・AC30・AC31・AC34）。

固定するのは、契約の文書（`issues/issue-550-657-design-contracts.md`）の
「`AgentRecord` の値」と「コマンド」の表である。フィクスチャは実物の記録を最小化したもので、
深さ 0 / 1 / 2・conductor が直接起動した worker・同じ `message.id` の重複行・先頭の合成の
応答・429 で終わる記録・続けて完了した記録・壊れた行・語彙に無い `description` を含む。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
SCRIPT = LIB / "transcript_agents.py"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "transcript_agents"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("ndf_lib_transcript_agents", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    # **読み込む前に `sys.modules` へ登録する。** Python 3.14 の dataclasses は注釈の文字列を
    # 解決するためにモジュールを引く。登録しないと `AttributeError` で読み込みが落ちる。
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def records(mod):
    return {r.agent_id or "conductor": r for r in mod.read_session("sess-a", root=FIXTURES)}


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = {"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": str(FIXTURES)}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=e, capture_output=True, text=True,
    )


# ---------- AC20 / AC21: 記録 1 件が 11 項目を持つ ----------

def test_a_session_yields_one_record_per_transcript(records) -> None:
    assert set(records) == {
        "conductor", "s1", "s2", "s2b", "s3", "x1", "w0", "w1", "w2", "w3", "b1", "e1",
    }


def test_every_record_carries_the_eleven_columns(records) -> None:
    for name, r in records.items():
        row = r.as_row()
        assert set(row) == {
            "layer", "role", "depth", "model", "fixed", "peak", "work",
            "responses", "duration_seconds", "ending", "interruptions",
        }, name


def test_as_json_carries_the_eighteen_contract_fields(records) -> None:
    assert records["s1"].as_json() == {
        "layer": "supervisor",
        "role": "設計",
        "depth": 1,
        "model": "claude-opus-5",
        "fixed": 60000,
        "peak": 90000,
        "work": 30000,
        "responses": 3,
        "duration_seconds": 1800,
        "ending": "completed",
        "interruptions": 0,
        "agent_id": "s1",
        "parent_agent_id": None,
        "session": "sess-a",
        "started_at": "2026-09-17T10:01:00+00:00",
        "ended_at": "2026-09-17T10:31:00+00:00",
        "resets_at": None,
        "rate_limit_type": None,
    }


def test_the_conductor_is_depth_zero_and_has_no_role(records) -> None:
    c = records["conductor"]
    assert (c.layer, c.depth, c.role, c.agent_id) == ("conductor", 0, "-", None)


# ---------- AC22: 固定費と実作業が別で、実作業は最大充填 − 固定費 ----------

def test_work_is_peak_minus_fixed(records) -> None:
    for name, r in records.items():
        if r.fixed is None:
            continue
        assert r.work == r.peak - r.fixed, name


def test_the_supervisor_of_the_design_post_has_the_contract_values(records) -> None:
    s1 = records["s1"]
    assert (s1.layer, s1.role, s1.depth) == ("supervisor", "設計", 1)
    assert (s1.fixed, s1.peak, s1.work) == (60000, 90000, 30000)
    assert s1.model == "claude-opus-5"


# ---------- AC23: 応答数は message.id の異なる数 ----------

def test_three_lines_of_the_same_message_count_as_one_response(records) -> None:
    # conductor の記録は msg-c1 を 3 行持ち、固有の応答は 4 件である。
    assert records["conductor"].responses == 4


# ---------- AC24: 合成の応答は計算に入らない ----------

def test_a_leading_synthetic_response_does_not_become_the_fixed_cost(records) -> None:
    # conductor の記録は合成の応答で始まる。固定費は最初の合成でない応答の値になる。
    assert records["conductor"].fixed == 30000


def test_synthetic_responses_are_excluded_from_model_and_peak(records) -> None:
    s2 = records["s2"]
    assert s2.model == "claude-opus-5"
    assert (s2.fixed, s2.peak, s2.responses) == (80000, 100000, 3)


# ---------- AC25: 終わり方の 4 値と判定の順序 ----------

@pytest.mark.parametrize(
    ("agent", "ending"),
    [
        ("conductor", "completed"),
        ("s1", "completed"),
        ("s2", "rate_limit"),
        ("s3", "in_progress"),
        ("e1", "api_error"),
    ],
)
def test_the_ending_is_one_of_the_four_values(records, agent, ending) -> None:
    assert records[agent].ending == ending


def test_a_record_continued_after_a_rate_limit_counts_the_interruption(records) -> None:
    # s2 は 429 の後に続けて応答しており、最後にもう一度 429 で終わっている。
    assert records["s2"].interruptions == 1
    assert records["s2"].resets_at == "2026-09-15T11:00:00+00:00"
    assert records["s2"].rate_limit_type == "seven_day"


def test_an_unfinished_record_reports_no_reset_time(records) -> None:
    # 429 の後に user の行が続く記録は in_progress であり、解除時刻を持たない。
    s3 = records["s3"]
    assert (s3.ending, s3.resets_at, s3.interruptions) == ("in_progress", None, 0)


def test_only_the_rate_limit_is_counted_as_an_interruption(records) -> None:
    assert records["e1"].interruptions == 0


# ---------- AC26: 層は深さと description で決まる ----------

@pytest.mark.parametrize(
    ("agent", "layer", "role"),
    [
        ("s1", "supervisor", "設計"),
        ("s2", "supervisor", "実装"),
        ("s3", "supervisor", "検査"),
        ("x1", "supervisor", "その他"),   # 深さ 1 で語彙に当たらない
        ("w0", "worker", "調査"),        # 深さ 1 だが作業の種類の語彙で始まる
        ("w1", "worker", "修正"),
        ("w3", "worker", "調査"),
        ("b1", "worker", "集計"),
    ],
)
def test_the_layer_and_the_role_come_from_depth_and_description(
    records, agent, layer, role,
) -> None:
    assert (records[agent].layer, records[agent].role) == (layer, role)


def test_role_of_reads_only_the_head_word(mod) -> None:
    assert mod.role_of("設計: #550 #657", "supervisor") == "設計"
    assert mod.role_of("調査: 既存の規約の突き合わせ", "worker") == "調査"
    # 持ち場の語彙は supervisor、作業の種類の語彙は worker にだけ当たる
    assert mod.role_of("調査: 何か", "supervisor") == "その他"
    assert mod.role_of("設計: 何か", "worker") == "その他"
    assert mod.role_of("G2 設計 #540", "supervisor") == "その他"


def test_the_parent_is_resolved_through_the_tool_use_id(records) -> None:
    # 深さ 2 の記録は起動元の supervisor を指し、conductor が起動したものは null になる
    assert records["w1"].parent_agent_id == "s1"
    assert records["w3"].parent_agent_id == "s2b"
    assert records["w0"].parent_agent_id is None
    assert records["s1"].parent_agent_id is None


# ---------- AC27: --session で絞る。繰り返して複数を渡せる ----------

def test_list_takes_more_than_one_session() -> None:
    p = run("list", "--session", "sess-a", "--session", "sess-b", "--format", "json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert len(out["agents"]) == 12 + 3


def test_list_narrows_to_one_layer() -> None:
    p = run("list", "--session", "sess-a", "--layer", "supervisor", "--format", "json")
    assert p.returncode == 0, p.stderr
    layers = {r["layer"] for r in json.loads(p.stdout)["agents"]}
    assert layers == {"supervisor"}


def test_an_unknown_session_ends_with_zero_and_a_reason() -> None:
    p = run("list", "--session", "sess-none")
    assert p.returncode == 0
    assert p.stderr.strip() != ""


def test_a_bad_argument_ends_with_two() -> None:
    p = run("list", "--session", "sess-a", "--layer", "president")
    assert p.returncode == 2


# ---------- AC30: 出力に本文・パス・description の後ろ半分を含めない ----------

def test_the_output_carries_no_text_no_path_and_no_description_tail() -> None:
    p = run("list", "--session", "sess-a", "--session", "sess-b", "--format", "json")
    assert p.returncode == 0, p.stderr
    for forbidden in (
        "#550 #657", "レビュー指摘の反映", "既存の規約の突き合わせ",
        "/work/sample", "req_", "develop", "起動の指示",
    ):
        assert forbidden not in p.stdout, forbidden


def test_the_markdown_output_has_the_contract_columns() -> None:
    p = run("list", "--session", "sess-a")
    assert p.returncode == 0, p.stderr
    header = next(line for line in p.stdout.splitlines() if line.startswith("| 層 "))
    assert header.split("|")[1:-1] == [
        " 層 ", " 持ち場 ", " 深さ ", " モデル ", " 固定費 ", " 最大充填 ",
        " 実作業 ", " 応答数 ", " 所要（分） ", " 終わり方 ", " 中断 ", " agent_id ",
    ]


# ---------- AC31: 壊れた行があっても 0 で終わる ----------

def test_a_broken_line_is_skipped_and_reported_once() -> None:
    p = run("list", "--session", "sess-a", "--format", "json")
    assert p.returncode == 0
    assert len([ln for ln in p.stderr.splitlines() if "飛ばした" in ln]) == 1
    assert json.loads(p.stdout)["skipped"] == 1


def test_the_record_with_the_broken_line_still_has_its_values(records) -> None:
    e1 = records["e1"]
    assert (e1.responses, e1.fixed, e1.peak) == (3, 11000, 16000)


# ---------- AC34: ネットワークを使わない ----------

def test_the_command_runs_with_the_socket_module_blocked(tmp_path) -> None:
    guard = tmp_path / "sitecustomize.py"
    guard.write_text(
        "import socket\n"
        "def _blocked(*a, **k):\n"
        "    raise AssertionError('ネットワークを開こうとした')\n"
        "socket.socket = _blocked\n"
        "socket.create_connection = _blocked\n",
        encoding="utf-8",
    )
    p = run(
        "list", "--session", "sess-a",
        env={"PYTHONSTARTUP": "", "PYTHONPATH": str(tmp_path)},
    )
    assert p.returncode == 0, p.stderr


def test_the_module_imports_no_network_library() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for name in ("import socket", "urllib", "http.client", "requests", "subprocess"):
        assert name not in text, name
