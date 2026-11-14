"use client";

import { useCallback, useEffect, useState } from "react";
import Icon from "./icon";

/* The saved choice is applied before paint by public/theme-init.js; this keeps the control in step with it. */
type Theme = "system" | "light" | "dark";
const THEMES: Theme[] = ["system", "light", "dark"];
const THEME_ICON: Record<Theme, string> = { system: "monitor", light: "sun", dark: "moon" };

export function useTheme() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => { try { const saved = localStorage.getItem("gluons.theme"); if (saved === "light" || saved === "dark") setTheme(saved); } catch { /* System setting applies. */ } }, []);
  const cycle = useCallback(() => {
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    setTheme(next);
    if (next === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
    try { if (next === "system") localStorage.removeItem("gluons.theme"); else localStorage.setItem("gluons.theme", next); } catch { /* The choice lasts for this page only. */ }
  }, [theme]);
  return { theme, cycle };
}

export function ThemeButton({ className = "" }: { className?: string }) {
  const { theme, cycle } = useTheme();
  const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  return <button className={`icon-button ${className}`} onClick={cycle} aria-label={`Colour theme: ${theme}. Switch to ${next}.`} title={`Colour theme: ${theme}`}><Icon name={THEME_ICON[theme]} size={18} /></button>;
}
