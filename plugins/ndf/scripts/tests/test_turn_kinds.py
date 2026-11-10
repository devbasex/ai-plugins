"""ターンの型を数える実験版（実験版の置き場の `turn-kinds.py`、#2909 の F7）。

合成した transcript（Tool の呼び出し 10 件・型 4 種）で、型と候補の件数と割合、期間の切り方、出力に会話の ID と
コマンドを出さないこと（I8）を見る。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "experimental" / "turn-kinds.py"
SESSION = "5e55a0b1-0000-4000-8000-c0ffee000001"
SECRET = "ひみつのコマンドの文字列"


def bash(i: int, cmd: str, at: str = "2026-10-20T03:00:00Z") -> dict:
    return {
        "type": "assistant",
        "sessionId": SESSION,
        "timestamp": at,
        "message": {"content": [{"type": "tool_use", "id": f"toolu_{i}", "name": "Bash", "input": {"command": cmd}}]},
    }


def other(i: int, name: str = "Read") -> dict:
    return {
        "type": "assistant",
        "sessionId": SESSION,
        "timestamp": "2026-10-20T03:00:00Z",
        "message": {"content": [{"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": {"file_path": "/x"}}]},
    }


def transcript(root: Path) -> None:
    d = root / "projects" / "-work-repo"
    d.mkdir(parents=True)
    rows = [
        bash(1, "python3 plugins/ndf/scripts/sprint-state.py get s.json"),  # 往復
        bash(2, "cat .ndf/sv/sprint.json | python3 -c 'import json,sys; print(json.load(sys.stdin))'"),  # 変換・候補 1
        bash(3, "gh pr view 12 --json state"),  # 照合（PR）
        bash(4, "cd /w && gh pr checks 12"),  # 照合（CI）
        bash(5, f'bash "$S/projects-sync.sh" 1 stage 設計  # {SECRET}'),  # 往復（連打の 1 本目）
        bash(6, 'bash "$S/projects-sync.sh" 1 stage 計画'),  # 往復・候補 4（直前も進行の記録）
        bash(7, "grep -rn foo plugins/"),  # どの型でもない（コード探索）
        other(8),
        other(9, "Edit"),
        bash(10, "jq .items lint.json"),  # 変換（状態ファイルでないので候補 1 ではない）
        bash(11, "gh pr view 1", at="2026-11-30T03:00:00Z"),  # 期間の外
        {"type": "api-request-blob", "message": {"content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {}}]}},
    ]
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n{broken\n"
    (d / f"{SESSION}.jsonl").write_text(text, encoding="utf-8")


def run(root: Path, *extra: str) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--since", "2026-10-13", "--until", "2026-11-09", "--root", str(root), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return p.returncode, p.stdout


def test_counts_kinds_and_candidates_over_all_tool_calls(tmp_path):
    transcript(tmp_path)
    code, out = run(tmp_path)
    assert code == 0
    m = json.loads(out.strip().splitlines()[-1])["metrics"]
    assert m["tool_calls"] == 10  # 期間の外と api-request-blob を数えない。Bash 以外も分母に入る
    kinds = {k: v["count"] for k, v in m["kinds"].items()}
    assert kinds == {
        "往復: NDF スクリプトを 1 行打つ": 3,
        "変換: JSON・出力を python -c / jq / cut で読む": 2,
        "照合: gh で PR・Issue を読む": 1,
        "照合: CI の状態を gh で見る": 1,
    }
    cands = {k: v["count"] for k, v in m["candidates"].items()}
    assert cands == {"1. 状態の取り出し": 1, "2. PR・CI の照合": 2, "3. スクリプトの往復": 3, "4. 進行の連打": 1}
    assert m["candidates"]["3. スクリプトの往復"]["share"] == 0.3
    assert m["files"] == 1


def test_output_has_no_session_id_or_command(tmp_path):
    transcript(tmp_path)
    _, out = run(tmp_path)
    assert SESSION not in out and SECRET not in out and "gh pr view" not in out


def test_bad_period_and_missing_root(tmp_path):
    code, _ = run(tmp_path / "none")
    assert code == 3
    p = subprocess.run([sys.executable, str(SCRIPT), "--since", "2026-11-09", "--until", "2026-10-13"], capture_output=True, text=True)
    assert p.returncode == 2
