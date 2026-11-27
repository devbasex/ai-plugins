"""指摘の基準と重要度（#3941。純粋な関数）。

- 基準の文は `contracts/data/review-criteria.json`、重要度の尺度と収束の妨げになるか（`blocking`）は
  `contracts/domains/severities.json` が持つ。相手役と修正担当への指示はここで組む
- 相手役は基準に当たる指摘だけを重要度つきで書き、critical・major には起きることの 1 文（`impact`）を添える。ジャーナルへ入れるときは
  1 文の無い critical・major を minor へ下げ、重要度の無い・知らない値の指摘は 1 文があれば major、無ければ minor にする
  （#4014。`effective_severity`）。分からない重要度（None）を
  収束の妨げになる側に倒すのは、ジャーナルへ入れる処理を通らない行（引き継いだ行・前のバージョンの行）を読むときである
- 重点（基準 3）は `.ndf/review.json` の `version: 1` の `focus` だけを読む。`version: 2`・`3` のプロファイルは
  読まず、形が違えば `ReviewDeclInvalid`（黙って無視しない）
- 同じ `version: 1` の `limits` は、費用と時間の上限（`rounds`・`seconds`・`usd`。どれも省ける）である（#2468）
- レビューの単位の指示（`NDF_UNIT_FILE`）があれば、単位の範囲と例外を相手役と修正担当の両方へ渡す（`unit_block`。#4369）
- 基準 5（プロジェクト MVV に反しないか）は、ベースブランチの MVV が承認済みのときだけ指示に載せる。MVV の本文と
  NDF の共通原則は、起動の始めに保存したスナップショットのパスで渡す（#4076）
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import facts, writing
from ndf.contracts.result import SEVERITIES
from ndf.domains.records.mvv_decide import State

if TYPE_CHECKING:
    from ndf.domains.records.mvv import Snapshot

TABLE = "data/review-criteria.json"
# 読む設定のバージョン
DECL_VERSION = 1
# 文書の基準を足す指示の相手
REVIEWER, FIXER = "reviewer", "fixer"
# 修正担当の会話を保存するかのキー（#4013）
FIXER_SESSIONS = "fixer_sessions"
# 必ず人の承認が要る操作の番号（`P1`・`C7`。MVV の項目の `Value 5`・`Vision` と見分ける）
_REDLINE = re.compile(r"[A-Z][0-9]+")


class ReviewDeclInvalid(ValueError):
    """`.ndf/review.json` の形が違うか、cross-review が読まないバージョンである。"""


class ProjectLimits(NamedTuple):
    """`.ndf/review.json` の `limits`（#2468）。ラウンド数・秒・費用（ドル）で、上限の無いものは None。"""

    rounds: Optional[int] = None
    seconds: Optional[float] = None
    usd: Optional[float] = None


@contracts.cached_reader
def _table() -> dict[str, Any]:
    return dict(contracts.load_json(TABLE))


def severity_of(value: Any) -> Optional[str]:
    """尺度にある重要度ならそのまま、ほか（無い・知らない値）は None。"""
    return value if isinstance(value, str) and value in facts.ids(SEVERITIES) else None


def blocking(severity: Optional[str]) -> bool:
    """収束の妨げになる重要度か。分からない重要度（None）は妨げになる側に倒す。"""
    return severity is None or bool(facts.value(SEVERITIES, severity, "blocking"))


def at_least_blocking(severity: Optional[str]) -> str:
    """収束の妨げになる重さならそのまま、ほか（分からない重要度を含む）は妨げになる重さのうち最も軽いもの（major）。"""
    return severity if severity is not None and blocking(severity) else _lightest_blocking()


def _rank(severity: Optional[str]) -> int:
    """重さの順位。分からない重要度は収束の妨げになる重さのうち最も軽いものと同じに数える。"""
    return int(facts.value(SEVERITIES, severity if severity is not None else _lightest_blocking(), "rank"))


def _edge(blocking: bool, pick: Callable[..., Mapping[str, Any]]) -> str:
    """収束の妨げになるか（`blocking`）で分けた重さのうち、`pick`（min か max）で順位の端にあるもの。"""
    return str(pick(facts.rows(SEVERITIES, blocking=blocking), key=lambda r: int(r["rank"]))["id"])


def _lightest_blocking() -> str:
    """収束の妨げになる重さのうち最も軽いもの（major）。"""
    return _edge(True, min)


def _heaviest_light() -> str:
    """収束の妨げにならない重さのうち最も重いもの（minor）。"""
    return _edge(False, max)


def _has_impact(impact: object) -> bool:
    """起きることの 1 文を持つか（空白を除いて空でない文字列。`journal._text` と同じ規則）。"""
    return isinstance(impact, str) and bool(impact.strip())


def effective_severity(severity: Optional[str], impact: object, *, redline: bool = False) -> str:
    """ジャーナルの行に書く重要度（#4014）。`severity` は `severity_of` を通した値。1 文の無い収束の妨げになる重さと
    重要度の無い指摘は、妨げにならない重さのうち最も重いもの（minor）へ下げ、1 文のある重要度の無い指摘は妨げになる
    重さのうち最も軽いもの（major）にする。妨げにならない重さは 1 文の有無によらずそのまま。必ず人の承認が要る操作に
    当たる指摘（`redline`）は、1 文が無くても下げない（#4076）。"""
    if severity is not None and not blocking(severity):
        return severity
    if not redline and not _has_impact(impact):
        return _heaviest_light()
    return severity if severity is not None else _lightest_blocking()


def downgraded(row: Mapping[str, Any]) -> bool:
    """ジャーナルへ入れるときに minor へ下げた行か（#4014）。`raised_severity` の無い行（引き継いだ行・前のバージョンの行）は偽。"""
    if "raised_severity" not in row:
        return False
    return blocking(severity_of(row["raised_severity"])) and row.get("severity") == _heaviest_light()


def heavier(known: Optional[str], raised: Optional[str]) -> Optional[str]:
    """同じ指摘が出し直されたときに残す重要度（重い方。同じ重さなら前の値）。"""
    return raised if _rank(raised) > _rank(known) else known


def heavier_raised(known: Optional[str], raised: Optional[str]) -> Optional[str]:
    """同じ指摘が出し直されたときに残す元の重要度（`raised_severity`）。収束の判定の `heavier` と違い、尺度にある値を
    無い値（None）より優先し、両方あれば重い方（同じ重さなら前の値）を残す。ジャーナルへ入れる順によらず同じ値になる（#4014）。"""
    if known is None or raised is None:
        return raised if known is None else known
    return heavier(known, raised)


def focus_of(raw: Any, where: str) -> tuple[str, ...]:
    """`.ndf/review.json` の中身から重点の並び。`version: 1` だけを読み、ほかのバージョンと形の違いは `ReviewDeclInvalid`。"""
    version = raw.get("version") if isinstance(raw, Mapping) else None
    if isinstance(version, bool) or version != DECL_VERSION:
        raise ReviewDeclInvalid(
            f"{where} の version が {version!r}。cross-review は version 1 の focus だけを読む"
            "（profile・profiles のプロファイルは読まない。version 1 の focus に書き直す）"
        )
    focus = raw.get("focus")
    if not isinstance(focus, list) or not all(isinstance(x, str) and x.strip() for x in focus):
        raise ReviewDeclInvalid(f'{where} の focus の形が違う（{{"version": 1, "focus": ["<重点>", ...]}} を書く）')
    return tuple(x.strip() for x in focus)


def fixer_sessions_of(raw: Mapping[str, Any], where: str) -> bool:
    """`.ndf/review.json` の中身（`focus_of` が version を確かめた後）から、修正担当の会話を保存するか（#4013）。
    キーが無ければ偽。真偽値でなければ `ReviewDeclInvalid`（どちらの側にも黙って倒さない）。"""
    if FIXER_SESSIONS not in raw:
        return False
    value = raw[FIXER_SESSIONS]
    if not isinstance(value, bool):
        raise ReviewDeclInvalid(f"{where} の {FIXER_SESSIONS} が {value!r}。true か false を書く")
    return value


def project_limits_of(raw: Mapping[str, Any], where: str) -> ProjectLimits:
    """`.ndf/review.json` の中身（`focus_of` が version を確かめた後）から、書かれた上限（#2468）。`limits` と各キーは省ける。
    `rounds` は 1 以上の整数値（JSON Schema の integer と同じく `4.0` も受け、`int` にして返す）、`seconds`・`usd` は正の
    有限の数だけを受け、知らないキーと形の違いは `ReviewDeclInvalid`。例外の文は結果 JSON に出るので、書かれた値と知らないキーの
    名前を載せない（誤って貼られた認証情報を出さない）。"""
    if "limits" not in raw:
        return ProjectLimits()
    value = raw["limits"]
    if not isinstance(value, Mapping) or not set(value) <= set(ProjectLimits._fields):
        keys = "・".join(ProjectLimits._fields)
        raise ReviewDeclInvalid(f"{where} の limits の形が違う。{keys} だけを持つオブジェクトを書く")
    for key, got in value.items():
        number = isinstance(got, (int, float)) and not isinstance(got, bool)
        if not number or not 0 < got <= sys.float_info.max or (key == "rounds" and got % 1):
            wanted = "1 以上の整数" if key == "rounds" else "正の数"
            raise ReviewDeclInvalid(f"{where} の limits.{key} の形か範囲が違う。{wanted}を書く")
    declared = ProjectLimits(**value)
    return declared if declared.rounds is None else declared._replace(rounds=int(declared.rounds))


def approved(mvv: Optional[Snapshot]) -> Optional[Snapshot]:
    """承認済みの MVV ならそのまま返す。MVV が無いか承認済みでなければ None。"""
    return mvv if mvv is not None and mvv.state == State.APPROVED else None


def mvv_item(value: object, items: Sequence[str]) -> Optional[str]:
    """相手役が指摘の `mvv` に書いた MVV の項目（例: `Value 5`）。前後の空白を除き、`items` に無ければ None。"""
    item = value.strip() if isinstance(value, str) else None
    return item if item in items else None


def redline(item: Optional[str]) -> bool:
    """MVV の項目（`mvv_item` の値）が、必ず人の承認が要る操作か。"""
    return item is not None and _REDLINE.fullmatch(item) is not None


def _mvv_lines(who: str, mvv: Optional[Snapshot]) -> list[str]:
    """MVV の指摘の書き方・扱い方の文（`who` は `reviewer` か `fixer`）。承認済みの MVV が無ければ空。"""
    t = _table()
    return [t["mvv"][who].format(number=t["mvv_number"])] if approved(mvv) else []


def mvv_shape(mvv: Optional[Snapshot]) -> str:
    """相手役が出す指摘の JSON に足すキー `mvv` の説明。承認済みの MVV が無ければ空文字列。"""
    t = _table()
    return str(t["mvv"]["shape"].format(number=t["mvv_number"])) if approved(mvv) else ""


def criteria_lines(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]:
    """基準の行（番号の順）。重点があるときだけ基準 3 を、承認済みの MVV があるときだけ基準 5 を足す。"""
    t = _table()
    items = dict(t["criteria"])
    if focus:
        items[t["focus_criterion"]] = f"{t['focus_label']}: {' / '.join(focus)}"
    if (held := approved(mvv)) is not None:
        items[t["mvv_number"]] = t["mvv_criterion"].format(version=held.version, path=held.path, principles=held.principles_path)
    return [f"{n}. {items[n]}" for n in sorted(items, key=int)]


def reviewer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]:
    """相手役への「指摘の基準」のセクション。"""
    t = _table()
    names = "・".join(facts.ids(SEVERITIES))
    impact = t["impact"]["reviewer"].format(
        blocking="・".join(facts.ids(SEVERITIES, blocking=True)),
        light_first=_heaviest_light(),
        light="・".join(facts.ids(SEVERITIES, blocking=False)),
    )
    return [
        "## 指摘の基準",
        "次のどれかに当たるものだけを指摘として書く。見つけたものはこのラウンドですべて書き、次のラウンドへ残さない。",
        *criteria_lines(focus, mvv),
        f"書かないもの: {'・'.join(t['not_written'])}",
        f"対応のアクションの無いもの（{'・'.join(t['no_action'])}）は指摘にしない。",
        f"重要度 severity は {names} から選ぶ。{impact}",
        *_mvv_lines("reviewer", mvv),
    ]


def fixer_impact(row: Mapping[str, Any]) -> str:
    """修正担当へ渡す指摘の行の末尾に添える起きることの 1 文（行が `impact` を持たなければ空。#4014）。"""
    impact = row.get("impact")
    return f"— {_table()['impact']['fixer_label']}: {impact.strip()}" if _has_impact(impact) else ""


def citation_text(error: Mapping[str, Any], version: int) -> str:
    """根拠の記述の誤り `error`（`mvv_decide.citation_errors` の 1 件）の指摘の文。`version` は承認済みのバージョン。行がずれても
    同じ文になるよう、行の番号を入れない。"""
    t = _table()["mvv"]
    reason = t["citation_reasons"][error["kind"]].format(version=version)
    return str(t["citation"].format(path=error["path"], cited=error["cited"], version=version, reason=reason))


def fixer_ci_only() -> str:
    """テストを CI に寄せる設定のリポジトリで、修正担当への指示に足す文（#4030）。"""
    return str(_table()["fixer_ci_only"])


def fixer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]:
    """修正担当への「指摘の基準」のセクション。"""
    t = _table()
    return [
        "## 指摘の基準",
        "指摘ごとに次の基準のどれかに当たるかを確かめ直す。当たれば直す（fixed）。当たらなければ対象のファイルを変えず、"
        "どの基準にも当たらない理由を添えて見送る（deferred）。",
        *criteria_lines(focus, mvv),
        f"当たらないもの: {'・'.join(t['not_written'])}",
        t["out_of_scope"] + t["defer"],
        *_mvv_lines("fixer", mvv),
    ]


class Previous(NamedTuple):
    """今回の相手役の全員が読んだ前のラウンド。`head` はその head、`diff` は `head` から今の head までの差分のファイル。"""

    head: str
    diff: Path


def unit_block(role: str, unit: str) -> list[str]:
    """レビューの単位の指示のファイルの中身 `unit` と、役 `role`（`REVIEWER`・`FIXER`）の扱いの文（#4369 の決定 8）。
    `unit` が空なら空。"""
    if not unit:
        return []
    text = _table()["unit"]
    return [text["heading"], unit.rstrip(), text[role]]


def document_block(role: str, documents: bool, previous: Optional[Previous] = None) -> list[str]:
    """文書を含む PR（`documents`）の指示に足す「文書の指摘の基準」のセクション（#4285 の決定 4〜6）。含まなければ空。

    `role` は `REVIEWER`（相手役）か `FIXER`（修正担当）。`previous` は今回の相手役の全員が読んだ
    前のラウンドで、比較元に使える前のラウンドが無ければ None。基準の本文を読めなければ `writing.CriteriaBroken`。"""
    if not documents:
        return []
    doc = _table()["document"]
    lines = ["## 文書の指摘の基準", "この PR は文書を変えているので、次の基準を足す。", f"{doc['number']}. {doc['text']}"]
    if role == REVIEWER:
        lines += doc["fields"]
        if previous is not None:
            lines.append(doc["since"].format(since=previous.head, diff=previous.diff))
    else:
        lines.append(doc["fixer"])
    return [*lines, writing.criteria().rstrip()]
