"""手動反映の本番系（#1454）の承認ゲート 2 の材料を組む（`release-steps.py deploy-facts` から分けた）。

`verify`（`.ndf/pace.json` の `<節>.verify`）を `root` で `sh -c` で走らせ、本番系へ届ける行（`production: true` と、
`production` の無い手動の行）の target・trigger、ベースブランチの先頭のコミット（本番へ届けるのはこのコミットに限る）、
確認の終了コードと出力の末尾を `out` へ書く。宣言が読めない・`out` へ書けないときは EXIT_UNREADABLE の StepError を投げる。

使う側は `lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import delivery
import proc
from step_result import EXIT_UNREADABLE, StepError

DEPLOY_TAIL = 40  # 承認資料へ載せる確認の出力の末尾の行数


def deploy_rows(decl) -> list[dict]:
    """本番系へ届ける行: production: true と、production を書いていない手動の行（承認ゲート 2 の後に届ける。#1454 の I7）。"""
    return [r for r in decl.rows or [] if r.get("production") is True or (r.get("production") is None and r.get("kind") == "manual")]


def deploy_markdown(rows, base: str, sha: str, verify: str, code: int, tail: str) -> str:
    lines = ["# 本番のデプロイの承認資料", "", "## 本番系へ届ける行", ""]
    lines += [f"- {r.get('target') or '（無し）'}: `{r.get('trigger') or '（手順の記述無し）'}`" for r in rows] or ["- （無し）"]
    lines += [
        "",
        "## 届けるコミット",
        "",
        f"- ベースブランチ {base or '（不明）'} の先頭: `{sha or '（読めない）'}`。本番へ届けるのはこのコミットに限る。先頭が変わったら承認ゲート 2 からやり直す",
        "",
        "## 導入の確認",
        "",
        f"- コマンド: `{verify}`",
        f"- 終了コード: {code}",
        "",
        "```text",
        tail,
        "```",
        "",
    ]
    return "\n".join(lines)


def add_parser(sub, common, func) -> None:
    """`deploy-facts` のサブコマンドを足す。"""
    p = sub.add_parser("deploy-facts", parents=[common], help="手動反映の本番系の承認資料を書く（導入の確認を走らせる）")
    p.add_argument("--verify", required=True, help="導入の確認のコマンド（.ndf/pace.json の <節>.verify）")
    p.add_argument("--out", required=True, help="承認資料の置き場")
    p.set_defaults(func=func)


def deploy_facts(root, verify: str, out: str) -> tuple[list[dict], dict]:
    """承認資料を `out` へ書き、(items, metrics) を返す。metrics の verify_exit が確認の終了コード。"""
    decl = delivery.load_delivery(root)
    if decl.problems or decl.rows is None:
        why = " / ".join(decl.problems) or "delivery が無いか不明"
        raise StepError(f"配布の宣言を読めない: {why}", EXIT_UNREADABLE)
    rows = deploy_rows(decl)
    base = decl.base or ""
    if base:
        proc.git_out(root, "fetch", "-q", "origin", base)
    sha = (base and (proc.git_out(root, "rev-parse", f"origin/{base}") or proc.git_out(root, "rev-parse", base))) or ""
    p = subprocess.run(["sh", "-c", verify], cwd=root, capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).rstrip().splitlines()[-DEPLOY_TAIL:])
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(deploy_markdown(rows, base, sha, verify, p.returncode, tail), encoding="utf-8")
    except OSError as e:
        raise StepError(f"承認資料を書けない: {e}", EXIT_UNREADABLE) from e
    items = [{"kind": "decl", "name": r.get("target") or "", "result": r.get("trigger") or ""} for r in rows]
    return items, {"verify_exit": p.returncode, "rows": len(rows), "sha": sha, "base": base}
