"""GitHub へ書く本文（Issue・コメント・PR と Issue の本文）のエントリポイントと、公開範囲の検査（外部との接続の層。#3391）。

Issue・コメント・本文を書く操作は、このエントリポイント（`Publisher`）だけを通す。エントリポイントは書き先のリポジトリの公開範囲を読み、

| 書き先 | 本文から落とすもの |
| --- | --- |
| すべて | 秘密（秘密の環境変数の値とトークンの形。`run_log.redact`） |
| 公開（`PUBLIC`） | 上に加えて、公開でないリポジトリへの参照・`/home/<人>` と `/Users/<人>`・メール・手元のアカウント名 |

落とした所は固定の文に置き換え、種類ごとの件数を `Published.dropped` で返す。参照の公開範囲は本文に出る
リポジトリをまとめて 1 回のクエリで読み、読めないリポジトリは公開でないとして扱う（伏せる側へ倒す）。
"""

from __future__ import annotations

import getpass
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.adapters import run_log
from ndf.adapters.github import PUBLIC, GitHub, qualified_ref

# 落とした所の固定の文
PRIVATE_REF_TEXT = "（非公開のリポジトリへの参照）"
HOME_TEXT = "（利用者のホーム）"
EMAIL_TEXT = "（メール）"
ACCOUNT_TEXT = "（アカウント名）"
SECRET_KIND = "secret"
# 公開のリポジトリへ書いてよいメール（GitHub が公開用に発行する宛先・git の SSH の利用者）
PUBLIC_EMAIL_LOCALS = frozenset({"noreply", "git"})
PUBLIC_EMAIL_DOMAIN = "users.noreply.github.com"
# 手元のアカウント名として扱わない短い名前（3 字未満は語の一部と区別できない）
MIN_ACCOUNT_CHARS = 3
# リポジトリへの参照（`https://github.com/<所有者>/<リポジトリ>...` と `<所有者>/<リポジトリ>#<番号>`）
_REPO_URL = re.compile(r"https?://github\.com/(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?(?=[/#?\s)\]>\"'`]|$)[^\s)\]>\"'`]*")
_REPO_REF = re.compile(r"(?<![\w./-])(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+)#\d+")
_HOME = re.compile(r"/(?:home|Users)/[^/\s)\]>\"'`]+")
_EMAIL = re.compile(r"(?<![\w.+-])(?P<local>[\w.+-]+)@(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


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
    return {m["repo"] for pattern in (_REPO_URL, _REPO_REF) for m in pattern.finditer(text)}


def _drop_private_refs(text: str, private: set[str]) -> tuple[str, int]:
    lowered = {r.lower() for r in private}

    def repl(m: re.Match[str]) -> str:
        return PRIVATE_REF_TEXT if m["repo"].lower() in lowered else m[0]

    total = 0
    for pattern in (_REPO_URL, _REPO_REF):
        hits = sum(1 for m in pattern.finditer(text) if m["repo"].lower() in lowered)
        text = pattern.sub(repl, text)
        total += hits
    return text, total


def _drop_emails(text: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        if m["local"].lower() in PUBLIC_EMAIL_LOCALS or m["domain"].lower().endswith(PUBLIC_EMAIL_DOMAIN):
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


def scrub(text: str, public: bool, private_repos: Iterable[str] = (), accounts: Iterable[str] = ()) -> Scrubbed:
    """書く本文から、書き先に出してはいけないものを落とす（上の表）。`public` は書き先が公開か。"""
    dropped: dict[str, int] = {}
    masked = run_log.redact(text)
    if masked != text:
        dropped[SECRET_KIND] = 1
    text = masked
    if public:
        steps = (
            ("private_ref", lambda t: _drop_private_refs(t, set(private_repos))),
            ("home", lambda t: _HOME.subn(HOME_TEXT, t)),
            ("email", _drop_emails),
            ("account", lambda t: _drop_accounts(t, accounts)),
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

    def guarded(self, *texts: str) -> tuple[list[str], dict[str, int]]:
        """書く本文（題と本文など）を検査した結果と、落とした所の種類ごとの件数の和。"""
        public = self.visibility() == PUBLIC
        private: set[str] = set()
        if public:
            refs = {r for t in texts for r in repo_refs(t) if r.lower() != self.hub.repo.lower()}
            seen = self.hub.visibilities(refs) if refs else {}
            private = {r for r, v in seen.items() if v != PUBLIC}
        out: list[str] = []
        dropped: dict[str, int] = {}
        for text in texts:
            got = scrub(text, public, private, self.accounts)
            out.append(got.text)
            for kind, n in got.dropped.items():
                dropped[kind] = dropped[kind] + n if kind in dropped else n
        return out, dropped

    def origin(self, origin: str, source_repo: str) -> str:
        """由来の参照（`issue #12`）を、書き先の画面でも同じ課題を指す形にする（#3077）。

        書き先が公開で由来が公開でなければ、参照を伏せた固定の文にする（3391-2）。完全な参照は呼び手が
        手元の引継ぎ文書だけに置く。
        """
        if source_repo.lower() == self.hub.repo.lower():
            return origin
        seen = self.hub.visibilities([source_repo])
        seen[self.hub.repo] = self.visibility()
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
