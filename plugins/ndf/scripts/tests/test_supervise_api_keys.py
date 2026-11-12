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
import claude_settings  # noqa: E402
import usage_ledger  # noqa: E402
from account_fake import accounts  # noqa: E402,F401
from test_supervise import LIMIT, LIMIT_NO_TIME, OK, SEQ_CLAUDE, progress_rows, run_plan, seq  # noqa: E402,F401

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
    """受け入れ条件 9・10（#2930 の受け入れ条件 10）: 残高切れならその呼び出しでだけキーを外して次のキー、無ければ
    サブスクリプションのログインで起動し直し、ステップは通る。次の呼び出しではまた同じキーが候補に入る。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("a", KEY_A, "gmail"), ("b", KEY_B, "main"))
    set_responses(CREDIT, BAD_KEY, OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text
    assert [c["api_key"] for c in calls()] == [KEY_A, KEY_B, None]
    assert not (accounts.root / "api-key-state.json").exists()
    rows = progress_rows(tmp_path, "api_key")
    assert [(r["name"], r["reason"]) for r in rows] == [("a", "credit_low"), ("b", "auth_failed")]
    assert KEY_A not in state_text(tmp_path) and KEY_B not in state_text(tmp_path)
    # 次の呼び出しでは外したキーがまた候補に入る（残高切れを内部に記録しない）
    set_responses(OK)
    (tmp_path / "seq" / "calls").unlink()
    run_plan(again(tmp_path), STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A]


def test_limit_on_a_key_drops_it_only_for_this_call(tmp_path, seq, accounts):  # noqa: F811
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("a", KEY_A, "main"))
    set_responses(LIMIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A, None] and "結果: 完了" in text


def test_near_limit_uses_the_key_only_when_weekly_is_over_the_threshold(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """#2930 の受け入れ条件 2・8: 週の使用率が T 未満ならキーを使わず、T 以上でだけ使う。キーで起動した帳簿の行にだけ
    キーの名前が載り、値は載らない。"""
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))
    set_responses, calls = setup(tmp_path, seq, accounts, "near-limit", section="main", monkeypatch=monkeypatch)
    monkeypatch.setenv("NDF_ACCOUNT_CHECK_INTERVAL", "0")  # 2 回目の起動で使用量を読み直す
    accounts.add("main", util5=95, util7=50)  # 5 時間の使用率が高くても週が T 未満なら使わない
    ak.add_key("k", KEY_A, "main", monthly_usd=200.0)
    set_responses(OK)
    run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [None]
    accounts.add("main", token="main-hot-SECRET", util5=10, util7=90)
    (tmp_path / "seq" / "calls").unlink()
    run_plan(again(tmp_path), STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A]
    rows = usage_ledger.read_usage_ledger("claude", tmp_path / "usage")
    assert [r.get("api_key") for r in rows] == [None, "k"]
    text = "".join(p.read_text() for p in (tmp_path / "usage").rglob("*.jsonl"))
    assert KEY_A not in text


def test_near_limit_waits_on_a_five_hour_limit(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """#2930 の受け入れ条件 1: 5 時間の上限だけに当たり週の使用率が T 未満なら、キーで起動せず解除を待つ。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "near-limit", section="main", monkeypatch=monkeypatch)
    accounts.add("main", util5=10, util7=10)
    ak.add_key("k", KEY_A, "main", monthly_usd=200.0)
    set_responses(LIMIT_NO_TIME, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [None, None] and "結果: 完了" in text
    assert "limit_waited" in s.state.results["w"]


def test_broken_book_runs_without_keys(tmp_path, seq, accounts):  # noqa: F811
    set_responses, calls = setup(tmp_path, seq, accounts, None)
    (accounts.root / ak.BOOK_FILE).write_text("{broken")
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text and [c["api_key"] for c in calls()] == [None]
    assert len(progress_rows(tmp_path, "api_keys_invalid")) == 1


def test_nested_supervise_records_the_inherited_key(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """入れ子の起動: 受け継いだキーが残高切れなら進捗ログに 1 行残し、キーを外した環境で起動し直す。"""
    set_responses, calls = setup(tmp_path, seq, accounts, None, ("k", KEY_A, "main"))
    k = ak.get("k")
    monkeypatch.setenv(ak.KEY_ENV, KEY_A)
    monkeypatch.setenv(ak.NAME_ENV, "k")
    monkeypatch.setenv(ak.GENERATION_ENV, k.generation)
    set_responses(CREDIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert [c["api_key"] for c in calls()] == [KEY_A, None] and "結果: 完了" in text
    assert [(r["name"], r["reason"]) for r in progress_rows(tmp_path, "api_key")] == [("k", "credit_low")]


def test_nested_always_drops_the_inherited_key_and_returns_to_an_account(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """宣言が always の入れ子: 登録簿から選んだ同じ名前のキーが残高切れでも、受け継いだキーを外し、登録済みアカウントで起動し直す。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("k", KEY_A, "main"))
    k = ak.get("k")
    monkeypatch.setenv(ak.KEY_ENV, KEY_A)
    monkeypatch.setenv(ak.NAME_ENV, "k")
    monkeypatch.setenv(ak.GENERATION_ENV, k.generation)
    monkeypatch.setenv(ak.BACK_ENV, "main")
    set_responses(CREDIT, OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text
    assert [(c["api_key"], c["account"]) for c in calls()] == [(KEY_A, None), (None, "main")]


def test_nested_drop_switches_to_the_back_account_and_reassigns_on_auth(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """キーを外した入れ子は戻り先を今のアカウントとして持ち、そのログインが通らなければ別のアカウントへ振り替える。"""
    set_responses, calls = setup(tmp_path, seq, accounts, None, ("k", KEY_A, "a"))
    accounts.add("b")
    k = ak.get("k")
    for name, value in ((ak.KEY_ENV, KEY_A), (ak.NAME_ENV, "k"), (ak.GENERATION_ENV, k.generation), (ak.BACK_ENV, "a")):
        monkeypatch.setenv(name, value)
    set_responses(CREDIT, BAD_KEY, OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text
    assert [(c["api_key"], c["account"]) for c in calls()] == [(KEY_A, None), (None, "a"), (None, "b")]


def test_key_is_not_sent_to_a_foreign_base_url(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """継承した ANTHROPIC_BASE_URL が Anthropic の外を指すなら、登録済みのキーを使わずに今の認証で起動する。"""
    assert ak.sends_to_anthropic({}) and ak.sends_to_anthropic({ak.BASE_URL_ENV: "https://api.anthropic.com"})
    for url in ("https://gateway.example.com", "http://api.anthropic.com", "https://api.anthropic.com.example.com", "::bad"):
        assert not ak.sends_to_anthropic({ak.BASE_URL_ENV: url}), url
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("k", KEY_A, "main"))
    monkeypatch.setenv(ak.BASE_URL_ENV, "https://gateway.example.com")
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text and [c["api_key"] for c in calls()] == [None]


def test_inherited_key_is_not_sent_to_a_foreign_base_url(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """入れ子でも送り先が Anthropic の外なら、受け継いだキーを外し、戻り先のログインで起動する。"""
    set_responses, calls = setup(tmp_path, seq, accounts, "always", ("k", KEY_A, "main"))
    k = ak.get("k")
    for name, value in ((ak.KEY_ENV, KEY_A), (ak.NAME_ENV, "k"), (ak.GENERATION_ENV, k.generation), (ak.BACK_ENV, "main")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(ak.BASE_URL_ENV, "https://gateway.example.com")
    set_responses(OK)
    s, text = run_plan(tmp_path, STEP)
    assert "結果: 完了" in text
    assert [(c["api_key"], c["account"]) for c in calls()] == [(None, "main")]


def test_key_failure_is_excluded_only_for_the_call(accounts, monkeypatch):  # noqa: F811
    """#2930 の決定 2: 認証の失敗はその呼び出しの除外へ足し、進捗ログに 1 行残すだけで、状態のファイルを書かない。"""
    from supervise_lib import claude as sc

    accounts.add("main")
    ak.add_key("k", KEY_A, "main")
    rows: list[dict] = []
    ctx = SimpleNamespace(state=SimpleNamespace(cur={"id": "s1"}, switched=[], progress_write=rows.append))
    r = sc.ClaudeRunner(ctx)
    tried: set[str] = set()
    assert r.keys.result(ak.get("k"), {"ok": False, "auth": True}, tried) is True
    assert tried == {"k"} and not (accounts.root / "api-key-state.json").exists()
    assert rows == [{"kind": "api_key", "step": "s1", "name": "k", "reason": "auth_failed"}]
    assert json.dumps(rows).count("SECRET") == 0


def test_key_is_not_sent_when_settings_point_elsewhere(tmp_path, monkeypatch):
    """子が読む設定のファイルの env にある送り先（起動の環境より優先される）も確かめる。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(claude_settings, "MANAGED_SETTINGS", (str(tmp_path / "managed" / "managed-settings.json"),))
    proj = tmp_path / "repo" / "sub"
    proj.mkdir(parents=True)

    def put(path: Path, url: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"env": {ak.BASE_URL_ENV: url}}))

    assert ak.sends_to_anthropic({}, str(proj))
    put(tmp_path / "repo" / ".claude" / "settings.json", "https://api.anthropic.com")
    assert ak.sends_to_anthropic({}, str(proj))
    for path in (
        tmp_path / "repo" / ".claude" / "settings.local.json",
        tmp_path / "conf" / "settings.json",
        tmp_path / "managed" / "managed-settings.d" / "10.json",
    ):
        put(path, "https://gateway.example.com")
        assert not ak.sends_to_anthropic({"CLAUDE_CONFIG_DIR": str(tmp_path / "conf")}, str(proj)), path
        path.unlink()
    (tmp_path / "repo" / ".claude" / "settings.local.json").write_text("{broken")  # claude -p も読み飛ばす
    assert ak.sends_to_anthropic({}, str(proj))


def test_decision_is_recorded_only_when_it_changes(tmp_path, seq, accounts, monkeypatch):  # noqa: F811
    """#2930 の受け入れ条件 23: キーへ移ったときと規則 3〜5 に当たったときに、規則の名前と根拠の値を 1 行ずつ残す。
    同じ決定が続く呼び出しでは増やさない。"""
    steps = [{"id": "w1", "type": "work", "prompt": "直す", "next": "w2"}, {"id": "w2", "type": "work", "prompt": "直す", "next": "end"}]
    set_responses, calls = setup(tmp_path, seq, accounts, "near-limit", section="main", monkeypatch=monkeypatch)
    accounts.add("main", util5=10, util7=90)
    ak.add_key("k", KEY_A, "main", monthly_usd=200.0)
    set_responses(OK, OK)
    run_plan(tmp_path, steps)
    rows = [r for r in progress_rows(tmp_path, "account") if r.get("to_key")]
    assert len(rows) == 1 and rows[0]["rule"] == "near_limit_key" and rows[0]["evidence"]["monthly_usd"] == 200.0
    ak.set_monthly("k", None)
    (tmp_path / "seq" / "calls").unlink()
    d = again(tmp_path)
    run_plan(d, steps)
    assert [c["api_key"] for c in calls()] == [None, None]
    rules = [r for r in progress_rows(d, "account") if r.get("reason") == "launch_rule"]
    assert [r["rule"] for r in rules] == ["near_limit_no_declared_key"] and KEY_A not in json.dumps(rules)
