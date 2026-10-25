"""会話の記録の 1 行が持つ鍵の集合（`test_transcript_agents.py` と `skill-stats/tests/test_agents_report.py` が共有する）。

本体の `ROW_KEYS` を参照せずにリテラルで持つ。本体から取ると、期待値が実装と同じ値になって何も固定しない。
"""

ROW_KEYS = {
    "layer",
    "role",
    "depth",
    "model",
    "fixed",
    "peak",
    "work",
    "responses",
    "duration_seconds",
    "ending",
    "interruptions",
}
