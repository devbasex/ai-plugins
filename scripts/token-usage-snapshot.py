#!/usr/bin/env python3
"""正式版を出すときに、版ごとのトークン消費と所要時間の記録を書き出す（#893）。

`.ndf/release.json` の段として `release` の手順 3 から呼ばれる。集計は `token-usage.py` の
`collect()` を読み込んで 1 回だけ行い、記録の `.md` / `.json` を `docs/metrics/ndf-token-usage/` へ書く。

    python3 scripts/token-usage-snapshot.py --released 10.17.9 [--until 2026-09-30T09:00:00Z]
                                            [--min-version 10.17.7] [--out <dir>]

- 表の最初の行は、前の記録（`--out` の `.json` のうち、`meta.released` が配る版と違い、`meta.until` が
  この打ち切りより前で最も新しいもの）の最後の版。`meta.until` の無い記録は最も古いとみなす
- ファイル名は打ち切りの UTC の日付。同じ版の記録があればそこへ書き直し、別の版の記録が同じ名前を
  持っていれば `-2`・`-3` と進める
- 前の行との比が ±30% を超えた版を標準出力に挙げる。その版の読み取りだけをエージェントが書き足す
  （手引きは `docs/metrics/ndf-token-usage/README.md`）

終了コード: 0 書き出した / 1 読み込み・書き出しに失敗した / 2 前の記録が無く `--min-version` も無い。

**出力に会話の本文・ファイルのパス・リポジトリ名・会話の ID を載せない**（`token-usage.py` と同じ）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THRESHOLD = 0.30  # 前の行との比がこれを超えた版だけ読み取りを求める（決定 2）
FEW_SESSIONS = 2  # PR を作った会話がこの件数以下の版は、作業の中身の違いが版の違いより大きい
LIVE_WINDOW = 1800  # 打ち切りのこの秒数前以降にも行がある会話は、進行中を含みうる（未確認 U1）
CHANGELOG_RE = re.compile(r"^## \[ndf ([^\]\s]+)\]", re.M)


def load_token_usage():
    """ファイル名にハイフンがあるためパスから読み込む。`@dataclass` がモジュールを引けるよう先に登録する。"""
    name = "token_usage"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name("token-usage.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tu = load_token_usage()


def safe_key(v: str):
    try:
        return tu.version_key(v)
    except ValueError:
        return None


# ---------- 前の記録と名前 ----------

def read_records(out: Path) -> list[tuple[str, dict]]:
    records = []
    for path in sorted(out.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("meta"), dict):
            records.append((path.name, data))
    return records


def previous_record(records: list[tuple[str, dict]], released: str, until: float) -> tuple[str, dict] | None:
    cands = []
    for name, data in records:
        meta = data["meta"]
        if meta.get("released") == released:
            continue  # 走らせ直しで自分が前に書いた記録を取らない
        t = tu.parse_ts(meta.get("until"))
        t = -math.inf if t is None else t
        if t < until:
            cands.append((t, name, data))
    if not cands:
        return None
    _, name, data = max(cands, key=lambda c: (c[0], c[1]))
    return name, data


def last_version(data: dict) -> str | None:
    versions = [r.get("version") for r in data.get("per_pr") or [] if isinstance(r, dict)]
    keyed = [(safe_key(v), v) for v in versions if isinstance(v, str) and safe_key(v) is not None]
    return max(keyed)[1] if keyed else None


def target_name(out: Path, date: str, released: str) -> str:
    n = 1
    while True:
        name = date if n == 1 else f"{date}-{n}"
        js, md = out / f"{name}.json", out / f"{name}.md"
        if not js.exists() and not md.exists():
            return name
        try:
            if json.loads(js.read_text(encoding="utf-8"))["meta"]["released"] == released:
                return name
        except (OSError, ValueError, KeyError, TypeError):
            pass
        n += 1


# ---------- 表 ----------

def table(cols: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    return lines + ["| " + " | ".join(r) + " |" for r in rows]


def diff_rows(per_pr: list[dict]) -> list[tuple[dict, float, float | None]]:
    """PR のある版の行と、換算の合計・1 つ上の行との比。"""
    out, prev = [], None
    for r in per_pr:
        if not r["prs"]:
            continue
        total = r["conductor_cost"] + r["supervisor_cost"] + r["worker_cost"]
        ratio = None if not prev else total / prev - 1
        out.append((r, total, ratio))
        prev = total
    return out


def layer_rows(per_role: list[dict]) -> list[list[str]]:
    acc: dict = defaultdict(lambda: defaultdict(float))
    order = {"conductor": 0, "supervisor": 1, "worker": 2}
    for r in per_role:
        a = acc[(r["version"], r["layer"])]
        n = r["count"]
        a["n"] += n
        for k in ("p", "k", "w5", "w1h"):
            a[k] += r[k] * n
        a["rewrites"] += r["rewrites"]
        a["after"] += r["rewrites_after_5m"]
    rows = []
    for (v, layer), a in sorted(acc.items(), key=lambda x: (tu.version_key(x[0][0]), order.get(x[0][1], 9))):
        n = a["n"]
        rows.append([v, layer, str(int(n)), tu._k(a["p"] / n), f"{a['k'] / n:.1f}", tu._m(a["w5"] / n),
                     tu._m(a["w1h"] / n), str(int(a["rewrites"])), str(int(a["after"])), f"{a['rewrites'] / n:.2f}"])
    return rows


@dataclass
class Snapshot:
    released: str
    until_s: str
    until: float
    date: str
    floor: str
    previous: str | None
    diffs: list
    large: list[str]
    by_version: dict
    sessions: list
    changelog: Path


def notes(snap: Snapshot) -> list[str]:
    diffs, sessions, changelog, until = snap.diffs, snap.sessions, snap.changelog, snap.until
    shown = {r["version"] for r, _, _ in diffs}
    few = [f"{r['version']}（{r['sessions_with_pr']} 件）" for r, _, _ in diffs if r["sessions_with_pr"] <= FEW_SESSIONS]
    out = [f"- **PR を作った会話が {FEW_SESSIONS} 件以下の版:** {', '.join(few) or '無し'}。"
           "作業の中身の違いが版の違いより大きい"]
    try:
        listed = CHANGELOG_RE.findall(changelog.read_text(encoding="utf-8"))
    except OSError:
        out.append(f"- **CHANGELOG.md にあって表に出ない版:** 確かめていない（{changelog.name} を読めない）")
    else:
        lo, hi = tu.version_key(snap.floor), tu.version_key(snap.released)
        missing = sorted({v for v in listed if safe_key(v) is not None and lo <= safe_key(v) <= hi and v not in shown},
                         key=tu.version_key)
        out.append(f"- **CHANGELOG.md にあって表に出ない版:** {', '.join(missing) or '無し'}。"
                   "その版で始めた PR つきの会話が無い（会話の版は最初に読んだ版で決まる）")
    live = sorted({s.version for s in sessions if s.end >= until - LIVE_WINDOW}, key=tu.version_key)
    out.append(f"- **打ち切りの {LIVE_WINDOW // 60} 分前以降にも行がある会話を含む版:** {', '.join(live) or '無し'}。"
               "進行中だった会話は、値が打ち切りの時刻までの分である")
    return out


def _diff_table_rows(diffs) -> list[list[str]]:
    return [[r["version"], str(r["sessions_with_pr"]), str(r["prs"]), tu._m(total),
             "-" if ratio is None else f"{ratio:+.0%}", tu._m(r["conductor_cost"]), tu._m(r["supervisor_cost"]),
             tu._m(r["worker_cost"]), tu._m(r["context"]), f"{r['minutes']:.1f}", tu._m(r["codex_input"])]
            for r, total, ratio in diffs]


def _reading_lines(large: list[str]) -> list[str]:
    if large:
        return [f"差の大きい版: {', '.join(large)}。手引き（README.md）に従い、版ごとに 1〜3 行をここへ書く。"]
    return [f"差の大きい版は無い（すべて ±{THRESHOLD:.0%} の内）。読み取りは要らない。"]


def render(snap: Snapshot) -> str:
    released, until_s, date, floor, previous = snap.released, snap.until_s, snap.date, snap.floor, snap.previous
    diffs, large, by_version = snap.diffs, snap.large, snap.by_version
    cmd = f"python3 scripts/token-usage-snapshot.py --released {released} --until {until_s} --min-version {floor}"
    out = [f"# ndf の版ごとのトークン消費と所要時間（{date} 集計・{released} の配布）", "",
           f"**{released} を正式版として出したときの記録である。** 打ち切りの時刻は `{until_s}`。"
           f"表の最初の行は {floor}（前の記録: {previous or 'なし'}）。既定では前の記録の最後の版で、記録どうしが 1 行ずつ重なる。"
           "生の記録・会話の本文・リポジトリ名・会話の ID は残していない。"
           "集計した日時点の記録で、以後の記録の増減には追随しない。", "",
           "## 作り方", "",
           "同じ打ち切りの時刻で走らせ直すと、記録が残っている間は同じ `.json` ができる。ただし kiro の記録は"
           "ターンに時刻を持たないため、打ち切りをまたいで進行中だった席は credit が変わりうる。", "",
           "```bash", cmd,
           f"python3 scripts/token-usage.py --min-version {floor} --until {until_s} --by version   # 末尾の「集計の出力」",
           "```", "",
           "## 比べるときの注意", "",
           *notes(snap), "",
           "## 版ごとの差（PR 1 本あたり）", "",
           f"換算の合計は conductor・supervisor・worker の換算の和。前の版との差は、表の 1 つ上の行との比である。"
           f"±{THRESHOLD:.0%} を超えた版を下の「読み取り」で扱う。", ""]
    out += table(["ndf の版", "会話", "PR", "換算の合計", "前の版との差", "conductor", "supervisor", "worker",
                  "入力", "所要", "codex 入力"],
                 _diff_table_rows(diffs))
    out += ["", "## 版と層ごとの呼び出しとキャッシュ（1 起動あたり）", "",
            "P と k と書き込みは 1 起動あたり、書き直しとうち 5 分超は合計の回数である。", ""]
    out += table(["ndf の版", "層", "起動", "P", "k", "書き込み 5 分", "書き込み 1 時間", "書き直し", "うち 5 分超",
                  "1 起動あたりの書き直し"], layer_rows(by_version["per_role"]))
    out += ["", "## 読み取り", "", *_reading_lines(large)]
    body = tu.render_md(by_version, ["version"])
    body = "\n".join("#" + line if line.startswith("## ") else line for line in body.splitlines())
    out += ["", "## 集計の出力", "", body]
    return "\n".join(out) + "\n"


# ---------- 入口 ----------

def parse_until(value: str | None) -> tuple[str, float]:
    if value is None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        value = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    t = tu.parse_ts(value)
    if t is None or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError(f"--until を時間帯つきの時刻として読めない（例: 2026-09-30T09:00:00Z）: {value}")
    return value, t


def resolve_floor(out: Path, released: str, until: float, min_version: str | None) -> tuple[tuple[str, dict] | None, str | None]:
    records = read_records(out) if out.is_dir() else []
    prev = previous_record(records, released, until)
    return prev, min_version or (last_version(prev[1]) if prev else None)


def build(args, floor: str, prev, until_s: str, until: float, date: str) -> tuple[dict, str, list[str]]:
    sessions, unlinked, skipped = tu.collect(args.claude_root, args.codex_root, args.kiro_root,
                                             until=until, min_version=floor)
    meta = {"by": list(tu.AXES), "min_version": floor, "until": until_s, "sessions": len(sessions),
            "sessions_with_pr": sum(1 for s in sessions if s.prs), "unlinked_external": unlinked,
            "skipped": skipped, "released": args.released, "previous": prev[0] if prev else None}
    full = tu.aggregate(sessions, list(tu.AXES)) | {"meta": meta}
    by_version = tu.aggregate(sessions, ["version"]) | {"meta": meta | {"by": ["version"]}}
    diffs = diff_rows(by_version["per_pr"])
    large = [f"{r['version']}（{ratio:+.0%}）" for r, _, ratio in diffs if ratio is not None and abs(ratio) > THRESHOLD]
    md = render(Snapshot(args.released, until_s, until, date, floor,
                         prev[0].removesuffix(".json") + ".md" if prev else None, diffs, large,
                         by_version, sessions, args.changelog))
    return full, md, large


def write_record(out: Path, date: str, released: str, full: dict, md: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    name = target_name(out, date, released)
    (out / f"{name}.json").write_text(json.dumps(full, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / f"{name}.md").write_text(md, encoding="utf-8")
    return name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="版ごとのトークン消費と所要時間の記録を書き出す")
    home = Path(os.path.expanduser("~"))
    ap.add_argument("--released", required=True, help="配る版")
    ap.add_argument("--until", help="打ち切りの時刻（ISO 8601・時間帯つき）。既定は実行した時刻（UTC・分で切り捨て）")
    ap.add_argument("--min-version", help="表の最初の行。既定は前の記録の最後の版")
    ap.add_argument("--out", type=Path, default=REPO / "docs/metrics/ndf-token-usage")
    ap.add_argument("--changelog", type=Path, default=REPO / "CHANGELOG.md")
    ap.add_argument("--claude-root", type=Path, default=home / ".claude/projects")
    ap.add_argument("--codex-root", type=Path, default=home / ".codex/sessions")
    ap.add_argument("--kiro-root", type=Path, default=home / ".kiro/sessions/cli")
    args = ap.parse_args(argv)
    try:
        until_s, until = parse_until(args.until)
    except ValueError as e:
        ap.error(str(e))
    if safe_key(args.released) is None:
        ap.error(f"--released を版として読めない: {args.released}")
    date = datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d")

    try:
        prev, floor = resolve_floor(args.out, args.released, until, args.min_version)
        if not floor:
            print("前の記録が無い。--min-version で表の最初の版を渡す", file=sys.stderr)
            return 2
        full, md, large = build(args, floor, prev, until_s, until, date)
        name = write_record(args.out, date, args.released, full, md)
    except OSError as e:
        print(f"記録を読めない・書けない: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"書き出した: {name}.md / {name}.json（表は {floor} から）")
    print(f"差の大きい版: {', '.join(large) or '無し'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
