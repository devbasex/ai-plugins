"""他のプラグイン（宣言の release.plugin 以外で、前のタグからの差分にあるプラグイン）の上げ幅（#1752）。

- 候補: 版に含む PR のうちそのプラグインのパスに触れた PR の材料だけから、字面で決める（LLM を挟まない）。
  PR の閉じる語が指す課題の要求の「影響」の表の「公開インタフェース」の行に互換なしの印（`BREAKING_MARKS`）が
  あるか、PR 本文の `## 移行の手順` の箇条がプラグインの名前に語として触れれば MAJOR、無ければ PATCH。MINOR の
  候補は出さない（承認ゲート 2 で `--set <名前>=MINOR` として選ぶ）。材料を読めない PR は根拠の欄に書く
- 承認資料の `## 版を上げる他のプラグイン` の節の組み立て・読み取りと、本番の上げ幅の確かめ（`decided_versions`）

出す（emit）のと GitHub・git を読むのは呼び手（`release-steps.py changed-plugins`）である。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import closing
import gh_sections
import md
import mdtable
from release_lib.names import h2_lines, next_h2

OTHERS_HEADING = "## 版を上げる他のプラグイン"
CHANGES_HEADING = "## 利用者向けの変化"  # 配布の説明文（release-steps.py notes）の材料になる PR 本文の節
MIGRATION_HEADING = "## 移行の手順"  # PR 本文の節・承認資料の節（CHANGELOG の版の節の中では `### 移行の手順`）
CONSENT_HEADING = "## 同意を求めること"
CONSENT_LINE = "- [ ] 「版を上げる他のプラグイン」の表の上げ幅で、他のプラグインの版を上げる"
BREAKING_MARKS = ("互換なし", "互換の経路は持たない")  # 要求の「影響」の「公開インタフェース」の行に書く印
IMPACT, PUBLIC = "影響", "公開インタフェース"
ALREADY = "上げ済み"
LEVELS = {"major": "MAJOR", "minor": "MINOR", "patch": "PATCH"}
COLUMNS = ["プラグイン", "今の版", "上げ幅", "上げた後の版", "根拠"]
NO_MATERIAL = "材料に互換の無い変更の記述が無い"
INTRO = (
    "上げ幅は材料から機械で出した候補である。変えるときは `release-steps.py changed-plugins --approval <この資料> "
    "--set <名前>=<上げ幅>` で書き直してから承認する。"
)


@dataclass
class IssueMaterial:
    number: int
    public_interface: str | None = None
    unread: str | None = None  # 読めなかった理由


@dataclass
class PrMaterial:
    number: int
    files: list[str] | None  # マージのコミットの第 1 親との差分のパス。読めなければ None
    migration: list[str] = field(default_factory=list)
    issues: list[IssueMaterial] = field(default_factory=list)
    unread: str | None = None


@dataclass
class OtherPlugin:
    name: str
    current: str
    level: str  # MAJOR / MINOR / PATCH / 上げ済み
    to: str
    basis: list[str]


def bumped(current: str, level: str) -> str:
    """今の版を上げ幅で上げた正式版（接尾辞付きは基底から上げる）。読めなければ ValueError。"""
    import versions  # semver は版を上げる側（release-steps.py）だけが読む。PR 本文を書く supervise.py の経路で読み込まない

    return versions.next_version(versions.release_base(current), level)


def breaking(cell: str | None) -> str | None:
    """公開インタフェースの行の互換なしの印。無ければ None（「互換の無い削除はしない」のような否定の文は印でない）。"""
    return next((m for m in BREAKING_MARKS if m in (cell or "")), None)


def row_value(text: str, section: str, key: str) -> str | None:
    """本文の `## <section>` の節の表で、1 列目が key の行の 2 列目。無ければ None。"""
    sec = md.section_named(text or "", section, 2)
    if sec is None:
        return None
    for table in md.tables(text):
        if sec.start <= table.start < sec.end:
            for row in table.rows:
                if len(row) > 1 and row[0].strip() == key:
                    return " ".join(row[1].split())
    return None


def touches_plugin(files: list[str], name: str) -> bool:
    return any(f.startswith((f"plugins/{name}/", f"plugins/mcp/{name}/")) for f in files)


def mentions(text: str, name: str) -> bool:
    """text がプラグインの名前に語として触れるか（前後が英数字と `-` でない）。"""
    return re.search(rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9-])", text) is not None


def migration_items(body: str, n: int) -> list[str]:
    """PR 本文の `## 移行の手順` の箇条（`（#n）` つき）。節が無い・「無し」だけなら空。"""
    return gh_sections.section_items(body, MIGRATION_HEADING, n)


def gather_prs(prs, view_pr, view_issue, diff_files, skipped: list[int]) -> list[PrMaterial]:
    """版に含む PR の材料を読む。`view_pr(n)` は gh pr view の dict（読めなければ例外）、`view_issue(n)` は
    `(本文 | None, 読めない理由 | None)`、`diff_files(oid)` はマージのコミットの第 1 親との差分のパス（読めなければ None）。
    マージされていない PR は材料に入れず、番号を skipped へ足す。"""
    out = []
    for n in prs:
        try:
            d = view_pr(n)
        except Exception as e:  # noqa: BLE001  読めない PR は根拠の欄に書く（I4）
            out.append(PrMaterial(n, None, unread=str(e)[:200]))
            continue
        d = d if isinstance(d, dict) else {}
        if d.get("state", "MERGED") != "MERGED":
            skipped.append(n)
            continue
        body = d.get("body") or ""
        oid = (d.get("mergeCommit") or {}).get("oid")
        files = diff_files(oid) if oid else None
        why = None if files is not None else (f"マージのコミット {oid[:8]} の差分を読めない" if oid else "マージのコミットが無い")
        issues = []
        for m in closing.closing_numbers(body):
            text, unread = view_issue(m)
            issues.append(IssueMaterial(m, row_value(text, IMPACT, PUBLIC) if text is not None else None, unread))
        out.append(PrMaterial(n, files, migration_items(body, n), issues, why))
    return out


def candidate(name: str, current: str, prs: list[PrMaterial]) -> OtherPlugin:
    """材料から上げ幅の候補を決める（I3・I4）。"""
    basis: list[str] = []
    major = False
    for pr in prs:
        if pr.files is None:
            basis.append(f"#{pr.number} を読めない（{pr.unread or 'マージのコミットの差分'}）")
            continue
        if not touches_plugin(pr.files, name):
            continue
        for issue in pr.issues:
            if issue.unread:
                basis.append(f"#{pr.number} が閉じる #{issue.number} を読めない（{issue.unread}）")
            elif mark := breaking(issue.public_interface):
                major = True
                basis.append(f"#{pr.number} が閉じる #{issue.number} の{PUBLIC}: {mark}")
        if any(mentions(m, name) for m in pr.migration):
            major = True
            basis.append(f"#{pr.number} の移行の手順が {name} に触れる")
    level = "major" if major else "patch"
    return OtherPlugin(name, current, LEVELS[level], bumped(current, level), basis or [NO_MATERIAL])


def section_lines(rows: list[OtherPlugin]) -> list[str]:
    """承認資料の `## 版を上げる他のプラグイン` の節の中身の行。0 件なら「- 無し」。"""
    if not rows:
        return ["- 無し"]
    body = [[r.name, r.current, r.level, r.to, "<br>".join(r.basis)] for r in rows]
    return [INTRO, "", *mdtable.table_markdown(COLUMNS, body).splitlines()]


def read_section(text: str) -> list[OtherPlugin]:
    """承認資料の節を読む。節が無い・表の形が違えば ValueError。「- 無し」なら空。"""
    content = gh_sections.get_section(text or "", OTHERS_HEADING)
    if content is None:
        raise ValueError(f"承認資料に {OTHERS_HEADING} の節が無い（開発版の配布の others のステップが書く）")
    tables = md.tables(content)
    if not tables:
        if [line.strip() for line in content.splitlines() if line.strip()] == ["- 無し"]:
            return []
        raise ValueError(f"{OTHERS_HEADING} の節に表が無い")
    if [h.strip() for h in tables[0].header] != COLUMNS:
        raise ValueError(f"{OTHERS_HEADING} の表の列が違う（{' / '.join(COLUMNS)}）")
    out = []
    for row in tables[0].rows:
        cells = [c.strip() for c in row] + [""] * (len(COLUMNS) - len(row))
        out.append(OtherPlugin(cells[0], cells[1], cells[2], cells[3], [b.strip() for b in cells[4].split("<br>") if b.strip()]))
    return out


def set_level(rows: list[OtherPlugin], name: str, level: str) -> OtherPlugin:
    """承認ゲート 2 で決めた上げ幅へ行を書き換える。表に無い名前・上げ済みの行・知らない上げ幅は ValueError。"""
    row = next((r for r in rows if r.name == name), None)
    if row is None:
        raise ValueError(f"表に {name} の行が無い")
    if row.level == ALREADY:
        raise ValueError(f"{name} は前のタグから版が変わっている（{ALREADY}）。上げ幅を変えない")
    if level.lower() not in LEVELS:
        raise ValueError(f"上げ幅が違う: {level}（{' / '.join(LEVELS.values())}）")
    row.level, row.to = LEVELS[level.lower()], bumped(row.current, level)
    row.basis.insert(0, f"承認ゲート 2 で {row.level} に決めた")
    return row


def decided_versions(rows: list[OtherPlugin], pending: dict[str, str], done: dict[str, str] | None = None) -> dict[str, str]:
    """本番で上げる版（名前 → 版）。pending は差分にあってまだ上げていないプラグインの今の版、done は前のタグから
    版が変わったプラグインの HEAD の版。表の上げ済みでない行のうち HEAD の版が表の「上げた後の版」と同じものは、
    途中まで進んだ版上げで上げ終えた行として外す（再開できるように）。
    残りの行の集合が pending と合わない（I2）・上げ幅を読めない・版が合わない（I1）なら ValueError。"""
    done = done or {}
    table = {r.name: r for r in rows if r.level != ALREADY and not (r.name not in pending and done.get(r.name) == r.to)}
    missing, extra = sorted(pending.keys() - table.keys()), sorted(table.keys() - pending.keys())
    if missing or extra:
        raise ValueError(
            "承認資料の表と差分のプラグインが合わない"
            + (f"（表に無い: {', '.join(missing)}）" if missing else "")
            + (f"（差分に無い: {', '.join(extra)}）" if extra else "")
        )
    out = {}
    for name, old in sorted(pending.items()):
        r = table[name]
        level = next((k for k, v in LEVELS.items() if v == r.level), None)
        if level is None:
            raise ValueError(f"{name} の上げ幅を読めない: {r.level!r}")
        if r.current != old:
            raise ValueError(f"{name} の今の版が合わない（表 {r.current}・前のタグ {old}）")
        want = bumped(old, level)
        if r.to != want:
            raise ValueError(f"{name} の上げた後の版が合わない（表 {r.to}・{r.level} で上げた版 {want}）")
        out[name] = want
    return out


def put_section(lines: list[str], heading: str, block: list[str], replace: bool = True) -> None:
    """承認資料の heading の節を block にする。あれば replace のときだけ差し替え、無ければ `## 同意を求めること` の前へ置く。"""
    at = next((i for i in h2_lines(lines) if lines[i] == heading), None)
    if at is not None:
        if replace:
            lines[at + 1 : next_h2(lines, at)] = ["", *block, ""]
        return
    at = next((i for i in h2_lines(lines) if lines[i] == CONSENT_HEADING), len(lines))
    lines[at:at] = [heading, "", *block, ""]


def add_consent(lines: list[str]) -> bool:
    """`## 同意を求めること` の箇条の末尾へ同意の行を足す。既にある・節が無いときは足さない。"""
    if CONSENT_LINE in lines:
        return False
    at = next((i for i in h2_lines(lines) if lines[i] == CONSENT_HEADING), None)
    if at is None:
        return False
    end = next_h2(lines, at)
    last = max((i for i in range(at + 1, end) if lines[i].startswith("- ")), default=at + 1)
    lines[last + 1 : last + 1] = [CONSENT_LINE]
    return True
