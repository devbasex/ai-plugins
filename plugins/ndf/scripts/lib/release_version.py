"""配布の版の判定（#2164）。次に空いている開発版を決め、ある版がもう出ているかを判定する。

キューの版の差し込み（`supervise_lib/queue_fill.py` の `fill_release_version`）と `release-steps.py bump` が呼ぶ 1 か所で、
開発版と本番・キューと `bump` で版の規則を分けない（#2164 の決定 4）。ファイルを書かない。

- `observe(repo, plugin, base, production)`: `git fetch -q origin --tags` の後に、origin のベースブランチと本番のブランチの
  `plugin.json` の版と、`<plugin>--v<版>` の正式版のタグを読む。読めなかったものは `unreadable` に名前で入れる
- `decide_dev(obs, level)`: 次に空いている開発版
- `taken(obs, version)`: その版がもう出ているか（開発版はベースブランチの版が同じか大きい、正式版は同じ版のタグがある）
- `own_released(repo, branch, base)`: `branch` → `base` のマージ済みの PR があるか（照会できなければ None）

版の比較は `versions`（semver）を使う。呼ぶ側は先に `deps.require("versions")` を済ませておく。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import versions

LEVELS = ("patch", "minor", "major")


@dataclass
class Observed:
    plugin: str
    base_ref: str  # origin/<ベースブランチ>
    base_version: str | None
    production_ref: str  # origin/<本番のブランチ>。本番のブランチを宣言していなければ空
    production_version: str | None
    latest_tag: str | None  # 最新の正式版のタグの版（タグが 1 つも無ければ None）
    tags: list[str] = field(default_factory=list)  # 正式版のタグの版（`<plugin>--v` を外したもの）
    unreadable: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """版の記録の `observed`（読んだ値）。タグの一覧と読めなかったものは載せない。"""
        d = asdict(self)
        for k in ("tags", "unreadable", "plugin"):
            d.pop(k)
        return d


@dataclass
class Decision:
    version: str | None  # 決めた・確かめた版（決められなければ None）
    base: str | None  # version の接尾辞を外した版
    how: str  # decided / explicit / series
    existing: str | None = None  # すでに出ている版（ベースブランチの版かタグ）
    next_free: str | None = None  # 次に空いている版（すでに出ているときだけ）
    observed: dict = field(default_factory=dict)
    unreadable: list[str] = field(default_factory=list)

    @property
    def is_taken(self) -> bool:
        return self.existing is not None


def plugin_json_rel(repo: str | Path, ref: str, plugin: str) -> str | None:
    """`ref` の木で、名前が `plugin` のディレクトリの `.claude-plugin/plugin.json` の位置。無い・2 つ以上なら None。"""
    p = _git_run(repo, "ls-tree", "-r", "--name-only", ref)
    if p.returncode:
        return None
    tail = f"{plugin}/.claude-plugin/plugin.json"
    hits = [f for f in p.stdout.splitlines() if f == tail or f.endswith(f"/{tail}")]
    return hits[0] if len(hits) == 1 else None


def _git_run(repo: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _version_at(repo: str | Path, ref: str, rel: str) -> str | None:
    p = _git_run(repo, "show", f"{ref}:{rel}")
    if p.returncode:
        return None
    try:
        v = json.loads(p.stdout)["version"]
    except (ValueError, KeyError, TypeError):
        return None
    return v if isinstance(v, str) and versions.parse_version(v) else None


def observe(repo: str | Path, plugin: str, base: str, production: str | None, fetch: bool = True, rel: str | None = None) -> Observed:
    """origin を取り直してから、ベースブランチ・本番のブランチ・タグを読む。本番のブランチが無ければ読まない（読めないに数えない）。
    タグが 1 つも無いことは読めないに数えない。取り直しに落ちたら、読んだ値が古いかもしれないため `origin` を読めないに数える。
    `rel` は `plugin.json` のリポジトリの根からの位置で、省けばベースブランチの木から探す。"""
    unreadable: list[str] = []
    if fetch and _git_run(repo, "fetch", "-q", "origin", "--tags").returncode:
        unreadable.append("origin")
    base_ref = f"origin/{base}"
    rel = rel or plugin_json_rel(repo, base_ref, plugin)
    base_version = _version_at(repo, base_ref, rel) if rel else None
    if base_version is None:
        unreadable.append(base_ref)
    prod_ref = f"origin/{production}" if production else ""
    prod_version = _version_at(repo, prod_ref, rel) if prod_ref and rel else None
    if prod_ref and prod_version is None:
        unreadable.append(prod_ref)
    p = _git_run(repo, "tag", "--list", f"{plugin}--v*")
    tags: list[str] = []
    if p.returncode:
        unreadable.append("tags")
    else:
        head = f"{plugin}--v"
        for t in p.stdout.split():
            v = t[len(head) :]
            ver = versions.parse_version(v)
            if ver is not None and not ver.prerelease:
                tags.append(v)
    latest = max(tags, key=versions.version_order) if tags else None
    return Observed(plugin, base_ref, base_version, prod_ref, prod_version, latest, tags, unreadable)


def _max_base(vs: list[str | None]) -> str:
    bases = [versions.release_base(v) for v in vs if v]
    return max(bases, key=versions.version_order)


def decide_dev(obs: Observed, level: str = "patch") -> Decision:
    """次に空いている開発版。ベースブランチの版 B が `X-dev.N` で X のタグが無ければ `X-dev.(N+1)`、そうでなければ
    最新のタグ T・本番のブランチ P・B の基底の版のうち最も大きいものを上げ幅で上げた Y の `Y-dev.1`。読めないものがあれば決めない。"""
    if obs.unreadable:
        return Decision(None, None, "decided", observed=obs.as_dict(), unreadable=list(obs.unreadable))
    b = obs.base_version or ""
    if versions.dev_number(b) is not None and versions.release_base(b) not in obs.tags:
        v = versions.next_dev(b)
    else:
        v = f"{versions.next_version(_max_base([obs.latest_tag, obs.production_version, b]), level)}-dev.1"
    return Decision(v, versions.release_base(v), "decided", observed=obs.as_dict())


def taken(obs: Observed, version: str, how: str = "explicit", level: str = "patch") -> Decision:
    """`version` がもう出ているか。開発版（接尾辞つき）はベースブランチの版が同じか大きいとき、正式版は同じ版のタグがあるとき、
    `existing` に既に出ている版、`next_free` に次に空いている版を入れる。読めないものがあれば判定しない（`unreadable`）。"""
    d = Decision(version, versions.release_base(version), how, observed=obs.as_dict(), unreadable=list(obs.unreadable))
    if obs.unreadable:
        return d
    if versions.parse_version(version).prerelease:
        if obs.base_version and versions.compare_versions(obs.base_version, version) >= 0:
            d.existing = obs.base_version
            d.next_free = decide_dev(obs, level).version
        return d
    if version in obs.tags:
        d.existing = f"{obs.plugin}--v{version}"
        nxt = version
        while nxt in obs.tags:
            nxt = versions.next_patch(nxt)
        d.next_free = nxt
    return d


def own_released(repo: str | Path, branch: str, base: str) -> bool | None:
    """`branch` → `base` のマージ済みの PR があるか（自分の配布か）。1 回だけ照会し、照会できなければ None。"""
    import gh_call

    r = gh_call.gh(["pr", "list", "--head", branch, "--base", base, "--state", "merged", "--json", "number", "--limit", "1"], cwd=str(repo))
    if r.returncode:
        return None
    try:
        return bool(json.loads(r.stdout or "[]"))
    except ValueError:
        return None


@dataclass
class BumpStop:
    """`bump` を止める理由（要約・数値・終了コード）。"""

    summary: str
    metrics: dict
    code: int


def bump_check(repo: str | Path, plugin: str, old: str, new: str, base: str, rel: str) -> tuple[str | None, BumpStop | None]:
    """`bump` が書き換える前の判定（#2164 の I7）。打ち直しなら (origin のベースブランチの版, None)、書き換えてよければ (None, None)。
    新しい版がベースブランチの版以下か、正式版でそのタグがあれば終了コード 3 で止める（版を戻さない）。
    版の形が違う・ベースブランチの版を読めない・自分の配布の照会ができなければ、推定で進めずに終了コード 2 で止める。
    作業場所がすでに新しい版で、ベースブランチの版が等しいときだけ `release/v<新しい版>` → ベースブランチのマージ済みの
    PR を 1 回照会し、あれば自分の配布の後の流し直しとして打ち直しにする。"""
    if versions.parse_version(new) is None:
        return None, BumpStop(f"版の形が違う: {new}（X.Y.Z / X.Y.Z-dev.N / X.Y.Z-rc.N）", {"plugin": plugin, "to": new}, 2)
    obs = observe(repo, plugin, base, None, rel=rel)
    metrics = {"plugin": plugin, "from": old, "to": new, "base_version": obs.base_version}
    if obs.unreadable:
        why = f"版を確かめる値を読めない: {'・'.join(obs.unreadable)}。推定で上げない"
        return None, BumpStop(why, {**metrics, "unreadable": obs.unreadable}, 2)
    cmp = versions.compare_versions(obs.base_version, new)
    if old == new:
        rerun = _rerun(repo, new, base, obs, cmp, metrics)
        if rerun is not None:
            return rerun
    d = taken(obs, new)
    if cmp < 0 and not d.is_taken:
        return None, None
    return None, _taken_stop(plugin, new, obs, cmp, d, metrics)


def _rerun(repo: str | Path, new: str, base: str, obs: Observed, cmp: int, metrics: dict) -> tuple[str | None, BumpStop | None] | None:
    """作業場所がすでに新しい版のときの打ち直しの判定。打ち直しか止めるなら (already, stop)、どちらでもなければ None。"""
    if cmp < 0:
        return obs.base_version, None
    if cmp > 0:
        return None
    own = own_released(repo, f"release/v{new}", base)
    if own is None:
        why = f"release/v{new} → {base} のマージ済みの PR を照会できない。推定で進めない"
        return None, BumpStop(why, {**metrics, "unreadable": ["pr"]}, 2)
    return (obs.base_version, None) if own else None


def _taken_stop(plugin: str, new: str, obs: Observed, cmp: int, d: Decision, metrics: dict) -> BumpStop:
    """新しい版がすでに出ているときに止める理由（既に出ている版と次に空いている版）。"""
    existing = obs.base_version if cmp >= 0 else d.existing
    if d.is_taken:
        nxt = d.next_free
    elif versions.parse_version(new).prerelease:
        nxt = decide_dev(obs).version
    else:
        nxt = versions.next_patch(obs.base_version)
    why = f"{plugin} の {new} はすでに出ている（{existing}）。版を戻さない（次に空いている版は {nxt}）"
    return BumpStop(why, {**metrics, "existing": existing, "next_free": nxt}, 3)
