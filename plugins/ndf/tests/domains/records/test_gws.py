"""gws の認証の判定（`gws.authenticated`。#2067）。"""

from __future__ import annotations

import pytest

from ndf.domains.records import gws

TOKEN_ENV = {"GOOGLE_WORKSPACE_CLI_TOKEN": "t"}
NO_TOKEN = {"auth_method": "none", "credential_source": "client_secret.json"}


@pytest.mark.parametrize(
    ("status", "env", "expected"),
    [
        pytest.param(NO_TOKEN, {}, False, id="no-token-2067"),
        pytest.param(NO_TOKEN, TOKEN_ENV, True, id="token-in-env"),
        pytest.param({"credential_source": "token_cache"}, {}, True, id="token-cache"),
        pytest.param({"credential_source": "none"}, {}, False, id="none"),
        pytest.param({"credential_source": "none", "auth_method": "oauth2"}, {}, False, id="none-with-oauth2"),
        pytest.param({"auth_method": "oauth2", "credential_source": "client_secret.json"}, {}, True, id="oauth2-login"),
        pytest.param({"auth_method": "none", "credential_source": "environment_variables"}, {}, True, id="env-vars"),
    ],
)
def test_authenticated_by_status_form(status: dict, env: dict, expected: bool) -> None:
    assert gws.authenticated(status, env) is expected
