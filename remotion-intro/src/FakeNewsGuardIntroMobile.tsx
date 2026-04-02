import React from 'react';
import { Series } from 'remotion';
import { LogoSceneMobile } from './scenes-mobile/LogoSceneMobile';
import { FeaturesSceneMobile } from './scenes-mobile/FeaturesSceneMobile';
import { PipelineSceneMobile } from './scenes-mobile/PipelineSceneMobile';
import { AgentsSceneMobile } from './scenes-mobile/AgentsSceneMobile';
import { ScoutWorkflowMobile, CommanderWorkflowMobile } from './scenes-mobile/TiersSceneMobile';
import { StatsSceneMobile } from './scenes-mobile/StatsSceneMobile';

export const FakeNewsGuardIntroMobile: React.FC = () => {
  return (
    <Series>
      <Series.Sequence durationInFrames={150}>
        <LogoSceneMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={150}>
        <FeaturesSceneMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={135}>
        <PipelineSceneMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={120}>
        <AgentsSceneMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={150}>
        <ScoutWorkflowMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={180}>
        <CommanderWorkflowMobile />
      </Series.Sequence>
      <Series.Sequence durationInFrames={105}>
        <StatsSceneMobile />
      </Series.Sequence>
    </Series>
  );
};
