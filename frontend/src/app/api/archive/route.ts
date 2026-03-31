import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_DEFAULT } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/archive${qs ? `?${qs}` : ""}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_DEFAULT),
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
