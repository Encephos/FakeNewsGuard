import React from 'react';
import { Series } from 'remotion';
import { LogoScene } from './scenes/LogoScene';
import { FeaturesScene } from './scenes/FeaturesScene';
import { PipelineScene } from './scenes/PipelineScene';
import { AgentsScene } from './scenes/AgentsScene';
import { StatsScene } from './scenes/StatsScene';

export const FakeNewsGuardIntro: React.FC = () => {
  return (
    <Series>
      <Series.Sequence durationInFrames={90}>
        <LogoScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={90}>
        <FeaturesScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={75}>
        <PipelineScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={60}>
        <AgentsScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={45}>
        <StatsScene />
      </Series.Sequence>
    </Series>
  );
};
