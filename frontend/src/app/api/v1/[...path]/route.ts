import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_DEFAULT, TIMEOUT_ANALYZE, TIMEOUT_EXTRACT } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

function getTimeout(path: string): number {
  if (path.startsWith("analyze")) return TIMEOUT_ANALYZE;
  if (path.startsWith("extract")) return TIMEOUT_EXTRACT;
  return TIMEOUT_DEFAULT;
}

async function proxy(req: NextRequest, method: string) {
  const url = new URL(req.url);
  const segments = url.pathname.replace(/^\/api\/v1\//, "");
  const qs = url.searchParams.toString();
  const target = `${BACKEND}/api/v1/${segments}${qs ? `?${qs}` : ""}`;
  const timeout = getTimeout(segments);

  const headers: Record<string, string> = {};
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;

  const init: RequestInit = {
    method,
    headers,
    cache: "no-store",
    signal: AbortSignal.timeout(timeout),
  };

  if (method !== "GET" && method !== "HEAD") {
    init.body = await req.text();
  }

  let backendRes: Response;
  try {
    backendRes = await fetch(target, init);
  } catch {
    return new Response(
      JSON.stringify({ error: "Backend nicht erreichbar." }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  // Stream SSE responses through directly
  if (backendRes.headers.get("content-type")?.includes("text/event-stream")) {
    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }

  const data = await backendRes.text();
  const resContentType = backendRes.headers.get("content-type") || "application/json";
  return new Response(data, {
    status: backendRes.status,
    headers: { "Content-Type": resContentType },
  });
}

export async function GET(req: NextRequest) {
  return proxy(req, "GET");
}

export async function POST(req: NextRequest) {
  return proxy(req, "POST");
}

export async function PUT(req: NextRequest) {
  return proxy(req, "PUT");
}

export async function PATCH(req: NextRequest) {
  return proxy(req, "PATCH");
}

export async function DELETE(req: NextRequest) {
  return proxy(req, "DELETE");
}
