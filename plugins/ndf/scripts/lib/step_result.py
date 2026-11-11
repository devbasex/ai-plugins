"""step_result.py: Skill から呼ぶ手順のスクリプトが返す結果の共通の形（#846）。

結果は 1 行の JSON で標準出力へ出し、終了コードで終える。形と終了コードの表は同じ
ディレクトリの README.md にある。

    {"tool": "merged", "status": "ok|gate|stopped", "summary": "...", "items": [...],
     "metrics": {...}, "gaps": [...], "process": [...], "decisions": [...],
     "presentation_path": "...", "next": "..."}

読み手は `status`（と終了コード）だけで次の手を決め、成功と読むのは `ok`（終了コード 0）だけである。
`ok` は欠け（`gaps`）が無いときだけ返せ、欠けが残るなら `stopped` と終了コード 6 で返す（#2227）。
`items[].result` の語彙はスクリプトごとに持ってよい。承認ゲートの提示物は `approval_present()` が書き出す。
シェルのスクリプトは `python3 step_result.py emit ...`（`step-result.sh` の `sr_emit`）で同じ形を出す。
ほかの手順の結果から値を読むときは `... | python3 step_result.py pick --field <JSON Pointer> [--raw]` を使い、
`python -c` で読み直さない（#2909。取り出しの規則は `json_pick.py`）。

後半は、手順のスクリプトが共通に使う小さな関数（git / gh の呼び出し、版の形、plugin の置き場所）。
標準ライブラリだけで書く。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import exit_codes  # noqa: E402
import jsonio  # noqa: E402
import proc  # noqa: E402

# 子プロセスと git の起動は `proc` が持つ（#1142 の L0）。ここの名前は同じものを指す
StepError = proc.StepError
run = proc.run
git = proc.git
git_root = proc.git_root

STATUSES = ("ok", "gate", "stopped")
REQUIRED = ("tool", "status", "summary", "items", "metrics")
OPTIONAL = ("presentation_path", "next", "run_id")
# オブジェクトの任意の項目。`lease` は持ち主の鍵を宣言した入口の持ち主の記録の結果（#2342）
OPTIONAL_OBJECTS = ("lease",)
# 核の外で動く入口へ、実行の核が結果に足す項目（`run_core.hold` が `lease` を置く）。`emit` が出す前に足す
ATTACH: dict = {}
# 欠け・過程・判断の項目（任意）。各要素はオブジェクトで、この鍵を空でない文字列で持つ（#2227 の I3）
RECORD_KEYS = {"gaps": ("kind", "name", "reason"), "process": ("run", "result"), "decisions": ("decision", "basis")}

# 終了コードの表は `exit_codes` が持つ（#2988）。ここの名前は同じものを指す
EXIT_OK = exit_codes.EXIT_OK
EXIT_VIOLATION = exit_codes.EXIT_VIOLATION
EXIT_UNREADABLE = exit_codes.EXIT_UNREADABLE
EXIT_PRECONDITION = exit_codes.EXIT_PRECONDITION
EXIT_BASE_CONFLICT = exit_codes.EXIT_BASE_CONFLICT
EXIT_CD_FAILED = exit_codes.EXIT_CD_FAILED
EXIT_INCOMPLETE = exit_codes.EXIT_INCOMPLETE
EXIT_GATE = exit_codes.EXIT_GATE
EXIT_PAUSE = exit_codes.EXIT_PAUSE
EXIT_DEPS_MISSING = exit_codes.EXIT_DEPS_MISSING
EXIT_INFRA_WAIT = exit_codes.EXIT_INFRA_WAIT
GATE_CODES = exit_codes.GATE_CODES
PAUSE_CODES = exit_codes.PAUSE_CODES
STOPPED_CODES = exit_codes.STOPPED_CODES
EXIT_LABELS = exit_codes.EXIT_LABELS
label_of = exit_codes.label_of

# 実行の核（`run_core.py`）が動いている間は真。`emit` は出力せずに `Emitted` を投げ、核が確かめてから出す（決定 3）
CAPTURE = False


class Emitted(SystemExit):
    """核が動いている間の `emit` の結果。本体の `except Exception` に握られないよう SystemExit の子にする。"""

    def __init__(self, result, code: int):
        super().__init__(code)
        self.result = result


def default_code(status: str) -> int:
    return {"ok": EXIT_OK, "gate": EXIT_GATE, "stopped": EXIT_VIOLATION}[status]


def code_matches(status: str, code: int) -> bool:
    """status と終了コードが食い違わないか。"""
    if status == "ok":
        return code == EXIT_OK
    if status == "gate":
        return code in GATE_CODES or code in PAUSE_CODES
    return code in STOPPED_CODES


def _check_item_types(obj: dict, errs: list[str]) -> None:
    if "items" not in obj:
        return
    if not isinstance(obj["items"], list):
        errs.append("items は配列で書く")
        return
    for i, it in enumerate(obj["items"]):
        if not isinstance(it, dict):
            errs.append(f"items[{i}] はオブジェクトで書く")


def _check_records(obj: dict, errs: list[str]) -> None:
    """gaps / process / decisions の各要素が決めた鍵を空でない文字列で持つか（I3）。"""
    for field, keys in RECORD_KEYS.items():
        if field not in obj:
            continue
        if not isinstance(obj[field], list):
            errs.append(f"{field} は配列で書く")
            continue
        for i, it in enumerate(obj[field]):
            if not isinstance(it, dict):
                errs.append(f"{field}[{i}] はオブジェクトで書く")
                continue
            for k in keys:
                if not isinstance(it.get(k), str) or not it[k].strip():
                    errs.append(f"{field}[{i}] の {k} を空でない文字列で書く")


def _check_field_types(obj: dict, errs: list[str]) -> None:
    if "tool" in obj and (not isinstance(obj["tool"], str) or not obj["tool"]):
        errs.append("tool は空でない文字列で書く")
    if "status" in obj and obj["status"] not in STATUSES:
        errs.append(f"status は {' / '.join(STATUSES)} のどれか: {obj['status']!r}")
    if "summary" in obj and not isinstance(obj["summary"], str):
        errs.append("summary は文字列で書く")
    _check_item_types(obj, errs)
    _check_records(obj, errs)
    if "metrics" in obj and not isinstance(obj["metrics"], dict):
        errs.append("metrics はオブジェクトで書く")
    for k in OPTIONAL:
        if k in obj and obj[k] is not None and not isinstance(obj[k], str):
            errs.append(f"{k} は文字列で書く")
    for k in OPTIONAL_OBJECTS:
        if k in obj and not isinstance(obj[k], dict):
            errs.append(f"{k} はオブジェクトで書く")


def _check_status_code(obj: dict, code: int | None, errs: list[str]) -> None:
    if obj.get("status") == "gate" and not obj.get("presentation_path") and not obj.get("next"):
        errs.append("gate には presentation_path か next を添える")
    has_gaps = isinstance(obj.get("gaps"), list) and bool(obj["gaps"])
    if obj.get("status") == "ok" and has_gaps:
        errs.append("ok に gaps を添えない（欠けが残るなら stopped と終了コード 6）")
    if code == EXIT_INCOMPLETE and obj.get("status") == "stopped" and not has_gaps:
        errs.append("終了コード 6 には gaps を 1 件以上書く")
    if code is not None and obj.get("status") in STATUSES and not code_matches(obj["status"], code):
        errs.append(f"status {obj['status']} と終了コード {code} が合わない")


def validate_result(obj, code: int | None = None) -> list[str]:
    """結果の形の誤りを並べて返す。空なら正しい。code を渡すと status との対応も確かめる。"""
    if not isinstance(obj, dict):
        return ["結果はオブジェクトで書く"]
    errs = [f"{k} が無い" for k in REQUIRED if k not in obj]
    unknown = set(obj) - set(REQUIRED) - set(OPTIONAL) - set(RECORD_KEYS) - set(OPTIONAL_OBJECTS)
    if unknown:
        errs.append(f"知らない項目: {', '.join(sorted(unknown))}")
    _check_field_types(obj, errs)
    _check_status_code(obj, code, errs)
    return errs


def result(
    tool: str,
    status: str,
    summary: str,
    items=None,
    metrics=None,
    presentation_path: str | None = None,
    next: str | None = None,
    *,
    gaps=None,
    process=None,
    decisions=None,
) -> dict:
    """結果を組む。gaps / process / decisions は渡したときだけ置く（空の配列は置かない）。"""
    out = {"tool": tool, "status": status, "summary": summary, "items": list(items or []), "metrics": dict(metrics or {})}
    for k, v in (("gaps", gaps), ("process", process), ("decisions", decisions)):
        if v:
            out[k] = list(v)
    if presentation_path:
        out["presentation_path"] = str(presentation_path)
    if next:
        out["next"] = next
    return out


def emit(obj: dict, code: int | None = None, stream=None) -> "NoReturn":  # noqa: F821
    """結果を 1 行の JSON で出し、終了コードで終える。

    code を省くと status から決める（ok=0 / gate=10 / stopped=1。`stopped` で gaps があれば 6）。形が誤っていれば、
    誤りを標準エラーへ出して 2 で終える（誤った結果を「通った」と読ませない）。実行の核が動いている間は出さずに
    `Emitted` を投げ、核が形と終了コードを確かめてから出す。
    """
    if code is None and isinstance(obj, dict) and obj.get("status") in STATUSES:
        code = default_code(obj["status"])
        if obj["status"] == "stopped" and isinstance(obj.get("gaps"), list) and obj["gaps"]:
            code = EXIT_INCOMPLETE
    if CAPTURE and stream is None:
        raise Emitted(obj, EXIT_VIOLATION if code is None else code)
    if ATTACH and isinstance(obj, dict):
        obj = {**obj, **ATTACH}
    errs = validate_result(obj, code)
    if errs:
        print("結果の形が誤っている: " + " / ".join(errs), file=sys.stderr)
        raise SystemExit(EXIT_UNREADABLE)
    print(json.dumps(obj, ensure_ascii=False), file=stream or sys.stdout)
    (stream or sys.stdout).flush()
    raise SystemExit(code)


# --- 承認ゲートの提示物 --------------------------------------------------------


def presentation_dir() -> Path:
    base = os.environ.get("NDF_PRESENTATION_DIR") or str(Path(tempfile.gettempdir()) / "ndf")
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_present_args(targets, consent, rollback: str) -> None:
    if not targets:
        raise ValueError("targets が空")
    if not consent:
        raise ValueError("consent が空")
    if not rollback or not rollback.strip():
        raise ValueError("rollback が空（戻し方を必ず示す）")


def _targets_lines(targets) -> list[str]:
    """「対象を開くためのもの」節の箇条（URL は生のまま書く）。"""
    lines = []
    for t in targets:
        url = t["url"]
        lines.append(f"- {url}" + (f"  {t['title']}" if t.get("title") else ""))
        if t.get("base_head"):
            lines.append(f"  - ベースと head: {t['base_head']}")
    return lines


def _judge_table(judge) -> list[str]:
    """「判断に使うもの」の表。"""
    lines = ["| 項目 | 内容 |", "| --- | --- |"]
    for k, v in judge:
        lines.append(f"| {k} | {str(v).replace('|', chr(92) + '|').replace(chr(10), '<br>')} |")
    return lines


# approval_present が書く `## ` の見出し（書く順）。承認資料の作り直しで、これ以外の節を残す（#1834）
PRESENT_HEADINGS = ("## 1. 対象を開くためのもの", "## 2. 承認の判断に使うもの", "## 同意を求めること", "## 戻し方")


def approval_present(
    tool: str, name: str, *, title: str, targets, change: str, judge, consent, rollback: str, path=None, sections=()
) -> str:
    """approval-request.md の 2 層の形で提示物の Markdown を書き出し、パスを返す。

    targets: [{"url", "title"?, "base_head"?}]（URL は生のまま書く）
    change: 変更量（コミット数・ファイル数・行数など）
    judge: [(項目, 内容)]（判断に使うもの）
    consent: 同意を求める項目の列
    rollback: 戻し方（デプロイでは取り消しの手段とその限界）
    sections: [(見出し, 行の列)]。`## 同意を求めること` の前へ、この順で置く
    """
    _validate_present_args(targets, consent, rollback)
    open_h, judge_h, consent_h, rollback_h = PRESENT_HEADINGS
    lines = [f"# {title}", "", open_h, ""]
    lines += _targets_lines(targets)
    lines += [f"- 変更量: {change}", "", judge_h, ""]
    lines += _judge_table(judge)
    lines.append("")
    for heading, body in sections:
        lines += [heading, "", *body, ""]
    lines += [consent_h, ""]
    lines += [f"- [ ] {c}" for c in consent]
    lines += ["", rollback_h, "", rollback.strip(), ""]
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


def commit(root, subject, paths=()):
    """コミットして HEAD を返す。paths を渡すとその分だけを確定し（`git commit --only`）、他に stage 済みの変更は残す。"""
    git(root, "commit", "-q", "-m", f"{subject}\n\n{CO_AUTHOR}\n", *(["--only", "--", *paths] if paths else []))
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
        p = proc.run(["gh", *args], cwd=root, check=False)
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
        raise StepError(f"プラグインが無い: {name}（{d.relative_to(root)}/.claude-plugin/plugin.json）", EXIT_PRECONDITION)
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


# --- シェルから結果を出す CLI（`step-result.sh` の `sr_emit` が呼ぶ。#2227） ----------------


def _metric_value(v: str):
    try:
        return int(v)
    except ValueError:
        return v


def _cli_result(a) -> dict:
    items = [{"kind": k, "name": n, "result": r, **({"reason": why} if why else {})} for k, n, r, why in a.item]
    return result(
        a.tool,
        a.status,
        a.summary,
        items,
        {k: _metric_value(v) for k, v in a.metric},
        a.presentation_path,
        a.next,
        gaps=[{"kind": k, "name": n, "reason": why} for k, n, why in a.gap],
        process=[{"run": r, "result": res} for r, res in a.process],
        decisions=[{"decision": d, "basis": b} for d, b in a.decision],
    )


def cli_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="step_result.py", description="手順のスクリプトの結果 JSON を 1 行出して終える")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit", help="値を 1 つずつ別の引数で受けて結果 JSON を出す")
    e.add_argument("--tool", required=True)
    e.add_argument("--status", required=True, choices=STATUSES)
    e.add_argument("--summary", required=True)
    e.add_argument("--code", type=int, default=None, help="終了コード（省けば status と gaps から決める）")
    e.add_argument("--item", nargs=4, action="append", default=[], metavar=("KIND", "NAME", "RESULT", "REASON"))
    e.add_argument("--gap", nargs=3, action="append", default=[], metavar=("KIND", "NAME", "REASON"))
    e.add_argument("--process", nargs=2, action="append", default=[], metavar=("RUN", "RESULT"))
    e.add_argument("--decision", nargs=2, action="append", default=[], metavar=("DECISION", "BASIS"))
    e.add_argument("--metric", nargs=2, action="append", default=[], metavar=("KEY", "VALUE"))
    e.add_argument("--next", default=None)
    e.add_argument("--presentation-path", default=None)
    e.add_argument("--out", default=None, help="同じ 1 行をこのファイルへも書く（worker の作業の結果ファイル）")
    p = sub.add_parser("pick", help="標準入力の最後の行の結果 JSON から JSON Pointer の値を取り出す（json_pick.py。#2909）")
    p.add_argument("--field", action="append", required=True, help="JSON Pointer（結果 JSON を根にする。繰り返し）")
    p.add_argument("--raw", action="store_true", help="--field 1 つの値だけを 1 行で出す（文字列は引用符なし）")
    return ap


def _write_out(path: str, obj: dict, code: int | None) -> None:
    """形を確かめてから 1 行をファイルへ書く。誤っていれば書かない（emit が 2 で終える）。"""
    if code is None and obj.get("status") in STATUSES:
        code = EXIT_INCOMPLETE if obj["status"] == "stopped" and obj.get("gaps") else default_code(obj["status"])
    if validate_result(obj, code):
        return
    jsonio.write_atomic(path, obj, indent=None)


def main(argv=None) -> "NoReturn":  # noqa: F821
    a = cli_parser().parse_args(argv)  # 引数の誤りは argparse が 2 で終える
    if a.cmd == "pick":
        import json_pick

        json_pick.cli_pick(a)
    obj = _cli_result(a)
    if a.out:
        _write_out(a.out, obj, a.code)
    emit(obj, a.code)


if __name__ == "__main__":
    main()
