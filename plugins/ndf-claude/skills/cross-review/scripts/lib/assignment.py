"""ホスト判定と担当の決定（収束ループ共通層）。

**役割ごとに母集合が違う**ことがこの層の要点である。

| 母集合 | 定義 | 中身 |
| --- | --- | --- |
| 提案・レビュー | 全ランタイム − ホスト | 常に 3 者 |
| 適用 | 全ランタイム − gemini | 常に claude / codex / kiro |

gemini はどの配布先でもないためホストになれず、NDF Skill を持たないため適用にも
参加しない。この 2 つが噛み合うので、どのホストでも提案・レビューは 3 者、
適用候補も 3 者で揃い、輪番の式が全ホストで同じ形になる。
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

# 固定順。輪番の再現性を保つため並べ替えない。
ALL_RUNTIMES: tuple[str, ...] = ("claude", "codex", "gemini", "kiro")

# ホストになりうるランタイム（NDF の配布先）。gemini は配布先ではない。
HOST_RUNTIMES: tuple[str, ...] = ("claude", "codex", "kiro")

# 適用に参加できないランタイム。NDF Skill を配布しておらず、
# `refactoring` Skill の手順を踏ませる適用には向かない。
IMPL_EXCLUDED: tuple[str, ...] = ("gemini",)

# ホスト推定に使う環境変数。値の中身は見ず、**存在するかどうか**だけで判定する。
HOST_ENV_HINTS: tuple[tuple[str, str], ...] = (
    ("CLAUDE_PLUGIN_ROOT", "claude"),
    ("CLAUDECODE", "claude"),
    ("CODEX_PLUGIN_ROOT", "codex"),
    ("CODEX_HOME", "codex"),
    ("KIRO_PLUGIN_ROOT", "kiro"),
    ("KIRO_AGENT", "kiro"),
)


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
        "ホストを推定できませんでした。`--host claude|codex|kiro` で明示してください"
    )


def review_pool(host: str) -> list[str]:
    """提案・レビューの母集合（全ランタイム − ホスト）。常に 3 者になる。"""
    if host not in HOST_RUNTIMES:
        raise AssignmentError(f"ホストになれないランタイムです: {host}")
    return [r for r in ALL_RUNTIMES if r != host]


def impl_pool() -> list[str]:
    """適用の母集合（全ランタイム − gemini）。ホストによらず常に同じ。"""
    return [r for r in ALL_RUNTIMES if r not in IMPL_EXCLUDED]


def assign(round_no: int, host: str) -> tuple[str, list[str]]:
    """ラウンド番号から `(実装担当, レビュー担当 2 者)` を決める。

    輪番の単位は**ラウンド**である。1 ラウンドの適用を 1 者へ集約することで、
    レビュー担当を「実装担当以外」から機械的に決められる。

        実装担当   = 適用候補[ラウンド番号 % 3]
        候補       = 提案・レビュー − 実装担当
        レビュー担当 = 候補が 2 者ならそのまま
                     3 者なら 候補[(ラウンド番号 // 3) % 3] を除いた 2 者

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
