import React, { useState, useEffect, useCallback } from 'react';
import {
  Sparkles,
  AlertTriangle,
  RotateCw,
  ArrowUp,
  Bot,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  ThumbsUp,
  ThumbsDown,
  X,
  Radio,
  CheckCircle2,
} from 'lucide-react';
import { DiagnosticState, ChatMessage, CopilotAction } from '../../types/can';
import { DesktopBridge, UserDiagnosticCard } from '../../services/bridge';

// ────────────────────────────────────────────────────────────────────────
// Karar Kartı — Sadece gerçek arıza/analiz durumunda gösterilir (RED/YELLOW/GREEN)
// ────────────────────────────────────────────────────────────────────────
const RISK_UI: Record<string, { icon: string; badge: string; text: string; edge: string; bg: string }> = {
  RED: { icon: '🔴', badge: 'KRİTİK ARİZA', text: 'text-del', edge: 'border-del/40', bg: 'bg-delbg' },
  YELLOW: { icon: '🟡', badge: 'DİKKAT — SERVİS KONTROLÜ', text: 'text-accent-text', edge: 'border-accent/40', bg: 'bg-accent-soft' },
  GREEN: { icon: '🟢', badge: 'NOMİNAL — BİLGİ', text: 'text-add', edge: 'border-addedge/40', bg: 'bg-addbg' },
};

const DecisionCard: React.FC<{ card: UserDiagnosticCard }> = ({ card }) => {
  const ui = RISK_UI[card.risk_level];
  if (!ui) return null;

  return (
    <div className={`rounded-[10px] border ${ui.edge} ${ui.bg} p-3 text-[12.5px] leading-relaxed space-y-1.5`}>
      <div className="flex items-center gap-2">
        <span className="text-sm">{ui.icon}</span>
        <span className={`font-sans text-[11px] font-bold uppercase tracking-wider ${ui.text}`}>
          {ui.badge}
        </span>
      </div>
      <div className="font-sans text-[13px] font-semibold text-text-hi">
        {card.headline_tr}
      </div>
      <p className="whitespace-pre-line font-sans text-text-body text-[12px]">
        {card.summary_tr}
      </p>
      {card.source_badges && card.source_badges.length > 0 && (
        <div className="pt-1 font-mono text-[10px] text-text-low">
          Kaynak: {card.source_badges.join(', ')}
        </div>
      )}
    </div>
  );
};

export interface DialogueQuestion {
  id: string;
  kind: 'yes_no' | 'choice' | 'measurement';
  text: string;
  why: string;
  evidence_link: string;
  unit?: string | null;
  expected_range?: [number, number] | null;
  choices?: string[];
  how_to_measure?: string | null;
  target_hypothesis_id?: string | null;
}

export interface EliminatedHypothesis {
  id: string;
  fault: string;
  score: number;
  reason: string;
  eliminated_by_question_id?: string | null;
}

interface AiCopilotPanelProps {
  diagnosticState: DiagnosticState;
  chatMessages: ChatMessage[];
  isAiLoading: boolean;
  onRescan: () => void;
  onSendMessage: (query: string) => void;
  onExecuteAction?: (action: CopilotAction) => Promise<void>;
  onClose?: () => void;
}

export const AiCopilotPanel: React.FC<AiCopilotPanelProps> = ({
  diagnosticState,
  chatMessages,
  isAiLoading,
  onRescan,
  onSendMessage,
  onExecuteAction,
  onClose,
}) => {
  const [inputText, setInputText] = useState('');
  const [executingActionId, setExecutingActionId] = useState<string | null>(null);

  // ── Teşhis Karar Kartı (Python motorundan, yalnız gerçek arıza varsa) ──
  const [card, setCard] = useState<UserDiagnosticCard | null>(null);

  // ── İnteraktif Teşhis Sorusu ──
  const [activeQuestion, setActiveQuestion] = useState<DialogueQuestion | null>(null);
  const [eliminatedHypotheses, setEliminatedHypotheses] = useState<EliminatedHypothesis[]>([]);
  const [dialogueState, setDialogueState] = useState<string>('IDLE');
  const [concludedFault, setConcludedFault] = useState<string | null>(null);
  const [measurementVal, setMeasurementVal] = useState<string>('');
  const [showHowToMeasure, setShowHowToMeasure] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // ── Teknisyen geri bildirimi (isteğe bağlı ve tamamlanmış teşhiste) ──
  const [feedbackSent, setFeedbackSent] = useState<boolean>(false);
  const [showFeedbackBox, setShowFeedbackBox] = useState<boolean>(false);
  const [technicianNote, setTechnicianNote] = useState<string>('');

  const refreshDialogue = useCallback(async () => {
    try {
      const d = await DesktopBridge.getDialogueState();
      if (d?.success) {
        setActiveQuestion((d.current_question as DialogueQuestion) || null);
        setEliminatedHypotheses((d.eliminated_hypotheses as EliminatedHypothesis[]) || []);
        setDialogueState(d.dialogue_state || 'IDLE');
        setConcludedFault(d.concluded_fault || null);
      }
    } catch {
      // Offline fallback
    }
  }, []);

  const refreshCard = useCallback(async () => {
    try {
      const a = await DesktopBridge.getDiagnosticAnalysis();
      if (a?.success && a.user_card) {
        setCard(a.user_card as UserDiagnosticCard);
      }
    } catch {
      // Offline fallback
    }
  }, []);

  useEffect(() => {
    void refreshCard();
    void refreshDialogue();
    const timer = window.setInterval(() => {
      void refreshCard();
      void refreshDialogue();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [refreshCard, refreshDialogue]);

  const handleFeedback = async (resolved: boolean) => {
    const activeCode = (diagnosticState.dtcCount > 0 && diagnosticState.healthStatus !== 'nominal') ? 'ACTIVE_DTC' : 'SESSION';
    await DesktopBridge.recordTechnicianFeedback(activeCode, resolved, technicianNote);
    setFeedbackSent(true);
    setShowFeedbackBox(false);
    setTechnicianNote('');
  };

  const handleAnswer = async (value: any, isUnknown = false) => {
    if (!activeQuestion || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const res = await DesktopBridge.recordOperatorAnswer(
        activeQuestion.id,
        value,
        activeQuestion.kind,
        activeQuestion.unit,
        isUnknown
      );
      if (res?.success) {
        setActiveQuestion((res.next_question as DialogueQuestion) || null);
        setEliminatedHypotheses((res.eliminated_hypotheses as EliminatedHypothesis[]) || []);
        setDialogueState(res.dialogue_state || 'IDLE');
        setConcludedFault(res.concluded_fault || null);
        setMeasurementVal('');
        setShowHowToMeasure(false);
        void refreshCard();
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleActionClick = async (action: CopilotAction) => {
    if (!onExecuteAction) return;
    if (action.requires_confirmation) {
      const isClear = action.action_type.includes('clear_dtc') || action.action_type === 'j1939_dm11';
      const confirmText = isClear
        ? 'Bu işlem arıza ışığını söndürebilir ama sorunu çözmez.\nSorun devam ederse ışık tekrar yanar.\nSadece onarım sonrası kullanılması önerilir.'
        : action.confirm_text || `"${action.label}" aksiyonunu yürütmek istediğinizden emin misiniz?`;
      const confirmed = window.confirm(confirmText);
      if (!confirmed) return;
    }
    setExecutingActionId(action.id);
    try {
      await onExecuteAction(action);
    } finally {
      setExecutingActionId(null);
    }
  };

  const handleSend = () => {
    if (!inputText.trim()) return;
    onSendMessage(inputText.trim());
    setInputText('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSend();
    }
  };

  const isFault = diagnosticState.healthStatus === 'warning' || diagnosticState.healthStatus === 'critical';

  const renderFormattedText = (rawText: string, isCopilot: boolean) => {
    if (!isCopilot) {
      return <div className="whitespace-pre-line font-sans text-[12.5px] leading-relaxed text-text-hi">{rawText}</div>;
    }

    const lines = rawText.split('\n');
    return (
      <div className="space-y-1.5 font-sans text-[12.5px] leading-relaxed">
        {lines.map((line, idx) => {
          const trimmed = line.trim();
          if (!trimmed) return <div key={idx} className="h-0.5" />;

          const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
          const renderedLine = parts.map((part, pIdx) => {
            if (part.startsWith('**') && part.endsWith('**')) {
              return <strong key={pIdx} className="font-semibold text-text-hi">{part.slice(2, -2)}</strong>;
            }
            if (part.startsWith('`') && part.endsWith('`')) {
              return (
                <code key={pIdx} className="rounded-[4px] bg-accent-soft px-1 py-0.5 font-mono text-[11px] text-accent-text">
                  {part.slice(1, -1)}
                </code>
              );
            }
            return part;
          });

          if (trimmed.startsWith('•') || trimmed.startsWith('- ') || /^[0-9]+\./.test(trimmed)) {
            return (
              <div key={idx} className="flex items-start space-x-2 pl-0.5">
                <span className="mt-1 shrink-0 font-bold text-accent text-[10px]">•</span>
                <span className="flex-1 text-text-body">{renderedLine}</span>
              </div>
            );
          }

          return <div key={idx} className="text-text-body">{renderedLine}</div>;
        })}
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col overflow-hidden text-text-body select-none">
      {/* Top Header — clean, modern, zero clutter */}
      <div className="flex shrink-0 items-center justify-between border-b border-border/40 px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-[8px] bg-accent-soft text-accent shadow-xs">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <h2 className="text-[12.5px] font-semibold text-text-hi">CAN Teşhis Copilot</h2>
              <span className="flex h-1.5 w-1.5 rounded-full bg-add" title="Çevrimdışı Teşhis Motoru Aktif" />
            </div>
            <p className="text-[10px] text-text-low font-mono">Çevrimdışı Uzman Analiz Motoru</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onRescan}
            className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi"
            title="CAN veri yolunu yeniden tara"
          >
            <RotateCw className="h-3.5 w-3.5" />
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="flex h-6 w-6 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-del"
              title="Paneli Kapat"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Scrollable Conversation & Diagnostic Area */}
      <div className="flex-1 space-y-3.5 overflow-y-auto p-3.5">
        {/* Critical Bus Fault Alert (Only when active DTCs exist) */}
        {isFault && (
          <div className="flex items-start gap-2.5 rounded-[10px] border border-del/40 bg-delbg p-2.5 text-xs text-del">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div className="flex-1">
              <span className="font-semibold">Kritik Arıza Tespiti ({diagnosticState.dtcCount} DTC)</span>
              <p className="mt-0.5 text-[11px] opacity-90">
                CAN veri yolunda anomali eşiği aşıldı. Hata kodlarını çözümlemek için aşağıdaki komutları kullanabilirsiniz.
              </p>
            </div>
          </div>
        )}

        {/* Diagnostic Decision Card — only shown when there's an actual diagnosed risk */}
        {card && card.risk_level && card.risk_level !== 'GRAY' && (
          <DecisionCard card={card} />
        )}

        {/* Interactive Diagnostic Question Card */}
        {activeQuestion && (
          <div className="rounded-[10px] border border-accent-line/50 bg-accent-soft/40 p-3 text-xs space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-accent-text flex items-center gap-1.5">
                <HelpCircle className="h-3.5 w-3.5" />
                <span>İnteraktif Teşhis Sorusu</span>
              </span>
              {activeQuestion.unit && (
                <span className="font-mono text-[10px] text-text-mid">
                  Birim: {activeQuestion.unit}
                </span>
              )}
            </div>

            <div>
              <p className="font-medium text-text-hi text-[12px]">{activeQuestion.text}</p>
              {activeQuestion.why && (
                <p className="mt-1 text-[11px] text-text-mid leading-relaxed">{activeQuestion.why}</p>
              )}
            </div>

            {/* How to measure instruction */}
            {activeQuestion.how_to_measure && (
              <div>
                <button
                  type="button"
                  onClick={() => setShowHowToMeasure(!showHowToMeasure)}
                  className="flex items-center gap-1 text-[10.5px] font-medium text-accent-text hover:underline"
                >
                  {showHowToMeasure ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  <span>Nasıl Ölçerim? (Talimat)</span>
                </button>
                {showHowToMeasure && (
                  <p className="mt-1 rounded-[6px] border border-surface-inset-border bg-surface-inset p-2 text-[10.5px] leading-relaxed text-text-body">
                    {activeQuestion.how_to_measure}
                  </p>
                )}
              </div>
            )}

            {/* Question Response Controls */}
            <div className="pt-1">
              {activeQuestion.kind === 'yes_no' && (
                <div className="flex flex-wrap gap-2">
                  <button
                    disabled={isSubmitting}
                    onClick={() => handleAnswer('Evet')}
                    className="rounded-[6px] border border-border/80 bg-surface-inset px-3 py-1 font-medium text-text-body transition-all hover:border-accent-line hover:text-accent disabled:opacity-50"
                  >
                    Evet
                  </button>
                  <button
                    disabled={isSubmitting}
                    onClick={() => handleAnswer('Hayır')}
                    className="rounded-[6px] border border-border/80 bg-surface-inset px-3 py-1 font-medium text-text-body transition-all hover:border-del hover:text-del disabled:opacity-50"
                  >
                    Hayır
                  </button>
                  <button
                    disabled={isSubmitting}
                    onClick={() => handleAnswer(null, true)}
                    className="rounded-[6px] px-2.5 py-1 text-text-low transition-colors hover:text-text-mid disabled:opacity-50"
                  >
                    Bilmiyorum
                  </button>
                </div>
              )}

              {activeQuestion.kind === 'choice' && (
                <div className="flex flex-wrap gap-1.5">
                  {(activeQuestion.choices || []).map((ch, idx) => (
                    <button
                      key={idx}
                      disabled={isSubmitting}
                      onClick={() => handleAnswer(ch)}
                      className="rounded-[6px] border border-border/80 bg-surface-inset px-2.5 py-1 text-[11px] font-medium text-text-body transition-all hover:border-accent-line hover:text-accent"
                    >
                      {ch}
                    </button>
                  ))}
                  <button
                    disabled={isSubmitting}
                    onClick={() => handleAnswer(null, true)}
                    className="rounded-[6px] px-2 py-1 text-[11px] text-text-low hover:text-text-mid"
                  >
                    Bilmiyorum
                  </button>
                </div>
              )}

              {activeQuestion.kind === 'measurement' && (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="any"
                    value={measurementVal}
                    onChange={(e) => setMeasurementVal(e.target.value)}
                    placeholder={
                      activeQuestion.expected_range
                        ? `Aralık: ${activeQuestion.expected_range[0]} - ${activeQuestion.expected_range[1]}`
                        : 'Ölçüm değeri'
                    }
                    className="w-32 rounded-[6px] border border-surface-inset-border bg-surface-inset px-2.5 py-1 font-mono text-[11px] text-text-hi placeholder:text-text-faint focus:border-border-focus focus:outline-none"
                  />
                  <button
                    disabled={isSubmitting || !measurementVal.trim()}
                    onClick={() => handleAnswer(parseFloat(measurementVal))}
                    className="rounded-[6px] border border-accent-line bg-accent-soft px-3 py-1 font-medium text-accent-text transition-all hover:bg-accent hover:text-white disabled:opacity-50"
                  >
                    Kaydet
                  </button>
                  <button
                    disabled={isSubmitting}
                    onClick={() => handleAnswer(null, true)}
                    className="rounded-[6px] px-2 py-1 text-[11px] text-text-low hover:text-text-mid"
                  >
                    Bilmiyorum
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Quick Suggestion Chips (Shown on clean initial chat) */}
        {chatMessages.length <= 1 && (
          <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-3 space-y-2.5">
            <div className="flex items-center gap-2">
              <Bot className="h-3.5 w-3.5 text-accent" />
              <span className="text-[11.5px] font-semibold text-text-hi">Hızlı Teşhis Başlıkları</span>
            </div>
            <p className="text-[11px] text-text-mid leading-relaxed">
              CAN veri yolunu analiz etmek veya arıza teşhisi başlatmak için bir konu seçebilirsiniz:
            </p>
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {[
                'P0300 Tekleme Teşhisi',
                '0x18FEF200 Yakıt Tüketimi',
                'UDS 0x22 DID Okuma',
                'Baud Rate & Hat Durumu',
              ].map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => onSendMessage(chip)}
                  className="rounded-[6px] border border-border/80 bg-surface-inset px-2.5 py-1 text-[11px] font-medium text-text-body transition-all hover:border-accent-line hover:text-accent-text active:scale-95"
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Chat Messages Feed */}
        <div className="space-y-3 pt-1">
          {chatMessages.map((msg) =>
            msg.sender === 'user' ? (
              <div
                key={msg.id}
                className="ml-auto max-w-[85%] rounded-[10px] border border-border/60 bg-surface-inset px-3 py-2 shadow-xs"
              >
                <div className="mb-1 flex items-center justify-end gap-1.5 font-mono text-[10px] text-text-low">
                  <span>Siz</span>
                  <span>·</span>
                  <span>{msg.timestamp}</span>
                </div>
                <div>{renderFormattedText(msg.text, false)}</div>
              </div>
            ) : (
              <div key={msg.id} className="mr-auto max-w-[95%] space-y-1">
                <div className="flex items-center gap-1.5 font-mono text-[10.5px] text-text-mid">
                  <div className="flex h-4 w-4 items-center justify-center rounded-[4px] bg-accent-soft text-accent">
                    <Sparkles className="h-2.5 w-2.5" />
                  </div>
                  <span className="font-semibold text-text-hi">Teşhis Asistanı</span>
                  <span className="text-text-faint">·</span>
                  <span className="text-text-low">{msg.timestamp}</span>
                </div>

                <div className="rounded-[10px] border border-border/40 bg-surface-inset/30 px-3 py-2 text-[12.5px] text-text-body">
                  {renderFormattedText(msg.text, true)}
                </div>

                {/* Actions Attached to Assistant Message */}
                {msg.actions && msg.actions.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {msg.actions.map((act) => (
                      <button
                        key={act.id}
                        onClick={() => handleActionClick(act)}
                        disabled={executingActionId === act.id || isAiLoading}
                        className="inline-flex items-center gap-1.5 rounded-[6px] border border-accent-line/40 bg-accent-soft px-2.5 py-1 font-sans text-[11px] font-semibold text-accent-text transition-all hover:bg-accent hover:text-white active:scale-95 disabled:opacity-50"
                        title={act.confirm_text || act.label}
                      >
                        {executingActionId === act.id ? (
                          <RotateCw className="h-3 w-3 animate-spin" />
                        ) : (
                          <span className="text-[10px]">▶</span>
                        )}
                        <span>{act.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
          )}

          {/* Typing / Processing indicator */}
          {isAiLoading && (
            <div className="flex items-center space-x-2 font-mono text-[11px] text-text-mid">
              <Bot className="h-3.5 w-3.5 animate-pulse text-accent" />
              <span>Copilot analiz ediyor...</span>
            </div>
          )}
        </div>

        {/* Completed Diagnosis Feedback (Only when fault has been concluded) */}
        {concludedFault && !feedbackSent && (
          <div className="rounded-[8px] border border-border/60 bg-surface-inset/20 p-2.5 space-y-2 text-xs">
            <div className="flex items-center justify-between text-[11px] text-text-mid">
              <span>Teşhis Sonucu Doğrulama:</span>
              <span className="font-medium text-text-hi">{concludedFault}</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleFeedback(true)}
                className="flex-1 flex items-center justify-center gap-1 rounded-[6px] border border-addedge/40 px-2 py-1 text-[11px] font-medium text-add hover:bg-addbg transition-colors"
              >
                <ThumbsUp className="h-3 w-3" />
                <span>Sorunu Çözdü</span>
              </button>
              <button
                onClick={() => setShowFeedbackBox(!showFeedbackBox)}
                className="flex-1 flex items-center justify-center gap-1 rounded-[6px] border border-deledge/40 px-2 py-1 text-[11px] font-medium text-del hover:bg-delbg transition-colors"
              >
                <ThumbsDown className="h-3 w-3" />
                <span>Farklıydı</span>
              </button>
            </div>
            {showFeedbackBox && (
              <div className="space-y-1.5 pt-1">
                <input
                  type="text"
                  value={technicianNote}
                  onChange={(e) => setTechnicianNote(e.target.value)}
                  placeholder="Gerçek kök neden / onarım detayı..."
                  className="w-full rounded-[6px] border border-surface-inset-border bg-surface-inset px-2 py-1 text-[11px] text-text-hi placeholder:text-text-faint focus:outline-none"
                />
                <button
                  disabled={!technicianNote.trim()}
                  onClick={() => handleFeedback(false)}
                  className="w-full rounded-[6px] border border-border px-2 py-1 text-[11px] font-medium text-text-body hover:bg-bg-row-hover disabled:opacity-50"
                >
                  Düzeltmeyi Kaydet
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Modern Composer Input at Bottom */}
      <div className="flex shrink-0 items-center gap-2 border-t border-border/40 px-3.5 py-2.5 bg-transparent">
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="CAN telemetrisi veya arıza sorusu yazın..."
          className="h-8 flex-1 rounded-[8px] border border-surface-inset-border bg-surface-inset px-3 font-sans text-[12px] text-text-hi placeholder:text-text-faint transition-colors focus:border-border-focus focus:outline-none"
        />
        <button
          onClick={handleSend}
          disabled={!inputText.trim() || isAiLoading}
          aria-label="Mesaj Gönder"
          title="Mesaj Gönder"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border/80 bg-surface-inset text-text-hi transition-all hover:border-accent-line hover:text-accent active:scale-95 disabled:opacity-40"
        >
          <ArrowUp className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
};
