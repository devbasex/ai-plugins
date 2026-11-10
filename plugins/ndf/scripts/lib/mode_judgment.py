"""判定の根拠の検査とスプリントのモードの算出（#2923）。ファイルとプロセスに触れない純粋な関数だけを持つ。

判定表は `development-workflow` の SKILL.md の「2. 上から順に条件を判定する」の表で、複数に当たれば上の行が勝つ。
判定の根拠は 1 つの課題の {number, row, mode, condition, files} の辞書で、ループの記録の `judgments` にそのまま残る。
"""

from __future__ import annotations

ROWS = {1: "operation", 2: "documentation", 3: "standard", 4: "legacy-refactor", 5: "light"}
ROW_OF = {m: r for r, m in ROWS.items()}
DOCUMENTATION = "documentation"


class JudgmentError(Exception):
    """判定の根拠が契約に合わない。`reasons` に理由を 1 件ずつ持つ。"""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _checked_judgment(x) -> tuple[dict | None, list[str]]:
    if not isinstance(x, dict) or not isinstance(x.get("number"), int) or isinstance(x.get("number"), bool):
        return None, [f"課題の番号（number）が整数でない根拠がある: {str(x)[:80]}"]
    n, bad = x["number"], []
    row, mode, cond, files = x.get("row"), x.get("mode"), x.get("condition"), x.get("files")
    if not isinstance(row, int) or isinstance(row, bool) or row not in ROWS:
        bad.append(f"#{n}: 順（row）は判定表の 1〜5 のどれか（{row!r}）")
    elif mode != ROWS[row]:
        bad.append(f"#{n}: 順 {row} のモードは {ROWS[row]}（{mode!r}）")
    if not isinstance(cond, str) or not cond.strip():
        bad.append(f"#{n}: 当たった条件の文（condition）が空")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
        bad.append(f"#{n}: 触るファイル（files）が空")
    if bad:
        return None, bad
    return {"number": n, "row": row, "mode": mode, "condition": cond.strip(), "files": [f.strip() for f in files]}, []


def check_judgments(data, selected: list[int]) -> list[dict]:
    """判定の根拠のファイルの中身を検査し、選んだ課題の根拠を課題の番号の順で返す（I1・I2）。
    選んだ課題に根拠が無い・選んでいない課題の根拠がある・同じ課題が 2 件ある・項目が契約に合わないとき JudgmentError。"""
    items = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise JudgmentError(["判定の根拠のファイルに issues の配列が無い"])
    reasons: list[str] = []
    got: dict[int, dict] = {}
    for x in items:
        j, bad = _checked_judgment(x)
        reasons += bad
        if j is None:
            continue
        if j["number"] in got:
            reasons.append(f"#{j['number']}: 根拠が 2 件ある")
        got[j["number"]] = j
    named = {x.get("number") for x in items if isinstance(x, dict)}
    reasons += [f"#{n}: 選んだ課題の判定の根拠が無い" for n in selected if n not in named]
    reasons += [f"#{n}: 選んでいない課題の根拠がある" for n in sorted(n for n in got if n not in selected)]
    if reasons:
        raise JudgmentError(reasons)
    return [got[n] for n in sorted(got)]


def sprint_mode(judgments: list[dict]) -> str:
    """スプリントのモード: 根拠のうち順が最も小さい行のモード（I3）。空のとき・`documentation` と他のモードが
    混ざるとき（I8）は JudgmentError。"""
    if not judgments:
        raise JudgmentError(["判定の根拠が 1 件も無い"])
    modes = {j["mode"] for j in judgments}
    if DOCUMENTATION in modes and len(modes) > 1:
        by = {m: [j["number"] for j in judgments if j["mode"] == m] for m in sorted(modes, key=ROW_OF.__getitem__)}
        listed = "、".join(f"{m}: {' '.join(f'#{n}' for n in ns)}" for m, ns in by.items())
        raise JudgmentError([f"documentation と他のモードが混ざる（{listed}）。一方のモードの課題を loop exclude で外す"])
    return ROWS[min(j["row"] for j in judgments)]


def redecide(judgments: list[dict], kept: list[int], current: str) -> str:
    """モードの決め直し（I4）: 残った課題の根拠だけから sprint_mode と同じ規則で決める。残った課題の根拠が無い・
    今のモードより上の行になるときは JudgmentError。"""
    by = {j["number"]: j for j in judgments}
    if missing := [n for n in kept if n not in by]:
        raise JudgmentError([f"#{n}: 残った課題の判定の根拠が記録に無い" for n in missing])
    mode = sprint_mode([by[n] for n in kept])
    if current in ROW_OF and ROW_OF[mode] < ROW_OF[current]:
        raise JudgmentError([f"決め直したモード {mode} が今のモード {current} より上になる"])
    return mode
