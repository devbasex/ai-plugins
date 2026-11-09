"""契約の一覧（`plugins/ndf/contracts/`）の項目を読み、形を確かめる。標準ライブラリだけで書く。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONTRACTS = "plugins/ndf/contracts"
STDIN = ("none", "json", "text", "hook-json")
STDOUT = ("step-result", "json", "kv", "text", "markdown", "none")
FILE_KINDS = ("state", "record", "result", "report")
FILE_PLACES = ("worktree", "main", "tmp", "state-dir", "home")
SETTING_PLACES = ("worktree", "main")
SCOPES = ("team", "personal")
SET_BY = ("user", "runtime", "ci", "ndf")
# 核の対象外の印（#2337 の決定 5）。hook は hook の定義から起動される、runtime はランタイムが直接起動して標準出力を使う
CORE_EXEMPT = ("hook", "runtime")
# 持ち主の契約（入口の行の `lease`。#2342）の単位と鍵
LEASE_UNITS = ("run", "session")
LEASE_KEYS = {"key": str, "unit": str, "window_seconds": int}


class ContractError(Exception):
    """項目が読めない・形が誤っている（終了コード 2）。"""


@dataclass
class Contracts:
    entrypoints: dict[str, dict] = field(default_factory=dict)  # path → 項目
    entry_files: dict[str, str] = field(default_factory=dict)  # path → 項目のファイル（root からの相対）
    files: dict[str, dict] = field(default_factory=dict)  # name → 項目
    settings: dict[str, dict] = field(default_factory=dict)  # file → 項目
    env: dict[str, dict] = field(default_factory=dict)  # name → 項目
    forms: dict[str, dict] = field(default_factory=dict)  # name → 項目（ファイルでなく文の形の受け渡し）


def entry_file_name(path: str) -> str:
    """エントリポイントの項目のファイル名（パスの `/` を `__` にしたもの）。"""
    return path.replace("/", "__") + ".json"


def _read(f: Path) -> dict:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ContractError(f"契約の項目を読めない: {f}: {e}")
    if not isinstance(data, dict):
        raise ContractError(f"契約の項目の最上位がオブジェクトでない: {f}")
    return data


def _need(f: Path, d: dict, keys: dict[str, type | tuple], optional: dict[str, type | tuple] | None = None) -> None:
    optional = optional or {}
    extra = set(d) - set(keys) - set(optional)
    if extra:
        raise ContractError(f"{f.name}: 知らないキー {sorted(extra)}")
    for k, t in keys.items():
        if k not in d:
            raise ContractError(f"{f.name}: {k} が無い")
    for k, t in {**keys, **optional}.items():
        if k in d and not isinstance(d[k], t):
            raise ContractError(f"{f.name}: {k} の型が違う")


def _strs(f: Path, d: dict, k: str) -> None:
    if not all(isinstance(x, str) for x in d.get(k, [])):
        raise ContractError(f"{f.name}: {k} は文字列の並び")


def _one_of(f: Path, d: dict, k: str, allowed: tuple) -> None:
    if d.get(k) not in allowed:
        raise ContractError(f"{f.name}: {k} は {'/'.join(allowed)} のどれか: {d.get(k)}")


def _check_args(f: Path, args: dict) -> None:
    if "unparsed" in args:
        if set(args) != {"unparsed"} or not isinstance(args["unparsed"], str) or not args["unparsed"].strip():
            raise ContractError(f"{f.name}: args の unparsed は理由の文字列だけを持つ")
        return
    _need(f, args, {"options": list, "positionals": list}, {"subcommands": dict})
    for name, sub in args.get("subcommands", {}).items():
        if not isinstance(sub, dict):
            raise ContractError(f"{f.name}: subcommands.{name} はオブジェクト")
        _need(f, sub, {"options": list, "positionals": list})


def _check_entry(f: Path, d: dict) -> None:
    _need(
        f,
        d,
        {
            "version": int,
            "path": str,
            "summary": str,
            "args": dict,
            "env": list,
            "settings": list,
            "stdin": str,
            "stdout": str,
            "writes": list,
            "reads": list,
            "exit": dict,
        },
        {"callers": list, "external": str, "core": dict, "lease": dict},
    )
    if ("callers" in d) == ("external" in d):
        raise ContractError(f"{f.name}: callers と external はどちらか 1 つ")
    if f.name != entry_file_name(d["path"]):
        raise ContractError(f"{f.name}: ファイル名は {entry_file_name(d['path'])} にする")
    for k in ("env", "writes", "reads", "callers"):
        _strs(f, d, k)
    _one_of(f, d, "stdin", STDIN)
    _one_of(f, d, "stdout", STDOUT)
    _check_args(f, d["args"])
    for s in d["settings"]:
        if not isinstance(s, dict) or set(s) != {"file", "keys"} or not isinstance(s["keys"], list):
            raise ContractError(f"{f.name}: settings の要素は file と keys を持つ")
    if not d["exit"] or not all(isinstance(v, str) and v.strip() for v in d["exit"].values()):
        raise ContractError(f"{f.name}: exit は終了コード → 意味（unknown なら理由）")
    if "lease" in d:
        _check_lease(f, d["lease"])
    if "core" in d:
        core = d["core"]
        if set(core) != {"exempt", "evidence"} or core["exempt"] not in CORE_EXEMPT:
            raise ContractError(f"{f.name}: core は exempt（{'/'.join(CORE_EXEMPT)}）と evidence を持つ")
        if not isinstance(core["evidence"], str) or not core["evidence"].strip():
            raise ContractError(f"{f.name}: core.evidence は根拠の文")


def _check_lease(f: Path, lease: dict) -> None:
    """`lease` は副命令の名前（無ければ空）→ 鍵の形・単位・寿命の窓・返す副命令か。"""
    for sub, decl in lease.items():
        where = f"{f.name}: lease[{sub!r}]"
        if not isinstance(decl, dict) or not set(LEASE_KEYS) <= set(decl) or not set(decl) <= {*LEASE_KEYS, "release"}:
            raise ContractError(f"{where} は key・unit・window_seconds と任意の release を持つ")
        if not isinstance(decl["key"], str) or "{" not in decl["key"]:
            raise ContractError(f"{where}.key は入力の名前を {{}} で埋める鍵の形")
        if decl["unit"] not in LEASE_UNITS:
            raise ContractError(f"{where}.unit は {'/'.join(LEASE_UNITS)}")
        if not isinstance(decl["window_seconds"], int) or isinstance(decl["window_seconds"], bool) or decl["window_seconds"] <= 0:
            raise ContractError(f"{where}.window_seconds は正の整数")
        if not isinstance(decl.get("release", False), bool):
            raise ContractError(f"{where}.release は真偽値")


def core_exempt(entry: dict) -> str | None:
    """核の対象外の印（hook / runtime）。対象なら None。"""
    return entry.get("core", {}).get("exempt")


def load(root: Path) -> Contracts:
    """契約の一覧を読む。形が誤っていれば `ContractError`。"""
    base = root / CONTRACTS
    c = Contracts()
    _load_entrypoints(root, base, c)
    _load_files(base, c)
    _load_settings(base, c)
    _load_env(base, c)
    _load_forms(base, c)
    return c


def _load_entrypoints(root: Path, base: Path, c: Contracts) -> None:
    for f in sorted((base / "entrypoints").glob("*.json")):
        d = _read(f)
        _check_entry(f, d)
        c.entrypoints[d["path"]] = d
        c.entry_files[d["path"]] = f.relative_to(root).as_posix()


def _load_files(base: Path, c: Contracts) -> None:
    for f in sorted((base / "files").glob("*.json")):
        d = _read(f)
        # writers: 呼ぶ側が渡したパスへ書く関数（`<path>:<関数>`）。書き先を静的に拾えなくても file-dynamic にしない（#2337）
        _need(f, d, {"name": str, "pattern": str, "kind": str, "place": str, "skill_readers": list}, {"writers": list})
        _strs(f, d, "writers")
        _one_of(f, d, "kind", FILE_KINDS)
        _one_of(f, d, "place", FILE_PLACES)
        if f.name != d["name"] + ".json":
            raise ContractError(f"{f.name}: ファイル名は {d['name']}.json にする")
        c.files[d["name"]] = d


def _load_settings(base: Path, c: Contracts) -> None:
    for f in sorted((base / "settings").glob("*.json")):
        d = _read(f)
        _need(f, d, {"file": str, "place": str, "scope": str, "readers": list, "keys": dict, "summary": str})
        _one_of(f, d, "place", SETTING_PLACES)
        _one_of(f, d, "scope", SCOPES)
        _strs(f, d, "readers")
        _check_setting_keys(f, d)
        if f.name != d["file"] + ".json":
            raise ContractError(f"{f.name}: ファイル名は {d['file']}.json にする")
        c.settings[d["file"]] = d


def _check_setting_keys(f: Path, d: dict) -> None:
    for k, v in d["keys"].items():
        if not isinstance(v, dict) or "summary" not in v or set(v) - {"summary", "migrate_from", "duplicates", "place"}:
            raise ContractError(f"{f.name}: keys.{k} は summary と任意の migrate_from・duplicates・place を持つ")
        # キーの place はファイルの place より先に効く（認可に関わる値をメインディレクトリから読む。lib/decl.py の key_places）
        if "place" in v and v["place"] not in SETTING_PLACES:
            raise ContractError(f"{f.name}: keys.{k}.place は {' / '.join(SETTING_PLACES)} のどれか")


def _load_env(base: Path, c: Contracts) -> None:
    for f in sorted((base / "env").glob("*.json")):
        d = _read(f)
        _need(f, d, {"name": str, "set_by": str, "summary": str})
        _one_of(f, d, "set_by", SET_BY)
        if f.name != d["name"] + ".json":
            raise ContractError(f"{f.name}: ファイル名は {d['name']}.json にする")
        c.env[d["name"]] = d


def _load_forms(base: Path, c: Contracts) -> None:
    for f in sorted((base / "forms").glob("*.json")):
        d = _read(f)
        _check_form(f, d)
        c.forms[d["name"]] = d


# 例の組の形。ndf-next のブロック（文 → 拾うブロックと起動の有無）と、メッセージの形（メッセージ → 正しいか。#2436 の配布の依頼）
CASE_SHAPES: tuple[dict[str, type], ...] = (
    {"text": str, "blocks": list, "launch": bool},
    {"message": dict, "valid": bool},
)


def _check_form(f: Path, d: dict) -> None:
    """受け渡しの形（`forms/`）: 規則の文と、読み手が同じ答えを返すべき例の組（#2347 の決定 8）。"""
    _need(f, d, {"name": str, "summary": str, "rule": str, "writers": list, "readers": list, "cases": list})
    if f.name != d["name"] + ".json":
        raise ContractError(f"{f.name}: ファイル名は {d['name']}.json にする")
    for k in ("writers", "readers"):
        _strs(f, d, k)
    if not d["cases"]:
        raise ContractError(f"{f.name}: cases は 1 つ以上")
    for i, case in enumerate(d["cases"]):
        if not isinstance(case, dict):
            raise ContractError(f"{f.name}: cases[{i}] はオブジェクト")
        shape = next((sh for sh in CASE_SHAPES if set(case) == set(sh)), CASE_SHAPES[0])
        _need(f, case, shape)
        if "blocks" in shape and not all(isinstance(x, str) for x in case["blocks"]):
            raise ContractError(f"{f.name}: cases[{i}].blocks は文字列の並び")


def flat_args(args: dict) -> set[tuple[str, str, str]]:
    """契約の args を `(副命令, "option"|"positional", 名前)` の集合にする（unparsed は空）。"""
    if "unparsed" in args:
        return set()
    out = {("", "option", o) for o in args.get("options", [])} | {("", "positional", p) for p in args.get("positionals", [])}
    for name, sub in args.get("subcommands", {}).items():
        out |= {(name, "option", o) for o in sub.get("options", [])} | {(name, "positional", p) for p in sub.get("positionals", [])}
    return out


def args_of(options: set[tuple[str, str]], positionals: list[tuple[str, str]]) -> dict:
    """拾った引数を契約の args の形にする。位置引数は呼び出しで並びが意味を持つため、登録した順のまま置く。"""
    out: dict = {"options": sorted(o for s, o in options if not s), "positionals": [p for s, p in positionals if not s]}
    subs = sorted(({s for s, _ in options} | {s for s, _ in positionals}) - {""})
    if subs:
        out["subcommands"] = {
            s: {"options": sorted(o for t, o in options if t == s), "positionals": [p for t, p in positionals if t == s]} for s in subs
        }
    return out


def positional_order(args: dict) -> dict[str, list[str]]:
    """契約の args の位置引数を副命令ごとの並びにする（unparsed は空）。"""
    if "unparsed" in args:
        return {}
    out = {"": list(args.get("positionals", []))}
    for name, sub in args.get("subcommands", {}).items():
        out[name] = list(sub.get("positionals", []))
    return out
