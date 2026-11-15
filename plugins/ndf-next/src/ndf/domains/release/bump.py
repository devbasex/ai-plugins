"""main へのマージの後に、版上げの PR を作り・進め・置き換え、必須のチェックの後に照合付きでマージする（CD が打つ。#3572 の決定 7）。

main の ruleset は PR を通さない push を断るため、版上げのコミットは `release/v<版>` を head にした版上げの PR で main へ入れる。
動きは `bump_decide.action` が main の先端の状態から決める。範囲は「前の版上げのコミットの起点..main の先端」で、上げ幅と
CHANGELOG の版の節（決定 11）はこの範囲にマージされた PR から決める。

| 動き | すること |
| --- | --- |
| `PUBLISH` | 何も書かずに `published` を返す（workflow がタグと GitHub Release を作る） |
| `NOTHING` | 何も書かずに `not_applicable` を返す（I11） |
| `CREATE` | main の先端から `release/v<版>` を作り、版のファイルと CHANGELOG の版の節だけを変えたコミットを足して PR を作る |
| `UPDATE` | 開いた版上げの PR へ main を取り込むコミットと、版の節を組み直したコミットを足す（強制 push を使わない） |
| `REPLACE` | 古い版の版上げの PR を理由つきで閉じてブランチを消し、`CREATE` へ進む |

作るか進めた後は、main の先端が head の祖先のときだけ `land.Lander.land_bump` でマージする（祖先でなければ `reused`。次の
main への push の `bump` が進める）。同じ main の先端で 2 回打っても版上げの PR は 1 本で、差分が同じならコミットを足さない（I8）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, Pull, text_of
from ndf.domains.release import bump_decide, changelog_decide, gather, land, materials_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


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


class _Tip(NamedTuple):
    """main の先端のプラグインの名前 → 版（定義が無ければ None）と、版のある全プラグインのタグが origin にあるか。"""

    versions: dict[str, str | None]
    tagged: bool


class _Plan(NamedTuple):
    """範囲にマージされた PR の番号・最も大きい上げ幅・版を上げる予定（出し済みの版より大きく繰り上げたもの）。"""

    numbers: list[int]
    level: str
    planned: list[version_decide.Planned]


def allowed_paths(root: Path, decl: decl_mod.Declaration) -> frozenset[str]:
    """版上げの PR が変えてよいパス（プラグインの定義・マーケットプレイス・宣言の `version_files`・CHANGELOG・手順の書き先）。"""
    paths = {plugins_mod.MARKETPLACE.as_posix(), decl.changelog, *decl.version_files}
    for step in decl.for_stage(version_decide.PRODUCTION):
        paths |= set(step.writes)
    for p in plugins_mod.load(root):
        paths |= {m.relative_to(root).as_posix() for m in p.manifests(root)}
    return frozenset(paths)


def write_bump(
    root: Path,
    decl: decl_mod.Declaration,
    planned: Sequence[version_decide.Planned],
    pulls: Sequence[materials_decide.PullFact],
    today: str,
) -> frozenset[str]:
    """版のファイルと CHANGELOG の版の節を書き、宣言の手順を流す。書いたパスを返す。"""
    found = plugins_mod.by_name(plugins_mod.load(root))
    entries = [changelog_decide.Entry(p.number, p.title) for p in pulls]
    log_path = root / decl.changelog
    written: set[str] = set()
    for p in planned:
        written |= set(plugins_mod.bump(root, found[p.plugin], p.next, decl.version_files))
        if log_path.is_file():
            new = changelog_decide.block(p.plugin, p.next, today, entries)
            log_path.write_text(changelog_decide.insert(log_path.read_text(encoding="utf-8"), p.plugin, p.next, new), encoding="utf-8")
            written.add(decl.changelog)
    for step in decl.for_stage(version_decide.PRODUCTION):
        decl_mod.run_step(root, step, planned[0].next)
        written |= set(step.writes)
    return frozenset(written)


def planned_for(root: Path, hub: GitHub, number: int, production: str) -> str | None:
    """本番の PR の予定の版（PR のラベルと main の版から。マージ済みはマージの第 1 親の版から）。読めなければ None。"""
    try:
        found = plugins_mod.load(root)
        if not found:
            return None
        pull = hub.pull(number)
        level = bump_decide.level_of(gather.labels_of(hub, number))
        repo_git = git_mod.Git(root)
        if pull.merged and pull.merge_sha:
            ref = f"{pull.merge_sha}^1"
        else:
            gather.fetch(repo_git, production)
            ref = f"{git_mod.ORIGIN}/{production}"
        current = gather.version_at(repo_git, found[0], ref)
        return bump_decide.planned(current, level) if current is not None else None
    except (git_mod.GitError, plugins_mod.PluginsError, version_decide.VersionError):  # noqa: NDF401 外部コマンド: 予定の版は表示だけで、確定の版は confirm が読む
        return None


@dataclass(frozen=True)
class Bumper:
    """1 つのリポジトリの版上げ。`root` は main を取り出した作業場所、`today` は UTC の日付。"""

    root: Path
    hub: GitHub
    lander: land.Lander
    production: str
    today: str

    @property
    def _git(self) -> git_mod.Git:
        return git_mod.Git(self.root)

    @property
    def _tip_ref(self) -> str:
        return f"{git_mod.ORIGIN}/{self.production}"

    def _tags(self) -> set[str]:
        refs = self._git.run("ls-remote", "--tags", "--refs", git_mod.ORIGIN).splitlines()
        return {line.partition("\t")[2].removeprefix("refs/tags/") for line in refs}

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
        found = plugins_mod.load(self.root)
        current = gather.version_at(repo_git, found[0], tip) if found else None
        if current is not None:
            tag = version_decide.tag_of(found[0].name, current)
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

    def _changed(self, span: gather.Range) -> list[str]:
        """範囲で変わったファイル。前の版上げの PR のマージ（M）が範囲にあれば、その版上げ自身の変更を数えない
        （起点..M の第 1 親 と M..main の先端 の和）。"""
        repo_git = self._git
        last = self._last_bump(span.until)
        chain = (
            repo_git.run("rev-list", "--first-parent", "--ancestry-path", git_mod.END_OF_OPTIONS, f"{last}..{span.until}").split()
            if last is not None
            else []
        )
        if not chain:
            return repo_git.changed_files(span.since, span.until)
        merged = chain[-1]
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
        return bump_decide.OpenBump(int(r["number"]), version_decide.version_of_branch(ref), ref, text_of(r["head"]["sha"]))

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
        if repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", git_mod.ORIGIN, branch).returncode == 0:
            gather.fetch(repo_git, branch)
            remote = f"{git_mod.ORIGIN}/{branch}"
            outside = bump_decide.only_bump(repo_git.changed_files(span.until, remote), allowed_paths(self.root, decl))
            if repo_git.rev_parse(f"{remote}^1") != span.until or outside:
                raise BumpError(f"origin に {branch} が既にあり、main の先端の版上げだけを持つブランチでない（人が見てから打ち直す）")
        else:
            repo_git.run("switch", "--quiet", "-C", branch, span.until)
            write_bump(self.root, decl, planned, pulls, self.today)
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
        """開いた版上げの PR へ main を取り込むコミットと、版の節を組み直したコミットを足す（版が同じとき）。"""
        repo_git = self._git
        gather.fetch(repo_git, opened.head_ref)
        repo_git.run("switch", "--quiet", "-C", opened.head_ref, f"{git_mod.ORIGIN}/{opened.head_ref}")
        before = repo_git.rev_parse("HEAD")
        if repo_git.attempt("merge", "--quiet", "--no-edit", git_mod.END_OF_OPTIONS, span.until).returncode != 0:
            conflicted = repo_git.attempt("diff", "--name-only", "--diff-filter=U").stdout.split()
            repo_git.attempt("merge", "--abort")
            raise BumpConflict(f"{span.until[:12]} を {opened.head_ref} へ取り込めない（衝突: {', '.join(conflicted) or '読めない'}）")
        write_bump(self.root, decl_mod.load(self.root), planned, pulls, self.today)
        committed = self._commit(bump_decide.message_of(planned[0].plugin, opened.version, span.until))
        if committed or repo_git.rev_parse("HEAD") != before:
            self._push(opened.head_ref)

    def _replace(self, opened: bump_decide.OpenBump, version: str) -> None:
        """古い版の版上げの PR を理由つきで閉じ、そのブランチを消す。"""
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
            return Bumped("reused", version, level, number)
        allowed = allowed_paths(self.root, decl_mod.load(self.root))

        def outside(p: Pull) -> list[str]:
            base = repo_git.merge_base(self._tip_ref, f"{git_mod.ORIGIN}/{p.head_ref}")
            return bump_decide.only_bump(repo_git.changed_files(base, f"{git_mod.ORIGIN}/{p.head_ref}"), allowed)

        landed = self.lander.land_bump(number, outside)
        state = "merged" if landed.merged else "stopped"
        return Bumped(state, version, level, number, landed)

    def _tip(self, span: gather.Range, found: Sequence[plugins_mod.Plugin]) -> _Tip:
        """main の先端のプラグインごとの版と、版のある全プラグインのタグが origin にあるか。"""
        tags = self._tags()
        versions = {p.name: gather.version_at(self._git, p, span.until) for p in found}
        tagged = all(v is None or version_decide.tag_of(n, v) in tags for n, v in versions.items())
        return _Tip(versions, tagged)

    def _plan(self, span: gather.Range, found: Sequence[plugins_mod.Plugin]) -> _Plan:
        """範囲にマージされた PR・その上げ幅・出し済みの版より大きく繰り上げた版を上げる予定。"""
        repo_git = self._git
        numbers = gather.included(repo_git, span, self.production)
        level = bump_decide.widest([bump_decide.level_of(gather.labels_of(self.hub, n)) for n in numbers])
        decl = decl_mod.load(self.root)
        change_list = gather.changes(self._changed(span), found, decl)
        bumps = gather.plan_bumps(self.root, found, change_list, decl, level, version_decide.PRODUCTION)
        planned = gather.clear_shipped(bumps, gather.shipped(repo_git, found, self._tip_ref), level, version_decide.PRODUCTION)
        return _Plan(numbers, level, planned)

    def bump(self) -> Bumped:
        """main の先端に合わせて版上げの PR を作るか進めるか置き換え、マージまで進める（I8・I11）。"""
        decl_mod.require_github_flow(self.root)
        span = self._span()
        found = plugins_mod.load(self.root)
        tip = self._tip(span, found)
        plan = self._plan(span, found)
        opened = self._open_bump()
        planned, level = plan.planned, plan.level
        version = planned[0].next if planned else None
        act = bump_decide.action(tip.tagged, bool(planned), opened, version if version is not None else "")
        if act == bump_decide.PUBLISH:
            shown = next((v for v in tip.versions.values() if v is not None), None)
            return Bumped("published", shown)
        if act == bump_decide.NOTHING or version is None:
            return Bumped("not_applicable")
        pulls = gather.pull_facts(self.hub, plan.numbers)
        if act == bump_decide.UPDATE and opened is not None:
            self._update(opened, span, pulls, planned)
            return self._merge(opened.number, version, level)
        if act == bump_decide.REPLACE and opened is not None:
            self._replace(opened, version)
        number = self._create(version, span, pulls, planned)
        return self._merge(number, version, level)
