"""secret_redact: 外へ出す文から認証情報を伏せて縮める（#1692 の I6）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secret_redact import redact_output as redact  # noqa: E402

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def test_redact_hides_credentials_in_urls_and_token_shapes():
    text = "\n".join(
        [
            f"fatal: unable to access 'https://x-access-token:{TOKEN}@github.com/o/r.git/'",
            "remote: https://user:secret-pass@example.com/o/r.git",
            f"token {TOKEN} and github_pat_" + "abcdefghij_1234567890",
        ]
    )
    out = redact(text)
    assert TOKEN not in out and "secret-pass" not in out and "github_pat_abcdefghij" not in out
    assert "https://***@github.com/o/r.git" in out and "https://***@example.com" in out


def test_redact_keeps_the_last_lines_and_chars():
    text = "\n".join(f"line {n}" for n in range(20)) + "\n\n"
    assert redact(text).splitlines() == [f"line {n}" for n in range(15, 20)]
    long = "x" * 2000
    out = redact(long)
    assert len(out) == 500 and out.startswith("…")
