## 🔧 PRレビュー指摘対応 (dd08b91)

### 対応した指摘

| # | 優先度 | ファイル | 指摘内容 | 対応 |
|---|--------|---------|----------|------|
| 1 | **P1** | `lease_screening_service.py` | CICスコア取得が日本語キーに対応していない | ✅ 修正済み |
| 2 | **P1** | `inquiry.py` | 同様にCICスコア取得が日本語キーに対応していない | ✅ 修正済み |
| 3 | **P1** | `docs/lease-screening-*.md` | final_categoryラベルがコードと不一致 | ✅ 修正済み |
| 4 | **P2** | `docs/lease-screening-*.md` | TIER_VERSIONがコードと不一致 | ✅ 修正済み |

---

### 修正1・2: CICスコア取得キー修正

**問題**: `translated_data`（日本語キー変換後）から`creditGuidance`を参照していたため、CICスコアが常に`None`になっていた

**修正**: 翻訳後のキー名を使用

```python
# Before
credit_answer = cic_result.get("信用照会回答エリア", {})
cic_score_str = credit_answer.get("creditGuidance")

# After
credit_answer = cic_result.get("信用照会回答エリア", {})
cg_area = credit_answer.get("クレジットガイダンスエリア", {})
cic_score_str = cg_area.get("指数")
```

**翻訳マップ（jp.yml）確認結果:**
- `creditGuidance` → `クレジットガイダンス`
- `creditGuidanceArea` → `クレジットガイダンスエリア`
- `cgResult` → `指数`

---

### 修正3: final_categoryラベル名更新

ドキュメントをコード実装に合わせて更新:

| 旧ドキュメント記載 | 新ドキュメント記載（コードと一致） |
|-------------------|----------------------------------|
| `自社審査_否決_パティオ通販` | `自社審査_否決_ナーチャ連携` |
| `自社審査_否決_買取` | `自社審査_否決_ナーチャ連携_買取案内` |
| `自社審査_完全否決` | `自社審査_否決_完全否決` |

---

### 修正4: tier_version更新

| 項目 | 旧値 | 新値 |
|------|------|------|
| `tier_version` | 20260201 | 20260205 |

criteria37-2.sql対応でバージョンが更新されていたため、ドキュメントも同期。

---

### テスト結果

```
============================================================
TierCalculator 単体テスト (criteria37-2.sql対応)
============================================================
全テスト完了!
============================================================
```

### 修正ファイル

- `cic-proxy/lib/lease_screening_service.py` - CICスコア取得キー修正
- `cic-proxy/lib/inquiry.py` - バッチTier判定のCICスコア取得修正
- `docs/lease-screening-api.md` - final_category・tier_version更新
- `docs/lease-screening-changelog.md` - 変更履歴追記・ラベル名更新

**PR URL**: https://github.com/takemi-ohama/ai-plugins/pull/13
