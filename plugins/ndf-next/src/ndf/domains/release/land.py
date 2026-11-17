"""承認の証跡の後に、リリースの PR をブランチ戦略の設定の主体と方式で入れる（GitHub Actions の workflow が打つ）。

設定は `.ndf/project.json` の `branch_strategy`（`core.branch_strategy` が読む）の 1 か所で、手順は 10 系の
`release_lib/land.py` の `land_approved` と同じ（#3672 の I5・I7・I10・I12）。

1. マージ済みの PR は、本番ブランチ宛てなら記録を補い（人のマージの記録・迂回の記録）、ベースブランチへ戻す PR へ再開する
2. `merge.actor: human` なら NDF はマージしない（人のマージを待つ）
3. 本番ブランチ宛ての PR は、書く token の書き手が記録の読み手に信用されることを確かめる（#3487）
4. 本番ブランチの ruleset とセクションの値を比べ（`ruleset_mismatch`）、食い違えば止まる。読めなければ比べずに止まる
5. マージの方式ごとの確かめ（`check_production_pr`）: `strict` は head が本番の HEAD を含むか、`merge-queue` は版上げの PR の
   版数が衝突しないか（切った時点の版数と本番の版数が同じで、キューに別の版上げの PR が無いか）
6. 証跡を読み直し、今の head が承認されていることを確かめる（承認の後に head が動けば止まる。#3661）。必須のチェックが
   通るまで待ち（落ちたら打ち直さずに止まる。#1983）、待ちの後にもう一度証跡を読み直す
7. 方式で入れる: `direct` は承認済みのコミットを添えてマージ（`merge.admin` で本番ブランチが承認を求めれば管理者の迂回）、
   `strict` は ruleset があれば同じマージ（ruleset が最新でない head を断る）、無ければ確かめた HEAD からの早送り、
   `merge-queue` は承認済みのコミットを添えて版上げの PR をキューの先頭へ入れ、マージされるまで待つ
8. 本番ブランチへ入れた後、同期の PR が要るリリース方式（`route_decide.route_of` の `sync`）なら、本番ブランチから
   ベースブランチ宛ての PR を作り（開いたものがあれば使い直す）、必須のチェックの後に merge commit でマージする

`approval.via: review` の設定では、承認は GitHub のレビュー（ruleset の承認の規則）が持ち、NDF は証跡の代わりに write 以上の人の
最新の承認のレビューのコミットを今の head と比べる（書き手は `github`。`trail.trail_writer`）。迂回でマージするときの証跡の検証は、担い手が MVV 判定の証跡を数えない（#3506 の I9）。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import NamedTuple, Optional
from urllib.parse import quote

from ndf.adapters import ci, merge_queue
from ndf.adapters.github import BYPASS_ORDER, MERGE_COMMIT, MERGE_QUEUE_PENDING, GitHub, GitHubError, MergeOutcome, Pull, RuleFacts, text_of
from ndf.adapters.publish import Publisher
from ndf.contracts import facts
from ndf.core import json_pointer
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR, TRAILER_TIP, Strategy
from ndf.domains.release import approval_decide, plugins, route_decide, trail, version_decide
from ndf.domains.release.plugins import CLAUDE_MANIFEST, MARKETPLACE, PLUGINS_DIR

# 規則の種類（必須のチェック）
REQUIRED_RULE = "required_status_checks"
# 戻す PR のマージの方式。本番ブランチとベースブランチの祖先の関係を保つため merge commit に限る
# （squash・rebase では次のリリースの範囲に出した変更が再び入る）。許されていなければ GitHub へ送らずに止まる。
# 本番の PR は設定の `merge.commit` で入れる
SYNC_METHOD = MERGE_COMMIT
# GitHub が PR の作成を断る状態と、そのうち差分が無いことを表す文言（ベースブランチへ戻す差分が無い）
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
NOTHING_TO_SYNC_TEXT = "No commits between"
# 従来の保護（branch protection）が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 迂回して本番の PR を越えられる迂回の可否（ruleset の `current_user_can_bypass`。強さの並びの `pull_requests_only` 以上）
BYPASS_OK = BYPASS_ORDER[BYPASS_ORDER.index("pull_requests_only") :]
REAPPROVE = "承認を取り直してください"
# 人のマージを待つときの停止理由の種類（NDF はマージしない。正常な否定の結果）
HUMAN_KIND = "not_applicable"
# 結論の出ていない状態（事実の表 ci-outcomes の pending。commit status の `pending` を含む）
PENDING_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending"))
# マージキューへ入れた PR がマージされたかを読み直す間隔（秒）
QUEUE_POLL_SECONDS = 30.0
# merge queue を読むページの上限（オーナーは merge_queue。本番の PR の確かめが読み切れる範囲として land の名前でも引ける）
QUEUE_PAGES = merge_queue.QUEUE_PAGES
# 従来の保護の `app_id` で「どの発行元でもよい」を表す値
ANY_APP = -1


class Required(NamedTuple):
    """マージ先の必須のチェック。`names` は名前（重複なく整列）、`apps` は発行元のアプリ（ruleset の `integration_id`・
    従来の保護の `app_id`）を指定された名前ごとのアプリの ID（#3506）。"""

    names: list[str]
    apps: dict[str, frozenset[int]]


class Landed(NamedTuple):
    """マージの結果。止まったら `kind` に停止理由の種類。"""

    merged: bool
    kind: str | None
    detail: str
    sync_pr: int | None = None
    # 本番ブランチへ入ったコミット（タグと GitHub Release を作るバージョン。本番ブランチへ入れていなければ None）
    production_sha: str | None = None
    # 本番の PR を管理者の迂回でマージしたか・迂回の記録の URL・記録を書けなかった理由（#3506）
    admin: bool = False
    admin_record: str | None = None
    record_error: str | None = None


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


def _list(raw: dict, key: str) -> list:
    """`raw[key]` が配列ならそれ、ほかは空の配列。"""
    got = raw.get(key)
    return got if isinstance(got, list) else []


def _app(value: object) -> int | None:
    """発行元のアプリの ID（指定が無い・どれでもよい（-1）なら None）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value != ANY_APP and value > 0 else None


def _protection_checks(branch: dict) -> list[tuple[str, int | None]]:
    """`GET /branches/<名前>` の従来の保護の必須のチェック（名前, 発行元）。`contexts` は発行元を持たない。"""
    protection = branch.get("protection")
    checks = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict):
        return []
    contexts: list[tuple[str, int | None]] = [(text_of(c), None) for c in _list(checks, "contexts")]
    return contexts + [(text_of(c["context"]), _app(c.get("app_id"))) for c in _list(checks, "checks") if isinstance(c, dict)]


def _restricted(name: str, apps: dict[str, frozenset[int]]) -> list[frozenset[int]]:
    """チェックの名前 `name` が `ci.read` の検証で当たる、発行元を指定された必須のチェックのアプリの集合の並び。"""
    return [want for required, want in apps.items() if ci.fulfils(name, required)]  # noqa: NDF201 上限: 発行元を指定した必須のチェックの数（ci.fulfils は名前の検証だけで外へ出ない）


def _from_sources(runs: list[dict], apps: dict[str, frozenset[int]]) -> list[dict]:
    """発行元を指定された必須のチェックに当たる実行は、指定のアプリのもの（check run と、そのアプリの bot が書いた commit status）
    だけを残す（同じ名前の別の発行元を数えない。#3506）。

    当たるかは `ci.read` と同じ検証（`ci.fulfils`）で決める。生の名前で絞ると、前置きや分割ジョブの末尾の付いた別の発行元の
    実行（`other / pytest`）が絞り込みを抜け、`ci.read` が必須のチェックとして数える。同じ名前に別々のアプリが指定されていれば、
    1 つの実行では満たせないため残さない（待ちが止める）。"""
    return [r for r in runs if all(want == {_app_of(r)} for want in _restricted(text_of(r.get("name")), apps))]


def _app_of(run: dict) -> object:
    """実行の発行元のアプリの ID（`app` が無い commit status は None）。"""
    app = run.get("app")
    return app.get("id") if isinstance(app, dict) else None


def ruleset_mismatch(strategy: Strategy, facts: RuleFacts) -> list[dict]:
    """セクションの値と本番ブランチの ruleset の食い違いを I10 の比べ方で並べる（10 系と同じ）。空なら一致。

    ruleset が無いときは `merge-queue` の設定だけを食い違いにする。`merge.admin: true` では承認の数を比べず、
    迂回の可否が `always`・`pull_requests_only` 以外なら食い違い。
    """
    sec = strategy.section
    method = sec["merge"]["method"]
    out: list[dict] = []
    if not facts.present:
        if method == "merge-queue":
            out.append({"key": "merge.method", "declared": method, "ruleset": "ruleset が無い（merge queue が無い）"})
        return out
    if (method == "merge-queue") != facts.queue:
        out.append({"key": "merge.method", "declared": method, "ruleset": "merge queue あり" if facts.queue else "merge queue なし"})
    queue_commit = facts.queue_merge_method.lower() if facts.queue_merge_method else None
    if method == "merge-queue" and facts.queue and queue_commit is not None and queue_commit != sec["merge"]["commit"]:
        # キューは自分の merge_method でマージする。設定の履歴の作り方（merge.commit）と違えば投入前に止める
        out.append({"key": "merge.commit", "declared": sec["merge"]["commit"], "ruleset": f"merge queue のマージ方式 {queue_commit}"})
    if method == "strict" and not facts.up_to_date:
        out.append({"key": "merge.method", "declared": method, "ruleset": "最新のマージを求めない"})
    return out + _approval_mismatch(strategy, facts)


def _approval_mismatch(strategy: Strategy, facts: RuleFacts) -> list[dict]:
    """管理者の迂回と承認の数の食い違い。`merge.admin: true` なら迂回の可否だけを比べ、承認の数は比べない。"""
    sec = strategy.section
    via, admin = sec["approval"]["via"], sec["merge"]["admin"]
    if admin:
        return [] if facts.bypass in BYPASS_OK else [{"key": "merge.admin", "declared": True, "ruleset": f"迂回の可否 {facts.bypass}"}]
    if via == "gate" and facts.approvals > 0:
        return [{"key": "approval.via", "declared": via, "ruleset": f"承認の数 {facts.approvals}"}]
    if via == "review" and facts.approvals < 1:
        return [{"key": "approval.via", "declared": via, "ruleset": "承認の数 0"}]
    return []


def bump_of(cut: Optional[str], head: Optional[str], prod: Optional[str], *, optional: bool) -> Optional[bool]:
    """版上げの PR か。切った時点 `cut` と `head` の版数が違えば真・同じなら偽。

    `optional` は版数が任意の保存先か（`version_store.kind` が `plugin` か `none`。プラグインの定義の `version` は任意で、`none` は版数を持たない）。任意の保存先で
    切った時点・head・本番 `prod` のどれにも版数が無ければ偽（バージョンを上げようがない）。明示の保存先（`json` の path・pointer）で
    読めないのは設定の誤りで、片側を読めないときと同じく None（版上げとして衝突を確かめ、版数を読めずに止まる）。"""
    if cut and head:
        return cut != head
    if optional and cut is None and head is None and prod is None:
        return False
    return None


def may_bump(row: dict) -> bool:
    """版上げの PR でありうるか。`bump` が None（版数を比べられない）は版上げとみなす（衝突の検査から外さない）。"""
    return "bump" in row and row["bump"] is not False


def check_production_pr(strategy: Strategy, pr: dict, prod: dict, queue: list[dict]) -> Optional[str]:
    """本番の PR がマージの方式ごとの確かめを通るか（10 系と同じ）。通れば None、落ちれば理由の文。

    `pr` は `number`・`bump`（版上げの PR か。比べられなければ None）・`cut_version`（切った時点の本番の版数）・
    `contains_tip`（head が本番の HEAD を含むか）、`prod` は `tip`・`version`、`queue` はキューの PR（各 `number`・`bump`）と、
    承認したがまだキューへ入れていない版上げの PR（`approved_release` が真の行）。
    """
    method = strategy.merge_method
    if method == "direct":
        return None
    held = [q for q in queue if q.get("approved_release") and q.get("number") != pr.get("number")]
    if method == "merge-queue" and pr.get("bump") is not True and held:
        return f"版上げの PR #{held[0]['number']} を承認してからキューへ入れるまで、スプリントの PR はキューへ入れない。入った後に打ち直してください"
    if method == "strict":
        if not pr.get("contains_tip"):
            return f"head が本番ブランチの HEAD {str(prod['tip'])[:12]} を含まない。本番の PR を作り直し、{REAPPROVE}"
        return None
    if not may_bump(pr):
        return None
    if pr.get("cut_version") is None or prod.get("version") is None:
        return (
            f"版数を読めない（版上げの PR を切った時点 {pr.get('cut_version')}・本番ブランチ {prod.get('version')}）。版数の衝突を確かめられないため"
            f"キューへ入れない。設定の版数の保存先（release.version_store）とそのファイルを直してから打ち直してください"
        )
    if pr.get("cut_version") != prod.get("version"):
        return (
            f"本番ブランチの版数 {prod.get('version')} が、版上げの PR を切った時点の {pr.get('cut_version')} と違う。作り直し、{REAPPROVE}"
        )
    others = [q for q in queue if may_bump(q) and not q.get("approved_release") and q.get("number") != pr.get("number")]
    if others:
        return f"キューに別の版上げの PR（#{others[0]['number']}）がある。作り直し、{REAPPROVE}"
    return None


def manifest_paths(strategy: Strategy, show: Callable[[str], Optional[str]]) -> list[str]:
    """版数の保存先が `plugin` のとき、版数を持つプラグインの定義の候補。

    保存先は `plugins.load` と同じく、`show` で読んだマーケットプレイスの登録の `source` から求める（`./tools/kit` などの
    配置も読む）。登録を読めない・名前の行が無いときは、10 系の `land_read.manifest_paths` と同じ `plugins/<名前>` と根の候補。"""
    name = strategy.release_plugin
    manifest = CLAUDE_MANIFEST.as_posix()
    if not name:
        return [manifest]
    text = show(MARKETPLACE.as_posix())
    try:
        data = json.loads(text) if text is not None else None
        found = plugins.by_name(plugins.entries(data)) if isinstance(data, dict) else {}
    except (ValueError, plugins.PluginsError):
        found = {}
    if name in found:
        # 根に置いたプラグイン（source が `./`）は dir が空で、定義は根の `manifest` そのもの
        return ["/".join(filter(None, [found[name].dir, manifest]))]
    return [f"{PLUGINS_DIR}/{name}/{manifest}", manifest]


def version_rows(strategy: Strategy, show: Callable[[str], Optional[str]]) -> list[tuple[str, str]]:
    """版数を引く候補（ファイルのパスと JSON Pointer）。保存先が `none` なら空。`plugin` の保存先の解決は `show` で
    マーケットプレイスの登録を読むため、`land` 1 回につき本番の HEAD で 1 度だけ求めて使い回す。"""
    store = strategy.version_store
    if store["kind"] == "none":
        return []
    if store["kind"] == "plugin":
        return [(p, "/version") for p in manifest_paths(strategy, show)]
    return [(store["path"], store["pointer"])]


def read_version(rows: list[tuple[str, str]], show: Callable[[str], Optional[str]]) -> Optional[str]:
    """候補 `rows`（`version_rows`）のうち `show` で読める最初のファイルの版数（10 系の `land_read.store_version` と同じ）。
    候補が無い（保存先が `none`）・読めないときは None。"""
    for path, pointer in rows:
        text = show(path)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except ValueError:  # noqa: NDF401 ファイル: JSON として読めない版数のファイルは版数を比べられない（None。版上げとして確かめる）
            return None
        _, value = json_pointer.resolve(doc, pointer)
        return value if isinstance(value, str) and value else None
    return None


def trailers(pr: int, head: str, tip: str) -> str:
    """早送りのマージのコミットの trailer（PR・承認済みのコミット・確かめた本番の HEAD）。"""
    return f"{TRAILER_PR}: {pr}\n{TRAILER_HEAD}: {head}\n{TRAILER_TIP}: {tip}"


class Gate(NamedTuple):
    """本番の PR の確かめの結果。`stop` が None なら進める。`bypass` は管理者の迂回で入れるか、`pr` は読んだ PR の事実。"""

    stop: Optional[Landed]
    bypass: bool = False
    rules: Optional[RuleFacts] = None
    pr: Optional[dict] = None


@dataclass(frozen=True)
class Lander:
    """1 つのリポジトリのリリースの PR を、ブランチ戦略の設定 `strategy` の主体と方式で入れる。
    `wait` は必須のチェックの待ちを組む関数（テストが差し替える）。"""

    hub: GitHub
    strategy: Strategy
    budget: float
    wait: Callable[[Callable[[], ci.Reading], float], ci.WatchResult] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    @property
    def base(self) -> str:
        return self.strategy.base

    @property
    def production(self) -> str:
        return self.strategy.production

    @property
    def landing(self) -> route_decide.Landing:
        return route_decide.landing_of(self.strategy)

    def _by_review(self) -> bool:
        """承認を GitHub のレビューで受ける設定か（NDF は証跡を読まない。書き手 `github`）。"""
        return self.strategy.approval_via == "review"

    def required(self, branch: str) -> Required:
        """マージ先のブランチの規則（ruleset。全ページ）と従来の保護の必須のチェック（承認の commit status を除く）。"""
        pairs: list[tuple[str, int | None]] = [
            (text_of(check["context"]), _app(check.get("integration_id")))
            for rule in self.hub.branch_rules(branch)
            if rule.get("type") == REQUIRED_RULE
            for check in rule["parameters"]["required_status_checks"]
        ]
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}")
        except GitHubError as exc:
            if exc.status != NOT_FOUND:
                raise
            got = {}
        pairs += _protection_checks(got if isinstance(got, dict) else {})
        pairs = [(n, app) for n, app in pairs if n != approval_decide.STATUS_CONTEXT]
        apps: dict[str, set[int]] = {}
        for name, app in pairs:
            if app is not None:
                apps.setdefault(name, set()).add(app)
        return Required(sorted({n for n, _ in pairs}), {k: frozenset(v) for k, v in apps.items()})

    def _runs(self, sha: str, apps: dict[str, frozenset[int]], bots: dict[str, int | None]) -> list[dict]:
        """コミットの check run と commit status（check run の形にしたもの）の一覧。

        発行元を指定された必須のチェックがあれば、commit status を全件（書き手つき）で読み、それに当たる status には
        書き手の bot のアプリを付ける（指定のアプリが付けた status を満たしたと数える。#3506）。`bots` は bot ごとのアプリのバックアップ。"""
        if not apps:
            combined = self.hub.rest("GET", f"/repos/{self.hub.repo}/commits/{sha}/status", params={"per_page": 100})
            statuses = _list(combined, "statuses") if isinstance(combined, dict) else []
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

    def _watch(self, pull: Pull, deadline: float) -> str | None:
        """必須のチェック（check run と commit status）を時刻 `deadline` まで待ち、停止理由の種類（通れば None）。"""
        required = self.required(pull.base_ref)
        bots: dict[str, int | None] = {}

        def reader() -> ci.Reading:
            return ci.read(_from_sources(self._runs(pull.head_sha, required.apps, bots), required.apps), required.names, [])

        left = max(0.0, deadline - self.clock())
        watched = self.wait(reader, left) if self.wait is not None else ci.Watch(reader, left).run()
        return watched.cause([])

    def _refusal(self, pull: Pull, human_only: bool = False) -> Landed | None:
        """本番ブランチ宛ての PR の証跡を読み直し、`pull` の HEAD が承認されていなければ止まった結果（承認されていれば None）。
        `human_only` なら担い手が MVV 判定の証跡を数えない（迂回の前提。#3506 の I9）。承認の後に HEAD が動けば、証跡は
        古い HEAD へのものになり止まる（#3661）。`approval.via: review` では証跡の代わりに GitHub のレビューの承認を読む
        （迂回・ruleset の無い早送りでも、今の head を承認したレビューの無い PR を入れない）。"""
        if pull.base_ref != self.production:
            return None
        if self._by_review():
            return self._review_refusal(pull, self.hub.pull(pull.number))
        got = trail.check(self.hub, pull.number, human_only)
        if got.pull.head_sha != pull.head_sha:
            return Landed(
                False, "refused", f"#{pull.number} の HEAD が {pull.head_sha[:12]} から {got.pull.head_sha[:12]} へ動いた。{REAPPROVE}"
            )
        if not got.judgment.approved:
            return Landed(False, "refused", f"#{pull.number} は承認されていない: {got.judgment.detail}。{REAPPROVE}")
        return None

    def _review_refusal(self, pull: Pull, now: Pull) -> Landed | None:
        """`approval.via: review`: 読み直した PR `now` の HEAD が `pull` から動いたか、write 以上の人の最新の承認のレビューが
        今の HEAD へのものでなければ止まった結果（承認されていれば None。10 系の `approval_of` と同じ読み方）。"""
        if now.head_sha != pull.head_sha:
            return Landed(
                False, "refused", f"#{pull.number} の HEAD が {pull.head_sha[:12]} から {now.head_sha[:12]} へ動いた。{REAPPROVE}"
            )
        if trail.review_commit(self.hub, pull.number) != pull.head_sha.lower():
            return Landed(
                False,
                "refused",
                f"#{pull.number} の HEAD {pull.head_sha[:12]} を承認した GitHub のレビューが無い（approval.via: review）。{REAPPROVE}",
            )
        return None

    def _approved_at(self, number: int, head: str) -> bool:
        """PR `number` の今の HEAD `head` が、設定の承認の受け方（`approval.via`）で承認されているか。"""
        if self._by_review():
            return trail.review_commit(self.hub, number) == head.lower()
        got = trail.read(self.hub, number)
        return got.pull.head_sha == head and got.judgment.approved

    def _blocker(self, pull: Pull, deadline: float, bypass: bool = False) -> Landed | None:
        """マージの前の停止条件を順に確かめ、止まった結果（進めてよければ None）。

        証跡の読み直し → 必須のチェックの待ち（時刻 `deadline` まで）→ 待ちの後の証跡の読み直し（本番ブランチ宛てでなければ
        読み直しは素通り）。
        """
        refused = self._refusal(pull, bypass)
        if refused is not None:
            return refused
        kind = self._watch(pull, deadline)
        if kind is not None:
            return Landed(False, kind, f"#{pull.number} の必須のチェックが通らない（{kind}。打ち直さずに人へ戻す）")
        return self._refusal(pull, bypass)

    def _file_at(self, ref: str, path: str) -> Optional[str]:
        """`ref` の時点のファイルの中身（無ければ None）。"""
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/contents/{quote(path)}", params={"ref": ref})
        except GitHubError as exc:
            if exc.status == NOT_FOUND:
                return None
            raise
        if not isinstance(got, dict) or got.get("encoding") != "base64":
            return None
        return base64.b64decode(text_of(got["content"])).decode("utf-8", errors="replace")

    def _version_rows(self, ref: str) -> list[tuple[str, str]]:
        """`ref` の時点の登録から求めた版数の候補（`version_rows`）。"""
        return version_rows(self.strategy, lambda path: self._file_at(ref, path))

    def _version_at(self, ref: str, rows: list[tuple[str, str]]) -> Optional[str]:
        return read_version(rows, lambda path: self._file_at(ref, path))

    def _production_facts(self, versioned: bool) -> tuple[dict, list[tuple[str, str]]]:
        """本番ブランチの HEAD と、その時点の版数（`check_production_pr` の `prod`）と、HEAD で求めた版数の候補。
        `versioned` が偽（版数を比べない方式・設定）なら、版数の保存先も版数のファイルも読まない（候補は空）。"""
        got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{quote(self.production, safe='')}")
        tip = text_of(got["commit"]["sha"]) if isinstance(got, dict) else ""
        rows = self._version_rows(tip) if tip and versioned else []
        return {"tip": tip, "version": self._version_at(tip, rows) if tip else None}, rows

    def _bump_facts(self, number: int, head: str, prod: dict, rows: list[tuple[str, str]]) -> dict:
        """PR の head と本番の HEAD `prod["tip"]` の比べ（10 系の `GhPort._bump_facts` と同じ値）。

        `bump` は版上げの PR か（`bump_of`）、`cut_version` は切った時点（head と本番の分岐点）の版数、`contains_tip` は
        head が本番の HEAD を含むか。`prod` は `_production_facts` の本番の HEAD と版数、`rows` は HEAD で求めた版数の候補
        （空なら版数のファイルを読まない）。
        """
        tip = prod["tip"]
        got = self.hub.rest("GET", f"/repos/{self.hub.repo}/compare/{tip}...{head}")
        cmp = got if isinstance(got, dict) else {}
        fork = cmp.get("merge_base_commit")
        fork_sha = text_of(fork.get("sha")) if isinstance(fork, dict) else ""
        cut = self._version_at(fork_sha, rows) if fork_sha else None
        optional = self.strategy.version_store["kind"] != "json"
        bump = bump_of(cut, self._version_at(head, rows), prod.get("version"), optional=optional)
        return {"number": number, "bump": bump, "cut_version": cut, "contains_tip": cmp.get("behind_by") == 0, "tip": tip}

    def _queue_rows(self, number: int, prod: dict, rows: list[tuple[str, str]]) -> list[dict]:
        """本番ブランチの merge queue の PR（自分を除く）と、版上げの PR か。全ページを読む（後ろの版上げを衝突の検査から外さない）。

        キューの外の、本番ブランチ宛てで開いており今の head が承認された版上げの PR（版上げでないと分かるものを除く）は
        `approved_release` の行で足す（10 系の `GhPort.queue` と同じ。`check_production_pr` の保留の検査。決定 18）。"""
        heads = merge_queue.heads(self.hub, self.production)
        out = [self._bump_facts(n, h, prod, rows) for n, h in heads if n != number]
        queued = {n for n, _ in heads}
        for n, head in self.hub.open_heads(self.production):
            if n == number or n in queued or self._bump_facts(n, head, prod, rows)["bump"] is False:
                continue
            if self._approved_at(n, head):
                out.append({"number": n, "bump": True, "approved_release": True})
        return out

    def _gate(self, pull: Pull) -> Gate:
        """本番の PR を入れる前の確かめ（ruleset とセクションの一致・マージの方式ごとの確かめ）。"""
        rules = self.hub.rule_facts(self.production)
        if rules is None:
            return Gate(Landed(False, "refused", f"本番ブランチ {self.production} の ruleset を読めない（比べずに入れない）"))
        mismatch = ruleset_mismatch(self.strategy, rules)
        if mismatch:
            return Gate(Landed(False, "refused", "セクションと ruleset が食い違う: " + json.dumps(mismatch, ensure_ascii=False)))
        landing = self.landing
        pr: Optional[dict] = None
        if landing.method != "direct":
            # 版数を比べるのは merge-queue で版数を持つ設定だけ。strict は HEAD を含むかだけを見て版数の保存先とファイルを読まず、
            # 版数を持たない設定（version_store が none）は版上げの PR が無く、キューと開いた PR も読まない
            bumps = landing.method == "merge-queue" and self.strategy.version_store["kind"] != "none"
            prod, rows = self._production_facts(bumps)
            pr = self._bump_facts(pull.number, pull.head_sha, prod, rows)
            queue = self._queue_rows(pull.number, prod, rows) if bumps else []
            why = check_production_pr(self.strategy, pr, prod, queue)
            if why is not None:
                return Gate(Landed(False, "refused", f"#{pull.number}: {why}"), rules=rules, pr=pr)
        # 本番ブランチが承認を求めるときだけ迂回する（承認数 0 なら普通のマージで入る。#3506 の I2b）
        return Gate(None, landing.admin and rules.approvals >= 1, rules, pr)

    def _merge(self, pull: Pull, deadline: float, method: str, bypass: bool = False, jump: bool = False) -> Landed:
        """停止条件（`_blocker`）を確かめた後に、承認済みのコミットを添えて方式 `method` でマージする。待ちは時刻 `deadline`
        （`land` が 1 度だけ決める）まで。`bypass` なら管理者の迂回で打つ。マージキューへ入れたら（`jump` なら先頭へ）、
        マージされるまで待つ。"""
        blocked = self._blocker(pull, deadline, bypass)
        if blocked is not None:
            return blocked
        if bypass:
            outcome: MergeOutcome = self.hub.merge_as_admin(pull.number, expect_head=pull.head_sha, method=method)
            return Landed(outcome.merged, outcome.kind, outcome.detail)  # 迂回のマーカーは _record が立てる
        outcome = self.hub.merge(pull.number, expect_head=pull.head_sha, method=method, require_method=True, jump=jump)
        if outcome.kind == MERGE_QUEUE_PENDING:
            return self._await_queue(pull, deadline)
        return Landed(outcome.merged, outcome.kind, outcome.detail)

    def _fast_forward(self, pull: Pull, deadline: float, tip: str) -> Landed:
        """ruleset の無い `strict`: 確かめた HEAD `tip` から本番ブランチが進んでいないときだけ、承認済みのコミットを早送りで入れる
        （10 系の `fast_forward_land` と同じ）。`merge`・`squash` はマージのコミットに trailer を残し、`squash` は PR を閉じる。"""
        blocked = self._blocker(pull, deadline)
        if blocked is not None:
            return blocked
        commit, head, repo = self.landing.commit, pull.head_sha, self.hub.repo
        if commit == "rebase":
            sha = head
        else:
            tree = text_of(self.hub.rest("GET", f"/repos/{repo}/git/commits/{head}")["tree"]["sha"])
            parents = [tip, head] if commit == "merge" else [tip]
            message = f"Land #{pull.number}\n\n{trailers(pull.number, head, tip)}\n"
            made = self.hub.rest("POST", f"/repos/{repo}/git/commits", {"message": message, "tree": tree, "parents": parents})
            sha = text_of(made["sha"])
        try:
            self.hub.rest("PATCH", f"/repos/{repo}/git/refs/heads/{self.production}", {"sha": sha, "force": False})
        except GitHubError as exc:
            if exc.status not in (HTTPStatus.CONFLICT, UNPROCESSABLE):
                raise
            return Landed(
                False,
                "refused",
                f"本番ブランチ {self.production} が確かめた HEAD から進んだ（早送りでない）。本番の PR を作り直し、{REAPPROVE}",
            )
        if commit == "squash":
            Publisher(self.hub).comment(pull.number, f"{sha} で本番ブランチへ入れた（squash）")
            self.hub.rest("PATCH", f"/repos/{repo}/pulls/{pull.number}", {"state": "closed"})
        return Landed(True, None, f"#{pull.number} を {sha[:12]} で本番ブランチへ早送りで入れた", production_sha=sha)

    def _record(self, pull: Pull, done: Landed) -> Landed:
        """迂回でマージした結果に、迂回の記録の URL か、書けなかった理由を足す（#3506 の I7・決定 7）。"""
        try:
            _wrote, url = trail.write_admin_record(self.hub, pull.number, pull.head_sha)
        except trail.TrailError as exc:
            return done._replace(admin=True, record_error=str(exc))
        return done._replace(admin=True, admin_record=url)

    def _queued_refusal(self, pull: Pull, now: Pull) -> Landed | None:
        """キューで待つ本番ブランチ宛ての PR の証跡を読み直し、承認が失効していれば止まった結果（承認のままなら None）。

        `now` はこのループの 1 回で読んだ PR で、読み直さずに判定へ渡す（ループの 1 回ごとの照会を 1 回に保つ）。
        待ちの間は commit status を付け直さない（読み直しのたびに書かない）。
        """
        if pull.base_ref != self.production:
            return None
        if self._by_review():
            return self._review_refusal(pull, now)
        got = trail.judged(self.hub, now)
        if got.pull.head_sha != pull.head_sha:
            return Landed(False, "refused", f"#{pull.number} の HEAD がキューの待ちの間に {got.pull.head_sha[:12]} へ動いた")
        if not got.judgment.approved:
            return Landed(False, "refused", f"#{pull.number} の承認がキューの待ちの間に失効した: {got.judgment.detail}")
        return None

    def _await_queue(self, pull: Pull, deadline: float) -> Landed:
        """マージキューへ入れた PR `pull` がマージされるまで、時刻 `deadline`（`clock` の値）まで読み直す。

        読み直すたびに、キューに残っているかと（本番ブランチ宛てなら）承認を確かめる。キューが外したら `not_ci` で
        すぐに止まり、承認が失効したらキューから外して `refused` で止まる（前の承認のままキューがマージしない）。
        予算の内にマージされなければ `ci_pending` で止まり、キューがマージした後の打ち直しを促す（`land` はマージ済みの
        本番ブランチ宛ての PR から、戻す PR へ再開する）。
        """
        number = pull.number
        while True:
            now = self.hub.pull(number)
            if now.merged:
                return Landed(True, None, f"#{number} をマージキューがマージした")
            refused = self._queued_refusal(pull, now)
            if refused is not None:
                self.hub.dequeue(number)
                return refused._replace(detail=f"{refused.detail}。マージキューから外した")
            if not self.hub.in_merge_queue(number):
                # 読み直しの間にキューがマージしていれば、外されたのではない
                if self.hub.pull(number).merged:
                    return Landed(True, None, f"#{number} をマージキューがマージした")
                return Landed(
                    False,
                    "not_ci",
                    f"#{number} はマージされずにマージキューから外された。merge_group の必須のチェック（または衝突）を"
                    "確かめて人へ戻す（打ち直してもキューへ入れ直すだけになる）",
                )
            left = deadline - self.clock()
            if left <= 0:
                return Landed(
                    False,
                    MERGE_QUEUE_PENDING,
                    f"#{number} はマージキューに入れたが、待ちの内にマージされない。"
                    "キューがマージした後に `ndf release-steps merge` を打ち直す（戻す PR・リリース記録・タグへ進む）",
                )
            self.sleep(min(QUEUE_POLL_SECONDS, left))

    def _open_sync(self) -> int | None:
        """本番ブランチからベースブランチへ戻す PR の番号（開いたものがあれば使い直し、無ければ作る。差分が無ければ None）。

        head は本番ブランチにする（リリースのブランチはマージの後に自動で消えることがある）。
        """
        owner = self.hub.repo.partition("/")[0]
        found = self.hub.rest(
            "GET",
            f"/repos/{self.hub.repo}/pulls",
            params={"state": "open", "head": f"{owner}:{self.production}", "base": self.base},
        )
        if isinstance(found, list) and found:
            return int(found[0]["number"])
        try:
            made = self.hub.rest(
                "POST",
                f"/repos/{self.hub.repo}/pulls",
                {"title": f"Sync: {self.production} を {self.base} へ戻す", "head": self.production, "base": self.base, "body": ""},
            )
        except GitHubError as exc:
            if exc.status != UNPROCESSABLE or NOTHING_TO_SYNC_TEXT not in str(exc):
                raise
            return None
        return int(made["number"])

    def _sync(self, pull: Pull, deadline: float, landed: Optional[str] = None) -> Landed:
        """本番ブランチへ入れた版上げを、ベースブランチへ戻す PR でマージする（マージ済みからの再開でも打つ）。
        同期の PR が要らないリリース方式（`route_decide.route_of` の `sync` が偽）では戻さない。"""
        merged_sha = landed or self.hub.pull(pull.number).merge_sha
        landed = merged_sha if merged_sha else None  # noqa: NDF501 ネットワーク: マージの sha を返さない応答は未着として扱う
        if not route_decide.route_of(self.strategy).sync:
            return Landed(True, None, f"#{pull.number} を本番ブランチへ入れた", production_sha=landed)
        number = self._open_sync()
        if number is None:
            return Landed(True, None, f"#{pull.number} をマージした（{self.base} へ戻す差分は無い）", production_sha=landed)
        synced = self._merge(self.hub.pull(number), deadline, SYNC_METHOD)
        detail = f"#{pull.number} をマージした。戻す PR: {synced.detail}"
        return synced._replace(sync_pr=number, detail=detail, production_sha=landed)

    def _resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed:
        """マージ済みの PR からの再開。本番ブランチ宛てならベースブランチへ戻し、記録が無ければ補う。

        `merge.actor: human` の設定では人のマージの記録を書く（書き手 `land`。#3672 の I12）。迂回の設定で、利用者の証跡が
        マージした HEAD への承認なら、迂回でマージしたとして迂回の記録を補う（I7）。"""
        if pull.base_ref != self.production:
            return Landed(True, None, f"#{number} は既にマージされている")
        if trail.trail_writer(self.strategy, merged_by_human=False) == trail.WRITER_LAND:
            trail.write_human_record(self.hub, number, pull.head_sha)
        synced = self._sync(pull, deadline)
        if self.landing.admin and (judged := trail.user_judgment_at(self.hub, number, pull.head_sha)) is not None and judged.approved:
            return self._record(pull, synced)
        return synced

    def land(self, number: int) -> Landed:
        """リリースの PR を入れる。本番ブランチへ入れた後（マージ済みからの再開を含む）は、要ればベースブランチへ戻す。

        待ちの予算（`budget`）はエントリポイントで 1 つの締め切りにし、本番の PR と戻す PR のチェックの待ち・マージキューの待ちで
        分け合う（PR ごとに取り直すと合計が予算の倍になり、workflow の時間の上限を超えて取り消される）。
        """
        deadline = self.clock() + self.budget
        pull = self.hub.pull(number)
        if pull.merged:
            return self._resume_merged(number, pull, deadline)
        route = route_decide.route_of(self.strategy)
        if route.release_pr and route.bump:
            # 版上げの PR はリリースのブランチから出す。`production_pr` ではスプリントの PR が本番の PR を兼ね、
            # 版数を持たない `merge_base_branch`（bump が偽）ではベースブランチから本番への PR そのものを入れる
            version_decide.version_of_branch(pull.head_ref)
        if pull.base_ref != self.production:
            return self._merge(pull, deadline, SYNC_METHOD)
        landing = self.landing
        if landing.actor == "human":
            return Landed(False, HUMAN_KIND, f"#{number} は人がマージする（設定の merge.actor が human）。人のマージを待つ")
        if not self.hub.token_trusted():
            return Landed(
                False,
                "refused",
                f"#{number} を {self.production} へ入れる token の書き手が write 以上の権限を持つ利用者でなく、マージの後の"
                "リリース記録が読まれない。利用者の token（workflow では secrets.NDF_RELEASE_TOKEN）でマージする",
            )
        gate = self._gate(pull)
        if gate.stop is not None:
            return gate.stop
        if landing.method == "strict" and gate.rules is not None and not gate.rules.present and gate.pr is not None:
            done = self._fast_forward(pull, deadline, gate.pr["tip"])
        else:
            jump = landing.method == "merge-queue" and gate.pr is not None and may_bump(gate.pr)
            done = self._merge(pull, deadline, landing.commit, gate.bypass, jump)
        if not done.merged:
            return done
        synced = self._sync(pull, deadline, done.production_sha)
        return self._record(pull, synced) if gate.bypass else synced
