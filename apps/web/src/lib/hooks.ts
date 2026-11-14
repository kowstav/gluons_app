"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Loads one API resource and keeps the previous data visible while it reloads. */
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const latest = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const call = ++latest.current;
    setLoading(true); setError(null);
    try { const value = await api<T>(path); if (call === latest.current) setData(value); }
    catch (e) { if (call === latest.current) setError((e as Error).message); }
    finally { if (call === latest.current) setLoading(false); }
  }, [path]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, loading, load };
}

/** Runs one owner action at a time and reports its error. The server decides the outcome. */
export function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true); setError(null);
    try { setResult(await fn()); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally { setBusy(false); }
  }
  return { error, busy, result, run, setError };
}

/** One query-string value kept in the address bar, so a tab or filter survives reload and sharing. */
export function useQueryState(key: string, fallback: string): [string, (value: string) => void] {
  const [value, setValue] = useState(fallback);
  useEffect(() => {
    const read = () => setValue(new URLSearchParams(window.location.search).get(key) || fallback);
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [key, fallback]);
  const update = useCallback((next: string) => {
    setValue(next);
    const url = new URL(window.location.href);
    if (next === fallback) url.searchParams.delete(key); else url.searchParams.set(key, next);
    window.history.replaceState(window.history.state, "", url);
  }, [key, fallback]);
  return [value, update];
}
