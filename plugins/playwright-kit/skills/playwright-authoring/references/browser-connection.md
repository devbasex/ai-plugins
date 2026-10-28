# ブラウザ接続構成 (local / CDP remote)

E2E テスト実行時のブラウザ接続先を構成する手順。概要と設定項目は `SKILL.md` の「ブラウザ接続」節を参照。

## パターン 1: ローカルコンテナ Chromium (デフォルト)

設定不要。`run.sh` 初回実行時に `playwright install chromium` が自動実行される。

```yaml
browser:
  mode: local
```

## パターン 2: 利用者の Chrome (cdp-remote)

**Chrome の起動と、コンテナから Chrome まで届かせる経路は、ndf プラグインの `chrome-cdp` スキル（`/ndf:chrome-cdp`）に従う。** playwright-kit では扱わない。

`chrome-cdp` が扱う範囲:

- Chrome を専用プロファイル（`--user-data-dir`）で起動する。Chrome 136 以降は、既定のプロファイルではリモート操作の口が開かない
- 経路の判定。同じ PC の Docker（`host.docker.internal` が引ける）と、EC2 などリモートのホスト（SSH の `RemoteForward` と中継コンテナ）
- つながったかの確かめ方と、うまくいかないときの切り分け
- ログインだけ利用者の Chrome で行い、Cookie を取り出して `local` モードの headless で回す方法

`--remote-allow-origins=*` や `--remote-debugging-address=0.0.0.0` で Chrome の検査を外す方法は使わない。同じネットワークの誰からでも利用者の Chrome を操作できてしまう。

### scenario.config.yaml

**`cdp_endpoint` は IP アドレスで書く。** Chrome は、Host ヘッダが IP でも localhost でもない接続を拒む。`host.docker.internal` のような名前で書くと、`connect_over_cdp` が `Host header is specified and is not an IP address or localhost` で失敗する。IP で書けば、Host ヘッダを書き換えずに接続できる。

```yaml
browser:
  mode: cdp-remote
  cdp_endpoint: ${CDP_ENDPOINT:-http://localhost:9222}
```

```bash
# 同じ PC の Docker: 名前を IP に引き直す
export CDP_ENDPOINT="http://$(getent hosts host.docker.internal | cut -d' ' -f1):9222"
# リモートのホスト: chrome-cdp で立てた中継の IP（コンテナのネットワークのゲートウェイ）
export CDP_ENDPOINT="http://172.18.0.1:9222"
```

### 大量の操作は local モードで回す

CDP の操作は 1 回ごとに利用者の PC と往復するので、リモートの開発環境では遅い。テストの本数が多いときは、`chrome-cdp` の手順で Cookie を `storageState` として取り出し、`local` モードの headless の Chromium で回す。`cdp-remote` は、ログイン状態の確認や画面を見せながらの少数の操作に使う。

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
# エンドポイントの疎通確認（Browser を含む JSON が返ればよい）
curl -s -m 5 "$CDP_ENDPOINT/json/version" | python3 -m json.tool
```

## トラブルシュート

| 症状 | 原因 | 対策 |
|---|---|---|
| `Host header is specified and is not an IP address or localhost` | `cdp_endpoint` を名前で書いた | IP で書く（上の「scenario.config.yaml」） |
| `connect_over_cdp` で接続拒否・空の応答 | Chrome が起動していない / 経路が切れている | `/ndf:chrome-cdp` の「つながったか確かめる」で切り分ける |
| ページ操作が異常に遅い | 利用者の PC との往復が積み重なっている | Cookie を取り出して `local` モードで回す |
