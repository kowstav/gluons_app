/** Display helpers. Formatting follows the viewer's locale; nothing here decides state. */

export const human = (text: unknown) => String(text ?? "—").replaceAll("_", " ").replaceAll(".", " · ");

export const date = (text: unknown) =>
  text ? new Date(String(text)).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

export const day = (text: unknown) =>
  text ? new Date(String(text)).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }) : "—";

export const time = (text: unknown) =>
  text ? new Date(String(text)).toLocaleTimeString(undefined, { timeStyle: "short" }) : "—";

export const money = (value: unknown) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(Number(value || 0));

export const json = (value: unknown) => JSON.stringify(value, null, 2);

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "in 3 hours", "2 days ago". Coarse on purpose: exact times sit next to it. */
export function fromNow(text: unknown): string {
  if (!text) return "—";
  const seconds = (new Date(String(text)).getTime() - Date.now()) / 1000;
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"], [Infinity, "year"]];
  let value = seconds;
  for (const [size, unit] of steps) {
    if (Math.abs(value) < size) return relative.format(Math.round(value), unit);
    value /= size;
  }
  return date(text);
}

const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
export const looksLikeTime = (value: unknown): value is string => typeof value === "string" && ISO_TIME.test(value);
