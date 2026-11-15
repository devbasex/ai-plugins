"""宛先のブランチの必須のチェックを解決し、PR の先端のチェックの実行を読む（`land` の部品）。

必須のチェックは、宛先のブランチの規則（ruleset）と従来の保護（branch protection）の両方から読み、承認の commit status
（`approval_decide.STATUS_CONTEXT`）を除く。発行元のアプリ（ruleset の `integration_id`・従来の保護の `app_id`）を
指定された必須のチェックは、指定のアプリの実行だけを数える（#3506）。
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import GitHub, GitHubError, text_of
from ndf.contracts import facts
from ndf.domains.release import approval_decide
from ndf.domains.release.shapes import list_of

# 規則の種類（必須のチェック）
REQUIRED_RULE = "required_status_checks"
# 従来の保護（branch protection）が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 結論の出ていない状態（事実の表 ci-outcomes の pending。commit status の `pending` を含む）
PENDING_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending"))
# 従来の保護の `app_id` で「どの発行元でもよい」を表す値
ANY_APP = -1


class Required(NamedTuple):
    """宛先の必須のチェック。`names` は名前（重複なく整列）、`apps` は発行元のアプリ（ruleset の `integration_id`・
    従来の保護の `app_id`）を指定された名前ごとのアプリの ID（#3506）。"""

    names: list[str]
    apps: dict[str, frozenset[int]]


def _status_run(status: dict, app: int | None = None) -> dict:
    """commit status 1 件を check run の形にする（`ci.read` に渡す）。`app` は書き手の GitHub App（分かれば `app.id` に入れる）。"""
    state = text_of(status["state"]).lower()
    conclusion = "" if state in PENDING_STATES else state
    run = {
        "name": text_of(status["context"]),
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "completed_at": text_of(status["updated_at"]) if conclusion else "",
        "started_at": text_of(status["created_at"]),
        "id": int(status["id"]),
    }
    if app is not None:
        run["app"] = {"id": app}
    return run


def _app(value: object) -> int | None:
    """発行元のアプリの ID（指定が無い・どれでもよい（-1）なら None）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value != ANY_APP and value > 0 else None


def _protection_checks(branch: dict) -> list[tuple[str, int | None]]:
    """`GET /branches/<名前>` の従来の保護の必須のチェック（名前, 発行元）。`contexts` は発行元を持たない。"""
    protection = branch.get("protection")
    checks = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict):
        return []
    contexts: list[tuple[str, int | None]] = [(text_of(c), None) for c in list_of(checks, "contexts")]
    return contexts + [(text_of(c["context"]), _app(c.get("app_id"))) for c in list_of(checks, "checks") if isinstance(c, dict)]


def _ruleset_checks(rules: list[dict]) -> list[tuple[str, int | None]]:
    """宛先のブランチの規則（ruleset）の必須のチェック（名前, 発行元）。"""
    return [
        (text_of(check["context"]), _app(check.get("integration_id")))
        for rule in rules
        if rule.get("type") == REQUIRED_RULE
        for check in rule["parameters"]["required_status_checks"]
    ]


def _required_of(pairs: list[tuple[str, int | None]]) -> Required:
    """必須のチェック（名前, 発行元）の並びを、承認の commit status を除いて名前と発行元のアプリへ集める。"""
    pairs = [(n, app) for n, app in pairs if n != approval_decide.STATUS_CONTEXT]
    apps: dict[str, set[int]] = {}
    for name, app in pairs:
        if app is not None:
            apps.setdefault(name, set()).add(app)
    return Required(sorted({n for n, _ in pairs}), {k: frozenset(v) for k, v in apps.items()})


def _restricted(name: str, apps: dict[str, frozenset[int]]) -> list[frozenset[int]]:
    """チェックの名前 `name` が `ci.read` の照合で当たる、発行元を指定された必須のチェックのアプリの集合の並び。"""
    return [want for required, want in apps.items() if ci.fulfils(name, required)]  # noqa: NDF201 上限: 発行元を指定した必須のチェックの数（ci.fulfils は名前の照合だけで外へ出ない）


def from_sources(runs: list[dict], apps: dict[str, frozenset[int]]) -> list[dict]:
    """発行元を指定された必須のチェックに当たる実行は、指定のアプリのもの（check run と、そのアプリの bot が書いた commit status）
    だけを残す（同じ名前の別の発行元を数えない。#3506）。

    当たるかは `ci.read` と同じ照合（`ci.fulfils`）で決める。生の名前で絞ると、前置きや分割ジョブの末尾の付いた別の発行元の
    実行（`other / pytest`）が絞り込みを抜け、`ci.read` が必須のチェックとして数える。同じ名前に別々のアプリが指定されていれば、
    1 つの実行では満たせないため残さない（待ちが止める）。"""
    return [r for r in runs if all(want == {_app_of(r)} for want in _restricted(text_of(r.get("name")), apps))]


def _app_of(run: dict) -> object:
    """実行の発行元のアプリの ID（`app` が無い commit status は None）。"""
    app = run.get("app")
    return app.get("id") if isinstance(app, dict) else None


@dataclass(frozen=True)
class RequiredChecks:
    """1 つのリポジトリの、宛先のブランチの必須のチェックと、コミットのチェックの実行を読む。"""

    hub: GitHub

    def required(self, branch: str) -> Required:
        """宛先のブランチの規則（ruleset。全ページ）と従来の保護の必須のチェック（承認の commit status を除く）。"""
        pairs = _ruleset_checks(self.hub.branch_rules(branch))
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}")
        except GitHubError as exc:
            if exc.status != NOT_FOUND:
                raise
            got = {}
        return _required_of(pairs + _protection_checks(got if isinstance(got, dict) else {}))

    def runs(self, sha: str, apps: dict[str, frozenset[int]], bots: dict[str, int | None]) -> list[dict]:
        """コミットの check run と commit status（check run の形にしたもの）の一覧。

        発行元を指定された必須のチェックがあれば、commit status を全件（書き手つき）で読み、それに当たる status には
        書き手の bot のアプリを付ける（指定のアプリが付けた status を満たしたと数える。#3506）。`bots` は bot ごとのアプリの控え。"""
        if not apps:
            combined = self.hub.rest("GET", f"/repos/{self.hub.repo}/commits/{sha}/status", params={"per_page": 100})
            statuses = list_of(combined, "statuses") if isinstance(combined, dict) else []
            return self.hub.check_runs(sha) + [_status_run(s) for s in statuses if isinstance(s, dict)]
        return self.hub.check_runs(sha) + [_status_run(s, self._status_app(s, apps, bots)) for s in self.hub.statuses(sha)]

    def _status_app(self, status: dict, apps: dict[str, frozenset[int]], bots: dict[str, int | None]) -> int | None:
        """発行元を指定された必須のチェックに当たる commit status の書き手のアプリ（当たらない・bot でなければ None）。"""
        if not _restricted(text_of(status.get("context")), apps):
            return None
        creator = status.get("creator")
        login = text_of(creator.get("login")) if isinstance(creator, dict) else ""
        if login not in bots:
            bots[login] = self.hub.bot_app(login)
        return bots[login]
