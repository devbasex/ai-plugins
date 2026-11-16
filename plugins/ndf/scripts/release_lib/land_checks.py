"""land_checks.py: land が入れる前に head の検査がそろったかを確かめる（#3672 の I5）。

GitHub の必須の検査を通らない取り込み（ruleset の無い `strict` の参照の更新・承認なしで入れるスプリントの PR）で使う。
`port` は `land.GhPort`（`_rest`・`_show`・`_git`・`_fetch` を使う）。判定は止める側へ倒す: 読めない事実は検査が未完了とみなす。

- `checks_failing`: check run と commit status（全ページ）・宣言の検査名から、そろっていなければ理由の文を返す
- `required_unmet`: 管理者の迂回の前に、宛先が要求するチェックが先端に現れ、発行元の指定があればその発行元で通ったかを
  `checks_failing` と同じ読みで確かめる
- `no_checks_reason`: 検査が 0 件のとき、PR の head で動く workflow か、Actions 以外の CI があれば待つ理由を返す
"""

from __future__ import annotations

from release_lib.workflow_trigger import BASE_DEFINED, HEAD_DEFINED, PullEvent, runs_on_pull_head

CHECKS_OK = ("success", "skipped", "neutral")
_PAGE = 100


def _keyed_pages(port, path: str, key: str) -> list | None:
    """`path` を `per_page=100` で全ページ読み、`key` の配列をつなぐ（既定の 30 件で切らない）。読めなければ None。"""
    out: list = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        d = port._rest(f"{path}{sep}per_page={_PAGE}&page={page}")
        rows = (d.get(key) or []) if isinstance(d, dict) else None
        if not isinstance(rows, list):
            return None
        out += rows
        total = d.get("total_count")
        if len(rows) < _PAGE or (isinstance(total, int) and len(out) >= total):
            return out
        page += 1


def _status_rows(port, sha: str) -> list | None:
    """`sha` の commit status（context ごとの最新）を全ページ読む（承認の証跡の status を含む）。読めなければ None。"""
    rows = _keyed_pages(port, f"repos/{port.repo}/commits/{sha}/status", "statuses")
    return None if rows is None else [c for c in rows if isinstance(c, dict)]


def statuses(port, sha: str) -> list | None:
    """`sha` の commit status を全ページ読み、承認の証跡の status を除く。読めなければ None。"""
    from release_lib import approval_trail as trail

    rows = _status_rows(port, sha)
    # 承認の証跡の status は検査でなく承認の印（承認の免除の経路では付かない）。reviewed.wait_ci_except と同じく除く
    return None if rows is None else [c for c in rows if c.get("context") != trail.STATUS_CONTEXT]


def declared_checks(root) -> list[str]:
    """CI テストの宣言（`test.ci.only: true`）が名指す検査の名前。宣言が無い・名指しが無ければ空。"""
    import project_decl
    import test_strategy

    decl = project_decl.read_project_decl(root)
    return test_strategy.ci_checks(decl) if test_strategy.declares_ci_only(decl) else []


def checks_failing(port, sha: str, pull: dict | None = None, required: tuple = ()) -> str | None:
    """head の検査がすべて成功なら None、そろわなければ理由の文。宣言の検査名がまだ登録されていなければ未完了とする。
    `required` は管理者の迂回で飛ばす宛先の必須のチェック（`RuleFacts.required`）で、同じ 1 回の読みで発行元まで照らす（`required_unmet`）。"""
    import gh_checks
    from release_lib import approval_trail as trail

    raw = gh_checks.fetch_check_runs(port.repo, sha, empty_ok=True)
    rows = _status_rows(port, sha)
    if raw is None or rows is None:
        return "検査の結果を読めない"
    folded = gh_checks.fold_check_runs(raw)
    unmet = required_unmet(port, sha, required, raw, folded, rows)
    if unmet:
        return unmet
    got = [c for c in rows if c.get("context") != trail.STATUS_CONTEXT]
    runs = [(str(r.get("name") or ""), gh_checks.run_result(r)) for r in folded]
    runs += [(str(c.get("context") or ""), str(c.get("state") or "")) for c in got]
    names = {name for name, _ in runs}
    absent = [c for c in declared_checks(port.root) if c not in names]
    if absent:
        return f"CI テストの宣言の検査 {', '.join(absent)[:200]} がまだ始まっていない。始まるのを待って打ち直してください"
    if not runs:
        return no_checks_reason(port, sha, pull or {})
    bad = [f"{name}={res}" for name, res in runs if res not in CHECKS_OK]
    return ", ".join(bad)[:300] or None


def required_unmet(port, sha: str, required: tuple, raw: list, folded: list, rows: list) -> str | None:
    """管理者の迂回の前に、宛先が要求するチェック `required`（名前, 発行元）を先端 `sha` の読んだ結果で照らす。満たせば None、
    満たさなければ理由の文。迂回は必須のチェックも飛ばすため、発行元（`integration_id`・`app_id`）の指定があれば、そのアプリの
    check run か、そのアプリの bot が付けた commit status で通ったかを確かめる（同じ名前の別の発行元の成功で通さない。#3506）。
    指定の無い名前は先端に現れたかだけを見る（承認の証跡の status も必須にされうるため除かない。成否は `checks_failing` が見る）。
    bot の付けた commit status は、check run で満たせない発行元の指定があるときだけ読む。読めなければ止める側へ倒す。"""
    import check_sources as cs

    apps = sorted({(n, a) for n, a in required if a is not None})
    unmet = [(n, a) for n, a in apps if not cs.latest_passed([r for r in raw if r.get("name") == n], a)]
    if unmet:
        listed = _status_list(port, sha)
        if listed is None:
            return "宛先の必須のチェックの commit status を読めない"
        cache: dict = {}
        unmet = [(n, a) for n, a in unmet if not cs.status_passed(listed, n, a, lambda login: cs.bot_app(port._rest, login, cache))]
    if unmet:
        return "宛先の必須のチェックが指定の発行元で通っていない: " + ", ".join(f"{n}（アプリ {a}）" for n, a in unmet)[:200]
    seen = {str(r.get("name") or "") for r in folded} | {str(c.get("context") or "") for c in rows}
    absent = sorted({n for n, a in required if a is None and n not in seen})
    if absent:
        return f"宛先の必須のチェック {', '.join(absent)[:200]} がまだ始まっていない。始まるのを待って打ち直してください"
    return None


def _status_list(port, sha: str) -> list | None:
    """`sha` の commit status の全件（書き手 `creator` を含む。全ページ）。読めなければ None。"""
    out: list = []
    page = 1
    while True:
        d = port._rest(f"repos/{port.repo}/commits/{sha}/statuses?per_page={_PAGE}&page={page}")
        if not isinstance(d, list):
            return None
        out += [s for s in d if isinstance(s, dict)]
        if len(d) < _PAGE:
            return out
        page += 1


def pull_changed_files(port, base: str | None, sha: str) -> list[str] | None:
    """PR の変更ファイル（宛先の origin と head の三点の差分。GitHub の PR の変更ファイルと同じ）。読めなければ None。"""
    if not base:
        return None
    return diff_names(port, f"origin/{base}...{sha}")


def diff_names(port, *revs: str) -> list[str] | None:
    """差分のファイル名。ASCII でないパスを引用符と 8 進の形にしないよう core.quotePath を切り、改行を含むパスも分けられるよう NUL で区切る。
    名前の変更は元と先の両方を数える（`--no-renames`。変更の検出で元の名前が落ちると、元の側の paths の絞り込みを見落とす）。"""
    r = port._git("-c", "core.quotePath=false", "diff", "--no-renames", "--name-only", "-z", *revs)
    return [f for f in r.stdout.split("\0") if f] if r.returncode == 0 else None


def push_files(port, head_ref: str | None, sha: str, pull_files) -> list[str] | None:
    """head を作った直近の push の変更ファイル（GitHub が push の paths に照らす before..after の二点の差分）。
    直近の push が `sha` を作ったと確かめられない・比較元を読めないときは None（paths の絞り込みは動くとみなす）。
    新しいブランチの push（before が 0）は、GitHub が押したコミットの最も古い祖先の親と比べるため、PR の変更ファイル（`pull_files()`）で代える。"""
    from urllib.parse import quote

    if not head_ref:
        return None
    # 種類で絞らない（強制 push・ブランチの作成も head を作る）。最新の 1 件が `sha` を作っていなければ比較元を決めない
    d = port._rest(f"repos/{port.repo}/activity?ref={quote('refs/heads/' + head_ref, safe='/')}&per_page=1")
    last = d[0] if isinstance(d, list) and d and isinstance(d[0], dict) else None
    if last is None or last.get("after") != sha or not last.get("before"):
        return None
    before = str(last["before"])
    if not before.strip("0"):
        return pull_files()
    if port._git("cat-file", "-e", f"{before}^{{commit}}").returncode != 0:
        port._fetch(before)
    return diff_names(port, before, sha)


def workflows(port) -> list | None:
    """有効・無効を問わず workflow の一覧を全ページ読む（既定の 1 ページ 30 件で切ると、後ろのページの PR の CI を見落とす）。読めなければ None。"""
    return _keyed_pages(port, f"repos/{port.repo}/actions/workflows", "workflows")


def external_ci(port, refs: list[str]) -> list[str] | None:
    """`refs`（本番ブランチの先端・head の親）に、GitHub Actions 以外が付けた検査（app の check suite か commit status）の名前。
    外部の CI（CircleCI・Jenkins など）は workflow の一覧に現れないため、近いコミットに結果を残しているかで有無を確かめる。読めなければ None。"""
    found: list[str] = []
    for ref in refs:
        suites = _keyed_pages(port, f"repos/{port.repo}/commits/{ref}/check-suites", "check_suites")
        got = statuses(port, ref)
        if suites is None or got is None:
            return None
        for s in suites:
            slug = str(((s or {}).get("app") or {}).get("slug") or "")
            if slug and slug != "github-actions" and (s.get("latest_check_runs_count") or 0) > 0:
                found.append(slug)
        found += [str(c.get("context") or "") for c in got]
    return sorted(set(found))


def no_checks_reason(port, sha: str, pull: dict) -> str | None:
    """検査が 1 件も無いときの理由。PR の head で動く有効な workflow も、Actions 以外の CI も無ければ None（検査なしで通す）。
    本番への push・schedule でだけ動く workflow と、絞り込み（branches・paths）でこの PR に当たらない workflow は
    PR の head に検査を作らないため数えない。動く workflow か外部の CI があれば push の直後で未開始とみなして止める。照会は検査が 0 件のときだけ打つ。"""
    rows = workflows(port)
    if rows is None:
        return "検査がまだ 1 件も無く、workflow の一覧も読めない"
    active = [str(w.get("path") or "") for w in rows if isinstance(w, dict) and w.get("state") == "active"]
    show = port._show(sha)
    cache: dict = {}  # 変更ファイルは paths の絞り込みに当たったときだけ 1 回読む

    def files() -> list[str] | None:
        if "pull" not in cache:
            cache["pull"] = pull_changed_files(port, pull.get("base"), sha)
        return cache["pull"]

    def pushed() -> list[str] | None:
        if "push" not in cache:
            cache["push"] = push_files(port, pull.get("head_ref"), sha, files)
        return cache["push"]

    event = PullEvent(pull.get("head_ref"), pull.get("base"), files, pushed)
    tip = pull.get("tip") or ""
    show_tip = port._show(tip) if tip else None

    def runs(p: str) -> bool:
        if not p:  # パスを読めない workflow も動くとみなす
            return True
        head = show(p)
        if runs_on_pull_head(head, event, HEAD_DEFINED):
            return True
        # pull_request_target は宛先の先端の定義で起動を決める（PR の head の書き換えで起動の条件を外させない）。先端が無ければ head の定義で数え、
        # 先端に定義が無ければこの種類では動かない
        if show_tip is None:
            return runs_on_pull_head(head, event, BASE_DEFINED)
        text = show_tip(p)
        return text is not None and runs_on_pull_head(text, event, BASE_DEFINED)

    # 名前が空でも数に入れる（文字列の真偽で判定しない）
    on_head = [p or "（パス不明）" for p in active if runs(p)]
    if on_head:
        return f"検査がまだ 1 件も無い（PR の head で動く workflow {', '.join(on_head)[:200]} がある。始まるのを待って打ち直してください）"
    parent = port._git("rev-parse", "--verify", "--quiet", f"{sha}^").stdout.strip()
    refs = [r for r in dict.fromkeys([pull.get("tip") or "", parent]) if r]
    if not refs:
        return "検査がまだ 1 件も無く、外部の CI の有無を確かめるコミット（本番ブランチの先端・head の親）を読めない"
    external = external_ci(port, refs)
    if external is None:
        return "検査がまだ 1 件も無く、外部の CI の有無を読めない"
    if external:
        return f"検査がまだ 1 件も無い（近いコミットに Actions 以外の CI {', '.join(external)[:200]} がある。始まるのを待って打ち直してください）"
    return None
