import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { VehicleIdentityResult, VehicleProfileInfo } from '../../services/bridge';
import { BTN_SECONDARY, L, pick } from './text';

/** Warns when the vehicle's VIN / J1939 maker contradicts the selection (MECHANIC_FLOW.md §4). */
export const IdentityNotice: React.FC<{
  result: VehicleIdentityResult | null;
  profiles: VehicleProfileInfo[];
  onSwitch: (profileId: string) => void;
}> = ({ result, profiles, onSwitch }) => {
  if (!result || !result.success || result.status !== 'mismatch') return null;
  const suggestion = profiles.find((p) => p.id === result.suggested_profile_id);
  const detected = pick(result, 'detected');
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-warn bg-bg-card p-3 text-sm" role="alert">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-warn" />
        <span>
          {L(
            `Seçtiğiniz araç ile aracın kendisi uyuşmuyor. Araç kendini "${detected}" olarak bildiriyor.`,
            `Your selection doesn't match the vehicle. It reports itself as "${detected}".`,
          )}
        </span>
      </div>
      {suggestion && (
        <button type="button" className={BTN_SECONDARY} onClick={() => onSwitch(suggestion.id)}>
          {L(`${pick(suggestion, 'label')} olarak değiştir`, `Switch to ${pick(suggestion, 'label')}`)}
        </button>
      )}
    </div>
  );
};
