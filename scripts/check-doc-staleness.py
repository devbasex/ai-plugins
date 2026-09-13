#!/usr/bin/env python3
"""説明文書に書かれた Skill 数と版数を、実体・マニフェスト・plugin.json と突き合わせる。

対象は利用者が読む 4 本の説明文書（`README.md` / `AGENTS.md` /
`docs/versioning-and-distribution.md` / `plugins/ndf/README.md`）である。

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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# 説明文書に数と版数を書いているのは NDF プラグインだけである。他の family が同じ書き方を
# 始めたときに対象を広げる。
FAMILY = "ndf"
ROOT_README = "README.md"
AGENTS_MD = "AGENTS.md"
# 版数と配布の扱いの正本（#499）。版の付け方の章（J）はここにある。
VERSIONING_MD = "docs/versioning-and-distribution.md"
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

# J: 区間の検査。正本のこの見出しから次の同位以上の見出しの直前までに並ぶ版数を、
# 現行版の基底と比べる。
VERSION_SECTION_HEADING = "## 版の付け方と開発版の配布"
# 終端は自身と同じか上位の見出しで取り、深さは位置決めの見出しから導く。同じ深さだけで
# 区切ると、次が上位の見出しのときに区間が閉じず、後ろの章に並ぶ前の版の版数まで現行版と
# 比べてしまう。深さを固定すると、位置決めの見出しの深さを変えたときに規則から外れる
# （`## ` の章を固定の 3 段で閉じると、章の中の `### ` 小見出しで区間が切れる）。
_SECTION_DEPTH = len(VERSION_SECTION_HEADING) - len(VERSION_SECTION_HEADING.lstrip("#"))
SECTION_HEADING = re.compile(r"^#{1,%d}\s" % _SECTION_DEPTH)
# 囲みの中の `# ` 始まりはシェルのコメントであって見出しではない。囲みを跨いで数えると、
# 節の途中の実行例で区間が切れる。
CODE_FENCE = re.compile(r"^\s*(?:```|~~~)")
# 囲みまで含めて位置を固定する。前後の 1 文字を塞ぐだけでは、空白で区切られた
# `codex-cli 0.146.1` の `0.146.1` が走査へ入り、現行版より小さい基底として誤検出になる。
# この章の版数はすべて `` `9.6.0` `` の形で書く（正本へ移した時点の章の中の 10 箇所すべてが
# 囲まれていることを確認済み）。囲まずに書いた版数は走査に入らないため、例を足すときは囲みを付ける。
SECTION_VERSION = re.compile(r"`v?" + VERSION + r"`")

# J の続き: 章 2 の中で、次に出す版を指すはずの例（#566）。版数を一括で置換すると、
# 次の版を指す例が現行版を指す形へ崩れる。崩れた値は現行版そのものと等しいことがあり、
# 値だけでは正式版を指す正しい例と区別できない。値が何を指すはずかは周囲の語で決める。
#
# 比べる相手は現行版ではなく同じ章の例である。`develop` の版は接尾辞付きになりうるため、
# 現行版との一致を求めると開発版の配布のたびに正しい例が落ちる。正式版の行を現行版へ結び
# 付けるのは、上の区間の規則（基底が現行版より小さければ落ちる）が持つ。
VERSION_FORM_ROW = re.compile(
    r"^\|\s*(?P<label>正式版|開発版|公開前の確認版)\s*\|\s*`v?" + VERSION + r"`\s*\|"
)
NEXT_DEVELOPMENT = re.compile(r"`v?" + VERSION + r"`\s*の次を開発するなら\s*`v?" + VERSION + r"`")
# 表の行の語ごとに、版数が終わるべき接尾辞の形。`None` は接尾辞を持たないことを求める。
VERSION_FORM_SUFFIX: dict[str, tuple[re.Pattern[str] | None, str]] = {
    "正式版": (None, "接尾辞なし"),
    "開発版": (re.compile(r"-dev\.\d+$"), "-dev.<連番>"),
    "公開前の確認版": (re.compile(r"-rc\.\d+$"), "-rc.<連番>"),
}
DEV_SUFFIX = VERSION_FORM_SUFFIX["開発版"][0]


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


@dataclass(frozen=True)
class PointVersionSpec:
    """周囲の固定の語で位置を決める版数記載の定義。"""

    path: str
    subject: str
    wording: str
    pattern: re.Pattern[str]


# 点で照合する版数記載（G・I・K・L・M）の一覧。点の照合を足すときはここへ 1 行足す。
# 同じ文書の中では並びの順に報告する。
POINT_VERSION_SPECS: list[PointVersionSpec] = [
    PointVersionSpec(ROOT_README, "概要の版数", "**NDFプラグイン v<版>**", OVERVIEW_VERSION),  # G
    PointVersionSpec(
        AGENTS_MD,
        "「主要プラグインです（v<版>）」の版数",
        "主要プラグインです（v<版>）",
        MAIN_PLUGIN_VERSION,
    ),  # I
    PointVersionSpec(PLUGIN_README, "Kiro の確認例の版数", "（Kiro CLI用 / v<版>）", KIRO_AGENT_VERSION),  # K
    PointVersionSpec(
        PLUGIN_README,
        "Codex のキャッシュパスの例の版数",
        f"~/.codex/plugins/cache/ai-plugins/{FAMILY}/<版>/skills/...",
        CODEX_CACHE_PATH,
    ),  # L
    PointVersionSpec(
        PLUGIN_README,
        "`codex plugin list` の出力例の版数",
        f"{FAMILY}@ai-plugins  installed, enabled  <版>",
        CODEX_LIST_OUTPUT,
    ),  # M
]


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
    spec: PointVersionSpec,
    body: str,
    version: str | None,
    report: Report,
) -> None:
    """周囲の固定の語で位置を決めた 1 種類の版数を、現行版と照合する。"""
    described, lines = versions_of(spec.pattern, body)
    verify(
        Claim(
            path=spec.path,
            subject=spec.subject,
            wording=spec.wording,
            described=described,
            expected=version,
            source=PLUGIN_JSON,
            lines=lines,
        ),
        report,
    )


def check_point_versions(path: str, body: str, version: str | None, report: Report) -> None:
    """`POINT_VERSION_SPECS` のうち、その文書に書かれる記載をすべて照合する。"""
    for spec in POINT_VERSION_SPECS:
        if spec.path == path:
            check_point_version(spec, body, version, report)


def parse_plugin_table_rows(body: str) -> list[tuple[str, str, int]]:
    """プラグイン一覧表の行を、名前・記載の版数・行番号の組として拾う。"""
    rows: list[tuple[str, str, int]] = []
    for number, line in enumerate(body.splitlines(), 1):
        found = PLUGIN_TABLE_ROW.match(line)
        if found:
            rows.append((found.group("name"), found.group(2), number))
    return rows


def compare_plugin_table_row(root: Path, name: str, value: str, number: int, report: Report) -> None:
    """一覧表の 1 行の版数を、その名前の `plugin.json` と突き合わせる。"""
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


def check_plugin_table(root: Path, body: str, report: Report) -> None:
    """プラグイン一覧表の版数を、行ごとにその名前の `plugin.json` と突き合わせる（H）。

    一覧表には NDF 以外のプラグインも並ぶ。行の名前から突き合わせ先を引くことで、表へ
    プラグインを足しても検査を書き換えずに済む。
    """
    rows = parse_plugin_table_rows(body)
    if not any(name == FAMILY for name, _, _ in rows):
        report.add(
            ROOT_README,
            f"プラグイン一覧表の {FAMILY} の版数を読み取れない"
            f"（`| **{FAMILY}** | <版> | ... |` の形で書く。{PLUGIN_JSON} と突き合わせる）",
        )
    for name, value, number in rows:
        compare_plugin_table_row(root, name, value, number, report)


def section_lines(lines: list[str]) -> list[tuple[int, str, bool]] | None:
    """「版の付け方と開発版の配布」章の行を、行番号と囲みの中かどうかを付けて返す。

    見出しを見つけ、次の同位以上の見出しの直前までを返す。区間の終わりは自身と同じか
    上位の見出しであり、囲みの中は見出しとして数えない。囲みの開始と終了の行そのものも
    囲みの中として扱う。見出しが無ければ `None` を返す。
    """
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == VERSION_SECTION_HEADING),
        None,
    )
    if start is None:
        return None
    section: list[tuple[int, str, bool]] = []
    in_fence = False
    for number, line in enumerate(lines[start + 1 :], start + 2):
        if CODE_FENCE.match(line):
            in_fence = not in_fence
            section.append((number, line, True))
            continue
        if not in_fence and SECTION_HEADING.match(line):
            break
        section.append((number, line, in_fence))
    return section


def scan_section_versions(lines: list[str]) -> tuple[list[str], list[int]]:
    """「版の付け方と開発版の配布」章に囲みで並ぶ版数と、その行番号を拾う。

    章の区間は `section_lines` が決める。拾うのは `` `9.6.0` `` のように囲まれた版数だけで、
    コードの囲みの中の行も拾う。見出しが無ければ空を返す。
    """
    values: list[str] = []
    line_numbers: list[int] = []
    for number, line, _ in section_lines(lines) or []:
        for found in SECTION_VERSION.finditer(line):
            values.append(found.group(1))
            line_numbers.append(number)
    return values, line_numbers


def check_version_section(body: str, version: str | None, report: Report) -> bool:
    """正本の「版の付け方と開発版の配布」章に並ぶ版数を、現行版の基底と比べる（J）。

    この節の版数は 1 つの値ではなく、現行版を基にした例の集まりである。現行版そのもの・
    接尾辞を付けたもの・次の版を指すものが混ざるため、点の照合ではなく区間の規則にする。
    節へ例を足しても検査を書き換えずに済み、版を上げた時点で前の版の例だけが残らない。

    **接尾辞は基底を取り出す時点で捨てる。** semver の順序では `9.6.0-dev.1` が `9.6.0`
    より小さいため、接尾辞まで見て比べると節の内容がそのまま失敗になる。接尾辞の
    付け忘れ・外し忘れをここでは見ない（正本の「検査に載らず手で直す箇所」に書かれているとおりである）。

    節の走査（見出しの探索・囲みの追跡・囲まれた版数の収集）は `scan_section_versions` が担う。
    ここでは読み取れないことの報告と、現行版の基底との比較だけを行う。

    節の版数を読み取れたかを返す。読み取れなければ、例どうしの比較（`check_version_examples`）
    は同じ原因の報告を重ねるだけになる。
    """
    values, line_numbers = scan_section_versions(body.splitlines())
    if not values:
        report.add(
            VERSIONING_MD,
            "版の付け方の節の版数を読み取れない"
            f"（`{VERSION_SECTION_HEADING}` の節へ版数の例を囲みで置く。"
            f"{PLUGIN_JSON}: {version}）",
        )
        return False
    if version is None:
        return True
    current = base_of(version)
    for value, number in zip(values, line_numbers):
        if base_of(value) < current:
            report.add(
                VERSIONING_MD,
                "版の付け方の節の版数が現行版より古い"
                f"（記載: {value}（L{number}） / {PLUGIN_JSON}: {version}）",
            )
    return True


def check_version_examples(body: str, report: Report) -> None:
    """章 2 の版の形の表と次の開発の例を、例どうしで比べる（J の続き。#566）。

    見るのは囲みの外の行だけである。囲みの中の表や文は実行例か出力例で、章の例そのもの
    ではない。数えると、実行例を足しただけで「同じ行が複数ある」に当たる。

    位置を決める語（表の 1 列目の 3 語と「の次を開発するなら」）が見つからないときも、
    重なるときも失敗にする。黙って通すと、行を言い換えるだけで規則が外れる。
    """
    rows: dict[str, list[tuple[str, int]]] = {label: [] for label in VERSION_FORM_SUFFIX}
    examples: list[tuple[str, str, int]] = []
    for number, line, in_fence in section_lines(body.splitlines()) or []:
        if in_fence:
            continue
        row = VERSION_FORM_ROW.match(line)
        if row:
            rows[row.group("label")].append((row.group(2), number))
        for found in NEXT_DEVELOPMENT.finditer(line):
            examples.append((found.group(1), found.group(2), number))

    for label, found_rows in rows.items():
        if not found_rows:
            report.add(
                VERSIONING_MD,
                f"版の付け方の節の版の形の表を読み取れない（無い行: {label}。"
                f"| {label} | `<版>` | ... | の形で書く）",
            )
        elif len(found_rows) > 1:
            numbers = ", ".join(f"L{number}" for _, number in found_rows)
            report.add(VERSIONING_MD, f"版の付け方の節の版の形の表に同じ行が複数ある（{label}: {numbers}）")

    for label, found_rows in rows.items():
        suffix, wording = VERSION_FORM_SUFFIX[label]
        for value, number in found_rows:
            if ("-" in value) if suffix is None else not suffix.search(value):
                report.add(
                    VERSIONING_MD,
                    f"版の付け方の節の{label}の行の接尾辞が違う"
                    f"（記載: {value}（L{number}） / 求める形: {wording}）",
                )

    # 比べる相手が 1 つに決まるときだけ、正式版の行より新しい基底を指すかを見る。
    if len(rows["正式版"]) == 1:
        stable, stable_number = rows["正式版"][0]
        for label in ("開発版", "公開前の確認版"):
            for value, number in rows[label]:
                if base_of(value) <= base_of(stable):
                    report.add(
                        VERSIONING_MD,
                        f"版の付け方の節の{label}の行が正式版の行より新しい版を指していない"
                        f"（記載: {value}（L{number}） / 正式版: {stable}（L{stable_number}））",
                    )

    if not examples:
        report.add(
            VERSIONING_MD,
            "版の付け方の節の次の開発の例を読み取れない"
            "（`<版>` の次を開発するなら `<版>-dev.<連番>` の形で書く）",
        )
    for left, right, number in examples:
        if base_of(right) <= base_of(left) or not DEV_SUFFIX.search(right):
            report.add(
                VERSIONING_MD,
                f"版の付け方の節の次の開発の例が次の版を指していない（記載: {left} → {right}（L{number}））",
            )


def check_root_readme_versions(root: Path, body: str, report: Report) -> None:
    """`README.md` のプラグイン一覧表の版数（H）を見る。概要の版数（G）は `POINT_VERSION_SPECS` が持つ。"""
    check_plugin_table(root, body, report)


# --- 説明文書ごとの検査 ---


def check_runtime_counts(
    path: str,
    described_by_label: dict[str, list[int]],
    labels: dict[str, str],
    counts: dict[str, int | None],
    subject_fmt: str,
    wording_fmt: str,
    source_of: Callable[[str], str],
    report: Report,
) -> None:
    """ランタイム別の Skill 数を突き合わせる。"""
    for label, runtime in labels.items():
        verify(
            Claim(
                path=path,
                subject=subject_fmt.format(label, label=label),
                wording=wording_fmt.format(label, label=label),
                described=described_by_label[label],
                expected=counts.get(runtime),
                source=source_of(runtime),
            ),
            report,
        )


def check_root_readme(
    body: str, counts: dict[str, int | None], total: int | None, source: str, report: Report
) -> None:
    """`README.md` のランタイム別の数（A）・元 Skill 数（B）・カテゴリ内訳（C）を見る。"""
    found = labelled_numbers(RUNTIME_COUNT, body, ROOT_README_RUNTIMES)
    check_runtime_counts(
        ROOT_README,
        found,
        ROOT_README_RUNTIMES,
        counts,
        "公開Skills の {label} の数",
        "{label}向け core <数>個",
        manifest_path,
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
    check_runtime_counts(
        PLUGIN_README,
        found,
        PLUGIN_README_RUNTIMES,
        counts,
        "配布先の表の {label} の数",
        "| {label} | <数> 個 | ... |",
        manifest_path,
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
    機会は `docs/versioning-and-distribution.md` の「検査に載らず手で直す箇所」が作る。
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


@dataclass(frozen=True)
class Sources:
    """突き合わせ先として集めた値。説明文書の記載はこれと比べる。"""

    counts: dict[str, int | None]
    skills: int | None
    version: str | None
    total: int | None
    source: str


def collect_sources(root: Path, report: Report) -> Sources:
    """配布 Skill 数・実体 Skill 数・plugin 版数など、突き合わせ先の値を集める。"""
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
    return Sources(counts=counts, skills=skills, version=version, total=total, source=source)


def check_root_readme_document(root: Path, sources: Sources, report: Report) -> None:
    """`README.md` を読み、数（A〜C）・点の版数（G）・一覧表の版数（H）を検査する。"""
    body = read_document(root, ROOT_README, report)
    if body is None:
        return
    check_root_readme(body, sources.counts, sources.total, sources.source, report)
    check_point_versions(ROOT_README, body, sources.version, report)
    check_root_readme_versions(root, body, report)


def check_agents_document(root: Path, sources: Sources, report: Report) -> None:
    """`AGENTS.md` を読み、点の版数（I）を検査する。"""
    body = read_document(root, AGENTS_MD, report)
    if body is None:
        return
    check_point_versions(AGENTS_MD, body, sources.version, report)


def check_versioning_document(root: Path, sources: Sources, report: Report) -> None:
    """版数正本を読み、版の付け方の章（J）を検査する。

    検査 I（`AGENTS.md`）と検査 J（正本）は別の文書を読む。本文を共有すると、正本の記載が
    古いことを `AGENTS.md` の失敗として報告してしまう。
    """
    body = read_document(root, VERSIONING_MD, report)
    if body is not None and check_version_section(body, sources.version, report):
        check_version_examples(body, report)


def check_plugin_readme_document(root: Path, sources: Sources, report: Report) -> None:
    """`plugins/ndf/README.md` を読み、配布先の表（D）・レイアウト図（E）・更新案内（F）・点の版数（K〜M）を検査する。"""
    body = read_document(root, PLUGIN_README, report)
    if body is None:
        return
    check_plugin_readme(body, sources.counts, sources.skills, sources.version, report)
    check_point_versions(PLUGIN_README, body, sources.version, report)


def report_errors(report: Report) -> int:
    """食い違いを標準エラーへ出し、終了コードを決める。"""
    if report.errors:
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("documented skill counts and versions are up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="リポジトリの根（既定: カレントディレクトリ）")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    report = Report()
    sources = collect_sources(root, report)

    check_root_readme_document(root, sources, report)
    check_agents_document(root, sources, report)
    check_versioning_document(root, sources, report)
    check_plugin_readme_document(root, sources, report)

    return report_errors(report)


if __name__ == "__main__":
    raise SystemExit(main())
