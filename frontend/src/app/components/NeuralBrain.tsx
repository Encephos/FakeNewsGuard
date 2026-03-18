"use client";

import { useRef, useEffect, useCallback } from "react";

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  baseAlpha: number;
  alpha: number;
  pulse: number;
  layer: number; // 0=core, 1=mid, 2=cortex
}

interface Synapse {
  from: number;
  to: number;
  progress: number;
  speed: number;
  alpha: number;
  active: boolean;
}

function parseHex(hex: string) {
  const h = hex.replace("#", "");
  if (h.length === 6) {
    return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
  }
  return null;
}

function getCssColor(prop: string, fallback: { r: number; g: number; b: number }) {
  if (typeof window === "undefined") return fallback;
  return parseHex(getComputedStyle(document.documentElement).getPropertyValue(prop).trim()) ?? fallback;
}

/**
 * Brain silhouette test — returns true if (x, y) is inside a stylised brain shape.
 * The shape is two overlapping ellipses (hemispheres) with a slight vertical
 * gap (longitudinal fissure) and a rounded bottom (brain stem hint).
 */
function insideBrain(x: number, y: number, cx: number, cy: number, scaleX: number, scaleY: number): boolean {
  // Normalise to unit space centred on (0,0)
  const nx = (x - cx) / scaleX;
  const ny = (y - cy) / scaleY;

  // Left hemisphere — shifted left, slightly tilted
  const lx = nx + 0.28;
  const ly = ny + 0.05;
  const leftDist = (lx * lx) / (0.62 * 0.62) + (ly * ly) / (0.88 * 0.88);

  // Right hemisphere — shifted right
  const rx = nx - 0.28;
  const ry = ny + 0.05;
  const rightDist = (rx * rx) / (0.62 * 0.62) + (ry * ry) / (0.88 * 0.88);

  // Central fissure rejection — narrow vertical strip
  const fissureWidth = 0.045;
  const inFissure = Math.abs(nx) < fissureWidth && ny < 0.35 && ny > -0.7;

  // Brain stem — small ellipse at bottom
  const sx = nx;
  const sy = ny - 0.65;
  const stemDist = (sx * sx) / (0.12 * 0.12) + (sy * sy) / (0.22 * 0.22);

  return ((leftDist < 1 || rightDist < 1) && !inFissure) || stemDist < 1;
}

export default function NeuralBrain() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const nodesRef = useRef<Node[]>([]);
  const synapsesRef = useRef<Synapse[]>([]);
  const initedRef = useRef(false);
  const timeRef = useRef(0);

  const initNodes = useCallback((w: number, h: number) => {
    const cx = w / 2;
    const cy = h / 2;
    const scaleX = w * 0.38;
    const scaleY = h * 0.44;
    const count = 100;
    const nodes: Node[] = [];

    // Rejection-sample points inside the brain silhouette
    let attempts = 0;
    while (nodes.length < count && attempts < 5000) {
      attempts++;
      const x = cx + (Math.random() - 0.5) * w * 0.85;
      const y = cy + (Math.random() - 0.5) * h * 0.95;
      if (!insideBrain(x, y, cx, cy, scaleX, scaleY)) continue;

      // Determine distance from centre for layer assignment
      const nx = (x - cx) / scaleX;
      const ny = (y - cy) / scaleY;
      const dist = Math.sqrt(nx * nx + ny * ny);
      const layer = dist < 0.35 ? 0 : dist < 0.65 ? 1 : 2;

      // Cortex nodes are smaller & dimmer; core nodes are brighter
      const sizeBase = layer === 0 ? 2.0 : layer === 1 ? 1.6 : 1.1;
      const alphaBase = layer === 0 ? 0.35 : layer === 1 ? 0.25 : 0.15;

      nodes.push({
        x, y,
        vx: (Math.random() - 0.5) * 0.12,
        vy: (Math.random() - 0.5) * 0.12,
        radius: sizeBase + Math.random() * 1.0,
        baseAlpha: alphaBase + Math.random() * 0.15,
        alpha: alphaBase + Math.random() * 0.15,
        pulse: Math.random() * Math.PI * 2,
        layer,
      });
    }
    nodesRef.current = nodes;

    // Synapses — connect nearby nodes, preferring same-layer or adjacent-layer
    const synapses: Synapse[] = [];
    const maxDist = Math.min(w, h) * 0.18;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > maxDist) continue;
        const layerDiff = Math.abs(nodes[i].layer - nodes[j].layer);
        // Higher connection probability for same-layer; lower for cross-layer
        const prob = layerDiff === 0 ? 0.35 : layerDiff === 1 ? 0.18 : 0.06;
        if (Math.random() < prob) {
          synapses.push({
            from: i, to: j,
            progress: 0,
            speed: 0.006 + Math.random() * 0.014,
            alpha: 0,
            active: false,
          });
        }
      }
    }
    synapsesRef.current = synapses;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const rect = canvas.parentElement?.getBoundingClientRect();
      if (!rect) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initNodes(rect.width, rect.height);
      initedRef.current = true;
    };

    resize();
    window.addEventListener("resize", resize);

    const animate = () => {
      if (!initedRef.current) { animRef.current = requestAnimationFrame(animate); return; }

      const rect = canvas.parentElement?.getBoundingClientRect();
      if (!rect) return;
      const w = rect.width;
      const h = rect.height;
      const cx = w / 2;
      const cy = h / 2;
      const scaleX = w * 0.38;
      const scaleY = h * 0.44;

      ctx.clearRect(0, 0, w, h);
      timeRef.current++;

      const nodes = nodesRef.current;
      const synapses = synapsesRef.current;
      const accent = getCssColor("--accent", { r: 196, g: 30, b: 30 });
      const tertiary = getCssColor("--text-tertiary", { r: 128, g: 124, b: 120 });

      // Update nodes
      for (const node of nodes) {
        node.x += node.vx;
        node.y += node.vy;
        node.pulse += 0.018;

        // Keep nodes inside brain silhouette with soft pull
        if (!insideBrain(node.x, node.y, cx, cy, scaleX, scaleY)) {
          node.vx += (cx - node.x) * 0.003;
          node.vy += (cy - node.y) * 0.003;
        }

        node.vx *= 0.996;
        node.vy *= 0.996;
        node.alpha = node.baseAlpha + Math.sin(node.pulse) * 0.12;
      }

      // Fire synapses — more active in core
      if (timeRef.current % 2 === 0) {
        const inactive = synapses.filter((s) => !s.active);
        if (inactive.length > 0) {
          const toFire = Math.min(3, inactive.length);
          for (let i = 0; i < toFire; i++) {
            const s = inactive[Math.floor(Math.random() * inactive.length)];
            s.active = true;
            s.progress = 0;
            s.alpha = 0.5 + Math.random() * 0.5;
          }
        }
      }

      // Draw static connections
      for (const s of synapses) {
        const a = nodes[s.from];
        const b = nodes[s.to];
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(${tertiary.r}, ${tertiary.g}, ${tertiary.b}, 0.05)`;
        ctx.lineWidth = 0.4;
        ctx.stroke();
      }

      // Draw active synapses
      for (const s of synapses) {
        if (!s.active) continue;
        s.progress += s.speed;

        const a = nodes[s.from];
        const b = nodes[s.to];

        if (s.progress >= 1) {
          s.active = false;
          s.progress = 0;
          nodes[s.to].alpha = 1;
          nodes[s.to].pulse = 0;
          // Chain reaction — occasionally trigger a connected synapse from target
          if (Math.random() < 0.4) {
            const next = synapses.find((ns) => !ns.active && (ns.from === s.to || ns.to === s.to));
            if (next) {
              next.active = true;
              next.progress = 0;
              next.alpha = s.alpha * 0.7;
            }
          }
          continue;
        }

        const px = a.x + (b.x - a.x) * s.progress;
        const py = a.y + (b.y - a.y) * s.progress;
        const fadeAlpha = s.alpha * (1 - s.progress * 0.25);

        // Glowing trail
        const grad = ctx.createLinearGradient(a.x, a.y, px, py);
        grad.addColorStop(Math.max(0, s.progress - 0.2), `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0)`);
        grad.addColorStop(s.progress, `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha * 0.45})`);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(px, py);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.2;
        ctx.stroke();

        // Pulse head
        ctx.beginPath();
        ctx.arc(px, py, 2.2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha})`;
        ctx.fill();

        // Outer glow
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha * 0.12})`;
        ctx.fill();
      }

      // Draw nodes
      for (const node of nodes) {
        if (node.alpha > 0.45) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, node.radius * 4.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${(node.alpha - 0.45) * 0.12})`;
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
        const mix = Math.max(0, (node.alpha - 0.25) / 0.75);
        const r = Math.round(tertiary.r + (accent.r - tertiary.r) * mix);
        const g = Math.round(tertiary.g + (accent.g - tertiary.g) * mix);
        const b = Math.round(tertiary.b + (accent.b - tertiary.b) * mix);
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${node.alpha})`;
        ctx.fill();

        if (node.alpha > node.baseAlpha + 0.15) {
          node.alpha -= 0.007;
        }
      }

      animRef.current = requestAnimationFrame(animate);
    };

    animRef.current = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
    };
  }, [initNodes]);

  return (
    <div className="relative w-full h-56 my-5 flex items-center justify-center overflow-hidden">
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
      <span className="relative z-10 text-[10px] font-mono text-text-tertiary/40 tracking-[0.25em] uppercase select-none">
        Analysiere
      </span>
    </div>
  );
}
