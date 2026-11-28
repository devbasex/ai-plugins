"""送り先を確かめ、承認した文書をスプリントブランチへ反映する（#3702 の決定 1・11。issues/issue-3702-design-detail.md の「送り先の確かめ」と「処理の手順」）。

工程のステップ `design-sync`（`ndf progress units sync`）が打つ。push は手元の HEAD までのコミットをすべて公開するので、
承認の記録と合う送り先にだけ、新しく送るコミットを限って送る。取得と push の相手は確かめた URL で、リモート名を渡さない
（リモート名で送ると、確かめた後に設定が指す先へ送る）。URL は認証の値を含み得るので、理由には
`<所有者>/<リポジトリ>` と当たった条件だけを出す。

`destination_errors`・`outgoing_errors` は、この変更より前のスプリントの変換の反映も使う。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters import github
from ndf.adapters.git import REGULAR_MODES, Git, github_target, mask_urls, parse_status_z, repo_of_url
from ndf.contracts import result
from ndf.core import branch_strategy
from ndf.core.plan import files as plan_files
from ndf.core.plan import units_files
from ndf.progress import command, design_record, run_view, store
from ndf.progress.units_flow import DESIGN_SYNC_FILE
from ndf.progress.units_invoke import released_branches

# 反映する文書の名前（名前の番号の並びが課題）
_DOCUMENT = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-(?:plan\.units\.json|plan\.md|requirements\.md|design\.md|design-detail\.md)$")
_COMMIT_MESSAGE = "Docs: 承認した設計の文書を反映する（{head}）"

# `<所有者>/<リポジトリ>` を受けて、PR を読める hub（`pull(number)`）を返す
Hub = Callable[[str], Any]


class _Stop(Exception):
    """反映を止める。`kind` は停止理由の種類。"""

    def __init__(self, kind: str, detail: str, retry_after: Optional[float] = None) -> None:
        super().__init__(detail)
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


# --- 送り先と送るコミットの確かめ ----------------------------------------------------------------


def push_url(git: Git, repo: str, url: Optional[str] = None) -> tuple[Optional[str], list[str]]:
    """`repo` へ送る URL を確かめる。通れば（URL, []）、違えば（None, 理由）。URL を使う通信はしない。

    `url` を渡せば設定を読み直さず、同じ値を確かめる。
    """
    if url is None:
        urls = git.push_urls(git.remote())
        if len(urls) != 1:
            return None, [f"push の URL が {len(urls)} 件ある（1 件にする）"]
        url = urls[0]
    target = github_target(url)
    if target is None:
        return None, ["push の URL が GitHub の受ける形（https・ssh・scp）でない"]
    if target.secret:
        fix = "認証情報の無い URL を git remote set-url --push で設定し、認証は credential helper に任せる"
        return None, [f"push の URL（{target.repo}）が認証情報を含む。{fix}"]
    if target.repo.casefold() != repo.casefold():
        return None, [f"push の URL のリポジトリが違う（承認 {repo}、今 {target.repo}）"]
    if git.rewrites_again(url):
        detail = "insteadOf・pushInsteadOf の設定か、URL と同じ名前のリモートがある"
        return None, [f"push の URL（{target.repo}）を git がもう一度読み替える（{detail}）"]
    return url, []


def destination_errors(
    git: Git,
    record: Mapping[str, Any],
    context: Mapping[str, Any],
    machine_repo: str,
    url: Optional[str] = None,
    project: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[str], list[str]]:
    """確かめた URL と、送り先のリポジトリとブランチがゲートの記録とスプリントの状態に合わない点。合わない点があれば URL は None。

    `git` はスプリントの作業場所、`machine_repo` はステートマシンの `repo`（作成の時点のリモートの URL）。
    リリース先は設定 `project`（読んだ `.ndf/project.json`）で照らす。None なら作業場所の設定を読む。
    """
    repo = str(record["review"]["repo"])
    checked, errors = push_url(git, repo, url)
    machine = repo_of_url(machine_repo)  # 送らない値（取得の URL からユーザー名を除いた保存値）なので、送り先の形は求めない
    if machine is None or machine.casefold() != repo.casefold():
        now = "解析できない" if machine is None else machine
        errors.append(f"スプリントのリポジトリが違う（承認 {repo}、今 {now}）")
    branch = record["sprint_branch"] if "sprint_branch" in record else None
    if not branch:
        return None, [*errors, "承認の記録に送り先のブランチ（sprint_branch）が無い"]
    registered = context["branch"] if "branch" in context else None
    current = git.run("branch", "--show-current").strip()
    for label, now in (("文脈の branch", registered), ("作業場所の今のブランチ", current)):
        if now != branch:
            errors.append(f"送り先のブランチが違う（承認 {branch}、{label} {now or '無し'}）")
    try:
        released = released_branches(_strategy(git, project).section)
    except contracts.ContractError as exc:
        errors.append(f"ブランチ戦略の設定を読めない: {exc}")
    else:
        if branch in released:
            errors.append(f"送り先のブランチ {branch} は {command.PROJECT_FILE} の branch_strategy がリリース先に挙げている")
    return (None if errors else checked), errors


def _strategy(git: Git, project: Optional[Mapping[str, Any]] = None) -> branch_strategy.Strategy:
    if project is None:
        root = Path(git.run("rev-parse", "--show-toplevel").strip())
        project = command.ndf_settings_in(root, command.PROJECT_FILE)
    return branch_strategy.of_project(project)


def outgoing_errors(git: Git, tip: str, head: str, allowed: Sequence[str]) -> list[str]:
    """送り先の HEAD `tip` に無いコミット（`head` まで）のうち、送ってはいけない点。

    送ってよいのは 0 件か、`allowed` のファイルだけを変えた、マージでない 1 コミットである。`tip` は呼び手が読んだ値で、
    ここでは読み直さない。
    """
    commits = git.run("rev-list", "--parents", f"{tip}..{head}", "--").splitlines()
    if len(commits) > 1:
        shas = ", ".join(line.split()[0][:12] for line in commits)
        return [f"送り先に無いコミットが {len(commits)} 件ある（{shas}）"]
    if not commits:
        return []
    sha, *parents = commits[0].split()
    if len(parents) > 1:
        return [f"送り先に無いコミット {sha[:12]} はマージのコミットである"]
    out = git.run("diff-tree", "-r", "-z", "--root", "--no-commit-id", "--name-only", sha)
    others = sorted(set(filter(None, out.split("\0"))) - set(allowed))
    return [f"送り先に無いコミット {sha[:12]} が対象でないファイルを変える: {', '.join(others)}"] if others else []


# --- 反映 ------------------------------------------------------------------------------------


class _Sync(NamedTuple):
    """1 回の反映で変わらない値。`url` は確かめた送り先、`head` は承認の記録の HEAD。"""

    git: Git
    root: Path
    url: str
    branch: str
    record: Mapping[str, Any]
    saved: Path
    listings: dict[str, dict[str, tuple[str, str, str]]]  # 読んだ一覧。キーは完全な SHA なので、中身は変わらない

    @property
    def head(self) -> str:
        return str(self.record["review"]["head"])


def sync_approved(st: store.Store, machine_id: str, cwd: Path, hub: Hub) -> dict[str, Any]:
    """承認した文書をスプリントブランチへ反映して送る。反映は文脈の `worktree` で行う。"""
    try:
        done = _import_documents(st, machine_id, cwd, hub)
    except _Stop as exc:
        return result.stopped(command.TOOL, exc.kind, exc.detail, retry_after=exc.retry_after, metrics={"machine_id": machine_id})
    return result.result(command.TOOL, result.OK, done, metrics={"machine_id": machine_id})


def _import_documents(st: store.Store, machine_id: str, cwd: Path, hub: Hub) -> str:
    view, machine_repo = _read_machine(st, machine_id)
    record = design_record.approved_of(view)
    if record is None:
        raise _Stop("precondition", "設計承認ゲートの承認の記録が無いので、反映しない")
    context = view.context()
    if not context.get("worktree"):
        raise _Stop("precondition", "スプリントの作業場所（文脈の worktree）が無い")
    location = cwd / str(context["worktree"])
    git = Git(location)
    url, errors = destination_errors(git, record, context, machine_repo)
    if url is None:
        raise _Stop("refused", "送り先が承認の記録と違うので、反映しない: " + "; ".join(errors))
    root = Path(git.run("rev-parse", "--show-toplevel").strip())
    saved = run_view.work_root(st.root, machine_id) / DESIGN_SYNC_FILE
    # パスは根からの相対なので、以後の git は根で打つ
    s = _Sync(Git(root), root, url, str(record["sprint_branch"]), record, saved, {})

    tip: Optional[str] = None
    commit = _recorded_commit(s)
    if commit is not None:
        tip = _tip(s)
        if s.git.is_ancestor(commit, tip):
            return f"承認した文書は {s.branch} に届いている"
        if s.git.is_ancestor(commit, "HEAD"):
            _ensure_head(s)
            return _push_documents(s, _documents(s), tip)
    if s.git.commit_or_none(s.head) is not None and s.git.is_ancestor(s.head, "HEAD"):
        # 手元は古いことがあるので、届いたかは送り先の HEAD で決める
        tip = tip if tip is not None else _tip(s)
        if s.git.is_ancestor(s.head, tip):
            return f"{s.branch} は承認した HEAD を含むので、反映するものは無い"
    _check_merged(s, hub)
    _ensure_head(s)
    files = _documents(s)
    if not files:
        return "承認した HEAD に反映する文書が無い"
    tip, local = _tip_after_checks(s, files, tip)
    _commit(s, files, local)
    return _push_documents(s, files, tip)


def _read_machine(st: store.Store, machine_id: str) -> tuple[run_view.View, str]:
    with store.transaction(st) as conn:
        current = command.read_current(conn, machine_id)
        meta = store.read_machine(conn, machine_id)
    assert meta is not None  # read_current が無いステートマシンを断る
    return run_view.View(machine_id, current.rev, current.snapshot, current.raw, current.events), str(meta["repo"])


def _recorded_commit(s: _Sync) -> Optional[str]:
    """今の承認の HEAD で作り、手元にある文書のコミット。"""
    held = plan_files.read_json(s.saved)
    if held is None or held.get("record_head") != s.head or "commit" not in held:
        return None
    return s.git.commit_or_none(str(held["commit"]))


def _tip(s: _Sync) -> str:
    """確かめた URL から読んだ、送り先のブランチのHEAD。取得先の URL と remote-tracking ref は使わない。"""
    done = s.git.fetch(s.url, f"refs/heads/{s.branch}")
    if done.returncode != 0:
        raise _Stop("precondition", f"送り先の {s.branch} を読めない: {mask_urls(done.stderr.strip())}")
    return s.git.rev_parse("FETCH_HEAD")


def _ensure_head(s: _Sync) -> None:
    """承認した HEAD のコミットが手元に無ければ、確かめた URL から取得する。"""
    if s.git.commit_or_none(s.head) is not None:
        return
    done = s.git.fetch(s.url, s.head)
    if done.returncode != 0 or s.git.commit_or_none(s.head) is None:
        raise _Stop("precondition", f"承認した HEAD {s.head} が送り先から読めない: {mask_urls(done.stderr.strip())}")


def _check_merged(s: _Sync, hub: Hub) -> None:
    """記録の PR が、承認した HEAD のまま文書のマージ先へマージ済みかを確かめる。"""
    review = s.record["review"]
    number, base = review["pr"], str(review["base"])
    if number is None:
        raise _Stop("precondition", f"承認の記録に PR が無く、承認した HEAD {s.head} が {s.branch} に無い")
    try:
        pull = hub(str(review["repo"])).pull(int(number))
    except github.GitHubError as exc:
        kind = "transient" if exc.passing else "precondition"
        raise _Stop(kind, f"PR #{number} を読めない: {exc}", exc.retry_after) from None
    if not pull.merged:
        raise _Stop("precondition", f"PR #{number} がマージされていない")
    try:
        strategy = _strategy(s.git)
        # 設計 PR の head がスプリントブランチ自身なら、スプリントの PR と同じマージ先である
        target = strategy.pr_target("sprint") if pull.head_ref == s.branch else strategy.pr_target("doc", s.branch)
    except contracts.ContractError as exc:
        raise _Stop("precondition", f"ブランチ戦略の設定を読めない: {exc}") from None
    now = (("head", s.head, pull.head_sha), ("base", base, pull.base_ref), ("base がマージ先の設定どおり", target, base))
    wrong = [f"{label}（承認 {want}、今 {got}）" for label, want, got in now if want != got]
    if wrong:
        raise _Stop("refused", f"PR #{number} が承認の記録と違う: {' / '.join(wrong)}")


def _listing(s: _Sync, commit: str) -> dict[str, tuple[str, str, str]]:
    """コミットの `issues/` の直下の項目（(モード, 種類, id)）。`commit` は完全な SHA で、同じ SHA は 1 回だけ読む。"""
    if commit not in s.listings:
        found = units_files.Tree(s.root, commit).listing()
        s.listings[commit] = {path: entry for path, entry in found.items() if entry is not None}
    return s.listings[commit]


def _documents(s: _Sync) -> list[str]:
    """承認した HEAD にある、記録の課題の単位のファイル・実装計画・要求と設計の文書。"""
    issues = {int(n) for n in s.record["targets"]["issues"]}
    found = []
    for path, (mode, _, _) in sorted(_listing(s, s.head).items()):
        named = _DOCUMENT.match(path)
        if named is None or not issues & {int(n) for n in named.group(1).split("-")}:
            continue
        if mode not in REGULAR_MODES:
            raise _Stop("refused", f"承認した HEAD の {path} が通常のファイルでない")
        found.append(path)
    return found


def _differing(s: _Sync, commit: str, files: Sequence[str]) -> list[str]:
    """`commit` の中身が承認した HEAD と違うファイル。"""
    now, approved = _listing(s, commit), _listing(s, s.head)
    return [path for path in files if now.get(path) != approved.get(path)]


def _tip_after_checks(s: _Sync, files: Sequence[str], tip: Optional[str]) -> tuple[str, str]:
    """ファイルを書き換える前に作業場所を確かめ、送り先の HEAD と手元の HEAD の SHA を返す。"""
    dirty = parse_status_z(s.git.run("status", "--porcelain", "-z", "--untracked-files=all", "--ignored", "--", *files))
    if dirty:
        raise _Stop("precondition", f"対象のファイルに未コミットの変更がある: {', '.join(sorted(dirty))}")
    if s.git.commit_or_none("MERGE_HEAD") is not None:
        raise _Stop("precondition", "作業場所がマージの途中である。終えるか中止してから打ち直す")
    local = s.git.rev_parse("HEAD")
    base, ours = _listing(s, s.git.merge_base(local, s.head)), _listing(s, local)
    conflicts = [path for path in _differing(s, local, files) if ours.get(path) != base.get(path)]
    if conflicts:
        raise _Stop("base_conflict", f"{s.branch} の側も変えていて、承認した中身と違う: {', '.join(conflicts)}")
    tip = tip if tip is not None else _tip(s)
    _check_outgoing(s, files, tip, local)
    return tip, local


def _check_outgoing(s: _Sync, files: Sequence[str], tip: str, head: str) -> None:
    found = outgoing_errors(s.git, tip, head, files)
    if found:
        raise _Stop("refused", "手元のコミットを公開できないので送らない: " + "; ".join(found))


def _commit(s: _Sync, files: Sequence[str], local: str) -> None:
    """承認した HEAD の中身を、対象のファイルだけのコミットにする。コミットの直後に中断した打ち直しは、コミットしない。"""
    if _differing(s, local, files):
        s.git.checkout_paths(s.head, files)
        s.git.run("commit", "-q", "-m", _COMMIT_MESSAGE.format(head=s.head), "--", *files)
    plan_files.write_json(s.saved, {"record_head": s.head, "commit": s.git.rev_parse("HEAD")})


def _push_documents(s: _Sync, files: Sequence[str], tip: str) -> str:
    """HEAD の中身と送るコミットを照らし、照らした SHA を送って、届いたことを確かめる。"""
    sha = s.git.rev_parse("HEAD")
    wrong = _differing(s, sha, files)
    if wrong:
        raise _Stop("refused", f"コミットの中身が承認した HEAD と違うので送らない: {', '.join(wrong)}")
    _check_outgoing(s, files, tip, sha)
    refspec = f"{sha}:refs/heads/{s.branch}"
    done = s.git.attempt("push", "--no-follow-tags", "--recurse-submodules=no", "--", s.url, refspec)
    if done.returncode != 0:
        hint = f"文書のコミットを送り先の {s.branch} のHEADの上へ載せ直して（git rebase。マージのコミットは作らない）打ち直す"
        raise _Stop("precondition", f"{s.branch} への push が拒否された。{hint}: {mask_urls(done.stderr.strip())}")
    if not s.git.is_ancestor(sha, _tip(s)):
        raise _Stop("precondition", f"push の後の {s.branch} に文書のコミット {sha} が無い")
    return f"承認した文書を {s.branch} へ反映した（{sha}）"
