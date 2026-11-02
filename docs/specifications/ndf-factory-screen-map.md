# Neural Development Factory: 画面の地図

Neural Development Factory の Web の画面の全体を 1 つの表にまとめる。どの経路にどの画面があり、どの役割が使い、確定仕様のどの機能がどの画面に載るかを示す。画面を足す課題は、この表に 1 行を足し、`factory/web/src/screens.ts` に同じ行を足し、翻訳のファイルにキーを足し、[画面の部品](ndf-factory-ui-components.md)で画面を組む。

## 概要

- **経路の定義は `factory/web/src/screens.ts` の 1 か所にある。** アプリの経路（`App.tsx`）とナビゲーション（`NavMenu`）はそこから作り、経路を別に書かない。この文書の表の「状態が あり」の行の経路の集合と `screens.ts` の `available` の経路の集合は、テスト（`factory/web/src/docs.test.ts`）が一致を確かめる
- **ログインの後の画面は枠（`AppShell`）で包む。** 枠は見出しの帯・ナビゲーション・つながりの状態を持つ。ログインの画面・ログインの戻り・地図に無い経路の転送は枠で包まない
- **地図に無い経路を開くと `/account` へ移る。**
- **ナビゲーションの行き先には、スマートフォンの幅（360 px）でもメニューのボタンと行き先の 2 手で届く。**

## 用語

定義は [用語集](../glossary.md) が正である（文脈 `ndf-factory`）。この文書で使う語は「画面の地図」「見本の画面」「つながりの状態」である。

## 役割

| 役割 | `screens.ts` の値 | 決め方 |
| --- | --- | --- |
| 個人 | `personal` | 今の経路のアカウント（無ければ個人のアカウント）が `kind: "personal"` |
| Team の管理者 | `team_owner` | 今の経路のアカウントが Team で、`role: "owner"` |
| 席 | `team_seat` | 今の経路のアカウントが Team で、`role: "member"` |

枠は `api.me()` を token ごとに 1 度だけ呼んで役割を決める。経路のアカウントが `Me.accounts` に無いとき（チームを作った直後など）だけ、そのアカウントにつき 1 度呼び直す。役割が決まるまでと失敗したときは、役割の要る行き先（キーの持ち込み）を出さない。画面の判定は表示のためだけで、認可は API が行う。

## 画面の地図

| 経路 | 画面 | 役割 | 状態 | 載る機能（確定仕様の節） |
| --- | --- | --- | --- | --- |
| `/login` | ログイン | ログイン前 | あり | Google・GitHub でログイン（[accounts-and-plans](ndf-factory-accounts-and-plans.md)「ログインの流れ」） |
| `/callback` | ログインの戻り | ログイン前 | あり | code を token に換える（accounts-and-plans「ログインの流れ」） |
| `/account` | 自分のアカウント | 個人・管理者・席 | あり | 利用者の ID・個人のプランと実行の形態・使える量・チームの一覧・ログアウト（accounts-and-plans） |
| `/teams/new` | チームを作る | 個人 | あり | Team を作る（accounts-and-plans） |
| `/accounts/:accountId` | チームのアカウント | 管理者・席 | あり | 席の追加・種類の変更・削除（管理者）・実行の形態（管理者）・使える量（accounts-and-plans） |
| `/accounts/:accountId/repositories` | リポジトリ | 個人・管理者・席 | あり | 導入の案内・登録と外す（個人・管理者）・実行の状態（[runs-and-runner](ndf-factory-runs-and-runner.md)「画面」） |
| `/accounts/:accountId/model-credential` | キーの持ち込み | 個人・管理者 | あり | キーの保存と消去・外部 ID（runs-and-runner） |
| `/accounts/:accountId/repositories/:repositoryId/sprints/new` | スプリントを始める | 個人・管理者・席 | あり | 課題の候補・選び直し・開始（runs-and-runner） |
| `/accounts/:accountId/sprints` | スプリントの一覧 | 個人・管理者・席 | あり（見本） | フェーズと状態・取り消し |
| `/accounts/:accountId/sprints/:sprintRunId` | スプリントの詳細 | 個人・管理者・席 | あり（見本） | 工程・PR・CI・承認ゲート・起票の依頼（[issue-requests](ndf-factory-issue-requests.md)） |
| `/accounts/:accountId/repositories/:repositoryId/backlog` | バックログ | 個人・管理者・席 | 予定 | 順位の表と棚卸しの記録 |
| 未定 | 承認 | 個人・管理者・席 | 予定 | 承認の依頼・Web からの承認・通知 |
| 未定 | 請求と使用量 | 個人・管理者 | 予定 | プランの料金・使用量の台帳・決済 |
| 未定 | 手元のランナー | 個人・管理者 | 予定 | ランナーの登録・心拍 |
| 未定 | ループ | 個人・管理者・席 | 予定 | 開発ループと並行する実行の見え方 |

## ナビゲーション

ナビゲーションに出す行き先は、自分のアカウント・スプリントの一覧・リポジトリ・キーの持ち込み（個人と管理者だけ）の 4 つである。`:accountId` は今開いている経路のアカウントで埋め、無ければ個人のアカウントで埋める。幅 1024 px 以上は見出しの帯に横に並べ、未満はメニューのボタンで開く。

## 役割ごとの主な動線

| 役割 | 主な動線 |
| --- | --- |
| 個人 | ログイン → 自分のアカウント → リポジトリ → スプリントを始める → スプリントの一覧 → スプリントの詳細 |
| Team の管理者 | ログイン → 自分のアカウント → チームのアカウント（席の管理・実行の形態）→ キーの持ち込み・リポジトリ |
| 席 | ログイン → 自分のアカウント → チームのアカウント → リポジトリ → スプリントを始める → スプリントの一覧 |

## 見本の画面

スプリントの一覧と詳細は、部品だけで組み、ダミーデータで動く見本である。並べる項目・フェーズの集合・工程の詳細パネルは #2172 が置き換え、経路は変えない。

| 状態 | 一覧 | 詳細 |
| --- | --- | --- |
| 読み込み中 | `LoadingState` | 同じ |
| 0 件・見つからない | `EmptyState`「スプリントの実行はまだありません」 | `EmptyState`「このスプリントの実行は見つかりません」 |
| 失敗（5xx など） | `ErrorState` と「もう一度読む」 | 同じ |
| 401 | token を消してログインへ | 同じ |
| 403 | 「このアカウントを見る権限がありません」 | 同じ |
| オフライン | 枠の `ConnectionStatus` が出し、画面は最後の表示を残す | 同じ |

詳細は `Api` の `sprintRunDetails`（実行の ID の配列を受け、要素の配列を返す）で読む。本物の実装は詳細の経路がまだ無いため、アカウントの新しい順の 50 件から選ぶ。

## ダミーで起動する

`factory/` で `npm run dev:dummy -w web` を打つと、API を呼ばずにダミーの `Auth`・`Api`・つながりで画面が動く。切り替えは `factory/web/src/main.tsx` の 1 か所で、本番のビルドにはダミーが入らない（`npm run check:bundle -w web` が確かめる）。

| 検索の値 | 値 | 何が出るか |
| --- | --- | --- |
| `scenario` | `normal`（既定）・`empty`・`error` | 決まったデータ・一覧が 0 件・読み込みがすべて失敗 |
| `connection` | `online`（既定）・`offline`・`pending` | オンライン・最後に受け取った時刻つきのオフライン・つながってから確定する操作が 2 件 |

例: `http://localhost:5173/accounts/00000000-0000-4000-8000-000000000001/sprints?scenario=empty&connection=offline`

## 関連リンク

- [画面の部品](ndf-factory-ui-components.md)
- [認証とアカウント・席・プラン](ndf-factory-accounts-and-plans.md)
- [リポジトリの登録・スプリントの実行の制御・ランナー](ndf-factory-runs-and-runner.md)
- [起票の依頼と起票の代行](ndf-factory-issue-requests.md)
