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


def check_state_of(requests: Iterable[dict], ids: Iterable[str]) -> Optional[Path]:
    """依頼 `ids` のスプリントの検査のプランの状態ディレクトリ（#3585）。依頼の `sprint_state`（リリースの依頼のプランの状態）と
    同じディレクトリの `<連番>-check-state` のうち連番の最も大きいもの。無ければ None。"""
    for req in (r for r in requests if r.get("id") in set(ids) and r.get("sprint_state")):
        found = [d for d in Path(str(req["sprint_state"])).parent.glob("*-check-state") if CHECK_STATE.fullmatch(d.name)]
        if found := [d for d in found if d.is_dir()]:
            return max(found, key=lambda d: int(CHECK_STATE.fullmatch(d.name).group(1)))
    return None


def check_md(root: Path, mapped: dict, requests: Iterable[dict]) -> list[str]:
    """承認資料の `## 検査の結果` の本文。レーンごとに、その依頼のスプリントの検査のプランの結果（レビュー・リファクタリング・
    全体テスト）を書き、検査の状態の無い Pull Request は番号を挙げて無いと書く（#3585。#2655 の今の NDF の側）。"""
    from release_lib import materials

    requests = list(requests)
    lines: list[str] = []
    missing: list[int] = list(mapped["orphans"])
    for name, row in sorted(mapped["lanes"].items()):
        d = check_state_of(requests, row.get("requests") or [])
        got = materials.read_check(d, root) if d is not None else (None, None, None)
        if not any(got):
            missing += row["prs"]
            continue
        lines += [f"- レーン {name}（{' '.join(f'#{n}' for n in row['prs'])}。検査の状態 `{d}`）"]
        lines += [f"  {x}" for x in materials.check_lines(materials.Materials(*got))]
    if missing:
        lines.append(f"- 検査の状態が無い Pull Request: {' '.join(f'#{n}' for n in sorted(set(missing)))}")
    return lines or ["- 無し（含む Pull Request が無い）"]


def put_checks(approval: Path, lines: list[str]) -> None:
    """承認資料の `## 検査の結果` の節を `lines` へ置き換える（`notes --approval` と同じ `others.put_section`）。"""
    from release_lib import materials, others

    if not approval.is_file():
        return
    body = approval.read_text(encoding="utf-8").split("\n")
    others.put_section(body, materials.CHECK_HEADING, lines)
    approval.write_text("\n".join(body), encoding="utf-8")
