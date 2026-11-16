"""耐久の記録に秘密が入らない（B8）。DBOS の記録へ書く値は殻の serializer の 1 か所で伏せる。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ndf.core import durable

from ._durable_helpers import SRC

SCRIPT = Path(__file__).with_name("_durable_secret.py")
TOKEN = "durable-s3cr3t-token-77"
# 進行表の DB の URL は名前が秘密の形でなくても秘密として扱う（資格情報の無い形でも値ごと伏せる）
PROGRESS_URL = "postgresql://ndf@db.internal.example:5432/progress"


def test_inputs_outputs_and_errors_are_masked_in_the_records(root):
    env = {**os.environ, "PYTHONPATH": str(SRC), "NDF_TEST_TOKEN": TOKEN, "NDF_PROGRESS_DB": PROGRESS_URL}
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "secrets"], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    live = json.loads(done.stdout.splitlines()[-1])
    # 流れている呼び出しには元の値が渡る
    assert live["seen"]["token"] == TOKEN
    assert live["url"] == PROGRESS_URL
    stored = [p for p in root.rglob("*") if p.is_file()]
    assert any(p.suffix == durable.DB_SUFFIX for p in stored)
    leaked = [p for p in stored for s in (TOKEN, PROGRESS_URL) if s.encode() in p.read_bytes()]
    assert leaked == []


def test_exception_attributes_and_notes_are_masked():
    exc = RuntimeError("failed with " + TOKEN)
    exc.url = "https://x:" + TOKEN + "@example.com"
    exc.add_note("token " + TOKEN)
    masked = durable._redact(exc, [TOKEN])
    assert TOKEN not in repr((masked.args, vars(masked)))
    assert exc.url.endswith(TOKEN + "@example.com")
