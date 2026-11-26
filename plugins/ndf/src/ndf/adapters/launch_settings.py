"""起動の設定（`.ndf/launch.json`）を読んで検証し、工程ごとの指定と承認の対象を返す（#1959）。

読むのはリポジトリの根の設定と、工程が指す MCP の定義ファイルである。壊れた指定は黙って捨てず、ファイルとキーの位置を載せた
`precondition` で止める。文には定義の値（引数・`env`・`headers`・URL）も `allow` の指定子も載せない。資格情報が書かれうるためである。
承認の対象は `permissions` の全体と定義ファイルの中身で、ハッシュと承認の記録は `launch_approval` が持つ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from ndf import contracts
from ndf.adapters import launch_approval
from ndf.adapters.secret_forms import holds_secret
from ndf.contracts import facts, internal

SCHEMA = "schemas/ndf-settings/launch.schema.json"
# 反映の途中で反映先を退避した名前（`launch_approval` の置き換えの手順）
REFLECTING = launch_approval.REFLECTING
ALL_STEPS = "*"
INHERIT = "inherit"
# `inherit` の起動で claude が尋ねずに読むリポジトリの MCP の設定
REPO_MCP = ".mcp.json"
SPEC_KEYS = ("load", "allow", "mcp")

Mcp = Union[None, str, tuple[str, ...]]


@dataclass(frozen=True)
class Settings:
    """読んだ起動の設定。`permissions` はファイルが無ければ空。"""

    root: Path
    path: Path
    permissions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepSpec:
    """1 つの工程の指定。`mcp` は None（読まない）・`inherit`・定義ファイルのパスの並び。"""

    load: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    mcp: Mcp = None

    @property
    def empty(self) -> bool:
        """許可を広げない指定か（承認を調べずに起動してよい）。"""
        return not self.load and not self.allow and self.mcp is None


@dataclass(frozen=True)
class Target:
    """承認の対象のハッシュと、出力してよい一覧（定義の中身を含まない）。"""

    sha256: str
    listing: dict[str, Any]


def _precondition(detail: str) -> internal.InternalError:
    return internal.InternalError("precondition", detail)


def _given_object(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """書かれたオブジェクトのキー。無ければ空（指定なし）。形はスキーマで確かめ済み。"""
    value = raw.get(key)
    return {} if value is None else value


def _given_words(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """書かれた文字列の並びのキー。無ければ空（指定なし）。"""
    value = raw.get(key)
    return () if value is None else tuple(value)


def launch_file(root: Path) -> Path:
    """リポジトリの根 `root` の起動の設定のパス（表 ndf-files の行 launch）。"""
    row = facts.row("ndf-files", "launch")
    return root / facts.value("locations", row["location"], "path") / row["path"]


def _launch_json(path: Path, label: str) -> Any:
    try:
        return contracts.parse_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _precondition(f"{label} が無い") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise _precondition(f"{label} の読み取りに失敗した（{type(exc).__name__}）") from None
    except (ValueError, contracts.ContractError):  # 同じキーが 2 つあれば parse_json は ContractError（ValueError の派生でない）を投げる
        raise _precondition(f"{label} が JSON でない（同じキーが 2 つある場合を含む）") from None


def load(root: Path, source: Optional[Path] = None) -> Settings:
    """`<root>/.ndf/launch.json`（`source` があればそのファイル）を読み、ファイル全体を検証する。無ければ空の設定。"""
    path = source if source is not None else launch_file(root)
    if not path.exists():
        if path.with_name(path.name + REFLECTING).exists():
            raise _precondition(f"{path} は反映の途中で止まっている。`ndf launch-config approve` を打ち直して片付ける")
        return Settings(root, path)
    raw = _launch_json(path, str(path))
    broken = contracts.broken_places(raw, SCHEMA)
    if broken:
        raise _precondition(f"{path} がスキーマに合わない: {'; '.join(broken)}")
    permissions = _given_object(raw, "permissions")
    for name, spec in _given_object(permissions, "steps").items():
        where = f"{path} の permissions/steps/{name}"
        for i, word in enumerate(_given_words(spec, "allow")):
            if holds_secret(word):
                raise _precondition(
                    f"{where}/allow/{i} に秘密の形の値がある（値は出さない）。資格情報は環境変数で渡し、指定子には $名前 の参照だけを書く"
                )
        for i, rel in enumerate(_mcp_files(spec)):
            _in_root(root, rel, f"{where}/mcp/{i}")
    return Settings(root, path, permissions)


def _mcp_files(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """工程の指定が読む定義ファイル。`inherit` と指定なしは空。"""
    mcp = spec.get("mcp")
    return tuple(mcp) if isinstance(mcp, list) else ()


def _in_root(root: Path, rel: str, where: str) -> Path:
    """リポジトリの根の中の定義ファイル（`..` とシンボリックリンクをたどった先も根の中）。外なら止める。"""
    if Path(rel).is_absolute():
        raise _precondition(f"{where} が絶対パス。リポジトリの根からの相対パスで書く")
    base = root.resolve()
    if not (base / rel).resolve().is_relative_to(base):
        raise _precondition(f"{where} がリポジトリの根の外を指す")
    return base / rel


def step_spec(settings: Settings, name: str) -> StepSpec:
    """工程 `name` の指定。工程の指定にあるキーは `*` の同じキーを置き換え、無いキーは `*` を使う。"""
    steps = _given_object(settings.permissions, "steps")
    merged = {**_given_object(steps, ALL_STEPS), **_given_object(steps, name)}
    mcp = merged.get("mcp")
    return StepSpec(_given_words(merged, "load"), _given_words(merged, "allow"), mcp if mcp is None or mcp == INHERIT else tuple(mcp))


def check_definitions(settings: Settings, paths: Iterable[str]) -> dict[str, Any]:
    """定義ファイル（リポジトリの根からの相対パス）を読み、MCP サーバの定義の形を確かめて、パスから中身への対応を返す。"""
    return {rel: _mcp_definition(_in_root(settings.root, rel, rel), rel) for rel in paths}


def _mcp_definition(path: Path, rel: str) -> dict[str, Any]:
    raw = _launch_json(path, f"MCP の定義ファイル {rel}")
    if not isinstance(raw, dict):
        raise _precondition(f"MCP の定義ファイル {rel} の最上位がオブジェクトでない")
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        raise _precondition(f"MCP の定義ファイル {rel} の mcpServers がオブジェクトでない")
    for name, server in servers.items():
        if not isinstance(server, dict) or not (isinstance(server.get("command"), str) or isinstance(server.get("url"), str)):
            raise _precondition(f"MCP の定義ファイル {rel} の mcpServers/{name} が文字列の command も url も持たない")
    return raw


def approval_target(settings: Settings) -> Target:
    """承認の対象（`permissions`・全工程の定義ファイル・`inherit` があれば `.mcp.json`）のハッシュと一覧。"""
    steps = _given_object(settings.permissions, "steps")
    files: dict[str, Any] = check_definitions(settings, sorted({rel for spec in steps.values() for rel in _mcp_files(spec)}))
    if any(spec.get("mcp") == INHERIT for spec in steps.values()):
        repo_mcp = _in_root(settings.root, REPO_MCP, REPO_MCP)  # リンクの先が根の外なら、読む前に止める
        files[REPO_MCP] = _mcp_definition(repo_mcp, REPO_MCP) if repo_mcp.exists() else None
    sha = launch_approval.target_digest(settings.permissions, files)
    listing = {
        "sha256": sha,
        "steps": {name: {k: spec[k] for k in SPEC_KEYS if k in spec} for name, spec in steps.items()},
        "unsandboxed": list(_given_words(settings.permissions, "unsandboxed")),
        "files": [_file_listing(rel, raw) for rel, raw in files.items()],
    }
    return Target(sha, listing)


def _file_listing(rel: str, raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """定義ファイル 1 つの、出してよい欄（場所・ハッシュ・サーバ名・コマンド名）。"""
    if raw is None:
        return {"path": rel, "sha256": None, "servers": []}
    servers = [{"name": name, "command": _command_name(server)} for name, server in raw["mcpServers"].items()]
    return {"path": rel, "sha256": launch_approval.canonical_sha(raw), "servers": servers}


def _command_name(server: Mapping[str, Any]) -> str:
    """`command` の最初の語のパスを除いた名前。`url` のサーバは接続先を出さず `url` とだけ返す。"""
    command = server.get("command")
    if not isinstance(command, str):
        return "url"
    words = command.split()
    return Path(words[0]).name if words else ""
