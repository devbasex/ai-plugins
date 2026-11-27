"""#2944: 起動のモデルと effort を選ぶ処理（同梱の表と優先の順）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import model_choice as mc
from ndf.contracts import internal

MODEL_ENV = identity.env_name("launch-model")
EFFORT_ENV = identity.env_name("launch-effort")
CODEX_EFFORT_ENV = identity.env_name("launch-codex-effort")
INHERITED_ENV = identity.env_name("claude-effort-level")


def _repo(tmp_path: Path, experiments: dict | None = None) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    if experiments is not None:
        (root / ".ndf").mkdir()
        (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": experiments}), encoding="utf-8")
    return root


def _pick(root: Path, layer: str = "worker", name: str = "impl", runtime: str = "claude", **kw) -> mc.Choice:
    kw.setdefault("environ", {})
    return mc.launch_choice(layer, name, runtime, cwd=root, **kw)


def _stopped(call) -> str:
    with pytest.raises(internal.InternalError) as got:
        call()
    assert got.value.kind == "precondition"
    return str(got.value)


# --- table ---


def test_the_bundled_choices_fit_the_settings_schema():
    """同梱の値は起動のたびに照らさないので、形が起動の設定のスキーマに合うことをここで確かめる。"""
    assert mc.bundled_errors() == []


def test_table_without_settings_is_the_bundled_choices(tmp_path):
    got = mc.table(_repo(tmp_path))
    assert got.enabled and got.arms["impl"]["claude"][0] == {} and len(got.arms["impl"]["claude"]) == 3
    assert got.allocation["window_days"] == 60


def test_table_replaces_only_the_written_stage_and_runtime(tmp_path):
    root = _repo(tmp_path, {"stages": {"review": {"arms": {"claude": [{"model": "opus"}]}}}, "allocation": {"window_days": 30}})
    got = mc.table(root)
    assert got.arms["review"]["claude"] == ({"model": "opus"},)
    assert len(got.arms["review"]["codex"]) == 3  # 書いていないランタイムは同梱のまま
    assert got.allocation["window_days"] == 30 and got.allocation["min_units"] == 10


@pytest.mark.parametrize(
    ("experiments", "where"),
    [
        ({"stages": {"impl": {"arms": {"claude": []}}}}, "arms/claude"),
        ({"stages": {"impl": {"arms": {"claude": [{}, {}]}}}}, "arms/claude/1"),
        ({"stages": {"impl": {"arms": {"claude": [{"effort": "turbo"}]}}}}, "arms/claude/0/effort"),
        ({"stages": {"impl": {"arms": {"claude": [{"model": "a\nb"}]}}}}, "arms/claude/0/model"),
        ({"stages": {"nope": {"arms": {"claude": [{}]}}}}, "stages/nope"),
        ({"stages": {"impl": {"arms": {"codex": [{}]}}}}, "arms/codex"),
        ({"allocation": {"floor": 0.5}}, "allocation/floor"),
        ({"allocation": {"floor": 0}}, "allocation/floor"),
        ({"allocation": {"min_units": 1}}, "allocation/min_units"),
        ({"allocation": {"window_days": 366}}, "allocation/window_days"),
        ({"allocation": {"margin": 1.5}}, "allocation/margin"),
    ],
)
def test_table_stops_on_a_broken_value_with_where_to_fix(tmp_path, experiments, where):
    root = _repo(tmp_path, experiments)
    detail = _stopped(lambda: mc.table(root))
    assert where in detail and str(root / ".ndf" / "launch.json") in detail
    assert "turbo" not in detail  # 誤った値は載せない（誤って入れた秘密を標準エラーへ漏らさない）


def test_given_settings_are_used_without_reading_the_file_again(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"stages": {"delegate": {"arms": {"codex": [{"effort": "high"}]}}}})
    settings = mc.launch_settings.load(root)
    monkeypatch.setattr(mc.launch_settings, "load", lambda *a, **kw: pytest.fail("読み直した"))
    assert mc.table(root, settings=settings).arms["delegate"]["codex"] == ({"effort": "high"},)
    assert _pick(root, "seat", "delegate", "codex", settings=settings).cli_args() == ["-c", "model_reasoning_effort=high"]


def test_a_broken_unused_value_still_stops_the_launch(tmp_path):
    """上位の指定で使わない工程の誤りでも止まる（I7）。"""
    root = _repo(tmp_path, {"stages": {"design": {"arms": {"claude": [{"effort": "turbo"}]}}}})
    _stopped(lambda: _pick(root, argument=("opus", "high")))


def test_a_settings_file_of_the_wrong_shape_stops(tmp_path):
    root = _repo(tmp_path, {"stages": {"impl": {"arms": {"claude": [{"speed": 1}]}}}})
    _stopped(lambda: mc.table(root))


# --- stage_of ---


@pytest.mark.parametrize(
    ("layer", "name", "stage"),
    [
        ("worker", "design-detail", "design"),
        ("skill", "requirements-design", "design"),
        ("worker", "impl", "impl"),
        ("worker", "ci-fix", "fix"),
        ("worker", "worktree", "mechanical"),
        ("judge", "ask", "judge"),
        ("seat", "review", "review"),
        ("seat", "delegate", "delegate"),
        ("seat", "fix", None),
        ("worker", "release", None),
    ],
)
def test_stage_of(layer, name, stage):
    assert mc.stage_of(layer, name) == stage


def test_stage_of_an_unknown_layer_stops():
    _stopped(lambda: mc.stage_of("lane", "impl"))


# --- raised ---


@pytest.mark.parametrize(
    ("runtime", "before", "after"),
    [
        ("claude", (None, "low"), (None, "medium")),
        ("claude", ("sonnet", "medium"), ("sonnet", "high")),
        ("claude", ("haiku", "high"), ("sonnet", "high")),
        ("claude", ("sonnet", None), ("opus", None)),
        ("claude", ("opus", "high"), ("opus", "xhigh")),
        ("claude", ("claude-opus-9", "high"), ("claude-opus-9", "xhigh")),
        ("claude", (None, "high"), (None, "xhigh")),
        ("claude", ("opus", "xhigh"), ("opus", "xhigh")),
        ("claude", ("opus", "max"), ("opus", "max")),
        ("claude", (None, None), (None, None)),
        ("codex", (None, "medium"), (None, "high")),
        ("codex", ("gpt-9", "high"), ("gpt-9", "xhigh")),
        ("codex", (None, "xhigh"), (None, "xhigh")),
        ("codex", (None, None), (None, None)),
    ],
)
def test_raised(runtime, before, after):
    assert mc.raised(runtime, *before) == after


# --- pick ---


def test_a_launch_without_any_choice_adds_no_arguments(tmp_path):
    got = _pick(_repo(tmp_path))
    assert (got.model, got.effort, got.model_source, got.effort_source) == (None, None, mc.Source.DEFAULT, mc.Source.DEFAULT)
    assert got.cli_args() == [] and got.env_changes() == {} and got.assign_id is None


def test_a_fixed_choice_launches_with_its_values(tmp_path):
    """選択肢を 1 つにすると、その値に固定する（受け入れ条件 7・19）。"""
    root = _repo(tmp_path, {"stages": {"design": {"arms": {"claude": [{"model": "opus", "effort": "high"}]}}}})
    got = _pick(root, name="design-detail", unit="run-1:design")
    assert (got.model_source, got.effort_source, got.stage) == (mc.Source.BASE, mc.Source.BASE, "design")
    assert got.cli_args() == ["--model", "opus", "--effort", "high"]


def test_a_stopped_experiment_uses_the_base_choice(tmp_path):
    root = _repo(tmp_path, {"enabled": False, "stages": {"impl": {"arms": {"claude": [{"model": "sonnet"}, {"model": "opus"}]}}}})
    got = _pick(root, unit="run-1:impl")
    assert (got.model, got.effort, got.model_source, got.effort_source) == ("sonnet", None, mc.Source.BASE, mc.Source.DEFAULT)


def test_an_argument_on_one_side_takes_the_base_value_on_the_other(tmp_path):
    root = _repo(tmp_path, {"stages": {"impl": {"arms": {"claude": [{"model": "sonnet", "effort": "medium"}, {}]}}}})
    got = _pick(root, argument=(None, "high"), environ={MODEL_ENV: "opus", INHERITED_ENV: "low"})
    assert (got.model, got.model_source, got.effort, got.effort_source) == ("opus", mc.Source.ENVIRONMENT, "high", mc.Source.ARGUMENT)
    assert got.env_changes() == {INHERITED_ENV: None}  # NDF が effort を渡すので、子の中で優先する変数を外す


def test_the_argument_comes_before_the_environment(tmp_path):
    got = _pick(_repo(tmp_path), argument=("haiku", None), environ={MODEL_ENV: "opus", EFFORT_ENV: "low"})
    assert (got.model, got.model_source, got.effort, got.effort_source) == ("haiku", mc.Source.ARGUMENT, "low", mc.Source.ENVIRONMENT)


def test_the_runtimes_own_effort_variable_is_inherited_and_not_passed(tmp_path):
    root = _repo(tmp_path, {"stages": {"impl": {"arms": {"claude": [{"model": "sonnet", "effort": "medium"}]}}}})
    got = _pick(root, environ={INHERITED_ENV: "whatever"})  # 値は検証しない
    assert (got.effort, got.effort_source, got.model_source) == (None, mc.Source.ENVIRONMENT, mc.Source.BASE)
    assert got.cli_args() == ["--model", "sonnet"] and got.env_changes() == {INHERITED_ENV: "whatever"}


def test_codex_takes_its_own_variables_and_argument_form(tmp_path):
    root = _repo(tmp_path)
    got = _pick(root, "seat", "review", "codex", environ={EFFORT_ENV: "max"})  # claude の変数は効かない
    assert (got.effort, got.effort_source) == ("low", mc.Source.BASE) and got.cli_args() == ["-c", "model_reasoning_effort=low"]
    got = _pick(root, "seat", "review", "codex", argument=("gpt-9", None), environ={CODEX_EFFORT_ENV: "high"})
    assert got.cli_args() == ["-c", "model_reasoning_effort=high", "-m", "gpt-9"] and got.env_changes() == {}


def test_a_model_name_with_a_backslash_is_passed_as_written(tmp_path):
    """受理したモデル名は置換の構文として解釈せず、そのまま 1 語で渡す。"""
    root = _repo(tmp_path)
    assert _pick(root, argument=(r"a\1\nb", None)).cli_args() == ["--model", r"a\1\nb"]
    assert _pick(root, "seat", "review", "codex", argument=(r"g\g<0>", None)).cli_args()[-2:] == ["-m", r"g\g<0>"]


def test_a_wrong_lower_value_does_not_stop_when_a_higher_one_is_given(tmp_path):
    """引数が上書きする環境変数の値は使わないので、誤っていても止めない。"""
    got = _pick(_repo(tmp_path), argument=("opus", "high"), environ={MODEL_ENV: "a\nb", EFFORT_ENV: "turbo"})
    assert (got.model, got.effort) == ("opus", "high")


def test_a_launch_without_a_stage_takes_only_the_manual_values(tmp_path):
    root = _repo(tmp_path)
    assert _pick(root, "seat", "fix").cli_args() == []
    assert _pick(root, "seat", "fix", environ={EFFORT_ENV: "high"}).cli_args() == ["--effort", "high"]


def test_a_retry_raises_the_chosen_values_once(tmp_path):
    root = _repo(tmp_path, {"stages": {"fix": {"arms": {"claude": [{"model": "sonnet", "effort": "medium"}]}}}})
    got = _pick(root, name="fix", retry="test_failed")
    assert (got.model, got.effort, got.raised, got.effort_source) == ("sonnet", "high", True, mc.Source.BASE)
    assert got.row_fields()["raised"] is True


def test_a_retry_does_not_raise_manual_values_or_the_top(tmp_path):
    root = _repo(tmp_path, {"stages": {"fix": {"arms": {"claude": [{"model": "opus", "effort": "max"}]}}}})
    assert not _pick(root, name="fix", retry="test_failed").raised
    got = _pick(root, name="fix", retry="test_failed", argument=(None, "low"))
    assert (got.effort, got.raised) == ("low", False)


@pytest.mark.parametrize(
    "kw",
    [
        {"retry": "flaky"},
        {"argument": (None, "turbo")},
        {"argument": ("a\nb", None)},
        {"environ": {EFFORT_ENV: "turbo"}},
        {"runtime": "kiro"},
    ],
)
def test_a_wrong_value_stops_before_the_launch(tmp_path, kw):
    root = _repo(tmp_path)
    assert "turbo" not in _stopped(lambda: _pick(root, **kw))


def test_row_fields_hold_only_present_values(tmp_path, monkeypatch):
    """割り付けを受けていない起動は、単位を渡されても使用量ログの欄に unit を置かない。"""
    monkeypatch.setattr(mc.launch_approval, "repo_key", str)  # 単位のある起動はリポジトリのキーを求める。git を起動しない
    got = _pick(_repo(tmp_path, {"enabled": False}), mode="light", unit="run-1:impl")
    assert got.row_fields() == {
        "layer": "worker",
        "name": "impl",
        "runtime": "claude",
        "mode": "light",
        "model_source": mc.Source.DEFAULT,
        "effort_source": mc.Source.DEFAULT,
        "raised": False,
    }


def test_row_fields_fit_the_usage_log_row(tmp_path):
    """使用量ログの行の形（usage.schema.json）に、選んだ値の欄がそのまま入る。"""
    from ndf.contracts import internal as rows

    root = _repo(tmp_path, {"stages": {"fix": {"arms": {"claude": [{"model": "sonnet", "effort": "medium"}]}}}})
    fields = _pick(root, name="fix", retry="test_failed", mode="standard").row_fields()
    assert rows.write_record("usage", {"caller": "claude-p", "usage": "unknown", **fields})["raised"] is True


def test_codex_reads_the_process_environment_without_an_inherited_variable(tmp_path, monkeypatch):
    """環境変数を渡さない起動（os.environ を読む）でも、自身の effort の変数を持たないランタイムは止まらない。"""
    monkeypatch.delenv(CODEX_EFFORT_ENV, raising=False)
    got = mc.launch_choice("seat", "review", "codex", cwd=_repo(tmp_path))
    assert got.effort == "low" and got.env_changes() == {}
