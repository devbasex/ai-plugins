# #928: 中継の導入を明示の操作にし、/ndf:restart を足し、関門を越えない守りを入れる — 実装計画

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/928
- 要求と受け入れ条件: [issue-928-requirements.md](issue-928-requirements.md)（AC1〜AC30）
- 設計: [issue-928-design.md](issue-928-design.md)・決定の記録: [issue-928-design-decisions.md](issue-928-design-decisions.md)・送り込みの実測: [issue-928-design-injection.md](issue-928-design-injection.md)
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/932（develop へマージ済み。関門 1 を 2026-09-24 に利用者が承認）
- 範囲外: 送り込みの実装 #931、既存の定義の判定を別ファイルへ広げる #936、devbase の読み込み devbasex/devbase#253（この PR では待たない）

## モード

standard（既存の中継の振る舞いを変え、Skill 2 つと hook を足す。設計の関門は通過済み）。

## 目的と非目的

達成したい状態:

- SessionStart hook が利用者のシェル設定を書かない。導入・取り外し・状態は `/ndf:install-wrapper` だけが持つ
- 10.17.4 の自動の囲みを 1 度だけ知らせ、写しを今の版へ置き直す（版は後退させない）
- `/ndf:restart` で中継の下の再起動を 1 回の入力に畳む
- 切れ目の `/exit` が質問（`AskUserQuestion`）の答えにならない（G1〜G3）

やらないこと:

- 送り込み（`/exit` 以外の入力）の実装（#931）
- Codex / Kiro / agy への配布
- 確定仕様 `docs/specifications/ndf-relay-segment-restart.md` の改訂（確定仕様化の工程で行う）
- 版上げ（release が行う）

## 受け入れ条件

要求の文書の AC1〜AC30 をそのまま使う（AC19・AC30 は起票済み）。各タスクに番号で紐づける。

## 代替案と採否

設計の決定 1〜21 のとおり。実装で新たに選んだものだけを置く。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `relay.py` の 1 ファイルに副命令を足す | 採用 | 写しは 1 ファイルで配られ、`startup` も `run` も同じ写しから動く。分けると写しの置き直しが複数ファイルになる |
| B | 導入の処理を別モジュールに分ける | 不採用 | 写しが 1 ファイルである前提（決定 5・21）を崩す |

## 不変条件

- `startup` はシェルの設定・中継の rc・読み込み先のファイルを書かない。常に終了コード 0
- `install` / `uninstall` は囲みの外を 1 バイトも変えない。書く前にバックアップを取る。ロックが取れなければ何も変えない
- 共有の写しを古い版の `startup` が置き直さない
- 中継は、`question` がある間・印の後に応答の行がある間は子の端末へ書かない
- テストは一時の HOME・`XDG_*`・`CLAUDE_CONFIG_DIR` の下でだけ動かす

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `relay.py install` | 自動の導入から明示の導入へ。終了コード 1 / 3 を持つ | hook からは呼ばなくなる。10.17.4 の hook（古い版に戻した場合）は古い写しの install を呼ぶので影響なし |
| `relay.py` の新しい副命令 | `uninstall` / `status` / `startup` / `question` | 追加のみ |
| 作業ディレクトリ | `question`・`question.lock` を足す。`next.json` の形は変えない | 古い版の中継は読まないだけ |
| 状態の親 | `rc-user`・`rc-noticed` を足す。`rc-added` / `rc-skipped` はそのまま | 10.17.4 の I5 は `rc-added` だけを見る |
| `NDF_RELAY_AUTO` / `NDF_RELAY_EXIT_GAP` | 使わなくなる | 置いたままでも害は無い |

## 修正対象

- `plugins/ndf/scripts/relay.py`
- `plugins/ndf/scripts/tests/test_relay.py`・`plugins/ndf/scripts/tests/fixtures/relay_fake_claude.py`
- `plugins/ndf/hooks/claude.json`
- `plugins/ndf/skills/install-wrapper/SKILL.md`（新規）・`plugins/ndf/skills/restart/SKILL.md`（新規）
- `plugins/ndf/manifests/claude-skills.txt`
- `plugins/ndf/skills/development-workflow/references/relay.md`・`plugins/ndf/README.md`
- 生成物の同期が要る配布物（`scripts/build-runtime-plugins.sh` があれば）

## タスク分解

### Task 1: 写しの置き場所と版の比べ方
- **対象:** `relay.py`（`config_dir()`・`copy_path()`・`version_key()`・`plugin_version()`・`place_copy()`）
- **変更:** 写し・写しの版・中継の rc を `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` に置く。版を `X.Y.Z[-dev.N|-rc.N]` で比べる。`_is_self` に新しい写しを含める
- **満たす AC:** AC27・AC29 の比較部分
- **進め方:** 版の比較の単体テスト → 実装

### Task 2: `startup`（SessionStart）と hook の差し替え
- **対象:** `relay.py`（`cmd_startup`）・`hooks/claude.json`
- **変更:** 在る写し・旧い写しの置き直し（`copy.lock`、後退しない）と、自動の囲みの 1 度だけの知らせ（`install.lock`・`rc-noticed`）。hook の `relay.py install` を外し `startup|resume` の新しい 1 件へ
- **満たす AC:** AC1・AC2・AC9・AC10・AC11・AC29
- **進め方:** 失敗するテスト → 実装

### Task 3: 明示の `install`
- **対象:** `relay.py`（`cmd_install` を E0〜E6 へ）
- **変更:** 引用できないパス・既存の定義・閉じの無い囲み・他シェルで終了コード 1。写し・中継の rc・読み込み（読み込み先のファイルか囲み）を置く。10.17.4 の囲みの中を置き換える。`rc-added`・`rc-user` に足す。両ロックを 2 秒で取れなければ 3
- **満たす AC:** AC3・AC4・AC5・AC27・AC28
- **進め方:** 失敗するテスト → 実装。既存の install のテストは新しい契約へ置き換える

### Task 4: `uninstall` と `status`
- **対象:** `relay.py`（`cmd_uninstall`・`cmd_status`）
- **満たす AC:** AC6・AC7・AC28
- **進め方:** 失敗するテスト → 実装

### Task 5: 関門を越えない守り（G1〜G3）
- **対象:** `relay.py`（`cmd_question`・`cmd_mark`・`Relay._tick`・`Relay.end_child`・`Relay.loop`）・`hooks/claude.json`（`PreToolUse` / `PostToolUse` の `AskUserQuestion`）・試験用の偽の claude（質問の印を置く・会話の記録に行を足す）
- **変更:** `question open` / `close`、静まりの条件 (4)(5)、`question.lock` の中の確かめ直しと 1 回の write、書いた後の質問の間は SIGTERM までの秒を数えない、子が終わった後に印を読み直す
- **満たす AC:** AC23・AC24・AC25・AC25b・AC26・AC26b
- **進め方:** 失敗するテスト → 実装

### Task 6: 2 つの Skill と manifests
- **対象:** `skills/install-wrapper/SKILL.md`・`skills/restart/SKILL.md`・`manifests/claude-skills.txt`
- **満たす AC:** AC8・AC12〜AC16
- **進め方:** テスト駆動を適用しない（Skill の本文。検査は frontmatter の検査と manifests。文言のテストは書かない）

### Task 7: 文書と通しの確かめ
- **対象:** `relay.md`・`README.md`
- **変更:** 始め方を明示の導入へ、止め方・戻し方に `uninstall`、`/ndf:restart`、守りが中継を起動し直した後から効くこと
- **満たす AC:** AC20〜AC22（AC21 は隔離した HOME・`CLAUDE_CONFIG_DIR` で本物の Claude Code を 1 度通し、記録を PR に残す）

## 影響範囲

- `token-guard.sh` は `relay.py is-child` を呼ぶだけで、変えない
- 既存の中継のテスト（`run` / `mark` / `stop`）は退行の検査として残す。`install` の旧テストは新しい契約へ置き換える

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `relay.py` は 1035 行の 1 ファイルで、切り替えの本体（`Relay`）と導入の処理が同居する | 実装の後の構造改善で足りる。導入の処理は `# --- install` の節にまとまり、`Relay` とは関数を共有しない。テストが厚い（擬似端末の通し）ので、タスクごとにテストを通す |
| 本物の claude の通しで利用者の設定を変える | `env -i` と一時の HOME・`CLAUDE_CONFIG_DIR`・`bash --norc` で隔離する |

## 切り戻し手順

- PR を revert すれば 10.17.5 の振る舞いへ戻る。利用者の手元に新しい版の囲みや写しが残っても、10.17.4 の I4 は囲みがあれば何もしない（設計の「移行性」）

## 完了の定義

- [ ] AC1〜AC30 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `test_relay.py` と全体テストが通る。`claude plugin validate .`・`check-skill-frontmatter.py` が終了コード 0
