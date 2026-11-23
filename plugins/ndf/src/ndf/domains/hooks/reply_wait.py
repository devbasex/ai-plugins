"""本文が人の回答・承認を待って終えたかをルールだけで判定する（#4000）。

10 系の `scripts/lib/wait_notice.py` の「本文の判定」（`classify_reply_wait`）を移した。最後の 3 行の取り方・
操作の案内の外し方は 10 系と同じである。語の列には 11.0.22 の `classify_reply` が拾う本人の回答待ちの言い回し
（「返答を待ちます」「ご回答をお待ちしています」）を足し、文の分け方はインラインコード・リンク先・URL の中の記号で切らない。guard `stop-wait` が軽いモデルの前に当てるのは `reply_kind` で、
`classify_reply_wait` の待ちの文から、かぎ括弧の引用と、別の場所（ほかのセッション・レーン）の待ちの報告を外す。

hook の閉包に入るため、標準ライブラリと、コードのブロックの行を決める `ndf.adapters.markdown`（CommonMark の解析）だけを読む。
"""

from __future__ import annotations

import re
from typing import NamedTuple

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
    "承認の指示",
    "ご意向を伺",
    "入力を待",
    # 11.0.22 の `classify_reply` が拾う本人の回答待ち（「返答を待ちます」「ご回答をお待ちしています」）
    "返答を待",
    "回答を待",
    "回答をお待ち",
    "返事をお待ち",
    "返答をお待ち",
    "確認をお待ち",
    "承認をお待ち",
    "指示をお待ち",
    "判断をお待ち",
)
# 待つ相手を語で決めない「お待ちしています」（11.0.22 の `classify_reply` が拾う形）。条件の添え書き
# （「ご質問があれば、ご連絡をお待ちしています。」）のときだけ待ちから外す
GENERIC_USER_WAIT = ("お待ちしています", "お待ちしております")
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
# 見出しの問いを利用者への承認・許可の依頼と読む形（「このままマージしてよいですか？」）。承認の語（「承認」「マージ」）の
# 出現だけでは依頼にしない（「マージの承認は必要ですか？」は事実を問う章題）
_PERMISSION_ASKS = (
    "てよいですか",
    "てよろしいですか",
    "てもよいですか",
    "てもよろしいですか",
    "てもいいですか",
    "ていいですか",
    "でよいですか",
    "でよろしいですか",
    "でよろしいでしょうか",
    "でいいですか",
    "てよいか",
    "てもよいか",
    "でよいか",
    "てよければ",
    "でよければ",
    "よろしければ",
    "問題なければ",
)
# 利用者に何かをしてもらう問いの形（「ご確認いただけますか？」）。回答・承認の語と並ぶときだけ依頼にする
_FAVOR_ASKS = ("いただけますか", "いただけませんか", "もらえますか", "もらえませんか", "くれますか", "くれませんか")
# 見出しの問いを選択肢から選ばせる問いと読む語（「どちらの案で進めますか？」）。手段を問う「どのように」は数えない
_CHOICE_ASK = re.compile(r"どちら|どっち|どれ|いずれ|選|どの(?!よう|程度|くらい|ぐらい)")

_SENTENCE_END = re.compile(r"(?<=[。？?！])")
# 文の区切りに数えない範囲: インラインコード・リンク先・URL（クエリの `?` を文末と読まない）。リンク先は山括弧で
# 囲んだ形（空白を含められる）・入れ子 1 つの丸括弧を含む形・タイトルつきの形まで 1 つの範囲にする
_INLINE_CODE = r"`[^`\n]+`"
_LINK_DEST = r"\]\(\s*(?:<[^<>\n]*>|[^\s()<>]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
_URL = r"https?://[^\s`()（）「」『』。、？！]+"
_OPAQUE = re.compile(f"{_INLINE_CODE}|{_LINK_DEST}|{_URL}")
# インラインコードの中身の置き換え。示した値（`answer=回答を待つ`）を待ちの語として読まない
_CODE_PLACEHOLDER = "`_`"
# 文の区切りの記号の、範囲の中での置き換え（判定の語にも末尾の形にも当たらない字）
_OPAQUE_MARKS = str.maketrans({"。": "．", "？": "＿", "?": "_", "！": "＿", "!": "_"})
# 別の場所の待ちの報告の後の、句のつなぎ（「…待っていますが、」の「っていますが、」）
_CLAUSE_SEPARATORS = "、，, "
# 句の区切り（読点の後で切る）。主題の「は」「も」で終わる句は次の句の主体なので、次の句とつなぐ（「別のレーンは、承認を待っています。」）
_CLAUSE_END = re.compile(r"(?<=[、，,])")
_TOPIC_ENDINGS = ("は", "も")
# 句の主体（最初の「は」「が」「も」までの句頭。句の末尾の「…ますが、」のつなぎは主体に数えない）
_CLAUSE_TOPIC = re.compile(r"^[^。]*?[はがも](?!$)")
_PAREN_INNER = re.compile(r"[（(]([^（）()]*)[）)]")
_TRAILING_PAREN = re.compile(r"\s*(?:（[^（）]*）|\([^()]*\))\s*$")
_DECORATION = "*_`~ 　"
_HEADING = re.compile(r"^#{1,6}(?:\s|$)")
# ATX の見出しの開きの `#` と、閉じの `#` の並び（見出しの文だけを残す）
_HEADING_MARKS = re.compile(r"^#{1,6}\s*|\s+#+\s*$")
_BULLET = re.compile(r"^(?:[-*+・]|\d+[.)])\s+")
# かぎ括弧の引用（ほかの人の質問を引いて経緯を書いた文を、本人の質問と読まない）
_QUOTED = re.compile(r"「[^「」]*」|『[^『』]*』")
# 別の場所（ほかのセッション・レーン）の待ちを報告する文。11 系の `classify_reply` と同じ形
_ELSEWHERE_PLACES = r"(セッション|レーン|計画|オーガナイザー)"
ELSEWHERE_RE = re.compile(rf"(別の|ほかの|他の)?{_ELSEWHERE_PLACES}[^\n]{{0,40}}(承認|回答|判断)[^\n]{{0,10}}待")
# 句の中の別の場所の待ちの報告。USER_WAIT_CONTAINS で待ちにした語（確認・指示・返事など）も報告として外す
_ELSEWHERE_CLAUSE_RE = re.compile(
    rf"(別の|ほかの|他の)?{_ELSEWHERE_PLACES}[^\n]{{0,40}}(承認|回答|判断|確認|指示|返事|返答|答え|入力|意向)[^\n]{{0,10}}待"
)
_ELSEWHERE_PLACE = re.compile(_ELSEWHERE_PLACES)
# 主体を書かない句でも、こちらの操作への承認・回答を求める句と読む目じるしの語（前の句の別の場所を引き継がない）
_OWN_REQUEST_MARKERS = ("こちら", "私", "当方", "てよいか", "てよろしいか", "でよいか", "でよろしいか", "てもよいか")
# 人の返事でないものを待つ「お待ちしています」（「CI の結果をお待ちしています。」）。待ちの文に数えない
_NON_REPLY_WAIT = re.compile(r"(結果|完了|終了|終わり|処理|反映|実行|ビルド|デプロイ|ジョブ|テスト|CI)をお待ち")
# 回答・承認を求めない定型の結び（「引き続きよろしくお願いします。」）。文がこれだけなら待ちの文に数えない。
# 求めるものを書いたリクエスト（「ご確認をよろしくお願いします。」）は当たらず、待ちに残る
_CLOSING = re.compile(
    r"^(?:(?:引き続き|今後とも|今後も|どうぞ|何卒|なにとぞ|以上です|以上)[、，,\s]*)*"
    r"よろしくお願い(?:します|いたします|致します|申し上げます)$"
    r"|^以上(?:です|となります)?$"
)
# 定型の結びの前の文が完了の報告だけと確かめる末尾（丁寧の過去の言い切り）。これで終わらない文が 1 つでもあれば、
# ルールで通さずに軽いモデルへ渡す（「いかがでしょう。」「判断が必要です。」は当たらない）
_REPORT_ENDINGS = ("ました", "でした")


def _markdown_lines(text: str) -> list[str]:
    """markdown-it と同じ数え方の行（改行は `\\r\\n`・`\\r`・`\\n`。末尾の改行の後は行にしない）。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return []
    lines = normalized.split("\n")
    return lines[:-1] if normalized.endswith("\n") else lines


class ProseLine(NamedTuple):
    """本文の 1 行。`bullet` は箇条書きの項目か。`heading` は ATX の見出しか（`text` は `#` を外した文）。"""

    text: str
    bullet: bool
    heading: bool = False


def prose_lines(text: str) -> list[ProseLine]:
    """コードのブロック・引用・表を除いた、空でない行。見出しは `heading` の印を付けて残す。

    見出しは最後の 3 行にも完了の報告の文にも数えず、利用者への問いの見出し（`_directed_heading`）だけを待ちの
    候補にする（`_waits`）。
    コードのブロック（囲みの種類と長さ・インデントのコード）は CommonMark の解析（`markdown.code_lines`）で決める。
    本文の全行の抜き出しはこの 1 つだけで行う。呼び手は返りを持ち、`prose_kind` へ渡す（1 回の Stop で 2 回しない）。
    """
    from ndf.adapters import markdown

    lines = _markdown_lines(text)
    code = markdown.code_lines(text)
    out: list[ProseLine] = []
    for raw, in_code in zip(lines, code):
        line = raw.strip()
        if in_code or not line or line.startswith((">", "|")):
            continue
        if _HEADING.match(line):
            heading = _HEADING_MARKS.sub("", line)
            if heading:
                out.append(ProseLine(heading, False, True))
            continue
        stripped = _BULLET.sub("", line)
        out.append(ProseLine(stripped, stripped != line))
    return out


def _last_lines(lines: list[ProseLine]) -> list[str]:
    """見出しを除いた最後の 3 行。箇条書きで終わるなら、その箇条書きの前置きの行まで含める（「どうしますか。」＋選択肢）。"""
    body = [line for line in lines if not line.heading]
    start = max(0, len(body) - 3)
    while 0 < start and body[start].bullet:
        start -= 1
    return [line.text for line in body[start:]]


def _directed_heading(lines: list[ProseLine], at: int) -> bool:
    """`at` の見出しが利用者への問いか（章題でないか）。

    見出しが回答・承認を待つ文を持ち、次のどれかに当たれば問い: 後に本文の行が無い（「## このまま進めてよいですか？」で
    終える）・利用者への依頼（`_requests_user`。「## このままマージしてよいですか？」「## 確認してください」の下に
    報告と結び）・選択肢を選ばせる問いで、次の見出しまでの行がどれも箇条書き（「## どちらの案で進めますか？」の下に
    選択肢）。承認の語の出現や箇条書きの有無だけでは問いにしない。後の本文が問いに答える章題（「### どうやって
    設定しますか？」の下の説明や手順の箇条書き・「## マージの承認は必要ですか？」の下の説明）は問いにしない。
    """
    asks = [s for s in _sentences([lines[at].text]) if _asks_reply(s)]
    if not asks:
        return False
    below: list[ProseLine] = []
    for line in lines[at + 1 :]:
        if line.heading:
            break
        below.append(line)
    if not any(not line.heading for line in lines[at + 1 :]):
        return True
    if any(_requests_user(s) for s in asks):
        return True
    return bool(below) and all(line.bullet for line in below) and any(_CHOICE_ASK.search(s) for s in asks)


def _requests_user(sentence: str) -> bool:
    """文が利用者に承認・回答を直接求める依頼か（事実や手段を問う文でないか）。

    許可を求める形（`_PERMISSION_ASKS`）・返事を待つと述べる文・回答や承認の語を持つ依頼（「確認してください」
    「ご承認をお願いします」「ご確認いただけますか？」）を依頼にする。
    """
    core = _core(sentence)
    if any(w in core for w in _PERMISSION_ASKS) or _user_wait(core):
        return True
    if not any(w in core for w in REPLY_WORDS + APPROVAL_WORDS):
        return False
    return any(w in core for w in WAIT_CONTAINS + _FAVOR_ASKS)


def _opaque(match: re.Match[str]) -> str:
    """区切りに数えない範囲の置き換え。インラインコードは中身ごと伏せ、リンク先と URL は区切りの記号だけを替える。"""
    text = match.group(0)
    if text.startswith("`"):
        return _CODE_PLACEHOLDER
    return text.translate(_OPAQUE_MARKS)


def _sentences(lines: list[str]) -> list[str]:
    """行の並びを、句点・疑問符・感嘆符で切った文の並びにする。"""
    out: list[str] = []
    for line in lines:
        line = _OPAQUE.sub(_opaque, line)
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


def _user_wait(core: str) -> bool:
    """利用者の返事を待つと述べる文か。
    「お待ちしています」の形の連絡の案内は、条件の添え書き（「問題があれば、ご指示をお待ちしています。」）なら
    具体的な待ちの語でも汎用の形でも外す（「…場合は、…ご意向を伺ってからにします。」は待ちに残す）。汎用の形は、
    人の返事でないもの（結果・完了など）を待つときも外す。
    """
    conditional = any(m in core for m in CONDITIONAL_MARKERS)
    if any(w in core for w in USER_WAIT_CONTAINS if not (conditional and "お待ち" in w)):
        return True
    if conditional:
        return False
    return any(w in core for w in GENERIC_USER_WAIT) and _NON_REPLY_WAIT.search(core) is None


def _is_wait(sentence: str) -> bool:
    """文が人の返事を待つ形か。"""
    core = _core(sentence)
    if _CLOSING.match(core):
        return False
    if _user_wait(core):
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
    if _user_wait(core) or ends_with_mark or core.endswith(WAIT_ENDINGS):
        return True
    hits = [w for w in WAIT_CONTAINS if w in core]
    if not set(hits) <= set(INSTRUCTION_ONLY):
        return True
    if not any(w in core for w in OPERATION_VERBS):
        return True
    return any(w in core for w in REPLY_WORDS + APPROVAL_WORDS)


def _waits(lines: list[ProseLine]) -> list[str]:
    """本文の行（`prose_lines` の返り）の、利用者への問いの見出しと、見出しを除いた最後の行から、回答・承認を待つ文を取り出す。"""
    headings = [line.text for i, line in enumerate(lines) if line.heading and _directed_heading(lines, i)]
    return [s for s in _sentences(headings + _last_lines(lines)) if _asks_reply(s)]


def _unquoted(text: str) -> str:
    """かぎ括弧の引用の中身を除いた文（入れ子は内側から外す）。"""
    prev = None
    while prev != text:
        prev = text
        text = _QUOTED.sub("", text)
    return text


def _clauses(sentence: str) -> list[str]:
    """文を読点で句に分ける。主題（「…は、」「…も、」）だけの句は、その主題で述べる次の句とつなぐ。"""
    out: list[str] = []
    for part in _CLAUSE_END.split(sentence):
        if not part:
            continue
        if out and out[-1].rstrip(_CLAUSE_SEPARATORS).endswith(_TOPIC_ENDINGS):
            out[-1] += part
        else:
            out.append(part)
    return out


def _elsewhere_clause(clause: str) -> bool:
    """句が別の場所の待ちの報告か。報告の前後（同じ句の中）に回答・承認を待つ質問が残れば偽。"""
    found = _ELSEWHERE_CLAUSE_RE.search(clause)
    if found is None:
        return False
    before = clause[: found.start()].rstrip(_CLAUSE_SEPARATORS)
    after = clause[found.end() :]
    return not any(part and _asks_reply(part) for part in (before, after))


def _only_elsewhere(sentence: str) -> bool:
    """文が別の場所の待ちの報告だけか。

    句ごとに待っている主体を見る。別の場所の待ちと確かめられた句だけを外し、ほかの句に回答・承認を待つ質問が
    残れば偽（「別のレーンを止めて、こちらの変更は回答を待ちます。」は、こちらの回答待ち）。主体を書かない句は
    前の句の主体を引き継ぐ（「別のレーンはレビューを終え、承認を待っています。」は、別のレーンの承認待ち）。
    ただし、こちらの操作への承認・回答を求める句（「こちらの変更をマージしてよいかご承認をお待ちしています。」）は
    引き継ぐ前に判定し、待ちに残す。
    """
    found = False
    subject = ""
    for clause in _clauses(sentence):
        topic = _CLAUSE_TOPIC.match(clause.rstrip(_CLAUSE_SEPARATORS))
        if topic is not None:
            subject = topic.group(0) if _ELSEWHERE_PLACE.search(topic.group(0)) else ""
        elif subject and any(m in clause for m in _OWN_REQUEST_MARKERS) and _asks_reply(clause):
            return False
        text = clause if topic is not None else subject + clause
        if _elsewhere_clause(text):
            found = True
        elif _asks_reply(text):
            return False
    return found


def classify_reply_wait(text: str) -> str:
    """本文が回答・承認を待って終えたかの種類（ANSWER / APPROVAL / NONE）。10 系と同じ。

    操作の案内のリクエストの形（「更新を終えました。アプリを再起動してください。」）は待ちに数えない。
    """
    waits = _waits(prose_lines(text))
    if not waits:
        return NONE
    return APPROVAL if any(any(w in s for w in APPROVAL_WORDS) for s in waits) else ANSWER


def _blank_quote(match: re.Match[str]) -> str:
    """かぎ括弧の引用 1 つの置き換え。中の改行だけを残し、行の数と行の並びを変えない。"""
    return "\n" * match.group(0).count("\n")


def _unquoted_lines(lines: list[ProseLine]) -> list[ProseLine]:
    """かぎ括弧の引用の中身を除き、空になった行を落とす（行を抜き出し直さない）。

    引用の開閉は行をまたいで追う（「ログの抜粋です。」の後の、2 行にわたる『…続けてもよいですか？』を本人の質問と
    読まない）。行を改行でつないだ 1 つの文字に `_QUOTED` を当て、引用の中の改行だけを残して行の位置を保つ。
    閉じない開きかっこは、これまでどおり引用に数えない。インラインコード・リンク先・URL は引用の開閉を追う前に
    伏せる（`「` を説明する行どうしで、かぎ括弧が対応して間の質問を消さない）。
    """
    joined = "\n".join(_OPAQUE.sub(_opaque, line.text) for line in lines)
    prev = None
    while prev != joined:
        prev = joined
        joined = _QUOTED.sub(_blank_quote, joined)
    out: list[ProseLine] = []
    for line, text in zip(lines, joined.split("\n")):
        rest = text.strip()
        if rest:
            out.append(line._replace(text=rest))
    return out


def _paren_wait(sentence: str) -> bool:
    """文の中の括弧書きに、待ちの形の文があるか（`_core` が落とす文末の括弧書きの中の質問・承認のリクエストを見る）。"""
    return any(_is_wait(inner) for inner in _PAREN_INNER.findall(sentence))


def _closing_report(lines: list[ProseLine]) -> bool:
    """定型の結び（「引き続きよろしくお願いします。」）で終え、結びの前が完了の報告だけの本文か。

    本文の全部の文（引用を除いた行）を見る。末尾から定型の結びの文を外し、残りの文がどれも待ちの形でなく
    丁寧の過去の言い切り（`_REPORT_ENDINGS`）で終わるときだけ真。結びの前に質問・承認のリクエスト・言い切らない文が
    あれば偽（ルールの待ちか、軽いモデルの判定へ回す）。括弧書きの中に待ちの形の文（「（このままマージしてよいですか）」）
    があっても偽。最後の行の最後の文が定型の結びでなければ、本文全体を文に分けずに偽を返す。
    """
    # 見出しは章題で、完了の報告の文に数えない（問いの見出しは `_waits` が先に待ちにする）
    lines = [line for line in lines if not line.heading]
    if not lines:
        return False
    tail = _sentences([lines[-1].text])
    if not tail or not _CLOSING.match(_core(tail[-1])):
        return False
    sentences = _sentences([line.text for line in lines])
    body = list(sentences)
    while body and _CLOSING.match(_core(body[-1])):
        # 結びの文の括弧書きの待ち（「引き続きよろしくお願いします（このままマージしてよいですか）。」）は外さない
        if _paren_wait(body[-1]):
            return False
        body.pop()
    if not body or len(body) == len(sentences):
        return False
    return all(not _is_wait(s) and not _paren_wait(s) and _core(s).endswith(_REPORT_ENDINGS) for s in body)


def prose_kind(lines: list[ProseLine]) -> str:
    """本文の行（`prose_lines` の空でない返り）を持つ応答の種類。`report` / `closing_report` / `elsewhere_wait` /
    `prose_wait`。

    行を抜き出し直さない。guard は `prose_lines` の返りを持ったまま、本文の行がある Stop でだけこれを呼ぶ
    （本文の全行の抜き出しを 1 回の Stop で 2 回しない）。待ちの文は `classify_reply_wait` と同じ形で探し、
    かぎ括弧の引用の中身は見ない（行をまたぐ引用も外す）。待ちの文が無く、定型の結びの前が完了の報告だけなら
    `closing_report`（guard は軽いモデルを呼ばずに通す）。
    """
    unquoted = _unquoted_lines(lines)
    waits = _waits(unquoted)
    if not waits:
        return "closing_report" if _closing_report(unquoted) else "report"
    if all(_only_elsewhere(s) for s in waits):
        return "elsewhere_wait"
    return "prose_wait"


def reply_kind(text: str) -> str:
    """guard `stop-wait` が使う最後の応答の種類（11 系の `classify_reply` と同じ語）。

    `none`（コードのブロック・引用・表のほかに本文が無い）・`elsewhere_wait`（待ちの文がどれも別の場所の
    待ちの報告だけで、ほかの句に本人への質問が無い）・`prose_wait`（本文で人の回答・承認を待つ）・`closing_report`
    （定型の結びの前が完了の報告だけ）・`report`。
    `prose_lines` の返りを既に持つ呼び手は、これでなく `prose_kind` を呼ぶ。
    """
    lines = prose_lines(text)
    return prose_kind(lines) if lines else "none"
