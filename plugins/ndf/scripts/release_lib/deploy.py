"""手動反映の本番系（#1454）の承認ゲート 2 の材料を組む（`release-steps.py deploy-facts`）。

    python3 release-steps.py deploy-facts --verify <コマンド> --out <承認資料> [--root <dir>]

`verify`（`.ndf/pace.json` の `<節>.verify`）を `root` で `sh -c` で走らせ、本番系へ届ける行（`production: true` と、
`production` の無い手動の行）の target・trigger、ベースブランチの先頭のコミット（本番へ届けるのはこのコミットに限る）、
確認の終了コードを `out` へ書く。確認するコミットと届けるコミットを同じにするため、`root` の HEAD が先頭と違う・
追跡中のファイルに変更があるときは確認を走らせずに 3 で止まる。確認の出力は秘密を含みうるため承認資料
（MVV 判定で外部の LLM へ渡る）へ載せず、所有者だけが読める `<out>.verify.log` へ分ける。宣言が読めない・
`out` へ書けないときは EXIT_UNREADABLE の StepError を投げる。

使う側は `lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import delivery
import proc
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, git_root, result

TOOL = "release"


def deploy_rows(decl) -> list[dict]:
    """本番系へ届ける行: production: true と、production を書いていない手動の行（承認ゲート 2 の後に届ける。#1454 の I7）。"""
    return [r for r in decl.rows or [] if r.get("production") is True or (r.get("production") is None and r.get("kind") == "manual")]


def deploy_markdown(rows, base: str, sha: str, verify: str, code: int) -> str:
    lines = ["# 本番のデプロイの承認資料", "", "## 本番系へ届ける行", ""]
    lines += [f"- {r.get('target') or '（無し）'}: `{r.get('trigger') or '（手順の記述無し）'}`" for r in rows] or ["- （無し）"]
    lines += [
        "",
        "## 届けるコミット",
        "",
        f"- ベースブランチ {base} の先頭: `{sha}`。導入の確認もこのコミットで走らせた。本番へ届けるのはこのコミットに限る。"
        "先頭が変わったら承認ゲート 2 からやり直す",
        "",
        "## 導入の確認",
        "",
        f"- コマンド: `{verify}`",
        f"- 終了コード: {code}",
        "- 出力は秘密を含みうるため、この資料へ載せない（手元のログに残す）",
        "",
    ]
    return "\n".join(lines)


def add_deploy_parser(sub, common, ap):
    """`deploy-facts` のサブコマンドを足し、`ap` をそのまま返す。"""
    p = sub.add_parser("deploy-facts", parents=[common], help="手動反映の本番系の承認資料を書く（導入の確認を走らせる）")
    p.add_argument("--verify", required=True, help="導入の確認のコマンド（.ndf/pace.json の <節>.verify）")
    p.add_argument("--out", required=True, help="承認資料の置き場")
    p.set_defaults(func=cmd_deploy_facts)
    return ap


def cmd_deploy_facts(a):
    """確認が非 0 なら 1（承認資料は書く）、宣言が読めない・--out へ書けないなら 2、確認するコミットが違えば 3 で終える。"""
    items, m = deploy_facts(git_root(a.root), a.verify, a.out)
    if m["verify_exit"] != 0:
        emit(result(TOOL, "stopped", f"導入の確認が {m['verify_exit']} で終わった。本番のデプロイへ進まない", items, m, a.out))
    emit(result(TOOL, "ok", f"承認資料を書いた（本番系の行 {m['rows']}・{m['base']} の先頭 {m['sha'][:8]}）", items, m, a.out))


def head_of_base(root, base: str) -> str:
    """ベースブランチの先頭のコミット。root の HEAD がそれと違う・追跡中のファイルに変更があれば StepError（3）。"""
    if not base:
        raise StepError("ベースブランチを決められない。届けるコミットを定められない", EXIT_PRECONDITION)
    proc.git_out(root, "fetch", "-q", "origin", base)
    sha = proc.git_out(root, "rev-parse", f"origin/{base}") or proc.git_out(root, "rev-parse", base) or ""
    head = proc.git_out(root, "rev-parse", "HEAD") or ""
    dirty = proc.git_out(root, "status", "--porcelain", "--untracked-files=no")
    if not sha or head != sha or dirty is None or dirty:
        why = f"HEAD {head[:8] or '不明'} / {base} の先頭 {sha[:8] or '不明'}" + ("・追跡中のファイルに変更あり" if dirty else "")
        raise StepError(f"確認するコミットが届けるコミットと違う（{why}）。{root} を {base} の先頭にしてから打ち直す", EXIT_PRECONDITION)
    return sha


def write_private(path: Path, text: str) -> None:
    """所有者だけが読めるファイル（0600）として書く。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def deploy_facts(root, verify: str, out: str) -> tuple[list[dict], dict]:
    """承認資料を `out` へ書き、(items, metrics) を返す。metrics の verify_exit が確認の終了コード、log が出力の置き場。"""
    decl = delivery.load_delivery(root)
    if decl.problems or decl.rows is None:
        why = " / ".join(decl.problems) or "delivery が無いか不明"
        raise StepError(f"配布の宣言を読めない: {why}", EXIT_UNREADABLE)
    rows = deploy_rows(decl)
    base = decl.base or ""
    sha = head_of_base(root, base)
    p = subprocess.run(["sh", "-c", verify], cwd=root, capture_output=True, text=True)
    log = Path(f"{out}.verify.log")
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        write_private(log, p.stdout + p.stderr)
        Path(out).write_text(deploy_markdown(rows, base, sha, verify, p.returncode), encoding="utf-8")
    except OSError as e:
        raise StepError(f"承認資料を書けない: {e}", EXIT_UNREADABLE) from e
    items = [{"kind": "decl", "name": r.get("target") or "", "result": r.get("trigger") or ""} for r in rows]
    return items, {"verify_exit": p.returncode, "rows": len(rows), "sha": sha, "base": base, "log": str(log)}
