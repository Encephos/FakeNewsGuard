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

const PARTICLE_COUNT = 60;

const Particles: React.FC = () => {
  const frame = useCurrentFrame();
  const particles = Array.from({ length: PARTICLE_COUNT }, (_, i) => {
    const x = random(`x-${i}`) * 1920;
    const y = random(`y-${i}`) * 1080;
    const size = 1.5 + random(`s-${i}`) * 2.5;
    const speed = 0.2 + random(`sp-${i}`) * 0.4;
    const phase = random(`ph-${i}`) * Math.PI * 2;
    const opacity = interpolate(
      Math.sin(frame * 0.03 * speed + phase),
      [-1, 1],
      [0.05, 0.2],
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

export const LogoScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Shield logo scale-in
  const logoScale = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 80, mass: 0.8 },
  });
  const logoOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: 'clamp',
  });

  // Title fade-in (delay 15 frames)
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

  // Tagline fade-in (delay 35 frames)
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

  // Subtitle (delay 50)
  const subDelay = 50;
  const subFrame = Math.max(0, frame - subDelay);
  const subOpacity = interpolate(subFrame, [0, 25], [0, 1], {
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{ backgroundColor: colors.bgPrimary }}>
      <Particles />
      <AbsoluteFill
        style={{
          justifyContent: 'center',
          alignItems: 'center',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        {/* Shield logo */}
        <div
          style={{
            opacity: logoOpacity,
            transform: `scale(${logoScale})`,
            marginBottom: 16,
          }}
        >
          <Img src={staticFile('header-logo-dark.svg')} style={{ width: 420 }} />
        </div>

        {/* Tagline */}
        <div
          style={{
            opacity: titleOpacity,
            transform: `translateY(${titleY}px)`,
            fontSize: 36,
            fontWeight: 300,
            color: colors.textSecondary,
            fontFamily: fonts.sans,
            letterSpacing: -0.3,
          }}
        >
          KI-gestützter Faktencheck
        </div>

        {/* Subtitle */}
        <div
          style={{
            opacity: tagOpacity,
            transform: `translateY(${tagY}px)`,
            fontSize: 22,
            fontWeight: 400,
            color: colors.textTertiary,
            fontFamily: fonts.sans,
            maxWidth: 600,
            textAlign: 'center',
            lineHeight: 1.5,
          }}
        >
          für Nachrichten und Behauptungen
        </div>

        {/* Red accent line */}
        <div
          style={{
            opacity: subOpacity,
            width: interpolate(subFrame, [0, 30], [0, 120], { extrapolateRight: 'clamp' }),
            height: 3,
            backgroundColor: colors.accent,
            borderRadius: 2,
            marginTop: 8,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
