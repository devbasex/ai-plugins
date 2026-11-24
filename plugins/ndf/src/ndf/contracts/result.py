"""結果 JSON の組み立て・形の確かめ・出力。

サブコマンドと作業は結果を標準出力へ 1 行の JSON で出し、終了コードで終える。読み手は `status` と `cause.kind`
（と終了コード）だけで次の手を決め、`summary` や Markdown の文から判定しない。
形は `contracts/schemas/result.schema.json`、`stopped` の終了コードは停止理由の表の種類から引く。
"""

from __future__ import annotations

import json
import sys
from typing import Any, NamedTuple, Optional, TextIO

from ndf import contracts, identity
from ndf.contracts import causes, exit_codes, facts, internal

# 結果の status の語彙は exit_codes が持ち、ここから result.OK などとしても引ける
from ndf.contracts.exit_codes import GATE, OK, STATUSES, STOPPED

SCHEMA = contracts.schema_path("result")


# 任意の項目のうち配列のもの（置くときは写しの list にする）
_LISTS = ("gaps", "process", "decisions", "warnings")
# 任意の項目（渡した順でなく、この順に置く）
_OPTIONAL = (*_LISTS, "cause", "routes", "presentation_path", "next", "outputs", "run_id")
# routes のキーの語彙を持つ事実の表
ANSWERS = "answers"
# items の severity の語彙を持つ事実の表
SEVERITIES = "severities"
# エントリポイントの短い名前（next・routes のコマンドの先頭。パスとバージョンを含めない）
ENTRY = identity.FIXED_WORDS["entrypoint"]
# 新しいバージョンの結果を止めるときの文（値を含まない）
NEWER = "新しいバージョンの結果を古い NDF が受け取った。プラグインを更新する"
# 新しいバージョンの結果で止めたことを、止まった結果の `cause.instance` で後の読み手へ伝える値（ふつうの precondition と見分ける）
NEWER_INSTANCE = "result-newer"


def _put_present(out: dict[str, Any], **optional: Any) -> None:
    """空でない任意の項目だけを渡した順に `out` へ置く。配列の項目は写しの list にする。"""
    for key, val in optional.items():
        if val:
            out[key] = list(val) if key in _LISTS else val


def result(
    tool: str,
    status: str,
    summary: str,
    items: Optional[list] = None,
    metrics: Optional[dict] = None,
    **optional: Any,
) -> dict[str, Any]:
    """結果を組む。任意の項目（`_OPTIONAL`: cause・routes・gaps・process・decisions・warnings・presentation_path・next・
    outputs・run_id）は
    渡したときだけ置く（空の配列は置かない）。知らない項目は `TypeError`。形は `errors` が確かめる。"""
    unknown = sorted(set(optional) - set(_OPTIONAL))
    if unknown:
        raise TypeError(f"結果の知らない項目: {', '.join(unknown)}")
    out: dict[str, Any] = {
        "tool": tool,
        "status": status,
        "summary": summary,
        "items": list(items) if items is not None else [],
        "metrics": dict(metrics) if metrics is not None else {},
    }
    _put_present(out, **{key: optional.get(key) for key in _OPTIONAL})
    return out


def stopped(
    tool: str,
    kind: str,
    detail: str,
    *,
    summary: Optional[str] = None,
    instance: Optional[str] = None,
    retry_after: Optional[float] = None,
    **extra: Any,
) -> dict[str, Any]:
    """`stopped` の結果を種類と説明から組む。`summary` を省けば説明を使う。

    `instance` と `retry_after` は `cause` へ、残りのキーワード（`extra`）は `result` へ渡す。
    """
    c = causes.cause(kind, detail, instance=instance, retry_after=retry_after)
    return result(tool, STOPPED, summary if summary is not None else detail, cause=c, **extra)


def command(*args: str, ask: tuple[str, ...] = ()) -> dict[str, Any]:
    """`next`・`routes` の値（そのまま打てるコマンド）を組む。`args` はエントリポイントの名前の後ろの引数、`ask` は打つ側が埋めるキー。"""
    out: dict[str, Any] = {"argv": [ENTRY, *args]}
    if ask:
        out["ask"] = list(ask)
    return out


def answer_errors(routes: object) -> list[str]:
    """routes のキーのうち、回答の語彙（事実の表 answers）に無いもの。形の誤りはスキーマが報告する。"""
    if not isinstance(routes, dict):
        return []
    known = set(facts.ids(ANSWERS))
    return [f"routes のキー {key!r} は回答の語彙（facts/{ANSWERS}.json）に無い" for key in sorted(set(routes) - known)]


def severity_errors(items: object) -> list[str]:
    """items の `severity` のうち、重さの表（事実の表 severities）に無いもの。形の誤り（文字列でない・空）はスキーマが報告する。"""
    rows = items if isinstance(items, list) else []
    given = [(i, row["severity"]) for i, row in enumerate(rows) if isinstance(row, dict) and "severity" in row]
    words = [(i, word) for i, word in given if isinstance(word, str) and word]
    known = set(facts.ids(SEVERITIES)) if words else set()
    return [f"items/{i}/severity: {word!r} は重さの表（{facts.location(SEVERITIES)}）に無い" for i, word in words if word not in known]


def item_fields() -> frozenset[str]:
    """結果のコントラクトが items の要素に形を決めた欄の名前。"""
    return frozenset(contracts.load_json(SCHEMA)["$defs"]["item"]["properties"])


def code_for(obj: Any) -> Optional[int]:
    """既定の終了コード。ok は 0、gate は承認ゲートの最初の値、stopped は cause.kind の終了コード。決められなければ None。"""
    if not isinstance(obj, dict):
        return None
    status = obj.get("status")
    if status in (OK, GATE):
        return exit_codes.first_code(status)
    if status == STOPPED:
        kind = causes.kind_of(obj.get("cause"))
        if kind is not None and kind in causes.table():
            return causes.exit_code(kind)
    return None


def errors(obj: Any, code: Optional[int] = None) -> list[str]:
    """結果の形の誤りを並べる。空なら正しい。`code` を渡すと status と種類との対応も確かめる。"""
    errs = contracts.schema_errors(obj, SCHEMA)
    if not isinstance(obj, dict):
        return errs
    if "cause" in obj:
        # cause の形はスキーマ（$defs/cause）が上で確かめた。表と照らす種類の有無だけを足す
        errs += causes.kind_errors(obj["cause"])
    if "routes" in obj:
        errs += answer_errors(obj["routes"])
    if "items" in obj:
        errs += severity_errors(obj["items"])
    if newer(obj):
        errs.append("version が読めるバージョンより新しい")
    if code is not None and obj.get("status") in STATUSES:
        errs += _code_errors(obj, code)
    return errs


def newer(obj: Any) -> bool:
    """結果の形のバージョンが、読めるバージョンより新しいか（バージョンの無い結果は 1）。整数でないバージョンは形の誤りで、ここでは見ない。"""
    found = obj.get("version") if isinstance(obj, dict) else None
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return False
    return found > internal.version() and found % 1 == 0  # JSON の 2.0 はスキーマでは整数


def received(obj: Any) -> Optional[dict[str, Any]]:
    """ほかのプロセスや Skill から受けた結果。新しいバージョンなら代わりの `stopped`（precondition）、形が違えば None。"""
    refused = refused_newer(obj)
    if refused is not None:
        return refused
    return obj if not errors(obj) else None


def refused_newer(obj: Any) -> Optional[dict[str, Any]]:
    """結果の形のバージョンが新しければ、代わりの `stopped`（precondition）。読めるバージョンなら None。

    確かめていない結果の値は `tool` も引き継がない（形の合う秘密が入りうる）。`tool` はエントリポイントの名前にする。
    """
    return stopped(ENTRY, "precondition", NEWER, instance=NEWER_INSTANCE) if newer(obj) else None


def stopped_by_newer(obj: Any) -> bool:
    """新しいバージョンの結果を受けて止まった結果（`refused_newer` か、それを受けて止まった呼び手の結果）か。"""
    cause = obj.get("cause") if isinstance(obj, dict) else None
    return causes.kind_of(cause) == "precondition" and cause.get("instance") == NEWER_INSTANCE


def _code_errors(obj: dict[str, Any], code: int) -> list[str]:
    """終了コードが status の区分と、stopped なら cause.kind の終了コードと合うか。"""
    errs: list[str] = []
    if not exit_codes.matches(obj["status"], code):
        errs.append(f"status {obj['status']} と終了コード {code} が合わない")
    expected = code_for(obj)
    if obj["status"] == STOPPED and expected is not None and code != expected:
        errs.append(f"cause.kind {obj['cause']['kind']} の終了コードは {expected}（渡された値 {code}）")
    return errs


def dumps_line(obj: Any) -> str:
    """結果を 1 行の JSON にする（非 ASCII はそのまま）。"""
    return json.dumps(obj, ensure_ascii=False)


class Line(NamedTuple):
    """出す結果の 1 行（改行を含まない）と終了コード。`valid` は元の結果の行か（偽なら形の違う結果の代わりの `stopped`）。"""

    text: str
    code: int
    valid: bool


def line_of(obj: Any, code: Optional[int] = None) -> Line:
    """結果を確かめ、1 行に 1 回だけ整形する。形が違えば誤りを標準エラーへ書き、代わりの `stopped`（invalid）の行にする。"""
    if code is None:
        code = code_for(obj)
    errs = errors(obj, code)
    if errs or code is None:
        errs = errs or ["終了コードを決められない（stopped に cause が無い）"]
        for line in errs:
            print(f"結果の形の誤り: {line}", file=sys.stderr)
        return _instead(obj, len(errs))
    return Line(dumps_line(obj), code, True)


def emit(obj: dict[str, Any], code: Optional[int] = None, stream: Optional[TextIO] = None) -> int:
    """結果を確かめて 1 行で出し、終了コードを返す。形が違えば元の結果を出さず、誤りを標準エラーへ書き、代わりの `stopped`（invalid）を
    1 行で出して invalid の終了コードを返す。"""
    line = line_of(obj, code)
    print(line.text, file=stream or sys.stdout)
    return line.code


def _instead(obj: Any, count: int) -> Line:
    """形の違う結果の代わりの `stopped`（invalid）。元の結果の値（`tool` を含む）も、値を含みうる誤りの文も入れない。"""
    detail = "・".join([f"結果 JSON の形が違う（{count} 件）", *contracts.broken_rules(obj, SCHEMA)])
    try:
        return Line(dumps_line(stopped(ENTRY, "invalid", detail)), causes.exit_code("invalid"), False)
    except (contracts.ContractError, ValueError):  # 停止理由の表が読めないか、種類が無い
        return Line(dumps_line(_unchecked(ENTRY, "invalid", detail)), exit_codes.unreadable_code(), False)


def _unchecked(tool: str, kind: str, detail: str, items: Optional[list] = None) -> dict[str, Any]:
    """表で確かめずに組んだ `stopped` の結果。"""
    return result(tool, STOPPED, detail, items=items, cause=causes.build(kind, detail))


def emit_unchecked(tool: str, kind: str, detail: str, items: Optional[list] = None) -> int:
    """コントラクトの表が読めないとき、表で確かめずに `stopped` の結果を 1 行で出す。終了コードは事実の表の unreadable。"""
    print(dumps_line(_unchecked(tool, kind, detail, items)))
    return exit_codes.unreadable_code()


def render_report(tool: str, items: list, check: bool, *, stale_summary: str, ok_summary: str, written_summary: str) -> int:
    """生成物の検証（`render [--check]`）の結果を出し、終了コードを返す。

    `--check` で違いがあれば `stopped`（check_failed）に違うファイルを、無ければ ok を出す。書いたときは書いたファイルを
    ok の items に置く。`items` の path の形は呼ぶ側が決める。
    """
    if check and items:
        return emit(stopped(tool, "check_failed", stale_summary, items=items))
    return emit(result(tool, OK, ok_summary if check else written_summary, items=items))
