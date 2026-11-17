export const n0 = (v) => Number(v).toLocaleString();
export const share = (part, whole) => (whole > 0 ? (part / whole < 0.01 && part > 0 ? "<1%" : `${Math.round((100 * part) / whole)}%`) : "–");
