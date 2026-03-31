"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useI18n } from "../lib/i18n";

// ── Neural Network Particle System (Canvas) ───────────────────

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  baseRadius: number;
  pulse: number;
  pulseSpeed: number;
  opacity: number;
}

const NODE_COUNT = 80;
const CONNECTION_DISTANCE = 140;
const MOUSE_RADIUS = 200;
const MOUSE_REPEL = 0.8;

function createNodes(w: number, h: number): Node[] {
  return Array.from({ length: NODE_COUNT }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    vx: (Math.random() - 0.5) * 0.4,
    vy: (Math.random() - 0.5) * 0.4,
    radius: 1.5 + Math.random() * 2,
    baseRadius: 1.5 + Math.random() * 2,
    pulse: Math.random() * Math.PI * 2,
    pulseSpeed: 0.01 + Math.random() * 0.02,
    opacity: 0.3 + Math.random() * 0.4,
  }));
}

function useNeuralCanvas(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  isDark: boolean,
) {
  const nodesRef = useRef<Node[]>([]);
  const mouseRef = useRef({ x: -9999, y: -9999 });
  const rafRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;

      if (nodesRef.current.length === 0) {
        nodesRef.current = createNodes(rect.width, rect.height);
      }
    };
    resize();
    window.addEventListener("resize", resize);

    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const handleMouseLeave = () => {
      mouseRef.current = { x: -9999, y: -9999 };
    };
    window.addEventListener("mousemove", handleMouseMove);
    canvas.addEventListener("mouseleave", handleMouseLeave);

    const accentColor = isDark ? [224, 48, 48] : [196, 30, 30]; // --accent
    const nodeColor = isDark ? [168, 164, 160] : [74, 71, 66]; // --text-secondary

    const draw = () => {
      const W = canvas.getBoundingClientRect().width;
      const H = canvas.getBoundingClientRect().height;
      const nodes = nodesRef.current;
      const mouse = mouseRef.current;

      ctx.clearRect(0, 0, W, H);

      // Update & draw connections
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];

        // Mouse repulsion
        const mdx = a.x - mouse.x;
        const mdy = a.y - mouse.y;
        const mDist = Math.sqrt(mdx * mdx + mdy * mdy);
        if (mDist < MOUSE_RADIUS && mDist > 0) {
          const force = (1 - mDist / MOUSE_RADIUS) * MOUSE_REPEL;
          a.vx += (mdx / mDist) * force;
          a.vy += (mdy / mDist) * force;
        }

        // Damping
        a.vx *= 0.98;
        a.vy *= 0.98;

        // Move
        a.x += a.vx;
        a.y += a.vy;

        // Wrap around edges smoothly
        if (a.x < -20) a.x = W + 20;
        if (a.x > W + 20) a.x = -20;
        if (a.y < -20) a.y = H + 20;
        if (a.y > H + 20) a.y = -20;

        // Pulse
        a.pulse += a.pulseSpeed;
        a.radius = a.baseRadius + Math.sin(a.pulse) * 0.5;

        // Draw connections to nearby nodes
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < CONNECTION_DISTANCE) {
            const alpha = (1 - dist / CONNECTION_DISTANCE) * 0.15;

            // If mouse is near the midpoint, highlight the connection
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            const mmd = Math.sqrt((mx - mouse.x) ** 2 + (my - mouse.y) ** 2);
            const isNearMouse = mmd < MOUSE_RADIUS * 0.8;

            const [r, g, b2] = isNearMouse ? accentColor : nodeColor;
            const finalAlpha = isNearMouse ? alpha * 2.5 : alpha;

            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = `rgba(${r},${g},${b2},${finalAlpha})`;
            ctx.lineWidth = isNearMouse ? 1 : 0.5;
            ctx.stroke();
          }
        }
      }

      // Draw nodes on top
      for (const node of nodes) {
        const mdx = node.x - mouse.x;
        const mdy = node.y - mouse.y;
        const mDist = Math.sqrt(mdx * mdx + mdy * mdy);
        const isNearMouse = mDist < MOUSE_RADIUS;

        const [r, g, b] = isNearMouse ? accentColor : nodeColor;
        const alpha = isNearMouse
          ? node.opacity + (1 - mDist / MOUSE_RADIUS) * 0.5
          : node.opacity;
        const radius = isNearMouse
          ? node.radius + (1 - mDist / MOUSE_RADIUS) * 2
          : node.radius;

        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
        ctx.fill();

        // Glow for highlighted nodes
        if (isNearMouse && mDist < MOUSE_RADIUS * 0.5) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, radius + 3, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${r},${g},${b},${alpha * 0.15})`;
          ctx.fill();
        }
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", handleMouseMove);
      canvas.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, [canvasRef, isDark]);
}

// ── Icons ─────────────────────────────────────────────────────

const SearchIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
);
const ShieldIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
);
const LayersIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
);
const BoltIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
);

// Step icons (larger, for the pipeline)
const InputIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
);
const ExtractIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
);
const ResearchIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
);
const VerdictIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
);

// Agent icons
const BrainIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>
);
const DatabaseIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
);
const EyeIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
);
const ScaleIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="3" x2="12" y2="21"/><polyline points="8 8 4 12 8 16"/><polyline points="16 8 20 12 16 16"/></svg>
);
const HashIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/></svg>
);
const ImageIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
);

// Platform icons
const TwitterIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
);
const YouTubeIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"/><polygon fill="white" points="9.545 15.568 15.818 12 9.545 8.432"/></svg>
);
const InstagramIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"/></svg>
);
const ArticleIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
);
const FacebookIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
);
const ThreadsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017C1.5 8.418 2.35 5.564 3.995 3.513 5.845 1.209 8.598.028 12.179.004h.014c2.746.02 5.043.725 6.826 2.098 1.677 1.29 2.858 3.13 3.506 5.467l-2.725.681c-1.036-3.738-3.572-5.476-7.593-5.505h-.01c-2.78.019-4.885.9-6.258 2.622-1.218 1.527-1.857 3.78-1.88 6.634v.012c.023 2.854.662 5.107 1.88 6.634 1.373 1.722 3.478 2.604 6.258 2.622h.01c2.467-.016 4.288-.623 5.573-1.854 1.37-1.312 2.07-3.16 2.07-5.487l.001-.126c-.004-.173-.01-.347-.02-.52a5.71 5.71 0 0 0-2.97-.825c-1.121 0-2.005.342-2.553.99-.467.553-.707 1.317-.707 2.213 0 1.867 1.186 3.228 2.818 3.228.852 0 1.574-.305 2.088-.882.38-.426.636-.995.741-1.644a3.558 3.558 0 0 1-.322.018c-.547 0-.975-.18-1.24-.52-.2-.254-.3-.59-.3-1.003V12.1c0-1.637-.282-2.863-.84-3.645-.661-.926-1.756-1.396-3.255-1.396-1.254 0-2.271.39-3.024 1.16-.663.677-1.065 1.593-1.196 2.725l2.67.368c.072-.564.248-1 .526-1.297.328-.35.81-.528 1.432-.528.737 0 1.234.21 1.478.624.196.333.295.866.295 1.586v.44l-2.96.174c-1.553.091-2.738.497-3.52 1.206-.818.742-1.233 1.767-1.233 3.044 0 1.37.44 2.473 1.307 3.277.895.83 2.09 1.252 3.553 1.252 1.094 0 2.086-.268 2.95-.797a5.348 5.348 0 0 0 1.24-1.074c.1.504.254.968.46 1.39h2.848a8.186 8.186 0 0 1-.616-2.065 7.895 7.895 0 0 0 3.593-1.143c.03.378.046.76.046 1.143 0 3.143-.98 5.675-2.912 7.524C18.16 23.1 15.607 23.98 12.2 24h-.014z"/></svg>
);

// ── Component ──────────────────────────────────────────────────

export default function LandingPage() {
  const { t } = useI18n();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Detect dark mode
  const isDark = typeof document !== "undefined"
    ? document.documentElement.classList.contains("dark")
    : false;

  useNeuralCanvas(canvasRef, isDark);

  const features = [
    { title: t("landing.feature1title"), desc: t("landing.feature1desc"), icon: <SearchIcon /> },
    { title: t("landing.feature2title"), desc: t("landing.feature2desc"), icon: <ShieldIcon /> },
    { title: t("landing.feature3title"), desc: t("landing.feature3desc"), icon: <LayersIcon /> },
    { title: t("landing.feature4title"), desc: t("landing.feature4desc"), icon: <BoltIcon /> },
  ];

  const steps = [
    { num: "01", title: t("landing.step1title"), desc: t("landing.step1desc"), icon: <InputIcon /> },
    { num: "02", title: t("landing.step2title"), desc: t("landing.step2desc"), icon: <ExtractIcon /> },
    { num: "03", title: t("landing.step3title"), desc: t("landing.step3desc"), icon: <ResearchIcon /> },
    { num: "04", title: t("landing.step4title"), desc: t("landing.step4desc"), icon: <VerdictIcon /> },
  ];

  const agents = [
    { title: t("landing.agent1title"), desc: t("landing.agent1desc"), icon: <BrainIcon /> },
    { title: t("landing.agent2title"), desc: t("landing.agent2desc"), icon: <DatabaseIcon /> },
    { title: t("landing.agent3title"), desc: t("landing.agent3desc"), icon: <EyeIcon /> },
    { title: t("landing.agent4title"), desc: t("landing.agent4desc"), icon: <HashIcon /> },
    { title: t("landing.agent5title"), desc: t("landing.agent5desc"), icon: <ImageIcon /> },
    { title: t("landing.agent6title"), desc: t("landing.agent6desc"), icon: <ScaleIcon /> },
  ];

  const stats = [
    { value: t("landing.stat1value"), label: t("landing.stat1label") },
    { value: t("landing.stat2value"), label: t("landing.stat2label") },
    { value: t("landing.stat3value"), label: t("landing.stat3label") },
    { value: t("landing.stat4value"), label: t("landing.stat4label") },
  ];

  const platforms = [
    { name: t("landing.platformTwitter"), icon: <TwitterIcon /> },
    { name: t("landing.platformYouTube"), icon: <YouTubeIcon /> },
    { name: t("landing.platformInstagram"), icon: <InstagramIcon /> },
    { name: t("landing.platformFacebook"), icon: <FacebookIcon /> },
    { name: t("landing.platformThreads"), icon: <ThreadsIcon /> },
    { name: t("landing.platformArticle"), icon: <ArticleIcon /> },
  ];

  return (
    <div className="relative">
      {/* Neural network canvas background — hero section only */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-[100vh] pointer-events-none"
        style={{ zIndex: 0 }}
        aria-hidden="true"
      />

      {/* ═══ Hero Section ═══ */}
      <div className="relative z-10 flex flex-col items-center justify-center min-h-[calc(100vh-64px)] px-4 py-16">
        <div className="landing-element text-center mb-3" style={{ animationDelay: "0s" }}>
          <h1 className="text-5xl sm:text-6xl lg:text-8xl font-bold tracking-tight text-text-primary leading-[1.05]">
            {t("landing.hero")}
          </h1>
        </div>

        <div className="landing-element text-center mb-4" style={{ animationDelay: "0.1s" }}>
          <p className="text-lg sm:text-xl lg:text-2xl text-text-secondary font-light max-w-2xl leading-snug">
            {t("landing.tagline")}
          </p>
        </div>

        <div className="landing-element text-center mb-10" style={{ animationDelay: "0.2s" }}>
          <p className="text-sm text-text-tertiary max-w-lg leading-relaxed">
            {t("landing.subtitle")}
          </p>
        </div>

        <div className="landing-element flex items-center gap-3 mb-20" style={{ animationDelay: "0.3s" }}>
          <Link
            href="/login"
            className="px-7 py-3 text-sm font-medium bg-text-primary text-bg-primary rounded-full hover:opacity-90 transition-opacity"
          >
            {t("landing.ctaLogin")}
          </Link>
          <a
            href="https://t.me/FakeNewsGuardBot"
            target="_blank"
            rel="noopener noreferrer"
            className="px-7 py-3 text-sm font-medium border border-border text-text-primary rounded-full hover:bg-surface-hover transition-colors"
          >
            {t("landing.ctaTelegram")}
          </a>
        </div>

        {/* Feature cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl w-full">
          {features.map((f, i) => (
            <div
              key={i}
              className="landing-element glass-card rounded-xl px-4 py-4 hover:border-accent/20 transition-colors group"
              style={{ animationDelay: `${0.4 + i * 0.08}s` }}
            >
              <div className="flex items-start gap-3">
                <div className="text-text-tertiary group-hover:text-accent transition-colors flex-shrink-0 mt-0.5">
                  {f.icon}
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-text-primary mb-0.5">{f.title}</h3>
                  <p className="text-[11px] text-text-tertiary leading-relaxed">{f.desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Scroll indicator */}
        <div className="landing-element mt-16 text-text-tertiary/40 animate-bounce" style={{ animationDelay: "1s" }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </div>
      </div>

      {/* ═══ How It Works + Stats ═══ */}
      <div className="relative z-10 px-4 py-16 sm:py-24">
        <div className="max-w-3xl mx-auto">
          {/* Section header with inline stats */}
          <div className="text-center mb-6">
            <h2 className="text-3xl sm:text-4xl font-bold text-text-primary mb-3">
              {t("landing.howItWorksTitle")}
            </h2>
            <p className="text-sm text-text-tertiary max-w-md mx-auto">
              {t("landing.howItWorksSubtitle")}
            </p>
          </div>

          {/* Stats as compact pills */}
          <div className="flex flex-wrap items-center justify-center gap-2 mb-14">
            {stats.map((s, i) => (
              <div key={i} className="glass-badge px-3.5 py-1.5 flex items-center gap-1.5">
                <span className="text-xs font-bold text-accent">{s.value}</span>
                <span className="text-[10px] text-text-tertiary">{s.label}</span>
              </div>
            ))}
          </div>

          {/* Pipeline — vertical timeline */}
          <div className="relative">
            {/* Vertical connecting line */}
            <div className="absolute left-[19px] sm:left-[23px] top-3 bottom-3 w-px bg-gradient-to-b from-accent/30 via-accent/15 to-accent/30" />

            <div className="space-y-0">
              {steps.map((step, i) => (
                <div key={i} className="relative flex gap-5 sm:gap-7 group">
                  {/* Step number dot */}
                  <div className="relative z-10 flex-shrink-0 mt-1">
                    <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-bg-primary border-2 border-accent/30 group-hover:border-accent flex items-center justify-center transition-colors">
                      <span className="text-[11px] sm:text-xs font-mono font-bold text-accent">{step.num}</span>
                    </div>
                  </div>

                  {/* Content */}
                  <div className={`flex-1 ${i < steps.length - 1 ? 'pb-8 sm:pb-10' : 'pb-0'}`}>
                    <div className="glass-inner rounded-xl px-5 py-4 group-hover:border-accent/20 transition-colors">
                      <div className="flex items-center gap-2.5 mb-2">
                        <div className="text-accent/60 group-hover:text-accent transition-colors">
                          {step.icon}
                        </div>
                        <h3 className="text-sm font-semibold text-text-primary">{step.title}</h3>
                      </div>
                      <p className="text-xs text-text-tertiary leading-relaxed">{step.desc}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ═══ Architecture ═══ */}
      <div className="relative z-10 px-4 py-16 sm:py-24 bg-bg-secondary/40">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <h2 className="text-3xl sm:text-4xl font-bold text-text-primary mb-3">
              {t("landing.architectureTitle")}
            </h2>
            <p className="text-sm text-text-tertiary max-w-md mx-auto">
              {t("landing.architectureSubtitle")}
            </p>
          </div>

          {/* Single card with 4 agents */}
          <div className="glass-card rounded-2xl overflow-hidden divide-y divide-[var(--glass-inner-border)]">
            {agents.map((agent, i) => (
              <div
                key={i}
                className="px-6 py-5 flex items-start gap-4 hover:bg-[var(--glass-inner-bg)] transition-colors group"
              >
                <div className="w-10 h-10 rounded-lg bg-accent/10 text-accent flex items-center justify-center flex-shrink-0 group-hover:bg-accent/15 transition-colors mt-0.5">
                  {agent.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-semibold text-text-primary mb-0.5">{agent.title}</h3>
                  <p className="text-xs text-text-tertiary leading-relaxed">{agent.desc}</p>
                </div>
                <div className="hidden sm:flex items-center gap-1 text-text-tertiary/30 flex-shrink-0 mt-2">
                  <div className="w-1 h-1 rounded-full bg-current" />
                  <div className="w-6 h-px bg-current" />
                  <div className="w-1 h-1 rounded-full bg-current" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ═══ Platforms ═══ */}
      <div className="relative z-10 px-4 py-20 sm:py-28">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-14">
            <h2 className="text-3xl sm:text-4xl font-bold text-text-primary mb-3">
              {t("landing.platformsTitle")}
            </h2>
            <p className="text-sm text-text-tertiary max-w-md mx-auto">
              {t("landing.platformsSubtitle")}
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {platforms.map((p, i) => (
              <div
                key={i}
                className="glass-card rounded-xl px-4 py-4 flex items-center gap-3 hover:border-accent/20 transition-colors group"
              >
                <div className="text-text-tertiary group-hover:text-accent transition-colors flex-shrink-0">
                  {p.icon}
                </div>
                <span className="text-sm text-text-secondary group-hover:text-text-primary transition-colors">{p.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ═══ Bottom CTA ═══ */}
      <div className="relative z-10 px-4 py-20 sm:py-28 border-t border-border bg-bg-secondary/40">
        <div className="max-w-xl mx-auto text-center">
          <h2 className="text-2xl sm:text-3xl font-bold text-text-primary mb-4">
            {t("landing.tagline")}
          </h2>
          <p className="text-sm text-text-tertiary mb-8 max-w-md mx-auto leading-relaxed">
            {t("landing.subtitle")}
          </p>
          <div className="flex items-center justify-center gap-3">
            <Link
              href="/login"
              className="px-7 py-3 text-sm font-medium bg-text-primary text-bg-primary rounded-full hover:opacity-90 transition-opacity"
            >
              {t("landing.ctaLogin")}
            </Link>
            <a
              href="https://t.me/FakeNewsGuardBot"
              target="_blank"
              rel="noopener noreferrer"
              className="px-7 py-3 text-sm font-medium border border-border text-text-primary rounded-full hover:bg-surface-hover transition-colors"
            >
              {t("landing.ctaTelegram")}
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
