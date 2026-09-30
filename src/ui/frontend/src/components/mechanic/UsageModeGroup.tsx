import React, { useState } from 'react';
import { Users } from 'lucide-react';
import { SettingRow, SettingsGroup, StatusPill, btnGhost } from '../settings/SettingsPrimitives';
import { useMechanicMode } from './MechanicFlow';
import { L, messageOf, pick } from './text';

/** Settings row: switch Mechanic ⇄ Engineer and change the vehicle (Aşama 4). */
export const UsageModeGroup: React.FC = () => {
  const api = useMechanicMode();
  const [error, setError] = useState('');
  if (!api) return null;
  const isEngineer = api.mode === 'engineer';
  const canEngineer = !!api.entitlements?.engineer;

  const toggle = async () => {
    setError('');
    const res = await api.setMode(isEngineer ? 'mechanic' : 'engineer');
    if (!res.success) setError(messageOf(res) || L('Mod değiştirilemedi.', 'Could not change the mode.'));
  };

  return (
    <SettingsGroup label={L('Kullanım modu', 'Usage mode')} icon={<Users className="h-3 w-3" />}>
      <SettingRow
        label={isEngineer ? L('Mühendis', 'Engineer') : L('Tamirci', 'Mechanic')}
        hint={
          canEngineer || isEngineer
            ? L('Tamirci modu sade sonuç gösterir; Mühendis modu uzman araçlarını açar.', 'Mechanic mode shows plain results; Engineer mode opens the expert tools.')
            : L('Mühendis modu paketinizde yok.', 'Engineer mode is not in your plan.')
        }
      >
        <StatusPill label={isEngineer ? 'ENGINEER' : 'MECHANIC'} tone={isEngineer ? 'accent' : 'ok'} />
        {(canEngineer || isEngineer) && (
          <button type="button" className={btnGhost} onClick={() => void toggle()}>
            {isEngineer ? L("Tamirci moduna geç", 'Switch to Mechanic') : L("Mühendis moduna geç", 'Switch to Engineer')}
          </button>
        )}
      </SettingRow>
      {!isEngineer && (
        <SettingRow
          label={L('Araç', 'Vehicle')}
          hint={api.vehicle ? pick(api.vehicle, 'label') : L('Henüz seçilmedi', 'Not selected yet')}
        >
          <button type="button" className={btnGhost} onClick={api.changeVehicle}>
            {L('Aracı değiştir', 'Change vehicle')}
          </button>
        </SettingRow>
      )}
      {error && <p className="px-4 py-2 text-[11px] text-del" role="alert">{error}</p>}
    </SettingsGroup>
  );
};
