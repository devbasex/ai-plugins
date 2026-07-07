# Runtime Smoke Tests

`scripts/runtime-smoke-test.sh` runs Claude, Codex, and Kiro plugin smoke tests in disposable Docker containers. The host home directory and credential directories are not mounted.

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

Artifacts are written to `tmp/runtime-smoke/<runtime>/` by default:

- `smoke.log`
- `version.log`
- `generated-tree.txt`
- `junit.xml`

Secret modes:

- `--with-secrets=off`: PR-safe unauthenticated smoke.
- `--with-secrets=auto`: inject allowlisted secrets when present and skip authenticated checks when absent.
- `--with-secrets=required`: fail when no allowlisted secret exists.

`--keep-container` is local-debug only and is rejected with `auto` or `required` secret modes.

File secrets are passed with allowlisted keys:

```bash
bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto \
  --secret-file bigquery-key-file=/path/to/service-account.json
```

The authenticated GitHub workflow accepts `BIGQUERY_KEY_FILE_JSON` as a protected secret and writes it to a temporary file before passing it through `--secret-file`.
