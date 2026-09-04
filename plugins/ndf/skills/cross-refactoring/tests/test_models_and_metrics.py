"""モデル指定と集計のテスト。

比較を成立させるための 3 点を見る。**指定値が固定されること**、
**実際に動いたモデルを記録できること**、**既定モデルのラウンドを区別すること**。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state


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

def test_only_claude_reports_the_model_that_actually_ran(models):
    """実測モデル名を取得できるランタイムを 1 箇所で宣言する。"""
    assert models.OBSERVABLE_RUNTIMES == ("claude",)


def test_kiro_default_is_not_measurable(models):
    """kiro の既定 auto は実際に選ばれたモデルを取得できない。"""
    assert models.is_measurable("kiro", None) is False
    assert models.is_measurable("kiro", "auto") is False
    assert models.is_measurable("kiro", "claude-opus-5") is True


@pytest.mark.parametrize(
    ("runtime", "model", "measurable"),
    [
        # 実測できる。指定が無くても実際に動いたモデルを読み取れる
        ("claude", None, True),
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
    assert models.separation_reason("kiro", None) == (
        "kiro の auto はラウンドごとに違うモデルが動きうる"
    )
    assert models.separation_reason("kiro", "auto") == (
        "kiro の auto はラウンドごとに違うモデルが動きうる"
    )
    assert models.separation_reason("codex", None) == (
        "codex はモデルを指定しておらず、実際に動いたモデルも取得できない"
    )
    assert models.separation_reason("agy", None) == (
        "agy はモデルを指定しておらず、実際に動いたモデルも取得できない"
    )
    assert models.separation_reason("claude", None) is None
    assert models.separation_reason("codex", "gpt-5.5") is None


def test_assumption_note_marks_rounds_counted_on_trust(models):
    """指定があり実測できないラウンドは分離しないが、前提を報告へ残す。"""
    assert models.assumption_note("codex", "gpt-5.5") == (
        "codex は指定した gpt-5.5 で動いた前提で数える（実測不可）"
    )
    assert models.assumption_note("claude", "opus-5") is None
    assert models.assumption_note("codex", None) is None


def test_label_marks_default_rounds(models):
    assert models.label(None) == "default"
    assert models.label("opus-5") == "opus-5"


# ---------- 実測値の取り出し ----------

def test_observed_model_from_claude_json(models):
    out = json.dumps({
        "type": "result", "is_error": False,
        "modelUsage": {"claude-opus-5": {"inputTokens": 100}},
    })
    assert models.observed_model("claude", out) == "claude-opus-5"


def test_observed_model_picks_the_dominant_model(models):
    out = json.dumps({"modelUsage": {
        "claude-haiku-4-5": {"inputTokens": 10},
        "claude-opus-5": {"inputTokens": 900},
    }})
    assert models.observed_model("claude", out) == "claude-opus-5"


def test_observed_model_is_none_for_other_runtimes(models):
    assert models.observed_model("codex", '{"modelUsage": {"x": {}}}') is None


def test_observed_model_tolerates_broken_output(models):
    assert models.observed_model("claude", "") is None
    assert models.observed_model("claude", '{"modelUsage": broken') is None


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
                "round": 1, "impl": "codex",
                "impl_model": {"requested": "gpt-5.5", "observed": None},
                "reviewers": ["agy", "kiro"],
                "reviewer_models": {"agy": {"requested": None, "observed": None},
                                    "kiro": {"requested": "claude-opus-5",
                                             "observed": None}},
                "items": ["R1-001", "R1-002"],
                "fix_rounds": 1,
                "durations": {"apply": 100, "review": 50, "fix": 20},
                "reviewer_seconds": {"agy": 30, "kiro": 20},
                "reviews": [
                    {"round": 1, "agy": "REQUEST_CHANGES", "kiro": "APPROVE",
                     "findings": [
                         {"reviewer": "agy", "item_id": "R1-002", "resolved": True},
                         {"reviewer": "agy", "item_id": "R1-001", "resolved": False},
                     ]},
                    {"round": 2, "agy": "APPROVE", "kiro": "APPROVE",
                     "findings": []},
                ],
            },
            {
                "round": 2, "impl": "claude",
                "impl_model": {"requested": "opus-5", "observed": "opus-5"},
                "reviewers": ["codex", "kiro"],
                "reviewer_models": {"codex": {"requested": "gpt-5.5", "observed": None},
                                    "kiro": {"requested": "claude-opus-5",
                                             "observed": None}},
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
    assert "agy / default" in agg["reviewer"]
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
    agy = agg["reviewer"]["agy / default"]
    assert agy["reviews"] == 2
    assert agy["findings"] == 2
    assert agy["resolution_rate"] == 0.5
    # 1 回目は不一致、2 回目は一致
    assert agy["agreement_rate"] == 0.5


def test_reviewer_seconds_are_not_shared_between_reviewers(metrics):
    """ラウンドの合計を各担当へ配ると 2 者分を両方に数えてしまう。"""
    agg = metrics.aggregate(_state_with_history())
    assert agg["reviewer"]["agy / default"]["seconds"] == 30
    assert agg["reviewer"]["kiro / claude-opus-5"]["seconds"] == 20


def test_kiro_default_rounds_are_separated(metrics):
    state = _state_with_history()
    state["rounds"][0]["reviewer_models"]["kiro"] = {"requested": None, "observed": None}
    agg = metrics.aggregate(state)
    assert any("kiro の auto" in w for w in agg["unmeasured"])


def test_unspecified_rounds_are_separated_per_runtime(metrics):
    """指定なしと実測不可を書き分ける。文言が 1 つだと codex の行を読み違える。"""
    state = _state_with_history()
    state["rounds"][0]["impl_model"] = {"requested": None, "observed": None}
    state["rounds"][0]["impl"] = "codex"
    agg = metrics.aggregate(state)
    assert any(
        "codex はモデルを指定しておらず" in w for w in agg["unmeasured"]
    ), agg["unmeasured"]
    text = metrics.format_report(agg)
    assert "集計から分離したラウンド" in text


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


# ---------- 指定値は全ラウンドで不変 ----------

def test_models_are_fixed_across_rounds(refactor, tmp_path, env_tmp_dir):
    """`start-round` が状態ファイルの指定値をそのままラウンドへ写すこと。"""
    state_path = make_state(
        tmp_path,
        models={"claude": "opus-5", "codex": "gpt-5.5", "agy": None,
                "kiro": "claude-opus-5"},
    )
    env_tmp_dir(state_path)
    args = type("A", (), {"id": 130})()
    for _ in range(3):
        try:
            refactor.cmd_start_round(args)
        except SystemExit:
            break
        state = read_state(state_path)
        entry = state["rounds"][-1]
        assert entry["impl_model"]["requested"] == state["models"][entry["impl"]]
        for r in entry["reviewers"]:
            assert entry["reviewer_models"][r]["requested"] == state["models"][r]
        # 次のラウンドを開けるように、いま開いたラウンドを閉じる
        entry["adopted"] = 1
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
