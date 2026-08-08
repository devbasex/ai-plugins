#!/usr/bin/env python3
"""Skill の frontmatter が執筆規約に適合しているかを検査する。

規約の本文は plugins/ndf-shared/skills/README.md にある。本スクリプトはそのうち
機械的に判定できる項目だけを検査し、継続的インテグレーションで実行する。

検査は 3 種類に分かれる。

- **individual** — Skill 単位。仕様準拠・安全性・可搬性・運用
- **aggregate**  — 全 Skill の合計。Codex の初期一覧予算と frontmatter 総量
- **cross**      — Skill 間。トリガ語の重複

判定が本質的に近似になる項目（description 先頭のトリガ語、when_to_use の追加トリガ）は
警告にとどめ、`--strict` を付けたときだけ失敗させる。

使い方:

    python3 scripts/check-skill-frontmatter.py
    python3 scripts/check-skill-frontmatter.py --skills-dir plugins/ndf-shared/skills
    python3 scripts/check-skill-frontmatter.py --strict
    python3 scripts/check-skill-frontmatter.py --report   # 実測値の一覧だけ出す
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# --- 規約の上限値 -----------------------------------------------------------
# 出典は plugins/ndf-shared/skills/README.md「上限値」。
NAME_MAX = 64                 # Agent Skills 仕様
DESCRIPTION_SPEC_MAX = 1024   # Agent Skills 仕様
DESCRIPTION_OPS_MAX = 300     # 運用目標
DESC_LEAD_CHARS = 160         # この範囲に用途またはトリガ語を置く（Codex の短縮対策）
COMPATIBILITY_MAX = 500       # Agent Skills 仕様
DESC_PLUS_WTU_MAX = 1536      # Claude Code の一覧切り詰め
SKILL_MD_MAX_LINES = 500      # 仕様の推奨 / コンパクション対策
CODEX_LISTING_MAX = 8000      # Codex の初期一覧予算（コンテキスト長不明時）
FRONTMATTER_TOTAL_MAX = 12000 # 全 Skill の frontmatter 合計。棚卸完了時の実測を基準に設定

# --- 許可する frontmatter の項目 -------------------------------------------
# Agent Skills 仕様の 6 項目 + Claude Code 独自項目。
# 未知の項目はハイフン誤り（when-to-use など）を弾くために失敗させる。
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
CLAUDE_KEYS = {
    "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
    "user-invocable", "paths", "effort", "context", "background", "agent", "model",
}
ALLOWED_KEYS = SPEC_KEYS | CLAUDE_KEYS

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")
TRIGGER_LABEL_RE = re.compile(
    r"(?:Triggers?|明示トリガ|トリガー?)\s*[:：]\s*(.+)", re.IGNORECASE | re.DOTALL
)
# 「いつ使うか」を示す語。description にこれが無いと Codex / Kiro で発動判定できない。
USE_WHEN_RE = re.compile(r"Use\s+when|use\s+when|使う|使い|とき|時に|ときに")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.。])\s*")


class Finding:
    __slots__ = ("skill", "level", "code", "message")

    def __init__(self, skill: str, level: str, code: str, message: str) -> None:
        self.skill = skill
        self.level = level
        self.code = code
        self.message = message

    def __str__(self) -> str:
        mark = "ERROR" if self.level == "error" else "WARN "
        return f"{mark} [{self.code}] {self.skill}: {self.message}"


def parse_front_matter(text: str) -> tuple[dict[str, str], str] | tuple[None, str]:
    """frontmatter を {key: 生の値} と生ブロックの組で返す。

    値は引用符を外さずそのまま保持する。二重引用符の有無を検査するため。
    リスト値（allowed-tools 等）は改行区切りの文字列にまとめる。
    """
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return None, ""
    block = m.group(1)
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in block.splitlines():
        km = KEY_RE.match(line)
        if km:
            if key is not None:
                out[key] = "\n".join(buf).strip()
            key = km.group(1)
            buf = [km.group(2)]
        elif key is not None:
            buf.append(line.strip())
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out, block


def unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def extract_triggers(*fields: str) -> list[str]:
    """`Triggers:` / `明示トリガ:` 以降に列挙された引用符付きの語を集める。"""
    triggers: list[str] = []
    for field in fields:
        if not field:
            continue
        m = TRIGGER_LABEL_RE.search(field)
        if not m:
            continue
        triggers.extend(q.strip() for q in QUOTED_RE.findall(m.group(1)))
    seen: set[str] = set()
    out: list[str] = []
    for t in triggers:
        k = t.lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def load_skills(skills_dir: pathlib.Path) -> list[dict]:
    skills: list[dict] = []
    for d in sorted(skills_dir.iterdir()):
        f = d / "SKILL.md"
        if not d.is_dir() or not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, block = parse_front_matter(text)
        skills.append({
            "dir": d.name,
            "path": f,
            "fm": fm,
            "block": block,
            "lines": len(text.splitlines()),
        })
    return skills


def check_skill(s: dict) -> list[Finding]:
    name_hint = s["dir"]
    fm = s["fm"]
    if fm is None:
        return [Finding(name_hint, "error", "spec/frontmatter", "frontmatter がない")]

    out: list[Finding] = []
    add = lambda level, code, msg: out.append(Finding(name_hint, level, code, msg))

    raw_desc = fm.get("description", "")
    desc = unquote(raw_desc)
    raw_wtu = fm.get("when_to_use", "")
    wtu = unquote(raw_wtu)
    name = unquote(fm.get("name", ""))

    # --- 仕様準拠 ---
    if not name:
        add("error", "spec/name", "name がない")
    else:
        if name != s["dir"]:
            add("error", "spec/name", f"name '{name}' が親ディレクトリ名 '{s['dir']}' と一致しない")
        if len(name) > NAME_MAX:
            add("error", "spec/name", f"name が {len(name)} 文字（上限 {NAME_MAX}）")
        if not NAME_RE.match(name):
            add("error", "spec/name",
                f"name '{name}' は小文字英数とハイフンのみ・先頭末尾ハイフン不可・連続ハイフン不可")

    if not desc:
        add("error", "spec/description", "description がない、または空")
    elif len(desc) > DESCRIPTION_SPEC_MAX:
        add("error", "spec/description", f"description が {len(desc)} 文字（仕様上限 {DESCRIPTION_SPEC_MAX}）")

    compat = unquote(fm.get("compatibility", ""))
    if len(compat) > COMPATIBILITY_MAX:
        add("error", "spec/compatibility", f"compatibility が {len(compat)} 文字（上限 {COMPATIBILITY_MAX}）")

    # --- 安全性 ---
    # Agent Skills 仕様がシステムプロンプトへの注入リスクとして警告している。
    if "<" in s["block"] or ">" in s["block"]:
        bad = [k for k, v in fm.items() if "<" in v or ">" in v]
        add("error", "safety/angle-bracket",
            f"frontmatter に < または > が含まれる（{', '.join(bad) or '不明'}）")

    # --- 可搬性 ---
    # Codex と Kiro は when_to_use を読まないため、発動条件は description に要る。
    if desc and not USE_WHEN_RE.search(desc):
        add("error", "portability/use-when",
            "description に発動条件を示す語（Use when / 使う / とき）がない")
    if raw_desc and not raw_desc.startswith('"'):
        add("error", "portability/quote",
            "description が二重引用符で囲まれていない（Kiro が未引用のコロンで検出に失敗する）")
    if desc:
        # Codex は初期一覧が予算を超えると description を先頭から残して短縮する。
        # 「いつ使うか」が後半にしかないと、短縮後は暗黙起動の判定に届かない。
        # 厳密な判定はできないため、先頭 DESC_LEAD_CHARS 文字の中に用途を示す語か
        # 宣言トリガ語のどちらかが現れることを目安にする。
        lead = desc[:DESC_LEAD_CHARS]
        triggers = extract_triggers(desc, wtu)
        has_trigger = any(t.lower() in lead.lower() for t in triggers)
        if not has_trigger and not USE_WHEN_RE.search(lead):
            add("warn", "portability/lead",
                f"description の先頭 {DESC_LEAD_CHARS} 文字に用途もトリガ語も現れない"
                "（Codex は予算超過時に description を先頭から残して短縮する）")

    # --- 運用 ---
    if len(desc) > DESCRIPTION_OPS_MAX:
        add("error", "ops/description-length",
            f"description が {len(desc)} 文字（運用上限 {DESCRIPTION_OPS_MAX}）")
    if len(desc) + len(wtu) > DESC_PLUS_WTU_MAX:
        add("error", "ops/desc-plus-wtu",
            f"description + when_to_use が {len(desc) + len(wtu)} 文字（上限 {DESC_PLUS_WTU_MAX}）")
    if s["lines"] > SKILL_MD_MAX_LINES:
        add("error", "ops/skill-lines", f"SKILL.md が {s['lines']} 行（上限 {SKILL_MD_MAX_LINES}）")

    if wtu:
        # when_to_use は「Claude Code 向けの追加トリガ」がある場合だけ付ける。
        # description のトリガ語を並べ替えただけのものは、その根拠を持たない。
        d_trigs = {t.lower() for t in extract_triggers(desc)}
        w_trigs = {t.lower() for t in extract_triggers(wtu)}
        if w_trigs and not (w_trigs - d_trigs):
            add("warn", "ops/wtu-no-extra",
                "when_to_use のトリガ語が description と同一で、追加トリガがない")

    dmi = unquote(fm.get("disable-model-invocation", "")).lower() == "true"
    uinv = unquote(fm.get("user-invocable", "")).lower() == "false"
    if dmi and uinv:
        add("error", "ops/uninvocable",
            "disable-model-invocation: true と user-invocable: false の同時指定は誰も起動できない")
    if dmi and not fm.get("argument-hint"):
        add("warn", "ops/argument-hint",
            "disable-model-invocation があるのに argument-hint がない（明示起動時の引数が伝わらない）")

    ctx = unquote(fm.get("context", ""))
    for k in ("agent", "background"):
        if k in fm and ctx != "fork":
            add("error", "ops/context-fork", f"{k} は context: fork のときだけ指定できる")

    unknown = sorted(set(fm) - ALLOWED_KEYS)
    if unknown:
        add("error", "ops/unknown-key", f"未知の項目名: {', '.join(unknown)}")

    return out


def check_aggregate(skills: list[dict], skills_dir: pathlib.Path) -> tuple[list[Finding], dict]:
    """Codex の初期一覧予算と frontmatter 総量を検査する。

    Codex は起動時に name / description / ファイルパスを一覧として読み込み、
    この一覧に総量予算を設けている（超過すると description を短縮し、なお超えると
    Skill を一覧から省略して警告を出す）。
    """
    listing = 0
    fm_total = 0
    for s in skills:
        fm = s["fm"] or {}
        name = unquote(fm.get("name", s["dir"]))
        desc = unquote(fm.get("description", ""))
        rel = f"{skills_dir.name}/{s['dir']}/SKILL.md"
        listing += len(name) + len(desc) + len(rel)
        fm_total += len(s["block"])

    out: list[Finding] = []
    if listing > CODEX_LISTING_MAX:
        out.append(Finding("(全体)", "error", "ops/codex-listing",
                           f"Codex の初期一覧に載る合計が {listing} 文字（上限 {CODEX_LISTING_MAX}）"))
    if fm_total > FRONTMATTER_TOTAL_MAX:
        out.append(Finding("(全体)", "error", "ops/frontmatter-total",
                           f"全 Skill の frontmatter 合計が {fm_total} 文字（上限 {FRONTMATTER_TOTAL_MAX}）"))
    return out, {"codex_listing": listing, "frontmatter_total": fm_total}


def check_trigger_collisions(skills: list[dict]) -> list[Finding]:
    """同じトリガ語を複数の Skill が宣言していないかを検査する。

    重複すると同じ依頼で複数の Skill が起動を競い、どちらが選ばれるかが
    依頼文の細部に左右される。
    """
    owners: dict[str, list[str]] = {}
    for s in skills:
        fm = s["fm"] or {}
        trigs = extract_triggers(unquote(fm.get("description", "")),
                                 unquote(fm.get("when_to_use", "")))
        for t in trigs:
            owners.setdefault(t.lower(), []).append(s["dir"])
    out: list[Finding] = []
    for trig, names in sorted(owners.items()):
        uniq = sorted(set(names))
        if len(uniq) > 1:
            out.append(Finding(", ".join(uniq), "error", "ops/trigger-collision",
                               f"トリガ語 '{trig}' が複数の Skill で重複している"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skills-dir", default="plugins/ndf-shared/skills",
                    help="検査対象の Skill ディレクトリ（default: %(default)s）")
    ap.add_argument("--strict", action="store_true",
                    help="警告も失敗として扱う")
    ap.add_argument("--report", action="store_true",
                    help="判定せず実測値の一覧だけ出力する")
    args = ap.parse_args()

    skills_dir = pathlib.Path(args.skills_dir)
    if not skills_dir.is_dir():
        print(f"[check-skill-frontmatter] ディレクトリがない: {skills_dir}", file=sys.stderr)
        return 2

    skills = load_skills(skills_dir)
    if not skills:
        print(f"[check-skill-frontmatter] SKILL.md が見つからない: {skills_dir}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for s in skills:
        findings.extend(check_skill(s))
    agg, metrics = check_aggregate(skills, skills_dir)
    findings.extend(agg)
    findings.extend(check_trigger_collisions(skills))

    if args.report:
        print(f"{'skill':34} {'lines':>5} {'desc':>5} {'wtu':>5}  flags")
        for s in sorted(skills, key=lambda x: x["dir"]):
            fm = s["fm"] or {}
            flags = [k for k in ("disable-model-invocation", "user-invocable", "paths",
                                 "effort", "context", "arguments", "license")
                     if k in fm]
            print(f"{s['dir']:34} {s['lines']:>5} "
                  f"{len(unquote(fm.get('description', ''))):>5} "
                  f"{len(unquote(fm.get('when_to_use', ''))):>5}  {','.join(flags)}")
        print(f"\nSkill 数: {len(skills)}")
        print(f"Codex 初期一覧の合計: {metrics['codex_listing']} 文字 (上限 {CODEX_LISTING_MAX})")
        print(f"frontmatter 合計: {metrics['frontmatter_total']} 文字 (上限 {FRONTMATTER_TOTAL_MAX})")
        return 0

    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]
    for f in sorted(findings, key=lambda x: (x.level != "error", x.skill, x.code)):
        print(str(f), file=sys.stderr if f.level == "error" else sys.stdout)

    print(f"\nSkill {len(skills)} 個を検査 — エラー {len(errors)} 件 / 警告 {len(warns)} 件")
    print(f"Codex 初期一覧の合計: {metrics['codex_listing']} / {CODEX_LISTING_MAX} 文字")
    print(f"frontmatter 合計: {metrics['frontmatter_total']} / {FRONTMATTER_TOTAL_MAX} 文字")

    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
