import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_EXTRACT } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

export async function POST(req: NextRequest) {
  const body = await req.json();

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_EXTRACT),
    });
  } catch {
    return new Response(
      JSON.stringify({ error: "Backend nicht erreichbar." }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  const data = await backendRes.json();

  return new Response(JSON.stringify(data), {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
