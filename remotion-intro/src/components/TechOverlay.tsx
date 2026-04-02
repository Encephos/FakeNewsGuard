import React from 'react';
import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';
import { colors, fonts } from './theme';

/**
 * Shared tech-style overlay: scanline sweep, corner HUD brackets,
 * subtle grid dots, and a top-bar label.
 */
export const TechOverlay: React.FC<{ label?: string }> = ({ label }) => {
  const frame = useCurrentFrame();
  const { height } = useVideoConfig();

  // Slow scanline sweeping down
  const scanY = interpolate(frame % 180, [0, 180], [0, height + 40]);

  return (
    <>
      {/* Grid dot pattern */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage:
            'radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)',
          backgroundSize: '40px 40px',
          pointerEvents: 'none',
        }}
      />

      {/* Horizontal scanline */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: scanY,
          height: 2,
          background: `linear-gradient(90deg, transparent 0%, ${colors.accent}30 30%, ${colors.accent}50 50%, ${colors.accent}30 70%, transparent 100%)`,
          pointerEvents: 'none',
        }}
      />

      {/* Corner brackets — top-left */}
      <Corner position="top-left" />
      <Corner position="top-right" />
      <Corner position="bottom-left" />
      <Corner position="bottom-right" />

      {/* Top-bar label */}
      {label && (
        <div
          style={{
            position: 'absolute',
            top: 36,
            left: 0,
            right: 0,
            display: 'flex',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              fontSize: 13,
              fontFamily: fonts.mono,
              color: `${colors.accent}90`,
              letterSpacing: 4,
              textTransform: 'uppercase',
              padding: '6px 20px',
              border: `1px solid ${colors.accent}25`,
              borderRadius: 4,
              backgroundColor: `${colors.accent}08`,
            }}
          >
            {label}
          </div>
        </div>
      )}
    </>
  );
};

const Corner: React.FC<{
  position: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
}> = ({ position }) => {
  const size = 28;
  const thickness = 2;
  const offset = 20;
  const color = `${colors.accent}40`;

  const isTop = position.includes('top');
  const isLeft = position.includes('left');

  return (
    <div
      style={{
        position: 'absolute',
        top: isTop ? offset : undefined,
        bottom: !isTop ? offset : undefined,
        left: isLeft ? offset : undefined,
        right: !isLeft ? offset : undefined,
        width: size,
        height: size,
        pointerEvents: 'none',
      }}
    >
      {/* Horizontal bar */}
      <div
        style={{
          position: 'absolute',
          [isTop ? 'top' : 'bottom']: 0,
          [isLeft ? 'left' : 'right']: 0,
          width: size,
          height: thickness,
          backgroundColor: color,
        }}
      />
      {/* Vertical bar */}
      <div
        style={{
          position: 'absolute',
          [isTop ? 'top' : 'bottom']: 0,
          [isLeft ? 'left' : 'right']: 0,
          width: thickness,
          height: size,
          backgroundColor: color,
        }}
      />
    </div>
  );
};
