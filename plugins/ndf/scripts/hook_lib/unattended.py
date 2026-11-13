"""人のいない実行（`NDF_FACTORY_RUN`）の conductor の hook（#3242）。

    UserPromptSubmit（`hook.py unattended-start`）: プロンプトの 1 行目が `/ndf:development-workflow`（前に `/goal ` を許す）なら
        印を作り、人のいない実行の決まりの案内を返す。worker・レビューの `claude -p` には印を付けない
    Stop（`hook.py unattended-stop`）: 印のあるセッションで、最後の応答が区間の報告の形に合わないか、
        `次のコマンド:` にコマンドを載せたまま結果が `判断が要る` でも `完了` でもなければ差し戻す。
        背景の作業か予定が残る Stop は差し戻さない。形に合わない Stop が続く間の差し戻しは上限の宣言
        （`hook.unattended-pushback`）の回数までで、形に合った Stop で数え直す
    SessionEnd（`hook.py unattended-end`）: 印を消す。消し残した印は次の `unattended-start` が `hook.unattended-mark` の秒を過ぎたものから消す

`NDF_FACTORY_RUN` が空の実行では、登録の行（`hooks/claude.json`）の sh が Python を起動せずに終える。
形の正本は `contracts/forms/unattended-report.json` で、判定は `lib/unattended_report.py` が持つ。標準ライブラリだけを import する。
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import jsonio
import limits
import unattended_report as ur
from state_home import container_home

TARGET_RE = re.compile(r"^(?:/goal\s+)?/ndf:development-workflow(?:\s|$)")
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
MARK_DIR_NAME = "unattended"
PUSHBACK_POLICY = "hook.unattended-pushback"
MARK_POLICY = "hook.unattended-mark"
FORM_REL = "${CLAUDE_PLUGIN_ROOT}/contracts/forms/unattended-report.json"
# 判定結果の `次のコマンド:` の行（前に `- ` を許す）。値が空か `無し` で始まればコマンドが無い
NEXT_COMMAND_RE = re.compile(r"^[ \t]*(?:-[ \t]*)?次のコマンド:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# `次のコマンド:` を打たずに区間を終えてよい結果の値（承認ゲート・区分の問いと、工程を通し切ったとき）
STOP_RESULTS = ("判断が要る", "完了")
ERR_NEXT_COMMAND = (
    "`次のコマンド:` にコマンドが載ったまま区間を終えようとしている。止まる理由（承認ゲート・区分のある問い・人の操作・"
    "続けられない理由）に当たらなければ、区間を終えずにそのコマンドを打って続ける"
)
RULE_REF = "development-workflow の references/asking.md の「人のいない実行」"

GUIDE = (
    "NDF: これは人のいない実行（NDF_FACTORY_RUN）である。AskUserQuestion は使えず、応答を人が読まない。"
    "区分の無い問い（.ndf/ をコミットするか・次のコマンドを流してよいか など）は人へ問わずに自分で決め、"
    '`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask.py" decide --question "<問い>" --answer "<答え>" --reason "<理由>"` で記録して続ける。'
    f"区間の終え方（承認ゲート・区分のある問い・終わり・続けられないとき）は {RULE_REF} に従い、"
    f"最後の応答の末尾に区間の報告（形は {FORM_REL}）を置く。"
)


@dataclass(frozen=True)
class Mark:
    """人のいない実行の印（`<状態の根>/unattended/<session_id>.json`）。"""

    session_id: str
    started_at: str
    pushbacks: int


def is_target(prompt: object) -> bool:
    """プロンプトの 1 行目が人のいない実行の conductor の起動か。"""
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    first = prompt.strip().splitlines()[0].strip()
    return bool(TARGET_RE.match(first))


def mark_dir() -> Path:
    """印の置き場（`<状態の根>/unattended`）。"""
    return container_home() / MARK_DIR_NAME


def mark_path(session_id: object) -> Path | None:
    """セッションの識別子から印のファイルのパス。識別子が `SESSION_RE` に合わなければ None。"""
    if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
        return None
    return mark_dir() / f"{session_id}.json"


def read_mark(path: Path) -> Mark | None:
    """印を読む。無い・壊れている・形が違えば None。"""
    data = jsonio.read(path, missing=None, broken=None, want=dict)
    if data is None:
        return None
    sid, started, count = data.get("session_id"), data.get("started_at"), data.get("pushbacks")
    if not isinstance(sid, str) or not isinstance(started, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return Mark(session_id=sid, started_at=started, pushbacks=count)


def write_mark(path: Path, mark: Mark) -> None:
    """印を原子的に書く。書けなければ `OSError` を呼び出し元へ返す。"""
    jsonio.write_atomic(path, asdict(mark))


def prune_marks(directory: Path, now: float, max_age: float) -> int:
    """更新時刻から `max_age` 秒を過ぎた印を消し、消した数を返す。消せない印は飛ばす。"""
    removed = 0
    for f in directory.glob("*.json"):
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def next_count(errors: list[str], pushbacks: int, limit: int, busy: bool) -> tuple[bool, int]:
    """差し戻すかと、次の回数。"""
    if busy:
        return False, pushbacks
    if not errors:
        return False, 0
    if pushbacks < limit:
        return True, pushbacks + 1
    return False, pushbacks


def pending_command_errors(text: object) -> list[str]:
    """`次のコマンド:` にコマンドが載ったまま、結果が `STOP_RESULTS` のどれでもなければ誤りを 1 つ返す。"""
    if not isinstance(text, str):
        return []
    commands = [v for v in NEXT_COMMAND_RE.findall(text) if v and not v.startswith(ur.NONE_VALUE)]
    if not commands or ur.result_value(text) in STOP_RESULTS:
        return []
    return [ERR_NEXT_COMMAND]


def pushback_reason(errors: list[str], remaining: int) -> str:
    """差し戻しの文（誤りの一覧・契約のファイルのパス・残りの差し戻しの回数）。"""
    lines = "\n".join(f"- {e}" for e in errors)
    return (
        "NDF: 人のいない実行では、最後の応答の末尾に区間の報告を置いて区間を終える。今の応答は形に合わない。\n"
        f"{lines}\n"
        f"形は {FORM_REL}、決まりは {RULE_REF} にある。区間の報告を足して応答を終え直す"
        f"（この後の差し戻しは残り {remaining} 回）。"
    )


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _stderr_warn(command: str, text: str) -> None:
    print(f"ndf: {command}: {text}", file=sys.stderr)


def mark_session(hook_input: dict, now: float) -> str | None:
    """過ぎた印を消し、対象のセッションなら印（回数 0）を作って案内の文を返す。対象でなければ None。"""
    try:
        prune_marks(mark_dir(), now, limits.seconds(MARK_POLICY))
    except limits.LimitsError as e:
        _stderr_warn("unattended-start", str(e))
    if not isinstance(hook_input, dict) or not is_target(hook_input.get("prompt")):
        return None
    path = mark_path(hook_input.get("session_id"))
    if path is None:
        return None
    write_mark(path, Mark(session_id=path.stem, started_at=_utc_now(), pushbacks=0))
    return GUIDE


def review_stop(hook_input: dict) -> dict | None:
    """印のあるセッションの Stop で、差し戻すかを決めて回数を更新する。差し戻すなら `decision: block` の辞書。"""
    if not isinstance(hook_input, dict):
        return None
    path = mark_path(hook_input.get("session_id"))
    mark = read_mark(path) if path else None
    if mark is None:
        return None
    form = _read_report_form()
    if form is None:
        return None
    try:
        limit = int(limits.value(PUSHBACK_POLICY, "attempts"))
    except limits.LimitsError as e:
        _stderr_warn("unattended-stop", str(e))
        return None
    busy = bool(hook_input.get("background_tasks")) or bool(hook_input.get("session_crons"))
    text = hook_input.get("last_assistant_message")
    errors = ur.report_errors(text, form) + pending_command_errors(text)
    block, count = next_count(errors, mark.pushbacks, limit, busy)
    if count != mark.pushbacks:
        _save_count(path, mark, count)
    if not block:
        return None
    return {"decision": "block", "reason": pushback_reason(errors, limit - count)}


def _read_report_form() -> ur.ReportForm | None:
    """区間の報告の契約を読む。読めなければ警告して None。"""
    try:
        return ur.load_form()
    except ur.FormError as e:
        _stderr_warn("unattended-stop", f"区間の報告の契約を読めない: {e}")
        return None


def _save_count(path: Path, mark: Mark, count: int) -> None:
    """差し戻しの回数を印へ書く。書けなければ警告して続ける。"""
    try:
        write_mark(path, replace(mark, pushbacks=count))
    except OSError as e:
        _stderr_warn("unattended-stop", f"回数を書けない: {e.strerror or type(e).__name__}")


def clear_mark(hook_input: dict) -> None:
    """セッションの印を消す。無ければ何もしない。消せなければ `OSError` を呼び出し元へ返す。"""
    path = mark_path(hook_input.get("session_id")) if isinstance(hook_input, dict) else None
    if path is not None:
        path.unlink(missing_ok=True)
