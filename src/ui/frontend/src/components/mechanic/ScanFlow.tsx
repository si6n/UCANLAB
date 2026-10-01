import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, ClipboardList, Eraser, Ear, FileText, Loader2, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { AuthEntitlements, DesktopBridge, MechanicScanResult, ScanStatus, VehicleProfileInfo, VehicleTypeInfo } from '../../services/bridge';
import { BTN_PRIMARY, BTN_SECONDARY, L, pick } from './text';
import { useUiHeartbeat } from './useUiHeartbeat';

/**
 * Scan → result → customer report (Aşama 6, MECHANIC_FLOW.md §3.11-3.14).
 *
 * A car only reports fault codes when asked, so it gets a one-time "Allow
 * reading" choice (plus the OS confirmation dialog); everything else listens.
 * The result card order is fixed by the spec.
 */

type Phase = 'consent' | 'scanning' | 'result' | 'report' | 'clear' | 'failed';

const Shell: React.FC<{ children: React.ReactNode; wide?: boolean }> = ({ children, wide }) => (
  <div className="flex min-h-screen items-start justify-center bg-bg-app px-4 py-10 text-text-body">
    <div className={`flex w-full ${wide ? 'max-w-2xl' : 'max-w-md'} flex-col gap-5 rounded-2xl border border-border-whisper bg-bg-card p-6 shadow-sm`}>
      {children}
    </div>
  </div>
);

const URGENCY_TONE: Record<string, string> = {
  RED: 'border-del text-del',
  YELLOW: 'border-warn text-warn',
  GREEN: 'border-ok text-ok',
  GRAY: 'border-border-strong text-text-mid',
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="flex flex-col gap-2">
    <h2 className="text-xs font-semibold uppercase tracking-wide text-text-low">{title}</h2>
    {children}
  </section>
);

function clearActionFor(vehicleType: string): Record<string, unknown> | null {
  if (vehicleType === 'car') {
    return {
      id: 'act_uds_0x14_clear_dtc',
      label: 'UDS 0x14 DTC Temizle',
      action_type: 'uds_clear_dtc',
      params: { group: 0xffffff },
      requires_confirmation: true,
    };
  }
  if (vehicleType === 'truck' || vehicleType === 'construction') {
    return {
      id: 'act_j1939_dm11_clear',
      label: 'J1939 DM11 Arıza Temizle',
      action_type: 'j1939_clear_dtc',
      params: { pgn: 65235 },
      requires_confirmation: true,
    };
  }
  return null;
}

export const ScanFlow: React.FC<{
  vehicle: VehicleProfileInfo;
  vtype: VehicleTypeInfo;
  entitlements: AuthEntitlements | null;
  onChangeVehicle: () => void;
  onExpert: (() => void) | null;
}> = ({ vehicle, vtype, entitlements, onChangeVehicle, onExpert }) => {
  const isCar = vtype.id === 'car';
  const [phase, setPhase] = useState<Phase>(isCar ? 'consent' : 'scanning');
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [result, setResult] = useState<MechanicScanResult | null>(null);
  const [reportText, setReportText] = useState('');
  const [workshop, setWorkshop] = useState('');
  const [notice, setNotice] = useState('');
  const [clearAck, setClearAck] = useState(false);
  const [reading, setReading] = useState(false);
  const timer = useRef<number | null>(null);
  const started = useRef(false);

  // Only a consented car read can transmit; keep the watchdog lease alive for it.
  useUiHeartbeat(phase === 'scanning' && reading);

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  };
  useEffect(() => clearTimer, []);

  const start = useCallback(async (allowRead: boolean) => {
    clearTimer();
    setNotice('');
    setResult(null);
    setStatus({ success: true, state: 'running', step: 'listening', progress: 0 });
    setPhase('scanning');
    const res = await DesktopBridge.scanStart(allowRead);
    if (!res.success) {
      if (res.error_code === 'CONSENT_REQUIRED') {
        setNotice(L('Okuma onaylanmadı. Sadece dinleyerek devam edebilirsiniz.', 'Reading was not confirmed. You can continue listening only.'));
        setPhase('consent');
        return;
      }
      setNotice(L('Tarama başlatılamadı. Bağlantıyı yeniden kurun.', 'Could not start the scan. Reconnect first.'));
      setPhase('failed');
      return;
    }
    setReading(Boolean(res.reading));
    const poll = async () => {
      const st = await DesktopBridge.scanStatus();
      setStatus(st);
      if (st.state === 'done') {
        if (st.step === 'done' && st.result) {
          setResult(st.result);
          setReportText(st.report_text ?? '');
          setPhase('result');
        } else if (st.step === 'cancelled') {
          setPhase(isCar ? 'consent' : 'failed');
        } else {
          setNotice(L('Tarama tamamlanamadı. Tekrar deneyin.', "The scan couldn't finish. Try again."));
          setPhase('failed');
        }
        return;
      }
      timer.current = window.setTimeout(poll, 500);
    };
    timer.current = window.setTimeout(poll, 500);
  }, [isCar]);

  useEffect(() => {
    if (!isCar && !started.current) {
      started.current = true;
      void start(false);
    }
  }, [isCar, start]);

  const clearAction = clearActionFor(vtype.id);
  const canClear = Boolean(entitlements?.dtc_clear) && clearAction !== null && (result?.technical.codes.length ?? 0) > 0;

  const runClear = async () => {
    if (!clearAction) return;
    setNotice('');
    const challenge = await DesktopBridge.requestDiagnosticChallenge(clearAction);
    if (!challenge.success || !challenge.token) {
      setNotice(L('Silme onaylanmadı.', 'Clearing was not confirmed.'));
      return;
    }
    const res = await DesktopBridge.executeDiagnosticAction(clearAction, challenge.token);
    setNotice(res.message || (res.success ? L('Arıza kodları silindi.', 'Fault codes cleared.') : L('Arıza kodları silinemedi.', 'Fault codes could not be cleared.')));
  };

  if (phase === 'consent') {
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Search className="h-5 w-5 text-accent" />
          {L('Arızaları okuyalım', "Let's read the faults")}
        </h1>
        <p className="text-sm">
          {L(
            'Arıza kodlarını okumak için araca okuma isteği gönderilecek. Bu, araçta hiçbir ayarı değiştirmez; silme veya yazma yapılmaz.',
            "To read fault codes, a read request will be sent. It doesn't change anything in the vehicle; nothing is cleared or written.",
          )}
        </p>
        {vehicle.high_voltage && (
          <p className="text-xs text-text-low">{L('Yüksek voltajlı araç: yalnız okuma yapılır.', 'High-voltage vehicle: reading only.')}</p>
        )}
        {notice && (
          <p className="text-sm text-warn" role="alert">
            {notice}
          </p>
        )}
        <button type="button" data-testid="allow-read" className={BTN_PRIMARY} onClick={() => void start(true)}>
          <ShieldCheck className="h-4 w-4" />
          {L('Okumaya izin ver', 'Allow reading')}
        </button>
        <button type="button" data-testid="listen-only" className={BTN_SECONDARY} onClick={() => void start(false)}>
          <Ear className="h-4 w-4" />
          {L('Sadece dinle', 'Listen only')}
        </button>
        <p className="text-xs text-text-low">
          {L('İzin verdiğinizde bilgisayar ayrıca bir onay penceresi açar.', 'When you allow it, the computer also shows a confirmation window.')}
        </p>
      </Shell>
    );
  }

  if (phase === 'scanning') {
    const step = status?.step;
    const pct = Math.round((status?.progress ?? 0) * 100);
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
          {step === 'reading'
            ? L('Arıza kodları okunuyor…', 'Reading fault codes…')
            : step === 'analyzing'
              ? L('Sonuç hazırlanıyor…', 'Preparing the result…')
              : L('Arızalar okunuyor… (yaklaşık 20 sn)', 'Reading faults… (about 20 s)')}
        </h1>
        <div className="h-2 w-full overflow-hidden rounded-full bg-surface-inset-raw" aria-label={`${pct}%`}>
          <div className="h-full bg-accent transition-all" style={{ width: `${step === 'listening' ? pct : 100}%` }} />
        </div>
        <p className="text-sm text-text-mid">
          {reading
            ? L('Yalnız okuma istekleri gönderiliyor; silme veya yazma yok.', 'Only read requests are sent; nothing is cleared or written.')
            : L('Araca hiçbir şey gönderilmiyor, sadece dinliyoruz.', 'Nothing is sent to the vehicle — we only listen.')}
        </p>
        <button type="button" className="bg-transparent text-sm text-accent-text hover:underline" onClick={() => void DesktopBridge.scanCancel()}>
          {L('Durdur', 'Stop')}
        </button>
      </Shell>
    );
  }

  if (phase === 'failed') {
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <AlertTriangle className="h-5 w-5 text-warn" />
          {L('Tarama yapılamadı', "Couldn't scan")}
        </h1>
        {notice && <p className="text-sm">{notice}</p>}
        <button type="button" className={BTN_PRIMARY} onClick={() => (isCar ? setPhase('consent') : void start(false))}>
          <RefreshCw className="h-4 w-4" />
          {L('Tekrar dene', 'Try again')}
        </button>
        <button type="button" className={BTN_SECONDARY} onClick={onChangeVehicle}>
          {L('Aracı değiştir', 'Change vehicle')}
        </button>
      </Shell>
    );
  }

  if (phase === 'report' && result) {
    return (
      <Shell wide>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <FileText className="h-5 w-5 text-accent" />
          {L('Müşteri raporu', 'Customer report')}
        </h1>
        <label className="flex flex-col gap-1 text-sm">
          {L('Atölye adı (isteğe bağlı)', 'Workshop name (optional)')}
          <input
            className="rounded-lg border border-border-strong bg-bg-app px-3 py-2 text-text-hi"
            maxLength={80}
            value={workshop}
            onChange={(e) => setWorkshop(e.target.value)}
          />
        </label>
        <pre className="whitespace-pre-wrap rounded-xl bg-surface-inset-raw p-4 text-sm text-text-hi" data-testid="report-text">
          {(workshop ? reportText.replace('ARAÇ KONTROL RAPORU\n', `ARAÇ KONTROL RAPORU\n${workshop}\n`) : reportText)}
        </pre>
        {notice && <p className="text-sm text-text-mid">{notice}</p>}
        <button
          type="button"
          className={BTN_PRIMARY}
          onClick={async () => {
            const saved = await DesktopBridge.scanSaveReport(workshop);
            if (!saved.success) {
              setNotice(L('Rapor kaydedilemedi.', 'Could not save the report.'));
              return;
            }
            const opened = await DesktopBridge.scanOpenReport();
            setNotice(
              opened.success
                ? L('Rapor tarayıcıda açıldı: "Yazdır / PDF olarak kaydet" ile yazdırın.', 'Report opened in the browser: use "Print / Save as PDF".')
                : L(`Kaydedildi: ${saved.path}`, `Saved: ${saved.path}`),
            );
          }}
        >
          {L('Raporu kaydet ve aç (yazdır / PDF)', 'Save and open report (print / PDF)')}
        </button>
        <button type="button" className={BTN_SECONDARY} onClick={() => setPhase('result')}>
          {L('Sonuca dön', 'Back to result')}
        </button>
      </Shell>
    );
  }

  if (phase === 'clear' && result) {
    return (
      <Shell>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-text-hi">
          <Eraser className="h-5 w-5 text-warn" />
          {L('Arıza kodlarını sil', 'Clear fault codes')}
        </h1>
        <p className="text-sm">
          {L(
            'Arıza kodları silinecek. Arıza giderilmediyse kodlar geri gelir ve uyarı lambası yeniden yanar. Motor çalışmıyor olmalı.',
            "Fault codes will be cleared. If the fault isn't fixed they'll come back. Engine must be off.",
          )}
        </p>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={clearAck} onChange={(e) => setClearAck(e.target.checked)} className="mt-1" />
          {L('Arızayı giderdim ve motor çalışmıyor.', 'I fixed the fault and the engine is off.')}
        </label>
        {notice && <p className="text-sm text-text-mid" role="status">{notice}</p>}
        <button type="button" className={BTN_PRIMARY} disabled={!clearAck} onClick={() => void runClear()}>
          {L('Sil', 'Clear')}
        </button>
        <button type="button" className={BTN_SECONDARY} onClick={() => setPhase('result')}>
          {L('Vazgeç', 'Cancel')}
        </button>
      </Shell>
    );
  }

  if (phase === 'result' && result) {
    return (
      <Shell wide>
        {/* 1. Safety first */}
        {result.safety_tr.map((line) => (
          <div key={line} className="flex items-start gap-2 rounded-lg border border-del bg-bg-card p-3 text-sm font-semibold text-del" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
            <span>{line}</span>
          </div>
        ))}
        <div className="flex items-center justify-between gap-2 text-sm">
          <span>
            <span className="text-text-low">{L('Araç: ', 'Vehicle: ')}</span>
            <span className="font-semibold text-text-hi">{pick(vehicle, 'label')}</span>
          </span>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${URGENCY_TONE[result.risk_level]}`} data-testid="urgency">
            {L('Aciliyet: ', 'Urgency: ')}
            {L(result.urgency_tr, result.urgency_en)}
          </span>
        </div>
        {/* 2. Summary */}
        <div>
          <h1 className="text-xl font-semibold text-text-hi" data-testid="result-headline">{result.headline_tr}</h1>
          <p className="mt-1 text-sm">{result.summary_tr}</p>
          {result.advice_tr && <p className="mt-2 whitespace-pre-line text-sm text-text-mid">{result.advice_tr}</p>}
        </div>
        {/* 4. Causes */}
        {result.causes.length > 0 && (
          <Section title={L('Olası nedenler', 'Likely causes')}>
            <ol className="flex flex-col gap-2">
              {result.causes.map((c, i) => (
                <li key={c.text_tr} className="rounded-lg border border-border-whisper p-3 text-sm">
                  <span className="font-semibold text-text-hi">
                    {i + 1}. {c.text_tr}
                  </span>
                  <span className="mt-1 block text-xs text-text-mid">
                    {L('Neden böyle düşünüyoruz: ', 'Why we think so: ')}
                    {c.why_tr}
                  </span>
                  <span className="block text-xs text-text-low">{c.source_tr}</span>
                </li>
              ))}
            </ol>
          </Section>
        )}
        {/* 5. What to do */}
        {result.steps.length > 0 && (
          <Section title={L('Ne yapmalı (basitten zora)', 'What to do (simple first)')}>
            <ol className="flex flex-col gap-2 text-sm">
              {result.steps.map((s) => (
                <li key={s.n} className="flex gap-2">
                  <span className="font-semibold text-accent-text">{s.n}.</span>
                  <span>
                    {s.action_tr}
                    {s.difficulty_tr && <span className="ml-1 text-xs text-text-low">· {s.difficulty_tr}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </Section>
        )}
        {/* 6. Missing data */}
        {result.missing_tr.length > 0 && (
          <Section title={L('Eksik veri ve nasıl alınır', 'Missing data and how to get it')}>
            <ul className="flex flex-col gap-1 text-sm text-text-mid">
              {result.missing_tr.map((m) => (
                <li key={m}>• {m}</li>
              ))}
            </ul>
          </Section>
        )}
        {/* 7. Technical details (collapsed) */}
        <details className="rounded-lg border border-border-whisper p-3 text-sm">
          <summary className="flex cursor-pointer items-center gap-1 font-semibold text-text-hi">
            <ChevronDown className="h-4 w-4" />
            {L('Teknik detay', 'Technical details')}
          </summary>
          <ul className="mt-2 flex flex-col gap-1 font-mono text-xs">
            {result.technical.codes.map((c) => (
              <li key={`${c.code}-${c.kind}`}>
                {c.code} · {c.kind}
                {c.ecu_tr ? ` · ${c.ecu_tr}` : ''}
                {c.severity ? ` · ${c.severity}` : ''} {c.title_tr ? `— ${c.title_tr}` : ''}
              </li>
            ))}
            {result.technical.subsystems.length > 0 && <li>{result.technical.subsystems.join('; ')}</li>}
            {result.technical.confidence_tr && <li>{result.technical.confidence_tr}</li>}
            {result.technical.source_notes.map((n) => (
              <li key={n} className="text-text-low">
                {n}
              </li>
            ))}
          </ul>
        </details>
        {result.glossary.length > 0 && (
          <details className="rounded-lg border border-border-whisper p-3 text-sm">
            <summary className="flex cursor-pointer items-center gap-1 font-semibold text-text-hi">
              <ChevronDown className="h-4 w-4" />
              {L('Terimler', 'Terms')}
            </summary>
            <dl className="mt-2 flex flex-col gap-1 text-xs">
              {result.glossary.map((g) => (
                <div key={g.term}>
                  <dt className="inline font-semibold text-text-hi">{g.term}: </dt>
                  <dd className="inline text-text-mid">{g.meaning_tr}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
        {/* 8. Sources */}
        <p className="text-xs text-text-low">
          {L('Kaynak: ', 'Source: ')}
          {result.sources_tr.join(' · ')}
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <button type="button" data-testid="customer-report" className={BTN_PRIMARY} onClick={() => setPhase('report')}>
            <ClipboardList className="h-4 w-4" />
            {L('Müşteri raporu', 'Customer report')}
          </button>
          <button type="button" className={BTN_SECONDARY} onClick={() => (isCar ? setPhase('consent') : void start(false))}>
            <RefreshCw className="h-4 w-4" />
            {L('Yeni tarama', 'New scan')}
          </button>
          {canClear && (
            <button type="button" className={BTN_SECONDARY} onClick={() => { setClearAck(false); setNotice(''); setPhase('clear'); }}>
              <Eraser className="h-4 w-4" />
              {L('Arıza kodlarını sil…', 'Clear fault codes…')}
            </button>
          )}
          {onExpert && (
            <button type="button" className={BTN_SECONDARY} onClick={onExpert}>
              {L('Uzman ekranı', 'Expert screen')}
            </button>
          )}
        </div>
        <button type="button" className="bg-transparent text-sm text-accent-text hover:underline" onClick={onChangeVehicle}>
          {L('Başka araç', 'Another vehicle')}
        </button>
      </Shell>
    );
  }

  return null;
};
