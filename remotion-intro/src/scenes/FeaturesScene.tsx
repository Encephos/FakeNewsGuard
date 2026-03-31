import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';
import { colors, fonts } from '../components/theme';
import { GlassCard } from '../components/GlassCard';

const features = [
  {
    title: 'Multi-Quellen-Verifizierung',
    desc: 'Automatische Recherche und Abgleich mit vertrauenswürdigen Quellen',
    icon: '🔍',
  },
  {
    title: 'Manipulationserkennung',
    desc: 'Erkennung rhetorischer Tricks, Zahlenmanipulation und Framing',
    icon: '🛡️',
  },
  {
    title: 'Claim-Extraktion',
    desc: 'Automatische Identifikation prüfbarer Behauptungen',
    icon: '📋',
  },
  {
    title: 'Echtzeit-Analyse',
    desc: 'Vollständige Analyse in unter 3 Minuten',
    icon: '⚡',
  },
];

export const FeaturesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Section title
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
        padding: 80,
      }}
    >
      {/* Section title */}
      <div
        style={{
          opacity: titleOpacity,
          fontSize: 20,
          fontWeight: 600,
          color: colors.accent,
          fontFamily: fonts.sans,
          textTransform: 'uppercase',
          letterSpacing: 4,
          marginBottom: 48,
        }}
      >
        Features
      </div>

      {/* Feature cards grid */}
      <div
        style={{
          display: 'flex',
          gap: 24,
          flexWrap: 'wrap',
          justifyContent: 'center',
          maxWidth: 1400,
        }}
      >
        {features.map((f, i) => {
          const delay = 10 + i * 12;
          const localFrame = Math.max(0, frame - delay);
          const progress = spring({
            frame: localFrame,
            fps,
            config: { damping: 14, stiffness: 100, mass: 0.6 },
          });
          const opacity = interpolate(localFrame, [0, 15], [0, 1], {
            extrapolateRight: 'clamp',
          });
          const translateY = interpolate(progress, [0, 1], [60, 0]);

          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `translateY(${translateY}px)`,
                width: 310,
              }}
            >
              <GlassCard>
                <div style={{ fontSize: 32, marginBottom: 12 }}>{f.icon}</div>
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: colors.textPrimary,
                    fontFamily: fonts.sans,
                    marginBottom: 8,
                  }}
                >
                  {f.title}
                </div>
                <div
                  style={{
                    fontSize: 15,
                    color: colors.textSecondary,
                    fontFamily: fonts.sans,
                    lineHeight: 1.5,
                  }}
                >
                  {f.desc}
                </div>
              </GlassCard>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
