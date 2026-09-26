"""T2: hook のテストが渡すシェルの入力を集める pytest のプラグイン（hook-trial.py collect が -p で読む）。

subprocess.run を包み、テストの呼び出しはそのまま流す。その後で:
- worktree-common.sh / worktree-guard.sh を使う呼び出しは、採取の包みを足した複製で打ち直し、
  wt_extract_write_target の引数（コマンドと起点）と PWD を集める
- token-guard.sh の呼び出しは、標準入力の JSON と NDF_SLEEP_MAX_SEC を集める
"""
from __future__ import annotations

import json
import os
import subprocess

W = os.environ["NDF_T2_WORK"]
REPO = os.environ["NDF_T2_REPO"]
REAL = os.path.join(REPO, "plugins/ndf/scripts")
COPY = os.path.join(W, "scripts-copy")
CAP = os.path.join(W, "cap", f"wt.{os.getpid()}.cap")
TOKEN = os.path.join(W, "cap", f"token.{os.getpid()}.jsonl")
_run = subprocess.run


def _swap(x):
    if isinstance(x, str):
        return x.replace(REAL + "/", COPY + "/")
    if isinstance(x, (list, tuple)):
        return type(x)(_swap(a) for a in x)
    return x


def _flat(args) -> str:
    return args if isinstance(args, str) else " ".join(map(str, args))


def run(*a, **kw):
    out = _run(*a, **kw)
    args = a[0] if a else kw.get("args")
    try:
        flat = _flat(args)
        if REAL + "/token-guard.sh" in flat:
            env = kw.get("env") or os.environ
            data = kw.get("input")
            if isinstance(data, bytes):
                data = data.decode()
            with open(TOKEN, "a") as f:
                f.write(json.dumps({"input": data, "max": env.get("NDF_SLEEP_MAX_SEC"),
                                    "guard": env.get("NDF_SLEEP_GUARD")}, ensure_ascii=False) + "\n")
        elif (REAL + "/worktree-guard.sh" in flat or REAL + "/lib/worktree-common.sh" in flat) and \
                ("wt_extract_write_target" in flat or "worktree-guard.sh" in flat):
            kw2 = dict(kw)
            env = dict(kw.get("env") or os.environ)
            env["NDF_T2_CAP"] = CAP
            kw2["env"] = kw2.get("env") and env or env
            kw2["capture_output"] = True
            kw2.pop("stdout", None)
            kw2.pop("stderr", None)
            kw2.pop("check", None)
            _run(_swap(args), *a[1:], **kw2)
    except Exception:  # 採取の失敗でテストを落とさない
        pass
    return out


subprocess.run = run
