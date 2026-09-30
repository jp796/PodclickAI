const root = import.meta.dir.replace(/\/scripts$/, "");

const read = async (name: string) => Bun.file(`${root}/${name}`).text();

const [html, css, source, product, pkg] = await Promise.all([
  read("index.html"),
  read("styles.css"),
  read("src/app.ts"),
  read("PRODUCT_DIRECTION.md"),
  read("package.json"),
]);

const checks: Array<[string, boolean]> = [
  ["named navigation region", html.includes('data-region="navigation"')],
  ["named operations region", html.includes('data-region="operations-canvas"')],
  ["named ATLAS region", html.includes('data-region="atlas-continuity"')],
  ["exactly five primary destinations", (html.match(/data-nav=/g) ?? []).length === 5],
  ["prevent-harm queue", html.includes("Prevent harm")],
  ["move-today queue", html.includes("Move today")],
  ["ATLAS health state", html.includes("data-atlas-health")],
  ["blocked continuity path", html.includes("data-trace-state=\"blocked\"")],
  ["recovered continuity path", html.includes("data-trace-state=\"recovered\"")],
  ["alternate evidence source", html.includes("alternate evidence")],
  ["receipt action", html.includes("data-receipt-field=\"action\"")],
  ["receipt evidence", html.includes("data-receipt-field=\"evidence\"")],
  ["receipt confidence", html.includes("data-receipt-field=\"confidence\"")],
  ["receipt timestamp", html.includes("data-receipt-field=\"timestamp\"")],
  ["receipt undo", html.includes("data-action=\"undo-receipt\"")],
  ["review drawer", html.includes('id="review-drawer"')],
  ["four email previews", (html.match(/data-signal-class=/g) ?? []).length === 4],
  ["four frequency controls", (html.match(/data-frequency=/g) ?? []).length === 4],
  ["seven continuity stages", (product.match(/^\d\. \*\*/gm) ?? []).length === 7],
  ["email taxonomy", product.includes("Operating alerts") && product.includes("Product updates")],
  ["three roadmap horizons", (product.match(/^### Horizon /gm) ?? []).length >= 3],
  ["tablet breakpoint", css.includes("@media (max-width: 1100px)")],
  ["mobile breakpoint", css.includes("@media (max-width: 760px)")],
  ["reduced motion", css.includes("prefers-reduced-motion")],
  ["build script", pkg.includes('"build"')],
  ["serve script", pkg.includes('"serve"')],
  ["check script", pkg.includes('"check"')],
  ["interactive deal switching", source.includes("selectDeal")],
  ["interactive command response", source.includes("answerAtlas")],
  ["no remote runtime dependencies", !/https?:\/\//.test(`${html}\n${css}\n${source}`)],
  ["no unsafe silent-autonomy claim", !/silently (change|changes|changing)/i.test(html)],
];

const failures = checks.filter(([, passed]) => !passed);

for (const [name, passed] of checks) {
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}`);
}

if (failures.length > 0) {
  console.error(`\n${failures.length} prototype check(s) failed.`);
  process.exit(1);
}

console.log(`\n${checks.length}/${checks.length} prototype checks passed.`);
