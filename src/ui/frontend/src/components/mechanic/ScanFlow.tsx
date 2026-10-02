import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, ChevronDown, ClipboardList, Cpu, Eraser, Ear, FileText, Loader2, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { AuthEntitlements, DesktopBridge, MechanicScanResult, ScanStatus, VehicleProfileInfo, VehicleTypeInfo } from '../../services/bridge';
import { cx } from '../workbench/ui';
import { CARD, Heading, Notice, Screen } from './Screen';
import { BAR_DANGER, BAR_PRIMARY, BAR_QUIET, BAR_SECONDARY, L, pick } from './text';
import { useUiHeartbeat } from './useUiHeartbeat';

/**
 * Scan → result → customer report (Aşama 6, MECHANIC_FLOW.md §3.11-3.14).
 *
 * A car only reports fault codes when asked, so it gets a one-time "Allow
 * reading" choice (plus the OS confirmation dialog); everything else listens.
 * The result card order is fixed by the spec.
 */

type Phase = 'consent' | 'scanning' | 'result' | 'report' | 'clear' | 'failed';

const URGENCY_BAND: Record<string, string> = {
  RED: 'border-danger-border bg-danger-soft text-del',
  YELLOW: 'border-warn-border bg-warn-soft text-warn',
  GREEN: 'border-ok-border bg-ok-soft text-ok',
  GRAY: 'border-border-whisper bg-bg-row-hover text-text-mid',
};

const SectionCard: React.FC<{ title: string; hint?: string; children: React.ReactNode }> = ({ title, hint, children }) => (
  <section className={cx(CARD, 'flex min-w-0 flex-col overflow-hidden')}>
    <div className="border-b border-border-whisper px-5 py-3">
      <h2 className="text-[15px] font-semibold text-text-hi">{title}</h2>
      {hint && <p className="mt-0.5 text-xs text-text-mid">{hint}</p>}
    </div>
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

  const eyebrow = L('Adım 3 / 4 · Tarama', 'Step 3 of 4 · Scan');
  const changeVehicleButton = (
    <button type="button" className={BAR_QUIET} onClick={onChangeVehicle}>
      <ArrowLeft className="h-4 w-4" />
      {L('Aracı değiştir', 'Change vehicle')}
    </button>
  );

  if (phase === 'consent') {
    return (
      <Screen
        step={3}
        back={changeVehicleButton}
        actions={
          <>
            <button type="button" data-testid="listen-only" className={BAR_SECONDARY} onClick={() => void start(false)}>
              <Ear className="h-4 w-4" />
              {L('Sadece dinle', 'Listen only')}
            </button>
            <button type="button" data-testid="allow-read" className={BAR_PRIMARY} onClick={() => void start(true)}>
              <ShieldCheck className="h-4 w-4" />
              {L('Okumaya izin ver', 'Allow reading')}
            </button>
          </>
        }
      >
        <Heading
          eyebrow={eyebrow}
          icon={<Search className="h-5 w-5 text-accent" />}
          title={L('Arızaları okuyalım', "Let's read the faults")}
          lead={L(
            'Arıza kodlarını okumak için araca okuma isteği gönderilecek. Bu, araçta hiçbir ayarı değiştirmez; silme veya yazma yapılmaz.',
            "To read fault codes, a read request will be sent. It doesn't change anything in the vehicle; nothing is cleared or written.",
          )}
        />
        <div className={cx(CARD, 'grid grid-cols-1 gap-4 p-5 text-sm md:grid-cols-2')}>
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 flex-none text-accent" />
            <p>
              <b className="text-text-hi">{L('Okumaya izin ver', 'Allow reading')}</b>
              <br />
              {L('Yalnız okuma istekleri gönderilir. İzin verdiğinizde bilgisayar ayrıca bir onay penceresi açar.', 'Only read requests are sent. When you allow it, the computer also shows a confirmation window.')}
            </p>
          </div>
          <div className="flex items-start gap-3">
            <Ear className="mt-0.5 h-5 w-5 flex-none text-text-mid" />
            <p>
              <b className="text-text-hi">{L('Sadece dinle', 'Listen only')}</b>
              <br />
              {L(
                'Araca hiçbir şey gönderilmez. Otomobil arıza kodlarını çoğu zaman yalnız sorulunca bildirdiği için kod görünmeyebilir.',
                'Nothing is sent to the vehicle. A car usually reports fault codes only when asked, so none may show up.',
              )}
            </p>
          </div>
        </div>
        {vehicle.high_voltage && <p className="text-xs text-text-mid">{L('Yüksek voltajlı araç: yalnız okuma yapılır.', 'High-voltage vehicle: reading only.')}</p>}
        {notice && <Notice tone="warn">{notice}</Notice>}
      </Screen>
    );
  }

  if (phase === 'scanning') {
    const step = status?.step;
    const pct = Math.round((status?.progress ?? 0) * 100);
    return (
      <Screen
        step={3}
        actions={
          <button type="button" className={BAR_SECONDARY} onClick={() => void DesktopBridge.scanCancel()}>
            {L('Durdur', 'Stop')}
          </button>
        }
      >
        <Heading
          eyebrow={eyebrow}
          icon={<Loader2 className="h-5 w-5 animate-spin text-accent" />}
          title={
            step === 'reading'
              ? L('Arıza kodları okunuyor…', 'Reading fault codes…')
              : step === 'analyzing'
                ? L('Sonuç hazırlanıyor…', 'Preparing the result…')
                : L('Arızalar okunuyor… (yaklaşık 20 sn)', 'Reading faults… (about 20 s)')
          }
        />
        <div className={cx(CARD, 'flex flex-col gap-3 p-5')}>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-surface-inset-raw"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={step === 'listening' ? pct : 100}
            aria-label={L('Tarama ilerlemesi', 'Scan progress')}
          >
            <div className="h-full bg-accent transition-all" style={{ width: `${step === 'listening' ? pct : 100}%` }} />
          </div>
          <p className="text-sm text-text-mid">
            {reading
              ? L('Yalnız okuma istekleri gönderiliyor; silme veya yazma yok.', 'Only read requests are sent; nothing is cleared or written.')
              : L('Araca hiçbir şey gönderilmiyor, sadece dinliyoruz.', 'Nothing is sent to the vehicle — we only listen.')}
          </p>
        </div>
      </Screen>
    );
  }

  if (phase === 'failed') {
    return (
      <Screen
        step={3}
        back={changeVehicleButton}
        actions={
          <button type="button" className={BAR_PRIMARY} onClick={() => (isCar ? setPhase('consent') : void start(false))}>
            <RefreshCw className="h-4 w-4" />
            {L('Tekrar dene', 'Try again')}
          </button>
        }
      >
        <Heading eyebrow={eyebrow} icon={<AlertTriangle className="h-5 w-5 text-warn" />} title={L('Tarama yapılamadı', "Couldn't scan")} lead={notice || undefined} />
      </Screen>
    );
  }

  if (phase === 'report' && result) {
    return (
      <Screen
        step={4}
        back={
          <button type="button" className={BAR_QUIET} onClick={() => setPhase('result')}>
            <ArrowLeft className="h-4 w-4" />
            {L('Sonuca dön', 'Back to result')}
          </button>
        }
        actions={
          <button
            type="button"
            className={BAR_PRIMARY}
            data-testid="report-save"
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
            <FileText className="h-4 w-4" />
            {L('Raporu kaydet ve aç (yazdır / PDF)', 'Save and open report (print / PDF)')}
          </button>
        }
      >
        <Heading
          eyebrow={L('Müşteri raporu', 'Customer report')}
          title={L('Raporu kontrol edin', 'Check the report')}
          lead={L(
            'Sade dilde, tek sayfa. Kaydedince tarayıcıda açılır; oradan yazdırın veya PDF alın.',
            'Plain language, one page. Saving opens it in the browser; print it or save a PDF from there.',
          )}
        />
        <label className="flex flex-col gap-1 text-sm">
          {L('Atölye adı (isteğe bağlı)', 'Workshop name (optional)')}
          <input
            className="rounded-lg border border-border-strong bg-bg-card px-3 py-2 text-text-hi outline-none focus:border-accent"
            maxLength={80}
            value={workshop}
            onChange={(e) => setWorkshop(e.target.value)}
          />
        </label>
        <pre className={cx(CARD, 'whitespace-pre-wrap p-6 text-sm text-text-hi')} data-testid="report-text">
          {workshop ? reportText.replace('ARAÇ KONTROL RAPORU\n', `ARAÇ KONTROL RAPORU\n${workshop}\n`) : reportText}
        </pre>
        {notice && (
          <p className="text-sm text-text-mid" role="status">
            {notice}
          </p>
        )}
      </Screen>
    );
  }

  if (phase === 'clear' && result) {
    return (
      <Screen
        step={4}
        back={
          <button type="button" className={BAR_QUIET} onClick={() => setPhase('result')}>
            <ArrowLeft className="h-4 w-4" />
            {L('Vazgeç', 'Cancel')}
          </button>
        }
        actions={
          <button type="button" className={BAR_DANGER} disabled={!clearAck} onClick={() => void runClear()} data-testid="clear-confirm">
            <Eraser className="h-4 w-4" />
            {L('Sil', 'Clear')}
          </button>
        }
      >
        <Heading
          icon={<Eraser className="h-5 w-5 text-warn" />}
          title={L('Arıza kodlarını sil', 'Clear fault codes')}
          lead={L(
            'Arıza kodları silinecek. Arıza giderilmediyse kodlar geri gelir ve uyarı lambası yeniden yanar. Motor çalışmıyor olmalı.',
            "Fault codes will be cleared. If the fault isn't fixed they'll come back. Engine must be off.",
          )}
        />
        <label className={cx(CARD, 'flex items-start gap-3 p-4 text-sm')}>
          <input type="checkbox" checked={clearAck} onChange={(e) => setClearAck(e.target.checked)} className="mt-0.5 h-4 w-4" />
          {L('Arızayı giderdim ve motor çalışmıyor.', 'I fixed the fault and the engine is off.')}
        </label>
        {notice && (
          <p className="text-sm text-text-mid" role="status">
            {notice}
          </p>
        )}
      </Screen>
    );
  }

  if (phase === 'result' && result) {
    return (
      <Screen
        step={4}
        width="wide"
        back={
          <>
            <button type="button" className={BAR_SECONDARY} onClick={() => (isCar ? setPhase('consent') : void start(false))}>
              <RefreshCw className="h-4 w-4" />
              {L('Yeni tarama', 'New scan')}
            </button>
            <button type="button" className={BAR_QUIET} onClick={onChangeVehicle}>
              {L('Başka araç', 'Another vehicle')}
            </button>
          </>
        }
        actions={
          <>
            {onExpert && (
              <button type="button" className={BAR_QUIET} onClick={onExpert}>
                <Cpu className="h-4 w-4" />
                {L('Uzman ekranı', 'Expert screen')}
              </button>
            )}
            {canClear && (
              <button
                type="button"
                className={BAR_DANGER}
                onClick={() => {
                  setClearAck(false);
                  setNotice('');
                  setPhase('clear');
                }}
              >
                <Eraser className="h-4 w-4" />
                {L('Arıza kodlarını sil…', 'Clear fault codes…')}
              </button>
            )}
            <button type="button" data-testid="customer-report" className={BAR_PRIMARY} onClick={() => setPhase('report')}>
              <ClipboardList className="h-4 w-4" />
              {L('Müşteri raporu', 'Customer report')}
            </button>
          </>
        }
      >
        {/* 1. Safety first */}
        {result.safety_tr.map((line) => (
          <div key={line} className="flex items-start gap-2 rounded-xl border border-del bg-danger-soft p-3 text-sm font-semibold text-del" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
            <span>{line}</span>
          </div>
        ))}
        {/* 2-3. Summary and urgency */}
        <section className={cx(CARD, 'overflow-hidden')}>
          <div className={cx('flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3', URGENCY_BAND[result.risk_level] ?? URGENCY_BAND.GRAY)}>
            <div className="text-sm" data-testid="urgency">
              {L('Aciliyet: ', 'Urgency: ')}
              <b className="font-semibold">{L(result.urgency_tr, result.urgency_en)}</b>
            </div>
            <div className="text-xs text-text-body">
              {L('Araç: ', 'Vehicle: ')}
              <b className="font-semibold text-text-hi">{pick(vehicle, 'label')}</b>
            </div>
          </div>
          <div className="flex flex-col gap-2 p-5">
            <h1 className="text-xl font-semibold text-text-hi" data-testid="result-headline">
              {result.headline_tr}
            </h1>
            <p className="text-sm text-text-body">{result.summary_tr}</p>
            {result.advice_tr && <p className="whitespace-pre-line text-sm text-text-mid">{result.advice_tr}</p>}
          </div>
        </section>
        {/* 4-5. Causes, then what to do */}
        {(result.causes.length > 0 || result.steps.length > 0) && (
          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
            {result.causes.length > 0 && (
              <SectionCard title={L('Olası nedenler', 'Likely causes')} hint={L('Kanıta göre sıralı; her birinin dayanağı altında yazar.', 'Ranked by evidence; each says what it rests on.')}>
                <ol className="flex flex-col divide-y divide-border-whisper">
                  {result.causes.map((c, i) => (
                    <li key={c.text_tr} className="px-5 py-3 text-sm">
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
              </SectionCard>
            )}
            {result.steps.length > 0 && (
              <SectionCard title={L('Ne yapmalı', 'What to do')} hint={L('Basitten zora.', 'Simple first.')}>
                <ol className="flex flex-col divide-y divide-border-whisper text-sm">
                  {result.steps.map((s) => (
                    <li key={s.n} className="flex gap-3 px-5 py-3">
                      <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full border border-border-strong text-xs font-semibold text-text-hi">
                        {s.n}
                      </span>
                      <span className="min-w-0">
                        {s.action_tr}
                        {s.difficulty_tr && <span className="mt-0.5 block text-xs text-text-mid">{s.difficulty_tr}</span>}
                      </span>
                    </li>
                  ))}
                </ol>
              </SectionCard>
            )}
          </div>
        )}
        {/* 6. Missing data */}
        {result.missing_tr.length > 0 && (
          <SectionCard title={L('Eksik veri ve nasıl alınır', 'Missing data and how to get it')}>
            <ul className="flex flex-col gap-1 px-5 py-3 text-sm text-text-body">
              {result.missing_tr.map((m) => (
                <li key={m}>• {m}</li>
              ))}
            </ul>
          </SectionCard>
        )}
        {/* 7. Technical details (collapsed) */}
        <details className={cx(CARD, 'px-5 py-3 text-sm')}>
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
          <details className={cx(CARD, 'px-5 py-3 text-sm')}>
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
      </Screen>
    );
  }

  return null;
};
