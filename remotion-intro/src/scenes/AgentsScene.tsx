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

const agents = [
  { title: 'Claim-Extraktor', icon: '🧠', color: colors.accent },
  { title: 'Faktenprüfer', icon: '🗄️', color: colors.success },
  { title: 'Rhetorik-Analyst', icon: '👁️', color: colors.warning },
  { title: 'Zahlenprüfer', icon: '🔢', color: '#6366f1' },
  { title: 'Bildanalyst', icon: '🖼️', color: '#8b5cf6' },
  { title: 'Synthesizer', icon: '⚖️', color: colors.accent },
];

export const AgentsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bgPrimary,
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: 60,
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
          marginBottom: 12,
        }}
      >
        Multi-Agenten-Architektur
      </div>
      <div
        style={{
          opacity: titleOpacity,
          fontSize: 32,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 48,
          letterSpacing: -0.5,
        }}
      >
        6 spezialisierte KI-Agenten
      </div>

      {/* Agent grid: 3 columns × 2 rows */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 20,
          justifyContent: 'center',
          maxWidth: 1100,
        }}
      >
        {agents.map((agent, i) => {
          const delay = 8 + i * 6;
          const localFrame = Math.max(0, frame - delay);
          const scale = spring({
            frame: localFrame,
            fps,
            config: { damping: 10, stiffness: 150, mass: 0.4 },
          });
          const opacity = interpolate(localFrame, [0, 10], [0, 1], {
            extrapolateRight: 'clamp',
          });

          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `scale(${scale})`,
                width: 320,
              }}
            >
              <GlassCard
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                  padding: '20px 24px',
                }}
              >
                <div
                  style={{
                    fontSize: 28,
                    width: 48,
                    height: 48,
                    borderRadius: 12,
                    backgroundColor: `${agent.color}20`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {agent.icon}
                </div>
                <div
                  style={{
                    fontSize: 18,
                    fontWeight: 600,
                    color: colors.textPrimary,
                    fontFamily: fonts.sans,
                  }}
                >
                  {agent.title}
                </div>
              </GlassCard>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
