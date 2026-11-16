"""並列の計画のまとめたリリースの承認資料の `## 検査の結果`（#3585。#2655 の今の NDF の側）。`parallel_release.present` が使う。

提示のプランの `notes` は 1 つの検査の状態しか受けず、まとめたリリースには渡す状態が無いため「無し」と書く。ここで依頼の
`sprint_state`（レーンのリリースの依頼のプランの状態）から同じスプリントの検査のプランの状態を探し、含む Pull Request ごとに
レビュー・リファクタリング・全体テストの結果を書き直す。検査の状態の無い Pull Request は番号を挙げて無いと書く。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

CHECK_STATE = re.compile(r"(\d+)-check-state")
SEQ = re.compile(r"(\d+)-")


def check_state_of(req: dict) -> Optional[Path]:
    """依頼 `req` のスプリントの検査のプランの状態ディレクトリ（#3585）。依頼の `sprint_state`（リリースの依頼のプランの状態
    `<連番>-...-state`）と同じディレクトリの `<連番>-check-state` のうち、依頼の連番より小さく最も大きいもの（同じ親で依頼の後に
    走った次のスプリントの検査を拾わない）。`sprint_state` の名前に連番が無ければ連番の最も大きいもの。無ければ None。"""
    if not req.get("sprint_state"):
        return None
    state = Path(str(req["sprint_state"]))
    m = SEQ.match(state.name)
    below = int(m.group(1)) if m else None
    by = [
        (int(c.group(1)), d)
        for d in state.parent.glob("*-check-state")
        if (c := CHECK_STATE.fullmatch(d.name)) and d.is_dir() and (below is None or int(c.group(1)) < below)
    ]
    return max(by, key=lambda x: x[0])[1] if by else None


def check_md(root: Path, mapped: dict, requests: Iterable[dict]) -> list[str]:
    """承認資料の `## 検査の結果` の本文。レーンの依頼ごとに、その依頼の Pull Request とその依頼のスプリントの検査のプランの結果
    （レビュー・リファクタリング・全体テスト）を書き、検査の状態の無い Pull Request は番号を挙げて無いと書く（#3585。#2655 の
    今の NDF の側）。同じレーンに別のスプリントの依頼が 2 件あれば、行を依頼ごとに分ける。"""
    from release_lib import included, materials

    by_id = {r.get("id"): r for r in requests}
    lines: list[str] = []
    missing: list[int] = list(mapped["orphans"])
    for name, row in sorted(mapped["lanes"].items()):
        covered: set[int] = set()
        for rid in row.get("requests") or []:
            req = by_id.get(rid) or {}
            prs = sorted(set(req.get("prs") or []) & set(row["prs"]))
            covered |= set(prs)
            d = check_state_of(req)
            got = materials.read_check(d, root)
            if not any(got):
                missing += prs
                continue
            lines += [f"- レーン {name}（{included.pr_refs(prs)}。検査の状態 `{d}`）"]
            lines += [f"  {x}" for x in materials.check_lines(materials.Materials(*got))]
        missing += [n for n in row["prs"] if n not in covered]
    if missing:
        lines.append(f"- 検査の状態が無い Pull Request: {included.pr_refs(sorted(set(missing)))}")
    return lines or ["- 無し（含む Pull Request が無い）"]


def put_checks(approval: Path, lines: list[str]) -> None:
    """承認資料の `## 検査の結果` の節を `lines` へ置き換える（`notes --approval` と同じ `others.put_section`）。"""
    from release_lib import materials, others

    if not approval.is_file():
        return
    body = approval.read_text(encoding="utf-8").split("\n")
    others.put_section(body, materials.CHECK_HEADING, lines)
    approval.write_text("\n".join(body), encoding="utf-8")
