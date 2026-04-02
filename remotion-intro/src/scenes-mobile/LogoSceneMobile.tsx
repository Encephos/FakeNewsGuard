import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  random,
  Img,
  staticFile,
} from 'remotion';
import { colors, fonts } from '../components/theme';
import { TechOverlay } from '../components/TechOverlay';

const PARTICLE_COUNT = 50;

const Particles: React.FC = () => {
  const frame = useCurrentFrame();
  const particles = Array.from({ length: PARTICLE_COUNT }, (_, i) => {
    const x = random(`mx-${i}`) * 1080;
    const y = random(`my-${i}`) * 1920;
    const size = 1 + random(`ms-${i}`) * 2.5;
    const speed = 0.15 + random(`msp-${i}`) * 0.35;
    const phase = random(`mph-${i}`) * Math.PI * 2;
    const opacity = interpolate(
      Math.sin(frame * 0.03 * speed + phase),
      [-1, 1],
      [0.03, 0.18],
    );
    return { x, y, size, opacity };
  });

  return (
    <>
      {particles.map((p, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: p.x,
            top: p.y,
            width: p.size,
            height: p.size,
            borderRadius: '50%',
            backgroundColor: colors.accent,
            opacity: p.opacity,
          }}
        />
      ))}
    </>
  );
};

export const LogoSceneMobile: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoScale = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 80, mass: 0.8 },
  });
  const logoOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: 'clamp',
  });

  const titleDelay = 15;
  const titleFrame = Math.max(0, frame - titleDelay);
  const titleOpacity = interpolate(titleFrame, [0, 20], [0, 1], {
    extrapolateRight: 'clamp',
  });
  const titleY = interpolate(
    spring({ frame: titleFrame, fps, config: { damping: 200, stiffness: 100 } }),
    [0, 1],
    [25, 0],
  );

  const tagDelay = 35;
  const tagFrame = Math.max(0, frame - tagDelay);
  const tagOpacity = interpolate(tagFrame, [0, 25], [0, 1], {
    extrapolateRight: 'clamp',
  });
  const tagY = interpolate(
    spring({ frame: tagFrame, fps, config: { damping: 200, stiffness: 80 } }),
    [0, 1],
    [20, 0],
  );

  const subDelay = 50;
  const subFrame = Math.max(0, frame - subDelay);
  const subOpacity = interpolate(subFrame, [0, 25], [0, 1], {
    extrapolateRight: 'clamp',
  });

  // Version badge
  const verDelay = 65;
  const verFrame = Math.max(0, frame - verDelay);
  const verOpacity = interpolate(verFrame, [0, 15], [0, 1], {
    extrapolateRight: 'clamp',
  });

  // Glow pulse
  const glowPulse = interpolate(Math.sin(frame * 0.04), [-1, 1], [0.08, 0.18]);

  return (
    <AbsoluteFill style={{ backgroundColor: colors.bgPrimary }}>
      <Particles />
      <TechOverlay />

      {/* Radial glow */}
      <div
        style={{
          position: 'absolute',
          top: '35%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          width: 600,
          height: 600,
          borderRadius: '50%',
          background: `radial-gradient(circle, rgba(224,48,48,${glowPulse}) 0%, transparent 70%)`,
          filter: 'blur(60px)',
        }}
      />

      <AbsoluteFill
        style={{
          justifyContent: 'center',
          alignItems: 'center',
          flexDirection: 'column',
          gap: 32,
        }}
      >
        <div
          style={{
            opacity: logoOpacity,
            transform: `scale(${logoScale})`,
            marginBottom: 24,
          }}
        >
          <Img src={staticFile('header-logo-dark.svg')} style={{ width: 500 }} />
        </div>

        <div
          style={{
            opacity: titleOpacity,
            transform: `translateY(${titleY}px)`,
            fontSize: 44,
            fontWeight: 300,
            color: colors.textSecondary,
            fontFamily: fonts.sans,
            letterSpacing: -0.3,
            textAlign: 'center',
          }}
        >
          KI-gest&uuml;tzter Faktencheck
        </div>

        <div
          style={{
            opacity: tagOpacity,
            transform: `translateY(${tagY}px)`,
            fontSize: 30,
            fontWeight: 400,
            color: colors.textTertiary,
            fontFamily: fonts.sans,
            maxWidth: 700,
            textAlign: 'center',
            lineHeight: 1.5,
          }}
        >
          f&uuml;r Nachrichten und Behauptungen
        </div>

        {/* Animated underline */}
        <div
          style={{
            opacity: subOpacity,
            width: interpolate(subFrame, [0, 30], [0, 160], { extrapolateRight: 'clamp' }),
            height: 2,
            background: `linear-gradient(90deg, transparent, ${colors.accent}, transparent)`,
            borderRadius: 2,
            marginTop: 8,
          }}
        />

        {/* Version badge */}
        <div
          style={{
            opacity: verOpacity,
            fontSize: 14,
            fontFamily: fonts.mono,
            color: colors.textTertiary,
            letterSpacing: 2,
            marginTop: 16,
            padding: '6px 16px',
            border: `1px solid ${colors.border}`,
            borderRadius: 4,
          }}
        >
          v2.0 // NEURAL VERIFICATION ENGINE
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
