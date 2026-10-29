#!/usr/bin/env python3
"""agy に配るエージェント定義（`dev.agy/agents/*.md`）を生成・確認する（`scripts/build-runtime-plugins.sh` の一部）。

agy 1.4 系はエージェント定義の `model` に tier 名（`flash` / `pro` / `inherit`）しか受け付けず、
Claude Code の型の名前（`sonnet` / `opus`）を持つ定義を読み飛ばす（#2087）。そこで `agents/*.md` を写し、
frontmatter の `model:` の 1 行だけを対応表で tier 名へ置き換える。YAML として読み直さないのは、
写しと元の差を `model:` の 1 行に限ってバイト列で確かめるためである（設計の決定 3）。

    python3 scripts/lib/agy_agents.py <plugin ディレクトリ> <true|false>

2 つ目が `true` なら書かずに確かめ、食い違いを標準エラーに出して終了コード 1 で終わる。
"""

from __future__ import annotations

import sys
from pathlib import Path

# agy 1.4 系が `model` に受け付ける tier 名。欄が無いときは agy の既定（`inherit`）になる。
AGY_TIERS = frozenset({"flash", "pro", "inherit"})

# Claude Code の型の名前 → agy の tier 名。安い型は安い tier へ、高い型は高い tier へ写す（設計の決定 2）。
# ここに無い値は生成を止める（設計の決定 4）。足すときは 1 行足す。
MODEL_MAP = {
    "sonnet": "flash",
    "opus": "pro",
    "haiku": "flash",
    "inherit": "inherit",
}


class UnknownModel(Exception):
    """対応表に無い `model` の値。"""


def model_value(line: str) -> str:
    """`model: <値>` の行から値を取り出す（引用符は外す）。"""
    value = line.split(":", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def frontmatter_model(text: str) -> str | None:
    """frontmatter の `model` の値。欄が無ければ None。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("model:"):
            return model_value(line)
    return None


def agy_copy(text: str, model_map: dict[str, str]) -> str:
    """frontmatter の `model:` の 1 行だけを tier 名へ置き換えた写し。欄が無ければそのまま返す。"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if line.startswith("model:"):
            value = model_value(line)
            if value not in model_map:
                raise UnknownModel(value)
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = f"model: {model_map[value]}{ending}"
            break
    return "".join(lines)


def map_errors(model_map: dict[str, str]) -> list[str]:
    """対応表の値のうち tier 名でないもの。"""
    return [f"Model map value is not an agy tier: {key}: {value}" for key, value in sorted(model_map.items()) if value not in AGY_TIERS]


def sync(plugin_dir: Path, check: bool, model_map: dict[str, str] | None = None) -> bool:
    """生成物を書く（`check` なら確かめる）。食い違いがあれば偽。"""
    model_map = MODEL_MAP if model_map is None else model_map
    dev_dir = plugin_dir / "dev.agy"
    source_dir = plugin_dir / "agents"
    if not dev_dir.is_dir():
        return True
    root = plugin_dir.resolve().parents[1]

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    errors = map_errors(model_map)
    for message in errors:
        print(message, file=sys.stderr)
    if errors:
        return False

    out_dir = dev_dir / "agents"
    expected = _expected_copies(source_dir, out_dir, model_map, rel)
    if expected is None:
        return False
    if check:
        return _check_copies(out_dir, expected, rel)
    _write_copies(out_dir, expected)
    return True


def _expected_copies(source_dir: Path, out_dir: Path, model_map: dict[str, str], rel) -> dict[Path, str] | None:
    """写しの置き場 → 中身。対応表に無い `model` があれば標準エラーに出して None。"""
    sources = sorted(source_dir.glob("*.md")) if source_dir.is_dir() else []
    expected: dict[Path, str] = {}
    ok = True
    for source in sources:
        text = source.read_text(encoding="utf-8")
        try:
            expected[out_dir / source.name] = agy_copy(text, model_map)
        except UnknownModel as exc:
            print(f"Unknown model for agy: {rel(source)}: {exc}", file=sys.stderr)
            ok = False
    return expected if ok else None


def _check_copies(out_dir: Path, expected: dict[Path, str], rel) -> bool:
    """書かずに確かめる。食い違いを標準エラーに出し、あれば偽。"""
    if out_dir.is_symlink():
        print(f"Generated agents must be files, not a symlink: {rel(out_dir)}", file=sys.stderr)
        return False
    ok = True
    actual = {p for p in out_dir.iterdir()} if out_dir.is_dir() else set()
    for path, content in sorted(expected.items()):
        if not path.is_file():
            print(f"Generated agent missing: {rel(path)}", file=sys.stderr)
            ok = False
        elif path.read_text(encoding="utf-8") != content:
            print(f"Generated agent is out of date: {rel(path)}", file=sys.stderr)
            ok = False
    for path in sorted(actual - set(expected)):
        print(f"Generated agent is stale: {rel(path)}", file=sys.stderr)
        ok = False
    return ok


def _write_copies(out_dir: Path, expected: dict[Path, str]) -> None:
    """写しを書き、元に無い写しを消す。"""
    # 古い symlink（`../agents`）を外してから書く。外さずに書くと元の定義を書き換える。
    if out_dir.is_symlink():
        out_dir.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        path.write_text(content, encoding="utf-8")
    for path in sorted(set(out_dir.iterdir()) - set(expected)):
        path.unlink()


def main(argv: list[str]) -> int:
    plugin_dir, check = Path(argv[0]), argv[1] == "true"
    return 0 if sync(plugin_dir, check) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
