---
name: chrome-cdp
description: "Connect from a dev container to the user's own desktop Chrome over CDP, for login and visual checks, then run tests headless with its cookies. Use when the browser must be the user's Chrome（手元のChromeに接続・ローカルChromeを操作・ログイン済みのブラウザを使う）."
allowed-tools:
  - Read
  - Write
  - Bash
---

# 利用者の Chrome を開発コンテナから操作する

開発コンテナの中のエージェントが、**利用者の PC で動いている Chrome** を CDP（Chrome DevTools Protocol）で操作する手順。

**使う場面:** 認証のある画面（管理画面・SSO）を確かめるとき。ログインは利用者がいつもの画面で行い、エージェントはその Chrome を見る・操作する。

**使わない場面:** 認証の要らないページの自動テスト。コンテナの中の headless の Chromium で足りる。

## 方針: ログインは利用者の Chrome、大量の操作はコンテナで

CDP の操作は 1 回ごとに利用者の PC と往復する。リモートの開発環境では数十 ms の往復が積み重なり、数百回クリックするテストは目に見えて遅くなる。そこで役割を分ける。

| 何を | どこで |
| --- | --- |
| ログイン・画面の目視・少数の操作 | 利用者の Chrome（CDP） |
| E2E のような大量の操作 | コンテナの headless の Chromium。利用者の Chrome から**対象の origin の Cookie だけ**を取り出して `storageState` として渡す |
| テスト結果の表示 | `playwright show-report` などを、エディタのポート転送でコンテナから利用者の PC へ出す |

## 1. 経路を決める

まずコンテナから利用者の PC へ届く経路を判定する。

```bash
getent hosts host.docker.internal && echo "経路A の候補"
while read -r iface dest gw _; do if [ "$dest" = 00000000 ]; then echo "既定ゲートウェイ(16進): $gw"; fi; done < /proc/net/route
env | grep -qE '^REMOTE_CONTAINERS=' && echo "Dev Containers の中"
```

| 経路 | 当たる環境 | 利用者の PC までの道 |
| --- | --- | --- |
| A. 同じ PC の Docker | Docker Desktop（macOS / Windows）の上のコンテナ。`host.docker.internal` が引ける | コンテナ → `host.docker.internal:9222` → Chrome |
| B. リモートのホスト | クラウドの VM（EC2 など）の上のコンテナに、エディタが SSH でつないでいる。`host.docker.internal` が引けない | Chrome ← SSH の `RemoteForward` ← ホストの `127.0.0.1:9222` ← 中継コンテナ ← コンテナ |

どちらにも当たらない（ホストへ SSH していない・PC から VM へ届く経路が無い）ときは、ここで止めて利用者に構成を尋ねる。**Tailscale などで 9222 をネットワークへ開く案は採らない**（下の「安全のための決まり」）。

## 2. 利用者の PC 側の設定（利用者が行う）

利用者に頼む。利用者の PC で動くエージェント（WSL の Claude など）がいるなら、[references/local-side-setup.md](references/local-side-setup.md) をそのまま指示書として渡す。

### Chrome を専用プロファイルで起動する

**Chrome 136 以降は、既定のプロファイルではリモート操作の口が開かない。** 専用の `--user-data-dir` で別の Chrome を起動する。ログインは初回だけ要る。

```
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\chrome-cdp
# macOS
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-cdp"
```

### 経路 B だけ: SSH の転送を足す

エディタ（VS Code の Remote-SSH など）が使う SSH の設定の、そのホストの節に 1 行足す。**SSH の接続を張り直すまで効かない**（ウィンドウを閉じて開き直す）。

```
Host <リモートのホスト>
    RemoteForward 9222 127.0.0.1:9222
```

**エディタのポート転送では代わりにならない。** あれはコンテナのポートを PC へ見せる向きで、PC のポートをコンテナへ届ける向きを持たない。

## 3. 経路 B だけ: ホストに中継を立てる

sshd の既定（`GatewayPorts no`）では、転送はホストの `127.0.0.1` にしか開かない。ブリッジネットワークのコンテナからは届かないので、ホストのネットワークで動く中継を 1 つ立てる。docker のソケットが使えるときは次で足りる。

```bash
# コンテナが属するネットワークのゲートウェイ（ホスト側の口）
GW=$(docker network inspect "$(docker inspect "$(hostname)" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
docker run -d --name cdp-relay --restart unless-stopped --network host alpine/socat \
  TCP-LISTEN:9222,bind="$GW",fork,reuseaddr TCP:127.0.0.1:9222
```

- `bind` はブリッジのゲートウェイに限る。`0.0.0.0` にしない（VM の外へ 9222 が開く）
- docker のソケットが使えないときは、ホストで同じ socat を動かしてもらうか、sshd に `GatewayPorts clientspecified` を足して `RemoteForward <ゲートウェイ>:9222 127.0.0.1:9222` にしてもらう。どちらもホストの管理者に頼む
- 中継は複数のコンテナで共有してよい。既にあれば作り直さない（`docker ps --filter name=cdp-relay`）

## 4. つながったか確かめる

```bash
CDP_HOST=host.docker.internal   # 経路 B なら上の $GW
curl -s -m 5 "http://$CDP_HOST:9222/json/version" -H 'Host: localhost:9222'
```

**Host ヘッダを `localhost:9222` に上書きする。** Chrome は Host が IP でも localhost でもない接続を拒む（`Host header is specified and is not an IP address or localhost`）。WebSocket の接続も同じ。上書きが要るのは `host.docker.internal` のような名前で接続するときで、IP（経路 B の中継など）で接続するなら上書きしなくても通る（Chrome 155 で確認）。上書きしても害はないので、下の手順は常に上書きする。

| 応答 | 意味 |
| --- | --- |
| `Browser` を含む JSON | つながった |
| 空の応答・`socket hang up` | 経路 B で、中継は生きているが、その先（SSH の転送か Chrome）が無い。接続の張り直しと Chrome の起動を利用者に確かめてもらう |
| 接続できない・名前が引けない | 経路の判定が違う。1 へ戻る |

## 5. 操作する（Playwright）

Node の `fetch` は Host ヘッダを変えられないので、エンドポイントは `http.get`（または `curl`）で取る。取った WebSocket の URL のホストを、届く側の名前へ差し替える。

```js
const http = require('http');
const { chromium } = require('playwright'); // または '@playwright/test'

const CDP = { host: process.env.CDP_HOST, port: 9222 };
const HOST = 'localhost:9222';

function wsEndpoint() {
  return new Promise((resolve, reject) => {
    http.get({ ...CDP, path: '/json/version', headers: { Host: HOST }, timeout: 5000 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        const ws = new URL(JSON.parse(body).webSocketDebuggerUrl);
        ws.hostname = CDP.host;
        resolve(ws.toString());
      });
    }).on('error', reject);
  });
}

const browser = await chromium.connectOverCDP(await wsEndpoint(), { headers: { Host: HOST } });
const context = browser.contexts()[0];      // 利用者のログイン状態を持つ文脈
const page = await context.newPage();       // 利用者のタブは触らず、新しいタブで操作する
// ...
await browser.close();                      // CDP の接続を切るだけ。利用者の Chrome は閉じない
```

- 画面を利用者に見せたいときは `await page.bringToFront()`
- **Chrome DevTools の MCP は、手元に Chrome の実行ファイルを探して起動しようとする。** リモートの Chrome へ向けられない構成では使えないので、上の Playwright で操作する

### Cookie を取り出して headless で回す

```js
const origin = new URL(process.env.TARGET_URL).origin;
const cookies = await context.cookies(origin);          // 対象の origin だけを取る
fs.writeFileSync('.auth/state.json', JSON.stringify({ cookies, origins: [] }));
```

#### `Browser context management is not supported` で拒まれたとき

Chrome の起動のしかたによっては、ブラウザ全体への命令が拒まれる。`connectOverCDP` は接続の直後に `Browser.setDownloadBehavior` を送るので、そこで失敗する。`Storage.getCookies` も同じ理由で通らない。

**タブ（page）単位につなぎ、`Network.getCookies` に対象の URL を渡す。** この経路なら通る。URL を渡すので、ほかのサイトの Cookie は返らない。

```js
const WebSocket = require('ws');                         // Node 標準の WebSocket は Host を変えられない
const page = (await getJson('/json/list')).find((t) => t.type === 'page');   // getJson は Host を上書きした http.get
const u = new URL(page.webSocketDebuggerUrl); u.hostname = CDP.host;
const ws = new WebSocket(u.toString(), { headers: { Host: 'localhost:9222' } });
ws.on('open', () => ws.send(JSON.stringify({ id: 1, method: 'Network.getCookies', params: { urls: [`${origin}/`] } })));
// 応答の cookies を storageState の形（name・value・domain・path・expires（session なら -1）・httpOnly・secure・sameSite）へ直して保存する
```

この状態の Chrome では、Playwright で画面を操作することもできない。目視や操作は利用者に頼み、テストはコンテナの headless で回す。

Playwright のテストでは `use: { storageState: '.auth/state.json' }` で読ませる。`.auth/` はリポジトリに入れない（`.gitignore` に足す）。セッションの期限が切れたら、利用者に Chrome でログインし直してもらい、取り出し直す。

## 安全のための決まり

- **9222 をネットワークへ開かない。** CDP には認証が無く、届けば誰でもログイン済みのブラウザを操作できる。Chrome の待ち受けは `127.0.0.1` のまま、`--remote-debugging-address=0.0.0.0`・Windows の `netsh portproxy`・Tailscale での公開はしない
- **見るのは対象の origin だけ。** `context.cookies()` を引数なしで呼ぶと、利用者の全サイトの Cookie（名前と値）が取れてしまう。一覧を出力しない。必要な origin を渡す
- **利用者のタブを閉じない・移動しない。** 操作は `newPage()` で開いたタブで行い、終わったら接続だけを切る
- 共有の検証環境では、書き込み（保存・送信・削除）を伴う操作は利用者に確かめてから行う

## 完了報告に書くこと

- 使った経路（A / B）と、立てた・使った中継（名前と待ち受けアドレス）
- 取り出した Cookie の origin と件数（値は書かない）
- 利用者に頼んだ設定と、まだ済んでいないもの
