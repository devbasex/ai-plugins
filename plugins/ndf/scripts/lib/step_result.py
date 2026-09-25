"""step_result.py: Skill から呼ぶ手順のスクリプトが返す結果の共通の形（#846）。

結果は 1 行の JSON で標準出力へ出し、終了コードで終える。形と終了コードの表は同じ
ディレクトリの README.md にある。

    {"tool": "merged", "status": "ok|gate|stopped", "summary": "...", "items": [...],
     "metrics": {...}, "presentation_path": "...", "next": "..."}

読み手は `status`（と終了コード）だけで次の手を決める。`items[].result` の語彙はスクリプトごとに
持ってよい。承認の関門の提示物は `approval_present()` が書き出す。

後半は、手順のスクリプトが共通に使う小さな関数（git / gh の呼び出し、版の形、plugin の置き場所）。
標準ライブラリだけで書く。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

STATUSES = ("ok", "gate", "stopped")
REQUIRED = ("tool", "status", "summary", "items", "metrics")
OPTIONAL = ("presentation_path", "next")

EXIT_OK = 0
EXIT_VIOLATION = 1      # チェックで違反あり・手順が失敗した
EXIT_UNREADABLE = 2     # 読めない・呼び出しの誤り（「一致」「0 件」と読ませない）
EXIT_PRECONDITION = 3   # 前提が無い（宣言・認証・対象のファイル）、または各スクリプトが定めた正常な否定の結果
                        # （立たない・変更なし・飛ばしてよい）。読めないときは 2 で返し、3 と混ぜない
EXIT_GATE = 10          # 10〜19: 関門（人の同意が要る）
EXIT_PAUSE = 20         # 20〜29: LLM の判断待ち

GATE_CODES = range(10, 20)
PAUSE_CODES = range(20, 30)


def default_code(status: str) -> int:
    return {"ok": EXIT_OK, "gate": EXIT_GATE, "stopped": EXIT_VIOLATION}[status]


def code_matches(status: str, code: int) -> bool:
    """status と終了コードが食い違わないか。"""
    if status == "ok":
        return code == EXIT_OK
    if status == "gate":
        return code in GATE_CODES or code in PAUSE_CODES
    return code in (EXIT_VIOLATION, EXIT_UNREADABLE, EXIT_PRECONDITION)


def validate_result(obj, code: int | None = None) -> list[str]:
    """結果の形の誤りを並べて返す。空なら正しい。code を渡すと status との対応も確かめる。"""
    if not isinstance(obj, dict):
        return ["結果はオブジェクトで書く"]
    errs = []
    for k in REQUIRED:
        if k not in obj:
            errs.append(f"{k} が無い")
    unknown = set(obj) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        errs.append(f"知らない項目: {', '.join(sorted(unknown))}")
    if "tool" in obj and (not isinstance(obj["tool"], str) or not obj["tool"]):
        errs.append("tool は空でない文字列で書く")
    if "status" in obj and obj["status"] not in STATUSES:
        errs.append(f"status は {' / '.join(STATUSES)} のどれか: {obj['status']!r}")
    if "summary" in obj and not isinstance(obj["summary"], str):
        errs.append("summary は文字列で書く")
    if "items" in obj:
        if not isinstance(obj["items"], list):
            errs.append("items は配列で書く")
        else:
            for i, it in enumerate(obj["items"]):
                if not isinstance(it, dict):
                    errs.append(f"items[{i}] はオブジェクトで書く")
    if "metrics" in obj and not isinstance(obj["metrics"], dict):
        errs.append("metrics はオブジェクトで書く")
    for k in OPTIONAL:
        if k in obj and obj[k] is not None and not isinstance(obj[k], str):
            errs.append(f"{k} は文字列で書く")
    if obj.get("status") == "gate" and not obj.get("presentation_path") and not obj.get("next"):
        errs.append("gate には presentation_path か next を添える")
    if code is not None and obj.get("status") in STATUSES and not code_matches(obj["status"], code):
        errs.append(f"status {obj['status']} と終了コード {code} が合わない")
    return errs


def result(tool: str, status: str, summary: str, items=None, metrics=None,
           presentation_path: str | None = None, next: str | None = None) -> dict:
    out = {"tool": tool, "status": status, "summary": summary,
           "items": list(items or []), "metrics": dict(metrics or {})}
    if presentation_path:
        out["presentation_path"] = str(presentation_path)
    if next:
        out["next"] = next
    return out


def emit(obj: dict, code: int | None = None, stream=None) -> "NoReturn":  # noqa: F821
    """結果を 1 行の JSON で出し、終了コードで終える。

    code を省くと status から決める（ok=0 / gate=10 / stopped=1）。形が誤っていれば、
    誤りを標準エラーへ出して 2 で終える（誤った結果を「通った」と読ませない）。
    """
    if code is None and isinstance(obj, dict) and obj.get("status") in STATUSES:
        code = default_code(obj["status"])
    errs = validate_result(obj, code)
    if errs:
        print("結果の形が誤っている: " + " / ".join(errs), file=sys.stderr)
        raise SystemExit(EXIT_UNREADABLE)
    print(json.dumps(obj, ensure_ascii=False), file=stream or sys.stdout)
    (stream or sys.stdout).flush()
    raise SystemExit(code)


# --- 承認の関門の提示物 --------------------------------------------------------

def presentation_dir() -> Path:
    base = os.environ.get("NDF_PRESENTATION_DIR") or str(Path(tempfile.gettempdir()) / "ndf")
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d


def approval_present(tool: str, name: str, *, title: str, targets, change: str,
                     judge, consent, rollback: str, path=None) -> str:
    """approval-request.md の 2 層の形で提示物の Markdown を書き出し、パスを返す。

    targets: [{"url", "title"?, "base_head"?}]（URL は生のまま書く）
    change: 変更量（コミット数・ファイル数・行数など）
    judge: [(項目, 内容)]（判断に使うもの）
    consent: 同意を求める項目の列
    rollback: 戻し方（配布では取り消しの手段とその限界）
    """
    if not targets:
        raise ValueError("targets が空")
    if not consent:
        raise ValueError("consent が空")
    if not rollback or not rollback.strip():
        raise ValueError("rollback が空（戻し方を必ず示す）")
    lines = [f"# {title}", "", "## 1. 対象を開くためのもの", ""]
    for t in targets:
        url = t["url"]
        lines.append(f"- {url}" + (f"  {t['title']}" if t.get("title") else ""))
        if t.get("base_head"):
            lines.append(f"  - ベースと head: {t['base_head']}")
    lines += [f"- 変更量: {change}", "", "## 2. 承認の判断に使うもの", "",
              "| 項目 | 内容 |", "| --- | --- |"]
    for k, v in judge:
        lines.append(f"| {k} | {str(v).replace('|', chr(92) + '|').replace(chr(10), '<br>')} |")
    lines += ["", "## 同意を求めること", ""]
    lines += [f"- [ ] {c}" for c in consent]
    lines += ["", "## 戻し方", "", rollback.strip(), ""]
    if path is None:
        safe = re.sub(r"[^0-9A-Za-z._-]+", "-", f"{tool}-{name}").strip("-")
        path = presentation_dir() / f"{safe}-gate.md"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


# --- 手順のスクリプトが共通に使う小関数 ------------------------------------------

CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


class StepError(Exception):
    """手順の失敗。status=stopped と code（既定 1）で終える。"""

    def __init__(self, msg, code=EXIT_VIOLATION):
        super().__init__(msg)
        self.code = code


def run(cmd, cwd=None, check=True, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise StepError(f"{' '.join(map(str, cmd))} が終了コード {p.returncode}: {p.stderr.strip()[:500]}")
    return p


def git(root, *args, check=True):
    return run(["git", "-C", str(root), *args], check=check)


def git_root(arg):
    if arg:
        return Path(arg).resolve()
    p = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if p.returncode != 0:
        raise StepError("カレントが git の作業ツリーではない（--root を渡す）", EXIT_UNREADABLE)
    return Path(p.stdout.strip())


def commit(root, subject):
    git(root, "commit", "-q", "-m", f"{subject}\n\n{CO_AUTHOR}\n")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def repo_slug(root):
    """owner--name。gh が使えなければ origin の URL から決める。"""
    p = run(["gh", "repo", "view", "--json", "owner,name"], cwd=root, check=False) if _has_gh() else None
    if p is not None and p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            return f"{d['owner']['login']}--{d['name']}"
        except (ValueError, KeyError, TypeError):
            pass
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}--{m.group(2)}" if m else None


def _has_gh():
    from shutil import which
    return which("gh") is not None


def gh_json(root, args, what):
    try:
        p = run(["gh", *args], cwd=root, check=False)
    except FileNotFoundError:
        raise StepError("gh が無い", EXIT_PRECONDITION)
    if p.returncode != 0:
        raise StepError(f"{what} が失敗: {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout or "null")
    except ValueError:
        raise StepError(f"{what} の出力を読めない", EXIT_UNREADABLE)


def version_arg(s):
    if not VERSION_RE.match(s):
        raise argparse.ArgumentTypeError(f"版の形が X.Y.Z[-接尾辞] でない: {s}")
    return s


def base_of(v):
    return v.split("-", 1)[0]


def plugin_dir(root, name):
    d = root / "plugins" / name if name in ("ndf", "playwright-kit") else root / "plugins" / "mcp" / name
    if not (d / ".claude-plugin" / "plugin.json").is_file():
        raise StepError(f"プラグインが無い: {name}（{d.relative_to(root)}/.claude-plugin/plugin.json）",
                        EXIT_PRECONDITION)
    return d


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def common_parser():
    """--root をサブコマンドの前でも後でも受けるための親パーサ（後ろ側）。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="対象のリポジトリの根")
    return common


def main_with(ap, tool_of, argv=None):
    """parse して func を呼び、StepError を stopped の結果にして終える。"""
    a = ap.parse_args(argv)  # 引数の誤りは argparse が 2 で終える
    if not hasattr(a, "root"):
        a.root = None
    try:
        return a.func(a)
    except StepError as e:
        emit(result(tool_of(a), "stopped", str(e)), e.code)
