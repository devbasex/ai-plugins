# relay のアカウントの容量の逆算（2026-09-28）

relay.py のアカウントを「残りの量」で選ぶ課題（未起票。`handoff-milestone26.md` の「次にやること」3b）の、倍率の表の既定値の根拠。
計算の材料とスクリプトは `~/.local/state/ndf/capacity-2026-09-28/`（analyze.py・units.py・attribution.py・fetch_usage.py と出力の analyze.txt・units.txt・attribution.txt・timeline.txt・usage_now.json）。

## 結論

- **5 時間の枠: 20x : 5x ≒ 4.0（範囲 3.4〜4.8、API 料金の換算額）**。公式の比（20 : 5）と一致。公式の Pro 比をそのまま既定値にしてよい
- **週の枠: 20x（nyle-personal）≒ 1,100 USD**（Opus 5.5 の料金で換算。上限の事例と今の利用率の 2 つの推定の差は 1% 未満）
- **5x と team の週の枠は上限に達した事例が無い**。ohama は 205〜350 USD 以上、team は約 700 USD（追加利用が混じりうる）。非公式の 1 : 3.5 : 6 は否定も確認もできない → 仮の既定値として置き、宣言で上書きできる形にする
- **比べる単位は API 料金の換算額が安定する**（5 時間の枠の 5x・team の 3 件で変動係数 5%）。重み無しのトークン数は変動係数 62%、Opus 5.5 の単価で統一した量は 36% で使えない
- team : 5x の 5 時間の枠は約 1.0（1 件）。公式の team premium（6.25）より低く、team の個人の tier（`default_claude_max_5x`）と合う → 倍率は `rateLimitTier` から決めればよい

## 事例（JST）

| 事例 | アカウント（帰属） | 枠 | 区間 | USD | モデル |
| --- | --- | --- | --- | ---: | --- |
| W1 | nyle-personal（中） | 週 | 09-08 15:00〜09-14 23:16 | 1,805 | Opus 5 |
| W2 | nyle-personal（中） | 週 | 09-15 15:00〜09-18 22:28 | 1,721 | Opus 5 |
| W3 | nyle-personal（高） | 週 | 09-22 18:27〜09-25 05:50 | 1,101 | Opus 5.5 65% |
| S2 | nyle-team（中） | 5h | 09-18 22:30〜09-19 00:21 | 53 | Opus 5 |
| S3 | team か ohama（低） | 5h | 09-25 05:58〜08:58 | 55 | Opus 5.5 |
| S4 | ohama?（低） | 5h | 09-27 21:00〜21:35 | 49 | Fable 5.1 |

- 20x の 5 時間の枠は今日の観測 7 点から約 210 USD（変動係数 8%）
- W1・W2（Opus 5 の時期、約 1,760 USD）は今の 1,100 USD と合わない。Opus 5 の重みが料金より軽いか、容量が変わったと見られる

## 材料と帰属

- 会話の記録 7,188 ファイル・82,225 応答（06-13〜09-29）。`~/.claude` は 09-27 19:48 JST から `/persistent/group/.claude` への symlink で、同じグループのコンテナで共有
- どのアカウントかは `/login` の時刻・上限の文面の reset 時刻・使用量の API の reset 時刻を突き合わせて決めた。09-08 以降の消費 6,039 USD のうち不明 0.9%
- Bedrock の期間（09-25 09:16〜09-27 19:44、`usage.inference_geo` で見分けた）の 502 USD は除いた
- 上限の文面: `You've hit your session limit · resets …` / `You've hit your weekly limit · resets …` / `You've hit your individual spend limit …`

## 未確定と気づいたこと

- 5x の週の容量の確定には、5x のアカウントをこの端末だけで使い、週の枠が 20% 以上動く間に `fetch_usage.py` で 2 点以上観測する
- nyle-personal の週の枠が 09-28 に予定外にリセットされた（原因不明）
- どのアカウントにも Fable の週の枠（`weekly_scoped`）がある。ohama は 58% で全体（38%）より先に尽きる
- team は追加利用が有効で、枠を超えても止まらず支出上限で止まる
