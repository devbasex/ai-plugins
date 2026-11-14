"""リリースの PR の承認の証跡を読み書きし、判定を PR の先端の commit status へ付ける。

- 提示（`present`）: 承認資料を、提示した先端の SHA を添えて PR へコメントする（会話のセッションが打つ）。資料が対象にした
  コミットが今の先端でなければ提示しない
- 答え（`answer`）: 人の答えを証跡のコメントに書き、書いた後に PR を読み直して、答えが今の先端への判定として
  届いたことを確かめる。届いていなければ `TrailError`（答えたつもりで止まり続けない）
- 確かめ（`check`）: 証跡を読んで判定し、判定を commit status `approval_decide.STATUS_CONTEXT` に付ける（workflow が打つ）

答えは提示した先端へのもので、答えるときに先端が動いていれば書かずに断る（見ていないコミットを承認しない）。
"""

from __future__ import annotations

from typing import NamedTuple

from ndf.adapters.github import GitHub, Pull
from ndf.adapters.publish import Publisher
from ndf.domains.release import approval_decide

# 判定 → commit status の状態（承認だけが success。待ちは pending で必須のチェックがマージを塞ぐ）
STATUS_STATE = {
    approval_decide.APPROVED: "success",
    approval_decide.REJECTED: "failure",
    approval_decide.STALE: "pending",
    approval_decide.MISSING: "pending",
}


class TrailError(Exception):
    """証跡を書けない・書いた答えが判定に届かない・先端が提示から動いた。"""


class Read(NamedTuple):
    pull: Pull
    judgment: approval_decide.Judgment


def read(hub: GitHub, number: int) -> Read:
    """PR と、信用する書き手の証跡からの判定。"""
    pull = hub.pull(number)
    comments = [(c.author, c.body, hub.trusts(c)) for c in hub.comments(number)]
    return Read(pull, approval_decide.judge(approval_decide.trails(comments), pull.head_sha))


def present(hub: GitHub, number: int, material: str) -> str:
    """承認資料を提示する。提示した先端の SHA を返す。

    資料が対象にしたコミット（行 `approval_decide.HEAD_LINE`）が PR の今の先端でなければ、提示せずに `TrailError`
    （資料の後に積んだ変更を含まない資料から、新しい先端を承認させない。資料を組み直してから提示する）。
    """
    pull = hub.pull(number)
    shown = approval_decide.material_head(material)
    if shown != pull.head_sha:
        target = shown[:12] if shown else "（資料に無い）"
        raise TrailError(f"#{number} の承認資料が対象にしたコミット {target} が今の先端 {pull.head_sha[:12]} でない（資料を組み直す）")
    Publisher(hub).comment(number, approval_decide.render_request(material, pull.head_sha))
    return pull.head_sha


def answer(hub: GitHub, number: int, value: str, by: str, presented: str) -> approval_decide.Judgment:
    """答えを証跡に書き、届いたことを読み直して確かめる。"""
    pull = hub.pull(number)
    if pull.head_sha != presented:
        raise TrailError(f"#{number} の先端が提示した {presented[:12]} から {pull.head_sha[:12]} へ動いた（提示し直す）")
    Publisher(hub).comment(number, approval_decide.render_trail(value, pull.head_sha, by))
    after = read(hub, number)
    if after.judgment.state != value or after.pull.head_sha != pull.head_sha:
        raise TrailError(f"#{number} へ書いた答え {value} が判定に届いていない（判定: {after.judgment.detail}）")
    return after.judgment


def post_status(hub: GitHub, sha: str, judgment: approval_decide.Judgment) -> None:
    """判定を commit status に付ける。"""
    payload = {
        "state": STATUS_STATE[judgment.state],
        "context": approval_decide.STATUS_CONTEXT,
        "description": judgment.detail[:140],
    }
    hub.rest("POST", f"/repos/{hub.repo}/statuses/{sha}", payload)


def check(hub: GitHub, number: int) -> Read:
    """証跡を読んで判定し、PR の先端へ commit status を付ける。"""
    got = read(hub, number)
    post_status(hub, got.pull.head_sha, got.judgment)
    return got
