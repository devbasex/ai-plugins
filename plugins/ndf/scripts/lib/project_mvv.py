"""project_mvv.py: プロジェクト MVV の宣言の読み取りと、判断の地点へ渡す MVV の節（#1366）。標準ライブラリだけで書く。

宣言は本文 `.ndf/mvv.md`（人が読む Markdown）と履歴 `.ndf/mvv.json`（版・sha256・承認の記録）の 2 ファイルで、書くのは
`project-mvv.py approve` だけである。ここは読むだけで、例外を上げない。

    mvv = load(root)            # status: approved / none / unapproved / mismatch / unreadable
    text = block(mvv)           # NDF の共通原則の本文全体 → プロジェクト MVV（か「MVV なし」）→ 判断の決まり
    ref = record(mvv)           # 状態ファイル・jsonl へ写す {status, version, sha256}
    items = basis(raw, mvv)     # 根拠の項目（Value 3 / C4 / P1 ...）。空なら「MVV なし」か「根拠なし」

NDF の共通原則は配布物の `scripts/data/ndf-common-principles.md` にあり、宣言へは写さない（プロジェクトが上書きも除外もできない）。
宣言の JSON Schema の型（`lib/schema.py` の `Shape`）は `decl_models()` が pydantic を読んでから組む。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRINCIPLES_PATH = HERE.parent / "data" / "ndf-common-principles.md"
DECL_DIR = ".ndf"
BODY_FILE = "mvv.md"
DECL_FILE = "mvv.json"
DECL_VERSION = 1

# 汎用の既定（決定 7・8）。宣言の settings と引数で変える。特定のリポジトリの値を置かない（I12）
DEFAULTS: dict = {
    "trend_commits": 100,
    "trend_issues": 20,
    "unknown_streak": 3,
    "revise_after": {"overrides": 3, "unknowns": 5, "escapes": 3},
}

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

# 本文の見出し（I4）。Mission / Vision / Value は必須、固有の操作の見出しは任意
REQUIRED_SECTIONS = ("Mission", "Vision", "Value")
OPERATIONS_HEADING = "必ず人の承認が要る操作"
# 共通原則の写しに当たる見出し（本文に持てない）
PRINCIPLE_HEADINGS = ("上位の原則", "優先順位")

ITEM_RE = re.compile(r"(?i)\b(mission|vision|value\s*\d+|[CPR]\d+)\b")
TABLE_ID_RE = re.compile(r"^\|\s*([CPR])(\d+)\s*\|")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

CONTRACT = """## 判断の決まり（NDF の共通原則）

- 上の NDF の共通原則を判断の基準として使う。プロジェクト MVV とミッション MVV はその範囲で読み、優先順位は共通原則のとおりにする（下位は上位を上書きしない）
- 判断の結果として取れる行動は「進める」か「止めて人へ戻す（理由と根拠を添える）」の 2 つだけである。原則を理由に、指示と違う変更を独自に加えない。決められないときは人へ戻す
- 判断の範囲は目の前の変更・操作に限る
- 必ず人の承認が要る操作（C の番号・P の番号・R の番号）は、あなたの解釈で緩めない
- 根拠にした項目の番号（`Mission` / `Vision` / `Value 3` / `C4` / `P1` / `R2` など）を答えの根拠の欄に並べる。プロジェクト MVV が無いときは共通原則の番号（`C<番号>`）だけを使う"""


# ---------------------------------------------------------------- 置き場


def state_base() -> Path:
    """記録（照合・覆し・判定）の置き場。`NDF_MVV_STATE_DIR` → `~/.local/state/ndf`。"""
    env = os.environ.get("NDF_MVV_STATE_DIR")
    return Path(env).expanduser() if env else Path("~/.local/state/ndf").expanduser()


def vet_log_path() -> Path:
    return state_base() / "project-mvv.jsonl"


def signals_log_path() -> Path:
    return state_base() / "project-mvv-signals.jsonl"


def gate_log_path() -> Path:
    return state_base() / "mvv-gate.jsonl"


def decl_paths(root) -> tuple[Path, Path]:
    d = Path(root) / DECL_DIR
    return d / BODY_FILE, d / DECL_FILE


def repo_key(root) -> str:
    """記録の `repo`。根の絶対パス。"""
    return str(Path(root).resolve())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def principles() -> str:
    """NDF の共通原則の本文全体。読めなければ上位の原則の 1 文だけでも先頭に置けるよう、理由つきの文を返す。"""
    try:
        return PRINCIPLES_PATH.read_text(encoding="utf-8").strip()
    except OSError as e:
        return f"# NDF の共通原則\n\n（配布物の {PRINCIPLES_PATH} を読めない: {e}）\n\n**人類を守り、発展させる。**"


def principle_ids() -> tuple[str, ...]:
    """共通原則の「必ず人の承認が要る操作」の番号（C1〜）。"""
    return tuple(f"{k}{n}" for k, n in _table_ids(principles()) if k == "C")


# ---------------------------------------------------------------- 本文の解析


def _lines_outside_fences(text: str) -> list[tuple[int, str]]:
    out, fenced = [], False
    for i, ln in enumerate(text.splitlines()):
        if FENCE_RE.match(ln):
            fenced = not fenced
            continue
        if not fenced:
            out.append((i, ln))
    return out


def sections(text: str) -> dict[str, list[str]]:
    """`## ` の見出しの語 → その節の行（見出しは含まない）。見出しの語は括弧の前まで。"""
    found: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for _, ln in _lines_outside_fences(text):
        if ln.startswith("## "):
            title = re.split(r"[（(]", ln[3:].strip(), maxsplit=1)[0].strip()
            cur = found.setdefault(title, [])
            continue
        if ln.startswith("# "):
            cur = None
            continue
        if cur is not None:
            cur.append(ln)
    return found


def _table_ids(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), int(m.group(2))) for _, ln in _lines_outside_fences(text) if (m := TABLE_ID_RE.match(ln.strip()))]


def _section(secs: dict[str, list[str]], word: str) -> list[str] | None:
    return next((v for k, v in secs.items() if k == word or k.startswith(word)), None)


def item_ids(text: str) -> list[str]:
    """本文の項目の番号。Mission / Vision と、Value の番号つきの箇条（Value N）と、固有の操作の表の P の番号。"""
    secs = sections(text)
    ids = [k for k in ("Mission", "Vision") if _section(secs, k) is not None]
    for ln in _section(secs, "Value") or []:
        m = re.match(r"^(\d+)\.\s", ln)
        if m:
            ids.append(f"Value {int(m.group(1))}")
    ops = _section(secs, OPERATIONS_HEADING)
    if ops is not None:
        ids += [f"P{n}" for k, n in _table_ids("\n".join(ops)) if k == "P"]
    return ids


def shape_problems(text: str) -> list[dict]:
    """本文の形の誤り（I4）。`[{"item", "reason"}]`。空なら形は正しい。"""
    secs = sections(text)
    out = []
    for word in REQUIRED_SECTIONS:
        if _section(secs, word) is None:
            out.append({"item": word, "reason": f"`## {word}` の見出しが無い"})
    value = _section(secs, "Value")
    if value is not None and not any(re.match(r"^\d+\.\s", ln) for ln in value):
        out.append({"item": "Value", "reason": "`## Value` に番号つきの箇条（`1. ...`）が無い"})
    for word in PRINCIPLE_HEADINGS:
        if any(k.startswith(word) for k in secs):
            out.append(
                {"item": "priority" if word == "優先順位" else "principle", "reason": f"共通原則の写し（`## {word}` の節）を持てない"}
            )
    for k, n in _table_ids(text):
        if k == "C":
            out.append({"item": f"C{n}", "reason": f"共通原則の操作（C{n} の行）を本文へ写せない。固有の操作は P の番号で書く"})
        if k == "R":
            out.append({"item": f"R{n}", "reason": "R の番号はミッション MVV のものである。固有の操作は P の番号で書く"})
    ops = _section(secs, OPERATIONS_HEADING)
    if ops is not None:
        others = [f"{k}{n}" for k, n in _table_ids("\n".join(ops)) if k != "P"]
        if not any(k == "P" for k, _ in _table_ids("\n".join(ops))) and not others:
            out.append({"item": OPERATIONS_HEADING, "reason": f"`## {OPERATIONS_HEADING}` に `| P1 | ... |` の行が無い"})
    return out


# ---------------------------------------------------------------- 読み取り


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


def resolve_settings(settings: dict | None) -> dict:
    """宣言の settings を既定へ重ねる。"""
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (settings or {}).items():
        if k == "revise_after" and isinstance(v, dict):
            out["revise_after"].update({kk: vv for kk, vv in v.items() if vv is not None})
        elif v is not None:
            out[k] = v
    return out


def decl_problems(data) -> list[str]:
    """`.ndf/mvv.json` の形の誤り（`decl_models()` の型と同じ規則を標準ライブラリで見る）。"""
    if not isinstance(data, dict):
        return ["オブジェクトでない"]
    errs = []
    allowed = {"version", "body", "settings", "versions"}
    errs += [f"{k}: 知らない項目" for k in sorted(set(data) - allowed)]
    if data.get("version") != DECL_VERSION:
        errs.append(f"version: {DECL_VERSION} にする")
    if not isinstance(data.get("body"), str) or not data.get("body"):
        errs.append("body: 文字列にする")
    st = data.get("settings", {})
    if not isinstance(st, dict):
        errs.append("settings: オブジェクトにする")
    else:
        errs += [f"settings.{k}: 知らない項目" for k in sorted(set(st) - set(DEFAULTS))]
        for k in ("trend_commits", "trend_issues", "unknown_streak"):
            if k in st and st[k] is not None and (not isinstance(st[k], int) or isinstance(st[k], bool) or st[k] < 0):
                errs.append(f"settings.{k}: 0 以上の整数にする")
        ra = st.get("revise_after")
        if ra is not None:
            if not isinstance(ra, dict):
                errs.append("settings.revise_after: オブジェクトにする")
            else:
                errs += [f"settings.revise_after.{k}: 知らない項目" for k in sorted(set(ra) - set(DEFAULTS["revise_after"]))]
                for k, v in ra.items():
                    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                        errs.append(f"settings.revise_after.{k}: 1 以上の整数にする")
    vs = data.get("versions")
    if not isinstance(vs, list) or not vs:
        errs.append("versions: 1 件以上の配列にする")
        return errs
    keys = {"version", "sha256", "approved_at", "approved_by", "reason", "vet", "changes", "body"}
    for i, v in enumerate(vs):
        w = f"versions[{i}]"
        if not isinstance(v, dict):
            errs.append(f"{w}: オブジェクトにする")
            continue
        errs += [f"{w}.{k}: 知らない項目" for k in sorted(set(v) - keys)]
        errs += [f"{w}.{k}: 必須の項目が無い" for k in sorted(keys - set(v) - {"reason"})]
        if v.get("version") != i + 1:
            errs.append(f"{w}.version: {i + 1} にする（版は 1 から 1 ずつ上がる）")
        for k in ("sha256", "approved_at", "approved_by", "body"):
            if k in v and (not isinstance(v[k], str) or not v[k]):
                errs.append(f"{w}.{k}: 空でない文字列にする")
        if i > 0 and not (isinstance(v.get("reason"), str) and v["reason"].strip()):
            errs.append(f"{w}.reason: 版 2 以降は改訂の理由が要る")
        if "vet" in v and not (isinstance(v["vet"], dict) and v["vet"].get("verdict") in ("follow", "unknown")):
            errs.append(f"{w}.vet: 承認の前の照合（verdict が follow か unknown）にする")
        if "changes" in v and not isinstance(v["changes"], list):
            errs.append(f"{w}.changes: 配列にする")
    return errs


def load(root) -> ProjectMvv:
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


# ---------------------------------------------------------------- 判断の地点へ渡すもの


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


def block(mvv: ProjectMvv, mission: str | None = None) -> str:
    """MVV の節（I5）。共通原則の本文全体 → プロジェクト MVV（か「MVV なし」）→ ミッション MVV → 判断の決まり。"""
    parts = [principles(), project_part(mvv)]
    if mission:
        parts.append(f"# ミッション MVV\n\n{mission.strip()}")
    parts.append(contract())
    return "\n\n".join(parts) + "\n"


def allowed_ids(mvv: ProjectMvv, extra=()) -> set[str]:
    return {*principle_ids(), *mvv.item_ids(), *extra}


def _norm_id(s: str) -> str:
    s = s.strip()
    low = s.lower()
    if low in ("mission", "vision"):
        return low.title()
    m = re.match(r"(?i)value\s*(\d+)$", s)
    if m:
        return f"Value {int(m.group(1))}"
    return s[0].upper() + str(int(s[1:]))


def basis(raw, mvv: ProjectMvv, extra=()) -> list[str]:
    """根拠の項目の正規化（I7）。本文・共通原則に無い番号は落とす。空なら「MVV なし」か「根拠なし」。"""
    values = raw if isinstance(raw, (list, tuple)) else [raw] if raw else []
    # 状態に写した参照から組み直した MVV（本文を持たない）は、番号の形だけを見る
    allowed = None if (mvv.approved and mvv.body is None) else allowed_ids(mvv, extra)
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        for m in ITEM_RE.finditer(v):
            item = _norm_id(m.group(1))
            if (allowed is None or item in allowed) and item not in out:
                out.append(item)
    if out:
        return out
    return [NO_BASIS] if mvv.approved else [NO_MVV]


def basis_phrase(items, mvv: ProjectMvv) -> str:
    """見送りの返信の末尾の句。例: 「根拠: Value 1（MVV 版 1）」「（MVV なし）」。"""
    items = [x for x in (items or []) if x not in (NO_MVV, NO_BASIS)]
    if not mvv.approved:
        return f"根拠: {' / '.join(items)}（{NO_MVV}）" if items else f"（{NO_MVV}）"
    return f"根拠: {' / '.join(items) if items else NO_BASIS}（MVV 版 {mvv.version}）"


def from_record(ref) -> ProjectMvv:
    """状態に写した参照から、本文を持たない `ProjectMvv` を組み直す（返信の句に使う）。"""
    if not isinstance(ref, dict) or ref.get("status") not in STATUSES:
        return ProjectMvv("none")
    return ProjectMvv(ref["status"], version=ref.get("version"), sha256=ref.get("sha256"))


# ---------------------------------------------------------------- fast の許可


def mission_mvv_refusal(state: dict) -> str | None:
    """ミッション MVV の 3 者（ファイル・状態・承認の記録）の一致。外れた理由を返す。"""
    mvv = state.get("mvv") or {}
    if not mvv.get("path") or not mvv.get("sha256"):
        return "ミッションの状態に MVV が無い（mission-state.py init --pace fast --milestone M で写す）"
    path = Path(mvv["path"])
    if not path.is_file():
        return f"MVV のファイルが無い: {path}"
    approval = next((g for g in state.get("gates") or [] if g.get("name") == "MVV"), None)
    if not approval or not approval.get("sha256"):
        return "MVV の承認の記録が無い（利用者の承認を得てから mission-state.py gate <状態> MVV を打つ）"
    now = hashlib.sha256(path.read_bytes()).hexdigest()
    if not (now == mvv["sha256"] == approval["sha256"]):
        return "MVV のハッシュが承認の記録と一致しない（承認の後に MVV が変わった）"
    return None


def approval_refusal(state: dict, root, mvv: ProjectMvv | None = None) -> str | None:
    """fast の許可の照合（I11・決定 12）。ミッション MVV があれば 3 者の一致、無ければ承認済みのプロジェクト MVV。
    どちらでも、状態に残したプロジェクト MVV の参照が今の宣言と食い違えば断る。"""
    mvv = mvv or load(root)
    if mvv.status in ("unapproved", "mismatch", "unreadable"):
        return f"プロジェクト MVV が{STATUS_LABEL[mvv.status]}（{mvv.error or ''}）。利用者の承認へ戻す"
    ref = state.get("project_mvv") or {}
    if ref.get("sha256") and (not mvv.approved or ref["sha256"] != mvv.sha256):
        now = f"版 {mvv.version}" if mvv.approved else STATUS_LABEL[mvv.status]
        return f"プロジェクト MVV が改訂された（状態は版 {ref.get('version')}、今は {now}）。利用者の承認へ戻す"
    if (state.get("mvv") or {}).get("path"):
        return mission_mvv_refusal(state)
    if mvv.approved:
        if not ref.get("sha256"):
            return "ミッションの状態にプロジェクト MVV の参照が無い（mission-state.py init --pace fast で書く）"
        return None
    return mission_mvv_refusal(state)


# ---------------------------------------------------------------- 改訂の兆候


def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def append_jsonl(path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_at(s) -> datetime.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _after(row: dict, since: datetime.datetime | None) -> bool:
    at = _parse_at(row.get("at"))
    return since is None or (at is not None and at > since)


def unknown_streak(gate_rows: list[dict], sha: str | None) -> int:
    """同じプロジェクト MVV の sha256 のもとで、末尾から続いた「判定できない」の回数（I13）。"""
    n = 0
    for r in reversed(gate_rows):
        ref = r.get("project_mvv") or {}
        if ref.get("sha256") != sha:
            break
        if r.get("verdict") in ("machine",):
            continue  # 機械の検査で戻したものは判定ではない
        if r.get("verdict") != "unknown":
            break
        n += 1
    return n


def signals(root, mvv: ProjectMvv, *, signals_log=None, gate_log=None, escapes: list[dict] | None = None) -> dict:
    """改訂の兆候の集計（I18）。現行の版の sha256 と承認の日時より後の記録だけを数え、閾値と比べる。"""
    st = resolve_settings(mvv.settings)
    th = st["revise_after"]
    key = repo_key(root)
    since = _parse_at(mvv.approved_at) if mvv.approved else None
    sha = mvv.sha256 if mvv.approved else None
    sig = [r for r in read_jsonl(signals_log or signals_log_path()) if r.get("repo") == key and _after(r, since)]
    if mvv.approved:
        sig = [r for r in sig if r.get("project_sha256") == sha]
    gate_rows = [
        r for r in read_jsonl(gate_log or gate_log_path()) if (r.get("project_mvv") or {}).get("sha256") == sha and _after(r, since)
    ]
    if mvv.approved:
        gate_rows = [r for r in gate_rows if r.get("repo") in (None, key)]
    esc = [e for e in (escapes or []) if e.get("kind") == "escape" and _after(e, since)]
    counts = {
        "overrides": len(sig),
        "override_reject": sum(1 for r in sig if r.get("kind") == "override_reject"),
        "override_pass": sum(1 for r in sig if r.get("kind") == "override_pass"),
        "unknowns": sum(1 for r in gate_rows if r.get("verdict") == "unknown"),
        "escapes": len(esc),
        "unknown_streak": unknown_streak(gate_rows, sha),
    }
    over = [k for k in ("overrides", "unknowns", "escapes") if counts[k] >= th[k]]
    if counts["unknown_streak"] >= st["unknown_streak"]:
        over.append("unknown_streak")
    return {"counts": counts, "thresholds": {**th, "unknown_streak": st["unknown_streak"]}, "over": over, "version": mvv.version}


def revise_suggestion(sig: dict) -> dict | None:
    """閾値を超えていれば改訂の提案の 1 件（`items` に載せる）。"""
    if not sig["over"]:
        return None
    parts = [f"{k} {sig['counts'][k]} 件（閾値 {sig['thresholds'][k]}）" for k in sig["over"]]
    return {
        "kind": "revise",
        "result": "suggest",
        "name": "MVV の改訂を提案する",
        "reason": "現行の版のもとで " + "・".join(parts) + " に届いた。`references/project-mvv.md` の改訂の手順へ入る",
    }


# ---------------------------------------------------------------- 版の差分


def item_texts(text: str) -> dict[str, str]:
    """項目の番号 → その項目の文（差分の計算に使う）。"""
    secs = sections(text)
    out = {}
    for k in ("Mission", "Vision"):
        s = _section(secs, k)
        if s is not None:
            out[k] = "\n".join(s).strip()
    cur = None
    for ln in _section(secs, "Value") or []:
        m = re.match(r"^(\d+)\.\s", ln)
        if m:
            cur = f"Value {int(m.group(1))}"
            out[cur] = ln
        elif cur and ln.strip():
            out[cur] += "\n" + ln
    ops = _section(secs, OPERATIONS_HEADING) or []
    for ln in ops:
        m = TABLE_ID_RE.match(ln.strip())
        if m and m.group(1) == "P":
            out[f"P{int(m.group(2))}"] = ln.strip()
    return out


def changes(old: str, new: str) -> list[dict]:
    """前の版との差分を項目の単位で並べる。"""
    a, b = item_texts(old), item_texts(new)
    out = []
    for k in b:
        if k not in a:
            out.append({"item": k, "kind": "added"})
        elif a[k] != b[k]:
            out.append({"item": k, "kind": "changed"})
    out += [{"item": k, "kind": "removed"} for k in a if k not in b]
    return out


# ---------------------------------------------------------------- 型（pydantic）


def decl_models():
    """宣言の型（`lib/schema.py` の `Shape`）。使う側は `deps.require("schema")` を先に呼ぶ。"""
    from typing import Literal, Optional

    import schema
    from pydantic import Field

    class RevisePolicy(schema.Shape):
        overrides: Optional[int] = Field(default=None, ge=1)
        unknowns: Optional[int] = Field(default=None, ge=1)
        escapes: Optional[int] = Field(default=None, ge=1)

    class MvvSettings(schema.Shape):
        trend_commits: Optional[int] = Field(default=None, ge=0)
        trend_issues: Optional[int] = Field(default=None, ge=0)
        unknown_streak: Optional[int] = Field(default=None, ge=0)
        revise_after: Optional[RevisePolicy] = None

    class MvvVet(schema.Shape):
        verdict: Literal["follow", "unknown"]
        at: str
        accepted_unknown: bool = False

    class MvvChange(schema.Shape):
        item: str
        kind: Literal["added", "changed", "removed"]

    class MvvVersion(schema.Shape):
        version: int = Field(ge=1)
        sha256: str = Field(min_length=1)
        approved_at: str = Field(min_length=1)
        approved_by: str = Field(min_length=1)
        reason: Optional[str] = None
        vet: MvvVet
        changes: list[MvvChange]
        body: str = Field(min_length=1)

    class MvvDecl(schema.Shape):
        version: Literal[1]
        body: str = Field(default=BODY_FILE, min_length=1)
        settings: MvvSettings = Field(default_factory=MvvSettings)
        versions: list[MvvVersion] = Field(min_length=1)

    return MvvDecl, MvvVersion


def json_schema() -> str:
    decl, _ = decl_models()
    s = decl.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = "NDF のプロジェクト MVV の宣言（.ndf/mvv.json）"
    return json.dumps(s, ensure_ascii=False, indent=2) + "\n"
