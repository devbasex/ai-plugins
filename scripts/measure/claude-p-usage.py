#!/usr/bin/env python3
"""マイルストーン 26（ndf 10.17.0〜10.17.26）の claude -p（作業ツリーで動いた CLI）の消費を版ごとに集計する（#1142 の進め方 0）。

読むだけ。本文・プロンプトは出力に写さない（数値・ブランチ名・PR 番号・計画の名前だけ）。

    python3 scripts/measure/claude-p-usage.py [--since 2026-09-22T00:00:00Z] [--until 2026-09-26T00:00:00Z]
        [--repo <リポジトリ>] [--sv-root /tmp/ndf-sv] [--projects ~/.claude/projects]
        [--out ~/.local/state/ndf/measure-1142/claude-p-usage]

置き場所は引数で受ける（既定は E1 を測ったときの値）。`--repo` はタグ・CHANGELOG・`gh` を読むリポジトリと、
会話の置き場の名前（`<repo のパスの / と . を - にしたもの>--worktrees-`）を決める。`--sv-root` は計画の状態の置き場、
`--projects` は Claude Code の会話の置き場。

集める 3 つの出所:
  A. 記録の残る claude -p: ~/.claude/projects/-work-ai-plugins--worktrees-*/<会話>.jsonl
     （supervise.py の work の full のステップ。cwd = <repo>/.worktrees/<ブランチ>）。
     呼び出しごとの usage が取れるので token-usage.py と同じ Usage / scan_file で数える
  B. 記録の残らない claude -p: supervise.py の最小構成の worker と judge は --no-session-persistence で
     jsonl を残さない。使用量は計画ごとの「## フェーズの報告」（report.md / 会話の tool_result）の
     「LLM の使用量」の行にだけ残る。記録（state-dir）で重複を除く。呼び出し回数・P・モデル・
     書き込みの 5 分/1 時間の別は無い
  C. レビュー・改修の席: <projects>/-tmp-ndf-worktrees-*（cross-review / cross-refactoring の claude の席）。
     token-usage.py が外部 CLI として扱うもの。参考として別に数える
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("token_usage", Path(__file__).resolve().parents[1] / "token-usage.py")
tu = importlib.util.module_from_spec(_spec)
sys.modules["token_usage"] = tu
_spec.loader.exec_module(tu)  # 係数（WEIGHTS / READ_RATES）と数え方は token-usage.py から取る

# 置き場所は main が引数から決める（configure）。既定は E1 を測ったときの値
REPO = Path("/work/ai-plugins")
SV_ROOT = Path("/tmp/ndf-sv")
PROJ = Path.home() / ".claude/projects"
WT_PREFIX = "-work-ai-plugins--worktrees-"
SEAT_PREFIX = "-tmp-ndf-worktrees-devbasex--ai-plugins-"

USE_RE = re.compile(r"LLM の使用量: 入力 (\d+) / cache read (\d+) / cache write (\d+) / 出力 (\d+) / \$([\d.]+)")
REC_RE = re.compile(r"- 記録: (\S+)")
PR_RE = re.compile(r"- Pull Request: ([^\n]*)")
ISSUE_RE = re.compile(r"- 課題: ([^\n]*)")
PHASE_RE = re.compile(r"- フェーズ: ([^\n]*)")
WORKER_RE = re.compile(r"使った worker: 修正 (\d+)（claude -p）/ 判断 (\d+)")
ROW_RE = re.compile(r"^\| (\S+) \| (\d+) \| ([\d.]*) \| (\d+) \| (\d+) \| (\d+) \| \$([\d.]+) \|$", re.M)
PLANNAME_RE = re.compile(re.escape(str(SV_ROOT)) + r"/(r\d+)/(?:plans/)?([\w.-]+?)-state")


def project_dir_name(path: Path) -> str:
    """Claude Code が会話の置き場の名前にする形（/ と . を - にする）。"""
    return re.sub(r"[/.]", "-", str(path))


def configure(repo: Path, sv_root: Path, projects: Path, seat_prefix: str) -> None:
    global REPO, SV_ROOT, PROJ, WT_PREFIX, SEAT_PREFIX, PLANNAME_RE
    REPO, SV_ROOT, PROJ, SEAT_PREFIX = repo, sv_root, projects, seat_prefix
    WT_PREFIX = project_dir_name(repo / ".worktrees") + "-"
    PLANNAME_RE = re.compile(re.escape(str(sv_root)) + r"/(r\d+)/(?:plans/)?([\w.-]+?)-state")


def iso(t: float | None) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else "-"


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------- 版と PR ----------

def load_versions() -> list[tuple[str, float]]:
    out = subprocess.run(["git", "tag", "-l", "ndf--v10.17.*", "--format=%(refname:short) %(creatordate:unix)"],
                         cwd=REPO, capture_output=True, text=True).stdout.split("\n")
    vs = [(l.split()[0].removeprefix("ndf--v"), float(l.split()[1])) for l in out if l.strip()]
    vs = [v for v in vs if "-" not in v[0]]
    return sorted(vs, key=lambda v: v[1])


def changelog_prs() -> dict[int, str]:
    sec, m = None, {}
    for line in (REPO / "CHANGELOG.md").read_text().split("\n"):
        h = re.match(r"## \[ndf (10\.17\.\d+)\]", line)
        if h:
            sec = h.group(1)
            continue
        if line.startswith("## ["):
            sec = None
        if sec:
            for n in re.findall(r"#(\d+)", line):
                m.setdefault(int(n), sec)
    return m


def load_prs(cache: Path) -> list[dict]:
    if not cache.exists():
        data = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "400", "--search", "created:>=2026-09-21",
                               "--json", "number,title,headRefName,mergedAt,createdAt,state"],
                              cwd=REPO, capture_output=True, text=True, check=True).stdout
        cache.write_text(data)
    return json.loads(cache.read_text())


class Mapper:
    def __init__(self, prs: list[dict]):
        self.versions = load_versions()
        self.cl = changelog_prs()
        self.prs = {p["number"]: p for p in prs}
        self.by_branch = defaultdict(list)
        self.by_issue = defaultdict(list)
        for p in prs:
            self.by_branch[p["headRefName"]].append(p)
            for n in re.findall(r"#(\d+)", p["title"]):
                self.by_issue[int(n)].append(p)

    def version_at(self, t: float) -> str | None:
        """時刻 t の後に最初に打たれた正式版のタグ（その作業が載る版）。"""
        for v, vt in self.versions:
            if t <= vt:
                return v
        return None

    def version_of_pr(self, n: int) -> tuple[str | None, str]:
        if n in self.cl:
            return self.cl[n], "changelog"
        p = self.prs.get(n)
        if p and p.get("mergedAt"):
            return self.version_at(ts(p["mergedAt"])), "merged_at"
        return None, "pr_unmerged"


# ---------- A / C: 記録の残る会話 ----------

def first_user_head(path: Path) -> str:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "user" and not d.get("isMeta"):
            return tu._text((d.get("message") or {}).get("content"))[:200]
    return ""


def kind_of(branch: str, head: str) -> str:
    h = head.lower()
    if "cross-review" in h:
        return "review"
    if "cross-refactoring" in h:
        return "refactoring"
    for pre, k in (("design/", "design"), ("release/", "release"), ("check", "check"), ("feat", "impl"),
                   ("fix/", "impl"), ("docs/", "impl")):
        if branch.startswith(pre):
            return k
    return "不明"


def conv_row(path: Path, until: float | None) -> dict | None:
    s = tu.scan_file(path, until=until)
    subs = [tu.scan_file(p, until=until) for p in sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))]
    u = tu.Usage()
    u.add(s.usage)
    for x in subs:
        u.add(x.usage)
    if not s.times or not u.calls:
        return None
    return {"start": min(s.times), "end": max(s.times), "active_sec": tu.active_seconds(s.times, tu.IDLE_CAP),
            "model": tu.most_common(s.models), "cwd": s.cwd, "calls": u.calls, "calls_main": s.usage.calls,
            "subagents": len(subs), "input": u.inp, "cache_read": u.read, "cache_write_5m": u.w5,
            "cache_write_1h": u.w1h, "output": u.out, "P": s.usage.p, "cost": round(u.cost, 1)}


def read_persisted(since: float, until: float | None, mp: Mapper) -> tuple[list[dict], list[dict]]:
    wt, seats = [], []
    for d in sorted(PROJ.iterdir()):
        if "worktrees" not in d.name:
            continue
        for f in sorted(d.glob("*.jsonl")):
            r = conv_row(f, until)
            if not r or r["start"] < since:
                continue
            if d.name.startswith(WT_PREFIX):
                branch = r["cwd"].replace(str(REPO / ".worktrees") + "/", "") if r["cwd"] else d.name[len(WT_PREFIX):]
                r.update(source="A", branch=branch, kind=kind_of(branch, first_user_head(f)))
                cands = mp.by_branch.get(branch, [])
                r["prs"] = [p["number"] for p in cands]
                wt.append(r)
            elif d.name.startswith(SEAT_PREFIX):
                key = d.name[len(SEAT_PREFIX):]
                r.update(source="C", branch=key, kind="review" if key.startswith("pr") else "refactoring",
                         prs=[int(key[2:])] if key.startswith("pr") and key[2:].isdigit() else [])
                seats.append(r)
            else:  # 他のリポジトリの席は数えない（名前も残さない）
                continue
    return wt, seats


# ---------- B: 記録の残らない claude -p（フェーズの報告） ----------

def parse_report(text: str) -> list[dict]:
    out = []
    for block in text.split("## フェーズの報告")[1:]:
        m = USE_RE.search(block)
        rec = REC_RE.search(block)
        if not m or not rec:
            continue
        w = WORKER_RE.search(block)
        rows = ROW_RE.findall(block)
        pr = PR_RE.search(block)
        iss = ISSUE_RE.search(block)
        ph = PHASE_RE.search(block)
        out.append({"record": rec.group(1).rstrip("`）)"), "input": int(m.group(1)), "cache_read": int(m.group(2)),
                    "cache_write": int(m.group(3)), "output": int(m.group(4)), "usd": float(m.group(5)),
                    "work": int(w.group(1)) if w else None, "judge": int(w.group(2)) if w else None,
                    "turns": sum(int(r[1]) for r in rows), "llm_sec": sum(float(r[2] or 0) for r in rows),
                    "pr_field": re.findall(r"(?:pull/|#|^)(\d{3,5})\b", pr.group(1).strip()) if pr else [],
                    "issues": [int(x) for x in re.findall(r"#(\d+)", iss.group(1))] if iss else [],
                    "phase_len": len(ph.group(1)) if ph else 0})
    return out


def read_reports(since: float, until: float | None) -> list[dict]:
    found: dict[tuple, dict] = {}
    # 1) 残っている report.md（時刻はファイルの更新時刻）
    for p in SV_ROOT.glob("r*/plans/*-state/report.md"):
        t = p.stat().st_mtime
        for r in parse_report(p.read_text(errors="replace")):
            r["at"] = t
            found.setdefault((r["record"], r["input"], r["cache_read"], r["output"]), r)
    # 2) 会話の tool_result に出た報告（conductor / サブエージェントが読んだもの）
    for f in PROJ.glob("**/*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        if "LLM の使用量" not in raw:
            continue
        for line in raw.split("\n"):
            if "LLM の使用量" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "user":
                continue
            t = tu.parse_ts(d.get("timestamp"))
            if t is None or t < since or (until and t > until):
                continue
            content = (d.get("message") or {}).get("content")
            texts = [tu._text(c.get("content")) for c in content if isinstance(c, dict) and c.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            for tx in texts:
                for r in parse_report(tx):
                    k = (r["record"], r["input"], r["cache_read"], r["output"])
                    if k not in found or t < found[k]["at"]:
                        r["at"] = t
                        found[k] = r
    out = []
    for r in found.values():
        m = PLANNAME_RE.search(r["record"])
        r["plan"] = f"{m.group(1)}/{m.group(2)}" if m else "不明"
        name = m.group(2) if m else ""
        r["kind"] = next((k for k in ("impl", "design", "spec", "check", "review", "release") if k in name),
                         "impl" if re.fullmatch(r"plan-[\d-]+", name) else "不明")  # 初期の名前（plan-<課題>）は実装の計画
        if r["kind"] == "spec":
            r["kind"] = "design"
        if r["input"] + r["cache_read"] + r["cache_write"] + r["output"] == 0:
            continue  # LLM を使わなかった計画（run だけ）は数えない
        # 換算: モデルと 5 分/1 時間の別が無い。既定のモデル（claude-opus-5-5）の read 倍率と、書き込み 5 分（下限）
        r["cost_low"] = round(r["input"] + r["cache_read"] * tu.read_rate("claude-opus-5-5")
                              + r["cache_write"] * tu.WEIGHTS["w5"] + r["output"] * tu.WEIGHTS["out"], 1)
        r["cost_high"] = round(r["cost_low"] + r["cache_write"] * (tu.WEIGHTS["w1h"] - tu.WEIGHTS["w5"]), 1)
        out.append(r)
    # 同じ記録で数値が伸びた報告（途中と最後）は最後の 1 件だけ残す
    best: dict[str, dict] = {}
    for r in sorted(out, key=lambda r: r["cache_read"] + r["output"]):
        best[r["record"]] = r
    return sorted(best.values(), key=lambda r: r["at"])


# ---------- 寄せと集計 ----------

def assign(rows: list[dict], mp: Mapper, pr_key) -> Counter:
    unassigned = Counter()
    for r in rows:
        prs = pr_key(r)
        vers = [(n, *mp.version_of_pr(n)) for n in prs]
        good = [(n, v, how) for n, v, how in vers if v]
        if good:
            r["pr"], r["version"], r["by"] = good[0]
        else:
            v = mp.version_at(r.get("end") or r.get("at"))
            r["pr"] = prs[0] if prs else None
            r["version"], r["by"] = v, "time_only"
            unassigned["PR に寄せられない（" + ("PR 無し" if not prs else vers[0][2]) + "）→ 時刻で版だけ決めた"] += 1
            if not v:
                unassigned["版も決まらない（最後のタグより後）"] += 1
    return unassigned


def pr_of_report(mp: Mapper):
    def f(r):
        if r["pr_field"]:
            return [int(x) for x in r["pr_field"] if int(x) in mp.prs]
        prs = []
        for i in r["issues"]:
            prs += [p["number"] for p in mp.by_issue.get(i, []) if p.get("mergedAt")]
        return sorted(set(prs))[:1]
    return f


def table(mp: Mapper, a: list[dict], b: list[dict], c: list[dict]) -> list[dict]:
    rows = []
    for v, _ in mp.versions:
        av = [r for r in a if r["version"] == v]
        bv = [r for r in b if r["version"] == v]
        cv = [r for r in c if r["version"] == v]
        prs = {r["pr"] for r in av + bv if r.get("pr")}
        # full のステップの会話（A）の使用量は、その計画のフェーズの報告（B）にも足されている。
        # 同じ版で B の課題に A のブランチの課題が入っていれば、A を B に含まれるものとして合計から外す
        b_issues = {i for r in bv for i in r["issues"]}
        for r in av:
            m = re.search(r"issue-(\d+)", r["branch"])
            r["in_B"] = bool(m and int(m.group(1)) in b_issues)
        cost = sum(r["cost"] for r in av if not r["in_B"]) + sum(r["cost_low"] for r in bv)
        rows.append({
            "version": v, "A_convs": len(av), "B_plans": len(bv), "prs": len(prs),
            "cost_A": round(sum(r["cost"] for r in av)), "cost_B_low": round(sum(r["cost_low"] for r in bv)),
            "cost_B_high": round(sum(r["cost_high"] for r in bv)), "cost_total_low": round(cost),
            "A_in_B": sum(r["in_B"] for r in av),
            "cost_per_pr": round(cost / len(prs)) if prs else None,
            "A_P_per_conv": round(sum(r["P"] for r in av) / len(av)) if av else None,
            "A_calls_per_conv": round(sum(r["calls"] for r in av) / len(av), 1) if av else None,
            "A_min_per_conv": round(sum(r["active_sec"] for r in av) / len(av) / 60, 1) if av else None,
            "B_turns_per_plan": round(sum(r["turns"] for r in bv) / len(bv), 1) if bv else None,
            "B_llm_min_per_plan": round(sum(r["llm_sec"] for r in bv) / len(bv) / 60, 1) if bv else None,
            "C_seats": len(cv), "C_cost": round(sum(r["cost"] for r in cv)),
            "C_P_per_seat": round(sum(r["P"] for r in cv) / len(cv)) if cv else None,
            "C_calls_per_seat": round(sum(r["calls"] for r in cv) / len(cv), 1) if cv else None,
            "kinds_A": dict(Counter(r["kind"] for r in av)), "kinds_B": dict(Counter(r["kind"] for r in bv)),
        })
    return rows


def md(rows: list[dict], meta: dict) -> str:
    def f(x):
        return "-" if x is None else (f"{x:,}" if isinstance(x, int) else str(x))
    out = ["# マイルストーン 26 の claude -p の消費（#1142 の進め方 0）", "",
           f"生成: {meta['generated']} / 範囲: {meta['since']} 〜 {meta['until'] or '最後まで'}", "",
           "換算は token-usage.py の係数（input 1 / cache read はモデル別 / cache write 5 分 1.25・1 時間 2 / output 5）。"
           "A = 記録の残る claude -p（.worktrees の会話）、B = 記録の残らない supervise.py の claude -p（フェーズの報告の合計。"
           "換算の下限: opus-5-5 の read 倍率・書き込みを 5 分とみなす）、C = レビュー・改修の claude の席（/tmp/ndf-worktrees）。"
           "換算 A+B は B に含まれる A（full のステップの会話）を除いて足す。PR 数と PR あたりは A + B。所要は A の行の間隔（30 分で打ち切り）、B は報告の秒の合計。", "",
           "| 版 | A 会話（うち B に含む） | B 計画 | PR | 換算 A | 換算 B（下限〜上限） | 換算 A+B | PR あたり | A の P/会話 | A の呼び出し/会話 | A の所要（分） | B の往復/計画 | B の LLM 所要（分） | C 席 | 換算 C | C の P/席 |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        out.append(f"| {r['version']} | {r['A_convs']}（{r['A_in_B']}） | {r['B_plans']} | {r['prs']} | {f(r['cost_A'])} | "
                   f"{f(r['cost_B_low'])}〜{f(r['cost_B_high'])} | {f(r['cost_total_low'])} | {f(r['cost_per_pr'])} | "
                   f"{f(r['A_P_per_conv'])} | {f(r['A_calls_per_conv'])} | {f(r['A_min_per_conv'])} | "
                   f"{f(r['B_turns_per_plan'])} | {f(r['B_llm_min_per_plan'])} | {r['C_seats']} | {f(r['C_cost'])} | "
                   f"{f(r['C_P_per_seat'])} |")
    out += ["", "## 寄せられなかったもの", ""]
    for src, c in meta["unassigned"].items():
        for k, n in c.items():
            out.append(f"- {src}: {k} {n} 件")
    out += ["", "## 会話・計画ごと（A と B）", "",
            "| 出所 | ブランチ / 計画 | 種類 | 開始 | 終了 | モデル | 呼び出し | input | cache read | write 5 分 | write 1 時間 | output | P | 換算 | PR | 版 | 寄せ方 |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |"]
    for r in meta["A"]:
        out.append(f"| A | {r['branch']} | {r['kind']} | {iso(r['start'])} | {iso(r['end'])} | {r['model']} | {r['calls']} | "
                   f"{r['input']:,} | {r['cache_read']:,} | {r['cache_write_5m']:,} | {r['cache_write_1h']:,} | {r['output']:,} | "
                   f"{r['P']:,} | {r['cost']:,} | {r.get('pr') or '-'} | {r['version'] or '-'} | {r['by']} |")
    for r in meta["B"]:
        out.append(f"| B | {r['plan']} | {r['kind']} | - | {iso(r['at'])} | 不明 | 往復 {r['turns']} | {r['input']:,} | "
                   f"{r['cache_read']:,} | 合計 {r['cache_write']:,} | - | {r['output']:,} | - | {r['cost_low']:,} | "
                   f"{r.get('pr') or '-'} | {r['version'] or '-'} | {r['by']} |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-09-22T00:00:00Z")
    ap.add_argument("--until")
    ap.add_argument("--repo", type=Path, default=REPO, help="タグ・CHANGELOG・gh を読むリポジトリ（既定 %(default)s）")
    ap.add_argument("--sv-root", type=Path, default=SV_ROOT, help="計画の状態の置き場（既定 %(default)s）")
    ap.add_argument("--projects", type=Path, default=PROJ, help="Claude Code の会話の置き場（既定 %(default)s）")
    ap.add_argument("--seat-prefix", default=SEAT_PREFIX, help="レビュー・改修の席の会話の置き場の接頭辞（既定 %(default)s）")
    ap.add_argument("--out", default=str(Path.home() / ".local/state/ndf/measure-1142/claude-p-usage"),
                    help="出力の接頭辞（.md と .json を書く。既定 %(default)s）")
    a = ap.parse_args()
    configure(a.repo, a.sv_root, a.projects, a.seat_prefix)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    since, until = ts(a.since), (ts(a.until) if a.until else None)
    mp = Mapper(load_prs(Path(a.out).parent / "prs.json"))
    A, C = read_persisted(since, until, mp)
    B = read_reports(since, until)
    un = {"A": assign(A, mp, lambda r: r["prs"]), "B": assign(B, mp, pr_of_report(mp)),
          "C": assign(C, mp, lambda r: r["prs"])}
    rows = table(mp, A, B, C)
    meta = {"generated": iso(datetime.now(timezone.utc).timestamp()), "since": a.since, "until": a.until,
            "unassigned": {k: dict(v) for k, v in un.items()}, "A": A, "B": B}
    Path(a.out + ".md").write_text(md(rows, meta))
    for r in A + C:
        r.pop("cwd", None)
    for r in B:
        r.pop("record", None)
    Path(a.out + ".json").write_text(json.dumps({"meta": {k: meta[k] for k in ("generated", "since", "until", "unassigned")},
                                                 "by_version": rows, "A": A, "B": B, "C": C},
                                                ensure_ascii=False, indent=1))
    print(json.dumps({"A": len(A), "B": len(B), "C": len(C), "unassigned": meta["unassigned"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
