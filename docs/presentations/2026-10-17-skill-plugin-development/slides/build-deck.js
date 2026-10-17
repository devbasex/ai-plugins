// 発表資料「独自スキルプラグイン開発の薦め」の生成スクリプト（23 ページ）。
//
//   node build-deck.js <出力>.pptx [<theme.json>]
//
// 枠組みは lib/（NDF の release の assets/slides/lib/ の写し。書き換えない）、
// トーン＆マナーは theme.json、スピーカーノートは ../script.md（台本）から入る。
// ページの役割と根拠は ../notes.md にある。
const path = require("path");
const { run } = require("./lib/deck-frame");

const SCRIPT = path.join(__dirname, "..", "script.md");
const REPO = "https://github.com/devbasex/ai-plugins";

// ---- このページ群で使う部品（座標は本文の領域の中だけ） ----
const X0 = 0.26;
const W = 9.48;

// 横に n 等分した区画の x と幅
function cols(n, gap, x0, w) {
  const g = gap == null ? 0.16 : gap;
  const left = x0 == null ? X0 : x0;
  const total = w == null ? W : w;
  const cw = (total - g * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: left + i * (cw + g), w: cw }));
}

// 見出しと本文を持つ区画
function card(d, s, x, y, w, h, head, body, fill, opt) {
  const o = opt || {};
  d.box(s, x, y, w, h, fill || "panel1", false);
  if (o.band) d.box(s, x, y, w, 0.06, o.band, false);
  d.text(s, head, { x: x + 0.14, y: y + 0.1, w: w - 0.28, h: o.headH || 0.32, fontSize: o.headSize || 12, bold: true, color: "text", valign: "middle" });
  if (body) d.text(s, body, { x: x + 0.14, y: y + 0.12 + (o.headH || 0.32), w: w - 0.28, h: h - 0.22 - (o.headH || 0.32), fontSize: o.bodySize || 10, valign: "top", paraSpaceAfter: 3 });
}

// 大きな数値の区画
function stat(d, s, x, y, w, h, value, unit, label) {
  d.box(s, x, y, w, h, "panel1", false);
  d.box(s, x, y, 0.06, h, "accent", false);
  d.text(s, [
    { text: value, options: { fontSize: 30, bold: true, color: "accent" } },
    { text: " " + unit, options: { fontSize: 13, bold: true, color: "text" } },
  ], { x: x + 0.2, y: y + 0.14, w: w - 0.3, h: 0.62, valign: "middle" });
  d.text(s, label, { x: x + 0.2, y: y + 0.8, w: w - 0.3, h: h - 0.9, fontSize: 10 });
}

// 箇条書き（"- " で始まる項目は 1 段下げる）
function bullets(items, size) {
  return items.map((t, i) => {
    const sub = t.startsWith("- ");
    return { text: sub ? t.slice(2) : t, options: { bullet: { indent: 12 }, indentLevel: sub ? 1 : 0, fontSize: size || 10, breakLine: i < items.length - 1 } };
  });
}

// 横に並べた工程（chip と矢印）
function flow(d, s, x, y, w, h, labels, opt) {
  const o = opt || {};
  const gap = o.gap == null ? 0.22 : o.gap;
  const cw = (w - gap * (labels.length - 1)) / labels.length;
  labels.forEach((l, i) => {
    const cx = x + i * (cw + gap);
    const gate = typeof l === "object";
    d.chip(s, cx, y, cw, h, gate ? l.text : l, gate ? "caution" : o.fill || "accentSoft", { fontSize: o.size || 9.5 });
    if (i < labels.length - 1) d.arrow(s, cx + cw + 0.02, y + h / 2, gap - 0.04, 0);
  });
}

run(__dirname, { script: SCRIPT, title: "独自スキルプラグイン開発の薦め", author: "NDF" }, (d) => {
  let s;

  // 1. タイトル
  d.cover("独自スキルプラグイン開発の薦め", "NDF を例に、自分の開発スタイルを AI に持たせる", "github.com/devbasex/ai-plugins");

  // 2. AI に同じことを何度も言っていませんか
  s = d.page("AI に同じことを何度も言っていませんか", "指摘の回数は、自分のやり方がまだ AI に伝わっていない量である");
  cols(3).forEach((c, i) => {
    const q = ["「PR は develop 宛てで」", "「テストを先に書いて」", "「無いと言う前に\n検索結果を見せて」"][i];
    d.chip(s, c.x, 1.5, c.w, 1.0, q, "panel1", { fontSize: 15 });
  });
  d.arrow(s, 4.99, 2.62, 0, 0.42);
  d.callout(s, 3.16, 0.76, "caution", "セッションが切れるたびに AI は忘れ、指摘は消えていく", "毎日使うほど、同じ指摘を何度もしていることに気づく。");

  // 3. CLAUDE.md への追記だけでは足りない理由
  s = d.page("CLAUDE.md への追記だけでは足りない理由", "まず CLAUDE.md や AGENTS.md に追記するが、4 つの壁に当たる");
  [
    ["コンテキスト", "毎回すべてを読み込む。書くほどコストが上がり、重要なルールが埋もれる"],
    ["強制力", "書いても守られないことがある"],
    ["手順", "「どの順に何を実行するか」は文章では伝わりにくい"],
    ["ポータビリティ", "リポジトリやランタイムを変えると失われる"],
  ].forEach((r, i) => {
    const c = cols(2)[i % 2];
    card(d, s, c.x, 1.42 + Math.floor(i / 2) * 1.62, c.w, 1.46, r[0], r[1], "panel1", { band: "accent", headSize: 15, bodySize: 12, headH: 0.42 });
  });

  // 4. スキルプラグインとは
  s = d.page("スキルプラグインとは", "判断基準とワークフローをパッケージにして、どのリポジトリでも再利用する");
  d.tbl(s, [
    [d.H("構成要素"), d.H("役割")],
    [d.B("Skill"), "「いつ・何を・どの順で」を書いたワークフロー。必要なときだけ読み込まれる"],
    [d.B("スクリプト"), "決まった処理を確実に実行する"],
    [d.B("hook"), "ルールを強制する"],
    [d.B("サブエージェント"), "役割を分ける"],
  ], { x: X0, y: 1.4, w: W, colW: [2.2, 7.28], rowH: [0.32, 0.42, 0.42, 0.42, 0.42], fontSize: 11 });
  d.text(s, "まとめて配布し、同じものを 4 つのランタイムで使える", { x: X0, y: 3.62, w: W, h: 0.3, fontSize: 11, bold: true, color: "text" });
  cols(4).forEach((c, i) => d.chip(s, c.x, 4.0, c.w, 0.62, ["Claude Code", "Codex", "Kiro", "agy"][i], "accentSoft", { fontSize: 13 }));

  // 5. NDF: 1 人の開発スタイルをプラグインにした
  s = d.page("NDF: 1 人の開発スタイルをプラグインにした", "GitHub で公開している。ほぼ 1 人と AI で開発した");
  [
    ["102", "回", "リリース（2026 年 4 月に開発を始めて半年）"],
    ["48", "個", "Skill"],
    ["4", "つ", "対応するランタイム"],
    ["10", "リポジトリ", "自分以外への導入"],
  ].forEach((r, i) => {
    const c = cols(4)[i];
    stat(d, s, c.x, 1.55, c.w, 1.7, r[0], r[1], r[2]);
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.6, w: W, h: 0.36, fontSize: 14, bold: true, align: "center", valign: "middle" });
  d.note(s, "数値は 2026-10-17 時点の実測");

  // 6. NDF の全体像
  s = d.page("NDF の全体像", "マイルストーンを渡せば、スプリントとして並列に進む");
  d.chip(s, X0, 1.62, 1.55, 0.9, "マイルストーン\n（Issue の束）", "panel2", { fontSize: 10 });
  d.arrow(s, 1.85, 2.07, 0.3, 0);
  d.chip(s, 2.19, 1.62, 1.55, 0.9, "グループに分ける\n触る場所と依存関係", "panel2", { fontSize: 10 });
  d.arrow(s, 3.78, 2.07, 0.3, 0);
  d.box(s, 4.12, 1.36, 4.0, 1.98, "panel1", false);
  d.text(s, "スプリント（グループごとに並列）", { x: 4.24, y: 1.4, w: 3.8, h: 0.28, fontSize: 10, bold: true, color: "text", valign: "middle" });
  ["グループ A", "グループ B", "グループ C"].forEach((g, i) => {
    const y = 1.74 + i * 0.52;
    d.text(s, g, { x: 4.24, y, w: 0.9, h: 0.4, fontSize: 9, valign: "middle" });
    flow(d, s, 5.12, y, 2.9, 0.4, ["設計", "実装", "レビュー"], { gap: 0.18, size: 9 });
  });
  d.arrow(s, 8.16, 2.35, 0.3, 0);
  d.chip(s, 8.5, 1.9, 1.24, 0.9, "リリース", "panel2", { fontSize: 11 });
  cols(2).forEach((c, i) => {
    const g = [
      ["ゲート 1: 設計の承認", "スプリントの設計 PR をまとめて 1 回"],
      ["ゲート 2: 本番リリースの承認", "開発版で確かめた後に 1 回"],
    ][i];
    card(d, s, c.x, 3.52, c.w, 0.82, g[0], g[1], "caution", { headSize: 11, bodySize: 10, headH: 0.28 });
  });
  d.text(s, "人が判断するのは 2 か所だけ。承認には決めたこと・未検証の点・判断の根拠がそろって届く", { x: X0, y: 4.44, w: W, h: 0.3, fontSize: 10, bold: true, color: "text", valign: "middle" });
  d.note(s, "例: 直前のリリースでは、致命的な Issue 18 件を 2 つのスプリントに分け、1 つのスプリントの中で 3 つのグループの設計と実装を並列に進めた");

  // 7. /ndf:development-workflow
  s = d.page("/ndf:development-workflow", "変更を 5 つのモードに分類し、モードごとに通る Skill を切り替える");
  d.tbl(s, [
    [d.H("モード"), d.H("対象")],
    [d.B("light"), "文言や設定などの局所的な変更"],
    [d.B("operation"), "コードを変えずに外部の状態を変える作業"],
    [d.B("standard"), "機能追加やバグ修正"],
    [d.B("legacy-refactor"), "テストの薄いコードのリファクタリング"],
    [d.B("documentation"), "提案書や報告書などのビジネス文書"],
  ], { x: X0, y: 1.4, w: W, colW: [2.4, 7.08], rowH: 0.36, fontSize: 11 });
  d.callout(s, 3.78, 0.72, "panel1", "通る Skill はモードごとの表で決まっている", "light なら実装してレビューするだけ。standard なら要件定義から振り返りまで通る。すべての変更にフルのワークフローを課すと遅くなる。");

  // 8. standard モードの全体の流れ
  s = d.page("standard モードの全体の流れ", "スプリント単位で 1 周する。中の設計と実装は Issue のグループごとに並列に進む");
  [
    ["設計", ["要件定義", "worktree", "設計", "ドキュメントの\n再構成", "クロスレビュー", { text: "ゲート 1\n設計の承認" }]],
    ["実装", ["実装計画", "テストファースト\nの実装", "クロス\nリファクタリング", "クロスレビュー", "完了判定（証跡）", "マージ"]],
    ["リリース", ["仕様書へ変換", "開発版リリース", { text: "ゲート 2\n本番の承認" }, "本番リリース", "リリース後テスト", "振り返り", "Issue の棚卸し"]],
  ].forEach((row, i) => {
    const y = 1.48 + i * 1.08;
    d.text(s, row[0], { x: X0, y, w: 0.8, h: 0.7, fontSize: 11, bold: true, color: "text", valign: "middle" });
    const n = row[1].length;
    const g = 0.2;
    flow(d, s, 1.06, y, (8.68 * n + g * (n - 7)) / 7, 0.7, row[1], { gap: g, size: 8.5 });
    // 段の続きは左の見出しの列で示す（工程の箱の間に置くと、箱どうしの流れに読める）
    if (i < 2) d.arrow(s, X0 + 0.3, y + 0.76, 0, 0.26);
  });
  d.note(s, "クロスレビューの指摘は修正して繰り返す。開発版は自分で使って確かめてからゲート 2 へ出す");

  // 9. 実績のある開発技法を組み込む
  s = d.page("実績のある開発技法を、AI が守れる形で組み込む", "新しい方法論の発明ではない。人間のチームで実績のある技法を、AI が毎回守れる手順にした");
  [
    ["DDD: ユビキタス言語", ["要件定義の前に用語集を作り、語・意味・コンテキストを決める", "同じ「注文」でも受注と配送では意味が違う。コンテキストごとに 1 つの意味に決め、AI はその語で設計とコードを書く", "このリポジトリの用語集は 580 語。CI が食い違いを検査する"]],
    ["テストファースト", ["失敗するテストを書く → 通す最小の実装 → 整理、のサイクル", "テストが薄いコードは、先に今の振る舞いを固定するテストを書いてからリファクタリングする"]],
    ["アジャイル", ["マイルストーンをバックログとして扱い、スプリントで進める", "スプリントごとに振り返りとバックログリファインメントを行う", "優先順位は WSJF（遅延コスト ÷ 大きさ）で付ける"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.42, c.w, 2.75, r[0], bullets(r[1], 11.5), "panel1", { band: "accent", headSize: 13 });
  });

  // 10. /ndf:requirements-design と /ndf:design
  s = d.page("/ndf:requirements-design と /ndf:design", "コードより先に「何を」「どう」を決める");
  [
    ["requirements-design: 何を満たせば完了か", ["観測できる受け入れ条件にする", "「いい感じに」「速く」のような評価語を、確かめられる条件に変える"]],
    ["design: どう作るか", ["データ構造・入出力の契約・処理の流れ・決定の理由を書く", "担当する AI が変わっても同じ構成の成果物になるよう、書く内容を規約で決めている"]],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    card(d, s, c.x, 1.42, c.w, 1.9, r[0], bullets(r[1], 12.5), "panel1", { band: "accent", headSize: 13 });
  });
  d.callout(s, 3.56, 0.72, "caution", "設計は PR として出す", "主題ごとに並列で開き、ゲート 1 でまとめて承認する。");

  // 11. /ndf:document-restructuring
  s = d.page("/ndf:document-restructuring", "書いた順ではなく、読み手が知りたい順へ並べ替える");
  d.text(s, "AI が書いた順（調べた順）", { x: X0, y: 1.4, w: 4.3, h: 0.3, fontSize: 11, bold: true, color: "text" });
  flow(d, s, X0, 1.76, 4.3, 0.56, ["問題に気づく", "測る", "結論"], { fill: "panel2", size: 10 });
  d.arrow(s, 4.66, 2.04, 0.5, 0, "accent");
  d.text(s, "読み手が知りたい順", { x: 5.32, y: 1.4, w: 4.42, h: 0.3, fontSize: 11, bold: true, color: "text" });
  flow(d, s, 5.32, 1.76, 4.42, 0.56, ["結論", "根拠（測った結果）", "経緯"], { size: 10 });
  d.text(s, "手順（内容は足さない）", { x: X0, y: 2.62, w: W, h: 0.3, fontSize: 11, bold: true, color: "text" });
  flow(d, s, X0, 2.98, W, 0.62, ["測る", "章立てを読み手の\n問いの順に並べる", "長い文を分ける", "対比を表にする", "もう一度測る"], { size: 10 });
  d.callout(s, 3.86, 0.7, "panel1", "レビューの前に通す", "レビューの指摘が、構成の指摘と内容の指摘で混ざらなくなる。");

  // 12. /ndf:cross-review
  s = d.page("/ndf:cross-review", "2 つの AI で、新しい指摘が出なくなるまでレビューする");
  flow(d, s, X0, 1.5, 5.0, 0.7, ["AI を 2 つ選ぶ\nclaude・codex・kiro", "レビュー", "指摘を修正"], { size: 9 });
  d.arrow(s, 2.9, 2.32, 0, 0.36);
  d.chip(s, 1.5, 2.72, 2.8, 0.62, "新しい指摘が出たか", "panel2", { fontSize: 10 });
  d.text(s, "出た → 組み合わせを入れ替えて繰り返す\n出ない → 終える", { x: X0, y: 3.46, w: 5.0, h: 0.56, fontSize: 10 });
  d.text(s, "レビューの観点は PR の中身で切り替わり、リポジトリごとにレビューの基準を設定できる", { x: X0, y: 4.1, w: 5.0, h: 0.6, fontSize: 10 });
  d.text(s, "指摘の再現率（手元の実測）", { x: 5.6, y: 1.42, w: 4.14, h: 0.3, fontSize: 11, bold: true, color: "text" });
  [
    ["1 つの AI（低い方）", 0.21, "panel2"],
    ["1 つの AI（高い方）", 0.49, "panel2"],
    ["2 つの AI を合わせる", 0.91, "accent"],
  ].forEach((r, i) => {
    const y = 1.86 + i * 0.86;
    d.text(s, r[0], { x: 5.6, y, w: 4.14, h: 0.28, fontSize: 10 });
    d.box(s, 5.6, y + 0.3, 3.4 * r[1], 0.36, r[2], false);
    d.text(s, r[1].toFixed(2), { x: 5.66 + 3.4 * r[1], y: y + 0.3, w: 0.6, h: 0.36, fontSize: 13, bold: true, color: "text", valign: "middle" });
  });

  // 13. /ndf:cross-refactoring
  s = d.page("/ndf:cross-refactoring", "全員で提案し、1 人が時間内に実施する");
  flow(d, s, X0, 1.5, W, 0.86, ["参加する AI 全員が\n1 回ずつ多面的に提案", "1 つの AI を選ぶ", "指定した時間（既定 30 分）に\n収まる分だけ実施", "テストが壊れた変更は\n取り消す"], { size: 10 });
  d.text(s, "実施の中身: 計画 → テスト追加 → 実装 → 検証", { x: 5.0, y: 2.48, w: 4.74, h: 0.3, fontSize: 10, align: "center" });
  card(d, s, X0, 3.0, 4.66, 1.2, "終わりが見える", "時間に関わる数値は、すべて指定した時間から逆算する", "panel1", { band: "accent", headSize: 13, bodySize: 12 });
  card(d, s, 5.08, 3.0, 4.66, 1.2, "結果が追える", "採用と取り消しの内訳を報告に残す", "panel1", { band: "accent", headSize: 13, bodySize: 12 });

  // 14. 3 層のエージェント
  s = d.page("3 層のエージェント", "長いタスクを止めずに進め、止まっても再開できる");
  [
    ["conductor", "人と話して全体を指揮する"],
    ["supervisor", "1 つのフェーズ（設計・実装・テストなど）を通す"],
    ["worker", "1 つの作業（調査・修正など）を行う"],
  ].forEach((r, i) => {
    const y = 1.46 + i * 1.02;
    d.chip(s, X0 + i * 0.3, y, 1.7, 0.74, r[0], i === 0 ? "accent" : "accentSoft", { fontSize: 13, color: i === 0 ? "onHeader" : "text" });
    d.text(s, r[1], { x: 2.12 + i * 0.3, y, w: 3.0, h: 0.74, fontSize: 10.5, valign: "middle" });
    if (i < 2) d.arrow(s, 1.1 + i * 0.3, y + 0.76, 0.2, 0.24);
  });
  card(d, s, 5.7, 1.46, 4.04, 1.3, "止まっても続きから再開", "フェーズは JSON のプランとしてスクリプトが実行する", "panel1", { band: "accent", headSize: 13, bodySize: 12 });
  card(d, s, 5.7, 2.96, 4.04, 1.5, "セッションを自動で切り替える", "コンテキストが大きくなったら、次に実行するコマンドを残して新しいセッションへ移る", "panel1", { band: "accent", headSize: 13, bodySize: 12 });

  // 15. 判断基準: MVV と 2 つの承認ゲート
  s = d.page("判断基準: MVV と 2 つの承認ゲート", "AI はプロジェクトの MVV を根拠に判断し、根拠の番号をログに残す");
  d.text(s, "AI が迷う場面: 指摘を直すか見送るか、人の承認を省いてよいか", { x: X0, y: 1.38, w: W, h: 0.3, fontSize: 10.5 });
  [
    ["Mission", "何のためにやるか"],
    ["Vision", "目指す姿"],
    ["Value", "判断の基準\n例: 「まず届ける」\n「処理の改善に寄与しない指摘は直さない」"],
  ].forEach((r, i) => {
    const c = cols(3, 0.16, X0, 5.9)[i];
    card(d, s, c.x, 1.8, c.w, 1.9, r[0], r[1], "accentSoft", { headSize: 15, bodySize: 11 });
  });
  card(d, s, 6.4, 1.8, 3.34, 1.9, "必ず人の承認が要る操作", "本番デプロイのような操作は、MVV に照らしても AI が省かない。ここに定義する", "caution", { headSize: 12, bodySize: 11.5 });
  d.callout(s, 3.9, 0.4, "panel1", "どこまで AI に任せるかは、進め方の設定で事前に選べる", null);

  // 16. /ndf:quality-gates と /ndf:investigation-rules
  s = d.page("/ndf:quality-gates と /ndf:investigation-rules", "完了も「無い」も、証拠がそろって初めて認める");
  [
    ["「完了しました」", "quality-gates", "実行したコマンド・終了コード・実行時刻・対象範囲がそろわない限り、完了と認めない"],
    ["「該当のコードはありません」", "investigation-rules", "「無い」という結論には、必ず検索のコマンドと結果を添えさせる"],
  ].forEach((r, i) => {
    const c = cols(2)[i];
    d.chip(s, c.x, 1.48, c.w, 0.72, r[0], "panel2", { fontSize: 15 });
    d.arrow(s, c.x + c.w / 2, 2.26, 0, 0.3);
    card(d, s, c.x, 2.62, c.w, 1.4, r[1], r[2], "panel1", { band: "accent", headSize: 13, bodySize: 12.5 });
  });
  d.text(s, "AI はもっともらしいが間違った推論で「無い」と断定しがちである", { x: X0, y: 4.3, w: W, h: 0.34, fontSize: 11, bold: true, color: "text", align: "center", valign: "middle" });

  // 17. 自分を直すループ
  s = d.page("自分を直すループ", "out-of-scope・retrospective・backlog-refinement で、NDF 自身を直し続ける");
  [
    ["out-of-scope", "作業中に見つけた範囲外の問題を、その場で Issue にする"],
    ["retrospective", "リリースのたびに進め方を見直す"],
    ["backlog-refinement", "スプリントの終わりに Issue の一覧を現状に合わせ、優先順位を付け直す"],
  ].forEach((r, i) => {
    const y = 1.42 + i * 1.08;
    card(d, s, X0, y, 4.9, 0.92, r[0], r[1], "accentSoft", { headSize: 12, bodySize: 10, headH: 0.28 });
    if (i < 2) d.arrow(s, 2.7, y + 0.93, 0, 0.14);
  });
  stat(d, s, 5.4, 1.42, 4.34, 1.44, "56", "%", "ある時点の棚卸しで、open の Issue 43 件のうち 24 件の本文が現状と食い違っていた");
  stat(d, s, 5.4, 3.0, 4.34, 1.5, "49", "件", "今日 71 件を 6 つの worker で並列に調べたところ、49 件は本文が古く、2 件はすでに修正済みだった");
  d.note(s, "放っておくと、すでに直った Issue を直すことになる");

  // 18. その他の特徴的な機能
  s = d.page("その他の特徴的な機能", "見積もり・届け方の違う成果物のリリース・AI がやりがちなことの強制的な停止");
  d.tbl(s, [
    [d.H("機能"), d.H("何をするか")],
    [d.B("estimate"), "AI エージェントが実施する前提で工数を見積もる。人日では見積もらず、AI の実行時間・人の作業時間・完了までの時間・費用の 4 つを、似た過去の実績から別々に出す"],
    [d.B("release"), "パッケージ・Web サービス・スライドや文書まで、届け方の違う成果物を同じ骨格でリリースする"],
    [d.B("hook"), "メインディレクトリの直接編集の警告、sleep による待ちの禁止、同じファイルの繰り返し読み込みの防止など、AI がやりがちなことを強制的に止める"],
  ], { x: X0, y: 1.42, w: W, colW: [1.8, 7.68], rowH: [0.36, 1.0, 0.76, 1.0], fontSize: 12 });

  // 19. 事例: 他のプロジェクトでも動く
  s = d.page("事例: 他のプロジェクトでも動く", "言語もチームも違う 4 つのプロジェクトで使った結果");
  d.tbl(s, [
    [d.H("プロジェクトの種類"), d.H("結果")],
    [d.B("AWS のインフラ"), "導入前後の 37 日で、1 日あたりのマージ PR 数が 0.95 から 3.38 に増えた（社内ツールの移行と重なっており、すべてが NDF の効果ではない）"],
    [d.B("PHP の業務システム\n（20 人で開発）"), "設計 PR 1 本の承認から 51 時間で、31 本の実装 PR が入った"],
    [d.B("機械学習のモデル"), "精度改善の調査で 4 か月分のデータ欠損を見つけ、修正からバックフィルまでを 1 つのスプリントで本番に出した。相関係数は 0.73 から 0.954 に改善した"],
    [d.B("価格推定"), "PR 数は変わらず、レビューを必ず通す開発プロセスに変わった"],
  ], { x: X0, y: 1.42, w: W, colW: [2.3, 7.18], rowH: [0.32, 0.72, 0.56, 0.72, 0.5], fontSize: 11 });
  d.note(s, "プロジェクトは種類で呼び、名前は出さない。数値は 2026-10-17 時点の実測");

  // 20. 事例から分かったこと
  s = d.page("事例から分かったこと", "詰まったところが次のバージョンになり、ルールはプロジェクトごとに違う");
  card(d, s, X0, 1.42, 4.66, 2.6, "1. どこでも最初は詰まり、次の版で直る", bullets([
    "詰まったところが NDF の Issue として上がり、次のバージョンで直った",
    "PHP のプロジェクトでは前提の違いから 17 件の Issue が上がった",
    "- worktree に vendor/ が無い",
    "- 全テストに 64 分かかる",
    "- マージがそのまま本番リリースになる",
  ], 12), "panel1", { band: "accent", headSize: 12.5 });
  d.text(s, "2. どのプロジェクトも独自のルールを足した", { x: 5.08, y: 1.48, w: 4.66, h: 0.32, fontSize: 12, bold: true, color: "text", valign: "middle" });
  d.tbl(s, [
    [d.H("プロジェクト"), d.H("足したルール")],
    [d.B("インフラ"), "本番デプロイの承認"],
    [d.B("業務システム"), "「マージ = 本番リリース」の承認"],
    [d.B("価格推定"), "全 PR へのレビュー"],
  ], { x: 5.08, y: 1.9, w: 4.66, colW: [1.5, 3.16], rowH: 0.5, fontSize: 12 });

  // 21. だから「フォークして自分用に」
  s = d.page("だから「フォークして自分用に」", "仕組みはそのまま使い、ルールを自分のものに差し替える");
  card(d, s, X0, 1.42, 4.66, 1.86, "NDF も私のルールのかたまり", bullets(["承認は 2 回だけ", "ドキュメントでは「道具」でなく「Tool」と書く", "文言を照合するテストは書かない"], 12), "warn", { headSize: 13 });
  card(d, s, 5.08, 1.42, 4.66, 1.86, "そのまま使う仕組み", bullets(["ワークフロー", "3 層のエージェント", "クロスレビュー", "判断基準"], 12), "accentSoft", { headSize: 13 });
  d.text(s, "他人のルールは、自分にとってはノイズになる。そのまま導入するより、フォークする", { x: X0, y: 3.36, w: W, h: 0.3, fontSize: 10.5, bold: true, color: "text", align: "center", valign: "middle" });
  cols(3).forEach((c, i) => d.chip(s, c.x, 3.84, c.w, 0.72, ["指摘が資産になる", "ランタイムに依存しない", "計測して改善できる"][i], "panel1", { fontSize: 13 }));

  // 22. 始め方と育て方
  s = d.page("始め方と育て方", "フォークする → 削る → 育てる");
  [
    ["1. フォークする", ["プラグイン名とマーケットプレイス名を自分のものに変える", "自分のリポジトリからインストール"]],
    ["2. 削る", ["配布する Skill はマニフェストで決まる。使わない Skill は外すだけ", "残すものはセッションのログの呼び出し回数で決める"]],
    ["3. 育てる", ["同じ指摘を 2 回したら形にする", "手順なら Skill", "決まった処理ならスクリプト", "必ず守らせたいなら hook"]],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    card(d, s, c.x, 1.42, c.w, 2.12, r[0], bullets(r[1], 11), "accentSoft", { headSize: 13 });
  });
  d.callout(s, 3.7, 1.04, "caution", "気をつける点", bullets([
    "description を増やしすぎない（すべてのセッションで読み込まれる）",
    "思いつきは experimental に置き、効いたものだけを本体に移す",
    "開発版を自分で使ってから正式版にする",
  ], 9.5));

  // 23. まとめ: 明日からの 3 ステップ
  s = d.page("まとめ: 明日からの 3 ステップ", "AI に自分のやり方を教えるのは 1 回でよい。スキルプラグインにして、フォークして育てる");
  [
    ["1", "ai-plugins をフォークして\n自分の名前でインストールする"],
    ["2", "使わない Skill を外す"],
    ["3", "同じ指摘を 2 回したら\nSkill にする"],
  ].forEach((r, i) => {
    const c = cols(3)[i];
    d.box(s, c.x, 1.5, c.w, 2.0, "panel1", false);
    d.text(s, r[0], { x: c.x, y: 1.6, w: c.w, h: 0.7, fontSize: 36, bold: true, color: "accent", align: "center", valign: "middle" });
    d.text(s, r[1], { x: c.x + 0.14, y: 2.36, w: c.w - 0.28, h: 1.0, fontSize: 13, bold: true, color: "text", align: "center", valign: "middle" });
  });
  d.text(s, [{ text: REPO, options: { hyperlink: { url: REPO }, color: "accent" } }], { x: X0, y: 3.86, w: W, h: 0.5, fontSize: 18, bold: true, align: "center", valign: "middle" });
});
