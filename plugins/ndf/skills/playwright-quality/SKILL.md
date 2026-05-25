---
name: playwright-quality
description: "Playwright テスト実行時の品質自動計測。accessibility (axe-core / WCAG)、Core Web Vitals (LCP/CLS/TTFB)、body_check (PHP/SSR エラー検出) を page role marker に連動して自動実行する。"
when_to_use: "accessibility チェック / Core Web Vitals 計測 / PHP エラー検出が必要なとき。Triggers: 'a11y テスト', 'accessibility テスト', 'axe-core', 'WCAG', 'Core Web Vitals', 'Web Vitals', 'LCP', 'CLS', 'body_check', 'PHP エラー検出'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(python *)
---

# Playwright Quality (品質自動計測)

E2E テスト実行時に **accessibility / Core Web Vitals / body_check** を自動計測する。

## accessibility (axe-core)

`@pytest.mark.page_role` marker が付いたテストで、auto_roles にマッチする role の場合に axe-core を自動実行。

### 設定 (scenario.config.yaml)

```yaml
accessibility:
  enabled: true
  auto_roles: [lp, list, form, dashboard, cart, checkout, settings, auth]
  tags: [wcag2a, wcag2aa, wcag21aa, wcag22aa]
  fail_on_violations: true
```

### テストコードでの手動実行

```python
def test_form_accessibility(page, pwk_accessibility_scan):
    page.goto("/contact")
    violations = pwk_accessibility_scan()
    assert len(violations) == 0, f"{len(violations)} violations found"
```

### 単発スキャン (テスト外)

> スクリプトの実行は `/ndf:playwright-kit-ops` skill を参照。

```bash
# playwright-kit-ops/ ディレクトリ内で実行
python scripts/run_a11y_scan.py --url https://example.com
```

## Core Web Vitals

`@pytest.mark.page_role` marker + auto_roles マッチで LCP / CLS / TTFB / longest_task を自動計測。

### 設定 (scenario.config.yaml)

```yaml
web_vitals:
  enabled: true
  auto_roles: [lp, list, dashboard, search]
  observe_ms: 5000
  fail_on_poor: true
```

### テストコードでの手動計測

```python
def test_dashboard_performance(page, pwk_web_vitals_measure):
    page.goto("/dashboard")
    metrics = pwk_web_vitals_measure()
    assert metrics["lcp"]["rating"] != "poor"
```

### 単発計測 (テスト外)

> スクリプトの実行は `/ndf:playwright-kit-ops` skill を参照。

```bash
# playwright-kit-ops/ ディレクトリ内で実行
python scripts/check_cwv.py --url https://example.com
```

## body_check (PHP/SSR エラー検出)

`page.on("response")` で全 HTML レスポンスを監視し、`Fatal error` / `Warning:` 等の文字列パターンを検出する。**デフォルトで有効** (PHP 系パターン内蔵)。

### 設定 (scenario.config.yaml)

```yaml
body_check:
  enabled: true
  fatal_patterns: ["Fatal error", "Uncaught", "Parse error"]
  warning_patterns: ["STRICT:", "Warning:", "Notice:", "Deprecated:"]
  warning_head_chars: 300
  fail_on_match: true
```

### Opt-out

```python
@pytest.mark.no_body_check
def test_known_warning_page(page):
    page.goto("/legacy")
```

非 PHP プロジェクトで誤検出する場合は `body_check.enabled: false` で無効化。

## 関連 Skill

- `/ndf:playwright-evidence` — 基本エビデンス収集
- `/ndf:playwright-report` — 品質計測結果を含むレポート生成
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
