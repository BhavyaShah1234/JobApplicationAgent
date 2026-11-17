export const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
export const hours = (h) => (h == null ? "–" : h < 48 ? `${Math.round(h * 10) / 10} h` : `${Math.round(h / 24)} d`);
