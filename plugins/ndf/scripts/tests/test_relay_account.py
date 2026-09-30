"""`relay.py account` の引数化・Bedrock の登録・OAuth の 2 回の登録（#1468）。

`aws` と `claude` は偽物（`aws_fake.py`・`test_relay.AUTH_FAKE`）に差し替える。端末は擬似端末で見せかけ、
端末でない呼び出しは標準入力を `/dev/null` かパイプにする。
"""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
import time

import pytest
from account_fake import accounts  # noqa: F401
from aws_fake import FakeAws
from test_relay import AUTH_FAKE, RELAY, auth_calls, isolated_env  # noqa: F401

sys.path.insert(0, str(RELAY.parent / "lib"))
import claude_accounts as ca  # noqa: E402
import procs  # noqa: E402

MODEL = "us.anthropic.claude-sonnet-4-5-v1:0"
AWS_SECRETS = {
    "AWS_ACCESS_KEY_ID": "AKIA-KEY-SECRET",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-SECRET",
    "AWS_SESSION_TOKEN": "aws-session-SECRET",
}


@pytest.fixture()
def aws(tmp_path):
    return FakeAws(tmp_path)


@pytest.fixture(autouse=True)
def _stop_pending(accounts):  # noqa: F811
    """テストの後に待機中のログインを残さない。"""
    yield
    for e in os.listdir(accounts.root):
        if e.startswith(".pending-"):
            ca.discard_pending(e.removeprefix(".pending-"))


def run(tmp_path, accounts, aws, *args, tty=False, stdin=None, **env):  # noqa: F811
    fake = tmp_path / "auth_fake.py"
    fake.write_text(AUTH_FAKE)
    fake.chmod(0o755)
    e = isolated_env(
        tmp_path,
        **{
            "NDF_RELAY_CLAUDE": fake,
            "FAKE_AUTH_LOG": tmp_path / "auth.jsonl",
            "FAKE_EMAIL": "x@example.com",
            **accounts.env(),
            **aws.env(),
            **env,
        },
    )
    cmd = [sys.executable, str(RELAY), "account", *args]
    if not tty:
        kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        return subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=60, **kw)
    master, slave = pty.openpty()
    try:
        if stdin:
            os.write(master, stdin.encode())
        return subprocess.run(cmd, env=e, stdin=slave, capture_output=True, text=True, timeout=60)
    finally:
        os.close(master)
        os.close(slave)


def out_json(p):
    """標準出力が JSON 1 つだけであること（AC6）。"""
    return json.loads(p.stdout)


def metered_file(accounts):  # noqa: F811
    return accounts.root / "metered.json"


# ---------------------------------------------------------------- Bedrock の登録（AC1〜AC3）


def test_add_bedrock_interactive_then_list(tmp_path, accounts, aws):  # noqa: F811
    """AC1: 端末で引数なしに打ち、プロファイルとモデルを番号で選んで y を返すと登録され、一覧に従量の接続の行が出る。"""
    aws.set(profiles=["bedrock-dev", "default"], regions={"bedrock-dev": "us-west-2"}, models=["us.anthropic.claude-a", MODEL])
    p = run(tmp_path, accounts, aws, "add-bedrock", tty=True, stdin="1\n2\ny\n")
    assert p.returncode == 0, p.stderr
    assert f"登録した: 従量の接続（Bedrock・bedrock-dev・us-west-2・{MODEL}）" in p.stdout
    decl = json.loads(metered_file(accounts).read_text())
    assert decl["env"] == {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_PROFILE": "bedrock-dev",
        "AWS_REGION": "us-west-2",
        "ANTHROPIC_MODEL": MODEL,
    }
    assert oct(metered_file(accounts).stat().st_mode & 0o777) == "0o600"
    lines = run(tmp_path, accounts, aws, "list").stdout.splitlines()
    assert any(
        x.startswith("metered")
        and "bedrock-dev" in x
        and "us-west-2" in x
        and MODEL.removeprefix("us.anthropic.") in x
        and MODEL not in x
        and "保存した宣言" in x
        for x in lines
    )


def test_add_bedrock_single_profile_confirm(tmp_path, accounts, aws):  # noqa: F811
    """AC1: 候補が 1 つなら確認だけ（プロファイル・モデル・保存の 3 回の y）。"""
    p = run(tmp_path, accounts, aws, "add-bedrock", tty=True, stdin="y\ny\ny\n")
    assert p.returncode == 0, p.stderr
    assert json.loads(metered_file(accounts).read_text())["details"]["profile"] == "bedrock-dev"


def test_add_bedrock_by_args_without_terminal(tmp_path, accounts, aws):  # noqa: F811
    """AC2・AC6: 端末でなく引数が揃えば何も聞かずに登録し、--json は JSON 1 つを返す。"""
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes", "--json")
    assert p.returncode == 0, p.stderr
    got = out_json(p)
    assert got == {
        "ok": True,
        "command": "add-bedrock",
        "provider": "bedrock",
        "profile": "bedrock-dev",
        "region": "us-west-2",
        "model": MODEL,
        "replaced": False,
    }
    assert "?" not in p.stderr  # 問いを出していない
    assert not [c for c in aws.calls() if c["argv"][:2] == ["bedrock", "list-inference-profiles"]]  # --model があれば候補を読まない
    rows = out_json(run(tmp_path, accounts, aws, "list", "--json"))
    assert rows[-1]["name"] == "metered" and rows[-1]["kind"] == "metered" and rows[-1]["source"] == "saved"
    assert rows[-1]["details"] == {"profile": "bedrock-dev", "region": "us-west-2", "model": MODEL}


def test_add_bedrock_missing_args_without_terminal(tmp_path, accounts, aws):  # noqa: F811
    """AC6・I7: 端末でなければ待たずに、足りない引数の名前と候補を示して 2 で終わる。"""
    aws.set(profiles=["bedrock-dev", "default"], regions={}, models=[MODEL, "us.anthropic.claude-b"])
    p = run(tmp_path, accounts, aws, "add-bedrock", "--json")
    assert p.returncode == 2 and out_json(p)["missing"] == ["--profile"]
    assert out_json(p)["candidates"] == {"--profile": ["bedrock-dev", "default"]} and out_json(p)["reason"] == "missing_args"
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--json")
    assert p.returncode == 2 and out_json(p)["missing"] == ["--region"]
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--region", "us-east-1", "--json")
    assert p.returncode == 2 and out_json(p)["missing"] == ["--model"] and MODEL in out_json(p)["candidates"]["--model"]
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--region", "us-east-1", "--model", MODEL)
    assert p.returncode == 2 and "--yes" in p.stderr and p.stdout == ""
    assert not metered_file(accounts).exists()


@pytest.mark.parametrize(
    ("conf", "reason", "name"),
    [
        ({"sts_error": "An error occurred (ExpiredToken) when calling GetCallerIdentity: body-SECRET"}, "auth_expired", "ExpiredToken"),
        (
            {
                "converse_error": "An error occurred (AccessDeniedException) when calling Converse: User: arn:x is not authorized to perform: bedrock:InvokeModel body-SECRET"
            },
            "no_permission",
            "AccessDeniedException",
        ),
        (
            {
                "converse_error": "An error occurred (AccessDeniedException) when calling Converse: You don't have access to the model with the specified model ID. body-SECRET"
            },
            "model_unavailable",
            "AccessDeniedException",
        ),
        (
            {
                "converse_error": "An error occurred (ValidationException) when calling Converse: The provided model identifier is invalid. body-SECRET"
            },
            "model_unavailable",
            "ValidationException",
        ),
        (
            {"converse_error": 'Could not connect to the endpoint URL: "https://bedrock-runtime.xx.amazonaws.com/" body-SECRET'},
            "region_unavailable",
            "EndpointConnectionError",
        ),
        (
            {"converse_error": "An error occurred (ThrottlingException) when calling Converse: body-SECRET"},
            "unclassified",
            "ThrottlingException",
        ),
    ],
)
def test_add_bedrock_verify_failure_keeps_old(tmp_path, accounts, aws, conf, reason, name):  # noqa: F811
    """AC3・I2: 呼べなければ区分とエラーの種類の名前を出して 1 で終わり、前の宣言のバイト列は変わらない。本文は出さない。"""
    assert run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes").returncode == 0
    before = metered_file(accounts).read_bytes()
    aws.set(**conf)
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", "us.anthropic.claude-x", "--yes", "--json")
    assert p.returncode == 1 and out_json(p)["reason"] == reason and out_json(p)["aws_error"] == name
    assert metered_file(accounts).read_bytes() == before
    t = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", "us.anthropic.claude-x", "--yes")
    assert t.returncode == 1 and name in t.stderr
    assert "SECRET" not in p.stdout + p.stderr + t.stdout + t.stderr


def test_add_bedrock_replace_needs_yes(tmp_path, accounts, aws):  # noqa: F811
    """I3: 前の宣言があるとき、端末でなく --yes が無ければ 2。端末で n なら前の宣言が残る。--yes で置き換える。"""
    assert run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes").returncode == 0
    aws.set(profiles=["bedrock-dev", "other"], regions={"bedrock-dev": "us-west-2", "other": "eu-west-1"})
    before = metered_file(accounts).read_bytes()
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "other", "--model", MODEL, "--json")
    assert p.returncode == 2 and out_json(p)["missing"] == ["--yes"]
    assert run(tmp_path, accounts, aws, "add-bedrock", "--profile", "other", "--model", MODEL, tty=True, stdin="n\n").returncode == 2
    assert metered_file(accounts).read_bytes() == before
    p = run(tmp_path, accounts, aws, "add-bedrock", "--profile", "other", "--model", MODEL, "--yes", "--json")
    assert p.returncode == 0 and out_json(p)["replaced"] is True and out_json(p)["previous_profile"] == "bedrock-dev"
    assert json.loads(metered_file(accounts).read_text())["details"]["profile"] == "other"


def test_add_bedrock_without_aws_or_profiles(tmp_path, accounts, aws):  # noqa: F811
    """aws が無ければ aws_missing、プロファイルが 0 件なら no_profiles で 1。"""
    if os.path.exists("/usr/bin/aws") or os.path.exists("/bin/aws"):
        pytest.skip("/usr/bin か /bin に本物の aws がある")
    p = run(tmp_path, accounts, aws, "add-bedrock", "--json", PATH="/usr/bin:/bin")
    assert p.returncode == 1 and out_json(p)["reason"] == "aws_missing"
    aws.set(profiles=[])
    p = run(tmp_path, accounts, aws, "add-bedrock", "--json")
    assert p.returncode == 1 and out_json(p)["reason"] == "no_profiles"


def test_remove_and_check_metered(tmp_path, accounts, aws):  # noqa: F811
    """F2・F4: check metered は今の宣言で呼べるかを見る。remove metered は保存した宣言を消し、無ければ 1。"""
    p = run(tmp_path, accounts, aws, "check", "metered", "--json")
    assert p.returncode == 1 and out_json(p)["reason"] == "no_declaration"
    assert run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes").returncode == 0
    p = run(tmp_path, accounts, aws, "check", "metered", "--yes", "--json")
    assert p.returncode == 0 and out_json(p)["model"] == MODEL
    aws.set(sts_error="An error occurred (NoCredentials): Unable to locate credentials.")
    p = run(tmp_path, accounts, aws, "check", "metered")
    assert p.returncode == 1 and "認証切れ" in p.stderr and "NoCredentials" in p.stderr
    p = run(tmp_path, accounts, aws, "check", "metered", "--json", NDF_SUPERVISE_CLAUDE_FALLBACK="ANTHROPIC_API_KEY=k")
    assert p.returncode == 1 and out_json(p)["reason"] == "not_bedrock"
    assert run(tmp_path, accounts, aws, "check", "work1").returncode == 2
    p = run(tmp_path, accounts, aws, "remove", "metered", "--yes", "--json")
    assert p.returncode == 0 and out_json(p) == {"ok": True, "command": "remove", "name": "metered"} and not metered_file(accounts).exists()
    p = run(tmp_path, accounts, aws, "remove", "metered", "--json")
    assert p.returncode == 1 and out_json(p)["reason"] == "no_declaration"
    assert all(r["name"] != "metered" for r in out_json(run(tmp_path, accounts, aws, "list", "--json")))


def test_list_shows_env_and_broken_declarations(tmp_path, accounts, aws):  # noqa: F811
    """F3・I4・I5: 環境変数の宣言は値を出さず変数の名前だけ。保存先が壊れていれば壊れていると出す。"""
    metered_file(accounts).write_text("{not json")
    rows = out_json(run(tmp_path, accounts, aws, "list", "--json"))
    assert rows == [
        {
            "name": "metered",
            "kind": "metered",
            "source": "saved",
            "provider": None,
            "details": {},
            "state": "壊れている（宣言なしとして扱う）",
        }
    ]
    p = run(tmp_path, accounts, aws, "list", "--json", NDF_SUPERVISE_CLAUDE_FALLBACK="ANTHROPIC_API_KEY=sk-SECRET")
    assert out_json(p)[-1]["keys"] == ["ANTHROPIC_API_KEY"] and "SECRET" not in p.stdout
    lines = run(tmp_path, accounts, aws, "list", NDF_SUPERVISE_CLAUDE_FALLBACK="ANTHROPIC_API_KEY=sk-SECRET").stdout
    assert "環境変数（ANTHROPIC_API_KEY）" in lines and "SECRET" not in lines


def test_add_bedrock_allowed_on_macos(tmp_path, accounts, aws, monkeypatch, capsys):  # noqa: F811
    """決定 10: Bedrock の登録は macOS でも進む。"""
    from relay_lib import accounts as relay_accounts

    for k, v in aws.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(relay_accounts.sys, "platform", "darwin")
    assert relay_accounts.cmd_account(["add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------- OAuth の 2 回の登録（AC5）


def pending_of(accounts, name):  # noqa: F811
    return accounts.root / f".pending-{name}"


def test_add_in_two_calls(tmp_path, accounts, aws):  # noqa: F811
    """AC5・I10: 端末でない 1 回目が URL を出して 0、2 回目の --code - が登録して 0。途中の状態は残らない。"""
    p = run(tmp_path, accounts, aws, "add", "work2", "--json", FAKE_EMAIL="b@example.com")
    assert p.returncode == 0, p.stderr
    got = out_json(p)
    assert got["stage"] == "awaiting_code" and got["url"].startswith("https://claude.com/cai/oauth/authorize") and got["expires_at"]
    d = pending_of(accounts, "work2")
    assert oct(d.stat().st_mode & 0o777) == "0o700" and oct((d / "config").stat().st_mode & 0o777) == "0o700"
    for f in ("pending.json", "code.fifo", "login.out"):
        assert oct((d / f).stat().st_mode & 0o777) == "0o600", f
    assert ca.read_pending("work2").alive()
    p = run(tmp_path, accounts, aws, "add", "work2", "--code", "-", "--json", stdin="code-SECRET\n", FAKE_EMAIL="b@example.com")
    assert p.returncode == 0, p.stderr
    assert out_json(p) == {"ok": True, "command": "add", "name": "work2", "stage": "registered", "email": "b@example.com", "org_name": None}
    assert not d.exists() and accounts.account("work2")["email"] == "b@example.com"
    assert (accounts.root / "work2" / ".credentials.json").exists()


def test_add_second_call_failures(tmp_path, accounts, aws):  # noqa: F811
    """AC5・I8: 違うコードは bad_code、状態が無ければ no_pending。どれも状態を残さない。"""
    p = run(tmp_path, accounts, aws, "add", "work2", "--code", "-", "--json", stdin="code-SECRET\n")
    assert p.returncode == 1 and out_json(p)["reason"] == "no_pending"
    assert run(tmp_path, accounts, aws, "add", "work2").returncode == 0
    p = run(tmp_path, accounts, aws, "add", "work2", "--code", "wrong-code", "--json")
    assert p.returncode == 1 and out_json(p)["reason"] == "bad_code"
    assert not pending_of(accounts, "work2").exists() and not (accounts.root / "work2").exists()


def test_add_second_call_after_expiry(tmp_path, accounts, aws):  # noqa: F811
    """AC5・I8・E10: 期限を過ぎた 2 回目は expired（no_pending でない）で 1。状態と待機中のログインは残らない。"""
    assert run(tmp_path, accounts, aws, "add", "work2").returncode == 0
    pend = ca.read_pending("work2")
    path = pending_of(accounts, "work2") / "pending.json"
    row = json.loads(path.read_text())
    row["expires_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    p = run(tmp_path, accounts, aws, "add", "work2", "--code", "-", "--json", stdin="code-SECRET\n")
    assert p.returncode == 1 and out_json(p)["reason"] == "expired"
    assert not pending_of(accounts, "work2").exists()
    _gone(pend)


def test_sweep_on_any_account_call_and_restart(tmp_path, accounts, aws):  # noqa: F811
    """I8: 同じ名前の 1 回目を 2 度打つと前の待機中のログインが止まる。期限切れはどの account の呼び出しでも消える。"""
    assert run(tmp_path, accounts, aws, "add", "work2").returncode == 0
    first = ca.read_pending("work2")
    assert run(tmp_path, accounts, aws, "add", "work2").returncode == 0
    _gone(first)
    second = ca.read_pending("work2")
    assert second.pid != first.pid and second.alive()
    path = pending_of(accounts, "work2") / "pending.json"
    row = json.loads(path.read_text())
    row["expires_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    assert run(tmp_path, accounts, aws, "list").returncode == 0
    assert not pending_of(accounts, "work2").exists()
    _gone(second)


def _gone(p: ca.Pending) -> None:
    deadline = time.time() + 10
    while time.time() < deadline and p.alive():
        time.sleep(0.1)
    assert not p.alive()


def test_add_on_terminal_is_unchanged(tmp_path, accounts, aws):  # noqa: F811
    """AC10: 端末での add は今までどおり 1 回で登録し、2 回の登録へ進まない。"""
    p = run(tmp_path, accounts, aws, "add", "work1", tty=True, FAKE_EMAIL="a@example.com")
    assert p.returncode == 0 and "登録した: work1（a@example.com）" in p.stdout
    assert not pending_of(accounts, "work1").exists()
    p = run(tmp_path, accounts, aws, "add", "work3", "--json", tty=True, FAKE_EMAIL="c@example.com")
    assert p.returncode == 0 and out_json(p)["stage"] == "registered"


# ---------------------------------------------------------------- すべての副命令（AC6）と秘密（AC7）


def test_every_subcommand_takes_yes_and_json(tmp_path, accounts, aws):  # noqa: F811
    """AC6: 6 つの副命令が --yes を受け、--json で JSON 1 つを返し、端末でなく足りなければ名前を示して 2。"""
    accounts.add("work1")
    for args in (["add"], ["remove"], ["capacity"], ["capacity", "work1"]):
        p = run(tmp_path, accounts, aws, *args, "--yes", "--json")
        assert p.returncode == 2 and out_json(p)["reason"] == "missing_args" and out_json(p)["missing"], args
    p = run(tmp_path, accounts, aws, "capacity", "work1", "100", "-", "--yes", "--json")
    assert p.returncode == 0 and out_json(p)["capacity"]["five_hour"] == 100
    assert isinstance(out_json(run(tmp_path, accounts, aws, "list", "--yes", "--json")), list)
    p = run(tmp_path, accounts, aws, "remove", "work1", "--yes", "--json")
    assert p.returncode == 0 and out_json(p)["name"] == "work1"
    p = run(tmp_path, accounts, aws, "check", "metered", "--yes", "--json")
    assert p.returncode == 1 and out_json(p)["ok"] is False
    p = run(tmp_path, accounts, aws, "add", "Bad!", "--yes", "--json")
    assert p.returncode == 2 and out_json(p)["reason"] == "invalid_name"


def test_secrets_never_leak(tmp_path, accounts, aws):  # noqa: F811
    """AC7・I9・決定 14: 認可コード・AWS の鍵が画面・JSON・置き場のファイルに出ず、aws へ鍵の変数を渡さない。"""
    outs = []
    outs.append(run(tmp_path, accounts, aws, "add-bedrock", "--profile", "bedrock-dev", "--model", MODEL, "--yes", "--json", **AWS_SECRETS))
    outs.append(run(tmp_path, accounts, aws, "check", "metered", **AWS_SECRETS))
    outs.append(run(tmp_path, accounts, aws, "add", "work2", "--json", **AWS_SECRETS))
    login_out = (pending_of(accounts, "work2") / "login.out").read_text()
    outs.append(run(tmp_path, accounts, aws, "add", "work2", "--code", "-", "--json", stdin="code-SECRET\n", **AWS_SECRETS))
    outs.append(run(tmp_path, accounts, aws, "list", "--json", **AWS_SECRETS))
    outs.append(run(tmp_path, accounts, aws, "list", **AWS_SECRETS))
    assert all(p.returncode == 0 for p in outs), [p.stderr for p in outs]
    known = ["code-SECRET", "reply-body-SECRET", *AWS_SECRETS.values()]
    text = "".join(p.stdout + p.stderr for p in outs) + login_out + metered_file(accounts).read_text()
    assert not [k for k in known if k in text]
    for c in aws.calls():
        assert not any(c["keys"].values()), c


# ---------------------------------------------------------------- 宣言の読み先（AC4・I1・I4・I5・I6）


def test_fallback_env_reads_saved_and_env_wins(accounts, monkeypatch):  # noqa: F811
    """AC4・I4: 保存した宣言を読み、環境変数が定義されていれば（空でも）それだけを読む。"""
    ca.save_metered("bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p"}, {"profile": "p"})
    assert ca.fallback_env({}) == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p"}
    assert ca.fallback_env({ca.FALLBACK_ENV: "ANTHROPIC_API_KEY=k"}) == {"ANTHROPIC_API_KEY": "k"}
    assert ca.fallback_env({ca.FALLBACK_ENV: ""}) == {}


def test_metered_env_drops_aws_keys_only_for_saved(accounts):  # noqa: F811
    """AC4・決定 14: 保存した宣言の子からは AWS の鍵とトークンを外す。環境変数の宣言では鍵を外さない（今と同じ）。"""
    ca.save_metered("bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p"}, {"profile": "p"})
    base = {**AWS_SECRETS, "CLAUDE_CODE_OAUTH_TOKEN": "tok-SECRET"}
    env = ca.account_env(ca.METERED, base)
    assert env["AWS_PROFILE"] == "p" and not any(k in env for k in (*AWS_SECRETS, "CLAUDE_CODE_OAUTH_TOKEN"))
    env = ca.account_env(ca.METERED, {**base, ca.FALLBACK_ENV: "CLAUDE_CODE_USE_BEDROCK=1"})
    assert "AWS_PROFILE" not in env and env["AWS_ACCESS_KEY_ID"] == AWS_SECRETS["AWS_ACCESS_KEY_ID"]


def test_account_env_keeps_user_vars_with_saved_decl(accounts):  # noqa: F811
    """保存した宣言があっても、アカウントの子から利用者のシェルの AWS_PROFILE などを外さない（認証の変数だけ外す）。"""
    ca.save_metered("bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p", "ANTHROPIC_MODEL": "m"}, {"profile": "p"})
    tok = accounts.add("a")
    base = {"AWS_PROFILE": "mine", "ANTHROPIC_MODEL": "my-model", "CLAUDE_CODE_USE_BEDROCK": "1"}
    env = ca.account_env("a", base)
    assert env["AWS_PROFILE"] == "mine" and env["ANTHROPIC_MODEL"] == "my-model"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == tok and "CLAUDE_CODE_USE_BEDROCK" not in env


def test_account_env_from_metered_section_drops_saved_decl(accounts):  # noqa: F811
    """従量の接続の区間の中（base が保存した宣言の変数を持つ）からアカウントへ戻すと、宣言のキーを外す。"""
    decl = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p", "AWS_REGION": "r", "ANTHROPIC_MODEL": "m"}
    ca.save_metered("bedrock", decl, {"profile": "p"})
    tok = accounts.add("a")
    base = ca.account_env(ca.METERED, {"PATH": "/bin"})
    env = ca.account_env("a", base)
    assert not any(k in env for k in decl) and env["PATH"] == "/bin"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == tok and env[ca.NAME_ENV] == "a"


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        json.dumps({"version": 2, "provider": "bedrock", "env": {"A": "1"}, "details": {"p": "x"}}),
        json.dumps({"version": 1, "provider": "bedrock", "env": {"AWS_SECRET_ACCESS_KEY": "x"}, "details": {"p": "x"}}),
    ],
)
def test_broken_saved_declaration_is_ignored(accounts, body):  # noqa: F811
    """I1・I5: 読めない・知らない版・鍵の変数を含む保存先は宣言なしとして扱い、理由の 1 行を返す。"""
    (accounts.root / "metered.json").write_text(body)
    assert ca.fallback_env({}) == {} and ca.load_metered() is None
    assert "壊れている" in ca.metered_problem({})
    assert ca.metered_problem({ca.FALLBACK_ENV: ""}) is None


def test_save_metered_rejects_secret_keys_and_keeps_old(accounts, monkeypatch):  # noqa: F811
    """I1・I6: 鍵の変数を含む宣言は書かない。書き損じても前の宣言が残る。"""
    ca.save_metered("bedrock", {"AWS_PROFILE": "p"}, {"profile": "p"})
    before = (accounts.root / "metered.json").read_bytes()
    with pytest.raises(ValueError):
        ca.save_metered("bedrock", {"AWS_PROFILE": "q", "AWS_SESSION_TOKEN": "t"}, {"profile": "q"})
    monkeypatch.setattr(ca.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        ca.save_metered("bedrock", {"AWS_PROFILE": "q"}, {"profile": "q"})
    assert (accounts.root / "metered.json").read_bytes() == before


def test_relay_uses_saved_declaration_when_all_limited(accounts):  # noqa: F811
    """AC4: 保存した宣言があり、登録アカウントがすべて使えなければ、ラッパーは従量の接続の環境で起動する。"""
    from test_relay import _bare_relay

    accounts.add("a", util5=100)
    ca.save_metered("bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p"}, {"profile": "p"})
    to, reason, _, env = _bare_relay({**AWS_SECRETS}).replace_unusable("a")
    assert (to, reason, env["AWS_PROFILE"]) == ("metered", "auth", "p") and "AWS_ACCESS_KEY_ID" not in env
    with pytest.raises(Exception, match="従量の接続の宣言も無い"):
        _bare_relay({ca.FALLBACK_ENV: ""}).replace_unusable("a")


def test_pending_modes_and_start_time(accounts):  # noqa: F811
    """I10: 登録の途中の状態のディレクトリは 0700、ファイルと FIFO は 0600。pid の開始の時刻で生死を見る。"""
    d = ca.make_pending("w")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        p = ca.save_pending("w", child.pid)
        assert p.alive() and ca.read_pending("w").pid_start == procs.start_time(child.pid)
        assert oct(os.stat(d).st_mode & 0o777) == "0o700"
        assert oct(os.stat(os.path.join(d, "pending.json")).st_mode & 0o777) == "0o600"
        assert ca.sweep_pending(p.expires_at + 1) == {"w"} and not os.path.exists(d)
        assert not p.alive()  # 止めた（psutil が刈り取るため終了コードは見ない）
    finally:
        child.kill()
