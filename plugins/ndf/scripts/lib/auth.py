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


def check_auth(
    runtimes: Iterable[str],
    *,
    info: Callable[[str], None],
    die: Callable[[str], None],
    env: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """参加する CLI の認証状態を確かめる。1 つでも欠けたら呼び出し側を中断させる。

    **出力と中断の手段は呼び出し側から受け取る。** 工程ごとに終了コードの意味が違う
    （`cross-refactoring` の中断は 4、`cross-review` は 1）ため、この層で決めない。

    確認コマンドは CLI の版で変わりうるので、`NDF_SKIP_AUTH_CHECK` で飛ばせるように
    しておく。飛ばしたことは必ず出力へ残す（黙って劣化させない）。
    """
    environ = os.environ if env is None else env
    if environ.get(SKIP_ENV):
        info(f"⚠ {SKIP_ENV} が設定されているため認証確認を飛ばしました")
        return {}

    results: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for runtime in runtimes:
        probe = AUTH_PROBES.get(runtime)
        if probe is None:
            continue
        try:
            r = subprocess.run(list(probe), capture_output=True, text=True,
                               timeout=AUTH_PROBE_TIMEOUT)
            merged = f"{r.stdout}\n{r.stderr}".lower()
            ok = r.returncode == 0 and not any(
                m in merged for m in UNAUTHENTICATED_MARKERS
            )
            detail = (r.stderr.strip() or r.stdout.strip())[:200]
        except FileNotFoundError:
            ok, detail = False, "コマンドが見つかりません"
        except subprocess.TimeoutExpired:
            ok, detail = False, f"{AUTH_PROBE_TIMEOUT} 秒で応答しませんでした"
        results[runtime] = {"command": " ".join(probe), "ok": ok, "detail": detail}
        info(f"{'✅' if ok else '❌'} {runtime}: {' '.join(probe)}")
        if not ok:
            failed.append(f"{runtime}（{detail}）")

    if failed:
        die(
            "認証されていない CLI があります: " + " / ".join(failed) + "。"
            "参加者が欠けたまま進むと、その者のレビューが無いまま収束します。"
            "各 CLI でログインしてから再実行してください"
        )
    return results
