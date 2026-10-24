# 文書系 Skill との関係

**組み直しは、他の文書系 Skill の規約を書き写さず、節を指して従う。** この表は、それぞれの Skill が何を求めるか・組み直しのどの手順で読むか（または読まない理由）を持つ。

**文書系 Skill は、`description` が文書の執筆・組み直し・出所・置き場・版面・判断の材料のどれかを扱う Skill とする。** 文書を書く手順を自分で持つ Skill（`pr`・`retrospective` など）は、本文の形を自分で固定しているため、共通ルール K3 で扱う。

| Skill・参照 | 何を求めるか | 読む手順 |
| --- | --- | --- |
| [document-style](../../document-style/SKILL.md) | 書き方の規則 1〜12・セルフチェック | 手順 3（すべての文書形式。ソースが Markdown のとき） |
| [document-drafting](../../document-drafting/SKILL.md) と `references/type-*.md` | 必ず書く節・読み手が最初に問うこと | 手順 0（目的との対応）・手順 2（節を消さない） |
| `development-workflow` の [document-types.md](../../development-workflow/references/document-types.md) | 文書タイプの判定 | 手順 0 |
| `design` の [design-template.md](../../design/references/design-template.md) | 承認する人が読む欄と固定の章 | 手順 2（動かさない） |
| `design` の [layout-common.md](../../design/references/layout-common.md)・`layout-<形>.md` | 版面で決める項目（見出しの階層・1 ページの情報量） | 手順 1（形式別ガイドから指される） |
| `release` の [form-slide.md](../../release/references/form-slide.md) | ページの文言・章扉・台本の尺 | 手順 1・3（文書形式がスライド） |
| `release` の [form-document.md](../../release/references/form-document.md)・[form-page.md](../../release/references/form-page.md)・[form-spreadsheet.md](../../release/references/form-spreadsheet.md) | 区切り（ページ・シートの単位） | 手順 1（文書形式が当たるとき） |
| [decision-request](../../decision-request/SKILL.md) | 判断を求める前に示す 5 項目 | 手順 2（目的が決議依頼・議論） |
| [document-sources](../../document-sources/SKILL.md) | 出典の節と区別の語 | 手順 2（出典の節を末尾に保つ）・手順 3（区別の語を消さない） |
| [investigation-rules](../../investigation-rules/SKILL.md) | 否定の結論に探した跡を示す | 手順 3（目的が作業レポート） |
| [document-systems](../../document-systems/SKILL.md)・[google-workspace](../../google-workspace/SKILL.md) | 置き場からの取得と投稿 | 手順 0 の前（ソースが置き場にあるとき、取得だけ）。投稿は読まない（組み直しの外） |
| [notion-writing](../../notion-writing/SKILL.md) | Notion の記法と、子ページを消さない編集 | 手順 2・3（ソースが Notion の記法のとき）。投稿は読まない |
| [layout-review](../../layout-review/SKILL.md) | 生成物の版面 | 読まない（生成の後の工程）。報告の「呼び出し先」に書く |
| [official-skills-autoloader](../../official-skills-autoloader/SKILL.md) | Office と PDF の生成 | 読まない（生成の手段） |

## 文書タイプと目的別ガイドの対応

**`documentation` モードの文書は、文書タイプ（`document-types.md` の 6 つ）から目的別ガイドを選ぶ。** 対応させた文書では、最初の章に `type-*.md` の「読み手が最初に問うこと」の答えを置く。目的別ガイドはその答えを書き写さずに節を指す。

| 文書タイプ | 目的別ガイド | 分かれ目 |
| --- | --- | --- |
| 提案・企画 | `purpose-decision.md`（未決の問いを含むなら `purpose-discussion.md`） | 読み手が採否を返すか、読み手と論点を決めるか |
| 決裁・稟議 | `purpose-decision.md` | — |
| 定例報告 | `purpose-report.md` | — |
| 指標定義 | `purpose-decided.md`（定義を変える提案なら `purpose-decision.md`） | 定義が決まっているか |
| 運用マニュアル | `purpose-manual.md` | — |
| 説明・研修 | `purpose-decided.md` | — |

## 規約が食い違ったとき

**正本の側に従う。** 組み直しの参照と他の Skill の規約が食い違ったら、他の Skill の節を正本として従い、食い違いを範囲外の課題として `out-of-scope` で残す。組み直しの中で他の Skill の規約を書き換えない。

## `document-style` と重なっていた規約

| 対象 | 正本 | もう片方の扱い |
| --- | --- | --- |
| 文書の長さ | ファイルの長さと分割: `document-style` のルール 9。章と文の長さ: `format-<形式>.md` | 単位が違うため両方に残し、互いに 1 行で指す |
| 文の長さ | `format-<形式>.md`（数え方は `lang-<言語>.md`） | `document-style` は持たない |
| 対比・列挙の形 | `document-style` のルール 8 | 組み直しの手順 3 はルール 8 を指す |
| 多義語 | `document-style` のルール 2 | 組み直しは手順 3 のセルフチェックの多義語の候補で見る |
