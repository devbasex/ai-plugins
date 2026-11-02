// ndf-next のブロックの読み方（I55）。規則の正本は NDF の契約の一覧（plugins/ndf/contracts/forms/ndf-next-block.json）で、
// ここは形を写す。NDF の relay.py（relay_lib/mark.py の next_blocks・_mark_action）と同じ例の組をテストが読む
const RESULT_LINE = /^\s*-\s*結果:\s*(.*?)\s*$/gm;

// 3 つのバッククォートの囲みのうち、情報文字列が ndf-next のものの中身（前後の改行を除く）。
// 囲みは CommonMark のとおり、開きと同じ文字で同じ数以上の閉じまでを 1 つとし、ほかの囲みの中の囲みは中身として読む
export function nextBlocks(text: string): string[] {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const fence = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(line);
    if (!fence || (fence[2]![0] === "`" && fence[3]!.includes("`"))) {
      i += 1;
      continue;
    }
    const [, indent, marker, info] = fence as unknown as [string, string, string, string];
    const close = new RegExp(`^ {0,3}${marker[0] === "`" ? "`" : "~"}{${marker.length},}\\s*$`);
    const body: string[] = [];
    i += 1;
    while (i < lines.length && !close.test(lines[i]!)) {
      body.push(stripIndent(lines[i]!, indent.length));
      i += 1;
    }
    i += 1; // 閉じの行（無ければ文の終わりまでが囲み）
    if (marker === "```" && info.trim() === "ndf-next") out.push(body.join("\n").replace(/^\n+|\n+$/g, ""));
  }
  return out;
}

function stripIndent(line: string, n: number): string {
  let k = 0;
  while (k < n && line[k] === " ") k += 1;
  return line.slice(k);
}

// 応答の文の最後の「- 結果:」の行が「判断が要る」か
export function decisionNeeded(text: string): boolean {
  const found = [...text.matchAll(RESULT_LINE)].map((m) => m[1]);
  return found.length > 0 && found.at(-1) === "判断が要る";
}

// 次の区間を起動するか。ブロックがちょうど 1 つで、結果が「判断が要る」でないときだけ起動する
export function readNext(text: string): { blocks: string[]; launch: boolean; command: string | null } {
  const blocks = nextBlocks(text);
  const launch = blocks.length === 1 && !decisionNeeded(text);
  return { blocks, launch, command: launch ? blocks[0]! : null };
}
