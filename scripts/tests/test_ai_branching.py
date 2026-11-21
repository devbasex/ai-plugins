"""`scripts/measure/ai-branching.py` の規則の表と分類の表（#3412 の Task 2）を、模した記録で確かめる。

- 退行: 今の規則の表（形 `before`）で、`main` の出力の形と値が変わらない
- 規則の表（`load_rules`）: `rule`・`form`・`kind` を確かめ、前と後の行が形ごとに分かれる。欠けた行は終了コード 2
- 分類の表（`load_measures`）: 規則の番号ごとの `measure` と形ごとの出所。規則の表の番号が分類の表に無ければ終了コード 2
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AB = HERE.parent / "measure" / "ai-branching.py"
RULES = HERE.parent / "measure" / "ai-branching-rules.json"
MEASURES = HERE.parent / "measure" / "ai-branching-measures.json"


def load_ab():
    spec = importlib.util.spec_from_file_location("ai_branching", AB)
    mod = importlib.util.module_from_spec(spec)
    # ワーカーのプロセスへ関数を名前で渡せるよう、モジュールを sys.modules へ載せる
    sys.modules["ai_branching"] = mod
    spec.loader.exec_module(mod)
    return mod


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _bash(tid: str, at: str, cmd: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": at,
        "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]},
    }


def _tool(tid: str, at: str, name: str, inp: dict) -> dict:
    return {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid: str, at: str, text: str) -> dict:
    return {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def build(tmp: Path) -> dict[str, Path]:
    """旧い形の記録の見本。時刻の形は実データ（2026-11-21 に確かめた）を写す。"""
    ndf = tmp / ".ndf"
    state = tmp / "state"
    tr = tmp / "transcripts"
    # 旧い progress.jsonl の at は秒まで・+00:00
    _jsonl(
        state / "p1-state" / "progress.jsonl",
        [
            {"kind": "run", "at": "2026-10-13T23:00:00+00:00"},
            {"kind": "step", "at": "2026-10-14T01:00:00+00:00", "step": "test-all", "type": "work", "exit": 0, "next": "judge"},
            {"kind": "step", "at": "2026-10-14T01:01:00+00:00", "step": "judge", "type": "judge", "exit": 0, "decision": "fix"},
            {"kind": "step", "at": "2026-10-14T01:02:00+00:00", "step": "impl", "type": "work", "exit": 1, "next": "end"},
            {"kind": "attention", "at": "2026-10-14T01:03:00+00:00", "reason": "スプリント MVV を使えない"},
            {"kind": "run", "at": "2026-10-14T02:00:00+00:00", "from": "impl"},
            {"kind": "step", "at": "2026-10-14T02:01:00+00:00", "step": "impl", "type": "work", "exit": 0, "next": "end"},
        ],
    )
    # 旧い台帳の at は秒まで・Z
    _jsonl(
        ndf / "parallel" / "p4" / "ledger.jsonl",
        [
            {"kind": "ask", "at": "2026-10-14T03:00:00Z", "msg": {"source": "lane"}},
            {"kind": "resume", "at": "2026-10-14T03:01:00Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-10-13T03:01:00Z", "msg": {"phase": "confirm", "take": True}},
        ],
    )
    # 会話の JSONL の timestamp は UTC・ミリ秒・Z
    _jsonl(
        tr / "s1.jsonl",
        [
            _bash("t1", "2026-10-14T04:00:00.000Z", "python3 supervise.py queue a.json --resume"),
            _bash("t2", "2026-10-14T04:01:00.000Z", "gh pr checks 12 && gh pr merge 12"),
            _bash("t3", "2026-10-13T04:01:00.000Z", "gh pr checks 13"),
            _bash("t4", "2026-10-14T04:02:00.000Z", "ls"),
            _tool("t5", "2026-10-14T04:03:00.000Z", "AskUserQuestion", {"metadata": {"source": "ndf-ask:design:1"}}),
            _tool("t6", "2026-10-14T04:04:00.000Z", "SendMessage", {}),
            _tool("t7", "2026-10-14T04:05:00.000Z", "Write", {"file_path": "/x/plan.json", "content": '{"steps": []}'}),
            _result("t2", "2026-10-14T04:06:00.000Z", "結果: 止まった。基盤待ち"),
        ],
    )
    return {"ndf": ndf, "state": state, "transcripts": tr}


def run_main(paths: dict[str, Path], *extra: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(AB),
        "--since",
        "2026-10-14",
        "--ndf",
        str(paths["ndf"]),
        "--state",
        str(paths["state"]),
        "--transcripts",
        str(paths["transcripts"]),
        "--jobs",
        "1",
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# ---------- 退行: 今の規則の表で出力が変わらない ----------


EXPECTED = {
    "since": "2026-10-14",
    "progress": {
        "plans": 1,
        "resume_by_previous_end": {"失敗の後・同じステップから": 1},
        "resume_from_step": {"impl": 1},
        "attention_by_reason": {"スプリント MVV を使えない": 1},
        "judge_by_decision": {"judge → fix": 1},
        "stopped_by_step_exit": {"impl exit=1": 1},
    },
    "ledger": {"kinds": {"ask": 1, "resume": 1}, "ask_by_source": {"lane": 1}, "resume": {"settle: rejected": 1}},
    "transcripts": {
        "files": 1,
        "bash_by_rule": {
            "queue の再開（queue --resume）": 1,
            "CI のチェックを手で読む（gh pr checks / gh run view）": 1,
            "マージを手で打つ（merge-when-green / gh pr merge）": 1,
        },
        "hand_written_plans": 1,
        "tools": {"AskUserQuestion（ndf-ask:design）": 1, "SendMessage": 1},
        "results_with_phrase": {"結果: 止まった": 1, "基盤待ち": 1},
    },
}


def test_main_output_is_unchanged_with_current_rules(tmp_path):
    p = run_main(build(tmp_path))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == EXPECTED


def test_main_skip_transcripts_keeps_keys(tmp_path):
    p = run_main(build(tmp_path), "--skip-transcripts")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert list(out) == ["since", "progress", "ledger"]
    assert out["progress"] == EXPECTED["progress"]


# ---------- 規則の表 ----------


def _write(tmp: Path, name: str, data) -> Path:
    path = tmp / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_rules_splits_rows_by_form():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    forms = {r["form"] for r in rules}
    assert forms == {"before", "after"}
    before = [r for r in rules if r["form"] == "before"]
    after = [r for r in rules if r["form"] == "after"]
    # 前の形は今の 25 行の Bash の規則に、Tool と文の行を足したもの
    assert sum(r["kind"] == "bash" for r in before) == 25
    assert {r["kind"] for r in before} == {"bash", "tool", "phrase"}
    # 後の形の再開のコマンドは R03 の 1 組だけ（R10・R13 は別の正規表現を持たない）
    assert {r["rule"] for r in after if r["kind"] == "bash"} >= {"R03", "R06", "R07", "R08"}
    assert not [r for r in after if r["rule"] in ("R10", "R13")]


def test_load_rules_after_rows_match_11_commands():
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after" and r["kind"] == "bash"]

    def rules_of(cmd: str) -> set[str]:
        return {r["rule"] for r in after if r["compiled"].search(cmd)}

    assert rules_of("ndf ci watch 3900") == {"R06"}
    assert rules_of("ndf ci merge") == {"R08"}
    assert rules_of("ndf plan next impl.json --from merge") == {"R03"}
    assert rules_of("ndf plan queue a.json b.json --from a=impl") == {"R03"}
    assert rules_of("ndf plan queue a.json b.json --resume") == {"R03"}
    assert rules_of("ndf plan wait a.json") == {"R07"}
    # --from を持たない開始と回答は再開に数えない
    assert rules_of("ndf plan next impl.json --answer approved") == set()


def test_load_rules_accepts_zero_rows(tmp_path):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", []))
    assert rules == []
    # 規則が 0 行でも会話の数えは形を保って空を返す
    paths = build(tmp_path)
    out = ab.count_transcripts([str(paths["transcripts"])], "2026-10-14", 1, rules)
    assert out["bash_by_rule"] == {}
    assert out["tools"] == {}
    assert out["results_with_phrase"] == {}


def test_count_transcripts_builds_matcher_once_per_worker(tmp_path, monkeypatch):
    ab = load_ab()
    paths = build(tmp_path)
    for n in range(2, 5):
        _jsonl(paths["transcripts"] / f"s{n}.jsonl", [_bash(f"u{n}", "2026-10-14T04:00:00.000Z", "gh pr checks 1")])
    calls = tmp_path / "calls"
    real = ab.build_matcher

    def counted(rules):
        with open(calls, "a", encoding="utf-8") as f:
            f.write("x")
        return real(rules)

    monkeypatch.setattr(ab, "build_matcher", counted)
    before = [r for r in ab.load_rules(RULES) if r["form"] == "before"]
    out = ab.count_transcripts([str(paths["transcripts"])], "2026-10-14", 1, before)
    assert out["files"] == 4
    assert out["bash_by_rule"]["CI のチェックを手で読む（gh pr checks / gh run view）"] == 4
    # 4 つのファイルを 1 本のワーカーで読み、照合用の値は初期化の 1 度だけ組む
    assert calls.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize(
    ("row", "missing"),
    [
        ({"form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R3", "form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R03", "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "later", "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "before", "kind": "file", "name": "x", "pattern": "x"}, "kind"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x"}, "pattern"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x", "pattern": "("}, "pattern"),
        ({"rule": "R12", "form": "before", "kind": "tool", "name": "x"}, "tool"),
        ({"rule": "R04", "form": "before", "kind": "phrase", "name": "x"}, "phrase"),
        # 手で編集したときの型の誤り: 配列の kind・数の rule・配列の pattern
        ({"rule": "R03", "form": "before", "kind": ["bash"], "name": "x", "pattern": "x"}, "kind"),
        ({"rule": 3, "form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R03", "form": ["before"], "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x", "pattern": ["x"]}, "pattern"),
    ],
)
def test_load_rules_rejects_bad_row_with_exit_2(tmp_path, capsys, row, missing):
    ab = load_ab()
    ok = {"rule": "R06", "form": "before", "kind": "bash", "name": "ok", "pattern": "ok"}
    with pytest.raises(SystemExit) as e:
        ab.load_rules(_write(tmp_path, "rules.json", [ok, row]))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "2 行目" in err
    assert missing in err


# ---------- 分類の表 ----------


def test_load_measures_covers_every_rule_number():
    ab = load_ab()
    measures = ab.load_measures(MEASURES)
    assert sorted(measures) == [f"R{n:02d}" for n in range(1, 28)]
    by_g: dict[str, set[str]] = {}
    for rule, m in measures.items():
        by_g.setdefault(m["measure"], set()).add(rule)
    # 洗い出しの表の判定: できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、数えられない → none
    assert by_g["G3"] == {"R14", "R21"}
    assert by_g["none"] == {"R26", "R27"}
    assert len(by_g["G1"]) == 11
    assert len(by_g["G2"]) == 12


def test_rules_and_measures_agree():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    measures = ab.load_measures(MEASURES)
    ab.check_measures(rules, measures)
    # 規則の行を持つ形の出所は none でない（数える行があるのに数えない、を作らない）
    for r in rules:
        assert measures[r["rule"]][r["form"]]["source"] != "none", (r["rule"], r["form"])


def test_check_measures_rejects_unknown_rule_with_exit_2(tmp_path, capsys):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", [{"rule": "R99", "form": "before", "kind": "tool", "name": "x", "tool": "x"}]))
    with pytest.raises(SystemExit) as e:
        ab.check_measures(rules, ab.load_measures(MEASURES))
    assert e.value.code == 2
    assert "R99" in capsys.readouterr().err


@pytest.mark.parametrize(
    "entry",
    [
        {"before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G4", "before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "logs"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "none"}, "after": {"source": "none", "reason": "x"}},
        # 型の誤り: 文字列の keys・配列の measure・配列の source・数の prefix
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds", "keys": "slow"}, "after": {"source": "none", "reason": "x"}},
        {"measure": ["G1"], "before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": ["none"], "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds", "prefix": 1}, "after": {"source": "none", "reason": "x"}},
    ],
)
def test_load_measures_rejects_bad_entry_with_exit_2(tmp_path, capsys, entry):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        ab.load_measures(_write(tmp_path, "measures.json", {"R01": entry}))
    assert e.value.code == 2
    assert "R01" in capsys.readouterr().err


@pytest.mark.parametrize("loader", ["load_rules", "load_measures"])
def test_loaders_reject_broken_json_with_exit_2(tmp_path, capsys, loader):
    ab = load_ab()
    path = tmp_path / "table.json"
    # 手で編集したときの末尾のカンマ
    path.write_text('[\n  {"rule": "R01"},\n]\n' if loader == "load_rules" else '{\n  "R01": {},\n}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        getattr(ab, loader)(path)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert str(path) in err
    # 余分なカンマの位置（2 行目）を示す
    assert "2 行" in err


def test_main_stops_with_exit_2_on_bad_rules(tmp_path, monkeypatch, capsys):
    ab = load_ab()
    monkeypatch.setattr(ab, "RULES_FILE", _write(tmp_path, "rules.json", [{"form": "before", "kind": "tool", "name": "x", "tool": "x"}]))
    monkeypatch.setattr(sys, "argv", ["ai-branching.py", "--skip-transcripts", "--ndf", str(tmp_path), "--state", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        ab.main()
    assert e.value.code == 2
    assert "rule" in capsys.readouterr().err
