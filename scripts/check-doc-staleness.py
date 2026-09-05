#!/usr/bin/env python3
"""説明文書に書かれた Skill 数と版数を、実体・マニフェスト・plugin.json と突き合わせる。

対象は利用者が読む 3 本の説明文書（`README.md` / `AGENTS.md` / `plugins/ndf/README.md`）
である。

配布する Skill の数はランタイムごとに違い、その数が `README.md` と `plugins/ndf/README.md`
に書かれている。数を機械的に突き合わせる検査はプラグインの定義ファイルにしか届いていな
かったため、版を上げるたびに説明文書の側へ古い数が残った。ここでは説明文書の側を突き合わせの
対象へ入れる。

版数も同じことが起きる。検査していたのは更新案内の見出し（`## v<版> へ更新するとき`）
だけで、概要・期待出力・キャッシュパスの例に書かれた版数は古いまま残った。周囲の固定の語で
位置を決めた 7 種類を突き合わせの対象へ入れる。

**すべての版数を現行版へ揃えるわけではない。** 変更履歴・履歴の説明・意図的に前の版を指す
記載は、前の版のまま残すのが正しい。位置を決めてから照合する形にしているため、それらは
最初から走査に入らない。

読み取れないこと自体も食い違いと同じく失敗として扱う。素通りさせると、記載を消すか
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
AGENTS_MD = "AGENTS.md"
PLUGIN_README = f"plugins/{FAMILY}/README.md"
PLUGIN_JSON = f"plugins/{FAMILY}/.claude-plugin/plugin.json"
SKILLS_DIR = f"plugins/{FAMILY}/skills"


def manifest_path(runtime: str) -> str:
    return f"plugins/{FAMILY}/manifests/{runtime}-skills.txt"


def plugin_json_path(name: str) -> str:
    return f"plugins/{name}/.claude-plugin/plugin.json"


# `README.md` はランタイムを Claude Code / Kiro / Codex / agy の順、`plugins/ndf/README.md` は
# Claude Code / Codex / Kiro CLI / agy の順で書いている。位置ではなく名前で対応づける。表記も
# 2 本で違う（`Kiro` と `Kiro CLI`）ため、対応表を文書ごとに持つ。**agy はどちらも同じ表記で
# ある。** コマンド名がそのまま配布先の名前であり、製品名で言い換えると読み手が結び付けられない。
ROOT_README_RUNTIMES = {"Claude Code": "claude", "Kiro": "kiro", "Codex": "codex", "agy": "agy"}
PLUGIN_README_RUNTIMES = {
    "Claude Code": "claude",
    "Codex": "codex",
    "Kiro CLI": "kiro",
    "agy": "agy",
}

RUNTIME_COUNT = re.compile(r"(Claude Code|Kiro|Codex|agy)向け core\s*(\d+)\s*個")
SOURCE_COUNT = re.compile(r"元Skills（\s*(\d+)\s*個\s*）")
CATEGORY_LINE = re.compile(r"^\s+-\s+(?P<label>.+?)\s+\((?P<count>\d+)\)\s*[:：]\s*(?P<names>.+)$")
TABLE_ROW = re.compile(r"^\|\s*(Claude Code|Codex|Kiro CLI|agy)\s*\|\s*(\d+)\s*個\s*\|")
LAYOUT_SKILLS = re.compile(r"唯一の実体（\s*(\d+)\s*個\s*）")
NAME_SEPARATOR = re.compile(r"[,、]")

# 版数の書式は `scripts/lib/version_pattern.py` が唯一の定義を持つ。定義ファイルの検査
# （`scripts/validate-runtime-plugins.sh`）も同じ場所から読む。ここへ書き写すと、書式を
# 変えたときに片方の検査だけが新しい書式を読める状態になる。
#
# `VERSION_VALUE` は突き合わせ先そのものの形を確かめる。`base_of` は数字 3 つに割れることを
# 前提にしており、`1.0` のような値が来ると例外で検査全体が止まる。読み取りの時点で弾き、
# 他の記載の判定を巻き添えにせず 1 件の食い違いとして出す。
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
try:
    from version_pattern import VERSION, VERSION_VALUE
except ImportError as exc:  # pragma: no cover - 読み込めないこと自体が検査の前提の崩れ
    raise SystemExit(
        f"版数の書式を読み込めない（scripts/lib/version_pattern.py）: {exc}"
    )

# F: 更新案内の見出し。版数の拾い方は `VERSION` へ揃える。数字 3 つだけで拾うと、接尾辞の
# 付いた版（`9.7.0-dev.1`）では見出しを読み落とし、接尾辞を外して書けば今度は古いと判定
# されるため、どちらの書き方でも検査を通せない。
UPGRADE_HEADING = re.compile(r"^##\s+v" + VERSION + r"\s+へ更新するとき\s*$", re.MULTILINE)

# --- 現行版を指す記載（G〜M）---
#
# 周囲の固定の語で位置を決めてから照合する。文書内の版数のうち現行版を指すのはこれだけで、
# 残りは変更履歴・履歴の説明であり、前の版のまま残すのが正しい。
OVERVIEW_VERSION = re.compile(r"\*\*NDFプラグイン v" + VERSION + r"\*\*")  # G
PLUGIN_TABLE_ROW = re.compile(r"^\|\s*\*\*(?P<name>[A-Za-z0-9_.-]+)\*\*\s*\|\s*" + VERSION + r"\s*\|")  # H
MAIN_PLUGIN_VERSION = re.compile(r"主要プラグインです（v" + VERSION + r"）")  # I
KIRO_AGENT_VERSION = re.compile(r"Kiro CLI用 / v" + VERSION + r"）")  # K
CODEX_CACHE_PATH = re.compile(r"plugins/cache/ai-plugins/" + FAMILY + r"/" + VERSION + r"/skills/")  # L
CODEX_LIST_OUTPUT = re.compile(FAMILY + r"@ai-plugins\s+installed, enabled\s+" + VERSION)  # M

# J: 区間の検査。この見出しから次の `### ` の直前までに並ぶ版数を、現行版の基底と比べる。
VERSION_SECTION_HEADING = "### 版の付け方と開発版の配布"
# 終端は自身と同じか上位の見出しで取る。`^###` だけで区切ると、次が `## ` のときに区間が
# 閉じず、そのまま変更履歴まで走査して前の版の版数を現行版と比べてしまう。
SECTION_HEADING = re.compile(r"^#{1,3}\s")
# 囲みの中の `# ` 始まりはシェルのコメントであって見出しではない。囲みを跨いで数えると、
# 節の途中の実行例で区間が切れる。
CODE_FENCE = re.compile(r"^\s*(?:```|~~~)")
# 囲みまで含めて位置を固定する。前後の 1 文字を塞ぐだけでは、空白で区切られた
# `codex-cli 0.146.1` の `0.146.1` が走査へ入り、現行版より小さい基底として誤検出になる。
# この節の版数はすべて `` `9.6.0` `` の形で書く（節の中の 10 箇所すべてが囲まれていることを
# 確認済み）。囲まずに書いた版数は走査に入らないため、例を足すときは囲みを付ける。
SECTION_VERSION = re.compile(r"`v?" + VERSION + r"`")


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
    """説明文書に書かれた 1 種類の値と、その突き合わせ先。

    「どのファイルのどの記載が、どの値と食い違ったか」を出力するために要るものを 1 つに
    まとめる。記載ごとに判定の書き方が分かれていると、失敗の出力の形も分かれてしまう。

    値は数（Skill の数）と文字列（版数）のどちらも取る。比べ方はどちらも等値である。
    """

    path: str
    """説明文書のパス。"""
    subject: str
    """記載の識別。「公開Skills の Claude Code の数」のように、読み手が本文中から探せる語句。"""
    wording: str
    """期待する書き方。読み取れなかったときに案内する。"""
    described: list[int] | list[str]
    """説明文書から読み取れた値。同じ記載が複数箇所にあれば並ぶ。"""
    expected: int | str | None
    """突き合わせ先の値。突き合わせる相手が無いときは None。"""
    source: str
    """突き合わせ先の名前。"""
    lines: list[int] | None = None
    """`described` と同じ並びの行番号。渡さなければ出力へ添えない。

    行番号を必須にしないのは、既存の数の検査 6 種類の出力を変えないためである。区間の検査は
    同じ節の複数の行を挙げうるため、そちらでは行番号が無いと直す場所が決まらない。
    """


def location_of(claim: Claim, index: int) -> str:
    """食い違った記載の行番号。持っていなければ空文字を返す。"""
    if not claim.lines or index >= len(claim.lines):
        return ""
    return f"（L{claim.lines[index]}）"


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
    for index, value in enumerate(claim.described):
        if value != claim.expected:
            report.add(
                claim.path,
                f"{claim.subject}が食い違う"
                f"（記載: {value}{location_of(claim, index)} / {claim.source}: {claim.expected}）",
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
    if not VERSION_VALUE.fullmatch(version):
        report.add_source(
            f"{PLUGIN_JSON} の version が `<major>.<minor>.<patch>` の形でない"
            f"（記載: {version}）"
        )
        return None
    return version


def named_plugin_version(root: Path, name: str) -> str | None:
    """一覧表の行に載る名前から、そのプラグインの版数を読む。

    突き合わせ先が無いことは呼び出し側が食い違いとして扱う。ここで報告しないのは、行に
    書かれた名前そのものを出力へ含めたいためである。
    """
    path = root / plugin_json_path(name)
    if not path.is_file():
        return None
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError:
        return None
    return version if isinstance(version, str) else None


def base_of(version: str) -> tuple[int, int, int]:
    """接尾辞を捨てた数字 3 つ。`9.6.0-dev.1` の基底は `(9, 6, 0)` になる。

    整数の組にするのは、桁数によらず順序を揃えるためである。文字列のままだと
    `"9.10.0" < "9.9.0"` が真になり、minor か patch が 10 に達した時点で順序を取り違える。

    渡る値が数字 3 つに割れることは呼び出し側が保証する。文書側の版数は `SECTION_VERSION`
    が、`plugin.json` の版数は `plugin_version` が形を確かめてから渡す。
    """
    major, minor, patch = version.split("-", 1)[0].split(".")
    return int(major), int(minor), int(patch)


def read_document(root: Path, relative: str, report: Report) -> str | None:
    path = root / relative
    if not path.is_file():
        report.add_source(f"{relative} が無い（検査の対象の説明文書）")
        return None
    return path.read_text(encoding="utf-8")


# --- 説明文書から数を読み取る ---


def numbers_of(pattern: re.Pattern[str], body: str) -> list[int]:
    return [int(value) for value in pattern.findall(body)]


def versions_of(pattern: re.Pattern[str], body: str) -> tuple[list[str], list[int]]:
    """版数と、その行番号。同じ書き方が複数箇所にあればすべて拾う。"""
    values: list[str] = []
    lines: list[int] = []
    for number, line in enumerate(body.splitlines(), 1):
        for found in pattern.finditer(line):
            values.append(found.group(1))
            lines.append(number)
    return values, lines


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


# --- 現行版を指す記載の検査（G〜M）---


def check_point_version(
    path: str,
    subject: str,
    wording: str,
    pattern: re.Pattern[str],
    body: str,
    version: str | None,
    report: Report,
) -> None:
    """周囲の固定の語で位置を決めた 1 種類の版数を、現行版と照合する。"""
    described, lines = versions_of(pattern, body)
    verify(
        Claim(
            path=path,
            subject=subject,
            wording=wording,
            described=described,
            expected=version,
            source=PLUGIN_JSON,
            lines=lines,
        ),
        report,
    )


def check_plugin_table(root: Path, body: str, report: Report) -> None:
    """プラグイン一覧表の版数を、行ごとにその名前の `plugin.json` と突き合わせる（H）。

    一覧表には NDF 以外のプラグインも並ぶ。行の名前から突き合わせ先を引くことで、表へ
    プラグインを足しても検査を書き換えずに済む。
    """
    rows: list[tuple[str, str, int]] = []
    for number, line in enumerate(body.splitlines(), 1):
        found = PLUGIN_TABLE_ROW.match(line)
        if found:
            rows.append((found.group("name"), found.group(2), number))
    if not any(name == FAMILY for name, _, _ in rows):
        report.add(
            ROOT_README,
            f"プラグイン一覧表の {FAMILY} の版数を読み取れない"
            f"（`| **{FAMILY}** | <版> | ... |` の形で書く。{PLUGIN_JSON} と突き合わせる）",
        )
    for name, value, number in rows:
        expected = named_plugin_version(root, name)
        if expected is None:
            report.add(
                ROOT_README,
                f"プラグイン一覧表の {name} の版数を突き合わせられない"
                f"（記載: {value}（L{number}） / {plugin_json_path(name)} が無い）",
            )
        elif value != expected:
            report.add(
                ROOT_README,
                f"プラグイン一覧表の {name} の版数が食い違う"
                f"（記載: {value}（L{number}） / {plugin_json_path(name)}: {expected}）",
            )


def check_version_section(body: str, version: str | None, report: Report) -> None:
    """「版の付け方と開発版の配布」節に並ぶ版数を、現行版の基底と比べる（J）。

    この節の版数は 1 つの値ではなく、現行版を基にした例の集まりである。現行版そのもの・
    接尾辞を付けたもの・次の版を指すものが混ざるため、点の照合ではなく区間の規則にする。
    節へ例を足しても検査を書き換えずに済み、版を上げた時点で前の版の例だけが残らない。

    **接尾辞は基底を取り出す時点で捨てる。** semver の順序では `9.6.0-dev.1` が `9.6.0`
    より小さいため、接尾辞まで見て比べると節の内容がそのまま失敗になる。接尾辞の
    付け忘れ・外し忘れをここでは見ない（`AGENTS.md` に書かれているとおりである）。

    **区間の終わりは、自身と同じか上位の見出しである。** 囲みの中は見出しとして数えない。

    **拾うのは `` `9.6.0` `` のように囲まれた版数だけである。** 節には配布に使う CLI の名前と
    版数を並べて書くことがあり、位置を固定しないと他のソフトの版数まで現行版と比べてしまう。
    """
    lines = body.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == VERSION_SECTION_HEADING),
        None,
    )
    values: list[str] = []
    numbers: list[int] = []
    if start is not None:
        in_fence = False
        for number, line in enumerate(lines[start + 1 :], start + 2):
            if CODE_FENCE.match(line):
                in_fence = not in_fence
            elif not in_fence and SECTION_HEADING.match(line):
                break
            for found in SECTION_VERSION.finditer(line):
                values.append(found.group(1))
                numbers.append(number)
    if not values:
        report.add(
            AGENTS_MD,
            "版の付け方の節の版数を読み取れない"
            f"（`{VERSION_SECTION_HEADING}` の節へ版数の例を囲みで置く。"
            f"{PLUGIN_JSON}: {version}）",
        )
        return
    if version is None:
        return
    current = base_of(version)
    for value, number in zip(values, numbers):
        if base_of(value) < current:
            report.add(
                AGENTS_MD,
                "版の付け方の節の版数が現行版より古い"
                f"（記載: {value}（L{number}） / {PLUGIN_JSON}: {version}）",
            )


def check_root_readme_versions(root: Path, body: str, version: str | None, report: Report) -> None:
    """`README.md` の概要の版数（G）とプラグイン一覧表の版数（H）を見る。"""
    check_point_version(
        ROOT_README,
        "概要の版数",
        "**NDFプラグイン v<版>**",
        OVERVIEW_VERSION,
        body,
        version,
        report,
    )
    check_plugin_table(root, body, report)


def check_agents_md(body: str, version: str | None, report: Report) -> None:
    """`AGENTS.md` の「主要プラグインです（v<版>）」（I）と版の付け方の節（J）を見る。"""
    check_point_version(
        AGENTS_MD,
        "「主要プラグインです（v<版>）」の版数",
        "主要プラグインです（v<版>）",
        MAIN_PLUGIN_VERSION,
        body,
        version,
        report,
    )
    check_version_section(body, version, report)


def check_plugin_readme_versions(body: str, version: str | None, report: Report) -> None:
    """`plugins/ndf/README.md` の Kiro の確認例（K）・キャッシュパス（L）・出力例（M）を見る。"""
    for subject, wording, pattern in (
        ("Kiro の確認例の版数", "（Kiro CLI用 / v<版>）", KIRO_AGENT_VERSION),
        (
            "Codex のキャッシュパスの例の版数",
            f"~/.codex/plugins/cache/ai-plugins/{FAMILY}/<版>/skills/...",
            CODEX_CACHE_PATH,
        ),
        (
            "`codex plugin list` の出力例の版数",
            f"{FAMILY}@ai-plugins  installed, enabled  <版>",
            CODEX_LIST_OUTPUT,
        ),
    ):
        check_point_version(PLUGIN_README, subject, wording, pattern, body, version, report)


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
    verify(
        Claim(
            path=PLUGIN_README,
            subject="レイアウト図の数",
            wording="唯一の実体（<数> 個）",
            described=numbers_of(LAYOUT_SKILLS, body),
            expected=skills,
            source=f"{SKILLS_DIR}/ の実体",
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
        for runtime in ("claude", "codex", "kiro", "agy")
    }
    skills = skill_dir_count(root, SKILLS_DIR, report)
    version = plugin_version(root, report)

    # 配らない Skill の置き場所（`optional-skills/`）は v10.5.0 で無くなった（#116）。
    # 元 Skill の数は `skills/` の実体だけで決まる。
    total = skills
    source = f"{SKILLS_DIR}/ の実体 {skills}"

    root_body = read_document(root, ROOT_README, report)
    if root_body is not None:
        check_root_readme(root_body, counts, total, source, report)
        check_root_readme_versions(root, root_body, version, report)

    agents_body = read_document(root, AGENTS_MD, report)
    if agents_body is not None:
        check_agents_md(agents_body, version, report)

    plugin_body = read_document(root, PLUGIN_README, report)
    if plugin_body is not None:
        check_plugin_readme(plugin_body, counts, skills, version, report)
        check_plugin_readme_versions(plugin_body, version, report)

    if report.errors:
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("documented skill counts and versions are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
