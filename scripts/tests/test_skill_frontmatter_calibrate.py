"""check-skill-frontmatter.py の calibrate の現状固定（I-021）。

`claude -p /context` の実行は `subprocess.run` を差し替え、換算比の保存先（`CALIBRATION_FILE`）は
一時ディレクトリへ向ける。期待値は今のコードの出力である。
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-frontmatter.py"
CMD = ["claude", "-p", "/context", "--output-format", "json"]


@pytest.fixture
def m(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_skill_frontmatter_calibrate", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    return module


def _fake_run(monkeypatch, m, *, stdout="", returncode=0, stderr="", raises=None):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(m.subprocess, "run", run)
    return calls


def _context(result: str) -> str:
    return json.dumps({"result": result})


def _skills(tmp_path: Path, claude: list[str], skills: dict[str, str]) -> Path:
    """skills/<名前>/SKILL.md と manifests/claude-skills.txt。値は description。"""
    plugin = tmp_path / "plugins/x"
    skills_dir = plugin / "skills"
    for name, desc in skills.items():
        (skills_dir / name).mkdir(parents=True)
        (skills_dir / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n", encoding="utf-8")
    (plugin / "manifests").mkdir()
    (plugin / "manifests/claude-skills.txt").write_text("".join(f"{n}\n" for n in claude), encoding="utf-8")
    return skills_dir


def _stderr_lines(capsys) -> tuple[list[str], list[str]]:
    out = capsys.readouterr()
    return out.out.splitlines(), out.err.splitlines()


def test_run_failure_stops(m, monkeypatch, capsys):
    calls = _fake_run(monkeypatch, m, raises=OSError("no claude"))
    assert m.calibrate([]) == 1
    out, err = _stderr_lines(capsys)
    assert out == []
    assert err == [f"[calibrate] 実行: {' '.join(CMD)}", "[calibrate] claude の実行に失敗: no claude"]
    assert calls == [(CMD, {"capture_output": True, "text": True, "timeout": 300})]
    assert not m.CALIBRATION_FILE.exists()


def test_timeout_stops(m, monkeypatch, capsys):
    _fake_run(monkeypatch, m, raises=subprocess.TimeoutExpired(CMD, 300))
    assert m.calibrate([]) == 1
    _, err = _stderr_lines(capsys)
    assert err[1].startswith("[calibrate] claude の実行に失敗: ")


def test_nonzero_exit_stops_with_stderr_tail(m, monkeypatch, capsys):
    _fake_run(monkeypatch, m, returncode=3, stderr="e" * 600)
    assert m.calibrate([]) == 1
    _, err = _stderr_lines(capsys)
    assert err[1:] == ["[calibrate] claude が異常終了（exit 3）", "e" * 500]


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not json", "[calibrate] 出力を JSON として読めない"),
        (_context("no table"), "[calibrate] 出力に Skills の内訳がない。/context の書式が変わった可能性がある"),
        (json.dumps({}), "[calibrate] 出力に Skills の内訳がない。/context の書式が変わった可能性がある"),
    ],
)
def test_unreadable_output_stops(m, monkeypatch, capsys, stdout, message):
    _fake_run(monkeypatch, m, stdout=stdout)
    assert m.calibrate([]) == 1
    _, err = _stderr_lines(capsys)
    assert err[1:] == [message]
    assert not m.CALIBRATION_FILE.exists()


def test_no_common_skill_stops(m, monkeypatch, tmp_path, capsys):
    """manifest の claude に載らない Skill は、実測に名前があっても突き合わせない。"""
    skills_dir = _skills(tmp_path, ["alpha"], {"alpha": "a", "beta": "b"})
    _fake_run(monkeypatch, m, stdout=_context("### Skills\n| beta | x | 10 |\n| gamma | x | 10 |\n"))
    assert m.calibrate([skills_dir]) == 1
    _, err = _stderr_lines(capsys)
    assert err[1:] == ["[calibrate] 実測結果と突き合わせられる Skill が無い"]


def test_zero_tokens_stops(m, monkeypatch, tmp_path, capsys):
    skills_dir = _skills(tmp_path, ["alpha"], {"alpha": "a"})
    _fake_run(monkeypatch, m, stdout=_context("### Skills\n| alpha | x | 0 |\n"))
    assert m.calibrate([skills_dir]) == 1
    _, err = _stderr_lines(capsys)
    assert err[1:] == ["[calibrate] 実測トークンが 0"]
    assert not m.CALIBRATION_FILE.exists()


def test_success_writes_ratio_and_prints_budgets(m, monkeypatch, tmp_path, capsys):
    """名前空間の前置き・`~`/`<`・`k` 付きの丸めを読み、名前が一致する Skill だけで比を取る。

    表の前（`### Skills` より前）の行は読まない。
    """
    skills_dir = _skills(tmp_path, ["alpha", "beta", "delta"], {"alpha": "a" * 95, "beta": "b" * 196, "delta": "d" * 95, "omega": "o"})
    result = (
        "| alpha | x | 999 |\n"
        "### Skills\n"
        "| Skill | Source | Tokens |\n"
        "| x:alpha | plugin | ~0.1k |\n"
        "| beta | plugin | <100 |\n"
        "| gamma | plugin | 5 |\n"
        "| omega | plugin | 50 |\n"
    )
    _fake_run(monkeypatch, m, stdout=_context(result))
    assert m.calibrate([skills_dir]) == 0
    out, err = _stderr_lines(capsys)
    assert err == [f"[calibrate] 実行: {' '.join(CMD)}"]
    # alpha: 5 + 95 = 100 文字 / 100 トークン、beta: 4 + 196 = 200 文字 / 100 トークン
    payload = json.loads(m.CALIBRATION_FILE.read_text(encoding="utf-8"))
    assert payload == {
        "measured_at": datetime.date.today().isoformat(),
        "runtime": "claude",
        "command": " ".join(CMD),
        "matched_skills": 2,
        "chars": 300,
        "tokens": 200.0,
        "chars_per_token": 1.5,
        "note": "Skill 単位のトークンを出せるのは Claude Code だけのため、これを基準にする。",
    }
    assert m.CALIBRATION_FILE.read_text(encoding="utf-8").endswith("}\n")
    limits = m.listing_limits()
    assert out == [
        "[calibrate] 突き合わせ 2 個 / 300 文字 / 200 トークン",
        f"[calibrate] 換算比 1.5 文字/トークン を {m.CALIBRATION_FILE} へ保存した",
        *(f"[calibrate]   {runtime} の予算: {limit} 文字" for runtime, limit in limits.items()),
    ]
    assert m.load_calibration()[0] == 1.5


def test_k_suffix_on_one_row_multiplies_every_row_with_the_same_number(m, monkeypatch, tmp_path, capsys):
    """NOTE: 現状固定。`k` の有無を表全体から同じ数字で探すため、`1k` の行があると `1` の行も 1000 倍になる。"""
    skills_dir = _skills(tmp_path, ["alpha", "beta"], {"alpha": "a" * 995, "beta": "b" * 996})
    _fake_run(monkeypatch, m, stdout=_context("### Skills\n| alpha | x | 1 |\n| beta | x | 1k |\n"))
    assert m.calibrate([skills_dir]) == 0
    payload = json.loads(m.CALIBRATION_FILE.read_text(encoding="utf-8"))
    assert (payload["chars"], payload["tokens"], payload["chars_per_token"]) == (2000, 2000.0, 1.0)


def test_parse_skill_tokens_reads_the_table_after_the_heading(m):
    """Skills の見出しより後の行だけを読み、名前空間の前置き・`~`/`<`・`k` 付きの丸めを読む。見出しが無ければ None。"""
    assert m._parse_skill_tokens("no table") is None
    result = "| alpha | x | 999 |\n### Skills\n| Skill | Source | Tokens |\n| x:alpha | plugin | ~0.1k |\n| beta | plugin | <100 |\n| gamma | plugin | 5 |\n"
    assert m._parse_skill_tokens(result) == {"alpha": 100.0, "beta": 100.0, "gamma": 5.0}
    # NOTE: 現状固定。`k` の有無を表全体から同じ数字で探すため、`1k` の行があると `1` の行も 1000 倍になる
    assert m._parse_skill_tokens("### Skills\n| alpha | x | 1 |\n| beta | x | 1k |\n| c | x | 2.5k |\n") == {
        "alpha": 1000.0,
        "beta": 1000.0,
        "c": 2500.0,
    }


def test_claude_listing_chars_by_skill_counts_only_claude_skills(m, tmp_path):
    """manifest の claude に載る Skill だけを、Claude Code の一覧に載る文字数（名前と description）で数える。"""
    skills_dir = _skills(tmp_path, ["alpha", "beta"], {"alpha": "a" * 95, "beta": "b" * 196, "omega": "o"})
    assert m._claude_listing_chars_by_skill([skills_dir]) == {"alpha": 100, "beta": 200}
