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

const stats = [
  { value: '20+', label: 'Quellen pro Analyse', id: 'SRC' },
  { value: '15+', label: 'Manipulationstechniken', id: 'DET' },
  { value: '40+', label: 'Unterstützte Sprachen', id: 'I18N' },
  { value: '<3 Min', label: 'Analyse-Dauer', id: 'LAT' },
];

export const StatsSceneMobile: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bgPrimary,
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: '80px 60px',
      }}
    >
      <TechOverlay label="sys::metrics" />

      {/* Stats in 2x2 grid */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 36,
          justifyContent: 'center',
          maxWidth: 720,
          marginBottom: 72,
        }}
      >
        {stats.map((stat, i) => {
          const delay = 5 + i * 10;
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
                width: 290,
              }}
            >
              <div
                style={{
                  backgroundColor: colors.glassBg,
                  border: `1px solid ${colors.glassBorder}`,
                  borderRadius: 10,
                  padding: '32px 20px 28px',
                }}
              >
                <div
                  style={{
                    fontSize: 11,
                    fontFamily: fonts.mono,
                    color: colors.textTertiary,
                    letterSpacing: 2,
                    marginBottom: 12,
                  }}
                >
                  [{stat.id}]
                </div>
                <div
                  style={{
                    fontSize: 56,
                    fontWeight: 800,
                    color: colors.accent,
                    fontFamily: fonts.mono,
                    letterSpacing: -1,
                    marginBottom: 8,
                  }}
                >
                  {stat.value}
                </div>
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 500,
                    color: colors.textSecondary,
                    fontFamily: fonts.sans,
                  }}
                >
                  {stat.label}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* CTA button */}
      {(() => {
        const ctaDelay = 30;
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
                background: `linear-gradient(135deg, ${colors.accent}, #ff5050)`,
                color: '#fff',
                fontSize: 26,
                fontWeight: 700,
                fontFamily: fonts.sans,
                padding: '22px 56px',
                borderRadius: 10,
                letterSpacing: 0.5,
                boxShadow: `0 8px 32px ${colors.accent}40`,
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
