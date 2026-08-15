#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  echo "==> $*"
  "$@"
}

run bash "$ROOT_DIR/scripts/build-runtime-plugins.sh" --check

# Skill を配る plugin family を <family>-shared から検出する（plugins/mcp/shared は別系統）。
# 後段の静的解析（claude plugin validate / Kiro installer の dry-run）もこの一覧で回すため、
# family を足したときに検査対象から漏れることがない。
FAMILIES=()
for shared_dir in "$ROOT_DIR"/plugins/*-shared; do
  [ -d "$shared_dir/manifests" ] || continue
  family="$(basename "$shared_dir")"
  FAMILIES+=("${family%-shared}")
done
if [ "${#FAMILIES[@]}" -eq 0 ]; then
  echo "ERROR: plugin family が見つからない（plugins/<family>-shared/manifests）" >&2
  exit 1
fi
echo "==> plugin families: ${FAMILIES[*]}"

run python3 -m json.tool "$ROOT_DIR/.claude-plugin/marketplace.json" >/dev/null
run python3 -m json.tool "$ROOT_DIR/.agents/plugins/marketplace.json" >/dev/null

while IFS= read -r manifest; do
  run python3 -m json.tool "$manifest" >/dev/null
done < <(find "$ROOT_DIR/plugins" -path '*/.claude-plugin/plugin.json' -o -path '*/.codex-plugin/plugin.json' | sort)

while IFS= read -r mcp_config; do
  run python3 -m json.tool "$mcp_config" >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp" -name .mcp.json | sort)

run python3 - "$ROOT_DIR" "${FAMILIES[@]}" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
# 検出済みの plugin family は呼び出し側から受け取る（検出を 2 箇所に持つと、
# 一方だけが新しい family を拾って検査範囲が食い違う）。
families = sys.argv[2:]

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
    if not (plugin_dir / ".codex-plugin/plugin.json").is_file():
        errors.append(f"Codex plugin manifest missing under {source_path}")

# 版数と Skill 数は plugin.json と marketplace の description に重複して書かれている。
# `.claude-plugin/marketplace.json` と Codex 版 plugin.json は build-runtime-plugins.sh の
# 生成対象ではなく、古い値が残っても JSON としては妥当なため他の検査に掛からない。
# 実際に版数と Skill 数の取り残しが繰り返し起きたので、Claude 版 plugin.json を基準に突き合わせる。
VERSION_IN_DESCRIPTION = re.compile(r"\(v(\d+\.\d+\.\d+)\)")
# `<数> ... skills` の形で書く規約。版数（8.0.0）や製品名（E2E）の数字を拾わないよう前後が
# 英数字・ドットでない整数だけを見て、さらに `skills` との間に挟める語を 3 語までに絞る。
# こうしないと離れた位置にある無関係な数（`8 specialized agents` など）を Skill 数と誤認する。
DESCRIBED_SKILL_COUNT = re.compile(r"(?<![\w.])(\d+)(?![\w.])(?:\s+[\w/()-]+){0,3}\s+skills\b")


def manifest_skill_count(family: str, runtime: str):
    manifest = root / f"plugins/{family}-shared/manifests/{runtime}-skills.txt"
    if not manifest.is_file():
        return None
    return sum(
        1
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    )


def described_skill_count(description: str):
    found = DESCRIBED_SKILL_COUNT.findall(description)
    return int(found[-1]) if found else None


def check_description(label: str, description, version: str, family: str, runtime: str) -> None:
    if not isinstance(description, str):
        return
    found = VERSION_IN_DESCRIPTION.search(description)
    if not found:
        errors.append(f"{label} の description に `(vX.Y.Z)` 形式の版数がない")
    elif found.group(1) != version:
        errors.append(
            f"{label} の description の版数が古い"
            f"（description: v{found.group(1)} / {family}-claude の plugin.json: v{version}）"
        )
    expected = manifest_skill_count(family, runtime)
    if expected is None:
        return
    # 抽出できないこと自体をエラーにする。素通りさせると、Skill 数の記述を消すか書式を変える
    # だけでこの検査を無効化できてしまう。
    described = described_skill_count(description)
    if described is None:
        errors.append(
            f"{label} の description から Skill 数を読み取れない"
            f"（`<数> ... skills` の形で書く。{runtime}-skills.txt: {expected}）"
        )
    elif described != expected:
        errors.append(
            f"{label} の description の Skill 数が manifest と食い違う"
            f"（description: {described} / {runtime}-skills.txt: {expected}）"
        )


for family in families:
    claude_plugin_path = root / f"plugins/{family}-claude/.claude-plugin/plugin.json"
    if not claude_plugin_path.is_file():
        continue
    claude_plugin = read_json(claude_plugin_path)
    version = claude_plugin.get("version")
    if not isinstance(version, str):
        errors.append(f"{family} の claude plugin.json に version がない")
        continue
    check_description(
        f"plugins/{family}-claude/.claude-plugin/plugin.json",
        claude_plugin.get("description"),
        version,
        family,
        "claude",
    )
    codex_plugin_path = root / f"plugins/{family}-codex/.codex-plugin/plugin.json"
    if codex_plugin_path.is_file():
        codex_plugin = read_json(codex_plugin_path)
        if codex_plugin.get("version") != version:
            errors.append(
                f"plugins/{family}-codex/.codex-plugin/plugin.json の version が claude 版と"
                f"食い違う（codex: {codex_plugin.get('version')} / claude: {version}）"
            )
        check_description(
            f"plugins/{family}-codex/.codex-plugin/plugin.json",
            codex_plugin.get("description"),
            version,
            family,
            "codex",
        )
    for plugin in claude_marketplace.get("plugins", []):
        if plugin.get("source") != f"./plugins/{family}-claude":
            continue
        check_description(
            f".claude-plugin/marketplace.json の {plugin.get('name')}",
            plugin.get("description"),
            version,
            family,
            "claude",
        )

for family in families:
    shared = root / f"plugins/{family}-shared"
    for manifest in sorted((shared / "manifests").glob("*-skills.txt")):
        runtime = manifest.name.removesuffix("-skills.txt")
        runtime_skills = root / f"plugins/{family}-{runtime}/skills"
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
            if not (shared / "skills" / skill / "SKILL.md").is_file():
                errors.append(f"{family} shared skill missing: {skill}")
            if not (runtime_skills / skill / "SKILL.md").is_file():
                errors.append(f"{family} {runtime} runtime skill missing: {skill}")

# Claude 版の plugin.json は skills を配列で明示する。配列に載っていない Skill は
# Claude Code から読み込まれないため、manifest と一致していないと配布漏れになる。
# 生成物のディレクトリだけを見る上の検査では検出できないので、ここで突き合わせる。
for family in families:
    claude_manifest = root / f"plugins/{family}-shared/manifests/claude-skills.txt"
    claude_plugin_json = root / f"plugins/{family}-claude/.claude-plugin/plugin.json"
    if not (claude_manifest.is_file() and claude_plugin_json.is_file()):
        continue
    expected = [
        line.split("#", 1)[0].strip()
        for line in claude_manifest.read_text(encoding="utf-8").splitlines()
    ]
    expected_set = {name for name in expected if name}
    declared = json.loads(claude_plugin_json.read_text(encoding="utf-8")).get("skills")
    if not isinstance(declared, list):
        # 配列以外（ディレクトリ指定・欠落）を許すと、この突き合わせが黙って skip され
        # 配布漏れの再発を検出できなくなる。Claude 版は配列で明示する形式に固定する。
        errors.append(
            f"{family} の claude plugin.json の skills が配列ではない"
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
                    "claude plugin.json の skills 配列に文字列以外の項目がある"
                    f"（{type(entry).__name__}）"
                )
                continue
            declared_entries.add(entry)
        for missing in sorted(expected_entries - declared_entries):
            errors.append(
                f"{family} の claude plugin.json の skills 配列に載っていない: {missing}"
                "（manifest には登録済み）"
            )
        for extra in sorted(declared_entries - expected_entries):
            errors.append(
                f"{family} の claude plugin.json の skills 配列に余分な項目: {extra}"
                "（manifest に無い、またはパスが `./skills/<Skill 名>` の形式でない）"
            )

for mcp in sorted((root / "plugins/mcp/shared").iterdir()):
    if not mcp.is_dir():
        continue
    for runtime in ("claude", "codex", "kiro"):
        runtime_dir = root / "plugins/mcp" / runtime / mcp.name
        if not runtime_dir.is_dir():
            errors.append(f"MCP runtime directory missing: plugins/mcp/{runtime}/{mcp.name}")
            continue
        if not (runtime_dir / ".mcp.json").is_file():
            errors.append(f"MCP config missing: plugins/mcp/{runtime}/{mcp.name}/.mcp.json")
        if runtime == "kiro" and not (runtime_dir / "install.sh").is_file():
            errors.append(f"Kiro MCP installer missing: plugins/mcp/kiro/{mcp.name}/install.sh")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("runtime plugin manifests and generated paths are valid")
PY

if command -v claude >/dev/null 2>&1; then
  for family in "${FAMILIES[@]}"; do
    [ -d "$ROOT_DIR/plugins/$family-claude" ] || continue
    run claude plugin validate "$ROOT_DIR/plugins/$family-claude"
  done
  run claude plugin validate "$ROOT_DIR/.claude-plugin/marketplace.json"
else
  echo "==> claude CLI not found; skipped claude plugin validate"
fi

for family in "${FAMILIES[@]}"; do
  installer="$ROOT_DIR/plugins/$family-kiro/install.sh"
  [ -f "$installer" ] || continue
  run bash "$installer" --dry-run >/dev/null
done

# --with-codex を持つのは NDF の installer だけ（Codex 向け Skill も併せて配置する経路）。
# family 共通の引数ではないため、ここだけは対象を明示して検査する。
run bash "$ROOT_DIR/plugins/ndf-kiro/install.sh" --dry-run --with-codex >/dev/null

while IFS= read -r installer; do
  run bash "$installer" --dry-run >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp/kiro" -name install.sh | sort)

run python3 "$ROOT_DIR/scripts/check-markdown-links.py" --root "$ROOT_DIR"

echo "runtime plugin validation passed"
