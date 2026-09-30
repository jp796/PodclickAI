const port = Number(process.env.PORT ?? 4173);

const files: Record<string, { path: string; type: string }> = {
  "/": { path: "./index.html", type: "text/html; charset=utf-8" },
  "/index.html": { path: "./index.html", type: "text/html; charset=utf-8" },
  "/styles.css": { path: "./styles.css", type: "text/css; charset=utf-8" },
  "/app.js": { path: "./app.js", type: "text/javascript; charset=utf-8" },
};

Bun.serve({
  port,
  fetch(request) {
    const pathname = new URL(request.url).pathname;
    const asset = files[pathname];

    if (!asset) {
      return new Response("Not found", { status: 404 });
    }

    return new Response(Bun.file(asset.path), {
      headers: {
        "Content-Type": asset.type,
        "Cache-Control": "no-store",
      },
    });
  },
});

console.log(`REOS SuperApp Concept → http://localhost:${port}`);
