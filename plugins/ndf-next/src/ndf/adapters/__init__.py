"""外部との接続の層（GitHub・git・CI・HTTP・LLM・外部の CLI）。

上の層（`ndf.core` から上）は外部のシステムをこの層の関数とクラスを通してだけ使う。この層は `ndf.contracts` だけを読む。
"""
