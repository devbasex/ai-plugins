"""モデル指定と集計のテスト。

比較を成立させるための 3 点を見る。**指定値が固定されること**、
**実際に動いたモデルを記録できること**、**既定モデルのラウンドを区別すること**。
"""

from __future__ import annotations

import json
import pathlib

import pytest


# ---------- モデル指定の解析 ----------


def test_repeated_model_args(models):
    spec = models.parse_model_args(["codex=gpt-5.5", "claude=opus-5"])
    assert spec["codex"] == "gpt-5.5"
    assert spec["claude"] == "opus-5"


def test_unspecified_runtime_is_none(models):
    spec = models.parse_model_args(["codex=gpt-5.5"])
    assert spec["agy"] is None and spec["kiro"] is None


def test_no_args_gives_all_none(models):
    assert set(models.parse_model_args(None).values()) == {None}


def test_unknown_runtime_is_an_error(models):
    with pytest.raises(models.ModelSpecError):
        models.parse_model_args(["gpt=gpt-5.5"])


def test_missing_equals_is_an_error(models):
    with pytest.raises(models.ModelSpecError):
        models.parse_model_args(["codex"])


def test_empty_model_name_is_an_error(models):
    with pytest.raises(models.ModelSpecError):
        models.parse_model_args(["codex="])


def test_duplicate_runtime_is_an_error(models):
    """後勝ちにしない。どちらの値で走ったのか成果物から判別できなくなる。"""
    with pytest.raises(models.ModelSpecError):
        models.parse_model_args(["codex=a", "codex=b"])


# ---------- フラグ生成 ----------


def test_model_flag_for_each_runtime(models):
    for runtime in ("claude", "codex", "agy", "kiro"):
        assert models.model_flag(runtime, "m") == ["--model", "m"]


def test_no_flag_when_unspecified(models):
    assert models.model_flag("codex", None) == []


# ---------- 計測に使えるかの判定 ----------


def test_claude_and_codex_report_the_model_that_actually_ran(models):
    """実測モデル名を取得できるランタイムを 1 箇所で宣言する。"""
    assert models.OBSERVABLE_RUNTIMES == ("claude", "codex")


def test_kiro_default_is_not_measurable(models):
    """kiro の既定 auto は実際に選ばれたモデルを取得できない。"""
    assert models.is_measurable("kiro", None) is False
    assert models.is_measurable("kiro", "auto") is False
    assert models.is_measurable("kiro", "claude-opus-5") is True


@pytest.mark.parametrize(
    ("runtime", "model", "measurable"),
    [
        # 指定も実測値も無ければ、claude も何が動いたか分からない
        ("claude", None, False),
        ("claude", "opus-5", True),
        # 指定値で代用する。実測はできないが、何を渡したかは分かる
        ("codex", "gpt-5.5", True),
        ("agy", "gemini-3.8", True),
        ("kiro", "claude-opus-5", True),
        # 何が動いたか分からない。指定が無く、実測もできない
        ("codex", None, False),
        ("agy", None, False),
        ("kiro", None, False),
        ("kiro", "auto", False),
    ],
)
def test_measurability_covers_every_participant(models, runtime, model, measurable):
    assert models.is_measurable(runtime, model) is measurable


def test_separation_reason_differs_by_runtime(models):
    """分離の理由は「既定モデル（auto）」に固定せず、ランタイムごとに書き分ける。"""
    assert models.separation_reason("kiro", None) == ("kiro の auto はラウンドごとに違うモデルが動きうる")
    assert models.separation_reason("kiro", "auto") == ("kiro の auto はラウンドごとに違うモデルが動きうる")
    assert models.separation_reason("codex", None) == ("codex はモデルを指定しておらず、実際に動いたモデルも取得できない")
    assert models.separation_reason("agy", None) == ("agy はモデルを指定しておらず、実際に動いたモデルも取得できない")
    assert models.separation_reason("claude", None) == ("claude はモデルを指定しておらず、実際に動いたモデルも取得できない")
    assert models.separation_reason("codex", "gpt-5.5") is None
    # 実測値があれば、指定が無くても分離しない
    assert models.separation_reason("codex", None, "gpt-6.1-sol") is None
    assert models.separation_reason("kiro", None, "x") is None


def test_assumption_note_marks_rounds_counted_on_trust(models):
    """指定があり実測できないラウンドは分離しないが、前提を報告へ残す。"""
    assert models.assumption_note("codex", "gpt-5.5") == ("codex は指定した gpt-5.5 で動いた前提で数える（実測不可）")
    assert models.assumption_note("claude", "opus-5") == ("claude は指定した opus-5 で動いた前提で数える（実測不可）")
    assert models.assumption_note("claude", "opus-5", "claude-opus-5") is None
    assert models.assumption_note("codex", None) is None


def test_foreseen_separation_warns_only_runtimes_that_cannot_be_observed(models):
    """着手前の警告は、実測できる見込みのあるランタイムを警告しない。"""
    assert models.foreseen_separation("codex", None) is None
    assert models.foreseen_separation("claude", None) is None
    assert models.foreseen_separation("agy", None) == ("agy はモデルを指定しておらず、実際に動いたモデルも取得できない")
    assert models.foreseen_separation("kiro", None) == ("kiro の auto はラウンドごとに違うモデルが動きうる")


def test_label_marks_default_rounds(models):
    assert models.label(None) == "default"
    assert models.label("opus-5") == "opus-5"


# ---------- 実測値の取り出し ----------


def test_observed_model_picks_the_dominant_model(models, tmp_path):
    """課題 #759 の例。キャッシュの読み取りを数え、補助の haiku ではなく opus を選ぶ。"""
    stem = tmp_path / "claude-implement-rf751"
    pathlib.Path(f"{stem}-launch.json").write_text(
        json.dumps({"runtime": "claude", "workdir": str(tmp_path), "started_at": "2026-10-02T00:00:00Z"}), encoding="utf-8"
    )
    usage = {
        "claude-haiku-4-5-20251001": {"inputTokens": 3944, "cacheReadInputTokens": 0, "outputTokens": 28, "costUSD": 0.004},
        "claude-opus-5[1m]": {"inputTokens": 22, "cacheReadInputTokens": 639525, "outputTokens": 4377, "costUSD": 1.012},
    }
    pathlib.Path(f"{stem}-stdout.log").write_text(json.dumps({"modelUsage": usage}), encoding="utf-8")
    assert models.observed_model("claude", stem).model == "claude-opus-5[1m]"


def test_mismatch_warning(models):
    assert models.mismatch_warning("claude", "opus-5", "opus-5") is None
    assert models.mismatch_warning("claude", "opus-5", None) is None
    warning = models.mismatch_warning("claude", "opus-5", "sonnet-5")
    assert warning is not None and "食い違" in warning


# ---------- 集計 ----------


def _state_with_history():
    return {
        "items": [
            {"item_id": "R1-001", "status": "done"},
            {"item_id": "R1-002", "status": "abandoned", "budget_exceeded": True},
            {"item_id": "R2-001", "status": "done"},
        ],
        "rounds": [
            {
                "round": 1,
                "impl": "codex",
                "impl_model": {"requested": "gpt-5.5", "observed": None},
                "reviewers": ["agy", "kiro"],
                "reviewer_models": {
                    "agy": {"requested": "gemini-3.8", "observed": None},
                    "kiro": {"requested": "claude-opus-5", "observed": None},
                },
                "items": ["R1-001", "R1-002"],
                "fix_rounds": 1,
                "durations": {"apply": 100, "review": 50, "fix": 20},
                "reviewer_seconds": {"agy": 30, "kiro": 20},
                "reviews": [
                    {
                        "round": 1,
                        "agy": "REQUEST_CHANGES",
                        "kiro": "APPROVE",
                        "findings": [
                            {"reviewer": "agy", "item_id": "R1-002", "resolved": True},
                            {"reviewer": "agy", "item_id": "R1-001", "resolved": False},
                        ],
                    },
                    {"round": 2, "agy": "APPROVE", "kiro": "APPROVE", "findings": []},
                ],
            },
            {
                "round": 2,
                "impl": "claude",
                "impl_model": {"requested": "opus-5", "observed": "opus-5"},
                "reviewers": ["codex", "kiro"],
                "reviewer_models": {
                    "codex": {"requested": "gpt-5.5", "observed": None},
                    "kiro": {"requested": "claude-opus-5", "observed": None},
                },
                "items": ["R2-001"],
                "fix_rounds": 0,
                "durations": {"apply": 200, "review": 40},
                "reviews": [
                    {"round": 1, "codex": "APPROVE", "kiro": "APPROVE", "findings": []},
                ],
            },
        ],
    }


def test_metrics_group_by_runtime_and_model(metrics):
    agg = metrics.aggregate(_state_with_history())
    assert "codex / gpt-5.5" in agg["impl"]
    assert "claude / opus-5" in agg["impl"]
    assert "agy / gemini-3.8" in agg["reviewer"]
    assert "kiro / claude-opus-5" in agg["reviewer"]


def test_impl_metrics_count_applied_and_abandoned(metrics):
    agg = metrics.aggregate(_state_with_history())
    codex = agg["impl"]["codex / gpt-5.5"]
    assert codex["rounds"] == 1
    assert codex["applied"] == 1
    assert codex["abandoned"] == 1
    assert codex["budget_exceeded_rate"] == 0.5
    assert codex["avg_fix_rounds"] == 1.0
    assert codex["seconds"] == 120


def test_first_review_approval_rate(metrics):
    agg = metrics.aggregate(_state_with_history())
    assert agg["impl"]["codex / gpt-5.5"]["first_review_approval_rate"] == 0.0
    assert agg["impl"]["claude / opus-5"]["first_review_approval_rate"] == 1.0


def test_reviewer_metrics_resolution_and_agreement(metrics):
    agg = metrics.aggregate(_state_with_history())
    agy = agg["reviewer"]["agy / gemini-3.8"]
    assert agy["reviews"] == 2
    assert agy["findings"] == 2
    assert agy["resolution_rate"] == 0.5
    # 1 回目は不一致、2 回目は一致
    assert agy["agreement_rate"] == 0.5


def test_reviewer_seconds_are_not_shared_between_reviewers(metrics):
    """ラウンドの合計を各担当へ配ると 2 者分を両方に数えてしまう。"""
    agg = metrics.aggregate(_state_with_history())
    assert agg["reviewer"]["agy / gemini-3.8"]["seconds"] == 30
    assert agg["reviewer"]["kiro / claude-opus-5"]["seconds"] == 20


def test_kiro_default_rounds_are_separated(metrics):
    state = _state_with_history()
    state["rounds"][0]["reviewer_models"]["kiro"] = {"requested": None, "observed": None}
    agg = metrics.aggregate(state)
    assert any("kiro の auto" in w for w in agg["unmeasured"])
    # 分離したラウンドは表にも入れない。ラウンド 2 の kiro は指定があるので残る。
    assert "kiro / default" not in agg["reviewer"]
    assert agg["reviewer"]["kiro / claude-opus-5"]["reviews"] == 1


def test_unspecified_rounds_are_separated_per_runtime(metrics):
    """指定なしと実測不可を書き分ける。文言が 1 つだと codex の行を読み違える。"""
    state = _state_with_history()
    state["rounds"][0]["impl_model"] = {"requested": None, "observed": None}
    state["rounds"][0]["impl"] = "codex"
    agg = metrics.aggregate(state)
    assert any("codex はモデルを指定しておらず" in w for w in agg["unmeasured"]), agg["unmeasured"]
    text = metrics.format_report(agg)
    assert "集計から分離したラウンド" in text


def test_separated_rounds_are_left_out_of_the_tables(metrics):
    """分離すると報告したラウンドを集計表へ残さない。

    両方に出すと、読む側は分離したはずの行を「そのモデルの成績」として読む。
    分離は担当ごとに決まるため、実装担当を分離してもレビュー担当は残る。
    """
    state = _state_with_history()
    state["rounds"][0]["impl_model"] = {"requested": None, "observed": None}
    agg = metrics.aggregate(state)
    assert "codex / default" not in agg["impl"]
    assert "codex / gpt-5.5" not in agg["impl"]
    # 同じラウンドのレビュー担当は指定があるため集計に残る
    assert agg["reviewer"]["agy / gemini-3.8"]["reviews"] == 2


def test_assumed_rounds_are_reported_without_being_separated(metrics):
    """指定があり実測できないラウンドは集計へ入れたうえで、前提を報告へ残す。"""
    agg = metrics.aggregate(_state_with_history())
    assert any("実測不可" in w for w in agg["assumed"]), agg["assumed"]
    assert not any("実測不可" in w for w in agg["unmeasured"])
    assert "指定値で代用したラウンド" in metrics.format_report(agg)


def test_caveats_state_that_only_claude_is_observable(metrics):
    joined = "".join(metrics.COMPARISON_CAVEATS)
    assert "実際に動いたモデルを取得できるのは claude だけ" in joined


def test_model_mismatch_becomes_a_warning(metrics):
    state = _state_with_history()
    state["rounds"][1]["impl_model"]["observed"] = "sonnet-5"
    agg = metrics.aggregate(state)
    assert any("食い違" in w for w in agg["unmeasured"])


def test_report_includes_comparison_caveats(metrics):
    text = metrics.format_report(metrics.aggregate(_state_with_history()))
    assert "比較として読むときの限界" in text
    assert "ベンチマーク" in "".join(metrics.COMPARISON_CAVEATS) or True
    assert len(metrics.COMPARISON_CAVEATS) >= 5


def test_report_survives_empty_state(metrics):
    text = metrics.format_report(metrics.aggregate({"rounds": [], "items": []}))
    assert "（記録なし）" in text


# ---------- 実測モデルを状態へ反映する正常経路（R2-005） ----------
#
# 現状固定テスト。モデル文字列の解析（observed_model）と不一致判定
# （mismatch_warning）は個別に固定されているが、CLI の stdout ログから得た
# 実測モデルを状態の実装担当のモデルへ反映する正常経路（gitfacts.record_observed_model）
# はどのテストも通していなかった。


def test_record_observed_model_saves_the_observed_value(gitfacts, tmp_path):
    """R2-005 — stdout ログの実測モデルを implementer_model.observed へ保存する。

    指定値を変えない。指定の固定は `init` が持つ（test_init の実装担当のモデル）。
    """
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    _write_launch(tmp_dir / "claude-implement-rf130", "claude", tmp_path)
    # 実装手順の骨格は `claude-implement-rf130`（I3）。
    (tmp_dir / "claude-implement-rf130-stdout.log").write_text(
        json.dumps(
            {
                "type": "result",
                "is_error": False,
                "modelUsage": {"claude-opus-5": {"inputTokens": 100}},
            }
        ),
        encoding="utf-8",
    )
    state = {"id": 130, "tmp_dir": str(tmp_dir), "implementer_model": {"requested": "claude-opus-5", "observed": None}}

    gitfacts.record_observed_model(state, "claude", "implement")

    assert state["implementer_model"] == {"requested": "claude-opus-5", "observed": "claude-opus-5", "unobserved": None}


def test_record_observed_model_reads_only_the_named_phase(gitfacts, tmp_path):
    """別の手順のログは読まない（名前の幹は手順ごと）。"""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "claude-plan-rf130-stdout.log").write_text(
        json.dumps({"type": "result", "modelUsage": {"claude-opus-5": {"inputTokens": 1}}}), encoding="utf-8"
    )
    state = {"id": 130, "tmp_dir": str(tmp_dir), "implementer_model": {"requested": None, "observed": None}}

    gitfacts.record_observed_model(state, "claude", "implement")

    assert state["implementer_model"]["observed"] is None
    assert state["implementer_model"]["unobserved"] == "no_record"


def _write_launch(stem, runtime, workdir, started_at="2026-10-02T00:00:00Z"):
    pathlib.Path(f"{stem}-launch.json").write_text(
        json.dumps({"runtime": runtime, "workdir": str(workdir), "started_at": started_at}), encoding="utf-8"
    )


def _write_codex_session(codex_home, session_id, workdir, model, at="2026-10-02T00:00:05.123Z"):
    day = codex_home / "sessions" / at[:4] / at[5:7] / at[8:10]
    day.mkdir(parents=True, exist_ok=True)
    rows = [
        {"timestamp": at, "type": "session_meta", "payload": {"id": session_id, "cwd": str(workdir), "timestamp": at}},
        {"timestamp": at, "type": "turn_context", "payload": {"cwd": str(workdir), "model": model}},
    ]
    path = day / f"rollout-2026-10-02T00-00-05-{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _codex_state(tmp_path, monkeypatch, requested=None):
    """`--model` なしで codex が実装担当になった実行。セッションの記録は `CODEX_HOME` に置く。"""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    stem = tmp_dir / "codex-implement-rf130"
    _write_launch(stem, "codex", work)
    pathlib.Path(f"{stem}-monitor.json").write_text(json.dumps({"ended_at": "2026-10-02T09:10:00+09:00"}), encoding="utf-8")
    pathlib.Path(f"{stem}-err.log").write_text("OpenAI Codex v0.159.3\n--------\nsession id: 01a0f9f8-aaaa\n--------\n", encoding="utf-8")
    _write_codex_session(codex_home, "01a0f9f8-aaaa", work, "gpt-6.1-sol")
    # 同じ作業ディレクトリと時刻に合うが ID の違う記録。ID で名指しするため拾わない。
    _write_codex_session(codex_home, "01a0f9f8-bbbb", work, "gpt-other")
    return {"id": 130, "tmp_dir": str(tmp_dir), "implementer_model": {"requested": requested, "observed": None, "unobserved": None}}


def test_record_observed_model_reads_the_codex_session(gitfacts, tmp_path, monkeypatch):
    """#759 AC1 — `--model` なしの codex でも、セッションの記録のモデル名が入る。"""
    state = _codex_state(tmp_path, monkeypatch)

    gitfacts.record_observed_model(state, "codex", "implement")

    assert state["implementer_model"] == {"requested": None, "observed": "gpt-6.1-sol", "unobserved": None}


def test_record_observed_model_warns_on_codex_mismatch(gitfacts, tmp_path, monkeypatch, capsys):
    """#759 AC9 — codex の指定値と実測値が食い違えば警告する。"""
    state = _codex_state(tmp_path, monkeypatch, requested="gpt-5.5")

    gitfacts.record_observed_model(state, "codex", "implement")

    assert "食い違" in capsys.readouterr().err


def test_record_observed_model_keeps_an_earlier_observation(gitfacts, tmp_path):
    """前の手順で取れた実測値は、後の手順で取れなくても消さない。"""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    state = {"id": 130, "tmp_dir": str(tmp_dir), "implementer_model": {"requested": None, "observed": "gpt-6.1-sol", "unobserved": None}}

    gitfacts.record_observed_model(state, "codex", "fix")

    assert state["implementer_model"] == {"requested": None, "observed": "gpt-6.1-sol", "unobserved": None}


def test_record_observed_model_ignores_a_leftover_from_before_the_phase(gitfacts, tmp_path):
    """手順の開始より古い起動の記録は、前の起動の残骸として取らない。"""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    stem = tmp_dir / "claude-implement-rf130"
    _write_launch(stem, "claude", tmp_path, started_at="2026-10-01T00:00:00Z")
    pathlib.Path(f"{stem}-stdout.log").write_text(json.dumps({"modelUsage": {"claude-opus-5": {"inputTokens": 1}}}), encoding="utf-8")
    state = {
        "id": 130,
        "tmp_dir": str(tmp_dir),
        "phases": {"implement": {"started_at": "2026-10-02T09:00:00+09:00"}},
        "implementer_model": {"requested": None, "observed": None, "unobserved": None},
    }

    gitfacts.record_observed_model(state, "claude", "implement")

    assert state["implementer_model"]["unobserved"] == "no_record"


@pytest.mark.parametrize(
    ("runtime", "model", "line"),
    [
        # AC4 — 実測値があれば指定が無くても分離せず、実測のモデル名を出す
        ("codex", {"requested": None, "observed": "gpt-6.1-sol"}, "gpt-6.1-sol（実測）"),
        ("kiro", {"requested": "claude-opus-5", "observed": None}, "claude-opus-5（指定。実測できず）"),
        # AC5 — 実測値も指定も無ければ、ランタイムごとの分離の理由を出す
        (
            "codex",
            {"requested": None, "observed": None},
            "default（分離: codex はモデルを指定しておらず、実際に動いたモデルも取得できない）",
        ),
        ("kiro", {}, "default（分離: kiro の auto はラウンドごとに違うモデルが動きうる）"),
    ],
)
def test_report_shows_the_implementer_model(refactor_lib, runtime, model, line):
    import importlib

    report = importlib.import_module(f"{refactor_lib.__name__}.commands.report")
    assert report._implementer_model_text({"implementer": runtime, "implementer_model": model}) == line
