"""会話の記録を層の単位で読む部品（#550 の AC20〜AC28・AC30・AC31・AC34）。

固定するのは、確定仕様（`docs/specifications/ndf-context-window-metrics.md`）の
「`AgentRecord` の値」と「コマンド」の表である。フィクスチャは実物の記録を最小化したもので、
深さ 0 / 1 / 2・conductor が直接起動した worker・同じ `message.id` の重複行・先頭の合成の
応答・429 で終わる記録・続けて完了した記録・壊れた行・語彙に無い `description` を含む。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
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
    # フェーズの語彙は supervisor、作業の種類の語彙は worker にだけ当たる
    assert mod.role_of("調査: 何か", "supervisor") == "その他"
    assert mod.role_of("設計: 何か", "worker") == "その他"
    assert mod.role_of("G2 設計 #540", "supervisor") == "その他"


# ---------- #768: 工程名で書かれた description をフェーズへ写す ----------

AGENT_LAYERS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "skills" / "development-workflow" / "references" / "agent-layers.md"
)


def phase_steps_from_table() -> dict[str, str]:
    """agent-layers.md のフェーズの表の「通す工程」の列から、工程名 → フェーズを作る。"""
    text = AGENT_LAYERS.read_text(encoding="utf-8")
    section = text.split("## フェーズ\n", 1)[1]
    steps: dict[str, str] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("フェーズ", "---"):
            if steps:
                break  # 表の終わり
            continue
        phase, column = cells[0], cells[1]
        column = re.sub(r"（[^）]*）", "", column).split("。", 1)[0]
        for step in (s.strip() for s in column.split("/")):
            if step and step != phase:
                steps.setdefault(step, phase)
    return steps


def test_the_step_names_match_the_phase_table(mod) -> None:
    assert mod.STEP_PHASES == phase_steps_from_table()


@pytest.mark.parametrize(
    "description,phase",
    [
        ("確定仕様化: #540", "取り込み"),
        ("配布: #540", "取り込み"),
        ("リリース後テスト: #766", "仕上げ"),
        ("振り返り: #550", "仕上げ"),
        ("実装レビュー: #540", "検査"),
        ("作業場所の用意: #540", "設計"),
    ],
)
def test_a_step_name_maps_to_its_phase(mod, description, phase) -> None:
    assert mod.role_of(description, "supervisor") == phase


def test_a_step_name_does_not_map_for_a_worker_or_the_old_form(mod) -> None:
    assert mod.role_of("配布: 何か", "worker") == "その他"
    assert mod.role_of("#541 の引き継ぎ", "supervisor") == "その他"
    assert mod.role_of("実装 #540 実行計画", "supervisor") == "その他"


def test_unphased_supervisors_counts_only_supervisors_left_as_other(mod, records) -> None:
    # sess-a の supervisor のうち x1 だけが `その他`。worker の `その他` は数えない
    assert mod.unphased_supervisors(list(records.values())) == 1


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
        " 層 ", " フェーズ ", " 深さ ", " モデル ", " 固定費 ", " 最大充填 ",
        " 実作業 ", " 応答数 ", " 所要（分） ", " 終わり方 ", " 中断 ", " agent_id ",
    ]


def test_format_list_omits_agent_id_column_when_disabled(mod, records) -> None:
    rec_list = list(records.values())
    text = mod.format_list(rec_list, with_agent_id=False)
    lines = text.splitlines()
    assert lines[0] == mod.LIST_HEADER
    assert lines[1] == mod.LIST_RULE
    assert "agent_id" not in lines[0]
    expected_col_count = len(mod.LIST_HEADER.split("|")[1:-1])
    for line in lines[2:]:
        cols = line.split("|")[1:-1]
        assert len(cols) == expected_col_count


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


# ========== #657: 中断と再開（AC40〜AC44・AC46・AC47） ==========
#
# フィクスチャ `sess-c` は、中断した supervisor 2 本（`c1` / `c2`）・中断した worker 1 本
# （`c3`）・完了した記録 1 本（`c4`）・429 の後に `user` の行が追記された記録（`c6`）・
# 500 で終わった記録（`c7`）・認証の失敗で終わった記録（`c8`）を持つ。conductor は 429 の
# 後に自動の継続（`origin.kind` が `auto-continuation`）が追記されている。
#
# フィクスチャ `sess-d` は、conductor が直接起動した worker（深さ 1）だけが、遠い未来の
# 解除時刻で中断している。

# sess-c の解除時刻（フィクスチャの `quotaLimits.resetsAt`）
EARLY = "2026-09-17T09:00:00+00:00"   # c1 / c3
LATE = "2026-09-17T12:00:00+00:00"    # c2
# 早い方は過ぎ、遅い方はまだ来ていない時点
BETWEEN = "2026-09-17T10:00:00+00:00"
# どちらもまだ来ていない時点
BEFORE = "2026-09-17T08:35:00+00:00"
# どちらも過ぎた時点
AFTER = "2026-09-18T00:00:00+00:00"


@pytest.fixture()
def ic(mod):
    """sess-c の記録を `agent_id` で引ける形にする。"""
    return {r.agent_id or "conductor": r for r in mod.read_session("sess-c", root=FIXTURES)}


def interrupted_ids(*args: str) -> set[str]:
    p = run("interrupted", *args, "--format", "json")
    assert p.returncode == 0, p.stderr
    return {r["agent_id"] for r in json.loads(p.stdout)["agents"]}


# ---------- AC40: 上限の中断だけを拾い、500 と認証の失敗と区別する ----------

def test_only_the_rate_limit_records_are_listed_as_interrupted() -> None:
    assert interrupted_ids("--session", "sess-c") == {"c1", "c2", "c3"}


def test_a_server_error_is_not_a_rate_limit_interruption(ic) -> None:
    assert ic["c7"].ending == "api_error"
    assert "c7" not in interrupted_ids("--session", "sess-c")


def test_an_authentication_failure_is_not_a_rate_limit_interruption(ic) -> None:
    assert ic["c8"].ending == "api_error"
    assert "c8" not in interrupted_ids("--session", "sess-c")


def test_a_record_already_continued_is_not_listed_again(ic) -> None:
    # 429 の後に `user` の行が追記された記録は `in_progress` であり、再び現れない
    assert ic["c6"].ending == "in_progress"
    assert "c6" not in interrupted_ids("--session", "sess-c")


# ---------- AC46: 中断したすべての相手が返り、完了した相手は返らない ----------

def test_every_interrupted_partner_is_listed_and_the_finished_one_is_not(ic) -> None:
    assert ic["c4"].ending == "completed"
    listed = interrupted_ids("--session", "sess-c")
    assert {"c1", "c2", "c3"} <= listed
    assert "c4" not in listed


# ---------- AC41: 解除時刻は記録から取る。固定の間隔で待たない ----------

def test_the_reset_time_comes_from_the_record(ic) -> None:
    assert ic["c1"].resets_at == EARLY
    assert ic["c2"].resets_at == LATE
    assert (ic["c1"].rate_limit_type, ic["c2"].rate_limit_type) == (
        "five_hour", "seven_day")


def test_resets_passed_compares_the_reset_time_with_now() -> None:
    p = run("interrupted", "--session", "sess-c", "--now", BETWEEN, "--format", "json")
    assert p.returncode == 0, p.stderr
    passed = {r["agent_id"]: r["resets_passed"] for r in json.loads(p.stdout)["agents"]}
    assert passed == {"c1": True, "c2": False, "c3": True}


def test_an_interruption_without_a_reset_time_counts_as_passed(mod) -> None:
    """解除時刻を持たない上限の中断は、待ち先が無いため再開に回す。"""
    record = mod.AgentRecord(layer="supervisor", role="設計", depth=1,
                             ending="rate_limit")
    assert mod.resets_passed(record, mod.parse_now(BEFORE)) is True


def test_resets_passed_when_now_matches_resets_at_exactly(mod) -> None:
    """解除時刻と now が完全に等しい境界で解除済みになる経路を固定する。"""
    record = mod.AgentRecord(layer="supervisor", role="設計", depth=1,
                             ending="rate_limit", resets_at=EARLY)
    assert mod.resets_passed(record, mod.parse_now(EARLY)) is True


# ---------- AC43: 自動の継続の後の点検が解除済みを返す ----------

def test_after_the_auto_continuation_the_check_returns_the_released_partners(ic) -> None:
    # conductor 自身は自動の継続の行が追記されたため `in_progress` である
    assert ic["conductor"].ending == "in_progress"
    p = run("interrupted", "--session", "sess-c", "--depth", "1",
            "--now", "2026-09-17T09:01:00+00:00", "--format", "json")
    assert p.returncode == 0, p.stderr
    rows = {r["agent_id"]: r["resets_passed"] for r in json.loads(p.stdout)["agents"]}
    assert rows == {"c1": True, "c2": False}


def test_the_conductor_transcript_carries_the_auto_continuation() -> None:
    path = (FIXTURES / "projects" / "-work-sample" / "sess-c.jsonl")
    assert '"auto-continuation"' in path.read_text(encoding="utf-8")


# ---------- AC42・AC47: 層ごと・起動元ごと・直下だけに絞れる ----------

def test_the_supervisors_alone_can_be_listed() -> None:
    assert interrupted_ids("--session", "sess-c", "--layer", "supervisor") == {"c1", "c2"}


def test_one_worker_can_be_picked_by_its_agent_id() -> None:
    assert interrupted_ids(
        "--session", "sess-c", "--layer", "worker", "--agent", "c3") == {"c3"}


def test_the_agent_filter_takes_more_than_one_id() -> None:
    assert interrupted_ids(
        "--session", "sess-c", "--agent", "c1", "--agent", "c3") == {"c1", "c3"}


def test_the_workers_of_one_launcher_can_be_listed() -> None:
    assert interrupted_ids("--session", "sess-c", "--parent", "c2") == {"c3"}


def test_depth_one_returns_the_direct_partners_of_the_conductor() -> None:
    # supervisor（sess-c）も、conductor が直接起動した worker（sess-d）も深さ 1 である
    assert interrupted_ids("--session", "sess-c", "--depth", "1") == {"c1", "c2"}
    assert interrupted_ids("--session", "sess-d", "--depth", "1") == {"d1"}


def test_the_interrupted_table_shows_the_reset_time() -> None:
    p = run("interrupted", "--session", "sess-c")
    assert p.returncode == 0, p.stderr
    header = next(line for line in p.stdout.splitlines() if line.startswith("| 層 "))
    assert header.split("|")[1:-1] == [
        " 層 ", " フェーズ ", " 深さ ", " 終わり方 ", " 上限の種類 ",
        " 解除時刻 ", " 解除済み ", " 起動元 ", " agent_id ",
    ]
    assert EARLY in p.stdout and LATE in p.stdout


def test_an_uninterrupted_session_ends_with_zero_and_an_empty_list() -> None:
    p = run("interrupted", "--session", "sess-b", "--format", "json")
    assert p.returncode == 0
    assert json.loads(p.stdout)["agents"] == []


def test_a_bad_now_of_interrupted_ends_with_two_and_an_input_error() -> None:
    p = run("interrupted", "--session", "sess-c", "--now", "not-an-iso-time")
    assert p.returncode == 2
    assert "時刻として読めない" in p.stderr


# ---------- AC30: 中断の一覧にも本文・パス・description の後ろ半分を載せない ----------

def test_the_interrupted_output_carries_no_text_no_path_and_no_description_tail() -> None:
    p = run("interrupted", "--session", "sess-c", "--session", "sess-d",
            "--format", "json")
    assert p.returncode == 0, p.stderr
    for forbidden in (
        "#657", "中断の見分け方", "収束の指摘の反映", "差分の数え上げ",
        "/work/sample", "req_", "develop", "起動の指示",
    ):
        assert forbidden not in p.stdout, forbidden


# ---------- AC44: 解除まで眠る。固定の間隔で待たない ----------

class Sleeper:
    """眠った秒数を控えるだけの差し替え。"""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_wait_reset_returns_at_once_when_every_reset_time_has_passed(mod) -> None:
    sleeper = Sleeper()
    slept, remaining, code = mod.wait_reset(
        ["sess-c"], root=FIXTURES, now=mod.parse_now(AFTER), sleeper=sleeper)
    assert (slept, code) == (0, 0)
    assert sleeper.slept in ([], [0])
    assert remaining == 3


def test_wait_reset_sleeps_until_the_earliest_reset_time_plus_the_margin(mod) -> None:
    sleeper = Sleeper()
    slept, _, code = mod.wait_reset(
        ["sess-c"], root=FIXTURES, now=mod.parse_now(BEFORE), sleeper=sleeper)
    # 08:35:00 から 09:00:00（早い方）+ 60 秒の余白まで
    assert slept == 25 * 60 + 60
    assert sleeper.slept == [slept]
    assert code == 0


def test_wait_reset_uses_only_the_selected_layer(mod, tmp_path) -> None:
    root = tmp_path / "transcript_agents"
    shutil.copytree(FIXTURES, root)
    worker = (
        root / "projects" / "-work-sample" / "sess-c" / "subagents" / "agent-c3.jsonl"
    )
    worker.write_text(
        worker.read_text(encoding="utf-8").replace(
            '"resetsAt": 1789635600', '"resetsAt": 1789634700',
        ),
        encoding="utf-8",
    )

    sleeper = Sleeper()
    slept, remaining, code = mod.wait_reset(
        ["sess-c"], root=root, layer="supervisor", now=mod.parse_now(BEFORE),
        sleeper=sleeper,
    )

    # worker の解除は 08:45、指定した supervisor の最初の解除は 09:00 である。
    assert slept == 25 * 60 + 60
    assert sleeper.slept == [slept]
    assert (remaining, code) == (2, 0)


def test_wait_reset_takes_the_margin_from_the_argument(mod) -> None:
    slept, _, code = mod.wait_reset(
        ["sess-c"], root=FIXTURES, now=mod.parse_now(BEFORE), margin=0,
        sleeper=Sleeper())
    assert (slept, code) == (25 * 60, 0)


def test_wait_reset_sleeps_for_a_lone_worker_launched_by_the_conductor(mod) -> None:
    """conductor が直接起動した worker だけが中断しているときも眠る。"""
    sleeper = Sleeper()
    slept, remaining, code = mod.wait_reset(
        ["sess-d"], root=FIXTURES, depth=1, now=mod.parse_now(BEFORE),
        sleeper=sleeper)
    assert slept > 0 and sleeper.slept == [slept]
    assert (remaining, code) == (1, 0)


def test_wait_reset_stops_at_the_max_sleep_and_returns_three(mod) -> None:
    sleeper = Sleeper()
    slept, _, code = mod.wait_reset(
        ["sess-d"], root=FIXTURES, depth=1, now=mod.parse_now(BEFORE),
        max_sleep=540, sleeper=sleeper)
    assert (slept, code) == (540, 3)
    assert sleeper.slept == [540]


def test_wait_reset_ends_with_zero_when_nothing_is_interrupted(mod) -> None:
    slept, remaining, code = mod.wait_reset(
        ["sess-b"], root=FIXTURES, now=mod.parse_now(BEFORE), sleeper=Sleeper())
    assert (slept, remaining, code) == (0, 0, 0)


def test_the_wait_reset_command_returns_zero_when_the_reset_has_passed() -> None:
    # sess-c の解除時刻はどちらも過去である（実時間で判定する）
    p = run("wait-reset", "--session", "sess-c")
    assert p.returncode == 0, p.stderr
    assert "眠った" in p.stdout


def test_the_wait_reset_command_returns_three_when_cut_by_max_sleep() -> None:
    p = run("wait-reset", "--session", "sess-d", "--depth", "1", "--max-sleep", "0")
    assert p.returncode == 3, p.stdout + p.stderr


def test_a_bad_argument_of_wait_reset_ends_with_two() -> None:
    p = run("wait-reset", "--session", "sess-d", "--layer", "president")
    assert p.returncode == 2


def test_no_fixed_interval_is_written_into_the_waiting() -> None:
    """待ちの長さは記録から取る。固定の間隔を持たない（AC41）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "time.sleep(" in text
    # 眠る秒数は解除時刻から計算する。定数の秒数で眠る呼び出しを持たない
    assert not re.search(r"time\.sleep\(\s*[0-9]", text)


def test_a_negative_margin_or_max_sleep_ends_with_two() -> None:
    """負の秒数は待ちの保証を壊すため、眠る前に引数の誤りとして弾く。"""
    for bad in (("--margin", "-1"), ("--max-sleep", "-1")):
        p = run("wait-reset", "--session", "sess-d", "--depth", "1", *bad)
        assert p.returncode == 2, (bad, p.stdout, p.stderr)


def test_wait_reset_never_wakes_before_the_reset_time(mod) -> None:
    """端数を切り捨てると解除時刻より早く起きる。`--margin 0` でも前に戻らない。"""
    sleeper = Sleeper()
    slept, _, code = mod.wait_reset(
        ["sess-c"], root=FIXTURES, margin=0, sleeper=sleeper,
        now=mod.parse_now("2026-09-17T08:59:59.500000+00:00"))
    # 解除は 09:00:00。0.5 秒を捨てると 0 秒になり、解除の前に戻る
    assert slept == 1
    assert sleeper.slept == [1]
    assert code == 0


# ---------- #764: .meta.json が読めない配下の記録を conductor に数えない ----------

def _session_without_meta(tmp_path: pathlib.Path) -> pathlib.Path:
    src = FIXTURES / "projects" / "-work-sample"
    dst = tmp_path / "projects" / "-work-sample"
    (dst / "sess-z" / "subagents").mkdir(parents=True)
    shutil.copy(src / "sess-d.jsonl", dst / "sess-z.jsonl")
    shutil.copy(src / "sess-d" / "subagents" / "agent-d1.jsonl",
                dst / "sess-z" / "subagents" / "agent-x.jsonl")
    return tmp_path


def test_a_sub_record_without_meta_is_not_a_conductor(mod, tmp_path) -> None:
    root = _session_without_meta(tmp_path)
    layers = {r.agent_id or "conductor": r.layer for r in mod.read_session("sess-z", root=root)}
    assert layers == {"conductor": "conductor", "x": mod.OTHER}


def test_a_sub_record_without_meta_is_reported(tmp_path) -> None:
    root = _session_without_meta(tmp_path)
    p = run("list", "--session", "sess-z", "--format", "json",
            env={"CLAUDE_CONFIG_DIR": str(root)})
    assert p.returncode == 0, p.stderr
    assert [ln for ln in p.stderr.splitlines() if ".meta.json" in ln] == [
        "[transcript-agents] 層が読めない記録（.meta.json が無いか壊れている）: 1 件"]
