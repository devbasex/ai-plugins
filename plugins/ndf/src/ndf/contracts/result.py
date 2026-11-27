"""結果 JSON の組み立て・形の確かめ・出力。

サブコマンドと作業は結果を標準出力へ 1 行の JSON で出し、終了コードで終える。読み手は `status` と `cause.kind`
（と終了コード）だけで次の手を決め、`summary` や Markdown の文から判定しない。
標準出力が端末のときだけ、同じ結果を人が読む文（`text_view`）で出す（#4003）。AI の Bash Tool・hook・スクリプト・`$(...)` の
起動では標準出力が端末にならないので、読み手の受ける形は変わらない。エントリポイント（`cli`）が読んだ `--output-format=json|text` の明示は、
端末かどうかより優先する（`choose_format`）。明示も端末の判定も標準出力へ出すときだけ効き、`stream` を渡した出力は常に JSON。
形の確かめと終了コードは出す形によらず同じ。
形は `contracts/schemas/result.schema.json`、`stopped` の終了コードは停止理由の表の種類から引く。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Optional, TextIO

from ndf import contracts, identity
from ndf.contracts import causes, exit_codes, facts, internal, text_view

# 結果の status の語彙は exit_codes が持ち、ここから result.OK などとしても引ける
from ndf.contracts.exit_codes import GATE, OK, STATUSES, STOPPED

SCHEMA = contracts.schema_path("result")
# 判断を求めた結果の metrics で、確かめを通らなかった必須の出力の名前を載せるキー（worker・プラン・工程の進行が読み書きする。#4513）
MISSING_OUTPUTS = "missing_outputs"


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
    """routes のキーのうち、回答の語彙（事実の表 answers）に無いもの（1 つにつき 1 行。キーは文に入れない）。形の誤りはスキーマが報告する。"""
    if not isinstance(routes, dict):
        return []
    known = set(facts.ids(ANSWERS))
    return [f"routes のキーが回答の語彙（facts/{ANSWERS}.json）に無い" for _ in set(routes) - known]


def severity_errors(items: object) -> list[str]:
    """items の `severity` のうち、重さの表（事実の表 severities）に無いもの（値は文に入れない）。形の誤り（文字列でない・空）はスキーマが報告する。"""
    rows = items if isinstance(items, list) else []
    given = [(i, row["severity"]) for i, row in enumerate(rows) if isinstance(row, dict) and "severity" in row]
    words = [(i, word) for i, word in given if isinstance(word, str) and word]
    known = set(facts.ids(SEVERITIES)) if words else set()
    return [f"items/{i}/severity が重さの表（{facts.location(SEVERITIES)}）に無い" for i, word in words if word not in known]


def pr_numbers(obj: Any) -> list[int]:
    """結果 JSON の items の PR の番号（`{"kind": "pr", "number": <整数>}`）を並びの順に。items が無い・形が違えば空。

    プランのステップの結果（`core.plan.steps`）と工程の出力の補い（`progress.stages.step_outputs`）が同じ規則で読む。"""
    items = obj.get("items") if isinstance(obj, dict) and isinstance(obj.get("items"), list) else []
    return [i["number"] for i in items if isinstance(i, dict) and i.get("kind") == "pr" and type(i.get("number")) is int]


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
        if kind is not None and causes.has(kind):
            return causes.exit_code(kind)
    return None


def errors(obj: Any, code: Optional[int] = None) -> list[str]:
    """結果の形の誤りを並べる。空なら正しい。`code` を渡すと status と種類との対応も確かめる。スキーマの誤りの文は結果の値を含む
    （`emit` が外へ出す文は `_safe_errors`）。"""
    return contracts.schema_errors(obj, SCHEMA) + _table_errors(obj, code)


def _safe_errors(obj: Any, code: Optional[int]) -> list[str]:
    """結果の値もキーも含まない誤りの文（誤り 1 件につき 1 つ）。スキーマの誤りは、スキーマの場所と規則の名前にする。"""
    return contracts.broken_rules(obj, SCHEMA) + _table_errors(obj, code)


def _table_errors(obj: Any, code: Optional[int]) -> list[str]:
    """スキーマでは見ない誤り（表に無い語・バージョン・終了コード）。文は結果の値もキーも含まない。"""
    errs: list[str] = []
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


def outputs_of(obj: Mapping[str, Any]) -> dict[str, Any]:
    """結果や事象の `outputs`。dict でなければ空の dict。"""
    found = obj.get("outputs")
    return found if isinstance(found, dict) else {}


def received(obj: Any) -> Optional[dict[str, Any]]:
    """ほかのプロセスや Skill から受けた結果。新しいバージョンなら代わりの `stopped`（precondition）、形が違えば None。"""
    refused = refused_newer(obj)
    if refused is not None:
        return refused
    return obj if not errors(obj) else None


def last_line(stdout: str) -> Optional[dict[str, Any]]:
    """標準出力の最後の空でない行の結果 JSON（新しいバージョンなら代わりの `stopped`。`received`）。読めないか形が違えば None。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 結果 JSON を返さない作業は None で呼ぶ側が bad_report にする
        return None
    return received(obj)


def refused_newer(obj: Any) -> Optional[dict[str, Any]]:
    """結果の形のバージョンが新しければ、代わりの `stopped`（precondition）。読めるバージョンなら None。"""
    if not newer(obj):
        return None
    return stopped(ENTRY, "precondition", "新しいバージョンの結果を古い NDF が受け取った。プラグインを更新する")


def _code_errors(obj: dict[str, Any], code: int) -> list[str]:
    """終了コードが status の区分と、stopped なら cause.kind の終了コードと合うか。渡された終了コードは文に入れない。"""
    errs: list[str] = []
    if not exit_codes.matches(obj["status"], code):
        errs.append(f"status {obj['status']} と終了コードが合わない")
    expected = code_for(obj)
    if obj["status"] == STOPPED and expected is not None and code != expected:
        errs.append(f"cause.kind {obj['cause']['kind']} の終了コードは {expected} で、渡された終了コードと合わない")
    return errs


# 出力の形（`--output-format` の値）
JSON, TEXT = "json", "text"
FORMATS = (JSON, TEXT)
# エントリポイントが読んだ明示の形（None なら標準出力が端末かどうかで決める）
_chosen: Optional[str] = None


def choose_format(fmt: Optional[str]) -> None:
    """標準出力へ出す結果の形を明示する（None で明示を外す）。知らない形は `ValueError`。"""
    global _chosen
    if fmt is not None and fmt not in FORMATS:
        raise ValueError(f"知らない出力の形: {fmt}")
    _chosen = fmt


def _format_for(stream: Optional[TextIO]) -> str:
    """出す形。`stream` を渡した出力は JSON、標準出力は明示があればそれ、無ければ端末なら text。"""
    if stream is not None and stream is not sys.stdout:
        return JSON
    if _chosen is not None:
        return _chosen
    try:
        return TEXT if sys.stdout.isatty() else JSON
    except (AttributeError, ValueError):  # 標準出力が閉じているか、差し替えで isatty が無いときは JSON
        return JSON


def _put_out(obj: dict[str, Any], stream: Optional[TextIO], columns: Optional[Sequence[text_view.Column]] = None) -> None:
    """結果を出す形で 1 回書く。"""
    text = text_view.render_text(obj, columns) if _format_for(stream) == TEXT else dumps_line(obj)
    print(text, file=stream or sys.stdout)


def dumps_line(obj: Any) -> str:
    """結果を 1 行の JSON にする（非 ASCII はそのまま）。"""
    return json.dumps(obj, ensure_ascii=False)


def emit(
    obj: dict[str, Any],
    code: Optional[int] = None,
    stream: Optional[TextIO] = None,
    columns: Optional[Sequence[text_view.Column]] = None,
) -> int:
    """結果を確かめて 1 行で出し、終了コードを返す。形が違えば元の結果を出さず、値を含まない誤りの文を標準エラーへ書き、代わりの `stopped`（invalid）を
    1 行で出して invalid の終了コードを返す。標準出力が端末なら文で出す（`_format_for`）。`columns` は文の items の表の列と見出し
    （省けば items のキーを並べる。JSON には効かない）。"""
    if code is None:
        code = code_for(obj)
    errs = _safe_errors(obj, code)
    if errs or code is None:
        return _emit_instead(errs or ["終了コードを決められない（stopped に cause が無い）"], stream)
    _put_out(obj, stream, columns)
    return code


def _emit_instead(errs: list[str], stream: Optional[TextIO]) -> int:
    """形の違う結果の誤りの文（値を含まないもの）を標準エラーへ書き、代わりの `stopped`（invalid）を 1 行で出す。元の結果の値は `tool` も入れない。"""
    lines = list(dict.fromkeys(errs))
    for line in lines:
        print(f"結果の形の誤り: {line}", file=sys.stderr)
    detail = "・".join([f"結果 JSON の形が違う（{len(errs)} 件）", *lines])
    try:
        instead, code = stopped(ENTRY, "invalid", detail), causes.exit_code("invalid")
    except (contracts.ContractError, ValueError):  # 停止理由の表が読めないか、種類が無い
        return emit_unchecked(ENTRY, "invalid", detail)
    _put_out(instead, stream)
    return code


def emit_unchecked(tool: str, kind: str, detail: str, items: Optional[list] = None) -> int:
    """コントラクトの表が読めないとき、表で確かめずに `stopped` の結果を 1 行で出す。終了コードは事実の表の unreadable。"""
    _put_out(result(tool, STOPPED, detail, items=items, cause=causes.build(kind, detail)), None)
    return exit_codes.unreadable_code()


def render_report(tool: str, items: list, check: bool, *, stale_summary: str, ok_summary: str, written_summary: str) -> int:
    """生成物の検証（`render [--check]`）の結果を出し、終了コードを返す。

    `--check` で違いがあれば `stopped`（check_failed）に違うファイルを、無ければ ok を出す。書いたときは書いたファイルを
    ok の items に置く。`items` の path の形は呼ぶ側が決める。
    """
    if check and items:
        return emit(stopped(tool, "check_failed", stale_summary, items=items))
    return emit(result(tool, OK, ok_summary if check else written_summary, items=items))
