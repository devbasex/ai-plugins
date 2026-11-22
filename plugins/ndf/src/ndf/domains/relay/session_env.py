"""中継が起動するセッション（レーンの claude・supervise の子の `claude -p`）へ渡す環境（#2464・#2504・#2985）。

渡し方は環境変数の表（`contracts/domains/env.json`）の `child` の列が決める。

- `drop`: 渡さない（VS Code の git 認証の変数。窓を閉じると push と fetch が固まる。#2985。親の Claude Code の
  `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID`。残ると claude が入れ子の起動を断る）
  NDF の起動の部品が入れる `UV_PROJECT_ENVIRONMENT`・`VIRTUAL_ENV`・`UV_RUN_RECURSION_DEPTH`・`CLAUDE_PLUGIN_ROOT`。残ると
  セッションの中の `uv run` が NDF の依存の環境を書き換える）
- `PYTHONPATH` は、`bin/ndf` が先頭に足した NDF の `src` だけを外し、利用者の値は残す
- `set`: NDF が値を入れる（入力欄のサジェストを止める `false`。#2504。指定のアカウントの名前。#2464）
- `account-set`: 登録済みアカウントの子で NDF が値を入れる（`account_env`。設定ディレクトリ・共有の設定ディレクトリ・
  プラグインのキャッシュ・使用率の書き先。#3604）
- `account-drop`: 登録済みアカウントと従量の接続と API キーの子へ渡さない（認証の変数。`account_env`・`metered_env`・`key_env`）

子を起動する処理はどれもこの関数で環境を作る（中継の起動と supervise の子で指定のアカウントが食い違わない）。
登録済みアカウント・従量の接続・API キーの子は、`child_env` の出力にそれぞれ `account_env`・`metered_env`・`key_env` を当てる。
どれも純粋な関数で、ファイルを読まない（用意の済んだ設定ディレクトリのパスは呼び手が渡す）。

中継のセグメントの子は、さらに `with_launched_ndf` で `PATH` を起動先の NDF のバージョンにそろえる（#3909）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.domains.relay import account_registry, accounts

# `bin/ndf` が PYTHONPATH の先頭に足す NDF の `src`（このファイルから 4 つ上）
NDF_SRC = Path(__file__).resolve().parents[3]
# ラッパーが確定し、従量の接続の設定が持ってはいけない変数（表の child が set・drop・account-set の変数）。
# 名前が `NDF_` で始まる変数も同じ扱い（`account_registry.check_decl` と同じ規則）
METERED_FIXED = frozenset(n for c in ("set", "drop", "account-set") for n in facts.ids("env", child=c))
# API キーの子が親から受け継ぐ接続先とヘッダー（起動の前の確かめが許す接続先と比べ、当たればそのキーを候補から外す）
KEY_KEPT = ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS")


def _set_values(environ: Mapping[str, str], state_dir: Path, account: Optional[str], decided: bool) -> dict[str, Optional[str]]:
    """`set` の変数に入れる値。None は入れない（親の値も外す）。"""
    return {
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
        "NDF_CLAUDE_ACCOUNT": account if decided or account is not None else accounts.chosen(environ, state_dir),
    }


def _account_values(env: Mapping[str, str], config_dir: Path, usage_file: Path) -> dict[str, str]:
    """`account-set` の変数に入れる値。"""
    # 親に設定ディレクトリが無ければ空文字（~/.claude を指す）
    shared = next((env[k] for k in ("NDF_SHARED_CONFIG_DIR", "CLAUDE_CONFIG_DIR") if k in env), "")
    cache = (
        env["CLAUDE_CODE_PLUGIN_CACHE_DIR"]
        if "CLAUDE_CODE_PLUGIN_CACHE_DIR" in env
        else str(account_registry.shared_config_dir(env) / "plugins")
    )
    return {
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "NDF_SHARED_CONFIG_DIR": shared,
        "CLAUDE_CODE_PLUGIN_CACHE_DIR": cache,
        "NDF_ACCOUNT_USAGE_FILE": str(usage_file),
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


def child_env(environ: Mapping[str, str], state_dir: Path, account: Optional[str] = None, decided: bool = False) -> dict[str, str]:
    """子のセッションへ渡す環境。`account` を渡せば選び方より先にそれを使う（中継がアカウントを替えたとき）。

    `decided` が真なら `account` をそのまま `NDF_CLAUDE_ACCOUNT` にし、None なら外す（起動先を確定したラッパー。
    指定の用意に落ちて既定のログインへ移った子に、落ちたアカウントの名前を残さない。#3604）。
    偽のときだけ、`account` が None なら選び方（`accounts.chosen`）を引く。
    """
    out = tool_env(environ)
    values = _set_values(environ, state_dir, account, decided)
    for name in facts.ids("env", child="set"):
        value = values[name]
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def _without_auth(env: Mapping[str, str]) -> dict[str, str]:
    """`account-drop` の変数（認証の変数）を外した写し。"""
    drop = set(facts.ids("env", child="account-drop"))
    return {k: v for k, v in env.items() if k not in drop}


def account_env(env: Mapping[str, str], config_dir: Path, usage_file: Path) -> dict[str, str]:
    """登録済みアカウントの子の環境。`child_env` の出力 `env` に `account-set` の変数を入れ、`account-drop` の変数を外す。

    `config_dir` は用意の済んだアカウントの設定ディレクトリ（`account_dir.prepare` が ok を返したパス）、`usage_file` は
    ラッパーが解いた使用率の書き先の絶対パス。`NDF_SHARED_CONFIG_DIR` は親にあれば親の値、無ければ親の
    `CLAUDE_CONFIG_DIR`（無ければ空文字）。`CLAUDE_CODE_PLUGIN_CACHE_DIR` は親にあれば親の値、無ければ
    `<共有の設定ディレクトリ>/plugins`。表の `account-set` に値の無い名前があれば `KeyError`（表とコードの食い違い）。
    """
    out = _without_auth(env)
    values = _account_values(env, config_dir, usage_file)
    for name in facts.ids("env", child="account-set"):
        out[name] = values[name]
    return out


def metered_env(env: Mapping[str, str], extra: Mapping[str, str], saved: bool) -> dict[str, str]:
    """従量の接続の子の環境。`child_env` の出力 `env` を共有の設定ディレクトリへ戻し、`extra`（従量の接続の設定）を重ねる。

    `CLAUDE_CONFIG_DIR` は、親に `NDF_SHARED_CONFIG_DIR` があればその値（空なら外す）、無ければ親のまま。
    `NDF_SHARED_CONFIG_DIR`・`NDF_ACCOUNT_USAGE_FILE` と `account-drop` の変数を外し、保存した設定（`saved`）なら
    起動の前に比べる変数（`account_registry.METERED_CHECKED`。秘密の変数のほか接続先・資格情報の選択・地域）も親から外す
    （比べる期待値をこの出力から作るので、保存した設定に無い親の接続先や資格情報の選択を期待値に入れない）。`extra` は検証を通したもの（呼び手が確かめる）で、
    ラッパーが確定する変数（`METERED_FIXED` と `NDF_` で始まる変数）は `extra` に依らず、`NDF_CLAUDE_ACCOUNT` は `metered`。
    API キーの設定（`metered_kind(extra)` が `API_KEY_KIND`）では、比べる変数をすべて親から外し、`extra` のキー（`ANTHROPIC_API_KEY`）を
    重ねない（親だけにある接続先・ヘッダー・資格情報の選択を子に残さず、キーは helper で渡す）。
    """
    # 親がアカウントの子なら共有の設定ディレクトリへ戻し、それが空（~/.claude）なら外す
    if "NDF_SHARED_CONFIG_DIR" in env:
        config = None if env["NDF_SHARED_CONFIG_DIR"] == "" else env["NDF_SHARED_CONFIG_DIR"]
    else:
        config = env.get("CLAUDE_CONFIG_DIR")
    key = account_registry.metered_kind(extra) == account_registry.API_KEY_KIND
    drop = {"NDF_SHARED_CONFIG_DIR", "NDF_ACCOUNT_USAGE_FILE", *(account_registry.METERED_CHECKED if saved or key else ())}
    out = {k: v for k, v in _without_auth(env).items() if k not in drop}
    skip = {"ANTHROPIC_API_KEY"} if key else set()  # キーは `--settings` の `apiKeyHelper` で渡す
    out.update({k: v for k, v in extra.items() if k not in METERED_FIXED and not k.startswith("NDF_") and k not in skip})
    out["NDF_CLAUDE_ACCOUNT"] = account_registry.METERED
    if config is None:
        out.pop("CLAUDE_CONFIG_DIR", None)
    else:
        out["CLAUDE_CONFIG_DIR"] = config
    return out


def key_env(env: Mapping[str, str], key: str) -> dict[str, str]:
    """API キーの子の環境。従量の接続の保存した設定と同じく共有の設定ディレクトリへ戻し、認証の変数と秘密の変数を外す。

    `NDF_CLAUDE_ACCOUNT` は `key:<キーの名前>`。キーの値は受けず、環境にも入れない（起動引数の `--settings` の
    `apiKeyHelper` で渡す）。親の `ANTHROPIC_BASE_URL`・`ANTHROPIC_CUSTOM_HEADERS`（`KEY_KEPT`）は黙って外さない
    （起動の前の確かめが許す接続先と比べ、ヘッダーがあればそのキーを候補から外す）。
    """
    out = metered_env(env, {}, saved=True)
    for name in KEY_KEPT:
        if name in env:
            out[name] = env[name]
    out["NDF_CLAUDE_ACCOUNT"] = f"key:{key}"
    return out


# 1 つのパスを解く間に辿るリンクの上限（Linux の名前解決と同じ 40）。超えたら循環とみなす
_MAX_LINK_HOPS = 40


def _resolve(path: Path) -> Optional[Path]:
    """`path` の実体のパス。循環するリンクを通るなど解決できなければ None。

    先頭から 1 要素ずつリンクを辿って解く。リンク先が存在しないリンクはリンク先の字面へ進めるので、
    削除済みの仮想環境を指すリンクもリンク先で判定できる（結果にリンクは残らない）。存在しない要素は字面のまま進める。
    `..` は辿った後の実体のパスの親へ戻すので、`loop/../old/bin` のように循環の直後の `..` が循環を消すことはない。
    辿ったリンクが上限を超えたら循環として None を返す。相対パスで、作業ディレクトリが削除されているなど
    `os.getcwd()` が失敗するときも None を返す（呼び出し側は要素を残し、作業ディレクトリの確認で止める）。
    `os.path.realpath` は循環したリンクの名前に残りを字面でつなぎ、その `..` を字面で消して循環を隠す。
    `Path.resolve()` は 3.11・3.12 が循環で RuntimeError を出す。どちらも使わない（#3909）。
    """
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        try:
            raw = os.path.join(os.getcwd(), raw)
        except OSError:  # noqa: NDF401 ファイル: 作業ディレクトリが消えていれば要素を解かずに残す
            return None
    pending = list(reversed(raw.split(os.sep)))
    resolved = os.sep
    hops = 0
    while pending:
        name = pending.pop()
        if name in ("", "."):
            continue
        if name == "..":
            resolved = os.path.dirname(resolved)
            continue
        candidate = os.path.join(resolved, name)
        try:
            target = os.readlink(candidate)
        except OSError:
            # リンクでない・存在しない・読めない要素は字面のまま進める
            resolved = candidate
            continue
        hops += 1
        if hops > _MAX_LINK_HOPS:
            return None
        if os.path.isabs(target):
            resolved = os.sep
        pending.extend(reversed(target.split(os.sep)))
    return Path(resolved)


def _venv_home(env: Mapping[str, str], keep: Path) -> Optional[Path]:
    """NDF の仮想環境の保存先（`bin/ndf` の `${XDG_CACHE_HOME:-$HOME/.cache}/<プラグイン名>/venv` と同じ規則）。解けなければ None。

    循環するリンクを通るなど解決できないときも None を返し、`PATH` の残りの整形を続ける。
    """
    cache = env.get("XDG_CACHE_HOME") or (str(Path(env["HOME"]) / ".cache") if env.get("HOME") else None)
    name = identity.name_of(keep)
    if not cache or not name:
        return None
    return _resolve(Path(cache) / name / "venv")


def should_drop(item: str, root: Optional[Path], venv: Optional[Path]) -> bool:
    """`PATH` の要素 `item` が、起動先の根 `root` 以外の NDF の `bin` か仮想環境の保存先 `venv` の下なら真。"""
    # 循環するリンクを通るなど解決できない要素は NDF と判定できないので残す
    resolved = _resolve(Path(item))
    if resolved is None:
        return False
    if root is not None and resolved.name == "bin" and resolved.parent.parent == root.parent and resolved.parent != root:
        return True
    return venv is not None and resolved.is_relative_to(venv)


def with_launched_ndf(env: Mapping[str, str], keep: Path) -> dict[str, str]:
    """`PATH` から別のバージョンの NDF の `bin` と NDF の仮想環境の下を外し、起動先 `keep` の `bin` を先頭に置いた環境。

    外すのは、解決したパスが `keep` と同じ親の下の `keep` 以外の根の `bin` か、仮想環境の保存先の下の要素である。
    先頭に置くのは `keep/bin/ndf` が実行できるときだけで、`PATH` の上の旧版へのリンク（`~/.local/bin/ndf` など）を
    残したまま `ndf` を起動先へ解決させる（#3909）。`PATH` が無ければ写しを返す。
    """
    out = dict(env)
    if "PATH" not in out:
        return out
    root = _resolve(keep)
    venv = _venv_home(env, keep)
    kept = [p for p in out["PATH"].split(os.pathsep) if not (p and should_drop(p, root, venv))]
    if os.access(identity.entrypoint(keep), os.X_OK):
        kept.insert(0, str(keep / "bin"))
    out["PATH"] = os.pathsep.join(kept)
    return out
