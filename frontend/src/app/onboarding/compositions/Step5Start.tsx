"use client";

import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";
import { c, font } from "./theme";

const tips = [
  { icon: "🔗", text: "Füge eine URL ein — wir extrahieren den Inhalt automatisch" },
  { icon: "📋", text: "Oder kopiere direkt den Text einer Behauptung" },
  { icon: "🌍", text: "FakeNewsGuard funktioniert in über 40 Sprachen" },
];

export const Step5Start: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Title scale
  const titleScaleRaw = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 60, mass: 0.8 },
  });
  const titleScale = interpolate(titleScaleRaw, [0, 1], [0.9, 1]);

  // CTA
  const ctaDelay = 50;
  const ctaLocal = Math.max(0, frame - ctaDelay);
  const ctaScaleRaw = spring({
    frame: ctaLocal,
    fps,
    config: { damping: 8, stiffness: 80, mass: 0.5 },
  });
  const ctaScale = interpolate(ctaScaleRaw, [0, 1], [0.85, 1]);

  // Pulse (continuous, only after CTA appears)
  const pulseOpacity =
    ctaLocal > 20
      ? interpolate(Math.sin(ctaLocal * 0.06), [-1, 1], [0.08, 0.25])
      : 0;

  return (
    <AbsoluteFill style={{ backgroundColor: c.bg, overflow: "hidden" }}>
      {/* Top glow */}
      <div
        style={{
          position: "absolute",
          top: "15%",
          left: "50%",
          transform: "translateX(-50%)",
          width: 700,
          height: 400,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${c.accent}10 0%, transparent 70%)`,
          filter: "blur(50px)",
        }}
      />

      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          gap: 16,
          padding: 80,
        }}
      >
        {/* Title — always visible */}
        <div
          style={{
            transform: `scale(${titleScale})`,
            fontSize: 58,
            fontWeight: 800,
            color: c.text,
            fontFamily: font,
            letterSpacing: -1.5,
            marginBottom: 4,
          }}
        >
          Bereit für deine erste Analyse?
        </div>

        <div
          style={{
            fontSize: 24,
            color: c.textTer,
            fontFamily: font,
            marginBottom: 40,
          }}
        >
          Ein paar Tipps zum Start:
        </div>

        {/* Tips — always visible, only transform animation */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 16,
            marginBottom: 52,
          }}
        >
          {tips.map((tip, i) => {
            const delay = 18 + i * 10;
            const local = Math.max(0, frame - delay);
            const translateX = interpolate(
              spring({
                frame: local,
                fps,
                config: { damping: 200, stiffness: 80 },
              }),
              [0, 1],
              [-20, 0],
            );

            return (
              <div
                key={i}
                style={{
                  transform: `translateX(${translateX}px)`,
                  display: "flex",
                  alignItems: "center",
                  gap: 18,
                  backgroundColor: c.bgCard,
                  border: `1px solid ${c.borderCard}`,
                  borderRadius: 18,
                  padding: "20px 28px",
                  width: 640,
                  boxShadow: "0 4px 24px rgba(0,0,0,0.2)",
                }}
              >
                <div
                  style={{
                    fontSize: 32,
                    width: 56,
                    height: 56,
                    borderRadius: 14,
                    backgroundColor: `${c.accent}12`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {tip.icon}
                </div>
                <div
                  style={{
                    fontSize: 20,
                    color: c.textSec,
                    fontFamily: font,
                    lineHeight: 1.4,
                  }}
                >
                  {tip.text}
                </div>
              </div>
            );
          })}
        </div>

        {/* CTA — always visible, only scale animation */}
        <div style={{ position: "relative" }}>
          {/* Pulse ring */}
          <div
            style={{
              position: "absolute",
              inset: -10,
              borderRadius: 18,
              border: `2px solid ${c.accent}`,
              opacity: pulseOpacity,
            }}
          />
          <div
            style={{
              transform: `scale(${ctaScale})`,
              background: `linear-gradient(135deg, ${c.accent}, #ff5050)`,
              color: "#fff",
              fontSize: 28,
              fontWeight: 800,
              fontFamily: font,
              padding: "22px 60px",
              borderRadius: 16,
              letterSpacing: 0.5,
              boxShadow: `0 8px 32px ${c.accent}40`,
            }}
          >
            Erste Analyse starten →
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
