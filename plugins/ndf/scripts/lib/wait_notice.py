"""待ちの通知の判定・戻り先・関連 URL・本文（#821）。

入出力を持たない純粋な処理だけを置く。フック入力・環境変数・ホスト名・transcript の中身は、
入口（`scripts/wait-notify.py`）が読んで引数で渡す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

ANSWER = "回答待ち"
APPROVAL = "承認待ち"
DONE = "完了"
NONE = "待ちでない"

MARKS = {ANSWER: "【回答待ち】", APPROVAL: "【承認待ち】", DONE: "【完了】"}

EXCERPT_LIMIT = 200
URL_LIMIT = 3

# 本文の判定の語。プロジェクトごとに変える宣言は持たない（決定 1）
WAIT_ENDINGS = ("?", "？", "ですか", "ますか", "でしょうか", "ませんか", "ましょうか")
WAIT_CONTAINS = ("ください", "お願いします", "いただければ", "もらえれば", "よろしければ", "てよければ",
                 "でよければ", "問題なければ")
# 利用者の返事を待つと述べる文。末尾が「待っています」でも待ちの文にする
USER_WAIT_CONTAINS = ("承認を待", "返事を待", "答えを待", "判断を待", "指示を待", "ご指示をお待ち",
                      "承認の指示", "ご意向を伺", "ご判断をお待ち", "入力を待")
# 条件付きの依頼（「…がある場合は…ください」）は、待たずに進む応答の添え書きとして待ちの文から外す
CONDITIONAL_MARKERS = ("場合は", "あれば", "場合には", "際は")
BACKGROUND_WAIT_ENDINGS = ("待ちます", "待っています")
APPROVAL_WORDS = ("承認", "許可", "マージ", "てよいですか", "てよければ", "てよろしいですか",
                  "でよいですか", "でよろしいですか", "でよろしいでしょうか", "てよいか", "よろしければ",
                  "でよければ", "問題なければ",
                  "進めて", "approve")

_SENTENCE_END = re.compile(r"(?<=[。？?！])")
_TRAILING_PAREN = re.compile(r"\s*(?:（[^（）]*）|\([^()]*\))\s*$")
_DECORATION = "*_`~ 　"
_HEADING = re.compile(r"^#{1,6}(?:\s|$)")
_BULLET = re.compile(r"^(?:[-*+・]|\d+[.)])\s+")


@dataclass(frozen=True)
class Wait:
    kind: str
    excerpt: str
    key: str = ""


@dataclass(frozen=True)
class Locator:
    host: str
    cwd: str
    url: str | None = None
    resume: str | None = None

    def lines(self) -> list[str]:
        out = []
        if self.url:
            out.append(f"セッション: {self.url}")
        elif self.resume:
            out.append(f"再開: {self.resume}")
        out.append(f"host: {self.host} / cwd: {self.cwd}")
        return out


@dataclass(frozen=True)
class Notice:
    wait: Wait
    locator: Locator
    repo: str
    urls: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def text(self, mention: str = "") -> str | None:
        mark = MARKS.get(self.wait.kind)
        if not mark:
            return None
        head = f"{mark}[{self.repo}] {self.wait.excerpt}".rstrip()
        if mention:
            head = f"{mention} {head}"
        body = [head, *self.locator.lines(), *(f"{label}: {url}" for label, url in self.urls)]
        return "\n".join(body)


# ---------------------------------------------------------------------------
# 本文の判定
# ---------------------------------------------------------------------------

def _prose_lines(text: str) -> list[tuple[str, bool]]:
    """コードのブロック・引用・表・見出しを除いた、空でない行と、箇条書きの項目かどうか。"""
    out: list[tuple[str, bool]] = []
    in_code = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if line.startswith((">", "|")) or _HEADING.match(line):
            continue
        stripped = _BULLET.sub("", line)
        out.append((stripped, stripped != line))
    return out


def _last_lines(lines: list[tuple[str, bool]]) -> list[str]:
    """最後の 3 行。箇条書きで終わるなら、その箇条書きの前置きの行まで含める（「どうしますか。」＋選択肢）。"""
    start = max(0, len(lines) - 3)
    while 0 < start and lines[start][1]:
        start -= 1
    return [text for text, _ in lines[start:]]


def _sentences(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for part in _SENTENCE_END.split(line):
            s = part.strip()
            if s:
                out.append(s)
    return out


def _core(sentence: str) -> str:
    """文の末尾の句点・飾り・括弧書きを除いた形。"""
    s = sentence.strip()
    prev = None
    while prev != s:
        prev = s
        s = s.rstrip(_DECORATION)
        s = _TRAILING_PAREN.sub("", s)
        if s.endswith(("。", "！", "!")):
            s = s[:-1]
    return s


def _is_wait(sentence: str) -> bool:
    core = _core(sentence)
    if any(w in core for w in USER_WAIT_CONTAINS):
        return True
    if sentence.rstrip(_DECORATION).endswith(("?", "？")) or core.endswith(WAIT_ENDINGS):
        return True
    if core.endswith(BACKGROUND_WAIT_ENDINGS):
        return False
    hits = [w for w in WAIT_CONTAINS if w in core]
    if not hits:
        return False
    # 「…場合は…ください」だけの文は添え書き。許可を条件にする形（「進めてよければ」）は待ちに数える
    if hits == ["ください"] and any(m in core for m in CONDITIONAL_MARKERS):
        return False
    return True


def classify_text(text: str) -> tuple[str, str]:
    """応答の本文から待ちの種類と抜粋を決める。"""
    sentences = _sentences(_last_lines(_prose_lines(text)))
    if not sentences:
        return NONE, ""
    waits = [s for s in sentences if _is_wait(s)]
    if not waits:
        return NONE, ""
    kind = APPROVAL if any(any(w in s for w in APPROVAL_WORDS) for s in waits) else ANSWER
    return kind, " ".join(waits)[:EXCERPT_LIMIT]


def done_excerpt(text: str) -> str:
    """完了の通知の抜粋: 最後の段落の先頭 200 字（決定 7）。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paragraphs:
        return ""
    return " ".join(paragraphs[-1].split())[:EXCERPT_LIMIT]


# ---------------------------------------------------------------------------
# フックの事象から待ちへの訳し
# ---------------------------------------------------------------------------

def _plan_excerpt(plan: str) -> str:
    for line in (plan or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return f"計画の承認: {s}"[:EXCERPT_LIMIT]
    return "計画の承認"


def _question_excerpt(tool_input: dict) -> str:
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions:
        return "選択式の問い"
    first = questions[0].get("question", "") if isinstance(questions[0], dict) else str(questions[0])
    extra = f"（ほか {len(questions) - 1} 問）" if len(questions) > 1 else ""
    return f"{first}{extra}"[:EXCERPT_LIMIT]


def _from_text(text: str, key: str) -> Wait:
    kind, excerpt = classify_text(text)
    return Wait(kind, excerpt, key)


def classify_event(runtime: str, hook_input: dict, transcript_text: str = "", key: str = "") -> Wait | None:
    """ランタイムの事象を待ちへ訳す。待ちを作らない事象は None。

    `transcript_text` は transcript の最後の assistant の本文（`Stop` に本文が無いときに使う）。
    """
    event = hook_input.get("hook_event_name") or ""
    tool = hook_input.get("tool_name") or ""
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    if runtime == "claude":
        if event == "Notification":
            ntype = hook_input.get("notification_type")
            message = str(hook_input.get("message") or "")[:EXCERPT_LIMIT]
            if ntype == "permission_prompt":
                return Wait(APPROVAL, message, key)
            if ntype in ("elicitation_dialog", "elicitation_url_dialog"):
                return Wait(ANSWER, message, key)
            return None
        if event == "PermissionRequest":
            if tool == "ExitPlanMode":
                return Wait(APPROVAL, _plan_excerpt(str(tool_input.get("plan") or "")), key)
            return None
        if event == "PreToolUse":
            if tool == "AskUserQuestion":
                return Wait(ANSWER, _question_excerpt(tool_input), key)
            return None
        if event == "Stop":
            return _from_text(hook_input.get("last_assistant_message") or transcript_text, key)
        return None
    if runtime == "codex":
        if event == "PermissionRequest":
            return Wait(APPROVAL, f"{tool or 'ツール'} の実行の承認", key)
        if event == "Stop":
            return _from_text(hook_input.get("last_assistant_message") or transcript_text, key)
        return None
    if runtime == "kiro":
        # 入口は stop フックにだけ置く。名前の付いたほかの事象は応答の途中でありうるため待ちにしない。
        if event and event.lower() != "stop":
            return None
        return _from_text(hook_input.get("assistant_response") or transcript_text, key)
    return None


# ---------------------------------------------------------------------------
# 戻り先の組み立て
# ---------------------------------------------------------------------------

def session_url(session_id: str) -> str:
    sid = "session_" + session_id[len("cse_"):] if session_id.startswith("cse_") else session_id
    return f"https://claude.ai/code/{sid}"


def build_locator(runtime: str, hook_input: dict, env: dict, host: str, cwd: str) -> Locator:
    sid = str(hook_input.get("session_id") or "")
    if runtime == "claude":
        remote = env.get("CLAUDE_CODE_BRIDGE_SESSION_ID") or env.get("CLAUDE_CODE_REMOTE_SESSION_ID")
        if remote:
            return Locator(host, cwd, url=session_url(remote))
        return Locator(host, cwd, resume=f"claude --resume {sid}" if sid else None)
    if runtime == "codex":
        return Locator(host, cwd, resume=f"codex resume {sid}" if sid else None)
    if runtime == "kiro":
        kid = sid or str(hook_input.get("conversation_id") or env.get("KIRO_SESSION_ID") or "")
        return Locator(host, cwd, resume=f"kiro-cli chat --resume-id {kid}" if kid else "kiro-cli chat --resume")
    return Locator(host, cwd)


# ---------------------------------------------------------------------------
# 関連 URL の抽出
# ---------------------------------------------------------------------------

_URL = re.compile(r"https?://[^\s<>()\[\]「」、。`'\"]+")
_GH_PULL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")
_GH_ISSUE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/issues/\d+$")
_REDMINE_REF = re.compile(r"Redmine\s*#(\d+)", re.IGNORECASE)
_HASH_REF = re.compile(r"(?<![\w/#&])#(\d+)(?![\w])")
_GH_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def github_slug(remote_url: str) -> str | None:
    m = _GH_REMOTE.search((remote_url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def extract_urls(kind: str, text: str, slug: str | None = None, redmine_url: str | None = None,
                 pr_fallback: str | None = None) -> list[tuple[str, str]]:
    """本文から関連 URL を抜く。承認待ちは PR を先頭に置き、本文に無ければ `pr_fallback` を使う。"""
    text = text or ""
    redmine_base = (redmine_url or "").rstrip("/")
    redmine_host = urlparse(redmine_base).netloc if redmine_base else ""
    found: list[tuple[int, str, str]] = []
    prs: list[str] = []
    for m in _URL.finditer(text):
        url = m.group(0).rstrip(".,;:!?)")
        if _GH_PULL.match(url):
            prs.append(url)
        elif _GH_ISSUE.match(url):
            found.append((m.start(), "issue", url))
        elif redmine_host and urlparse(url).netloc == redmine_host:
            found.append((m.start(), "Redmine", url))
    stripped = _URL.sub(lambda m: " " * len(m.group(0)), text)
    if redmine_base:
        for m in _REDMINE_REF.finditer(stripped):
            found.append((m.start(), "Redmine", f"{redmine_base}/issues/{m.group(1)}"))
    stripped = _REDMINE_REF.sub(lambda m: " " * len(m.group(0)), stripped)
    if slug:
        for m in _HASH_REF.finditer(stripped):
            found.append((m.start(), "issue", f"https://github.com/{slug}/issues/{m.group(1)}"))
    found.sort()
    out: list[tuple[str, str]] = []
    if kind == APPROVAL:
        for url in prs or ([pr_fallback] if pr_fallback else []):
            if ("PR", url) not in out:
                out.append(("PR", url))
    for _, label, url in found:
        if all(u != url for _, u in out):
            out.append((label, url))
    return out[:URL_LIMIT]
