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
  play: "m7 4 14 8-14 8V4Z", pause: "M7 4v16M17 4v16", stop: "M5 5h14v14H5z", alert: "m12 3 10 18H2L12 3Zm0 6v5m0 3v1", refresh: "M20 7V3l-4 4m4 0a9 9 0 1 0 1 9M20 7h-5",
};
export default function Icon({ name, size = 20, className = "" }: { name: string; size?: number; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true"><path d={paths[name] || paths.layers} /></svg>;
}
export function Brand() { return <span className="brand"><svg width="32" height="34" viewBox="0 0 48 48" aria-hidden="true"><g fill="none" stroke="currentColor" strokeWidth="2.5"><path d="m9 14 28 3-11 23L9 14 24 5l13 12"/><circle cx="9" cy="14" r="4" fill="currentColor"/><circle cx="37" cy="17" r="4" fill="currentColor"/><circle cx="26" cy="40" r="4" fill="currentColor"/><circle cx="24" cy="5" r="3" fill="currentColor"/></g></svg><span>gluons<span className="brand-dot">.</span></span></span>; }
