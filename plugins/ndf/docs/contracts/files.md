# 受け渡しのファイル

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

NDF の工程が書くファイル（設定・計測値・実行の記録・文書・受け渡し）である。SSOT は `contracts/domains/ndf-files.json` と保存先の表 `contracts/domains/locations.json`。形（項目）を約束するのは公開する行だけで、内部の行は保存先と種類だけを載せる

## 一覧

| 行 | 保存先 | パス | 種類 | 追跡 | 書き手 | 消す時点 | 公開か |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `approval` | `issues` repo:issues | `approval-<id>.md` | document | — | 承認ゲート（P2） | cleanup | 内部（予告なく変えてよい） |
| `archived-docs` | `issues-old` repo:issues/old | `<name>.md` | document | 追跡 | クリーンアップの工程 | kept | 内部（予告なく変えてよい） |
| `ask` | `ndf` repo:.ndf | `ask.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `ask-journal` | `mvv-state` state:.（NDF_MVV_STATE_DIR で差し替え） | `ask.jsonl` | record | — | 人への質問の守り（M3a） | kept | 公開 |
| `backlog` | `ndf` repo:.ndf | `backlog.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `backlog-before` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/before-<id>.json` | record | — | 棚卸し（M6） | kept | 内部（予告なく変えてよい） |
| `backlog-candidates` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/candidates.json` | record | — | 棚卸し（M6） | replaced | 内部（予告なく変えてよい） |
| `backlog-depths` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/depths.jsonl` | record | — | 棚卸し（M6） | kept | 内部（予告なく変えてよい） |
| `backlog-estimates` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/estimates.json` | record | — | 棚卸し（M6） | kept | 内部（予告なく変えてよい） |
| `backlog-journal` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/journal.json` | record | — | 棚卸し（M6） | kept | 内部（予告なく変えてよい） |
| `backlog-rank` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/rank.json` | record | — | 棚卸し（M6） | replaced | 内部（予告なく変えてよい） |
| `backlog-rejected` | `backlog-state` main:.ndf/backlog/state（NDF_UPKEEP_STATE_DIR で差し替え） | `<repo>/rejected.json` | record | — | 棚卸し（M6） | kept | 内部（予告なく変えてよい） |
| `code-metrics` | `ndf` repo:.ndf | `code-metrics.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `design` | `issues` repo:issues | `issue-<n>-design.md` | document | 追跡 | 設計の工程 | archived | 内部（予告なく変えてよい） |
| `design-detail` | `issues` repo:issues | `issue-<n>-design-detail.md` | document | 追跡 | 詳細設計の工程 | archived | 内部（予告なく変えてよい） |
| `estimate` | `ndf` repo:.ndf | `estimate.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `gate-rejections` | `mvv-state` state:.（NDF_MVV_STATE_DIR で差し替え） | `gate-rejections.jsonl` | record | — | 承認ゲート（P2） | kept | 公開 |
| `glossary` | `ndf` repo:.ndf | `glossary.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `gws-install-material` | `google-workspace` state:gws | `install.md` | document | — | gws の確認（S1d） | replaced | 内部（予告なく変えてよい） |
| `handoff` | `ndf-handoff` repo:.ndf/handoff | `<id>.md` | record | — | 中継（M3） | cleanup | 内部（予告なく変えてよい） |
| `instructions` | `ndf` repo:.ndf | `instructions.json` | ndf_settings | 追跡 | 人と解析（M7） | kept | 内部（予告なく変えてよい） |
| `issue-items` | `ndf` repo:.ndf | `issue-items.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `issue-request` | `issue-request` env:.（NDF_ISSUE_REQUEST_FILE で差し替え） | `.` | exchange | — | 起票（M7） | kept | 公開 |
| `loop-record` | `ndf-loop` main:.ndf/loop | `<loop>/loop.json` | record | — | 開発ループとスプリント（M1） | cleanup | 公開 |
| `mvv` | `ndf` repo:.ndf | `mvv.json` | ndf_settings | 追跡 | 人の承認（M7） | kept | 内部（予告なく変えてよい） |
| `mvv-body` | `ndf` repo:.ndf | `mvv.md` | ndf_settings | 追跡 | 人の承認（M7） | kept | 内部（予告なく変えてよい） |
| `mvv-signals` | `mvv-state` state:.（NDF_MVV_STATE_DIR で差し替え） | `mvv-signals.jsonl` | record | — | 承認ゲートの回答の写し（M7） | kept | 内部（予告なく変えてよい） |
| `mvv-validation` | `mvv-state` state:.（NDF_MVV_STATE_DIR で差し替え） | `mvv-validation.jsonl` | record | — | MVV 判定（M7） | kept | 内部（予告なく変えてよい） |
| `organizer-answer` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `answers/<id>.json` | exchange | — | 外のオーガナイザー（サービス） | kept | 公開 |
| `organizer-batch` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `batch.json` | exchange | — | 外のオーガナイザー（サービス） | kept | 公開 |
| `organizer-presented` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `presented/<n>-<k>.json` | exchange | — | リリース（M2） | kept | 公開 |
| `organizer-progress` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `progress/<n>.json` | exchange | — | リリース（M2） | kept | 公開 |
| `organizer-range` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `ranges/<n>.json` | exchange | — | 外のオーガナイザー（サービス） | kept | 公開 |
| `organizer-request` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `requests/<id>.json` | exchange | — | 計画とレーンのステートマシン（P3） | kept | 公開 |
| `organizer-result` | `organizer` env:.（NDF_ORGANIZER_DIR で差し替え） | `results/<n>.json` | exchange | — | リリース（M2） | kept | 公開 |
| `pace` | `ndf` repo:.ndf | `pace.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `parallel` | `ndf-parallel` repo:.ndf/parallel | `<id>/` | record | — | 計画のステートマシン（P3） | cleanup | 内部（予告なく変えてよい） |
| `plan-progress` | `ndf-tmp` repo:.ndf/tmp | `<sprint>/<n>-<plan>-state/progress.jsonl` | record | — | プランの実行（C2） | cleanup | 公開 |
| `project` | `ndf` repo:.ndf | `project.json` | ndf_settings | 追跡 | 人と解析（M7） | kept | 内部（予告なく変えてよい） |
| `project-measured` | `measured` state:measured | `<repo>/project.json` | measured | — | 解析とテストの実行（M7） | replaced | 内部（予告なく変えてよい） |
| `projects` | `ndf` repo:.ndf | `projects.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `relay-auth-held` | `relay-accounts` state:relay/accounts | `held.json` | measured | — | 中継のラッパー（M3b） | replaced | 内部（予告なく変えてよい） |
| `relay-copy-launcher` | `relay-copy` config:ndf | `relay.py` | exchange | — | 人（ndf relay install） | replaced | 内部（予告なく変えてよい） |
| `relay-copy-root` | `relay-copy` config:ndf | `relay.root` | exchange | — | 中継（M3b）・SessionStart hook（更新後の向け直し） | replaced | 内部（予告なく変えてよい） |
| `relay-hub-log` | `relay-hub` state:relay/hub | `hub-log.jsonl` | record | — | 中継のリレーハブ（M3） | cleanup | 内部（予告なく変えてよい） |
| `relay-hub-registry` | `relay-hub` state:relay/hub | `registry.json` | exchange | — | 中継（M3） | replaced | 内部（予告なく変えてよい） |
| `relay-hub-request` | `relay-hub` state:relay/hub | `requests-v2/<id>.json` | exchange | — | 中継の頼む側（M3） | run | 内部（予告なく変えてよい） |
| `relay-hub-result` | `relay-hub` state:relay/hub | `results/<id>.json` | exchange | — | 中継のリレーハブ（M3） | run | 内部（予告なく変えてよい） |
| `relay-hub-state` | `relay-hub` state:relay/hub | `hub.json` | exchange | — | 中継（M3） | replaced | 内部（予告なく変えてよい） |
| `relay-marker` | `relay-markers` state:relay/markers | `<id>.json` | exchange | — | 中継のラッパー（M3b） | run | 内部（予告なく変えてよい） |
| `relay-next` | `relay-wrappers` state:relay/wrappers | `<名前>/next.json` | exchange | — | Stop の hook（M3a） | run | 内部（予告なく変えてよい） |
| `relay-pinned-account` | `relay-accounts` state:relay/accounts | `pinned.json` | ndf_settings | — | 人（ndf relay account use） | replaced | 内部（予告なく変えてよい） |
| `relay-question` | `relay-wrappers` state:relay/wrappers | `<名前>/question.json` | exchange | — | PreToolUse の hook（M3a） | run | 内部（予告なく変えてよい） |
| `relay-stop` | `relay-wrappers` state:relay/wrappers | `<名前>/stop` | exchange | — | ndf relay stop（M3b）と人（touch） | run | 内部（予告なく変えてよい） |
| `relay-usage-fetch` | `relay-account-store` config:ndf/accounts（NDF_ACCOUNTS_DIR で差し替え） | `.usage-fetch/<名前>/<世代>.json` | measured | — | 中継のラッパー（使用率の API の取得） | replaced | 内部（予告なく変えてよい） |
| `relay-wrapper` | `relay-wrappers` state:relay/wrappers | `<名前>/wrapper.json` | exchange | — | 中継のラッパー（M3b） | replaced | 内部（予告なく変えてよい） |
| `relay-wrapper-lock` | `relay-wrappers` state:relay/wrappers | `<名前>/relay.lock` | exchange | — | 中継のラッパー（M3b） | replaced | 内部（予告なく変えてよい） |
| `relay-wrapper-log` | `relay-wrappers` state:relay/wrappers | `<名前>/log.jsonl` | record | — | 中継のラッパー（M3b） | cleanup | 内部（予告なく変えてよい） |
| `release` | `ndf` repo:.ndf | `release.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `requirements` | `issues` repo:issues | `issue-<n>-requirements.md` | document | 追跡 | 要求の工程 | archived | 内部（予告なく変えてよい） |
| `review` | `ndf` repo:.ndf | `review.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `runtime-policy` | `ndf` repo:.ndf | `runtimes.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `scratch` | `ndf-tmp` repo:.ndf/tmp | `<id>/` | record | — | 各サブコマンド | run | 内部（予告なく変えてよい） |
| `sprint` | `ndf-sprints` repo:.ndf/sprints | `<id>/` | record | — | 開発ループとスプリント（M1） | cleanup | 内部（予告なく変えてよい） |
| `supervise` | `ndf` repo:.ndf | `supervise.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `usage-log-file` | `usage-log` state:usage（NDF_USAGE_DIR で差し替え） | `usage.jsonl` | record | — | 使用量の使用量ログ（M7） | kept | 内部（予告なく変えてよい） |
| `worktree` | `ndf` repo:.ndf | `worktree.json` | ndf_settings | 追跡 | 人 | kept | 内部（予告なく変えてよい） |
| `worktree-local` | `ndf` repo:.ndf | `worktree.local.json` | ndf_settings | — | 人（各自の機械の値。worktree.json のキーを上書きする） | kept | 内部（予告なく変えてよい） |

## 公開する行の項目

書き手は項目の名前と意味を変えない。ほかの項目は足してよく、読み手は知らない項目を受け流す。

| 行 | 読む外の呼び手 | 項目 |
| --- | --- | --- |
| `ask-journal` | service | — |
| `gate-rejections` | service | — |
| `issue-request` | service | `repo`・`title`・`body`・`labels` |
| `loop-record` | service | — |
| `organizer-answer` | — | `result` |
| `organizer-batch` | — | `step`・`lanes` |
| `organizer-presented` | service | `k`・`auto` |
| `organizer-progress` | service | `stages` |
| `organizer-range` | — | `sha`・`version`・`prs`・`lanes` |
| `organizer-request` | service | `kind`・`id`・`lane`・`prs`・`issues`・`sprint_state`・`at`・`ndf_version` |
| `organizer-result` | service | `result` |
| `plan-progress` | service | `step`・`attention` |
