// 認可の state と候補の証明（10 分）。利用者とアカウントを署名して画面へ返し、戻りで照らす（決定 12）
import { jwtVerify, SignJWT } from "jose";
import type { ReachableRepository } from "../github/port.ts";

export const PROOF_SECONDS = 10 * 60;
const ISSUER = "ndf-factory/repositories";

export type ProofKey = () => Promise<Uint8Array>;

interface Subject {
  userId: string;
  accountId: string;
}

async function sign(key: ProofKey, purpose: string, subject: Subject, extra: Record<string, unknown> = {}): Promise<string> {
  return new SignJWT({ acc: subject.accountId, purpose, ...extra })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuer(ISSUER)
    .setSubject(subject.userId)
    .setIssuedAt()
    .setExpirationTime(`${PROOF_SECONDS}s`)
    .sign(await key());
}

// 署名・期限・利用者・アカウント・用途のどれかが違えば null
async function verify(key: ProofKey, token: unknown, purpose: string, subject: Subject): Promise<Record<string, unknown> | null> {
  if (typeof token !== "string" || token === "") return null;
  try {
    const { payload } = await jwtVerify(token, await key(), { issuer: ISSUER, algorithms: ["HS256"] });
    if (payload.sub !== subject.userId || payload.acc !== subject.accountId || payload.purpose !== purpose) return null;
    return payload as Record<string, unknown>;
  } catch {
    return null;
  }
}

export const signState = (key: ProofKey, subject: Subject) => sign(key, "state", subject);
export const verifyState = async (key: ProofKey, token: unknown, subject: Subject) => (await verify(key, token, "state", subject)) !== null;

export const signProof = (key: ProofKey, subject: Subject, repos: ReachableRepository[]) =>
  sign(key, "proof", subject, { repos: repos.map((r) => [r.repositoryId, r.fullName, r.installationId]) });

export async function verifyProof(key: ProofKey, token: unknown, subject: Subject): Promise<ReachableRepository[] | null> {
  const payload = await verify(key, token, "proof", subject);
  if (!payload || !Array.isArray(payload.repos)) return null;
  return (payload.repos as [number, string, number][]).map(([repositoryId, fullName, installationId]) => ({ repositoryId, fullName, installationId }));
}
