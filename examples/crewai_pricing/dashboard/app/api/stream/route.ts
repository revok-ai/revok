import http from "http";
import { type NextRequest } from "next/server";

// Server-side env var — available inside the container as http://server:8080
const BACKEND = process.env.API_URL ?? "http://localhost:8080";

// Never cache; re-run on every request
export const dynamic = "force-dynamic";

export function GET(req: NextRequest): Promise<Response> {
  const backendUrl = new URL(`${BACKEND}/stream`);

  return new Promise<Response>((resolve) => {
    // Use a TransformStream so we can write into the ReadableStream from Node events
    const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
    const writer = writable.getWriter();
    const enc = new TextEncoder();

    const options: http.RequestOptions = {
      hostname: backendUrl.hostname,
      port: Number(backendUrl.port) || 80,
      path: backendUrl.pathname + backendUrl.search,
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      },
    };

    const upstream = http.request(options, (res) => {
      res.on("data", (chunk: Buffer) => {
        writer.write(enc.encode(chunk.toString())).catch(() => upstream.destroy());
      });
      res.on("end", () => { writer.close().catch(() => {}); });
      res.on("error", (err) => { writer.abort(err).catch(() => {}); });
    });

    upstream.on("error", (err) => { writer.abort(err).catch(() => {}); });

    // Abort the upstream request when the browser disconnects
    req.signal.addEventListener("abort", () => {
      upstream.destroy();
      writer.abort(new Error("client disconnected")).catch(() => {});
    });

    upstream.end();

    // Resolve immediately with the ReadableStream — data flows in as it arrives
    resolve(
      new Response(readable, {
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          "X-Accel-Buffering": "no",
        },
      }),
    );
  });
}
