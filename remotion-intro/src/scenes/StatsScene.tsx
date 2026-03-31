import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';
import { colors, fonts } from '../components/theme';

const stats = [
  { value: '20+', label: 'Quellen pro Analyse' },
  { value: '15+', label: 'Manipulationstechniken' },
  { value: '40+', label: 'Unterstützte Sprachen' },
  { value: '<3 Min', label: 'Analyse-Dauer' },
];

export const StatsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

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
      {/* Stats row */}
      <div
        style={{
          display: 'flex',
          gap: 64,
          marginBottom: 64,
        }}
      >
        {stats.map((stat, i) => {
          const delay = 5 + i * 8;
          const localFrame = Math.max(0, frame - delay);
          const progress = spring({
            frame: localFrame,
            fps,
            config: { damping: 14, stiffness: 100, mass: 0.5 },
          });
          const opacity = interpolate(localFrame, [0, 12], [0, 1], {
            extrapolateRight: 'clamp',
          });
          const scale = interpolate(progress, [0, 1], [0.7, 1]);

          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `scale(${scale})`,
                textAlign: 'center',
              }}
            >
              <div
                style={{
                  fontSize: 56,
                  fontWeight: 800,
                  color: colors.accent,
                  fontFamily: fonts.sans,
                  letterSpacing: -1,
                  marginBottom: 8,
                }}
              >
                {stat.value}
              </div>
              <div
                style={{
                  fontSize: 17,
                  fontWeight: 500,
                  color: colors.textSecondary,
                  fontFamily: fonts.sans,
                }}
              >
                {stat.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* CTA button */}
      {(() => {
        const ctaDelay = 25;
        const ctaFrame = Math.max(0, frame - ctaDelay);
        const ctaOpacity = interpolate(ctaFrame, [0, 15], [0, 1], {
          extrapolateRight: 'clamp',
        });
        const ctaScale = spring({
          frame: ctaFrame,
          fps,
          config: { damping: 12, stiffness: 100, mass: 0.5 },
        });

        return (
          <div
            style={{
              opacity: ctaOpacity,
              transform: `scale(${ctaScale})`,
            }}
          >
            <div
              style={{
                backgroundColor: colors.accent,
                color: '#fff',
                fontSize: 22,
                fontWeight: 700,
                fontFamily: fonts.sans,
                padding: '18px 48px',
                borderRadius: 12,
                letterSpacing: 0.5,
              }}
            >
              Jetzt starten →
            </div>
          </div>
        );
      })()}
    </AbsoluteFill>
  );
};
