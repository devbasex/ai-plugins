"""実際に動いたモデルの取得（`models.observed_model`。#759）。

セッションの記録は codex-cli 0.159.3 の rollout と同じ形の小さな固定データで作る。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import models  # noqa: E402

LAUNCH = LIB / "launch-cli.sh"
SECRET = "会話の本文 sk-test-secret"


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _launch(stem: pathlib.Path, runtime: str, workdir: pathlib.Path, started_at: str = "2026-10-02T00:00:00Z") -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(f"{stem}-launch.json").write_text(
        json.dumps({"runtime": runtime, "workdir": str(workdir), "started_at": started_at}), encoding="utf-8"
    )


def _rollout(
    home: pathlib.Path, sid: str, cwd: pathlib.Path, at: str, models_: tuple[str, ...] = ("gpt-6.1-sol",), day: str | None = None
) -> pathlib.Path:
    day = day or at[:10]
    directory = home / "sessions" / day[:4] / day[5:7] / day[8:10]
    directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [
        {"timestamp": at, "type": "session_meta", "payload": {"id": sid, "session_id": sid, "cwd": str(cwd), "timestamp": at}}
    ]
    rows.append({"timestamp": at, "type": "response_item", "payload": {"type": "message", "content": [{"text": SECRET}]}})
    rows += [{"timestamp": at, "type": "turn_context", "payload": {"cwd": str(cwd), "model": m}} for m in models_]
    path = directory / f"rollout-{at[:19].replace(':', '-')}-{sid}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def _codex_stem(tmp_path: pathlib.Path, sid: str | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    stem = tmp_path / "tmp" / "codex-implement-rf1"
    _launch(stem, "codex", work)
    header = "OpenAI Codex v0.159.3\n--------\nmodel: gpt-header\n"
    if sid:
        header += f"session id: {sid}\n"
    pathlib.Path(f"{stem}-err.log").write_text(header + "--------\n", encoding="utf-8")
    return stem, work


END = "2026-10-02T09:30:00+09:00"  # 監視の ended_at（現地時刻とオフセット）


def _observe(stem, ended_at=END, not_before=None):
    o = models.observed_model("codex", stem, ended_at, not_before)
    return o.model, o.reason


def test_codex_session_id_names_the_rollout(tmp_path, codex_home):
    """ID があれば、時刻と作業ディレクトリが合う別の記録があっても名指しした 1 件を読む。"""
    stem, work = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(codex_home, "01a0-mine", tmp_path / "elsewhere", "2026-10-02T00:00:05.000Z")
    _rollout(codex_home, "01a0-other", work, "2026-10-02T00:00:06.000Z", ("gpt-other",))
    assert _observe(stem) == ("gpt-6.1-sol", None)


def test_codex_session_id_without_a_rollout_is_no_record(tmp_path, codex_home):
    stem, work = _codex_stem(tmp_path, sid="01a0-missing")
    _rollout(codex_home, "01a0-other", work, "2026-10-02T00:00:06.000Z")
    assert _observe(stem) == (None, "no_record")


def test_without_id_picks_the_rollout_of_its_own_workdir(tmp_path, codex_home):
    """AC6 — 同じ時刻範囲に別の作業ディレクトリの起動が並行しても、自分の 1 件を選ぶ。"""
    stem, work = _codex_stem(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _rollout(codex_home, "01a0-a", other, "2026-10-02T00:00:03.000Z", ("gpt-other",))
    _rollout(codex_home, "01a0-b", work, "2026-10-02T00:00:04.500Z")
    assert _observe(stem) == ("gpt-6.1-sol", None)


def test_without_id_two_candidates_are_ambiguous(tmp_path, codex_home):
    """AC6・I1 — 同じ作業ディレクトリに 2 件あれば、先頭を選ばず null にする。"""
    stem, work = _codex_stem(tmp_path)
    _rollout(codex_home, "01a0-a", work, "2026-10-02T00:00:03.000Z")
    _rollout(codex_home, "01a0-b", work, "2026-10-02T00:00:04.000Z", ("gpt-other",))
    assert _observe(stem) == (None, "ambiguous")


def test_without_id_ignores_records_outside_the_time_range(tmp_path, codex_home):
    """範囲の前後の時刻の記録と、範囲外の日付ディレクトリの記録は拾わない。"""
    stem, work = _codex_stem(tmp_path)
    _rollout(codex_home, "01a0-before", work, "2026-10-01T23:59:58.000Z")
    _rollout(codex_home, "01a0-after", work, "2026-10-02T00:30:01.000Z")
    # 時刻は範囲内だが、開始日の前日〜終了日の翌日の外の日付ディレクトリにある
    _rollout(codex_home, "01a0-far", work, "2026-10-02T00:00:05.000Z", day="2026-09-20")
    assert _observe(stem) == (None, "no_record")


def test_without_id_reads_the_next_day_directory(tmp_path, codex_home):
    """日付ディレクトリが現地時刻で切られても、前後 1 日を見るため拾える。"""
    stem, work = _codex_stem(tmp_path)
    _rollout(codex_home, "01a0-a", work, "2026-10-02T00:00:05.000Z", day="2026-10-03")
    assert _observe(stem) == ("gpt-6.1-sol", None)


def test_without_id_compares_real_paths(tmp_path, codex_home):
    stem, work = _codex_stem(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(work)
    _rollout(codex_home, "01a0-a", link, "2026-10-02T00:00:05.000Z")
    assert _observe(stem) == ("gpt-6.1-sol", None)


def test_two_models_in_one_session_are_ambiguous(tmp_path, codex_home):
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z", ("gpt-a", "gpt-b"))
    assert _observe(stem) == (None, "ambiguous")


def test_a_session_without_turn_context_has_no_model_field(tmp_path, codex_home):
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z", ())
    assert _observe(stem) == (None, "no_model_field")


def test_a_broken_rollout_is_unreadable(tmp_path, codex_home):
    """AC10・I4 — 形が壊れていても例外を出さない。"""
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    path = _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z")
    path.write_text(path.read_text(encoding="utf-8") + '{"type": "turn_context", broken\n', encoding="utf-8")
    assert _observe(stem) == (None, "unreadable")


@pytest.mark.parametrize("launch", ["missing", "broken", "no-time"])
def test_without_a_launch_record_nothing_is_taken(tmp_path, codex_home, launch):
    """I2 — 起動の記録が無ければ、記録があっても前の起動の残骸として取らない。"""
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z")
    path = pathlib.Path(f"{stem}-launch.json")
    if launch == "missing":
        path.unlink()
    elif launch == "broken":
        path.write_text("{", encoding="utf-8")
    else:
        path.write_text('{"runtime": "codex"}', encoding="utf-8")
    assert _observe(stem) == (None, "no_record")


def test_a_launch_older_than_not_before_is_a_leftover(tmp_path, codex_home):
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z")
    assert _observe(stem, not_before="2026-10-02T09:00:01+09:00") == (None, "no_record")
    # 同じ秒の下限は残骸にしない（起動の記録は秒未満を切り捨てる）
    assert _observe(stem, not_before="2026-10-02T09:00:00.700+09:00") == ("gpt-6.1-sol", None)


@pytest.mark.parametrize("runtime", ["agy", "kiro", "unknown"])
def test_agy_and_kiro_are_unsupported(tmp_path, runtime):
    """AC2 — 記録や指定があっても、確かめていない形からは取らない。"""
    stem = tmp_path / f"{runtime}-implement-rf1"
    _launch(stem, runtime, tmp_path)
    pathlib.Path(f"{stem}-stdout.log").write_text('{"modelId": "auto", "modelUsage": {"x": {}}}', encoding="utf-8")
    observation = models.observed_model(runtime, stem)
    assert (observation.model, observation.reason) == (None, "unsupported")


def test_nothing_from_the_conversation_is_returned_or_written(tmp_path, codex_home):
    """I6 — 返り値に本文が無く、記録の中身と更新時刻が変わらない。"""
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    path = _rollout(codex_home, "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    observation = models.observed_model("codex", stem, END)
    assert SECRET not in repr(observation)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_codex_home_defaults_to_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    stem, _ = _codex_stem(tmp_path, sid="01a0-mine")
    _rollout(tmp_path / "home" / ".codex", "01a0-mine", tmp_path, "2026-10-02T00:00:05.000Z")
    assert _observe(stem) == ("gpt-6.1-sol", None)


def test_observation_holds_exactly_one_of_model_and_reason(tmp_path, codex_home):
    """I3 — どの経路でも、モデル名と理由のどちらか一方だけが入る。"""
    stem, work = _codex_stem(tmp_path)
    for setup in (lambda: None, lambda: _rollout(codex_home, "01a0-a", work, "2026-10-02T00:00:05.000Z")):
        setup()
        o = models.observed_model("codex", stem, END)
        assert (o.model is None) != (o.reason is None)


def test_launch_cli_writes_the_launch_record_before_cd(tmp_path):
    """`launch-cli.sh` は前の起動の記録を消し、実パスの作業ディレクトリと開始の時刻を書く。"""
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "link").symlink_to(work)
    prompt = tmp_path / "p.md"
    prompt.write_text("x\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    stem = tmp_path / "out" / "codex-x"
    stem.parent.mkdir()
    pathlib.Path(f"{stem}-launch.json").write_text('{"started_at": "2000-01-01T00:00:00Z"}', encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    # 作業ディレクトリは相対のシンボリックリンクで渡す。記録には実パスが入る
    subprocess.run(
        [str(LAUNCH), "codex", "link", str(prompt), str(stem), "", "", "60"], cwd=tmp_path, env=env, check=True, capture_output=True
    )
    record = json.loads(pathlib.Path(f"{stem}-launch.json").read_text(encoding="utf-8"))
    assert record["runtime"] == "codex"
    assert record["workdir"] == os.path.realpath(work)
    assert record["started_at"] != "2000-01-01T00:00:00Z" and record["started_at"].endswith("Z")
