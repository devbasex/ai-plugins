"""queue の差し込み（`{queue_pr:<名>}`・`{queue_state:<名>}`）を、名前の一致する計画の結果で埋める（#1488・#2079）。
`{queue_record}` は、計画の `課題` のリリース記録を置いた PR を GitHub から探して埋める（#2230。`sprint_close_lib/record_find.py`）。

`queue.py` から切り出した。`queue` は同じ名前で読み込み直して公開する。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from supervise_lib.paths import report_result, state_dir_of
from supervise_lib.plan import pr_number

FINISHED = ("完了", "関門")  # 打ち直しでも流し直さない結果（止まったは次の実行の回で頭から流す）


QUEUE_PR = re.compile(r"\{queue_pr:([A-Za-z0-9._-]+)\}")  # 名前の一致する計画の Pull Request 1 本
QUEUE_RECORD = "{queue_record}"  # 計画の `課題` のリリース記録を置いた PR（#2230）
QUEUE_STATE = re.compile(r"\{queue_state(\??):([A-Za-z0-9._-]+)\}")  # 名前の一致する計画の状態ディレクトリ（#1488）。`?` 付きは無ければ空


def plan_pr(item: dict) -> str:
    """完了か関門で終えた計画の報告の Pull Request の番号（設計のプランは承認ゲート 1 の関門で終わる。#1426 の決定 4）。無ければ空。"""
    rep = Path(item.get("report") or "")
    if item.get("result") not in FINISHED or not rep.is_file():
        return ""
    m = re.search(r"^- Pull Request: (.*)$", rep.read_text(), re.M)
    return pr_number(m.group(1).strip()) if m else ""


def named(plan: str, name: str) -> bool:
    stem = Path(plan).stem
    return stem == name or stem.endswith(f"-{name}")


def results_named(name: str, items: list[dict], plan: str) -> list[dict]:
    """名前の一致する計画の結果。`items` に無ければ、`plan` と同じディレクトリの計画の報告を読む（関門の後に単独で流すとき）。"""
    hits = [i for i in items if named(i.get("plan", ""), name)]
    if hits:
        return hits
    return [
        {
            "plan": str(f),
            "result": report_result((state_dir_of(str(f)) / "report.md").read_text()),
            "report": str(state_dir_of(str(f)) / "report.md"),
        }
        for f in sorted(Path(plan).parent.glob("*.json"))
        if str(f) != plan and named(str(f), name) and (state_dir_of(str(f)) / "report.md").is_file()
    ]


def _fill_prs(text: str, names: list[str], items: list[dict], plan: str) -> tuple[str, list[str]]:
    """{queue_pr:<名>} を完了か関門で終えた結果の PR の番号（無ければ 0）で置き換える。そうした結果が無い名前は欠けの文で返す。
    設計のプランは承認ゲート 1 の関門で終わるため、関門も受ける（plan_pr と同じ FINISHED）。"""
    missing = []
    for name in names:
        done = [i for i in results_named(name, items, plan) if i.get("result") in FINISHED]
        if not done:
            missing.append(f"{{queue_pr:{name}}}（名前の一致する計画の完了か関門で終えた報告が無い）")
            continue
        prs = [n for n in (plan_pr(i) for i in done) if n]
        text = text.replace(f"{{queue_pr:{name}}}", prs[-1] if prs else "0")
    return text, missing


def _fill_states(text: str, states: list[tuple[str, str]], items: list[dict], plan: str) -> tuple[str, list[str]]:
    """{queue_state:<名>} を状態ディレクトリの絶対パスで置き換える。`?` 付きは無ければ空文字、無印は欠けの文で返す。"""
    missing = []
    for opt, name in states:
        found = [state_dir_of(str(Path(i["plan"]).absolute())) for i in results_named(name, items, plan) if i.get("plan")]
        dirs = [d for d in found if d.is_dir()]
        if not dirs and not opt:
            missing.append(f"{{queue_state:{name}}}（名前の一致する計画の状態ディレクトリが無い）")
            continue
        text = text.replace(f"{{queue_state{opt}:{name}}}", str(dirs[-1]) if dirs else "")
    return text, missing


def _fill_record(text: str, plan: str) -> tuple[str, list[str]]:
    """{queue_record} を、計画の `課題` のリリース記録を置いた PR の番号で置き換える。見つからなければ理由の文で返す。"""
    from sprint_close_lib.record_find import find_record  # GitHub を読む差し込みを持つ計画だけが読み込む

    try:
        info = json.loads(text)
    except ValueError:
        return text, [f"{QUEUE_RECORD}（計画の JSON を読めない）"]
    issues = [n for n in info.get("課題") or [] if isinstance(n, int)] if isinstance(info, dict) else []
    root = (info.get("リポジトリ") if isinstance(info, dict) else None) or str(Path(plan).parent)
    hit = find_record(root, None, issues)
    if hit.pr is None:
        return text, [f"{QUEUE_RECORD}（{hit.reason()}）"]
    return text.replace(QUEUE_RECORD, str(hit.pr)), []


def fill_queue_pr(plan: str, items: list[dict]) -> str | None:
    """計画の {queue_pr:<名>} を、前のステージの名前の一致する計画の Pull Request 1 本で置き換える。
    前のステージに無ければ同じディレクトリの計画の報告を読む（関門の後に単独で流すとき）。完了して PR が無い・飛ばされたなら 0。
    {queue_state:<名>} は、名前の一致する計画の状態ディレクトリの絶対パスで置き換える（#1488）。結果（完了した報告・
    状態ディレクトリ）が見つからなければ、0・空文字で埋めずに計画を書き換えず理由を返す（#2079）。
    {queue_state?:<名>} だけは、無いことを読む側が扱うと明示した差し込みで、無ければ空文字で埋める（単独で流す本番の approve）。
    {queue_record} は計画の `課題` のリリース記録を置いた PR で置き換え、見つからなければ同じく書き換えずに理由を返す（#2230）。"""
    try:
        text = Path(plan).read_text()
    except OSError as e:
        return f"計画を読めない: {e}"
    names, states = sorted(set(QUEUE_PR.findall(text))), sorted(set(QUEUE_STATE.findall(text)))
    record = QUEUE_RECORD in text
    if not names and not states and not record:
        return None
    text, missing_prs = _fill_prs(text, names, items, plan)
    text, missing_states = _fill_states(text, states, items, plan)
    text, missing_record = _fill_record(text, plan) if record else (text, [])
    missing = missing_prs + missing_states + missing_record
    if missing:
        return "差し込みを埋める前の計画の結果が見つからない: " + "、".join(missing)
    write_text_atomic(Path(plan), text)
    return None


def write_text_atomic(path: Path, text: str) -> None:
    """一時ファイルへ書いてから rename する（待つ側が書きかけを読まない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
