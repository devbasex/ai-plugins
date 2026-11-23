"""本文が人の回答・承認を待って終えたかをルールだけで判定する（#4000）。

10 系の `scripts/lib/wait_notice.py` の「本文の判定」（`classify_reply_wait`）をそのまま移した。語の列・文の分け方・
最後の 3 行の取り方・操作の案内の外し方は 10 系と同じである。guard `stop-wait` が軽いモデルの前に当てるのは `reply_kind` で、
`classify_reply_wait` の待ちの文から、かぎ括弧の引用と、別の場所（ほかのセッション・レーン）の待ちの報告を外す。

hook の閉包に入るため、標準ライブラリだけを読む。
"""

from __future__ import annotations

import re

ANSWER = "回答待ち"
APPROVAL = "承認待ち"
NONE = "待ちでない"

# 本文の判定の語。リポジトリごとに変える設定は持たない（10 系の #821 の決定 1）
WAIT_ENDINGS = ("?", "？", "ですか", "ますか", "でしょうか", "ませんか", "ましょうか")
WAIT_CONTAINS = ("ください", "お願いします", "いただければ", "もらえれば", "よろしければ", "てよければ", "でよければ", "問題なければ")
# 利用者の返事を待つと述べる文。末尾が「待っています」でも待ちの文にする
USER_WAIT_CONTAINS = (
    "承認を待",
    "返事を待",
    "答えを待",
    "判断を待",
    "指示を待",
    "ご指示をお待ち",
    "承認の指示",
    "ご意向を伺",
    "ご判断をお待ち",
    "入力を待",
)
# 条件つきのリクエスト（「…がある場合は…ください」）は、待たずに進む応答の添え書きとして待ちの文から外す
CONDITIONAL_MARKERS = ("場合は", "あれば", "場合には", "際は")
BACKGROUND_WAIT_ENDINGS = ("待ちます", "待っています")
APPROVAL_WORDS = (
    "承認",
    "許可",
    "マージ",
    "てよいですか",
    "てよければ",
    "てよろしいですか",
    "でよいですか",
    "でよろしいですか",
    "でよろしいでしょうか",
    "てよいか",
    "よろしければ",
    "でよければ",
    "問題なければ",
    "進めて",
    "approve",
)
# 操作の案内（「アプリを再起動してください」）にだけ現れるリクエストの語。回答・承認を待つ質問とは分ける
INSTRUCTION_ONLY = ("ください", "お願いします")
# 操作の案内と見なす動詞（許可リスト）。リクエストの語だけの文は、これを含むときに限り待ちから外す
OPERATION_VERBS = (
    "再起動",
    "起動し直",
    "開き直",
    "読み込み直",
    "再読み込み",
    "リロード",
    "インストール",
    "導入",
    "実行して",
    "ログインし直",
    "再ログイン",
)
# 回答・判断を求める語。操作の動詞と並んでも待ちに残す
REPLY_WORDS = ("確認", "選", "教え", "回答", "答え", "判断", "レビュー", "返事", "指示", "意見", "知らせ", "決め")

_SENTENCE_END = re.compile(r"(?<=[。？?！])")
_TRAILING_PAREN = re.compile(r"\s*(?:（[^（）]*）|\([^()]*\))\s*$")
_DECORATION = "*_`~ 　"
_HEADING = re.compile(r"^#{1,6}(?:\s|$)")
_BULLET = re.compile(r"^(?:[-*+・]|\d+[.)])\s+")
# かぎ括弧の引用（ほかの人の質問を引いて経緯を書いた文を、本人の質問と読まない）
_QUOTED = re.compile(r"「[^「」]*」|『[^『』]*』")
# 別の場所（ほかのセッション・レーン）の待ちを報告する文。11 系の `classify_reply` と同じ形（context.py もこれを読む）
ELSEWHERE_RE = re.compile(r"(別の|ほかの|他の)?(セッション|レーン|計画|オーガナイザー)[^\n]{0,40}(承認|回答|判断)[^\n]{0,10}待")


def _prose_lines(text: str) -> list[tuple[str, bool]]:
    """コードのブロック・引用・表・見出しを除いた、空でない行と、箇条書きの項目かどうか。"""
    out: list[tuple[str, bool]] = []
    in_code = False
    for raw in text.splitlines():
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
    """行の並びを、句点・疑問符・感嘆符で切った文の並びにする。"""
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
    """文が人の返事を待つ形か。"""
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


def _asks_reply(sentence: str) -> bool:
    """回答・承認を待つ質問か。

    リクエストの語だけで待ちになった文は、操作の案内（OPERATION_VERBS を含み、回答・判断・承認の語を含まない）の
    ときだけ外す。
    """
    if not _is_wait(sentence):
        return False
    core = _core(sentence)
    ends_with_mark = sentence.rstrip(_DECORATION).endswith(("?", "？"))
    if any(w in core for w in USER_WAIT_CONTAINS) or ends_with_mark or core.endswith(WAIT_ENDINGS):
        return True
    hits = [w for w in WAIT_CONTAINS if w in core]
    if not set(hits) <= set(INSTRUCTION_ONLY):
        return True
    if not any(w in core for w in OPERATION_VERBS):
        return True
    return any(w in core for w in REPLY_WORDS + APPROVAL_WORDS)


def _waits(text: str) -> list[str]:
    """本文の最後の行から、回答・承認を待つ文を取り出す。"""
    return [s for s in _sentences(_last_lines(_prose_lines(text))) if _asks_reply(s)]


def _unquoted(text: str) -> str:
    """かぎ括弧の引用の中身を除いた本文（入れ子は内側から外す）。"""
    prev = None
    while prev != text:
        prev = text
        text = _QUOTED.sub("", text)
    return text


def classify_reply_wait(text: str) -> str:
    """本文が回答・承認を待って終えたかの種類（ANSWER / APPROVAL / NONE）。10 系と同じ。

    操作の案内のリクエストの形（「更新を終えました。アプリを再起動してください。」）は待ちに数えない。
    """
    waits = _waits(text)
    if not waits:
        return NONE
    return APPROVAL if any(any(w in s for w in APPROVAL_WORDS) for s in waits) else ANSWER


def reply_kind(text: str) -> str:
    """guard `stop-wait` が使う最後の応答の種類（11 系の `classify_reply` と同じ語）。

    `none`（コードのブロック・引用・表・見出しのほかに本文が無い）・`elsewhere_wait`（待ちの文がどれも別の場所の
    待ちの報告）・`prose_wait`（本文で人の回答・承認を待つ）・`report`。待ちの文は `classify_reply_wait` と同じ形で
    探し、かぎ括弧の引用の中身は見ない。
    """
    if not _prose_lines(text):
        return "none"
    waits = _waits(_unquoted(text))
    if not waits:
        return "report"
    if all(ELSEWHERE_RE.search(s) for s in waits):
        return "elsewhere_wait"
    return "prose_wait"
