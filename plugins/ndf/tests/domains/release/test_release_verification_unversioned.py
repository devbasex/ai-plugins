"""バージョン番号を持たないリポジトリのリリース後テストの記録を、届け先ごとに書く（#4580 の Task 7。`verification-record`）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import cli as release_cli
from ndf.domains.release import record, record_decide

from ._fakes import FakeHub, strategy

VALUE = "main abc1234"
X = "abc1234" + "0" * 33
WEB, WORKER = "web", "worker"
RECORD_PULL, WORK_PULL, PRODUCTION = 11, 12, 20


def _made(environments: tuple[str, ...], unit: str | None = None) -> record_decide.Record:
    """本番の PR 20 が作業の PR 11・12 を含めたリリースの、届け先 `environments` の反映の記録（対象は web と worker）。"""
    included = (RECORD_PULL, WORK_PULL, PRODUCTION)
    confirmation = {e: "cd" for e in environments}
    return record_decide.Record(VALUE, PRODUCTION, included, unit, environments, (WEB, WORKER), X, None, confirmation)


def _hub(*made: record_decide.Record, checks: tuple[dict, ...] = ()) -> FakeHub:
    """含めた PR の各々に同じリリース記録があり、記録の PR にはテストの記録もある GitHub。"""
    hub = FakeHub()
    for n in (RECORD_PULL, WORK_PULL, PRODUCTION):
        hub.comments_of[n] = [Authored("octocat", "OWNER", record_decide.render_record(m)) for m in made]
    hub.comments_of[RECORD_PULL] += [Authored("octocat", "OWNER", record_decide.render_verification(c)) for c in checks]
    return hub


def _check(environments: tuple[str, ...], result: str = "passed", issue: int = 7, retest: str | None = None, **more) -> dict:
    row = {"number": issue, "result": result, "evidence": "画面で確かめた"} | ({"retest_reason": retest} if retest else {})
    return {"version": VALUE, "environments": list(environments), "issues": [row], **more}


def _written(hub: FakeHub) -> list[dict]:
    return record_decide.verifications(c.body for c in hub.comments_of[RECORD_PULL])


def test_writes_a_check_of_reflected_targets_to_the_record_pull() -> None:
    # 受け入れ条件 9: 値と environments を持つテストの記録を、反映の記録がある届け先について記録の PR へ書ける
    hub = _hub(_made((WEB,)))
    record.write_verification(hub, RECORD_PULL, _check((WEB,)), unversioned=True)
    assert _written(hub) == [_check((WEB,))]


@pytest.mark.parametrize(
    "payload",
    [
        {"version": VALUE, "issues": [{"number": 7, "result": "passed", "evidence": "e"}]},  # environments が無い
        _check((WEB, WORKER)),  # worker は反映の記録が無い
        _check((WEB,), unit="b"),  # 単位 b のリリース記録が無い
        _check((WEB,)) | {"version": "main def5678"},  # その値のリリース記録が無い
    ],
)
def test_refuses_a_check_without_a_reflected_target(payload: dict) -> None:
    hub = _hub(_made((WEB,)))
    with pytest.raises(record.RecordError):
        record.write_verification(hub, RECORD_PULL, payload, unversioned=True)
    assert _written(hub) == []


def test_points_to_the_record_pull_from_another_included_pull() -> None:
    # 記録の PR（含めた PR のうち番号が最も小さいもの）でない PR を渡すと、書かずに記録の PR を返す
    hub = _hub(_made((WEB,)))
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(hub, PRODUCTION, _check((WEB,)), unversioned=True)
    assert caught.value.holders == (RECORD_PULL,) and caught.value.items == [{"kind": "pull", "number": RECORD_PULL}]
    assert all(record_decide.verifications(c.body for c in hub.comments_of[n]) == [] for n in (WORK_PULL, PRODUCTION))


def test_needs_the_unit_when_the_value_has_several_units() -> None:
    hub = _hub(_made((WEB,), unit="a"), _made((WEB,), unit="b"))
    with pytest.raises(record.RecordError):
        record.write_verification(hub, RECORD_PULL, _check((WEB,)), unversioned=True)
    record.write_verification(hub, RECORD_PULL, _check((WEB,), unit="a"), unversioned=True)
    assert _written(hub) == [_check((WEB,), unit="a")]


def test_a_retest_of_a_failed_pair_needs_a_reason() -> None:
    # 「判定に使う行」: 組の今の判定が不合格なら、新しい行に retest_reason が要る（書く側も同じ判定を使う）
    hub = _hub(_made((WEB, WORKER)), checks=(_check((WEB,), "failed"),))
    with pytest.raises(record.RetestWithoutReason) as caught:
        record.write_verification(hub, RECORD_PULL, _check((WEB, WORKER)), unversioned=True)
    assert caught.value.items == [{"kind": "delivery", "name": WEB, "number": 7, "result": "failed"}]
    # 別の届け先や別の課題の行は理由なしで書ける。理由を書けば不合格の組も書ける
    record.write_verification(hub, RECORD_PULL, _check((WORKER,)), unversioned=True)
    record.write_verification(hub, RECORD_PULL, _check((WEB,), issue=8), unversioned=True)
    record.write_verification(hub, RECORD_PULL, _check((WEB,), retest="検証環境の接続を直した"), unversioned=True)
    assert len(_written(hub)) == 4


def test_untrusted_token_writes_no_check() -> None:
    # 記録は信用する書き手（maintainer）のもので、書く token の書き手（偽の GitHub では octocat）は信用されない
    hub = _hub(_made((WEB,)))
    for comments in hub.comments_of.values():
        comments[:] = [c._replace(author="maintainer") for c in comments]
    hub.trusted = {"maintainer"}
    with pytest.raises(record.RecordError, match="token"):
        record.write_verification(hub, RECORD_PULL, _check((WEB,)), unversioned=True)
    assert _written(hub) == []


def _cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], hub: FakeHub, payload: dict
) -> tuple[int, dict]:
    path = tmp_path / "verification.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(release_cli, "_strategy", lambda args: strategy("web-trunk"))
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: hub)
    code = release_cli.main(["verification-record", "--root", str(tmp_path), "--pr", str(RECORD_PULL), "--record", str(path)])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_checks_the_targets_in_a_repository_without_version_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(_made((WEB,)), checks=(_check((WEB,), "failed"),))
    code, out = _cli(tmp_path, monkeypatch, capsys, hub, _check((WEB,)))
    assert code == 3 and out["cause"]["kind"] == "precondition" and out["items"][0]["name"] == WEB
    code, out = _cli(tmp_path, monkeypatch, capsys, hub, {"version": VALUE, "issues": [{"number": 7, "result": "passed", "evidence": "e"}]})
    assert code == 3 and out["cause"]["kind"] == "precondition"
    code, out = _cli(tmp_path, monkeypatch, capsys, hub, _check((WEB,), retest="検証環境の接続を直した"))
    assert code == 0 and out["status"] == "ok"
