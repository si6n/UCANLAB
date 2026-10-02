import React, { useState } from 'react';
import { AlertTriangle, Loader2, Send } from 'lucide-react';
import { CopilotEvidence, CopilotStructuredAnswer, DesktopBridge } from '../../services/bridge';
import { L, lang } from '../mechanic/text';
import { BTN_PRIMARY, Card, CardHeader, Chip, Tone } from './ui';

/**
 * Renders the Python copilot's six-section answer (summary, urgency, causes,
 * steps, missing data, collapsible technical details). Nothing is computed
 * here: every line comes from the payload, and every claim shows its
 * `source#key` reference. Read-only — no action in this card writes to the
 * vehicle.
 */

const URGENCY_TONE: Record<string, Tone> = { RED: 'danger', YELLOW: 'warn', GREEN: 'ok', GRAY: 'neutral' };

const Ref: React.FC<{ value: string }> = ({ value }) =>
  value ? <code className="ml-1 rounded bg-bg-row-hover px-1 text-[11px] text-text-low">{value}</code> : null;

const EvidenceList: React.FC<{ items: CopilotEvidence[]; sign: '+' | '−' }> = ({ items, sign }) => (
  <>
    {items.map((e) => (
      <li key={`${sign}${e.text}${e.ref}`} className="text-[12.5px] text-text-mid">
        {sign} {e.text}
        <Ref value={e.ref} />
      </li>
    ))}
  </>
);

export const CopilotAnswerView: React.FC<{ answer: CopilotStructuredAnswer }> = ({ answer }) => {
  const tone = URGENCY_TONE[answer.urgency.level] ?? 'neutral';
  return (
    <div className="flex flex-col gap-4" data-testid="copilot-answer">
      {answer.safety_banners.map((b) => (
        <div
          key={b.category}
          role="alert"
          data-testid={`copilot-banner-${b.category}`}
          className="flex items-start gap-2 rounded-xl border border-danger-border bg-danger-soft px-4 py-3 text-[13.5px] font-semibold text-del"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <span>{b.text.replace(/^⚠\s*/, '')}</span>
        </div>
      ))}

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-low">{L('1. Kısa özet', '1. Summary')}</h3>
        <p className="mt-1 text-[14px] text-text-body" data-testid="copilot-summary">{answer.summary}</p>
      </section>

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-low">{L('2. Acil mi?', '2. Is it urgent?')}</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Chip tone={tone} testId="copilot-urgency">{answer.urgency.label}</Chip>
        </div>
        <ul className="mt-1 text-[13px] text-text-body">
          {answer.urgency.advice.map((a) => (
            <li key={a}>• {a}</li>
          ))}
          {answer.urgency.reasons.map((r) => (
            <li key={r.text + r.ref} className="text-[12.5px] text-text-mid">
              • {r.text}
              <Ref value={r.ref} />
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-low">{L('3. En olası nedenler', '3. Most likely causes')}</h3>
        {answer.causes.length === 0 ? (
          <p className="mt-1 text-[13px] text-text-mid">
            {L('Kayıtlı kök neden bulunamadı; tahmin yürütülmedi.', 'No recorded root cause found; nothing was guessed.')}
          </p>
        ) : (
          <ol className="mt-1 flex flex-col gap-2" data-testid="copilot-causes">
            {answer.causes.map((c) => (
              <li key={c.id} className="rounded-xl border border-border-whisper px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[13.5px] font-semibold text-text-hi">
                    {c.rank}. {c.title}
                  </span>
                  <span className="flex gap-1.5">
                    <Chip>%{Math.round(c.likelihood * 100)}</Chip>
                    <Chip tone={c.confidence === 'high' ? 'ok' : c.confidence === 'medium' ? 'accent' : 'neutral'}>
                      {L('güven', 'confidence')}: {c.confidence_label}
                    </Chip>
                  </span>
                </div>
                <p className="text-[11.5px] text-text-low">{c.kind_label}</p>
                <ul className="mt-1">
                  <EvidenceList items={c.support} sign="+" />
                  <EvidenceList items={c.against} sign="−" />
                </ul>
                {c.measure_to_confirm.length > 0 && (
                  <p className="mt-1 text-[12.5px] text-text-body">
                    <b>{L('Doğrulamak için ölçün: ', 'Measure to confirm: ')}</b>
                    {c.measure_to_confirm.join(', ')}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
        {answer.causes.length > 0 && (
          <p className="mt-1 text-[11.5px] text-text-low">
            {L(
              'Yüzdeler yalnız listelenen adaylar arasındaki göreli sıralamadır, kesin olasılık değildir.',
              'Percentages are only the relative ranking among the listed candidates, not absolute probabilities.',
            )}
          </p>
        )}
      </section>

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-low">{L('4. Ne yapmalı', '4. What to do')}</h3>
        <ol className="mt-1 flex flex-col gap-1 text-[13px] text-text-body" data-testid="copilot-steps">
          {answer.steps.map((s) => (
            <li key={s.n}>
              {s.n}. {s.text}
              {s.difficulty && <span className="text-text-low"> ({s.difficulty})</span>}
              {s.refs.map((r) => (
                <Ref key={r} value={r} />
              ))}
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-low">{L('5. Eksik veri ve nasıl alınır', '5. Missing data and how to get it')}</h3>
        {answer.missing_data.length === 0 ? (
          <p className="mt-1 text-[13px] text-text-mid">{L('Bu aşamada kritik eksik veri yok.', 'No critical data missing at this point.')}</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1 text-[13px] text-text-body" data-testid="copilot-missing">
            {answer.missing_data.map((m) => (
              <li key={m.key}>
                • <b>{m.what}</b> {m.how}
              </li>
            ))}
          </ul>
        )}
      </section>

      <details className="rounded-xl border border-border-whisper px-3 py-2" data-testid="copilot-technical">
        <summary className="cursor-pointer text-[12px] font-semibold uppercase tracking-wide text-text-low">
          {L('6. Teknik detay', '6. Technical details')}
        </summary>
        <ul className="mt-2 flex flex-col gap-1 text-[12.5px] text-text-mid">
          {answer.technical.codes.map((c) => (
            <li key={c.key}>
              <b className="font-mono text-text-hi">{c.key}</b> — {c.title || L('veritabanında yok', 'not in database')}
              {c.fmi_text && <div>FMI: {c.fmi_text}</div>}
              {c.pgn_text && <div>PGN: {c.pgn_text}</div>}
              {c.severity && <div>{L('Ciddiyet', 'Severity')}: {c.severity}</div>}
              {c.reference_values && <div>{c.reference_values}</div>}
              {c.oem_text && <div>OEM: {c.oem_text}</div>}
              {c.refs.map((r) => (
                <Ref key={r} value={r} />
              ))}
            </li>
          ))}
          {answer.technical.telemetry.map((t) => (
            <li key={t.signal}>
              {t.signal} = {t.value} {t.unit} → {t.status_text}
              {t.reference && <span> ({t.reference})</span>}
              <Ref value={t.ref} />
            </li>
          ))}
          {answer.technical.similar_records.length > 0 && (
            <li>
              {L('Metin benzerliği olan kayıtlar (teşhis değil): ', 'Records with similar wording (not a diagnosis): ')}
              {answer.technical.similar_records.map((h) => h.title).join(' · ')}
            </li>
          )}
          {answer.technical.glossary.map((g) => (
            <li key={g.term}>
              <i>{g.term}</i>: {g.text}
            </li>
          ))}
        </ul>
        {(answer.recalls.items?.length ?? 0) > 0 && (
          <div className="mt-2 text-[12.5px] text-text-mid" data-testid="copilot-recalls">
            <b>NHTSA</b> — {answer.recalls.note}
            <ul>
              {answer.recalls.items?.map((r) => (
                <li key={r.campaign}>
                  • {r.campaign}: {r.component}
                  <Ref value={r.ref} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </details>
    </div>
  );
};

/** Ask box + answer. Falls back to the session-based answer from the analysis payload. */
export const CopilotAskCard: React.FC<{ sessionAnswer?: CopilotStructuredAnswer }> = ({ sessionAnswer }) => {
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<CopilotStructuredAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await DesktopBridge.askCopilotStructured(query.trim(), lang());
      if (res.success && res.answer) setAnswer(res.answer);
      else setError(res.error ?? L('Cevap oluşturulamadı.', 'No answer could be produced.'));
    } finally {
      setBusy(false);
    }
  };

  // While a question is in flight the previous answer is hidden, so a stale
  // answer can never be read as the reply to the new question.
  const shown = busy ? null : (answer ?? sessionAnswer ?? null);
  return (
    <Card testId="copilot-card">
      <CardHeader
        title={L('Copilot’a sorun', 'Ask the copilot')}
        hint={L(
          'Şikâyeti kendi cümlenizle yazın (ör. "motor ısınıyor, DPF lambası yandı"). Çevrimdışı çalışır; ölçüm uydurmaz.',
          'Describe the complaint in your own words (e.g. "engine overheating, DPF light on"). Works offline; never invents measurements.',
        )}
      />
      <div className="flex flex-col gap-4 p-5">
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void ask();
          }}
        >
          <input
            data-testid="copilot-query"
            value={query}
            maxLength={2000}
            onChange={(e) => setQuery(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-border-strong bg-transparent px-3 py-2 text-[14px] text-text-hi outline-none focus:border-accent"
            placeholder={L('Şikâyet, arıza kodu (P0101, SPN 110 FMI 0) veya ölçüm', 'Complaint, fault code (P0101, SPN 110 FMI 0) or a reading')}
          />
          <button type="submit" className={BTN_PRIMARY} disabled={busy || !query.trim()} data-testid="copilot-ask">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {L('Sor', 'Ask')}
          </button>
        </form>
        {error && <p className="text-[13px] text-del">{error}</p>}
        {shown && <CopilotAnswerView answer={shown} />}
      </div>
    </Card>
  );
};
