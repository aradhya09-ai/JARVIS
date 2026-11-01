import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../services/api';
import type { SystemStats } from '../types';

interface Options {
  intervalMs: number;
  enabled: boolean;
}

export function useSystemStats({ intervalMs, enabled }: Options) {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<number | null>(null);
  const activeRef = useRef(false);

  const refresh = useCallback(async () => {
    if (activeRef.current) return;
    activeRef.current = true;
    setLoading(true);
    try {
      const next = await api.stats();
      setStats(next);
      setError(next.error ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      activeRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
      timerRef.current = null;
      return;
    }
    const period = Math.max(500, Math.min(intervalMs, 10000));
    void refresh();
    const tick = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    timerRef.current = window.setInterval(tick, period);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [enabled, intervalMs, refresh]);

  return { stats, error, loading, refresh };
}
