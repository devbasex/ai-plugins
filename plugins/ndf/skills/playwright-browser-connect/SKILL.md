---
name: playwright-browser-connect
description: "Playwright E2E テストのブラウザ接続先を構成する。ローカル Chromium / Windows リモート Chrome (CDP) / macOS リモート Chrome (CDP) の 3 パターンをサポートし、scenario.config.yaml の browser: セクションで宣言的に切り替える。"
when_to_use: "E2E テストのブラウザ接続先を設定・変更するとき / remote Chrome に CDP で接続したいとき / WSL2 Docker から Windows Chrome を操作したいとき / macOS ホストの Chrome を使いたいとき。Triggers: 'ブラウザ接続', 'remote chrome', 'CDP接続', 'connectOverCDP', 'リモートブラウザ', 'Windows Chrome', 'macOS Chrome', 'browser connect', 'cdp endpoint', 'remote debugging'"
allowed-tools:
  - Read
  - Bash
---

# Playwright Browser Connect (ブラウザ接続構成)

E2E テスト実行時のブラウザ接続先を構成する。

## 接続モード一覧

| モード | scenario.config.yaml | 接続先 | 用途 |
|---|---|---|---|
| `local` | `browser.mode: local` | コンテナ内 Chromium | CI / ヘッドレス実行 (デフォルト) |
| `cdp-remote` | `browser.mode: cdp-remote` | リモート Chrome (CDP) | GUI 操作・ログイン済み Session 再利用 |

## 設定 (scenario.config.yaml)

```yaml
# --- ブラウザ接続設定 --------------------------------------------------
browser:
  # local: playwright install chromium でインストールしたローカルブラウザ (デフォルト)
  # cdp-remote: Chrome DevTools Protocol 経由でリモートブラウザに接続
  mode: local

  # cdp-remote 時のみ有効
  cdp_endpoint: ${CDP_ENDPOINT:-http://localhost:9222}
```

## パターン別セットアップ

### パターン 1: ローカルコンテナ Chromium (デフォルト)

設定不要。`run.sh` 初回実行時に `playwright install chromium` が自動実行される。

```yaml
browser:
  mode: local
```

### パターン 2: Windows ホスト Chrome (WSL2 + Docker → CDP)

WSL2 上の Docker コンテナから Windows 側の Chrome GUI を CDP 経由で操作する。

#### 構成図

```
Docker container (playwright)
  ↓ http://host-gateway:9222
WSL2 host
  ↓ NAT bridge
Windows host
  ↓ localhost:9222
Chrome (--remote-debugging-port=9222 --remote-allow-origins=*)
```

#### セットアップ手順

**Step 1: Windows Chrome をリモートデバッグモードで起動**

```powershell
# Windows PowerShell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-allow-origins=* `
  --user-data-dir="C:\tmp\chrome-debug"
```

既存プロファイルのログイン済み Session を使う場合:

```powershell
# 全 Chrome プロセスを閉じてから
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-allow-origins=*
```

**Step 2: `--remote-allow-origins=*` で Host ヘッダ検証を無効化**

Chrome CDP の WebSocket は Host ヘッダ検証があるため、リモートからの接続がデフォルトで拒否される。
Chrome 起動時に `--remote-allow-origins=*` フラグを付けることで、任意の Origin からの接続を許可できる。

Step 1 のコマンドにフラグを追加:

```powershell
# Windows PowerShell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-allow-origins=* `
  --user-data-dir="C:\tmp\chrome-debug"
```

これにより proxy を設置する必要がなくなり、Docker コンテナから直接 CDP エンドポイントに接続できる。

> **Note**: `--remote-allow-origins=*` は Chrome 106+ で利用可能。セキュリティ上、信頼できるネットワーク内での利用に限定すること。

**Step 3: WSL2 .wslconfig (NAT mode 確認)**

```ini
# %USERPROFILE%\.wslconfig
[wsl2]
networkingMode=NAT
```

**Step 4: Docker Compose で host-gateway を設定**

```yaml
# docker-compose.yml
services:
  app:
    extra_hosts:
      - "host-gateway:host-gateway"
```

**Step 5: scenario.config.yaml**

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host-gateway:9222}
```

環境変数で指定する場合:

```bash
export CDP_ENDPOINT="http://host-gateway:9222"
```

### パターン 3: macOS ホスト Chrome (Docker → CDP)

macOS 上の Docker コンテナから macOS 側の Chrome GUI を CDP 経由で操作する。

#### 構成図

```
Docker container (playwright)
  ↓ http://host.docker.internal:9222
macOS host
  ↓ localhost:9222
Chrome (--remote-debugging-port=9222 --remote-allow-origins=*)
```

#### セットアップ手順

**Step 1: macOS Chrome をリモートデバッグモードで起動**

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir="$HOME/tmp/chrome-debug"
```

既存プロファイルのログイン済み Session を使う場合:

```bash
# 全 Chrome プロセスを終了してから
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=*
```

**Step 2: scenario.config.yaml**

macOS Docker Desktop は `host.docker.internal` を標準サポートしており、
`--remote-allow-origins=*` で Host ヘッダ検証を無効化しているため proxy 不要。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host.docker.internal:9222}
```

## conftest.py への統合

`browser.mode: cdp-remote` の場合、pytest-playwright の通常のブラウザ起動をバイパスし、
`connectOverCDP()` で既存 Chrome に接続する fixture を有効化する必要がある。

`scenario-test/conftest.py` に以下を追加:

```python
import pytest
from playwright.sync_api import Browser, BrowserType

@pytest.fixture(scope="session")
def browser(
    browser_type: BrowserType,
    browser_type_launch_args: dict,
    pwk_config,
) -> Browser:
    """browser.mode に応じてブラウザ接続を切り替える。

    - local: pytest-playwright デフォルト (chromium.launch())
    - cdp-remote: chromium.connect_over_cdp(endpoint)
    """
    browser_cfg = pwk_config.browser
    # slow_mo: browser_type_launch_args が優先、なければ config の slow_mo_ms
    _slow_mo = browser_type_launch_args.get(
        "slow_mo", pwk_config.playwright.slow_mo_ms or None
    )
    if browser_cfg.mode == "cdp-remote":
        if browser_type.name != "chromium":
            pytest.fail(
                f"cdp-remote モードは Chromium 専用です (現在: {browser_type.name})。"
                "--browser chromium を指定するか、browser.mode を local に変更してください。"
            )
        browser = browser_type.connect_over_cdp(
            browser_cfg.cdp_endpoint,
            slow_mo=_slow_mo,
        )
        yield browser
        browser.close()
    else:
        launch_args = {**browser_type_launch_args}
        launch_args.setdefault("headless", pwk_config.playwright.headless)
        if _slow_mo is not None:
            launch_args.setdefault("slow_mo", _slow_mo)
        browser = browser_type.launch(**launch_args)
        yield browser
        browser.close()
```

### CDP モードでの既存セッション再利用

CDP 接続先のブラウザが持つ既存コンテキスト (ログイン済み Session 等) を再利用するには、
`browser.contexts[0]` を使用する。テンプレートの `conftest.py` には `_cdp_default_context`
fixture が用意されている。

```python
@pytest.fixture(scope="session")
def _cdp_default_context(browser, pwk_config):
    """CDP モードで既存ブラウザの最初のコンテキストを返す。"""
    if pwk_config.browser.mode == "cdp-remote" and browser.contexts:
        return browser.contexts[0]
    return None
```

`browser.new_context()` は新規コンテキストを作成するため、既存のログイン Session は引き継がれない。
既存 Session を再利用したい場合は `_cdp_default_context` fixture を注入して
`context.new_page()` でページを取得すること。

## run.sh での利用

`cdp-remote` モード時は `playwright install chromium` が不要。
`run.sh` は初回セットアップで `playwright install` を実行するが、
接続先がリモートの場合はスキップして問題ない (ローカルブラウザは使わないため)。

CDP 接続テストを手動確認する場合:

```bash
# エンドポイントの疎通確認
curl -s http://host-gateway:9222/json/version | python3 -m json.tool
```

## トラブルシュート

### 共通

| 症状 | 原因 | 対策 |
|---|---|---|
| `connect_over_cdp` で接続拒否 | Chrome が起動していない / ポートが違う | `curl http://<endpoint>/json/version` で確認 |
| WebSocket handshake 失敗 | Host ヘッダ不一致 | Chrome 起動時に `--remote-allow-origins=*` を付与 |
| ページ操作が異常に遅い | VPN / DNS 解決の遅延 | `extra_hosts` で IP 直指定 |

### Windows (WSL2) 固有

| 症状 | 原因 | 対策 |
|---|---|---|
| `host-gateway` 解決不能 | Docker Compose の `extra_hosts` 未設定 | `extra_hosts: ["host-gateway:host-gateway"]` を追加 |
| IPv6 でバインドされる | WSL2 が IPv6 優先 | proxy.js で `0.0.0.0` を明示 |
| `netsh portproxy` で接続ループ | portproxy の自己参照 | `--remote-allow-origins=*` を使い proxy を廃止 |
| mirrored mode で動かない | mirrored は localhost 共有だが CDP の WS 接続でポート競合 | NAT mode に戻す |

### macOS 固有

| 症状 | 原因 | 対策 |
|---|---|---|
| `host.docker.internal` 解決不能 | Docker Desktop が古い / Linux Docker | `--add-host=host.docker.internal:host-gateway` を指定 |
| ファイアウォールでブロック | macOS のアプリファイアウォール | システム設定 > ネットワーク > ファイアウォール で Chrome を許可 |

## CDP 接続のメリット

- **GUI Chrome をそのまま操作可能** — OBS 録画可、人間と AI の協調操作が可能
- **ログイン済み Session の再利用** — Google / AWS / Slack 等の MFA 済み Session をそのまま使える
- **ブラウザ拡張機能が有効** — テスト時にも拡張機能の影響を確認可能
- **AI Agent との相性** — Claude Code / Browser Use / OpenHands がリアルブラウザを操作

## 関連 Skill

- `/ndf:playwright-execution` — テスト実行 + エビデンス収集
- `/ndf:playwright-kit-ops` — プロジェクト初期化 / ツール群
- `/ndf:playwright-scenario-test` — 全機能統括
- `/ndf:docker-container-access` — Docker コンテナアクセス一般
