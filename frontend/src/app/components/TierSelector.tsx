"use client";

import { ScoutTier } from "../lib/types";
import { useI18n } from "../lib/i18n";

interface TierSelectorProps {
  value: ScoutTier;
  onChange: (tier: ScoutTier) => void;
  disabled?: boolean;
}

interface TierOption {
  id: ScoutTier;
  icon: string;
  group: "scout" | "commander";
}

const TIERS: TierOption[] = [
  { id: "lite", icon: "⚡", group: "scout" },
  { id: "pro", icon: "◆", group: "scout" },
  { id: "max", icon: "★", group: "scout" },
  { id: "commander-pro", icon: "🎖", group: "commander" },
  { id: "commander-max", icon: "🎖", group: "commander" },
];

export default function TierSelector({ value, onChange, disabled }: TierSelectorProps) {
  const { t } = useI18n();

  return (
    <div className="flex items-center gap-1">
      {TIERS.map(({ id, icon, group }, index) => {
        const isActive = value === id;
        // Separator between scout and commander groups
        const showSeparator = index > 0 && group !== TIERS[index - 1].group;
        return (
          <div key={id} className="flex items-center">
            {showSeparator && (
              <div className="w-px h-4 bg-border/40 mx-1" />
            )}
            <button
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
          </div>
        );
      })}
    </div>
  );
}
