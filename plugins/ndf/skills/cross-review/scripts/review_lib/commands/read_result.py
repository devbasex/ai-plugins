"""副命令 `read-result`（#1142 の C2）。"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import review_lib  # noqa: E402
import monitor_outcome  # noqa: E402
import result_posts  # noqa: E402
from review_lib import posts, store  # noqa: E402


def _record_no_result(
    pr: int, agent: str, reason: str, monitor_detail: str | None = None,
) -> None:
    """使える結果が残らなかったことを、そのラウンドへ残す。

    判定（`cmd_judge`）はこの記録を読んで、起動し直しか中断かを決める。記録が無い
    ラウンドも結果なしとして読むため、ここで書けなかった場合も収束はしない。

    `monitor_detail` は監視の `detail`（#729）。**鍵が無い = 監視の結果ファイルが
    無かった**を保つため、`None` と空文字では鍵を書かない。

    状態ファイルを読めないときとラウンドがまだ無いときは、何も書かずに戻る。呼び出し
    元はこの直後に die するため、ここで新たに止める理由が無い。
    """
    path = store._state_path(pr)
    if not path.exists():
        return
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(st, dict) or not st.get("rounds"):
        return
    entry: dict[str, Any] = {
        "intent": posts.NO_RESULT,
        "no_result_reason": reason,
        "posted_as": None,
        "comments": None,
        "review_url": None,
        "by_severity": {},
    }
    if monitor_detail:
        entry["monitor_detail"] = monitor_detail
    st["rounds"][-1][agent] = entry
    store._save(pr, st)


def _die_no_result(pr: int, agent: str, reason: str, msg: str, code: int = 1) -> None:
    """結果なしをラウンドへ残してから止める。終了コードは現行のまま変えない。"""
    _record_no_result(pr, agent, reason)
    review_lib.die(msg, code=code)


def _read_review_result_file(pr: int, agent: str, rfile: pathlib.Path) -> dict[str, Any]:
    """結果ファイルを、結末の共通層の値として読む（#729 の決定 2）。

    結果ファイルを自前で開かない。監視の結果ファイル（`<stem>-monitor.json`）と突き合わせて
    使える結果か理由かを決めるのは `read_launch_outcome` で、ここが決めるのは終了コードだけ
    である（読めない結果は 3、それ以外は 1。変更前と同じ）。理由の語彙も起動し直しの可否も
    ここには置かない。
    """
    outcome = monitor_outcome.read_launch_outcome(
        store._resolve_tmp_dir(pr), f"{agent}-review-pr{pr}", rfile)
    if outcome.payload is not None:
        return outcome.payload
    reason = outcome.reason or "missing"
    # 監視の結果ファイルがあるときだけ、その `detail` を残す（無いときの `outcome.detail` は
    # 読めなかった理由の 1 文で、監視の詳細ではない）
    monitor_detail = str(outcome.monitor.get("detail") or "") if outcome.monitor else None
    _record_no_result(pr, agent, reason, monitor_detail)
    if reason == "unparsable":
        # 結果ファイルはあるが JSON の dict として parse できない。launcher の出力形式不正
        review_lib.die(
            f"{agent}: result.json の parse に失敗、または dict ではない ({rfile}):"
            f" {outcome.detail}",
            code=3,
        )
    review_lib.die(f"{agent}: 使える結果が無い (reason={reason}, {rfile}): {outcome.detail}")


# 指摘へ既定を与える項目。**持たない指摘も捨てない**（#156）。捨てると、4 項目へ
# 対応していない担当の指摘が記録から消える。
_FINDING_DEFAULTS: dict[str, Any] = {
    "evidence": "",
    "falsification": "",
    "suggested_check": "",
    # 投稿先。プロンプトが求める値は `inline` / `body` の 2 つで、持たない指摘は
    # 従来どおり投稿したインラインの複製であるため `inline` として扱う。
    "posted_to": "inline",
}


def _has_evidence(finding: dict[str, Any]) -> bool:
    """根拠と反証条件の**両方**が空でないか。

    片方だけでは、別の担当がその指摘を確かめられない。根拠は「何がそう言えるか」で、
    反証条件は「何が成り立てば棄却できるか」である。
    """
    return all(
        str(finding.get(key) or "").strip()
        for key in ("evidence", "falsification")
    )


def _load_payload(agent: str, path: pathlib.Path) -> list[dict[str, Any]] | None:
    """payload.json を読み、検証して dict のリストとして返す。読めない・不正なときは None を返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        review_lib.info(f"⚠ {agent}: payload.json を読めません（{path}: {exc}）。"
             "指摘の記録は 0 件です")
        return None
    if not isinstance(payload, dict):
        # 実測: dict 以外（`[]` / `null` / 文字列 / 数値）を渡すと
        # `payload.get(...)` が AttributeError で落ち、取り込みが例外で終わっていた。
        review_lib.info(f"⚠ {agent}: payload.json が dict ではありません"
             f"（{path}, type={type(payload).__name__}）。指摘の記録は 0 件です。"
             " review launcher の出力形式不正で、判定は中断します")
        return None
    raw = payload.get("comments")
    if not isinstance(raw, list):
        review_lib.info(f"⚠ {agent}: payload.comments が list ではありません"
             f"（{path}, type={type(raw).__name__}）。指摘の記録は 0 件です。"
             " review launcher の出力形式不正で、判定は中断します")
        return None
    items = [c for c in raw if isinstance(c, dict)]
    if len(items) != len(raw):
        review_lib.info(f"⚠ {agent}: payload.comments に dict でないエントリが"
             f" {len(raw) - len(items)} 件あります（{path}）。"
             "その分を除いて記録します。判定は中断します")
    return items


def _collect_review_findings(
    st: dict[str, Any], agent: str, pr: int, round_no: int
) -> int:
    """その担当の `payload.json` を読み、`review_findings[]` へ積む。

    **`comments[]` は投稿の複製ではなく、その担当が出した指摘の全件である**（#156）。
    総評だけへ書いた指摘も入るため、インラインの件数（`comments_count`）とは一致しない。

    **形の不正は黙って読み飛ばさない。** 同じファイルを判定の直前に読む
    `_finding_keys` は、`payload` が dict でないときと `comments[]` の要素が dict で
    ないときを `die(code=3)` で止める。取り込みが無言で 0 件を返すと、記録は空なのに
    判定は致命という割れ方をし、原因が payload の形であることが読み取れない。

    **ただし、ここでは止めない。** `_save` はこの呼び出しの後にあるため、止めると
    そのラウンドのレビュー結果（`rounds[-1][agent]`）ごと失われる。警告で見えるように
    して、止める判断は判定の側に残す。
    """
    # **キーは読めたかどうかに関わらず作る。** 旧い状態ファイルを読んだときも、
    # 以後の取り込みが同じ形で積めるようにする。
    findings = st.setdefault("review_findings", [])
    path = store._payload_path(agent, pr, round_no)
    if not path.exists():
        return 0
    items = _load_payload(agent, path)
    if items is None:
        return 0
    # **同じ (pr, round, agent) の記録は入れ替える。** 中断からの再実行で
    # `cmd_read_result` が 2 度走ることがあり、`rounds[-1][agent]` は代入で上書き
    # されるのに対し、こちらは追記であるため、そのままでは同じ指摘が件数だけ増える
    # （実測: 3 回の実行で 1 件が 3 件になった）。
    # **落とすのは、書き込む中身が確定した後である。** 読めなかったときに先へ落とすと、
    # 一度取り込めていた記録を、再実行の失敗が消してしまう。
    findings[:] = [
        f for f in findings
        if not (f.get("pr") == pr and f.get("round") == round_no
                and f.get("agent") == agent)
    ]
    for index, item in enumerate(items):
        finding = {**_FINDING_DEFAULTS, **item}
        finding.update({
            # **識別子は取り込みの時点で採番する**（#156）。統合・反証・実行検証の記録が、
            # どの指摘を指すかをこの値で結ぶ。担当とラウンドを含めるため、別の担当が
            # 同じ索引を持っても衝突しない。
            "finding_id": f"{agent}-r{round_no}-{index}",
            "pr": pr, "round": round_no, "agent": agent,
            "has_evidence": _has_evidence(finding),
        })
        findings.append(finding)
    return len(items)


def cmd_read_result(args: argparse.Namespace) -> None:
    """Step 2.4 — 担当の結果を読み、レビューを投稿して state にマージ。

    使える結果が残らなかったときは、`NO_RESULT` と理由をラウンドへ残してから止める。
    終了コードは現行のまま（無い・判定の値を持たないときは 1、JSON として読めない
    ときは 3）で、進む先を決めるのは次の判定である。

    **「読んで記録する」と「投稿する」を 1 つに閉じる**（#730 の決定 4）。順序は
    「指摘のファイルを読む → 投稿を積む → 流す → 送信の応答を記録へ書き戻す → 指摘を取り込む」
    である。分けると「投稿したが記録していない」に加えて「記録したが投稿していない」が
    もう 1 つ増える。1 つに閉じれば、途中で止まった状態は「送れていない」か
    「送れたが記録が無い」の 2 つになる。

    | 止まった場所 | 待ち行列の項目 | 立て直し |
    | --- | --- | --- |
    | 送る前（上限などで送れていない） | 残る | 判定の終了コード 8 の枝が流し直す |
    | 送った後・記録の前 | 残らない | 取り込みをもう一度呼ぶ。照合が先客を見つける |

    **担当の申告と GitHub の実数を突き合わせない。** 投稿する側と記録する側が同じに
    なるため、確かめる対象が無い。記録に入る URL は送信の応答から取り、件数は送れた
    インラインの数から取る。
    """
    agent = args.agent
    pr = args.pr
    rfile = pathlib.Path(args.file or store._resolve_tmp_dir(pr) / f"{agent}-review-pr{pr}-result.json")
    r = _validate_review_result(pr, agent, rfile)

    st = store._load(pr)
    if not st.get("rounds"):
        review_lib.die(f"{agent}: state.rounds が空。`state.py start-round` を先に呼んでください")

    # **先に残りを流す。** 残っているのは、前の取り込みで送れずに積んだ投稿だけで、
    # その担当の記録は積んだ時点で書いてある。流した結果をその記録へ書き戻してから、
    # この担当の投稿を後ろへ積む（Pull Request 上の順序を保つ）。
    posts._auto_flush(pr)
    st = store._load(pr)
    last = st["rounds"][-1]
    round_no = last.get("round")
    posted = result_posts.post_review(
        posts._queue(pr),
        store._payload_path(agent, pr, round_no),
        rfile,
        repo=str(st.get("repo") or ""),
        pr=int(st.get("current_pr") or pr),
        round_no=int(round_no or 1),
        seat=agent,
        head_sha=str(last.get("head_sha") or ""),
        is_own_pr=bool(st.get("event_downgrade") or st.get("is_own_pr")),
        actor=str(st.get("viewer_login") or "") or None,
        since=str(last.get("started_at") or "") or None,
    )
    if posted.failed:
        review_lib.die(f"{agent}: レビューを投稿できませんでした ({posted.detail})")

    collected = _record_review_post(st, agent, pr, r, posted)
    store._save(pr, st)
    if posted.review_url:
        print(f"POSTED review_url={posted.review_url}")
    print(f"INLINE={posted.posted_inline} BODY={posted.posted_body}"
          f" QUEUED={posted.queued}")
    print(f"FINDINGS={collected}")
    review_lib.info(f"✅ {agent}: intent={posted.intent} posted_as={posted.posted_as}"
         f" comments={posted.posted_inline}")


def _validate_review_result(
    pr: int, agent: str, rfile: pathlib.Path
) -> dict[str, Any]:
    """結果ファイルを読み、`event` / `intent` を検証して中身を返す。

    判定の値を持たないときは `NO_RESULT` をラウンドへ残してから止める（終了コードは
    現行のまま。無い・判定の値を持たないときは 1、JSON として読めないときは 3）。
    """
    r = _read_review_result_file(pr, agent, rfile)
    intent = r.get("event") or r.get("intent")
    if intent is None:
        _die_no_result(
            pr,
            agent,
            "no_verdict",
            f"{agent}: result.json に event / intent フィールドが無い ({rfile})。"
            " launcher prompt のスキーマ違反の可能性。",
        )
    return r


def _record_review_post(
    st: dict[str, Any],
    agent: str,
    pr: int,
    r: dict[str, Any],
    posted: Any,
) -> int:
    """投稿の結果をラウンドへ書き戻し、指摘を取り込んで件数を返す。

    **指摘そのものは別に積む**（#156）。`comments` は送れたインラインの数で、
    総評へ移した指摘はそこに現れない。
    """
    last = st["rounds"][-1]
    round_no = last.get("round")
    last[agent] = {
        "intent": posted.intent,
        "posted_as": posted.posted_as,
        "comments": posted.posted_inline,
        "review_url": posted.review_url,
        "by_severity": r.get("by_severity", {}),
        "queued": bool(posted.queued),
        "posted_inline": posted.posted_inline,
        "posted_body": posted.posted_body,
    }
    return _collect_review_findings(st, agent, pr, round_no)
