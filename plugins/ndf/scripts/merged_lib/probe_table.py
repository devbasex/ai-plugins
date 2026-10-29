"""probe の分類の対応表（`merged-steps.py` の cmd_probe から分けた）。"""

# 分類ごとの (手, 要約)。並びは強い順で、PR が 2 つ以上に当たれば上を採り、複数の PR は最も上の分類で全体を表す。
# 要約の {names} は根拠のチェック名、{queued_runs} は待ち行列の件数
PROBE_TABLE = {
    "failed": ("fix", "失敗したチェックがある: {names}"),
    "stale": ("judge", "取り残されたチェックがある: {names}"),
    "stale_again": ("judge", "再実行したチェックが再び取り残された: {names}"),
    # Runner が付かずに取り消された（CI の基盤待ち）。再実行は merge-when-green が持つ
    "infra": ("wait", "Runner が付かずに取り消されたジョブがある（CI の基盤待ち）: {names}"),
    "settled": ("wait", "実行は終わりジョブに結論がある（表示だけが pending）: {names}"),
    "queued": ("wait", "ジョブがランナーを待っている（待ち行列 {queued_runs} 件）: {names}"),
    "running": ("wait", "実行中のチェックがある: {names}"),
    "passed": ("judge", "すべてのチェックが通っている（待つ側が抜けていない）"),
    "none": ("judge", "開いた PR が無い・読めない"),
}
PROBE_REMEDIED_SUMMARY = "取り残されたチェックを再実行した: {names}"  # stale を --act で直したとき
PROBE_CLASSES = tuple(PROBE_TABLE)
PROBE_ACTIONS = {cls: action for cls, (action, _) in PROBE_TABLE.items()}
