# #744: google-auth と google-drive を廃止し、Google Workspace の操作を gws の Skill へ一本化する

正は課題の本文（#744）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> `google-auth` と `google-drive` の 2 つの Skill を廃止する。Google Workspace の操作は **gws**（Google Workspace CLI、npm の `@googleworkspace/cli`）で行う単一の Skill へ置き換える。
>
> 新しい Skill の流れは次の 3 段だけにする。
>
> 1. `command -v gws` で gws の有無を確かめる
> 2. 無ければ `npm install -g @googleworkspace/cli` で入れる
> 3. `gws auth status` で認証を確かめ、未認証なら利用者に `! gws auth login` を案内する（ブラウザでの承認が要るため、エージェントが直接実行しない）。その後の操作はすべて gws で行う
>
> Skill 名は仮に `google-workspace` とする。

起票時の「なぜ変えるか」「範囲」「前提となる課題」「決めたこと」は下の節に原文のまま残す。

## 目的

- NDF の Google Workspace の操作を、gws の 1 つの経路と 1 つの Skill にまとめる。Python の依存・`uv.lock` 2 つ・`client_secret.json` の置き場所の案内・トークンの自前管理を NDF から無くす
- gws の有無の確認・導入・認証確認は決まった手順なので、スクリプトが状態を返し、LLM は導入の同意の判断だけを持つ（Value 4）

## なぜ変えるか（起票時の原文）

- **`google-drive` の機能はすべて gws で置き換えられる。** 2026-09-18 に gws 0.22.5 で Doc のエクスポート（text/plain・application/pdf）を実測し、取得できることを確かめた。

  | 現行（`gdrive_fetch.py`） | gws |
  |---|---|
  | `--id X`（エクスポート） | `gws drive files export --params '{"fileId":"X","mimeType":"text/plain"}' --output f.txt` |
  | `--download` | `gws drive files get --params '{"fileId":"X","alt":"media"}' --output f` |
  | `--upload` | `gws drive +upload f`。**既定は非公開。** 公開は明示したときだけ `gws drive permissions create`（`anyone` / `reader`）を続ける（#879 と揃える） |

- **gws は Drive 以外も扱える。** Sheets・Docs・Slides・Gmail・Calendar・Chat・Apps Script まで同じ CLI で操作できる。現行の `google-auth` が持つスコープの略記（spreadsheets / script / chat / calendar）は、Drive 以外の API を Python から叩く前提で置かれているが、その操作を行う Skill は無い
- **Python の依存と `uv.lock` 2 つ、`client_secret.json` の置き場所の案内、トークンの自前管理が不要になる**

## 前提となる課題（起票時の原文）

**playwright-kit の Drive 連携を外す**（2026-10-03 利用者の決定）。playwright-kit は `google-auth` の `get_credentials()` を Python から import している（`_drive_auth.py:55-83`）ため、`google-auth` を消すと Drive への保管が動かなくなる。gws への移行（#745）は not planned で閉じたため、Drive 連携そのものをこの課題の範囲で外す（範囲の表の playwright-kit の行）。

## 決めたこと（起票時の原文）

- **gws のインストールは、入れる前に取得元（npm の `@googleworkspace/cli`）と実行するコマンドを示し、利用者の同意を得てから行う。** 利用者の環境へ npm の全体インストールを行うためである。`official-skills-autoloader` の同意の取り方と揃える
- **前提の確認とインストールはスクリプトにし、LLM は同意の判断だけを持つ。** 上の 3 段は決まった手順なので、スクリプト（例 `gws-check`）が状態を JSON で返し、インストールの同意は #846 の承認ゲートの形で示す（#845 の判断の 3 段の段 1）
- **旧 Skill を予備の経路として残さない。** gws は Google の公式サポート外で（起動時に「This is not an officially supported Google product」と出る）、版も 0.x だが、経路を 2 つ持つと手順と検査が倍になる

## 前提

要求の段で決めきれない点を、後から成否を判定できる文で置く（2026-10-04、人へ問わずに置いた。設計の承認で覆してよい）。

- 前提 1: 新しい Skill の名前は `google-workspace` とする。マーケットプレイス内の他の Skill・プラグインと名前が衝突しない（`git grep -n "google-workspace"` が `issues/` 以外で 0 件、2026-10-04 に確認）
- 前提 2: gws の導入（`npm install -g`）は共通原則の C6（利用者のローカル環境を黙って書き換えない）に当たる。同意なしに入れる経路を作らない。`pace: fast` / `auto` でも同意は省かない
- 前提 3: `gws auth login` はブラウザでの承認が要り、トークンは gws が利用者のホーム（gws の設定ディレクトリ）に置く。NDF のスクリプトは `gws auth login` を実行せず、トークンを読まない・書かない（C1）
- 前提 4: 確認のスクリプトが返す状態は「gws が無い / 有るが未認証 / 認証済み」の 3 つと、それぞれの次の手（導入の同意を求める / `! gws auth login` を案内する / 操作へ進む）である。npm が無い環境は「導入できない」として理由を返して止まる
- 前提 5: playwright-kit の Drive 連携は、gws へ移さずに削除する（起票時の決定）。Drive への保管を使っていた利用者には、`google-workspace` の `gws drive +upload` を手で使う案内を CHANGELOG に書く
- 前提 6: playwright-kit からの機能の削除は利用者から見える変更なので、playwright-kit の `version` もこのスプリントで上げる。上げ幅は配布（`release`）で決める
- 前提 7: 受け入れ条件 3 の grep が拾う語には、Python のパッケージ名 `google-auth`（`uv.lock` / `pyproject.toml` の依存）も含む。Drive 連携を外せば依存も消えるので、除外にしない

## 対象範囲

含む（起票時の範囲の表に、2026-10-04 の `git grep` で見つかった行を足したもの）:

| 対象 | 変更 |
|---|---|
| `plugins/ndf/skills/google-auth/` `plugins/ndf/skills/google-drive/` | 削除 |
| `plugins/ndf/skills/google-workspace/SKILL.md`（仮） | 新設（上の 3 段と、よく使う操作の対応表） |
| 確認と導入のスクリプト（例 `gws-check`） | 新設。状態を JSON と終了コードで返す（#846 の契約） |
| `plugins/ndf/manifests/*-skills.txt`（4 つ） | 2 行を 1 行へ |
| `plugins/ndf/.claude-plugin/plugin.json` `plugins/ndf/.codex-plugin/plugin.json` | skills の列挙を更新 |
| `plugins/ndf/skills/document-systems/SKILL.md` `references/system-gdrive.md` | `gdrive_fetch.py` の手順を gws へ |
| `plugins/ndf/skills/document-sources/SKILL.md` | 参照先の Skill 名 |
| `scripts/check-cross-skill-refs.py`（#116 の例外） `scripts/tests/test_google_auth_codistribution.py` | 例外と検査を削除 |
| `scripts/tests/test_cross_skill_refs.py` `scripts/tests/test_refactoring_codistribution.py` | `google-auth` / `google-drive` を例に挙げた説明と検査を直す（追加） |
| `README.md` `docs/ndf-plugin-reference.md` `docs/specifications/ndf-skill-inventory/01-ledger-and-criteria.md` | Skill の一覧と判定を更新 |
| `docs/specifications/ndf-documentation-mode.md` | Drive の取得を持つ Skill の名前（追加） |
| `plugins/playwright-kit/skills/playwright-kit-ops/scripts/_drive_auth.py` `gdrive_upload_dir.py` `build_gdoc_with_drive_links.py` | 削除（playwright-kit の Drive 連携を外す） |
| 同じ `_drive_auth` を import する経路（`scripts/upload_evidence.py` `scripts/upload_md_as_gdoc.py` `playwright_kit/uploaders/` と、それを呼ぶ `pytest_sessionfinish`）と、`pyproject.toml` の `drive` の extra・`templates/pyproject.toml.runtime` の `google-auth` の依存・2 つの `uv.lock` | Drive への保管の経路を外す |
| `plugins/playwright-kit/skills/playwright-kit-ops/tests/test_drive_auth_candidates.py` `test_build_gdoc_with_drive_links.py` `test_upload_evidence.py` ほか Drive 連携のテスト | 削除か、Drive の経路を外した形へ直す |
| `plugins/playwright-kit/skills/playwright-evidence/SKILL.md`（description の「store its evidence on Google Drive」、Drive への保管の手順・`--pwk-drive-folder`・環境変数とトラブルシュートの Drive の行）、`playwright-kit-ops/SKILL.md` と `templates/runtime-README.md` の Drive 連携の節、`plugins/playwright-kit/README.md` の Skill の表 | 手順と案内を外す |
| `plugins/ndf/scripts/tests/fixtures/wait_notice_corpus.json` | 過去の通知の文面を集めたテストの素材。変えない（受け入れ条件 3 の除外に入れる） |
| `.ndf/pace.json` の `boundary_paths` | `plugins/ndf/skills/google-auth/**` を `plugins/ndf/skills/google-workspace/**` へ置き換える（2026-10-05 ゲート 1 で利用者が決定。C7 の確認は済み） |

含まない:

- gws の Drive 以外の操作（Sheets・Gmail・Calendar など）を手順として書くこと。対応表は Drive の 3 操作と、Docs のエクスポートに限る（#530 の Slides の差し替えは別の課題）
- playwright-kit の Drive 連携を gws へ移すこと（#745 は not planned）
- gws の版の固定・gws の不具合の回避策
- `issues/` の過去の課題の記録（`issues/issue-1078-pace-fast-design.md` `issues/issue-1142-design-migration.md` など）と `CHANGELOG.md`・`docs/development-history/`・`docs/ndf-version-decisions.md` の書き換え

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | gws の有無と認証の状態を確かめた | 利用者が Google Workspace の操作を頼み、Skill が確認のスクリプトを打った | スクリプトが異常で終わったら、出力をそのまま示して止まる | — |
| E2 | 導入の同意を求めた | E1 が「gws が無い」を返した | npm が無ければ導入できない理由を示して止まる | E1 |
| E3 | 利用者が導入に同意した / 断った | E2 の承認ゲート | 断ったら導入せずに止まる（gws の無い代替の経路は持たない） | E2 |
| E4 | gws を導入した | E3 の同意 | `npm install -g` が失敗したら、出力と終了コードを示して止まる。もう一度 E1 から確かめる | E3 |
| E5 | 認証を案内した | E1（または E4 の後の E1）が「未認証」を返した | 利用者が承認を終えなければ操作へ進まない | E1 |
| E6 | 利用者が `! gws auth login` で認証した | E5 の案内 | 承認が失敗したら、gws の出力を示して E5 からやり直す | E5 |
| E7 | Drive のファイルをエクスポート / ダウンロード / アップロードした | E1 が「認証済み」を返した後の利用者の依頼 | gws の終了コードが 0 でなければ、出力を示して止まる | E1（認証済み） |
| E8 | アップロードしたファイルに公開リンクを付けた | 利用者が公開を明示した | 付与が失敗したら、ファイルは非公開のまま残ると示す | E7（アップロード） |

## 用語

| 用語 | 意味 |
| --- | --- |
| gws | Google Workspace CLI（npm の `@googleworkspace/cli`）。Google の公式サポート外の CLI |

## 受け入れ条件

- [ ] gws の無い環境で、Skill の手順どおりにインストールから Doc のエクスポートまで通る
- [ ] gws のある環境で、エクスポート・ダウンロード・アップロード（既定は非公開、明示したときだけ公開リンク付与）が Skill の対応表どおりに通る
- [ ] 確認のスクリプトが、`PATH` に gws が無いとき・未認証の gws のとき・認証済みの gws のときに、それぞれ別の状態と次の手を JSON で返す（gws をスタブに置き換えた自動テストで確かめる）
- [ ] 確認のスクリプトは、同意の印（引数）を受け取らない限り `npm install -g` を実行しない。`gws auth login` はどの引数でも実行しない（自動テストで、スタブの npm・gws が呼ばれた引数を記録して確かめる）
- [ ] npm が無い環境で、確認のスクリプトが導入できない理由を返し、0 以外の終了コードで終わる
- [ ] `git grep -E "google-auth|google-drive|gdrive_fetch|google_auth"` が、履歴の文書（`CHANGELOG.md`・`docs/development-history/`・`issues/`・`docs/ndf-version-decisions.md`・`docs/ndf-version-decisions-v6-v10.5.md`）・`plugins/ndf/scripts/tests/fixtures/wait_notice_corpus.json` 以外で 0 件（playwright-kit を含む）
- [ ] playwright-kit の Skill（description を含む）・README・テンプレートと、playwright-kit を紹介する `.claude-plugin/marketplace.json` の description・根の `README.md` の行に、Drive への保管の手順と案内が残らない（`git grep -niE "drive" plugins/playwright-kit .claude-plugin/marketplace.json README.md` の残りが playwright-kit の Drive 連携の案内でない）
- [ ] `plugins/ndf/manifests/*-skills.txt` の 4 つに `google-workspace` が 1 行ずつあり、`google-auth` と `google-drive` が無い
- [ ] `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .`、`uv run --with pytest pytest scripts/tests plugins/ndf -q`、playwright-kit のテスト（`plugins/playwright-kit/skills/playwright-kit-ops/tests`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | Google Workspace の操作の経路は gws の 1 つだけにし、旧 Skill を予備の経路として残さない |
| 移行性 | 旧 Skill のトークン（`google-auth` が置いたもの）は gws へ移さない。利用者は `gws auth login` で認証し直す。CHANGELOG に移行の手順を書く |
| セキュリティ | アップロードの既定は非公開（#879）。NDF のスクリプトはトークンと `client_secret.json` を読まない・書かない（C1）。gws の導入は同意を得てから行う（C6） |
| システム環境 | gws の導入には npm が要る。npm が無い環境では導入せずに理由を示す |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`/ndf:google-auth` と `/ndf:google-drive` が消え、`/ndf:google-workspace` が増える。playwright-kit の Drive への保管（`--pwk-drive-folder` など）が消える。互換の経路は持たない |
| データ | 変わらない（利用者の Drive のデータに触れる操作は、利用者の依頼のときだけ gws が行う） |
| 既存の振る舞い | `document-systems` の Google Drive の取得の手順が gws に変わる。`google-auth` が持つ Drive 以外のスコープの略記は無くなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q`、playwright-kit の `tests`（`uv run --frozen --project . --all-extras pytest plugins/playwright-kit -q`） |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`python3 scripts/check-cross-skill-refs.py`、`claude plugin validate .`、受け入れ条件の `git grep` |
| 手動確認 | 受け入れ条件 1・2（gws の無い環境からの導入とブラウザでの認証、Drive の 3 操作と公開リンクの付与）。ブラウザでの承認が要るため自動化できない。リリース後テストで利用者の環境で確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | Skill の配布は `plugins/ndf/manifests/` が決める（`CLAUDE.md` の「NDF の Skill 構成」）。確認のスクリプトは新しい Skill の `scripts/` に置く |
| コーディング規約 | frontmatter は `plugins/ndf/skills/AUTHORING.md`（`check-skill-frontmatter.py`）。スクリプトの結果 JSON・終了コード・承認ゲートの提示は #846 の契約 |
| テスト戦略 | 確認のスクリプトは gws と npm をスタブにした単体テストで分岐を担保する。gws との実際の通信とブラウザでの認証はリリース後テストで確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、manifests と plugin.json の同期、受け入れ条件の `git grep` |
| 確認してから行う | gws の導入（利用者の同意。C6）、`.ndf/pace.json` の変更（C7） |
| 行わない | `gws auth login` の代行、トークンの読み書き、旧 Skill の予備の経路を残すこと、playwright-kit の Drive 連携の gws への移植、履歴の文書の書き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| playwright-kit の版の上げ幅（機能の削除を MAJOR とするか） | `release` の配布の工程 | 配布 |
