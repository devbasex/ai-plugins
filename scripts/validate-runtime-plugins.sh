#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  echo "==> $*"
  "$@"
}

run bash "$ROOT_DIR/scripts/build-runtime-plugins.sh" --check

# Skill を配る plugin family を manifests/ の有無から検出する（plugins/mcp/* は別系統）。
# 移行の途中は 2 つの構成が混ざるため、どちらも受け付ける。
#   split  … plugins/<family>-shared（編集元）+ plugins/<family>-{claude,codex,kiro}（生成物）
#   single … plugins/<family>（配布ディレクトリが 1 つだけ）
# 後段の静的解析（claude plugin validate / Kiro installer の dry-run）もこの一覧で回すため、
# family を足したときに検査対象から漏れることがない。
FAMILIES=()
for shared_dir in "$ROOT_DIR"/plugins/*-shared; do
  [ -d "$shared_dir/manifests" ] || continue
  family="$(basename "$shared_dir")"
  FAMILIES+=("${family%-shared}:split")
done
for plugin_dir in "$ROOT_DIR"/plugins/*; do
  [ -d "$plugin_dir/manifests" ] || continue
  family="$(basename "$plugin_dir")"
  case "$family" in
    *-shared|*-claude|*-codex|*-kiro) continue ;;
  esac
  FAMILIES+=("$family:single")
done
if [ "${#FAMILIES[@]}" -eq 0 ]; then
  echo "ERROR: plugin family が見つからない（plugins/<family>[-shared]/manifests）" >&2
  exit 1
fi
echo "==> plugin families: ${FAMILIES[*]}"

# 単一ディレクトリ構成の family 名だけを取り出す（ルートマニフェストの検査などで使う）。
single_families() {
  local entry
  for entry in "${FAMILIES[@]}"; do
    [ "${entry#*:}" = single ] || continue
    printf '%s\n' "${entry%%:*}"
  done
}

run python3 -m json.tool "$ROOT_DIR/.claude-plugin/marketplace.json" >/dev/null
run python3 -m json.tool "$ROOT_DIR/.agents/plugins/marketplace.json" >/dev/null

while IFS= read -r manifest; do
  run python3 -m json.tool "$manifest" >/dev/null
done < <(find "$ROOT_DIR/plugins" -path '*/.claude-plugin/plugin.json' -o -path '*/.codex-plugin/plugin.json' | sort)

while IFS= read -r family; do
  root_manifest="$ROOT_DIR/plugins/$family/plugin.json"
  [ -f "$root_manifest" ] || continue
  run python3 -m json.tool "$root_manifest" >/dev/null
done < <(single_families)

while IFS= read -r mcp_config; do
  run python3 -m json.tool "$mcp_config" >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp" -maxdepth 2 -name .mcp.json | sort)

run python3 - "$ROOT_DIR" "${FAMILIES[@]}" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
# 検出済みの plugin family は呼び出し側から受け取る（検出を 2 箇所に持つと、
# 一方だけが新しい family を拾って検査範囲が食い違う）。
# 受け取る形は `<family>:<layout>` で、layout は split か single。
families = [tuple(arg.split(":", 1)) for arg in sys.argv[2:]]


def plugin_dir_of(family: str, layout: str, runtime: str) -> Path:
    """ランタイム別の配布ディレクトリ。single ではどのランタイムも同じ場所を指す。"""
    if layout == "single":
        return root / f"plugins/{family}"
    return root / f"plugins/{family}-{runtime}"


def source_dir_of(family: str, layout: str) -> Path:
    """Skill と manifests の置き場所（split では編集元、single では配布ディレクトリ）。"""
    if layout == "single":
        return root / f"plugins/{family}"
    return root / f"plugins/{family}-shared"

def read_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)

errors: list[str] = []

claude_marketplace = read_json(root / ".claude-plugin/marketplace.json")
for plugin in claude_marketplace.get("plugins", []):
    source = plugin.get("source")
    if not isinstance(source, str):
        errors.append(f".claude-plugin marketplace plugin {plugin.get('name')} has invalid source")
        continue
    plugin_dir = (root / source).resolve()
    if not plugin_dir.is_dir():
        errors.append(f"Claude marketplace source missing: {source}")
        continue
    if not (plugin_dir / ".claude-plugin/plugin.json").is_file():
        errors.append(f"Claude plugin manifest missing under {source}")

codex_marketplace = read_json(root / ".agents/plugins/marketplace.json")
for plugin in codex_marketplace.get("plugins", []):
    source = plugin.get("source", {})
    source_path = source.get("path") if isinstance(source, dict) else None
    if not isinstance(source_path, str):
        errors.append(f"Codex marketplace plugin {plugin.get('name')} has invalid source.path")
        continue
    plugin_dir = (root / source_path).resolve()
    if not plugin_dir.is_dir():
        errors.append(f"Codex marketplace source missing: {source_path}")
        continue
    # Codex は plugin.json（Agent Plugins 形式）> .codex-plugin > .claude-plugin の順で採る。
    # 単一ディレクトリ構成では絞り込みの要らないプラグインがルートマニフェストだけを持つため、
    # どちらか一方があれば良い。
    if not (
        (plugin_dir / ".codex-plugin/plugin.json").is_file()
        or (plugin_dir / "plugin.json").is_file()
    ):
        errors.append(
            f"Codex plugin manifest missing under {source_path}"
            "（.codex-plugin/plugin.json かルートの plugin.json のどちらかが要る）"
        )

# 版数と Skill 数は plugin.json と marketplace の description に重複して書かれている。
# `.claude-plugin/marketplace.json` と Codex 版 plugin.json は build-runtime-plugins.sh の
# 生成対象ではなく、古い値が残っても JSON としては妥当なため他の検査に掛からない。
# 実際に版数と Skill 数の取り残しが繰り返し起きたので、Claude 版 plugin.json を基準に突き合わせる。
VERSION_IN_DESCRIPTION = re.compile(r"\(v(\d+\.\d+\.\d+)\)")
# `<数> ... skills` の形で書く規約。版数（8.0.0）や製品名（E2E）の数字を拾わないよう前後が
# 英数字・ドットでない整数だけを見て、さらに `skills` との間に挟める語を 3 語までに絞る。
# こうしないと離れた位置にある無関係な数（`8 specialized agents` など）を Skill 数と誤認する。
DESCRIBED_SKILL_COUNT = re.compile(r"(?<![\w.])(\d+)(?![\w.])(?:\s+[\w/()-]+){0,3}\s+skills\b")


def manifest_skill_count(family: str, layout: str, runtime: str):
    manifest = source_dir_of(family, layout) / f"manifests/{runtime}-skills.txt"
    if not manifest.is_file():
        return None
    return sum(
        1
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    )


def published_skill_count(family: str, layout: str):
    """ルートマニフェストが公開する Skill 数。Agent Plugins 1.0.0 §6.1 が
    `skills/` を固定位置と定めており、絞り込みを持たないため実体の数と一致する。"""
    skills_dir = source_dir_of(family, layout) / "skills"
    if not skills_dir.is_dir():
        return None
    return sum(1 for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())


def described_skill_count(description: str):
    found = DESCRIBED_SKILL_COUNT.findall(description)
    return int(found[-1]) if found else None


def check_description(label: str, description, version: str, expected, source: str) -> None:
    if not isinstance(description, str):
        return
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
    # だけでこの検査を無効化できてしまう。
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


for family, layout in families:
    claude_dir = plugin_dir_of(family, layout, "claude")
    claude_plugin_path = claude_dir / ".claude-plugin/plugin.json"
    if not claude_plugin_path.is_file():
        continue
    claude_plugin = read_json(claude_plugin_path)
    version = claude_plugin.get("version")
    if not isinstance(version, str):
        errors.append(f"{family} の claude plugin.json に version がない")
        continue
    check_description(
        str(claude_plugin_path.relative_to(root)),
        claude_plugin.get("description"),
        version,
        manifest_skill_count(family, layout, "claude"),
        "claude-skills.txt",
    )
    codex_plugin_path = plugin_dir_of(family, layout, "codex") / ".codex-plugin/plugin.json"
    if codex_plugin_path.is_file():
        codex_plugin = read_json(codex_plugin_path)
        if codex_plugin.get("version") != version:
            errors.append(
                f"{codex_plugin_path.relative_to(root)} の version が claude 版と"
                f"食い違う（codex: {codex_plugin.get('version')} / claude: {version}）"
            )
        check_description(
            str(codex_plugin_path.relative_to(root)),
            codex_plugin.get("description"),
            version,
            manifest_skill_count(family, layout, "codex"),
            "codex-skills.txt",
        )
    # ルートマニフェスト（Agent Plugins 形式）は絞り込みを持たず `skills/` を全件公開する。
    # description の Skill 数は manifest ではなく実体の数と突き合わせる。
    root_manifest_path = source_dir_of(family, layout) / "plugin.json"
    if layout == "single" and root_manifest_path.is_file():
        root_manifest = read_json(root_manifest_path)
        if root_manifest.get("version") != version:
            errors.append(
                f"{root_manifest_path.relative_to(root)} の version が claude 版と"
                f"食い違う（root: {root_manifest.get('version')} / claude: {version}）"
            )
        check_description(
            str(root_manifest_path.relative_to(root)),
            root_manifest.get("description"),
            version,
            published_skill_count(family, layout),
            "skills/ の実体",
        )
    expected_source = "./" + claude_dir.relative_to(root).as_posix()
    for plugin in claude_marketplace.get("plugins", []):
        if plugin.get("source") != expected_source:
            continue
        check_description(
            f".claude-plugin/marketplace.json の {plugin.get('name')}",
            plugin.get("description"),
            version,
            manifest_skill_count(family, layout, "claude"),
            "claude-skills.txt",
        )

for family, layout in families:
    source = source_dir_of(family, layout)
    for manifest in sorted((source / "manifests").glob("*-skills.txt")):
        runtime = manifest.name.removesuffix("-skills.txt")
        runtime_skills = plugin_dir_of(family, layout, runtime) / "skills"
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
for family, layout in families:
    if layout != "single" or not (source_dir_of(family, layout) / "plugin.json").is_file():
        continue
    source = source_dir_of(family, layout)
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
# 配らない Skill は optional-skills/ へ置く規約なので、その違反を検出する。
for family, layout in families:
    if layout != "single":
        continue
    source = source_dir_of(family, layout)
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
                "（配らない Skill は optional-skills/ へ置く）"
            )

# マニフェストの skills は配列で明示する。配列に載っていない Skill はランタイムから
# 読み込まれないため、manifest と一致していないと配布漏れになる。ディレクトリの中身を
# 見る上の検査では検出できないので、ここで突き合わせる。
for family, layout in families:
  for runtime, manifest_key in (("claude", ".claude-plugin"), ("codex", ".codex-plugin")):
    skills_manifest = source_dir_of(family, layout) / f"manifests/{runtime}-skills.txt"
    plugin_json = plugin_dir_of(family, layout, runtime) / f"{manifest_key}/plugin.json"
    if not (skills_manifest.is_file() and plugin_json.is_file()):
        continue
    expected = [
        line.split("#", 1)[0].strip()
        for line in skills_manifest.read_text(encoding="utf-8").splitlines()
    ]
    expected_set = {name for name in expected if name}
    declared = json.loads(plugin_json.read_text(encoding="utf-8")).get("skills")
    if not isinstance(declared, list):
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
        for entry in declared:
            if not isinstance(entry, str):
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
# 3 runtime が同じ .mcp.json を読む。
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
    # サーバが 1 つも登録されない。
    codex_manifest = mcp / ".codex-plugin/plugin.json"
    if codex_manifest.is_file():
        declared = read_json(codex_manifest).get("mcpServers")
        if declared != "./.mcp.json":
            errors.append(
                f"plugins/mcp/{mcp.name}/.codex-plugin/plugin.json の mcpServers が "
                f"`./.mcp.json` でない（実際: {declared!r}）"
            )

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("runtime plugin manifests and generated paths are valid")
PY

# 配布ディレクトリを構成ごとに解決する。single では 3 ランタイムが同じ場所を指す。
plugin_dir_for() {
  local entry="$1" runtime="$2"
  if [ "${entry#*:}" = single ]; then
    printf '%s\n' "$ROOT_DIR/plugins/${entry%%:*}"
  else
    printf '%s\n' "$ROOT_DIR/plugins/${entry%%:*}-$runtime"
  fi
}

if command -v claude >/dev/null 2>&1; then
  for entry in "${FAMILIES[@]}"; do
    claude_dir="$(plugin_dir_for "$entry" claude)"
    [ -d "$claude_dir" ] || continue
    run claude plugin validate "$claude_dir"
  done
  run claude plugin validate "$ROOT_DIR/.claude-plugin/marketplace.json"
else
  echo "==> claude CLI not found; skipped claude plugin validate"
fi

# Kiro の installer は split では配布ディレクトリ直下、single では Agent Plugins 仕様
# §8.2 のクライアント拡張ディレクトリ（dev.kiro/）に置く。
for entry in "${FAMILIES[@]}"; do
  if [ "${entry#*:}" = single ]; then
    installer="$ROOT_DIR/plugins/${entry%%:*}/dev.kiro/install.sh"
  else
    installer="$ROOT_DIR/plugins/${entry%%:*}-kiro/install.sh"
  fi
  [ -f "$installer" ] || continue
  run bash "$installer" --dry-run >/dev/null
done

# --with-codex を持つのは NDF の installer だけ（Codex 向け Skill も併せて配置する経路）。
# family 共通の引数ではないため、ここだけは対象を明示して検査する。
run bash "$ROOT_DIR/plugins/ndf/dev.kiro/install.sh" --dry-run --with-codex >/dev/null

while IFS= read -r installer; do
  run bash "$installer" --dry-run >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp" -path '*/dev.kiro/install.sh' | sort)

run python3 "$ROOT_DIR/scripts/check-markdown-links.py" --root "$ROOT_DIR"

echo "runtime plugin validation passed"
