#!/usr/bin/env python3
"""project-mvv.py: プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`）の判定・候補・照合・承認・改訂の兆候（#1366）。

    python3 project-mvv.py check    [--root DIR]
    python3 project-mvv.py collect  [--root DIR] [--out-dir DIR] [--request-file F] [--trend-commits N] [--trend-issues N] [--budget 60]
    python3 project-mvv.py propose  --materials F [--current] [--reason-file F] [--out-dir DIR] [--root DIR]
    python3 project-mvv.py vet      --body F --kind candidate|revision|sprint [--root DIR]
    python3 project-mvv.py approve  --body F --by NAME [--reason TEXT] [--accept-unknown] [--root DIR]
    python3 project-mvv.py show     [--version N] [--diff M] [--root DIR]
    python3 project-mvv.py context  [--root DIR] [--format text|json] [--sprint <状態> | --mvv F | --milestone M [--repo OWNER/REPO]]
    python3 project-mvv.py signals  [--root DIR] [--format text|json]
    python3 project-mvv.py schema   [--out FILE]

`development-workflow` の手順 0 が `check` を打つ。手順（対話・承認・改訂）は
`skills/development-workflow/references/project-mvv.md` にある。

- `check`: 0 = 承認済みで一致 / 2 = 無い / 3 = 壊れている / 4 = 承認と一致しない・未承認。LLM も `gh` も呼ばず、書かない
- `collect`: git・`gh`（読み取りだけ）・ファイルから材料を集め、リポジトリの外の状態ディレクトリへ `materials.json` を書く。
  LLM を呼ばない。コミット数と課題数の両方が閾値に満たなければ傾向モード（README・指示書・依頼文だけ）
- `propose`: 最小構成の claude -p を 1 回呼び、候補 2 案以上と分かれる点を `candidates.json`・`candidates.md` へ書く。
  形が足りなければ 1
- `vet`: 本文を NDF の共通原則（と承認済みのプロジェクト MVV）に照らす。0 = 従う / 10 = 反する疑い・判定できない・読めない。
  照合は `~/.local/state/ndf/project-mvv.jsonl` に残す
- `approve`: 利用者の承認の後に打つ。同じ本文への直近の照合が「従う」（か `--accept-unknown` で人が引き受けた「判定できない」）
  のときだけ宣言を書く。書くのはこの副命令だけである
- `show`: 版の本文、または版 M から N への差分
- `context`: 判断の地点へ渡す MVV の節（宣言が無くても共通原則と「MVV なし」を出す）。スプリント MVV の出所（`--sprint` の状態の
  `mvv`・`--mvv` のファイル・`--milestone` の説明）を 1 つ渡すと、プロジェクト MVV が承認済みのときだけスプリント MVV の節を足す（#1400）。
  特定できなければ止めずにプロジェクト MVV だけの節を出し、`--format json` の `sprint_mvv` に理由を書く
- `signals`: 現行の版のもとでの覆し・「判定できない」・流出不具合の件数と閾値。超えていれば `items` に改訂の提案

結果は lib/step_result.py の形の 1 行の JSON で、その前に人が読む行を出す。LLM は `supervise_lib/claude.py` の
`call_claude` を Tool なしで呼ぶ（NDF_SUPERVISE_CLAUDE で差し替えられる）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
import jsonio  # noqa: E402
import project_mvv as pm  # noqa: E402
import clock  # noqa: E402
import legacy_names  # noqa: E402
import project_mvv_decl as pmd  # noqa: E402
import project_mvv_signals as pms  # noqa: E402
from step_result import EXIT_GATE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, main_with, result, validate_result  # noqa: E402

from project_lib import mvv_candidates as mc  # noqa: E402
from project_lib import mvv_collect as mcol  # noqa: E402
from project_lib import mvv_llm  # noqa: E402

TOOL = "project-mvv"
EXIT_MISMATCH = 4


# ---------------------------------------------------------------- 共通


def _root(a) -> Path:
    return Path(a.root).resolve() if getattr(a, "root", None) else Path.cwd().resolve()


def _emit_code(obj: dict, code: int) -> None:
    """`emit` の対応表に無い終了コード（4 = 承認と一致しない）で終える。形は同じ検査を通す。"""
    errs = validate_result(obj)
    if errs:
        print("結果の形が誤っている: " + " / ".join(errs), file=sys.stderr)
        raise SystemExit(EXIT_UNREADABLE)
    print(json.dumps(obj, ensure_ascii=False))
    raise SystemExit(code)


def _work_dir(root: Path, out_dir: str | None) -> Path:
    if out_dir:
        d = Path(out_dir).expanduser().resolve()
    else:
        name = re.sub(r"[^0-9A-Za-z._-]+", "-", str(root)).strip("-") or "root"
        d = pm.mvv_state_base() / "project-mvv" / name
    try:
        d.relative_to(root)
        raise StepError(f"作業ファイルはリポジトリの外に置く（承認の前に .ndf/ へ何も書かない）: {d}", 1)
    except ValueError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- check


def cmd_check(a):
    root = _root(a)
    mvv = pm.load_mvv(root)
    label = pm.STATUS_LABEL[mvv.status]
    guide = {
        "none": "策定の手順（references/project-mvv.md）へ入る: collect → propose → 対話 → vet → 承認 → approve",
        "unapproved": "本文を vet に通し、利用者の承認の後に approve を打つ",
        "mismatch": "本文を承認の版へ戻すか、改訂の手順（propose --current → vet --kind revision → approve --reason）へ入る",
        "unreadable": ".ndf/mvv.json を直す（approve は壊れた宣言に版を足さない）",
    }
    print(f"プロジェクト MVV: {label}" + (f"（版 {mvv.version}）" if mvv.approved else ""))
    if mvv.error:
        print(mvv.error)
    items = [{"kind": "status", "name": mvv.status, "result": mvv.status, **pm.record(mvv)}]
    if mvv.approved:
        emit(result(TOOL, "ok", f"プロジェクト MVV は承認済み（版 {mvv.version}）", items, pm.record(mvv)))
    items.append({"kind": "next", "name": guide[mvv.status], "result": "guide"})
    out = result(TOOL, "stopped", label + (f": {mvv.error}" if mvv.error else ""), items, pm.record(mvv), next=guide[mvv.status])
    if mvv.status == "none":
        emit(out, EXIT_UNREADABLE)
    if mvv.status == "unreadable":
        emit(out, EXIT_PRECONDITION)
    _emit_code(out, EXIT_MISMATCH)


# ---------------------------------------------------------------- collect


def cmd_collect(a):
    root = _root(a)
    data = mcol.collect_materials(root, a)
    out = _work_dir(root, a.out_dir) / "materials.json"
    jsonio.write_atomic(out, data)
    print(f"材料: {out}（{data['mode']}・出典 {len(data['sources'])} 件・{data['seconds']} 秒）")
    for m in data["missing"]:
        print(f"欠け: {m['what']}（{m['reason']}）")
    emit(
        result(
            TOOL,
            "ok",
            f"材料を集めた（{'傾向モード' if data['mode'] == 'trend' else '履歴モード'}・出典 {len(data['sources'])} 件）: {out}",
            [{"kind": "missing", "name": m["what"], "result": "missing", "reason": m["reason"]} for m in data["missing"]],
            {"materials": str(out), "mode": data["mode"], **data["counts"], "sources": len(data["sources"]), "seconds": data["seconds"]},
        )
    )


# ---------------------------------------------------------------- propose


def cmd_propose(a):
    root = _root(a)
    mpath = Path(a.materials).resolve()
    try:
        mats = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"材料を読めない: {mpath}: {e}", EXIT_UNREADABLE) from None
    current = None
    kind = "new"
    if a.current:
        mvv = pm.load_mvv(root)
        if not mvv.approved:
            raise StepError(f"--current には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）", EXIT_PRECONDITION)
        current, kind = mvv.body, "revision"
    reason = Path(a.reason_file).read_text(encoding="utf-8") if a.reason_file else None
    data, raw, usage = mvv_llm.call_llm_json(mc.PROPOSE_SYSTEM, mc.propose_prompt(mats, current, reason), root, "mvv-propose")
    known = {s["id"] for s in mats.get("sources", [])}
    errs = ["LLM が候補を返さない"] if data is None else mc.candidate_problems(data, known)
    if errs:
        emit(
            result(
                TOOL,
                "stopped",
                "候補の形が足りない: " + "／".join(errs[:10]),
                [{"kind": "materials", "name": str(mpath), "result": "ask_user", "reason": "材料を示して利用者に直接問う", "raw": raw}],
                usage,
                next="材料を示して利用者に直接問う",
            ),
            1,
        )
    out_dir = _work_dir(root, a.out_dir or str(mpath.parent))
    bodies = {}
    for c in data["candidates"]:
        p = out_dir / f"candidate-{re.sub(r'[^0-9A-Za-z_-]+', '', str(c.get('id') or len(bodies) + 1))}.md"
        p.write_text(mc.candidate_body(c), encoding="utf-8")
        bodies[c.get("id")] = str(p)
    cj = out_dir / "candidates.json"
    jsonio.write_atomic(cj, {"materials": str(mpath), "kind": kind, **data, "bodies": bodies})
    cm = out_dir / "candidates.md"
    cm.write_text(mc.candidates_md(data, mats, kind, current, bodies), encoding="utf-8")
    print(f"候補: {cm}（{len(data['candidates'])} 案）")
    emit(
        result(
            TOOL,
            "ok",
            f"候補を {len(data['candidates'])} 案書いた: {cm}",
            [
                {
                    "kind": "candidate",
                    "name": str(c.get("id")),
                    "result": "recommended" if c.get("recommended") else "option",
                    "body": bodies[c.get("id")],
                }
                for c in data["candidates"]
            ],
            {**usage, "candidates_json": str(cj), "candidates_md": str(cm)},
            presentation_path=str(cm),
            next="candidates.md を示し、分かれる点を AskUserQuestion で問う。承認の前に .ndf/ へ書かない",
        )
    )


# ---------------------------------------------------------------- vet


def cmd_vet(a):
    root = _root(a)
    try:
        body = Path(a.body).read_text(encoding="utf-8")
    except OSError as e:
        raise StepError(f"本文を読めない: {e}", EXIT_UNREADABLE) from None
    rec, usage = mvv_llm.vet_body(root, body, a.kind)
    items = [
        {"kind": "location", "name": str(x.get("item", "")), "result": rec["verdict"], "reason": str(x.get("reason", ""))}
        for x in rec["locations"]
    ]
    for it in items:
        print(f"{it['name']}: {it['reason']}")
    if rec["verdict"] == "follow":
        emit(result(TOOL, "ok", f"照合: 従う（{a.kind}・sha256 {rec['sha256'][:12]}）", items, usage))
    label = {"suspect": "反する疑い", "unknown": "判定できない", "unreadable": "読めない"}[rec["verdict"]]
    nxt = (
        "箇所を示して本文を直し、照合をやり直す"
        if rec["verdict"] == "suspect"
        else "人へ戻す（判定できないを引き受けるなら approve --accept-unknown）"
    )
    emit(result(TOOL, "gate", f"照合: {label}（{a.kind}）", items, usage, next=nxt), EXIT_GATE)


# ---------------------------------------------------------------- approve


def last_vet(root: Path, sha: str) -> dict | None:
    key = pm.mvv_repo_key(root)
    rows = [
        r
        for r in pms.read_jsonl(pm.vet_log_path())
        if r.get("repo") == key and r.get("sha256") == sha and r.get("kind") in ("candidate", "revision")
    ]
    return rows[-1] if rows else None


def _approvable_vet(root: Path, sha: str, accept_unknown: bool) -> dict:
    vet = last_vet(root, sha)
    if vet is None:
        raise StepError("この本文への照合の記録が無い（vet を打ってから利用者の承認を取る）", 1)
    if vet["verdict"] == "unknown" and not accept_unknown:
        raise StepError("照合が「判定できない」。人が引き受けると決めたときだけ --accept-unknown で承認する", 1)
    if vet["verdict"] not in ("follow", "unknown"):
        where = "／".join(f"{x.get('item')}: {x.get('reason')}" for x in vet.get("locations") or [])
        raise StepError(f"照合が「{vet['verdict']}」のため承認できない（{where}）。本文を直して照合をやり直す", 1)
    return vet


def _next_version(decl_path: Path, body: str, sha: str, a, vet: dict) -> tuple[dict, list, dict]:
    versions: list = []
    settings: dict = json.loads(json.dumps(pm.DEFAULTS))
    if decl_path.is_file():
        data = json.loads(decl_path.read_text(encoding="utf-8"))
        versions, settings = list(data["versions"]), data.get("settings") or {}
    reason = (a.reason or "").strip()
    if versions:
        prev = versions[-1]
        if not reason:
            raise StepError("版 2 以降は改訂の理由（--reason）が要る", 1)
        if prev["sha256"] == sha:
            raise StepError(f"本文が現行の版 {prev['version']} と同じ", 1)
        diff = pm.changes(prev["body"], body)
    else:
        diff = []
        reason = reason or "初版"
    v = {
        "version": len(versions) + 1,
        "sha256": sha,
        "approved_at": clock.now_iso("utc"),
        "approved_by": a.by,
        "reason": reason,
        "vet": {"verdict": vet["verdict"], "at": vet["at"], "accepted_unknown": vet["verdict"] == "unknown"},
        "changes": diff,
        "body": body,
    }
    return v, versions, settings


def cmd_approve(a):
    root = _root(a)
    try:
        body = Path(a.body).read_text(encoding="utf-8")
    except OSError as e:
        raise StepError(f"本文を読めない: {e}", 1) from None
    probs = pm.shape_problems(body)
    if probs:
        raise StepError("本文の形が足りない: " + "／".join(f"{p['item']}: {p['reason']}" for p in probs), 1)
    mvv = pm.load_mvv(root)
    if mvv.status == "unreadable":
        raise StepError(f"宣言が壊れているため版を足さない: {mvv.error}", 1)
    sha = pm.sha256_text(body)
    vet = _approvable_vet(root, sha, a.accept_unknown)
    body_path, decl_path = pm.decl_paths(root)
    v, versions, settings = _next_version(decl_path, body, sha, a, vet)
    diff = v["changes"]
    decl = {"version": pm.DECL_VERSION, "body": pm.BODY_FILE, "settings": settings, "versions": [*versions, v]}
    errs = pm.decl_problems(decl)
    if errs:
        raise StepError("宣言の形が合わない: " + "／".join(errs), 1)
    try:
        pmd.write_decl(body_path, body, decl_path, decl)
    except OSError as e:
        raise StepError(f"宣言を書けない: {e}。本文を利用者へ示す", 1) from None
    print(f"プロジェクト MVV: 版 {v['version']} を書いた（{body_path}・{decl_path}）")
    emit(
        result(
            TOOL,
            "ok",
            f"プロジェクト MVV の版 {v['version']} を書いた（sha256 {sha[:12]}）",
            [{"kind": "change", "name": c["item"], "result": c["kind"]} for c in diff],
            {"version": v["version"], "sha256": sha, "body": str(body_path), "decl": str(decl_path)},
        )
    )


# ---------------------------------------------------------------- show


def _versions(root: Path) -> list[dict]:
    _, decl_path = pm.decl_paths(root)
    try:
        data = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"宣言を読めない: {e}", EXIT_UNREADABLE) from None
    if pm.decl_problems(data):
        raise StepError(f"宣言の形が違う: {decl_path}", EXIT_UNREADABLE)
    return data["versions"]


def cmd_show(a):
    root = _root(a)
    vs = _versions(root)
    n = a.version or len(vs)
    if not 1 <= n <= len(vs):
        raise StepError(f"版 {n} が無い（1〜{len(vs)}）", EXIT_UNREADABLE)
    v = vs[n - 1]
    if a.diff:
        if not 1 <= a.diff <= len(vs):
            raise StepError(f"版 {a.diff} が無い（1〜{len(vs)}）", EXIT_UNREADABLE)
        old = vs[a.diff - 1]
        text = "".join(difflib.unified_diff(old["body"].splitlines(True), v["body"].splitlines(True), f"版 {a.diff}", f"版 {n}"))
        print(text)
        ch = pm.changes(old["body"], v["body"])
        emit(
            result(
                TOOL,
                "ok",
                f"版 {a.diff} → 版 {n} の差分（{len(ch)} 項目）",
                [{"kind": "change", "name": c["item"], "result": c["kind"]} for c in ch],
                {"from": a.diff, "to": n},
            )
        )
    print(v["body"])
    emit(
        result(
            TOOL,
            "ok",
            f"版 {n}（{v['approved_at']}・{v['approved_by']}・{v.get('reason') or ''}）",
            [{"kind": "version", "name": str(n), "result": "shown", "sha256": v["sha256"], "reason": v.get("reason") or ""}],
            {"version": n, "versions": len(vs)},
        )
    )


# ---------------------------------------------------------------- context / signals


def cmd_context(a):
    root = _root(a)
    mvv = pm.load_mvv(root)
    sprint, ref = pm.sprint_source(a.sprint, a.mvv, a.milestone, a.repo)
    if sprint is not None and not mvv.approved:  # 決定 16: プロジェクト MVV が承認済みのときだけ足す
        sprint, ref = None, {"reason": f"プロジェクト MVV が{pm.STATUS_LABEL[mvv.status]}。スプリント MVV を節へ足さない"}
    text = pm.block(mvv, sprint)
    if a.format == "text":
        sys.stdout.write(text)
        return 0
    item = {"kind": "context", "name": mvv.status, "result": mvv.status, "project_mvv": pm.record(mvv), "block": text}
    if ref is not None:
        item["sprint_mvv"] = ref
    emit(result(TOOL, "ok", f"MVV の節（{pm.STATUS_LABEL[mvv.status]}）", [item], pm.record(mvv)))


def cmd_signals(a):
    root = _root(a)
    mvv = pm.load_mvv(root)
    if mvv.status == "unreadable":
        raise StepError(f"宣言が壊れている: {mvv.error}", EXIT_PRECONDITION)
    sig = pms.signals(root, mvv, escapes=mvv_llm.escape_events(root))
    sug = pms.revise_suggestion(sig) if mvv.approved else None
    c, t = sig["counts"], sig["thresholds"]
    head = f"版 {mvv.version}" if mvv.approved else pm.NO_MVV
    lines = [
        f"改訂の兆候（{head}）",
        f"  覆し: {c['overrides']} 件（従うを退けた {c['override_reject']}・反する疑いか判定できないを通した {c['override_pass']}。閾値 {t['overrides']}）",
        f"  判定できない: {c['unknowns']} 件（閾値 {t['unknowns']}）・続けて {c['unknown_streak']} 回（閾値 {t['unknown_streak']}）",
        f"  流出不具合: {c['escapes']} 件（閾値 {t['escapes']}）",
    ]
    if a.format == "text":
        print("\n".join(lines + ([f"  → {sug['reason']}"] if sug else [])))
    emit(result(TOOL, "ok", "；".join(ln.strip() for ln in lines), [sug] if sug else [], {**c, "version": mvv.version}))


def cmd_schema(a):
    import deps

    deps.require("schema")
    text = pmd.mvv_json_schema()
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        emit(result(TOOL, "ok", f"schema を書いた: {a.out}"))
    sys.stdout.write(text)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="project-mvv.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--root", help="リポジトリの根（既定はカレント）")
        p.set_defaults(func=func)
        return p

    add("check", cmd_check, "宣言の有無と承認との一致を判定する")
    p = add("collect", cmd_collect, "候補の材料を集める")
    p.add_argument("--out-dir")
    p.add_argument("--request-file", help="依頼文のファイル（傾向モードの材料）")
    p.add_argument("--trend-commits", type=int)
    p.add_argument("--trend-issues", type=int)
    p.add_argument("--budget", type=float, default=60.0, help="締め切りの秒（既定 60）")
    p = add("propose", cmd_propose, "候補を書く")
    p.add_argument("--materials", required=True)
    p.add_argument("--current", action="store_true", help="改訂案を書く（現行の本文を材料に足す）")
    p.add_argument("--reason-file")
    p.add_argument("--out-dir")
    p = add("vet", cmd_vet, "本文を NDF の共通原則とプロジェクト MVV に照らす")
    p.add_argument("--body", required=True)
    p.add_argument("--kind", required=True, choices=("candidate", "revision", "sprint"))
    p = add("approve", cmd_approve, "利用者の承認の後に宣言を書く")
    p.add_argument("--body", required=True)
    p.add_argument("--by", required=True, help="承認者")
    p.add_argument("--reason")
    p.add_argument("--accept-unknown", action="store_true")
    p = add("show", cmd_show, "版の本文か差分を出す")
    p.add_argument("--version", type=int)
    p.add_argument("--diff", type=int, help="この版から --version（既定は現行）への差分")
    p = add("context", cmd_context, "MVV の節を出す")
    p.add_argument("--format", choices=("text", "json"), default="text")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--sprint", help="スプリントの状態（sprint-state.py のファイル）。状態の mvv を sha256 の一致を見て読む")
    src.add_argument("--mvv", help="スプリント MVV のファイル")
    src.add_argument("--milestone", help="説明に ## Mission / ## Vision / ## Value を持つマイルストーン（写しは作らない）")
    p.add_argument("--repo", help="--milestone を読むリポジトリ（OWNER/REPO。既定はカレント）")
    p = add("signals", cmd_signals, "改訂の兆候を集計する")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    p.add_argument("--out")
    p.set_defaults(func=cmd_schema)
    return ap


def main(argv=None):
    argv = legacy_names.rewrite_argv("project-mvv.py", sys.argv[1:] if argv is None else list(argv))
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
