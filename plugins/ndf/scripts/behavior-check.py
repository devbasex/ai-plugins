#!/usr/bin/env python3
"""behavior-check.py: 振る舞いを決める文書を変えた Pull Request の、振る舞いの確認の対象・記録・確認（#2925 の決定 6）。

    behavior-check.py targets --pr N [--root DIR]
    behavior-check.py post --pr N --file <結果の JSON> [--root DIR]
    behavior-check.py verify --pr N [--root DIR]

- `targets`: PR の差分（消したファイルを除く）を `lib/diff_kinds.py` で分け、振る舞いを決める文書を Skill
  （`.../skills/<名前>`）かエージェント定義（ファイル）の単位にまとめて出す。最後の行 `record` に PR の番号・head・
  記録を書く `post` のコマンド・worker の手順（`procedure`）を載せる。0 = 対象あり / 3 = 対象なし（飛ばす） / 2 = 差分か head を取れない
- `post`: worker が書いた結果の JSON（`{"head": "<sha>", "checks": [{"target", "prompt", "command", "observed",
  "verdict"}]}`。`verdict` は 通った / 落ちた / 確かめていない）を、印 `<!-- ndf:behavior-check head=<sha> -->` 付きの
  PR のコメント 1 件にする。0 / 2 = 結果の形が違う・投稿できない
- `verify`: PR の今の head の印を持つ最後の記録が、今の対象をすべて「通った」で覆うか。0 = 覆う / 1 = 足りない・
  落ちた・確かめていない・head の記録が無い / 2 = 読めない。確かめた後に文書を変えたら確かめ直しになる（I6）

出力は `lib/step_result.py` の形の 1 行の JSON。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("pathmatch", "md")
import diff_kinds  # noqa: E402
import gh_call  # noqa: E402
import gh_rest  # noqa: E402
import md  # noqa: E402
import run_core  # noqa: E402
import step_result  # noqa: E402
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION, result  # noqa: E402

TOOL = "behavior-check"
MARK = "<!-- ndf:behavior-check head={head} -->"
MARK_RE = re.compile(r"<!-- ndf:behavior-check head=([0-9a-f]{7,40}) -->")
PASSED = "通った"
VERDICTS = (PASSED, "落ちた", "確かめていない")
CHECK_KEYS = ("target", "prompt", "command", "observed", "verdict")
MAX_TARGETS = 6  # 1 回の確認で起動する対象の上限。超えた分は「確かめていない」として人へ戻す
# worker の手順（`targets` の結果の record.procedure。検査のプランの behavior のステップが読む）
PROCEDURE = (
    "1. 対象ごとに、差分（`git diff origin/<起点>...HEAD -- <対象>`）で変わった指示を 1 つ選び、その指示が効く依頼文を作る",
    "2. 作業ディレクトリへ worktree の一時コピーを作り、その中で 1 対象 1 回だけ起動する: `claude -p --plugin-dir <対象の plugin_root> "
    "--output-format stream-json --no-session-persistence --max-budget-usd 1 '<依頼文>'`",
    "3. 出力のツールの呼び出しと応答から、変わった指示どおりに動いたかを判定する。動いたら「通った」、動かなければ「落ちた」とし、"
    "見たことを observed に書く",
    f"4. 確かめるのは {MAX_TARGETS} 件まで。超えた分は「確かめていない」とする",
    "5. 結果を作業ディレクトリの JSON（head と checks。checks の各行は target・prompt・command・observed・verdict）に書き、"
    "post のコマンドで PR へ記録する。文書は直さない（落ちた・確かめていないものは人が判断する）",
)


class Unreadable(RuntimeError):
    """GitHub か結果のファイルを読めない。"""


def _pr_files(pr: int, root: Path) -> list[str]:
    r = gh_rest.pr_files(pr, cwd=str(root))
    if r.returncode != 0:
        raise Unreadable(f"PR #{pr} の差分のファイルを取れない: {(r.stderr or '').strip()[:300]}")
    return [f["path"] for f in json.loads(r.stdout) if f.get("status") != "removed"]


def unit_of(path: str) -> tuple[str, str]:
    """（対象, プラグインの根）。Skill は `<根>/skills/<名前>`、エージェント定義と他はファイルそのもの。"""
    parts = PurePosixPath(path).parts
    if "skills" in parts[:-1]:
        i = len(parts) - 1 - parts[::-1].index("skills")
        if i + 1 < len(parts) - 1:
            return "/".join(parts[: i + 2]), "/".join(parts[:i]) or "."
    if "agents" in parts[:-1]:
        i = len(parts) - 1 - parts[::-1].index("agents")
        return path, "/".join(parts[:i]) or "."
    return path, str(PurePosixPath(path).parent)


def behavior_targets(files: list[str], root: Path) -> list[dict]:
    """振る舞いを決める文書を対象の単位にまとめる（順序は差分の順）。"""
    out: dict[str, dict] = {}
    for f in diff_kinds.classify_root(root, files).behavior:
        target, plugin = unit_of(f)
        out.setdefault(target, {"target": target, "plugin_root": plugin, "files": []})["files"].append(f)
    return list(out.values())


def _pr_head_sha(pr: int, root: Path) -> str:
    r = gh_rest.view_json("pr", pr, "headRefOid", cwd=str(root))
    try:
        sha = json.loads(r.stdout).get("headRefOid") if r.returncode == 0 else None
    except ValueError:
        sha = None
    if not sha:
        raise Unreadable(f"PR #{pr} の head を読めない: {(r.stderr or '').strip()[:300]}")
    return sha


def _comments(pr: int, root: Path) -> list[str]:
    r = gh_call.gh(["api", "--paginate", f"repos/{{owner}}/{{repo}}/issues/{pr}/comments?per_page=100"], cwd=str(root))
    if r.returncode != 0:
        raise Unreadable(f"PR #{pr} のコメントを読めない: {(r.stderr or '').strip()[:300]}")
    try:
        return [str(c.get("body") or "") for c in gh_rest._decode_concatenated_arrays(r.stdout)]
    except ValueError as e:
        raise Unreadable(f"PR #{pr} のコメントの応答を読めない: {e}") from e


def parse_behavior_record(data) -> dict:
    """結果の JSON の形を確かめる。違えば ValueError（文は 1 行）。"""
    if not isinstance(data, dict) or not isinstance(data.get("head"), str) or not re.fullmatch(r"[0-9a-f]{7,40}", data["head"]):
        raise ValueError("head に 7〜40 桁の 16 進の SHA を書く")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks に 1 件以上を書く")
    for i, c in enumerate(checks):
        if not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in CHECK_KEYS):
            raise ValueError(f"checks[{i}] に {'・'.join(CHECK_KEYS)} を文字列で書く")
        if c["verdict"] not in VERDICTS:
            raise ValueError(f"checks[{i}].verdict は {' / '.join(VERDICTS)} のどれか")
    return data


def render_behavior_comment(record: dict) -> str:
    """PR のコメントの本文（印・表・結果の JSON）。"""
    cell = lambda s: s.replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = [MARK.format(head=record["head"]), f"## 振る舞いの確認（{record['head'][:12]}）", ""]
    lines += ["| 対象 | 判定 | 観察 | コマンド |", "| --- | --- | --- | --- |"]
    lines += [f"| `{cell(c['target'])}` | {c['verdict']} | {cell(c['observed'])} | `{cell(c['command'])}` |" for c in record["checks"]]
    lines += [
        "",
        "<details><summary>結果の JSON</summary>",
        "",
        "```json",
        json.dumps(record, ensure_ascii=False, indent=1),
        "```",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def latest_record(bodies: list[str], head: str) -> dict | None:
    """head の印を持つ最後のコメントの記録。読めない記録は無いものとして扱う。"""
    for body in reversed(bodies):
        m = MARK_RE.search(body)
        fences = [t.content for t in md.md_tokens(body) if t.type == "fence" and t.info.strip() == "json"] if m else []
        if not m or m.group(1) != head or not fences:
            continue
        try:
            return parse_behavior_record(json.loads(fences[0]))
        except ValueError:
            continue
    return None


def shortfalls(record: dict | None, targets: list[dict]) -> list[str]:
    """記録が対象を「通った」で覆わない理由の並び（空なら覆う）。"""
    if record is None:
        return ["今の head の振る舞いの確認の記録が無い"]
    verdicts = {c["target"]: c["verdict"] for c in record["checks"]}
    out = []
    for t in targets:
        v = verdicts.get(t["target"])
        if v != PASSED:
            out.append(f"{t['target']}: {v or '記録に無い'}")
    return out


def cmd_targets(a, root: Path) -> int:
    targets = behavior_targets(_pr_files(a.pr, root), root)
    if not targets:
        step_result.emit(result(TOOL, "stopped", "振る舞いを決める文書の変更が無い（振る舞いの確認を飛ばす）"), EXIT_PRECONDITION)
    head = _pr_head_sha(a.pr, root)
    post = f"python3 {Path(__file__).resolve()} post --pr {a.pr} --file <結果の JSON>"
    summary = f"PR #{a.pr}（head {head[:12]}）の振る舞いの確認の対象 {len(targets)} 件: " + "・".join(t["target"] for t in targets)
    record = {"record": {"pr": a.pr, "head": head, "post": post, "procedure": list(PROCEDURE)}}
    step_result.emit(result(TOOL, "ok", summary, [*targets, record], {"targets": len(targets)}))


def cmd_post(a, root: Path) -> int:
    try:
        record = parse_behavior_record(json.loads(Path(a.file).read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        step_result.emit(result(TOOL, "stopped", f"結果の JSON を読めない: {e}"), EXIT_UNREADABLE)
    posted = gh_call.gh(["pr", "comment", str(a.pr), "--body-file", "-"], render_behavior_comment(record), cwd=str(root))
    if posted.returncode != 0:
        step_result.emit(result(TOOL, "stopped", f"PR #{a.pr} へ記録を書けない: {posted.stderr.strip()[:300]}"), EXIT_UNREADABLE)
    failed = sum(c["verdict"] != PASSED for c in record["checks"])
    summary = f"振る舞いの確認の記録を PR #{a.pr} へ書いた（{len(record['checks'])} 件・通らなかった {failed} 件）"
    step_result.emit(result(TOOL, "ok", summary, [], {"checks": len(record["checks"]), "not_passed": failed}))


def cmd_verify(a, root: Path) -> int:
    targets = behavior_targets(_pr_files(a.pr, root), root)
    head = _pr_head_sha(a.pr, root)
    missing = shortfalls(latest_record(_comments(a.pr, root), head), targets) if targets else []
    if missing:
        summary = f"振る舞いの確認が {head[:12]} の対象を覆わない: " + "、".join(missing)
        step_result.emit(result(TOOL, "stopped", summary, [{"missing": missing}], {"missing": len(missing)}), EXIT_VIOLATION)
    step_result.emit(
        result(TOOL, "ok", f"振る舞いの確認の記録が {head[:12]} の対象 {len(targets)} 件を覆う", [], {"targets": len(targets)})
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    targets = sub.add_parser("targets", help="振る舞いの確認の対象を出す")
    targets.add_argument("--pr", type=int, required=True)
    targets.add_argument("--root", default=".")
    post = sub.add_parser("post", help="worker の結果を PR のコメントに記録する")
    post.add_argument("--pr", type=int, required=True)
    post.add_argument("--file", required=True, help="worker が書いた結果の JSON")
    post.add_argument("--root", default=".")
    verify = sub.add_parser("verify", help="今の head の記録が対象を覆うかを確かめる")
    verify.add_argument("--pr", type=int, required=True)
    verify.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    try:
        return {"targets": cmd_targets, "post": cmd_post, "verify": cmd_verify}[a.cmd](a, root)
    except Unreadable as e:
        step_result.emit(result(TOOL, "stopped", str(e)), EXIT_UNREADABLE)


if __name__ == "__main__":
    run_core.main("scripts/behavior-check.py", main)
