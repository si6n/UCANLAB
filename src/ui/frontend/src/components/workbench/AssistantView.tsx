import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, Loader2, RefreshCw, Stethoscope } from 'lucide-react';
import { CopilotStructuredAnswer, DesktopBridge, UserDiagnosticCard } from '../../services/bridge';
import { CopilotAskCard } from './CopilotAnswer';
import { L } from '../mechanic/text';
import { BTN_GHOST, BTN_PRIMARY, Card, CardHeader, Chip, EmptyState, Tone, cx } from './ui';

/**
 * Teşhis asistanı: the Python analysis pipeline (evidence gate → anomalies →
 * ranked hypotheses → decision card) and its question-and-answer triage.
 * Nothing here computes a diagnosis in TypeScript, and nothing here writes
 * to the vehicle: proposed actions (clear codes, active tests) are listed as
 * text; transmitting stays with the dedicated, confirmation-gated screens.
 */

interface Gate {
  anomaly_sufficient: boolean;
  dtc_sufficient: boolean;
  gaps: string[];
  signal_inventory: Record<string, number>;
  active_dtc_count: number;
}

interface Hypothesis {
  id: string;
  fault: string;
  score: number;
  confidence_interval: [number, number];
  supporting_evidence: string[];
  contradicting_evidence: string[];
  discriminating_tests: string[];
}

interface Analysis {
  success: boolean;
  error?: string;
  simulated?: boolean;
  user_card?: UserDiagnosticCard;
  gate?: Gate;
  hypotheses?: Hypothesis[];
  anomalies?: Array<{ signal: string; finding: string; ratio: number }>;
  structured_answer?: CopilotStructuredAnswer;
}

interface Question {
  id: string;
  kind: 'yes_no' | 'choice' | 'measurement';
  text: string;
  why?: string;
  unit?: string | null;
  expected_range?: [number, number] | null;
  choices?: string[];
  how_to_measure?: string;
}

interface Dialogue {
  success: boolean;
  dialogue_state?: string;
  current_question?: Question | null;
  next_question?: Question | null;
  answered_count?: number;
  proposed_actions?: Array<ProposedAction | string>;
  concluded_fault?: string | null;
  concluded_confidence?: number;
  next_guidance?: string | null;
}

interface ProposedAction {
  type?: string;
  title?: string;
  target?: string;
  requires_operator_confirm?: boolean;
  is_mutating?: boolean;
}

const RISK: Record<string, { tone: Tone; label: () => string }> = {
  RED: { tone: 'danger', label: () => L('Hemen durun', 'Stop now') },
  YELLOW: { tone: 'warn', label: () => L('Dikkat', 'Caution') },
  GREEN: { tone: 'ok', label: () => L('Acil sorun görünmüyor', 'No urgent issue') },
  GRAY: { tone: 'neutral', label: () => L('Belirsiz', 'Unclear') },
};

const QuestionCard: React.FC<{ q: Question; busy: boolean; onAnswer: (value: unknown, unknown: boolean) => void }> = ({ q, busy, onAnswer }) => {
  const [value, setValue] = useState('');
  const num = Number(value.replace(',', '.'));
  const valid = value.trim() !== '' && Number.isFinite(num);
  return (
    <div className="flex flex-col gap-3" data-testid="assistant-question">
      <div className="text-[15px] font-semibold text-text-hi">{q.text}</div>
      {q.why && <p className="text-[13px] text-text-mid">{q.why}</p>}
      {q.how_to_measure && (
        <p className="rounded-xl border border-border-whisper px-3 py-2 text-[12.5px] text-text-body">
          <b>{L('Nasıl ölçülür: ', 'How to measure: ')}</b>
          {q.how_to_measure}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {q.kind === 'yes_no' && (
          <>
            <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={() => onAnswer(true, false)} data-testid="answer-yes">
              {L('Evet', 'Yes')}
            </button>
            <button type="button" className={BTN_GHOST} disabled={busy} onClick={() => onAnswer(false, false)} data-testid="answer-no">
              {L('Hayır', 'No')}
            </button>
          </>
        )}
        {q.kind === 'choice' &&
          (q.choices ?? []).map((c) => (
            <button key={c} type="button" className={BTN_GHOST} disabled={busy} onClick={() => onAnswer(c, false)}>
              {c}
            </button>
          ))}
        {q.kind === 'measurement' && (
          <>
            <label className="flex items-center gap-2 rounded-lg border border-border-strong px-3 py-2 focus-within:border-accent">
              <input
                data-testid="answer-value"
                inputMode="decimal"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="w-24 bg-transparent text-[14px] text-text-hi outline-none"
                placeholder={q.expected_range ? `${q.expected_range[0]}–${q.expected_range[1]}` : ''}
              />
              {q.unit && <span className="text-[13px] text-text-mid">{q.unit}</span>}
            </label>
            <button type="button" className={BTN_PRIMARY} disabled={busy || !valid} onClick={() => onAnswer(num, false)} data-testid="answer-submit">
              {L('Kaydet', 'Save')}
            </button>
          </>
        )}
        <button type="button" className={BTN_GHOST} disabled={busy} onClick={() => onAnswer(null, true)} data-testid="answer-unknown">
          {L('Bilmiyorum / ölçemiyorum', "Don't know / can't measure")}
        </button>
      </div>
    </div>
  );
};

export const AssistantView: React.FC = () => {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [dialogue, setDialogue] = useState<Dialogue | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    const [a, d] = await Promise.all([DesktopBridge.getDiagnosticAnalysis(), DesktopBridge.getDialogueState()]);
    setAnalysis(a as Analysis | null);
    setDialogue(d as Dialogue);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const answer = async (q: Question, value: unknown, unknown: boolean) => {
    setBusy(true);
    try {
      const res = (await DesktopBridge.recordOperatorAnswer(q.id, value, q.kind, q.unit ?? null, unknown)) as Dialogue;
      setDialogue((prev) => ({ ...(prev ?? { success: true }), ...res, current_question: res.next_question ?? null }));
      const a = await DesktopBridge.getDiagnosticAnalysis();
      setAnalysis(a as Analysis | null);
    } finally {
      setBusy(false);
    }
  };

  const sendFeedback = async (dtc: string, resolved: boolean) => {
    await DesktopBridge.recordTechnicianFeedback(dtc, resolved, '');
    setFeedback((f) => ({ ...f, [dtc]: resolved }));
  };

  if (!analysis) {
    return (
      <Card>
        <p className="flex items-center gap-2 p-6 text-[13px] text-text-mid">
          <Loader2 className="h-4 w-4 animate-spin" />
          {L('Analiz ediliyor…', 'Analysing…')}
        </p>
      </Card>
    );
  }
  if (!analysis.success || !analysis.user_card) {
    return (
      <Card className="h-full">
        <EmptyState
          testId="assistant-empty"
          icon={Stethoscope}
          title={L('Teşhis oturumu yok', 'No diagnostic session')}
          body={L('Hat dinlenmeye başlayınca kanıtlar burada toplanır.', 'Evidence collects here once the bus is being listened to.')}
        />
      </Card>
    );
  }

  const card = analysis.user_card;
  const risk = RISK[card.risk_level] ?? RISK.GRAY;
  const gate = analysis.gate;
  const question = dialogue?.current_question ?? null;

  const hypotheses = analysis.hypotheses ?? [];
  const alternatives = card.technical.alternatives_tr ?? [];
  const actions = dialogue?.proposed_actions ?? [];
  const showFeedback = !analysis.simulated && card.technical.dtcs.length > 0;

  // At most three panels: the assessment (with its causes and the data it
  // rests on), the questions (with suggested actions and repair feedback)
  // and the copilot's six-section answer.
  return (
    <div className="grid h-full min-h-0 grid-cols-1 content-start gap-3 overflow-auto xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]" data-testid="assistant-view">
      <div className="flex min-w-0 flex-col gap-3">
        <Card testId="assistant-card" className="overflow-hidden">
          {analysis.simulated && (
            <div role="status" data-testid="assistant-simulated" className="border-b border-warn-border bg-warn-soft px-5 py-2.5 text-[13px] text-warn">
              <b>{L('Simülasyon sonucu.', 'Simulation result.')}</b>{' '}
              {L(
                'Bu değerlendirme simüle araçtan gelir; gerçek bir aracın teşhisi değildir ve teknisyen raporuna girmez.',
                'This assessment comes from the simulated vehicle; it is not a diagnosis of a real vehicle and never enters the technician report.',
              )}
            </div>
          )}
          <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
            <Chip tone={risk.tone} testId="assistant-risk">
              {risk.label()}
            </Chip>
            <button type="button" className={BTN_GHOST} onClick={() => void load()} title={L('Yeniden değerlendir', 'Re-evaluate')}>
              <RefreshCw className="h-4 w-4" />
              {L('Yenile', 'Refresh')}
            </button>
          </div>
          <div className="flex flex-col gap-3 px-5 pb-5 pt-3">
            <h2 className="text-[20px] font-semibold text-text-hi" data-testid="assistant-headline">
              {card.headline_tr}
            </h2>
            <p className="text-[14px] text-text-body">{card.summary_tr}</p>
            {card.risk_advice_tr && (
              <ul className="flex flex-col gap-1 text-[13.5px] text-text-body">
                {card.risk_advice_tr.split('\n').map((line) => (
                  <li key={line}>• {line}</li>
                ))}
              </ul>
            )}
            {card.evidence_tr.length > 0 && (
              <div>
                <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Neye dayanıyor', 'Based on')}</div>
                <ul className="mt-1 flex flex-col gap-0.5 text-[13px] text-text-mid">
                  {card.evidence_tr.map((e) => (
                    <li key={e}>• {e}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              {card.technical.dtcs.map((d) => (
                <Chip key={d}>{d}</Chip>
              ))}
              {card.technical.subsystem && <Chip>{card.technical.subsystem}</Chip>}
              {card.technical.confidence_label && <Chip tone="accent">{card.technical.confidence_label}</Chip>}
            </div>
          </div>
          {(hypotheses.length > 0 || alternatives.length > 0) && (
            <div className="flex flex-col gap-2 border-t border-border-whisper px-5 py-4" data-testid="assistant-causes">
              {hypotheses.length > 0 && (
                <>
                  <div>
                    <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Olası nedenler', 'Possible causes')}</div>
                    <p className="mt-0.5 text-[12.5px] text-text-mid">
                      {L('Kanıta göre sıralı; puanlar kalibre edilmiş tahmindir, olasılık değildir.', 'Ranked by evidence; scores are calibrated estimates, not probabilities.')}
                    </p>
                  </div>
                  <ul className="flex flex-col gap-2">
                    {hypotheses.map((h) => (
                      <li key={h.id} className="rounded-xl border border-border-whisper px-4 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-[13.5px] font-semibold text-text-hi">{h.fault}</span>
                          <span className="text-[12px] tabular-nums text-text-mid">%{Math.round(h.score * 100)}</span>
                        </div>
                        {h.supporting_evidence.length > 0 && <p className="mt-1 text-[12.5px] text-text-mid">+ {h.supporting_evidence.join(' · ')}</p>}
                        {h.contradicting_evidence.length > 0 && <p className="text-[12.5px] text-text-mid">− {h.contradicting_evidence.join(' · ')}</p>}
                        {h.discriminating_tests.length > 0 && (
                          <p className="mt-1 text-[12.5px] text-text-body">
                            <b>{L('Ayırt etmek için: ', 'To tell apart: ')}</b>
                            {h.discriminating_tests.join(' · ')}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {alternatives.length > 0 && (
                <div className={hypotheses.length > 0 ? 'pt-1' : undefined}>
                  <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Diğer adaylar ve teknik not', 'Other candidates and technical note')}</div>
                  <ul className="mt-1 flex flex-col gap-1 text-[12.5px] text-text-mid">
                    {alternatives.map((a) => (
                      <li key={a}>• {a}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          {gate && (
            <div className="flex flex-col gap-2 border-t border-border-whisper px-5 py-4 text-[13px]" data-testid="assistant-data">
              <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Veri durumu', 'Data status')}</div>
              <div className="flex flex-wrap gap-1.5">
                <Chip tone={gate.dtc_sufficient ? 'ok' : 'neutral'}>{gate.dtc_sufficient ? L('Kod analizi için yeterli', 'Enough for code analysis') : L('Kod analizi için yetersiz', 'Not enough for code analysis')}</Chip>
                <Chip tone={gate.anomaly_sufficient ? 'ok' : 'neutral'}>{gate.anomaly_sufficient ? L('Sinyal analizi için yeterli', 'Enough for signal analysis') : L('Sinyal analizi için yetersiz', 'Not enough for signal analysis')}</Chip>
              </div>
              {gate.gaps.length > 0 && (
                <ul className="text-text-mid" data-testid="assistant-gaps">
                  {gate.gaps.map((g) => (
                    <li key={g}>• {L('Eksik', 'Missing')}: {g}</li>
                  ))}
                </ul>
              )}
              <div className="text-text-mid">
                {L('Aktif kod', 'Active codes')}: <b className="text-text-hi">{gate.active_dtc_count}</b> ·{' '}
                {Object.entries(gate.signal_inventory)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(' · ') || L('ölçülen sinyal yok', 'no measured signal')}
              </div>
            </div>
          )}
        </Card>

      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <Card testId="assistant-dialogue">
          <CardHeader
            title={L('Soru-cevap', 'Questions')}
            hint={L('Cevaplarınız kanıta eklenir ve nedenleri eler. Bilmediğinizi söylemek de bir cevaptır.', 'Your answers become evidence and rule causes out. Saying you don’t know is an answer too.')}
          >
            {dialogue?.answered_count ? <Chip>{dialogue.answered_count} {L('cevap', 'answers')}</Chip> : null}
          </CardHeader>
          <div className="p-5">
            {dialogue?.concluded_fault ? (
              <div className="flex flex-col gap-2" data-testid="assistant-concluded">
                <p className="text-[14px] text-text-hi">
                  <b>{L('En olası: ', 'Most likely: ')}</b>
                  {dialogue.concluded_fault}
                  {typeof dialogue.concluded_confidence === 'number' && dialogue.concluded_confidence > 0
                    ? ` · ${L('güven', 'confidence')} %${Math.round(dialogue.concluded_confidence * 100)}`
                    : ''}
                </p>
                {gate && !gate.dtc_sufficient && (
                  <Chip tone="warn" testId="assistant-preliminary">
                    {L('Ön değerlendirme: veri henüz yetersiz, kesin değil', 'Preliminary: not enough data yet, not certain')}
                  </Chip>
                )}
              </div>
            ) : question ? (
              <QuestionCard key={question.id} q={question} busy={busy} onAnswer={(v, u) => void answer(question, v, u)} />
            ) : (
              <p className="text-[13px] text-text-mid">{L('Şu an sorulacak bir soru yok.', 'No question to ask right now.')}</p>
            )}
            {dialogue?.next_guidance && <p className="mt-3 text-[12.5px] text-text-mid">{dialogue.next_guidance}</p>}
          </div>

          {actions.length > 0 && (
            <div className="border-t border-border-whisper px-5 py-4">
              <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Önerilen işlemler', 'Suggested actions')}</div>
              <p className="mt-1 text-[12.5px] text-text-mid">
                {L(
                  'Araca yazan işlemler (kod silme, aktif test) buradan yapılmaz; ayrı, açık onaylı ekrandan ve güvenlik geçidinden geçer.',
                  'Actions that write to the vehicle (clear codes, active tests) are not run from here; they go through a separate, explicitly confirmed screen and the safety gateway.',
                )}
              </p>
              <ul className="mt-2 flex flex-col gap-1 text-[13px] text-text-body" data-testid="assistant-actions">
                {actions.map((a, i) => {
                  const act: ProposedAction = typeof a === 'string' ? { title: a } : a;
                  return (
                    <li key={`${act.type ?? ''}-${act.title ?? ''}-${i}`} className="flex flex-wrap items-center gap-2">
                      <span>• {act.title ?? act.type}</span>
                      {act.target && <span className="text-text-mid">({act.target})</span>}
                      {act.is_mutating && <Chip tone="warn">{L('Araca yazar', 'Writes to vehicle')}</Chip>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {showFeedback && (
            <div className="border-t border-border-whisper px-5 py-4">
              <div className="text-[11.5px] font-semibold uppercase tracking-wide text-text-low">{L('Onarım sonrası geri bildirim', 'After the repair')}</div>
              <p className="mt-1 text-[12.5px] text-text-mid">
                {L('Kod gerçekten çözüldü mü? Cevabınız bu bilgisayardaki öğrenme kaydına yazılır.', 'Was the code really fixed? Your answer is written to this computer’s learning log.')}
              </p>
              <ul className="mt-2 flex flex-col gap-2">
                {card.technical.dtcs.map((d) => (
                  <li key={d} className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-[13px] text-text-hi">{d}</span>
                    {d in feedback ? (
                      <Chip tone="ok">
                        <ClipboardCheck className="h-3.5 w-3.5" />
                        {feedback[d] ? L('Çözüldü kaydedildi', 'Saved as fixed') : L('Çözülmedi kaydedildi', 'Saved as not fixed')}
                      </Chip>
                    ) : (
                      <span className="flex gap-2">
                        <button type="button" className={cx(BTN_GHOST, 'py-1.5')} onClick={() => void sendFeedback(d, true)}>
                          {L('Çözüldü', 'Fixed')}
                        </button>
                        <button type="button" className={cx(BTN_GHOST, 'py-1.5')} onClick={() => void sendFeedback(d, false)}>
                          {L('Çözülmedi', 'Not fixed')}
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <CopilotAskCard sessionAnswer={analysis.structured_answer} />
      </div>
    </div>
  );
};
