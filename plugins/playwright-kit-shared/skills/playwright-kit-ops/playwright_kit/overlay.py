"""動画録画用 HUD オーバーレイ (赤丸カーソル + 2 行字幕) の制御。

ブラウザ標準のカーソルは録画に焼き付かないため、JS で `<div>` を 2 つ inject する:
  - `#__hud_cursor` — mousemove/down/up を追う赤丸（クリックで黄色リップル）
  - `#__hud_caption` — 画面上部の 2 行字幕（`直前 │ … / 次へ │ …`）

OVERLAY_INIT_SCRIPT は context.add_init_script() に渡す。
他の関数は Page を受け取り、HUD の状態を JS evaluate 越しに更新する。
"""

from __future__ import annotations

from playwright.sync_api import Page


OVERLAY_INIT_SCRIPT = r"""
(() => {
  if (window.__hudInited) return;
  window.__hudInited = true;
  function setup() {
    if (!document.body) return false;
    const cursor = document.createElement('div');
    cursor.id = '__hud_cursor';
    cursor.style.cssText =
      'position:fixed;width:24px;height:24px;border-radius:50%;' +
      'background:rgba(255,80,80,0.55);border:2px solid #f33;' +
      'pointer-events:none;z-index:2147483647;' +
      'transform:translate(-50%,-50%);' +
      'box-shadow:0 0 10px rgba(255,0,0,0.7);transition:background 0.1s;';
    document.documentElement.appendChild(cursor);
    // 前のページから引き継いだ最終カーソル位置と表示状態を復元
    try {
      const cx = sessionStorage.getItem('__hudCursorX');
      const cy = sessionStorage.getItem('__hudCursorY');
      const cv = sessionStorage.getItem('__hudCursorVisible');
      if (cx !== null) cursor.style.left = cx + 'px';
      if (cy !== null) cursor.style.top = cy + 'px';
      // 既定は「非表示」 (擬似クリック対象がないステップは消す方針)
      if (cv === '1') {
        cursor.style.opacity = '1';
        cursor.style.visibility = 'visible';
      } else {
        cursor.style.opacity = '0';
        cursor.style.visibility = 'hidden';
      }
    } catch (e) {}
    document.addEventListener('mousemove', (e) => {
      cursor.style.left = e.clientX + 'px';
      cursor.style.top = e.clientY + 'px';
      try {
        sessionStorage.setItem('__hudCursorX', String(e.clientX));
        sessionStorage.setItem('__hudCursorY', String(e.clientY));
      } catch (err) {}
    }, true);
    // 任意座標でクリックリップルを発火させる外部 API
    window.__hudFlash = function(x, y) {
      cursor.style.left = x + 'px';
      cursor.style.top = y + 'px';
      cursor.style.opacity = '1';
      cursor.style.visibility = 'visible';
      try { sessionStorage.setItem('__hudCursorVisible', '1'); } catch (e) {}
      spawnRipple(x, y);
      cursor.style.background = 'rgba(0,255,200,0.95)';
      cursor.style.transform = 'translate(-50%,-50%) scale(1.6)';
      cursor.style.boxShadow = '0 0 22px rgba(255,224,0,0.95)';
      cursor.style.borderColor = '#ffe000';
      setTimeout(() => {
        cursor.style.background = 'rgba(255,80,80,0.55)';
        cursor.style.transform = 'translate(-50%,-50%) scale(1)';
        cursor.style.boxShadow = '0 0 10px rgba(255,0,0,0.7)';
        cursor.style.borderColor = '#f33';
      }, 400);
    };

    // 擬似クリック対象が見つからないステップで非表示にする
    window.__hudHideCursor = function() {
      cursor.style.opacity = '0';
      cursor.style.visibility = 'hidden';
      try { sessionStorage.setItem('__hudCursorVisible', '0'); } catch (e) {}
    };

    function spawnRipple(x, y) {
      // 3 重リングのリップルでクリック箇所を強調
      for (let i = 0; i < 3; i++) {
        const ring = document.createElement('div');
        ring.style.cssText =
          'position:fixed;pointer-events:none;z-index:2147483645;' +
          'left:' + x + 'px;top:' + y + 'px;' +
          'width:0;height:0;border:4px solid #ffe000;' +
          'border-radius:50%;transform:translate(-50%,-50%);' +
          'box-shadow:0 0 12px rgba(255,224,0,0.8);';
        document.documentElement.appendChild(ring);
        ring.animate(
          [
            { width: '24px', height: '24px', opacity: 1, borderWidth: '5px',
              borderColor: '#ffe000' },
            { width: '110px', height: '110px', opacity: 0, borderWidth: '2px',
              borderColor: '#ff6600' }
          ],
          { duration: 800, delay: i * 130, easing: 'ease-out',
            fill: 'forwards' }
        );
        setTimeout(() => { try { ring.remove(); } catch(e) {} },
                   850 + i * 130);
      }
      const cross = document.createElement('div');
      cross.style.cssText =
        'position:fixed;pointer-events:none;z-index:2147483647;' +
        'left:' + x + 'px;top:' + y + 'px;' +
        'width:48px;height:48px;transform:translate(-50%,-50%);' +
        'background:radial-gradient(circle,rgba(255,224,0,0.55) 0%,rgba(255,224,0,0) 70%);';
      document.documentElement.appendChild(cross);
      cross.animate(
        [{ opacity: 1 }, { opacity: 0 }],
        { duration: 600, easing: 'ease-out', fill: 'forwards' }
      );
      setTimeout(() => { try { cross.remove(); } catch(e) {} }, 650);
    }

    document.addEventListener('mousedown', (e) => {
      cursor.style.background = 'rgba(0,255,200,0.95)';
      cursor.style.transform = 'translate(-50%,-50%) scale(1.6)';
      cursor.style.boxShadow = '0 0 22px rgba(255,224,0,0.95)';
      cursor.style.borderColor = '#ffe000';
      spawnRipple(e.clientX, e.clientY);
    }, true);
    document.addEventListener('mouseup', () => {
      cursor.style.background = 'rgba(255,80,80,0.55)';
      cursor.style.transform = 'translate(-50%,-50%) scale(1)';
      cursor.style.boxShadow = '0 0 10px rgba(255,0,0,0.7)';
      cursor.style.borderColor = '#f33';
    }, true);

    const HUD_HEIGHT = 60;  // 字幕 2 行分の固定高
    const cap = document.createElement('div');
    cap.id = '__hud_caption';
    cap.style.cssText =
      'position:fixed;top:0;left:0;right:0;' +
      'height:' + HUD_HEIGHT + 'px;' +
      'background:rgba(0,0,0,0.88);color:#fff;' +
      'font:14px/1.5 "Noto Sans CJK JP","Noto Sans JP",' +
      '"Hiragino Sans","Yu Gothic","Meiryo",IPAGothic,sans-serif;' +
      'font-feature-settings:"palt";' +
      'padding:8px 16px;pointer-events:none;z-index:2147483646;' +
      'box-sizing:border-box;border-bottom:3px solid #fa0;' +
      'white-space:pre-wrap;word-break:break-all;';
    document.documentElement.appendChild(cap);

    // 本文が字幕で隠れないよう body を下にずらす (border-bottom 3px を含めて +3)
    try {
      const padTop = (HUD_HEIGHT + 3) + 'px';
      document.body.style.paddingTop = padTop;
      document.documentElement.style.scrollPaddingTop = padTop;
    } catch (e) {}
    let stored = '';
    try { stored = sessionStorage.getItem('__hudCaption') || ''; } catch (e) {}
    cap.textContent = window.__pendingCaption || stored || '';
    return true;
  }
  if (!setup()) {
    document.addEventListener('DOMContentLoaded', setup);
  }
})();
"""


_SET_CAPTION_JS = """(text) => {
  window.__pendingCaption = text;
  try { sessionStorage.setItem('__hudCaption', text); } catch (e) {}
  const cap = document.getElementById('__hud_caption');
  if (cap) cap.textContent = text;
}"""


def set_caption(page: Page, *, previous: str = "", next_action: str = "") -> None:
    """現ページの HUD 字幕を「直前 / 次へ」の 2 行で更新する。

    sessionStorage にも書き込むので、次の navigation 後に init script が拾い直す。
    """
    parts = [
        f"直前 │ {previous}" if previous else None,
        f"次へ │ {next_action}" if next_action else None,
    ]
    text = "\n".join(p for p in parts if p)
    try:
        page.evaluate(_SET_CAPTION_JS, text)
    except Exception:
        pass


def flash_click(page: Page, x: int, y: int, *, settle_ms: int = 250) -> None:
    """指定座標 (viewport 内) にカーソルを移動し、HUD リップルを発火させる。

    DOM 要素はクリックしない (`page.mouse.click` は呼ばない)。HUD オーバーレイの
    `__hudFlash(x, y)` を JS evaluate で呼び出すだけ。
    """
    try:
        page.mouse.move(x, y, steps=10)
    except Exception:
        pass
    try:
        page.evaluate(
            "(c) => { if (window.__hudFlash) window.__hudFlash(c[0], c[1]); }",
            [x, y],
        )
    except Exception:
        return
    if settle_ms > 0:
        try:
            page.wait_for_timeout(settle_ms)
        except Exception:
            pass


def hide_cursor(page: Page) -> None:
    """擬似クリック対象が見つからないとき、HUD カーソルを非表示にする。"""
    try:
        page.evaluate("() => { if (window.__hudHideCursor) window.__hudHideCursor(); }")
    except Exception:
        pass
