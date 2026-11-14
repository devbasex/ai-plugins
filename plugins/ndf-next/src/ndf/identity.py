"""自分のディレクトリ・プラグイン名・Skill の前置き・状態の置き場を求める（#1142 の決定 75・決定 79）。

プラグイン名は `plugin.json` の `name` だけから求め、コードは自分の名前を文字で持たない。
入れ替えで書き換えるのは `plugin.json` 2 本の `name` と `version` だけになる。

プラグインの根は、このファイルの位置（`ROOT`）の 1 通りで求める。契約の表（`ndf.contracts`）も同じ `ROOT` を読む。
環境変数 `CLAUDE_PLUGIN_ROOT` は根を決めず、自分の根と同じかを確かめるだけに使う。違えば（ほかのプラグインの
hook の子として動いたなど）`PluginIdentity.foreign_root` に残し、旧い状態の置き場を退避しない（#3367）。
`bin/ndf` は子へ自分の根を渡す前に、呼び出し元の値を `NDF_CALLER_PLUGIN_ROOT` へ写す。それがあれば（空でも）
そちらを呼び出し元の値として確かめる。

状態の置き場の用意（`prepare_state_dir`）:

- 旧い NDF の状態の置き場の全一覧は `OLD_STATE_PLACES` の表で、行ごとに退避するか・その理由を持つ。退避するのは
  新しい NDF が同じパスへ書く置き場だけで、どれも同じ規則で退避する。規則は「形の版のファイル `format.json` が
  無く、ほかのファイルがある置き場は、名前に `.v10-<日付>` を足して名前を変える」で、消さない（決定 79・判断 (b)）。
  チェックアウトの置き場（`<git の共通ディレクトリ>/<名前>/state`）は `core/durable.py` の `prepare_store` が
  同じ規則で持つ（決定 85）
- 判定から `format.json` を置くまでを、置き場の隣のロックのファイル（`lock_path`）の排他の下で行う。同時に起動した
  処理は待ち、先の処理が置いた `format.json` を見て退避しない
- `format.json` は一時の名前で書いてから置き換える（`os.replace`）。`format.json` があることが「用意が済んだ」印で、
  途中で落ちても中身の半端な `format.json` は残らない
- 退避する置き場が別の退避する置き場の中にあれば（`XDG_STATE_HOME` と `CLAUDE_CONFIG_DIR` が同じなど）、外側の
  置き場だけを名前を変え、中の置き場は外側と一緒に動く
- 退避する置き場が退避しない置き場（`Handling.KEEP` の行。アカウントの設定・動いているプロセスの排他など）か、
  新しい状態の置き場（`config-state` の下に `XDG_STATE_HOME` を置いたなど）を中に持てば、外側ごと動かすとそれらまで
  動くため、何も変えずに `IdentityError` を投げて人へ戻す
- 戻り値の `StateReady` が、退避した置き場の元と先を持つ（結果 JSON の `items` へ載せる形は `StateReady.items`）
"""

from __future__ import annotations

import fcntl
import itertools
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# プラグインの根（このファイルから 3 つ上）。根を求めるのはここだけ
ROOT = Path(__file__).resolve().parents[2]
# 状態の置き場の先頭に置く形の版のファイル。無い置き場は旧い実装のものとみなす
FORMAT_FILE = "format.json"
FORMAT_VERSION = 1
# 旧い置き場の退避先の名前の接尾辞（`<置き場の名前>.v10-<日付>`）
RETIRED_SUFFIX = ".v10-"
# `bin/ndf` が子へ自分の根を渡す前に写した、呼び出し元の `CLAUDE_PLUGIN_ROOT`
CALLER_ROOT_ENV = "NDF_CALLER_PLUGIN_ROOT"


class IdentityError(Exception):
    """`plugin.json` が読めず自分の名前を求められない、または旧い置き場を退避してよいと確かめられない。"""


@dataclass(frozen=True)
class PluginIdentity:
    root: Path
    name: str
    prefix: str
    state_dir: Path
    # `CLAUDE_PLUGIN_ROOT` が自分の根と違うときの、その値。同じか無ければ None
    foreign_root: Optional[Path] = None


@dataclass(frozen=True)
class Retired:
    """退避した旧い置き場の元と先。"""

    source: Path
    target: Path


@dataclass(frozen=True)
class StateReady:
    """用意した状態の置き場と、このときに退避した旧い置き場。"""

    path: Path
    retired: tuple[Retired, ...] = ()

    def items(self) -> list[dict[str, str]]:
        """結果 JSON の `items` へ載せる、退避した置き場の元と先。"""
        return [{"retired_from": str(r.source), "retired_to": str(r.target)} for r in self.retired]


def _env_dir(var: str, *default: str) -> Path:
    """環境変数 `var` が空でなければそのパス、空か無ければ HOME の下の `default`。"""
    value = os.environ.get(var, "")
    return Path(value) if value else Path.home().joinpath(*default)


def _state_home() -> Path:
    return _env_dir("XDG_STATE_HOME", ".local", "state")


def _config_home() -> Path:
    """Claude Code の設定の置き場（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）。"""
    return _env_dir("CLAUDE_CONFIG_DIR", ".claude")


def _foreign_root(root: Path) -> Optional[Path]:
    env = os.environ
    env_root = env[CALLER_ROOT_ENV] if CALLER_ROOT_ENV in env else env.get("CLAUDE_PLUGIN_ROOT", "")
    if not env_root or Path(env_root).resolve() == root.resolve():
        return None
    return Path(env_root)


def load() -> PluginIdentity:
    """自分の根（`ROOT`）の `plugin.json` から自分を求める。"""
    root = ROOT
    manifest = root / ".claude-plugin" / "plugin.json"
    try:
        name = json.loads(manifest.read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityError(f"plugin.json を読めない: {manifest}: {exc}") from exc
    if not isinstance(name, str) or not name:
        raise IdentityError(f"plugin.json の name が文字列でない: {manifest}")
    return PluginIdentity(root=root, name=name, prefix=name + ":", state_dir=_state_home() / name, foreign_root=_foreign_root(root))


class Handling(Enum):
    """旧い置き場の扱い。"""

    RETIRE_HERE = "retire_here"  # このモジュールで退避する
    RETIRE_ELSEWHERE = "retire_elsewhere"  # ほかのモジュールが同じ規則で退避する
    KEEP = "keep"  # 退避しない


@dataclass(frozen=True)
class OldPlace:
    """旧い NDF の状態の置き場の 1 行。`handling` が扱い、`reason` がその理由。

    `path` は置き場のパス。このモジュールで退避する行（`Handling.RETIRE_HERE`）と、退避しない行（`Handling.KEEP`）が
    持つ。退避しない行のパスは、退避する置き場の中に入っていないかを確かめるためだけに使い、触らない。
    ほかのモジュールが退避する行（`Handling.RETIRE_ELSEWHERE`）は None。
    """

    key: str
    where: str
    handling: Handling
    reason: str
    path: Optional[Callable[[PluginIdentity], Path]] = None


# 旧い NDF の状態の置き場の全一覧（#3341 の 3341-3）。正本はこの表だけで、文書へ写さない。
# 規則は「新しい NDF が同じパスへ書く旧い置き場だけを退避する」。退避は `prepare_state_dir` が上から順に行う
OLD_STATE_PLACES: tuple[OldPlace, ...] = (
    OldPlace(
        key="state-home",
        where="<XDG_STATE_HOME か ~/.local/state>/<名前>",
        handling=Handling.RETIRE_HERE,
        reason="新しい状態の置き場と同じパスで、新しい NDF がそこへ書く。退避の後に作り直す",
        path=lambda ident: ident.state_dir,
    ),
    OldPlace(
        key="config-state",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/state",
        handling=Handling.RETIRE_HERE,
        reason="旧い NDF の既定の状態の置き場。新しい NDF は同じ事実を新しい状態の置き場へ書くため、旧い記録を退避して"
        "2 か所に持たない。作り直さない",
        path=lambda ident: _config_home() / ident.name / "state",
    ),
    OldPlace(
        key="checkout-state",
        where="<git の共通ディレクトリ>/<名前>/state",
        handling=Handling.RETIRE_ELSEWHERE,
        reason="新しい NDF の耐久の記録が同じパスへ書く。退避は `core/durable.py` の `prepare_store` が同じ規則で行う（決定 85）",
    ),
    OldPlace(
        key="config-accounts",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/accounts",
        handling=Handling.KEEP,
        reason="利用者のアカウントの設定。新しい NDF は新しい状態の置き場の `relay/accounts` を使い重ならない。移行は S2 #3304",
        path=lambda ident: _config_home() / ident.name / "accounts",
    ),
    OldPlace(
        key="config-relay",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/relay.py",
        handling=Handling.KEEP,
        reason="中継の 1 つの複製で、新しい NDF の導入（`ndf relay install`）が上書きする",
        path=lambda ident: _config_home() / ident.name / "relay.py",
    ),
    OldPlace(
        key="config-locks",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/locks",
        handling=Handling.KEEP,
        reason="動いているプロセスの排他。退避すると動いている旧い NDF の排他が外れる",
        path=lambda ident: _config_home() / ident.name / "locks",
    ),
    OldPlace(
        key="cache",
        where="~/.cache/<名前>",
        handling=Handling.KEEP,
        reason="新しい NDF が使わない、再生成できるキャッシュ",
        path=lambda ident: Path.home() / ".cache" / ident.name,
    ),
)


def old_state_places(ident: PluginIdentity) -> list[Path]:
    """`OLD_STATE_PLACES` のうち、このモジュールで退避する置き場のパス（表の順）。"""
    return _places(ident, Handling.RETIRE_HERE)


def kept_places(ident: PluginIdentity) -> list[Path]:
    """`OLD_STATE_PLACES` のうち、退避しない置き場のパス（表の順）。"""
    return _places(ident, Handling.KEEP)


def _places(ident: PluginIdentity, handling: Handling) -> list[Path]:
    return [place.path(ident) for place in OLD_STATE_PLACES if place.handling is handling and place.path is not None]


def _retire_candidates(place: Path, now: datetime) -> Iterator[Path]:
    """退避先の候補を順に出す。日付の名前、時刻を足した名前、その後に番号を足した名前。"""
    base = place.with_name(place.name + RETIRED_SUFFIX + now.strftime("%Y%m%d"))
    yield base
    stamped = base.with_name(base.name + now.strftime("T%H%M%S"))
    yield stamped
    for n in itertools.count(1):
        yield stamped.with_name(f"{stamped.name}-{n}")


def _retire_target(place: Path, now: datetime) -> Path:
    """退避先のパス。候補のうち最初にまだ無い名前。消さない。"""
    return next(candidate for candidate in _retire_candidates(place, now) if not candidate.exists())


def lock_path(ident: PluginIdentity) -> Path:
    """状態の置き場を用意する処理どうしを排他するロックのファイル。退避で動かないよう置き場の隣に置く。"""
    return ident.state_dir.with_name(ident.state_dir.name + ".lock")


def prepare_state_dir(ident: PluginIdentity, now: Optional[datetime] = None) -> StateReady:
    """状態の置き場を用意し、用意した置き場と退避した旧い置き場を返す（手順はモジュールの説明）。

    退避が要るのに `CLAUDE_PLUGIN_ROOT` が自分の根と違えば、何も変えずに `IdentityError` を投げる。
    """
    ident.state_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path(ident), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_locked(ident, now or datetime.now())


def _is_old(place: Path) -> bool:
    return place.is_dir() and not (place / FORMAT_FILE).exists() and any(place.iterdir())


def _outermost(places: list[Path]) -> list[Path]:
    """ほかの置き場の中にある置き場と、前に同じパスがある置き場を除く（外側を動かせば中も動く）。順は保つ。"""
    resolved = [p.resolve() for p in places]
    return [p for i, (p, r) in enumerate(zip(places, resolved)) if r not in resolved[:i] and not any(o in r.parents for o in resolved)]


def _prepare_locked(ident: PluginIdentity, now: datetime) -> StateReady:
    old = _outermost([p for p in old_state_places(ident) if _is_old(p)])
    _refuse_unsafe_retire(ident, old)
    retired = _retire_all(old, now)
    ident.state_dir.mkdir(parents=True, exist_ok=True)
    _write_format(ident.state_dir)
    return StateReady(ident.state_dir, retired)


def _refuse_unsafe_retire(ident: PluginIdentity, old: list[Path]) -> None:
    """退避してよいと確かめられなければ、何も変えずに `IdentityError` を投げる（ほかの根・退避しない置き場を含む）。"""
    if old and ident.foreign_root is not None:
        raise IdentityError(
            f"CLAUDE_PLUGIN_ROOT（{ident.foreign_root}）が自分の根（{ident.root}）と違うため、旧い状態の置き場を退避しない: "
            + ", ".join(str(p) for p in old)
        )
    holding = _holding_state_dir(ident, old) + _holding_kept(ident, old)
    if holding:
        raise IdentityError(
            "退避する旧い状態の置き場が、退避しない置き場を中に持つため退避しない（外側ごと動かすとそれらも動く）: "
            + ", ".join(f"{outer} が {kept} を含む" for outer, kept in holding)
        )


def _holding_state_dir(ident: PluginIdentity, old: list[Path]) -> list[tuple[Path, Path]]:
    """新しい状態の置き場（とその隣のロック）を中に持つ、退避する置き場との組。

    置き場が無くても、パスの上で中にあれば組にする（ロックを置いた時点で外側は空でなくなり、毎回旧いと判定される）。
    """
    state_dir = ident.state_dir.parent.resolve() / ident.state_dir.name
    return [(outer, ident.state_dir) for outer in old if outer.resolve() in state_dir.parents]


def _holding_kept(ident: PluginIdentity, old: list[Path]) -> list[tuple[Path, Path]]:
    """退避する置き場と、その中にある（あれば）退避しない置き場の組。"""
    # 置き場そのものがリンクでも、置かれている場所で比べる（リンクの先へたどらない）
    kept = [(k, k.parent.resolve() / k.name) for k in kept_places(ident) if os.path.lexists(k)]
    return [(outer, k) for outer in old for k, resolved in kept if outer.resolve() in resolved.parents]


def _retire_all(old: list[Path], now: datetime) -> tuple[Retired, ...]:
    """旧い置き場を順に退避先の名前へ変え、退避した元と先を返す。"""
    retired = []
    for place in old:
        target = _retire_target(place, now)
        place.rename(target)
        retired.append(Retired(place, target))
    if retired:
        log.warning("旧い状態の置き場を退避した: %s", ", ".join(f"{r.source} -> {r.target}" for r in retired))
    return tuple(retired)


def _write_format(state_dir: Path) -> None:
    """`format.json` が無ければ一時の名前で書いてから置き換える（途中で落ちても半端な中身を残さない）。"""
    fmt = state_dir / FORMAT_FILE
    if not fmt.exists():
        tmp = fmt.with_name(f"{FORMAT_FILE}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"format": FORMAT_VERSION}) + "\n", encoding="utf-8")
        os.replace(tmp, fmt)
