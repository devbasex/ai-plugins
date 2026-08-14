# Checklist: `modal` / `wizard` — モーダル / ウィザード

## 適用条件
- **modal**: `role="dialog"` + `aria-modal="true"` + 背景 overlay + close ボタン
- **wizard**: 内部に step (進捗インジ) + 戻る/次へ + 保存 / 完了

代表: 削除確認 dialog, onboarding wizard, 設定 modal, login modal.

W3C WAI-ARIA APG "Dialog (Modal) Pattern" 準拠が前提.

## 必須テスト観点 (modal)

### MD1: open 時のフォーカス移動 `[Statutes / Automatic]`
- ダイアログ open → 最初の操作可能要素 or `aria-labelledby` 要素にフォーカス
- 主要 CTA (例: "削除を確定") にフォーカスを置く実装も推奨

### MD2: Focus trap `[Statutes / User]`
- Tab で dialog 内をループ
- Shift+Tab で逆順ループ
- dialog 外の要素にフォーカスが行かない
- 背景の `inert` 属性 or `aria-hidden="true"` で背景を不活性化

### MD3: Esc キーで閉じる `[User / Statutes]`
- Esc 押下 → close
- ただし破壊的 dialog (削除確認等) は Esc を無視 (仕様による)

### MD4: close 後のフォーカス復帰 `[State / User]`
- close → トリガー要素 (ダイアログを開いたボタン) にフォーカス復帰
- スクリーンリーダーで「閉じた」アナウンス (任意)

### MD5: オーバーレイクリックで閉じる `[User]`
- 背景 overlay クリック → close (仕様による)
- 重要な dialog は overlay クリックを無視

### MD6: 背景スクロールロック `[User]`
- mobile で重要: dialog open 中、`<body>` に `overflow: hidden`
- 背景がスクロールしない
- dialog 内部は独立してスクロール可能

### MD7: スクリーンリーダー読み上げ `[Statutes / Automatic]`
- `role="dialog"` + `aria-modal="true"`
- `aria-labelledby` で title 紐付け
- `aria-describedby` で本文紐付け

### MD8: ネスト dialog `[State / User]`
- modal 内に確認 modal を開く
- 親 modal の状態保持
- 子 modal close → 親 modal にフォーカス復帰

### MD9: アニメーション `[Statutes / User]`
- `prefers-reduced-motion` 環境でアニメ削減
- `page.emulate_media(reduced_motion="reduce")` で確認

### MD10: モバイルでフルスクリーン化 `[Compatibility / User]`
- mobile viewport (≤ 480px) で dialog がフルスクリーン
- 閉じるボタンが指で押しやすい位置 (右上 / 下部 sticky)

## 必須テスト観点 (wizard)

### WZ1: step 進捗表示 `[Statutes / User]`
- 「Step 2 of 5」明示
- `aria-current="step"` で現在 step
- スクリーンリーダーが進捗読み上げ

### WZ2: 前後遷移と入力保持 `[State / User]`
- step2 → 「戻る」 → step1 (入力値保持)
- step2 で入力 → 「次へ」 → step3 → 「戻る」 → step2 (入力保持)
- 全 step を一巡 → 確認画面 (入力サマリ表示)

### WZ3: 完了 step `[State / Functional]`
- 完了画面で「最初に戻る」 → 全クリア + step1
- 「ダッシュボードへ」 → 別画面 (wizard 終了)
- 完了画面リロードで二重登録しない (POST/Redirect/GET)

### WZ4: 中断と再開 `[Interruptions / Reliability]`
- step3 でブラウザ閉じる → 再アクセス時の挙動 (再開 / リセット)
- 中間保存があれば: 「続きから再開」「最初から」選択肢

### WZ5: skip / optional step `[Decision Table / State]`
- 任意 step を skip → 次 step へ
- skip した内容が確認画面に「未入力」と表示
- skip 不可な必須 step を skip しようとすると拒否

### WZ6: 動的 step (条件分岐) `[Decision Table]`
**Decision Table 必須**.
- step1 で「個人」選択 → step2 = 個人情報, step3 確認
- step1 で「法人」選択 → step2 = 会社情報, step3 = 担当者, step4 確認
- 各分岐を別 testcase として網羅

### WZ7: a11y (form フィールド) `[Statutes / Automatic]`
- 各 step が独立した `<form>` (or fieldset)
- 各フィールドに label
- エラー表示は `aria-invalid` + `aria-describedby`
- 戻る / 次へボタンが `<button type="button"/"submit">` で意味的に正しい

### WZ8: 確認画面の編集リンク `[State / User]`
- 各 section に「修正」リンク
- リンク先が該当 step
- 修正後は確認画面に戻る (or 順次踏み)

### WZ9: 完了通知 `[Functional / i18n]`
- 完了メール / 通知の送信
- 内容が wizard で入力した値と一致

### WZ10: 並行 wizard `[Multi-user / State]`
- 同じユーザが別タブで同じ wizard を進める
- どちらが優先されるか (last-write-wins / lock / merge)

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| State Transition | MD1〜MD8 (open/close/focus), WZ2 (step1→2→3) |
| Decision Table | WZ5, WZ6 (動的 step) |
| Risk Testing | MD8 (ネスト), WZ4 (中断) |
| Compatibility | MD10 |
| Multi-user | WZ10 |

## Playwright 実装パターン

```python
# MD2: Focus trap
page.get_by_role("button", name="削除").click()  # modal open
dialog = page.get_by_role("dialog")
expect(dialog).to_be_visible()

# Tab で dialog 内をループ
focusable = dialog.locator("button, [href], input, [tabindex]:not([tabindex='-1'])").all()
for _ in range(len(focusable) + 2):  # 1 周以上回す
    page.keyboard.press("Tab")
focused = page.evaluate("document.activeElement.outerHTML")
# focused 要素が dialog 内であることを確認
assert dialog.locator("*:focus").count() == 1

# MD3: Esc で close
page.keyboard.press("Escape")
expect(dialog).to_be_hidden()
# トリガーボタンにフォーカス復帰
expect(page.get_by_role("button", name="削除")).to_be_focused()

# MD7: aria attributes
expect(dialog).to_have_attribute("aria-modal", "true")
expect(dialog).to_have_attribute("aria-labelledby", re.compile(r".+"))

# WZ2: 入力保持
page.get_by_role("textbox", name="名前").fill("Alice")
page.get_by_role("button", name="次へ").click()
page.get_by_role("button", name="戻る").click()
expect(page.get_by_role("textbox", name="名前")).to_have_value("Alice")
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y, **C1.2 キーボード / C1.3 focus**), C5 (mobile viewport).

## 参考文献

- W3C WAI-ARIA APG "Dialog (Modal) Pattern", https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
- W3C WAI-ARIA APG "Wizard (multi-step form)" / `aria-current` 仕様
- MDN `aria-modal`, `inert`
- WCAG 2.4.3 Focus Order, 2.1.2 No Keyboard Trap
