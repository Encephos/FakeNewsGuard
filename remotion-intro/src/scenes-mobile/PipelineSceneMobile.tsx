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

const steps = [
  { num: '01', title: 'Eingabe', desc: 'Text oder URL', status: 'INPUT' },
  { num: '02', title: 'Extraktion', desc: 'Claims identifizieren', status: 'NLP' },
  { num: '03', title: 'Recherche', desc: 'Multi-Quellen-Check', status: 'SEARCH' },
  { num: '04', title: 'Urteil', desc: 'Synthese & Verdikt', status: 'OUTPUT' },
];

export const PipelineSceneMobile: React.FC = () => {
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
        padding: '80px 60px',
      }}
    >
      <TechOverlay label="sys::pipeline" />

      <div
        style={{
          opacity: titleOpacity,
          fontSize: 20,
          fontWeight: 600,
          color: colors.accent,
          fontFamily: fonts.mono,
          textTransform: 'uppercase',
          letterSpacing: 5,
          marginBottom: 16,
        }}
      >
        {'> Wie es funktioniert'}
      </div>
      <div
        style={{
          opacity: titleOpacity,
          fontSize: 40,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 72,
          letterSpacing: -0.5,
          textAlign: 'center',
        }}
      >
        Vier Phasen der Faktenpr&uuml;fung
      </div>

      {/* Vertical pipeline */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 0,
        }}
      >
        {steps.map((step, i) => {
          const delay = 12 + i * 15;
          const localFrame = Math.max(0, frame - delay);
          const progress = spring({
            frame: localFrame,
            fps,
            config: { damping: 14, stiffness: 120, mass: 0.5 },
          });
          const opacity = interpolate(localFrame, [0, 12], [0, 1], {
            extrapolateRight: 'clamp',
          });
          const scale = interpolate(progress, [0, 1], [0.5, 1]);

          const arrowDelay = delay + 10;
          const arrowFrame = Math.max(0, frame - arrowDelay);
          const arrowHeight = interpolate(arrowFrame, [0, 15], [0, 48], {
            extrapolateRight: 'clamp',
          });

          // Pulsing glow for active step (last step)
          const isLast = i === steps.length - 1;
          const glowIntensity = isLast
            ? interpolate(Math.sin(frame * 0.06), [-1, 1], [0.15, 0.35])
            : 0;

          return (
            <React.Fragment key={i}>
              <div
                style={{
                  opacity,
                  transform: `scale(${scale})`,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 28,
                  width: 620,
                }}
              >
                {/* Number circle */}
                <div
                  style={{
                    width: 76,
                    height: 76,
                    borderRadius: 12,
                    backgroundColor: isLast ? `${colors.accent}20` : colors.bgTertiary,
                    border: `2px solid ${isLast ? colors.accent : colors.glassBorder}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 26,
                    fontWeight: 800,
                    color: isLast ? colors.accent : colors.textPrimary,
                    fontFamily: fonts.mono,
                    flexShrink: 0,
                    boxShadow: isLast ? `0 0 24px rgba(224, 48, 48, ${glowIntensity})` : 'none',
                  }}
                >
                  {step.num}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
                    <div
                      style={{
                        fontSize: 27,
                        fontWeight: 700,
                        color: colors.textPrimary,
                        fontFamily: fonts.sans,
                      }}
                    >
                      {step.title}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        fontFamily: fonts.mono,
                        color: colors.textTertiary,
                        padding: '2px 8px',
                        border: `1px solid ${colors.border}`,
                        borderRadius: 3,
                        letterSpacing: 1.5,
                      }}
                    >
                      {step.status}
                    </div>
                  </div>
                  <div
                    style={{
                      fontSize: 20,
                      color: colors.textTertiary,
                      fontFamily: fonts.sans,
                    }}
                  >
                    {step.desc}
                  </div>
                </div>
              </div>

              {/* Vertical connector — dashed style */}
              {i < steps.length - 1 && (
                <div
                  style={{
                    width: 2,
                    height: arrowHeight,
                    backgroundImage: `repeating-linear-gradient(to bottom, ${colors.border} 0px, ${colors.border} 4px, transparent 4px, transparent 8px)`,
                    marginLeft: 0,
                  }}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
