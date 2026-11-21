#!/usr/bin/env python3
"""定義ファイル（マーケットプレイス・plugin.json・manifests・MCP）の形と突き合わせ（`scripts/validate-runtime-plugins.sh` の一部）。

    python3 scripts/lib/validate_manifests.py <リポジトリの根> <family>...

形（項目が文字列か・配列か）は `lib/schema.py`（pydantic）で確かめる（#1142 の D8）。見つけた食い違いを
`ERROR: ` で標準エラーへ出し、1 件でもあれば終了コード 1 で終わる。
"""

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（docs/specifications/ndf-rebuild-libraries.md の決定 19）
from skill_manifests import SKILL_TABLE, runtime_skills  # noqa: E402

require("schema")
import schema  # noqa: E402


class _Open(schema.Shape):
    """定義ファイルの一部の項目だけを見る形（ほかの項目は読み飛ばす）。"""

    model_config = {"extra": "ignore", "validate_assignment": True}


class Sourced(_Open):
    source: str


class Versioned(_Open):
    version: str


class Described(_Open):
    description: str


class SkillPaths(_Open):
    skills: list[str]


def shaped(model: type[_Open], data: Any) -> _Open | None:
    """`data` が形に合えばモデル、合わなければ None。"""
    try:
        return schema.load_shape(model, data)
    except schema.ShapeError:
        return None


def read_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# 版数と Skill 数は plugin.json と marketplace の description に重複して書かれている。
# `.claude-plugin/marketplace.json` と Codex 版 plugin.json は build-runtime-plugins.sh の
# 生成対象ではなく、古い値が残っても JSON としては妥当なため他のチェックに掛からない。
# 実際に版数と Skill 数の取り残しが繰り返し起きたので、Claude 版 plugin.json を基準に突き合わせる。
#
# 版数の書式は、引数で受け取った根の下の共有の定義から読む。読み込めないときは素通り
# させずに止める。書式を持たないまま続けると、版数の突き合わせがすべて素通りする。
# このファイルの隣にも同じ定義があるため、import の探索ではなく根の下のファイルを指して読む。
def _version_in_description(lib: Path):
    spec = importlib.util.spec_from_file_location("version_pattern", lib / "version_pattern.py")
    if spec is None or spec.loader is None or not (lib / "version_pattern.py").is_file():
        raise ImportError(f"{lib / 'version_pattern.py'} が無い")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VERSION_IN_DESCRIPTION


# `<数> ... skills` の形で書く規約。版数（8.0.0）や製品名（E2E）の数字を拾わないよう前後が
# 英数字・ドットでない整数だけを見て、さらに `skills` との間に挟める語を 3 語までに絞る。
# こうしないと離れた位置にある無関係な数（`8 specialized agents` など）を Skill 数と誤認する。
DESCRIBED_SKILL_COUNT = re.compile(r"(?<![\w.])(\d+)(?![\w.])(?:\s+[\w/()-]+){0,3}\s+skills\b")


def described_skill_count(description: str):
    found = DESCRIBED_SKILL_COUNT.findall(description)
    return int(found[-1]) if found else None


def _bad_fields(manifest_data: Any) -> set[str]:
    """`SkillPaths` の形に合わない項目の場所（`skills`・`skills[<添字>]`）。合っていれば空。"""
    try:
        schema.load_shape(SkillPaths, manifest_data)
    except schema.ShapeError as exc:
        return {where for where, _ in exc.problems}
    return set()


class VersionPatternUnavailable(Exception):
    """版数の書式（`scripts/lib/version_pattern.py`）を読み込めない。"""


class ManifestChecker:
    """1 つの根の下の定義ファイルを突き合わせ、見つけた食い違いを `errors` へためる。

    検出済みの plugin family は呼び出し側から受け取る（検出を 2 箇所に持つと、
    一方だけが新しい family を拾ってチェック範囲が食い違う）。
    """

    def __init__(self, root: Path, families: list[str]) -> None:
        self.root = root
        self.families = families
        self.errors: list[str] = []
        self.claude_marketplace: dict = {}
        self.version_in_description: re.Pattern[str] | None = None

    def plugin_dir_of(self, family: str) -> Path:
        """プラグインの配布ディレクトリ。3 ランタイムとも同じ場所を読む。"""
        return self.root / f"plugins/{family}"

    def present_skills(self, family: str) -> set[str]:
        """`skills/` の下で SKILL.md を持つディレクトリの名前。`skills/` が無いときの守りは呼び出し側が持つ。"""
        return {d.name for d in (self.plugin_dir_of(family) / "skills").iterdir() if (d / "SKILL.md").is_file()}

    def skill_table(self, family: str) -> dict[str, list[str]]:
        """ランタイムごとの配る Skill の名前（ランタイム → 名前の並び）。

        `manifests/skills.json`（配る Skill の表。11.0.0 からの NDF）があればそれを読み、行の `runtimes` に
        そのランタイムを持つ Skill を返す。`skills/<名前>/SKILL.md` がまだ無い行は配らない（表の約束。
        `ndf manifests render` と同じ）。表が無い family（playwright-kit）は `manifests/<ランタイム>-skills.txt` を読む。
        """
        source = self.plugin_dir_of(family)
        skills = runtime_skills(source)
        if not (source / SKILL_TABLE).is_file():
            return skills
        present = self.present_skills(family) if (source / "skills").is_dir() else set()
        return {runtime: [name for name in names if name in present] for runtime, names in skills.items()}

    def skill_definition(self, family: str, runtime: str) -> Path | None:
        """Skill の並びを書くランタイムの定義（plugin.json）。並びを書かない（`skills/` をすべて読む）ランタイムは None。"""
        source = self.plugin_dir_of(family)
        table = source / SKILL_TABLE
        if table.is_file():
            definition = (read_json(table)["runtimes"].get(runtime) or {}).get("definition")
            return source / definition if definition else None
        return source / f".{runtime}-plugin/plugin.json"

    def skill_table_name(self, family: str, runtime: str) -> str:
        """食い違いの文に出す、配る Skill の基準の名前。"""
        if (self.plugin_dir_of(family) / SKILL_TABLE).is_file():
            return f"{SKILL_TABLE} の {runtime}"
        return f"{runtime}-skills.txt"

    def run(self) -> list[str]:
        """すべての検査を決まった順序で走らせ、食い違いの一覧を返す。"""
        self.claude_marketplace = read_json(self.root / ".claude-plugin/marketplace.json")
        self.check_marketplace()
        self.load_version_pattern()
        self.check_versions()
        self.check_manifest_skills()
        self.check_root_manifest_skills()
        self.check_unlisted_skills()
        self.check_skill_arrays()
        self.check_mcp()
        return self.errors

    def check_marketplace(self) -> None:
        for plugin in self.claude_marketplace.get("plugins", []):
            entry = shaped(Sourced, plugin)
            if entry is None:
                self.errors.append(f".claude-plugin marketplace plugin {plugin.get('name')} has invalid source")
                continue
            source = entry.source
            plugin_dir = (self.root / source).resolve()
            if not plugin_dir.is_dir():
                self.errors.append(f"Claude marketplace source missing: {source}")
                continue
            if not (plugin_dir / ".claude-plugin/plugin.json").is_file():
                self.errors.append(f"Claude plugin manifest missing under {source}")

        # Codex は専用のマーケットプレイス定義が無ければ .claude-plugin/marketplace.json へ
        # フォールバックする。定義は 1 つに統合したので、同じエントリを Codex 側の要件でもチェックする。
        # Codex は plugin.json（Agent Plugins 形式）> .codex-plugin > .claude-plugin の順で採るため、
        # ルートマニフェストか Codex 用マニフェストのどちらかがあれば良い。
        for plugin in self.claude_marketplace.get("plugins", []):
            entry = shaped(Sourced, plugin)
            if entry is None:
                continue
            source = entry.source
            plugin_dir = (self.root / source).resolve()
            if not plugin_dir.is_dir():
                continue
            if not ((plugin_dir / ".codex-plugin/plugin.json").is_file() or (plugin_dir / "plugin.json").is_file()):
                self.errors.append(
                    f"Codex plugin manifest missing under {source}（.codex-plugin/plugin.json かルートの plugin.json のどちらかが要る）"
                )
            # Codex は policy / category / interface を要求する。統合した定義から欠けると
            # Codex 側の一覧に出ない。
            for key in ("policy", "category", "interface"):
                if key not in plugin:
                    self.errors.append(f"marketplace entry {plugin.get('name')} に {key} がない（Codex が要求する）")

    def load_version_pattern(self) -> None:
        """読み込めないときは素通りさせずに止める（`VersionPatternUnavailable`）。"""
        try:
            self.version_in_description = _version_in_description(self.root / "scripts" / "lib")
        except (ImportError, AttributeError, OSError) as exc:
            raise VersionPatternUnavailable(str(exc)) from exc

    def manifest_skill_count(self, family: str, runtime: str):
        names = self.skill_table(family).get(runtime)
        return None if names is None else len(names)

    def published_skill_count(self, family: str):
        """ルートマニフェストが公開する Skill 数。Agent Plugins 1.0.0 §6.1 が
        `skills/` を固定位置と定めており、絞り込みを持たないため実体の数と一致する。"""
        skills_dir = self.plugin_dir_of(family) / "skills"
        if not skills_dir.is_dir():
            return None
        return len(self.present_skills(family))

    def check_description(self, label: str, manifest: dict, version: str, expected, source: str) -> None:
        described_entry = shaped(Described, manifest)
        if described_entry is None:
            return
        description = described_entry.description
        found = self.version_in_description.search(description)
        if not found:
            self.errors.append(f"{label} の description に `(vX.Y.Z)` 形式の版数がない")
        elif found.group(1) != version:
            self.errors.append(f"{label} の description の版数が古い（description: v{found.group(1)} / Claude 版 plugin.json: v{version}）")
        if expected is None:
            return
        # 抽出できないこと自体をエラーにする。素通りさせると、Skill 数の記述を消すか書式を変える
        # だけでこのチェックを無効化できてしまう。
        described = described_skill_count(description)
        if described is None:
            self.errors.append(f"{label} の description から Skill 数を読み取れない（`<数> ... skills` の形で書く。{source}: {expected}）")
        elif described != expected:
            self.errors.append(f"{label} の description の Skill 数が食い違う（description: {described} / {source}: {expected}）")

    def check_versions(self) -> None:
        for family in self.families:
            claude_dir = self.plugin_dir_of(family)
            claude_plugin_path = claude_dir / ".claude-plugin/plugin.json"
            if not claude_plugin_path.is_file():
                continue
            claude_plugin = read_json(claude_plugin_path)
            versioned = shaped(Versioned, claude_plugin)
            if versioned is None:
                self.errors.append(f"{family} の claude plugin.json に version がない")
                continue
            version = versioned.version
            self.check_description(
                str(claude_plugin_path.relative_to(self.root)),
                claude_plugin,
                version,
                self.manifest_skill_count(family, "claude"),
                self.skill_table_name(family, "claude"),
            )
            self._check_sibling_manifest(
                claude_dir / ".codex-plugin/plugin.json",
                "codex",
                version,
                self.manifest_skill_count(family, "codex"),
                self.skill_table_name(family, "codex"),
            )
            # ルートマニフェスト（Agent Plugins 形式）は絞り込みを持たず `skills/` を全件公開する。
            # description の Skill 数は manifest ではなく実体の数と突き合わせる。
            self._check_sibling_manifest(claude_dir / "plugin.json", "root", version, self.published_skill_count(family), "skills/ の実体")
            expected_source = "./" + claude_dir.relative_to(self.root).as_posix()
            for plugin in self.claude_marketplace.get("plugins", []):
                if plugin.get("source") != expected_source:
                    continue
                self.check_description(
                    f".claude-plugin/marketplace.json の {plugin.get('name')}",
                    plugin,
                    version,
                    self.manifest_skill_count(family, "claude"),
                    "claude-skills.txt",
                )

    def _check_sibling_manifest(self, path: Path, side: str, version: str, expected, source: str) -> None:
        """Claude 版と並ぶ定義（Codex 版・ルート）があれば、version を Claude 版と比べ、description を確かめる。"""
        if not path.is_file():
            return
        manifest = read_json(path)
        if manifest.get("version") != version:
            self.errors.append(
                f"{path.relative_to(self.root)} の version が claude 版と食い違う（{side}: {manifest.get('version')} / claude: {version}）"
            )
        self.check_description(str(path.relative_to(self.root)), manifest, version, expected, source)

    def check_manifest_skills(self) -> None:
        for family in self.families:
            source = self.plugin_dir_of(family)
            runtime_skills = source / "skills"
            for runtime, names in self.skill_table(family).items():
                if not runtime_skills.is_dir():
                    self.errors.append(f"runtime skills directory missing: {runtime_skills.relative_to(self.root)}")
                    continue
                for skill in names:
                    if "/" in skill or ".." in skill:
                        self.errors.append(f"invalid skill name in {self.skill_table_name(family, runtime)}: {skill}")
                        continue
                    if not (runtime_skills / skill / "SKILL.md").is_file():
                        self.errors.append(f"{family} {runtime} runtime skill missing: {skill}")

    def check_root_manifest_skills(self) -> None:
        # ルートマニフェストを置く family は `skills/` を全件公開する。Codex はこのマニフェストを
        # 優先して読むため、codex-skills.txt に載らない Skill を `skills/` へ置くと配布先が増える。
        # 実体と codex 用 manifest が一致していることを確かめる。
        for family in self.families:
            if not (self.plugin_dir_of(family) / "plugin.json").is_file():
                continue
            codex = self.skill_table(family).get("codex")
            if codex is None:
                continue
            listed = set(codex)
            present = self.present_skills(family)
            for extra in sorted(present - listed):
                self.errors.append(
                    f"{family} の skills/ にあるが codex-skills.txt に無い: {extra}"
                    "（ルートマニフェストは skills/ を全件公開するため Codex へ配られる）"
                )

    def check_unlisted_skills(self) -> None:
        # 単一ディレクトリ構成では skills/ に全 runtime 分の実体が並ぶ。どのランタイムへも配らない
        # Skill をここへ置くと、ルートマニフェストや将来の絞り込み漏れで配られる。
        # skills/ に置く Skill は少なくとも 1 つのランタイムへ配る規約なので、その違反を
        # 検出する（配らない置き場所は v10.5.0 で無くなった。#116）。
        for family in self.families:
            listed = {name for names in self.skill_table(family).values() for name in names}
            for name in sorted(self.present_skills(family) - listed):
                self.errors.append(
                    f"{family} の skills/ にあるがどのランタイムへも配らない: {name}"
                    "（skills/ に置く Skill は少なくとも 1 つのランタイムの配る Skill へ載せる）"
                )

    def check_skill_arrays(self) -> None:
        # マニフェストの skills は配列で明示する。配列に載っていない Skill はランタイムから
        # 読み込まれないため、manifest と一致していないと配布漏れになる。ディレクトリの中身を
        # 見る上のチェックでは検出できないので、ここで突き合わせる。
        for family in self.families:
            for runtime, expected in self.skill_table(family).items():
                plugin_json = self.skill_definition(family, runtime)
                if plugin_json is None or not plugin_json.is_file():
                    continue
                manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
                declared = manifest_data.get("skills")
                bad = _bad_fields(manifest_data)
                if "skills" in bad:
                    # 配列以外（ディレクトリ指定・欠落）を許すと、この突き合わせが黙って skip され
                    # 配布漏れの再発を検出できなくなる。配列で明示する形式に固定する。
                    self.errors.append(
                        f"{family} の {runtime} plugin.json の skills が配列ではない"
                        f"（実際: {type(declared).__name__}）。manifest との突き合わせができない"
                    )
                else:
                    self._check_declared_entries(family, runtime, expected, declared, bad)

    def _check_declared_entries(self, family: str, runtime: str, expected: list[str], declared: list, bad: set[str]) -> None:
        """plugin.json の skills 配列（`declared`）と配る Skill（`expected`）の欠け・余分・文字列以外を追記する。"""
        # 比較はパス全体で行う。basename だけを見ると `./wrong/pr` のように
        # 実在しない場所を指す項目を通してしまう（claude CLI が無い環境では
        # 後段の `claude plugin validate` も skip されるため気づけない）。
        expected_entries = {f"./skills/{name}" for name in set(expected)}
        declared_entries = set()
        for index, entry in enumerate(declared):
            if f"skills[{index}]" in bad:
                self.errors.append(f"{runtime} plugin.json の skills 配列に文字列以外の項目がある（{type(entry).__name__}）")
                continue
            declared_entries.add(entry)
        for missing in sorted(expected_entries - declared_entries):
            self.errors.append(f"{family} の {runtime} plugin.json の skills 配列に載っていない: {missing}（manifest には登録済み）")
        for extra in sorted(declared_entries - expected_entries):
            self.errors.append(
                f"{family} の {runtime} plugin.json の skills 配列に余分な項目: {extra}"
                "（manifest に無い、またはパスが `./skills/<Skill 名>` の形式でない）"
            )

    def check_mcp(self) -> None:
        # MCP プラグインも 1 ディレクトリにまとめた。runtime ごとの配布物は無く、
        # 2 runtime が同じ .mcp.json を読む（Codex だけ .codex.mcp.json を読ませる例外がある）。
        for mcp in sorted((self.root / "plugins/mcp").iterdir()):
            if not mcp.is_dir():
                continue
            if not (mcp / ".mcp.json").is_file():
                self.errors.append(f"MCP config missing: plugins/mcp/{mcp.name}/.mcp.json")
                continue
            if not (mcp / ".claude-plugin/plugin.json").is_file():
                self.errors.append(f"Claude plugin manifest missing: plugins/mcp/{mcp.name}/.claude-plugin/plugin.json")
            if not (mcp / ".codex-plugin/plugin.json").is_file():
                self.errors.append(f"Codex plugin manifest missing: plugins/mcp/{mcp.name}/.codex-plugin/plugin.json")
            # Codex は .mcp.json を manifest の mcpServers から読む。指定が無いと
            # サーバが 1 つも登録されない。Codex だけ別の起動定義を読ませるときは
            # `./.codex.mcp.json` を指す（mcp-serena の `--context codex`。#818）。
            codex_manifest = mcp / ".codex-plugin/plugin.json"
            if codex_manifest.is_file():
                declared = read_json(codex_manifest).get("mcpServers")
                allowed = ("./.mcp.json", "./.codex.mcp.json")
                if declared not in allowed or not (mcp / declared).is_file():
                    self.errors.append(
                        f"plugins/mcp/{mcp.name}/.codex-plugin/plugin.json の mcpServers が "
                        f"実在する `./.mcp.json` か `./.codex.mcp.json` でない（実際: {declared!r}）"
                    )


def main(argv: list[str]) -> None:
    checker = ManifestChecker(Path(argv[1]), argv[2:])
    try:
        errors = checker.run()
    except VersionPatternUnavailable as exc:
        raise SystemExit(f"版数の書式を読み込めない（scripts/lib/version_pattern.py）: {exc}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("runtime plugin manifests and generated paths are valid")


if __name__ == "__main__":
    main(sys.argv)
