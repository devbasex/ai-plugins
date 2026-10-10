#!/usr/bin/env python3
"""リポジトリが宣言した配布のコマンドを、配布の段階に合わせて走らせる（#893）。

宣言はリポジトリの `.ndf/release.json`。形は `skills/release/schemas/release.schema.json` が定め、
書き方は `skills/release/references/release-steps.md` にある。

    python3 release-steps.py run   --root <dir> --stage production|verification --version <版> [--dry-run]
    python3 release-steps.py check --root <dir>

終了コード:

    0  宣言が無い（run は何も出力しない）、段階に合うコマンドが無い、またはすべてのコマンドが 0 で終わり
       書いてよい場所の中だけが変わった
    1  コマンドが 0 以外で終わった・時間切れ・書いてよい場所の外が変わった。最初に落ちたコマンドで止める
    2  check だけが返す。宣言が無い
    3  宣言が読めない。どの項目かを標準エラーに出す

コマンドはシェルを通さずに `--root` を作業ディレクトリにして実行する。コマンドが変えたパスは、コマンドの前後の
`git status --porcelain -uall` に出たパスの内容の要約（`git hash-object`）を比べて決める。

配布の決まった手順（#862。試作は #827 の phase-steps.py）も同じスクリプトに置く。結果は
`lib/step_result.py` の形の 1 行の JSON で、終了コードは 0 = ok / 1 = 失敗（stopped）/ 2 = 読めない / 3 = 前提が無い /
10 = 本番への配布の承認が要る（approval-facts）/ 75 = 待つ PR の CI の基盤待ち（中身の失敗ではない）。

    python3 release-steps.py bump           --plugin <名前> --to <版> [--base <ベースブランチ>] [--root <dir>]
    python3 release-steps.py changed-plugins [--since <タグ>] [--plugin <名前>] [--root <dir>]  # 差分のある他のプラグイン
                                            [--prs <PR番号>...] [--approval <承認資料> [--set <名前>=<上げ幅>]...]
                                            [--decided <承認資料>]
    python3 release-steps.py changelog      --version <版> --prs <PR番号>... [--plugin <名前>] [--root <dir>]
    python3 release-steps.py release        --version <版> --channel dev|prod [--plugins <名前>,...] [--root <dir>]
                                            [--approved-sha <SHA> | --approval <承認資料>]   # prod はどちらかが要る
    python3 release-steps.py record         --version <版> --prs <PR番号>... [--plugin <名前>] [--root <dir>]   # prod の後
    python3 release-steps.py record         --promote --head <ベースブランチ> --base <本番チャネル> --prs <PR番号>... [--root <dir>]
    python3 release-steps.py approve        --approval <承認資料> --approved-sha <SHA> --by user|mvv [--root <dir>]
    python3 release-steps.py approval-facts --version <版> --prs <PR番号>... [--prev-tag <タグ>] [--plugin <名前>] [--root <dir>]
    python3 release-steps.py notes          --version <版> --prs <PR番号>... [--approval <提示物>]
                                            [--verified claude,codex,kiro] [--ref <ブランチ>] [--plugin <名前>] [--root <dir>]

release --channel prod は承認したコミットを受け取り、配布の PR をマージした後の `origin/<ベースブランチ>` の先端と比べ、
承認の外の変更があれば本番チャネルの PR もタグも作らずに承認ゲート（10）で止まる（release_lib/approval.py。#815）。

record は release --channel prod の後に、本番のリリースの PR（release/v<版> → ベースブランチ）へリリース記録
（`## 配布の記録`。形は lib/dist_record.py）をコメントで 1 件書く。タグと GitHub Release が無ければ書かずに 3 で止まり、
同じ記録が既にあれば書かずに ok を返す。投稿の失敗は 1、PR を読めなければ 2。metrics.release_pr_url を
プランの `pr_from` が報告の Pull Request へ移す（#1273）。
`record --promote` は昇格の経路で、最後にマージした昇格の Pull Request（--head → --base）へ同じ形の記録を書く。
`版:` はマージのコミット（`<本番チャネル> <短い SHA>`）である（#837）。

ブランチ（ベースブランチ・本番チャネル）は `.ndf/worktree.json` の base_branch・production_branch（無ければ既定ブランチ）、
プラグイン（タグ `<名前>--v<版>`・題 `Release: <名前> v<版>`）は引数 → `.ndf/supervise.json` の release.plugin から読む（#1336）。
CHANGELOG.md の置き場と見出し・`plugins/<名前>/` の配置・版の形は、形 package-plugin の約束である（form-package-plugin.md）。

notes は PR 本文の `## 利用者向けの変化` の節（無い・「無し」の PR は題名）から、CHANGELOG.md の版の節を
組み直す。bump・changelog・notes は plugin の README に触れない（#1867。更新情報は CHANGELOG.md の版の節が正本）。
`--approval` を渡すと、代わりに本番承認の提示物の
「配る中身」「検証への配布で確かめたこと」の欄と、PR 本文の `## 未検証・残る危険` を集めた節を書く。
changelog と notes は未マージの PR を載せず、番号を metrics.unmerged へ出す。渡した PR がすべて未マージなら
書き込む前に 3（前提エラー）で止まる。PR の読み取り（`gh pr view --json`）は、GraphQL が上限なら REST で読み直す
（`lib/gh_parts.py` の `view_json`）。

bump は、作業場所の版がすでに `--to` で起点（origin/<base>）の版と違うなら、同じステップの打ち直しとして何もせず
ok を返す。起点の版がすでに `--to` なら（同じ版を出し直そうとしている）止まる。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("schema", "versions", "bump", "md", "mdtable", "durable")
import approved_commit as ac  # noqa: E402
import mdtable  # noqa: E402
import schema  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merged_lib import trash  # noqa: E402
from release_lib import approval, bump, changed, deploy, others, record, step_run  # noqa: E402
from release_lib.approval import HeadMoved  # noqa: E402
from release_lib.names import changelog_section, changelog_span, h2_lines, next_h2, plugin_of, release_decl  # noqa: E402
from step_result import (
    EXIT_GATE,
    EXIT_INFRA_WAIT,
    EXIT_PRECONDITION,
    StepError,
    approval_present,
    base_of,  # noqa: E402
    common_parser,
    emit,
    gh_json,
    git,
    git_root,
    main_with,
    plugin_dir,
    result,
    run,
    today,
    version_arg,
)
import gh_parts  # noqa: E402
import gh_sections  # noqa: E402
import jsonio  # noqa: E402
import proc  # noqa: E402
import repo as repo_lib  # noqa: E402
import delivery  # noqa: E402

DECLARATION = ".ndf/release.json"
SUPPORTED_VERSIONS = (1,)
STAGES = ("production", "verification", "any")
DEFAULT_TIMEOUT = 600


class DeclarationError(Exception):
    pass


@dataclass
class Step:
    name: str
    stage: str
    command: list[str]
    writes: list[str]
    guide: str | None
    timeout: int


def _relative(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeclarationError(f"{where}: 空でない文字列で書く")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise DeclarationError(f"{where}: リポジトリの根からの相対パスで書く（絶対パスと .. は使えない）: {value!r}")
    return value


class _StepShape(schema.Shape):
    model_config = {**schema.Shape.model_config, "strict": True}
    name: str
    stage: Literal["production", "verification", "any"]
    command: list[str]
    writes: list[str]
    guide: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT


class _DeclarationShape(schema.Shape):
    model_config = {**schema.Shape.model_config, "strict": True}
    version: int
    steps: list[_StepShape]


def parse_steps(raw) -> list[Step]:
    """宣言の形は lib/schema.py で見て、値の決まり（空でない・相対パス・1 以上）はここで見る。"""
    if not isinstance(raw, dict):
        raise DeclarationError("release.json: 最上位はオブジェクトで書く")
    try:
        decl = schema.load_shape(_DeclarationShape, {k: v for k, v in raw.items() if k != "$schema"})
    except schema.ShapeError as e:
        raise DeclarationError(f"release.json: {e}") from None
    if decl.version not in SUPPORTED_VERSIONS:
        raise DeclarationError(f"version: 無いか未対応である: {decl.version!r}（読めるのは {SUPPORTED_VERSIONS}）")
    out = []
    for i, s in enumerate(decl.steps):
        where = f"steps[{i}]"
        if not s.name.strip():
            raise DeclarationError(f"{where}.name: 空でない文字列で書く（必須）")
        if not s.command:
            raise DeclarationError(f"{where}.command: 空でない文字列の配列で書く（必須）")
        writes = [_relative(w, f"{where}.writes[{j}]") for j, w in enumerate(s.writes)]
        guide = None if s.guide is None else _relative(s.guide, f"{where}.guide")
        if s.timeout_seconds < 1:
            raise DeclarationError(f"{where}.timeout_seconds: 1 以上の整数で書く: {s.timeout_seconds!r}")
        out.append(Step(s.name, s.stage, s.command, writes, guide, s.timeout_seconds))
    return out


def load_steps(root: Path) -> list[Step] | None:
    path = root / DECLARATION
    if not path.is_file():
        return None
    try:
        raw = jsonio.read(path)
    except jsonio.JsonReadError as e:
        raise DeclarationError(f"release.json: JSON として読めない: {e.detail or e}") from e
    return parse_steps(raw)


# ---------- 変更の判定 ----------


def status_paths(root: Path) -> set[str]:
    """`git status` が挙げたパス。名前の変更は元と先の両方を返す。"""
    tokens = proc.git(root, "status", "--porcelain=v1", "-z", "-uall").stdout.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            paths.add(tokens[i])
            i += 1
    return paths


def digests(root: Path, paths: set[str]) -> dict[str, str | None]:
    """パスごとの内容の要約。無いパスは None。"""
    present = sorted(p for p in paths if (root / p).is_file() or (root / p).is_symlink())
    out: dict[str, str | None] = {p: None for p in paths}
    if present:
        hashes = proc.run(["git", "-C", str(root), "hash-object", "--stdin-paths"], input="\n".join(present) + "\n").stdout.split()
        out.update(zip(present, hashes))
    return out


def allowed(path: str, writes: list[str]) -> bool:
    for w in writes:
        prefix = w.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# ---------- 実行 ----------


def selected(steps: list[Step], stage: str) -> list[Step]:
    return [s for s in steps if s.stage in (stage, "any")]


def expand(step: Step, version: str) -> list[str]:
    return [c.replace("{version}", version) for c in step.command]


def changed_paths(root: Path, before_paths: set[str], before: dict[str, str | None]) -> list[str]:
    """コマンドの後の状態と比べ、内容の変わったパスを返す。"""
    after_paths = status_paths(root)
    union = before_paths | after_paths
    after = digests(root, union)
    for p in after_paths - before_paths:  # コマンドの前は変更が無かった = HEAD の内容
        before[p] = _head_digest(root, p)
    return sorted(p for p in union if before[p] != after[p])


def run_steps(root: Path, stage: str, version: str, dry_run: bool) -> int:
    steps = load_steps(root)
    if steps is None:
        return 0
    for step in selected(steps, stage):
        command = expand(step, version)
        if dry_run:
            step_run.print_step(step, command)
            continue
        try:
            before_paths = status_paths(root)
            before = digests(root, before_paths)
        except (OSError, StepError) as e:
            print(f"コマンド: {step.name} → 実行しない（git status を取れない: {e}）", file=sys.stderr)
            return 1
        rc = step_run.run_command(root, step, command)
        if rc is None:
            return 1
        changed = changed_paths(root, before_paths, before)
        outside = [p for p in changed if not allowed(p, step.writes)]
        print(f"コマンド: {step.name} → {rc}")
        for p in changed:
            print(f"  変えたパス: {p}{'（書いてよい場所の外）' if p in outside else ''}")
        if rc != 0:
            return 1
        if outside:
            print(f"コマンド: {step.name} が書いてよい場所（{', '.join(step.writes) or 'なし'}）の外を変えた", file=sys.stderr)
            return 1
        if step.guide:
            print(f"guide: {step.guide}")
    return 0


def _head_digest(root: Path, path: str) -> str | None:
    """コマンドの前に変更の無かったパスの要約（HEAD の内容。HEAD に無ければ None）。"""
    try:
        return proc.git(root, "rev-parse", "--verify", "-q", f"HEAD:{path}").stdout.strip() or None
    except StepError:
        return None


# ---------- 配布の決まった手順（#862） ----------

TOOL = "release"


def run_staleness(root, expected=()):
    """check-doc-staleness.py を走らせる。expected に合う ERROR だけなら通ったと見なす。"""
    script = root / "scripts" / "check-doc-staleness.py"
    if not script.is_file():
        return None, "scripts/check-doc-staleness.py が無い"
    p = run([sys.executable, str(script), "--root", str(root)], cwd=root, check=False)
    if p.returncode == 0:
        return True, "ok"
    out = (p.stdout + p.stderr).strip().splitlines()
    errors = [l for l in out if l.startswith("ERROR")]
    rest = [l for l in errors if not any(x in l for x in expected)]
    if errors and not rest:
        return True, "ok（後のステップで直す分だけ: " + " / ".join(errors)[:800] + "）"
    return False, " / ".join((rest or out)[-10:])[:1000]


def base_version(root, pdir, ref):
    """ref（起点のブランチなら origin/<base>・タグ・HEAD）の plugin.json の版。読めなければ None。"""
    rel = (pdir / ".claude-plugin" / "plugin.json").relative_to(root).as_posix()
    p = git(root, "show", f"{ref}:{rel}", check=False)
    try:
        return json.loads(p.stdout)["version"] if p.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        return None


def bump_versioning_doc(ed):
    """docs/versioning-and-distribution.md の「版の付け方と開発版の配布」章の正式版の版数。"""
    doc = ed.root / "docs" / "versioning-and-distribution.md"
    rel = "docs/versioning-and-distribution.md"
    ob, nb = base_of(ed.old), base_of(ed.new)
    if ob == nb:
        return
    if not doc.is_file():
        ed.manual.append(f"{rel} が無い（この章は手で直す）")
        return
    lines = ed.lines(doc)
    targets = [
        (re.compile(r"^\| 正式版 \| `([^`]+)` \|"), "正式版の表の行"),
        (re.compile(r"`([^`]+)` の次を開発するなら"), "接尾辞の例"),
    ]
    for rx, what in targets:
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if len(hits) != 1:
            ed.manual.append(f"{rel}: 「版の付け方と開発版の配布」章の{what}が特定できない（この章は手で直す）")
            continue
        i = hits[0]
        cur = rx.search(lines[i]).group(1)
        if cur == nb:
            continue
        if cur != ob:
            ed.manual.append(f"{rel}: {what}の版が {cur} で旧版 {ob} と違う（この章は手で直す）")
            continue
        esc = bump.braces(lines[i])
        ed.place(doc, [i], esc.replace(f"`{ob}`", f"`{bump.CUR_BASE}`", 1), esc.replace(f"`{ob}`", f"`{bump.NEW_BASE}`", 1), what)


def plan_bump(root, pdir, plugin, old, new):
    """`plugin` の版を `old` から `new` へ上げる箇所を集めた `BumpPlan` を返す（まだ書き換えない）。"""
    ed = bump.BumpPlan(root, old, new)
    desc_re = r'^\s*"description"\s*:.*\(v' + re.escape(old) + r"\)"

    for rel, _ in (
        (".claude-plugin/plugin.json", True),
        (".codex-plugin/plugin.json", False),
        ("dev.agy/plugin.json", False),
        ("plugin.json", False),
    ):
        f = pdir / rel
        if not f.is_file():
            continue
        ed.sub(f, r'^\s*"version"\s*:', f"{rel} の version")
        if f"(v{old})" in f.read_text(encoding="utf-8"):
            ed.sub(f, desc_re, f"{rel} の description")

    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        s, e = bump.marketplace_range(ed.lines(mp), plugin)
        if s is None:
            ed.manual.append(f".claude-plugin/marketplace.json に {plugin} の項目が無い")
        elif any(f"(v{old})" in l for l in ed.lines(mp)[s:e]):
            ed.sub(mp, desc_re, "marketplace.json の description", start=s, stop=e)

    readme = root / "README.md"
    rows = [l for l in (ed.lines(readme) if readme.is_file() else []) if l.startswith(f"| **{plugin}** |")]
    if rows:
        ed.sub(readme, r"^\| \*\*" + re.escape(plugin) + r"\*\* \|", "README.md のプラグイン一覧表")
    elif plugin in ("ndf", "playwright-kit"):
        ed.manual.append(f"README.md のプラグイン一覧表に {plugin} の行が無い")
    if plugin == "ndf":
        ed.sub(readme, r"\*\*NDFプラグイン v", "README.md の概要の版")
        nr = pdir / "README.md"
        ed.sub(nr, r"（Kiro CLI用 / v", "plugins/ndf/README.md の Kiro の確認例")
        ed.sub(nr, r"/plugins/cache/ai-plugins/ndf/", "plugins/ndf/README.md の Codex のパス例", count=2)
        ed.sub(nr, r"ndf@ai-plugins\s+installed", "plugins/ndf/README.md の codex plugin list の出力例")
        bump_versioning_doc(ed)

    return ed


def cmd_bump(a):
    root = git_root(a.root)
    a.base = a.base or delivery.load_delivery(root).base  # 既定は宣言のベースブランチ（#1336）
    pdir = plugin_dir(root, a.plugin)
    try:
        old = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as e:
        raise StepError(f"旧版を plugin.json から読めない: {e}", 2)
    new = a.to
    if old == new:
        base_ver = base_version(root, pdir, f"origin/{a.base}")
        if base_ver is None or base_ver == new:
            raise StepError(f"旧版と新版が同じ: {old}" + ("" if base_ver else f"（origin/{a.base} の版を読めない）"))
        # 同じステップの打ち直し（作業場所の版はすでに上げてある）
        emit(
            result(
                TOOL,
                "ok",
                f"{a.plugin} はすでに {new}（origin/{a.base} は {base_ver}）。上げ直さない",
                [],
                {"plugin": a.plugin, "from": base_ver, "to": new, "already": True},
            )
        )
    ed = plan_bump(root, pdir, a.plugin, old, new)
    ed.apply()

    ok, summary = run_staleness(root)
    if ok is None:
        ed.manual.append(summary)
    items = [{"kind": "file", "name": f, "result": "updated"} for f in ed.files] + [
        {"kind": "manual", "name": m, "result": "manual"} for m in ed.manual
    ]
    metrics = {"plugin": a.plugin, "from": old, "to": new, "staleness": summary}
    if ok is False:
        emit(result(TOOL, "stopped", f"{old} → {new} の後に check-doc-staleness.py が失敗", items, metrics))
    emit(
        result(
            TOOL,
            "ok",
            f"{a.plugin} を {old} → {new} へ上げた（{len(ed.files)} ファイル・手で直す {len(ed.manual)} 件）",
            items,
            metrics,
            next="items の manual を手で直す" if ed.manual else None,
        )
    )


def release_tag_before(root, plugin, current=None):
    """<plugin>--v で始まり接尾辞の無いタグのうち、current を除いて最も新しいもの。無ければ None。"""
    head = f"{plugin}--v"
    tags = git(root, "tag", "--list", f"{head}*", "--sort=-v:refname").stdout.split()
    return next((t for t in tags if t != current and "-" not in t[len(head) :]), None)


def next_release(name, old, level="patch"):
    """上げ幅（既定は PATCH）で上げた正式版（`2.3.4` と `2.3.4-dev.1` の PATCH は `2.3.5`）。"""
    try:
        return others.bumped(old, level)
    except ValueError as e:
        raise StepError(f"{name} の版を読めない: {e}", 2)


def other_plugins(root, a):
    """(前のタグ, 差分にあってまだ上げていない {名前: 前のタグの版}, 上げ済みの行)。"""
    since = a.since or release_tag_before(root, a.plugin) or f"{a.plugin}--v*"
    p = git(root, "diff", "--name-only", f"refs/tags/{since}", "HEAD", check=False)
    if p.returncode:
        raise StepError(f"前のタグが無い: {since}", 2)
    pending, already = {}, []
    for name in sorted({m[1] for f in p.stdout.split() if (m := re.match(r"plugins/(?:mcp/)?([^/]+)/", f))} - {a.plugin}):
        pdir = root / "plugins" / (name if name in ("ndf", "playwright-kit") else f"mcp/{name}")
        old, head = base_version(root, pdir, since), base_version(root, pdir, "HEAD")
        if old and head == old:  # 差分の中でまだ上げていない
            pending[name] = old
        elif head:
            already.append(others.OtherPlugin(name, old or "—", others.ALREADY, head, ["前のタグから版が変わっている"]))
    return since, pending, already


def cmd_changed_plugins(a):
    """前のタグからの差分にある --plugin 以外のプラグインと、上げた版を items に返す（上げ幅は release_lib/others.py）。"""
    root = git_root(a.root)
    a.plugin = plugin_of(root, a)
    if (a.decided and (a.prs or a.approval)) or (a.set and not a.approval):
        raise StepError("引数の組み合わせが違う（--decided は --prs・--approval と並べない。--set は --approval と使う）", 2)
    if a.set:
        return emit(changed.set_levels(TOOL, changed.approval_path(root, a.approval), a.set))
    since, pending, already = other_plugins(root, a)
    skipped, decided = [], None
    if a.decided and pending:
        decided = changed.approval_path(root, a.decided)
        rows = changed.decided_rows(decided, pending, already)
    elif a.prs:
        rows = changed.candidate_rows(root, a.prs, pending, lambda n: pr_view(root, n, "body,state,mergeCommit"), skipped)
    else:
        rows = [others.OtherPlugin(n, old, "PATCH", next_release(n, old), ["材料を渡していない"]) for n, old in pending.items()]
    if a.approval:
        path = changed.approval_path(root, a.approval)
        changed.write_others(path, changed.read_approval(path), rows + already)
    emit(
        result(
            TOOL,
            "ok",
            f"{since} からの差分で版を上げるプラグイン {len(rows)} 件（{a.plugin} を除く）",
            changed.plugin_items(rows) + unmerged_items(skipped),
            {"since": since, "plugins": len(rows), "already": [r.name for r in already], "decided": decided and str(decided)},
        )
    )


def pr_view(root, n, fields):
    """gh pr view <n> --json <fields> を読む。GraphQL が上限なら gh_parts が REST で読み直す。"""
    what = f"gh pr view {n}"
    r = gh_parts.view_json("pr", n, fields, cwd=str(root))
    if r.returncode == 127 and "gh を実行できない" in r.stderr:
        raise StepError("gh が無い", EXIT_PRECONDITION)
    if r.returncode != 0:
        raise StepError(f"{what} が失敗: {r.stderr.strip()[:300]}")
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        raise StepError(f"{what} の出力を読めない", 2)


def unmerged(d):
    """gh pr view の出力がマージされていない PR を指すか。state の無い出力はマージ済みとして扱う。"""
    return isinstance(d, dict) and d.get("state", "MERGED") != "MERGED"


def require_merged(found, prs):
    """マージ済みが 0 件なら、書き込む前に前提エラーで止める（既存の節や欄を空で上書きしない）。"""
    if not found:
        raise StepError(f"渡した PR {' '.join(f'#{n}' for n in prs)} にマージ済みが無い", EXIT_PRECONDITION)
    return found


def pr_titles(root, prs, skipped=None):
    """PR ごとに (番号, 箇条) を返す。マージされていない PR は載せず、番号を skipped へ足す。"""
    items = []
    for n in prs:
        d = pr_view(root, n, "title,state")
        if unmerged(d):
            skipped is not None and skipped.append(n)
            continue
        try:
            title = d["title"].strip()
        except (TypeError, KeyError, AttributeError):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        items.append((n, f"- {title}（#{n}）"))
    return require_merged(items, prs)


def _update_changelog(root, a, items) -> dict:
    """CHANGELOG.md へこの版の節を足すか、既存の節へ PR を挿す（見出しは基底の版。開発版の接尾辞は載せない）。"""
    cl = root / "CHANGELOG.md"
    lines = cl.read_text(encoding="utf-8").split("\n")
    a.plugin = plugin_of(root, a)
    head, (at, end) = f"## [{a.plugin} {base_of(a.version)}]", changelog_span(lines, a.plugin, a.version)
    if at is None:
        section = _add_changelog_section(lines, head, items)
    else:
        section = _insert_into_changelog_section(lines, at, end, items)
    cl.write_text("\n".join(lines), encoding="utf-8")
    return section


def _add_changelog_section(lines, head, items) -> dict:
    """最初の版の節の前へ、この版の節を足す（lines をその場で書き換える）。"""
    first = next((i for i in h2_lines(lines) if lines[i].startswith("## [")), len(lines))
    block = [f"{head} - {today()}", ""] + [b for _, b in items] + [""]
    if first == len(lines) and lines and lines[-1] != "":
        block = [""] + block
    lines[first:first] = block
    return {
        "kind": "section",
        "name": "CHANGELOG.md",
        "result": "added",
        "heading": block[0] or block[1],
        "added": [n for n, _ in items],
    }


def _insert_into_changelog_section(lines, at, end, items) -> dict:
    """既存の節（lines[at:end]）の末尾へ、まだ載っていない PR だけを挿す（lines をその場で書き換える）。"""
    body = "\n".join(lines[at:end])
    add = [(n, b) for n, b in items if f"#{n}）" not in body and f"#{n})" not in body]
    ins = end
    while ins - 1 > at and lines[ins - 1].strip() == "":
        ins -= 1
    lines[ins:ins] = [b for _, b in add]
    return {"kind": "section", "name": "CHANGELOG.md", "result": "added", "heading": lines[at], "added": [n for n, _ in add]}


def cmd_changelog(a):
    root = git_root(a.root)
    if not (root / "CHANGELOG.md").is_file():
        raise StepError("CHANGELOG.md が無い", EXIT_PRECONDITION)
    skipped = []
    items = pr_titles(root, a.prs, skipped)
    sections = [_update_changelog(root, a, items)]
    emit(
        result(
            TOOL,
            "ok",
            f"{len(items)} 件の PR を {len(sections)} 箇所へ並べた{unmerged_note(skipped)}",
            sections + unmerged_items(skipped),
            {"version": a.version, "prs": len(items), "unmerged": skipped},
        )
    )


def run_checks(root):
    """リリース前のチェックを回し、[(名前, 通ったか, 末尾の出力)] を返す。"""
    res = []
    for name, cmd in (
        ("check-doc-staleness", ["python3", "scripts/check-doc-staleness.py", "--root", str(root)]),
        ("validate-runtime-plugins", ["bash", "scripts/validate-runtime-plugins.sh"]),
    ):
        if not (root / cmd[1]).is_file():
            res.append((name, None, "スクリプトが無い"))
            continue
        p = run(cmd, cwd=root, check=False)
        res.append((name, p.returncode == 0, "\n".join((p.stdout + p.stderr).strip().split("\n")[-3:])))
    return res


def find_pr(root, head, base, states=("OPEN",)):
    """head → base の PR を探す。states の順に最初に見つかった {number, state} を返す。"""
    args = ["pr", "list", "--head", head, "--base", base, "--state", "all", "--json", "number,state", "--limit", "20"]
    items = gh_json(root, args, "gh pr list") or []
    for st in states:
        for it in items:
            if it.get("state") == st:
                return it
    return None


def create_pr(root, base, head, title, body):
    p = gh_parts.gh(["pr", "create", "--base", base, "--head", head, "--title", title, "--body", body], cwd=root)
    if p.returncode != 0:
        raise StepError(f"gh pr create（{head} → {base}）が失敗: {p.stderr.strip()[:300]}")
    m = re.search(r"/pull/(\d+)", p.stdout)
    if not m:
        raise StepError(f"作った PR の番号を読めない: {p.stdout.strip()[:200]}", 2)
    return int(m.group(1))


def pr_check_buckets(root, n):
    p = gh_parts.gh(["pr", "checks", str(n), "--json", "name,bucket"], cwd=root)
    try:
        return json.loads(p.stdout or "[]") or []
    except ValueError:
        return []


def wait_and_merge(root, n, expect=None, ci_wait=3600.0):
    """PR のチェックを merge-when-green で待って（上限 ci_wait 秒）マージする。止まったらその summary で止める。
    CI の基盤待ち（75）は同じ 75 で、中身の失敗は 1 で止める（#1645）。後片付けは行わせない。expect を渡すと、
    PR の先端がその SHA のときだけマージし、違えば HeadMoved を投げる。
    """
    script = Path(__file__).resolve().parent / "merged-steps.py"
    cmd = [sys.executable, str(script), "merge-when-green", str(n), "--no-cleanup", "--interval", "5", "--timeout", f"{ci_wait:g}"]
    p = run(cmd + (["--expect-head", expect] if expect else []), cwd=root, check=False)
    try:
        out = json.loads((p.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {}
    if p.returncode != 0:
        if any(isinstance(i, dict) and i.get("result") == "head_moved" for i in out.get("items") or []):
            raise HeadMoved(out.get("summary") or "")
        code = EXIT_INFRA_WAIT if p.returncode == EXIT_INFRA_WAIT else 1
        raise StepError(f"PR #{n} をマージできない: {out.get('summary') or p.stderr.strip()[:300]}", code)
    return merge_commit_of(root, n)


def merge_commit_of(root, n):
    d = pr_view(root, n, "mergeCommit") or {}
    return ((d.get("mergeCommit") or {}).get("oid")) or None


def cmd_release(a):
    root = git_root(a.root)
    ver = a.version
    base, prod, plugin = release_decl(root, a)
    try:
        approved = approval.approved_of(root, a, plugin)
    except approval.Gate as g:
        emit(g.out, EXIT_GATE)
    plugins = [s.strip() for s in (a.plugins or plugin).split(",") if s.strip()]
    if (branch := git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()) != f"release/v{ver}":
        raise StepError(f"作業ツリーのブランチが release/v{ver} でない: {branch}", EXIT_PRECONDITION)
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StepError("作業ツリーにコミットしていない変更がある（bump と changelog をコミットしてから呼ぶ）", EXIT_PRECONDITION)
    bump.require_bumped(root, plugin, ver)

    git(root, "push", "-q", "-u", "origin", "HEAD")

    # 開発版の PR（release/v<版> → ベースブランチ）
    pr = find_pr(root, branch, base, states=("OPEN", "MERGED"))
    if pr and pr["state"] == "MERGED":
        # マージ済みの PR へ後から積んだコミットは GitHub が開き直さずベースブランチに入らない。入っていなければ PR を作り直す
        git(root, "fetch", "-q", "origin", base)
        pr = pr if git(root, "merge-base", "--is-ancestor", "HEAD", f"origin/{base}", check=False).returncode == 0 else None
    if pr is None:
        section = changelog_section(root, ver, plugin)
        mark = {True: "pass", False: "fail", None: "skip"}
        body = "\n".join(
            [
                f"{plugin} v{ver} のリリース（{a.channel}）。対象の plugin: {', '.join(plugins)}",
                "",
                "## 含む PR",
                "",
                section or "（CHANGELOG.md に該当の節が無い）",
                "",
                "## チェックの結果",
                "",
                *[f"- {name}: {mark[ok]}" + (f"（{tail.splitlines()[-1]}）" if tail else "") for name, ok, tail in run_checks(root)],
                "",
                "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
            ]
        )
        pr = {"number": create_pr(root, base, branch, f"Release: {plugin} v{ver}", body), "state": "OPEN"}
    release_pr = pr["number"]
    release_commit = wait_and_merge(root, release_pr, ci_wait=a.ci_wait) if pr["state"] == "OPEN" else merge_commit_of(root, release_pr)

    metrics = {
        "channel": a.channel,
        "version": ver,
        "release_pr": release_pr,
        "main_pr": None,
        "tag": None,
        "merge_commit": release_commit,
        "plugins": ",".join(plugins),
    }
    items = [{"kind": "pr", "name": f"#{release_pr}", "result": "merged", "base": base}]
    if a.channel == "dev":
        emit(result(TOOL, "ok", f"{plugin} v{ver} を {base} へ出した（#{release_pr}）", items, metrics))

    # 本番: ベースブランチ → 本番チャネル、タグ、GitHub Release（利用者の承認を得てから呼ぶ）
    tag = f"{plugin}--v{ver}"
    git(root, "fetch", "-q", "origin", "--tags")
    if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0:
        raise StepError(f"タグ {tag} は既にある")
    # 承認したコミットの後に、配布の PR の外の変更がベースブランチへ入っていないか（#815）
    allowed = approval.release_pr_allowed(root, release_pr, pr_view)
    verdict = approval.check_approved(root, approved, base, allowed)
    if not verdict.ok:
        emit(approval.gate(root, a, plugin, base, verdict, items, metrics), EXIT_GATE)
    metrics.update({"approved_sha": approved, "compared_head": verdict.tip})
    mp = find_pr(root, base, prod, states=("OPEN",))
    main_pr = (
        mp["number"]
        if mp
        else create_pr(
            root,
            prod,
            base,
            f"Release: {plugin} v{ver} を {prod} へ",
            f"{plugin} v{ver} を {prod} へ出す（開発版の PR #{release_pr}）。\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        )
    )
    try:
        merge = wait_and_merge(root, main_pr, expect=verdict.tip, ci_wait=a.ci_wait)  # 比べた先端だけを本番チャネルへ入れる
    except HeadMoved:
        moved = approval.check_approved(root, approved, base, allowed)
        emit(approval.gate(root, a, plugin, base, moved, items, metrics, moved=True), EXIT_GATE)
    git(root, "fetch", "-q", "origin")
    if not merge:
        merge = git(root, "rev-parse", f"origin/{prod}").stdout.strip()
    _publish_tag_and_release(root, plugin, ver, tag, merge)

    metrics.update({"main_pr": main_pr, "tag": tag, "merge_commit": merge})
    items += [
        {"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod},
        {"kind": "tag", "name": tag, "result": "pushed"},
        {"kind": "release", "name": tag, "result": "created"},
    ]
    swept, sweep_metrics, nxt = _sweep_candidates(root, merge)
    items += swept
    metrics.update(sweep_metrics)
    emit(result(TOOL, "ok", f"{plugin} v{ver} を {prod} へ出し、{tag} と GitHub Release を作った", items, metrics, None, nxt))


def _publish_tag_and_release(root, plugin, ver, tag, merge):
    """本番チャネルへ入ったコミットにタグを打って push し、CHANGELOG.md の節を本文に GitHub Release を作る。"""
    git(root, "tag", "-a", tag, merge, "-m", f"{plugin} v{ver}")
    git(root, "push", "-q", "origin", tag)

    notes = changelog_section(root, ver, plugin) or f"{plugin} v{ver}"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(notes + "\n")
        notes_file = f.name
    try:
        p = gh_parts.gh(["release", "create", tag, "--title", f"{plugin} v{ver}", "--notes-file", notes_file, "--latest"], cwd=root)
    finally:
        os.unlink(notes_file)
    if p.returncode != 0:
        raise StepError(f"gh release create {tag} が失敗: {p.stderr.strip()[:300]}")


def _sweep_candidates(root, merge):
    """後片付け: この版に入ったブランチの退避先（merged の worktree-trash）を回収の候補として挙げる（#824）。
    退避先は Git にも本番のコミットにも無い利用者のファイルを含むため、本番の承認だけでは消さない（C3・C4）。
    消すのは、候補を人へ示して承認を得た後に、承認した名前だけを --only で渡す sweep-trash である。
    (items, metrics, next) を返す。
    """
    swept, sweep_metrics = trash.sweep(root, merge)
    cmd = trash.sweep_command(merge, swept)
    nxt = cmd and f"回収の候補の退避先（items の kind: trash・result: candidate）を人へ示し、承認した名前だけを渡して消す: {cmd}"
    return swept, sweep_metrics, nxt


def cmd_record(a):
    """本番の配布の後に、本番のリリースの PR（release/v<版> → ベースブランチ）へリリース記録を 1 件コメントで書く（#1273）。

    タグ <plugin>--v<版> が origin に無い・同じタグの GitHub Release が無い・マージ済みの PR が無いなら、書かずに 3 で止まる
    （記録の `段階: 本番` は配布が終わった事実を表す）。PR の最後のリリース記録が同じ版・同じスプリントの PR の本番の記録なら
    書かずに ok（exists）を返す。組み立てと投稿は release_lib/record.py、形は lib/dist_record.py が持つ。
    """
    root = git_root(a.root)
    if a.promote:
        return record_promote(root, a)
    ver = a.version
    base, _prod, plugin = release_decl(root, a)
    tag = f"{plugin}--v{ver}"
    if git(root, "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}", check=False).returncode != 0:
        raise StepError(f"origin にタグ {tag} が無い（本番の配布が済んでいない）", EXIT_PRECONDITION)
    git(root, "fetch", "-q", "origin", "--tags", check=False)  # 直前の正式版とタグの時刻を読む
    if gh_parts.gh(["release", "view", tag, "--json", "tagName"], cwd=root).returncode != 0:
        raise StepError(f"GitHub Release {tag} が無い（本番の配布が済んでいない）", EXIT_PRECONDITION)
    pr = find_pr(root, f"release/v{ver}", base, states=("MERGED",))
    if pr is None:
        raise StepError(f"release/v{ver} → {base} のマージ済みの PR が無い", EXIT_PRECONDITION)
    n = pr["number"]
    prev_tag = release_tag_before(root, plugin, current=tag)
    prev = prev_tag[len(f"{plugin}--v") :] if prev_tag else None
    body = record.body_of(root, plugin, ver, tag, prev_tag, a.prs)
    emit(result(TOOL, "ok", *record.write(root, n, ver, prev, body, a.prs, f"v{ver}")))


def record_promote(root, a):
    """昇格の経路（--promote）: 最後にマージした昇格の Pull Request（--head → --base）へリリース記録を書く（#837）。
    版はマージのコミット（`<本番チャネル> <短い SHA>`）、直前の版はその 1 つ目の親。マージ済みの PR が無ければ 3。"""
    if not (a.head and a.base):
        raise StepError("--promote には --head（ベースブランチ）と --base（本番チャネル）が要る", EXIT_PRECONDITION)
    pr = find_pr(root, a.head, a.base, states=("MERGED",))
    sha = merge_commit_of(root, pr["number"]) if pr else None
    if not sha:
        raise StepError(f"{a.head} → {a.base} のマージ済みの昇格の PR が無い（本番の配布が済んでいない）", EXIT_PRECONDITION)
    n, prev, ver = pr["number"], record.parent_of(root, a.base, sha), f"{a.base} {sha[:7]}"
    body = record.promote_body_of(n, a.head, a.base, prev, sha[:7], a.prs)
    emit(result(TOOL, "ok", *record.write(root, n, ver, prev, body, a.prs, ver)))


def cmd_approval_facts(a):
    root = git_root(a.root)
    repo = repo_lib.owner_repo(root)
    if not repo:
        raise StepError("リポジトリの owner/name を決められない", EXIT_PRECONDITION)
    base, prod, plugin = release_decl(root, a)
    git(root, "fetch", "-q", "origin", "--tags")
    cur_tag = f"{plugin}--v{a.version}"
    prev = a.prev_tag or release_tag_before(root, plugin, cur_tag)
    if not prev:
        raise StepError("前のタグを決められない（--prev-tag を渡す）", EXIT_PRECONDITION)
    dev = git(root, "rev-parse", f"origin/{base}").stdout.strip()

    stat = git(root, "diff", "--shortstat", f"origin/{prod}...origin/{base}").stdout.strip()
    files = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", stat)) else 0
    ins = int(m.group(1)) if (m := re.search(r"(\d+) insertions?", stat)) else 0
    dels = int(m.group(1)) if (m := re.search(r"(\d+) deletions?", stat)) else 0

    items, rows = [], []
    for n in a.prs:
        d = pr_view(root, n, "number,title,state,mergeCommit,url") or {}
        oid = (d.get("mergeCommit") or {}).get("oid") or ""
        checks = pr_check_buckets(root, n)
        passed = sum(1 for c in checks if c.get("bucket") == "pass")
        url = d.get("url") or f"https://github.com/{repo}/pull/{n}"
        items.append(
            {
                "kind": "pr",
                "name": f"#{n}",
                "result": str(d.get("state", "")).lower() or "unknown",
                "url": url,
                "title": d.get("title", ""),
                "merge_commit": oid,
                "checks_passed": passed,
                "checks": len(checks),
            }
        )
        rows.append(f"#{n} {d.get('title', '')}（{d.get('state', '')}・{oid[:8] if oid else '—'}・CI {passed}/{len(checks)}）")

    compare = f"https://github.com/{repo}/compare/{prev}...{dev}"
    path = approval_present(
        TOOL,
        f"v{a.version}",
        title=f"{plugin} v{a.version} の本番への配布",
        targets=[{"url": compare, "title": f"{prev} → {base}（{dev[:8]}）", "base_head": f"{prod} ← {base}"}],
        change=f"`{prod}...{base}` の差分 {files} ファイル / +{ins} / −{dels}",
        judge=[
            ac.material_row(dev),
            ("版数", a.version),
            ("含む PR", "\n".join(rows) or "—"),
            ("配る中身", NOTES_PENDING),
            ("検証への配布で確かめたこと", NOTES_PENDING),
        ],
        consent=[f"{plugin} v{a.version} を {prod} へ出し、タグ {cur_tag} と GitHub Release を作る"],
        rollback=(
            f"タグ {cur_tag} と GitHub Release を消し、{prod} を {prev} の内容へ戻す PR を出す。"
            "導入済みの利用者の環境は利用者の側の操作（前の版の導入し直し）でしか戻せない。"
        ),
    )
    emit(
        result(
            TOOL,
            "gate",
            f"{plugin} v{a.version} の本番承認の提示物を書いた（PR {len(a.prs)} 件）",
            items,
            {"files": files, "insertions": ins, "deletions": dels, "prev_tag": prev, "compare": compare, "approved_sha": dev},
            path,
            f"利用者の承認を得たら release-steps.py release --version {a.version} --channel prod --approved-sha {dev}",
        ),
        EXIT_GATE,
    )


def cmd_approve(a):
    """ゲート 2 の承認を承認資料へ記録する（#815 の I10。本体は release_lib/approval.py の approve）。"""
    emit(approval.approve(git_root(a.root), a))


CHANGES_HEADING = others.CHANGES_HEADING
RISKS_HEADING = "## 未検証・残る危険"
NOTES_PENDING = "（release-steps.py notes --approval が PR 本文の「利用者向けの変化」から書く）"
RUNTIME_NAMES = {"claude": "Claude Code", "codex": "Codex", "kiro": "Kiro", "agy": "Antigravity"}


def pr_notes(root, prs, skipped=None):
    """PR ごとに (番号, 利用者向けの変化の箇条, 未検証・残る危険の箇条, 題名で代えたか, 移行の手順の箇条) を返す。
    マージされていない PR は配る中身に入らないため載せず、番号を skipped へ足す。"""
    out = []
    for n in prs:
        d = pr_view(root, n, "title,body,state")
        if unmerged(d):
            skipped is not None and skipped.append(n)
            continue
        if not isinstance(d, dict) or not isinstance(d.get("title"), str):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        body = d.get("body") or ""
        items = gh_sections.section_items(body, CHANGES_HEADING, n)
        risks = gh_sections.section_items(body, RISKS_HEADING, n)
        out.append((n, items or [f"{d['title'].strip()}（#{n}）"], risks, not items, others.migration_items(body, n)))
    return require_merged(out, prs)


def replace_lines_under(lines, at, block):
    """lines[at] の見出しから次の `## ` までの中身を block に差し替える。"""
    lines[at + 1 : next_h2(lines, at)] = [""] + block + [""]


def write_notes(root, version, plugin, bullets, migration=()):
    """CHANGELOG.md の版の節を箇条にする。移行の手順があれば `### 移行の手順` の下へ並べる（I6・I7）。
    plugin の README は読まず書かない（#1867: 更新情報は CHANGELOG.md の版の節が正本）。"""
    bullets = [*bullets, *(["", "### 移行の手順", "", *migration] if migration else [])]
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        raise StepError("CHANGELOG.md が無い", EXIT_PRECONDITION)
    lines = cl.read_text(encoding="utf-8").split("\n")
    head = f"## [{plugin} {base_of(version)}]"
    at = changelog_span(lines, plugin, version)[0]
    if at is None:
        raise StepError(f"CHANGELOG.md に {head} の節が無い（先に changelog を走らせる）", EXIT_PRECONDITION)
    replace_lines_under(lines, at, bullets)
    cl.write_text("\n".join(lines), encoding="utf-8")
    return [{"kind": "section", "name": "CHANGELOG.md", "result": "replaced", "heading": lines[at], "lines": len(bullets)}]


def approval_cell(text):
    """提示物の表の 1 セル（改行は <br>）。"""
    return mdtable.cell_text(text.replace("\n", "<br>"))


def write_approval(path, version, bullets, risks, verified, ref, migration=()):
    if not path.is_file():
        raise StepError(f"提示物 {path} が無い（先に approval-facts を走らせる）", EXIT_PRECONDITION)
    names = "・".join(RUNTIME_NAMES.get(r, r) for r in verified)
    check = (
        f"{names} の {len(verified)} 経路で {ref} から ndf {version} を導入し、版と中身が一致した"
        f"（release-verification-steps.py verify-install）"
        if verified
        else "—"
    )
    rows = {"配る中身": "\n".join(bullets) or "—", "検証への配布で確かめたこと": check}
    lines = path.read_text(encoding="utf-8").split("\n")
    done = []
    for i, line in enumerate(lines):
        for key, value in rows.items():
            if line.startswith(f"| {key} |"):
                lines[i] = f"| {key} | {approval_cell(value)} |"
                done.append(key)
    missing = [k for k in rows if k not in done]
    if missing:
        raise StepError(f"提示物に欄が無い: {', '.join(missing)}", EXIT_PRECONDITION)
    others.put_section(lines, RISKS_HEADING, risks or ["- PR の本文に記載が無い"], replace=False)
    others.put_section(lines, others.MIGRATION_HEADING, list(migration) or ["- 無し"])
    path.write_text("\n".join(lines), encoding="utf-8")
    return [{"kind": "cell", "name": k, "result": "written"} for k in rows] + [
        {"kind": "section", "name": RISKS_HEADING, "result": "written", "lines": len(risks)},
        {"kind": "section", "name": others.MIGRATION_HEADING, "result": "written", "lines": len(migration)},
    ]


def unmerged_items(skipped):
    return [{"kind": "pr", "name": f"#{n}", "result": "skipped", "reason": "マージされていない"} for n in skipped]


def unmerged_note(skipped):
    return f"（マージされていない {' '.join(f'#{n}' for n in skipped)} は載せない）" if skipped else ""


def cmd_notes(a):
    root = git_root(a.root)
    skipped = []
    notes = pr_notes(root, a.prs, skipped)
    bullets = [f"- {i}" for _, items, *_ in notes for i in items]
    risks = [f"- {i}" for _, _, rs, *_ in notes for i in rs]
    migration = [f"- {i}" for *_, ms in notes for i in ms]
    fallback = sum(1 for *_, by_title, _ in notes if by_title)
    if a.approval:
        path = Path(a.approval)
        path = path if path.is_absolute() else root / path
        verified = [r for r in (a.verified or "").split(",") if r]
        ref = a.ref or release_decl(root, a)[0]
        items = write_approval(path, a.version, bullets, risks, verified, ref, migration) + unmerged_items(skipped)
        emit(
            result(
                TOOL,
                "ok",
                f"提示物の欄を {len(notes)} 件の PR から書いた{unmerged_note(skipped)}",
                items,
                {
                    "version": a.version,
                    "prs": len(notes),
                    "lines": len(bullets),
                    "migration": len(migration),
                    "approval": str(path),
                    "unmerged": skipped,
                },
            )
        )
        return
    items = write_notes(root, a.version, plugin_of(root, a), bullets, migration) + unmerged_items(skipped)
    emit(
        result(
            TOOL,
            "ok",
            f"{len(notes)} 件の PR の利用者向けの変化を書いた{unmerged_note(skipped)}",
            items,
            {
                "version": a.version,
                "prs": len(notes),
                "lines": len(bullets),
                "migration": len(migration),
                "fallback": fallback,
                "unmerged": skipped,
            },
        )
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="リポジトリが宣言した配布のコマンドと、配布の決まった手順を走らせる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="段階に合うコマンドを順に実行する")
    r.add_argument("--root", type=Path, default=Path("."))
    r.add_argument("--stage", choices=("production", "verification"), required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check", help="宣言を読むだけ")
    c.add_argument("--root", type=Path, default=Path("."))

    common = common_parser()
    p = sub.add_parser("bump", parents=[common], help="plugin の版数を持つ箇所を旧版から新版へ上げる")
    p.add_argument("--plugin", required=True, help="ndf / playwright-kit / plugins/mcp の名前（例 mcp-serena）")
    p.add_argument("--to", required=True, type=version_arg)
    p.add_argument(
        "--base",
        help="起点のブランチ（既定は宣言のベースブランチ）。作業場所の版がすでに --to で、origin/<base> の版と違うなら打ち直しとして ok を返す",
    )
    p.set_defaults(func=cmd_bump)
    p = sub.add_parser("changed-plugins", parents=[common], help="前のタグからの差分にある他のプラグインと上げる版")
    p.add_argument("--since", help="前のタグ（省略時は <--plugin>--v の接尾辞の無い最も新しいタグ）")
    p.add_argument("--plugin", help="除くプラグイン（既定は宣言の release.plugin）")
    p.add_argument("--prs", nargs="+", type=int, metavar="PR番号", help="版に含む PR（本文と閉じる課題から上げ幅の候補を出す）")
    p.add_argument("--approval", help="承認資料。「版を上げる他のプラグイン」の節を書く")
    p.add_argument("--set", action="append", metavar="名前=上げ幅", help="--approval: 承認ゲート 2 で決めた上げ幅へ行を書き直す")
    p.add_argument("--decided", help="承認資料の表の上げ幅で上げた版を返す（本番の bump-others）")
    p.set_defaults(func=cmd_changed_plugins)

    p = sub.add_parser("changelog", parents=[common], help="CHANGELOG.md の版の節へ PR のタイトルを並べる")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin）")
    p.set_defaults(func=cmd_changelog)

    p = sub.add_parser(
        "release",
        parents=[common],
        help="release/v<版> をベースブランチへマージし、prod なら本番チャネルへ出してタグと Release を作る（ブランチは .ndf/worktree.json）",
    )
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--channel", required=True, choices=("dev", "prod"))
    p.add_argument("--plugins", help="カンマ区切り（例 ndf,mcp-serena。既定は宣言の release.plugin。先頭がタグと題に使うプラグイン）")
    p.add_argument("--ci-wait", type=float, default=3600.0, help="待つ PR ごとの CI の待ちの上限（秒）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--approved-sha", metavar="SHA", help="prod: 承認したコミット（approval-facts の metrics.approved_sha。40 桁）")
    g.add_argument("--approval", help="prod: 承認資料（「承認したコミット」と「承認の記録」の欄を読む）")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("record", parents=[common], help="本番の配布の後に、本番のリリースの PR へリリース記録（## 配布の記録）を書く")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--version", type=version_arg)
    g.add_argument("--promote", action="store_true", help="昇格の Pull Request（--head → --base）のマージで出たときの記録")
    p.add_argument("--head", help="--promote: 昇格の Pull Request の head（ベースブランチ）")
    p.add_argument("--base", help="--promote: 昇格の Pull Request の base（本番チャネル）")
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号", help="スプリントの PR（`スプリント:` の行に並べる）")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin。タグ <名前>--v<版> に使う）")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("approve", parents=[common], help="本番への配布の承認（ゲート 2）を承認資料へ記録する")
    p.add_argument("--approval", required=True, help="承認資料")
    p.add_argument("--approved-sha", required=True, metavar="SHA", help="提示した承認資料を書いた approval-facts の metrics.approved_sha")
    p.add_argument("--by", required=True, choices=("user", "mvv"), help="承認した者")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("approval-facts", parents=[common], help="本番承認の提示物のうち機械で作れる部分を書き出す")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--prev-tag")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin。タグ <名前>--v<版> に使う）")
    p.set_defaults(func=cmd_approval_facts)

    p = sub.add_parser(
        "notes", parents=[common], help="PR 本文の「利用者向けの変化」から CHANGELOG の版の節（--approval なら提示物の欄）を組む"
    )
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin）")
    p.add_argument("--approval", help="本番承認の提示物。渡すと「配る中身」「検証への配布で確かめたこと」の欄を書く")
    p.add_argument("--verified", default="", help="--approval: 導入を確かめた経路（カンマ区切り。例 claude,codex,kiro）")
    p.add_argument("--ref", help="--approval: 検証への配布で導入した ref（既定は宣言のベースブランチ）")
    p.set_defaults(func=cmd_notes)
    return deploy.add_deploy_parser(sub, common, ap)  # deploy-facts（手動反映の本番系の承認資料。#1454）


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd not in ("run", "check"):
        return main_with(ap, lambda a: TOOL, argv)
    root = args.root.resolve()
    try:
        if args.cmd == "check":
            return 0 if load_steps(root) is not None else 2
        return run_steps(root, args.stage, args.version, args.dry_run)
    except DeclarationError as e:
        print(f"宣言を読めない（{DECLARATION}）: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
