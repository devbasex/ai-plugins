// 読み直し（#2172 の前提 7・決定 12）。画面を開いている間、設定の間隔で読み直す。押し出しの通知は使わない。
// 見えていない間は読み直さず、前の読み直しが終わる前に次を打たない。読み直しが失敗しても前の表示は残す
import { useCallback, useEffect, useRef, useState } from "react";
import { useFailureHandler, useServices } from "./context.tsx";

export const DEFAULT_POLL_INTERVAL_MS = 30_000;

export interface Polled<T> {
  data: T | null;
  // 最初の読み込みの失敗の文（401 では null のままログインへ移る）
  error: string | null;
  // 読み直しが失敗した時刻（前の表示を残している）。成功すれば null に戻る
  staleSince: string | null;
  reload: () => void;
}

// read が変わると最初から読み直す。enabled が偽の間は読まない。poll が偽なら間隔では読み直さず、開いたときと reload だけで読む
// （変わることの少ない画面で、1 回の読み出しが重いもの。バックログなど）
export function usePolled<T>(read: () => Promise<T>, enabled: boolean, { poll = true }: { poll?: boolean } = {}): Polled<T> {
  const { pollIntervalMs } = useServices();
  const interval = pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const onFailure = useFailureHandler();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [staleSince, setStaleSince] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const hasData = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let running = false;
    hasData.current = false;
    setData(null);
    setError(null);
    setStaleSince(null);
    const tick = async () => {
      if (running) return;
      running = true;
      try {
        const value = await read();
        if (cancelled) return;
        hasData.current = true;
        setData(value);
        setError(null);
        setStaleSince(null);
      } catch (err) {
        if (cancelled) return;
        const message = onFailure(err);
        if (message === null) return;
        if (hasData.current) setStaleSince(new Date().toISOString());
        else setError(message);
      } finally {
        running = false;
      }
    };
    void tick();
    if (!poll) {
      return () => {
        cancelled = true;
      };
    }
    const timer = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      void tick();
    }, interval);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [read, enabled, interval, onFailure, generation, poll]);

  const reload = useCallback(() => setGeneration((g) => g + 1), []);
  return { data, error, staleSince, reload };
}
