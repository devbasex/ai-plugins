// 生成したファイル。手で直さない。定義（schemas/）を直して `npm run generate -w contracts` で作り直す

/**
 * 手元のマシンが登録と心拍で申告する、同時に動かす実行ジョブの数（#2347）。登録と心拍の本文はこの形を含む
 */
export interface LocalRunnerDeclaration {
  /**
   * 登録のたびに発行するランナーの識別子
   */
  runner_id: string;
  /**
   * 同時のジョブの数。無ければ 1
   */
  max_jobs?: number;
  /**
   * 起動の時点の CPU の数とメモリから出した目安。表示だけに使う
   */
  capacity_hint?: number;
}
