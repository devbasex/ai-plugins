"""ホスト判定と担当の決定（収束ループ共通層）。

**役割ごとに母集合が違う**ことがこの層の要点である。

| 母集合 | 定義 | 中身 |
| --- | --- | --- |
| 提案・レビュー | 全ランタイム − ホスト | 常に 3 者 |
| 適用 | 全ランタイム | 常に 4 者 |

参加する 4 者はいずれも NDF の配布先であるため、**適用から外す者はいない**。
ホストは提案・レビューから外れるが適用には入るため、2 つの母集合は重なるが
一致しない。輪番の式はホストによらず同じ形になる。
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

# 固定順。輪番の再現性を保つため並べ替えない。
ALL_RUNTIMES: tuple[str, ...] = ("claude", "codex", "agy", "kiro")

# ホストになりうるランタイム。4 者とも NDF の配布先であるため全員がなれる。
# **名前は `ALL_RUNTIMES` へ寄せない。**「ホストになれるか」と「参加できるか」は
# 別の問いで、配布先でない CLI が参加 CLI に加わると 2 つは再び分かれる。
HOST_RUNTIMES: tuple[str, ...] = ALL_RUNTIMES

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
