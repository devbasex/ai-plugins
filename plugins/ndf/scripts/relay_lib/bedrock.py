"""`relay.py account add-bedrock` / `check metered` の aws の呼び出しと、呼べるかの確認の失敗の区分（#1468）。

aws の外の語（プロファイル・エラーの種類の名前）をこちらの語（従量の接続の宣言・失敗の区分）へ変える翻訳の層である。
aws へ渡すのはプロファイル名・地域・モデルの ID だけで、鍵は aws 自身が `~/.aws/` から読む。aws の出力のうち使うのは
エラーの種類の名前（`An error occurred (<名前>)` の括弧の中）と終了コードだけで、応答とエラーの本文は画面へ出さない（I9）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from .common import PKG_ROOT  # noqa: F401  lib/ を sys.path に置く

import claude_accounts as ca  # noqa: E402,I001

PROVIDER = "bedrock"
TIMEOUT = 60
ERROR_RE = re.compile(r"An error occurred \(([A-Za-z0-9_.]+)\)")
REASONS = {
    "auth_expired": "認証切れ",
    "no_permission": "権限が無い",
    "region_unavailable": "地域で使えない",
    "model_unavailable": "モデルが有効でない",
    "unclassified": "区分できない失敗",
}


@dataclass(frozen=True)
class Target:
    """呼ぶ先（プロファイル・地域・モデル）。宣言の変数の組・一覧の識別・結果の文の識別を作る。"""

    profile: str
    region: str
    model: str

    def details(self) -> dict:
        return {"profile": self.profile, "region": self.region, "model": self.model}

    def env(self) -> dict:
        """従量の接続の宣言の変数の組。"""
        return {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": self.profile, "AWS_REGION": self.region, "ANTHROPIC_MODEL": self.model}

    def label(self) -> str:
        return decl_label(self.details())


def from_env(env: dict) -> Target | None:
    """従量の接続の宣言の変数の組から呼ぶ先を読む（`Target.env` の逆）。Bedrock の宣言でなければ None。"""
    region, model = env.get("AWS_REGION", ""), env.get("ANTHROPIC_MODEL", "")
    if env.get("CLAUDE_CODE_USE_BEDROCK") in (None, "", "0") or not region or not model:
        return None
    return Target(env.get("AWS_PROFILE", "default"), region, model)


@dataclass
class VerifyFailure:
    """呼べるかの確認の失敗。`reason` は区分、`aws_error` は元の AWS のエラーの種類の名前（読めなければ空）。"""

    reason: str
    aws_error: str

    def text(self) -> str:
        return f"呼べない（{REASONS[self.reason]}・{self.aws_error or '不明'}）"


def aws_path() -> str | None:
    return shutil.which("aws")


def _aws_env(profile: str | None = None, region: str | None = None) -> dict:
    """子へ渡すのと同じ環境（AWS の鍵の変数を外し、プロファイルと地域を置く。決定 14）。プロファイルが無ければ鍵を外すだけ。"""
    env = {k: v for k, v in os.environ.items() if k not in ca.AWS_KEY_ENV}
    if not profile:
        return env
    env["AWS_PROFILE"] = profile
    if region:
        env["AWS_REGION"] = region
    return env


def _aws(args: list[str], profile: str | None = None, region: str | None = None) -> subprocess.CompletedProcess | None:
    aws = aws_path()
    if aws is None:
        return None
    env = _aws_env(profile, region)
    try:
        return subprocess.run([aws, *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "Timeout")
    except OSError:
        return None


def profiles() -> list[str]:
    """`aws configure list-profiles` の候補。読めなければ空。"""
    p = _aws(["configure", "list-profiles"])
    if p is None or p.returncode != 0:
        return []
    return [x.strip() for x in p.stdout.splitlines() if x.strip()]


def region(profile: str) -> str:
    """プロファイルの `region`。未設定なら空。"""
    p = _aws(["configure", "get", "region", "--profile", profile])
    return p.stdout.strip() if p is not None and p.returncode == 0 else ""


def models(profile: str, reg: str) -> list[str]:
    """推論プロファイル（SYSTEM_DEFINED）のうち ID に `anthropic.claude` を含むもの。読めなければ空。"""
    p = _aws(
        [
            "bedrock",
            "list-inference-profiles",
            "--type-equals",
            "SYSTEM_DEFINED",
            "--output",
            "json",
            "--profile",
            profile,
            "--region",
            reg,
        ],
        profile,
        reg,
    )
    if p is None or p.returncode != 0:
        return []
    try:
        rows = json.loads(p.stdout).get("inferenceProfileSummaries") or []
    except (ValueError, AttributeError):
        return []
    ids = [r.get("inferenceProfileId") for r in rows if isinstance(r, dict)]
    return sorted(i for i in ids if isinstance(i, str) and "anthropic.claude" in i)


def error_name(err: str) -> str:
    """aws の標準エラーからエラーの種類の名前。接続の失敗は `EndpointConnectionError`。読めなければ空。"""
    m = ERROR_RE.search(err)
    if m:
        return m.group(1)
    if "Could not connect to the endpoint URL" in err:
        return "EndpointConnectionError"
    if "Timeout" in err or "timed out" in err:
        return "Timeout"
    return ""


def classify_failure(err: str) -> VerifyFailure:
    """`bedrock-runtime converse` の失敗を区分へ振り分ける（設計の「失敗の 4 区分への振り分け」）。"""
    name = error_name(err)
    low = err.lower()
    if name == "EndpointConnectionError" or "not supported in this region" in low or "not available in this region" in low:
        return VerifyFailure("region_unavailable", name)
    if name in ("ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException"):
        return VerifyFailure("auth_expired", name)
    if name == "AccessDeniedException":
        if "not authorized to perform" in low or "explicit deny" in low:
            return VerifyFailure("no_permission", name)
        if "access to the model" in low or "model access" in low:
            return VerifyFailure("model_unavailable", name)
        return VerifyFailure("no_permission", name)
    if name in ("ValidationException", "ResourceNotFoundException") and ("model" in low or "identifier" in low):
        return VerifyFailure("model_unavailable", name)
    return VerifyFailure("unclassified", name)


def verify(t: Target) -> VerifyFailure | None:
    """認証（`sts get-caller-identity`）と 1 回の短い応答（`bedrock-runtime converse`・最大 1 トークン）を確かめる。"""
    profile, reg = t.profile, t.region
    p = _aws(["sts", "get-caller-identity", "--output", "json", "--profile", profile, "--region", reg], profile, reg)
    if p is None:
        return VerifyFailure("unclassified", "")
    if p.returncode != 0:
        return VerifyFailure("auth_expired", error_name(p.stderr))
    messages = json.dumps([{"role": "user", "content": [{"text": "ping"}]}])
    args = ["bedrock-runtime", "converse", "--model-id", t.model, "--messages", messages]
    args += ["--inference-config", json.dumps({"maxTokens": 1}), "--output", "json", "--profile", profile, "--region", reg]
    p = _aws(args, profile, reg)
    if p is None:
        return VerifyFailure("unclassified", "")
    return None if p.returncode == 0 else classify_failure(p.stderr)


def _decl_body(d: dict) -> str:
    return "・".join(d.get(k, "-") for k in ("profile", "region", "model"))


def decl_label(d: dict) -> str:
    """一覧と結果の文に出す識別（`Bedrock（<プロファイル>・<地域>・<モデル>）`）。"""
    return f"Bedrock（{_decl_body(d)}）"


def decl_short(d: dict) -> str:
    """表に出す短い識別（`Bedrock（[<プロファイル>・]<地域>・<モデルの短い名>）`）。プロファイルは default 以外のときだけ出す。"""
    model = re.sub(r"^(?:[a-z]{2,4}\.)?anthropic\.", "", d.get("model", "-"))
    parts = [d.get("region", "-"), model]
    if d.get("profile", "-") != "default":
        parts.insert(0, d.get("profile", "-"))
    return f"Bedrock（{'・'.join(parts)}）"


def decl_inline(d: dict) -> str:
    """括弧の中に入れる形の識別（`Bedrock・<プロファイル>・<地域>・<モデル>`）。"""
    return f"Bedrock・{_decl_body(d)}"
