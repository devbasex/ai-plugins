"""副命令の引数の定義を取り出す（規約の生成の入力。#3518 の決定 3）。

副命令のモジュールを変えずに、`main([])` を argparse が引数を読む呼び出し（`parse_known_args`。`parse_args` も
これを通る）で止め、その時点の parser から引数の木を作る。下位の命令（`_SubParsersAction` の選択肢）を辿り、
下位の命令ごとの引数まで載せる。argparse を使わない副命令は、手書きの引数の表（`manual-args`）の行から木を作る。

argparse には引数の定義を読む公開の API が無く、`_actions` と `_SubParsersAction` の内部の属性を読む。Python の
版の上げで属性が変われば `CheckError` で止まり、`tests/checks/test_spec_args.py` も落ちる。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
from dataclasses import dataclass, field
from typing import Any

from ndf.checks.model import CheckError

_SKIPPED = (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)


@dataclass(frozen=True)
class Arg:
    """引数 1 つ。`name` は位置引数なら dest、オプションなら最も長い `--` の名前。"""

    name: str
    positional: bool
    type: str
    required: bool
    default: Any = None
    choices: tuple[str, ...] | None = None
    help: str = ""
    items: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArgTree:
    """1 つの parser（副命令か下位の命令）の引数と、下位の命令の木。"""

    options: tuple[Arg, ...] = ()
    subcommands: dict[str, ArgTree] = field(default_factory=dict)


class _Captured(Exception):
    """`parse_known_args` に達した parser を運ぶ（`main` の残りを流さない）。"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__("captured")
        self.parser = parser


def extract(module_path: str) -> ArgTree | None:
    """副命令のモジュールの `main([])` を引数を読む直前で止め、その parser の引数の木を返す。

    parser に達せずに `main` が戻るか、`_Captured` 以外の例外（`SystemExit` を含む）で終われば `None`。
    import できなければ `CheckError`。
    """
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:  # 副命令のモジュールの import の失敗を config_broken にする（送出し直す）
        raise CheckError(f"副命令のモジュールを読めない: {module_path}: {type(exc).__name__}: {exc}") from exc
    original = argparse.ArgumentParser.parse_known_args

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        raise _Captured(self)

    argparse.ArgumentParser.parse_known_args = capture  # type: ignore[method-assign]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module.main([])
    except _Captured as got:
        return tree_of(got.parser)
    except BaseException:  # noqa: BLE001, NDF401 外部コマンド: parser に達しない副命令は手書きの引数の表へ回す（決定 3）
        return None
    finally:
        argparse.ArgumentParser.parse_known_args = original  # type: ignore[method-assign]
    return None


def tree_of(parser: argparse.ArgumentParser) -> ArgTree:
    """parser の `_actions` を辿り、引数（定義の順）と下位の命令（名前の昇順）の木を作る。"""
    try:
        actions = list(parser._actions)
        options = tuple(a for a in (arg_of(x) for x in actions) if a is not None)
        choices = {name: sub for a in actions if isinstance(a, argparse._SubParsersAction) for name, sub in a.choices.items()}
    except AttributeError as exc:
        raise CheckError(f"argparse の定義を読めない（内部の属性が変わった）: {exc}") from exc
    return ArgTree(options, {name: tree_of(choices[name]) for name in sorted(choices)})


def _type_word(action: argparse.Action) -> str:
    """値の型の語（JSON Schema の type）。"""
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse.BooleanOptionalAction)):
        return "boolean"
    if isinstance(action, argparse._CountAction) or action.type is int:
        return "integer"
    return "number" if action.type is float else "string"


def _is_array(action: argparse.Action) -> bool:
    return (
        isinstance(action, (argparse._AppendAction, argparse._ExtendAction)) or action.nargs in ("*", "+") or isinstance(action.nargs, int)
    )


def _default(action: argparse.Action) -> Any:
    """既定値。値が無い・真偽の旗の既定（store_true の偽など）は `None`。"""
    value = action.default
    flag_default = isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))
    if value is None or value is argparse.SUPPRESS or flag_default:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, (str, int, float, bool)) for v in value):
        return list(value)
    return None if isinstance(value, (list, tuple)) else str(value)


def arg_of(action: argparse.Action) -> Arg | None:
    """1 つの action を `Arg` にする。help・version・下位の命令の action は `None`。"""
    if isinstance(action, _SKIPPED):
        return None
    positional = not action.option_strings
    if positional:
        name = action.dest
        aliases: tuple[str, ...] = ()
    else:
        longs = [o for o in action.option_strings if o.startswith("--")]
        name = max(longs, key=len) if longs else action.option_strings[0]
        aliases = tuple(o for o in action.option_strings if o != name)
    word = _type_word(action)
    array = _is_array(action) and word != "boolean"
    required = bool(action.required) if not positional else action.nargs not in ("?", "*")
    choices = tuple(str(c) for c in action.choices) if action.choices is not None else None
    help_text = "" if action.help in (None, argparse.SUPPRESS) else str(action.help)
    return Arg(
        name=name,
        positional=positional,
        type="array" if array else word,
        required=required,
        default=_default(action),
        choices=choices,
        help=help_text,
        items=word if array else None,
        aliases=aliases,
    )


def from_manual(rows: list[dict[str, Any]]) -> ArgTree:
    """手書きの引数の表（`manual-args`）の、1 つの副命令の行（呼び方ごとに 1 行）から引数の木を作る。

    行の列 `path` は下位の命令の道（無ければ副命令そのもの）、列 `args` は `<名前>:<型>:<required か optional>:<説明>`
    の並び（型は JSON Schema の type）。
    """
    top: tuple[Arg, ...] = ()
    subs: dict[str, ArgTree] = {}
    for row in rows:
        args = tuple(_manual_arg(spec) for spec in (row["args"] if "args" in row else []))
        if "path" in row:
            subs[row["path"]] = ArgTree(args)
        else:
            top = args
    return ArgTree(top, dict(sorted(subs.items())))


def _manual_arg(spec: str) -> Arg:
    name, kind, need, help_text = spec.split(":", 3)
    return Arg(name=name, positional=not name.startswith("-"), type=kind, required=need == "required", help=help_text)
