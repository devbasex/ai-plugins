"""`relay.py account add-bedrock` / `check metered` のテストが使う偽の aws（#1468）。

`FAKE_AWS_CONF`（JSON）で振る舞いを決める: `profiles`（名前の並び）・`regions`（プロファイル → 地域）・`models`
（推論プロファイルの ID の並び）・`sts_error` / `converse_error`（標準エラーへ出す文。あれば終了コード 254）。
呼ばれるたびに引数と、AWS の鍵の変数が見えたかを `FAKE_AWS_LOG` へ 1 行残す。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MODEL = "us.anthropic.claude-sonnet-4-5-v1:0"  # 偽の aws が既定で返すモデルの ID

AWS_FAKE = """#!/usr/bin/env python3
import json, os, sys
conf = json.load(open(os.environ["FAKE_AWS_CONF"]))
a = sys.argv[1:]
seen = {k: k in os.environ for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")}
open(os.environ["FAKE_AWS_LOG"], "a").write(json.dumps({"argv": a, "keys": seen, "profile": os.environ.get("AWS_PROFILE")}) + "\\n")
def opt(k):
    return a[a.index(k) + 1] if k in a else None
if a[:2] == ["configure", "list-profiles"]:
    print("\\n".join(conf.get("profiles", [])))
    sys.exit(0)
if a[:3] == ["configure", "get", "region"]:
    r = conf.get("regions", {}).get(opt("--profile"))
    if r:
        print(r)
    sys.exit(0 if r else 1)
if a[:2] == ["bedrock", "list-inference-profiles"]:
    print(json.dumps({"inferenceProfileSummaries": [{"inferenceProfileId": m} for m in conf.get("models", [])]}))
    sys.exit(0)
if a[:2] == ["sts", "get-caller-identity"]:
    if conf.get("sts_error"):
        print(conf["sts_error"], file=sys.stderr)
        sys.exit(254)
    print(json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/x"}))
    sys.exit(0)
if a[:2] == ["bedrock-runtime", "converse"]:
    if conf.get("converse_error"):
        print(conf["converse_error"], file=sys.stderr)
        sys.exit(254)
    print(json.dumps({"output": {"message": {"content": [{"text": "reply-body-SECRET"}]}}}))
    sys.exit(0)
sys.exit(3)
"""


class FakeAws:
    """偽の aws を `bin/aws` に置き、設定と呼び出しの記録を持つ。"""

    def __init__(self, tmp_path: Path):
        self.bin = tmp_path / "fake-aws-bin"
        self.bin.mkdir(exist_ok=True)
        aws = self.bin / "aws"
        aws.write_text(AWS_FAKE)
        aws.chmod(0o755)
        self.conf_path = tmp_path / "fake-aws.json"
        self.log_path = tmp_path / "fake-aws.jsonl"
        self.conf: dict = {}
        self.set(profiles=["bedrock-dev"], regions={"bedrock-dev": "us-west-2"}, models=[MODEL])

    def set(self, **conf) -> None:
        self.conf.update(conf)
        self.conf_path.write_text(json.dumps(self.conf))

    def env(self) -> dict:
        return {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "FAKE_AWS_CONF": str(self.conf_path),
            "FAKE_AWS_LOG": str(self.log_path),
        }

    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.log_path.read_text().splitlines()] if self.log_path.exists() else []
