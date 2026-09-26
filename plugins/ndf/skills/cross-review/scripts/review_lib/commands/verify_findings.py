"""副命令 `verify-findings`（#1142 の C2）。"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from typing import Any, Optional

import review_lib  # noqa: E402
from review_lib import findings as findings_mod, matching, store  # noqa: E402


# 実行検証の上限。実測が無いため、まず 300 秒で置く（#156 の 3 本目）。
VERIFY_TIMEOUT_SECONDS = 300

# 再現とみなす終了コードの既定。**「0 でない」を再現としない。** pytest は対象が無い
# ときに 4、収集 0 件で 5 を返し、バグの再現と指摘の書き誤りが別の値で分かれる。
VERIFY_REPRODUCED_CODES = (1,)


def _resolves_inside(token: str, work: str) -> bool:
    """位置指定が作業ツリーの配下へ解決されるか。

    **`realpath` で解決する。** `abspath` は symlink をたどらないため、作業ツリーの
    中から外を指すリンクを見逃す（実測）。`::` を含む値はファイル名の部分だけを見る。
    """
    path = token.split("::", 1)[0]
    if not path:
        return False
    root = os.path.realpath(work)
    target = os.path.realpath(os.path.join(work, path))
    return target == root or target.startswith(root + os.sep)


def _verify_argv(check: str, allowed: list[str], work: str) -> Optional[list[str]]:
    """実行してよい形なら引数の並びを返す。**そうでなければ `None`。**

    照合はトークン単位で行う。文字列の前方一致では `pytest` の宣言に `pytest-danger`
    が当たる。**メタ文字の一覧は持たない**（列挙から漏れた文字が通る）。区切りの
    メタ文字は `shlex.split` でトークンの一部になるため、照合で自然に外れる。
    """
    try:
        argv = shlex.split(str(check or ""))
    except ValueError:
        return None
    if not argv:
        return None
    for candidate in allowed:
        try:
            prefix = shlex.split(str(candidate or ""))
        except ValueError:
            continue
        if not prefix or argv[:len(prefix)] != prefix:
            continue
        rest = argv[len(prefix):]
        # 実行してよいのは対象を絞る引数までである。`-c` や `-p` は任意の設定と
        # プラグインを読み込ませる。
        if any(token.startswith("-") for token in rest):
            return None
        # **位置指定を 1 つも持たない実行は、指摘とは無関係な失敗を拾う。**
        if not rest:
            return None
        if not all(_resolves_inside(token, work) for token in rest):
            return None
        return argv
    return None


def _run_verify(argv: list[str], work: str) -> Optional[int]:
    """`shell=False` で実行し、終了コードを返す。起動できなければ `None`。"""
    try:
        proc = subprocess.run(
            argv, cwd=work, capture_output=True, text=True,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode


def _merged_root(
    finding: dict[str, Any], by_id: dict[Any, dict[str, Any]]
) -> dict[str, Any]:
    """束ねられた側から代表をたどる。**環になっていたらその場で止める。**"""
    seen = {finding.get("finding_id")}
    current = finding
    while current.get("merged_into"):
        nxt = by_id.get(current["merged_into"])
        if nxt is None or nxt.get("finding_id") in seen:
            break
        seen.add(nxt.get("finding_id"))
        current = nxt
    return current


def _run_finding_checks(
    targets: list[dict[str, Any]],
    allowed: list[str],
    work: str,
    codes: set[int],
    run: Any,
) -> None:
    """各指摘の検証コマンドを重複なく実行し、結果を記録する。"""
    ran: dict[tuple[str, ...], Optional[int]] = {}

    for finding in targets:
        check = str(finding.get("suggested_check") or "")
        record: dict[str, Any] = {
            "command": check, "finding_id": finding.get("finding_id"),
            "exit_code": None, "result": "not_run", "ran_at": None,
        }
        argv = _verify_argv(check, allowed, work) if allowed else None
        if argv is not None:
            key = tuple(argv)
            if key in ran:
                code = ran[key]
            else:
                code = run(argv, work)
                ran[key] = code
            record["exit_code"] = code
            record["ran_at"] = review_lib._now()
            if code in codes:
                record["result"] = "reproduced"
            elif code == 0:
                record["result"] = "not_reproduced"
        finding["verification"] = record


def _propagate_best_verification(
    targets: list[dict[str, Any]], by_id: dict[Any, dict[str, Any]]
) -> None:
    """束ねた組から最良の検証結果を代表へ反映する。"""
    for rep in targets:
        if rep.get("merged_into"):
            continue
        best = rep["verification"]
        for member in targets:
            if member is rep or not member.get("merged_into"):
                continue
            if _merged_root(member, by_id) is not rep:
                continue
            if findings_mod._VERIFY_RANK.get(findings_mod._verify_result(member), -1) > \
               findings_mod._VERIFY_RANK.get(str(best.get("result") or "not_run"), -1):
                best = member["verification"]
        if best is not rep["verification"]:
            rep["verification"] = dict(best)


def _verify_findings(
    st: dict[str, Any],
    round_no: int,
    allowed: list[str],
    work: str,
    reproduced_codes: Optional[list[int]] = None,
    runner: Optional[Any] = None,
) -> None:
    """`suggested_check` を実行し、結果を `verification` へ残す（#156）。

    **担当の再評価より先に走らせる。** 機械が再現した事実は、担当の支持より確かである。

    **束ねた組の全員を対象にする**（`docs/specifications/cross-review-evidence-based.md`
    の「重複の統合」）。
    代表の `suggested_check` だけを読むと、代表が手順を書いていない組は、束ねられた側が
    実行できる手順を書いていても実行回数 0・`not_run` のまま `insufficient_evidence` へ
    落ちる。**どちらが先に取り込まれたかで採否が変わる。** 1 段目の統合は実行検証より
    **前**にあるため、束ねられた側を読み飛ばすと、その手順は一度も実行されない。

    代表が持つのは組の集約である。`reproduced` > `not_reproduced` > `not_run` の順で
    最初に当たった 1 件を採り、**出所を `verification.finding_id` へ残す**。

    **同じコマンドは 1 度しか実行しない。** 組の全員が同じ `suggested_check` を書くのは
    普通に起こり（統合の条件は本文の一致である）、そのたびに走らせると実行が増える。

    **`ran_at` は結果を受け取った記録にだけ入れる。** 初期化で入れると、実行していない
    `not_run` の記録にも時刻が残り、実行済みに見える。記録の `result` / `exit_code` /
    `ran_at` が同じことを指すようにする。
    """
    codes = set(reproduced_codes or VERIFY_REPRODUCED_CODES)
    run = runner or _run_verify
    targets = [
        f for f in st.get("review_findings") or [] if f.get("round") == round_no
    ]
    by_id = {f.get("finding_id"): f for f in targets}
    _run_finding_checks(targets, allowed, work, codes, run)
    _propagate_best_verification(targets, by_id)


def cmd_verify_findings(args: argparse.Namespace) -> None:
    """Step 2.5 前段 — 重複を束ね（1 段目）、`suggested_check` を実行する（#156）。

    **反証より前に置く。** 反証を返す担当は実行の結果を読んだうえで賛否を決める。
    統合の 1 段目も反証より前に行う。後にすると、束ねられる 2 件へ別々に反証が付き、
    どちらの値を採るかという判断が増える。

    **実行してよいのは `init` へ渡されたコマンドだけである。** 渡されていなければ
    実行を行わず、区分は根拠と反証で決まる（`docs/06-evidence.md`）。
    """
    pr = args.pr
    st = store._load(pr)
    if not st.get("rounds"):
        review_lib.die("state.rounds が空。`state.py start-round` を先に呼んでください")
    round_no = st["rounds"][-1]["round"]

    _merge_duplicates(st, round_no)

    allowed = [str(c) for c in (st.get("verify_commands") or [])]
    codes = [int(c) for c in (st.get("verify_exit_codes") or [])] or None
    _verify_findings(
        st,
        round_no,
        allowed=allowed,
        work=str(st.get("worktree_path") or "."),
        reproduced_codes=codes,
    )

    merged = 0
    results: dict[str, int] = {}
    for finding in st.get("review_findings") or []:
        if finding.get("round") != round_no:
            continue
        if finding.get("merged_into"):
            merged += 1
            continue
        result = findings_mod._verify_result(finding)
        results[result] = results.get(result, 0) + 1
    store._save(pr, st)
    review_lib.info(
        f"✅ 統合: {merged} 件を束ねた / 実行検証: "
        + (" ".join(f"{k}={v}" for k, v in sorted(results.items())) or "対象なし")
    )


def _merge_duplicates(st: dict[str, Any], round_no: int) -> None:
    """同じラウンドの同じ指摘を 1 件へ束ねる（#156）。

    **結び方は振動の検知と同じにしない。** 振動は位置・近傍・本文の**いずれか**で結ぶが、
    統合は近傍**かつ**本文の一致を求める。**過剰な統合は指摘を失うが、統合し損ねても
    失われるものは無い**（両方が区分に載り、`origin_runtimes` が 1 者ずつになるだけ）。
    誤りの代償が非対称であるため、厳しい側へ倒す。

    **束ねられた側は消さない。** `merged_into` を書いて残す。消すと、反証の結果が
    その `finding_id` を指したときに結び先を失う。
    """
    findings = st.setdefault("review_findings", [])
    targets = [f for f in findings if f.get("round") == round_no]
    for f in targets:
        f.setdefault("origin_runtimes", [f.get("agent")])

    for i, rep in enumerate(targets):
        if rep.get("merged_into"):
            continue
        for other in targets[i + 1:]:
            if other.get("merged_into") or other.get("agent") == rep.get("agent"):
                continue
            if not _is_near(rep, other):
                continue
            if matching._normalized_body(rep.get("body")) == matching._normalized_body(other.get("body")):
                findings_mod._absorb(rep, other)
            else:
                # **位置の一致は候補の抽出までである。** 本文が違う組は残し、
                # 反証で相互に `duplicate` が付いたときに 2 段目で統合する。
                rep.setdefault("duplicate_candidates", []).append(other["finding_id"])
                other.setdefault("duplicate_candidates", []).append(rep["finding_id"])


def _is_near(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """同じファイルで、行差が `OSCILLATION_NEAR_LINES` 以内か。"""
    if str(a.get("path") or "") != str(b.get("path") or ""):
        return False
    try:
        return abs(int(a.get("line")) - int(b.get("line"))) <= matching.OSCILLATION_NEAR_LINES
    except (TypeError, ValueError):
        return False
