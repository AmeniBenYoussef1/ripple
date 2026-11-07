// Checks every text/background pair of both themes against WCAG AA (4.5:1 for text, 3:1 for icons and UI lines).
// Run: node scripts/contrast.mjs   (exit code 1 if a pair fails)
const lum = (hex) => {
  const c = hex.replace("#", "").match(/../g).map((h) => parseInt(h, 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const P = { raspberry: "#C14364", coral: "#FF916B", cream: "#EEE2D1", teal: "#2CA18C", tealDark: "#1E7A69",
  coralDark: "#B4502C", raspDark: "#A3354F", ink: "#2B2233", muted: "#6A5F6E", bg: "#FAF6F0", white: "#FFFFFF" };

export const LIGHT = { bg: P.bg, surface: P.white, chip: P.cream, text: P.ink, muted: P.muted, okText: P.tealDark,
  okBg: "#E3F4F0", okFg: P.tealDark, warnText: P.coralDark, warnBg: "#FFF1EB", warnFg: P.coralDark, warnSoft: "#FFF8F4",
  badText: P.raspDark, badBg: "#FDF0F3", badFg: P.raspDark, badSoft: "#FDF7F9", accent: P.raspberry, icon: P.tealDark,
  mapBg: P.bg, heroBg: P.cream, okSoft: "#F5FBF9", nodeStroke: "#8A7E8E" };
export const DARK = { bg: "#2B2233", surface: "#3A2F42", chip: "#4B3F55", text: "#EEE2D1", muted: "#C8BCC9", okText: "#EEE2D1",
  okBg: P.tealDark, okFg: P.white, warnText: P.coral, warnBg: P.coral, warnFg: P.ink, warnSoft: "#4A3743",
  badText: P.coral, badBg: P.raspberry, badFg: P.white, badSoft: "#4A3141", accent: P.coral, icon: P.teal,
  mapBg: "#33293B", heroBg: "#3A2F42", okSoft: "#33393F", nodeStroke: "#9F92A4" };

const checks = (t) => [
  // [label, fg, bg, minimum]
  ["text on page", t.text, t.bg, 4.5], ["text on card", t.text, t.surface, 4.5], ["text on chip", t.text, t.chip, 4.5],
  ["muted on page", t.muted, t.bg, 4.5], ["muted on card", t.muted, t.surface, 4.5],
  ["text on map", t.text, t.mapBg, 4.5], ["muted on map", t.muted, t.mapBg, 4.5],
  ["text on hero map", t.text, t.heroBg, 4.5], ["muted on hero map", t.muted, t.heroBg, 4.5],
  ["ok text on card", t.okText, t.surface, 4.5], ["ok text on page", t.okText, t.bg, 4.5], ["ok badge", t.okFg, t.okBg, 4.5],
  ["warn text on card", t.warnText, t.surface, 4.5], ["warn chip", t.warnFg, t.warnBg, 4.5],
  ["warn text on soft", t.warnText, t.warnSoft, 4.5], ["text on warn soft", t.text, t.warnSoft, 4.5],
  ["bad text on card", t.badText, t.surface, 4.5], ["bad badge", t.badFg, t.badBg, 4.5], ["bad text on soft", t.badText, t.badSoft, 4.5],
  ["text on bad soft", t.text, t.badSoft, 4.5], ["text on ok soft", t.text, t.okSoft, 4.5],
  ["accent heading on page (large)", t.accent, t.bg, 3], ["icon on card", t.icon, t.surface, 3], ["icon on page", t.icon, t.bg, 3],
  ["node outline on map", t.nodeStroke, t.mapBg, 3],
  // fills that are the same in both themes
  ["white on teal-dark button", P.white, P.tealDark, 4.5], ["white on raspberry", P.white, P.raspberry, 4.5],
  ["ink on coral", P.ink, P.coral, 4.5],
];

let failed = 0;
for (const [name, theme] of [["light", LIGHT], ["dark", DARK]]) {
  for (const [label, fg, bg, min] of checks(theme)) {
    const r = ratio(fg, bg);
    if (r < min) failed++;
    console.log(`${r >= min ? "ok  " : "FAIL"} ${name.padEnd(5)} ${label.padEnd(30)} ${fg} on ${bg}  ${r.toFixed(2)} (min ${min})`);
  }
}
console.log(failed ? `${failed} pair(s) fail` : "all pairs pass");
process.exit(failed ? 1 : 0);
