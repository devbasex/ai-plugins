from __future__ import annotations

import pytest

from ndf.testing.isolation import forbid_real_state  # noqa: F401  （fixture として受ける）


@pytest.fixture(autouse=True)
def _isolated(forbid_real_state):  # noqa: F811
    return forbid_real_state
