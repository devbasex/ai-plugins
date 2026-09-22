"""ホスト判定と担当の決定（収束ループ共通層）。

**役割ごとに母集合が違う**ことがこの層の要点である。

| 母集合 | 定義 | 中身 |
| --- | --- | --- |
| 提案・レビュー | 全ランタイム − ホスト | 常に 3 者 |
| 適用 | 全ランタイム | 常に 4 者 |

**担当の選び方は、適用の役があるかどうかで分かれる。** `assign()` は実装担当を先に決めて
から残りを絞り、`review_assign()` は母集合から直接 2 者を選ぶ。どちらも返すレビュー担当は
2 者である。

参加する 4 者はいずれも NDF の配布先であるため、**適用から外す者はいない**。
ホストは提案・レビューから外れるが適用には入るため、2 つの母集合は重なるが
一致しない。輪番の式はホストによらず同じ形になる。

## 使える者の解決と席の埋め方（#727）

参加者は「母集合の既定 ∪ 足す者 − 外す者」で決め（`resolve_participants`）、確認を
通った者だけを使える者（`available`）として記録する。cross-refactoring の母集合の
既定は `refactor_pool(host)`（`DEFAULT_REFACTOR_RUNTIMES` とホスト）、cross-review は
`review_pool(host)` のまま。担当の単位は席の名前（`SEAT_PATTERN`。`claude-2` のように
同じランタイムの 2 つ目を表す）で、cross-review の 2 席は `review_seats` が、
cross-refactoring の適用担当は `impl_assign` が決める。上の表と `impl_pool` /
`review_assign` / `assign` は、母集合が 1 つになる次の Pull Request（P7）まで残す。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

# 固定順。輪番の再現性を保つため並べ替えない。
ALL_RUNTIMES: tuple[str, ...] = ("claude", "codex", "agy", "kiro")

# ホストになりうるランタイム。4 者とも NDF の配布先であるため全員がなれる。
# **名前は `ALL_RUNTIMES` へ寄せない。**「ホストになれるか」と「参加できるか」は
# 別の問いで、配布先でない CLI が参加 CLI に加わると 2 つは再び分かれる。
HOST_RUNTIMES: tuple[str, ...] = ALL_RUNTIMES

# cross-refactoring の既定の参加者の表（ホストを除いた部分。設計の決定 4）。ホストは
# `refactor_pool(host)` が足す。表に無い者（agy）は `--include` で足す（#727）。
DEFAULT_REFACTOR_RUNTIMES: tuple[str, ...] = ("codex", "kiro")

# 席の名前の形: `^(claude|codex|agy|kiro)(-[2-9])?$`。ランタイム名そのままが 1 つ目の席、
# ハイフンと 2〜9 の接尾辞が同じランタイムの 2 つ目以降（設計の決定 10）。ランタイム名に
# ハイフンを含むものが無いため、シェル側の切り出し（`${SEAT%%-*}`）と同じ規則になる。
SEAT_PATTERN = re.compile(rf"^({'|'.join(ALL_RUNTIMES)})(-[2-9])?$")

# ホスト推定に使う環境変数。値の中身は見ず、**存在するかどうか**だけで判定する。
HOST_ENV_HINTS: tuple[tuple[str, str], ...] = (
    ("CLAUDE_PLUGIN_ROOT", "claude"),
    ("CLAUDECODE", "claude"),
    ("CODEX_PLUGIN_ROOT", "codex"),
    ("CODEX_HOME", "codex"),
    ("KIRO_PLUGIN_ROOT", "kiro"),
    ("KIRO_AGENT", "kiro"),
)
# agy の手掛かりは置かない。agy が子プロセスへ環境変数を渡すかを確かめていないため、
# 推定へ入れると母集合を狂わせうる。agy がホストのときは `--host agy` を明示する。


class AssignmentError(ValueError):
    """担当の決定に失敗した。呼び出し側は初期化ごと失敗させる。"""


def detect_host(
    explicit: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[str, str]:
    """ホストを確定し、`(ホスト名, 判定根拠)` を返す。

    判定根拠は `explicit`（`--host` の明示指定）か `env`（環境変数からの推定）。
    誤検出すると**提案・レビューの母集合が狂う**（ホストが提案側に混ざる、
    参加すべき者が外れる）ため、呼び出し側は結果を必ず出力と状態ファイルへ残す。

    推定できないときは例外を上げる。既定値を勝手に置くと、間違ったまま一周して
    しまい、成果物を見るまで気付けない。
    """
    if explicit:
        if explicit not in HOST_RUNTIMES:
            raise AssignmentError(
                f"--host には {'/'.join(HOST_RUNTIMES)} のいずれかを指定してください: {explicit}"
            )
        return explicit, "explicit"

    environ = os.environ if env is None else env
    for key, runtime in HOST_ENV_HINTS:
        if environ.get(key):
            return runtime, "env"
    raise AssignmentError(
        "ホストを推定できませんでした。"
        f"`--host {'|'.join(HOST_RUNTIMES)}` で明示してください"
    )


def review_pool(host: str) -> list[str]:
    """提案・レビューの母集合（全ランタイム − ホスト）。常に 3 者になる。"""
    if host not in HOST_RUNTIMES:
        raise AssignmentError(f"ホストになれないランタイムです: {host}")
    return [r for r in ALL_RUNTIMES if r != host]


def impl_pool() -> list[str]:
    """適用の母集合（全ランタイム）。ホストによらず常に同じ。

    **関数として残す。** 呼び出し側が提案・レビューの母集合と適用の母集合を
    別々に確定する構造を保つためである。両者は依然として一致しない
    （適用はホストを含み、提案・レビューは含まない）。
    """
    return list(ALL_RUNTIMES)


def review_assign(round_no: int, host: str) -> list[str]:
    """ラウンド番号から**レビュー担当 2 者**を決める。適用の役を持たない工程が使う。

    母集合は `review_pool(host)` の 3 者で、外す 1 者をラウンドごとに回す。

        レビュー担当 = 母集合 − 母集合[(ラウンド番号 - 1) % 3]

    `assign()` と分けているのは、**適用の役があるかどうかで選び方が変わる**ためである。
    `assign()` は先に実装担当を決めてから残りを絞るが、この工程には適用が無く、母集合から
    直接 2 者を選ぶ。3 者すべてを毎ラウンド起動しないのは、起動回数が 1.5 倍になるためで、
    ラウンドを重ねれば 3 者とも差分を見る。
    """
    if round_no < 1:
        raise AssignmentError(f"ラウンド番号は 1 以上です: {round_no}")
    pool = review_pool(host)
    dropped = (round_no - 1) % len(pool)
    return [r for i, r in enumerate(pool) if i != dropped]


def assign(round_no: int, host: str) -> tuple[str, list[str]]:
    """ラウンド番号から `(実装担当, レビュー担当 2 者)` を決める。

    輪番の単位は**ラウンド**である。1 ラウンドの適用を 1 者へ集約することで、
    レビュー担当を「実装担当以外」から機械的に決められる。

        実装担当   = 適用候補[ラウンド番号 % 4]
        候補       = 提案・レビュー − 実装担当
        レビュー担当 = 候補が 2 者ならそのまま
                     3 者なら 候補[(ラウンド番号 // 4) % 3] を除いた 2 者

    実装担当がホストと同じランタイムのとき、その者は提案・レビューの母集合に
    含まれないため候補が 3 者残る。**レビュー担当は常に 2 者**とし（起動回数を
    抑える方針と揃える）、余る 1 者はラウンドを跨いで順に外して負荷を均す。
    """
    if round_no < 1:
        raise AssignmentError(f"ラウンド番号は 1 以上です: {round_no}")
    pool = impl_pool()
    impl = pool[round_no % len(pool)]
    candidates = [r for r in review_pool(host) if r != impl]
    if len(candidates) > 2:
        dropped = (round_no // len(pool)) % len(candidates)
        candidates = [r for i, r in enumerate(candidates) if i != dropped]
    return impl, candidates


def _in_fixed_order(names: Iterable[str]) -> list[str]:
    """`ALL_RUNTIMES` の順に並べ直す（重複は 1 つにする）。"""
    wanted = set(names)
    return [r for r in ALL_RUNTIMES if r in wanted]


def refactor_pool(host: str) -> list[str]:
    """cross-refactoring の母集合の既定。`DEFAULT_REFACTOR_RUNTIMES` とホストの和集合。

    ホストが変わっても一覧を書き直さずに済むように、既定は「ホストを除いた部分」
    だけを持ち、ホストをここで足す（設計の決定 4）。並びは `ALL_RUNTIMES` の順。
    """
    if host not in HOST_RUNTIMES:
        raise AssignmentError(f"ホストになれないランタイムです: {host}")
    return _in_fixed_order((*DEFAULT_REFACTOR_RUNTIMES, host))


@dataclass
class Participants:
    """使える者の解決の結果。状態ファイルの `participants` のうち `fallback` を除く 7 項目。

    `fallback`（席の埋め合わせに使える者）は cross-review だけが持つため、呼び出し側が
    `to_state()` の辞書へ足す。
    """
    pool: list[str]
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    available: list[str] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)
    probe_skipped: bool = False
    require_all: bool = False

    def to_state(self) -> dict[str, Any]:
        return {
            "pool": list(self.pool),
            "included": list(self.included),
            "excluded": list(self.excluded),
            "available": list(self.available),
            "unavailable": dict(self.unavailable),
            "probe_skipped": self.probe_skipped,
            "require_all": self.require_all,
        }


# 止めない確認の形。`auth.probe_auth` を `functools.partial(auth.probe_auth, info=info)`
# のように包んで渡す。返り値は `(名前 → {"command", "ok", "detail"}, 飛ばしたか)`。
Probe = Callable[[list[str]], tuple[dict[str, dict[str, Any]], bool]]


def _validate_and_build_participants(
    pool: list[str], include: list[str], exclude: list[str],
) -> list[str]:
    """`include` / `exclude` の整合性を検査し、参加者列（`pool ∪ include − exclude`）を返す。

    順序 1〜2（`resolve_participants` の docstring）を担う。名前が `ALL_RUNTIMES` に
    あること・足す者と外す者が重ならないこと・外す者が母集合に含まれることを確かめる。
    """
    for name in (*include, *exclude):
        if name not in ALL_RUNTIMES:
            raise AssignmentError(
                f"参加できないランタイムです: {name}（{'/'.join(ALL_RUNTIMES)} のいずれか）"
            )
    overlap = set(include) & set(exclude)
    if overlap:
        raise AssignmentError(
            f"足す者と外す者に同じ名前があります: {', '.join(_in_fixed_order(overlap))}"
        )
    base = set(pool) | set(include)
    outside = [n for n in exclude if n not in base]
    if outside:
        raise AssignmentError(
            f"母集合に無い者は外せません: {', '.join(_in_fixed_order(outside))}"
            f"（母集合: {', '.join(_in_fixed_order(base))}）"
        )
    return _in_fixed_order(base - set(exclude))


def _apply_only(participants: list[str], only: str | None, exclude: list[str]) -> list[str]:
    """`only` を検査して適用し、参加者列を返す（順序 3）。

    `only` が `None` なら参加者列をそのまま返す。指定があれば `exclude` と矛盾せず、
    参加者に含まれることを確かめ、参加者をその 1 者にする。
    """
    if only is None:
        return participants
    if only in exclude:
        raise AssignmentError(f"--only と --exclude が矛盾しています: {only}")
    if only not in participants:
        raise AssignmentError(
            f"--only は参加者のいずれかを指定してください: {only}"
            f"（参加者: {', '.join(participants)}）"
        )
    return [only]


def _classify_probe(
    participants: list[str], probe: Probe,
) -> tuple[list[str], dict[str, str], bool]:
    """`probe` の戻り値から `(available, unavailable, probe_skipped)` を作る（順序 4）。

    飛ばされたら全員を通ったものとし、そうでなければ通らなかった者と理由を集める。
    """
    results, skipped = probe(list(participants))
    if skipped:
        return list(participants), {}, True
    unavailable = {
        n: str(results.get(n, {}).get("detail", ""))
        for n in participants
        if not results.get(n, {}).get("ok", False)
    }
    available = [n for n in participants if n not in unavailable]
    return available, unavailable, skipped


def resolve_participants(
    pool: Iterable[str],
    *,
    host: str,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    only: Optional[str] = None,
    probe: Probe,
    require_all: bool = False,
) -> Participants:
    """母集合の既定・足す者・外す者・1 者指定から使える者を決める（設計の決定 2〜4）。

    順序:

    1. `include` / `exclude` の各名前が `ALL_RUNTIMES` にあり、重ならないことを確かめる。
       `exclude` の名前が「`pool` ∪ `include`」に無ければ弾く（cross-review でホストを
       外す指定はここに当たる）
    2. 参加者 = `pool` ∪ `include` − `exclude`（`ALL_RUNTIMES` の順）
    3. `only` があれば、参加者に含まれ `exclude` に無いことを確かめ、参加者をその 1 者にする
    4. `probe(参加者)` で確かめる。飛ばされたら全員を通ったものとし `probe_skipped` を真にする
    5. `require_all` が真で通らない者がいれば `AssignmentError`（欠けた者と理由を並べる）
    6. 通った者を `available`、通らなかった者と理由を `unavailable` として返す

    名前の綴りの検査（argparse の型）はこの前段で済んでいる前提だが、ここでも
    `ALL_RUNTIMES` に無い名前は弾く。
    """
    pool = list(pool)
    include = list(include)
    exclude = list(exclude)

    participants = _validate_and_build_participants(pool, include, exclude)
    participants = _apply_only(participants, only, exclude)
    available, unavailable, skipped = _classify_probe(participants, probe)

    if require_all and unavailable:
        failed = " / ".join(f"{n}（{d}）" for n, d in unavailable.items())
        raise AssignmentError(
            "認証されていない CLI があります: " + failed + "。"
            "参加者が欠けたまま進むと、その者のレビューが無いまま収束します。"
            "各 CLI でログインしてから再実行してください"
        )

    return Participants(
        pool=pool,
        included=_in_fixed_order(include),
        excluded=_in_fixed_order(exclude),
        available=available,
        unavailable=unavailable,
        probe_skipped=skipped,
        require_all=require_all,
    )


def seat_runtime(seat: str) -> str:
    """席の名前からランタイム名を引く。形は `SEAT_PATTERN`（`kiro` / `kiro-2`）。

    形に合わなければ `AssignmentError`。結果の受け口・起動スクリプト・監視が、担当の
    引数の検査にこの関数を使う。
    """
    m = SEAT_PATTERN.match(seat)
    if m is None:
        raise AssignmentError(
            f"席の名前の形が違います: {seat}"
            f"（{'/'.join(ALL_RUNTIMES)} か、その名前に -2〜-9 を付けた形）"
        )
    return m.group(1)


def review_seats(round_no: int, available: list[str], fallback: list[str]) -> list[str]:
    """cross-review のラウンドの 2 席を決める（設計の決定 9・20。規則の正本はこの表）。

    | 使える者の数 n | 席 |
    | ---: | --- |
    | 3 以上 | `available[round_no % n]` と `available[(round_no + 1) % n]` を `available` の順に並べた 2 席 |
    | 2 | その 2 者 |
    | 1 | その 1 者と、`fallback` のうち `available` に含まれない先頭の者。無ければ `<その 1 者>-2` |
    | 0 | `fallback[0]` と `<fallback[0]>-2`。`fallback` が空なら `AssignmentError` |

    `available` の並びは `ALL_RUNTIMES` の順（`resolve_participants` が保つ）。n = 3 の値は
    変更前の `review_assign` と一致する。埋め合わせの候補は使える者に含まれない者だけを
    使い、含まれる者は飛ばす（同じ席の名前を 2 つ返さないため）。`only` の処理は呼び出し側が
    先に行う（1 者指定は埋め合わせをしない）。
    """
    if round_no < 1:
        raise AssignmentError(f"ラウンド番号は 1 以上です: {round_no}")
    n = len(available)
    if n >= 3:
        picked = {available[round_no % n], available[(round_no + 1) % n]}
        return [r for r in available if r in picked]
    if n == 2:
        return list(available)
    if n == 1:
        first = available[0]
        extra = next((f for f in fallback if f not in available), None)
        return [first, extra if extra is not None else f"{first}-2"]
    if not fallback:
        raise AssignmentError("使える者も席の埋め合わせに使える者もいません")
    return [fallback[0], f"{fallback[0]}-2"]


def impl_assign(round_no: int, participants: list[str]) -> str:
    """cross-refactoring の適用担当 1 者を決める: `participants[round_no % len]`。

    式は変更前の `assign()` と同じで、除数だけを参加者の数にする（設計の決定 7）。
    ラウンド 1 が `participants[1]` から始まるため、ホスト claude の既定
    （claude / codex / kiro）でもホストが最初に適用する形にならない。
    """
    if round_no < 1:
        raise AssignmentError(f"ラウンド番号は 1 以上です: {round_no}")
    if not participants:
        raise AssignmentError("適用担当を選べる参加者がいません")
    return participants[round_no % len(participants)]
