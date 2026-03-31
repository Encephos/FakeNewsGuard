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

const tiers = [
  {
    name: "Scout Lite",
    desc: "Schneller Überblick",
    features: ["Basis-Faktencheck", "3 Quellen", "Standard-Modell"],
    color: c.blue,
    icon: "🔎",
    tier: "lite",
  },
  {
    name: "Scout Pro",
    desc: "Tiefgehende Analyse",
    features: ["Multi-Quellen-Check", "Rhetorik-Analyse", "Pro-Modell"],
    color: c.purple,
    icon: "🛡️",
    highlighted: true,
    tier: "pro",
  },
  {
    name: "Scout Max",
    desc: "Maximale Präzision",
    features: ["Alle Agenten aktiv", "Bildanalyse", "Premium-Modell"],
    color: c.accent,
    icon: "⚡",
    tier: "max",
  },
];

export const Step4Tiers: React.FC<{ userTier?: string }> = ({ userTier }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleY = interpolate(
    spring({ frame, fps, config: { damping: 200, stiffness: 80 } }),
    [0, 1],
    [30, 0],
  );

  return (
    <AbsoluteFill style={{ backgroundColor: c.bg, overflow: "hidden" }}>
      {/* Background glow for highlighted tier */}
      <div
        style={{
          position: "absolute",
          top: "40%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 500,
          height: 500,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${c.purple}12 0%, transparent 70%)`,
          filter: "blur(40px)",
        }}
      />

      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          padding: 80,
        }}
      >
        {/* Title — always visible */}
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            textAlign: "center",
            marginBottom: 56,
          }}
        >
          <div
            style={{
              fontSize: 18,
              fontWeight: 700,
              color: c.accent,
              fontFamily: font,
              textTransform: "uppercase",
              letterSpacing: 5,
              marginBottom: 14,
            }}
          >
            Wähle dein Level
          </div>
          <div
            style={{
              fontSize: 54,
              fontWeight: 800,
              color: c.text,
              fontFamily: font,
              letterSpacing: -1,
            }}
          >
            Drei Scout-Stufen
          </div>
        </div>

        {/* Tier cards */}
        <div style={{ display: "flex", gap: 32, alignItems: "stretch" }}>
          {tiers.map((tier, i) => {
            const delay = 14 + i * 12;
            const local = Math.max(0, frame - delay);
            const scaleRaw = spring({
              frame: local,
              fps,
              config: { damping: 10, stiffness: 100, mass: 0.5 },
            });
            const scale = interpolate(scaleRaw, [0, 1], [0.85, 1]);

            const isActive = userTier === tier.tier || tier.highlighted;

            return (
              <div
                key={i}
                style={{
                  transform: `scale(${scale})`,
                  width: 340,
                  backgroundColor: c.bgCard,
                  border: `${isActive ? 2 : 1}px solid ${isActive ? tier.color : c.borderCard}`,
                  borderRadius: 24,
                  padding: "44px 36px 40px",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  position: "relative",
                  boxShadow: isActive
                    ? `0 8px 40px ${tier.color}20`
                    : "0 4px 24px rgba(0,0,0,0.2)",
                }}
              >
                {/* Badge */}
                {tier.highlighted && (
                  <div
                    style={{
                      position: "absolute",
                      top: -14,
                      background: `linear-gradient(135deg, ${tier.color}, ${tier.color}cc)`,
                      color: "#fff",
                      fontSize: 13,
                      fontWeight: 800,
                      fontFamily: font,
                      padding: "6px 20px",
                      borderRadius: 20,
                      letterSpacing: 1.5,
                    }}
                  >
                    EMPFOHLEN
                  </div>
                )}

                <div style={{ fontSize: 56, marginBottom: 20 }}>{tier.icon}</div>
                <div
                  style={{
                    fontSize: 30,
                    fontWeight: 800,
                    color: tier.color,
                    fontFamily: font,
                    marginBottom: 8,
                  }}
                >
                  {tier.name}
                </div>
                <div
                  style={{
                    fontSize: 18,
                    color: c.textTer,
                    fontFamily: font,
                    marginBottom: 32,
                  }}
                >
                  {tier.desc}
                </div>

                {/* Feature list */}
                <div style={{ width: "100%" }}>
                  {tier.features.map((f, j) => (
                    <div
                      key={j}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        marginBottom: 14,
                        fontSize: 18,
                        color: c.textSec,
                        fontFamily: font,
                      }}
                    >
                      <span style={{ color: c.success, fontSize: 18 }}>✓</span>
                      {f}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
