import React, { useState } from 'react';
import { Activity } from 'lucide-react';
import { DesktopBridge } from '../../services/bridge';
import { L } from '../mechanic/text';
import { StatusText } from '../workbench/ui';
import { DisplayMenu } from './DisplayMenu';
import { EstopBanner, EstopButton, EstopResetDialog } from './Estop';
import { safetyView } from './safety';
import { useSafetyState } from './useSafetyState';
import { WindowControls } from './WindowControls';

/**
 * Window frame for the sign-in and mechanic screens: the window is
 * frameless, so this bar carries the drag area, the safety state, the
 * E-Stop and the window buttons. Content scrolls below it.
 */
export const AppFrame: React.FC<{
  subtitle?: string;
  context?: React.ReactNode;
  extra?: React.ReactNode;
  children: React.ReactNode;
}> = ({ subtitle, context, extra, children }) => {
  const native = DesktopBridge.isNative();
  const { safety, refresh } = useSafetyState(native);
  const [resetOpen, setResetOpen] = useState(false);
  const sv = safetyView(native ? safety : null);

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-bg-app text-text-body">
      <header className="flex h-14 flex-none items-center gap-3 border-b border-border-whisper bg-bg-rail pl-4 pr-2">
        <div className="pywebview-drag-region flex min-w-0 flex-1 items-center gap-2.5 self-stretch">
          <span className="flex h-8 w-8 flex-none items-center justify-center rounded-xl border border-accent-line bg-accent-soft text-accent">
            <Activity className="h-4 w-4" />
          </span>
          <span className="flex-none">
            <span className="block text-[14px] font-semibold leading-tight text-text-hi">UCanLab</span>
            {subtitle && <span className="block text-[12px] text-text-mid">{subtitle}</span>}
          </span>
          {context && (
            <span className="ml-2 min-w-0 truncate border-l border-border-whisper pl-4 text-[13px] text-text-body" data-testid="frame-context">
              {context}
            </span>
          )}
        </div>
        <StatusText tone={sv.tone} title={sv.detail} testId="frame-safety">
          {sv.text}
        </StatusText>
        <EstopButton native={native} onTriggered={refresh} />
        <span className="mx-1 h-5 w-px flex-none bg-border-whisper" aria-hidden="true" />
        {extra}
        <DisplayMenu />
        <WindowControls native={native} />
      </header>
      {safety === 'FAULT' && <EstopBanner detail={sv.detail} onOpenReset={() => setResetOpen(true)} />}
      {resetOpen && <EstopResetDialog onClose={() => setResetOpen(false)} onReset={refresh} />}
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </div>
  );
};

/** Shown in the frame while the app reads its state at start-up. */
export const FrameLoading: React.FC<{ text?: string }> = ({ text }) => (
  <main className="flex flex-1 items-center justify-center text-[14px] text-text-mid">
    <p role="status">{text ?? L('UCanLab hazırlanıyor…', 'Getting UCanLab ready…')}</p>
  </main>
);
