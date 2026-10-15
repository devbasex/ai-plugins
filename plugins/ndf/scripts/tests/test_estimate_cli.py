"""scripts/estimate.py: 部品の CLI（#1856 の受け入れ条件 3・4・9・11 と非機能の条件）。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
CLI = SCRIPTS / "estimate.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def bare_repo(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.delenv("NDF_ESTIMATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return repo


def _cli(repo, *args, env=None):
    p = subprocess.run([sys.executable, str(CLI), *args], cwd=repo, capture_output=True, text=True, env={**os.environ, **(env or {})})
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr


def test_predict_in_bare_repo_is_uncalibrated(bare_repo):
    code, out, err = _cli(bare_repo, "predict", "--task", "ログインの画面を直す", "--done", "実装", "--items", "spread=軽")
    assert code == 0, err
    item = out["items"][0]
    assert set(item["values"]) == {"A", "H", "L", "C"}
    assert {v["mark"] for v in item["values"].values()} <= {"未校正", "材料なし"}
    assert "ai-plugins" not in json.dumps(out, ensure_ascii=False)
    assert item["items"]["spread"] == "軽" and item["items"]["rework"] == "判定できない"


def test_predict_rejects_bad_arguments(bare_repo):
    assert _cli(bare_repo, "predict", "--done", "実装")[0] == 2
    assert _cli(bare_repo, "predict", "--task", "x", "--done", "実装", "--items", "spread=中")[0] == 2


def test_save_human_errors_roundtrip(bare_repo):
    code, out, _ = _cli(bare_repo, "predict", "--issue", "5", "--done", "実装", "--provisional", "A=100-200", "--save")
    assert code == 0 and out["items"][0]["values"]["A"]["typical"] == 100
    rec = bare_repo / ".git" / "ndf" / "estimate"
    est = [json.loads(l) for l in (rec / "estimates.jsonl").read_text().splitlines()]
    assert est[0]["issue"] == 5 and "size_unit_seconds" not in est[0]
    assert _cli(bare_repo, "human", "--issue", "5", "--minutes", "3")[0] == 0
    code, out, _ = _cli(bare_repo, "errors")
    assert code == 0 and out["metrics"]["pairs"] == 0
    code, out, _ = _cli(bare_repo, "actuals", "--issue", "5")
    assert code == 0 and out["items"][0]["h"] == 180


def test_save_failure_is_a_warning(bare_repo, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, out, _ = _cli(bare_repo, "predict", "--task", "x", "--done", "実装", "--save", env={"NDF_ESTIMATE_DIR": str(blocker / "sub")})
    assert code == 0
    assert any(i.get("kind") == "warning" and i.get("reason") == "保存できなかった" for i in out["items"])


def test_batch(bare_repo, tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps([{"issue": 1, "done": "実装"}, {"task": "y", "done": "検査", "items": {"rework": "重"}}], ensure_ascii=False))
    code, out, _ = _cli(bare_repo, "predict", "--batch", str(f))
    assert code == 0 and [i["issue"] for i in out["items"]] == [1, None]


def test_import_from_sv_dir(bare_repo, tmp_path):
    sv = tmp_path / "sv" / "m"
    (sv / "p-state").mkdir(parents=True)
    (sv / "p.json").write_text(json.dumps({"フェーズ": "実装", "課題": [3]}, ensure_ascii=False))
    (sv / "p-state" / "progress.jsonl").write_text(json.dumps({"kind": "run", "at": "2026-10-01T00:00:00+00:00", "from": None}) + "\n")
    code, out, _ = _cli(bare_repo, "import", "--sv-dir", str(tmp_path / "sv"))
    assert code == 0 and out["metrics"]["added"] == 1


def test_import_default_takes_hashed_state_dirs_of_this_repo_only(bare_repo, tmp_path):
    """既定の置き場所は全プロジェクト共通: `<stem>-<hash>/` の実体を読み、別のリポジトリと持ち主の分からないプランは除く。"""
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    sv = tmp_path / "sv"
    run = json.dumps({"kind": "run", "at": "2026-10-01T00:00:00+00:00", "from": None}) + "\n"
    for name, plan in (
        ("mine-0123abcd", {"フェーズ": "実装", "課題": [3], "作業場所": str(bare_repo)}),
        ("theirs-4567ef01", {"フェーズ": "実装", "課題": [3], "作業場所": str(other)}),
        ("unknown-89ab2345", {"フェーズ": "実装", "課題": [3]}),
    ):
        (sv / name).mkdir(parents=True)
        (sv / name / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
        (sv / name / "progress.jsonl").write_text(run)
    code, out, err = _cli(bare_repo, "import", env={"NDF_SV_STATE_DIR": str(sv)})
    assert code == 0, err
    assert (out["metrics"]["added"], out["metrics"]["foreign"]) == (1, 2)


def test_predict_uses_no_subprocess_or_network(bare_repo, monkeypatch, capsys):
    import estimate

    def boom(*a, **k):
        raise AssertionError("呼んではならない")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.chdir(bare_repo)
    with pytest.raises(SystemExit) as e:
        estimate.main(["predict", "--task", "x", "--done", "実装"])
    assert e.value.code == 0


def test_result_does_not_depend_on_runtime_env(bare_repo):
    a = _cli(bare_repo, "predict", "--task", "x", "--done", "実装", env={"CLAUDE_PLUGIN_ROOT": "/a", "CODEX_HOME": "/c"})[1]
    b = _cli(bare_repo, "predict", "--task", "x", "--done", "実装", env={"CLAUDE_PLUGIN_ROOT": "/b", "KIRO_HOME": "/k"})[1]
    assert a == b
