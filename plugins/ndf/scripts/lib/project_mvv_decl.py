"""project_mvv_decl.py: プロジェクト MVV の履歴（`.ndf/mvv.json`）の形と設定の既定（#1366）。標準ライブラリだけで書く。

`decl_problems()` は標準ライブラリで形を見る（読み取りは例外を上げない）。同じ形の pydantic の型（`lib/schema.py` の
`Shape`）は `decl_models()` が組み、公開した `schemas/mvv.schema.json` はその生成物である。
"""

from __future__ import annotations

import json

BODY_FILE = "mvv.md"
DECL_VERSION = 1

# 汎用の既定（決定 7・8）。宣言の settings と引数で変える。特定のリポジトリの値を置かない（I12）
DEFAULTS: dict = {
    "trend_commits": 100,
    "trend_issues": 20,
    "unknown_streak": 3,
    "revise_after": {"overrides": 3, "unknowns": 5, "escapes": 3},
}


def resolve_settings(settings: dict | None) -> dict:
    """宣言の settings を既定へ重ねる。"""
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (settings or {}).items():
        if k == "revise_after" and isinstance(v, dict):
            out["revise_after"].update({kk: vv for kk, vv in v.items() if vv is not None})
        elif v is not None:
            out[k] = v
    return out


def decl_problems(data) -> list[str]:
    """`.ndf/mvv.json` の形の誤り（`decl_models()` の型と同じ規則を標準ライブラリで見る）。"""
    if not isinstance(data, dict):
        return ["オブジェクトでない"]
    errs = []
    allowed = {"version", "body", "settings", "versions"}
    errs += [f"{k}: 知らない項目" for k in sorted(set(data) - allowed)]
    if data.get("version") != DECL_VERSION:
        errs.append(f"version: {DECL_VERSION} にする")
    if not isinstance(data.get("body"), str) or not data.get("body"):
        errs.append("body: 文字列にする")
    st = data.get("settings", {})
    if not isinstance(st, dict):
        errs.append("settings: オブジェクトにする")
    else:
        errs += [f"settings.{k}: 知らない項目" for k in sorted(set(st) - set(DEFAULTS))]
        for k in ("trend_commits", "trend_issues", "unknown_streak"):
            if k in st and st[k] is not None and (not isinstance(st[k], int) or isinstance(st[k], bool) or st[k] < 0):
                errs.append(f"settings.{k}: 0 以上の整数にする")
        ra = st.get("revise_after")
        if ra is not None:
            if not isinstance(ra, dict):
                errs.append("settings.revise_after: オブジェクトにする")
            else:
                errs += [f"settings.revise_after.{k}: 知らない項目" for k in sorted(set(ra) - set(DEFAULTS["revise_after"]))]
                for k, v in ra.items():
                    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                        errs.append(f"settings.revise_after.{k}: 1 以上の整数にする")
    vs = data.get("versions")
    if not isinstance(vs, list) or not vs:
        errs.append("versions: 1 件以上の配列にする")
        return errs
    keys = {"version", "sha256", "approved_at", "approved_by", "reason", "vet", "changes", "body"}
    for i, v in enumerate(vs):
        w = f"versions[{i}]"
        if not isinstance(v, dict):
            errs.append(f"{w}: オブジェクトにする")
            continue
        errs += [f"{w}.{k}: 知らない項目" for k in sorted(set(v) - keys)]
        errs += [f"{w}.{k}: 必須の項目が無い" for k in sorted(keys - set(v) - {"reason"})]
        if v.get("version") != i + 1:
            errs.append(f"{w}.version: {i + 1} にする（版は 1 から 1 ずつ上がる）")
        for k in ("sha256", "approved_at", "approved_by", "body"):
            if k in v and (not isinstance(v[k], str) or not v[k]):
                errs.append(f"{w}.{k}: 空でない文字列にする")
        if i > 0 and not (isinstance(v.get("reason"), str) and v["reason"].strip()):
            errs.append(f"{w}.reason: 版 2 以降は改訂の理由が要る")
        if "vet" in v and not (isinstance(v["vet"], dict) and v["vet"].get("verdict") in ("follow", "unknown")):
            errs.append(f"{w}.vet: 承認の前の照合（verdict が follow か unknown）にする")
        if "changes" in v and not isinstance(v["changes"], list):
            errs.append(f"{w}.changes: 配列にする")
    return errs


def decl_models():
    """宣言の型（`lib/schema.py` の `Shape`）。使う側は `deps.require("schema")` を先に呼ぶ。"""
    from typing import Literal, Optional

    import schema
    from pydantic import Field

    class RevisePolicy(schema.Shape):
        overrides: Optional[int] = Field(default=None, ge=1)
        unknowns: Optional[int] = Field(default=None, ge=1)
        escapes: Optional[int] = Field(default=None, ge=1)

    class MvvSettings(schema.Shape):
        trend_commits: Optional[int] = Field(default=None, ge=0)
        trend_issues: Optional[int] = Field(default=None, ge=0)
        unknown_streak: Optional[int] = Field(default=None, ge=0)
        revise_after: Optional[RevisePolicy] = None

    class MvvVet(schema.Shape):
        verdict: Literal["follow", "unknown"]
        at: str
        accepted_unknown: bool = False

    class MvvChange(schema.Shape):
        item: str
        kind: Literal["added", "changed", "removed"]

    class MvvVersion(schema.Shape):
        version: int = Field(ge=1)
        sha256: str = Field(min_length=1)
        approved_at: str = Field(min_length=1)
        approved_by: str = Field(min_length=1)
        reason: Optional[str] = None
        vet: MvvVet
        changes: list[MvvChange]
        body: str = Field(min_length=1)

    class MvvDecl(schema.Shape):
        version: Literal[1]
        body: str = Field(default=BODY_FILE, min_length=1)
        settings: MvvSettings = Field(default_factory=MvvSettings)
        versions: list[MvvVersion] = Field(min_length=1)

    return MvvDecl, MvvVersion


def mvv_json_schema() -> str:
    decl, _ = decl_models()
    s = decl.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = "NDF のプロジェクト MVV の宣言（.ndf/mvv.json）"
    return json.dumps(s, ensure_ascii=False, indent=2) + "\n"
