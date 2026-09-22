"""参加する CLI の認証状態の確認（収束ループ共通層）。

**存在確認だけでは足りない。** 未認証の CLI は起動から 15 秒で終わり、結果ファイルを
残さないまま担当から脱落するが、初期化は成功として扱われる。参加者が 1 人欠けた構成の
まま最後まで進むことになる。

`cross-refactoring` と `cross-review` の両方が同じ確認を行う。片方だけに置くと、母集合を
広げたときにもう片方が未認証の CLI を担当へ入れる。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Iterable, Optional

ProbeResult = dict[str, dict[str, Any]]

# 認証状態の確認コマンド。CLI ごとに、認証を通ったときだけ成功する最も短い操作を選ぶ。
AUTH_PROBES: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "auth", "status"),
    "codex": ("codex", "login", "status"),
    # agy は認証を通ったときだけモデルの一覧を返す。プロンプトを投げる形より
    # 短く終わり、モデルの呼び出しを 1 回消費しない。
    "agy": ("agy", "models"),
    "kiro": ("kiro-cli", "whoami"),
}
AUTH_PROBE_TIMEOUT = 120

# **終了コード 0 でも未認証を示すことがある。** kiro は成否を終了コードで表さない。
UNAUTHENTICATED_MARKERS = (
    "not logged in", "not authenticated", "authentication failed",
    "login required", "unauthorized", "please log in",
)

SKIP_ENV = "NDF_SKIP_AUTH_CHECK"


def _skipped(env: Optional[dict[str, str]], info: Callable[[str], None]) -> bool:
    """`NDF_SKIP_AUTH_CHECK` が立っているか。立っていれば飛ばしたことを出力へ残す。"""
    environ = os.environ if env is None else env
    if not environ.get(SKIP_ENV):
        return False
    info(f"⚠ {SKIP_ENV} が設定されているため認証確認を飛ばしました")
    return True


def _run_probe(probe: tuple[str, ...]) -> tuple[bool, str]:
    """確認コマンドを 1 つ走らせ、`(通ったか, 理由)` を返す。例外は上げない。

    理由は stderr か stdout の先頭 200 文字。終了コード 0 でも未認証の文言を含めば
    通らなかったものとする（kiro は成否を終了コードで表さない）。
    """
    try:
        r = subprocess.run(list(probe), capture_output=True, text=True,
                           timeout=AUTH_PROBE_TIMEOUT)
    except FileNotFoundError:
        return False, "コマンドが見つかりません"
    except subprocess.TimeoutExpired:
        return False, f"{AUTH_PROBE_TIMEOUT} 秒で応答しませんでした"
    merged = f"{r.stdout}\n{r.stderr}".lower()
    ok = r.returncode == 0 and not any(m in merged for m in UNAUTHENTICATED_MARKERS)
    return ok, (r.stderr.strip() or r.stdout.strip())[:200]


def _probe_all(runtimes: Iterable[str], info: Callable[[str], None]) -> ProbeResult:
    """`AUTH_PROBES` にある名前だけを順に確かめ、名前 → 結果を返す。1 者 1 行を出力する。"""
    results: ProbeResult = {}
    for runtime in runtimes:
        probe = AUTH_PROBES.get(runtime)
        if probe is None:
            continue
        ok, detail = _run_probe(probe)
        results[runtime] = {"command": " ".join(probe), "ok": ok, "detail": detail}
        info(f"{'✅' if ok else '❌'} {runtime}: {' '.join(probe)}")
    return results


def probe_auth(
    runtimes: Iterable[str],
    *,
    info: Callable[[str], None],
    env: Optional[dict[str, str]] = None,
) -> tuple[ProbeResult, bool]:
    """参加する CLI の認証状態を確かめ、結果だけを返す（止めない確認。#727）。

    返り値は `(結果, 飛ばしたか)`。結果は名前 → `{"command", "ok", "detail"}`。
    **例外を上げず、呼び出し側も中断させない。** 通らなかった者を外して続けるか、
    全員を要して止めるかは、使える者の解決（`assignment.resolve_participants`）が
    決める。確認コマンド・未認証の文言・時間切れの秒数・飛ばす環境変数は
    この層の定数（`AUTH_PROBES` ほか）が持つ。

    `NDF_SKIP_AUTH_CHECK` が立てば確認コマンドを 1 回も呼ばず `({}, True)` を返す。
    飛ばしたことは出力へ残す（黙って劣化させない）。
    """
    if _skipped(env, info):
        return {}, True
    return _probe_all(runtimes, info), False
