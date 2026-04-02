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

/* ──────────────────────────────────────────────────────────────────────────
   Scout Workflow: The standard pipeline
   ────────────────────────────────────────────────────────────────────────── */

const scoutSteps = [
  { icon: '📝', title: 'Eingabe', desc: 'Text oder URL', color: colors.textSecondary },
  { icon: '🧠', title: 'Claim-Extraktion', desc: 'Behauptungen identifizieren', color: colors.accent },
  { icon: '🔍', title: 'Websuche', desc: 'SearXNG + LangSearch', color: colors.success },
  { icon: '⚖️', title: 'Faktencheck', desc: 'Evidenz bewerten', color: colors.warning },
  { icon: '📊', title: 'Verdikt', desc: 'Gesamtbewertung', color: colors.accent },
];

export const ScoutWorkflowMobile: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bgPrimary,
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: '60px 50px',
      }}
    >
      <TechOverlay label="mode::scout" />

      {/* Badge */}
      <WorkflowBadge
        frame={frame}
        label="Scout"
        sublabel="Pro · Max · Lite"
        badgeColor={colors.accent}
      />

      {/* Subtitle */}
      <div
        style={{
          opacity: interpolate(frame, [0, 15], [0, 1], { extrapolateRight: 'clamp' }),
          fontSize: 28,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 56,
          letterSpacing: -0.3,
          textAlign: 'center',
        }}
      >
        Standard-Pipeline
      </div>

      {/* Steps */}
      <WorkflowSteps steps={scoutSteps} frame={frame} fps={fps} />
    </AbsoluteFill>
  );
};

/* ──────────────────────────────────────────────────────────────────────────
   Commander Workflow: Iterative search refinement layer
   ────────────────────────────────────────────────────────────────────────── */

const commanderSteps = [
  { icon: '📝', title: 'Eingabe', desc: 'Text oder URL', color: colors.textSecondary },
  { icon: '🧠', title: 'Claim-Extraktion', desc: 'Behauptungen identifizieren', color: colors.accent },
  { icon: '🎖', title: 'Commander: Queries', desc: 'Suchanfragen pro Claim', color: '#c084fc', isCommander: true },
  { icon: '🔍', title: 'Websuche', desc: 'SearXNG + LangSearch', color: colors.success },
  { icon: '🎖', title: 'Commander: Review', desc: 'Kontext ausreichend?', color: '#c084fc', isDecision: true, isCommander: true },
  { icon: '⚖️', title: 'Faktencheck', desc: 'Evidenz bewerten', color: colors.warning },
  { icon: '📊', title: 'Verdikt', desc: 'Gesamtbewertung', color: colors.accent },
];

export const CommanderWorkflowMobile: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bgPrimary,
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: '60px 50px',
      }}
    >
      <TechOverlay label="mode::commander" />

      {/* Badge */}
      <WorkflowBadge
        frame={frame}
        label="Commander"
        sublabel="Pro · Max"
        badgeColor="#c084fc"
      />

      {/* Subtitle */}
      <div
        style={{
          opacity: interpolate(frame, [0, 15], [0, 1], { extrapolateRight: 'clamp' }),
          fontSize: 26,
          fontWeight: 700,
          color: colors.textPrimary,
          fontFamily: fonts.sans,
          marginBottom: 40,
          letterSpacing: -0.3,
          textAlign: 'center',
        }}
      >
        Scout + iterative Suchverfeinerung
      </div>

      {/* Steps */}
      <WorkflowSteps steps={commanderSteps} frame={frame} fps={fps} />

      {/* Loop indicator between steps 4→5 (Websuche → Commander: Review) */}
      {(() => {
        const loopDelay = 70;
        const loopFrame = Math.max(0, frame - loopDelay);
        const loopOpacity = interpolate(loopFrame, [0, 15], [0, 0.8], {
          extrapolateRight: 'clamp',
        });
        const pulseAlpha = interpolate(
          Math.sin(frame * 0.07),
          [-1, 1],
          [0.3, 0.8],
        );
        return (
          <div
            style={{
              position: 'absolute',
              right: 50,
              top: '52%',
              transform: 'translateY(0px)',
              opacity: loopOpacity,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 5,
            }}
          >
            <div
              style={{
                fontSize: 12,
                color: '#c084fc',
                fontFamily: fonts.mono,
                fontWeight: 600,
                writingMode: 'vertical-rl',
                textOrientation: 'mixed',
                letterSpacing: 3,
                opacity: pulseAlpha,
              }}
            >
              RETRY
            </div>
            <div style={{ fontSize: 20, color: '#c084fc', opacity: pulseAlpha }}>↺</div>
            <div
              style={{
                fontSize: 11,
                color: colors.textTertiary,
                fontFamily: fonts.mono,
                writingMode: 'vertical-rl',
                textOrientation: 'mixed',
                letterSpacing: 1,
              }}
            >
              max 3x
            </div>
          </div>
        );
      })()}
    </AbsoluteFill>
  );
};

/* ──────────────────────────────────────────────────────────────────────────
   Shared components
   ────────────────────────────────────────────────────────────────────────── */

interface WorkflowStep {
  icon: string;
  title: string;
  desc: string;
  color: string;
  isDecision?: boolean;
  isCommander?: boolean;
}

const WorkflowBadge: React.FC<{
  frame: number;
  label: string;
  sublabel: string;
  badgeColor: string;
}> = ({ frame, label, sublabel, badgeColor }) => {
  const opacity = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        opacity,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        marginBottom: 12,
      }}
    >
      <div
        style={{
          fontSize: 20,
          fontWeight: 600,
          color: badgeColor,
          fontFamily: fonts.mono,
          textTransform: 'uppercase',
          letterSpacing: 5,
        }}
      >
        {`> ${label}`}
      </div>
      <div
        style={{
          fontSize: 14,
          color: colors.textTertiary,
          fontFamily: fonts.mono,
          marginTop: 8,
          padding: '3px 12px',
          border: `1px solid ${colors.border}`,
          borderRadius: 3,
          letterSpacing: 1,
        }}
      >
        {sublabel}
      </div>
    </div>
  );
};

const WorkflowSteps: React.FC<{
  steps: WorkflowStep[];
  frame: number;
  fps: number;
}> = ({ steps, frame, fps }) => {
  const compact = steps.length > 5;
  const iconSize = compact ? 52 : 64;
  const titleSize = compact ? 19 : 22;
  const descSize = compact ? 14 : 16;
  const stepDelay = compact ? 9 : 12;
  const connectorTarget = compact ? 24 : 36;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0,
      }}
    >
      {steps.map((step, i) => {
        const delay = 10 + i * stepDelay;
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

        const arrowDelay = delay + 8;
        const arrowFrame = Math.max(0, frame - arrowDelay);
        const arrowHeight = interpolate(arrowFrame, [0, 12], [0, connectorTarget], {
          extrapolateRight: 'clamp',
        });

        // Commander steps get a subtle left-border highlight
        const isCmd = step.isCommander;

        return (
          <React.Fragment key={i}>
            <div
              style={{
                opacity,
                transform: `scale(${scale})`,
                display: 'flex',
                alignItems: 'center',
                gap: compact ? 16 : 22,
                width: 620,
                padding: isCmd ? '4px 0 4px 12px' : '4px 0',
                borderLeft: isCmd ? `2px solid ${step.color}50` : '2px solid transparent',
                borderRadius: isCmd ? 4 : 0,
              }}
            >
              {/* Icon circle */}
              <div
                style={{
                  width: iconSize,
                  height: iconSize,
                  borderRadius: step.isDecision ? 10 : 12,
                  backgroundColor: `${step.color}12`,
                  border: `2px solid ${step.color}40`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: compact ? 22 : 28,
                  flexShrink: 0,
                  boxShadow: step.isDecision ? `0 0 20px ${step.color}20` : 'none',
                }}
              >
                {step.icon}
              </div>

              {/* Text */}
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: titleSize,
                    fontWeight: 700,
                    color: step.color,
                    fontFamily: fonts.sans,
                    marginBottom: 1,
                  }}
                >
                  {step.title}
                </div>
                <div
                  style={{
                    fontSize: descSize,
                    color: colors.textTertiary,
                    fontFamily: fonts.mono,
                    letterSpacing: 0.5,
                  }}
                >
                  {step.desc}
                </div>
              </div>

              {/* NEW badge for commander steps */}
              {isCmd && (
                <div
                  style={{
                    fontSize: 10,
                    fontFamily: fonts.mono,
                    color: step.color,
                    padding: '2px 6px',
                    border: `1px solid ${step.color}40`,
                    borderRadius: 3,
                    letterSpacing: 1.5,
                    flexShrink: 0,
                  }}
                >
                  NEU
                </div>
              )}
            </div>

            {/* Vertical connector — dashed */}
            {i < steps.length - 1 && (
              <div
                style={{
                  width: 2,
                  height: arrowHeight,
                  backgroundImage: steps[i + 1]?.isCommander
                    ? `repeating-linear-gradient(to bottom, ${steps[i + 1].color}60 0px, ${steps[i + 1].color}60 4px, transparent 4px, transparent 8px)`
                    : `repeating-linear-gradient(to bottom, ${colors.border} 0px, ${colors.border} 4px, transparent 4px, transparent 8px)`,
                }}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};
