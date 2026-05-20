# NDF plugin / playwright-scenario-test に `body_check` 機能の復活を依頼

- 起票日: 2026-04-27
- 対象 Skill: `ndf:playwright-scenario-test` (v0.3.0+, pytest-playwright ベース)
- Skill 物理パス: `/home/ubuntu/.claude/plugins/cache/ai-plugins/ndf/4.2.0/skills/playwright-scenario-test/`
- 関連 Issue / PR: [uttaro-dev2/uttarov2-doc#56](https://github.com/uttaro-dev2/uttarov2-doc/pull/56) (prd-standby 動作確認テスト pytest 移植)
- 旧版 (検出ロジックあり): `scripts/prd-standby-test/` (自前 YAML runner) — 2026-04-25 報告書 [`prd-standby-vulnerability-scan-build-2026-04-25.md`](../reports/prd-standby-vulnerability-scan-build-2026-04-25.md)
- 新版 (検出ロジックなし): `scripts/prd-standby-test-pytest/` (pytest-playwright Skill 移植版)

## 要約

playwright-scenario-test v0.2.x (自前 YAML runner) には **ページ本文の特定パターン検査 (`body_check`)** があり、Uttaro (PHP 7.2 → 8.4 移行中) でフロントに漏れる `Fatal error` / `Uncaught` / `STRICT:` などの PHP エラーを **テスト失敗として検出** していた。v0.3.0 で pytest-playwright 化された際にこの検査が落ち、**console.error / pageerror のみ**に置き換わった。PHP のサーバ側生成 HTML に混入したエラー文字列はブラウザ console には出ないため、**現行 Skill では実不具合を見逃す**。

実際に prd-standby (https://e-chusya.com:20444) で 2 件の現存不具合を旧版は検出しており、本 PR (uttarov2-doc#56) の pytest 移植版では検出できないことを確認した。

## 背景

### v0.2.x の `body_check` (旧 `scripts/prd-standby-test/config.yaml`)

旧 config.yaml には以下の宣言があり、各ナビゲーション完了後にレスポンス本文を検査して FAIL 判定していた:

```yaml
# --- ページ本文エラー検出 (PHP プロジェクト固有) ----------------------
body_check:
  fatal_patterns:
    - "Fatal error"
    - "Uncaught"
    - "Parse error"
  warning_patterns:
    # ページ先頭 300 文字に出ていれば「ページが壊れた」とみなす
    - "STRICT:"
    - "Warning:"
    - "Notice:"
    - "Deprecated:"
  not_found_strings:
    - "File not found"
```

判定ロジック (旧 runner の挙動):

| パターン群 | 検査範囲 | 判定 |
|---|---|---|
| `fatal_patterns` | レスポンス本文全体 | 1 つでも含まれれば FAIL |
| `warning_patterns` | 本文の **先頭 300 文字のみ** | 1 つでも含まれれば FAIL (本文中の説明文や入力例の "Notice:" 等は許容、ページ最上段への漏れだけ拾う) |
| `not_found_strings` | レスポンス本文全体 | 1 つでも含まれれば FAIL |

### v0.3.0 (pytest-playwright) で残った検査

`scenario_test/fixtures/evidence.py` の `NdfEvidence` は以下を `page.on(...)` listener で収集:

- `console.error` (= ブラウザ devtools の error log)
- `pageerror` (= unhandled JavaScript exception)

許容パターンは `tolerated_console_errors` / `tolerated_page_errors` (config.yaml) で正規表現指定可能。

→ これらは **JavaScript ランタイム由来のエラー** しか拾わない。**PHP がサーバ側で HTML 本文に出力したエラー文字列**はブラウザから見れば単なる文字なので、console にも pageerror にも上がらない。結果として旧版で検出していた本文混入系不具合がスルーされる。

## 実機エビデンス (prd-standby 2026-04-27 検査結果)

各ロールでログイン後、各画面を直接 GET してレスポンス HTML 中の PHP パターン出現を確認した。検査スクリプト: 別記 (再現コードは末尾参照)。

### 検出された不具合 2 件

| # | ロール | URL | 検出箇所 | 検出パターン |
|---:|---|---|---|---|
| 1 | system (管理者) | `/system/user.php` (患者一覧) | ページ先頭 1000 文字以内 | `STRICT:` |
| 2 | clinic (クリニック代表) | `/clinic/ClinicUttaroSettingEdit.php` (Uttaro設定) | ページ先頭 1000 文字以内 | `Fatal error`, `Uncaught` |

スキャナ出力 (該当部分のみ抜粋):

```
=== system (post-login: https://e-chusya.com:20444/system/TopPage.php) ===
[system] /system/TopPage.php  clean
[system] /system/clinic.php  clean
[system] /system/user.php  HIT(head): ['STRICT:']
[system] /system/VaccineMaster.php  clean
[system] /system/Counting.php  clean
[system] /system/CertificateCount.php  clean
[system] /system/MotherChildNotebook.php  clean

=== clinic (post-login: https://e-chusya.com:20444/clinic/Reserve/Calendar.php) ===
[clinic] /clinic/Reserve/Calendar.php  clean
[clinic] /clinic/ClinicPatient.php  clean
[clinic] /clinic/ClinicInfoEdit.php  clean
[clinic] /clinic/ClinicUttaroSettingEdit.php  HIT(head): ['Fatal error', 'Uncaught']
[clinic] /clinic/Stock.php  clean
[clinic] /clinic/Notice.php  clean
[clinic] /clinic/CertificateCount.php  clean
```

### 旧版 (scripts/prd-standby-test, 2026-04-25 実行) での同等検出

`scripts/prd-standby-test/reports/20260425-125122/report.md` 抜粋:

```
| `TC-10` | 10 | system | FAIL | 13/14 | 65.6s | 管理者 - 全機能シナリオ ... |
| 6 | [03] 患者一覧 | FAIL | status=200 / ct=text/html / final_url=...system/user.php /
                              PHP STRICT: 警告がページ先頭に漏れています |
```

旧版 runner は `body_check.warning_patterns: ["STRICT:"]` を先頭 300 文字に対して当て、`/system/user.php` の `STRICT:` 漏れを **テスト失敗として明示的に表示** していた。新 Skill (pytest-playwright 移植版) で同 URL を `page.goto` した結果は `200 OK` でテスト PASS となり、不具合は検出されない。

→ **検出能力が後退した = 移植版に乗せ換えると本番事故を見逃す可能性がある**。

## 依頼内容 (Ask)

NDF plugin の `playwright-scenario-test` Skill に **body_check 相当の機能** を復活させて頂きたい。具体的には以下:

### 1. config.yaml への schema 追加

```yaml
# --- ページ本文エラー検出 (PHP / SSR プロジェクト向け) ---------------
body_check:
  enabled: true
  fatal_patterns:                # 全文検索 (どこに出ても致命)
    - "Fatal error"
    - "Uncaught"
    - "Parse error"
  warning_patterns:              # 先頭 N 文字検索 (本文中の説明文は許容)
    - "STRICT:"
    - "Warning:"
    - "Notice:"
    - "Deprecated:"
  warning_head_bytes: 300        # warning_patterns の検査範囲
  not_found_patterns:            # 全文検索
    - "File not found"
  fail_on_match: true            # false で warning 出力のみ (情報収集モード)
```

### 2. fixture / API 設計案

(A) **autouse fixture で全 page を監視** — Network listener で `response` イベントを取って HTML 本文を検査。利用者は何もしなくても全 navigation で自動チェック。Playwright の `page.on("response", ...)` が使える:

```python
@pytest.fixture(autouse=True)
def _ndf_body_check(page, ndf_config):
    if not ndf_config.body_check.enabled:
        yield; return
    violations = []
    def _on_resp(resp):
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("text/html"): return
        try: body = resp.text()
        except Exception: return
        # fatal/warning/not_found 判定 → violations に積む
    page.on("response", _on_resp)
    yield
    if violations and ndf_config.body_check.fail_on_match:
        pytest.fail("body_check violations: " + str(violations[:5]))
```

(B) **明示的ヘルパ fixture** — `ndf_body_check(page)` を user code から呼ぶ形。autouse より明示性高いが利用側コードが増える:

```python
def test_xxx(page: Page, ndf_role_admin, ndf_body_check):
    page.goto(...)
    ndf_body_check(page)   # 1 ナビゲーションごと
```

(C) **pytest marker** — `@pytest.mark.body_check` が付いた test だけ autouse 化。明示性と利便性のバランス。

→ おすすめは **(A) autouse + config 経由 enable/disable** + **(C) marker で個別 opt-out**。autouse なので既存テスト 0 行修正で動き、必要なら `@pytest.mark.no_body_check` で skip。

### 3. report.md への反映

- nodeid 行に `body_check.violations` 数カラムを追加
- 検出時の詳細セクション (URL / パターン / 該当箇所スニペット) を report.md に書き出す
- evidence ディレクトリに `body_check.jsonl` (1 navigation = 1 line) で生のヒット情報を保存

### 4. 互換性 / マイグレーション

- 既存利用者向けには `body_check.enabled: false` (default) で **opt-in 機能**にする — 突然 PASS していたテストが FAIL し始めるのを防ぐ
- 旧 v0.2.x 利用者向けに `scripts/migrate_body_check.py` のような config converter があるとなお良い

## 当面の workaround (uttaro 側)

uttarov2-doc#56 では本機能が無いため、project 側 `conftest.py` に同等のヘルパを書いて `page.goto` 後に明示的に呼ぶ形で代替する予定。コード提案は以下:

```python
# scripts/prd-standby-test-pytest/conftest.py (workaround until plugin supports)
_PHP_FATAL_PATTERNS = ("Fatal error", "Uncaught", "Parse error")
_PHP_WARNING_PATTERNS = ("STRICT:", "Warning:", "Notice:", "Deprecated:")
_NOT_FOUND_STRINGS = ("File not found",)
_WARNING_HEAD_BYTES = 300


@pytest.fixture
def assert_no_php_leak():
    def _check(page: Page) -> None:
        body = page.content()
        head = body[:_WARNING_HEAD_BYTES]
        for pat in _PHP_FATAL_PATTERNS:
            assert pat not in body, f"{page.url}: PHP fatal '{pat}'"
        for pat in _PHP_WARNING_PATTERNS:
            assert pat not in head, f"{page.url}: PHP warning '{pat}' on head"
        for s in _NOT_FOUND_STRINGS:
            assert s not in body, f"{page.url}: '{s}'"
    return _check


@pytest.fixture
def nav(ndf_config, assert_no_php_leak):
    def _nav(page: Page, path: str, *, delay_ms: int | None = None) -> None:
        page.goto(f"{ndf_config.base_url}{path}", wait_until="domcontentloaded")
        assert_no_php_leak(page)
        page.wait_for_timeout(
            ndf_config.playwright.step_delay_ms if delay_ms is None else delay_ms
        )
    return _nav
```

→ test 関数は `nav(page, "/clinic/ClinicUttaroSettingEdit.php")` で 1 行ずつ呼ぶ。 plugin が body_check を提供したら、本 workaround は廃棄して plugin 機能に置き換える。

## 再現スクリプト (PHP パターン scan)

```python
# 1 度実行すれば旧版 body_check 相当の HIT 一覧が得られる
import time
from playwright.sync_api import sync_playwright

PATTERNS = ["Fatal error", "Uncaught", "Parse error",
            "STRICT:", "Warning:", "Notice:", "Deprecated:"]
PATHS = {
    "system": ["/system/TopPage.php","/system/clinic.php","/system/user.php",
               "/system/VaccineMaster.php","/system/Counting.php",
               "/system/CertificateCount.php","/system/MotherChildNotebook.php"],
    "clinic": ["/clinic/Reserve/Calendar.php","/clinic/ClinicPatient.php",
               "/clinic/ClinicInfoEdit.php","/clinic/ClinicUttaroSettingEdit.php",
               "/clinic/Stock.php","/clinic/Notice.php","/clinic/CertificateCount.php"],
    "user":   ["/user/mypage.php","/user/profile.php","/user/history.php",
               "/user/history_healthcheck.php","/user/ClinicList.php"],
}
BASE = "https://e-chusya.com:20444"

def goto_with_retry(page, url, retries=3):
    for i in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception:
            time.sleep(2)
    return False

with sync_playwright() as p:
    for role, login_path, fields, paths in [
        ("system", "/system/login.php",
         {"LoginID":"sysstaff0@mail.jp","Password":"qsexefm27uda"},
         PATHS["system"]),
        ("clinic", "/clinic/login.php",
         {"ClinicAccountID":"77363","Password":"qsexefm27uda"},
         PATHS["clinic"]),
        ("user", "/user/login.php",
         {"LoginID":"user247135@mail.jp","Password":"qsexefm27uda"},
         PATHS["user"]),
    ]:
        kwargs = {"ignore_https_errors": True}
        if role == "system":
            kwargs["http_credentials"] = {"username":"testuser_202604",
                                          "password":"testuser_202604"}
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(**kwargs)
        page = ctx.new_page()
        page.goto(BASE+login_path, wait_until="domcontentloaded")
        for k,v in fields.items():
            page.locator(f'input[name="{k}"]').fill(v)
        with page.expect_navigation(wait_until="domcontentloaded"):
            try:
                page.locator('form input[type="image"]').first.click(timeout=2000)
            except Exception:
                page.locator('input[name="Password"]').press("Enter")
        for path in paths:
            if not goto_with_retry(page, BASE+path):
                print(f"[{role}] {path}  GOTO FAILED"); continue
            body = page.content(); head = body[:1000]
            hits = [pat for pat in PATTERNS if pat in head] \
                or [pat for pat in PATTERNS if pat in body]
            print(f"[{role}] {path}  {hits or 'clean'}")
        b.close()
```

## 関連リンク

- 移植 PR (検出能力後退の現場): [uttaro-dev2/uttarov2-doc#56](https://github.com/uttaro-dev2/uttarov2-doc/pull/56)
- 旧版 (body_check 動作実績あり): [`scripts/prd-standby-test/`](../scripts/prd-standby-test/)
  - config schema: [`scripts/prd-standby-test/config.example.yaml`](../scripts/prd-standby-test/config.example.yaml) 81-96 行
  - 実行レポート: `scripts/prd-standby-test/reports/20260425-125122/report.md`
- 新 Skill SKILL.md: `/home/ubuntu/.claude/plugins/cache/ai-plugins/ndf/4.2.0/skills/playwright-scenario-test/SKILL.md`
- 関連 console/pageerror 機構: `scenario_test/fixtures/evidence.py` (250 行〜)
