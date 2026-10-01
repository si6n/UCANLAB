import React, { useCallback, useEffect, useState } from 'react';
import { Hand, Play, Square, Undo2 } from 'lucide-react';
import { DesktopBridge, StimulusCandidate, StimulusStatus } from '../../services/bridge';
import { L } from '../mechanic/text';
import { formatId } from './frameBus';
import { BTN_GHOST, BTN_PRIMARY, Card, CardHeader, Chip, cx } from './ui';

/**
 * Bas-bırak deneyi (stimulus experiment): the operator alternates between
 * "dokunmayın" and "uygulayın" (press the pedal, turn the wheel, switch the
 * light) and Python ranks what changed between the two. On the workbench
 * simulator the buttons also press the simulated pedal, so the experiment
 * can be tried without a vehicle.
 */

function strength(c: StimulusCandidate): { text: string; width: number } {
  if (c.kind === 'bit') return { text: `%${Math.round(c.score * 100)}`, width: Math.min(1, c.score) };
  if (c.score >= 5) return { text: L('çok güçlü', 'very strong'), width: 1 };
  if (c.score >= 2) return { text: L('güçlü', 'strong'), width: 0.7 };
  return { text: L('orta', 'moderate'), width: 0.4 };
}

function fmt(v: number, kind: 'byte' | 'bit'): string {
  return kind === 'bit' ? `%${Math.round(v * 100)}` : v.toLocaleString('tr-TR', { maximumFractionDigits: 1 });
}

export const StimulusPanel: React.FC<{ simulator: boolean; onInspect: (key: string) => void }> = ({ simulator, onInspect }) => {
  const [status, setStatus] = useState<StimulusStatus>({ running: false });
  const [candidates, setCandidates] = useState<StimulusCandidate[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const res = await DesktopBridge.stimulusResult();
    if (res.error_code === 'NOT_RUNNING') {
      setStatus({ running: false });
      return;
    }
    setStatus(res);
    setCandidates(res.candidates ?? []);
  }, []);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(t);
  }, [refresh]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const start = () =>
    run(async () => {
      if (simulator) await DesktopBridge.simVehiclePedal(false);
      setCandidates([]);
      await DesktopBridge.stimulusStart();
    });
  const stop = () =>
    run(async () => {
      if (simulator) await DesktopBridge.simVehiclePedal(false);
      await DesktopBridge.stimulusStop();
      setCandidates([]);
    });
  const setPhase = (phase: 'rest' | 'active') =>
    run(async () => {
      await DesktopBridge.stimulusSetPhase(phase);
      if (simulator) await DesktopBridge.simVehiclePedal(phase === 'active');
    });

  const active = status.phase === 'active';
  const cycles = Math.floor((status.switches ?? 0) / 2);

  return (
    <div className="flex flex-col gap-3" data-testid="stimulus-panel">
      <Card>
        <CardHeader
          title={L('Bas-bırak deneyi', 'Press-and-release experiment')}
          hint={L(
            'Bir kontrolün (pedal, direksiyon, lamba…) hangi bitleri değiştirdiğini bulur. Uygulama araca hiçbir şey göndermez; yalnız dinler.',
            'Finds which bits a control (pedal, wheel, lamp…) changes. The app sends nothing to the vehicle; it only listens.',
          )}
        >
          {status.running ? (
            <button type="button" className={BTN_GHOST} onClick={() => void stop()} disabled={busy} data-testid="stimulus-stop">
              <Square className="h-4 w-4" />
              {L('Bitir', 'Finish')}
            </button>
          ) : (
            <button type="button" className={BTN_PRIMARY} onClick={() => void start()} disabled={busy} data-testid="stimulus-start">
              <Play className="h-4 w-4" />
              {L('Deneyi başlat', 'Start the experiment')}
            </button>
          )}
        </CardHeader>
        <ol className="grid grid-cols-1 gap-2 p-5 text-[13px] text-text-body md:grid-cols-3">
          <li className="rounded-xl border border-border-whisper p-3">
            <b className="text-text-hi">1.</b> {L('Başlatın ve birkaç saniye hiçbir şeye dokunmayın.', 'Start, then touch nothing for a few seconds.')}
          </li>
          <li className="rounded-xl border border-border-whisper p-3">
            <b className="text-text-hi">2.</b>{' '}
            {L('“Şimdi uygula”ya basın, kontrolü birkaç saniye tutun, sonra “Bırak”.', 'Press “Apply now”, hold the control a few seconds, then “Release”.')}
          </li>
          <li className="rounded-xl border border-border-whisper p-3">
            <b className="text-text-hi">3.</b> {L('2–3 kez tekrarlayın; sonuçlar her turda netleşir.', 'Repeat 2–3 times; results sharpen every round.')}
          </li>
        </ol>
        {status.running && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border-whisper px-5 py-4">
            <button
              type="button"
              data-testid="stimulus-toggle"
              onClick={() => void setPhase(active ? 'rest' : 'active')}
              disabled={busy}
              className={cx(
                'inline-flex min-w-[220px] items-center justify-center gap-2 rounded-xl px-5 py-3 text-[14px] font-semibold transition-colors',
                active ? 'border border-warn-border bg-warn-soft text-warn' : 'bg-accent text-bg-app hover:opacity-90',
              )}
            >
              {active ? <Undo2 className="h-4 w-4" /> : <Hand className="h-4 w-4" />}
              {active ? L('Bırak', 'Release') : L('Şimdi uygula', 'Apply now')}
            </button>
            <Chip tone={active ? 'warn' : 'neutral'} testId="stimulus-phase">
              {active ? L('Uygulanıyor', 'Applying') : L('Dokunmayın', 'Hands off')}
            </Chip>
            <span className="text-[12.5px] text-text-mid">
              {L(`${cycles} tur`, `${cycles} rounds`)} · {L('dinlenme', 'rest')} {status.rest_frames?.toLocaleString('tr-TR')} ·{' '}
              {L('uygulama', 'apply')} {status.active_frames?.toLocaleString('tr-TR')} {L('çerçeve', 'frames')}
            </span>
            {simulator && (
              <span className="text-[12.5px] text-warn">{L('Simülatörde düğme simüle pedala da basar.', 'On the simulator the button also presses the simulated pedal.')}</span>
            )}
          </div>
        )}
      </Card>

      {status.running && (
        <Card>
          <CardHeader
            title={L('Değişenler', 'What changed')}
            hint={L(
              'En çok değişen önce. Bu bir ilişkidir, kanıt değil: motor tepkisi gibi dolaylı etkiler de görünür. Anlamını siz doğrularsınız.',
              'Biggest change first. This is correlation, not proof: indirect effects (engine reaction) show up too. You confirm the meaning.',
            )}
          />
          {candidates.length === 0 ? (
            <p className="p-5 text-[13px] text-text-mid" data-testid="stimulus-none">
              {(status.active_frames ?? 0) === 0
                ? L('Henüz uygulama aşaması yok: “Şimdi uygula”ya basın.', 'No apply phase yet: press “Apply now”.')
                : L('Belirgin bir fark yok. Kontrolü daha uzun tutup tekrarlayın.', 'No clear difference yet. Hold the control longer and repeat.')}
            </p>
          ) : (
            <ul className="flex flex-col divide-y divide-border-whisper" data-testid="stimulus-results">
              {candidates.map((c) => {
                const st = strength(c);
                return (
                  <li key={`${c.key}-${c.kind}-${c.index}`} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                    <div className="min-w-0">
                      <div className="font-mono text-[13px] font-semibold text-text-hi">
                        {formatId(c.arbitration_id, c.extended)}{' '}
                        <span className="font-sans font-normal text-text-mid">
                          {c.kind === 'byte' ? L(`bayt ${c.index}`, `byte ${c.index}`) : L(`bit ${c.index}`, `bit ${c.index}`)}
                        </span>
                      </div>
                      <div className="text-[12.5px] tabular-nums text-text-mid">
                        {c.kind === 'bit' ? `${L('1 olma oranı', 'set share')} ` : `${L('ortalama', 'mean')} `}
                        {fmt(c.rest, c.kind)} → <b className="text-text-hi">{fmt(c.active, c.kind)}</b>
                        {c.simulated ? ` · ${L('simülatör', 'simulator')}` : ''}
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="w-28">
                        <div className="h-1.5 rounded-full bg-border-whisper">
                          <div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.round(st.width * 100)}%` }} />
                        </div>
                        <div className="mt-0.5 text-right text-[11.5px] text-text-mid">{st.text}</div>
                      </div>
                      <button type="button" className={BTN_GHOST} onClick={() => onInspect(c.key)}>
                        {L('İncele', 'Inspect')}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
};
