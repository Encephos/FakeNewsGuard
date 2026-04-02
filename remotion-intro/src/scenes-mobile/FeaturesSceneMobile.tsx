import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';
import { colors, fonts } from '../components/theme';
import { TechOverlay } from '../components/TechOverlay';

const features = [
  {
    title: 'Multi-Quellen-Verifizierung',
    desc: 'Automatische Recherche und Abgleich mit vertrauenswürdigen Quellen',
    icon: '🔍',
    tag: 'RETRIEVAL',
  },
  {
    title: 'Manipulationserkennung',
    desc: 'Erkennung rhetorischer Tricks, Zahlenmanipulation und Framing',
    icon: '🛡️',
    tag: 'DETECTION',
  },
  {
    title: 'Claim-Extraktion',
    desc: 'Automatische Identifikation prüfbarer Behauptungen',
    icon: '📋',
    tag: 'NLP',
  },
  {
    title: 'Echtzeit-Analyse',
    desc: 'Vollständige Analyse in unter 3 Minuten',
    icon: '⚡',
    tag: 'PIPELINE',
  },
];

export const FeaturesSceneMobile: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bgPrimary,
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: '80px 50px',
      }}
    >
      <TechOverlay label="sys::capabilities" />

      <div
        style={{
          opacity: titleOpacity,
          fontSize: 22,
          fontWeight: 600,
          color: colors.accent,
          fontFamily: fonts.mono,
          textTransform: 'uppercase',
          letterSpacing: 5,
          marginBottom: 56,
        }}
      >
        {'> Features'}
      </div>

      {/* Vertical stack */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
          width: '100%',
          maxWidth: 880,
        }}
      >
        {features.map((f, i) => {
          const delay = 10 + i * 14;
          const localFrame = Math.max(0, frame - delay);
          const progress = spring({
            frame: localFrame,
            fps,
            config: { damping: 14, stiffness: 100, mass: 0.6 },
          });
          const opacity = interpolate(localFrame, [0, 15], [0, 1], {
            extrapolateRight: 'clamp',
          });
          const translateX = interpolate(progress, [0, 1], [-30, 0]);

          // Animated left border glow
          const borderGlow = interpolate(localFrame, [0, 20, 40], [0, 1, 0.6], {
            extrapolateRight: 'clamp',
          });

          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `translateX(${translateX}px)`,
              }}
            >
              <div
                style={{
                  backgroundColor: colors.glassBg,
                  border: `1px solid ${colors.glassBorder}`,
                  borderLeft: `3px solid rgba(224, 48, 48, ${borderGlow})`,
                  borderRadius: 8,
                  padding: '24px 28px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 22,
                }}
              >
                <div
                  style={{
                    fontSize: 38,
                    width: 64,
                    height: 64,
                    borderRadius: 8,
                    backgroundColor: `${colors.accent}12`,
                    border: `1px solid ${colors.accent}20`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {f.icon}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                    <div
                      style={{
                        fontSize: 24,
                        fontWeight: 700,
                        color: colors.textPrimary,
                        fontFamily: fonts.sans,
                      }}
                    >
                      {f.title}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        fontFamily: fonts.mono,
                        color: colors.accent,
                        padding: '2px 8px',
                        border: `1px solid ${colors.accent}30`,
                        borderRadius: 3,
                        letterSpacing: 1,
                      }}
                    >
                      {f.tag}
                    </div>
                  </div>
                  <div
                    style={{
                      fontSize: 19,
                      color: colors.textSecondary,
                      fontFamily: fonts.sans,
                      lineHeight: 1.5,
                    }}
                  >
                    {f.desc}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
