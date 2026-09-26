import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { DesktopBridge, DataAttributionsPayload, DataSourceAttribution } from '../../services/bridge';
import { ExternalLink, ChevronDown, ChevronUp, RefreshCw, CheckCircle2 } from 'lucide-react';

/* T2-7 — "Acik Kaynak ve Veri Kaynaklari" paneli (CC-BY-4.0 rendered surface).
 *
 * Yasal baglam: T2-4 kapsaminda data/ altina dis veri kumeleri eklendi.
 * Dagitim-icerigi yukumlulugu depo dosyalarinda (data/licenses/*), gorunurluk
 * yukumlulugu bu paneldedir. TEK DOGRU KAYNAK: cizilen her alan (ad, lisans,
 * kaynak adresi, sabit commit, atif metni, kanonik dosya) ve kanonik dosyanin
 * tam govdesi salt-okunur kopru yontemi get_data_attributions() uzerinden
 * gelir; TypeScript icinde ikinci bir kopya (metin, SHA, URL) TUTULMAZ —
 * kopya sessizce kayabilir. Bu yuzden asagida hicbir deger gomulu degildir.
 *
 * Zorunlu atif metni (CC-BY-4.0 kredisi) HER ZAMAN gorunur kart govdesinde,
 * satir sonlari korunarak (whitespace-pre-wrap, kirpma yok) cizilir; hicbir
 * acilir bolumun/tooltip'in/modal'in arkasinda degildir. Uzun lisans dosyasi
 * govdesi yalnizca kullanici isterse acilir (varsayilan: kapali).
 */

interface SettingsAttributionPanelProps {
  onLoadStateChange?: (loaded: boolean) => void;
}

export const SettingsAttributionPanel: React.FC<SettingsAttributionPanelProps> = ({
  onLoadStateChange,
}) => {
  const [payload, setPayload] = useState<DataAttributionsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await DesktopBridge.getDataAttributions();
      if (res && res.success !== false && Array.isArray(res.sources)) {
        setPayload(res);
      } else {
        setPayload(null);
        setLoadError((res as any)?.error || 'Atif verisi okunamadi.');
      }
    } catch (err: any) {
      setPayload(null);
      setLoadError(err?.message || 'Atif verisi okunamadi.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    onLoadStateChange?.(!loading && payload !== null);
  }, [loading, payload, onLoadStateChange]);

  const requiredCount = useMemo(() => {
    if (!payload) return 0;
    if (typeof payload.obligationRequiredCount === 'number') {
      return payload.obligationRequiredCount;
    }
    return payload.sources.filter((s) => s.obligation === 'required').length;
  }, [payload]);

  /**
   * Zorunlu atif metni blogu: kartin govdesinde KOSULSUZ cizilir (bkz. cagri
   * yeri) — hicbir tiklama gerektirmez. Metin oldugu gibi gosterilir
   * (whitespace-pre-wrap); kirpma/ellipsis uygulanmaz.
   */
  const renderVerbatimBlock = (text: string, tone: 'required' | 'recorded') => (
    <div
      data-testid="attribution-verbatim-text"
      className={`rounded-[6px] border px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words ${
        tone === 'required'
          ? 'border-accent-line bg-accent-soft text-accent-text'
          : 'border-border-whisper bg-bg-app text-text-mid'
      }`}
    >
      {text}
    </div>
  );

  const renderSourceCard = (src: DataSourceAttribution) => {
    const isRequired = src.obligation === 'required';
    const isExpanded = expanded[src.id] === true;
    return (
      <div
        key={src.id}
        data-testid="attribution-source"
        className="rounded-box border border-border-whisper bg-bg-panel p-3.5 space-y-2.5 transition-colors hover:border-border-strong"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              data-testid="attribution-source-name"
              className="text-[12.5px] font-semibold text-text-hi truncate"
            >
              {src.name}
            </span>
            {isRequired && (
              <span className="inline-flex items-center rounded-[4px] border border-accent-line bg-accent-soft px-1.5 py-0.5 font-mono text-[9.5px] font-semibold text-accent">
                ATIF ZORUNLU
              </span>
            )}
          </div>
          <span
            data-testid="attribution-license"
            title={src.licenseName}
            className="shrink-0 rounded-[5px] border border-border-whisper bg-bg-panel px-2 py-0.5 font-mono text-[10.5px] text-text-mid"
          >
            {src.license}
          </span>
        </div>

        <a
          data-testid="attribution-source-url"
          href={src.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 break-all font-mono text-[11px] text-accent hover:underline"
        >
          <span>{src.sourceUrl}</span>
          <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
        </a>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10.5px] text-text-low">
          <div data-testid="attribution-pinned-commit">
            commit: <span className="text-text-mid">{src.pinnedCommit}</span>
          </div>
          <div data-testid="attribution-canonical-file">
            dosya: <span className="text-text-mid">{src.canonicalFile}</span>
          </div>
        </div>

        {/* Zorunlu atif metni: kosulsuz, acilir bolumun DISINDA ve ONCESINDE. */}
        {renderVerbatimBlock(src.attributionText, isRequired ? 'required' : 'recorded')}

        <div>
          <button
            type="button"
            aria-expanded={isExpanded}
            onClick={() => setExpanded((prev) => ({ ...prev, [src.id]: !prev[src.id] }))}
            className="inline-flex items-center gap-1 rounded-[5px] border border-border-whisper bg-bg-panel px-2.5 py-1 text-[11px] font-medium text-text-mid transition-colors hover:border-border-strong hover:text-text-hi hover:bg-bg-row-hover cursor-pointer"
          >
            <span>{isExpanded ? 'Lisans metnini gizle' : 'Lisans metnini goster'}</span>
            {isExpanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
          </button>
        </div>

        {isExpanded && (
          <div
            data-testid="attribution-source-text"
            className="rounded-[6px] border border-border-whisper bg-bg-app p-3 font-mono text-[11px] text-text-mid whitespace-pre-wrap break-words max-h-64 overflow-y-auto"
          >
            {src.sourceText}
          </div>
        )}
      </div>
    );
  };

  return (
    <div data-testid="settings-attribution-panel" className="space-y-3">
      <div className="flex items-center justify-between gap-2 rounded-btn border border-border-whisper bg-bg-panel px-3.5 py-2.5 text-[11.5px] text-text-mid">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-add shrink-0" />
          <span>
            Salt okunur liste. Çevrimdışı kullanımda da erişilebilir; ağ erişimi gerektirmez.
          </span>
        </div>
        {payload && (
          <span className="font-mono text-[10.5px] text-text-low shrink-0">
            ({payload.sources.length} kaynak, {requiredCount} zorunlu)
          </span>
        )}
      </div>

      {loading && (
        <div className="flex items-center gap-2 py-4 text-xs text-text-low">
          <RefreshCw className="h-3.5 w-3.5 animate-spin text-accent" />
          <span>Atif verisi yukleniyor...</span>
        </div>
      )}

      {loadError && (
        <div className="flex items-center justify-between gap-2 rounded-btn border border-danger-border bg-danger-soft p-3 text-xs text-del">
          <span className="min-w-0 flex-1">{loadError}</span>
          <button
            type="button"
            onClick={load}
            className="shrink-0 rounded-[6px] border border-danger-border px-2.5 py-1 text-[11px] font-medium hover:bg-bg-row-hover cursor-pointer"
          >
            Tekrar dene
          </button>
        </div>
      )}

      {!loading && !loadError && payload && (
        <div className="space-y-3">{payload.sources.map(renderSourceCard)}</div>
      )}
    </div>
  );
};
