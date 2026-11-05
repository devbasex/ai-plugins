// 環境変数を読む。無ければ起動の誤りとして投げる（API・受け口・GitHub の仲立ち・起票の代行・実行の制御が使う）
export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}
