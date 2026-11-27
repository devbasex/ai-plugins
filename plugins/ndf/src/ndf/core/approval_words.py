"""承認の回答の語と担い手の語、承認資料が対象にしたコミットの行（#4409）。

リリースの承認の証跡（`domains.release.approval_decide`）・マージの承認の証跡（`core.merge_approval`）・工程の承認ゲート
（`progress.gate`）・`ndf ci` が同じ綴りを使うために、ここだけに置く。判断のモジュール（`approval_decide`）が読むので、
外部との接続を読まない。
"""

from __future__ import annotations

from enum import StrEnum

# 回答の語（事実の表 answers の語と同じ綴り）
APPROVED, REJECTED = "approved", "rejected"
ANSWERS = (APPROVED, REJECTED)
# 承認資料が対象にしたコミットの行の見出し
HEAD_LINE = "提示したコミット:"
# 証跡の担い手の行。迂回の前提に数えるのは利用者の証跡だけ（#3506 の I9）
BY_LINE = "担い手:"


class By(StrEnum):
    """証跡の担い手（旧いプラグインの `approve --by` と同じ値）。"""

    USER = "user"  # 利用者
    MVV = "mvv"  # MVV 判定


USER_BY, MVV_BY = By.USER.value, By.MVV.value
BY_CHOICES = tuple(b.value for b in By)
