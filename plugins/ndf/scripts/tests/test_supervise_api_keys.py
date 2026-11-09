"""supervise.py の起動口が登録済み API キーで worker の claude -p を起動する（#2855）。

偽の claude（`test_supervise.SEQ_CLAUDE` に API キーの変数の記録を足したもの）が、残高切れ・認証の失敗・上限の応答を返す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as ca  # noqa: E402
import claude_api_keys as ak  # noqa: E402
from account_fake import accounts  # noqa: E402,F401
from test_supervise import LIMIT, OK, SEQ_CLAUDE, progress_rows, run_plan, seq  # noqa: E402,F401

KEY_A = "sk-ant-api03-FAKE-WORKER-A-SECRET"
KEY_B = "sk-ant-api03-FAKE-WORKER-B-SECRET"
CREDIT = {
    "out": {"result": "Credit balance too low · Add funds: https://console.anthropic.com/settings/billing", "is_error": True},
    "code": 1,
}
BAD_KEY = {"out": {"result": "Invalid API key · Fix external API key", "is_error": True, "api_error_status": 401}, "code": 1}
STEP = [{"id": "w", "type": "work", "prompt": "直す", "next": "end"}]
RECORD = '"api_key": os.environ.get("ANTHROPIC_API_KEY"), "key_name": os.environ.get("NDF_CLAUDE_API_KEY_NAME"), "argv": sys.argv[1:]'


def setup(tmp_path, seq, accounts, use, *keys, section=None, monkeypatch=None):  # noqa: F811
    """偽の claude を API キーの変数も記録する形へ差し替え、アカウントとキーを登録して宣言を置く。"""
    (tmp_path / "seq" / "claude.py").write_text(SEQ_CLAUDE.replace('"argv": sys.argv[1:]', RECORD))
    for name, value, account in keys:
        if account not in ca.names():
            accounts.add(account)
        ak.add_key(name, value, account)
    if use:
        ak.set_use(use)
    if section and monkeypatch:
        monkeypatch.setenv(ca.NAME_ENV, section)
    return seq


def again(tmp_path) -> Path:
    """2 回目のプランの作業場所（1 回目の状態から再開させない）。"""
    d = tmp_path / "again"
    d.mkdir()
    return d


def state_text(tmp_path) -> str:
    return "".join(p.read_text(errors="replace") for p in (tmp_path / "state").rglob("*") if p.is_file())


def test_off_keeps_the_launch_unchanged(tmp_path, seq, accounts):  # noqa: F811
    """受け入れ条件 7: 宣言が off（既定）ならキーを登録していても子の環境にキーは入らない。"""
    set_responses, calls = setup(tmp_path, seq, accounts, None, ("k", KEY_A, "main"))
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text and [c["api_key"] for c in calls()] == [None]
    assert progress_rows(tmp_path, "account") == []


def test_always_puts_one_key_and_drops_other_auth(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """受け入れ条件 5: always なら子の環境に API キーが 1 本入り、OAuth のトークンと AUTH_TOKEN は無い。値は記録に出ない。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("k", KEY_A, "main"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-SECRET")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-SECRET")
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    c = calls()[0]
    assert (c["api_key"], c["key_name"], c["token"], c["auth_token"]) == (KEY_A, "k", None, None)
    assert s.state.results["w"]["auth"] == "API キー k"
    assert progress_rows(tmp_path, "account")[0]["to_key"] == "k"
    assert KEY_A not in state_text(tmp_path) and KEY_A not in text


def test_credit_low_retries_next_key_then_login(tmp_path, seq, accounts):  # noqa: F811
    """受け入れ条件 9・10: 残高切れなら状態を記録して次のキー、無ければサブスクリプションのログインで起動し直し、ステップは通る。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("a", KEY_A, "gmail"), ("b", KEY_B, "main"))
    set_responses(CREDIT, BAD_KEY, OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text
    assert [c["api_key"] for c in calls()] == [KEY_A, KEY_B, None]
    assert ak.state_of(ak.get("a")).state == ak.CREDIT_LOW and ak.state_of(ak.get("b")).state == ak.AUTH_FAILED
    rows = progress_rows(tmp_path, "api_key")
    assert [(r["name"], r["reason"]) for r in rows] == [("a", "credit_low"), ("b", "auth_failed")]
    assert KEY_A not in state_text(tmp_path) and KEY_B not in state_text(tmp_path)
    # 次の呼び出しでは記録した 2 本を選ばない
    set_responses(OK)
    (tmp_path / "seq" / "calls").unlink()
    run_plan(again(tmp_path), STEP)
    assert [c["api_key"] for c in calls()] == [None]


def test_limit_on_a_key_drops_it_only_for_this_call(tmp_path, seq, accounts):  # noqa: F811
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("a", KEY_A, "main"))
    set_responses(LIMIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A, None] and "結果: 完了" in text
    assert ak.state_of(ak.get("a")).state == ak.OK


def test_near_limit_uses_the_key_only_above_the_threshold(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    set_responses, calls = setup(tmp_path, seq, accounts, "near-limit", section="main", monkeypatch=monkeypatch)
    monkeypatch.setenv("NDF_ACCOUNT_CHECK_INTERVAL", "0")  # 2 回目の起動で使用量を読み直す
    accounts.add("main", util5=50)
    ak.add_key("k", KEY_A, "main")
    set_responses(OK)
    run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [None]
    accounts.add("main", token="main-hot-SECRET", util5=95)
    (tmp_path / "seq" / "calls").unlink()
    run_plan(again(tmp_path), STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A]


def test_near_limit_after_a_limit_without_another_account(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """near-limit の条件 3: 上限に当たって替えるアカウントが無ければ、待つ前に API キーで起動し直す。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "near-limit", section="main", monkeypatch=monkeypatch)
    accounts.add("main", util5=10)
    ak.add_key("k", KEY_A, "main")
    set_responses(LIMIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [None, KEY_A] and "結果: 完了" in text
    assert "limit_waited" not in s.state.results["w"]


def test_broken_book_runs_without_keys(tmp_path, seq, accounts):  # noqa: F811
    set_responses, calls = setup(tmp_path, seq, accounts, None)
    (accounts.root / ak.BOOK_FILE).write_text("{broken")
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text and [c["api_key"] for c in calls()] == [None]
    assert len(progress_rows(tmp_path, "api_keys_invalid")) == 1


def test_nested_supervise_records_the_inherited_key(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """入れ子の起動: 受け継いだキーが残高切れなら状態を記録し、キーを外した環境で起動し直す。"""
    set_responses, calls = setup(tmp_path, seq, accounts, None, ("k", KEY_A, "main"))
    k = ak.get("k")
    monkeypatch.setenv(ak.KEY_ENV, KEY_A)
    monkeypatch.setenv(ak.NAME_ENV, "k")
    monkeypatch.setenv(ak.GENERATION_ENV, k.generation)
    set_responses(CREDIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A, None] and "結果: 完了" in text
    assert ak.state_of(ak.get("k")).state == ak.CREDIT_LOW


def test_failure_of_a_replaced_key_is_not_recorded(accounts, monkeypatch):  # noqa: F811
    """I10: 置き換える前の世代で起動した worker の 401 は、置き換えた後のキーの状態に書かない。"""
    from supervise_lib import claude as sc

    accounts.add("main")
    ak.add_key("k", KEY_A, "main")
    old = ak.get("k")
    ak.add_key("k", KEY_B, "main")
    rows: list[dict] = []
    ctx = SimpleNamespace(state=SimpleNamespace(cur={"id": "s1"}, switched=[], progress_write=rows.append))
    r = sc.ClaudeRunner(ctx)
    assert r.keys.result(old, {"ok": False, "auth": True}, set()) is True
    assert ak.state_of(ak.get("k")).state == ak.OK and ak.pick_key().name == "k"
    assert rows == [{"kind": "api_key", "step": "s1", "name": "k", "reason": "stale_generation"}]
    assert json.dumps(rows).count("SECRET") == 0
