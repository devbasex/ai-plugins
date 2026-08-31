#!/usr/bin/env python3
"""説明文書に書かれた Skill 数と版数を、実体・マニフェスト・plugin.json と突き合わせる。

配布する Skill の数はランタイムごとに違い、その数が利用者の読む 2 本の説明文書
（`README.md` と `plugins/ndf/README.md`）に書かれている。数を機械的に突き合わせる検査は
プラグインの定義ファイルにしか届いていなかったため、版を上げるたびに説明文書の側へ古い数が
残った。ここでは説明文書の側を突き合わせの対象へ入れる。

読み取れないこと自体も食い違いと同じく失敗として扱う。素通りさせると、数の記載を消すか
書式を変えるだけでこの検査を無効化できてしまう。

`scripts/validate-runtime-plugins.sh` から呼ばれる。単独でも実行できる。

    python3 scripts/check-doc-staleness.py --root .
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 説明文書に数と版数を書いているのは NDF プラグインだけである。他の family が同じ書き方を
# 始めたときに対象を広げる。
FAMILY = "ndf"
ROOT_README = "README.md"
PLUGIN_README = f"plugins/{FAMILY}/README.md"
PLUGIN_JSON = f"plugins/{FAMILY}/.claude-plugin/plugin.json"
SKILLS_DIR = f"plugins/{FAMILY}/skills"
OPTIONAL_DIR = f"plugins/{FAMILY}/optional-skills"


def manifest_path(runtime: str) -> str:
    return f"plugins/{FAMILY}/manifests/{runtime}-skills.txt"


# `README.md` はランタイムを Claude Code / Kiro / Codex の順、`plugins/ndf/README.md` は
# Claude Code / Codex / Kiro の順で書いている。位置ではなく名前で対応づける。表記も
# 2 本で違う（`Kiro` と `Kiro CLI`）ため、対応表を文書ごとに持つ。
ROOT_README_RUNTIMES = {"Claude Code": "claude", "Kiro": "kiro", "Codex": "codex"}
PLUGIN_README_RUNTIMES = {"Claude Code": "claude", "Codex": "codex", "Kiro CLI": "kiro"}

RUNTIME_COUNT = re.compile(r"(Claude Code|Kiro|Codex)向け core\s*(\d+)\s*個")
SOURCE_COUNT = re.compile(r"元Skills（\s*(\d+)\s*個\s*）")
CATEGORY_LINE = re.compile(r"^\s+-\s+(?P<label>.+?)\s+\((?P<count>\d+)\)\s*[:：]\s*(?P<names>.+)$")
TABLE_ROW = re.compile(r"^\|\s*(Claude Code|Codex|Kiro CLI)\s*\|\s*(\d+)\s*個\s*\|")
LAYOUT_SKILLS = re.compile(r"唯一の実体（\s*(\d+)\s*個\s*）")
LAYOUT_OPTIONAL = re.compile(r"どの配布先にも載せない Skill（\s*(\d+)\s*個\s*）")
UPGRADE_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s+へ更新するとき\s*$", re.MULTILINE)
NAME_SEPARATOR = re.compile(r"[,、]")


@dataclass
class Report:
    """食い違いを集めて、最後にまとめて出す。

    最初の 1 件で止めると、版を上げたときに残った古い記載を 1 つずつしか直せない。
    """

    errors: list[str] = field(default_factory=list)

    def add(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def add_source(self, message: str) -> None:
        """突き合わせ先そのものが欠けていることを記録する。"""
        self.errors.append(message)


@dataclass(frozen=True)
class Claim:
    """説明文書に書かれた 1 種類の数と、その突き合わせ先。

    「どのファイルのどの記載が、どの値と食い違ったか」を出力するために要る 5 つを 1 つに
    まとめる。記載ごとに判定の書き方が分かれていると、失敗の出力の形も分かれてしまう。
    """

    path: str
    """説明文書のパス。"""
    subject: str
    """記載の識別。「公開Skills の Claude Code の数」のように、読み手が本文中から探せる語句。"""
    wording: str
    """期待する書き方。読み取れなかったときに案内する。"""
    described: list[int]
    """説明文書から読み取れた数。同じ記載が複数箇所にあれば並ぶ。"""
    expected: int | None
    """突き合わせ先の値。数える相手が無いときは None。"""
    source: str
    """突き合わせ先の名前。"""


def verify(claim: Claim, report: Report) -> None:
    """記載が無いことと、値が食い違うことの両方を失敗として扱う。"""
    if not claim.described:
        report.add(
            claim.path,
            f"{claim.subject}を読み取れない"
            f"（`{claim.wording}` の形で書く。{claim.source}: {claim.expected}）",
        )
        return
    if claim.expected is None:
        return
    for value in claim.described:
        if value != claim.expected:
            report.add(
                claim.path,
                f"{claim.subject}が食い違う"
                f"（記載: {value} / {claim.source}: {claim.expected}）",
            )


# --- 突き合わせ先を数える ---


def manifest_skill_count(root: Path, runtime: str, report: Report) -> int | None:
    """ランタイムへ配る Skill の数。コメントと空行は数えない。

    数え方は `scripts/validate-runtime-plugins.sh` の `manifest_skill_count` と同じにする。
    """
    manifest = root / manifest_path(runtime)
    if not manifest.is_file():
        report.add_source(f"{manifest_path(runtime)} が無い（{runtime} の配布 Skill 数を数える相手）")
        return None
    return sum(
        1
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    )


def skill_dir_count(root: Path, relative: str, report: Report) -> int | None:
    """`SKILL.md` を持つディレクトリの数。`README.md` などのファイルは数えない。"""
    directory = root / relative
    if not directory.is_dir():
        report.add_source(f"{relative} が無い（Skill の実体を数える相手）")
        return None
    return sum(1 for child in directory.iterdir() if (child / "SKILL.md").is_file())


def plugin_version(root: Path, report: Report) -> str | None:
    path = root / PLUGIN_JSON
    if not path.is_file():
        report.add_source(f"{PLUGIN_JSON} が無い（版数を突き合わせる相手）")
        return None
    version = json.loads(path.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str):
        report.add_source(f"{PLUGIN_JSON} に version がない")
        return None
    return version


def read_document(root: Path, relative: str, report: Report) -> str | None:
    path = root / relative
    if not path.is_file():
        report.add_source(f"{relative} が無い（検査の対象の説明文書）")
        return None
    return path.read_text(encoding="utf-8")


# --- 説明文書から数を読み取る ---


def numbers_of(pattern: re.Pattern[str], body: str) -> list[int]:
    return [int(value) for value in pattern.findall(body)]


def labelled_numbers(pattern: re.Pattern[str], body: str, labels: dict[str, str]) -> dict[str, list[int]]:
    """ランタイム名で対応づけた数。同じ書き方が複数箇所にあればすべて拾う。"""
    found: dict[str, list[int]] = {label: [] for label in labels}
    for line in body.splitlines():
        for label, value in pattern.findall(line):
            found[label].append(int(value))
    return found


def category_lines(body: str) -> list[re.Match[str]] | None:
    """元 Skill 数の行に続くカテゴリ内訳を、途切れるまで拾う。

    位置で拾うのは、`README.md` の別の箇所にある同じ形の箇条書きを巻き込まないためである。
    """
    lines = body.splitlines()
    anchor = next((i for i, line in enumerate(lines) if SOURCE_COUNT.search(line)), None)
    if anchor is None:
        return None
    matched: list[re.Match[str]] = []
    for line in lines[anchor + 1 :]:
        found = CATEGORY_LINE.match(line)
        if not found:
            break
        matched.append(found)
    return matched


# --- 説明文書ごとの検査 ---


def check_root_readme(
    body: str, counts: dict[str, int | None], total: int | None, source: str, report: Report
) -> None:
    """`README.md` のランタイム別の数（A）・元 Skill 数（B）・カテゴリ内訳（C）を見る。"""
    found = labelled_numbers(RUNTIME_COUNT, body, ROOT_README_RUNTIMES)
    for label, runtime in ROOT_README_RUNTIMES.items():
        verify(
            Claim(
                path=ROOT_README,
                subject=f"公開Skills の {label} の数",
                wording=f"{label}向け core <数>個",
                described=found[label],
                expected=counts.get(runtime),
                source=manifest_path(runtime),
            ),
            report,
        )
    verify(
        Claim(
            path=ROOT_README,
            subject="元Skills の数",
            wording="元Skills（<数>個）",
            described=numbers_of(SOURCE_COUNT, body),
            expected=total,
            source=source,
        ),
        report,
    )
    check_category_breakdown(body, total, source, report)


def check_category_breakdown(body: str, total: int | None, source: str, report: Report) -> None:
    """カテゴリ内訳の合計と、1 行ごとの宣言と並ぶ Skill 名の数を突き合わせる（C）。"""
    matched = category_lines(body)
    if matched is None:
        matched = []
    for found in matched:
        label = found.group("label")
        declared = int(found.group("count"))
        listed = [name for name in NAME_SEPARATOR.split(found.group("names")) if name.strip()]
        if declared != len(listed):
            report.add(
                ROOT_README,
                f"カテゴリ内訳「{label}」の数が、並ぶ Skill 名の数と食い違う"
                f"（宣言: {declared} / 並ぶ名前: {len(listed)}）",
            )
    verify(
        Claim(
            path=ROOT_README,
            subject="元Skills のカテゴリ内訳の合計",
            wording="  - <分類> (<数>): <Skill 名>, ...（元Skills の行の直後に並べる）",
            described=[sum(int(found.group("count")) for found in matched)] if matched else [],
            expected=total,
            source=source,
        ),
        report,
    )


def check_plugin_readme(
    body: str,
    counts: dict[str, int | None],
    skills: int | None,
    optional: int | None,
    version: str | None,
    report: Report,
) -> None:
    """`plugins/ndf/README.md` の配布先の表（D）・レイアウト図（E）・更新案内（F）を見る。"""
    found = labelled_numbers(TABLE_ROW, body, PLUGIN_README_RUNTIMES)
    for label, runtime in PLUGIN_README_RUNTIMES.items():
        verify(
            Claim(
                path=PLUGIN_README,
                subject=f"配布先の表の {label} の数",
                wording=f"| {label} | <数> 個 | ... |",
                described=found[label],
                expected=counts.get(runtime),
                source=manifest_path(runtime),
            ),
            report,
        )
    for pattern, expected, wording, source in (
        (LAYOUT_SKILLS, skills, "唯一の実体（<数> 個）", f"{SKILLS_DIR}/ の実体"),
        (LAYOUT_OPTIONAL, optional, "どの配布先にも載せない Skill（<数> 個）", f"{OPTIONAL_DIR}/ の実体"),
    ):
        verify(
            Claim(
                path=PLUGIN_README,
                subject="レイアウト図の数",
                wording=wording,
                described=numbers_of(pattern, body),
                expected=expected,
                source=source,
            ),
            report,
        )
    check_upgrade_heading(body, version, report)


def check_upgrade_heading(body: str, version: str | None, report: Report) -> None:
    """更新案内の見出しの版数を `plugin.json` の版と突き合わせる（F）。

    本文がその版の変更内容を説明しているかは機械では決められない。ここで見るのは見出しの
    版数だけで、版を上げたときに必ずこの節へ触る状態を作ることを目的とする。本文を読み直す
    機会は `docs/plugin-development-guide.md` のバージョン管理の手順が作る。
    """
    headings = UPGRADE_HEADING.findall(body)
    if not headings:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しが無い（`## v<版> へ更新するとき` の形で書く。{PLUGIN_JSON}: v{version}）",
        )
        return
    if len(headings) > 1:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しが {len(headings)} 個ある（v{' / v'.join(headings)}）。"
            "この節は現行の版の 1 つだけにする",
        )
        return
    if version is not None and headings[0] != version:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しの版数が古い（見出し: v{headings[0]} / {PLUGIN_JSON}: v{version}）",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="リポジトリの根（既定: カレントディレクトリ）")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    report = Report()
    counts = {
        runtime: manifest_skill_count(root, runtime, report)
        for runtime in ("claude", "codex", "kiro")
    }
    skills = skill_dir_count(root, SKILLS_DIR, report)
    optional = skill_dir_count(root, OPTIONAL_DIR, report)
    version = plugin_version(root, report)

    total = None if skills is None or optional is None else skills + optional
    source = f"{SKILLS_DIR}/ の実体 {skills} + {OPTIONAL_DIR}/ の {optional} = {total}"

    root_body = read_document(root, ROOT_README, report)
    if root_body is not None:
        check_root_readme(root_body, counts, total, source, report)

    plugin_body = read_document(root, PLUGIN_README, report)
    if plugin_body is not None:
        check_plugin_readme(plugin_body, counts, skills, optional, version, report)

    if report.errors:
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("documented skill counts and version headings are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
