# PR #57 Gemini 独立技術レビュー

| メタ | 値 |
|---|---|
| **PR** | [#57 — feat(ndf)!: playwright-scenario-test v0.3.0 — pure pytest-playwright 完全移行 (v4.2.0)](https://github.com/takemi-ohama/ai-plugins/pull/57) |
| **レビュアー** | Gemini CLI v0.39.1 (`gemini -y -p` headless mode) |
| **観点** | 技術 (慣用性 / 可読性 / コード品質 / 保守性 / セキュリティ / テストカバレッジ) |
| **実行日** | 2026-04-26 |
| **実行時間** | 約 8 分 |
| **背景** | Codex 3 ラウンド + Amazon Q 5 件 全対応済みの状態で、未指摘観点を中心に依頼 |
| **結論** | **Approve** |
| **指摘** | Critical: 0 / Major: 4 (全て賛辞) / Minor: 3 (うち実質的な指摘 1) |
| **対応 commit** | [`19b0efb`](https://github.com/takemi-ohama/ai-plugins/commit/19b0efb) — Minor 5.1 (storage_state origin 跨ぎ) を予防修正 |
| **PR コメント** | https://github.com/takemi-ohama/ai-plugins/pull/57#issuecomment-4321848186 |

---

# PR #57 Gemini 独立レビュー

## 総評
**結論: Approve**

本 PR は `playwright-scenario-test` を独自の YAML DSL から純粋な `pytest-playwright` プラグインへと見事に移行させています。単なるライブラリの置き換えにとどまらず、`pytest` のエコシステム（`fixture`, `marker`, `hook`, `pytester`）を深く理解した、極めて完成度の高い実装です。特に、xdist 並列実行時の衝突回避、認証キャッシュ、HAR ライフサイクルの考慮、そして非 UI テストへの影響を最小限にする設計など、シニアエンジニアらしい配慮が随所に見られます。

## 1. pytest-playwright 慣用性 (Idiomatic) に関する指摘
### 1.1 `autouse` フィクスチャの条件付きスキップ [重要度: Major]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/fixtures/a11y.py:59`
- 状況: `_ndf_a11y_autouse` が `autouse=True` ですが、内部で `if "page" not in request.fixturenames: return` を行っています。
- 評価: これは非常に優れた設計です。`autouse=True` なフィクスチャが不用意に `page` を要求すると、全てのテスト（DB テストや単体テスト等）でブラウザが起動してしまいます。`request.fixturenames` を確認することで、Playwright を使うテストのみに計測を限定し、テストスイート全体の速度低下を防いでいます。`cwv.py` でも同様の処理が行われており、一貫性があります。

### 1.2 `browser_context_args` による HAR/Trace の個別制御 [重要度: Major]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/fixtures/evidence.py:165`
- 状況: `browser_context_args` を `function` スコープで override し、テストごとに `record_har_path` を動的に注入しています。
- 評価: `pytest-playwright` の機能を活かした最も慣用的な方法です。旧実装の session 単位 HAR を廃止し、テストごとの隔離（Isolation）を担保している点は、デバッグの容易性を大きく向上させます。

## 2. 可読性に関する指摘
### 2.1 独自マーカーのドキュメント化 [重要度: Minor]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/pytest_plugin.py:75`
- 指摘: `_NDF_MARKERS` を定義し、`pytest_configure` で `config.addinivalue_line("markers", ...)` を行っています。
- 評価: これにより `pytest --markers` を実行した際に NDF 独自のマーカー（`page_role`, `role`, `phase`, `priority`）の説明が表示されるようになり、利用者にとってのセルフドキュメンテーションが効いています。

## 3. コード品質に関する指摘
### 3.1 エラーメッセージの切り詰め [重要度: Minor]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/pytest_report.py:114`
- 状況: `lines.append(e.error_message[:2000])`
- 評価: 長大なスタックトレースやログによって `report.md` が肥大化するのを防ぐ適切な配慮です。

### 3.2 ログイン送信ボタンのフォールバック順序 [重要度: Major]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/fixtures/auth.py:53`
- 評価: `_submit_login_form` において、明示的なセレクタ → 汎用的な role/text マッチ → パスワード欄での Enter キー、という順序で試行しています。この「最後に Enter を押す」というフォールバックは、ボタンの特定が困難な難解な UI においてもログインを成功させる可能性を高める、実戦的な工夫です。

## 4. 保守性に関する指摘
### 4.1 テンプレートの充実 [重要度: Major]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/templates/`
- 評価: `conftest.py` だけでなく、`test_auth.py.template` などの役割別テンプレートが用意されており、利用者が迷わずに導入できるようになっています。特に `SKILL.md` のクイックスタートが「雛形をコピーして動かす」という手順で完結しており、OSS 的な使い勝手が非常に良いです。

## 5. セキュリティに関する指摘
### 5.1 `localStorage` 注入時の origin 跨ぎ [重要度: Minor]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/scenario_test/fixtures/auth.py:192`
- 状況: `state.get("origins")` をループして `page.goto(url)` してから `localStorage.setItem` を実行しています。
- 懸念: 外部のオリジン（広告や計測タグ等）が `storage_state` に含まれている場合、それらに対しても `goto` が試行されます。
- 評価: ただし、`try...except` で囲まれており、失敗しても認証自体（Cookie）には影響しないようになっているため、実用上の問題はありません。

## 6. テストカバレッジに関する指摘
### 6.1 `pytester` による高度な検証 [重要度: Major]
- ファイル: `/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/tests/test_pytester_integration.py`
- 評価: `pytest` の内部挙動に依存する「HAR の teardown 時フラッシュ」や「xdist 時の開始時刻計算」を、`pytester` を用いて隔離環境で本物の pytest を走らせて検証している点は非常に高く評価できます。これにより、プラグインとしての堅牢性が担保されています。

## 7. 良い点 (Codex / Amazon Q が触れていない点を中心に)
- **`NdfTestEntry` の `ok` プロパティの定義**: `xfailed` を `ok=True` としつつ、`all_pass` の判定では `xpassed` (意図せず通ってしまったテスト) がある場合に `False` とするロジックは、品質管理の観点から非常に正確です。
- **`HUD_INIT_SCRIPT` の `sessionStorage` 連携**: ナビゲーションを跨いでもカーソル位置や字幕を維持するための JS 実装が丁寧です。

## 8. 統計
- Critical: 0 件
- Major: 4 件 (1.1, 1.2, 3.2, 4.1, 6.1 - 全て「高く評価」)
- Minor: 3 件 (2.1, 3.1, 5.1)
- 合計: 7 件

## 9. 承認可否
- 結論: **Approve**
- 根拠: `pytest-playwright` への移行が、単なる機能移行に留まらず、開発者の生産性とテストの信頼性を大幅に向上させるレベルで完遂されています。実装の細部（特に fixture の依存関係とライフサイクルの管理）にまでこだわりが感じられ、即座にマージして OSS 配布可能な品質に達していると判断します。
