#!/usr/bin/env python3
"""リポジトリが宣言したリリースコマンドを、デプロイの段階に合わせて走らせる（#893）。

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

デプロイの決まった手順（#862。試作は #827 の phase-steps.py）も同じスクリプトに置く。結果は
`lib/step_result.py` の形の 1 行の JSON で、終了コードは 0 = ok / 1 = 失敗（stopped）/ 2 = 読めない / 3 = 前提が無い /
5 = CD の結果が失敗（record）/ 6 = 公開の欠け（release が merge queue か人のマージを待つ）/ 10 = 本番へのリリースの承認が要る
（approval-facts）/ 75 = 待つ PR の CI の基盤待ち（中身の失敗ではない）。

    python3 release-steps.py bump           --plugin <名前> --to <版> [--base <ベースブランチ>] [--root <dir>]
    python3 release-steps.py changed-plugins [--since <タグ>] [--plugin <名前>] [--root <dir>]  # 差分のある他のプラグイン
                                            [--prs <PR番号>...] [--approval <承認資料> [--set <名前>=<上げ幅>]...]
                                            [--decided <承認資料>]
    python3 release-steps.py review-open    --version <版> --diff pr|resolution [--production <ブランチ>]  # 差分が空なら 3
    python3 release-steps.py review-close   --version <版>
    python3 release-steps.py changelog      --version <版> --prs <PR番号>... [--plugin <名前>] [--root <dir>]
    python3 release-steps.py release        --version <版> --channel dev|prod [--plugins <名前>,...] [--root <dir>]
                                            [--approved-sha <SHA> | --approval <承認資料>]   # CD の無い prod はどちらかが要る
    python3 release-steps.py record         --version <版> --prs <PR番号>... [--plugin <名前>] [--root <dir>]   # prod の後
    python3 release-steps.py record         --promote --head <ベースブランチ> --base <本番チャネル> --prs <PR番号>... [--cd-result <ファイル>] [--root <dir>]
    python3 release-steps.py record         --merged --base <本番チャネル> --prs <PR番号>... [--cd-result <ファイル>] [--root <dir>]
    python3 release-steps.py publish        --revision <SHA> [--root <dir>]   # リリースタグと GitHub Release を作る（CD と切り戻し）
    python3 release-steps.py cd-result      --base <本番チャネル> [--head <ブランチ> | --prs <PR番号>...] --out <ファイル> [--root <dir>]
    python3 release-steps.py cd-guidance    [--root <dir>]   # CD の案内
    python3 release-steps.py approve        --approval <承認資料> --approved-sha <SHA> --by user|mvv [--root <dir>]
    python3 release-steps.py approve        --approval <承認資料> --facts-from <開発版の状態> [--answer-from <状態>] --by user
    python3 release-steps.py approve        --pr <本番の PR> (--approved-sha <SHA> | --facts-from <状態> [--answer-from <状態>]) --by user|mvv
    python3 release-steps.py approval-post  --pr <本番の PR> --approval <承認資料>   # 承認資料をリリースの PR へ置く
    python3 release-steps.py approval-check --pr <PR番号>   # 証跡のチェック（workflow が打つ）
    python3 release-steps.py release-case   [--root <dir>]   # リリースの場合
    python3 release-steps.py approval-facts --version <版> --prs <PR番号>... [--prev-tag <タグ>] [--plugin <名前>] [--root <dir>]
    python3 release-steps.py notes          --version <版> --prs <PR番号>... [--approval <提示物>]
                                            [--verified claude,codex] [--ref <ブランチ>] [--plugin <名前>] [--root <dir>]

release は release/v<版> をベースブランチへマージのコミットで取り込む。版数を持つ箇所だけが衝突すれば
release/v<版>-sync で本番の版を取って取り込み、ほかが衝突すれば 1 で止まる（release_lib/version_merge.py。#2436 の R5）。
release --channel prod は承認したコミットを受け取り、`origin/release/v<版>` の先端と比べ、承認の外の変更があれば
本番チャネルの PR もタグも作らずに承認ゲート（10）で止まる（release_lib/approval.py。#815）。承認の後にベースブランチが
進んでも止まらず、本番チャネルの PR は head を release/v<版> にして比べた先端だけを出す（#2436 の R2）。本番チャネルの
PR がマージ済みなら、比較とマージを飛ばし、タグと GitHub Release のうち無いものだけを作る（途中で止まったデプロイの再開）。
本番ブランチへ入れるマージは、どの経路も本番の取り込み（`release_lib/prod_land.py`。検査の待ち → head の照合 → 宣言の主体と方式の
`land_approved`）を通り、merge queue へ入れた・人のマージを待つときは公開せずに 6 で返す（#3703）。trunk（ベースブランチと本番ブランチが
同じ）の CD の無い本番は `release_lib/trunk_release.py` が、承認を読む前に版上げの PR を開き、承認が無ければ 10 で止まる。
ここまでは CD の無い場合（`lib/release_case.py` の no-cd）である。CD のある場合（cd-no-review / cd-review。#2773）の prod は
承認したコミットの引数を受けず、本番の PR の先端を PR の承認の証跡（cd-no-review）か承認のレビュー（cd-review）と比べ、
一致すれば管理者の迂回なしでマージし、一致しなければ本番の PR を開いたまま承認ゲート（10）で止まる。dev は
ブランチ戦略の宣言の開発版のチャネル（`channels` の `dev`）に workflow があれば、開発版の PR のマージをその workflow に任せて結果を待つ。

record は release --channel prod の後に、本番のリリースの PR（release/v<版> → ベースブランチ。マージ済みが無ければ
release/v<版>-sync → ベースブランチ）へリリース記録
（`## リリース記録`。形は lib/dist_record.py）をコメントで 1 件書く。タグと GitHub Release が無ければ書かずに 3 で止まり、
同じ記録が既にあれば書かずに ok を返す。投稿の失敗は 1、PR を読めなければ 2。metrics.release_pr_url を
プランの `pr_from` が報告の Pull Request へ移す（#1273）。
`record --promote` はリリースの Pull Request で届く経路で、最後にマージしたリリースの Pull Request（--head → --base）へ同じ形の記録を書く。
`版:` はマージのコミット（`<本番チャネル> <短い SHA>`）である（#837）。

ブランチ（ベースブランチ・本番ブランチ）はブランチ戦略の宣言（`.ndf/project.json` の branch_strategy.branches）、
プラグイン（タグ `<名前>--v<版>`・題 `Release: <名前> v<版>`）は引数 → ブランチ戦略の宣言の release.plugin から読む（#1336）。
CHANGELOG.md の置き場と見出し・`plugins/<名前>/` の配置・版の形は、形 package-plugin の約束である（form-package-plugin.md）。

notes は PR 本文の `## 利用者向けの変化` の節（無い・「無し」の PR は題名）から、CHANGELOG.md の版の節を
組み直す。bump・changelog・notes は plugin の README に触れない（#1867。更新情報は CHANGELOG.md の版の節が正本）。
`--approval` を渡すと、代わりに本番承認の提示物の
「配る中身」「承認前の試用で確かめたこと」の欄と、PR 本文の `## 未検証・残る危険` を集めた節を書く。
このとき PR は提示物の「含む PR」の欄から読み、`--prs` は欄にあることを確かめるだけにする（無ければ 3）。
approval-facts の「含む PR」は、前のタグから承認したコミットまでにベースブランチへ入った PR（版上げの `release/` を除く）と
`--prs` の和を番号の昇順に並べる（release_lib/included.py。#1274）。
changelog と notes は未マージの PR を載せず、番号を metrics.unmerged へ出す。渡した PR がすべて未マージなら
書き込む前に 3（前提エラー）で止まる。PR の読み取り（`gh pr view --json`）は、GraphQL が上限なら REST で読み直す
（`lib/gh_parts.py` の `view_json`）。

bump は、作業場所の版がすでに `--to` で起点（origin/<base>）の版と違うなら、同じステップの打ち直しとして何もせず
ok を返す。起点の版がすでに `--to` なら（同じ版を出し直そうとしている）止まる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("schema", "versions", "bump", "md", "mdtable", "durable", "waits", "jsonschema", "yamlio")
import approved_commit as ac  # noqa: E402
import step_read  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merged_lib import trash  # noqa: E402
from release_lib import approval, bump, carry, cd_result, changed, deploy, included, instructions, materials  # noqa: E402
from release_lib import names, others, port_mark, publish, record, replaced, review_range, reviewed, strategy_route  # noqa: E402
import limits
from release_lib import prod_land, trunk_release, version_merge  # noqa: E402
from release_lib.prod_land import land, pr_check_buckets  # noqa: E402,F401 -- 本番の取り込みと検査の読み取り（ops として渡す）
from release_lib.approval import HeadMoved  # noqa: E402
from release_lib.context import ReleaseCtx  # noqa: E402
from release_lib.land_read import merged_or_tip  # noqa: E402
from release_lib.notes_content import NotesContent  # noqa: E402
from release_lib.names import changelog_section, changelog_span, h2_lines, next_h2, plugin_of, release_decl  # noqa: E402
from release_lib.names import drop_untagged, require_unreleased, require_untagged, tag_glob, tag_name, tag_template_of, tag_version  # noqa: E402
from release_lib.included import outside_items, outside_note  # noqa: E402
from release_lib.publish import prev_release, release_tag_before  # noqa: E402
from step_result import (
    EXIT_CD_FAILED,
    EXIT_GATE,
    EXIT_INCOMPLETE,
    EXIT_INFRA_WAIT,
    EXIT_PRECONDITION,
    PRESENT_HEADINGS,
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
import repo as repo_lib  # noqa: E402
import strategy_rows  # noqa: E402
import delivery  # noqa: E402
from release_lib.declared_steps import DECLARATION, DeclarationError, load_steps, run_steps  # noqa: E402,F401


# ---------- デプロイの決まった手順（#862） ----------

TOOL = "release"
# 本番チャネルへのマージを待つ間にベースブランチが許可リスト（UNGATED）の下に収まるコミットだけで進んだとき、比べ直して入れ直す回数（#2221）
HEAD_MOVED_RETRIES = limits.value("release-steps.head-moved", "attempts") - 1


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


def _bump_manifests(ed, pdir, old, desc_re):
    """plugin.json 群の version と description の版を書き換える箇所を `ed` へ足す。"""
    for rel, _ in (
        (".claude-plugin/plugin.json", True),
        (".codex-plugin/plugin.json", False),
        ("plugin.json", False),
    ):
        f = pdir / rel
        if not f.is_file():
            continue
        ed.sub(f, r'^\s*"version"\s*:', f"{rel} の version")
        if f"(v{old})" in f.read_text(encoding="utf-8"):
            ed.sub(f, desc_re, f"{rel} の description")


def _bump_marketplace_and_readme(ed, root, plugin, old, desc_re):
    """marketplace.json の description と README.md のプラグイン一覧表の版を書き換える箇所を `ed` へ足す。"""
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


def _bump_ndf_docs(ed, root, pdir):
    """ndf だけが持つ版の記載（README 概要・Codex のパス例・list の出力例・版の文書）を `ed` へ足す。"""
    ed.sub(root / "README.md", r"\*\*NDFプラグイン v", "README.md の概要の版")
    nr = pdir / "README.md"
    ed.sub(nr, r"/plugins/cache/ai-plugins/ndf/", "plugins/ndf/README.md の Codex のパス例", count=2)
    ed.sub(nr, r"ndf@ai-plugins\s+installed", "plugins/ndf/README.md の codex plugin list の出力例")
    bump.versioning_doc(ed)


def plan_bump(root, pdir, plugin, old, new):
    """`plugin` の版を `old` から `new` へ上げる箇所を集めた `BumpPlan` を返す（まだ書き換えない）。"""
    ed = bump.BumpPlan(root, old, new)
    desc_re = r'^\s*"description"\s*:.*\(v' + re.escape(old) + r"\)"
    _bump_manifests(ed, pdir, old, desc_re)
    _bump_marketplace_and_readme(ed, root, plugin, old, desc_re)
    if plugin == "ndf":
        _bump_ndf_docs(ed, root, pdir)
    return ed


def bump_guard(root, plugin, old, new, base) -> str | None:
    """`bump` が書き換える前の判定（#2164 の I7。中身は release_version.bump_check）。打ち直しなら origin のベースブランチの版。"""
    import release_version

    rel = (plugin_dir(root, plugin) / ".claude-plugin" / "plugin.json").relative_to(root).as_posix()
    already, stop = release_version.bump_check(root, plugin, old, new, base, rel)
    if stop:
        emit(result(TOOL, "stopped", stop.summary, [], stop.metrics), code=stop.code)
    return already


def cmd_bump(a):
    root = git_root(a.root)
    a.base = a.base or delivery.load_delivery(root).base  # 既定は宣言のベースブランチ（#1336）
    pdir = plugin_dir(root, a.plugin)
    try:
        old = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as e:
        raise StepError(f"旧版を plugin.json から読めない: {e}", 2)
    new = a.to
    already = bump_guard(root, a.plugin, old, new, a.base)
    if already:
        # 同じステップの打ち直し（作業場所の版はすでに上げてある）か、自分のデプロイの後の流し直し
        emit(
            result(
                TOOL,
                "ok",
                f"{a.plugin} はすでに {new}（origin/{a.base} は {already}）。上げ直さない",
                [],
                {"plugin": a.plugin, "from": already, "to": new, "already": True},
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


def next_release(name, old, level="patch"):
    """上げ幅（既定は PATCH）で上げた正式版（`2.3.4` と `2.3.4-dev.1` の PATCH は `2.3.5`）。"""
    try:
        return others.bumped(old, level)
    except ValueError as e:
        raise StepError(f"{name} の版を読めない: {e}", 2)


def other_plugins(root, a):
    """(前のタグ, 差分にあってまだ上げていない {名前: 前のタグの版}, 上げ済みの行)。"""
    since = a.since or prev_release(root, a.plugin, a.end) or tag_glob(a.plugin, tag_template_of(root))  # 無ければ差分を取れず止まる
    p = git(root, "diff", "--name-only", names.since_ref(root, since), a.end, check=False)
    if p.returncode:
        raise StepError(f"前のタグが無い: {since}", 2)
    pending, already = {}, []
    for name in sorted({m[1] for f in p.stdout.split() if (m := re.match(r"plugins/(?:mcp/)?([^/]+)/", f))} - {a.plugin}):
        pdir = root / "plugins" / (name if name in ("ndf", "playwright-kit") else f"mcp/{name}")
        old, head = base_version(root, pdir, since), base_version(root, pdir, a.end)
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
    a.end = strategy_route.head_ref(root, getattr(a, "head", None))  # 選んで集める取り込み方はリリースブランチの先頭（#3121）
    since, pending, already = other_plugins(root, a)
    skipped, decided = [], None
    if a.decided and pending:
        decided = changed.approval_path(root, a.decided)
        rows = changed.decided_rows(decided, pending, already)
    elif a.prs is not None:  # 候補は範囲の PR と --prs の和の本文から出す（#1885。develop へ直接入った互換の無い変更を拾う）
        prs = included.included_prs(included.release_range(root, names.since_ref(root, since), a.end), a.prs)
        data = included.pr_fields(prs, cwd=str(root))[0] or {}
        view = lambda n: data[n] if isinstance(data.get(n), dict) else pr_view(root, n, "body,state,mergeCommit")  # noqa: E731  読めない PR は根拠に書く（I4）
        rows = changed.candidate_rows(root, prs, pending, view, skipped)
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


def pr_many(root, prs, fields="title,body,state", comments=False):
    """PR の番号 → gh pr view の出力（included.read_prs。読み直しは pr_view）。"""
    return included.read_prs(root, prs, lambda n, f: pr_view(root, n, f), fields, comments)


def section_prs(root, a):
    """CHANGELOG の版の節に並べる PR の候補・前のタグ・範囲の終点・範囲の外の --prs（included.section_range。
    #1885・#2555）。範囲の終点は worktree の先頭（出す版のコミット）。配るプラグインが決まらなければ --prs だけで
    終点なし（止めるのは版の節を書く側。PR の読み出しの失敗を先に返す）。"""
    git(root, "fetch", "-q", "origin", "--tags", check=False)
    try:
        plugin = plugin_of(root, a)
    except StepError:
        return included.with_extra([], a.prs), None, None, []
    prev = prev_release(root, plugin, "HEAD", tag_name(root, plugin, a.version))  # タグを作らない宣言は前のリリースの版上げ
    head = git(root, "rev-parse", "HEAD", check=False)
    if head.returncode:  # コミットの無いリポジトリ: 範囲も終点も無い
        return included.with_extra([], a.prs), prev, None, []
    to = head.stdout.strip()
    prs, extra_out = included.section_range(root, prev, to, a.prs)
    return prs, prev, to, extra_out


def _pr_view_fields(to):
    """PR の読み出しの欄。終点 to があれば絞り込みに使うマージコミットも読む。"""
    return "title,body,state" + (",mergeCommit" if to else "")


def _within_end(root, prs, data, to, extra_out, outside):
    """終点 to が無ければ prs をそのまま、あれば範囲の外の --prs のうち終点に入っていない番号を外す（included.drop_outside。
    外した番号は outside へ足す。None なら捨てる）。"""
    return included.drop_outside(root, prs, data, to, extra_out, outside if outside is not None else []) if to else prs


def pr_titles(root, prs, skipped=None, to=None, extra_out=(), outside=None):
    """PR ごとに (番号, 箇条)（included.titles_of。スプリント経由と利用者向けの変化なしの PR は箇条にしない）。
    終点 to を渡すと、範囲の外の --prs のうち終点に入っていない番号を外して outside へ足す（included.drop_outside）。"""
    prs = included.as_included(prs)
    data = pr_many(root, [p.number for p in prs], _pr_view_fields(to))
    prs = _within_end(root, prs, data, to, extra_out, outside)
    return included.titles_of(prs, data, skipped)


def _update_changelog(root, a, items, since=None) -> list[dict]:
    """CHANGELOG.md へこの版の節を足すか、既存の節へ PR を挿す（見出しは基底の版。開発版の接尾辞は載せない）。
    リリースタグのある版の節へは書かずに止める。正式版なら、前のリリースタグ since より新しくタグの無い版の節を消す（#2555）。"""
    cl = root / "CHANGELOG.md"
    lines = cl.read_text(encoding="utf-8").split("\n")
    a.plugin = plugin_of(root, a)
    require_unreleased(root, a.plugin, a.version)
    removed = drop_untagged(root, lines, a.plugin, since, a.version)
    head, (at, end) = f"## [{a.plugin} {base_of(a.version)}]", changelog_span(lines, a.plugin, a.version)
    if at is None:
        section = _add_changelog_section(lines, head, items)
    else:
        section = _insert_into_changelog_section(lines, at, end, items)
    cl.write_text("\n".join(lines), encoding="utf-8")
    return [section, *removed]


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
    skipped, outside = [], []
    prs, since, to, extra_out = section_prs(root, a)
    items = pr_titles(root, prs, skipped, to, extra_out, outside)
    sections = _update_changelog(root, a, items, since)
    emit(
        result(
            TOOL,
            "ok",
            f"{len(items)} 件の PR を 1 箇所へ並べた{unmerged_note(skipped)}{outside_note(outside)}",
            sections + unmerged_items(skipped) + outside_items(outside),
            {"version": a.version, "prs": len(items), "unmerged": skipped, "outside": outside, "range": len(prs), "since": since},
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
    p = repo_lib.pr_create_restoring_head(root, gh_parts.gh, base, head, title, body, run_git=git)  # head が消えていれば push し直す
    if p.returncode != 0:
        raise StepError(f"gh pr create（{head} → {base}）が失敗: {p.stderr.strip()[:300]}")
    m = re.search(r"/pull/(\d+)", p.stdout)
    if not m:
        raise StepError(f"作った PR の番号を読めない: {p.stdout.strip()[:200]}", 2)
    return int(m.group(1))


def wait_and_merge(root, n, ci_wait=3600.0, admin=None):
    """ベースブランチ宛ての PR（開発版の PR・`-sync` の PR）のチェックを merge-when-green で待って（上限 ci_wait 秒）マージする。
    止まったらその summary で止める。CI の基盤待ち（75）は同じ 75 で、中身の失敗は 1 で止める（#1645）。後片付けは行わせない。
    管理者の迂回は `admin` で、省けば宣言から決める（#3672 の I6）。本番ブランチ宛ての PR は本番の取り込み（`prod_land.land`）で入れる（#3703）。
    """
    script = Path(__file__).resolve().parent / "merged-steps.py"
    cmd = [sys.executable, str(script), "merge-when-green", str(n), "--no-cleanup", "--interval", "5", "--timeout", f"{ci_wait:g}"]
    cmd += [] if (strategy_route.admin_bypass(root) if admin is None else admin) else ["--no-admin"]
    p = run(cmd, cwd=root, check=False)
    out = step_read.last_json(p.stdout) or {}
    if p.returncode != 0:
        if p.returncode == EXIT_CONFLICT:
            raise Conflicted(f"PR #{n} をマージできない: {out.get('summary') or p.stderr.strip()[:300]}")
        code = EXIT_INFRA_WAIT if p.returncode == EXIT_INFRA_WAIT else 1
        raise StepError(f"PR #{n} をマージできない: {out.get('summary') or p.stderr.strip()[:300]}", code)
    return merge_commit_of(root, n)


EXIT_CONFLICT = 4  # merge-when-green: PR がベースブランチと衝突している


class Conflicted(StepError):
    """PR がベースブランチと衝突してマージできない（merge-when-green の 4）。呼び手が扱わなければ 1 で止まる。"""

    def __init__(self, msg: str):
        super().__init__(msg, 1)


def merge_commit_of(root, n):
    d = pr_view(root, n, "mergeCommit") or {}
    return ((d.get("mergeCommit") or {}).get("oid")) or None


def _base_contains_head(root, base) -> bool:
    """origin のベースブランチを取り直し、HEAD がその祖先か（マージ済みの PR を使い回してよいかの根拠）。"""
    git(root, "fetch", "-q", "origin", base)
    return git(root, "merge-base", "--is-ancestor", "HEAD", f"origin/{base}", check=False).returncode == 0


def _reusable_release_pr(root, branch, base):
    """開発版の PR（release/v<版> → ベースブランチ）のうち使い回せるものを返す。無ければ None。"""
    pr = find_pr(root, branch, base, states=("OPEN", "MERGED"))
    if pr and pr["state"] == "MERGED":
        # マージ済みの PR へ後から積んだコミットは GitHub が開き直さずベースブランチに入らない。入っていなければ PR を作り直す
        pr = pr if _base_contains_head(root, base) else None
    return pr


def _release_pr_body(root, a, plugin, plugins):
    """CHANGELOG.md の節とチェックの結果から開発版の PR の本文を組む。"""
    ver = a.version
    section = changelog_section(root, ver, plugin)
    mark = {True: "pass", False: "fail", None: "skip"}
    return "\n".join(
        [
            f"{plugin} v{ver} のリリース（{a.channel}）。対象の plugin: {', '.join(plugins)}\n\n{port_mark.NO_PORT_LINE}",
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


def _merge_release_pr(root, a, plugin, plugins, branch, base):
    """開発版の PR（release/v<版> → ベースブランチ）を使い回すか作ってマージし、(照合に渡す PR, 取り込んだ PR, マージコミット)を返す。衝突すれば release/v<版>-sync で取り込み（_merge_sync_pr）、照合に渡す PR は元の release/v<版> の PR のまま。"""
    pr = _reusable_release_pr(root, branch, base)
    if pr is None:
        body = _release_pr_body(root, a, plugin, plugins)
        pr = {"number": create_pr(root, base, branch, f"Release: {plugin} v{a.version}", body), "state": "OPEN", "new": True}
    n = pr["number"]
    reviewed.refresh_release_pr_body(root, pr, lambda: _release_pr_body(root, a, plugin, plugins))
    if pr["state"] != "OPEN":
        return n, n, merge_commit_of(root, n)
    if not pr.get("new") and (synced := _merged_sync_pr(root, branch, base)):
        return n, synced, merge_commit_of(root, synced)  # 再開: 前の回が -sync で取り込み済み
    try:
        return n, n, wait_and_merge(root, n, ci_wait=a.ci_wait)
    except Conflicted:
        sync_n, commit = _merge_sync_pr(root, a, plugin, branch, base, n)
        return n, sync_n, commit


def _merged_sync_pr(root, branch, base):
    """release/v<版>-sync → ベースブランチのマージ済みの PR があり、release/v<版> の先頭がベースブランチの祖先ならその番号。"""
    pr = find_pr(root, f"{branch}-sync", base, states=("MERGED",))
    if pr is None:
        return None
    return pr["number"] if _base_contains_head(root, base) else None


def _merge_sync_pr(root, a, plugin, branch, base, release_pr, lead=None, sections=False, merge=None):
    """release/v<版>-sync に origin/<ベースブランチ> を取り込み（版数だけの衝突は本番の版を取る。R5）、その PR でマージする。
    release/v<版> そのものへはベースブランチを取り込まない。待つ間にベースブランチが進んで再び衝突すれば、-sync の先頭へ積み直す（強制 push はしない）。(PR 番号, マージコミット) を返す。
    `lead` は PR の本文の頭、`sections` は CHANGELOG.md の版の節の衝突も解く（選んで集める取り込み方の戻すマージ。#3121 の決定 17）。
    `merge` は PR 番号からマージコミットを返す仕方（開発版の workflow）で、省くと手元の wait_and_merge。"""
    sync = f"{branch}-sync"
    for left in range(HEAD_MOVED_RETRIES, -1, -1):
        git(root, "fetch", "-q", "origin", base)
        have = git(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{sync}", check=False)
        ours = "HEAD"
        if have.returncode == 0:
            git(root, "fetch", "-q", "origin", sync)
            ours = f"origin/{sync}"
        resolved = []
        if git(root, "merge-base", "--is-ancestor", f"origin/{base}", ours, check=False).returncode != 0:
            msg = f"Merge {base} into {sync}（{plugin} v{a.version} の版数の衝突を本番の版で解く）"
            commit, resolved = version_merge.sync_commit(root, ours, f"origin/{base}", msg, sections=sections)
            git(root, "push", "-q", "origin", f"{commit}:refs/heads/{sync}")
        found = port_mark.ensure_no_port_line(root, find_pr(root, sync, base, states=("OPEN",)))
        head = lead or f"{plugin} v{a.version} の release/v{a.version} → {base}（#{release_pr}）が版数を持つ箇所で衝突したため"
        body = (
            f"{head}、本番の版を取って取り込む（解いたパス: {', '.join(resolved) or 'なし'}）。\n\n{port_mark.NO_PORT_LINE}\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        n = found["number"] if found else create_pr(root, base, sync, f"Release: {plugin} v{a.version} の版数を {base} へ揃える", body)
        try:
            return n, merge(n) if merge else wait_and_merge(root, n, ci_wait=a.ci_wait)
        except Conflicted:
            if not left:
                raise


def _merge_prod_pr(ctx, release_pr, approved, allowed, verdict, items, metrics):
    """本番の PR（head は release/v<版>）を使い回すか作り、承認と比べた release/v<版> の先端を本番の取り込み（`land`）で入れる。
    先端が動いたら比べ直し、許可リストの下に収まる限り HEAD_MOVED_RETRIES 回まで入れ直し、それ以外は承認ゲートで止める（不変条件 I9）。
    merge queue へ入れた・人のマージを待つときは `prod_land.LandPending` で抜ける。(PR 番号, マージコミット, 判定) を返す。"""
    root, a, plugin, branch, prod = ctx.root, ctx.a, ctx.plugin, ctx.branch, ctx.prod
    mp = port_mark.ensure_no_port_line(root, find_pr(root, branch, prod, states=("OPEN",)))  # 本番の PR も旧いプラグインを運ぶ
    title = f"Release: {plugin} v{a.version} を {prod} へ"
    body = f"{plugin} v{a.version} を {prod} へ出す（開発版の PR #{release_pr}）。\n\n{port_mark.NO_PORT_LINE}\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    main_pr = mp["number"] if mp else create_pr(root, prod, branch, title, body)
    for left in range(HEAD_MOVED_RETRIES, -1, -1):
        try:
            # 比べた先端だけを本番チャネルへ入れる
            merge = land(root, a, main_pr, verdict.tip)
            break
        except HeadMoved:
            moved = approval.check_approved(root, approved, branch, allowed)
            if not moved.ok or not left:
                emit(approval.gate(root, a, plugin, branch, moved, items, metrics, moved=not left), EXIT_GATE)
            verdict = moved  # 進んだ分が許可リストの下に収まるので、比べ直した先端で入れ直す（#2221）
    return main_pr, merge, verdict


def cmd_release(a):
    root = git_root(a.root)
    base, prod, plugin = release_decl(root, a)
    route = _release_route(root)
    if not route.sync and a.channel == "dev":
        # trunk には開発版のチャネルが無い。開発版の PR の段（版上げのブランチ → ベースブランチ）は本番ブランチへ入れてしまう
        raise StepError(
            f"ベースブランチ {base} が本番ブランチと同じ（trunk）で、開発版のチャネルが無い。--channel prod で出す", EXIT_PRECONDITION
        )
    try:
        _release(a, root, base, prod, plugin, route)
    except prod_land.LandPending as p:
        # merge queue へ入れた・人のマージを待つ: 本番ブランチへまだ入っていないので公開しない（決定 3・9）
        again = f"release-steps.py release --version {a.version} --channel prod" + (
            f" --approval {a.approval}" if getattr(a, "approval", None) else ""
        )
        nxt = (
            f"人が PR #{p.pr} をマージした後に {again} を打ち直す"
            if p.result == "human"
            else f"{again} を打ち直す（merge queue のマージを待って公開する）"
        )
        emit(prod_land.pending_result(plugin, a.version, prod, tag_name(root, plugin, a.version), p, nxt), EXIT_INCOMPLETE)


def _release(a, root, base, prod, plugin, route):
    """release の本体。本番ブランチへ入れる経路はどれも本番の取り込み（`land`）を通り、未着は `prod_land.LandPending` で抜ける。"""
    ver = a.version
    case = reviewed.release_case_or_stop(root) if a.channel == "prod" else None
    on_pr = case is not None and case.has_cd  # CD のある場合は承認を PR の上で照らす（#2773）
    rs = getattr(a, "release_strategy", None)
    selects = bool(rs) and strategy_rows.ROWS[rs].selects_units
    plugins = [s.strip() for s in (a.plugins or plugin).split(",") if s.strip()]
    if a.channel == "prod" and not route.sync and not on_pr and not selects:
        # CD の無い trunk: 承認を読む前に版上げの PR を開き、承認が無ければ承認ゲートで止まる（決定 4・6）
        branch = _prepare_release_branch(root, a, plugin)
        ctx = ReleaseCtx(root, a, plugin, plugins, branch, base, prod, sync=False)
        _published(root, a, plugin, prod, *trunk_release.release_trunk(ctx, sys.modules[__name__]))
    try:
        approved = approval.approved_of(root, a, plugin, case)
    except approval.Gate as g:
        emit(g.out, EXIT_GATE)
    branch = _prepare_release_branch(root, a, plugin)
    ctx = ReleaseCtx(root, a, plugin, plugins, branch, base, prod, sync=route.sync)
    # 本番ブランチへ直接出し、戻すマージを入れる（#3121）
    if selects:
        ops = sys.modules[__name__]  # 戻すマージ（back）は公開の後に打ち、失敗を本番の完了と分けて出す
        got = strategy_route.release_selected(ctx, approved, ops, case if on_pr else None)
        _published(root, a, plugin, prod, *got)
    if on_pr:
        reviewed.release_prod(ctx, case, _prod_ops())
    if a.channel == "dev" and (wf := reviewed.dev_workflow_of(root)) and reviewed.dev_workflow_ready(root, wf):
        reviewed.release_dev(ctx, wf, sys.modules[__name__])
    release_pr, merged_pr, release_commit = _merge_release_pr(root, a, plugin, plugins, branch, base)
    metrics = {
        "channel": a.channel,
        "version": ver,
        "release_pr": release_pr,
        "sync_pr": merged_pr if merged_pr != release_pr else None,
        "main_pr": None,
        "tag": None,
        "merge_commit": release_commit,
        "plugins": ",".join(plugins),
    }
    items = [{"kind": "pr", "name": f"#{release_pr}", "result": "merged", "base": base}]
    if metrics["sync_pr"]:
        items[0]["result"] = "compared"  # 取り込みは -sync の PR が行い、元の PR は照合にだけ使う
        items.append({"kind": "pr", "name": f"#{merged_pr}", "result": "merged", "base": base, "head": f"{branch}-sync"})
    if a.channel == "dev":
        emit(result(TOOL, "ok", f"{plugin} v{ver} を {base} へ出した（#{release_pr}）", items, metrics))

    # 本番: release/v<版> → 本番チャネル、タグ、GitHub Release（利用者の承認を得てから呼ぶ）
    git(root, "fetch", "-q", "origin", "--tags")
    done = find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":
        main_pr = done["number"]
        merge = _resume_merged_prod(ctx, approved, main_pr, items, metrics)
    else:
        main_pr, merge = _merge_new_prod(ctx, approved, release_pr, items, metrics)
    _published(root, a, plugin, prod, main_pr, merge, items, metrics)


def _release_route(root) -> strategy_route.Route:
    """宣言（メインディレクトリ）から版上げの経路を決める（取り込み方の分岐は `strategy_route.route_of` の 1 か所）。"""
    return strategy_route.route_of(strategy_route.strategy_or_stop(root))


def _resume_merged_prod(ctx, approved, main_pr, items, metrics):
    """再開: 本番チャネルへのマージは済んでいる（取り消せない）。比べ直さず、無いものだけを作って続ける。マージコミットを返す。"""
    root, prod = ctx.root, ctx.prod
    git(root, "fetch", "-q", "origin")
    merge = merged_or_tip(root, merge_commit_of(root, main_pr), prod, git)
    if git(root, "merge-base", "--is-ancestor", approved, merge, check=False).returncode != 0:
        raise StepError(f"マージ済みの本番の PR #{main_pr} のコミット {merge[:8]} が承認したコミット {approved[:8]} を含まない")
    metrics["approved_sha"] = approved
    items.append({"kind": "pr", "name": f"#{main_pr}", "result": "already_merged", "base": prod})
    return merge


def _merge_new_prod(ctx, approved, release_pr, items, metrics):
    """本番の PR を承認と照らしてマージする。(本番の PR 番号, マージコミット) を返す。"""
    root, a, plugin, branch, prod = ctx.root, ctx.a, ctx.plugin, ctx.branch, ctx.prod
    require_untagged(root, plugin, a.version, git=git)
    # 承認したコミットの後に、版上げの外のコミットが release/v<版> に無いか（#815・#2436 の R2）。
    # 照合に渡すのは元の release/v<版> の PR で、-sync の PR は渡さない（承認の後のベースブランチの変更を含むため）
    allowed = approval.release_pr_allowed(root, release_pr, pr_view)
    # 開発版の PR のマージで消えた release/v<版> を戻す。本番の照合と本番の PR の head はこの枝を読む
    repo_lib.restore_branch(root, branch, run_git=git)
    verdict = approval.check_approved(root, approved, branch, allowed)
    if not verdict.ok:
        emit(approval.gate(root, a, plugin, branch, verdict, items, metrics), EXIT_GATE)
    metrics.update({"approved_sha": approved, "compared_head": verdict.tip, "tolerated": len(verdict.tolerated)})
    main_pr, merge, verdict = _merge_prod_pr(ctx, release_pr, approved, allowed, verdict, items, metrics)
    metrics.update({"compared_head": verdict.tip, "tolerated": len(verdict.tolerated)})
    items += approval.tolerated_items(verdict)
    items.append({"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod})
    git(root, "fetch", "-q", "origin")
    return main_pr, merged_or_tip(root, merge, prod, git)


def _prod_ops() -> reviewed.ProdOps:
    """CD のある場合の本番が呼ぶ操作（呼ぶ時点のこのモジュールの関数）。"""
    return reviewed.ProdOps(
        find_pr=find_pr,
        create_pr=create_pr,
        merge_commit_of=merge_commit_of,
        merge_release_pr=_merge_release_pr,
        published=_published,
        land=land,
        pr_check_buckets=pr_check_buckets,
    )


def _prepare_release_branch(root, a, plugin) -> str:
    """作業ツリーがリリースブランチにあり、コミットしていない変更が無く、版を上げ済みであることを確かめて push する。ブランチ名を返す。"""
    ver = a.version
    if (branch := git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()) != f"release/v{ver}":
        raise StepError(f"作業ツリーのブランチが release/v{ver} でない: {branch}", EXIT_PRECONDITION)
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StepError("作業ツリーにコミットしていない変更がある（bump と changelog をコミットしてから呼ぶ）", EXIT_PRECONDITION)
    bump.require_bumped(root, plugin, ver)

    git(root, "push", "-q", "-u", "origin", "HEAD")
    return branch


def _published(root, a, plugin, prod, main_pr, merge, items, metrics, back=None):
    """本番チャネルへ入れた後: タグと GitHub Release（CD が無ければ）・退避先の回収の候補を足し、`back`（戻すマージ）を打って結果を出す。"""
    ver, tag = a.version, tag_name(root, plugin, a.version)
    decl = delivery.load_delivery(root)
    by_cd = delivery.publishes_by_cd(decl)
    want_tag, want_release = publish.wants(decl)
    if not by_cd and want_tag:
        items += publish.tag_and_release(root, plugin, ver, tag, merge, release=want_release)
    made = tag if want_tag else None
    metrics.update({"main_pr": main_pr, "tag": None if by_cd else made, "publisher": "cd" if by_cd else "ndf", "merge_commit": merge})
    swept, sweep_metrics, nxt = _sweep_candidates(root, merge)
    items += swept
    metrics.update(sweep_metrics)
    if by_cd:
        done = f"{tag} と GitHub Release は CD が作る"
    elif not want_tag:
        done = "宣言がタグと GitHub Release を作らない（release.tag が null）"
    else:
        done = f"{tag} と GitHub Release を作った" if want_release else f"{tag} を作った（宣言の release.github_release が偽）"
    status, code, done = back(items, metrics, done) if back else ("ok", None, done)
    emit(result(TOOL, status, f"{plugin} v{ver} を {prod} へ出し、{done}", items, metrics, None, nxt), code)


def cmd_approval_check(a):
    """PR の承認の証跡を判定し、先端へ commit status を付ける（証跡のチェックの workflow が打つ。#2773。本体は release_lib/reviewed.py）。"""
    reviewed.approval_check(a)


def cmd_approval_post(a):
    """承認資料をリリースの PR へ承認資料のコメントとして置く（#2773。本体は release_lib/reviewed.py）。"""
    reviewed.approval_post(a)


def cmd_release_case(a):
    """リリースの場合を metrics に出す（#2773。本体は lib/release_case.py）。"""
    reviewed.release_case_out(a)


def cmd_land(a):
    """承認した head だけを宣言の主体と方式で本番ブランチへ入れる（#3672 の I5・I7。本体は release_lib/land.py）。"""
    from release_lib import land as land_lib

    got = land_lib.land_cli(git_root(a.root), a.repo, a.pr)
    emit(result(TOOL, "stopped" if got["result"] == "stopped" else "ok", got["reason"] or f"#{a.pr}: {got['result']}", [], got))


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
    """リリースの後に、本番のリリースの PR（release/v<版> → ベースブランチ）へリリース記録を 1 件コメントで書く（#1273）。

    タグ <plugin>--v<版> が origin に無い・同じタグの GitHub Release が無い・マージ済みの PR が無いなら、書かずに 3 で止まる
    （記録の `段階: 本番` はリリースが終わった事実を表す）。PR の最後のリリース記録が同じ版・同じスプリントの PR の本番の記録なら
    書かずに ok（exists）を返す。組み立てと投稿は release_lib/record.py、形は lib/dist_record.py が持つ。
    """
    root = git_root(a.root)
    cd = cd_result.load_result(a.cd_result)
    if a.promote:
        return record_promote(root, a, cd)
    if a.merged:
        return record_merged(root, a, cd)
    ver = a.version
    base, _prod, plugin = release_decl(root, a)
    tag = tag_name(root, plugin, ver)
    decl = delivery.load_delivery(root)
    by_cd = cd is not None and delivery.publishes_by_cd(decl)  # 公開物は CD が作り、照会を CD の結果へ置き換える
    want_tag, want_release = (False, False) if by_cd else publish.wants(decl)  # 宣言が作らせない公開物は求めない
    if want_tag and git(root, "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}", check=False).returncode != 0:
        raise StepError(f"origin にタグ {tag} が無い（リリースが済んでいない）", EXIT_PRECONDITION)
    git(root, "fetch", "-q", "origin", "--tags", check=False)  # 直前の正式版とタグの時刻を読む
    if want_release and gh_parts.gh(["release", "view", tag, "--json", "tagName"], cwd=root).returncode != 0:
        raise StepError(f"GitHub Release {tag} が無い（リリースが済んでいない）", EXIT_PRECONDITION)
    # 版数の衝突を -sync で取り込んだときは、元の PR がマージされずに閉じていることがある（#2436）
    pr = find_pr(root, f"release/v{ver}", base, states=("MERGED",)) or find_pr(root, f"release/v{ver}-sync", base, states=("MERGED",))
    if pr is None:
        raise StepError(f"release/v{ver}（か release/v{ver}-sync）→ {base} のマージ済みの PR が無い", EXIT_PRECONDITION)
    n = pr["number"]
    tagged, template = by_cd or want_tag, tag_template_of(root)  # タグを作らない宣言は残った古いタグを読まず、本番ブランチの版から
    prev_tag = release_tag_before(root, plugin, current=tag) if tagged else None
    prev = (tag_version(prev_tag, plugin, template) if prev_tag else None) if tagged else publish.untagged_before(root, plugin, ver)
    body = record.body_of(root, plugin, ver, tag, prev_tag, a.prs, cd, by_cd, tagged=tagged, template=template, prev=prev)
    _emit_record(record.write(root, n, ver, prev, body, a.prs, f"v{ver}", cd), cd)


def _emit_record(written, cd):
    """リリース記録の結果を出す。CD の結果が失敗なら、書いた後に 5 で止める（デプロイの工程は完了でない。#2623 の I14）。"""
    summary, items, metrics = written
    if cd is not None and cd.value == cd_result.FAILURE:
        emit(result(TOOL, "stopped", f"CD の結果が失敗: {cd.reason}（{summary}）", items, metrics), EXIT_CD_FAILED)
    emit(result(TOOL, "ok", summary, items, metrics))


def record_promote(root, a, cd=None):
    """リリースの Pull Request で届く経路（--promote）: 最後にマージしたリリースの Pull Request（--head → --base）へリリース記録を書く（#837）。
    版はマージのコミット（`<本番チャネル> <短い SHA>`）、直前の版はその 1 つ目の親。マージ済みの PR が無ければ 3。"""
    if not (a.head and a.base):
        raise StepError("--promote には --head（ベースブランチ）と --base（本番チャネル）が要る", EXIT_PRECONDITION)
    pr = find_pr(root, a.head, a.base, states=("MERGED",))
    sha = merge_commit_of(root, pr["number"]) if pr else None
    if not sha:
        raise StepError(f"{a.head} → {a.base} のマージ済みのリリースの Pull Request が無い（リリースが済んでいない）", EXIT_PRECONDITION)
    n, prev, ver = pr["number"], record.parent_of(root, a.base, sha), f"{a.base} {sha[:7]}"
    body = record.promote_body_of(n, a.head, a.base, prev, sha[:7], a.prs, cd)
    _emit_record(record.write(root, n, ver, prev, body, a.prs, ver, cd), cd)


def record_merged(root, a, cd=None):
    """本番チャネルへ直接マージする経路（--merged）: 最後の --prs の Pull Request へリリース記録を書く（#2623 の決定 9）。
    版はマージのコミット（`<本番チャネル> <短い SHA>`）。マージのコミットを読めなければ 3。"""
    if not a.base:
        raise StepError("--merged には --base（本番チャネル）が要る", EXIT_PRECONDITION)
    n = a.prs[-1]
    sha = merge_commit_of(root, n)
    if not sha:
        raise StepError(f"#{n} のマージのコミットを読めない（リリースが済んでいない）", EXIT_PRECONDITION)
    prev, ver = record.parent_of(root, a.base, sha), f"{a.base} {sha[:7]}"
    body = record.merged_body_of(n, a.base, prev, sha[:7], a.prs, cd)
    _emit_record(record.write(root, n, ver, prev, body, a.prs, ver, cd), cd)


def cmd_publish(a):
    """`--revision` のコミットの版でリリースタグと GitHub Release のうち足りないものを作る（CD のワークフローと切り戻しの手段）。"""
    root = git_root(a.root)
    # 使うのはプラグイン名だけ。宣言の全体を検査すると、移行の途中（旧いキーが残る版）の CD で止まり、タグが作られない
    plugin = getattr(a, "plugin", None) or plugin_of(root, a)
    sha = git(root, "rev-parse", "--verify", f"{a.revision}^{{commit}}", check=False).stdout.strip()
    if not sha:
        raise StepError(f"コミット {a.revision} を読めない")
    ver, tag, items = publish.publish(root, plugin, sha)
    metrics = {"version": ver, "tag": tag, "revision": sha}
    emit(result(TOOL, "ok", f"{tag} を確かめた（タグと GitHub Release がそろっている）", items, metrics))


def _cd_revision(root, a):
    """CD の結果を読むリビジョン。--prs があれば最後の PR のマージのコミット（record --merged が記録するもの。読めなければ空で未確定）、
    --head があれば最後にマージした head → base の PR のコミット、どちらも無ければ本番チャネルの先端。"""
    if a.prs:
        try:
            return merge_commit_of(root, a.prs[-1]) or ""
        except StepError:
            return ""  # 先端で代えると別の PR のリビジョンを読むため、未確定にする
    git(root, "fetch", "-q", "origin", a.base, check=False)
    try:
        pr = find_pr(root, a.head, a.base, states=("MERGED",)) if a.head else None
        sha = merge_commit_of(root, pr["number"]) if pr else None
    except StepError:
        sha = None  # PR を読めなくても本番チャネルの先端で読む
    return sha or git(root, "rev-parse", f"origin/{a.base}", check=False).stdout.strip()


def cmd_cd_result(a):
    """本番チャネルへのマージの後に CD の結果を読み、結果のファイルへ書く。値にかかわらず 0 で終える（決定 8）。"""
    root = git_root(a.root)
    decl = delivery.load_delivery(root)
    if decl.problems:
        got = cd_result.CdResult(cd_result.UNDETERMINED, "", "宣言を読めない: " + " / ".join(decl.problems))
    else:
        got = cd_result.read_cd(root, decl, _cd_revision(root, a) if delivery.merge_rows(decl) else "")
    cd_result.save_result(a.out, got)
    metrics = {"cd_result": got.value, "revision": got.revision, "reason": got.reason}
    emit(result(TOOL, "ok", f"CD の結果: {got.value}（{got.reason or got.revision[:7]}）", got.evidence, metrics))


def cmd_cd_guidance(a):
    """CD の案内を出す。案内の有無で終了コードは変わらない（I3）。宣言を読めなければ 3 で止める（I4）。"""
    root = git_root(a.root)
    decl = delivery.load_delivery(root)
    if decl.problems:
        raise StepError("宣言を読めない: " + " / ".join(decl.problems), EXIT_PRECONDITION)
    got = delivery.cd_guidance(decl)
    summary = f"CD の案内: {got.missing} が無い（手順は {got.steps_ref}）" if got else "CD の案内は要らない"
    emit(result(TOOL, "ok", summary, [got.as_item()] if got else [], {"guided": bool(got)}))


APPROVAL_OWN = (*PRESENT_HEADINGS, instructions.HEADING)  # approval-facts が作り直す `## ` の節（#1834）


def kept_sections(root, approval):
    """前の承認資料のうち作り直しで残す節。資料を渡さない・無いときは空。読めない・H1 が無い・同じ見出しが
    2 つあるときは StepError（2。承認資料を書かない）。"""
    if not approval:
        return []
    path = Path(approval)
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return []
    try:
        return carry.kept(path.read_text(encoding="utf-8").split("\n"), APPROVAL_OWN)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise StepError(f"前の承認資料 {approval} を読めない（書き換えずに止める）: {e}", 2)


def included_pr_facts(prs, data, checks, repo):
    """含む PR ごとの事実から、機械向けの items と承認資料の「含む PR」の行を作る。

    data は PR 番号 → 取得済みの PR、checks は PR 番号 → CI の結果（pr_check_buckets の戻り）。"""
    items, rows = [], []
    for p in prs:
        n, d = p.number, data.get(p.number) or {}
        oid = (d.get("mergeCommit") or {}).get("oid") or ""
        pr_checks = checks[n]
        passed = included.ci_passed(pr_checks)
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
                "checks": len(pr_checks),
                "via": p.via,
                "no_change": included.no_change(d),
            }
        )
        facts = [str(d.get("state", "")), oid[:8] if oid else "—", included.ci_fact(pr_checks)]
        rows.append(included.included_row(n, d.get("title", ""), facts, p.via, included.no_change(d)))
    return items, rows


class ApprovalRange(NamedTuple):
    """承認に出す範囲: 承認に出すブランチ・その先頭のコミット・前のリリースの起点（タグか版上げのコミット）。"""

    source_branch: str
    approved_sha: str
    prev: str


def _approval_range(root, a, base, plugin, cur_tag) -> ApprovalRange:
    """承認に出す範囲を決める。`--head` があればそのブランチを取ってきて先頭を、無ければベースブランチの先頭を承認に出す。
    前のリリースの起点は --prev-tag → `prev_release` の順で決め、決まらなければ止める。"""
    source_branch = (head := getattr(a, "head", None)) or base  # 選んで集める取り込み方は組んだリリースブランチの先頭を承認に出す（#3121）
    if head:
        git(root, "fetch", "-q", "origin", f"+refs/heads/{source_branch}:refs/remotes/origin/{source_branch}")
    approved_sha = git(root, "rev-parse", f"origin/{source_branch}").stdout.strip()
    prev = a.prev_tag or prev_release(root, plugin, approved_sha, cur_tag)
    if not prev:
        raise StepError(
            "前のリリースの起点（前のタグか、タグを作らない宣言では前の版上げのコミット）を決められない（--prev-tag を渡す）",
            EXIT_PRECONDITION,
        )
    return ApprovalRange(source_branch, approved_sha, prev)


def cmd_approval_facts(a):
    root = git_root(a.root)
    repo = repo_lib.owner_repo(root)
    if not repo:
        raise StepError("リポジトリの owner/name を決められない", EXIT_PRECONDITION)
    base, prod, plugin = release_decl(root, a)
    git(root, "fetch", "-q", "origin", "--tags")
    cur_tag = tag_name(root, plugin, a.version)
    source_branch, approved_sha, prev = _approval_range(root, a, base, plugin, cur_tag)
    made = publish.made_by(delivery.load_delivery(root), cur_tag)
    keep = kept_sections(root, getattr(a, "approval", None))  # 前の承認資料の残す節（読めなければ書かずに止める。#1834）
    changes = instructions.changed_instructions(root, prev, approved_sha)
    prs, extra_out = included.section_range(root, prev, approved_sha, a.prs)  # 範囲の PR と --prs の和（#1274・#1885）
    data = pr_many(root, [p.number for p in prs], "number,title,body,state,mergeCommit,url")
    outside = []
    prs = _within_end(root, prs, data, approved_sha, extra_out, outside)  # 終点は承認に出すコミット（#2555 の I2）

    stat = git(root, "diff", "--shortstat", f"origin/{prod}...{approved_sha}").stdout.strip()
    files = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", stat)) else 0
    ins = int(m.group(1)) if (m := re.search(r"(\d+) insertions?", stat)) else 0
    dels = int(m.group(1)) if (m := re.search(r"(\d+) deletions?", stat)) else 0

    checks = {p.number: pr_check_buckets(root, p.number) for p in prs}
    items, rows = included_pr_facts(prs, data, checks, repo)
    compare = f"https://github.com/{repo}/compare/{prev}...{approved_sha}"
    path = approval_present(
        TOOL,
        f"v{a.version}",
        title=f"{plugin} v{a.version} のリリース",
        targets=[{"url": compare, "title": f"{prev} → {source_branch}（{approved_sha[:8]}）", "base_head": f"{prod} ← {source_branch}"}],
        change=f"`{prod}...{source_branch}` の差分 {files} ファイル / +{ins} / −{dels}",
        judge=[
            ac.material_row(approved_sha),
            ("版数", a.version),
            ("含む PR", "\n".join(rows) or "—"),
            ("配る中身", NOTES_PENDING),
            (materials.TRIAL_ROW, NOTES_PENDING),
        ],
        consent=[f"{plugin} v{a.version} を {prod} へ出す" + (f"（{made}を作る）" if made else "")],  # 宣言が作らせる公開物だけ
        rollback=f"{made + 'を消し、' if made else ''}{prod} を {prev} の内容へ戻す PR を出す。"
        "導入済みの利用者の環境は利用者の側の操作（前の版の導入し直し）でしか戻せない。",
        sections=[(instructions.HEADING, instructions.instruction_lines(changes, prev, approved_sha))],
    )
    if keep:
        out = Path(path)
        out.write_text("\n".join(carry.insert_kept(out.read_text(encoding="utf-8").split("\n"), keep)), encoding="utf-8")
    emit(
        result(
            TOOL,
            "gate",
            f"{plugin} v{a.version} の本番承認の提示物を書いた（PR {len(items)} 件）{outside_note(outside)}",
            items + outside_items(outside),
            {
                "outside": outside,
                "files": files,
                "insertions": ins,
                "deletions": dels,
                "prev_tag": prev,
                "compare": compare,
                "approved_sha": approved_sha,
                "kept": [k.heading for k in keep],
                "instructions": len(changes),
            },
            path,
            f"利用者の承認を得たら release-steps.py release --version {a.version} --channel prod --approved-sha {approved_sha}",
        ),
        EXIT_GATE,
    )


def cmd_release_branch(a):
    """選んで集める取り込み方のリリースブランチを本番チャネルから組んで push する（#3121 の E10。本体は release_lib/strategy_route.py）。"""
    root = git_root(a.root)
    prod = a.production or release_decl(root, a)[1]
    try:
        got = strategy_route.build_branch(root, f"{strategy_rows.RELEASE_PREFIX}{a.version}", prod, a.units)
    except strategy_rows.ReleaseBuildError as e:
        raise StepError(str(e)) from e
    items = [{"kind": "branch", "name": u, "result": "merged", "head": h} for u, h in got["units"].items()]
    emit(result(TOOL, "ok", f"{got['branch']} を {prod} から組んだ（先頭 {got['head'][:12]}）", items, got))


def cmd_review_open(a):
    """リリースブランチの PR のレビューの PR を組む（#3163。本体は release_lib/review_range.py）。差分が空なら 3。"""
    root = git_root(a.root)
    got = review_range.open_review(root, a.version, a.diff, a.production or release_decl(root, a)[1], review_range.GitHub(create_pr))
    status, summary, items, code = review_range.open_outcome(a.version, got)
    emit(result(TOOL, status, summary, items, got), code)


def cmd_review_close(a):
    """レビューの直しをリリースブランチへ移し、レビューの PR を閉じる（#3163 の I9〜I11）。"""
    got = review_range.close_review(git_root(a.root), a.version, review_range.GitHub(create_pr))
    emit(result(TOOL, "ok", f"直し {got['moved']} 件を移し、レビューの PR #{got['review_pr']} を閉じた", [], got))


def cmd_approve(a):
    """リリース承認ゲートの承認を承認資料へ記録する（#815 の I10。本体は release_lib/approval.py の approve）。"""
    try:
        emit(approval.approve(git_root(a.root), a))
    except approval.Gate as g:
        emit(g.out, EXIT_GATE)


CHANGES_HEADING = others.CHANGES_HEADING
RISKS_HEADING = materials.RISKS_HEADING
NOTES_PENDING = "（release-steps.py notes --approval が PR 本文の「利用者向けの変化」から書く）"


def pr_notes(root, prs, skipped=None, replacing=None, unreadable=None, data=None, to=None, extra_out=(), outside=None):
    """PR ごとの配る中身・危険・移行の手順（included.notes_of）。data は pr_many の出力（渡さなければここで読む）。
    終点 to を渡すと、範囲の外の --prs のうち終点に入っていない番号を外して outside へ足す（included.drop_outside）。"""
    prs = included.as_included(prs)
    data = data if data is not None else pr_many(root, [p.number for p in prs], _pr_view_fields(to))
    prs = _within_end(root, prs, data, to, extra_out, outside)
    return included.notes_of(prs, data, skipped, replacing, unreadable)


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
    require_unreleased(root, plugin, version)
    lines = cl.read_text(encoding="utf-8").split("\n")
    head = f"## [{plugin} {base_of(version)}]"
    at = changelog_span(lines, plugin, version)[0]
    if at is None:
        raise StepError(f"CHANGELOG.md に {head} の節が無い（先に changelog を走らせる）", EXIT_PRECONDITION)
    replace_lines_under(lines, at, bullets)
    cl.write_text("\n".join(lines), encoding="utf-8")
    return [{"kind": "section", "name": "CHANGELOG.md", "result": "replaced", "heading": lines[at], "lines": len(bullets)}]


def unmerged_items(skipped):
    return [{"kind": "pr", "name": f"#{n}", "result": "skipped", "reason": "マージされていない"} for n in skipped]


def unmerged_note(skipped):
    return f"（マージされていない {' '.join(f'#{n}' for n in skipped)} は載せない）" if skipped else ""


def notes_prs(root, a):
    """notes が欄を書く PR・承認資料・範囲の終点・範囲の外の --prs。--approval なら承認資料の「含む PR」
    （--prs はそこに無い番号があれば止まる。#1274。終点は渡さない: approval-facts が絞り込み済み。#2555）、
    無ければ changelog と同じ範囲の PR と --prs の和（#1885。出し直しでも前の開発版の PR を節に残す）。"""
    if not a.approval:
        prs, _, to, extra_out = section_prs(root, a)
        return prs, None, to, extra_out
    path = Path(a.approval)
    path = path if path.is_absolute() else root / path
    listed = included.read_included_rows(path)
    if missing := [n for n in a.prs if n not in {p.number for p in listed}]:
        absent = " ".join(f"#{n}" for n in missing)
        raise StepError(f"--prs の {absent} が承認資料の「含む PR」に無い（approval-facts で承認資料を作り直す）", EXIT_PRECONDITION)
    return listed, path, None, []


def issue_body(root, n, repo=None):
    """gh issue view <n> --json body の本文（repo を渡せばそのリポジトリの課題）。読めなければ StepError（gh が無い: 3）。"""
    r = gh_parts.view_json("issue", n, "body", repo=repo, cwd=str(root))
    if r.returncode == 127 and "gh を実行できない" in r.stderr:
        raise StepError("gh が無い", EXIT_PRECONDITION)
    if r.returncode != 0:
        raise StepError(f"gh issue view {n} が失敗: {r.stderr.strip()[:300]}")
    try:
        return (json.loads(r.stdout or "null") or {}).get("body") or ""
    except (ValueError, AttributeError):
        raise StepError(f"gh issue view {n} の出力を読めない", 2)


def cmd_notes(a):
    root = git_root(a.root)
    if getattr(a, "check_state", None) is not None and not a.approval:
        raise StepError("--check-state は --approval と使う", 2)
    skipped, outside = [], []
    prs, path, to, extra_out = notes_prs(root, a)
    reps, bad = [], []
    data = pr_many(root, [p.number for p in prs], "title,body,state,closingIssuesReferences", comments=True) if path else None
    notes = pr_notes(root, prs, skipped, reps, bad, data, to, extra_out, outside)
    before = {x.number: x.changes for x in notes}
    after, rep_items = replaced.apply(before, reps)  # 後の PR が置き換えた箇条を除く（#1834）
    rep_items += bad
    gone = sum(len(before[n]) - len(after[n]) for n in before)
    c = NotesContent.of(notes, after, skipped, rep_items, gone)
    fallback = sum(1 for x in notes if x.by_title)
    if path:
        _emit_approval_notes(root, a, path, prs, data, c)
        return
    items = write_notes(root, a.version, plugin_of(root, a), c.bullets, c.migration) + unmerged_items(skipped) + rep_items
    emit(
        result(
            TOOL,
            "ok",
            f"{len(notes)} 件の PR の利用者向けの変化を書いた{unmerged_note(skipped)}{outside_note(outside)}",
            items + outside_items(outside),
            _notes_metrics(a, c, fallback=fallback, outside=outside),
        )
    )


def _notes_metrics(a, c: NotesContent, **extra):
    """notes の 2 つの経路に共通の metrics。経路ごとの鍵（approval / fallback）は extra で migration の後へ入れる。"""
    return {
        "version": a.version,
        "prs": len(c.notes),
        "lines": len(c.bullets),
        "migration": len(c.migration),
        **extra,
        "unmerged": c.skipped,
        "replaced": c.gone,
    }


def _emit_approval_notes(root, a, path, prs, data, c: NotesContent):
    """承認の資料（--approval）の経路: 資料を集めて提示物の欄を書き、結果を出す。"""
    verified = [r for r in (a.verified or "").split(",") if r]
    base = release_decl(root, a)[0]
    ref = a.ref or base
    decl = (base, repo_lib.owner_repo(root) or "", a.version, a.prs)
    m = materials.approval_materials(
        root,
        decl,
        getattr(a, "check_state", None),
        prs,
        data,
        lambda r, n: issue_body(root, n) if materials.same_repo(r, decl[1]) else issue_body(root, n, repo=r),
    )
    results, machine = materials.check_lines(m), materials.risk_lines(m, bool(c.risks), materials.now_jst())
    items = materials.write_approval(path, a.version, c.bullets, c.risks, verified, ref, c.migration, results, machine)
    items += materials.items_of(m) + unmerged_items(c.skipped) + c.rep_items
    emit(
        result(
            TOOL,
            "ok",
            f"提示物の欄を {len(c.notes)} 件の PR から書いた{unmerged_note(c.skipped)}",
            items,
            _notes_metrics(a, c, approval=str(path)),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="リポジトリが宣言したリリースコマンドと、デプロイの決まった手順を走らせる")
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
    p.add_argument("--prs", nargs="*", type=int, metavar="PR番号", help="版に含む PR（本文と閉じる課題から上げ幅の候補。値なしは範囲だけ）")
    p.add_argument("--approval", help="承認資料。「版を上げる他のプラグイン」の節を書く")
    p.add_argument("--set", action="append", metavar="名前=上げ幅", help="--approval: リリース承認ゲートで決めた上げ幅へ行を書き直す")
    p.add_argument("--decided", help="承認資料の表の上げ幅で上げた版を返す（本番の bump-others）")
    p.add_argument("--head", help="差分の終点のブランチ（既定は HEAD。選んで集める取り込み方はリリースブランチ）")
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
    g.add_argument(
        "--approved-sha", metavar="SHA", help="prod の CD が無い場合: 承認したコミット（approval-facts の metrics.approved_sha。40 桁）"
    )
    g.add_argument("--approval", help="prod の CD が無い場合: 承認資料（「承認したコミット」と「承認の記録」の欄を読む）")
    p.add_argument("--release-strategy", choices=tuple(strategy_rows.ROWS), help="本番への取り込み方（開始時の値。単位を選ぶ行だけ）")
    p.set_defaults(func=cmd_release)
    p = sub.add_parser(
        "release-branch", parents=[common], help="選んだスプリントブランチを本番チャネルから組んだリリースブランチへ集めて push する"
    )
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--units", nargs="+", required=True, metavar="ブランチ", help="選んだリリースの単位（スプリントブランチ）")
    p.add_argument("--production", help="本番チャネル（既定は宣言の値）")
    p.set_defaults(func=cmd_release_branch)
    p = sub.add_parser("review-open", parents=[common], help="リリースブランチの PR のレビューの PR を組む（差分が空なら 3）")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument("--diff", required=True, choices=("pr", "resolution"), help="対象の差分（PR の差分全体か衝突の解消の差分）")
    p.add_argument("--production", help="本番チャネル（既定は宣言の値）")
    p.set_defaults(func=cmd_review_open)
    p = sub.add_parser("review-close", parents=[common], help="レビューの直しをリリースブランチへ移し、レビューの PR を閉じる")
    p.add_argument("--version", required=True, type=version_arg)
    p.set_defaults(func=cmd_review_close)

    p = sub.add_parser("record", parents=[common], help="リリースの後に、本番のリリースの PR へリリース記録（## リリース記録）を書く")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--version", type=version_arg)
    g.add_argument("--promote", action="store_true", help="リリースの Pull Request（--head → --base）のマージで出たときの記録")
    g.add_argument("--merged", action="store_true", help="本番チャネルへの直接のマージで CD が届けたときの記録（最後の --prs へ書く）")
    p.add_argument("--head", help="--promote: リリースの Pull Request の head（ベースブランチ）")
    p.add_argument("--base", help="--promote / --merged: 本番チャネル")
    p.add_argument("--cd-result", help="cd-result が書いた結果のファイル。値を `CD の結果:` の行へ書く")
    p.add_argument("--prs", nargs="+", required=True, type=int, metavar="PR番号", help="スプリントの PR（`スプリント:` の行に並べる）")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin。タグ <名前>--v<版> に使う）")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("publish", parents=[common], help="コミットの版でリリースタグと GitHub Release のうち足りないものを作る")
    p.add_argument("--revision", required=True, help="公開するコミット")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin）")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("cd-result", parents=[common], help="本番チャネルへのマージの後に CD の結果を読み、ファイルへ書く")
    p.add_argument("--base", required=True, help="本番チャネル")
    p.add_argument("--head", help="本番チャネルへマージした Pull Request の head（無ければ本番チャネルの先端を読む）")
    p.add_argument(
        "--prs", nargs="+", type=int, metavar="PR番号", help="本番チャネルへ直接マージした PR（最後の PR のマージのコミットを読む）"
    )
    p.add_argument("--out", required=True, help="結果のファイル")
    p.set_defaults(func=cmd_cd_result)

    p = sub.add_parser("cd-guidance", parents=[common], help="CD が無いリポジトリへ CD の設定と方式の記録を促す案内を出す")
    p.set_defaults(func=cmd_cd_guidance)

    p = sub.add_parser(
        "approve",
        parents=[common],
        help="本番へのリリースの承認（リリース承認ゲート）を承認資料へ記録する（--pr ならリリースの PR へ証跡を書く）",
    )
    p.add_argument("--approval", help="承認資料（--pr の無い経路で要る）")
    p.add_argument("--pr", type=int, help="CD のある場合: 承認の証跡のコメントを置くリリースの PR（#2773）")
    p.add_argument("--approved-sha", metavar="SHA", help="提示した承認資料を書いた approval-facts の metrics.approved_sha")
    p.add_argument("--facts-from", metavar="DIR", help="開発版のプランの状態ディレクトリ（facts の出力から SHA を読む。#1426）")
    p.add_argument(
        "--answer-from", metavar="STATE", help="--facts-from: リリース承認ゲートの利用者の承認の記録を読むスプリント状態ファイル"
    )
    p.add_argument("--by", required=True, choices=("user", "mvv"), help="承認した者")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("approval-check", parents=[common], help="リリースの PR の承認の証跡を判定し、先端へ commit status を付ける")
    p.add_argument("--pr", type=int, required=True)
    p.set_defaults(func=cmd_approval_check)
    p = sub.add_parser("approval-post", parents=[common], help="承認資料をリリースの PR へ承認資料のコメントとして置く")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--approval", required=True, help="承認資料")
    p.set_defaults(func=cmd_approval_post)
    p = sub.add_parser("land", parents=[common], help="承認した head だけを宣言の主体と方式で本番ブランチへ入れる（キューへの投入を含む）")
    p.add_argument("--pr", type=int, required=True, help="入れる PR の番号")
    p.add_argument("--repo", help="owner/repo（既定は origin）")
    p.set_defaults(func=cmd_land)
    p = sub.add_parser("release-case", parents=[common], help="リリースの場合（cd-no-review / cd-review / no-cd）を出す")
    p.set_defaults(func=cmd_release_case)

    p = sub.add_parser("approval-facts", parents=[common], help="本番承認の提示物のうち機械で作れる部分を書き出す")
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument(
        "--prs",
        nargs="*",
        required=True,
        type=int,
        metavar="PR番号",
        help="前のタグからベースブランチへ入った PR に足す PR（マージ前の PR など。値なしなら範囲の PR だけ）",
    )
    p.add_argument("--prev-tag")
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin。タグ <名前>--v<版> に使う）")
    p.add_argument("--approval", help="前の承認資料。作り直しで approval-facts の書かない節（手で足した節など）を残す")
    p.add_argument("--head", help="承認に出すブランチ（既定はベースブランチ。選んで集める取り込み方はリリースブランチ）")
    p.set_defaults(func=cmd_approval_facts)

    p = sub.add_parser(
        "notes", parents=[common], help="PR 本文の「利用者向けの変化」から CHANGELOG の版の節（--approval なら提示物の欄）を組む"
    )
    p.add_argument("--version", required=True, type=version_arg)
    p.add_argument(
        "--prs", nargs="*", required=True, type=int, metavar="PR番号", help="--approval なら提示物の「含む PR」にあることを確かめるだけ"
    )
    p.add_argument("--plugin", help="配るプラグイン（既定は宣言の release.plugin）")
    p.add_argument("--approval", help="本番承認の提示物。渡すと「配る中身」「承認前の試用で確かめたこと」の欄を書く")
    p.add_argument("--verified", default="", help="--approval: 導入を確かめた経路（カンマ区切り。例 claude,codex）")
    p.add_argument("--ref", help="--approval: 開発版のデプロイで導入した ref（既定は宣言のベースブランチ）")
    p.add_argument(
        "--check-state",
        help="--approval: 検査のプランの状態ディレクトリ（レビュー・リファクタリング・全体テストの結果を読む。結果 JSON が無ければ無い材料として書き、空文字・無いディレクトリなら書かずに 3 で止まる）",
    )
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
