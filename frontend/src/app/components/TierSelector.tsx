"use client";

import { ScoutTier } from "../lib/types";
import { useI18n } from "../lib/i18n";

interface TierSelectorProps {
  value: ScoutTier;
  onChange: (tier: ScoutTier) => void;
  disabled?: boolean;
}

const TIERS: { id: ScoutTier; icon: string }[] = [
  { id: "lite", icon: "⚡" },
  { id: "pro", icon: "◆" },
  { id: "max", icon: "★" },
];

export default function TierSelector({ value, onChange, disabled }: TierSelectorProps) {
  const { t } = useI18n();

  return (
    <div className="flex items-center gap-1">
      {TIERS.map(({ id, icon }) => {
        const isActive = value === id;
        return (
          <button
            key={id}
            onClick={() => onChange(id)}
            disabled={disabled}
            title={t(`tiers.${id}Desc`)}
            className={`
              flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium
              transition-all duration-150 select-none
              ${isActive
                ? "glass-inner border-accent/30 text-accent shadow-sm"
                : "text-text-tertiary hover:text-text-secondary hover:bg-surface-hover"
              }
              ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}
            `}
          >
            <span className="text-xs">{icon}</span>
            <span>{t(`tiers.${id}`)}</span>
          </button>
        );
      })}
    </div>
  );
}
