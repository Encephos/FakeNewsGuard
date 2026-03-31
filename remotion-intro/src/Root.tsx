import React from 'react';
import { Composition } from 'remotion';
import { FakeNewsGuardIntro } from './FakeNewsGuardIntro';

export const Root: React.FC = () => {
  return (
    <Composition
      id="FakeNewsGuardIntro"
      component={FakeNewsGuardIntro}
      durationInFrames={360}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
