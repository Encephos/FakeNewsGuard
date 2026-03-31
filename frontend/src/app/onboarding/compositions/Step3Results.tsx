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

const verdicts = [
  { label: "TRUE", color: c.success, pct: 100 },
  { label: "MOSTLY TRUE", color: "#6dd895", pct: 82 },
  { label: "MISLEADING", color: c.warning, pct: 55 },
  { label: "MOSTLY FALSE", color: "#e06040", pct: 30 },
  { label: "FALSE", color: c.accent, pct: 12 },
];

const features = [
  { icon: "📝", title: "Claim-Karten", desc: "Jede Behauptung einzeln bewertet mit Quellenbelegen" },
  { icon: "⚠️", title: "Manipulations-Hinweise", desc: "Rhetorische Tricks und Framing aufgedeckt" },
  { icon: "📈", title: "Konfidenzwert", desc: "Transparente Bewertung der Evidenzlage" },
];

export const Step3Results: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleY = interpolate(
    spring({ frame, fps, config: { damping: 200, stiffness: 80 } }),
    [0, 1],
    [30, 0],
  );

  return (
    <AbsoluteFill style={{ backgroundColor: c.bg, overflow: "hidden" }}>
      {/* Background gradient */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: "50%",
          background: `linear-gradient(to top, ${c.accent}08, transparent)`,
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
            Ergebnisse verstehen
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
            Die Verdikt-Skala
          </div>
        </div>

        <div style={{ display: "flex", gap: 72, alignItems: "flex-start" }}>
          {/* Verdict bars */}
          <div style={{ width: 560 }}>
            {verdicts.map((v, i) => {
              const delay = 12 + i * 8;
              const local = Math.max(0, frame - delay);
              const barPct = interpolate(local, [0, 25], [0, v.pct], {
                extrapolateRight: "clamp",
              });

              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 18,
                    marginBottom: 20,
                  }}
                >
                  <div
                    style={{
                      width: 160,
                      fontSize: 17,
                      fontWeight: 800,
                      color: v.color,
                      fontFamily: font,
                      textAlign: "right",
                      letterSpacing: 1,
                    }}
                  >
                    {v.label}
                  </div>
                  <div
                    style={{
                      flex: 1,
                      height: 36,
                      backgroundColor: `${c.border}80`,
                      borderRadius: 8,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${barPct}%`,
                        height: "100%",
                        background: `linear-gradient(90deg, ${v.color}cc, ${v.color})`,
                        borderRadius: 8,
                        boxShadow: `0 0 16px ${v.color}30`,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Feature cards */}
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {features.map((f, i) => {
              const delay = 30 + i * 10;
              const local = Math.max(0, frame - delay);
              const scaleRaw = spring({
                frame: local,
                fps,
                config: { damping: 12, stiffness: 100, mass: 0.5 },
              });
              const scale = interpolate(scaleRaw, [0, 1], [0.85, 1]);

              return (
                <div
                  key={i}
                  style={{
                    transform: `scale(${scale})`,
                    backgroundColor: c.bgCard,
                    border: `1px solid ${c.borderCard}`,
                    borderRadius: 18,
                    padding: "24px 28px",
                    display: "flex",
                    alignItems: "center",
                    gap: 18,
                    width: 420,
                    boxShadow: `0 4px 24px rgba(0,0,0,0.3)`,
                  }}
                >
                  <div
                    style={{
                      fontSize: 36,
                      width: 60,
                      height: 60,
                      borderRadius: 16,
                      backgroundColor: `${c.accent}15`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    {f.icon}
                  </div>
                  <div>
                    <div
                      style={{
                        fontSize: 20,
                        fontWeight: 700,
                        color: c.text,
                        fontFamily: font,
                        marginBottom: 4,
                      }}
                    >
                      {f.title}
                    </div>
                    <div
                      style={{
                        fontSize: 16,
                        color: c.textTer,
                        fontFamily: font,
                        lineHeight: 1.4,
                      }}
                    >
                      {f.desc}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
