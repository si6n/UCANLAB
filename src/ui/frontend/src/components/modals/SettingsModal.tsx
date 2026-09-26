import React, { useEffect, useState } from 'react';
import { DesktopBridge } from '../../services/bridge';
import { SettingsView } from '../settings/SettingsView';

export interface AppSettings {
  channel: string;
  baudRate: string;
  cloudBaseUrl?: string;
}

interface SettingsModalProps {
  isOpen: boolean;
  channel: string;
  baudRate: string;
  onClose: () => void;
  onSave: (settings: AppSettings) => void;
}

/* Compat shim (B-08): the modal shell was removed; the settings surface lives
 * in SettingsView. This file stays so persisted tests/imports keep a
 * `handleSave` that awaits the backend before persisting (D4 fail-closed). */
export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  channel,
  baudRate,
  onClose,
  onSave,
}) => {
  const [cloudUrl, setCloudUrl] = useState(() => {
    try {
      return localStorage.getItem('cloud_base_url') || 'https://ucan-cloud.si6n.io';
    } catch {
      return 'https://ucan-cloud.si6n.io';
    }
  });
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    try {
      setCloudUrl(localStorage.getItem('cloud_base_url') || 'https://ucan-cloud.si6n.io');
    } catch {
      /* offline */
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = async () => {
    const requested = cloudUrl.trim();
    try {
      const res: any = await DesktopBridge.cloudSaveConfig(requested);
      if (res && res.success === false) {
        setActionFeedback({ type: 'error', text: res.error || 'Ayar kaydedilemedi' });
        return;
      }
      localStorage.setItem('cloud_base_url', requested);
      onSave({ channel, baudRate, cloudBaseUrl: requested });
      onClose();
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err?.message || 'Kayıt hatası' });
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" role="dialog" aria-modal="true">
      <div className="max-h-[90vh] w-[720px] overflow-auto rounded bg-bg-popover p-4">
        {actionFeedback && <div className="mb-2 text-xs">{actionFeedback.text}</div>}
        <SettingsView channel={channel} baudRate={baudRate} onSave={onSave} />
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded border px-3 py-1.5 text-xs">Kapat</button>
          <button type="button" onClick={handleSave} className="rounded bg-accent px-3 py-1.5 text-xs text-white">Kaydet</button>
        </div>
      </div>
    </div>
  );
};
