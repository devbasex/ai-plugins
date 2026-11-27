"""起動の設定（`.ndf/launch.json`）を読んで検証し、工程ごとの指定と承認の対象を返す（#1959）。

読むのはリポジトリの根の設定と、工程が指す MCP の定義ファイルである。壊れた指定は黙って捨てず、ファイルとキーの位置を載せた
`precondition` で止める。文には定義の値（引数・`env`・`headers`・URL）も `allow` の指定子も載せない。資格情報が書かれうるためである。
承認の対象は `permissions` の全体と定義ファイルの中身で、ハッシュと承認の記録は `launch_approval` が持つ。

full の起動 1 回の指定は `prepared` が解く。許可を広げる指定（`load`・`allow`・`mcp`）があれば承認を確かめ、`mcp` の定義ファイルは
確かめた中身のコピーを渡す（元のパスを渡すと、確かめた後の書き換えがそのまま読まれる）。承認は全工程の定義ファイルをまとめたハッシュと照らすので、
どれか 1 つが読めなければ、承認の要る起動はすべて止まる（設計書の「受け入れる限界」）。止まった文は読めなかったファイルを示す。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Union

from filelock import FileLock, Timeout

from ndf import contracts, identity
from ndf.adapters import launch_approval
from ndf.adapters.secret_forms import holds_secret
from ndf.contracts import facts, internal

SCHEMA = "schemas/ndf-settings/launch.schema.json"
# 反映の途中で反映先を退避した名前（`launch_approval` の置き換えの手順）
REFLECTING = ".reflecting"
ALL_STEPS = "*"
INHERIT = "inherit"
# `inherit` の起動で claude が尋ねずに読むリポジトリの MCP の設定
REPO_MCP = ".mcp.json"
SPEC_KEYS = ("load", "allow", "mcp")
# MCP の定義のコピーを置く保存先の行（表 ndf-files。`<id>` は `<PID>-<一意な値>`）
COPIES = "launch-mcp"
# コピーのディレクトリ `<id>/` の隣に置く管理ファイル `<id>.owner` の拡張子。オーナーはコピーを使う間このファイルの錠を
# 取り続け、中身には NDF が作ったコピーであることの記録（`_owner_record`）を書く。掃除は錠が外れ、記録が合うものだけを消す
OWNER_SUFFIX = ".owner"
# コピーの `<id>`（`<PID>-<一意な値>`）の形。掃除はこの形の名前だけを見る
COPY_ID = re.compile(r"[0-9]+-[0-9a-f]{16}")
# 管理ファイルの錠を、作った直後に別の起動の掃除と取り合って取り損ねたとき、別の `<id>` で取り直す回数（最初の 1 回を含む）
OWNER_ATTEMPTS = 3
# 記録の終わりを示す文字。記録は JSON の後にこの 1 文字を付けて書く。これで終わらない中身は、書き込みの途中で落ちた記録である
RECORD_END = "\n"

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
    """`<root>/.ndf/launch.json`（`source` があればそのファイル）を読み、ファイル全体を検証する。無ければ空の設定。

    検証するのはスキーマと `allow` の秘密の形まで。MCP の定義ファイルのパス（根の外を指さないか）は、定義ファイルを読む
    承認の対象の組み立て（`_target_files`）で確かめる。指定の無い工程の起動は定義ファイルを読まないので、ほかの工程の
    パスの誤りでは止まらない。
    """
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
    files, sha = _read_target(settings)
    return Target(sha, _listing(settings, files, sha))


def _read_target(settings: Settings) -> tuple[dict[str, Any], str]:
    """承認の対象の定義ファイルを読み、読んだ中身と対象全体のハッシュを返す。承認の対象のハッシュはここだけで求める。

    一覧（定義ファイルごとのハッシュを含む）は作らない。一覧が要る経路（`approval_target` と未承認の停止）だけが作る。
    """
    files = _target_files(settings)
    return files, launch_approval.target_digest(settings.permissions, files)


def _target_files(settings: Settings) -> dict[str, Any]:
    """承認の対象の定義ファイル（リポジトリの根からの相対パスから中身への対応。`inherit` の `.mcp.json` が無ければ None）。"""
    steps = _given_object(settings.permissions, "steps")
    for name, spec in steps.items():  # 読む前に、書かれた位置を添えて根の外のパスを止める
        for i, rel in enumerate(_mcp_files(spec)):
            _in_root(settings.root, rel, f"{settings.path} の permissions/steps/{name}/mcp/{i}")
    files: dict[str, Any] = check_definitions(settings, sorted({rel for spec in steps.values() for rel in _mcp_files(spec)}))
    if any(spec.get("mcp") == INHERIT for spec in steps.values()):
        repo_mcp = _in_root(settings.root, REPO_MCP, REPO_MCP)  # リンクの先が根の外なら、読む前に止める
        files[REPO_MCP] = _mcp_definition(repo_mcp, REPO_MCP) if repo_mcp.exists() else None
    return files


def _listing(settings: Settings, files: Mapping[str, Any], sha: str) -> dict[str, Any]:
    """承認の対象の、出してよい一覧（定義の中身を含まない）。"""
    steps = _given_object(settings.permissions, "steps")
    return {
        "sha256": sha,
        "steps": {name: {k: spec[k] for k in SPEC_KEYS if k in spec} for name, spec in steps.items()},
        "unsandboxed": list(_given_words(settings.permissions, "unsandboxed")),
        "files": [_file_listing(rel, raw) for rel, raw in files.items()],
    }


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


# ---------------------------------------------------------------- 起動 1 回の指定（prepared）


@dataclass(frozen=True)
class Grant:
    """full の起動 1 回の指定。`sha256` は承認を確かめた対象のハッシュで、指定が空なら None（承認を調べていない）。

    `mcp_files` は確かめた中身のコピー（`prepared` を抜けると消える）。`inherit` なら環境の MCP をそのまま読む。
    """

    load: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    inherit: bool = False
    mcp_files: tuple[Path, ...] = ()
    sha256: Optional[str] = None
    root: Optional[Path] = None
    repo: Optional[str] = None

    def verify(self) -> Optional[str]:
        """承認の対象を読み直し、確かめた後に変わったか取り消されていれば理由の 1 行。同じなら None。"""
        if self.sha256 is None or self.root is None or self.repo is None:
            return None
        try:
            now = _read_target(load(self.root))[1]
        except internal.InternalError as exc:
            return f"起動の直前に起動の設定を読み直せない: {exc}"
        if now != self.sha256:
            return f"起動の設定か MCP の定義ファイルが、承認を確かめた後に変わった（確かめたハッシュ {self.sha256[:8]}）。{_approval_hint(self.root)}"
        if not launch_approval.is_approved(self.repo, self.sha256):
            return f"起動の設定の承認が、確かめた後に取り消された（ハッシュ {self.sha256[:8]}）。{_approval_hint(self.root)}"
        return None

    @property
    def verifier(self) -> Optional[Callable[[], Optional[str]]]:
        """子プロセスを起動する直前に打つ照らし直し。承認を調べていない（指定が空の）起動は None。"""
        return self.verify if self.sha256 is not None else None


def launch_root(cwd: Path) -> Path:
    """`cwd` から親へたどって最初に `.git`（ディレクトリかファイル）を持つディレクトリ。無ければ `cwd`。git は起動しない。"""
    here = cwd.absolute()
    for place in (here, *here.parents):
        if (place / ".git").exists():
            return place
    return here


def _approval_hint(root: Path, sha256: Optional[str] = None) -> str:
    """承認の手順の文（Skill と、端末で打つコマンド）。コマンドは `ndf` の絶対パスで出す（利用者の PATH に無くても打てる）。

    パスはシェル用に引用する（空白で引数が分かれず、特殊文字を解釈させない）。
    """
    ident = identity.load()
    ndf = shlex.quote(str(identity.entrypoint(ident.root)))
    command = f"cd {shlex.quote(str(root))} && {ndf} launch-config approve" + (f" --sha256 {sha256}" if sha256 else "")
    return f"/{ident.prefix}launch-config で承認するか、端末で `{command}` を打ってから打ち直す"


@contextlib.contextmanager
def prepared(name: str, cwd: Path) -> Iterator[Grant]:
    """工程 `name` の full の起動 1 回の指定を解き、承認を確かめ、MCP の定義のコピーを作って渡す。抜けるときにコピーを消す。

    指定が空（`load`・`allow`・`mcp` が無い）なら承認を調べず、git も起動しない。壊れた指定・未承認・承認の対象を読めない場合は
    `precondition` で止まる（起動しない）。
    """
    root = launch_root(cwd)
    settings = load(root)
    spec = step_spec(settings, name)
    if spec.empty:
        yield Grant(root=root)
        return
    try:
        files, sha = _read_target(settings)
    except internal.InternalError as exc:
        raise _precondition(
            f"工程 {name} の起動を止めた。承認の要る起動は、すべての工程の MCP の定義ファイルをまとめて承認と照らすため、"
            f"どれか 1 つでも読めないと止まる: {exc}"
        ) from None
    repo = launch_approval.repo_key(root)
    if not launch_approval.is_approved(repo, sha):
        listing = json.dumps(_listing(settings, files, sha), ensure_ascii=False)
        raise _precondition(
            f"工程 {name} の起動の承認の対象（起動の設定 {settings.path} と、全工程の MCP の定義ファイル）は、このマシンで承認されていない"
            "（どれかの承認の後の書き換え・このマシンで未承認・取り消しの後。書き換えた定義ファイルは承認の対象の files のハッシュで分かる）。"
            f"承認の対象: {listing}。{_approval_hint(root, sha)}"
        )
    if not isinstance(spec.mcp, tuple):
        yield Grant(spec.load, spec.allow, spec.mcp == INHERIT, (), sha, root, repo)
        return
    with _copies([files[rel] for rel in spec.mcp]) as copies:
        yield Grant(spec.load, spec.allow, False, copies, sha, root, repo)


def copies_dir() -> Path:
    """MCP の定義のコピーを置く保存先（状態の保存先の下。表 ndf-files の行 `launch-mcp`）。"""
    return identity.load().state_dir / Path(facts.value("ndf-files", COPIES, "path")).parent


@contextlib.contextmanager
def _copies(definitions: list[Any]) -> Iterator[tuple[Path, ...]]:
    """確かめた定義の中身を、所有者だけが読めるコピー（0700 のディレクトリの 0600 のファイル）にして渡し、抜けるときに消す。

    作る順は、管理ファイルの錠を取る → ディレクトリを作る → 記録（ディレクトリの実体を含む）を書く → 定義のコピーを書く。
    消すときは、ディレクトリを消し終えてから管理ファイルを消す。どこで落ちても、次の掃除が残りを見分けて消せる（`_sweep_copies`）。
    """
    base = copies_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        _sweep_copies(base)
        ident, lock = _take_owner(base)
    except (OSError, Timeout) as exc:
        raise _precondition(f"MCP の定義のコピーの保存先 {base} を作れない（{type(exc).__name__}）") from None
    where = base / ident
    try:
        try:
            where.mkdir(mode=0o700)
            os.chmod(where, 0o700)
            _write_record(base, ident, where.lstat())
            paths = tuple(_write_private(where / f"{i}.json", json.dumps(raw, ensure_ascii=False)) for i, raw in enumerate(definitions))
        except OSError as exc:
            raise _precondition(f"MCP の定義のコピーを {where} に書けない（{type(exc).__name__}）") from None
        yield paths
    finally:
        _reclaim_locked(base, ident)
        lock.release()


def _take_owner(base: Path) -> tuple[str, FileLock]:
    """新しい `<id>` の管理ファイルを作って錠を取る。取り損ねたら別の `<id>` で取り直す（`OWNER_ATTEMPTS` 回まで）。

    管理ファイルを作ってから錠を取るまでの間に、別の起動の掃除が空の管理ファイルを見つけて先に錠を取り、消すことがある。
    filelock はこのとき、待たない錠を `Timeout` で返す。その名前は掃除に任せ、別の名前で取り直す。
    """
    left = OWNER_ATTEMPTS
    while True:
        ident = f"{os.getpid()}-{secrets.token_hex(8)}"
        lock = _owner_lock(base, ident)
        left -= 1
        try:
            lock.acquire()
        except Timeout:
            if not left:
                raise
            continue  # 掃除と重なった名前は捨て、別の名前で取り直す
        return ident, lock


def _owner_lock(base: Path, ident: str) -> FileLock:
    """コピー `<id>` の管理ファイルの錠。待たずに取る（使用中なら `Timeout`）。flock の錠はカーネルが持つので、状態の保存先を
    共有する PID 名前空間の違うコンテナどうしでも、使用中と分かる。filelock は開くときにファイルを切り詰めないので、記録は残る。"""
    return FileLock(str(base / f"{ident}{OWNER_SUFFIX}"), timeout=0, mode=0o600)


def _owner_record(ident: str, st: os.stat_result) -> dict[str, Any]:
    """管理ファイルに書く記録。表 ndf-files の行の名前（`row`）、ディレクトリの名前（`id`）、作った直後のディレクトリの
    実体（`lstat` の `dev`・`ino`）。掃除は JSON の値として比べ、終わりの `RECORD_END` のほかは書式（空白・キーの順）を問わない。"""
    return {"row": COPIES, "id": ident, "dev": st.st_dev, "ino": st.st_ino}


def _write_record(base: Path, ident: str, st: os.stat_result) -> None:
    """錠を取った管理ファイル（空）に記録を書く。記録の終わりに `RECORD_END` を付け、途中で落ちた書き込みを完成した記録と
    見分けられるようにする（JSON の中に改行は無いので、途中までの中身は `RECORD_END` で終わらない）。"""
    fd = os.open(base / f"{ident}{OWNER_SUFFIX}", os.O_WRONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(_owner_record(ident, st), ensure_ascii=False) + RECORD_END)


def _write_private(path: Path, text: str) -> Path:
    """所有者だけが読めるファイル（0600）を新しく作って `text` を書く。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _sweep_copies(base: Path) -> None:
    """落ちた起動の消し残りを消す。錠を待たずに取れた管理ファイルだけを見て、`_reclaim_locked` が消すものを決める。

    錠を取られているもの（使用中）は触らない。
    """
    for owner in base.glob(f"*{OWNER_SUFFIX}"):
        ident = owner.name.removesuffix(OWNER_SUFFIX)
        if COPY_ID.fullmatch(ident) and not owner.is_symlink():
            _reclaim_unused(base, ident)


def _reclaim_unused(base: Path, ident: str) -> None:
    """コピー `<id>` の錠が待たずに取れれば、`_reclaim_locked` で残りを消す。"""
    lock = _owner_lock(base, ident)
    try:
        lock.acquire()
    except Timeout:  # noqa: NDF401 ファイル: オーナーが使用中のコピーは消さない
        return
    try:
        _reclaim_locked(base, ident)
    finally:
        lock.release()


def _reclaim_locked(base: Path, ident: str) -> None:
    """錠を取った管理ファイル `<id>.owner` の中身で、消すものを決めて消す。管理ファイルは最後に消す。

    | 管理ファイルの中身 | ディレクトリ `<id>` | 消すもの |
    | --- | --- | --- |
    | 空か、`RECORD_END` で終わらない（記録を書き終える前に落ちた） | 無いか空 | 空のディレクトリと管理ファイル（中身が無いので利用者のデータは失われない） |
    | 空か、`RECORD_END` で終わらない | 中身がある | 何も消さない（NDF が作ったと確かめられない） |
    | 記録の形で、`id` が名前と同じ | 記録の `dev`・`ino` と同じ実体か、無い | ディレクトリと管理ファイル |
    | 記録の形で、`id` が名前と同じ | 記録の `dev`・`ino` と違う実体（リンクを含む。記録の後に置き換わった） | 管理ファイルだけ（ディレクトリは NDF が作ったものでない） |
    | `RECORD_END` で終わるが、記録の形でないか `id` が違う | — | 何も消さない |
    """
    owner, where = base / f"{ident}{OWNER_SUFFIX}", base / ident
    text = _read_owner(owner)
    if text is None:
        return
    if not text.endswith(RECORD_END):  # 空か、書き込みの途中で落ちた記録
        _remove_if_empty(where)
        if where.exists():
            return  # 中身のあるディレクトリがある。NDF が作ったと確かめられない
        owner.unlink(missing_ok=True)
        return
    record = _parsed_record(text)
    if record is None or record["id"] != ident:
        return
    if _is_recorded_dir(where, record):
        shutil.rmtree(where, ignore_errors=True)
        if _is_recorded_dir(where, record):
            return  # 消し切れなかった。記録を残して、次の掃除に任せる
    owner.unlink(missing_ok=True)


def _remove_if_empty(where: Path) -> None:
    """`where` が空のディレクトリなら消す。中身があるもの・ディレクトリでないものは消さない。"""
    if where.is_dir() and not where.is_symlink() and not any(where.iterdir()):
        where.rmdir()


def _read_owner(owner: Path) -> Optional[str]:
    """管理ファイルの中身。読めなければ None（NDF が作ったと確かめられないので消さない）。"""
    try:
        return owner.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めない管理ファイルは消さない
        return None


def _parsed_record(text: str) -> Optional[dict[str, Any]]:
    """記録の形（`_owner_record` と同じキーと型、`row` が `launch-mcp`）なら解いた値、違えば None。"""
    try:
        raw = json.loads(text)
    except ValueError:  # noqa: NDF401 ファイル: JSON でない中身は NDF の記録でない
        return None
    shape = {"row": str, "id": str, "dev": int, "ino": int}
    if not isinstance(raw, dict) or set(raw) != set(shape) or raw["row"] != COPIES:
        return None
    return raw if all(isinstance(raw[k], t) and not isinstance(raw[k], bool) for k, t in shape.items()) else None


def _is_recorded_dir(where: Path, record: Optional[Mapping[str, Any]]) -> bool:
    """`where` が記録の実体（`dev`・`ino`）と同じディレクトリか。リンクは同じと見なさない。"""
    if record is None or where.is_symlink() or not where.is_dir():
        return False
    st = where.lstat()
    return (st.st_dev, st.st_ino) == (record["dev"], record["ino"])
