"use client";

import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  random,
} from "remotion";
import { c, font } from "./theme";

const PARTICLE_COUNT = 50;

export const Step1Welcome: React.FC<{ userName?: string }> = ({
  userName,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Glow pulse — always visible
  const orbPulse = interpolate(
    Math.sin(frame * 0.04),
    [-1, 1],
    [0.12, 0.22],
  );

  // Shield — starts at scale 0.85 and springs to 1
  const shieldScaleRaw = spring({
    frame,
    fps,
    config: { damping: 8, stiffness: 60, mass: 1 },
  });
  const shieldScale = interpolate(shieldScaleRaw, [0, 1], [0.85, 1]);

  // Title — slides up
  const titleY = interpolate(
    spring({ frame: Math.max(0, frame - 8), fps, config: { damping: 200, stiffness: 80 } }),
    [0, 1],
    [30, 0],
  );

  // Subtitle — slides up with delay
  const subY = interpolate(
    spring({ frame: Math.max(0, frame - 20), fps, config: { damping: 200, stiffness: 60 } }),
    [0, 1],
    [20, 0],
  );

  // Accent line grows
  const lineWidth = interpolate(Math.max(0, frame - 30), [0, 20], [0, 140], {
    extrapolateRight: "clamp",
    extrapolateLeft: "clamp",
  });

  const greeting = userName ? `Willkommen, ${userName}!` : "Willkommen!";

  return (
    <AbsoluteFill style={{ backgroundColor: c.bg, overflow: "hidden" }}>
      {/* Radial glow */}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -60%)",
          width: 600,
          height: 600,
          borderRadius: "50%",
          background: `radial-gradient(circle, rgba(224,48,48,${orbPulse}) 0%, transparent 70%)`,
          filter: "blur(60px)",
        }}
      />

      {/* Grid */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${c.border}40 1px, transparent 1px), linear-gradient(90deg, ${c.border}40 1px, transparent 1px)`,
          backgroundSize: "80px 80px",
          opacity: 0.25,
        }}
      />

      {/* Particles */}
      {Array.from({ length: PARTICLE_COUNT }, (_, i) => {
        const x = random(`wx-${i}`) * 1920;
        const y = random(`wy-${i}`) * 1080;
        const size = 2 + random(`ws-${i}`) * 3;
        const phase = random(`wph-${i}`) * Math.PI * 2;
        const pOpacity = interpolate(
          Math.sin(frame * 0.025 + phase),
          [-1, 1],
          [0.05, 0.18],
        );
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: "50%",
              backgroundColor: c.accent,
              opacity: pOpacity,
            }}
          />
        );
      })}

      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          gap: 24,
        }}
      >
        {/* Shield — always visible, animates scale */}
        <div
          style={{
            transform: `scale(${shieldScale})`,
            marginBottom: 20,
            filter: "drop-shadow(0 0 30px rgba(224, 48, 48, 0.3))",
          }}
        >
          <svg width="140" height="140" viewBox="0 0 24 24" fill="none">
            <defs>
              <linearGradient id="sg" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor={c.accent} />
                <stop offset="100%" stopColor="#ff6060" />
              </linearGradient>
            </defs>
            <path
              d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"
              stroke="url(#sg)"
              strokeWidth="1.2"
              fill="rgba(224,48,48,0.08)"
            />
            <polyline
              points="9 12 11 14 15 10"
              stroke={c.success}
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
            />
          </svg>
        </div>

        {/* Greeting — always visible, animates Y position */}
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            fontSize: 64,
            fontWeight: 800,
            color: c.text,
            fontFamily: font,
            letterSpacing: -2,
          }}
        >
          {greeting}
        </div>

        {/* Subtitle — always visible, animates Y position */}
        <div
          style={{
            transform: `translateY(${subY}px)`,
            fontSize: 28,
            fontWeight: 300,
            color: c.textSec,
            fontFamily: font,
            textAlign: "center",
            maxWidth: 650,
            lineHeight: 1.6,
          }}
        >
          Lass uns gemeinsam herausfinden, wie du
          Fake News erkennen kannst.
        </div>

        {/* Accent line */}
        <div
          style={{
            width: lineWidth,
            height: 3,
            background: `linear-gradient(90deg, ${c.accent}, #ff6060)`,
            borderRadius: 2,
            marginTop: 8,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
