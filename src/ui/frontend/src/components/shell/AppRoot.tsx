import React, { useEffect, useState } from 'react';
import { MechanicFlow } from '../mechanic/MechanicFlow';
import { SignInGate } from '../mechanic/SignInGate';
import { LANG_EVENT } from '../mechanic/text';
import { Workbench } from '../workbench/Workbench';
import { useSystemThemeFollower } from './theme';

/**
 * Root of the app. Builds the screen tree itself so that a language change
 * re-renders every screen in place (no remount: a scan result or an open
 * settings page stays where it is).
 */
export const AppRoot: React.FC = () => {
  const [, setLangTick] = useState(0);
  useSystemThemeFollower();

  useEffect(() => {
    const onLang = () => setLangTick((n) => n + 1);
    window.addEventListener(LANG_EVENT, onLang);
    return () => window.removeEventListener(LANG_EVENT, onLang);
  }, []);

  return (
    <SignInGate>
      <MechanicFlow>
        <Workbench />
      </MechanicFlow>
    </SignInGate>
  );
};
