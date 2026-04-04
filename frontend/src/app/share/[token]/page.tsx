import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { INTERNAL_BACKEND_URL } from "@/config";
import SharePageClient from "./SharePageClient";

interface ShareData {
  token: string;
  title: string | null;
  overall_rating: string | null;
  confidence: number | null;
  summary: string | null;
  claims_count: number | null;
  techniques_count: number | null;
  source_url: string | null;
  platform: string | null;
  created_at: number | null;
  allow_embed: boolean;
  view_count: number;
  claims?: Array<{
    id: string;
    text: string;
    type: string;
    rating: string;
    evidence: string;
    correction: string;
    missing_context: string;
    sources: string[];
  }>;
  rhetoric?: Array<{
    name: string;
    severity: string;
    description: string;
    example: string;
  }>;
  key_corrections?: string[];
  fairness_notes?: string[];
}

async function fetchShare(token: string): Promise<ShareData | null> {
  try {
    const res = await fetch(`${INTERNAL_BACKEND_URL}/api/v1/share/${token}`, {
      cache: "no-store",
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const { token } = await params;
  const data = await fetchShare(token);
  if (!data) return { title: "Faktencheck – FakeNewsGuard" };

  const title = data.title ?? "Faktencheck";
  const description = (data.summary ?? "").slice(0, 200);
  const rating = data.overall_rating ?? "";

  return {
    title: `Faktencheck: ${title} – FakeNewsGuard`,
    description,
    openGraph: {
      title: `Faktencheck: ${title}`,
      description,
      type: "article",
      siteName: "FakeNewsGuard",
    },
    other: {
      "og:label1": "Bewertung",
      "og:data1": rating,
      "og:label2": "Konfidenz",
      "og:data2": data.confidence != null ? `${data.confidence}%` : "",
    },
  };
}

export default async function SharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const data = await fetchShare(token);
  if (!data) notFound();

  return <SharePageClient data={data} token={token} />;
}
