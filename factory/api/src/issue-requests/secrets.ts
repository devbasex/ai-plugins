// 起票の前の最後の検査（#2216 の決定 12）。題と本文に秘密らしい値があれば起票せず下書きへ回す。
// 当たった値と本文はログ・例外の文に出さない
const SECRET_PATTERNS: readonly RegExp[] = [
  // AWS のアクセスキー
  /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,
  // PEM の秘密鍵の始まりの行（RSA・EC・OPENSSH・PKCS#8 など）
  /-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----/,
  // GitHub の token
  /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}/,
  /\bgithub_pat_[A-Za-z0-9_]{20,}/,
  // Anthropic の API キー
  /\bsk-ant-[A-Za-z0-9_-]{10,}/,
  // JWT の形（実行ジョブの token など）
  /\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}/,
];

export function containsSecret(...texts: string[]): boolean {
  return texts.some((text) => SECRET_PATTERNS.some((p) => p.test(text)));
}
