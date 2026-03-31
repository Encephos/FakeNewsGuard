import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_DEFAULT } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const headers: Record<string, string> = {};
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/jobs/${jobId}`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_DEFAULT),
    });
  } catch {
    return new Response(
      JSON.stringify({ status: "error", steps: [], result: null, error: "Backend nicht erreichbar." }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  const data = await backendRes.json();

  return new Response(JSON.stringify(data), {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
