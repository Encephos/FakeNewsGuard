import React from 'react';
import { Composition } from 'remotion';
import { FakeNewsGuardIntro } from './FakeNewsGuardIntro';
import { FakeNewsGuardIntroMobile } from './FakeNewsGuardIntroMobile';

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="FakeNewsGuardIntro"
        component={FakeNewsGuardIntro}
        durationInFrames={360}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="FakeNewsGuardIntroMobile"
        component={FakeNewsGuardIntroMobile}
        durationInFrames={990}
        fps={30}
        width={1080}
        height={1920}
      />
    </>
  );
};
