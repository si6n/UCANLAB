import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Scale,
  FileText,
  Info,
  Link2,
  Github,
  RefreshCw,
  AlertCircle,
  ShieldCheck,
  WifiOff,
  Database,
  ExternalLink,
} from 'lucide-react';
import { DesktopBridge, DataAttributionsPayload, DataSourceAttribution } from '../../services/bridge';

/**
 * T2-7 — "Açık Kaynak ve Veri Kaynakları" (Open Source & Data Attributions).
 *
 * LEGAL CONTEXT (do not delete this panel without a licence review):
 * T2-4 vendored four external datasets into `data/`. SITRAK error codes are
 * CC-BY-4.0, which obliges us to keep the credit
 * `Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)`
 * *reasonably visible to the recipient*; canboat is Apache-2.0 (NOTICE) and
 * Wal33D/dtc-database is MIT (copyright notice). Shipping the notice files in
 * the repository satisfies the distribution-content half of that duty — this
 * panel is the rendered half.
 *
 * SINGLE SOURCE OF TRUTH: every field rendered here (name, licence, URL,
 * pinned commit, attribution text, verbatim licence file) is returned by the
 * read-only bridge method `get_data_attributions()`, which is backed by
 * `src/ui/data_attribution_catalog.py`. Nothing is hardcoded in TypeScript —
 * there is no second copy that could silently drift.
 *
 * The SITRAK credit is rendered in the *always-visible* panel body: it is NOT
 * behind a per-source accordion, tooltip, modal or "read more" click. Only the
 * long-form licence file text of non-SITRAK sources is collapsed.
 */

interface SettingsAttributionPanelProps {
  /** Optional: lets a parent (e.g. a modal) react to a load failure. */
  onLoadStateChange?: (loaded: boolean) => void;
}

export const SettingsAttributionPanel: React.FC<SettingsAttributionPanelProps> = ({
  onLoadStateChange,
}) => {
  const [payload, setPayload] = useState<DataAttributionsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Which long-form licence bodies the operator has expanded. SITRAK is never
  // in here — its attribution text lives in the always-visible card body.
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
        setLoadError(
          (res as any)?.error ||
            'Atıf verisi okunamadı: yerel köprü (pywebview) yanıt vermedi.'
        );
      }
    } catch (err: any) {
      setPayload(null);
      setLoadError(err?.message || 'Atıf verisi okunamadı.');
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

  const sourceCount = payload?.sources.length ?? 0;

  /**
   * Renders the required attribution text VERBATIM.
   *
   * `whitespace-pre-wrap` + a plain text node (no markdown parser, no
   * ellipsis, no truncation) preserves the upstream licence text byte-for-byte,
   * including the canboat NOTICE block's internal line breaks and indent. This
   * is deliberate: Apache-2.0 §4(d) asks for a readable copy of the notice,
   * not a summary.
   */
  const renderVerbatimBlock = (text: string, tone: 'required' | 'recorded') => (
    <div
      data-testid="attribution-verbatim-text"
      className={`rounded-[6px] border px-2.5 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap break-words ${
        tone === 'required'
          ? 'border-accent-line/40 bg-accent-soft/40 text-accent-text'
          : 'border-surface-inset-border bg-surface-inset text-text-mid'
      }`}
    >
      {text}
    </div>
  );

  const renderSourceCard = (src: DataSourceAttribution) => {
    const isRequired = src.obligation === 'required';
    const isExpanded = expanded[src.id] === true;
    const hasBody = typeof src.sourceText === 'string' && src.sourceText.length > 0;

    return (
      <div
        key={src.id}
        data-testid="attribution-source"
        data-attribution-id={src.id}
        data-attribution-license={src.license}
        data-attribution-obligation={src.obligation}
        className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3"
      >
        {/* Header: name + licence + obligation badge */}
        <div className="flex items-start justify-between gap-3 border-b border-border/40 pb-2.5">
          <div className="flex items-start gap-2 min-w-0">
            <Database className="h-4 w-4 mt-0.5 shrink-0 text-text-low" />
            <div className="min-w-0">
              <h4
                data-testid="attribution-source-name"
                className="text-[12px] font-semibold text-text-hi break-words"
              >
                {src.name}
              </h4>
              <p className="text-[10.5px] text-text-mid">
                {src.licenseName || src.license}
              </p>
            </div>
          </div>
          <span
            data-testid="attribution-obligation-badge"
            className={`shrink-0 rounded-[4px] border px-2 py-0.5 font-mono text-[10.5px] font-bold ${
              isRequired
                ? 'border-accent-line/40 bg-accent-soft text-accent-text'
                : 'border-border bg-surface-inset text-text-low'
            }`}
          >
            {isRequired ? 'ATIF ZORUNLU' : 'KAYIT İÇİN'}
          </span>
        </div>

        {/* Required attribution text — always visible, verbatim. */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <ShieldCheck
              className={`h-3.5 w-3.5 ${isRequired ? 'text-accent' : 'text-text-low'}`}
            />
            <span className="font-sans text-[11px] font-semibold text-text-hi">
              {isRequired ? 'Zorunlu Atıf Metni' : 'Atıf Metni (yükümlülük yok)'}
            </span>
          </div>
          {renderVerbatimBlock(src.attributionText, isRequired ? 'required' : 'recorded')}
        </div>

        {/* Metadata table: licence, source URL, pinned commit, canonical file */}
        <div className="space-y-1 rounded-[6px] border border-surface-inset-border bg-surface-inset p-2.5 font-mono text-[11px]">
          <div className="flex justify-between gap-3">
            <span className="text-text-mid shrink-0">Lisans:</span>
            <span
              data-testid="attribution-license"
              className="font-semibold text-text-hi text-right break-words"
            >
              {src.license}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-text-mid shrink-0">Kaynak URL:</span>
            <span data-testid="attribution-source-url" className="text-right">
              {src.sourceUrl ? (
                <a
                  href={src.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent-text hover:underline break-all"
                >
                  {src.sourceUrl}
                </a>
              ) : (
                <span className="text-text-low">—</span>
              )}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-text-mid shrink-0">Sabitlenen commit:</span>
            <span
              data-testid="attribution-pinned-commit"
              className="text-right text-text-hi break-all select-all"
            >
              {src.pinnedCommit || '—'}
            </span>
          </div>
          {src.licenseUrl && (
            <div className="flex justify-between gap-3">
              <span className="text-text-mid shrink-0">Lisans metni:</span>
              <span className="text-right">
                <a
                  href={src.licenseUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent-text hover:underline break-all"
                >
                  {src.licenseUrl}
                </a>
              </span>
            </div>
          )}
          {src.canonicalFile && (
            <div className="flex justify-between gap-3">
              <span className="text-text-mid shrink-0">Kanonik dosya:</span>
              <span
                data-testid="attribution-canonical-file"
                className="text-right text-text-mid break-all"
              >
                {src.canonicalFile}
              </span>
            </div>
          )}
        </div>

        {/* Turkish explanation of what the obligation means. */}
        {src.noteTr && (
          <p className="font-sans text-[11px] leading-relaxed text-text-mid">
            {src.noteTr}
          </p>
        )}

        {/* Covered artefacts */}
        {src.artifacts && src.artifacts.length > 0 && (
          <div className="space-y-1">
            <span className="font-sans text-[10.5px] font-medium text-text-low">
              Kapsanan veri dosyaları
            </span>
            <div className="flex flex-wrap gap-1">
              {src.artifacts.map((a) => (
                <span
                  key={a}
                  className="rounded-[4px] border border-border/60 px-1.5 py-0.5 font-mono text-[10px] text-text-mid break-all"
                >
                  {a}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Long-form licence body — verbatim file text, fetch from disk only
            when the operator opens it. SITRAK's REQUIRED text is already shown
            above; this is the full canonfile, not the obligation. */}
        <div className="pt-0.5">
          <button
            type="button"
            data-testid="attribution-toggle-source"
            aria-expanded={isExpanded}
            onClick={() => setExpanded((prev) => ({ ...prev, [src.id]: !prev[src.id] }))}
            className="flex items-center gap-1.5 text-[10.5px] font-medium text-text-low hover:text-text-hi transition-colors"
          >
            <FileText className="h-3 w-3" />
            <span>
              {isExpanded
                ? 'Kanonik lisans/atıf dosyasını gizle'
                : 'Kanonik lisans/atıf dosyasını göster'}
            </span>
          </button>
          {isExpanded && (
            <pre
              data-testid="attribution-source-text"
              className="mt-2 max-h-64 overflow-auto rounded-[6px] border border-surface-inset-border bg-surface-inset p-2.5 font-mono text-[10.5px] leading-relaxed text-text-mid whitespace-pre-wrap break-words"
            >
              {hasBody
                ? src.sourceText
                : `Kanun dosyası okunamadı: ${src.sourcePath || src.canonicalFile}`}
            </pre>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="max-w-2xl space-y-4" data-testid="settings-attribution-panel">
      {/* Header */}
      <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 space-y-3">
        <div className="flex items-center justify-between gap-3 border-b border-border/40 pb-2.5">
          <div className="flex items-center gap-2">
            <Scale className="h-4 w-4 text-accent" />
            <div>
              <h3 className="text-[13px] font-semibold text-text-hi">
                Açık Kaynak ve Veri Kaynakları
              </h3>
              <p className="text-[10.5px] text-text-mid">
                Open Source &amp; Data Attributions — birlikte dağıtılan üçüncü taraf veri lisansları
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            title="Yenile"
            className="flex items-center gap-1.5 rounded-[6px] border border-border px-2.5 py-1 font-sans text-[11px] font-medium text-text-body transition-colors hover:bg-bg-row-hover hover:text-text-hi disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
            <span>Yenile</span>
          </button>
        </div>

        <p className="font-sans text-[11.5px] leading-relaxed text-text-mid">
          Bu ürün, açık kaynak topluluklarının yayımladığı teşhis veri kümelerini
          yerel olarak (çevrimdışı) içerir. Lisansların gerektirdiği atıf ve telif
          bildirimleri aşağıda listelenmiştir; her kaydın metni, ürünle birlikte
          dağıtılan kanonik lisans dosyasından okunur.
        </p>

        {/* Summary strip */}
        <div className="grid grid-cols-3 gap-2 text-[11.5px]">
          <div className="rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5 space-y-0.5">
            <span className="block text-[10px] text-text-low">Veri Kaynağı</span>
            <span
              data-testid="attribution-source-count"
              className="block font-mono text-[15px] font-bold text-text-hi"
            >
              {sourceCount}
            </span>
          </div>
          <div className="rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5 space-y-0.5">
            <span className="block text-[10px] text-text-low">Atıf Zorunlu</span>
            <span
              data-testid="attribution-required-count"
              className="block font-mono text-[15px] font-bold text-accent-text"
            >
              {requiredCount}
            </span>
          </div>
          <div className="rounded-[8px] border border-surface-inset-border bg-surface-inset p-2.5 space-y-0.5">
            <span className="block text-[10px] text-text-low">Çalışma Modu</span>
            <span className="flex items-center gap-1 font-mono text-[11px] font-bold text-text-hi">
              <WifiOff className="h-3 w-3 text-add" />
              <span>Çevrimdışı</span>
            </span>
          </div>
        </div>
      </div>

      {/* Load error — fail LOUD, never render an empty list as if compliant. */}
      {loadError && (
        <div
          data-testid="attribution-load-error"
          className="flex items-start gap-2 rounded-[8px] border border-deledge/40 bg-delbg p-2.5 text-[11.5px] font-medium text-del"
        >
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span className="flex-1">{loadError}</span>
        </div>
      )}

      {/* Missing canonical files reported by the backend — a compliance gap
          must be visible, not silently swallowed. */}
      {payload?.errors && payload.errors.length > 0 && (
        <div className="flex items-start gap-2 rounded-[8px] border border-warn-edge/40 bg-warn-soft p-2.5 text-[11.5px] text-warn">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <div className="flex-1 space-y-0.5">
            {payload.errors.map((e) => (
              <div key={e}>{e}</div>
            ))}
          </div>
        </div>
      )}

      {loading && !payload && (
        <div className="rounded-[10px] border border-border/60 bg-surface-inset/30 p-4 text-[11.5px] text-text-mid">
          Atıf kayıtları okunuyor…
        </div>
      )}

      {/* Source cards */}
      {payload?.sources.map(renderSourceCard)}

      {/* Runtime guarantee, stated for the operator. */}
      <div className="flex items-start gap-2 rounded-[10px] border border-border/60 bg-surface-inset/30 p-3 text-[11px] leading-relaxed text-text-mid">
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-text-low" />
        <span>
          Bu panel yalnızca cihazdaki yerel lisans dosyalarını okur: ağ erişimi,
          bulut çağrısı veya LLM kullanılmaz. Lisans metinleri LICENSES.md ve{' '}
          <span className="font-mono">data/licenses/</span> altındaki kanonik
          dosyalarla birebir aynıdır.
        </span>
      </div>

      {/* Canonical repo paths, so a reviewer can verify the setup. */}
      {payload?.sources && payload.sources.length > 0 && (
        <div className="flex items-center gap-2 rounded-[10px] border border-border/60 bg-surface-inset/30 p-3">
          <Github className="h-3.5 w-3.5 shrink-0 text-text-low" />
          <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] text-text-low">
            <Link2 className="h-3 w-3" />
            {Array.from(new Set(payload.sources.map((s) => s.canonicalFile))).map((f) => (
              <span key={f} className="rounded-[4px] border border-border/60 px-1.5 py-0.5 font-mono">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Footer: nothing here writes state. */}
      <div className="flex items-center gap-1.5 pb-1 text-[10.5px] text-text-faint">
        <ShieldCheck className="h-3 w-3" />
        <span>
          Salt okunur yüzey — bu panel hiçbir ayarı kaydetmez, hiçbir iletime
          (TX) veya E-Stop yetkisine dokunmaz.
        </span>
        <ExternalLink className="h-3 w-3" />
      </div>
    </div>
  );
};

export default SettingsAttributionPanel;
