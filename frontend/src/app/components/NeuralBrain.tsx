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
}

interface Synapse {
  from: number;
  to: number;
  progress: number;
  speed: number;
  alpha: number;
  active: boolean;
  delay: number;
}

export default function NeuralBrain() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const nodesRef = useRef<Node[]>([]);
  const synapsesRef = useRef<Synapse[]>([]);
  const initedRef = useRef(false);
  const timeRef = useRef(0);

  const getAccentColor = useCallback(() => {
    if (typeof window === "undefined") return { r: 196, g: 30, b: 30 };
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim();
    // Parse hex color
    const hex = accent.replace("#", "");
    if (hex.length === 6) {
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
      };
    }
    return { r: 196, g: 30, b: 30 };
  }, []);

  const getTextTertiaryColor = useCallback(() => {
    if (typeof window === "undefined") return { r: 128, g: 124, b: 120 };
    const style = getComputedStyle(document.documentElement);
    const color = style.getPropertyValue("--text-tertiary").trim();
    const hex = color.replace("#", "");
    if (hex.length === 6) {
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
      };
    }
    return { r: 128, g: 124, b: 120 };
  }, []);

  const initNodes = useCallback((w: number, h: number) => {
    const cx = w / 2;
    const cy = h / 2;
    const count = 80;
    const nodes: Node[] = [];

    for (let i = 0; i < count; i++) {
      // Distribute nodes in a brain-like elliptical shape
      const angle = Math.random() * Math.PI * 2;
      const radiusX = (w * 0.32) * Math.sqrt(Math.random());
      const radiusY = (h * 0.38) * Math.sqrt(Math.random());
      const x = cx + Math.cos(angle) * radiusX;
      const y = cy + Math.sin(angle) * radiusY;

      nodes.push({
        x,
        y,
        vx: (Math.random() - 0.5) * 0.15,
        vy: (Math.random() - 0.5) * 0.15,
        radius: 1.2 + Math.random() * 1.8,
        baseAlpha: 0.15 + Math.random() * 0.35,
        alpha: 0.15 + Math.random() * 0.35,
        pulse: Math.random() * Math.PI * 2,
      });
    }
    nodesRef.current = nodes;

    // Create synapses between nearby nodes
    const synapses: Synapse[] = [];
    const maxDist = Math.min(w, h) * 0.22;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < maxDist && Math.random() < 0.3) {
          synapses.push({
            from: i,
            to: j,
            progress: 0,
            speed: 0.005 + Math.random() * 0.015,
            alpha: 0,
            active: false,
            delay: Math.random() * 300,
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
      ctx.scale(dpr, dpr);
      initNodes(rect.width, rect.height);
      initedRef.current = true;
    };

    resize();
    window.addEventListener("resize", resize);

    const animate = () => {
      if (!initedRef.current) {
        animRef.current = requestAnimationFrame(animate);
        return;
      }

      const rect = canvas.parentElement?.getBoundingClientRect();
      if (!rect) return;
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);
      timeRef.current++;

      const nodes = nodesRef.current;
      const synapses = synapsesRef.current;
      const accent = getAccentColor();
      const tertiary = getTextTertiaryColor();
      const cx = w / 2;
      const cy = h / 2;

      // Update nodes
      for (const node of nodes) {
        node.x += node.vx;
        node.y += node.vy;
        node.pulse += 0.02;

        // Soft boundary – pull toward center ellipse
        const dx = node.x - cx;
        const dy = node.y - cy;
        const normDist = Math.sqrt((dx / (w * 0.34)) ** 2 + (dy / (h * 0.4)) ** 2);
        if (normDist > 1) {
          node.vx -= dx * 0.001;
          node.vy -= dy * 0.001;
        }

        // Damping
        node.vx *= 0.998;
        node.vy *= 0.998;

        // Pulse alpha
        node.alpha = node.baseAlpha + Math.sin(node.pulse) * 0.15;
      }

      // Randomly fire synapses
      if (timeRef.current % 3 === 0) {
        const inactive = synapses.filter((s) => !s.active);
        if (inactive.length > 0) {
          const count = Math.min(2, inactive.length);
          for (let i = 0; i < count; i++) {
            const s = inactive[Math.floor(Math.random() * inactive.length)];
            s.active = true;
            s.progress = 0;
            s.alpha = 0.6 + Math.random() * 0.4;
          }
        }
      }

      // Draw connections (static, faint)
      for (const s of synapses) {
        const a = nodes[s.from];
        const b = nodes[s.to];
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(${tertiary.r}, ${tertiary.g}, ${tertiary.b}, 0.06)`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }

      // Draw active synapses (firing)
      for (const s of synapses) {
        if (!s.active) continue;
        s.progress += s.speed;

        const a = nodes[s.from];
        const b = nodes[s.to];

        if (s.progress >= 1) {
          s.active = false;
          s.progress = 0;
          // Light up target node
          nodes[s.to].alpha = 1;
          nodes[s.to].pulse = 0;
          continue;
        }

        // Traveling pulse along synapse
        const px = a.x + (b.x - a.x) * s.progress;
        const py = a.y + (b.y - a.y) * s.progress;

        // Glowing line trail
        const grad = ctx.createLinearGradient(a.x, a.y, px, py);
        const fadeAlpha = s.alpha * (1 - s.progress * 0.3);
        grad.addColorStop(0, `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0)`);
        grad.addColorStop(Math.max(0, s.progress - 0.15), `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0)`);
        grad.addColorStop(s.progress, `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha * 0.5})`);

        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(px, py);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Bright dot at pulse head
        ctx.beginPath();
        ctx.arc(px, py, 2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha})`;
        ctx.fill();

        // Glow
        ctx.beginPath();
        ctx.arc(px, py, 5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${fadeAlpha * 0.2})`;
        ctx.fill();
      }

      // Draw nodes
      for (const node of nodes) {
        // Glow for bright nodes
        if (node.alpha > 0.5) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, node.radius * 4, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${(node.alpha - 0.5) * 0.15})`;
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
        const mix = Math.max(0, (node.alpha - 0.3) / 0.7);
        const r = Math.round(tertiary.r + (accent.r - tertiary.r) * mix);
        const g = Math.round(tertiary.g + (accent.g - tertiary.g) * mix);
        const b = Math.round(tertiary.b + (accent.b - tertiary.b) * mix);
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${node.alpha})`;
        ctx.fill();

        // Decay bright nodes back to base
        if (node.alpha > node.baseAlpha + 0.2) {
          node.alpha -= 0.008;
        }
      }

      animRef.current = requestAnimationFrame(animate);
    };

    animRef.current = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
    };
  }, [initNodes, getAccentColor, getTextTertiaryColor]);

  return (
    <div className="relative w-full h-48 my-4 flex items-center justify-center overflow-hidden">
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
      />
      <span className="relative z-10 text-[10px] font-mono text-text-tertiary/50 tracking-widest uppercase select-none">
        Analysiere
      </span>
    </div>
  );
}
