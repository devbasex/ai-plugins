"""guard の本体。1 つのモジュールが 1 つの guard で、`check(ctx, svc) -> Verdict | None` を持つ。

guard は解析を自分で行わず、`HookContext` が 1 度だけ作ったもの（構文木・会話の記録の末尾・最後の応答の分類）を
読む。どの guard を呼ぶかと順序は guard の表（`contracts/hooks/guards.json`）が決める。
"""
