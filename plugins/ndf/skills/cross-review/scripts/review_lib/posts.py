"""投稿の待ち行列と、投稿済みの確認（#291・#1142 の C2）。

**GitHub が使えない間も収束ループを進める。** 上限に達したときだけ投稿する内容を
ローカルへ積み、回復した後に順に流す。積む・流す・上限を見分けるところはライブラリ
（`post_queue`）が持ち、ここが持つのは置き場所の解決と、流した直後の確認と、
収束の判定への結び付けだけである。

**止めるのは `final = approved` を出すところだけである。** レビューも修正もローカルで
進み、判定も記録される。未反映のまま「両方が承認した」と記録しないことが、この課題で
守る一線である。
"""
from __future__ import annotations

from typing import Any

import review_lib  # noqa: E402
import post_queue  # noqa: E402
from review_lib import github, store  # noqa: E402


def _queue(pr: int) -> post_queue.Queue:
    """この Pull Request の待ち行列。状態ファイルと同じ親の下に置く。

    作業ツリーの中へ置くのは、巻き直しで作業ツリーを捨てるときに待ち行列も一緒に
    捨てられるようにするためである。**捨ててよいのは、巻き直しが close と create を
    積まないためである**（積む対象はその Pull Request 宛のコメントだけで、Pull Request
    ごと捨てるなら宛先も無くなる）。
    """
    return post_queue.Queue(store._resolve_tmp_dir(pr) / post_queue.QUEUE_DIRNAME)


def _pending_posts(pr: int) -> int:
    """まだ届いていない投稿の件数。**照会は行わない**（ファイルを数えるだけ）。"""
    return _queue(pr).count()


def _flushed_review(item: dict[str, Any]) -> tuple[Any, Any, str] | None:
    """待ち行列の項目から、確認するレビューの担当・ラウンド・URL を得る。"""
    if item.get("kind") != "review-post":
        return None
    extra = item.get("extra") or {}
    agent, round_no = extra.get("agent"), extra.get("round")
    if not (agent and round_no):
        return None
    response = item.get("response")
    response = response if isinstance(response, dict) else {}
    url = str(response.get("html_url") or "")
    if not url and response.get("id"):
        url = f"#pullrequestreview-{response['id']}"
    return agent, round_no, url


def _flushed_review_target(
        state: dict[str, Any], agent: Any, round_no: Any) -> dict[str, Any] | None:
    """レビューを積んだラウンドから、担当の書き戻し先を探す。"""
    return next(
        (entry for entry in state.get("rounds", [])
         if entry.get("round") == round_no
         and isinstance(entry.get(agent), dict)),
        None,
    )


def _confirm_flushed(pr: int, item: dict[str, Any]) -> None:
    """流した直後に、投稿が届いたことを 1 度だけ確かめる。

    #261 は「投稿が届いたことを確かめてから収束する」ことを求めている。待ち行列は
    確かめる時点を投稿の直後から**流した直後**へ移すだけで、決まりそのものは変えない。

    **送った項目と、既に届いていた項目を分けない。** 送信に成功した直後に中断すると、
    GitHub 側には投稿があるのに項目は残る。次に流すと冪等の照会で見つかって送らずに
    消えるため、ここを通さないと `queued` が解除されず `review_url` も戻らないまま
    待ち行列が空になる。**待ち行列が空で `queued` のままの状態は、収束を止める側にも
    確認を促す側にも働かない**（判定は `queued` を見て照会を飛ばす）。共通層が
    見つけた投稿を `response` として渡すため、どちらも同じ経路で確かめられる。
    """
    review = _flushed_review(item)
    if review is None:
        return
    agent, round_no, url = review
    st = store._load(pr)
    # **書き戻す先は、その項目が属するラウンドである。** 積んだラウンドと流した
    # ラウンドが同じとは限らないため、最後のラウンドへ書かない。
    target = _flushed_review_target(st, agent, round_no)
    if target is None:
        return
    exists = github._review_exists(str(st.get("repo") or ""),
                            int(st.get("current_pr") or pr), url)
    if exists is False:
        # #261 の決まり。届いていない投稿は結果なしとして扱い、起動し直しの経路へ乗せる。
        target[agent] = {
            "intent": NO_RESULT,
            "no_result_reason": "not_posted",
            "posted_as": None,
            "comments": None,
            "review_url": None,
            "by_severity": {},
        }
        review_lib.info(
            f"⚠ {agent}: 流した後も投稿を確認できません (review_url={url!r})。"
            " 結果なしとして記録します"
        )
    else:
        target[agent]["review_url"] = url
        target[agent]["queued"] = False
    store._save(pr, st)


def _auto_flush(pr: int) -> None:
    """進行側の各コマンドの入口で待ち行列を流す。

    **自動だけにも明示だけにもしない。** 自動だけだと、回復を待つあいだ何もコマンドを
    実行していない場合に流れない。明示だけだと、進行側が忘れたときに待ち行列が残った
    まま収束の判定へ進む。流せなくても工程は止めない。

    **入口は、書き戻し先が揃っている場所だけである。** 積むのは取り込み
    （`read-result`）だけで、積んだ時点でその担当の記録を書くため、流す時点では
    書き戻し先が揃っている。
    """
    q = _queue(pr)
    if not q.count():
        return
    result = q.flush()
    for item in result.sent + result.skipped:
        _confirm_flushed(pr, item)
    if result.sent or result.skipped or result.dropped:
        review_lib.info(
            f"↻ 待ち行列を流しました: 送った {len(result.sent)} 件 /"
            f" 既に届いていた {len(result.skipped)} 件 /"
            f" 送れずに飛ばした {len(result.dropped)} 件"
        )
    if result.remaining:
        reason = (result.failed or {}).get("last_error", "")
        review_lib.info(f"⏳ 待ち行列に {result.remaining} 件残っています: {reason}")


NO_RESULT = "NO_RESULT"
