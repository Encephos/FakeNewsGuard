import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

/**
 * SSE proxy: streams backend /api/v1/jobs/{jobId}/stream through Next.js
 * so the frontend EventSource can use same-origin requests (with auth).
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const headers: Record<string, string> = {};
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;
  const lastEventId = req.headers.get("last-event-id");
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/v1/jobs/${jobId}/stream`, {
      headers,
      cache: "no-store",
      // No timeout — SSE streams are long-lived
    });
  } catch {
    return new Response(
      "event: error\ndata: {\"error\":\"Backend nicht erreichbar.\"}\n\n",
      {
        status: 502,
        headers: { "Content-Type": "text/event-stream" },
      },
    );
  }

  if (!backendRes.ok || !backendRes.body) {
    const errorText = await backendRes.text().catch(() => "Unbekannter Fehler");
    return new Response(
      `event: error\ndata: ${JSON.stringify({ error: errorText })}\n\n`,
      {
        status: backendRes.status,
        headers: { "Content-Type": "text/event-stream" },
      },
    );
  }

  // Stream the backend SSE response through to the client
  return new Response(backendRes.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
