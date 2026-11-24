"""GitHub へ書く本文（Issue・コメント・PR と Issue の本文）のエントリポイントと、公開範囲の検査（外部との接続の層。#3391）。

Issue・コメント・本文を書く操作は、このエントリポイント（`Publisher`）だけを通す。エントリポイントは書き先のリポジトリの公開範囲を読み、

| 書き先 | 本文から落とすもの |
| --- | --- |
| すべて | 秘密（秘密の環境変数の値とトークンの形。`run_log.redact`） |
| 公開（`PUBLIC`）と、公開範囲を読めない（`UNKNOWN`） | 上に加えて、公開でないリポジトリへの参照・`/home/<人>` と `/Users/<人>`・メール・手元のアカウント名 |

落とした所は固定の文に置き換え、種類ごとの件数を `Published.dropped` で返す。参照の公開範囲は本文に出る
リポジトリをまとめて 1 回のクエリで読み、読めないリポジトリは公開でないとして扱う（伏せる側へ倒す）。
書き先の公開範囲を読めないときも、公開の書き先と同じ検査をかける（未検査の本文を送らない）。
"""

from __future__ import annotations

import getpass
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.adapters import run_log
from ndf.adapters.github import PUBLIC, UNKNOWN, GitHub, same_repo

# 落とした所の固定の文
PRIVATE_REF_TEXT = "（非公開のリポジトリへの参照）"
HOME_TEXT = "（利用者のホーム）"
EMAIL_TEXT = "（メール）"
ACCOUNT_TEXT = "（アカウント名）"
# 公開のリポジトリへ書いてよいメール。公開してよいと確かめたアドレスだけを完全な形で並べる（GitHub の SSH の利用者・
# GitHub と Claude の送信専用のアドレス）。ローカル部だけでは許さない（`git@<社内のホスト>` は社内の情報を出す）
PUBLIC_EMAILS = frozenset({"git@github.com", "noreply@github.com", "noreply@anthropic.com"})
# GitHub が公開用に発行するアドレスのドメイン（`<番号>+<login>@users.noreply.github.com`）
PUBLIC_EMAIL_DOMAIN = "users.noreply.github.com"
# 公開の書き先と同じ検査をかける書き先の公開範囲（読めない書き先は公開と同じに扱う）
SCRUBBED_VISIBILITIES = frozenset({PUBLIC, UNKNOWN})
# 手元のアカウント名として扱わない短い名前（3 字未満は語の一部と区別できない）
MIN_ACCOUNT_CHARS = 3
# リポジトリへの参照。URL の形は `https://github.com/<所有者>/<リポジトリ>...`・scheme の無い `github.com/...`・
# SSH の clone の URL（`git@github.com:<所有者>/<リポジトリ>.git`・`ssh://git@github.com/...`）・
# API の URL（`https://api.github.com/repos/<所有者>/<リポジトリ>...`）。番号の形は `<所有者>/<リポジトリ>#<番号>`。
# スキームの後の利用者の部分は任意の値を許す（`user:pass@`・`<トークン>@`・伏せ字済みの `***@`）。認証情報の付いた URL も
# 参照として拾い、秘密の範囲と重なる URL を全体ごと伏せるためである（#3959）。
# リポジトリ名はリポジトリ名に使えない文字の手前で切り（日本語の句読点・全角の括弧・`,` に接しても一致する）、
# 境界の `\w` は `re.ASCII` で ASCII に限る（`参照はacme/x#12` のように日本語の文字に接した参照も拾う）。
# URL の続き（`/issues/1` など）は `/#?` で始まるときだけ、ASCII の区切りでない文字まで含める。
# スキームとホストは URL の規則どおり大文字・小文字を区別しない（`HTTPS://tok@GitHub.com/...` も URL の全体を拾って伏せる）
_REPO_URL = re.compile(
    r"(?<![\w.@-])(?i:(?:(?:https?|ssh|git)://(?:[^\s/@]+@)?|git@)?(?:git@github\.com:|(?:api\.github\.com/repos/|(?:www\.)?github\.com/)))"
    r"(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?(?![A-Za-z0-9._-])(?:[/#?][^\s)\]>\"'`\u0080-\U0010ffff]*)?",
    re.ASCII,
)
_REPO_REF = re.compile(r"(?<![\w./-])(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+)#\d+", re.ASCII)
# リポジトリへの参照の形（URL を先に置き換えてから番号の形を見る）
_REPO_PATTERNS = (_REPO_URL, _REPO_REF)
_HOME = re.compile(r"/(?:home|Users)/[^/\s)\]>\"'`]+")
_EMAIL = re.compile(r"(?<![\w.+-])(?P<local>[\w.+-]+)@(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# 由来の参照（`issue #12`・`PR #12`）。別のリポジトリへ書くときはリポジトリを足す（#3077）
_BARE_REF = re.compile(r"^(?P<label>(?:issue|PR|pr|Issue)\s*)#(?P<number>\d+)$")
# 公開の書き先へ、非公開のリポジトリの由来を書くときの固定の文（3391-2）。完全な参照は手元の引継ぎ文書だけに置く
PRIVATE_ORIGIN = "非公開のリポジトリの課題"


class DropKind(StrEnum):
    """落とした所の種類（`Scrubbed.dropped`・`Published.dropped` のキー）。"""

    SECRET = "secret"
    PRIVATE_REF = "private_ref"
    HOME = "home"
    EMAIL = "email"
    ACCOUNT = "account"


class Scrubbed(NamedTuple):
    """検査した本文と、落とした所の種類ごとの件数（`private_ref`・`home`・`email`・`account`・`secret`）。"""

    text: str
    dropped: dict[str, int]


class Published(NamedTuple):
    """書いた結果。`number` は Issue か PR の番号（コメントなら書いた先の番号）、`url` は書いたものの URL。"""

    number: int
    url: str
    dropped: dict[str, int]


def local_accounts() -> frozenset[str]:
    """手元のアカウント名（ログインの名前とホームのディレクトリの名前）。短すぎる名前は除く。"""
    names = {getpass.getuser(), Path.home().name}
    return frozenset(n for n in names if len(n) >= MIN_ACCOUNT_CHARS)


def repo_refs(text: str) -> set[str]:
    """本文に出るリポジトリ（`<所有者>/<リポジトリ>`）。公開範囲を読む相手。"""
    return {m["repo"] for pattern in _REPO_PATTERNS for m in pattern.finditer(text)}


def is_public(visibility: Mapping[str, str], repo: str) -> bool:
    """`visibility`（リポジトリ → 公開範囲。`GitHub.visibilities` の値）で `repo` が公開か。載っていなければ公開でない。"""
    return any(same_repo(name, repo) and value == PUBLIC for name, value in visibility.items())


def qualified_ref(origin: str, source_repo: str, target_repo: str, visibility: Mapping[str, str]) -> str:
    """由来の参照を、書き込む先のリポジトリの画面でも同じ課題を指す形にする（#3077）。

    - 書き込む先が由来のリポジトリと同じなら、そのまま返す
    - 書き込む先が公開で由来が公開でなければ、参照を伏せた固定の文（`PRIVATE_ORIGIN`）を返す（3391-2）。
      完全な参照は呼び手が手元の引継ぎ文書だけに置く
    - ほかは、番号だけの参照（`issue #12`）を `issue <所有者>/<リポジトリ>#12` にする。番号だけの形でない参照はそのまま

    `visibility` はリポジトリ → 公開範囲（`GitHub.visibilities` の値）。載っていないリポジトリは公開でないとして扱う。
    """
    if same_repo(source_repo, target_repo):
        return origin
    m = _BARE_REF.match(origin.strip())
    if is_public(visibility, target_repo) and not is_public(visibility, source_repo):
        return f"{m.group('label').strip()}（{PRIVATE_ORIGIN}）" if m else PRIVATE_ORIGIN
    if m is None:
        return origin
    return f"{m.group('label')}{source_repo}#{m.group('number')}"


def _overlaps(span: tuple[int, int], spans: Sequence[tuple[int, int]]) -> bool:
    return any(start < span[1] and span[0] < end for start, end in spans)


def _bare_repo(repo: str) -> re.Pattern[str]:
    """対象のリポジトリの完全名 `repo` が単独で現れる所（`.git` の接尾辞ごと）に一致する正規表現（#3959）。

    前は URL や別の名前の一部でないこと、後ろは名前の続きが来ないことだけを境界にする（文末の `.`・`/`・読点・括弧は外に残す）。
    """
    return re.compile(
        r"(?<![A-Za-z0-9._/-])" + re.escape(repo) + r"(?:\.git)?(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])(?!/[A-Za-z0-9._-])",
        re.IGNORECASE,
    )


def _hidden_spans(text: str, private: set[str], bare: Optional[str] = None) -> list[tuple[int, int, bool]]:
    """原文の上で伏せる範囲と、秘密を含むか（真なら秘密の伏せ字、偽なら非公開の参照の文）。重なる範囲は和にまとめる。

    秘密の範囲（`run_log.secret_spans`）と、秘密と重なる URL の全体（認証情報の付いた URL）と、`private` の参照の範囲を
    伏せ字をかける前の原文で求め、1 回で置き換える。参照を先に置き換えると参照の境界をまたぐ秘密の後半が残り、
    秘密を先に伏せると `ssh://...@github.com/<所有者>/<リポジトリ>` が参照の形に一致しなくなってリポジトリ名が残るためである。
    """
    secrets = run_log.secret_spans(text)
    marked = [(start, end, True) for start, end in secrets]
    lowered = {r.lower() for r in private}
    for pattern in _REPO_PATTERNS:
        for m in pattern.finditer(text):
            if pattern is _REPO_URL and _overlaps(m.span(), secrets):
                marked.append((m.start(), m.end(), True))
            elif m["repo"].lower() in lowered:
                marked.append((m.start(), m.end(), False))
    if bare is not None and bare.lower() in lowered:
        marked += [(m.start(), m.end(), False) for m in _bare_repo(bare).finditer(text)]
    out: list[tuple[int, int, bool]] = []
    for start, end, secret in sorted(marked):
        if out and start < out[-1][1]:
            last = out[-1]
            out[-1] = (last[0], max(last[1], end), last[2] or secret)
        else:
            out.append((start, end, secret))
    return out


def _drop_hidden(text: str, private: set[str], bare: Optional[str] = None) -> tuple[str, dict[str, int]]:
    pieces, at, dropped = [], 0, Counter[str]()
    for start, end, secret in _hidden_spans(text, private, bare):
        pieces += [text[at:start], run_log.MASK if secret else PRIVATE_REF_TEXT]
        dropped[DropKind.SECRET if secret else DropKind.PRIVATE_REF] += 1
        at = end
    if dropped[DropKind.SECRET]:
        dropped[DropKind.SECRET] = 1
    return "".join(pieces) + text[at:], dict(dropped)


def _drop_emails(text: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        domain = m["domain"].lower()
        if m[0].lower() in PUBLIC_EMAILS or domain == PUBLIC_EMAIL_DOMAIN or domain.endswith("." + PUBLIC_EMAIL_DOMAIN):
            return m[0]
        count += 1
        return EMAIL_TEXT

    return _EMAIL.sub(repl, text), count


def _drop_accounts(text: str, accounts: Iterable[str]) -> tuple[str, int]:
    names = sorted({a for a in accounts if len(a) >= MIN_ACCOUNT_CHARS}, key=len, reverse=True)
    if not names:
        return text, 0
    pattern = re.compile(r"(?<![\w.-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w-])")
    return pattern.subn(ACCOUNT_TEXT, text)


def scrub(text: str, public: bool, private_repos: Iterable[str] = (), accounts: Iterable[str] = (), bare: Optional[str] = None) -> Scrubbed:
    """書く本文から、書き先に出してはいけないものを落とす（上の表）。`public` は書き先が公開か。

    秘密（認証情報の付いた URL は全体）は書き先によらず落とし、非公開の参照は公開の書き先でだけ落とす。`bare` のリポジトリが
    `private_repos` にあれば、その完全名が単独で現れる所も参照と同じに落とす（`_bare_repo`）。
    """
    text, dropped = _drop_hidden(text, set(private_repos) if public else set(), bare)
    if public:
        steps = (
            (DropKind.HOME, lambda t: _HOME.subn(HOME_TEXT, t)),
            (DropKind.EMAIL, _drop_emails),
            (DropKind.ACCOUNT, lambda t: _drop_accounts(t, accounts)),
        )
        for kind, step in steps:
            text, n = step(text)
            if n:
                dropped[kind] = n
    return Scrubbed(text, dropped)


@dataclass
class Publisher:
    """1 つのリポジトリ（`hub.repo`）へ本文を書くエントリポイント。書く前に `guarded` の検査を通す。"""

    hub: GitHub
    accounts: frozenset[str] = field(default_factory=local_accounts)
    _visibility: Optional[str] = field(default=None, init=False)

    def visibility(self) -> str:
        """書き先の公開範囲（1 つのエントリポイントで 1 回だけ読む）。"""
        if self._visibility is None:
            self._visibility = self.hub.visibilities([self.hub.repo])[self.hub.repo]
        return self._visibility

    def exposed(self) -> bool:
        """書き先へ公開の書き先と同じ検査をかけるか。公開か、公開範囲を読めない（`UNKNOWN`）なら真。"""
        return self.visibility() in SCRUBBED_VISIBILITIES

    def _private_refs(self, texts: Sequence[str], exposed: bool, assume_public: bool = False) -> set[str]:
        """公開（か読めない）書き先のとき、本文に出る他のリポジトリのうち公開でないもの。ほかの書き先・参照が無ければ読まずに空。

        秘密を含む参照（参照そのものを伏せると変わるか、原文の一致の範囲が秘密の範囲と重なる）は、秘密の値を GitHub へ
        送らないよう照会せず、公開でないものとして返す（#3959）。`assume_public` が真なら、書き先のリポジトリ自身への参照と
        その完全名が単独で現れる所も同じ 1 回の照会に入れる（受け手が書き先を読めるとは限らないため）。
        """
        if not exposed:
            return set()
        clean: set[str] = set()
        tainted: set[str] = set()

        def sort(repo: str, span: tuple[int, int], secrets: Sequence[tuple[int, int]]) -> None:
            bad = run_log.redact(repo) != repo or _overlaps(span, secrets)
            (tainted if bad else clean).add(repo)

        for text in texts:
            secrets = run_log.secret_spans(text)
            for m in (m for pattern in _REPO_PATTERNS for m in pattern.finditer(text)):
                if assume_public or not same_repo(m["repo"], self.hub.repo):
                    sort(m["repo"], m.span(), secrets)
            if assume_public:
                # 単独の完全名も、URL・番号付きの参照と同じく秘密と重なれば照会しない
                for m in _bare_repo(self.hub.repo).finditer(text):
                    sort(self.hub.repo, m.span(), secrets)
        # 照会の直前に same_repo と同じ規則（大文字・小文字を区別しない）で名前をまとめ、同じリポジトリを 1 回だけ問う
        dirty = {t.lower() for t in tainted}
        asked = set({r.lower(): r for r in sorted(clean) if r.lower() not in dirty}.values())
        seen = self.hub.visibilities(asked) if asked else {}
        return tainted | {r for r, v in seen.items() if v != PUBLIC}

    def guarded(self, *texts: str, assume_public: bool = False) -> tuple[list[str], dict[str, int]]:
        """書く本文（題と本文など）を検査した結果と、落とした所の種類ごとの件数の和。

        `assume_public` が真なら書き先の公開範囲を照会せず、公開の書き先として伏せる（受け手が書き先の閲覧者と限らない結果）。
        """
        exposed = True if assume_public else self.exposed()
        private = self._private_refs(texts, exposed, assume_public)
        bare = next((r for r in private if same_repo(r, self.hub.repo)), None) if assume_public else None
        got = [scrub(text, exposed, private, self.accounts, bare) for text in texts]
        return [g.text for g in got], dict(sum((Counter(g.dropped) for g in got), Counter()))

    def origin(self, origin: str, source_repo: str) -> str:
        """由来の参照（`issue #12`）を、書き先の画面でも同じ課題を指す形にする（#3077）。

        書き先が公開（か公開範囲を読めない）で由来が公開でなければ、参照を伏せた固定の文にする（3391-2）。
        完全な参照は呼び手が手元の引継ぎ文書だけに置く。
        """
        if same_repo(source_repo, self.hub.repo):
            return origin
        seen = self.hub.visibilities([source_repo])
        seen[self.hub.repo] = PUBLIC if self.exposed() else self.visibility()
        return qualified_ref(origin, source_repo, self.hub.repo, seen)

    def create_issue(self, title: str, body: str, labels: Sequence[str] = ()) -> Published:
        (safe_title, safe_body), dropped = self.guarded(title, body)
        made = self.hub.rest("POST", f"/repos/{self.hub.repo}/issues", {"title": safe_title, "body": safe_body, "labels": list(labels)})
        return Published(int(made["number"]), str(made["html_url"]), dropped)

    def comment(self, number: int, body: str) -> Published:
        (safe,), dropped = self.guarded(body)
        made = self.hub.rest("POST", f"/repos/{self.hub.repo}/issues/{int(number)}/comments", {"body": safe})
        return Published(int(number), str(made["html_url"]), dropped)

    def edit_body(self, number: int, body: str) -> Published:
        """Issue か PR の本文を書き換える（PR の本文も Issue の API で書ける）。"""
        (safe,), dropped = self.guarded(body)
        made = self.hub.rest("PATCH", f"/repos/{self.hub.repo}/issues/{int(number)}", {"body": safe})
        return Published(int(number), str(made["html_url"]), dropped)
