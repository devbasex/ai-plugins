"""登録済みアカウントの設定ディレクトリの起動の前の確かめと、共有の項目への symlink（#3604 #3864）。

- symlink の参照先は共有の設定ディレクトリの直下の項目（`<共有>/<項目>`）だけにし、認証ファイルを対象から外す（I1）
- アカウント側の実体の項目を上書き・移動・削除しない（I2）
- 起動の前に、実体がそのアカウントの設定ディレクトリにあること・認証ファイルと設定ファイルの本人・認証の上書きを
  確かめ、外れれば理由の符号を返す（決定 3）。確かめは読むだけで書かない
- トークン・キー・ヘッダー・`apiKeyHelper` の値を、返り・理由の文・例外の文に入れない（I5）。項目名と出所だけを返す

10 系の `scripts/lib/claude_account_dir.py` の symlink の作りを移し、確かめと認証の上書きを足した。
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional, Sequence, Union

from ndf.contracts import facts
from ndf.domains.relay import account_registry as reg

LEGACY_CONFIG_FILE = ".config.json"  # `.claude.json` の古い名前（Claude Code はあれば先に読む）
REMOTE_SETTINGS = "remote-settings.json"  # 管理の設定をサーバーから取った写し
# アカウント固有の項目（symlink にしない）。前方一致は書き込みの一時ファイルと排他を含む
LOCAL_PREFIXES = (reg.CRED_FILE, reg.CONFIG_FILE, ".oauth_refresh.lock")
LOCAL_NAMES = (
    LEGACY_CONFIG_FILE,
    "backups",
    "policy-limits.json",
    "policy-limits.json.stamp.json",
    REMOTE_SETTINGS,
    "mcp-needs-auth-cache.json",
    ".ndf-statusline-auth.json",
    reg.ACCOUNT_FILE,
    reg.USAGE_FILE,
    reg.BASE_FILE,
    reg.BASE_NEXT_FILE,
    reg.PENDING_FILE,
)
DEFAULT_API_URL = "https://api.anthropic.com"  # 登録済みアカウントの子に許す唯一の ANTHROPIC_BASE_URL
MANAGED_FILES = (Path("/etc/claude-code/managed-settings.json"),)
MANAGED_DIR = Path("/etc/claude-code/managed-settings.d")
# account-drop の認証の値の変数（ANTHROPIC_API_KEY・ANTHROPIC_AUTH_TOKEN・CLAUDE_CODE_OAUTH_TOKEN）
AUTH_VALUE_ENV = tuple(r["id"] for r in facts.rows("env", child="account-drop") if r.get("secret"))
PROFILE_LABEL = "Anthropic のプロファイル"
STORED_KEY = "primaryApiKey"  # /login で保存した API キー（設定ファイルにあれば登録先の OAuth より先に使われる）
# 管理の設定の制御キー。これだけの源はポリシーを持たないとみなされ、次の源へ進む
CONTROL_KEYS = frozenset({"managedSourcesBehavior", "wslInheritsWindowsSettings"})
# サーバーから取った写しの env のうち、起動の時点では保留され（下の源の値が効き）、確認の後に効くか起動の時点にだけ
# 読まれて効かない変数。account-drop の変数（認証の値・提供元・プロファイルとフェデレーションの選び手）と接続先
WITHHELD_ENV = frozenset({*(r["id"] for r in facts.rows("env", child="account-drop")), "ANTHROPIC_BASE_URL"})
CONFIG_DIR_ENV = "ANTHROPIC_CONFIG_DIR"  # 起動の時点にだけ読む。サーバーから取った写しの値は効かない
# 起動の時点にだけ読む選択の変数（設定ディレクトリ・プロファイル・フェデレーション）。写しでは適用が保留され、効かない
STARTUP_ONLY_ENV = frozenset({CONFIG_DIR_ENV, "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID"})
# Claude Code の起動引数の形（`claude --help` の 2.1.330 と、そこに出ない値を取るもの）
_ARG_ONE = frozenset(
    {
        "--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact", "--debug-file",
        "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd", "--max-turns",
        "--model", "-n", "--name", "--output-format", "--permission-mode", "--permission-prompt-tool", "--permission-prompts",
        "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--resume-session-at", "--sdk-url",
        "--session-id", "--setting-sources", "--settings", "--system-prompt", "--system-prompt-file",
        "--system-prompt-snapshot",
    }
)  # fmt: skip
_ARG_MANY = frozenset(
    {
        "--add-dir",
        "--allowedTools",
        "--allowed-tools",
        "--betas",
        "--disallowedTools",
        "--disallowed-tools",
        "--file",
        "--mcp-config",
        "--tools",
    }
)
_ARG_OPTIONAL = frozenset(
    {
        "--cloud",
        "-d",
        "--debug",
        "--from-pr",
        "--prompt-suggestions",
        "--remote-control",
        "-r",
        "--resume",
        "--teleport",
        "-w",
        "--worktree",
    }
)
_ARG_NONE = frozenset(
    {
        "--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background", "--bare", "--brief", "--chrome",
        "-c", "--continue", "--dangerously-skip-permissions", "--desktop", "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections", "--fork-session", "--forward-subagent-text", "-h", "--help", "--ide",
        "--include-hook-events", "--include-partial-messages", "--no-chrome", "--no-session-persistence", "-p", "--print",
        "--replay-user-messages", "--restricted", "--safe-mode", "--strict-mcp-config", "--tmux", "--verbose", "-v",
        "--version",
    }
)  # fmt: skip
_READ_FLAGS = ("--settings", "--setting-sources", "--bare", "--restricted")
BARE_ENV = "CLAUDE_CODE_SIMPLE"  # 最小モード（`--bare` が立てる）。認証は ANTHROPIC_API_KEY か --settings の apiKeyHelper だけ

NOT_LOCAL = "not_account_local"
RELOGIN_REASONS = ("no_refresh", "identity_mismatch", "legacy_config")
# 理由の符号 → 画面の 1 行の文（値を含まない）
REASONS = {
    "no_credentials": "認証ファイルが無いか読めない。ndf relay account add {name} で登録し直す",
    "no_refresh": "リフレッシュトークンが空。ndf relay account add {name} で登録し直す",
    "account_unreadable": "アカウントの .claude.json が無いか壊れている。ndf relay account add {name} で登録し直す",
    "identity_mismatch": "アカウントの .claude.json の本人が登録と違う。ndf relay account add {name} で登録し直す",
    "legacy_config": "旧形式の .config.json が残っている。ndf relay account add {name} で登録し直す",
    NOT_LOCAL: "設定ディレクトリか認証・設定のファイルがアカウントの外を指すか、ほかの名前と同じ実体。独立したファイルに戻す",
    "auth_overridden": "設定・環境・起動引数に、登録先の OAuth より上に来る認証か既定でない接続先か、OAuth を使わないモードがある。その項目を外す",
    "auth_unresolved": "認証の上書きを確かめる設定が読めないか、値が解けない。その設定を直す",
}


def is_local(item: str) -> bool:
    """アカウント固有の項目か（symlink にしない）。"""
    return item in LOCAL_NAMES or item.startswith(LOCAL_PREFIXES)


@dataclass
class Links:
    """`link_shared` の結果。`skipped` は同じ名前の実体があって飛ばした項目、`local_only` は共有側に無く
    アカウント側にだけある実体の名前。`projects_shared` はアカウント側の `projects` が共有側と同じ実体か。
    `refused` は共有側の symlink の最終の参照先が認証ファイルかアカウントの保存先の中で、張らなかった項目。"""

    added: int = 0
    skipped: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)
    projects_shared: bool = False
    refused: list[str] = field(default_factory=list)


@dataclass
class Local:
    """`check_local` が確かめた実体。以後の読み書きはこのパスだけを使い、名前を解き直さない。

    `ids` は項目ごとの `(st_dev, st_ino)`（無ければ None）。キーはファイルの名前と `dir`・`config`・`shared`。"""

    account: Path
    config: Path
    shared: Path
    ids: dict[str, Optional[tuple[int, int]]]


@dataclass
class Resolved:
    """Claude Code が採用する実効値と出所。`helper` は確かめにだけ使い、画面・記録・理由の文に出さない。

    `stamps` は読んだか探した源のパスごとの `(st_mtime_ns, st_ino, st_size)`（無ければ None）。
    `profile` は Anthropic の設定ディレクトリの有効なプロファイルの認証の形（無ければ None）。
    `bare` は最小モード（`--bare` か実効の `CLAUDE_CODE_SIMPLE`）の項目名（無ければ None）。最小モードの子は OAuth を読まない。
    `env`（子の環境の秘密を含みうる）と `helper` は `repr` に出さない。"""

    env: dict[str, str] = field(repr=False)
    origin: dict[str, str]
    helper: Optional[str] = field(repr=False)
    helper_origin: Optional[str]
    unresolved: list[str]
    stamps: dict[str, Optional[tuple]]
    profile: Optional[str] = None
    bare: Optional[str] = None


class Launch(NamedTuple):
    """子が実際に受け取る環境・作業ディレクトリ・起動引数（`effective_env` に渡す 3 つ）。"""

    env: Mapping[str, str]
    cwd: Path
    argv: Sequence[str]


@dataclass
class Checked:
    """`check_account` の成功の返り。`resolved` は判定に使った `effective_env` の結果。"""

    local: Local
    resolved: Resolved


class Refused(str):
    """`check_account` の失敗の返り。値は理由の符号（`str` として比べられる）。

    `items` は判定に当たった項目名（`<変数か項目>（<出所>）`。値を含まない）、`stamps` はその判定に使った源の
    `(st_mtime_ns, st_ino, st_size)`（アカウント側の認証ファイルと設定ファイル。`effective_env` まで進んだときはその `Resolved.stamps` も）。"""

    items: list[str]
    stamps: dict[str, Optional[tuple]]

    def __new__(cls, reason: str, items: Sequence[str] = (), stamps: Optional[Mapping[str, Optional[tuple]]] = None) -> "Refused":
        self = super().__new__(cls, reason)
        self.items = list(items)
        self.stamps = dict(stamps) if stamps is not None else {}
        return self


class _NoFile:
    """`effective_env` の `user_settings` に渡す印。子が開く `<設定ディレクトリ>/settings.json` が起動の後に無い。"""


NO_FILE = _NoFile()


class _Outside(Exception):
    """実体がアカウントの設定ディレクトリの外にある・確かめた実体と違う。"""


# ---------------------------------------------------------------- symlink


def _ensure_shared(shared: Path) -> None:
    """共有側に `projects` と `settings.json` が無ければ作る（無いまま起動すると claude がアカウント側に実体を作る）。"""
    shared.mkdir(parents=True, exist_ok=True)
    p = shared / "projects"
    if not os.path.lexists(p):
        try:
            os.mkdir(p, 0o700)
        except FileExistsError:  # noqa: S110 ファイル: 別のアカウントの初回の起動が先に作った。その項目を使う
            pass
    s = shared / "settings.json"
    if not os.path.lexists(s):
        try:
            fd = os.open(s, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # noqa: NDF401 ファイル: 別のアカウントの初回の起動が先に作った。その項目を使う
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("{}\n")


def _relink(target: Path, p: Path) -> None:
    """衝突しない一時の名前 `<項目>.<pid>.<乱数>.link` を symlink の作成で排他的に取ってから置き換える。

    既にある項目は消さない（同じ名前なら別の乱数で取り直す）。置き換えに失敗したら、自分が作った一時の名前だけを消して上げる。"""
    while True:
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.link")
        try:
            os.symlink(target, tmp)
        except FileExistsError:  # noqa: S112 ファイル: 同じ名前の項目は利用者のものかもしれないので触らず、名前を取り直す
            continue
        break
    try:
        os.replace(tmp, p)
    except OSError:
        os.unlink(tmp)
        raise


_HOPS = 40  # symlink をたどる上限（Linux の ELOOP と同じ）。超えれば解けないとして外す


def _within(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _reaches_local(target: Path, store: Path) -> bool:
    """共有側の項目から、アカウント固有の名前か登録の保存先の中へ到達できるか（公開すれば認証ファイルに届く）。

    symlink は参照を 1 つずつたどり、途中の参照先の名前と置き場も見る（最終の名前だけでなく）。行き着いた先がディレクトリなら、
    その配下の symlink も同じ規則で見る（登録の保存先そのものの中へは降りない）。列挙できない配下は確かめられないので外す。"""
    return _leads(target, Path(os.path.realpath(store)), set())


def _leads(path: Path, root: Path, seen: set[str]) -> bool:
    p = path
    for _ in range(_HOPS):
        if not p.is_symlink():
            break
        p = Path(os.path.normpath(os.path.join(os.path.realpath(p.parent), os.readlink(p))))
        if is_local(p.name) or _within(p, root):
            return True
    else:
        return True
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return False
    except OSError:  # noqa: NDF401 ファイル: 確かめられない参照先は公開しない（呼び手が外す）
        return True
    real = os.path.realpath(p)
    if not stat.S_ISDIR(st.st_mode) or real in seen or _within(Path(real), root):
        return False
    seen.add(real)
    try:
        with os.scandir(real) as it:
            entries = list(it)
    except OSError:  # noqa: NDF401 ファイル: 列挙できない配下は確かめられないので公開しない（呼び手が外す）
        return True
    return any((e.is_symlink() or e.is_dir(follow_symlinks=False)) and _leads(Path(e.path), root, seen) for e in entries)


def _refused(target: Path, store: Path) -> bool:
    """`link_shared` と `planned_links` が共に使う拒否の判定（共有側の項目を公開しないか）。"""
    return os.path.lexists(target) and _reaches_local(target, store)


def _link_one(target: Path, p: Path, store: Path, out: Links) -> None:
    """共有側の項目 `target` をアカウント側の `p` へ張る（`link_shared` の 1 項目）。"""
    item = target.name
    if _refused(target, store):
        # 認証ファイルか別のアカウントを指す別名は公開しない。前に張った自分の symlink だけを外す（実体は触らない）
        out.refused.append(item)
        if p.is_symlink() and Path(os.readlink(p)) == target:
            os.unlink(p)
    elif not os.path.lexists(p):
        os.symlink(target, p)
        out.added += 1
    elif not p.is_symlink():
        out.skipped.append(item)
    elif Path(os.readlink(p)) != target:
        _relink(target, p)
        out.added += 1


def link_shared(shared: Path, account: Path) -> Links:
    """共有の設定ディレクトリの直下の項目のうち、アカウント固有でないものをアカウント側の symlink にする。

    呼ぶ側がアカウントの排他を持つ。無ければ作り、参照先の違う symlink は付け替え、実体は飛ばす。`OSError` はそのまま上げる。"""
    shared = Path(os.path.abspath(shared))
    _ensure_shared(shared)
    out = Links()
    for item in sorted(os.listdir(shared)):
        if not is_local(item):
            _link_one(shared / item, account / item, account.parent, out)
    for item in sorted(os.listdir(account)):
        p = account / item
        if not is_local(item) and not p.is_symlink() and not os.path.lexists(shared / item):
            out.local_only.append(item)
    proj = account / "projects"
    out.projects_shared = proj.is_dir() and os.path.realpath(proj) == os.path.realpath(shared / "projects")
    return out


def planned_links(shared: Path, account: Path) -> dict[str, Optional[Path]]:
    """書かずに、`link_shared` の後に `projects`・`settings.json` が解ける先（アカウント側が実体ならその実体）。

    `link_shared` と同じ拒否の判定を使う。拒んだ項目は、前に張った自分の symlink が外れるので None（子の起動の後に無い）、
    アカウント側にほかの項目があればそのパス。"""
    shared = Path(os.path.abspath(shared))
    out: dict[str, Optional[Path]] = {}
    for item in ("projects", "settings.json"):
        p, target = account / item, shared / item
        if _refused(target, account.parent):
            mine = p.is_symlink() and Path(os.readlink(p)) == target
            out[item] = p if os.path.lexists(p) and not mine else None
        else:
            out[item] = p if os.path.lexists(p) and not p.is_symlink() else target
    return out


# ---------------------------------------------------------------- 設定ファイルの解決


def config_file(config_dir: Path, named: bool) -> Path:
    """Claude Code が実際に読む設定ファイル。`<config_dir>/.config.json` があればそれ、無ければ
    `named`（設定ディレクトリが変数で指定されている）なら `<config_dir>/.claude.json`、偽なら `~/.claude.json`。"""
    legacy = config_dir / LEGACY_CONFIG_FILE
    if os.path.lexists(legacy):
        return legacy
    return config_dir / reg.CONFIG_FILE if named else Path.home() / reg.CONFIG_FILE


def shared_config_file(environ: Mapping[str, str]) -> Path:
    """共有側の設定ファイル。`environ` は子の環境を重ねる前の親の環境。"""
    if "NDF_SHARED_CONFIG_DIR" in environ:
        named = bool(environ["NDF_SHARED_CONFIG_DIR"])
    else:
        named = bool(environ.get("CLAUDE_CONFIG_DIR"))
    return config_file(reg.shared_config_dir(environ), named)


# ---------------------------------------------------------------- 実効値（認証の上書き）


@dataclass
class _Layer:
    label: str
    data: dict[str, Any]
    broken: bool = False

    @property
    def has_items(self) -> bool:
        """ポリシーの項目を 1 つでも持つか（読めない管理の設定も「ある」として数える）。制御キーは数えない。"""
        return self.broken or any(k not in CONTROL_KEYS for k in self.data)


def _stamp(path: Path) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:  # noqa: NDF401 ファイル: 無い源の stat は None で持ち、後から作られると変わる
        return None
    return _of(st)


def _of(st: os.stat_result) -> tuple:
    return (st.st_mtime_ns, st.st_ino, st.st_size)


def _layer(path: Path, label: str, stamps: dict[str, Optional[tuple]], unresolved: Optional[list[str]], need: bool = False) -> _Layer:
    """設定ファイルを 1 つ読む。読めないか JSON の object でなければ、`unresolved` を渡したときだけその名前を足す。

    `need` なら、無いことも解けないとみなす（`--settings` の指したファイル）。印と在るか無いかは同じ 1 回の `stat` から取る。"""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        stamps[str(path)] = None
        if need and unresolved is not None:
            unresolved.append(label)
        return _Layer(label, {}, broken=need)
    except OSError:  # 権限が無いなど、在るか無いかが分からない
        stamps[str(path)] = None
        if unresolved is not None:
            unresolved.append(label)
        return _Layer(label, {}, broken=True)
    stamps[str(path)] = _of(st)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        if unresolved is not None:
            unresolved.append(label)
        return _Layer(label, {}, broken=True)
    return _Layer(label, data)


class _Ambiguous(Exception):
    """起動引数の区切りが解けない（値を取るか分からないオプションの直後に、読みたいオプションがある）。"""


def _options(argv: Sequence[str], strict: bool = True) -> list[tuple[str, Optional[str]]]:
    """起動引数のオプションと値の組を、Claude Code（commander）と同じ規則で読む。

    `--` より後は位置引数で、オプションとして読まない。値を 1 つ取るオプションは、次の引数を `-` で始まっても値として消費する。
    値を並べるオプションは `-` で始まらない引数を続けて消費し、値を省けるオプションは `-` で始まらない次の引数だけを消費する。
    表に無いオプションの直後に `--settings`・`--setting-sources` があれば、それが値か区切りか解けないので `_Ambiguous`
    （`strict` が偽ならオプションとして読む）。"""
    out: list[tuple[str, Optional[str]]] = []
    i, n, unknown = 0, len(argv), False
    while i < n:
        a = argv[i]
        i += 1
        if a == "--":
            break
        if not a.startswith("-") or a == "-":
            unknown = False
            continue
        *flags, (name, attached) = _split(a)
        out += [(f, None) for f in flags]
        if name in _READ_FLAGS and unknown and strict:
            raise _Ambiguous
        unknown = attached is None and not _known(name)
        if attached is not None:
            out.append((name, attached))
            continue
        if name in _ARG_ONE:
            out.append((name, argv[i] if i < n else None))
        elif name not in _ARG_MANY and name not in _ARG_OPTIONAL:
            out.append((name, None))
        i = _skip_values(name, argv, i)
    return out


def _known(name: str) -> bool:
    return name in _ARG_NONE or name in _ARG_ONE or name in _ARG_MANY or name in _ARG_OPTIONAL


def _split(a: str) -> list[tuple[str, Optional[str]]]:
    """1 つの引数を (オプション, 引数に付いた値か None) の並びにする（commander の規則）。

    `--name=value` は値付き。`-xyz` は、`-x` が値を取るなら残り `yz` がその値、値を取らないなら `-x` の後に `-yz` を
    同じ規則で読む。表に無い短縮オプションに当たれば、その残りを丸ごと 1 つの未知のオプションとする。"""
    if a.startswith("--"):
        name, eq, value = a.partition("=")
        return [(name, value if eq else None)]
    out: list[tuple[str, Optional[str]]] = []
    while len(a) > 2:
        head = a[:2]
        if head in _ARG_NONE:
            out.append((head, None))
            a = "-" + a[2:]
        elif _known(head):
            out.append((head, a[2:]))
            return out
        else:
            break
    out.append((a, None))
    return out


def _skip_values(name: str, argv: Sequence[str], i: int) -> int:
    """`argv[i]` から、オプション `name` が消費する値の後ろの位置。"""
    if name in _ARG_ONE:
        return i + 1
    if name in _ARG_MANY:
        i += 1
        while i < len(argv) and not argv[i].startswith("-"):
            i += 1
        return i
    if name in _ARG_OPTIONAL and i < len(argv) and not argv[i].startswith("-"):
        return i + 1
    return i


def _flag(opts: Sequence[tuple[str, Optional[str]]], name: str) -> tuple[bool, Optional[str]]:
    """`_options` の組のうち `name` の値。(あるか, 値。値が欠ければ None)。同じ引数が続けば後のもの。"""
    found, value = False, None
    for k, v in opts:
        if k == name:
            found, value = True, v
    return found, value


def _settings_flag(
    opts: Sequence[tuple[str, Optional[str]]], cwd: Path, stamps: dict[str, Optional[tuple]], unresolved: list[str]
) -> Optional[_Layer]:
    found, value = _flag(opts, "--settings")
    if not found:
        return None
    if not value:
        unresolved.append("--settings")
        return _Layer("--settings", {}, broken=True)
    if value.lstrip().startswith("{"):
        try:
            data = json.loads(value)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            unresolved.append("--settings")
            return _Layer("--settings", {}, broken=True)
        return _Layer("--settings", data)
    path = Path(value)
    return _layer(path if path.is_absolute() else cwd / path, "--settings", stamps, unresolved, need=True)


def _sources(opts: Sequence[tuple[str, Optional[str]]], unresolved: list[str]) -> set[str]:
    if _flag(opts, "--restricted")[0]:
        return set()  # 利用者・プロジェクト・ローカルの設定ファイルを読まない（管理の設定と --settings は効く）
    found, value = _flag(opts, "--setting-sources")
    if not found:
        return {"user", "project", "local"}
    if value is None:
        unresolved.append("--setting-sources")
        return {"user", "project", "local"}
    return {s.strip() for s in value.split(",") if s.strip()}


def _env_of(layer: Optional[_Layer]) -> dict[str, str]:
    e = layer.data.get("env") if layer else None
    if not isinstance(e, dict):
        return {}
    return {k: v if isinstance(v, str) else str(v) for k, v in e.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def _helper_of(layers: Sequence[Optional[_Layer]]) -> tuple[Optional[str], Optional[str]]:
    """`layers`（優先の高い順）のうち `apiKeyHelper` を定義する最上位の値と出所。空文字は無いものとする。"""
    for layer in layers:
        if layer is not None and "apiKeyHelper" in layer.data:
            v = layer.data["apiKeyHelper"]
            return (v, layer.label) if isinstance(v, str) and v else (None, None)
    return None, None


def _managed(
    config_dir: Path, files: Sequence[Path], d: Path, stamps: dict[str, Optional[tuple]], unresolved: list[str]
) -> tuple[_Layer, list[_Layer]]:
    """管理の設定の源。(サーバーから取った写し, ファイルの源を重ねる順に)。"""
    remote = _layer(config_dir / REMOTE_SETTINGS, f"管理の設定（{REMOTE_SETTINGS}）", stamps, unresolved)
    out = [_layer(f, f"管理の設定（{f.name}）", stamps, unresolved) for f in files]
    label = f"管理の設定（{d.name}）"
    try:
        is_dir = stat.S_ISDIR(os.stat(d).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        is_dir = False
    except OSError:  # 権限が無いなど、在るか無いかが分からない
        unresolved.append(label)
        return remote, [*out, _Layer(label, {}, broken=True)]
    if not is_dir:
        stamps[str(d.parent)] = _stamp(d.parent)
        return remote, out
    stamps[str(d)] = _stamp(d)
    try:
        names = os.listdir(d)
    except OSError:  # 在るのに列挙できない
        unresolved.append(label)
        return remote, [*out, _Layer(label, {}, broken=True)]
    # Claude Code は隠しファイルと .json で終わらないファイルを読まない
    picked = sorted(n for n in names if n.endswith(".json") and not n.startswith("."))
    return remote, out + [_layer(d / n, f"管理の設定（{d.name}/{n}）", stamps, unresolved) for n in picked]


def _profile(env: Mapping[str, str], cwd: Path, stamps: dict[str, Optional[tuple]], unresolved: list[str]) -> Optional[str]:
    """Anthropic の設定ディレクトリの有効なプロファイル（`active_config`、無ければ `default`）の認証の形。

    相対パスの `ANTHROPIC_CONFIG_DIR` は子の作業ディレクトリ `cwd` から解く（NDF 自身の作業ディレクトリでなく）。"""
    conf = cwd / env["ANTHROPIC_CONFIG_DIR"] if env.get("ANTHROPIC_CONFIG_DIR") else Path.home() / ".config" / "anthropic"
    active = conf / "active_config"
    stamps[str(active)] = _stamp(active)
    try:
        name = active.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        name = ""
    except OSError:
        unresolved.append(PROFILE_LABEL)
        return None
    layer = _layer(conf / "configs" / f"{name or 'default'}.json", PROFILE_LABEL, stamps, unresolved)
    auth = layer.data.get("authentication")
    kind = auth.get("type") if isinstance(auth, dict) else None
    return kind if isinstance(kind, str) else None


def effective_env(
    env: Mapping[str, str],
    config_dir: Path,
    cwd: Path,
    argv: Sequence[str],
    user_settings: Union[Path, None, "_NoFile"] = None,
) -> Resolved:
    """Claude Code が採用する実効値とその出所（詳細設計の「実効値の作り方」）。ファイルを読むだけで書かない。

    `env`・`argv`・`cwd` は子が実際に受け取る環境・起動引数・作業ディレクトリ。`user_settings` は利用者の設定の解けた先
    （省けば `<config_dir>/settings.json`、`NO_FILE` なら子の起動の後に無い）。管理の設定の保存先は `MANAGED_FILES`・`MANAGED_DIR`。

    子が開くパス `<config_dir>/settings.json` は、`user_settings` と違っても `stamps` に持つ（起動の後に symlink を別の
    ファイルへ付け替えるか実体に置き換えると、解けた先の stat が変わる）。起動の後に走っている子の実効値を判定し直す
    ときは、`user_settings` を省いてそのパスから読む。"""
    stamps: dict[str, Optional[tuple]] = {}
    unresolved: list[str] = []
    try:
        opts = _options(argv)
    except _Ambiguous:
        unresolved.append("起動引数")
        opts = _options(argv, strict=False)
    remote, files = _managed(config_dir, MANAGED_FILES, MANAGED_DIR, stamps, unresolved)
    flag = _settings_flag(opts, cwd, stamps, unresolved)
    sources = _sources(opts, unresolved)
    child_path = config_dir / "settings.json"
    if isinstance(user_settings, _NoFile):
        user = _Layer("利用者の設定", {}) if "user" in sources else None
        stamps[str(child_path)] = None  # 起動の後に作られると変わる
    else:
        read_path = Path(user_settings) if user_settings is not None else child_path
        user = _layer(read_path, "利用者の設定", stamps, None) if "user" in sources else None
        # 子が開くパスの stat は、解けた先（`link_shared` の後に symlink が指す先）と同じ値で持つ。見回りがそのパスを
        # `os.stat` して、付け替え・実体への置き換えで違う値になれば当たる
        stamps[str(child_path)] = stamps[str(read_path)] if str(read_path) in stamps else _stamp(read_path)
    project = _layer(cwd / ".claude" / "settings.json", "プロジェクトの設定", stamps, None) if "project" in sources else None
    local = _layer(cwd / ".claude" / "settings.local.json", "プロジェクトのローカルの設定", stamps, None) if "local" in sources else None
    values, origin = dict(env), dict.fromkeys(env, "子の環境")
    for layer in (user, project, local, flag, *files):  # 優先の低い順に重ねる
        for k, v in _env_of(layer).items():
            # プロジェクトとローカルの設定の env で Claude Code が無視する変数（STD8）は重ねない
            if (layer is project or layer is local) and k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES"):
                continue
            values[k], origin[k] = v, layer.label
    _overlay_remote(remote, values, origin)
    policy = _policy_helper(files)
    if policy:
        # 管理の設定のファイルの policyHelper は起動のときに管理の設定を作り直す。その出力は前もって確かめられない。
        # サーバーから取った写しがポリシーを持つ間は隠れるが、起動時の取得でサーバーの設定が消えたと分かると実行される
        unresolved.append(f"policyHelper（{policy}）")
    if remote.has_items:
        helper, helper_origin = _helper_of([remote])
    elif any(f.has_items for f in files):
        helper, helper_origin = _helper_of(list(reversed(files)))
    else:
        helper, helper_origin = _helper_of([flag, local, project, user])
    profile = _profile(values, cwd, stamps, unresolved)
    return Resolved(values, origin, helper, helper_origin, unresolved, stamps, profile, _bare(opts, values, origin, unresolved))


def _bare(
    opts: Sequence[tuple[str, Optional[str]]], values: Mapping[str, str], origin: Mapping[str, str], unresolved: list[str]
) -> Optional[str]:
    """最小モードの項目名。最小モードの子は登録先の OAuth を読まない。真偽の解けない `CLAUDE_CODE_SIMPLE` は解けないとする。"""
    if _flag(opts, "--bare")[0]:
        return "--bare（起動引数）"
    on = reg.provider_on(values.get(BARE_ENV))
    if on is None:
        unresolved.append(f"{BARE_ENV}（{origin[BARE_ENV]}）")
    return f"{BARE_ENV}（{origin[BARE_ENV]}）" if on else None


def _flagged(k: str, v: Optional[str]) -> bool:
    """その値が単独で「当たる条件」に当たるか、解けないか（`_judge` と同じ規則）。"""
    if k in reg.PROVIDER_ENV:
        return reg.provider_on(v) is not False
    if k == "ANTHROPIC_BASE_URL":
        return bool(v) and v.rstrip("/") != DEFAULT_API_URL
    return bool(v)


def _overlay_remote(remote: _Layer, values: dict[str, str], origin: dict[str, str]) -> None:
    """サーバーから取った写しの env を最上位に重ねる。起動の時点にだけ読む選択の変数（`STARTUP_ONLY_ENV`）は重ねない
    （写しの値は効かない）。

    保留される変数は、サーバーの確認までは下の源の値が効く。下の値が当たり写しの値が当たらなければ、下の値を残す
    （起動の時点で子はその値で送る）。写しの値が当たるなら写しの値を採る（確認の後に効く）。"""
    for k, v in _env_of(remote).items():
        if k in STARTUP_ONLY_ENV:
            continue
        if k in WITHHELD_ENV and _flagged(k, values.get(k)) and not _flagged(k, v):
            continue
        values[k], origin[k] = v, remote.label


def _policy_helper(files: Sequence[_Layer]) -> Optional[str]:
    """管理の設定のファイルを重ねた後の `policyHelper` の出所（設定されていなければ None）。後のファイルが前を上書きする。"""
    got: Optional[str] = None
    for layer in files:
        if "policyHelper" in layer.data:
            got = layer.label if layer.data["policyHelper"] not in (None, "", {}) else None
    return got


def _judge(r: Resolved) -> tuple[Optional[str], list[str]]:
    """実効値に「当たる条件」を当てる。(理由の符号か None, 項目名)。項目名は `<変数か項目>（<出所>）` で値を含まない。"""
    items = list(r.unresolved)
    unclear = bool(items)

    def tag(k: str) -> str:
        return f"{k}（{r.origin[k]}）"

    items += [tag(k) for k in AUTH_VALUE_ENV if r.env.get(k)]
    for k in reg.PROVIDER_ENV:
        on = reg.provider_on(r.env.get(k))
        if on is None:
            unclear = True
        if on is not False:
            items.append(tag(k))
    if r.helper:
        items.append(f"apiKeyHelper（{r.helper_origin}）")
    base = r.env.get("ANTHROPIC_BASE_URL")
    if base and base.rstrip("/") != DEFAULT_API_URL:
        items.append(tag("ANTHROPIC_BASE_URL"))
    items += [tag(k) for k in ("ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_PROFILE") if r.env.get(k)]
    federation = ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID")
    if all(r.env.get(k) for k in federation):
        items.append(f"{'・'.join(federation)}（{r.origin[federation[0]]}）")
    if r.bare:
        items.append(r.bare)
    if r.profile == "oidc_federation":
        items.append(f"oidc_federation（{PROFILE_LABEL}）")
    items = list(dict.fromkeys(items))
    if unclear:
        return "auth_unresolved", items
    return ("auth_overridden" if items else None), items


def auth_overrides(
    env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Union[Path, None, "_NoFile"] = None
) -> list[str]:
    """登録先の OAuth より上に来る認証の源と、既定でない API の接続先の項目名（値は返さない）。空なら上書きは無い。"""
    return _judge(effective_env(env, config_dir, cwd, argv, user_settings))[1]


# ---------------------------------------------------------------- 実体の確かめ


def _own_file(path: Path, parent: Path) -> Optional[tuple[int, int]]:
    """アカウント固有のファイルの `(st_dev, st_ino)`。無ければ None。symlink・ハードリンク・外を指すなら `_Outside`。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無いファイルは確かめる実体が無い（ids に None を入れる）
        return None
    except OSError:
        raise _Outside from None
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or Path(os.path.realpath(path)).parent != parent:
        raise _Outside
    return (st.st_dev, st.st_ino)


def _local(store: Path, account: Path, shared_file: Path) -> Local:
    root = Path(os.path.realpath(store))
    real = root / account.name
    try:
        st = os.lstat(account)
    except OSError:
        raise _Outside from None
    if not stat.S_ISDIR(st.st_mode) or Path(os.path.realpath(account)) != real:
        raise _Outside
    config = config_file(real, True)
    ids: dict[str, Optional[tuple[int, int]]] = {"dir": (st.st_dev, st.st_ino)}
    for key, path in (
        (reg.CRED_FILE, real / reg.CRED_FILE),
        ("config", config),
        *((n, real / n) for n in (reg.BASE_FILE, reg.BASE_NEXT_FILE, reg.PENDING_FILE)),
    ):
        ids[key] = _own_file(path, real)
    shared = Path(os.path.realpath(shared_file))
    if shared == root or root in shared.parents:
        raise _Outside
    try:
        sst = os.lstat(shared)
    except FileNotFoundError:
        ids["shared"] = None
    except OSError:
        raise _Outside from None
    else:
        sid = (sst.st_dev, sst.st_ino)
        if not stat.S_ISREG(sst.st_mode) or sst.st_nlink != 1 or sid in ids.values():
            raise _Outside
        ids["shared"] = sid
    return Local(account=real, config=config, shared=shared, ids=ids)


def check_local(store: Path, account: Path, shared_file: Path) -> Union[Local, str]:
    """実体の確かめだけを行う（読まない・書かない）。通れば `Local`、外れれば `not_account_local`。"""
    try:
        return _local(store, account, shared_file)
    except _Outside:
        return NOT_LOCAL


def _read_fixed(local: Local, key: str, path: Path) -> Any:
    """`Local` の固定したパスを開いて JSON を読む。無ければ None、壊れていれば `...`。

    開いたファイルの `(st_dev, st_ino)` が `Local` と違えば `_Outside`（確かめの後に置き換えられた）。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if local.ids.get(key) is not None:
            raise _Outside from None
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _Outside from None
        return ...
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if (st.st_dev, st.st_ino) != local.ids.get(key):
            raise _Outside
        try:
            return json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):  # noqa: NDF401 ファイル: 壊れた認証・設定のファイルは呼び手が理由の符号にする
            return ...


def check_config(local: Local, registered: reg.Account) -> Optional[str]:
    """設定ファイルの形式と本人の確かめ。旧形式なら `legacy_config`、JSON の object でなければ `account_unreadable`、
    `oauthAccount` のメールアドレスと組織（登録に組織が無ければメールアドレスだけ）が登録と違えば `identity_mismatch`。"""
    return _config_reason(local, registered)[0]


def _config_reason(local: Local, registered: reg.Account) -> tuple[Optional[str], Any]:
    """`check_config` の判定と、読んだ設定ファイルの中身（読まなかったか壊れていれば None）。"""
    if local.config.name != reg.CONFIG_FILE:
        return "legacy_config", None
    try:
        data = _read_fixed(local, "config", local.config)
    except _Outside:
        return NOT_LOCAL, None
    if not isinstance(data, dict):
        return "account_unreadable", None
    return _identity(data, registered), data


def _identity(data: dict[str, Any], registered: reg.Account) -> Optional[str]:
    who = data.get("oauthAccount")
    if not isinstance(who, dict) or who.get("emailAddress") != registered.email:
        return "identity_mismatch"
    if registered.org_id and who.get("organizationUuid") != registered.org_id:
        return "identity_mismatch"
    return None


def _check_credentials(local: Local) -> Optional[str]:
    try:
        cred = _read_fixed(local, reg.CRED_FILE, local.account / reg.CRED_FILE)
    except _Outside:
        return NOT_LOCAL
    oauth = cred.get("claudeAiOauth") if isinstance(cred, dict) else None
    if not isinstance(oauth, dict) or not _filled(oauth.get("accessToken")):
        return "no_credentials"
    if not _filled(oauth.get("refreshToken")):
        return "no_refresh"
    return None


def _filled(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def check_account(
    store: Path,
    account: Path,
    registered: reg.Account,
    shared: Path,
    shared_file: Path,
    launch: Launch,
) -> Union[Checked, str]:
    """起動の前の確かめ（読むだけ）。実体 → 認証ファイル → 設定ファイルの形式と本人 → 認証の上書きの順に見る。

    `shared_file` は親の環境から解いた共有側の設定ファイル、`launch` は子が受け取る環境・作業ディレクトリ・起動引数。通れば `Checked`、
    使えなければ最初に外れた理由の符号を `Refused`（`str`）で返す。`Refused` は判定に当たった項目名と、その判定に使った
    源の `stamps` を持つ（呼び出し元は設定を読み直さない）。

    設定ファイルに `/login` で保存した API キー（`primaryApiKey`）があれば、Claude Code は登録先の OAuth より先にそれを使う
    ので `auth_overridden`（値は返さない）。"""
    own = {str(p): _stamp(p) for p in (account / reg.CRED_FILE, config_file(account, True))}
    local = check_local(store, account, shared_file)
    if isinstance(local, str):
        return Refused(local, stamps=own)
    reason = _check_credentials(local)
    if reason:
        return Refused(reason, stamps=own)
    reason, data = _config_reason(local, registered)
    if reason:
        return Refused(reason, stamps=own)
    stored = [f"{STORED_KEY}（アカウントの {reg.CONFIG_FILE}）"] if isinstance(data, dict) and data.get(STORED_KEY) else []
    planned = planned_links(shared, account)["settings.json"]
    resolved = effective_env(launch.env, account, launch.cwd, launch.argv, NO_FILE if planned is None else planned)
    judged, items = _judge(resolved)
    if judged == "auth_unresolved":
        return Refused(judged, [*stored, *items], {**own, **resolved.stamps})
    if stored or judged:
        return Refused("auth_overridden", [*stored, *items], {**own, **resolved.stamps})
    return Checked(local, resolved)
