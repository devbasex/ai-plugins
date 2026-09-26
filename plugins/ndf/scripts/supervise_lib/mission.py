"""new mission / close の組み立てと、pace: fast と MVV の拒否の判定（#1142 の C1）。"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from pace import PaceError, read_pace
from step_result import result
from supervise_lib.decl import SUPERVISE_DECL, decl_roots, with_decls
from supervise_lib.mission_waves import (mission_branch, plan_fast_check, plan_fast_design, plan_fast_impl,
                                         plan_fast_release, plan_mission_branch, plan_mission_check,
                                         plan_mission_design, plan_mission_impl, plan_mission_release)
from supervise_lib.paths import CHECK_PY, HERE, MERGE_CMD, MERGE_PROBE, SELF, sha256_of
from supervise_lib.release_templates import RELEASE_FORMS


def prod_version(version: str) -> str:
    return re.sub(r"-.*$", "", version)


def fast_mission_plans(a) -> list[dict]:
    """pace: fast のミッションのステージ。ミッションのブランチを作らず、実装は起点のブランチへ直接入れる。
    実装の queue が --then のステージで 検査（実行の条件）→ 実装レビュー（開発版ごと）→ 開発版 → 本番（先頭が MVV 判定）を
    順に流す。検査が立てばその中でレビューも通るため、実装レビューのステージは範囲が空になり流れない。"""
    repo = str(Path(a.worktree).resolve())
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": plan_fast_design(a, n, repo) for n in a.design}})
        waves.append({"name": "関門 1", "gate": "設計の計画がすべて完了なら通過する。結果が関門の計画の Pull Request だけ、"
                                              "利用者の承認を取ってマージする"})
    waves += [
        {"name": "実装", "plans": {f"impl-{n}": plan_fast_impl(a, n, repo) for n in a.issue}},
        {"name": "検査", "plans": {"check": plan_fast_check(a, repo, f"{a.name}-1")}, "then_of": "実装"},
        {"name": "実装レビュー", "plans": {"review": plan_fast_check(a, repo, f"{a.name}-review", review_only=True)},
         "then_of": "実装"},
    ]
    if not has_release_template(a):
        return waves + [manual_release_wave(a)]
    waves += [
        {"name": "開発版", "plans": {"release": plan_fast_release(a, repo, a.version, "dev")}, "then_of": "実装"},
        {"name": "本番", "plans": {"release-prod": plan_fast_release(a, repo, prod_version(a.version), "prod")},
         "then_of": "実装"},
    ]
    return waves


def close_plan(a, repo: str) -> dict:
    """ミッションの終わりのまとめ: 確定仕様化 → Pull Request → 課題を閉じる → 振り返りを 1 回ずつ。"""
    issues = ",".join(map(str, a.issue))
    refs = " ".join(f"#{i}" for i in a.issue)
    branch = f"spec/{a.name}"
    stats = f"{CHECK_PY} stats --root {shlex.quote(repo)}"
    return with_decls({
        "フェーズ": "まとめ", "課題": a.issue, "モード": a.mode, "作業場所": f"{repo}/.worktrees/{branch}",
        "branch": branch, "起点": f"origin/{a.base}", "リポジトリ": repo, "記録": str(HERE / "projects-sync.sh"),
        "規則": "", "上限": 12, "進め方": "fast",
        "steps": [
            {"id": "spec", "type": "work", "full": True, "kind": "確定仕様化", "stage": "確定仕様化", "timeout": 3600,
             "prompt": f"/ndf:plan-to-spec {refs}。課題の issues/ の計画と設計を docs/ へ移し、コミットする"
                       "（push しない）。移すものが無ければ何もしない。", "next": "pr"},
            {"id": "pr", "type": "pr", "stage": "Pull Request", "base": a.base, "title": f"確定仕様化: ミッション {a.name}",
             "summary": f"ミッション {a.name}（{refs}）の計画と設計を docs/ へ移す。issues/ と docs/ だけを触る",
             "changes": "無し（文書の置き場所だけ）", "next": "ready"},
            {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "next": "merge"},
            {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "probe": MERGE_PROBE, "next": "close"},
            {"id": "close", "type": "run", "stage": "後片付け", "cwd": repo, "timeout": 900,
             "cmd": f"python3 {HERE / 'mission-close.py'} --record-pr {{queue_pr:release-prod}} --issues {issues} "
                    f"--with-verification --label {shlex.quote(f'ミッション {a.name}の後片付け')}", "next": "retro"},
            {"id": "retro", "type": "work", "full": True, "kind": "振り返り", "stage": "振り返り", "timeout": 3600,
             "prompt": f"/ndf:retrospective ミッション {a.name}（{refs}）。材料に検査の記録の集計（`{stats}` の出力: "
                       "トリガーが立った回数・検査ごとの指摘の件数・検査の後に逃げた不具合の件数）を使い、閾値の"
                       "見直しが要るかを書く。", "next": "end"},
        ],
    }, a)


def close_waves(a) -> list[dict]:
    """ミッションの終わりのステージ。最終の検査で変更があったときだけ開発版と本番が流れる。"""
    repo = str(Path(a.worktree).resolve())
    final = f"{a.name}-final"
    changed = {"cmd": f"{CHECK_PY} changed --id {final} --root {shlex.quote(repo)}", "skip_code": 3}
    return [
        {"name": "最終の検査", "plans": {"check": plan_fast_check(a, repo, final, final=True)}},
        {"name": "開発版", "plans": {"release": plan_fast_release(a, repo, a.version, "dev", changed)},
         "then_of": "最終の検査"},
        {"name": "本番", "plans": {"release-prod": plan_fast_release(a, repo, a.prod, "prod", changed)},
         "then_of": "最終の検査"},
        {"name": "まとめ", "plans": {"close": close_plan(a, repo)}, "then_of": "最終の検査"},
    ]


def fast_refusal(a) -> str | None:
    """pace: fast を使ってよい条件を確かめる。外れた理由を返す（満たせば None）。"""
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    try:
        pace = next((read_pace(r) for r in roots if (r / ".ndf" / "pace.json").is_file()), None)
    except PaceError as e:
        return str(e)
    if pace is None:
        return "進め方の宣言（.ndf/pace.json）が無い"
    if not pace["fast"]["enabled"]:
        return ".ndf/pace.json の fast.enabled が true でない"
    if not pace["fast"]["verify"]:
        return ".ndf/pace.json の fast.verify（導入の確認のコマンド）が無い"
    if a.mode not in pace["fast"]["modes"]:
        return f"モード {a.mode} は fast に入れられない（入れられるモード: {' / '.join(pace['fast']['modes'])}）"
    prod = a.production_branch
    if not prod:
        head = subprocess.run(["git", "-C", str(roots[0]), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                              capture_output=True, text=True).stdout.strip()
        prod = head[len("origin/"):] if head.startswith("origin/") else None
    if not prod or prod == a.base:
        return "開発版のチャネルが無い（起点のブランチと本番のブランチが同じか、本番のブランチが分からない）"
    return mvv_refusal(a.state)


def mvv_refusal(state_path: str | None) -> str | None:
    """MVV の承認の記録があり、そのハッシュが今の MVV と一致するか。外れた理由を返す。"""
    if not state_path:
        return "--pace fast には --state（ミッションの状態）が要る"
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as e:
        return f"ミッションの状態を読めない: {e}"
    mvv = state.get("mvv") or {}
    approval = next((g for g in state.get("gates") or [] if g.get("name") == "MVV"), None)
    if not mvv.get("path") or not Path(mvv["path"]).is_file():
        return "ミッションの状態に MVV が無い（mission-state.py init --pace fast --milestone M で写す）"
    if not approval or not approval.get("sha256"):
        return "MVV の承認の記録が無い（利用者の承認を得てから mission-state.py gate <状態> MVV を打つ）"
    now = sha256_of(Path(mvv["path"]))
    if not (now == mvv.get("sha256") == approval["sha256"]):
        return "MVV のハッシュが承認の記録と一致しない（承認の後に MVV が変わった）"
    return None


MANUAL_RELEASE = "/ndf:release"


def has_release_template(a) -> bool:
    """宣言のリリースの形（release.form）に雛形があるか。無い・知らない形なら False。"""
    return isinstance(a.release, dict) and a.release.get("form") in RELEASE_FORMS


def manual_release_wave(a) -> dict:
    """雛形の無いリリースの形のミッションの最後に置く、手で行うリリースの段（計画を持たない）。"""
    form = (a.release or {}).get("form") if isinstance(a.release, dict) else None
    why = (f"リリースの形 {form!r} に雛形が無い" if form
           else f".ndf/{SUPERVISE_DECL} に release.form が無い")
    return {"name": "リリース", "manual": MANUAL_RELEASE,
            "note": f"{why}（雛形のある形: {', '.join(RELEASE_FORMS)}）。検査の後に {MANUAL_RELEASE} で行う"}


def mission_plans(a) -> list[dict]:
    """ミッションのステージを順に返す。ステージの中の計画は queue --max 3 で同時に流してよい。
    リリースの形に雛形が無ければ、リリースの段の代わりに手で行う段（manual_release_wave）を最後に置く。"""
    if getattr(a, "pace", "normal") == "fast":
        return fast_mission_plans(a)
    repo = str(Path(a.worktree).resolve())
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": plan_mission_design(a, n, repo) for n in a.design}})
        waves.append({"name": "関門 1", "gate": "設計 Pull Request をまとめて承認してマージする"})
    waves += [
        {"name": "ミッションのブランチ", "plans": {"mission-branch": plan_mission_branch(a, repo)}},
        {"name": "実装", "plans": {f"impl-{n}": plan_mission_impl(a, n, repo) for n in a.issue}},
        {"name": "検査", "plans": {"check": plan_mission_check(a, repo)}},
    ]
    waves.append({"name": "配布", "plans": {"release": plan_mission_release(a, repo)}, "then_of": "検査"}
                 if has_release_template(a) else manual_release_wave(a))
    return waves


def cmd_new_mission(a, waves: list[dict] | None = None) -> dict:
    """ミッションの計画をステージごとのファイルへ書き出す。ステージは番号の順に queue で流す。
    then_of のステージは、そのステージの queue へ --then のステージとして足す（ステージは書いた順に流れる）。"""
    fast = waves is None and getattr(a, "pace", "normal") == "fast"
    if fast:
        why = fast_refusal(a)
        if why:
            return result("supervise-new", "stopped", f"pace: fast を使えない: {why}。計画を書かない", [],
                          {"pace": "fast"}, next="normal で進める（--pace を渡さない）か、条件を満たしてから打ち直す")
    waves = waves if waves is not None else mission_plans(a)
    out = Path(a.out or f"mission-{a.name}")
    out.mkdir(parents=True, exist_ok=True)
    items, index = [], []
    for i, wave in enumerate(waves, 1):
        entry = {"wave": i, "name": wave["name"]}
        if "gate" in wave:
            entry["gate"] = wave["gate"]
        elif "manual" in wave:
            entry.update(manual=wave["manual"], note=wave["note"])
        else:
            paths = []
            for key, plan in wave["plans"].items():
                p = out / f"{i}-{key}.json"
                p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
                paths.append(str(p))
            entry["plans"] = paths
            if "then_of" in wave:
                # 前のステージの queue が --then で続けて流す
                entry["then_of"] = wave["then_of"]
                prev = next(e for e in index if e["name"] == wave["then_of"])
                prev["command"] += " --then " + " ".join(map(shlex.quote, paths))
            else:
                entry["command"] = (f"python3 {shlex.quote(str(SELF))} queue "
                                    + " ".join(map(shlex.quote, paths)) + " --max 3")
        index.append(entry)
        items.append(entry)
    manifest = out / "mission.json"
    head = {"ミッション": a.name}
    if getattr(a, "state", None):
        head.update({"進め方": "fast", "状態": str(Path(a.state).resolve())})
    else:
        head["ブランチ"] = mission_branch(a.name)
    manifest.write_text(json.dumps({**head, "ステージ": index}, ensure_ascii=False, indent=2) + "\n")
    plans = sum(len(e.get("plans", [])) for e in index)
    nxt = "ステージの番号の順に command を打つ。関門のステージでは承認を取ってから次へ進む"
    manual = next((e for e in index if "manual" in e), None)
    if manual:
        nxt += f"。リリースは {manual['manual']} で行う（{manual['note']}）"
    return result("supervise-new", "ok", f"ミッション {a.name} の計画を {plans} 本・{len(index)} ステージで書いた: {manifest}",
                  items, {"waves": len(index), "plans": plans, "manifest": str(manifest)}, next=nxt)
