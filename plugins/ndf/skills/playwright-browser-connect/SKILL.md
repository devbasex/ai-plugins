---
name: playwright-browser-connect
description: "Playwrightのブラウザ接続先を構成する。"
when_to_use: "E2E テストのブラウザ接続先を設定・変更するとき / remote Chrome に CDP で接続したいとき / WSL2 Docker から Windows Chrome を操作したいとき / macOS ホストの Chrome を使いたいとき。Triggers: 'ブラウザ接続', 'remote chrome', 'CDP接続', 'connectOverCDP', 'リモートブラウザ', 'Windows Chrome', 'macOS Chrome', 'mac Chrome', 'browser connect', 'cdp endpoint', 'remote debugging', 'コンテナからホスト Chrome 起動', 'host.docker.internal'"
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
  ↓ http://host.docker.internal:9222
Docker Desktop (WSL2 backend)
  ↓ host.docker.internal → Windows host IP
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

> **Important**: Chrome はデフォルトで `127.0.0.1` にバインドするため、`--remote-allow-origins=*` だけでは WSL2/Docker から接続できない場合がある。対処方法は「ネットワーク別接続ガイド」セクションを参照。

**Step 3: WSL2 .wslconfig (NAT mode 確認)**

```ini
# %USERPROFILE%\.wslconfig
[wsl2]
networkingMode=NAT
```

**Step 4: scenario.config.yaml**

Docker Desktop (WSL2 backend) は `host.docker.internal` を標準サポートしている。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host.docker.internal:9222}
```

環境変数で指定する場合:

```bash
export CDP_ENDPOINT="http://host.docker.internal:9222"
```

> **Note (Docker Desktop を使わず WSL2 から直接実行する場合)**: `host.docker.internal` は Docker Desktop 固有の DNS 名のため利用できない。代わりに Windows ホストの IP アドレスを直接指定する:
> ```bash
> # WSL2 から Windows ホスト IP を取得
> export CDP_ENDPOINT="http://$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):9222"
> ```

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

**Step 1: macOS Chrome をリモートデバッグモードで起動 (ホスト側で実行)**

実績のある起動コマンド:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug \
  --remote-allow-origins=* \
  --disable-features=DialMediaRouteProvider
```

各フラグの意味:

| フラグ | 役割 |
|---|---|
| `--remote-debugging-port=9222` | CDP エンドポイントを 9222 で公開 |
| `--user-data-dir=/tmp/chrome-debug` | 専用プロファイルで起動 (既存の通常 Chrome と共存可能)。任意のパスでよい |
| `--remote-allow-origins=*` | CDP WebSocket の Host ヘッダ検証を無効化し、リモート (コンテナ) からの接続を許可 (Chrome 106+) |
| `--disable-features=DialMediaRouteProvider` | DIAL (Cast) のメディアルート探索を無効化。CDP ログのノイズと不要なネットワーク探索を抑制 |

> **Note**: 上記は macOS Docker Desktop で動作実績のあるコマンド。macOS Docker Desktop は `host.docker.internal` がホストの loopback (127.0.0.1) バインドのサービスに到達できるため、`--remote-debugging-address=0.0.0.0` は不要 (全ネットワークインターフェース公開によるセキュリティ低下も避けられる)。Linux / WSL2 ホストで loopback バインドが問題になる場合は `--remote-debugging-address=0.0.0.0` を付与する (`start-host-chrome.sh` では `CDP_BIND_ADDRESS=0.0.0.0` を指定)。詳細は後述の「ネットワーク別接続ガイド」(方法 1: `0.0.0.0` / 方法 2: socat / 方法 3: netsh portproxy) を参照。

既存プロファイルのログイン済み Session をそのまま使う場合は、全 Chrome プロセスを終了してから
`--user-data-dir` を外して起動する (デフォルトプロファイルを使用):

```bash
# 全 Chrome プロセスを終了してから
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --disable-features=DialMediaRouteProvider
```

**Step 2: scenario.config.yaml**

macOS Docker Desktop は `host.docker.internal` を標準サポートしており、
`--remote-allow-origins=*` で Host ヘッダ検証を無効化しているため、WSL2 と異なり **proxy 不要**。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://host.docker.internal:9222}
```

**Step 3 (任意): コンテナからホスト Chrome を起動する**

毎回ホスト側で手動起動するのを避けたい場合は、コンテナから SSH 経由でホストの Chrome を起動できる。
詳細は後述の「コンテナからホスト Chrome を起動する (SSH 経由)」セクションを参照。

## コンテナからホスト Chrome を起動する (SSH 経由)

### なぜ直接は起動できないのか

Docker コンテナはホストとプロセス空間が分離されているため、**コンテナ内のプロセスがホスト上に直接プロセスを生成することはできない**。
特に macOS / Windows の Docker Desktop はコンテナを LinuxKit VM 内で実行するため、`nsenter` やホスト PID namespace を使う Linux 系の回避策も VM 止まりで macOS ホストには届かない。

したがって「コンテナからホストの Chrome を起動する」には、**ホスト側に起動を受け付ける口** が必要になる。最も導入が容易でスクリプト化しやすいのは **SSH** (macOS の「リモートログイン」= sshd) を使う方法。
コンテナは `host.docker.internal` でホストに到達できるため、SSH でホストにログインして起動コマンドを実行する。

```
Docker container ──ssh──▶ host.docker.internal:22 (macOS sshd)
                                 └─▶ Google Chrome --remote-debugging-port=9222 ... (バックグラウンド起動)
Docker container ──CDP──▶ host.docker.internal:9222 (起動後に接続)
```

### ホスト側の準備 (一度だけ)

1. **リモートログインを有効化**: システム設定 > 一般 > 共有 > 「リモートログイン」を ON
   （CLI: `sudo systemsetup -setremotelogin on`）
2. **SSH 鍵を登録** (パスワードレス実行のため): コンテナ側の公開鍵をホストの `~/.ssh/authorized_keys` に追加
3. ログインユーザーは **コンソールにログイン中の本人** であること。
   macOS では GUI アプリ (Chrome) は WindowServer に接続するため、コンソールセッションの所有者として起動する必要がある。

### スクリプト

`scripts/start-host-chrome.sh` をコンテナ内から実行する。
冪等で、既に CDP が起動済みなら何もしない。

```bash
# コンテナ内
HOST_SSH_USER=<macのユーザー名> \
  ./scripts/start-host-chrome.sh
```

主な環境変数:

| 変数 | デフォルト | 説明 |
|---|---|---|
| `HOST_SSH_USER` | (必須) | ホスト (mac) のログインユーザー名 |
| `HOST_SSH_HOST` | `host.docker.internal` | SSH 接続先ホスト |
| `CDP_PORT` | `9222` | リモートデバッグポート |
| `CDP_BIND_ADDRESS` | (空=loopback) | Chrome の listen address。空なら付与せず Chrome 既定の loopback bind (macOS Docker Desktop はこれで動作・実証済み)。Linux/WSL2 等で loopback bind だとコンテナから到達できない場合のみ `0.0.0.0` 等を指定 (全インターフェース公開のためセキュリティ注意) |
| `CHROME_USER_DATA_DIR` | `/tmp/chrome-debug` | 起動プロファイル。空にするとデフォルトプロファイル (ログイン済み Session) を使用 |
| `CHROME_BIN` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | Chrome バイナリパス |
| `MANUAL_WAIT` | `120` | 手動フォールバック時の起動待ち秒。`0` で待たず即終了 |

スクリプトの動作:

1. `http://host.docker.internal:9222/json/version` に疎通すれば **起動済み** とみなし即終了
2. 未起動なら SSH (`BatchMode=yes`) でホストに接続し、Chrome をバックグラウンド (`nohup ... &`) で起動
3. CDP エンドポイントが応答するまで最大 30 秒ポーリングして待機

#### SSH が使えない場合のフォールバック

以下のいずれかに該当すると、スクリプトは **自動で手動フォールバックに切り替わる**:

- `HOST_SSH_USER` が未設定
- コンテナに `ssh` クライアントが無い
- SSH 接続/実行に失敗 (鍵未登録・リモートログイン無効・到達不可など。`BatchMode=yes` によりパスワード待ちで固まらず即失敗)

フォールバック時は、**ホスト側で実行すべき起動コマンドをそのまま画面に出力** し、
`MANUAL_WAIT` 秒 (既定 120s) のあいだ CDP の起動をポーリングして待機する。
利用者はその間にホストのターミナルへコマンドを貼り付けて実行すればよく、
起動が検知されればスクリプトは成功終了する。

```text
──────────────────────────────────────────────────────────────
⚠ SSH 自動起動を利用できません (SSH 接続/実行に失敗)。
  ホスト (mac) 側のターミナルで以下を実行してください:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir='/tmp/chrome-debug' --remote-allow-origins=* --disable-features=DialMediaRouteProvider

──────────────────────────────────────────────────────────────
→ ホストでの起動を待機中 (最大 120s, Ctrl-C で中断)...
```

CI など人手が介在しない環境では `MANUAL_WAIT=0` を指定すれば、案内を出して即座に非ゼロ終了する。

> **Note (SSH を使わない代替手段)**: ホスト側に常駐ランチャ (launchd エージェントや FIFO 監視スクリプト、簡易 HTTP エンドポイント等) を置き、コンテナからネットワーク経由でトリガする方法もある。
> ただし SSH 方式が最も追加実装が少なく確実。X11 forwarding (XQuartz + socat) は「コンテナ内 GUI をホスト画面に表示する」用途であり、本件 (ホストの既存 Chrome を起動する) には不要。

> **Note (Linux ホストの場合)**: ホストで sshd が動いていれば同じスクリプトが使える。`CHROME_BIN=google-chrome`、`HOST_SSH_HOST` をホスト IP (`172.17.0.1` 等) に設定する。GUI セッションへの接続には `DISPLAY` 等の追加考慮が必要。

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

`cdp-remote` モードでは、テンプレートの `conftest.py` が `context` / `page` fixture を
自動的にオーバーライドし、CDP 接続先の既存コンテキスト (ログイン済み Session) を返す。
標準のテストコードは何も変更せずに既存セッションを利用できる。

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

`browser.new_context()` は新規コンテキストを作成するため、既存のログイン Session は引き継がれない。
`cdp-remote` モードでは `context` fixture が自動的に `browser.contexts[0]` を返すため、
テスト側で特別な対応は不要。

## run.sh での利用

`cdp-remote` モード時は `playwright install chromium` が不要。
`run.sh` は初回セットアップで `playwright install` を実行するが、
接続先がリモートの場合はスキップして問題ない (ローカルブラウザは使わないため)。

CDP 接続テストを手動確認する場合:

```bash
# エンドポイントの疎通確認
curl -s http://host.docker.internal:9222/json/version | python3 -m json.tool
```

## ネットワーク別接続ガイド

Chrome はデフォルトで `127.0.0.1` (ループバック) にバインドするため、同一ホストからしか CDP エンドポイントにアクセスできない。Docker コンテナや WSL2 からリモート接続する場合は、以下のいずれかの方法でネットワーク到達性を確保する必要がある。

### 方法 1: `--remote-debugging-address=0.0.0.0` (推奨)

Chrome 起動時に全インターフェースでリッスンさせる。最もシンプルな方法。

```bash
# Linux / macOS
google-chrome \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=*
```

```powershell
# Windows PowerShell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-debugging-address=0.0.0.0 `
  --remote-allow-origins=*
```

> **Security**: `0.0.0.0` はすべてのネットワークインターフェースに公開するため、信頼できるネットワーク内でのみ使用すること。ファイアウォールでポート 9222 へのアクセスを制限することを推奨。

### 方法 2: socat によるポートフォワード (Linux)

Chrome を `127.0.0.1` バインドのまま維持し、socat でリモートからのアクセスを中継する。

```bash
# Chrome は通常どおり起動 (127.0.0.1 バインド)
google-chrome --remote-debugging-port=9222 --remote-allow-origins=*

# 別ターミナルで socat を起動
socat TCP-LISTEN:9222,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:9222
```

### 方法 3: netsh portproxy (Windows → WSL2)

Windows ホストの Chrome を WSL2 からアクセスする場合、Windows 側でポートフォワードを設定する。

```powershell
# 管理者権限の PowerShell で実行
netsh interface portproxy add v4tov4 `
  listenaddress=0.0.0.0 listenport=9222 `
  connectaddress=127.0.0.1 connectport=9222

# 確認
netsh interface portproxy show all

# 削除する場合
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=9222
```

### 接続先の早見表

| 実行環境 | Chrome の場所 | 推奨方法 | CDP エンドポイント |
|---|---|---|---|
| ローカル (同一ホスト) | 同一ホスト | 設定不要 | `http://localhost:9222` |
| Docker → ホスト (macOS) | macOS ホスト | 方法 1 | `http://host.docker.internal:9222` |
| Docker → ホスト (Linux) | Linux ホスト | 方法 1 or 2 | `http://host.docker.internal:9222` or `http://172.17.0.1:9222` |
| Docker (WSL2) → Windows | Windows ホスト | 方法 1 or 3 | `http://host.docker.internal:9222` |
| WSL2 → Windows | Windows ホスト | 方法 1 or 3 | `http://$(cat /etc/resolv.conf \| grep nameserver \| awk '{print $2}'):9222` |

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
| IPv6 でバインドされる | WSL2 が IPv6 優先 | proxy.js で `0.0.0.0` を明示 |
| `netsh portproxy` で接続ループ | portproxy の自己参照 | `--remote-allow-origins=*` を使い proxy を廃止 |
| mirrored mode で動かない | mirrored は localhost 共有だが CDP の WS 接続でポート競合 | NAT mode に戻す |

### macOS 固有

| 症状 | 原因 | 対策 |
|---|---|---|
| `host.docker.internal` 解決不能 | Docker Desktop が古い / Linux Docker | `--add-host=host.docker.internal:host-gateway` を指定 |
| ファイアウォールでブロック | macOS のアプリファイアウォール | システム設定 > ネットワーク > ファイアウォール で Chrome を許可 |
| SSH 起動で Chrome が表示されない / WindowServer エラー | コンソール非ログインユーザーで SSH した | コンソールにログイン中の本人ユーザーで SSH する (`start-host-chrome.sh` 参照) |
| `start-host-chrome.sh` が SSH で認証失敗 | リモートログイン未有効 / 鍵未登録 | `sudo systemsetup -setremotelogin on` と `authorized_keys` 登録を確認 |

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
