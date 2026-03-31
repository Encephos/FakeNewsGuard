import { NextRequest } from "next/server";
import { INTERNAL_BACKEND_URL, TIMEOUT_DEFAULT } from "@/config";

const BACKEND = INTERNAL_BACKEND_URL;

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/archive/${id}`, {
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

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND}/api/archive/${id}`, {
      method: "DELETE",
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
