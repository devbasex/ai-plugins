"""結果の取り込みが共通の結末を読み、理由と監視の詳細を残すことのテスト（#729 AC13）。

`read-result` は結果ファイルを自前で開かず、結末の共通層（`monitor_outcome`）の
`read_launch_outcome` を呼ぶ。使える結果が無いとき、`no_result_reason` に共通の `reason`
を残し、監視の結果ファイルがあれば `monitor_detail` に監視の `detail` を残す。
終了コードは変更前と同じ（`unparsable` は 3、それ以外は 1）である。

| 監視の結果ファイル | 結果ファイル | `no_result_reason` | `monitor_detail` | 終了コード |
| --- | --- | --- | --- | --- |
| 監視が決めた理由（6 語） | 無い | その値 | 監視の `detail` | 1 |
| 監視が決めた理由（6 語） | 読めない | その値 | 監視の `detail` | 1 |
| 無い | 無い | `missing` | 鍵なし | 1 |
| 無い | 読めない | `unparsable` | 鍵なし | 3 |
| `ok` / `missing` | 無い | `missing` | 監視の `detail`（空なら鍵なし） | 1 |
| 問わない | 読める | （使える結果として取り込む） | — | 0 |

stem の突き合わせ（設計文書の未確認 6）もここで固定する。起動の手順・監視・取り込みの
3 つが同じ形の stem を組み立てなければ、監視の結果ファイルを引けない。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import pytest
import review_lib.commands.read_result
import review_lib.github

PR = 7729
AGENT = "kiro"
STEM = f"{AGENT}-review-pr{PR}"
DETAIL = "Monthly request limit reached"

_LAUNCHER = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-reviewer.sh"

# 監視が結末に書く理由のうち、結果ファイルの状態を見ずにその値を採るもの（設計文書の
# 「理由の語彙」の表で「監視」が書き、`ok` / `missing` でない 6 語）。
MONITOR_DECIDED = ("timeout", "stalled", "early_error", "usage_limit", "cli_timeout", "pidfile_bad")


# ---------------- 足場 ----------------


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    """`CROSS_REVIEW_TMP_DIR` を tmp_path に向ける。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def review_posted(monkeypatch, state_mod):
    """投稿の実在確認は届いた前提にする。ここで見るのは結末の読み方である。"""
    monkeypatch.setattr(review_lib.github, "_review_exists", lambda repo, pr, url: True)


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-19T00:00:00+00:00"}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _entry(tmp_dir: pathlib.Path) -> dict:
    st = json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())
    return st["rounds"][-1][AGENT]


def _write_monitor(tmp_dir: pathlib.Path, reason: str, detail: str = DETAIL) -> None:
    """監視の結果ファイル `<stem>-monitor.json` を置く。キーは監視が書く形に倣う。"""
    outcome = {
        "agent": AGENT,
        "stem": STEM,
        "status": "EARLY_ERROR",
        "exit_code": 4,
        "reason": reason,
        "detail": detail,
    }
    (tmp_dir / f"{STEM}-monitor.json").write_text(json.dumps(outcome, ensure_ascii=False))


def _read_result(state_mod, rfile: pathlib.Path | None) -> int:
    """`read-result` を呼び、終了コードを返す。`rfile` が None なら既定のパスを使う。"""
    args = argparse.Namespace(pr=PR, agent=AGENT, file=str(rfile) if rfile else None)
    with pytest.raises(SystemExit) as e:
        review_lib.commands.read_result.cmd_read_result(args)
    return int(e.value.code or 0)


# ---------------- AC13: 理由と監視の詳細を残す ----------------


@pytest.mark.parametrize("reason", MONITOR_DECIDED)
def test_the_monitor_reason_is_recorded_when_no_result_file_exists(
    tmp_dir, state_mod, reason, capsys
):
    """監視が理由を決めていれば、結果ファイルが無いときその理由と詳細が残る。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, reason)

    assert _read_result(state_mod, tmp_dir / "absent-result.json") == 1

    entry = _entry(tmp_dir)
    assert entry["intent"] == "NO_RESULT"
    assert entry["no_result_reason"] == reason
    assert entry["monitor_detail"] == DETAIL
    err = capsys.readouterr().err
    assert reason in err
    assert DETAIL in err


def test_the_monitor_reason_wins_over_an_unreadable_result_file(tmp_dir, state_mod):
    """監視が止めたと分かっているなら、読めない結果ファイルがあってもその理由で 1 で止まる。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, "usage_limit")
    rfile = tmp_dir / "result.json"
    rfile.write_text("{ not json")

    assert _read_result(state_mod, rfile) == 1

    entry = _entry(tmp_dir)
    assert entry["no_result_reason"] == "usage_limit"
    assert entry["monitor_detail"] == DETAIL


def test_without_a_monitor_file_the_reason_is_missing_and_no_detail_key_is_written(
    tmp_dir, state_mod
):
    """監視の結果ファイルが無ければ `missing` で、`monitor_detail` の鍵そのものが無い。"""
    _seed_state(tmp_dir)

    assert _read_result(state_mod, tmp_dir / "absent-result.json") == 1

    entry = _entry(tmp_dir)
    assert entry["no_result_reason"] == "missing"
    assert "monitor_detail" not in entry


def test_an_unparsable_result_without_a_monitor_file_keeps_exit_code_3(tmp_dir, state_mod):
    """監視の結果ファイルが無く結果が読めなければ、`unparsable` で従来どおり 3 で止まる。"""
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps([{"event": "APPROVE"}]))

    assert _read_result(state_mod, rfile) == 3

    entry = _entry(tmp_dir)
    assert entry["no_result_reason"] == "unparsable"
    assert "monitor_detail" not in entry


@pytest.mark.parametrize("reason", ("ok", "missing"))
def test_a_monitor_that_did_not_decide_falls_back_to_the_result_file(
    tmp_dir, state_mod, reason
):
    """監視が `ok` / `missing` なら理由は結果ファイルの側で決まり、詳細だけ残る。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, reason, detail="exit 0")

    assert _read_result(state_mod, tmp_dir / "absent-result.json") == 1

    entry = _entry(tmp_dir)
    assert entry["no_result_reason"] == "missing"
    assert entry["monitor_detail"] == "exit 0"


def test_an_empty_monitor_detail_does_not_write_the_key(tmp_dir, state_mod):
    """監視の結果ファイルがあっても `detail` が空なら、`monitor_detail` を書かない。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, "timeout", detail="")

    assert _read_result(state_mod, tmp_dir / "absent-result.json") == 1

    entry = _entry(tmp_dir)
    assert entry["no_result_reason"] == "timeout"
    assert "monitor_detail" not in entry


def test_a_readable_result_file_wins_over_the_monitor_reason(tmp_dir, state_mod):
    """監視が利用上限で止めた後でも、結果ファイルが読めればそれは使える結果である。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, "usage_limit")
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "APPROVE", "posted_as": "APPROVE", "comments_count": 0,
        "review_url": "https://example/pr/1#1", "by_severity": {},
    }))

    review_lib.commands.read_result.cmd_read_result(argparse.Namespace(pr=PR, agent=AGENT, file=str(rfile)))

    entry = _entry(tmp_dir)
    assert entry["intent"] == "APPROVE"
    assert "no_result_reason" not in entry


def test_no_verdict_still_overrides_after_the_common_read(tmp_dir, state_mod):
    """判定の値が無い結果は、共通の読み取りの後で cross-review が `no_verdict` に上書きする。"""
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, "ok", detail="exit 0")
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({"comments_count": 0}))

    assert _read_result(state_mod, rfile) == 1
    assert _entry(tmp_dir)["no_result_reason"] == "no_verdict"


# ---------------- 未確認 6: stem の突き合わせ ----------------


def _launcher_stem_pattern() -> str:
    """`launch-reviewer.sh` の `STEM=` の行から、ディレクトリを除いた stem の形を取る。"""
    lines = [l for l in _LAUNCHER.read_text(encoding="utf-8").splitlines()
             if re.match(r"^\s*STEM=", l)]
    assert len(lines) == 1, lines
    value = lines[0].split("=", 1)[1].strip()
    assert value.startswith("$TMP_DIR/"), value
    return value[len("$TMP_DIR/"):]


def test_the_three_stems_have_the_same_shape(tmp_dir, state_mod, monitor_mod):
    """起動の手順・監視・取り込みの stem が同じ形で、監視の結果ファイルを引ける。"""
    # 起動の手順（bash）: 変数名を置き換えて形を比べる
    # 起動の手順が持つのは席の名前（`$SEAT`）である。CLI はそこから引く（#727）
    launcher = (_launcher_stem_pattern()
                .replace("$SEAT", AGENT).replace("$STATE_PR", str(PR)))
    # 監視（Python）: 既定の stem テンプレート
    monitor = monitor_mod.DEFAULT_STEM_TEMPLATE.format(agent=AGENT, id=PR)
    assert launcher == monitor == STEM

    # 取り込み: 既定のパス（`--file` 無し）で、監視の結果ファイルの理由を引ける
    _seed_state(tmp_dir)
    _write_monitor(tmp_dir, "usage_limit")
    assert _read_result(state_mod, None) == 1
    assert _entry(tmp_dir)["no_result_reason"] == "usage_limit"
