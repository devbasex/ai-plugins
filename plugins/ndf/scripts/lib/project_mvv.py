"""project_mvv.py: プロジェクト MVV の宣言の読み取りと、判断の地点へ渡す MVV の節（#1366）。標準ライブラリだけで書く。

宣言は本文 `.ndf/mvv.md`（人が読む Markdown）と履歴 `.ndf/mvv.json`（版・sha256・承認の記録）の 2 ファイルで、書くのは
`project-mvv.py approve` だけである。ここは読むだけで、例外を上げない。

    mvv = load_mvv(root)        # status: approved / none / unapproved / mismatch / unreadable
    text = block(mvv)           # NDF の共通原則の本文全体 → プロジェクト MVV（か「MVV なし」）→ 判断の決まり
    ref = record(mvv)           # 状態ファイル・jsonl へ写す {status, version, sha256}
    items = basis(raw, mvv)     # 根拠の項目（Value 3 / C4 / P1 ...）。空なら「MVV なし」か「根拠なし」

NDF の共通原則は配布物の `scripts/data/ndf-common-principles.md` にあり、宣言へは写さない（プロジェクトが上書きも除外もできない）。
本文の解析は `project_mvv_body.py`、履歴の形と既定は `project_mvv_decl.py`、改訂の兆候は `project_mvv_signals.py` にある。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_mvv_body import OPERATIONS_HEADING, changes, item_ids, shape_problems  # noqa: E402,F401
from project_mvv_decl import BODY_FILE, DECL_VERSION, DEFAULTS, decl_problems, resolve_settings  # noqa: E402,F401
from project_mvv_body import _table_ids  # noqa: E402

HERE = Path(__file__).resolve().parent
PRINCIPLES_PATH = HERE.parent / "data" / "ndf-common-principles.md"
DECL_DIR = ".ndf"
DECL_FILE = "mvv.json"
STATUSES = ("approved", "none", "unapproved", "mismatch", "unreadable")
STATUS_LABEL = {
    "approved": "承認済み",
    "none": "プロジェクト MVV が無い",
    "unapproved": "未承認（.ndf/mvv.md だけがあり .ndf/mvv.json が無い）",
    "mismatch": "承認と一致しない（.ndf/mvv.md が承認の後に変わった）",
    "unreadable": "壊れている（.ndf/mvv.json を読めない）",
}
NO_MVV = "MVV なし"
NO_BASIS = "根拠なし"
VERDICT_LABEL = {  # MVV 判定の語（mvv-gate.py の助言の判定の表示）
    "follow": "従う",
    "not_follow": "反する疑い",
    "unknown": "判定できない",
    "machine": "機械のチェックで外れた",
    "unreadable": "判定を読めない",
}
SPRINT_PREFIX = "スプリント"  # スプリント MVV の項目の頭（#1400 の決定 15。R の番号には付けない）
ITEM_RE = re.compile(r"(?i)(?:(スプリント)\s*|\b)(mission|vision|value\s*\d+|[CPR]\d+)\b")
SPRINT_HINT = "この節の項目を根拠に書くときは頭に「スプリント」を付ける（例: スプリント Value 4）。R の番号はそのまま"

CONTRACT = """## 判断の決まり（NDF の共通原則）

- 上の NDF の共通原則を判断の基準として使う。プロジェクト MVV とスプリント MVV はその範囲で読み、優先順位は共通原則のとおりにする（下位は上位を上書きしない）
- 判断の結果として取れる行動は「進める」か「止めて人へ戻す（理由と根拠を添える）」の 2 つだけである。原則を理由に、指示と違う変更を独自に加えない。決められないときは人へ戻す
- 判断の範囲は目の前の変更・操作に限る
- 必ず人の承認が要る操作（C の番号・P の番号・R の番号）は、あなたの解釈で緩めない
- 根拠にした項目の番号（`Mission` / `Vision` / `Value 3` / `C4` / `P1` / `R2` など）を答えの根拠の欄に並べる。プロジェクト MVV が無いときは共通原則の番号（`C<番号>`）だけを使う"""


def mvv_state_base() -> Path:
    """記録（照合・覆し・判定）の置き場。`NDF_MVV_STATE_DIR` → `~/.local/state/ndf`。"""
    env = os.environ.get("NDF_MVV_STATE_DIR")
    return Path(env).expanduser() if env else Path("~/.local/state/ndf").expanduser()


def vet_log_path() -> Path:
    return mvv_state_base() / "project-mvv.jsonl"


def signals_log_path() -> Path:
    return mvv_state_base() / "project-mvv-signals.jsonl"


def gate_log_path() -> Path:
    return mvv_state_base() / "mvv-gate.jsonl"


def decl_paths(root) -> tuple[Path, Path]:
    d = Path(root) / DECL_DIR
    return d / BODY_FILE, d / DECL_FILE


def mvv_repo_key(root) -> str:
    """記録の `repo`。根の絶対パス。"""
    return str(Path(root).resolve())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_object(text: str) -> dict | None:
    """文の最初の { から最後の } までを JSON として読む。dict でなければ None。"""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        data = None
    return data if isinstance(data, dict) else None


def principles() -> str:
    """NDF の共通原則の本文全体。読めなければ上位の原則の 1 文だけでも先頭に置けるよう、理由つきの文を返す。"""
    try:
        return PRINCIPLES_PATH.read_text(encoding="utf-8").strip()
    except OSError as e:
        return f"# NDF の共通原則\n\n（配布物の {PRINCIPLES_PATH} を読めない: {e}）\n\n**人を守り、人の発展を支える。**"


def principle_ids() -> tuple[str, ...]:
    """共通原則の「必ず人の承認が要る操作」の番号（C1〜）。"""
    return tuple(f"{k}{n}" for k, n in _table_ids(principles()) if k == "C")


@dataclass
class ProjectMvv:
    """宣言の読み取りの結果。`status` が approved のときだけ `body` と `version` を持つ。"""

    status: str
    version: int | None = None
    sha256: str | None = None
    body: str | None = None
    error: str | None = None
    settings: dict = field(default_factory=dict)
    approved_at: str | None = None
    versions: list = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    def item_ids(self) -> list[str]:
        return item_ids(self.body or "") if self.approved else []

    def setting(self, key: str):
        return resolve_settings(self.settings)[key]


def load_mvv(root) -> ProjectMvv:
    """宣言を読む。例外を上げない。"""
    body_path, decl_path = decl_paths(root)
    try:
        has_body, has_decl = body_path.is_file(), decl_path.is_file()
        if not has_body and not has_decl:
            return ProjectMvv("none")
        if not has_decl:
            return ProjectMvv("unapproved", error=f"{decl_path} が無い（本文だけで、承認の記録が無い）")
        data = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return ProjectMvv("unreadable", error=f"{decl_path} を読めない: {e}")
    errs = decl_problems(data)
    if errs:
        return ProjectMvv("unreadable", error=f"{decl_path} の形が違う: " + "／".join(errs))
    body_path = decl_path.parent / data["body"]
    settings = data.get("settings") or {}
    last = data["versions"][-1]
    try:
        body = body_path.read_text(encoding="utf-8")
    except OSError as e:
        return ProjectMvv(
            "mismatch",
            version=last["version"],
            sha256=last["sha256"],
            error=f"{body_path} を読めない: {e}",
            settings=settings,
            versions=data["versions"],
        )
    now = sha256_text(body)
    if now != last["sha256"]:
        return ProjectMvv(
            "mismatch",
            version=last["version"],
            sha256=now,
            settings=settings,
            versions=data["versions"],
            error=f"{body_path} の sha256 が版 {last['version']} の承認と一致しない",
        )
    return ProjectMvv(
        "approved",
        version=last["version"],
        sha256=now,
        body=body,
        settings=settings,
        approved_at=last["approved_at"],
        versions=data["versions"],
    )


def _git_show(root, ref: str, rel: str) -> bytes | None:
    """`ref` の `rel` を返す。ファイルが無ければ None。ref が引けなければ OSError。"""
    cp = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{ref}:{rel}"], capture_output=True)
    if cp.returncode != 0:
        return None
    cp = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{rel}"], capture_output=True)
    if cp.returncode != 0:
        raise OSError(f"git show {ref}:{rel} が失敗した")
    out = cp.stdout
    return out if isinstance(out, bytes) else str(out or "").encode("utf-8")


def load_mvv_at_ref(root, refs: list[str]) -> ProjectMvv:
    """git の `refs` のうち最初に引けるものの宣言を読む。例外を上げない（#1366）。

    PR のレビューでは、PR の head が MVV を足す・書き換えると、その PR 自身の判断の基準を
    変更者が決められてしまう。そのため base の側で承認済みの宣言を読む。どの ref も引けなければ
    `unreadable`（MVV なしで続ける）。
    """
    for ref in refs:
        cp = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], capture_output=True)
        if cp.returncode == 0:
            break
    else:
        return ProjectMvv("unreadable", error=f"base の ref（{' / '.join(refs) or 'なし'}）を引けない")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / DECL_DIR
            d.mkdir()
            decl = _git_show(root, ref, f"{DECL_DIR}/{DECL_FILE}")
            name = BODY_FILE
            if decl is not None:
                (d / DECL_FILE).write_bytes(decl)
                try:
                    named = json.loads(decl).get("body")
                except (ValueError, AttributeError):
                    named = None
                if isinstance(named, str) and named and "/" not in named and "\\" not in named and named != "..":
                    name = named
            body = _git_show(root, ref, f"{DECL_DIR}/{name}")
            if body is not None:
                (d / name).write_bytes(body)
            mvv = load_mvv(tmp)
    except OSError as e:
        return ProjectMvv("unreadable", error=f"{ref} の宣言を読めない: {e}")
    if mvv.error:
        mvv.error = mvv.error.replace(str(Path(tmp)), ref)
    return mvv


def read_settings(root) -> tuple[dict, str | None]:
    """(既定へ重ねた settings, 壊れているときの理由)。宣言が無ければ既定。"""
    _, decl_path = decl_paths(root)
    if not decl_path.is_file():
        return resolve_settings({}), None
    try:
        data = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return resolve_settings({}), f"{decl_path} を読めない: {e}"
    errs = decl_problems(data)
    if errs:
        return resolve_settings({}), f"{decl_path} の形が違う: " + "／".join(errs)
    return resolve_settings(data.get("settings")), None


def record(mvv: ProjectMvv) -> dict:
    """状態への写し。本文を写さず、参照だけを持つ。"""
    return {"status": mvv.status, "version": mvv.version, "sha256": mvv.sha256}


def contract() -> str:
    """判断を求めるプロンプトの固定の指示（I16）。どの地点も同じ文面を使う。"""
    return CONTRACT


def project_part(mvv: ProjectMvv) -> str:
    if mvv.approved:
        return f"# プロジェクト MVV（版 {mvv.version}・承認済み）\n\n{(mvv.body or '').strip()}"
    reason = STATUS_LABEL.get(mvv.status, mvv.status) + (f"。{mvv.error}" if mvv.error else "")
    return f"# プロジェクト MVV\n\n{NO_MVV}（{reason}）。NDF の共通原則だけを基準にする。"


def block(mvv: ProjectMvv, sprint: str | None = None) -> str:
    """MVV の節（I5）。共通原則の本文全体 → プロジェクト MVV（か「MVV なし」）→ スプリント MVV → 判断の決まり。

    スプリント MVV の節の見出しには本文の sha256 の先頭 8 文字を、直後には項目の書き方を置く（#1400 の決定 15）。"""
    parts = [principles(), project_part(mvv)]
    if sprint:
        parts.append(f"# スプリント MVV（sha256 {sha256_text(sprint)[:8]}）\n\n{SPRINT_HINT}\n\n{sprint.strip()}")
    parts.append(contract())
    return "\n\n".join(parts) + "\n"


def allowed_ids(mvv: ProjectMvv, extra=(), sprint: str | None = None) -> set[str]:
    """根拠に書いてよい番号。スプリント MVV の本文を渡すと、その項目（頭に「スプリント」）と R の番号も許す。"""
    own = {f"{SPRINT_PREFIX} {i}" for i in item_ids(sprint)} | set(re.findall(r"\bR\d+\b", sprint)) if sprint else set()
    return {*principle_ids(), *mvv.item_ids(), *extra, *own}


def _norm_id(s: str, prefix: str | None = None) -> str:
    s = s.strip()
    low = s.lower()
    if low in ("mission", "vision"):
        item = low.title()
    else:
        m = re.match(r"(?i)value\s*(\d+)$", s)
        item = f"Value {int(m.group(1))}" if m else s[0].upper() + str(int(s[1:]))
    # R の番号はスプリント MVV だけが持つので頭を付けない。C・P はプロジェクト MVV と共通原則の番号
    return f"{SPRINT_PREFIX} {item}" if prefix and item[0] not in "CPR" else item


def basis(raw, mvv: ProjectMvv, extra=(), sprint: str | None = None) -> list[str]:
    """根拠の項目の正規化（I7）。本文・共通原則に無い番号は落とす。空なら「MVV なし」か「根拠なし」。

    スプリント MVV の項目は頭に「スプリント」を付けたまま、プロジェクト MVV の同じ番号と別の項目として残す（#1400 の I12）。
    `sprint` にスプリント MVV の本文を渡すと、その項目を許す。"""
    values = raw if isinstance(raw, (list, tuple)) else [raw] if raw else []
    # 状態に写した参照から組み直した MVV（本文を持たない）は、番号の形だけを見る
    allowed = None if (mvv.approved and mvv.body is None) else allowed_ids(mvv, extra, sprint)
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        for m in ITEM_RE.finditer(v):
            item = _norm_id(m.group(2), m.group(1))
            if (allowed is None or item in allowed) and item not in out:
                out.append(item)
    if out:
        return out
    return [NO_BASIS] if mvv.approved else [NO_MVV]


def _item_order(item: str) -> int:
    """根拠の句の並び: プロジェクト MVV と共通原則 → スプリント MVV → R の番号。"""
    return 1 if item.startswith(SPRINT_PREFIX) else 2 if re.fullmatch(r"R\d+", item) else 0


def basis_phrase(items, mvv: ProjectMvv, sprint_sha: str | None = None) -> str:
    """見送りの返信と設計の決定の根拠の句。例: 「根拠: Value 1（MVV 版 1）」「（MVV なし）」。

    スプリント MVV の sha256 を渡すと、括弧にその先頭 8 文字を足す（「根拠: Value 6 / スプリント Value 4（MVV 版 1・スプリント MVV 3f9a1c2e）」）。"""
    items = sorted((x for x in (items or []) if x not in (NO_MVV, NO_BASIS)), key=_item_order)
    if not mvv.approved:
        return f"根拠: {' / '.join(items)}（{NO_MVV}）" if items else f"（{NO_MVV}）"
    tail = f"・スプリント MVV {sprint_sha[:8]}" if sprint_sha else ""
    return f"根拠: {' / '.join(items) if items else NO_BASIS}（MVV 版 {mvv.version}{tail}）"


def verdict_reason(verdict: dict) -> str:
    """越えない線に当たればその線を、当たらなければ判定の語を返す。"""
    boundary = verdict["boundary"]
    return "越えない線に当たる: " + " / ".join(map(str, boundary)) if boundary else verdict["verdict"]


def from_record(ref) -> ProjectMvv:
    """状態に写した参照から、本文を持たない `ProjectMvv` を組み直す（返信の句に使う）。"""
    if not isinstance(ref, dict) or ref.get("status") not in STATUSES:
        return ProjectMvv("none")
    return ProjectMvv(ref["status"], version=ref.get("version"), sha256=ref.get("sha256"))


def sprint_mvv_refusal(state: dict, advise: bool = False) -> str | None:
    """スプリント MVV の 3 者（ファイル・状態・承認の記録）の一致。外れた理由を返す。

    `advise`（助言の MVV 判定。#1400 の決定 14）では承認の記録（承認ゲート `MVV`）を求めず、ファイルと状態の一致だけを見る。"""
    mvv = state.get("mvv") or {}
    if not mvv.get("path") or not mvv.get("sha256"):
        return "スプリントの状態に MVV が無い（sprint-state.py init --milestone M で写す）"
    path = Path(mvv["path"])
    if not path.is_file():
        return f"MVV のファイルが無い: {path}"
    now = hashlib.sha256(path.read_bytes()).hexdigest()
    if advise:
        return None if now == mvv["sha256"] else "スプリント MVV が状態と一致しない（init の後に写しが変わった）"
    approval = next((g for g in state.get("gates") or [] if g.get("name") == "MVV"), None)
    if not approval or not approval.get("sha256"):
        return "MVV の承認の記録が無い（利用者の承認を得てから sprint-state.py gate <状態> MVV を打つ）"
    if not (now == mvv["sha256"] == approval["sha256"]):
        return "MVV のハッシュが承認の記録と一致しない（承認の後に MVV が変わった）"
    return None


def sprint_text_of(state: dict) -> tuple[str | None, str | None]:
    """状態の `mvv` からスプリント MVV の本文を読む（#1400 の I10）。(本文, 特定できなかった理由)。

    状態に `mvv` が無ければ (None, None)。ファイルが無い・状態の sha256 と一致しなければ (None, 理由)。例外を上げない。"""
    ref = state.get("mvv") if isinstance(state, dict) else None
    if not isinstance(ref, dict) or not ref.get("path"):
        return None, None
    why = sprint_mvv_refusal(state, advise=True)
    if why:
        return None, why
    try:
        return Path(ref["path"]).read_text(encoding="utf-8"), None
    except OSError as e:
        return None, f"MVV のファイルを読めない: {e}"


def sprint_source(sprint: str | None, mvv: str | None, milestone: str | None, repo: str | None) -> tuple[str | None, dict | None]:
    """`project-mvv.py context` のスプリント MVV の (本文, 結果の `sprint_mvv`)。出所を渡さなければ (None, None)。読めなくても止めない（#1400 の決定 12）。"""
    try:
        if sprint:
            text, why = sprint_text_of(json.loads(Path(sprint).read_text(encoding="utf-8")))
            if text is None and why is None:
                why = f"状態に mvv が無い: {sprint}"
            source = f"state:{sprint}"
        elif mvv:
            text, why, source = Path(mvv).read_text(encoding="utf-8"), None, f"file:{mvv}"
        elif milestone:
            import deps

            deps.require("md")
            import sprint_mvv as mm

            text = mm.mvv_sections(mm.milestone_description(milestone, repo))
            why = None if text else f"マイルストーン {milestone} の説明に ## Mission / ## Vision / ## Value の見出しがそろっていない"
            source = f"milestone:{milestone}"
        else:
            return None, None
    except (OSError, ValueError) as e:
        return None, {"reason": f"スプリント MVV を読めない: {e}"}
    if text is None:
        return None, {"reason": why}
    return text, {"sha256": sha256_text(text), "source": source}


def approval_refusal(state: dict, root, mvv: ProjectMvv | None = None, advise: bool = False) -> str | None:
    """MVV 判定の前の照合（I11・決定 12）。スプリント MVV があれば 3 者の一致、無ければ承認済みのプロジェクト MVV。
    どちらでも、状態に残したプロジェクト MVV の参照が今の宣言と食い違えば断る。`advise` は `sprint_mvv_refusal` へ渡す。"""
    mvv = mvv or load_mvv(root)
    if mvv.status in ("unapproved", "mismatch", "unreadable"):
        return f"プロジェクト MVV が{STATUS_LABEL[mvv.status]}（{mvv.error or ''}）。利用者の承認へ戻す"
    ref = state.get("project_mvv") or {}
    if ref.get("sha256") and (not mvv.approved or ref["sha256"] != mvv.sha256):
        now = f"版 {mvv.version}" if mvv.approved else STATUS_LABEL[mvv.status]
        return f"プロジェクト MVV が改訂された（状態は版 {ref.get('version')}、今は {now}）。利用者の承認へ戻す"
    if (state.get("mvv") or {}).get("path"):
        return sprint_mvv_refusal(state, advise)
    if mvv.approved:
        if not ref.get("sha256"):
            return "スプリントの状態にプロジェクト MVV の参照が無い（sprint-state.py init で書く）"
        return None
    return sprint_mvv_refusal(state, advise)
