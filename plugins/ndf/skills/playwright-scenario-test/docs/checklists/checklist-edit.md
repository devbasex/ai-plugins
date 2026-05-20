# Checklist: `edit` — 編集ページ

## 適用条件
既存値プリフィル + Save / Cancel ボタン + URL に id 含む.
dirty 検知 (`beforeunload` 確認) + CSRF token + バリデーション.

代表 URL: `/items/123/edit`, `/items?Cmd=Edit&ItemID=123`.

## 必須テスト観点

### ED1: プリフィル値の正確性 `[Product / History]`
- 直近 GET で取得した値と完全一致
- 日付 TZ / 通貨単位 / null の扱い
- リッチテキスト: HTML エスケープが適切 (`<` が `&lt;` で表示されない、ただし保存されている)

### ED2: 各フィールドの境界値 (BVA) `[BVA / Claims]`
仕様で定義された各フィールドの長さ / 値範囲を全境界網羅。
- 文字数: `min-1` / `min` / `min+1` / `max-1` / `max` / `max+1`
- 数値: `min-1` / `min` / `0` / `max-1` / `max` / `max+1` / `-1`
- 日付: 過去 / 現在 / 未来 / 閏年 2/29 / DST / 範囲外

例: パスワード長 8〜64

```yaml
- name: パスワード境界
  technique: BVA
  oracle: Claims (パスワードポリシー仕様 v1.2)
  inputs:
    - "1234567"           # 7 文字 (min-1) → 失敗期待
    - "12345678"          # 8 文字 (min)   → 成功期待
    - "abcdefg9"          # 8 文字 (min, パターン違い) → 成功
    - "a"*63              # 63 文字 (max-1)
    - "a"*64              # 64 文字 (max)  → 成功
    - "a"*65              # 65 文字 (max+1) → 失敗
```

### ED3: Goldilocks (Hendrickson) `[Domain / User]`
短すぎ・長すぎ・適切値.
- 名前 1 文字 / 100 文字 / 適切
- 説明 0 行 / 1 万行 / 適切

### ED4: 必須フィールド未入力 `[Decision Table / Claims]`
- すべての必須フィールドを空で submit → 422 + フィールド単位エラーメッセージ
- 1 個ずつ空にして submit → 当該フィールドのみエラー
- エラーメッセージのテキスト + a11y (`aria-invalid="true"` + `aria-describedby`)

### ED5: クライアント / サーバ validation 一致 `[Product / Claims]`
- JS validation を bypass (DevTools で `disabled` 削除 / `formnovalidate` 追加)
- サーバが同じバリデーションを行い 422
- エラーメッセージのテキストが一致

### ED6: 特殊文字の保持 `[Domain / Product]`
- 絵文字 (4-byte UTF-8: `🎉`)
- 結合文字 (`が` = 単独 vs `が` = `か` + 濁点)
- 全半角混在 (`ＡＢＣabc１２３123`)
- ヌル文字 `\x00` (拒否されるべき)
- BiDi 制御 (`‮` Right-to-Left Override)
- ゼロ幅 (`​` Zero-Width Space)
- HTML 特殊文字 (`<>&"'`)
- 改行 (`\r\n` / `\n` / `\r`)

すべて入力 → 保存 → 再表示で同一の文字列 (lossless) または明示的拒否.

### ED7: Optimistic locking / 競合 `[Multi-user / Reliability]`
- ユーザ A が編集中、B が同じレコードを更新
- A が保存しようとする → 409 Conflict + 「他のユーザが更新しました」
- 強制上書き or 取り直し選択肢

### ED8: CSRF token `[Risk / Statutes]`
- フォーム送信時に CSRF token (`<input type="hidden" name="_token">`) が含まれる
- token を削除 / 改ざんして submit → 403
- token は session ごと / 1 時間ごとに更新

### ED9: 未保存変更でページ離脱 `[State / User]`
- 1 文字編集 → 別ページへ navigate (リンククリック / `window.location`)
- `beforeunload` 確認ダイアログ表示
- 「はい」で離脱、「いいえ」で残留
- 保存後の離脱では確認なし

### ED10: キャンセル / 保存ボタン挙動 `[State / User]`
- キャンセル → 確認なしで一覧 (or 詳細) へ
- 保存 → 詳細へ + 成功通知 / 同画面リロード + 成功通知
- 保存中は「保存中...」表示 + ボタン disable (二重 submit 防止)

### ED11: ネットワーク中断 (Interruptions) `[Risk / Reliability]`
- 保存ボタン押下 → 即 offline (`context.set_offline(True)`)
- エラー表示 + リトライボタン
- offline 解除後、再送信で成功

### ED12: ファイルアップロード `[Domain / Statutes]`
- 拡張子: 許可された拡張子のみ
- MIME type: 拡張子と内容の整合 (拡張子偽装検査)
- サイズ上限: max-1 / max / max+1
- 同名: 上書き / リネーム / 拒否
- 0 byte / 巨大ファイル
- ウイルススキャン (EICAR test file が拒否されるか — 仕様による)

### ED13: 値が変わっていない状態の保存 (no-op) `[State / Reliability]`
- プリフィル値のまま「保存」押下
- 期待: 200 OK or 304 Not Modified
- audit log には記録 / されない (仕様による)

### ED14: 削除ボタン (edit から削除) `[State / Functional]`
- 削除確認 dialog
- 削除 → 一覧へ
- アンドゥの猶予 (5 秒) があれば動作確認

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| BVA | ED2 (境界値) |
| EP | ED6 (文字種分割) |
| Decision Table | ED4 (必須組合せ), ED5 (validation 状態) |
| State Transition | ED9 (clean→dirty→saving→saved/error) |
| Domain Testing | ED12 (ファイル種別) |
| Multi-user | ED7 |
| Risk Testing | ED8 (CSRF), ED11 (interruption) |

## Playwright 実装パターン

```python
# プリフィル値の検証
expect(page.get_by_label("商品名")).to_have_value("既存の商品")

# 境界値テスト (parametrize)
@pytest.mark.parametrize("password,expected", [
    ("1234567", "短すぎ"),       # min-1
    ("12345678", None),          # min OK
    ("a"*64, None),              # max OK
    ("a"*65, "長すぎ"),          # max+1
])
def test_password_boundary(page, password, expected):
    page.get_by_label("パスワード").fill(password)
    page.get_by_role("button", name="保存").click()
    if expected:
        expect(page.get_by_role("alert")).to_contain_text(expected)
    else:
        expect(page).to_have_url(re.compile(r"/items/\d+$"))

# CSRF token 削除攻撃
page.evaluate("document.querySelector('input[name=_token]').remove()")
page.get_by_role("button", name="保存").click()
# 期待: 403 表示
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y label / aria-invalid), C3 (CSRF), C6 (offline), C8 (console error).

## 参考文献

- ISTQB CTFL 4.2.1 (BVA), 4.2.2 (Decision Table)
- OWASP CSRF Prevention Cheat Sheet
- MDN Client-side form validation
- Hendrickson "Boundaries" / "Goldilocks" / "Constraints"
