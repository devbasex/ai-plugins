"""選んだモデルと effort で `claude -p` を起動する部品（#2944）。

値を選ぶ処理（`ndf.adapters.model_choice`）は、この層より後の子の部品なので読まない。一番上のエントリポイントが
`set_launch_chooser` で差し込み（`claude.set_launch_recorder` と同じ形）、差し込まれていなければ値を選ばずに起動する
（エントリポイントを通らない呼び出しとテスト）。効果指標は差し込まれた使用量ログの書き込み処理の `record_outcome` で書く。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ndf.adapters import claude
from ndf.contracts import internal

log = logging.getLogger(__name__)


class LaunchChooser(Protocol):
    """起動の値を選ぶ処理（`ndf.adapters.model_choice` が満たす）。値（Choice）は `model`・`effort`・`assign_id`・
    `cli_args()`・`env_changes()`・`row_fields()` を持つ。"""

    def launch_choice(self, layer: str, name: str, runtime: str = "claude", *, cwd: Path, **request: Any) -> Any: ...

    def precheck(self, runtime: str = "claude", *, cwd: Path, **given: Any) -> None: ...

    def base_fallback(self, choice: Any, reason: str) -> Optional[Any]: ...

    def observed(self, choice: Any, model: str) -> None: ...


# 差し込まれた値を選ぶ処理。None なら値を選ばない
launch_chooser: Optional[LaunchChooser] = None


def set_launch_chooser(chooser: Optional[LaunchChooser]) -> None:
    """値を選ぶ処理を差し込む（一番上のエントリポイントが起動時に 1 回呼ぶ）。"""
    global launch_chooser
    launch_chooser = chooser


def pick_values(layer: str, name: str, *, cwd: Path, **request: Any) -> Optional[Any]:
    """差し込まれた処理で値を選ぶ。差し込みが無ければ None。値や設定の誤りは処理の `InternalError`（precondition）をそのまま送る。"""
    return None if launch_chooser is None else launch_chooser.launch_choice(layer, name, cwd=cwd, **request)


def precheck_values(*, cwd: Path, **given: Any) -> None:
    """差し込まれた処理で、起動の前に止まる値の誤りだけを確かめる（記録を読み書きしない）。差し込みが無ければ何もしない。"""
    if launch_chooser is not None:
        launch_chooser.precheck(cwd=cwd, **given)


def report_outcome(assign_id: str, quality: Optional[float], detail: dict[str, Any]) -> None:
    """差し込まれた処理の `record_outcome` で効果指標を書く。受け口が無い・書けなければ警告を出して続ける（単位は数えない）。"""
    recorder = claude.launch_recorder
    if recorder is None or not hasattr(recorder, "record_outcome"):
        log.warning("効果指標の受け口が無いので、割り付け %s の指標を書かない", assign_id)
        return
    try:
        recorder.record_outcome(assign_id, quality, detail)
    except Exception as exc:  # noqa: BLE001 外部コマンド: 効果指標を書けなくても工程の結果を変えない（#2944 の詳細設計の異常系）
        log.warning("割り付け %s の効果指標を書けない: %s", assign_id, exc)


def _monotonic() -> float:
    """期限を測る単調時計の秒。テストが差し替える。"""
    return time.monotonic()


def deadline_after(timeout: float) -> float:
    """今から `timeout` 秒後の期限（単調時計）。"""
    return _monotonic() + timeout


def left_until(deadline: float) -> float:
    """期限までの残りの秒（過ぎていれば 0 以下）。"""
    return deadline - _monotonic()


@dataclass
class ChoiceRun:
    """選んだ値で `claude -p` を起動するリクエスト 1 件。`choice` は選んだ値（差し込みが無ければ None で、値を付けない）。

    `request_id` はリクエストごとの一意な値、`request_try` はそのリクエストの中の何番目の起動か（最初が 1）。`charge_to` は費用を
    手戻りとして負う割り付け ID。`switched` は基準の組へ切り替えたか（リクエストの中で 1 回だけ）。
    """

    choice: Optional[Any]
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_try: int = 1
    charge_to: Optional[str] = None
    switched: bool = False
    _seen: bool = False

    @property
    def model(self) -> Optional[str]:
        return None if self.choice is None else self.choice.model

    @property
    def effort(self) -> Optional[str]:
        return None if self.choice is None else self.choice.effort

    @property
    def assign_id(self) -> Optional[str]:
        return None if self.choice is None else self.choice.assign_id

    def cli_args(self) -> list[str]:
        """選んだ値の引数（`--model`・`--effort`）。値を選ばなかった起動は足さない。"""
        return [] if self.choice is None else list(self.choice.cli_args())

    def env_changes(self) -> dict[str, Optional[str]]:
        """子の環境の変更（None の名前は外す）。"""
        return {} if self.choice is None else self.choice.env_changes()

    def meta(self) -> dict[str, Any]:
        """使用量ログの行に足す値の欄（値の無い欄は省く）。"""
        values = {} if self.choice is None else self.choice.row_fields()
        fields = {**values, **dict(request_id=self.request_id, request_try=self.request_try, charge_to=self.charge_to)}
        return {k: v for k, v in fields.items() if v is not None}

    def seen(self, reply: claude.ClaudeReply) -> None:
        """実際のモデルを返した最初の起動で、割り付けの単位の `observed` の行を足す（足すかは差し込まれた処理が決める）。"""
        found = claude.main_model(reply.model_usage) if not self._seen and reply.model_usage is not None else None
        if found is not None and launch_chooser is not None and self.choice is not None:
            self._seen = True
            launch_chooser.observed(self.choice, found[0])

    def fall_back(self, reply: claude.ClaudeReply, ready: Optional[Callable[[], bool]] = None) -> bool:
        """`reply` がモデルを理由にした拒否で、基準の組へ切り替えられたら真。値を替え、`request_try` を 1 増やす。2 回目は切り替えない。

        `ready` は切り替える前に呼ぶ（期限と材料の確かめ）。偽を返すか例外を投げれば切り替えず、記録も書かない。その後の
        `base_fallback` が基準の組の値を返し、切り替えたことを持つ `launch_error` の行を書く。行は基準の組を起動する前に書かれる
        （基準の組の値を受け取るのと記録が同じ呼び出しのため）。
        """
        if reply.failure is not claude.Failure.MODEL_REFUSED or self.switched or launch_chooser is None or self.choice is None:
            return False
        if not self.choice.switchable:  # 切り替える対象でない拒否は、期限も材料も確かめず、拒否のまま返す
            return False
        if ready is not None and not ready():
            return False
        back = launch_chooser.base_fallback(self.choice, str(claude.Failure.MODEL_REFUSED))  # CLI の出力の本文は記録に残さない
        if back is None:
            return False
        self.choice, self.request_try, self.switched = back, self.request_try + 1, True
        return True

    def fall_back_until(
        self, reply: claude.ClaudeReply, deadline: float, verify: Optional[Callable[[], Optional[str]]] = None
    ) -> Optional[float]:
        """`fall_back` を期限 `deadline`（`deadline_after`）の中で行い、基準の組で起動し直せる残りの秒を返す。

        切り替えの記録の前に、期限の残り・起動の承認の対象（`verify`。`launch_settings.Grant.verifier`）・もう一度期限の残りの順に
        確かめる。残りが無ければ切り替えず記録も書かずに 0 を返し、承認の対象が変わったか承認が取り消されていれば、記録を書かずに
        `InternalError`（precondition）を送る。切り替えたら、記録を書いた後（錠の待ちを含む）に測り直した残りの秒を返す（0 以下なら
        呼び手が起動し直さない）。切り替えなければ None。割り付けの無い起動と基準の組の拒否は、期限が切れていても None。
        起動し直しには `LaunchOptions.left` で `deadline` の残りを渡す。
        """
        expired = False

        def ready() -> bool:
            nonlocal expired
            if left_until(deadline) > 0 and verify is not None and (changed := verify()) is not None:
                raise internal.InternalError("precondition", changed)
            expired = left_until(deadline) <= 0
            return not expired

        if not self.fall_back(reply, ready=ready):
            return 0.0 if expired else None
        return left_until(deadline)
