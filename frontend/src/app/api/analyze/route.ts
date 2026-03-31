import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_ANALYZE } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

export async function POST(req: NextRequest) {
  const body = await req.json();

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_ANALYZE),
    });
  } catch {
    return new Response(
      JSON.stringify({ error: "Backend nicht erreichbar. Bitte sicherstellen, dass der Backend-Server läuft." }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  const data = await backendRes.json();

  return new Response(JSON.stringify(data), {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
