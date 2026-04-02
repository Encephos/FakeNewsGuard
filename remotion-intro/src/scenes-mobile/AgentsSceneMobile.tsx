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

const agents = [
  { title: 'Claim-Extraktor', icon: '🧠', color: colors.accent, id: 'CLM' },
  { title: 'Faktenprüfer', icon: '🗄️', color: colors.success, id: 'FCK' },
  { title: 'Rhetorik-Analyst', icon: '👁️', color: colors.warning, id: 'RHT' },
  { title: 'Zahlenprüfer', icon: '🔢', color: '#6366f1', id: 'NUM' },
  { title: 'Bildanalyst', icon: '🖼️', color: '#8b5cf6', id: 'IMG' },
  { title: 'Synthesizer', icon: '⚖️', color: colors.accent, id: 'SYN' },
];

export const AgentsSceneMobile: React.FC = () => {
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
        padding: '80px 50px',
      }}
    >
      <TechOverlay label="sys::agents" />

      <div
        style={{
          opacity: titleOpacity,
          fontSize: 20,
          fontWeight: 600,
          color: colors.accent,
          fontFamily: fonts.mono,
          textTransform: 'uppercase',
          letterSpacing: 5,
          marginBottom: 12,
        }}
      >
        {'> Multi-Agenten'}
      </div>
      <div
        style={{
          opacity: titleOpacity,
          fontSize: 36,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 56,
          letterSpacing: -0.5,
          textAlign: 'center',
        }}
      >
        6 spezialisierte KI-Agenten
      </div>

      {/* 2-column grid */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 18,
          justifyContent: 'center',
          maxWidth: 860,
        }}
      >
        {agents.map((agent, i) => {
          const delay = 8 + i * 7;
          const localFrame = Math.max(0, frame - delay);
          const scale = spring({
            frame: localFrame,
            fps,
            config: { damping: 10, stiffness: 150, mass: 0.4 },
          });
          const opacity = interpolate(localFrame, [0, 10], [0, 1], {
            extrapolateRight: 'clamp',
          });

          // Animated border highlight
          const borderAlpha = interpolate(localFrame, [0, 15, 30], [0, 0.5, 0.2], {
            extrapolateRight: 'clamp',
          });

          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `scale(${scale})`,
                width: 400,
              }}
            >
              <div
                style={{
                  backgroundColor: colors.glassBg,
                  border: `1px solid rgba(255,255,255,${borderAlpha * 0.3})`,
                  borderLeft: `3px solid ${agent.color}`,
                  borderRadius: 8,
                  padding: '22px 24px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                }}
              >
                <div
                  style={{
                    fontSize: 32,
                    width: 56,
                    height: 56,
                    borderRadius: 8,
                    backgroundColor: `${agent.color}15`,
                    border: `1px solid ${agent.color}25`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {agent.icon}
                </div>
                <div style={{ flex: 1 }}>
                  <div
                    style={{
                      fontSize: 22,
                      fontWeight: 600,
                      color: colors.textPrimary,
                      fontFamily: fonts.sans,
                      marginBottom: 3,
                    }}
                  >
                    {agent.title}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      fontFamily: fonts.mono,
                      color: colors.textTertiary,
                      letterSpacing: 1.5,
                    }}
                  >
                    AGENT::{agent.id}
                  </div>
                </div>
                {/* Status indicator */}
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: agent.color,
                    boxShadow: `0 0 8px ${agent.color}60`,
                    opacity: interpolate(
                      Math.sin((frame - delay) * 0.08 + i),
                      [-1, 1],
                      [0.4, 1],
                    ),
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
