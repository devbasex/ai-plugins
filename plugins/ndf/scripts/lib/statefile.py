"""状態ファイルの基本操作（収束ループ共通層）。

読み書きと、bash から `eval` できる KEY=VALUE 形式の出力だけを持つ。
収束の判定やスキーマはこの層に入れない（Skill 固有のため）。

`cross-review` の `state.py` と `cross-refactoring` の `refactor.py` が共有する。
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import shlex
import sys
from typing import Any, Callable, Iterable, NamedTuple

# 保存の後に呼ぶ関数（#662）。**共通層は呼ぶだけで、何をするかは知らない。**
# cross-refactoring の `refactor.py` が実行の要約の書き出しを登録する。
AfterSave = Callable[[pathlib.Path, dict[str, Any]], None]
_AFTER_SAVE: list[AfterSave] = []


def register_after_save(hook: AfterSave) -> None:
    """保存の後に呼ぶ関数を登録する。同じ関数を 2 度登録しても 1 度だけ呼ぶ。"""
    if hook not in _AFTER_SAVE:
        _AFTER_SAVE.append(hook)


def unregister_after_save(hook: AfterSave) -> None:
    if hook in _AFTER_SAVE:
        _AFTER_SAVE.remove(hook)


def now() -> str:
    """状態ファイルに書く時刻。ローカル時刻の ISO 8601 形式（秒まで）。"""
    return _dt.datetime.now().isoformat(timespec="seconds")


def load(path: pathlib.Path) -> dict[str, Any]:
    """状態ファイルを読む。存在しなければ FileNotFoundError を上げる。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: pathlib.Path, data: Any) -> None:
    """JSON を原子的に書く。

    同じディレクトリへ一時ファイルを書いてから `replace` する。途中で落ちても
    半端な JSON が残らないため、再開時に必ず読める。失敗は例外で返し、一時
    ファイルは残さない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save(path: pathlib.Path, state: dict[str, Any]) -> None:
    """状態ファイルを原子的に書く（`write_json_atomic`）。"""
    write_json_atomic(path, state)
    # **差し込み口の失敗で保存の呼び出し側を止めない。** 状態は既に書けている。
    for hook in list(_AFTER_SAVE):
        try:
            hook(path, state)
        except Exception as exc:  # noqa: BLE001
            info(f"⚠ 保存の後の処理が失敗しました: {exc}")


def emit(**values: Any) -> None:
    """bash の `eval` で読める KEY=VALUE を標準出力へ書く。

    値は必ず `shlex.quote` を通す。空白や引用符を含む値（作業ディレクトリのパス、
    レビュー担当の CSV など）をそのまま出すと、呼び出し側の `eval` で語が割れる。
    """
    for key, value in values.items():
        if value is None:
            value = ""
        elif isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        print(f"{key}={shlex.quote(str(value))}")


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------- 再開の反映（#727 / #648） ----------


class ResumeField(NamedTuple):
    """反映の表の 1 行。`arg` は argparse の属性名、`key` は状態ファイルの鍵。

    `mode` は `"replace"`（値のある引数を状態へ書き、`resume_changes` に積む）か
    `"notify"`（状態と違うときだけ「反映しない」と知らせる。状態は変えない）。
    """
    arg: str
    key: str
    mode: str


def apply_resume_args(
    state: dict[str, Any],
    args: Any,
    spec: Iterable[ResumeField],
) -> list[str]:
    """再開で渡された引数を表に従って状態へ反映し、標準エラーへ出す行の一覧を返す。

    **この関数は出力も保存もしない。** 呼び出し側が行を `info` で出し、`save` を 1 回で
    行う。未指定（属性が無いか `None`）の項目は何もしない。値が同じ項目は行を返さず、
    記録にも積まない。予約語 `none` の正規化（1 者指定は `None`、一覧は `[]`）は
    呼び出し側が済ませてから渡し、ここでは値をそのまま `!=` で比べる。

    どの引数が `replace` / `notify` かは Skill ごとの表（`spec`）が持つ（設計の決定 13）。
    """
    lines: list[str] = []
    for field in spec:
        new = getattr(args, field.arg, None)
        if new is None:
            continue
        old = state.get(field.key)
        if old == new:
            continue
        if field.mode == "replace":
            state[field.key] = new
            state.setdefault("resume_changes", []).append(
                {"at": now(), "field": field.key, "from": old, "to": new}
            )
            lines.append(f"↻ {field.key}: {old} → {new}")
        else:
            option = "--" + field.arg.replace("_", "-")
            lines.append(f"ℹ {option} は再開では反映しません（状態: {old} / 指定: {new}）")
    return lines
