const paths: Record<string, string> = {
  pulse: "M3 12h4l3-8 4 16 3-8h4", grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  layers: "m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5", search: "m21 21-5-5M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  check: "m5 12 4 4L19 6", shield: "M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7l-9-4Zm-4 9 3 3 5-6",
  link: "m10 13 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0",
  arrow: "M4 12h16m-6-6 6 6-6 6", chevron: "m9 5 7 7-7 7", down: "m6 9 6 6 6-6", plus: "M12 5v14M5 12h14",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4", clock: "M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  team: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  chart: "M3 3v18h18M7 14l4-4 4 2 6-8", money: "M12 2v20m5-16H9a4 4 0 0 0 0 8h6a4 4 0 0 1 0 8H6",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9", menu: "M3 6h18M3 12h18M3 18h18",
  close: "m6 6 12 12M6 18 18 6", spark: "m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3 3-6Z",
  mail: "M3 5h18v14H3zM3 5l9 7 9-7", box: "m12 2 10 5v10l-10 5-10-5V7l10-5Zm0 10v10M2 7l10 5 10-5M7 4.5l10 5",
  sun: "M12 3v2m0 14v2M5.6 5.6 7 7m10 10 1.4 1.4M3 12h2m14 0h2M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z", moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z",
  monitor: "M3 4h18v12H3zM8 20h8M12 16v4", phone: "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z",
  message: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z", store: "m3 9 1.5-5h15L21 9M3 9v11h18V9M3 9h18M9 20v-6h6v6",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2m20 0-3.5-7h-13L2 12m20 0v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  help: "M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20Zm-2.5-13a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6m0 3v.01",
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Zm0 0A2.5 2.5 0 0 0 6.5 22H20v-5",
  flag: "M5 21V4m0 0h12l-2 4 2 4H5",
  play: "m7 4 14 8-14 8V4Z", pause: "M7 4v16M17 4v16", stop: "M5 5h14v14H5z", alert: "m12 3 10 18H2L12 3Zm0 6v5m0 3v1", refresh: "M20 7V3l-4 4m4 0a9 9 0 1 0 1 9M20 7h-5",
};
export default function Icon({ name, size = 20, className = "" }: { name: string; size?: number; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true"><path d={paths[name] || paths.layers} /></svg>;
}
export function Brand() { return <span className="brand"><svg width="32" height="34" viewBox="0 0 48 48" aria-hidden="true"><g fill="none" stroke="currentColor" strokeWidth="2.5"><path d="m9 14 28 3-11 23L9 14 24 5l13 12"/><circle cx="9" cy="14" r="4" fill="currentColor"/><circle cx="37" cy="17" r="4" fill="currentColor"/><circle cx="26" cy="40" r="4" fill="currentColor"/><circle cx="24" cy="5" r="3" fill="currentColor"/></g></svg><span>gluons<span className="brand-dot">.</span></span></span>; }
