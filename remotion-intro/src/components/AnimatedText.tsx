import React from 'react';
import { useCurrentFrame, interpolate, spring, useVideoConfig } from 'remotion';

export const AnimatedText: React.FC<{
  text: string;
  delay?: number;
  fontSize?: number;
  color?: string;
  fontWeight?: number;
  style?: React.CSSProperties;
}> = ({ text, delay = 0, fontSize = 48, color = '#ece8e2', fontWeight = 700, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const localFrame = Math.max(0, frame - delay);

  const opacity = interpolate(localFrame, [0, 20], [0, 1], {
    extrapolateRight: 'clamp',
  });

  const translateY = interpolate(
    spring({ frame: localFrame, fps, config: { damping: 200, stiffness: 100 } }),
    [0, 1],
    [30, 0],
  );

  return (
    <div
      style={{
        opacity,
        transform: `translateY(${translateY}px)`,
        fontSize,
        fontWeight,
        color,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        letterSpacing: -0.5,
        ...style,
      }}
    >
      {text}
    </div>
  );
};
