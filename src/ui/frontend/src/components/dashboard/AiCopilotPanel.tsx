import React, { useState } from 'react';
import {
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  RotateCw,
  Send,
  Bot,
  Radio,
} from 'lucide-react';
import { DiagnosticState, ChatMessage, CopilotAction } from '../../types/can';

interface AiCopilotPanelProps {
  diagnosticState: DiagnosticState;
  chatMessages: ChatMessage[];
  isAiLoading: boolean;
  onRescan: () => void;
  onSendMessage: (query: string) => void;
  onExecuteAction?: (action: CopilotAction) => Promise<void>;
}

export const AiCopilotPanel: React.FC<AiCopilotPanelProps> = ({
  diagnosticState,
  chatMessages,
  isAiLoading,
  onRescan,
  onSendMessage,
  onExecuteAction,
}) => {
  const [inputText, setInputText] = useState('');
  const [checkboxState, setCheckboxState] = useState<Record<string, boolean>>({});
  const [executingActionId, setExecutingActionId] = useState<string | null>(null);

  const handleActionClick = async (action: CopilotAction) => {
    if (!onExecuteAction) return;
    if (action.requires_confirmation) {
      const confirmText = action.confirm_text || `"${action.label}" aksiyonunu yürütmek istediğinizden emin misiniz?`;
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

  const isStandby = diagnosticState.healthStatus === 'standby';
  const isNominal = diagnosticState.healthStatus === 'nominal';
  const isFault = diagnosticState.healthStatus === 'warning' || diagnosticState.healthStatus === 'critical';

  const renderFormattedText = (rawText: string, isCopilot: boolean) => {
    if (!isCopilot) {
      return <div className="whitespace-pre-line font-sans">{rawText}</div>;
    }

    const lines = rawText.split('\n');
    return (
      <div className="space-y-1.5 font-sans leading-relaxed">
        {lines.map((line, idx) => {
          const trimmed = line.trim();
          if (!trimmed) return <div key={idx} className="h-0.5" />;

          const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
          const renderedLine = parts.map((part, pIdx) => {
            if (part.startsWith('**') && part.endsWith('**')) {
              return <strong key={pIdx} className="font-bold text-slate-900">{part.slice(2, -2)}</strong>;
            }
            if (part.startsWith('`') && part.endsWith('`')) {
              return (
                <code key={pIdx} className="rounded bg-slate-200/70 px-1 py-0.5 font-mono text-xs font-semibold text-brand-700">
                  {part.slice(1, -1)}
                </code>
              );
            }
            return part;
          });

          if (trimmed.startsWith('•') || trimmed.startsWith('- ') || /^[0-9]+\./.test(trimmed)) {
            return (
              <div key={idx} className="flex items-start space-x-1.5 pl-0.5">
                <span className="mt-0.5 shrink-0 font-bold text-brand-600">•</span>
                <span className="flex-1">{renderedLine}</span>
              </div>
            );
          }

          return <div key={idx}>{renderedLine}</div>;
        })}
      </div>
    );
  };

  return (
    <div className="surface-panel relative flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white shadow-xs">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-[13px] font-bold text-slate-900">AI Copilot</h2>
            <p className="text-xs font-medium text-slate-500">CAN teşhis asistanı</p>
          </div>
        </div>
        <button
          onClick={onRescan}
          className="btn-ghost !px-2 !py-2"
          title="CAN veri yolunu yeniden tara"
        >
          <RotateCw className="h-4 w-4 text-slate-500" />
        </button>
      </div>

      {/* Scrollable Body */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3.5">
        {/* System Health Status */}
        <div
          className={`rounded-xl border p-3 ${
            isStandby
              ? 'border-slate-200 bg-slate-50 text-slate-800'
              : isNominal
                ? 'border-signal-200/80 bg-signal-50/70'
                : 'border-rose-200/80 bg-rose-50/70'
          }`}
        >
          <div className="flex items-start gap-2.5">
            {isStandby ? (
              <Radio className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
            ) : isNominal ? (
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-signal-600" />
            ) : (
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
            )}
            <div>
              <h3
                className={`text-xs font-bold ${
                  isStandby ? 'text-slate-800' : isNominal ? 'text-signal-900' : 'text-rose-900'
                }`}
              >
                {isStandby
                  ? 'Veri Yolu Beklemede'
                  : isNominal
                    ? 'Sistem Nominal (0 DTC)'
                    : `Kritik Arıza (${diagnosticState.dtcCount} DTC)`}
              </h3>
              <p
                className={`mt-0.5 text-xs leading-relaxed ${
                  isStandby ? 'text-slate-500' : isNominal ? 'text-signal-700' : 'text-rose-700'
                }`}
              >
                {isStandby
                  ? 'CAN veri yolu dinleniyor. Simülasyonu başlatın veya donanım bağlayın.'
                  : isNominal
                    ? 'Tüm CAN düğümleri (ECU, TCU, ABS) normal aralıkta.'
                    : 'Anomali eşiği aşıldı! E-Stop veya acil kontrol önerilir.'}
              </p>
            </div>
          </div>
        </div>

        {/* Recommended Actions (Checklist) */}
        {diagnosticState.recommendedActions.length > 0 && (
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="mb-2 text-xs font-bold text-slate-800">Önerilen Aksiyonlar</div>
            <div className="space-y-1.5">
              {diagnosticState.recommendedActions.map((action) => (
                <label
                  key={action.id}
                  className="flex cursor-pointer select-none items-start space-x-2 text-xs text-slate-700 hover:text-slate-900"
                >
                  <input
                    type="checkbox"
                    checked={!!checkboxState[action.id]}
                    onChange={() =>
                      setCheckboxState((prev) => ({ ...prev, [action.id]: !prev[action.id] }))
                    }
                    className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500/20"
                  />
                  <span className={checkboxState[action.id] ? 'text-slate-500 line-through' : 'text-slate-700'}>
                    {action.text}
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Chat Stream */}
        <div className="space-y-2 pt-1">
          {chatMessages.map((msg) => (
            <div
              key={msg.id}
              className={`rounded-xl border p-3 text-xs leading-relaxed ${
                msg.sender === 'copilot'
                  ? 'border-slate-200 bg-slate-50/90 text-slate-800'
                  : 'ml-auto max-w-[85%] border-brand-600 bg-brand-600 text-white'
              }`}
            >
              <div
                className={`mb-1.5 flex items-center justify-between text-xs font-mono ${
                  msg.sender === 'copilot' ? 'text-slate-500' : 'text-brand-100'
                }`}
              >
                <span className="font-semibold">{msg.sender === 'copilot' ? 'AI Copilot' : 'Siz'}</span>
                <span>{msg.timestamp}</span>
              </div>
              <div>{renderFormattedText(msg.text, msg.sender === 'copilot')}</div>
              {msg.sender === 'copilot' && msg.actions && msg.actions.length > 0 && (
                <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-slate-200/80 pt-2">
                  {msg.actions.map((act) => (
                    <button
                      key={act.id}
                      onClick={() => handleActionClick(act)}
                      disabled={executingActionId === act.id || isAiLoading}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-brand-300 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 shadow-2xs hover:bg-brand-100 hover:border-brand-400 active:scale-95 transition-all disabled:opacity-50"
                      title={act.confirm_text || act.label}
                    >
                      {executingActionId === act.id ? (
                        <RotateCw className="h-3 w-3 animate-spin text-brand-600" />
                      ) : (
                        <span className="text-[11px]">▶️</span>
                      )}
                      <span>{act.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}

          {isAiLoading && (
            <div className="flex items-center space-x-2 rounded-xl border border-brand-200 bg-brand-50/60 p-3 text-xs text-brand-700">
              <Bot className="h-4 w-4 animate-bounce" />
              <span>Copilot analiz ediyor...</span>
            </div>
          )}
        </div>
      </div>

      {/* Message Input */}
      <div className="flex shrink-0 items-center space-x-2 border-t border-slate-200 bg-slate-50 p-2.5">
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="CAN telemetrisi veya arıza sorusu yazın..."
          className="input-field !px-3 !py-1.5 !text-xs"
        />
        <button
          onClick={handleSend}
          disabled={!inputText.trim() || isAiLoading}
          className="btn-primary !px-3 !py-2 disabled:opacity-50"
          title="Mesaj Gönder"
        >
          <Send className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
};
