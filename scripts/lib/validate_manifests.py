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
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

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


root = Path(sys.argv[1])
# 検出済みの plugin family は呼び出し側から受け取る（検出を 2 箇所に持つと、
# 一方だけが新しい family を拾ってチェック範囲が食い違う）。
families = sys.argv[2:]


def plugin_dir_of(family: str) -> Path:
    """プラグインの配布ディレクトリ。3 ランタイムとも同じ場所を読む。"""
    return root / f"plugins/{family}"

def read_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)

errors: list[str] = []

claude_marketplace = read_json(root / ".claude-plugin/marketplace.json")
for plugin in claude_marketplace.get("plugins", []):
    entry = shaped(Sourced, plugin)
    if entry is None:
        errors.append(f".claude-plugin marketplace plugin {plugin.get('name')} has invalid source")
        continue
    source = entry.source
    plugin_dir = (root / source).resolve()
    if not plugin_dir.is_dir():
        errors.append(f"Claude marketplace source missing: {source}")
        continue
    if not (plugin_dir / ".claude-plugin/plugin.json").is_file():
        errors.append(f"Claude plugin manifest missing under {source}")

# Codex は専用のマーケットプレイス定義が無ければ .claude-plugin/marketplace.json へ
# フォールバックする。定義は 1 つに統合したので、同じエントリを Codex 側の要件でもチェックする。
# Codex は plugin.json（Agent Plugins 形式）> .codex-plugin > .claude-plugin の順で採るため、
# ルートマニフェストか Codex 用マニフェストのどちらかがあれば良い。
for plugin in claude_marketplace.get("plugins", []):
    entry = shaped(Sourced, plugin)
    if entry is None:
        continue
    source = entry.source
    plugin_dir = (root / source).resolve()
    if not plugin_dir.is_dir():
        continue
    if not (
        (plugin_dir / ".codex-plugin/plugin.json").is_file()
        or (plugin_dir / "plugin.json").is_file()
    ):
        errors.append(
            f"Codex plugin manifest missing under {source}"
            "（.codex-plugin/plugin.json かルートの plugin.json のどちらかが要る）"
        )
    # Codex は policy / category / interface を要求する。統合した定義から欠けると
    # Codex 側の一覧に出ない。
    for key in ("policy", "category", "interface"):
        if key not in plugin:
            errors.append(f"marketplace entry {plugin.get('name')} に {key} がない（Codex が要求する）")

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


try:
    VERSION_IN_DESCRIPTION = _version_in_description(root / "scripts" / "lib")
except (ImportError, AttributeError, OSError) as exc:
    raise SystemExit(
        f"版数の書式を読み込めない（scripts/lib/version_pattern.py）: {exc}"
    )
# `<数> ... skills` の形で書く規約。版数（8.0.0）や製品名（E2E）の数字を拾わないよう前後が
# 英数字・ドットでない整数だけを見て、さらに `skills` との間に挟める語を 3 語までに絞る。
# こうしないと離れた位置にある無関係な数（`8 specialized agents` など）を Skill 数と誤認する。
DESCRIBED_SKILL_COUNT = re.compile(r"(?<![\w.])(\d+)(?![\w.])(?:\s+[\w/()-]+){0,3}\s+skills\b")


def manifest_skill_count(family: str, runtime: str):
    manifest = plugin_dir_of(family) / f"manifests/{runtime}-skills.txt"
    if not manifest.is_file():
        return None
    return sum(
        1
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    )


def published_skill_count(family: str):
    """ルートマニフェストが公開する Skill 数。Agent Plugins 1.0.0 §6.1 が
    `skills/` を固定位置と定めており、絞り込みを持たないため実体の数と一致する。"""
    skills_dir = plugin_dir_of(family) / "skills"
    if not skills_dir.is_dir():
        return None
    return sum(1 for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())


def described_skill_count(description: str):
    found = DESCRIBED_SKILL_COUNT.findall(description)
    return int(found[-1]) if found else None


def check_description(label: str, manifest: dict, version: str, expected, source: str) -> None:
    described_entry = shaped(Described, manifest)
    if described_entry is None:
        return
    description = described_entry.description
    found = VERSION_IN_DESCRIPTION.search(description)
    if not found:
        errors.append(f"{label} の description に `(vX.Y.Z)` 形式の版数がない")
    elif found.group(1) != version:
        errors.append(
            f"{label} の description の版数が古い"
            f"（description: v{found.group(1)} / Claude 版 plugin.json: v{version}）"
        )
    if expected is None:
        return
    # 抽出できないこと自体をエラーにする。素通りさせると、Skill 数の記述を消すか書式を変える
    # だけでこのチェックを無効化できてしまう。
    described = described_skill_count(description)
    if described is None:
        errors.append(
            f"{label} の description から Skill 数を読み取れない"
            f"（`<数> ... skills` の形で書く。{source}: {expected}）"
        )
    elif described != expected:
        errors.append(
            f"{label} の description の Skill 数が食い違う"
            f"（description: {described} / {source}: {expected}）"
        )


for family in families:
    claude_dir = plugin_dir_of(family)
    claude_plugin_path = claude_dir / ".claude-plugin/plugin.json"
    if not claude_plugin_path.is_file():
        continue
    claude_plugin = read_json(claude_plugin_path)
    versioned = shaped(Versioned, claude_plugin)
    if versioned is None:
        errors.append(f"{family} の claude plugin.json に version がない")
        continue
    version = versioned.version
    check_description(
        str(claude_plugin_path.relative_to(root)),
        claude_plugin,
        version,
        manifest_skill_count(family, "claude"),
        "claude-skills.txt",
    )
    codex_plugin_path = plugin_dir_of(family) / ".codex-plugin/plugin.json"
    if codex_plugin_path.is_file():
        codex_plugin = read_json(codex_plugin_path)
        if codex_plugin.get("version") != version:
            errors.append(
                f"{codex_plugin_path.relative_to(root)} の version が claude 版と"
                f"食い違う（codex: {codex_plugin.get('version')} / claude: {version}）"
            )
        check_description(
            str(codex_plugin_path.relative_to(root)),
            codex_plugin,
            version,
            manifest_skill_count(family, "codex"),
            "codex-skills.txt",
        )
    # ルートマニフェスト（Agent Plugins 形式）は絞り込みを持たず `skills/` を全件公開する。
    # description の Skill 数は manifest ではなく実体の数と突き合わせる。
    # agy 向けの定義はクライアント拡張ディレクトリ（Agent Plugins 1.0.0 §8.2）へ置く。
    # ルート直下へ置くと Codex がそちらを優先して読み、配布 Skill が codex-skills.txt では
    # なく skills/ の実体になる。agy には取得元の登録が無く `agy plugin list` も版数を出さない
    # ため、利用者が版を判断できる手がかりは clone した中身の版数だけである。
    agy_manifest_path = plugin_dir_of(family) / "dev.agy/plugin.json"
    if agy_manifest_path.is_file():
        agy_manifest = read_json(agy_manifest_path)
        if agy_manifest.get("version") != version:
            errors.append(
                f"{agy_manifest_path.relative_to(root)} の version が claude 版と"
                f"食い違う（agy: {agy_manifest.get('version')} / claude: {version}）"
            )
        check_description(
            str(agy_manifest_path.relative_to(root)),
            agy_manifest,
            version,
            manifest_skill_count(family, "agy"),
            "agy-skills.txt",
        )
    root_manifest_path = plugin_dir_of(family) / "plugin.json"
    if root_manifest_path.is_file():
        root_manifest = read_json(root_manifest_path)
        if root_manifest.get("version") != version:
            errors.append(
                f"{root_manifest_path.relative_to(root)} の version が claude 版と"
                f"食い違う（root: {root_manifest.get('version')} / claude: {version}）"
            )
        check_description(
            str(root_manifest_path.relative_to(root)),
            root_manifest,
            version,
            published_skill_count(family),
            "skills/ の実体",
        )
    expected_source = "./" + claude_dir.relative_to(root).as_posix()
    for plugin in claude_marketplace.get("plugins", []):
        if plugin.get("source") != expected_source:
            continue
        check_description(
            f".claude-plugin/marketplace.json の {plugin.get('name')}",
            plugin,
            version,
            manifest_skill_count(family, "claude"),
            "claude-skills.txt",
        )

for family in families:
    source = plugin_dir_of(family)
    for manifest in sorted((source / "manifests").glob("*-skills.txt")):
        runtime = manifest.name.removesuffix("-skills.txt")
        runtime_skills = plugin_dir_of(family) / "skills"
        if not runtime_skills.is_dir():
            errors.append(f"runtime skills directory missing: {runtime_skills.relative_to(root)}")
            continue
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            skill = raw.split("#", 1)[0].strip()
            if not skill:
                continue
            if "/" in skill or ".." in skill:
                errors.append(f"invalid skill name in {manifest.relative_to(root)}: {skill}")
                continue
            if not (source / "skills" / skill / "SKILL.md").is_file():
                errors.append(f"{family} source skill missing: {skill}")
            if not (runtime_skills / skill / "SKILL.md").is_file():
                errors.append(f"{family} {runtime} runtime skill missing: {skill}")

# ルートマニフェストを置く family は `skills/` を全件公開する。Codex はこのマニフェストを
# 優先して読むため、codex-skills.txt に載らない Skill を `skills/` へ置くと配布先が増える。
# 実体と codex 用 manifest が一致していることを確かめる。
for family in families:
    if not (plugin_dir_of(family) / "plugin.json").is_file():
        continue
    source = plugin_dir_of(family)
    manifest = source / "manifests/codex-skills.txt"
    if not manifest.is_file():
        continue
    listed = {
        line.split("#", 1)[0].strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }
    present = {d.name for d in (source / "skills").iterdir() if (d / "SKILL.md").is_file()}
    for extra in sorted(present - listed):
        errors.append(
            f"{family} の skills/ にあるが codex-skills.txt に無い: {extra}"
            "（ルートマニフェストは skills/ を全件公開するため Codex へ配られる）"
        )

# 単一ディレクトリ構成では skills/ に全 runtime 分の実体が並ぶ。どの manifest にも
# 載らない Skill をここへ置くと、ルートマニフェストや将来の絞り込み漏れで配られる。
# skills/ に置く Skill は少なくとも 1 つの manifest へ載せる規約なので、その違反を
# 検出する（配らない置き場所は v10.5.0 で無くなった。#116）。
for family in families:
    source = plugin_dir_of(family)
    listed: set[str] = set()
    for manifest in sorted((source / "manifests").glob("*-skills.txt")):
        listed |= {
            line.split("#", 1)[0].strip()
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()
        }
    for skill_dir in sorted((source / "skills").iterdir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        if skill_dir.name not in listed:
            errors.append(
                f"{family} の skills/ にあるがどの manifest にも載っていない: {skill_dir.name}"
                "（skills/ に置く Skill は少なくとも 1 つの manifest へ載せる）"
            )

# マニフェストの skills は配列で明示する。配列に載っていない Skill はランタイムから
# 読み込まれないため、manifest と一致していないと配布漏れになる。ディレクトリの中身を
# 見る上のチェックでは検出できないので、ここで突き合わせる。
for family in families:
  for runtime, manifest_key in (("claude", ".claude-plugin"), ("codex", ".codex-plugin")):
    skills_manifest = plugin_dir_of(family) / f"manifests/{runtime}-skills.txt"
    plugin_json = plugin_dir_of(family) / f"{manifest_key}/plugin.json"
    if not (skills_manifest.is_file() and plugin_json.is_file()):
        continue
    expected = [
        line.split("#", 1)[0].strip()
        for line in skills_manifest.read_text(encoding="utf-8").splitlines()
    ]
    expected_set = {name for name in expected if name}
    manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
    declared = manifest_data.get("skills")
    try:
        schema.load_shape(SkillPaths, manifest_data)
        bad: set[str] = set()
    except schema.ShapeError as exc:
        bad = {where for where, _ in exc.problems}
    if "skills" in bad:
        # 配列以外（ディレクトリ指定・欠落）を許すと、この突き合わせが黙って skip され
        # 配布漏れの再発を検出できなくなる。配列で明示する形式に固定する。
        errors.append(
            f"{family} の {runtime} plugin.json の skills が配列ではない"
            f"（実際: {type(declared).__name__}）。manifest との突き合わせができない"
        )
    else:
        # 比較はパス全体で行う。basename だけを見ると `./wrong/pr` のように
        # 実在しない場所を指す項目を通してしまう（claude CLI が無い環境では
        # 後段の `claude plugin validate` も skip されるため気づけない）。
        expected_entries = {f"./skills/{name}" for name in expected_set}
        declared_entries = set()
        for index, entry in enumerate(declared):
            if f"skills[{index}]" in bad:
                errors.append(
                    f"{runtime} plugin.json の skills 配列に文字列以外の項目がある"
                    f"（{type(entry).__name__}）"
                )
                continue
            declared_entries.add(entry)
        for missing in sorted(expected_entries - declared_entries):
            errors.append(
                f"{family} の {runtime} plugin.json の skills 配列に載っていない: {missing}"
                "（manifest には登録済み）"
            )
        for extra in sorted(declared_entries - expected_entries):
            errors.append(
                f"{family} の {runtime} plugin.json の skills 配列に余分な項目: {extra}"
                "（manifest に無い、またはパスが `./skills/<Skill 名>` の形式でない）"
            )

# MCP プラグインも 1 ディレクトリにまとめた。runtime ごとの配布物は無く、
# 3 runtime が同じ .mcp.json を読む（Codex だけ .codex.mcp.json を読ませる例外がある）。
for mcp in sorted((root / "plugins/mcp").iterdir()):
    if not mcp.is_dir():
        continue
    if not (mcp / ".mcp.json").is_file():
        errors.append(f"MCP config missing: plugins/mcp/{mcp.name}/.mcp.json")
        continue
    if not (mcp / ".claude-plugin/plugin.json").is_file():
        errors.append(f"Claude plugin manifest missing: plugins/mcp/{mcp.name}/.claude-plugin/plugin.json")
    if not (mcp / ".codex-plugin/plugin.json").is_file():
        errors.append(f"Codex plugin manifest missing: plugins/mcp/{mcp.name}/.codex-plugin/plugin.json")
    if not (mcp / "dev.kiro/install.sh").is_file():
        errors.append(f"Kiro MCP installer missing: plugins/mcp/{mcp.name}/dev.kiro/install.sh")
    # Codex は .mcp.json を manifest の mcpServers から読む。指定が無いと
    # サーバが 1 つも登録されない。Codex だけ別の起動定義を読ませるときは
    # `./.codex.mcp.json` を指す（mcp-serena の `--context codex`。#818）。
    codex_manifest = mcp / ".codex-plugin/plugin.json"
    if codex_manifest.is_file():
        declared = read_json(codex_manifest).get("mcpServers")
        allowed = ("./.mcp.json", "./.codex.mcp.json")
        if declared not in allowed or not (mcp / declared).is_file():
            errors.append(
                f"plugins/mcp/{mcp.name}/.codex-plugin/plugin.json の mcpServers が "
                f"実在する `./.mcp.json` か `./.codex.mcp.json` でない（実際: {declared!r}）"
            )

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("runtime plugin manifests and generated paths are valid")