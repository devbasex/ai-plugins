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
from ndf.domains.relay import claude_args

LEGACY_CONFIG_FILE = ".config.json"  # `.claude.json` の古い名前（Claude Code はあれば先に読む）
REMOTE_SETTINGS = "remote-settings.json"  # 管理の設定をサーバーから取った写し
# アカウント固有の項目（symlink にしない）。前方一致は書き込みの一時ファイルと排他を含む
RELINK_PREFIX = ".ndf-relink."  # `_relink` が排他で確保する一時の名前（`.ndf-relink.<項目>.<乱数>`）
LOCAL_PREFIXES = (reg.CRED_FILE, reg.CONFIG_FILE, ".oauth_refresh.lock", RELINK_PREFIX)
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
    "auth_overridden": "設定か環境に、登録先の OAuth より上に来る認証か既定でない接続先がある。その項目を外す",
    "auth_unresolved": "認証の上書きを確かめる設定が読めないか、値が解けない。その設定を直す",
}


def is_local(item: str) -> bool:
    """アカウント固有の項目か（symlink にしない）。"""
    return item in LOCAL_NAMES or item.startswith(LOCAL_PREFIXES)


@dataclass
class Links:
    """`link_shared` の結果。`skipped` は同じ名前の実体があって飛ばした項目、`local_only` は共有側に無く
    アカウント側にだけある実体の名前。`projects_shared` はアカウント側の `projects` が共有側と同じ実体か。"""

    added: int = 0
    skipped: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)
    projects_shared: bool = False


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
    `env`（子の環境の秘密を含みうる）と `helper` は `repr` に出さない（I5）。"""

    env: dict[str, str] = field(repr=False)
    origin: dict[str, str]
    helper: Optional[str] = field(repr=False)
    helper_origin: Optional[str]
    unresolved: list[str]
    stamps: dict[str, Optional[tuple]]
    profile: Optional[str] = None


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

    `items` は判定に当たった項目名（値を含まない）、`stamps` はその判定に使った源の `(st_mtime_ns, st_ino, st_size)`
    （アカウント側の認証ファイルと設定ファイル。`effective_env` まで進んだときはその `Resolved.stamps` も）。
    `ready` の `Probe.stamps` が失敗でも判定に使った `stamps` を持てるように、設定を読み直さずに渡す。"""

    items: list[str]
    stamps: dict[str, Optional[tuple]]

    def __new__(cls, reason: str, items: Sequence[str] = (), stamps: Optional[Mapping[str, Optional[tuple]]] = None) -> "Refused":
        self = super().__new__(cls, reason)
        self.items = list(items)
        self.stamps = dict(stamps) if stamps is not None else {}
        return self


class _Outside(Exception):
    """実体がアカウントの設定ディレクトリの外にある・確かめた実体と違う。"""


# ---------------------------------------------------------------- symlink


def _ensure_shared(shared: Path) -> None:
    """共有側に `projects` と `settings.json` が無ければ作る（無いまま起動すると claude がアカウント側に実体を作る）。"""
    shared.mkdir(parents=True, exist_ok=True)
    p = shared / "projects"
    if not os.path.lexists(p):
        os.makedirs(p, 0o700, exist_ok=True)  # 別のアカウントの初回の起動が先に作ったら、その項目を使う
    s = shared / "settings.json"
    if not os.path.lexists(s):
        try:
            fd = os.open(s, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # noqa: NDF401 ファイル: 別のアカウントの初回の起動が先に作った。その項目を使う
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("{}\n")


def _reserve(target: Path, p: Path) -> Path:
    """一時の名前 `<親>/.ndf-relink.<項目>.<乱数>` に `target` への symlink を排他で作って確保する。名前が衝突したときだけ引き直す。"""
    while True:
        tmp = p.parent / f"{RELINK_PREFIX}{p.name}.{secrets.token_hex(4)}"
        try:
            os.symlink(target, tmp)
        except FileExistsError:  # noqa: NDF401 ファイル: 乱数の名前が既にある。ほかの名前を引き直す
            continue
        return tmp


def _link_new(target: Path, p: Path) -> bool:
    """空いた `p` に symlink を排他で作る。間に同じ名前が作られたら、それを残して偽を返す（I2）。"""
    try:
        os.symlink(target, p)
    except FileExistsError:  # noqa: NDF401 ファイル: 付け替えの間に作られた同じ名前は利用者か子のもの。上書きせず飛ばす
        return False
    return True


def _put_back(aside: Path, p: Path, target: Path) -> bool:
    """退避した `aside` を片付ける。symlink なら消して `p` に張り直し、実体なら `p` へ戻して偽を返す。

    実体は `os.link` で戻すので、間に `p` が作られていれば上書きせず `FileExistsError` を上げ、実体は `aside` に残る
    （次の `link_shared` の片付けが戻す）。"""
    if os.path.islink(aside):
        os.unlink(aside)
        return _link_new(target, p)
    os.link(aside, p, follow_symlinks=False)
    os.unlink(aside)
    return False


def _relink(target: Path, p: Path) -> bool:
    """symlink の `p` を `target` へ付け替える。張れば真、置き換えの時点で `p` が実体だったら残して偽（I2）。

    確保した一時の名前へ `p` を `os.rename` で退避してから中身を見る。`os.replace` で上書きしないので、確かめた後に
    利用者や動いている子が同じ名前へ保存した実体も消さない。ディレクトリは symlink の上へ移らない（`ENOTDIR`）ので、
    そのまま残る。中断で残った一時の名前は、次の `link_shared` の `_sweep` が片付ける。"""
    aside = _reserve(target, p)
    try:
        os.rename(p, aside)
    except FileNotFoundError:
        os.unlink(aside)
        return _link_new(target, p)
    except OSError as exc:
        os.unlink(aside)
        if exc.errno in (errno.ENOTDIR, errno.EISDIR):  # 付け替えの間に同じ名前のディレクトリが作られた
            return False
        raise
    return _put_back(aside, p, target)


def _sweep(account: Path, out: Links) -> None:
    """中断した付け替えの一時の名前を片付ける。symlink は消し、実体は元の項目の名前が空いていれば戻す。

    戻せない実体（元の名前が埋まっている・ディレクトリ）は消さずに残し、その名前を `skipped` に入れる（I2）。"""
    for name in sorted(os.listdir(account)):
        if not name.startswith(RELINK_PREFIX):
            continue
        tmp = account / name
        item = name[len(RELINK_PREFIX) :].rpartition(".")[0]
        if tmp.is_symlink():
            os.unlink(tmp)
        elif item and not tmp.is_dir() and not os.path.lexists(account / item):
            os.link(tmp, account / item, follow_symlinks=False)
            os.unlink(tmp)
        else:
            out.skipped.append(name)


def link_shared(shared: Path, account: Path) -> Links:
    """共有の設定ディレクトリの直下の項目のうち、アカウント固有でないものをアカウント側の symlink にする。

    呼ぶ側がアカウントの排他を持つ。先に中断した付け替えの一時の名前を片付ける。無ければ作り、参照先の違う symlink は
    付け替え、実体は飛ばす。`OSError` はそのまま上げる。"""
    shared = Path(os.path.abspath(shared))
    _ensure_shared(shared)
    out = Links()
    _sweep(account, out)
    for item in sorted(os.listdir(shared)):
        if is_local(item):
            continue
        target, p = shared / item, account / item
        if not os.path.lexists(p):
            if _link_new(target, p):
                out.added += 1
            else:
                out.skipped.append(item)
        elif p.is_symlink():
            if Path(os.readlink(p)) != target:
                if _relink(target, p):
                    out.added += 1
                else:
                    out.skipped.append(item)
        else:
            out.skipped.append(item)
    for item in sorted(os.listdir(account)):
        p = account / item
        if not is_local(item) and not p.is_symlink() and not os.path.lexists(shared / item):
            out.local_only.append(item)
    proj = account / "projects"
    out.projects_shared = proj.is_dir() and os.path.realpath(proj) == os.path.realpath(shared / "projects")
    return out


def planned_links(shared: Path, account: Path) -> dict[str, Path]:
    """書かずに、`link_shared` の後に `projects`・`settings.json` が解ける先（アカウント側が実体ならその実体）。"""
    out = {}
    for item in ("projects", "settings.json"):
        p = account / item
        out[item] = p if os.path.lexists(p) and not p.is_symlink() else shared / item
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
        """ポリシーの項目を 1 つでも持つか（読めない管理の設定も「ある」として数える）。"""
        return self.broken or bool(self.data)


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

    `need` なら、無いことも解けないとみなす（`--settings` の指したファイル）。`stamps` と在るか無いかは同じ 1 回の `stat` から取り、
    在るか無いかが分からない（権限が無いなど）ときは読めないとみなす。"""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        stamps[str(path)] = None
        if need and unresolved is not None:
            unresolved.append(label)
        return _Layer(label, {}, broken=need)
    except OSError:
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


def _flag(argv: Sequence[str], name: str) -> tuple[bool, Optional[str]]:
    """起動引数の `name` の値。(あるか, 値。値が欠ければ None)。同じ引数が続けば後のもの。

    `argv[0]` はコマンドの名前。`--` より後の位置引数と、ほかのオプションの値になった語は読まない（`claude_args` の表）。"""
    found, value = False, None
    for opt, v in claude_args.options(argv[1:]):
        if opt == name:
            found, value = True, v
    return found, value


def _settings_flag(argv: Sequence[str], cwd: Path, stamps: dict[str, Optional[tuple]], unresolved: list[str]) -> Optional[_Layer]:
    found, value = _flag(argv, "--settings")
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


def _sources(argv: Sequence[str], unresolved: list[str]) -> set[str]:
    found, value = _flag(argv, "--setting-sources")
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
    if not d.is_dir():
        stamps[str(d.parent)] = _stamp(d.parent)
        return remote, out
    stamps[str(d)] = _stamp(d)
    try:
        names = os.listdir(d)
    except OSError:  # 在るのに列挙できない（読めない管理の設定と同じく解けない）
        label = f"管理の設定（{d.name}）"
        unresolved.append(label)
        return remote, [*out, _Layer(label, {}, broken=True)]
    # Claude Code は隠しファイルを読まない
    picked = sorted(n for n in names if n.endswith(".json") and not n.startswith("."))
    return remote, out + [_layer(d / n, f"管理の設定（{d.name}/{n}）", stamps, unresolved) for n in picked]


def _profile(env: Mapping[str, str], cwd: Path, stamps: dict[str, Optional[tuple]], unresolved: list[str]) -> Optional[str]:
    """Anthropic の設定ディレクトリの有効なプロファイル（`active_config`、無ければ `default`）の認証の形。

    相対パスの `ANTHROPIC_CONFIG_DIR` は子の作業ディレクトリ `cwd` から解く（NDF 自身の作業ディレクトリでなく）。"""
    raw = env.get("ANTHROPIC_CONFIG_DIR")
    if raw and "\0" in raw:  # パスにならない（値は理由に入れない）。ファイルに触れる前に外す
        unresolved.append(PROFILE_LABEL)
        return None
    conf = cwd / raw if raw else Path.home() / ".config" / "anthropic"
    active = conf / "active_config"
    stamps[str(active)] = _stamp(active)
    try:
        name = active.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        name = ""
    except (OSError, ValueError):  # 読めない・UTF-8 でない（値は理由に入れない）
        unresolved.append(PROFILE_LABEL)
        return None
    if name in (".", "..") or "/" in name or "\0" in name:  # configs/ の直下のファイルの名前にならない
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
    user_settings: Optional[Path] = None,
) -> Resolved:
    """Claude Code が採用する実効値とその出所（詳細設計の「実効値の作り方」）。ファイルを読むだけで書かない。

    `env`・`argv`・`cwd` は子が実際に受け取る環境・起動引数・作業ディレクトリ。`user_settings` は利用者の設定の解けた先
    （省けば `<config_dir>/settings.json`）。管理の設定の保存先は `MANAGED_FILES`・`MANAGED_DIR`。"""
    stamps: dict[str, Optional[tuple]] = {}
    unresolved: list[str] = []
    remote, files = _managed(config_dir, MANAGED_FILES, MANAGED_DIR, stamps, unresolved)
    flag = _settings_flag(argv, cwd, stamps, unresolved)
    sources = _sources(argv, unresolved)
    user = _layer(user_settings or config_dir / "settings.json", "利用者の設定", stamps, None) if "user" in sources else None
    project = _layer(cwd / ".claude" / "settings.json", "プロジェクトの設定", stamps, None) if "project" in sources else None
    local = _layer(cwd / ".claude" / "settings.local.json", "プロジェクトのローカルの設定", stamps, None) if "local" in sources else None
    values, origin = dict(env), dict.fromkeys(env, "子の環境")
    for layer in (user, project, local, flag, *files, remote):  # 優先の低い順に重ねる
        for k, v in _env_of(layer).items():
            # プロジェクトとローカルの設定の env で Claude Code が無視する変数（STD8）は重ねない
            if (layer is project or layer is local) and k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES"):
                continue
            values[k], origin[k] = v, layer.label
    if remote.has_items:
        helper, helper_origin = _helper_of([remote])
    elif any(f.has_items for f in files):
        helper, helper_origin = _helper_of(list(reversed(files)))
    else:
        helper, helper_origin = _helper_of([flag, local, project, user])
    profile = _profile(values, cwd, stamps, unresolved)
    return Resolved(values, origin, helper, helper_origin, unresolved, stamps, profile)


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
    if r.profile == "oidc_federation":
        items.append(f"oidc_federation（{PROFILE_LABEL}）")
    if unclear:
        return "auth_unresolved", items
    return ("auth_overridden" if items else None), items


def auth_overrides(
    env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Optional[Path] = None
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
    if local.config.name != reg.CONFIG_FILE:
        return "legacy_config"
    try:
        data = _read_fixed(local, "config", local.config)
    except _Outside:
        return NOT_LOCAL
    if not isinstance(data, dict):
        return "account_unreadable"
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
    使えなければ最初に外れた理由の符号を `Refused`（`str`）で返す。`Refused` は判定に当たった項目名と使った源の `stamps` を持つ。"""
    own = {str(p): _stamp(p) for p in (account / reg.CRED_FILE, config_file(account, True))}
    local = check_local(store, account, shared_file)
    if isinstance(local, str):
        return Refused(local, stamps=own)
    reason = _check_credentials(local) or check_config(local, registered)
    if reason:
        return Refused(reason, stamps=own)
    resolved = effective_env(launch.env, account, launch.cwd, launch.argv, planned_links(shared, account)["settings.json"])
    judged, items = _judge(resolved)
    return Refused(judged, items, {**own, **resolved.stamps}) if judged else Checked(local, resolved)
