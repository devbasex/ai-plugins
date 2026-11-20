"""中継が起動するセッション（レーンの claude・supervise の子の `claude -p`）へ渡す環境（#2464・#2504・#2985）。

渡し方は環境変数の表（`contracts/domains/env.json`）の `child` の列が決める。

- `drop`: 渡さない（VS Code の git 認証の変数。窓を閉じると push と fetch が固まる。#2985。親の Claude Code の
  `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID`。残ると claude が入れ子の起動を断る）
  NDF の起動の部品が入れる `UV_PROJECT_ENVIRONMENT`・`VIRTUAL_ENV`・`UV_RUN_RECURSION_DEPTH`・`CLAUDE_PLUGIN_ROOT`。残ると
  セッションの中の `uv run` が NDF の依存の環境を書き換える）
- `PYTHONPATH` は、`bin/ndf` が先頭に足した NDF の `src` だけを外し、利用者の値は残す
- `set`: NDF が値を入れる（入力欄のサジェストを止める `false`。#2504。指定のアカウントの名前。#2464）

子を起動する処理はどれもこの関数で環境を作る（中継の起動と supervise の子で指定のアカウントが食い違わない）。

中継のセグメントの子は、さらに `with_launched_ndf` で `PATH` を起動先の NDF のバージョンにそろえる（#3909）。
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Mapping, Optional

from ndf.contracts import facts
from ndf.domains.relay import accounts

# `bin/ndf` が PYTHONPATH の先頭に足す NDF の `src`（このファイルから 4 つ上）
NDF_SRC = Path(__file__).resolve().parents[3]


def _set_values(environ: Mapping[str, str], state_dir: Path, account: Optional[str]) -> dict[str, Optional[str]]:
    """`set` の変数に入れる値。None は入れない（親の値も外す）。"""
    return {
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
        "NDF_CLAUDE_ACCOUNT": account if account is not None else accounts.chosen(environ, state_dir),
    }


def _without_ndf_src(value: str) -> Optional[str]:
    """`PYTHONPATH` の値から NDF の `src` と空の要素を外した値。何も残らなければ None。"""
    kept = [p for p in value.split(os.pathsep) if p and Path(p).resolve() != NDF_SRC]
    return os.pathsep.join(kept) if kept else None


def tool_env(environ: Mapping[str, str]) -> dict[str, str]:
    """`drop` の変数と NDF の `src` の `PYTHONPATH` を外した環境（`set` の値は入れず、`CLAUDE_CONFIG_DIR` は残す）。

    中継が打つ `claude plugin` のコマンドはこれを渡し、子のセッションは `child_env` が `set` の値を足して渡す。
    """
    drop = set(facts.ids("env", child="drop"))
    out = {k: v for k, v in environ.items() if k not in drop}
    if "PYTHONPATH" in out:
        pythonpath = _without_ndf_src(out["PYTHONPATH"])
        if pythonpath is None:
            del out["PYTHONPATH"]
        else:
            out["PYTHONPATH"] = pythonpath
    return out


def child_env(environ: Mapping[str, str], state_dir: Path, account: Optional[str] = None) -> dict[str, str]:
    """子のセッションへ渡す環境。`account` を渡せば選び方より先にそれを使う（中継がアカウントを替えたとき）。"""
    out = tool_env(environ)
    values = _set_values(environ, state_dir, account)
    for name in facts.ids("env", child="set"):
        value = values[name]
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def _strict(path: Path) -> tuple[Optional[Path], bool]:
    """`path` を厳密に解いた結果と、存在しない要素で止まったか（ENOENT）。それ以外の失敗は (None, False)。

    循環は 3.11・3.12 が RuntimeError、3.13 以降が ELOOP の OSError。権限が無いなど ENOENT 以外の OSError も
    実体を確かめられないので解決できないものとする。
    """
    try:
        return path.resolve(strict=True), False
    except RuntimeError:
        return None, False
    except OSError as e:
        return None, e.errno == errno.ENOENT


_MAX_LINKS = 40  # Linux の MAXSYMLINKS と同じ。リンク先の無いリンクをたどる深さの上限


def _resolve(path: Path, depth: int = 0) -> Optional[Path]:
    """`path` の実体のパス。循環するリンクを通る・権限が無いなど解決できなければ None。

    既定の `resolve()` は 3.13 以降、循環するリンクで例外を出さず途中までのパスを返すので、厳密に解いて見分ける。
    存在しない要素（ENOENT）で止まったときだけ、存在しないパスも比べられるよう要素を 1 つずつ厳密に解き直し、
    存在しない要素は字面のまま進める。`absent/../loop` のように `..` で実在する側へ戻った先の循環も、
    この解き直しで見つける（厳密でない `resolve()` は `..` の先の循環を無視する）。
    リンク先が存在しないリンクはリンク名で止めず、リンク先を解き直して進める（削除済みの仮想環境を指すリンクを外すため）。
    """
    if depth > _MAX_LINKS:
        return None
    got, missing = _strict(path)
    if got is not None or not missing:
        return got
    cur = Path.cwd() if not path.is_absolute() else Path(path.anchor)
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            cur = cur.parent
            continue
        got, missing = _strict(cur / part)
        if got is not None:
            cur = got
        elif not missing:
            return None
        elif (cur / part).is_symlink():
            try:
                target = os.readlink(cur / part)
            except OSError:
                return None
            got = _resolve(cur / target, depth + 1)
            if got is None:
                return None
            cur = got
        else:
            cur = cur / part
    return cur


def _venv_home(env: Mapping[str, str]) -> Optional[Path]:
    """NDF の仮想環境の保存先（`bin/ndf` の `${XDG_CACHE_HOME:-$HOME/.cache}/ndf/venv` と同じ規則）。解けなければ None。

    循環するリンクを通るなど解決できないときも None を返し、`PATH` の残りの整形を続ける。
    """
    cache = env.get("XDG_CACHE_HOME") or (str(Path(env["HOME"]) / ".cache") if env.get("HOME") else None)
    if not cache:
        return None
    return _resolve(Path(cache) / "ndf" / "venv")


def with_launched_ndf(env: Mapping[str, str], keep: Path) -> dict[str, str]:
    """`PATH` から別のバージョンの NDF の `bin` と NDF の仮想環境の下を外し、起動先 `keep` の `bin` を先頭に置いた環境。

    外すのは、解決したパスが `keep` と同じ親の下の `keep` 以外の根の `bin` か、仮想環境の保存先の下の要素である。
    先頭に置くのは `keep/bin/ndf` が実行できるときだけで、`PATH` の上の旧版へのリンク（`~/.local/bin/ndf` など）を
    残したまま `ndf` を起動先へ解決させる（#3909）。`PATH` が無ければ写しを返す。
    """
    out = dict(env)
    if "PATH" not in out:
        return out
    root = keep.resolve()
    venv = _venv_home(env)

    def other_ndf(item: str) -> bool:
        # 循環するリンクなど解決できない要素は NDF と判定できないので残す
        q = _resolve(Path(item))
        if q is None:
            return False
        if q.name == "bin" and q.parent.parent == root.parent and q.parent != root:
            return True
        return venv is not None and q.is_relative_to(venv)

    kept = [p for p in out["PATH"].split(os.pathsep) if not (p and other_ndf(p))]
    if os.access(keep / "bin" / "ndf", os.X_OK):
        kept.insert(0, str(keep / "bin"))
    out["PATH"] = os.pathsep.join(kept)
    return out
