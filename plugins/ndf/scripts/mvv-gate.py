#!/usr/bin/env python3
"""mvv-gate.py: 関門の材料が MVV に従うかを判定し、従えば関門を省いた記録を残す（#1078）。

    python3 mvv-gate.py check --mission <ミッションの状態> --gate design|release
                              [--material <ファイル>...] [--pr N...] [--mode M]
                              [--log <jsonl>] [--repo OWNER/REPO] [--root DIR] [--note <ファイル>]

`pace: fast` の関門 1（設計の承認）と関門 2（本番への配布の承認）を、利用者が承認した MVV への事前の
承認として扱うための判定である。判定の前に、次を機械で見る。1 つでも外れれば LLM を呼ばずに関門へ戻す。

1. ミッションの状態に MVV（`mvv.path`・`mvv.sha256`）と、利用者の承認の記録（関門 `MVV` の `sha256`）がある
2. 今の MVV のファイルのハッシュ・状態の `mvv.sha256`・承認の記録の `sha256` がすべて一致する
3. `--mode` が `operation` でも `documentation` でもない
4. `--pr` の変更したファイルが、進め方の宣言（`.ndf/pace.json`）の `boundary_paths` に当たらない

通れば、材料（提示物のファイル・PR の題名と本文と変更したファイル。`--gate design` では PR が変更した `issues/` の設計文書の
PR の先頭のコミットの中身も足す）と MVV を最小構成の claude -p に渡し、
「従う / 従わない / 判定できない」と理由と越えない線を JSON で返させる。

- 従う（越えない線なし）: `mission-state.py gate` で関門の記録（`by: mvv`・判定・理由・ログ）を書き、
  status ok（終了コード 0）。記録を書けなければ関門へ戻す
- それ以外（従わない・判定できない・越えない線・判定を読めない・材料を取れない）: status gate（終了コード 10）。
  利用者の承認を求める

判定は毎回 --log（既定 ~/.local/state/ndf/mvv-gate.jsonl）へ 1 行で残す。--note を渡すと、Pull Request の
コメントに使う判定の記録（判定・理由・ログ）を Markdown で書く。claude は NDF_MVV_CLAUDE で差し替えられる。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from supervise_lib.paths import sha256_of  # noqa: E402  supervise_lib が lib/ を sys.path へ足す
from step_result import EXIT_GATE, emit, result  # noqa: E402
import clock  # noqa: E402
import gh_call  # noqa: E402
import gh_rest  # noqa: E402
from pace import EXCLUDED_MODES, PaceError, matches, read_pace  # noqa: E402

TOOL = "mvv-gate"
GATES = {"design": "関門 1（設計の承認）", "release": "関門 2（本番への配布の承認）"}
GATE_NAMES = {"design": "関門 1", "release": "関門 2"}  # mission-state.py の関門の記録の名前
MVV_GATE = "MVV"  # 利用者が MVV を承認した記録の名前
VERDICTS = ("follow", "not_follow", "unknown")
MAX_MATERIAL = 60_000  # 材料 1 件の上限の文字数（超えた分は切る）

SYSTEM = """あなたは開発の関門の判定者である。利用者はミッションの MVV（Mission / Vision / Value）を承認済みで、
MVV に従う変更なら関門での個別の承認を省いてよいと決めている。材料を読み、この変更が MVV に従うかを判定する。

- Mission と Vision に沿い、Value のどれにも反しないなら follow
- どれかに反するなら not_follow。材料から読み取れず決められないなら unknown（迷ったら unknown）
- 配布の前に取れない実測（配布した後の効果）は判定の対象にしない。配布の後の測定へ回るものとして扱い、
  それが無いことだけを理由に unknown にしない
- Value の「越えない線」（秘密・認証認可・利用者のデータ・戻せない操作・他のリポジトリへの公開）に当たる変更を
  含むなら、boundary にその中身を書く（boundary が空でなければ関門は省かれない）

出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|not_follow|unknown", "reasons": ["MVV のどの項目に照らしてそう判断したか（1〜5 件）"], "boundary": []}"""


class Back(Exception):
    """関門へ戻す（利用者の承認を求める）理由。"""


def gh_or_back(args: list[str], repo: str | None, cwd: Path) -> str:
    """`gh` を 1 回呼び（`lib/gh_call.py`）、標準出力を返す。失敗は Back。"""
    r = gh_call.gh([*args] + (["--repo", repo] if repo else []), cwd=str(cwd))
    if r.returncode == 127:
        raise Back("gh が無い")
    if r.returncode != 0:
        raise Back(f"gh {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def pr_facts(n: int, repo: str | None, cwd: Path) -> dict:
    """PR の題名・本文・変更したファイル・先頭のコミット（GraphQL が上限なら REST で読む）。取れなければ Back。"""
    r = gh_rest.view_json("pr", n, "title,body,files,headRefOid", repo, cwd=str(cwd))
    if r.returncode != 0:
        raise Back("gh が無い" if r.returncode == 127 else f"gh pr view {n}: {r.stderr.strip()[:300]}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Back(f"PR #{n} の出力を読めない")


def pr_material(n: int, info: dict) -> str:
    files = "\n".join(f"- {f['path']} (+{f.get('additions', 0)} -{f.get('deletions', 0)})"
                      for f in info.get("files", []))
    return f"## Pull Request #{n}: {info.get('title', '')}\n\n{info.get('body') or ''}\n\n### 変更したファイル\n{files}"


def design_docs(info: dict) -> list[str]:
    """PR が足したか直した `issues/` の設計文書のパス（消しただけのものは読めないので除く）。"""
    return [f["path"] for f in info.get("files", [])
            if f.get("path", "").startswith("issues/") and f["path"].endswith(".md") and "design" in Path(f["path"]).name
            and not (f.get("additions", 0) == 0 and f.get("deletions", 0) > 0)]


def design_material(n: int, path: str, info: dict, repo: str | None, cwd: Path) -> str:
    """PR の先頭のコミットの設計文書の中身を材料の 1 件にする。取れなければ Back。"""
    ref = info.get("headRefOid") or ""
    if not ref:
        raise Back(f"PR #{n} の先頭のコミットが分からず、設計文書 {path} を読めない")
    endpoint = f"repos/{repo or '{owner}/{repo}'}/contents/{urllib.parse.quote(path)}?ref={ref}"
    try:
        text = gh_or_back(["api", "-H", "Accept: application/vnd.github.raw", endpoint], None, cwd)
    except Back as e:
        raise Back(f"PR #{n} の設計文書 {path} を読めない: {e}")
    return f"## 設計文書 {path}（PR #{n}）\n\n{text}"


def build_prompt(mvv: str, gate: str, materials: list[str]) -> str:
    body = "\n\n".join(m[:MAX_MATERIAL] for m in materials)
    return f"# MVV\n\n{mvv}\n\n# 関門\n\n{GATES[gate]}\n\n# 材料\n\n{body}\n"


def ask(prompt: str) -> tuple[dict | None, str, dict]:
    """(判定, 生の文, 使用量) を返す。読めなければ判定は None。

    呼び出しごとに使用量の帳簿へ 1 行を足す（#1142 の不足 f。source は mvv-gate、プランの外の呼び出し）。
    """
    import usage_ledger  # lib/ は起動の時に sys.path へ足してある
    base = shlex.split(os.environ.get("NDF_MVV_CLAUDE", "claude"))
    cmd = base + ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "",
                  "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", SYSTEM, "--tools", ""]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)[-500:], {}
    try:
        outer = json.loads(p.stdout)
    except json.JSONDecodeError:
        outer = None
    if not isinstance(outer, dict):
        usage_ledger.append_safely(os.getcwd(), usage_ledger.UsageRecord(source="mvv-gate", kind="mvv"))
        return None, (p.stdout + p.stderr)[-500:], {}
    text = outer.get("result") or ""
    usage = {"cost_usd": outer.get("total_cost_usd"), "seconds": (outer.get("duration_ms") or 0) / 1000}
    usage_ledger.append_safely(os.getcwd(), usage_ledger.UsageRecord.from_claude(
        outer, source="mvv-gate", kind="mvv",
        seconds=usage["seconds"] if outer.get("duration_ms") is not None else None))
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = json.loads(text[start:end + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        verdict = None
    if not isinstance(verdict, dict) or verdict.get("verdict") not in VERDICTS:
        return None, text[-500:], usage
    return verdict, text, usage


def approved_mvv(state: dict) -> Path:
    """MVV が承認済みで、承認の後に変わっていないことを確かめ、MVV のファイルを返す。外れれば Back。"""
    mvv = state.get("mvv") or {}
    if not mvv.get("path") or not mvv.get("sha256"):
        raise Back("ミッションの状態に MVV が無い（mission-state.py init --pace fast で写す）")
    path = Path(mvv["path"])
    if not path.is_file():
        raise Back(f"MVV のファイルが無い: {path}")
    approval = next((g for g in state.get("gates") or [] if g.get("name") == MVV_GATE), None)
    if not approval or not approval.get("sha256"):
        raise Back("MVV の承認の記録が無い（利用者の承認の後に mission-state.py gate <状態> MVV を打つ）")
    now = sha256_of(path)
    if not (now == mvv["sha256"] == approval["sha256"]):
        raise Back("MVV のハッシュが承認の記録と一致しない（承認の後に MVV が変わった）")
    return path


def boundary_hits(root: Path, infos: dict[int, dict]) -> list[str]:
    try:
        patterns = read_pace(root)["boundary_paths"]
    except PaceError as e:
        raise Back(str(e))
    return [f"#{n} {f['path']}" for n, info in infos.items() for f in info.get("files", [])
            if matches(f.get("path", ""), patterns)]


def write_log(path: str, record: dict) -> None:
    log = Path(path).expanduser()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_gate(a, record: dict) -> str | None:
    """関門の記録をミッションの状態へ書く。書けなければ理由を返す。"""
    what = "MVV 判定: " + " / ".join([*(f"#{n}" for n in a.pr), *a.material]) if (a.pr or a.material) \
        else "MVV 判定"
    cmd = [sys.executable, str(HERE / "mission-state.py"), "gate", a.mission, GATE_NAMES[a.gate], "--what", what,
           "--by", "mvv", "--verdict", record["verdict"], "--reasons", json.dumps(record["reasons"], ensure_ascii=False),
           "--log", str(Path(a.log).expanduser())]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return None if p.returncode == 0 else (p.stdout + p.stderr).strip()[-300:] or f"終了コード {p.returncode}"


def write_note(path: str, a, record: dict) -> None:
    reasons = "\n".join(f"- {r}" for r in record["reasons"]) or "- （無し）"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        f"## MVV 判定（{GATES[a.gate]}）\n\n"
        f"- 判定: {record['verdict']}（関門を省いた。利用者が承認した MVV を事前の許可として扱う）\n"
        f"- 時刻: {record['at']}\n- ログ: `{Path(a.log).expanduser()}` の {record['at']} の行\n\n"
        f"### 理由\n\n{reasons}\n", encoding="utf-8")


def cmd_check(a) -> tuple[dict, int | None]:
    root = Path(a.root or ".").resolve()
    record = {"at": clock.now_iso("utc"), "gate": a.gate, "mission": a.mission, "material": list(a.material), "pr": a.pr,
              "mode": a.mode or ""}

    def back(why: str, verdict: str, extra: dict | None = None, usage: dict | None = None):
        record.update(verdict=verdict, reasons=[why] if verdict == "machine" else record.get("reasons", []),
                      passed=False, **(usage or {}))
        write_log(a.log, record)
        return result(TOOL, "gate", f"{GATES[a.gate]}: {why}。利用者の承認を求める", [{**record, **(extra or {})}],
                      usage or {}, next="利用者の承認を求める"), EXIT_GATE

    try:
        state = json.loads(Path(a.mission).read_text(encoding="utf-8"))
        mvv_path = approved_mvv(state)
        if a.mode in EXCLUDED_MODES:
            raise Back(f"モード {a.mode} は関門を省かない")
        infos = {n: pr_facts(n, a.repo, root) for n in a.pr}
        hits = boundary_hits(root, infos)
        if hits:
            raise Back("越えない線のパスに当たる: " + " / ".join(hits))
        materials = []
        for m in a.material:
            if not Path(m).is_file():
                raise Back(f"材料のファイルが無い: {m}")
            materials.append(f"## {m}\n\n{Path(m).read_text(encoding='utf-8')}")
        materials += [pr_material(n, info) for n, info in infos.items()]
        if a.gate == "design":
            for n, info in infos.items():
                for path in design_docs(info):
                    materials.append(design_material(n, path, info, a.repo, root))
                    record["material"].append(f"#{n} {path}")
        if not materials:
            raise Back("材料が無い（--material か --pr を渡す）")
    except (OSError, ValueError) as e:
        return back(f"ミッションの状態を読めない: {e}", "machine")
    except Back as e:
        return back(str(e), "machine")

    verdict, raw, usage = ask(build_prompt(mvv_path.read_text(encoding="utf-8"), a.gate, materials))
    if verdict is None:
        return back("判定を読めない", "unreadable", {"raw": raw}, usage)
    boundary = verdict.get("boundary") or []
    record.update(reasons=verdict.get("reasons", []), boundary=boundary)
    if verdict["verdict"] != "follow" or boundary:
        why = "越えない線に当たる: " + " / ".join(map(str, boundary)) if boundary else verdict["verdict"]
        return back(why, verdict["verdict"], usage=usage)
    record.update(verdict="follow", passed=True, **usage)
    write_log(a.log, record)
    err = record_gate(a, record)
    if err:
        record["passed"] = False
        return result(TOOL, "gate", f"{GATES[a.gate]}: MVV に従うが、関門の記録を書けない（{err}）。利用者の承認を求める",
                      [record], usage, next="利用者の承認を求める"), EXIT_GATE
    if a.note:
        write_note(a.note, a, record)
    return result(TOOL, "ok", f"{GATES[a.gate]}: MVV に従う。関門を省いて進めてよい", [record], usage), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--mission", required=True, help="ミッションの状態（mission-state.py のファイル）")
    c.add_argument("--gate", required=True, choices=sorted(GATES))
    c.add_argument("--material", nargs="+", default=[])
    c.add_argument("--pr", nargs="+", type=int, default=[])
    c.add_argument("--mode", default="")
    c.add_argument("--log", default="~/.local/state/ndf/mvv-gate.jsonl")
    c.add_argument("--repo")
    c.add_argument("--root", help="進め方の宣言を読むリポジトリの根（既定はカレント）")
    c.add_argument("--note", help="従うときに、判定の記録を Markdown で書く所（Pull Request のコメントに使う）")
    a = ap.parse_args()
    out, code = cmd_check(a)
    emit(out, code)


if __name__ == "__main__":
    main()
