"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./lib/auth";
import LandingPage from "./components/LandingPage";
import AnalysisPage from "./components/AnalysisPage";

export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // Redirect to onboarding if first login
  useEffect(() => {
    if (!loading && user) {
      const completed = localStorage.getItem("onboarding_completed");
      if (!completed) {
        router.push("/onboarding");
      }
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
        <div className="w-8 h-8 rounded-full border-2 border-accent/30 border-t-accent animate-spin" />
      </div>
    );
  }

  return user ? <AnalysisPage /> : <LandingPage />;
}
