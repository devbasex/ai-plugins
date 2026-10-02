"""スプリントのリリースの経路（lib/delivery.py の routes）と、それを届けるステージの組み立て（#1336・#1454）。"""

from __future__ import annotations

import delivery
from pace import PaceError, read_pace
from sprint_mvv import MVV_PACES
from supervise_lib.decl import DeclError, decl_roots, delivery_decl, require_versions
from supervise_lib.plan import QUEUE_PRS
from supervise_lib.delivery_templates import GATE_2_MATERIAL, plan_gate_2, plan_promote, plan_verify
from supervise_lib.release_templates import RELEASE_FORMS
from supervise_lib.verify_steps import plan_limits


def pace_decl(a) -> dict | None:
    """進め方の宣言（.ndf/pace.json）。宣言を探す場所の順に最初に見つかったもの。無ければ None、壊れていれば PaceError。"""
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    return next((read_pace(r) for r in roots if (r / ".ndf" / "pace.json").is_file()), None)


def dev_channel_of(a) -> delivery.DevChannel:
    """開発版のチャネルの形（lib/delivery.py の dev_channel）。宣言は apply_routes が載せた a.delivery を使う。"""
    decl = getattr(a, "delivery", None) or delivery_decl(a)
    return delivery.dev_channel(decl)


MANUAL_RELEASE = "/ndf:release"


def apply_routes(a) -> None:
    """a.routes（リリースの経路）を宣言から組み、雛形で組む経路なら版数を確かめる（足りなければ DeclError）。"""
    a.delivery = delivery_decl(a)
    a.routes = delivery.routes(a.delivery, tuple(RELEASE_FORMS))
    require_versions(a)


def routes_of(a) -> list:
    """リリースの経路（apply_decls が載せる a.routes。無ければ release.form だけから導く）。"""
    rs = getattr(a, "routes", None)
    if rs is None:
        d = delivery.build({}, {}, a.release, base=a.base, production=getattr(a, "production_branch", None))
        rs = delivery.routes(d, tuple(RELEASE_FORMS))
    return rs


def route_rows(a) -> list[dict]:
    """sprint.json の「リリースの経路」の行。"""
    return [r.as_dict() for r in routes_of(a)]


def has_release_template(a) -> bool:
    """リリースの経路が release.form の雛形（開発版 → 本番）で組むものか。"""
    return any(r.stage == delivery.STAGE_TEMPLATE for r in routes_of(a))


def manual_release_wave(a) -> dict:
    """手で届ける経路のために最後に置く、手で行うリリースのステージ（プランを持たない）。note に経路ごとの理由を書く。"""
    notes = [r.note for r in routes_of(a) if r.stage == delivery.STAGE_MANUAL and r.note]
    why = "。".join(notes) or "手で届ける経路がある"
    return {"name": "リリース", "manual": MANUAL_RELEASE, "note": f"{why}。検査の後に {MANUAL_RELEASE} で行う"}


def pace_verify(a) -> str:
    """選んだ進め方の節の verify（導入の確認のコマンド）。new close は fast のスプリントの終わりとして fast を読む。
    宣言が壊れている・節に verify が無ければ DeclError。"""
    pace = a.pace if getattr(a, "pace", None) in MVV_PACES else "fast"
    try:
        sec = (pace_decl(a) or {}).get(pace) or {}
    except PaceError as e:
        raise DeclError(str(e)) from e
    if not sec.get("verify"):
        raise DeclError(f".ndf/pace.json の {pace}.verify（導入の確認のコマンド）が無い")
    return sec["verify"]


def route_waves(a, repo: str, then_of: str, mvv: str | None = None, condition: dict | None = None, note: str | None = None) -> list[dict]:
    """雛形で組まない経路のステージ（#1336）。昇格の経路（promote）があれば「本番」（昇格のプラン）を、手で届ける経路
    （manual）があれば「リリース」（手で行う）をこの順に置く。fast / auto（mvv あり）では、昇格のプランの先頭で導入の確認を
    走らせ、昇格が無くベースブランチへのマージで届く経路（merged-by-check）があれば「導入の確認」のステージを置く（#1457）。
    normal の merged-by-check と、届けない経路（none）はステージを置かない。"""
    if mvv and dev_channel_of(a).value == delivery.MANUAL_PRODUCTION:
        return manual_production_waves(a, repo, then_of, mvv, condition)
    rs = routes_of(a)
    waves = []
    promote = next((r for r in rs if r.stage == delivery.STAGE_PROMOTE), None)
    merge = any(r.stage == delivery.STAGE_MERGED_BY_CHECK for r in rs)
    verify = pace_verify(a) if mvv and (promote or merge) else None
    if promote:
        ci_wait = int(plan_limits(a)["ci_wait_timeout"])
        plan = plan_promote(a, repo, ci_wait, mvv, condition, production=promote.branch, verify=verify)
        wave = {"name": "本番", "plans": {"promote": plan}, "then_of": then_of}
        if note:
            wave["note"] = note
        waves.append(wave)
    elif verify:
        wave = {"name": "導入の確認", "plans": {"verify": plan_verify(a, repo, verify, condition)}, "then_of": then_of}
        if note:
            wave["note"] = note
        waves.append(wave)
    if any(r.stage == delivery.STAGE_MANUAL for r in rs):
        waves.append(manual_release_wave(a))
    return waves


def manual_production_waves(a, repo: str, then_of: str, mvv: str, condition: dict | None = None) -> list[dict]:
    """手動反映の本番系の形（#1454）の fast / auto の手で届ける経路: 開発版（production: false の行。あれば）→
    承認ゲート 2（gate-2 のプラン）→ 本番（ほかの手で届ける行。production を書いていない行もここ）。開発版があれば
    承認ゲート 2 は単独の queue（conductor が開発版の後に command を打つ）、無ければ then_of で続けて流す。"""
    rs = [r for r in routes_of(a) if r.stage == delivery.STAGE_MANUAL]
    dev = [r for r in rs if r.production is False]
    prod = [r for r in rs if r.production is not False]
    pace = a.pace if getattr(a, "pace", None) in MVV_PACES else "fast"  # new close は fast のスプリントの終わり
    verify = pace_verify(a)
    # auto は検査のスプリントの PR（単独の queue でも同じディレクトリの検査の報告から埋まる）、fast は前のステージの PR のすべて。
    # fast の単独の queue は前のステージの PR を集められないため、MVV 判定を打たずに利用者の承認ゲートにする（決定 6）
    prs = "{queue_pr:check}" if pace == "auto" else None if dev else QUEUE_PRS
    gate = {"name": "承認ゲート 2", "plans": {"gate-2": plan_gate_2(a, repo, mvv, verify, prs, condition)}}
    waves = []
    if dev:
        why = "。".join(r.note for r in dev if r.note)
        waves.append(
            {
                "name": "開発版",
                "manual": MANUAL_RELEASE,
                "note": f"{why}。承認は要らない。検査の後に {MANUAL_RELEASE} で届け、済んだら承認ゲート 2 のステージの command を打つ",
            }
        )
    else:
        gate["then_of"] = then_of
    why = "。".join(r.note for r in prod if r.note) or "手で届ける経路がある"
    waves += [
        gate,
        {
            "name": "本番",
            "manual": MANUAL_RELEASE,
            "note": f"{why}。承認ゲート 2 が通過（関門 2 の記録が by: mvv か by: user）してから、承認資料（承認ゲート 2 のプランの "
            f"{GATE_2_MATERIAL}）のコミットを checkout して {MANUAL_RELEASE} で trigger を打つ。ベースブランチの先頭がそのコミットと"
            f"違えば、承認ゲート 2 の command からやり直す。{MANUAL_RELEASE} へはこの note と承認資料のパスを引き継ぐ",
        },
    ]
    return waves
