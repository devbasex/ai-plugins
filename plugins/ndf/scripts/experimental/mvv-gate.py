#!/usr/bin/env python3
"""mvv-gate.py: 関門の材料が MVV に従うかを最小構成の claude -p に判定させる（試行）。

    python3 mvv-gate.py check --mvv <MVV.md> --gate design|release [--material <ファイル>...] [--pr N...]
                              [--log <jsonl>] [--repo OWNER/REPO]

`pace: fast` の関門 1（設計の承認）と関門 2（本番への配布の承認）を、利用者が承認した MVV への事前の
承認として扱うための判定である。材料（提示物のファイル・PR の題名と本文と変更したファイル）と MVV を
渡し、「従う / 従わない / 判定できない」と理由を JSON で返させる。

- 従う: status ok（終了コード 0）。関門を省いて進めてよい
- 従わない・判定できない・越えない線に当たる・判定を読めない: status gate（終了コード 10）。利用者の承認を求める

判定は毎回 --log（既定 ~/.local/state/ndf/mvv-gate.jsonl）へ 1 行で残す。claude は NDF_MVV_CLAUDE で差し替えられる。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from step_result import emit, result  # noqa: E402

TOOL = "mvv-gate"
GATES = {"design": "関門 1（設計の承認）", "release": "関門 2（本番への配布の承認）"}
VERDICTS = ("follow", "not_follow", "unknown")
MAX_MATERIAL = 60_000  # 材料 1 件の上限の文字数（超えた分は切る）

SYSTEM = """あなたは開発の関門の判定者である。利用者はミッションの MVV（Mission / Vision / Value）を承認済みで、
MVV に従う変更なら関門での個別の承認を省いてよいと決めている。材料を読み、この変更が MVV に従うかを判定する。

- Mission と Vision に沿い、Value のどれにも反しないなら follow
- どれかに反するなら not_follow。材料から読み取れず決められないなら unknown（迷ったら unknown）
- Value の「越えない線」（秘密・認証認可・利用者のデータ・戻せない操作・他のリポジトリへの公開）に当たる変更を
  含むなら、boundary にその中身を書く（boundary が空でなければ関門は省かれない）

出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|not_follow|unknown", "reasons": ["MVV のどの項目に照らしてそう判断したか（1〜5 件）"], "boundary": []}"""


def gh(args: list[str], repo: str | None) -> str:
    cmd = ["gh", *args] + (["--repo", repo] if repo else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {p.stderr.strip()[:300]}")
    return p.stdout


def pr_material(n: int, repo: str | None) -> str:
    info = json.loads(gh(["pr", "view", str(n), "--json", "title,body,files"], repo))
    files = "\n".join(f"- {f['path']} (+{f['additions']} -{f['deletions']})" for f in info.get("files", []))
    return f"## Pull Request #{n}: {info['title']}\n\n{info.get('body') or ''}\n\n### 変更したファイル\n{files}"


def build_prompt(mvv: str, gate: str, materials: list[str]) -> str:
    body = "\n\n".join(m[:MAX_MATERIAL] for m in materials)
    return f"# MVV\n\n{mvv}\n\n# 関門\n\n{GATES[gate]}\n\n# 材料\n\n{body}\n"


def ask(prompt: str) -> tuple[dict | None, str, dict]:
    """(判定, 生の文, 使用量) を返す。読めなければ判定は None。"""
    base = shlex.split(os.environ.get("NDF_MVV_CLAUDE", "claude"))
    cmd = base + ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "",
                  "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", SYSTEM, "--tools", ""]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    try:
        outer = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, (p.stdout + p.stderr)[-500:], {}
    text = outer.get("result") or ""
    usage = {"cost_usd": outer.get("total_cost_usd"), "seconds": (outer.get("duration_ms") or 0) / 1000}
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = json.loads(text[start:end + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        verdict = None
    if not isinstance(verdict, dict) or verdict.get("verdict") not in VERDICTS:
        return None, text[-500:], usage
    return verdict, text, usage


def cmd_check(a) -> dict:
    mvv_path = Path(a.mvv)
    if not mvv_path.is_file():
        return result(TOOL, "stopped", f"MVV が無い: {a.mvv}")
    materials = [f"## {m}\n\n{Path(m).read_text(encoding='utf-8')}" for m in a.material]
    try:
        materials += [pr_material(n, a.repo) for n in a.pr]
    except RuntimeError as e:
        return result(TOOL, "stopped", f"材料を取れない: {e}")
    if not materials:
        return result(TOOL, "stopped", "材料が無い（--material か --pr を渡す）")
    verdict, raw, usage = ask(build_prompt(mvv_path.read_text(encoding="utf-8"), a.gate, materials))
    boundary = (verdict or {}).get("boundary") or []
    passed = bool(verdict) and verdict["verdict"] == "follow" and not boundary
    record = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "gate": a.gate, "mvv": str(mvv_path), "material": a.material, "pr": a.pr,
              "verdict": (verdict or {}).get("verdict", "unreadable"), "reasons": (verdict or {}).get("reasons", []),
              "boundary": boundary, "passed": passed, **usage}
    log = Path(a.log).expanduser()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if verdict is None:
        return result(TOOL, "gate", f"{GATES[a.gate]}: 判定を読めない。利用者の承認を求める",
                      [{**record, "raw": raw}], usage, next="利用者の承認を求める")
    why = "越えない線に当たる: " + " / ".join(map(str, boundary)) if boundary else verdict["verdict"]
    if passed:
        return result(TOOL, "ok", f"{GATES[a.gate]}: MVV に従う。関門を省いて進めてよい", [record], usage)
    return result(TOOL, "gate", f"{GATES[a.gate]}: {why}。利用者の承認を求める", [record], usage,
                  next="利用者の承認を求める")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--mvv", required=True)
    c.add_argument("--gate", required=True, choices=sorted(GATES))
    c.add_argument("--material", nargs="+", default=[])
    c.add_argument("--pr", nargs="+", type=int, default=[])
    c.add_argument("--log", default="~/.local/state/ndf/mvv-gate.jsonl")
    c.add_argument("--repo")
    a = ap.parse_args()
    emit(cmd_check(a))


if __name__ == "__main__":
    main()
