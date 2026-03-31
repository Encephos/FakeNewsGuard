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

const steps = [
  { num: "01", title: "Eingabe", desc: "Text, URL oder Behauptung einfügen", icon: "📥", color: c.blue },
  { num: "02", title: "Extraktion", desc: "KI identifiziert prüfbare Claims", icon: "🔎", color: c.purple },
  { num: "03", title: "Recherche", desc: "Abgleich mit 20+ Quellen", icon: "📊", color: c.warning },
  { num: "04", title: "Verdikt", desc: "Ergebnis mit Konfidenzwert", icon: "✅", color: c.success },
];

export const Step2HowItWorks: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleY = interpolate(
    spring({ frame, fps, config: { damping: 200, stiffness: 80 } }),
    [0, 1],
    [30, 0],
  );

  return (
    <AbsoluteFill style={{ backgroundColor: c.bg, overflow: "hidden" }}>
      {/* Subtle grid */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${c.border}30 1px, transparent 1px), linear-gradient(90deg, ${c.border}30 1px, transparent 1px)`,
          backgroundSize: "80px 80px",
          opacity: 0.25,
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
            marginBottom: 64,
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
            So funktioniert es
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
            Vier einfache Schritte
          </div>
        </div>

        {/* Steps row */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          {steps.map((step, i) => {
            const delay = 15 + i * 16;
            const local = Math.max(0, frame - delay);
            const progress = spring({
              frame: local,
              fps,
              config: { damping: 12, stiffness: 100, mass: 0.6 },
            });
            const translateY = interpolate(progress, [0, 1], [20, 0]);

            // Connector
            const connDelay = delay + 12;
            const connLocal = Math.max(0, frame - connDelay);
            const connWidth = interpolate(connLocal, [0, 10], [0, 48], {
              extrapolateRight: "clamp",
            });

            return (
              <React.Fragment key={i}>
                <div
                  style={{
                    transform: `translateY(${translateY}px)`,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    width: 260,
                    textAlign: "center",
                  }}
                >
                  {/* Icon card */}
                  <div
                    style={{
                      width: 110,
                      height: 110,
                      borderRadius: 24,
                      backgroundColor: `${step.color}18`,
                      border: `1.5px solid ${step.color}40`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 52,
                      marginBottom: 22,
                      boxShadow: `0 8px 32px ${step.color}15`,
                    }}
                  >
                    {step.icon}
                  </div>

                  <div
                    style={{
                      fontSize: 14,
                      fontWeight: 800,
                      color: step.color,
                      fontFamily: font,
                      letterSpacing: 3,
                      marginBottom: 8,
                    }}
                  >
                    SCHRITT {step.num}
                  </div>

                  <div
                    style={{
                      fontSize: 26,
                      fontWeight: 700,
                      color: c.text,
                      fontFamily: font,
                      marginBottom: 10,
                    }}
                  >
                    {step.title}
                  </div>

                  <div
                    style={{
                      fontSize: 18,
                      color: c.textTer,
                      fontFamily: font,
                      lineHeight: 1.5,
                      maxWidth: 220,
                    }}
                  >
                    {step.desc}
                  </div>
                </div>

                {/* Connector arrow */}
                {i < steps.length - 1 && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      marginTop: 52,
                    }}
                  >
                    <div
                      style={{
                        width: connWidth,
                        height: 2,
                        background: `linear-gradient(90deg, ${step.color}60, ${steps[i + 1].color}60)`,
                        borderRadius: 1,
                      }}
                    />
                    <div
                      style={{
                        width: 0,
                        height: 0,
                        borderTop: "5px solid transparent",
                        borderBottom: "5px solid transparent",
                        borderLeft: `8px solid ${steps[i + 1].color}60`,
                      }}
                    />
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
