"""new mission / close の組み立てと、pace: fast / auto と MVV の拒否の判定（#1142 の C1・#1370）。"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import project_mvv
from pace import MVV_PACES, PaceError, read_pace
from step_result import result
from supervise_lib.decl import SUPERVISE_DECL, decl_roots, with_decls
from supervise_lib.mission_waves import (
    mission_branch,
    plan_fast_check,
    plan_fast_impl,
    plan_mission_branch,
    plan_mission_check,
    plan_mission_design,
    plan_mission_impl,
    plan_mission_release,
    plan_mvv_design,
    plan_mvv_release,
)
from supervise_lib.paths import CHECK_PY, HERE, SELF
from supervise_lib.release_templates import RELEASE_FORMS
from supervise_lib.verify_steps import merge_step


def prod_version(version: str) -> str:
    return re.sub(r"-.*$", "", version)


def fast_mission_plans(a) -> list[dict]:
    """pace: fast のミッションのステージ。ミッションのブランチを作らず、実装は起点のブランチへ直接入れる。
    実装の queue が --then のステージで 検査（実行の条件）→ 実装レビュー（開発版ごと）→ 開発版 → 本番（先頭が MVV 判定）を
    順に流す。検査が立てばその中でレビューも通るため、実装レビューのステージは範囲が空になり流れない。"""
    repo = str(Path(a.worktree).resolve())
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": plan_mvv_design(a, n, repo) for n in a.design}})
        waves.append(
            {
                "name": "関門 1",
                "gate": "設計の計画がすべて完了なら通過する。結果が関門の計画の Pull Request だけ、利用者の承認を取ってマージする",
            }
        )
    waves += [
        {"name": "実装", "plans": {f"impl-{n}": plan_fast_impl(a, n, repo) for n in a.issue}},
        {"name": "検査", "plans": {"check": plan_fast_check(a, repo, f"{a.name}-1")}, "then_of": "実装"},
        {"name": "実装レビュー", "plans": {"review": plan_fast_check(a, repo, f"{a.name}-review", review_only=True)}, "then_of": "実装"},
    ]
    if not has_release_template(a):
        return waves + [manual_release_wave(a)]
    waves += [
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev")}, "then_of": "実装"},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, prod_version(a.version), "prod")}, "then_of": "実装"},
    ]
    return waves


AUTO_GATE_1 = (
    "設計のプランの MVV 判定が通せばマージ済みで、このステージは通過する。設計のプランが関門を返したら、承認を取り "
    '`mission-state.py gate <状態> "関門 1" --by user` と `run <プラン> --from approve` の後に次のステージの resume を打つ'
)


def auto_mission_plans(a) -> list[dict]:
    """pace: auto のミッションのステージ。並びは normal と同じ（設計 → 関門 1 → ミッションのブランチ → 実装 → 検査）で、
    後ろに開発版と本番（先頭が MVV 判定の関門 2）を続ける。設計と開発版・本番だけを MVV 判定つきのプランで作り、
    ミッションのブランチ以降を最初のステージの --then で 1 本の queue に流す（#1370 の決定 3）。check-trigger.py は通らない。"""
    repo = str(Path(a.worktree).resolve())
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": plan_mvv_design(a, n, repo) for n in a.design}})
        waves.append({"name": "関門 1", "gate": AUTO_GATE_1})
    first = "設計" if a.design else "ミッションのブランチ"
    then = {"then_of": first} if a.design else {}
    impl = {}
    for n in a.issue:
        plan = plan_mission_impl(a, n, repo)
        plan["進め方"] = "auto"  # 課題の本文の見出し行と通過記録へ進め方を書く（supervise_lib/state.py）
        impl[f"impl-{n}"] = plan
    waves += [
        {"name": "ミッションのブランチ", "plans": {"mission-branch": plan_mission_branch(a, repo)}, **then},
        {"name": "実装", "plans": impl, "then_of": first},
        {"name": "検査", "plans": {"check": plan_mission_check(a, repo)}, "then_of": first},
    ]
    if not has_release_template(a):
        return waves + [manual_release_wave(a)]
    prs = ["{queue_pr:check}"]  # 出す版の PR は検査のステージのミッションの PR（関門 2 の判定のコメントの宛先）
    return waves + [
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev", prs=prs)}, "then_of": first},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, prod_version(a.version), "prod", prs=prs)}, "then_of": first},
    ]


def close_plan(a, repo: str) -> dict:
    """ミッションの終わりのまとめ: 確定仕様化 → Pull Request → 課題を閉じる → 振り返りを 1 回ずつ。"""
    issues = ",".join(map(str, a.issue))
    refs = " ".join(f"#{i}" for i in a.issue)
    branch = f"spec/{a.name}"
    stats = f"{CHECK_PY} stats --root {shlex.quote(repo)}"
    return with_decls(
        {
            "フェーズ": "まとめ",
            "課題": a.issue,
            "モード": a.mode,
            "作業場所": f"{repo}/.worktrees/{branch}",
            "branch": branch,
            "起点": f"origin/{a.base}",
            "リポジトリ": repo,
            "記録": str(HERE / "projects-sync.sh"),
            "規則": "",
            "上限": 12,
            "進め方": "fast",
            "steps": [
                {
                    "id": "spec",
                    "type": "work",
                    "full": True,
                    "kind": "確定仕様化",
                    "stage": "確定仕様化",
                    "timeout": 3600,
                    "prompt": f"/ndf:plan-to-spec {refs}。課題の issues/ の計画と設計を docs/ へ移し、コミットする"
                    "（push しない）。移すものが無ければ何もしない。",
                    "next": "pr",
                },
                {
                    "id": "pr",
                    "type": "pr",
                    "stage": "Pull Request",
                    "base": a.base,
                    "title": f"確定仕様化: ミッション {a.name}",
                    "summary": f"ミッション {a.name}（{refs}）の計画と設計を docs/ へ移す。issues/ と docs/ だけを触る",
                    "changes": "無し（文書の置き場所だけ）",
                    "next": "ready",
                },
                {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "next": "merge"},
                merge_step(a, next="close"),
                {
                    "id": "close",
                    "type": "run",
                    "stage": "後片付け",
                    "cwd": repo,
                    "timeout": 900,
                    "cmd": f"python3 {HERE / 'mission-close.py'} --record-pr {{queue_pr:release-prod}} --issues {issues} "
                    f"--with-verification --label {shlex.quote(f'ミッション {a.name}の後片付け')}",
                    "next": "retro",
                },
                {
                    "id": "retro",
                    "type": "work",
                    "full": True,
                    "kind": "振り返り",
                    "stage": "振り返り",
                    "timeout": 3600,
                    "prompt": f"/ndf:retrospective ミッション {a.name}（{refs}）。材料に検査の記録の集計（`{stats}` の出力: "
                    "トリガーが立った回数・検査ごとの指摘の件数・検査の後に逃げた不具合の件数）を使い、閾値の"
                    "見直しが要るかを書く。",
                    "next": "end",
                },
            ],
        },
        a,
    )


def close_waves(a) -> list[dict]:
    """ミッションの終わりのステージ。最終の検査で変更があったときだけ開発版と本番が流れる。"""
    repo = str(Path(a.worktree).resolve())
    final = f"{a.name}-final"
    changed = {"cmd": f"{CHECK_PY} changed --id {final} --root {shlex.quote(repo)}", "skip_code": 3}
    return [
        {"name": "最終の検査", "plans": {"check": plan_fast_check(a, repo, final, final=True)}},
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev", changed)}, "then_of": "最終の検査"},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, a.prod, "prod", changed)}, "then_of": "最終の検査"},
        {"name": "まとめ", "plans": {"close": close_plan(a, repo)}, "then_of": "最終の検査"},
    ]



def pace_refusal(a) -> str | None:
    """pace: fast / auto を使ってよい条件を確かめる。外れた理由を返す（満たせば None）。
    読む宣言の節が進め方の名前（fast / auto）である以外は同じ条件で、ほかの節の値は使わない。"""
    name = a.pace
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    try:
        pace = next((read_pace(r) for r in roots if (r / ".ndf" / "pace.json").is_file()), None)
    except PaceError as e:
        return str(e)
    if pace is None:
        return "進め方の宣言（.ndf/pace.json）が無い"
    sec = pace[name]
    if not sec["enabled"]:
        return f".ndf/pace.json の {name}.enabled が true でない"
    if not sec["verify"]:
        return f".ndf/pace.json の {name}.verify（導入の確認のコマンド）が無い"
    if a.mode not in sec["modes"]:
        return f"モード {a.mode} は {name} に入れられない（入れられるモード: {' / '.join(sec['modes'])}）"
    prod = a.production_branch
    if not prod:
        head = subprocess.run(
            ["git", "-C", str(roots[0]), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], capture_output=True, text=True
        ).stdout.strip()
        prod = head[len("origin/") :] if head.startswith("origin/") else None
    if not prod or prod == a.base:
        return "開発版のチャネルが無い（起点のブランチと本番のブランチが同じか、本番のブランチが分からない）"
    mroot = next((r for r in roots if any((r / ".ndf" / f).is_file() for f in ("mvv.md", "mvv.json"))), roots[0])
    return mvv_refusal(a.state, mroot, name)


def mvv_refusal(state_path: str | None, root=None, pace: str = "fast") -> str | None:
    """MVV の承認の照合（`lib/project_mvv.approval_refusal`）。ミッション MVV の承認の記録とハッシュの一致、
    無ければ承認済みのプロジェクト MVV と状態に残した参照の一致を見る。外れた理由を返す。"""
    if not state_path:
        return f"--pace {pace} には --state（ミッションの状態）が要る"
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as e:
        return f"ミッションの状態を読めない: {e}"
    if not isinstance(state, dict):
        return "ミッションの状態を読めない: オブジェクトでない"
    return project_mvv.approval_refusal(state, root or Path.cwd())


MANUAL_RELEASE = "/ndf:release"


def has_release_template(a) -> bool:
    """宣言のリリースの形（release.form）に雛形があるか。無い・知らない形なら False。"""
    return isinstance(a.release, dict) and a.release.get("form") in RELEASE_FORMS


def manual_release_wave(a) -> dict:
    """雛形の無いリリースの形のミッションの最後に置く、手で行うリリースの段（計画を持たない）。"""
    form = (a.release or {}).get("form") if isinstance(a.release, dict) else None
    why = f"リリースの形 {form!r} に雛形が無い" if form else f".ndf/{SUPERVISE_DECL} に release.form が無い"
    return {
        "name": "リリース",
        "manual": MANUAL_RELEASE,
        "note": f"{why}（雛形のある形: {', '.join(RELEASE_FORMS)}）。検査の後に {MANUAL_RELEASE} で行う",
    }


def mission_plans(a) -> list[dict]:
    """ミッションのステージを順に返す。ステージの中の計画は queue --max 3 で同時に流してよい。
    リリースの形に雛形が無ければ、リリースの段の代わりに手で行う段（manual_release_wave）を最後に置く。"""
    if getattr(a, "pace", "normal") == "fast":
        return fast_mission_plans(a)
    if getattr(a, "pace", "normal") == "auto":
        return auto_mission_plans(a)
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
    waves.append(
        {"name": "配布", "plans": {"release": plan_mission_release(a, repo)}, "then_of": "検査"}
        if has_release_template(a)
        else manual_release_wave(a)
    )
    return waves


def normal_command(a) -> str:
    """断ったときに示す normal の起動の形（--pace と --state を外した同じコマンド）。"""
    words = ["python3", str(SELF), "new", "mission", "--name", a.name, "--worktree", a.worktree, "--issue", *map(str, a.issue)]
    if a.design:
        words += ["--design", *map(str, a.design)]
    words += ["--version", a.version, "--mode", a.mode]
    if a.out:
        words += ["--out", a.out]
    return " ".join(map(shlex.quote, words))


def add_resume(index: list[dict]) -> None:
    """then_of のステージへ resume（そのステージから最後までの then_of のステージを流す queue のコマンド）を書く。
    承認ゲートで止まった後に続きを流すときに写す（#1370 の決定 4）。"""
    for i, entry in enumerate(index):
        if "then_of" not in entry:
            continue
        rest = [e for e in index[i + 1 :] if e.get("then_of") == entry["then_of"]]
        cmd = f"python3 {shlex.quote(str(SELF))} queue " + " ".join(map(shlex.quote, entry["plans"])) + " --max 3"
        entry["resume"] = cmd + "".join(" --then " + " ".join(map(shlex.quote, e["plans"])) for e in rest)


def cmd_new_mission(a, waves: list[dict] | None = None) -> dict:
    """ミッションの計画をステージごとのファイルへ書き出す。ステージは番号の順に queue で流す。
    then_of のステージは、そのステージの queue へ --then のステージとして足す（ステージは書いた順に流れる）。"""
    pace = getattr(a, "pace", "normal")
    if waves is None and pace in MVV_PACES:
        why = pace_refusal(a)
        if why:
            return result(
                "supervise-new",
                "stopped",
                f"pace: {pace} を使えない: {why}。計画を書かない",
                [],
                {"pace": pace},
                next=f"normal で進める（{normal_command(a)}）か、条件を満たしてから打ち直す",
            )
    waves = waves if waves is not None else mission_plans(a)
    out = Path(a.out or f"mission-{a.name}")
    out.mkdir(parents=True, exist_ok=True)
    index = write_stage_index(waves, out)
    add_resume(index)
    manifest = out / "mission.json"
    head = manifest_head(a, pace)
    manifest.write_text(json.dumps({**head, "ステージ": index}, ensure_ascii=False, indent=2) + "\n")
    plans = sum(len(e.get("plans", [])) for e in index)
    return result(
        "supervise-new",
        "ok",
        f"ミッション {a.name} の計画を {plans} 本・{len(index)} ステージで書いた: {manifest}",
        index,
        {"waves": len(index), "plans": plans, "manifest": str(manifest)},
        next=next_text(pace, index),
    )


def write_stage_index(waves: list[dict], out: Path) -> list[dict]:
    """ステージごとのプランをファイルへ書き、ステージの一覧（command / then_of の連結を含む）を返す。"""
    index = []
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
                entry["command"] = f"python3 {shlex.quote(str(SELF))} queue " + " ".join(map(shlex.quote, paths)) + " --max 3"
        index.append(entry)
    return index


def manifest_head(a, pace: str) -> dict:
    """manifest の見出し（ミッション / 進め方 / 状態 / ブランチ）。"""
    head = {"ミッション": a.name}
    if pace in MVV_PACES:
        head.update({"進め方": pace, "状態": str(Path(a.state).resolve())})
        if pace != "fast":
            head["ブランチ"] = mission_branch(a.name)
        return head
    # new close（waves を渡す）は --pace を持たず、状態があれば fast のミッションの終わり
    if getattr(a, "state", None):
        head.update({"進め方": "fast", "状態": str(Path(a.state).resolve())})
        return head
    head["ブランチ"] = mission_branch(a.name)
    return head


def next_text(pace: str, index: list[dict]) -> str:
    """次に打つ手の文面。auto だけ別の文で、manual のステージがあれば追記する。"""
    nxt = "ステージの番号の順に command を打つ。関門のステージでは承認を取ってから次へ進む"
    if pace == "auto":
        nxt = (
            "最初のステージの command を打つ（後ろのステージは --then で続く）。queue が gate を返したら、承認資料に判定の理由と"
            "根拠の項目を添えて承認を取り、関門のステージの説明に沿って続きのステージの resume を打つ"
        )
    manual = next((e for e in index if "manual" in e), None)
    if manual:
        nxt += f"。リリースは {manual['manual']} で行う（{manual['note']}）"
    return nxt
