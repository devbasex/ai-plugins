"""耐久の記録に秘密が入らない（B8）。DBOS の記録へ書く値は殻の serializer の 1 か所で伏せる。"""

from __future__ import annotations

from ndf.core import durable

TOKEN = "durable-s3cr3t-token-77"


def test_exception_attributes_and_notes_are_masked():
    exc = RuntimeError("failed with " + TOKEN)
    exc.url = "https://x:" + TOKEN + "@example.com"
    exc.add_note("token " + TOKEN)
    masked = durable._redact(exc, [TOKEN])
    assert TOKEN not in repr((masked.args, vars(masked)))
    assert exc.url.endswith(TOKEN + "@example.com")
