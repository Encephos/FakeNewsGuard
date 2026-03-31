import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';
import { colors, fonts } from '../components/theme';

const steps = [
  { num: '01', title: 'Eingabe', desc: 'Text oder URL' },
  { num: '02', title: 'Extraktion', desc: 'Claims identifizieren' },
  { num: '03', title: 'Recherche', desc: 'Multi-Quellen-Check' },
  { num: '04', title: 'Urteil', desc: 'Synthese & Verdikt' },
];

export const PipelineScene: React.FC = () => {
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
          marginBottom: 16,
        }}
      >
        Wie es funktioniert
      </div>
      <div
        style={{
          opacity: titleOpacity,
          fontSize: 36,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 64,
          letterSpacing: -0.5,
        }}
      >
        Vier Phasen der Faktenprüfung
      </div>

      {/* Pipeline steps */}
      <div
        style={{
          display: 'flex',
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

          // Arrow between steps
          const arrowDelay = delay + 10;
          const arrowFrame = Math.max(0, frame - arrowDelay);
          const arrowWidth = interpolate(arrowFrame, [0, 15], [0, 80], {
            extrapolateRight: 'clamp',
          });

          return (
            <React.Fragment key={i}>
              <div
                style={{
                  opacity,
                  transform: `scale(${scale})`,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  width: 200,
                }}
              >
                {/* Number circle */}
                <div
                  style={{
                    width: 72,
                    height: 72,
                    borderRadius: '50%',
                    backgroundColor: i === 3 ? colors.accent : colors.bgTertiary,
                    border: `2px solid ${i === 3 ? colors.accent : colors.glassBorder}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 24,
                    fontWeight: 800,
                    color: i === 3 ? '#fff' : colors.textPrimary,
                    fontFamily: fonts.sans,
                    marginBottom: 16,
                  }}
                >
                  {step.num}
                </div>
                <div
                  style={{
                    fontSize: 22,
                    fontWeight: 700,
                    color: colors.textPrimary,
                    fontFamily: fonts.sans,
                    marginBottom: 6,
                  }}
                >
                  {step.title}
                </div>
                <div
                  style={{
                    fontSize: 15,
                    color: colors.textTertiary,
                    fontFamily: fonts.sans,
                  }}
                >
                  {step.desc}
                </div>
              </div>

              {/* Arrow connector */}
              {i < steps.length - 1 && (
                <div
                  style={{
                    width: arrowWidth,
                    height: 2,
                    backgroundColor: colors.border,
                    margin: '0 8px',
                    marginBottom: 50,
                    borderRadius: 1,
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
