#!/usr/bin/env python3
"""NDF のコントラクト（`plugins/ndf/contracts/`）のうち、NDF の検査（`plugins/ndf/bin/ndf check`）が見ない 2 つを根で見る。

- `copy-mismatch`: 利用先の宣言の `$schema` が名指す Skill の下のスキーマ 4 本が、契約のスキーマ
  （`contracts/schemas/ndf-settings/<名前>.schema.json`）と 1 バイトでも違う（#3341 の決定 12。URL を変えずに済むよう
  中身をそろえる。どちらを正本にしてコピーを無くすかは #3518 の持ち物）
- `limits-shape`・`limits-unknown`: 上限の設定（`contracts/limits/`）の方針のファイルが読めない・コードが文字列で引く
  方針のファイルが無い（`contracts_lib/limits_decl.py`。#2343）

サブコマンドの引数・環境変数・設定のキー・受け渡しのファイル・公開する面と生成した規約のページは、NDF の検査の
規約の規則（spec-io・spec-decl・spec-public・spec-docs）が見る。宣言の形は `ndf contracts check` が見る。

    python3 scripts/check-ndf-contracts.py [--root <リポジトリ>] [<ファイル>...]

結果は 1 行 JSON（`{"tool", "status", "summary", "items", "metrics"}`）。終了コードは 0 違反なし・1 違反あり。
ファイルを渡すと、そのファイル（かディレクトリの下）に関わる違反だけで合否を決める。このスクリプトと部品が
範囲に入れば木全体の違反で決める。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
from allowlist import rel_target, under  # noqa: E402
from contracts_lib import limits_decl  # noqa: E402

REPO = HERE.parent
SELF = "scripts/check-ndf-contracts.py"
# チェッカ自身とその部品。範囲に入れば木全体を数える
WHOLE = (SELF, "scripts/contracts_lib", "scripts/lib/allowlist.py")
SETTINGS_SCHEMAS = "plugins/ndf/contracts/schemas/ndf-settings"
# 利用先が名指す Skill の下のスキーマ（コピー）と、同じ中身を持つ契約のスキーマの名前
SCHEMA_COPIES = (
    ("plugins/ndf/skills/development-workflow/schemas/project.schema.json", "project"),
    ("plugins/ndf/skills/worktree/schemas/worktree.schema.json", "worktree"),
    ("plugins/ndf/skills/release/schemas/release.schema.json", "release"),
    ("plugins/ndf/skills/release/schemas/instructions.schema.json", "instructions"),
)


def v(kind: str, path: str, name: str, detail: str, via: str = "") -> dict:
    d = {"kind": kind, "path": path, "name": name, "result": "violation", "detail": detail}
    if via:
        d["via"] = via
    return d


def _read_bytes(root: Path, rel: str) -> bytes | None:
    p = root / rel
    return p.read_bytes() if p.is_file() else None


def copy_mismatches(root: Path) -> list[dict]:
    """Skill の下のスキーマのコピーと契約のスキーマの 1 バイトの違い。"""
    out = []
    for copy, name in SCHEMA_COPIES:
        origin = f"{SETTINGS_SCHEMAS}/{name}.schema.json"
        a, b = _read_bytes(root, origin), _read_bytes(root, copy)
        if a is None or a != b:
            why = "契約のスキーマが無い" if a is None else ("コピーが無い" if b is None else "契約のスキーマと 1 バイト以上違う")
            out.append(v("copy-mismatch", copy, origin, f"{why}。契約のスキーマを直してから cp {origin} {copy} で写す", origin))
    return out


def limits_violations(root: Path) -> list[dict]:
    return [
        v(kind, path, name, why, limits_decl.policy_path(name) if name else "")
        for kind, path, name, why in limits_decl.violations(root, limits_decl.code_files(root))
    ]


def find(root: Path) -> list[dict]:
    """木全体の違反（パス・種類・名前の順）。"""
    found = copy_mismatches(root) + limits_violations(root)
    return sorted(found, key=lambda i: (i["path"], i["kind"], i["name"]))


def judge(found: list[dict], targets: set[str]) -> tuple[list[dict], int]:
    """範囲（`targets`。ファイルかディレクトリ）の合否に数える違反と、範囲の外の件数。違反の `path` か `via` が
    範囲に入れば数える。範囲が空か、チェッカ自身とその部品（`WHOLE`）が範囲に入れば木全体を数える。"""
    if not targets or any(under(t, WHOLE) or under(w, targets) for t in targets for w in WHOLE):
        return found, 0
    items = [x for x in found if under(x["path"], targets) or (x.get("via") and under(x["via"], targets))]
    return items, len(found) - len(items)


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    print(
        json.dumps(
            {"tool": "check-ndf-contracts", "status": status, "summary": summary, "items": items, "metrics": metrics}, ensure_ascii=False
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    # 例外リストとベースブランチとの比較は持たない（11.0.0 で外した）。CI の呼び出しが渡す間は受けて使わない
    ap.add_argument("--base", help=argparse.SUPPRESS)
    ap.add_argument("files", nargs="*", help="合否を決めるファイル（省くと木全体の違反で決める）")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    targets = {rel_target(root, t) for t in a.files}
    items, outside = judge(find(root), targets)
    metrics = {"schema_copies": len(SCHEMA_COPIES), "violations": len(items)}
    if targets:
        metrics.update(targets=len(targets), outside=outside)
    if items:
        for i in items:
            print(f"{i['kind']}: {i['path']}: {i['name']}: {i['detail'][:200]}", file=sys.stderr)
        emit("stopped", f"契約の違反 {len(items)} 件", items, metrics)
        return 1
    emit("ok", f"違反なし（スキーマのコピー {len(SCHEMA_COPIES)} 本・上限の設定）", items, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
