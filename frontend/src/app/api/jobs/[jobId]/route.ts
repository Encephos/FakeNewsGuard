import { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_URL?.replace("/api/analyze", "") || "http://backend:8000";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;

  const backendRes = await fetch(`${BACKEND}/api/jobs/${jobId}`, {
    cache: "no-store",
  });

  const data = await backendRes.json();

  return new Response(JSON.stringify(data), {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
