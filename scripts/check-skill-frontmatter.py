#!/usr/bin/env python3
"""Skill の frontmatter が執筆規約に適合しているかを検査する。

規約の本文は plugins/ndf-shared/skills/README.md にある。本スクリプトはそのうち
機械的に判定できる項目だけを検査し、継続的インテグレーションで実行する。

検査は 3 種類に分かれる。

- **individual** — Skill 単位。仕様準拠・安全性・可搬性・運用
- **aggregate**  — 配布先ごとの初期一覧予算（Claude Code / Codex）と frontmatter 総量。
  予算は検査対象の plugin family すべての合計に対して判定する
- **cross**      — Skill 間。トリガ語の重複と、既知の外部 Skill 名との衝突

判定が本質的に近似になる項目（description 先頭のトリガ語、when_to_use の追加トリガ、
既知の外部 Skill 名との衝突）は警告にとどめ、`--strict` を付けたときだけ失敗させる。
外部 Skill 名の一覧は網羅できないため（KNOWN_EXTERNAL_SKILL_NAMES の注記を参照）、
この検査は見逃しを前提にした補助である。

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
CLAUDE_LISTING_MAX = 8000     # Claude Code の初期一覧予算（コンテキスト長不明時）
CLAUDE_ITEM_TRUNCATE = 250    # Claude Code は 1 項目をこの長さで切り詰める
# 全 Skill の frontmatter 合計。**plugin family をまたいで合計する**（利用者の環境では
# 複数のプラグインが同時に入るため、family 内だけ見ても実際の注入量にならない）。
# v7.0.0 時点の実測 10,559 文字（ndf 30 個 + playwright-kit 4 個、2026-08-14）を基準に、
# 約 6% の余裕を足して 11,200 とした。Skill を増やすときは実測しなおして更新する。
FRONTMATTER_TOTAL_MAX = 11200

# --- 既知の外部 Skill 名 ----------------------------------------------------
# ランタイム組み込み・他プラグインの Skill 名のうち、実際に観測できたもの。
#
# **エントリは名前空間（`coderabbit:` などのプラグイン接頭辞）を除いた Skill 名で持つ。**
# 理由は 2 つ。
#
# 1. 組み込み Skill（code-review / security-review）はそもそも名前空間を持たないため、
#    名前空間付きに統一することができない
# 2. `/` メニューで NDF の Skill が埋もれるかどうかを決めるのは名前空間ではなく
#    Skill 名の部分である。`coderabbit:code-review` の Skill 名は `code-review` で、
#    埋もれる原因になるのはこの `code-review` の側
#
# したがって一覧の単位は「名前空間を除いた Skill 名」が正しい。行末に、`/` メニューで
# どう表示されていたか（名前空間付きの表示名）を観測元として残す。
#
# この一覧は網羅ではない。配布先の環境に何が入っているかは検査時点では分からず、
# 利用者が入れる他プラグインまでは列挙できない。観測できたものを手で足していく
# best-effort の検査であり、ここに無い競合を見逃すことを前提にする。
#
# 出典: 2026-08-12 に Claude Code の `/` メニューで観測（issue #83）。
KNOWN_EXTERNAL_SKILL_NAMES = (
    "code-review",              # Claude Code 組み込み（`code-review`）/ `coderabbit:code-review`
    "security-review",          # Claude Code 組み込み（`security-review`）
    "coderabbit-review",        # `coderabbit:coderabbit-review`
    "requesting-code-review",   # `superpowers:requesting-code-review`
    "receiving-code-review",    # `superpowers:receiving-code-review`
)

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
# 廃止した旧書式（`Triggers: 'a', 'b'`）。残っていたら失敗させる。ラベルと引用符の分だけ
# 長いうえ、実測では description 末尾の列挙は暗黙起動へ届きにくかった
# （docs/specifications/ndf-skill-inventory.md「トリガ書式の変更の実測」）。
LEGACY_TRIGGER_RE = re.compile(
    # ラベルの直後に引用符付きの語が続くものだけを旧書式と見なす。
    # 「This will trigger: ...」のような一般名詞としての用法で失敗させないため。
    r"(?:Triggers?|明示トリガ|トリガー?|追加トリガ)\s*[:：]\s*['\"]", re.IGNORECASE
)
# 末尾の全角丸括弧に「・」区切りで並べたトリガ語（規約「トリガ語の書式」）。
#
# 誤検出を避けるため 2 つの条件を課す。
#   1. description の**末尾**にあること（本文中の `(Codex/Gemini)` のような補足を拾わない）
#   2. 日本語を 1 文字以上含むこと（英語の補足を拾わない）
# 条件を外すと、トリガ宣言でない括弧が重複検査へ流れ込み、偽の衝突が出る。
TRIGGER_PAREN_RE = re.compile(r"（([^（）]{2,160})）\s*[.。]?\s*$")
HAS_JA_RE = re.compile(r"[ぁ-んァ-ヶ一-龠ー]")
# 「いつ使うか」を示す語。description にこれが無いと Codex / Kiro で発動判定できない。
USE_WHEN_RE = re.compile(r"Use\s+when|use\s+when|使う|使い|とき|時に|ときに")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.。])\s*")

# --- 「引数を取る Skill か」の判定 -------------------------------------------
# 規約（README「発動制御の 4 分類」）は明示指示専用の Skill に対して
# 「disable-model-invocation: true（引数を取るなら + argument-hint）」と定めている。
# 引数を取らない明示指示専用 Skill まで落とさないよう、引数の有無を SKILL.md から
# 機械的に判定する。判定材料は、実際の Skill が引数を表現している次の 3 通り。
#
#   1. frontmatter の `arguments` … Claude Code の名前付き引数を宣言している
#   2. 本文の `$ARGUMENTS`        … 引数をそのまま展開する（deploy / fix / plan-to-spec）
#   3. 本文が引数を説明している    … 「## 引数」節（review / pr-tests / cross-review）、
#                                    「### 1. 引数・現状確認」（cherry-pick-pr）、
#                                    「引数に応じて…」（statusline）など表記は揺れる
#
# 3 は見出しに限定すると statusline のような散文の説明を取りこぼすため、本文中の
# 「引数」への言及も拾う。英語表記は一般語と紛れるので見出しに限定する。
# 引数を取るのに SKILL.md がそれを一切説明していない Skill は判定から漏れるが、
# その場合は利用者にも引数が伝わらないため argument-hint 以前の問題として扱う。
ARGUMENTS_VAR_RE = re.compile(r"\$\{?ARGUMENTS\}?")
ARGUMENTS_HEADING_RE = re.compile(r"^#{1,6}\s.*\b(?:arguments?|options?)\b", re.MULTILINE | re.IGNORECASE)
ARGUMENTS_TEXT_RE = re.compile(r"引数")


def takes_arguments(fm: dict[str, str], body: str) -> bool:
    """SKILL.md が引数を取ると読めるかを判定する（判定根拠は上のコメント）。"""
    if "arguments" in fm:
        return True
    return bool(
        ARGUMENTS_VAR_RE.search(body)
        or ARGUMENTS_TEXT_RE.search(body)
        or ARGUMENTS_HEADING_RE.search(body)
    )


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
    """宣言されたトリガ語を集める。

    書式は 1 つだけ。末尾の全角丸括弧に `・` 区切りで並べる
    （`… Use when a PR was merged（マージ後の後片付け・ブランチを整理）.`）。
    """
    triggers: list[str] = []
    for field in fields:
        if not field:
            continue
        p = TRIGGER_PAREN_RE.search(field.strip())
        if p and HAS_JA_RE.search(p.group(1)):
            triggers.extend(t.strip() for t in re.split(r"[・/／,、]", p.group(1)))
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
        m = FRONT_MATTER_RE.match(text)
        skills.append({
            "dir": d.name,
            "path": f,
            "fm": fm,
            "block": block,
            "body": text[m.end():] if m else text,
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

    if LEGACY_TRIGGER_RE.search(desc) or LEGACY_TRIGGER_RE.search(wtu):
        add("error", "portability/legacy-trigger",
            "廃止した旧書式のトリガ宣言（Triggers: / 明示トリガ:）が残っている。"
            "末尾の全角括弧へ `（語・語）` の形で並べる")

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

    # Codex と Kiro には disable-model-invocation / user-invocable がなく description は
    # 常に読まれる。発動制御の意図を description 自体へ書き残す必要がある。
    if unquote(fm.get("disable-model-invocation", "")).lower() == "true":
        if not re.search(r"明示|explicit|Explicit", desc):
            add("error", "portability/explicit-only",
                "明示指示専用の Skill は description に「利用者が明示的に指示したときのみ実行する」"
                "旨を書く（Codex / Kiro は disable-model-invocation を解釈しない）")
    if unquote(fm.get("user-invocable", "")).lower() == "false":
        if not re.search(r"知識として|参照する|実行しない|reference only|do not execute", desc):
            add("error", "portability/inject-only",
                "常時注入のみの Skill は description に「知識として参照する。手順として実行しない」"
                "旨を書く（Codex / Kiro は user-invocable を解釈しない）")

    dmi = unquote(fm.get("disable-model-invocation", "")).lower() == "true"
    uinv = unquote(fm.get("user-invocable", "")).lower() == "false"
    if dmi and uinv:
        add("error", "ops/uninvocable",
            "disable-model-invocation: true と user-invocable: false の同時指定は誰も起動できない")
    if dmi and takes_arguments(fm, s["body"]) and not fm.get("argument-hint"):
        # 規約は「引数を取るなら + argument-hint」。引数を取らない明示指示専用 Skill には
        # 要求しない（判定方法は takes_arguments の説明を参照）。
        add("error", "ops/argument-hint",
            "引数を取る明示指示専用 Skill に argument-hint がない（明示起動時の引数が伝わらない）")

    ctx = unquote(fm.get("context", ""))
    for k in ("agent", "background"):
        if k in fm and ctx != "fork":
            add("error", "ops/context-fork", f"{k} は context: fork のときだけ指定できる")

    unknown = sorted(set(fm) - ALLOWED_KEYS)
    if unknown:
        add("error", "ops/unknown-key", f"未知の項目名: {', '.join(unknown)}")

    return out


def load_manifests(skills_dir: pathlib.Path) -> dict[str, set[str]]:
    """manifests/(runtime)-skills.txt を読み、配布先ごとの Skill 名集合を返す。

    行末の `#` 以降はコメントとして落とす。scripts/build-runtime-plugins.sh の
    manifest 解釈と揃えるため（揃っていないと、コメント付きの manifest で
    配布先の判定が実際のビルド結果とずれる）。
    """
    man_dir = skills_dir.parent / "manifests"
    out: dict[str, set[str]] = {}
    for runtime in ("claude", "codex", "kiro"):
        f = man_dir / f"{runtime}-skills.txt"
        if not f.exists():
            continue
        members: set[str] = set()
        for line in f.read_text(encoding="utf-8").splitlines():
            name = line.split("#", 1)[0].strip()
            if name:
                members.add(name)
        out[runtime] = members
    return out


def measure_aggregate(skills: list[dict], skills_dir: pathlib.Path) -> dict:
    """初期一覧に載る文字数と frontmatter 総量を計測する（判定はしない）。

    Claude Code と Codex は起動時に name / description / ファイルパスを一覧として
    読み込み、この一覧に総量予算を設けている（超過すると description を短縮し、
    なお超えると Skill を一覧から省略して警告を出す）。予算は配布先ごとに効くため、
    manifest に載っている Skill だけを数える。

    予算の判定は plugin family をまたいだ合計に対して行う（利用者の環境では複数の
    plugin が同時に入るため、family 単位で判定すると合計の超過を見逃す）。
    そのためこの関数は計測値だけを返し、上限との比較は check_budget が行う。
    """
    manifests = load_manifests(skills_dir)
    listings: dict[str, int] = {r: 0 for r in manifests}
    fm_total = 0
    for s in skills:
        fm = s["fm"] or {}
        name = unquote(fm.get("name", s["dir"]))
        desc = unquote(fm.get("description", ""))
        rel = f"{skills_dir.name}/{s['dir']}/SKILL.md"
        fm_total += len(s["block"])
        for runtime, members in manifests.items():
            if s["dir"] not in members:
                continue
            # Claude Code は 1 項目を 250 文字で切り詰めてから積む。
            d = desc[:CLAUDE_ITEM_TRUNCATE] if runtime == "claude" else desc
            listings[runtime] += len(name) + len(d) + len(rel)

    return {"listings": listings, "frontmatter_total": fm_total}


def check_budget(metrics: dict) -> list[Finding]:
    """検査対象すべての合計値を予算と突き合わせる。

    引数は measure_aggregate の計測値を plugin family 横断で合計したもの。
    """
    out: list[Finding] = []
    limits = {"claude": CLAUDE_LISTING_MAX, "codex": CODEX_LISTING_MAX, "kiro": None}
    for runtime, total in sorted(metrics["listings"].items()):
        limit = limits.get(runtime)
        if limit is not None and total > limit:
            out.append(Finding("(全体)", "error", f"ops/{runtime}-listing",
                               f"{runtime} の初期一覧に載る合計が {total} 文字（上限 {limit}）"))
    fm_total = metrics["frontmatter_total"]
    if fm_total > FRONTMATTER_TOTAL_MAX:
        out.append(Finding("(全体)", "error", "ops/frontmatter-total",
                           f"全 Skill の frontmatter 合計が {fm_total} 文字（上限 {FRONTMATTER_TOTAL_MAX}）"))
    return out


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


def check_external_name_collisions(skills: list[dict]) -> list[Finding]:
    """Skill 名が既知の外部 Skill 名の末尾要素になっていないかを検査する。

    利用者が `/` メニューで名前の一部を打つと、その語を末尾に含む候補がすべて並ぶ。
    NDF の Skill 名が外部 Skill 名の末尾要素だと、外部側に埋もれて選びにくくなる。
    実例は `review`（`code-review` / `security-review` の末尾）で、issue #83 で
    `pr-review` へ改名した。

    逆向き（外部名が NDF 名の末尾要素）は検査しない。`pr-review` のように接頭辞で
    区別できていれば、利用者は `/pr-rev` まで打った時点で一意に決められる。

    突き合わせる相手は KNOWN_EXTERNAL_SKILL_NAMES で、そのエントリは名前空間を除いた
    Skill 名である（一覧の定義コメントを参照）。KNOWN_EXTERNAL_SKILL_NAMES が網羅でない
    ため、警告にとどめエラーにはしない。
    """
    out: list[Finding] = []
    for s in skills:
        name = unquote((s["fm"] or {}).get("name", "")) or s["dir"]
        # 区切りは `-`（`code-review` の `review`）と `:`（`plugin:review` の `review`）の両方を見る。
        # KNOWN_EXTERNAL_SKILL_NAMES の規約は「名前空間を除いた Skill 名」で確定していて、これを
        # 変える予定はない。`:` を見るのは規約を変える想定だからではなく、規約に反して
        # `coderabbit:code-review` のような表示名がそのまま貼られた場合に、検査が黙って
        # すり抜けるのを防ぐためである。`/` メニューの表示名をコピーしてしまう誤りは起きやすく、
        # `-` だけの判定だと衝突があっても警告が出ず、見逃したことにも気づけない。
        # 規約どおりのエントリしかない現状では、この分岐があっても挙動は変わらない。
        hits = [e for e in KNOWN_EXTERNAL_SKILL_NAMES
                if e == name or e.endswith(("-" + name, ":" + name))]
        if hits:
            out.append(Finding(s["dir"], "warn", "portability/external-name",
                               f"Skill 名 '{name}' が既知の外部 Skill "
                               f"({', '.join(hits)}) の末尾要素になっている。"
                               "`/` メニューで外部側に埋もれるため、"
                               "接頭辞で区別できる名前へ寄せる"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skills-dir", action="append", default=None,
                    help="検査対象の Skill ディレクトリ。複数指定できる"
                         "（既定: plugins/*-shared/skills を全て検査）")
    ap.add_argument("--strict", action="store_true",
                    help="警告も失敗として扱う")
    ap.add_argument("--report", action="store_true",
                    help="判定せず実測値の一覧だけ出力する")
    args = ap.parse_args()

    if args.skills_dir:
        skills_dirs = [pathlib.Path(d) for d in args.skills_dir]
    else:
        # plugin family（<family>-shared）を検出する。初期一覧の予算はプラグイン横断で
        # 共有されるため、既定では全 family を対象にして合計も出す。
        skills_dirs = sorted(
            d / "skills" for d in pathlib.Path("plugins").glob("*-shared")
            if (d / "skills").is_dir()
        )
    for d in skills_dirs:
        if not d.is_dir():
            print(f"[check-skill-frontmatter] ディレクトリがない: {d}", file=sys.stderr)
            return 2
    if not skills_dirs:
        print("[check-skill-frontmatter] 検査対象が見つからない", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    skills: list[dict] = []
    per_family: list[tuple[pathlib.Path, dict]] = []
    for skills_dir in skills_dirs:
        family_skills = load_skills(skills_dir)
        if not family_skills:
            print(f"[check-skill-frontmatter] SKILL.md が見つからない: {skills_dir}", file=sys.stderr)
            return 2
        for s in family_skills:
            findings.extend(check_skill(s))
        # トリガ語の重複・外部名の衝突・初期一覧の予算は family をまたいで判定する
        # （利用者の環境では両方のプラグインが同時に入るため、family 内だけ見ても
        # 衝突や合計の超過を見逃す）
        skills.extend(family_skills)
        per_family.append((skills_dir, measure_aggregate(family_skills, skills_dir)))
    findings.extend(check_trigger_collisions(skills))
    findings.extend(check_external_name_collisions(skills))
    metrics = {
        "listings": {},
        "frontmatter_total": sum(m["frontmatter_total"] for _, m in per_family),
    }
    for _, m in per_family:
        for runtime, total in m["listings"].items():
            metrics["listings"][runtime] = metrics["listings"].get(runtime, 0) + total
    findings.extend(check_budget(metrics))

    if args.report:
        for skills_dir, m in per_family:
            print(f"# {skills_dir}  Skill {sum(1 for s in skills if str(skills_dir) in str(s['path']))} 個"
                  f" / frontmatter {m['frontmatter_total']} 文字"
                  f" / claude 一覧 {m['listings'].get('claude', 0)} 文字")
        print()
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
        for runtime, total in sorted(metrics["listings"].items()):
            print(f"{runtime} の初期一覧の合計: {total} 文字")
        print(f"frontmatter 合計: {metrics['frontmatter_total']} 文字 (上限 {FRONTMATTER_TOTAL_MAX})")
        return 0

    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]
    for f in sorted(findings, key=lambda x: (x.level != "error", x.skill, x.code)):
        print(str(f), file=sys.stderr if f.level == "error" else sys.stdout)

    print(f"\nSkill {len(skills)} 個を検査 — エラー {len(errors)} 件 / 警告 {len(warns)} 件")
    for runtime, total in sorted(metrics["listings"].items()):
        limit = {"claude": CLAUDE_LISTING_MAX, "codex": CODEX_LISTING_MAX}.get(runtime)
        print(f"{runtime} の初期一覧の合計: {total}" + (f" / {limit} 文字" if limit else " 文字"))
    print(f"frontmatter 合計: {metrics['frontmatter_total']} / {FRONTMATTER_TOTAL_MAX} 文字")

    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
