"""承認の回答の語と、承認資料が対象にしたコミットの行（#4409）。

リリースの承認の証跡（`domains.release.approval_decide`）・マージの承認の証跡（`core.merge_approval`）・工程の承認ゲート
（`progress.gate`）・`ndf ci` が同じ綴りを使うために、ここだけに置く。判断のモジュール（`approval_decide`）が読むので、
外部との接続を読まない。
"""

from __future__ import annotations

# 回答の語（事実の表 answers の語と同じ綴り）
APPROVED, REJECTED = "approved", "rejected"
ANSWERS = (APPROVED, REJECTED)
# 承認資料が対象にしたコミットの行の見出し
HEAD_LINE = "提示したコミット:"
