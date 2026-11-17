"""人のいない実行（`NDF_FACTORY_RUN`）の conductor の guard（#3242・#3746）。

`NDF_FACTORY_RUN` が空の実行では何もしない。事象ごとに次を行う。

- SessionStart: 更新から `hook.unattended-mark` の秒を過ぎた印を消す（SessionEnd で消し残したもの）
- UserPromptSubmit: プロンプトの 1 行目が `/ndf:development-workflow`（前に `/goal ` を許す）なら印を作り、人のいない実行の
  決まりの案内に契約から作った形の説明を添えて返す。worker・レビューの `claude -p` には印を付けない
- Stop: 印のあるセッションで、最後の応答が区間の報告の形（契約 `contracts/hooks/unattended-report.json`）に合わないか、
  `次のコマンド:` にコマンドを載せたまま結果が `判断が要る` でも `完了` でもなく区分が `ndf-ask:human-action` でもなければ
  差し戻す。背景の作業か予定が残る Stop は差し戻さない。差し戻しの回数はセッションの数え手が利用者の入力ごとに理由を
  またいで数え（#3345 の決定 92・`hook.pushback`）、上限の後は dispatch が通して計測と待ちの知らせに残す
- SessionEnd: 印を消す

印は `<状態の置き場>/hooks/unattended/<session_id>.json` に置く。形の判定は `ndf.domains.hooks.report` が持つ。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from ndf import identity
from ndf.domains.hooks import report as ur
from ndf.domains.hooks import report_text
from ndf.domains.hooks import tables
from ndf.domains.hooks.counter import SESSION_RE
from ndf.domains.hooks.verdict import Verdict, advise, passing, pushback

# 印を付ける起動の Skill（プラグインの前置きは `identity` から足す）
TARGET_SKILL = "development-workflow"
MARK_DIR_NAME = "unattended"
MARK_POLICY = "hook.unattended-mark"
PUSHBACK_POLICY = "hook.pushback"
FORM_REL = f"${{CLAUDE_PLUGIN_ROOT}}/contracts/{ur.FORM_TABLE}"
# 判定結果の `次のコマンド:` の行（前に `- ` を許す）。値が空か `無し` で始まればコマンドが無い
NEXT_COMMAND_RE = re.compile(r"^[ \t]*(?:-[ \t]*)?次のコマンド:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# `次のコマンド:` を打たずに区間を終えてよい結果の値（承認ゲート・区分の問いと、工程を通し切ったとき）
STOP_RESULTS = ("判断が要る", "完了")
# `次のコマンド:` を残して区間を終えてよい区分（人の操作待ちは契約で認めた停止）
HUMAN_ACTION = "ndf-ask:human-action"
ERR_NEXT_COMMAND = (
    "`次のコマンド:` にコマンドが載ったまま区間を終えようとしている。止まる理由（承認ゲート・区分のある問い・人の操作・"
    "続けられない理由）に当たらなければ、区間を終えずにそのコマンドを打って続ける。"
    "打って失敗したなら、失敗を理由に書き、最後の応答に `次のコマンド:` の行を残さない"
)
RULE_REF = "development-workflow の references/asking.md の「人のいない実行」"
GUIDE = (
    "NDF: これは人のいない実行（NDF_FACTORY_RUN）である。AskUserQuestion は使えず、応答を人が読まない。"
    "区分の無い問い（"
    f"{identity.FIXED_WORDS['declaration_dir']}/ をコミットするか・次のコマンドを流してよいか など）は人へ問わずに自分で決め、"
    '`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask.py" decide --question "<問い>" --answer "<答え>" --reason "<理由>"` で記録して続ける。'
    f"区間の終え方（承認ゲート・区分のある問い・終わり・続けられないとき）は {RULE_REF} に従い、"
    f"最後の応答の末尾に区間の報告（形は {FORM_REL}）を置く。"
)


def target_re() -> re.Pattern[str]:
    """conductor の起動の 1 行目（`/<前置き>development-workflow`。前に `/goal ` を許し、後ろは空白か行末）。"""
    skill = re.escape(f"/{identity.load().prefix}{TARGET_SKILL}")
    return re.compile(rf"^(?:/goal\s+)?{skill}(?:\s|$)")


def is_target(prompt: object) -> bool:
    """プロンプトの 1 行目が人のいない実行の conductor の起動か。"""
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    return bool(target_re().match(prompt.strip().splitlines()[0].strip()))


def mark_dir(state_dir: Path) -> Path:
    """印の置き場（`<状態の置き場>/hooks/unattended`）。"""
    return tables.hooks_dir(state_dir) / MARK_DIR_NAME


def mark_path(state_dir: Path, session_id: object) -> Optional[Path]:
    """セッションの識別子から印のパス。識別子が数え手と同じ形に合わなければ None。"""
    if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
        return None
    return mark_dir(state_dir) / f"{session_id}.json"


def write_mark(path: Path, session_id: str) -> None:
    """印（セッションの識別子と作った時刻）を原子的に書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"session_id": session_id, "started_at": started}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def prune_marks(directory: Path, now: float, max_age: float) -> int:
    """更新から `max_age` 秒を過ぎた印を消し、消した数を返す。"""
    removed = 0
    for path in directory.glob("*.json"):
        try:
            if now - path.stat().st_mtime > max_age:
                path.unlink()
                removed += 1
        except OSError:  # noqa: S112 ファイル: 別の起動が先に消した印は次の SessionStart で見る
            continue
    return removed


def pending_command_errors(text: object) -> list[str]:
    """`次のコマンド:` にコマンドが載ったまま、結果が `STOP_RESULTS` のどれでもなく区分が `HUMAN_ACTION` でもなければ誤りを 1 つ返す。"""
    if not isinstance(text, str):
        return []
    values = (v.strip("` \t") for v in NEXT_COMMAND_RE.findall(text))
    commands = [v for v in values if v and not v.startswith(ur.NONE_VALUE)]
    if not commands or ur.result_value(text) in STOP_RESULTS or ur.report_items(text).get("区分") == HUMAN_ACTION:
        return []
    return [ERR_NEXT_COMMAND]


def stop_errors(text: object, form: ur.ReportForm) -> list[str]:
    """区間の報告の形の誤りと、`次のコマンド:` を残した誤りを並べる。合えば空。"""
    return ur.report_errors(text, form) + pending_command_errors(text)


def pushback_reason(errors: list[str], remaining: int, form: ur.ReportForm) -> str:
    """差し戻しの文（誤りの一覧・契約のパス・残りの差し戻しの回数・形の説明）。"""
    lines = "\n".join(f"- {e}" for e in errors)
    return (
        "NDF: 人のいない実行では、最後の応答の末尾に区間の報告を置いて区間を終える。今の応答は形に合わない。\n"
        f"{lines}\n"
        f"形は {FORM_REL}、決まりは {RULE_REF} にある。下の形の説明に合わせて区間の報告を書き、応答を終え直す"
        f"（この後の差し戻しは残り {remaining} 回）。\n\n"
        f"{report_text.describe_form(form)}"
    )


def start_guide(form: Optional[ur.ReportForm]) -> str:
    """区間の始めの案内。契約を読めていれば `GUIDE` の後ろに形の説明を続ける。"""
    return GUIDE if form is None else f"{GUIDE}\n\n{report_text.describe_form(form)}"


def _on_session_start(ctx, svc) -> Optional[Verdict]:
    removed = prune_marks(mark_dir(svc.state_dir), time.time(), float(tables.policy_value(MARK_POLICY, "seconds")))
    return passing(unattended_pruned=removed) if removed else None


def _on_prompt(ctx, svc) -> Optional[Verdict]:
    if not is_target(ctx.payload.get("prompt")):
        return None
    path = mark_path(svc.state_dir, ctx.session_id)
    if path is None:
        return None
    write_mark(path, ctx.session_id)
    try:
        form: Optional[ur.ReportForm] = ur.load_form()
    except ur.FormError as exc:  # 契約を読めなくても案内は返す。読めない理由は計測に残す
        return Verdict("advise", context=start_guide(None), notes={"unattended_form_error": str(exc)[:200]})
    return advise(start_guide(form))


def _on_stop(ctx, svc) -> Optional[Verdict]:
    path = mark_path(svc.state_dir, ctx.session_id)
    if path is None or not path.is_file() or _is_busy(ctx):
        return None
    form = ur.load_form()
    errors = stop_errors(ctx.last_reply, form)
    if not errors:
        return None
    remaining = max(int(tables.policy_value(PUSHBACK_POLICY, "attempts")) - svc.counter.data["pushbacks"] - 1, 0)
    return pushback(pushback_reason(errors, remaining, form))


def _on_session_end(ctx, svc) -> Optional[Verdict]:
    path = mark_path(svc.state_dir, ctx.session_id)
    if path is not None:
        path.unlink(missing_ok=True)
    return None


def _is_busy(ctx) -> bool:
    """背景の作業か予定が残る Stop か。"""
    return bool(ctx.background_tasks) or bool(ctx.payload.get("session_crons"))


_HANDLERS = {
    "SessionStart": _on_session_start,
    "UserPromptSubmit": _on_prompt,
    "Stop": _on_stop,
    "SessionEnd": _on_session_end,
}


def check(ctx, svc) -> Optional[Verdict]:
    if not ctx.env.get("NDF_FACTORY_RUN"):
        return None
    handler = _HANDLERS.get(ctx.event)
    return handler(ctx, svc) if handler else None
