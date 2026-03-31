import React from 'react';
import { colors } from './theme';

export const GlassCard: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ children, style }) => (
  <div
    style={{
      backgroundColor: colors.glassBg,
      border: `1px solid ${colors.glassBorder}`,
      borderRadius: 16,
      padding: '24px 28px',
      backdropFilter: 'blur(12px)',
      ...style,
    }}
  >
    {children}
  </div>
);
