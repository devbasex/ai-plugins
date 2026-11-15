"""main へのマージの後に、版上げの PR を作り・進め・置き換え、必須のチェックの後に照合付きでマージする（CD が打つ。#3572 の決定 7）。

取り込み方の行が `github-flow` のときだけ働き、ほかの行では何も読まずに `not_applicable` を返す（今の行はリリースブランチで
バージョンを上げる。決定 16）。バージョンの読み書きはバージョンの置き場（`version_store`。決定 22）を介し、`plugins` を直接呼ばない。

main の ruleset は PR を通さない push を断るため、版上げのコミットは `release/v<版>` を head にした版上げの PR で main へ入れる。
動きは `bump_decide.action` が main の先端の状態から決める。範囲は「前の版上げのコミットの起点..main の先端」で、上げ幅と
CHANGELOG の版の節（決定 11）はこの範囲にマージされた PR から決める。

| 動き | すること |
| --- | --- |
| `PUBLISH` | 何も書かずに `published` を返す（workflow がタグと GitHub Release を作る） |
| `NOTHING` | 何も書かずに `not_applicable` を返す（I11） |
| `CREATE` | main の先端から `release/v<版>` を作り、版のファイルと CHANGELOG の版の節だけを変えたコミットを足して PR を作る |
| `UPDATE` | 開いた版上げの PR へ main を取り込むコミットと、版の節を組み直したコミットを足す（強制 push を使わない） |
| `REPLACE` | 古い版の版上げの PR（同じリポジトリの head で、差分が版上げだけのもの。違えば止まる）を理由つきで閉じてブランチを消し、`CREATE` へ進む |

作るか進めた後は、main の先端が head の祖先のときだけ `land.Lander.land_bump` で入れる（マージの方式があれば迂回せずに方式で。決定 20）（祖先でなければ `reused`。次の
main への push の `bump` が進める）。同じ main の先端で 2 回打っても版上げの PR は 1 本で、差分が同じならコミットを足さない（I8）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, Pull, text_of
from ndf.domains.release import bump_decide, changelog_decide, gather, land, materials_decide, tagging, version_decide, version_store
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release.version_store import VersionStore


class BumpError(Exception):
    """版上げの前提が無い（開いた版上げの PR が 2 本以上・起点が main の祖先でない・同じ名前のブランチが別の中身を持つ）。"""


class BumpConflict(Exception):
    """開いた版上げの PR へ main を取り込めない（衝突）。"""


class Bumped(NamedTuple):
    """版上げの結果。止まったら `landed` が止まった結果を持つ。"""

    state: str
    version: str | None = None
    level: str | None = None
    pr: int | None = None
    landed: land.Landed | None = None
    # `published` のときにタグを打つコミット（直前の版上げの PR のマージ。無ければ main の先端）
    revision: str | None = None


class _Tip(NamedTuple):
    """main の先端の単位の名前 → バージョン（無ければ None）、タグを待つ単位のタグが origin にあるか、
    タグを待つ先頭の単位のバージョン（`shown`）、タグを打つコミット（`revision`）、最後のバージョン上げのコミットが
    キューで追い越されたか（`overtaken`。真なら範囲の起点をそのコミットの起点にして上げ直す）。"""

    versions: dict[str, str | None]
    tagged: bool
    shown: str | None
    revision: str
    overtaken: bool = False


class _Plan(NamedTuple):
    """範囲にマージされた PR の番号・最も大きい上げ幅・版を上げる予定（出し済みの版より大きく繰り上げたもの）。"""

    numbers: list[int]
    level: str
    planned: list[version_decide.Planned]


def allowed_paths(store: VersionStore, decl: decl_mod.Declaration) -> frozenset[str]:
    """バージョン上げの PR が変えてよいパス（バージョンの置き場・CHANGELOG・手順の書き先）。"""
    paths = {*store.paths(), decl.changelog}
    for step in decl.for_stage(version_decide.PRODUCTION):
        paths |= set(step.writes)
    return frozenset(paths)


def write_bump(
    store: VersionStore,
    decl: decl_mod.Declaration,
    planned: Sequence[version_decide.Planned],
    pulls: Sequence[materials_decide.PullFact],
    today: str,
    channel: str = version_decide.PRODUCTION,
) -> frozenset[str]:
    """バージョンと（本番チャネルなら）CHANGELOG のバージョンの節を書き、チャネルの手順を流す。書いたパスを返す。

    書く作業場所は置き場の `root`。CHANGELOG の見出しは単位の名前。
    """
    root = store.root
    units = {u.name: u for u in store.units()}
    entries = [changelog_decide.Entry(p.number, p.title) for p in pulls]
    log_path = root / decl.changelog
    written: set[str] = set()
    for p in planned:
        written |= set(store.write(units[p.plugin], p.next))
        if channel == version_decide.PRODUCTION and log_path.is_file():
            new = changelog_decide.block(p.plugin, p.next, today, entries)
            log_path.write_text(changelog_decide.insert(log_path.read_text(encoding="utf-8"), p.plugin, p.next, new), encoding="utf-8")
            written.add(decl.changelog)
    for step in decl.for_stage(channel):
        decl_mod.run_step(root, step, planned[0].next)
        written |= set(step.writes)
    return frozenset(written)


def planned_for(root: Path, hub: GitHub, number: int, production: str, store: VersionStore) -> str | None:
    """本番の PR の予定のバージョン（PR のラベルと main のバージョンから。マージ済みはマージの第 1 親から）。読めなければ None。"""
    try:
        units = store.units()
        if not units:
            return None
        pull = hub.pull(number)
        level = bump_decide.level_of(gather.labels_of(hub, number))
        repo_git = git_mod.Git(root)
        if pull.merged and pull.merge_sha:
            ref = f"{pull.merge_sha}^1"
        else:
            gather.fetch(repo_git, production)
            ref = f"{git_mod.ORIGIN}/{production}"
        current = store.read(repo_git, ref, units[0])
        return bump_decide.planned(current, level) if current is not None else None
    except (git_mod.GitError, version_store.VersionStoreError, version_decide.VersionError):  # noqa: NDF401 外部コマンド: 予定の版は表示だけで、確定の版は confirm が読む
        return None


@dataclass(frozen=True)
class Bumper:
    """1 つのリポジトリの版上げ。`root` は main を取り出した作業場所、`today` は UTC の日付。"""

    root: Path
    hub: GitHub
    lander: land.Lander
    production: str
    today: str
    # 取り込み方の行とバージョンの置き場（None なら `root` の宣言から読む）
    flow: str | None = None
    store: VersionStore | None = None

    @property
    def _git(self) -> git_mod.Git:
        return git_mod.Git(self.root)

    @property
    def _flow(self) -> str:
        return self.flow if self.flow is not None else decl_mod.flow_of(self.root)

    @property
    def _store(self) -> VersionStore:
        return self.store if self.store is not None else version_store.of(self.root, decl_mod.load(self.root), self._flow)

    @property
    def _tip_ref(self) -> str:
        return f"{git_mod.ORIGIN}/{self.production}"

    def _tags(self) -> set[str]:
        return set(gather.remote_tags(self._git))

    def _last_bump(self, tip: str) -> str | None:
        """main の祖先にある最新の版上げのコミット（起点の行を持つ。無ければ None）。"""
        found = self._git.run("log", "-1", "--format=%H", f"--grep=^{bump_decide.ORIGIN_LINE}", git_mod.END_OF_OPTIONS, tip).strip()
        return found if found else None

    def _origin(self, tip: str) -> str:
        """範囲の起点（main の祖先にある最新の版上げのコミットの起点。無ければ main の版のタグ、どちらも無ければ根のコミット）。"""
        repo_git = self._git
        last = self._last_bump(tip)
        if last is not None:
            origin = bump_decide.origin_of(repo_git.run("log", "-1", "--format=%B", git_mod.END_OF_OPTIONS, last))
            if origin is None or not repo_git.is_ancestor(origin, tip):
                raise BumpError(f"版上げのコミット {last[:12]} の起点 {str(origin)[:12]} が {self._tip_ref} の祖先でない")
            return origin
        store = self._store
        units = store.units()
        current = store.read(repo_git, tip, units[0]) if units else None
        if current is not None:
            tag = version_decide.tag_of(units[0].name, current)
            if repo_git.fetch(git_mod.ORIGIN, f"+refs/tags/{tag}:refs/tags/{tag}").returncode == 0:
                return repo_git.rev_parse(f"refs/tags/{tag}^{{commit}}")
        return repo_git.run("rev-list", "--max-parents=0", git_mod.END_OF_OPTIONS, tip).split()[-1]

    def _span(self) -> gather.Range:
        """範囲（前の版上げのコミットの起点..main の先端）。main を取り込み、先端を取り出す。"""
        repo_git = self._git
        gather.fetch(repo_git, self.production)
        repo_git.run("switch", "--quiet", "--detach", self._tip_ref)
        tip = repo_git.rev_parse(self._tip_ref)
        return gather.Range(self._origin(tip), tip)

    def _bump_merge(self, tip: str) -> str | None:
        """main の先端 `tip` の祖先にある、直前の版上げの PR のマージコミット（無ければ None）。"""
        last = self._last_bump(tip)
        if last is None:
            return None
        chain = self._git.run("rev-list", "--first-parent", "--ancestry-path", git_mod.END_OF_OPTIONS, f"{last}..{tip}").split()
        return chain[-1] if chain else None

    def _changed(self, span: gather.Range) -> list[str]:
        """範囲で変わったファイル。前の版上げの PR のマージ（M）が範囲にあれば、その版上げ自身の変更を数えない
        （起点..M の第 1 親 と M..main の先端 の和）。"""
        repo_git = self._git
        merged = self._bump_merge(span.until)
        if merged is None:
            return repo_git.changed_files(span.since, span.until)
        before = repo_git.changed_files(span.since, f"{merged}^1")
        after = repo_git.changed_files(merged, span.until)
        return sorted(set(before) | set(after))

    def _open_bump(self) -> bump_decide.OpenBump | None:
        """main 宛ての開いた版上げの PR（head が `release/v` で始まるもの）。2 本以上なら `BumpError`。"""
        rows = self.hub.rest(
            "GET", f"/repos/{self.hub.repo}/pulls", params={"state": "open", "base": self.production, "per_page": PER_PAGE}
        )
        found = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        bumps = [r for r in found if text_of(r["head"]["ref"]).startswith(version_decide.BRANCH_PREFIX)]
        if len(bumps) > 1:
            listed = ", ".join("#" + str(r["number"]) for r in bumps)
            raise BumpError(f"開いた版上げの PR が 2 本以上ある: {listed}（1 本に減らしてから打ち直す）")
        if not bumps:
            return None
        r = bumps[0]
        ref = text_of(r["head"]["ref"])
        repo = r["head"].get("repo")
        same = isinstance(repo, dict) and text_of(repo.get("full_name")) == self.hub.repo
        return bump_decide.OpenBump(int(r["number"]), version_decide.version_of_branch(ref), ref, text_of(r["head"]["sha"]), same)

    def _commit(self, message: str) -> bool:
        """変更をコミットする（変更が無ければ足さない）。足したか。"""
        repo_git = self._git
        repo_git.run("add", "--all")
        if not repo_git.run("status", "--porcelain").strip():
            return False
        repo_git.run("commit", "--quiet", "-m", message)
        return True

    def _push(self, branch: str) -> None:
        """先端を進めるだけの push（強制しない）。"""
        self._git.run("push", "--quiet", "--", git_mod.ORIGIN, f"HEAD:refs/heads/{branch}")

    def _body(self, version: str, span: gather.Range, pulls: Sequence[materials_decide.PullFact]) -> str:
        listed = "".join(f"- #{p.number} {p.title}\n" for p in pulls) or "- （無し）\n"
        return f"CD の版上げ（v{version}）。範囲 `{span.since}..{span.until}` にマージされた PR:\n\n{listed}"

    def _create(
        self,
        version: str,
        span: gather.Range,
        pulls: Sequence[materials_decide.PullFact],
        planned: Sequence[version_decide.Planned],
    ) -> int:
        """main の先端から `release/v<版>` を作り、版上げのコミットを足して版上げの PR を作る。PR の番号を返す。"""
        repo_git = self._git
        branch = version_decide.branch_of(version)
        decl = decl_mod.load(self.root)
        store = self._store
        if gather.remote_has_branch(repo_git, branch):
            gather.fetch(repo_git, branch)
            remote = f"{git_mod.ORIGIN}/{branch}"
            outside = self._outside(span.until, remote, decl)
            if repo_git.rev_parse(f"{remote}^1") != span.until or outside:
                raise BumpError(f"origin に {branch} が既にあり、main の先端の版上げだけを持つブランチでない（人が見てから打ち直す）")
        else:
            repo_git.run("switch", "--quiet", "-C", branch, span.until)
            write_bump(store, decl, planned, pulls, self.today)
            self._commit(bump_decide.message_of(planned[0].plugin, version, span.until))
            self._push(branch)
        made = self.hub.rest(
            "POST",
            f"/repos/{self.hub.repo}/pulls",
            {
                "title": f"Release: {planned[0].plugin} v{version}",
                "head": branch,
                "base": self.production,
                "body": self._body(version, span, pulls),
            },
        )
        return int(made["number"])

    def _update(
        self,
        opened: bump_decide.OpenBump,
        span: gather.Range,
        pulls: Sequence[materials_decide.PullFact],
        planned: Sequence[version_decide.Planned],
    ) -> None:
        """開いた版上げの PR へ main を取り込むコミットと、版の節を組み直したコミットを足す（版が同じとき）。

        宣言と書き換え先は、取り出した main の先端（信頼する本番側）から読む。PR の head が版上げの外の変更を持てば、
        その木を取り出して手順を流す前に `BumpError` で止まる（未承認のブランチの宣言の手順を token 付きで流さない）。"""
        repo_git = self._git
        decl = decl_mod.load(self.root)
        gather.fetch(repo_git, opened.head_ref)
        remote = f"{git_mod.ORIGIN}/{opened.head_ref}"
        outside = self._outside(repo_git.merge_base(self._tip_ref, remote), remote, decl)
        if outside:
            raise BumpError(f"開いた版上げの PR #{opened.number} が版上げの外の変更を持つ（人が見てから打ち直す）: {', '.join(outside)}")
        repo_git.run("switch", "--quiet", "-C", opened.head_ref, remote)
        before = repo_git.rev_parse("HEAD")
        conflicted: list[str] = []

        def read_conflicted() -> None:
            conflicted.extend(repo_git.attempt("diff", "--name-only", "--diff-filter=U").stdout.split())

        if not gather.merge_or_abort(repo_git, ("--quiet", "--no-edit", git_mod.END_OF_OPTIONS, span.until), read_conflicted):
            raise BumpConflict(f"{span.until[:12]} を {opened.head_ref} へ取り込めない（衝突: {', '.join(conflicted) or '読めない'}）")
        write_bump(self._store, decl, planned, pulls, self.today)
        committed = self._commit(bump_decide.message_of(planned[0].plugin, opened.version, span.until))
        if committed or repo_git.rev_parse("HEAD") != before:
            self._push(opened.head_ref)

    def _replace(self, opened: bump_decide.OpenBump, version: str) -> None:
        """古い版の版上げの PR を理由つきで閉じ、そのブランチを消す。

        消す前に、head が同じリポジトリのブランチで、main との差分が版上げだけかを確かめる。どちらかが違えば（フォークの PR・
        利用者が積んだ直し）閉じも消しもせず `BumpError` で止まる（戻せない削除を名前だけで選ばない）。"""
        if not opened.same_repo:
            raise BumpError(f"開いた版上げの PR #{opened.number} の head が別のリポジトリにある（閉じずに止まる。人が見てから打ち直す）")
        repo_git = self._git
        gather.fetch(repo_git, opened.head_ref)
        remote = f"{git_mod.ORIGIN}/{opened.head_ref}"
        outside = self._outside(repo_git.merge_base(self._tip_ref, remote), remote, decl_mod.load(self.root))
        if outside:
            raise BumpError(
                f"置き換える版上げの PR #{opened.number} が版上げの外の変更を持つ（閉じずに止まる。人が見てから打ち直す）: {', '.join(outside)}"
            )
        hub = self.hub
        detail = f"版 {opened.version} を {version} へ置き換えた（後に main へ入った PR が上げ幅を大きくした）"
        hub.rest("POST", f"/repos/{hub.repo}/issues/{opened.number}/comments", {"body": detail})
        hub.rest("PATCH", f"/repos/{hub.repo}/pulls/{opened.number}", {"state": "closed"})
        hub.rest("DELETE", f"/repos/{hub.repo}/git/refs/heads/{opened.head_ref}")

    def _merge(self, number: int, version: str, level: str) -> Bumped:
        """main の先端を含むときだけ、版上げの PR を `land_bump` でマージする。"""
        repo_git = self._git
        pull = self.hub.pull(number)
        gather.fetch(repo_git, self.production)
        gather.fetch(repo_git, pull.head_ref)
        if not repo_git.is_ancestor(self._tip_ref, f"{git_mod.ORIGIN}/{pull.head_ref}"):
            return Bumped(bump_decide.REUSED, version, level, number)
        decl = decl_mod.load(self.root)

        def outside(p: Pull) -> list[str]:
            # マージへ渡す先端（`p.head_sha`）そのものを検査する（取り込んだブランチの後に先端が動いても、検査していない
            # コミットを照合付きでマージしない）
            gather.fetch(repo_git, p.head_ref)
            head = repo_git.rev_parse(f"{git_mod.ORIGIN}/{p.head_ref}")
            if head != p.head_sha:
                return [f"（#{p.number} の先端が {p.head_sha[:12]} から {head[:12]} へ動いた）"]
            if not repo_git.is_ancestor(self._tip_ref, head):
                return [f"（{head[:12]} が {self._tip_ref} を含まない）"]
            return self._outside(repo_git.merge_base(self._tip_ref, head), head, decl)

        landed = self.lander.land_bump(number, outside)
        state = bump_decide.MERGED if landed.merged else bump_decide.STOPPED
        return Bumped(state, version, level, number, landed)

    def _outside(self, base: str, head: str, decl: decl_mod.Declaration) -> list[str]:
        """`base..head` の差分のうち、版上げの変更でないもの（許したパスの外と、許したパスの中の版の外の変更。I7）。"""
        repo_git = self._git
        store = self._store
        paths = repo_git.changed_files(base, head)
        allowed = allowed_paths(store, decl)
        outside = bump_decide.only_bump(paths, allowed)
        generated = frozenset(w for step in decl.for_stage(version_decide.PRODUCTION) for w in step.writes)
        pairs = [
            (old, new)
            for u in store.units()
            if (old := store.read(repo_git, base, u)) is not None and (new := store.read(repo_git, head, u)) is not None and old != new
        ]
        for path in paths:
            if (
                bump_decide.covered(path, allowed)
                and not bump_decide.covered(path, generated)
                and not self._version_only(base, head, path, decl.changelog, pairs)
            ):
                outside.append(f"{path}（版の外の変更）")
        return outside

    def _version_only(self, base: str, head: str, path: str, changelog: str, pairs: Sequence[tuple[str, str]]) -> bool:
        """`path` の `base..head` の変更が版上げだけか（CHANGELOG は足した行だけ、JSON は版の文字列だけ、ほかは版の行だけ）。"""
        repo_git = self._git
        if path.endswith(".json"):
            before, after = (repo_git.attempt("show", f"{ref}:{path}") for ref in (base, head))
            if before.returncode != 0 or after.returncode != 0:
                return False
            try:
                return bump_decide.json_version_only(json.loads(before.stdout), json.loads(after.stdout), pairs)
            except ValueError:  # noqa: NDF401 ファイル: JSON として読めない変更は版上げとして扱わない
                return False
        diff = repo_git.run("diff", "-U0", "--no-color", "--no-ext-diff", base, head, git_mod.END_OF_OPTIONS, path)
        removed, added = bump_decide.diff_lines(diff)
        return not removed if path == changelog else bump_decide.version_lines_only(removed, added, pairs)

    def _tip(self, span: gather.Range, store: VersionStore) -> _Tip:
        """main の先端の単位ごとのバージョンと、タグを待つ単位のタグが origin にあるか。

        タグを待つのは、直前のバージョン上げの PR のマージでバージョンが変わった単位（`tagging.bumped`。publish がタグを作る
        対象と同じ）。バージョン上げの PR のマージが無ければタグを待つ単位は無く、`tagged` は真。そのマージがキューで追い越されて
        いれば（`tagging.overtaken`）、そのバージョンにはタグを作らずに待たず、範囲（そのコミットの起点..先端）で上げ直す（決定 20）。"""
        tags = self._tags()
        versions = {u.name: store.read(self._git, span.until, u) for u in store.units()}
        merged = self._bump_merge(span.until)
        overtaken = merged is not None and tagging.overtaken(self._git, merged)
        waiting = [u.name for u in tagging.bumped(store, self.root, merged)] if merged is not None and not overtaken else []
        targets = [(n, v) for n in waiting if (v := versions.get(n)) is not None and not version_decide.is_dev(v)]
        tagged = all(version_decide.tag_of(n, v) in tags for n, v in targets)
        shown = targets[0][1] if targets else None
        return _Tip(versions, tagged, shown, merged if merged is not None else span.until, overtaken)

    def _plan(self, span: gather.Range, store: VersionStore) -> _Plan:
        """範囲にマージされた PR・その上げ幅・出し済みのバージョンより大きく繰り上げたバージョンを上げる予定。"""
        repo_git = self._git
        numbers = gather.included(repo_git, span, self.production)
        level = bump_decide.widest([bump_decide.level_of(gather.labels_of(self.hub, n)) for n in numbers])
        decl = decl_mod.load(self.root)
        planned = version_store.plan(store, repo_git, self._changed(span), decl, level, self._tip_ref)
        return _Plan(numbers, level, planned)

    def bump(self) -> Bumped:
        """main の先端に合わせて版上げの PR を作るか進めるか置き換え、マージまで進める（I8・I11）。`github-flow` でなければ何もしない。"""
        if self._flow != decl_mod.GITHUB_FLOW:
            return Bumped(bump_decide.NOT_APPLICABLE)
        store = self._store
        span = self._span()
        tip = self._tip(span, store)
        plan = self._plan(span, store)
        opened = self._open_bump()
        planned, level = plan.planned, plan.level
        version = planned[0].next if planned else None
        act = bump_decide.action(tip.tagged, bool(planned), opened, version if version is not None else "")
        if act == bump_decide.PUBLISH:
            return Bumped(bump_decide.PUBLISHED, tip.shown, revision=tip.revision)
        if act == bump_decide.NOTHING or version is None:
            return Bumped(bump_decide.NOT_APPLICABLE)
        pulls = gather.pull_facts(self.hub, plan.numbers)
        if act == bump_decide.UPDATE and opened is not None:
            self._update(opened, span, pulls, planned)
            return self._merge(opened.number, version, level)
        if act == bump_decide.REPLACE and opened is not None:
            self._replace(opened, version)
        number = self._create(version, span, pulls, planned)
        return self._merge(number, version, level)
