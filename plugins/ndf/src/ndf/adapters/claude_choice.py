"""選んだモデルと effort で `claude -p` を起動する部品（#2944）。

値を選ぶ処理（`ndf.adapters.model_choice`）は、この層より後の子の部品なので読まない。一番上のエントリポイントが
`set_launch_chooser` で差し込み（`claude.set_launch_recorder` と同じ形）、差し込まれていなければ値を選ばずに起動する
（エントリポイントを通らない呼び出しとテスト）。効果指標は差し込まれた使用量ログの書き込み処理の `record_outcome` で書く。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ndf.adapters import claude

log = logging.getLogger(__name__)


class LaunchChooser(Protocol):
    """起動の値を選ぶ処理（`ndf.adapters.model_choice` が満たす）。値（Choice）は `model`・`effort`・`assign_id`・`repo`・
    `cli_args()`・`env_changes()`・`row_fields()` を持つ。`repo` は選ぶときに解決したリポジトリのキー（解決しなければ None）。"""

    def launch_choice(self, layer: str, name: str, runtime: str = "claude", *, cwd: Path, **request: Any) -> Any: ...

    def base_choice(self, choice: Any) -> Optional[Any]: ...

    def record_refusal(self, choice: Any, reason: str) -> None: ...

    def record_base_switch(self, choice: Any) -> None: ...

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


@dataclass
class ChoiceRun:
    """選んだ値で `claude -p` を起動するリクエスト 1 件。`choice` は選んだ値（差し込みが無ければ None で、値を付けない）。

    `request_id` はリクエストごとの一意な値、`request_try` はそのリクエストの中の何番目の起動か（最初が 1）。`charge_to` は費用を
    手戻りとして負う割り付け ID。`switched` は基準の組の子プロセスを実際に起動して切り替えを確定したか（リクエストの中で 1 回だけ）。
    """

    choice: Optional[Any]
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_try: int = 1
    charge_to: Optional[str] = None
    switched: bool = False
    _seen: bool = False
    _refused: Optional[Any] = None  # 拒否された組の値。基準の組へ切り替えると決めたときに持つ

    @property
    def model(self) -> Optional[str]:
        return None if self.choice is None else self.choice.model

    @property
    def effort(self) -> Optional[str]:
        return None if self.choice is None else self.choice.effort

    @property
    def assign_id(self) -> Optional[str]:
        return None if self.choice is None else self.choice.assign_id

    @property
    def repo(self) -> Optional[str]:
        """値を選んだときに解決したリポジトリのキー。打ち直しの選択へ渡し、解決し直さない。"""
        return None if self.choice is None else self.choice.repo

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

        切り替えられると分かったら、まず拒否の記録を書く（基準の組の応答を待つ間に並んで割り付ける起動も、この拒否を数えられるように）。
        `ready` はその後に呼ぶ。偽を返すか例外を投げれば切り替えない。切り替えの記録はここでは書かず、基準の組を実際に起動した後に
        `record_switch` で書く。それまで `switched` は偽のままである。
        """
        if reply.failure is not claude.Failure.MODEL_REFUSED or self._refused is not None or launch_chooser is None or self.choice is None:
            return False
        back = launch_chooser.base_choice(self.choice)
        if back is None:
            return False
        launch_chooser.record_refusal(self.choice, reply.error or str(reply.failure))
        if ready is not None and not ready():
            return False
        self._refused = self.choice
        self.choice, self.request_try = back, self.request_try + 1
        return True

    def record_switch(self, reply: claude.ClaudeReply) -> None:
        """基準の組の起動の回答 `reply` を受け、子プロセスを実際に起動していれば切り替えを確定して記録を書く。

        起動前に止まった（`reply.launched` が偽）なら、記録を書かず `switched` も偽のままにする。
        """
        if self._refused is None or self.switched or not reply.launched:
            return
        self.switched = True
        if launch_chooser is not None:
            launch_chooser.record_base_switch(self._refused)
