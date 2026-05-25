---
name: playwright-overlay
description: "Playwright テスト動画に赤丸カーソル + 2行字幕 (操作説明) を重ねるオーバーレイ機能。操作内容を視覚的に伝えるエビデンス動画を生成する。"
when_to_use: "テスト動画に字幕やカーソルを追加したいとき / 操作内容を視覚的に説明するエビデンス動画を作りたいとき。Triggers: '字幕', 'カーソル', 'overlay', 'HUD', '動画装飾', 'エビデンス動画', 'pwk-overlay', '赤丸'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(playwright *)
---

# Playwright Overlay (動画装飾)

テスト動画に **赤丸カーソル + 2行字幕** を重ねて、操作内容を視覚的に伝えるエビデンス動画を生成する。

## 有効化

```bash
./scenario-test/run.sh --pwk-overlay
```

`--pwk-overlay` フラグで全テストの動画にオーバーレイが適用される。

## overlay API

テストコード内で以下の関数を呼び出して字幕・カーソルを制御する:

```python
from playwright_kit.overlay import set_caption, flash_click, hide_cursor
```

| 関数 | 説明 |
|---|---|
| `set_caption(page, previous="", next_action="")` | 2行字幕を更新 (上段: 直前の操作、下段: 次の操作) |
| `flash_click(page, x, y, settle_ms=250)` | 赤丸カーソルを (x, y) に移動 + リップルアニメーション |
| `hide_cursor(page)` | カーソルを非表示 (ナビゲーション後など) |

## テストコード例

```python
from playwright.sync_api import Page, expect
from playwright_kit.overlay import set_caption, flash_click

def test_login_scenario(page: Page, pwk_role_admin):
    set_caption(page, next_action="ログインページを開く")
    page.goto("/admin/login")

    set_caption(page, previous="ログインページを表示", next_action="メールアドレスを入力")
    email = page.get_by_label("メールアドレス")
    box = email.bounding_box()
    flash_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    email.fill("admin@example.com")

    set_caption(page, previous="メールアドレスを入力", next_action="ログインボタンをクリック")
    btn = page.get_by_role("button", name="ログイン")
    box = btn.bounding_box()
    flash_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    btn.click()

    set_caption(page, previous="ログイン成功", next_action="")
    expect(page).to_have_url(lambda u: "/admin/dashboard" in u)
```

## 動画フォーマット

`scenario.config.yaml` で mp4 を指定すると Drive プレビュアとの互換性が高い:

```yaml
playwright:
  video_format: mp4        # mp4 推奨 (webm も可)
  video_size: { width: 1280, height: 720 }
```

## 関連 Skill

- `/ndf:playwright-evidence` — 基本エビデンス収集 (overlay なし)
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
