# ブラウザ接続構成 (local / CDP remote)

E2E テスト実行時のブラウザ接続先を構成する手順。概要と設定項目は `SKILL.md` の「ブラウザ接続」節を参照。

## Chrome 起動フラグ

CDP 接続に使う Chrome は次のフラグで起動する。OS ごとの差はバイナリパスだけである。

| フラグ | 役割 |
|---|---|
| `--remote-debugging-port=9222` | CDP エンドポイントを 9222 で公開 |
| `--remote-allow-origins=*` | CDP WebSocket の Host ヘッダ検証を無効化し、コンテナ等リモートからの接続を許可 (Chrome 106+) |
| `--user-data-dir=/tmp/chrome-debug` | 専用プロファイルで起動し、通常の Chrome と共存させる。任意のパスでよい |
| `--disable-features=DialMediaRouteProvider` | DIAL (Cast) 探索を無効化し、CDP ログのノイズと不要な通信を抑制 |
| `--remote-debugging-address=0.0.0.0` | 全インターフェースで listen する。loopback bind でコンテナから届かない場合のみ付与 |

既存プロファイルのログイン済み Session をそのまま使う場合は、**全 Chrome プロセスを終了してから**
`--user-data-dir` を外して起動する (デフォルトプロファイルを使用)。

> **Security**: `--remote-allow-origins=*` と `--remote-debugging-address=0.0.0.0` は信頼できるネットワーク内でのみ使用する。ファイアウォールでポート 9222 へのアクセスを制限することを推奨。

## パターン 1: ローカルコンテナ Chromium (デフォルト)

設定不要。`run.sh` 初回実行時に `playwright install chromium` が自動実行される。

```yaml
browser:
  mode: local
```

## パターン 2: Windows ホスト Chrome (WSL2 + Docker → CDP)

```
Docker container (playwright)
  ↓ http://host.docker.internal:9222
Docker Desktop (WSL2 backend) → Windows host → localhost:9222
  ↓
Chrome (--remote-debugging-port=9222 --remote-allow-origins=*)
```

**Step 1: Windows Chrome をリモートデバッグモードで起動**

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-allow-origins=* `
  --user-data-dir="C:\tmp\chrome-debug"
```

> Chrome はデフォルトで `127.0.0.1` にバインドするため、`--remote-allow-origins=*` だけでは WSL2/Docker から接続できない場合がある。その場合は後述の「ネットワーク別接続ガイド」で到達性を確保する。

**Step 2: WSL2 .wslconfig を NAT mode にする**

```ini
# %USERPROFILE%\.wslconfig
[wsl2]
networkingMode=NAT
```

**Step 3: scenario.config.yaml**

Docker Desktop (WSL2 backend) は `host.docker.internal` を標準サポートしている。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host.docker.internal:9222}
```

> **Docker Desktop を使わず WSL2 から直接実行する場合**: `host.docker.internal` は Docker Desktop 固有の DNS 名のため使えない。Windows ホストの IP を直接指定する。
> ```bash
> export CDP_ENDPOINT="http://$(grep nameserver /etc/resolv.conf | awk '{print $2}'):9222"
> ```

## パターン 3: macOS ホスト Chrome (Docker → CDP)

```
Docker container (playwright)
  ↓ http://host.docker.internal:9222
macOS host → localhost:9222
  ↓
Chrome (--remote-debugging-port=9222 --remote-allow-origins=*)
```

**Step 1: ホスト側で Chrome を起動**

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug \
  --remote-allow-origins=* \
  --disable-features=DialMediaRouteProvider
```

> **Note**: 上記は macOS Docker Desktop で動作実績のあるコマンド。macOS Docker Desktop は `host.docker.internal` がホストの loopback (127.0.0.1) バインドのサービスに到達できるため、`--remote-debugging-address=0.0.0.0` は不要で、全インターフェース公開によるセキュリティ低下も避けられる。Linux / WSL2 ホストで loopback bind が問題になる場合のみ `0.0.0.0` を付与する。

**Step 2: scenario.config.yaml**

`--remote-allow-origins=*` で Host ヘッダ検証を無効化しているため、WSL2 と異なり **proxy 不要**。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host.docker.internal:9222}
```

**Step 3 (任意): コンテナからホスト Chrome を起動する** → 次節。

## コンテナからホスト Chrome を起動する (SSH 経由)

### なぜ直接は起動できないのか

Docker コンテナはホストとプロセス空間が分離されているため、**コンテナ内のプロセスがホスト上に直接プロセスを生成することはできない**。
特に macOS / Windows の Docker Desktop はコンテナを LinuxKit VM 内で実行するため、`nsenter` やホスト PID namespace を使う Linux 系の回避策も VM 止まりでホストには届かない。

したがって「コンテナからホストの Chrome を起動する」には、**ホスト側に起動を受け付ける口** が必要になる。最も導入が容易でスクリプト化しやすいのは **SSH** (macOS の「リモートログイン」= sshd) を使う方法。

```
Docker container ──ssh──▶ host.docker.internal:22 (macOS sshd)
                                 └─▶ Google Chrome --remote-debugging-port=9222 ... (バックグラウンド起動)
Docker container ──CDP──▶ host.docker.internal:9222 (起動後に接続)
```

### ホスト側の準備 (一度だけ)

1. **リモートログインを有効化**: システム設定 > 一般 > 共有 > 「リモートログイン」を ON (CLI: `sudo systemsetup -setremotelogin on`)
2. **SSH 鍵を登録** (パスワードレス実行のため): コンテナ側の公開鍵をホストの `~/.ssh/authorized_keys` に追加
3. ログインユーザーは **コンソールにログイン中の本人** であること。macOS では GUI アプリ (Chrome) は WindowServer に接続するため、コンソールセッションの所有者として起動する必要がある

### スクリプト

`scripts/start-host-chrome.sh` をコンテナ内から実行する。冪等で、既に CDP が起動済みなら何もしない。

```bash
# コンテナ内
HOST_SSH_USER=<macのユーザー名> ./scripts/start-host-chrome.sh
```

| 変数 | デフォルト | 説明 |
|---|---|---|
| `HOST_SSH_USER` | (必須) | ホスト (mac) のログインユーザー名 |
| `HOST_SSH_HOST` | `host.docker.internal` | SSH 接続先ホスト |
| `CDP_PORT` | `9222` | リモートデバッグポート |
| `CDP_BIND_ADDRESS` | (空=loopback) | Chrome の listen address。空なら Chrome 既定の loopback bind (macOS Docker Desktop はこれで動作・実証済み)。Linux/WSL2 等で到達できない場合のみ `0.0.0.0` 等を指定 |
| `CHROME_USER_DATA_DIR` | `/tmp/chrome-debug` | 起動プロファイル。空にするとデフォルトプロファイル (ログイン済み Session) を使用 |
| `CHROME_BIN` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | Chrome バイナリパス |
| `MANUAL_WAIT` | `120` | 手動フォールバック時の起動待ち秒。`0` で待たず即終了 |

動作:

1. `http://host.docker.internal:9222/json/version` に疎通すれば **起動済み** とみなし即終了
2. 未起動なら SSH (`BatchMode=yes`) でホストに接続し、Chrome をバックグラウンド (`nohup ... &`) で起動
3. CDP エンドポイントが応答するまで最大 30 秒ポーリングして待機

### SSH が使えない場合のフォールバック

`HOST_SSH_USER` 未設定 / コンテナに `ssh` クライアントが無い / SSH 接続・実行に失敗 (鍵未登録・リモートログイン無効・到達不可) のいずれかで、スクリプトは **自動で手動フォールバックに切り替わる**。

フォールバック時は、ホスト側で実行すべき起動コマンドをそのまま画面に出力し、`MANUAL_WAIT` 秒 (既定 120s) のあいだ CDP の起動をポーリングして待機する。利用者はその間にホストのターミナルへコマンドを貼り付けて実行すればよい。CI など人手が介在しない環境では `MANUAL_WAIT=0` を指定すれば、案内を出して即座に非ゼロ終了する。

> **Note (SSH を使わない代替手段)**: ホスト側に常駐ランチャ (launchd エージェントや FIFO 監視スクリプト、簡易 HTTP エンドポイント等) を置き、コンテナからネットワーク経由でトリガする方法もある。ただし SSH 方式が最も追加実装が少なく確実。X11 forwarding (XQuartz + socat) は「コンテナ内 GUI をホスト画面に表示する」用途であり、本件には不要。

> **Note (Linux ホストの場合)**: ホストで sshd が動いていれば同じスクリプトが使える。`CHROME_BIN=google-chrome`、`HOST_SSH_HOST` をホスト IP (`172.17.0.1` 等) に設定する。GUI セッションへの接続には `DISPLAY` 等の追加考慮が必要。

## conftest.py への統合

`browser.mode: cdp-remote` の場合、pytest-playwright の通常のブラウザ起動をバイパスし、
`connectOverCDP()` で既存 Chrome に接続する fixture を有効化する必要がある。

`scenario-test/conftest.py` に以下を追加する。

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
        # CDP 接続の場合、close() は接続を切断 (disconnect) するだけで、
        # リモートブラウザ自体は終了しない。
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

`browser.new_context()` は新規コンテキストを作成するため、既存のログイン Session は引き継がれない。
`cdp-remote` モードでは、テンプレートの `conftest.py` が `context` / `page` fixture を自動的にオーバーライドし、
CDP 接続先の既存コンテキスト (ログイン済み Session) を返す。標準のテストコードは無変更で既存セッションを利用できる。

```python
@pytest.fixture(scope="session")
def context(browser, pwk_config, _cdp_default_context):
    """cdp-remote: 既存コンテキスト / local: 新規コンテキスト"""
    if pwk_config.browser.mode == "cdp-remote" and _cdp_default_context is not None:
        yield _cdp_default_context
    else:
        ctx = browser.new_context()
        yield ctx
        ctx.close()

@pytest.fixture(scope="session")
def page(context, pwk_config):
    """cdp-remote: 既存ページ / local: 新規ページ"""
    if pwk_config.browser.mode == "cdp-remote" and context.pages:
        yield context.pages[0]
    else:
        pg = context.new_page()
        yield pg
        pg.close()
```

## run.sh での利用

`cdp-remote` モード時は `playwright install chromium` が不要。`run.sh` は初回セットアップで
`playwright install` を実行するが、接続先がリモートの場合はスキップして問題ない。

```bash
# エンドポイントの疎通確認
curl -s http://host.docker.internal:9222/json/version | python3 -m json.tool
```

## ネットワーク別接続ガイド

Chrome はデフォルトで `127.0.0.1` にバインドするため、同一ホストからしか CDP エンドポイントにアクセスできない。
Docker コンテナや WSL2 からリモート接続する場合は、次のいずれかで到達性を確保する。

### 方法 1: `--remote-debugging-address=0.0.0.0` (推奨)

Chrome 起動時に全インターフェースでリッスンさせる。最もシンプル。フラグの詳細は冒頭の表を参照。

### 方法 2: socat によるポートフォワード (Linux)

Chrome を `127.0.0.1` バインドのまま維持し、socat でリモートからのアクセスを中継する。

```bash
socat TCP-LISTEN:9222,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:9222
```

### 方法 3: netsh portproxy (Windows → WSL2)

```powershell
# 管理者権限の PowerShell で実行
netsh interface portproxy add v4tov4 `
  listenaddress=0.0.0.0 listenport=9222 `
  connectaddress=127.0.0.1 connectport=9222

netsh interface portproxy show all
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=9222
```

### 接続先の早見表

| 実行環境 | Chrome の場所 | 推奨方法 | CDP エンドポイント |
|---|---|---|---|
| ローカル (同一ホスト) | 同一ホスト | 設定不要 | `http://localhost:9222` |
| Docker → ホスト (macOS) | macOS ホスト | 方法 1 | `http://host.docker.internal:9222` |
| Docker → ホスト (Linux) | Linux ホスト | 方法 1 or 2 | `http://host.docker.internal:9222` or `http://172.17.0.1:9222` |
| Docker (WSL2) → Windows | Windows ホスト | 方法 1 or 3 | `http://host.docker.internal:9222` |
| WSL2 → Windows | Windows ホスト | 方法 1 or 3 | `http://$(grep nameserver /etc/resolv.conf \| awk '{print $2}'):9222` |

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
| `host.docker.internal` 解決不能 | Docker Desktop 未使用 or 古いバージョン | Docker Desktop を使用するか、WSL2 直接の場合は Windows ホスト IP を直接指定 |
| IPv6 でバインドされる | WSL2 が IPv6 優先 | listen address に `0.0.0.0` を明示 |
| `netsh portproxy` で接続ループ | portproxy の自己参照 | `--remote-allow-origins=*` を使い proxy を廃止 |
| mirrored mode で動かない | mirrored は localhost 共有だが CDP の WS 接続でポート競合 | NAT mode に戻す |

### macOS 固有

| 症状 | 原因 | 対策 |
|---|---|---|
| `host.docker.internal` 解決不能 | Docker Desktop が古い / Linux Docker | `--add-host=host.docker.internal:host-gateway` を指定 |
| ファイアウォールでブロック | macOS のアプリファイアウォール | システム設定 > ネットワーク > ファイアウォール で Chrome を許可 |
| SSH 起動で Chrome が表示されない / WindowServer エラー | コンソール非ログインユーザーで SSH した | コンソールにログイン中の本人ユーザーで SSH する |
| `start-host-chrome.sh` が SSH で認証失敗 | リモートログイン未有効 / 鍵未登録 | `sudo systemsetup -setremotelogin on` と `authorized_keys` 登録を確認 |

## CDP 接続のメリット

- **GUI Chrome をそのまま操作可能** — OBS 録画可、人間と AI の協調操作が可能
- **ログイン済み Session の再利用** — Google / AWS / Slack 等の MFA 済み Session をそのまま使える
- **ブラウザ拡張機能が有効** — テスト時にも拡張機能の影響を確認可能
- **AI Agent との相性** — Claude Code / Browser Use / OpenHands がリアルブラウザを操作
