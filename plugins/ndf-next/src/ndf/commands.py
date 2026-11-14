"""副命令の表（副命令の名前 → `main(argv: list[str]) -> int` を持つモジュール）と、モジュールから副命令の名前を引く関数。

副命令を足す子 Issue は、`COMMANDS` へ 1 行を足す。エントリポイント（`ndf.cli`）と各副命令はここを参照する（副命令がエントリポイントを
import しない）。
"""

from __future__ import annotations

# 副命令の名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "check": "ndf.checks.run",
    "ci": "ndf.ci_cli",
    "contracts": "ndf.contracts.cli",
    "hooks": "ndf.domains.hooks.cli",
    "launch-args": "ndf.adapters.cli",
    "notify": "ndf.adapters.notify",
    "plan": "ndf.core.plan.cli",
    "progress": "ndf.progress.command",
    "relay": "ndf.domains.relay.cli",
    "test-slot": "ndf.testing.command",
    "training": "ndf.adapters.training",
    "worker": "ndf.core.worker",
}


def tool_of(module: str) -> str:
    """モジュールのパス（副命令のモジュールの `__name__`）から副命令の名前を求める。

    副命令の結果 JSON の `tool` はこの名前にする（各副命令は `TOOL = commands.tool_of(__name__)` で引き、名前を写さない）。
    表に無いモジュールは `LookupError`。
    """
    for name, path in COMMANDS.items():
        if path == module:
            return name
    raise LookupError(f"副命令の表（cli.COMMANDS）に無いモジュール: {module}")
