"""CD の結果の読み取り（#2623 の F7）。本番チャネルへのマージの後に、行の結果の読み方で CD の状態を読み、3 つの値へ畳む。

値は `success` / `failure` / `undetermined`（読めない・上限に当たった）と、本番チャネルへ自動で届く行が無いときの `none`。
「成功」は状態を読んで確かめたときだけ返す（I13）。読み取りの失敗は例外にせず未確定にする。結果のファイルは計画の状態
ディレクトリに置く作業用の値で、過去の結果はリリース記録のコメントが残す。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import delivery
import gh_parts
from release_lib.deploy import write_material

SUCCESS, FAILURE, UNDETERMINED, NONE = "success", "failure", "undetermined", "none"
WAIT = "wait"
POLL_SECONDS = 15  # 照会の間隔（release の references/completion-check.md の既定）
COMMAND_WAIT = 75  # result.command の「待つ」の終了コード


@dataclass
class CdResult:
    value: str
    revision: str = ""
    reason: str = ""
    evidence: list[dict] = field(default_factory=list)


def _read_workflow(root, name: str, revision: str, _limit: float) -> tuple[str, str, str]:
    """GitHub Actions の実行を読む。(値か WAIT, 説明, URL) を返す。実行が無いか進行中なら待つ。"""
    args = ["run", "list", "--commit", revision, "--workflow", name, "--json", "status,conclusion,url", "--limit", "1"]
    p = gh_parts.gh(args, cwd=root)
    if p.returncode != 0:
        return WAIT, f"gh run list が失敗: {p.stderr.strip()[:200]}", ""
    try:
        runs = json.loads(p.stdout or "[]")
    except ValueError:
        return WAIT, "gh run list の出力を読めない", ""
    if not runs:
        return WAIT, f"{revision[:7]} の {name} の実行が無い", ""
    run = runs[0]
    url = run.get("url") or ""
    if run.get("status") != "completed":
        return WAIT, f"{name} は {run.get('status')}", url
    conclusion = run.get("conclusion")
    return (SUCCESS, "", url) if conclusion == "success" else (FAILURE, f"{name} が {conclusion} で終わった", url)


def _read_command(root, command: str, revision: str, limit: float) -> tuple[str, str, str]:
    """コマンドの終了コードを読む。0 成功 / 75 待つ / 1 失敗 / それ以外と時間切れは未確定。"""
    try:
        p = subprocess.run(
            command.replace("{revision}", shlex.quote(revision)),
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=max(1.0, limit),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return UNDETERMINED, "結果を読むコマンドが時間切れになった", ""
    except OSError as e:
        return UNDETERMINED, f"結果を読むコマンドを起動できない: {e}", ""
    if p.returncode == 0:
        return SUCCESS, "", ""
    if p.returncode == COMMAND_WAIT:
        return WAIT, "結果を読むコマンドが待ちを返した", ""
    tail = (p.stderr or p.stdout or "").strip()[-200:]
    value = FAILURE if p.returncode == 1 else UNDETERMINED
    return value, f"結果を読むコマンドが終了コード {p.returncode} で終わった: {tail}", ""


def _read_row(root, row: dict, revision: str, deadline: float, clock, sleep) -> dict:
    """1 行の結果を上限まで読む。上限に当たるか読む手段が無ければ未確定を返す。"""
    target = str(row.get("target") or "")
    spec = row.get("result") or {}
    workflow, command = spec.get("workflow"), spec.get("command")
    if not (workflow or command):
        return {
            "target": target,
            "via": "",
            "value": UNDETERMINED,
            "reason": f"{target} の結果の読み方（result）が宣言されていない",
            "url": "",
        }
    read, way = (_read_workflow, workflow) if workflow else (_read_command, command)
    end = min(deadline, clock() + (spec.get("timeout_seconds") or delivery.DEFAULT_RESULT_TIMEOUT))
    while True:
        value, reason, url = read(root, way, revision, end - clock())
        if value != WAIT:
            return {"target": target, "via": way, "value": value, "reason": reason, "url": url}
        if clock() + POLL_SECONDS >= end:
            why = f"{target} の結果が待ちの上限までに決まらなかった（最後に読んだ値: {reason}）"
            return {"target": target, "via": way, "value": UNDETERMINED, "reason": why, "url": url}
        sleep(POLL_SECONDS)


def _fold(rows: list[dict]) -> tuple[str, str]:
    """行ごとの値を 1 つへ畳む。1 行でも失敗なら失敗、すべて成功なら成功、それ以外は未確定。"""
    values = {r["value"] for r in rows}
    value = FAILURE if FAILURE in values else SUCCESS if values == {SUCCESS} else UNDETERMINED
    return value, " / ".join(r["reason"] for r in rows if r["value"] == value and r["reason"])


def read_cd(root, decl: delivery.DeliveryDecl, revision: str, *, clock=time.monotonic, sleep=time.sleep) -> CdResult:
    """`revision` に対する CD の結果を読む。本番チャネルへ自動で届く行が無ければ `none`。"""
    rows = delivery.merge_rows(decl)
    if not rows:
        return CdResult(NONE, revision, "本番チャネルへ自動で届く delivery の行が無い")
    if not revision:
        return CdResult(UNDETERMINED, "", "本番チャネルのリビジョンを読めない")
    deadline = clock() + delivery.RESULT_TOTAL_LIMIT
    evidence = [_read_row(root, r, revision, deadline, clock, sleep) for r in rows]
    value, reason = _fold(evidence)
    return CdResult(value, revision, reason, evidence)


def save_result(path, got: CdResult) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_material(path, json.dumps(asdict(got), ensure_ascii=False, indent=2) + "\n")


def load_result(path) -> CdResult | None:
    """結果のファイルを読む。渡されない・無い・`none` なら None（記録に CD の行を足さない）。壊れていれば未確定。"""
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        got = CdResult(data["value"], data.get("revision") or "", data.get("reason") or "", data.get("evidence") or [])
    except (OSError, ValueError, KeyError, TypeError):
        return CdResult(UNDETERMINED, "", f"結果のファイル {path} を読めない")
    return None if got.value == NONE else got
