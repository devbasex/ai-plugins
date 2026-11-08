"""mvv_llm.py: プロジェクト MVV の LLM の呼び出し（候補の生成・照合）と照合の記録（`project-mvv.py`・#1366）。

LLM は `supervise_lib/claude.py` の `call_claude` を Tool なしで 1 回呼ぶ（NDF_SUPERVISE_CLAUDE で差し替えられる）。
呼び出しごとに `call_claude` が使用量の帳簿へ 1 行を足す。照合は本文を書き換えず、判定と箇所を `project-mvv.jsonl` へ残すだけである（I14）。

LLM の行は照合の入力（判定の指示とプロンプト全体）の sha256 を `input_sha256` に持つ。呼ぶ側が再利用を求めたとき（`reuse`。
スプリント MVV の照合だけ）は、同じ入力の最新の判定が「従う」なら LLM を呼ばず、`reused_from`（元の LLM の判定の時刻）を持つ行を
足す（#2614）。共通原則を読めないときは入力を見分けられないため、再利用せず `input_sha256` も書かない。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import clock
import project_mvv as pm
import project_mvv_signals as pms

TOOL = "project-mvv"
SCRIPTS = Path(__file__).resolve().parents[1]

VET_VERDICTS = ("follow", "suspect", "unknown")

VET_SYSTEM = """あなたは MVV の照合者である。Tool は無い。
渡された本文（プロジェクト MVV の候補・改訂案、またはスプリント MVV）が、NDF の共通原則と（あれば）承認済みのプロジェクト MVV に
従うかを判定する。
- 共通原則の必ず人の承認が要る操作（C1〜）のどれかを承認なしで行えると書く、または優先順位を変える・入れ替えるなら suspect。
  locations の item に C の番号か priority を書く
- スプリント MVV がプロジェクト MVV のレッドライン（P の番号）を緩める・Value を打ち消すなら suspect。item にその番号を書く
- 原則に反する（人を害する・人の発展を妨げる）なら suspect
- 反する所が無ければ follow。材料から決められなければ unknown（迷ったら unknown）
- 本文を書き換えない。判定と箇所を返すだけである
出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|suspect|unknown", "locations": [{"item": "C4|priority|Value 3|P1", "reason": "..."}]}"""


def call_llm_json(system: str, prompt: str, root: Path, kind: str) -> tuple[dict | None, str, dict]:
    """最小構成の claude -p を 1 回呼ぶ（Tool なし）。(JSON, 生の文, 使用量)。

    `supervise_lib.claude` が読む外部パッケージを、読む前に用意する（足りなければ uv の環境で起動し直す。
    呼び出し元はここまで外へ書かない）。
    """
    import deps

    deps.require("procs", "locks")
    import supervise_lib  # noqa: F401  lib/ を sys.path へ足す
    from supervise_lib.claude import call_claude

    # 帳簿へは call_claude（起動の共有処理）が呼び出し元を付けて 1 行を書く（#1953 の決定 2）
    res = call_claude(system, prompt, None, str(root), 900, caller={"source": TOOL, "kind": kind})
    usage = {"cost_usd": res.get("cost"), "seconds": res.get("seconds")}
    text = str(res.get("text") or "")
    if not res.get("ok"):
        return None, text[-500:], usage
    return pm.json_object(text), text[-500:], usage


def vet_prompt(kind: str, body: str, mvv: pm.ProjectMvv) -> str:
    what = {
        "candidate": "プロジェクト MVV の候補",
        "revision": "プロジェクト MVV の改訂案",
        "sprint": "スプリント MVV",
        "issue": "起票する課題の本文",
    }[kind]
    parts = [pm.principles()]
    if kind in ("sprint", "revision", "issue"):  # issue: 見直しのニーズ・シーズと大整理の大方針（#2315 の決定 10）
        parts.append(pm.project_part(mvv))
    parts += [pm.contract(), f"# 照合する本文（{what}）", body.strip()]
    return "\n\n".join(parts) + "\n"


def input_sha256(prompt: str) -> str:
    """照合の入力の sha256。判定の指示とプロンプト全体（共通原則・プロジェクト MVV・判定の決まり・種類・本文）から作る。"""
    return pm.sha256_text(VET_SYSTEM + "\0" + prompt)


def principles_readable() -> bool:
    """共通原則のファイルを読めるか。読めないと `pm.principles()` は毎回同じ代わりの文を返し、入力で見分けられない。"""
    try:
        pm.PRINCIPLES_PATH.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    return True


def latest_vet_where(pred) -> dict | None:
    """照合の記録のうち `pred` を満たす最新の行（時刻が最も後、同じなら後ろの行）。読めない・壊れた行は飛ばす。

    旧の置き場から足し合わせた行は末尾に付くため、行の順は時刻の順でない（#2766）。
    """
    rows = [r for r in pms.read_jsonl(pm.vet_log_path()) if pred(r)]
    return max(enumerate(rows), key=lambda x: (str(x[1].get("at") or ""), x[0]))[1] if rows else None


def latest_vet(key: str) -> dict | None:
    """同じ照合の入力の最新の判定（時刻が最も後、同じなら後ろの行）。`repo` では絞らない。"""
    return latest_vet_where(lambda r: r.get("input_sha256") == key)


def _base_record(root: Path, body: str, kind: str, mvv) -> dict:
    """照合の記録の基礎。判定は「読めない」から始める。"""
    return {
        "at": clock.now_iso("utc"),
        "repo": pm.mvv_repo_key(root),
        "kind": kind,
        "sha256": pm.sha256_text(body),
        "project_sha256": mvv.sha256 if mvv.approved else "",
        "project_mvv": pm.record(mvv),
        "verdict": "unreadable",
        "locations": [],
    }


def _machine_prechecks(rec: dict, kind: str, body: str, mvv) -> bool:
    """候補・改訂の形の検査と改訂の前提の検査を記録へ書く。LLM に回さず決まったら真。"""
    if kind in ("candidate", "revision"):
        probs = pm.shape_problems(body)
        if probs:
            rec.update(verdict="suspect", locations=probs, machine=True)
            return True
    if kind == "revision" and not mvv.approved:
        rec["locations"] = [
            {"item": "project_mvv", "reason": f"改訂には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）"}
        ]
        return True
    return False


def _resolve_verdict(root: Path, prompt: str, key: str | None, reuse: bool) -> tuple[dict, dict]:
    """(記録へ足す項目, 使用量)。再利用が成り立てば LLM を呼ばない。LLM の答えが読めなければ判定を足さない。"""
    prev = latest_vet(key) if reuse and key else None
    if prev is not None and prev.get("verdict") == "follow":
        # 再利用の行は費用と秒を持たない。元の判定は常に LLM の行の時刻を指す（I3）
        locs = [x for x in prev.get("locations") or [] if isinstance(x, dict)]
        return {"verdict": "follow", "locations": locs, "input_sha256": key, "reused_from": prev.get("reused_from") or prev.get("at")}, {}
    data, raw, usage = call_llm_json(VET_SYSTEM, prompt, root, "mvv-vet")
    fields: dict = {}
    if data is None or data.get("verdict") not in VET_VERDICTS:
        fields["raw"] = raw
    else:
        fields["verdict"] = data["verdict"]
        fields["locations"] = [x for x in data.get("locations") or [] if isinstance(x, dict)]
    if key:
        fields["input_sha256"] = key
    fields.update(cost_usd=usage.get("cost_usd"), seconds=usage.get("seconds"))
    return fields, usage


def vet_body(root: Path, body: str, kind: str, reuse: bool = False) -> tuple[dict, dict]:
    """(照合の記録, 使用量)。記録は project-mvv.jsonl へ足す。本文は書き換えない（I14）。

    `reuse` が真なら、同じ照合の入力の最新の判定が「従う」のとき LLM を呼ばずにその判定を使う（#2614）。"""
    mvv = pm.load_mvv(root)
    rec = _base_record(root, body, kind, mvv)
    usage: dict = {}
    if not _machine_prechecks(rec, kind, body, mvv):
        prompt = vet_prompt(kind, body, mvv)
        key = input_sha256(prompt) if principles_readable() else None
        fields, usage = _resolve_verdict(root, prompt, key, reuse)
        rec.update(fields)
    pms.append_jsonl(pm.vet_log_path(), rec)
    return rec, usage


def escape_events(root: Path) -> list[dict]:
    """流出不具合の記録（check-trigger.py の `kind: escape`）。読めなければ空。"""
    try:
        spec = importlib.util.spec_from_file_location("ndf_check_trigger", SCRIPTS / "check-trigger.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [e for e in mod.read_events(root) if e.get("kind") == "escape"]
    except Exception:  # noqa: BLE001  集計の材料が欠けても止めない
        return []
