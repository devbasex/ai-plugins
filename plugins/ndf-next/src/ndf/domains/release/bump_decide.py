"""CD の版上げの判定（純粋な関数。#3572 の決定 7・11）。

上げ幅は PR のラベル（`LEVEL_LABELS`）で宣言する。ラベルが無い PR は PATCH で、1 つの PR に 2 つ以上あれば `LevelError`。
範囲（前の版上げのコミットの起点..main の先端）に複数の PR があれば、最も大きい上げ幅を採る。起点は版上げのコミットの
メッセージの行 `ORIGIN_LINE` に残す。

`action` の判定（main の先端の状態から `bump` の動きを 1 つ決める）:

| 先端の版のタグ | 範囲の変化 | 開いた版上げの PR | 動き |
| --- | --- | --- | --- |
| 無い | — | — | `PUBLISH`（版を上げず、タグと GitHub Release を作る側へ渡す） |
| ある | 無い | — | `NOTHING`（`not_applicable`。I11） |
| ある | ある | 無い | `CREATE` |
| ある | ある | 同じ版 | `UPDATE`（main を取り込み、版の節を組み直す） |
| ある | ある | 違う版 | `REPLACE`（古い PR を閉じてから作る） |
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

from ndf.domains.release import version_decide

# 上げ幅の宣言のラベル → 上げ幅
LEVEL_LABELS = {"release:major": "major", "release:minor": "minor", "release:patch": "patch"}
# 宣言が無い PR の上げ幅（要求の起票者の推定 S8。宣言の付け忘れでマージを止めない）
DEFAULT_LEVEL = "patch"
# 版上げのコミットのメッセージの起点の行
ORIGIN_LINE = "起点:"
_SHA = re.compile(r"[0-9a-f]{40}")
# `action` の動き
PUBLISH, NOTHING, CREATE, UPDATE, REPLACE = "publish", "nothing", "create", "update", "replace"
# `bump` の結果の state（`bump_states` が止まらなかった state の組を返す）
MERGED, REUSED, PUBLISHED, NOT_APPLICABLE, STOPPED = "merged", "reused", "published", "not_applicable", "stopped"


class LevelError(ValueError):
    """1 つの PR に上げ幅のラベルが 2 つ以上ある。"""


class OpenBump(NamedTuple):
    """main 宛ての開いた版上げの PR。"""

    number: int
    version: str
    head_ref: str
    head_sha: str
    # head がこのリポジトリのブランチか（フォークの PR なら偽。置き換えで閉じて消すのは真のときだけ）
    same_repo: bool = True


def bump_states() -> tuple[str, ...]:
    """`bump` の結果の state（版上げの PR をマージした・main を含まず次の push へ回した・タグを作る側へ渡した・何もしない）。"""
    return (MERGED, REUSED, PUBLISHED, NOT_APPLICABLE)


def level_of(labels: Sequence[str]) -> str:
    """1 本の PR のラベルから上げ幅（該当が 0 なら `DEFAULT_LEVEL`、2 つ以上なら `LevelError`）。"""
    hits = [x for x in labels if x in LEVEL_LABELS]
    if len(hits) > 1:
        raise LevelError(f"上げ幅のラベルが 2 つ以上ある: {', '.join(hits)}（{', '.join(LEVEL_LABELS)} のどれか 1 つにする）")
    return LEVEL_LABELS[hits[0]] if hits else DEFAULT_LEVEL


def widest(levels: Sequence[str]) -> str:
    """上げ幅のうち最も大きいもの（空なら `DEFAULT_LEVEL`）。"""
    order = version_decide.LEVELS
    known = [x for x in levels if x in order]
    return min(known, key=order.index) if known else DEFAULT_LEVEL


def planned(current: str, level: str) -> str:
    """今の版 `current`（接尾辞の無い版。あれば `VersionError`）を上げ幅 `level` で上げた次の版。"""
    if version_decide.base_of(current) != current:
        raise version_decide.VersionError(f"版上げの元の版は接尾辞の無い版（渡された値: {current!r}）")
    return version_decide.production_next(current, level)


def origin_of(message: str) -> str | None:
    """版上げのコミットのメッセージの起点の SHA（行が無いか 40 桁でなければ None）。"""
    for line in message.splitlines():
        if line.startswith(ORIGIN_LINE):
            value = line[len(ORIGIN_LINE) :].strip()
            return value if _SHA.fullmatch(value) else None
    return None


def message_of(plugin: str, version: str, origin: str) -> str:
    """版上げのコミットのメッセージ（題と起点の行）。"""
    return f"Release: {plugin} v{version}\n\n{ORIGIN_LINE} {origin}\n"


def covered(path: str, allowed: frozenset[str]) -> bool:
    """`path` が `allowed` のどれかに当たるか（同じパスか、`allowed` の値をディレクトリの前置きとしてその配下にあるか。
    手順の `writes` は書くパスの前置きの申告で、`docs/x/` のようなディレクトリの指定がその配下の生成ファイルに当たる）。"""
    return any(path == a or path.startswith(a.rstrip("/") + "/") for a in allowed)


def only_bump(paths: Sequence[str], allowed: frozenset[str]) -> list[str]:
    """版上げの PR の差分のうち、版のファイルと CHANGELOG（`allowed`）の外のパスを入力の順に返す（空なら版上げだけ。I7）。"""
    return [p for p in paths if not covered(p, allowed)]


def diff_lines(diff: str) -> tuple[list[str], list[str]]:
    """`git diff -U0` の出力の消した行と足した行（ファイルの見出しの行を除く。入力の順）。"""
    removed: list[str] = []
    added: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            removed.append(line[1:])
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("diff --git"):
            in_hunk = False
    return removed, added


def _swap(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    """`text` の前の版を後の版へ一度に置き換える（置き換えた後の版を再び置き換えない）。"""
    mapping = dict(pairs)
    if not mapping:
        return text
    pattern = re.compile("|".join(re.escape(old) for old in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda m: mapping[m.group(0)], text)


def version_lines_only(removed: Sequence[str], added: Sequence[str], pairs: Sequence[tuple[str, str]]) -> bool:
    """消した行の版（`pairs` の前の版）を後の版へ置き換えると足した行になる変更だけか（版の行だけの書き換え。I7）。"""
    return len(removed) == len(added) and all(_swap(r, pairs) == a for r, a in zip(removed, added))


def json_version_only(before: object, after: object, pairs: Sequence[tuple[str, str]]) -> bool:
    """JSON の値 `before` と `after` の形が同じで、違う文字列はどれも `before` の版（`pairs`）を後の版へ置き換えたものか
    （変わっていない文字列はそのまま許す。同じ版のほかのプラグインの説明を置き換えずに残しても通す。書式の違いは問わない。I7）。"""
    if isinstance(before, str) and isinstance(after, str):
        return before == after or _swap(before, pairs) == after
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(json_version_only(b, a, pairs) for b, a in zip(before, after))
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(json_version_only(before[k], after[k], pairs) for k in before)
    return type(before) is type(after) and before == after


def action(tip_tagged: bool, changed: bool, opened: OpenBump | None, version: str) -> str:
    """main の先端の状態から `bump` の動きを決める（モジュールの説明の表）。"""
    if not tip_tagged:
        return PUBLISH
    if not changed:
        return NOTHING
    if opened is None:
        return CREATE
    return UPDATE if opened.version == version else REPLACE


def late(included: Sequence[int], listed: Sequence[int]) -> list[int]:
    """タグを打つコミットに含まれ、版の節に載らない PR の番号（小さい順）。"""
    return sorted(set(included) - set(listed))
