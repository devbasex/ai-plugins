// スプリントの一覧と詳細が共有する部品（#2172）。フェーズの名前は契約の列挙（@ndf-factory/contracts/phases）を正にする
import { PHASE_NAMES, type PhaseName } from "@ndf-factory/contracts/phases";
import { useTranslation } from "react-i18next";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";

// 列挙の順と同じ並びの翻訳のキー
const PHASE_KEYS = ["design", "impl", "check", "integrate", "finish"] as const satisfies { length: (typeof PHASE_NAMES)["length"] };

// フェーズの名前の表示。null は始まる前、5 つに無い名前は「その他（<名前>）」
export function usePhaseLabel(): (name: string | null) => string {
  const { t } = useTranslation();
  return (name) => {
    if (name === null) return t("phase.notStarted");
    const i = (PHASE_NAMES as readonly string[]).indexOf(name);
    return i < 0 ? t("phase.other", { name }) : t(`phase.${PHASE_KEYS[i]!}`);
  };
}

export function phaseIndex(name: string | null): number {
  return name === null ? -1 : (PHASE_NAMES as readonly string[]).indexOf(name);
}

// 5 つの棒。今より前は濃く、今は薄く。始まる前と 5 つに無い名前は文字だけで出す
export function PhaseBars({ current }: { current: string | null }) {
  const { t } = useTranslation();
  const label = usePhaseLabel();
  const at = phaseIndex(current);
  if (at < 0) return <span className="phase-text">{label(current)}</span>;
  return (
    <span className="phase-bars" role="img" aria-label={t("phase.current", { name: label(current) })}>
      {PHASE_NAMES.map((name: PhaseName, i) => (
        <span key={name} className={`phase-bars__bar phase-bars__bar--${i < at ? "passed" : i === at ? "current" : "ahead"}`} />
      ))}
      <span className="phase-text">{label(current)}</span>
    </span>
  );
}

// 読み直しが失敗したときの断り。前の表示は残す
export function StaleNotice({ since }: { since: string | null }) {
  const { t } = useTranslation();
  const format = useFormat();
  if (since === null) return null;
  return <Alert>{t("poll.stale", { time: format.dateTime(since) })}</Alert>;
}
