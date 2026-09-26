import React, { useState } from 'react';
import {
  Search,
} from 'lucide-react';

interface PinDef {
  pin: number;
  label: string;
  short: string;
  name: string;
  voltage: string;
  nominalVoltage: string;
  wireColor: string;
  wireHex: string;
  type: 'CAN' | 'GND' | 'PWR' | 'K-LINE' | 'NC';
  desc: string;
  standard: string;
}

const OBD2_PINS: PinDef[] = [
  { pin: 1, label: '1', short: 'OEM', name: 'Üretici Opsiyonu', voltage: '0-12 V', nominalVoltage: '-', wireColor: 'Sarı / Siyah', wireHex: '#eab308', type: 'NC', desc: 'Üreticiye özel haberleşme veya ikincil veriyolu pini.', standard: 'OEM Özel' },
  { pin: 2, label: '2', short: 'J1850+', name: 'J1850 Bus+', voltage: '0-7 V', nominalVoltage: '5.0 V', wireColor: 'Mor', wireHex: '#a855f7', type: 'NC', desc: 'SAE J1850 PWM / VPW diferansiyel bus high hattı.', standard: 'SAE J1850' },
  { pin: 3, label: '3', short: 'OEM', name: 'Üretici Opsiyonu', voltage: '-', nominalVoltage: '-', wireColor: 'Gri', wireHex: '#6b7280', type: 'NC', desc: 'Üreticiye özel teşhis veya programlama kontrol hattı.', standard: 'OEM Özel' },
  { pin: 4, label: '4', short: 'Şasi GND', name: 'Şasi Toprağı', voltage: '0.0 V', nominalVoltage: '0.0 V', wireColor: 'Siyah', wireHex: '#1e293b', type: 'GND', desc: 'Araç metal şasi topraklaması ve gövde referansı.', standard: 'ISO 15031' },
  { pin: 5, label: '5', short: 'Sinyal GND', name: 'Sinyal Toprağı', voltage: '0.0 V', nominalVoltage: '0.0 V', wireColor: 'Kahverengi', wireHex: '#78350f', type: 'GND', desc: 'CAN alıcı-verici ve sensör sinyal referans toprağı.', standard: 'ISO 15031' },
  { pin: 6, label: '6', short: 'CAN-H', name: 'CAN High (Yüksek Hız)', voltage: '2.5-3.5 V', nominalVoltage: '3.5 V Dominant', wireColor: 'Sarı', wireHex: '#38bdf8', type: 'CAN', desc: 'Yüksek hızlı CAN High hattı. Resesif 2.5 V, dominant 3.5 V.', standard: 'ISO 11898-2' },
  { pin: 7, label: '7', short: 'K-Line', name: 'K-Line Teşhis', voltage: '0-12 V', nominalVoltage: '12 V Pull-up', wireColor: 'Beyaz / Mavi', wireHex: '#f59e0b', type: 'K-LINE', desc: 'ISO 9141-2 ve KWP2000 tek hatlı çift yönlü seri teşhis.', standard: 'ISO 9141-2' },
  { pin: 8, label: '8', short: 'KL15', name: 'Kontak Algılama', voltage: '12-14 V', nominalVoltage: '12.6 V', wireColor: 'Mavi', wireHex: '#3b82f6', type: 'NC', desc: 'Kontak açık (KL15) gerilim algılama hattı.', standard: 'OEM Özel' },
  { pin: 9, label: '9', short: 'OEM', name: 'Üretici Opsiyonu', voltage: '-', nominalVoltage: '-', wireColor: 'Turuncu', wireHex: '#f97316', type: 'NC', desc: 'Üreticiye özel ikincil veri hattı.', standard: 'OEM Özel' },
  { pin: 10, label: '10', short: 'J1850-', name: 'J1850 Bus-', voltage: '0-5 V', nominalVoltage: '0.0 V Resesif', wireColor: 'Açık Mavi', wireHex: '#38bdf8', type: 'NC', desc: 'SAE J1850 PWM diferansiyel eksi veri hattı.', standard: 'SAE J1850' },
  { pin: 11, label: '11', short: 'OEM', name: 'Üretici Opsiyonu', voltage: '-', nominalVoltage: '-', wireColor: 'Pembe', wireHex: '#ec4899', type: 'NC', desc: 'Üreticiye özel modül tetik hattı.', standard: 'OEM Özel' },
  { pin: 12, label: '12', short: 'OEM', name: 'Üretici Opsiyonu', voltage: '-', nominalVoltage: '-', wireColor: 'Açık Yeşil', wireHex: '#22c55e', type: 'NC', desc: 'Üreticiye özel gövde ağı hattı.', standard: 'OEM Özel' },
  { pin: 13, label: '13', short: 'OEM', name: 'Flash Yetkisi', voltage: '-', nominalVoltage: '-', wireColor: 'Lacivert', wireHex: '#1e3a8a', type: 'NC', desc: 'Üreticiye özel programlama ve bootloader pini.', standard: 'OEM Özel' },
  { pin: 14, label: '14', short: 'CAN-L', name: 'CAN Low (Yüksek Hız)', voltage: '1.5-2.5 V', nominalVoltage: '1.5 V Dominant', wireColor: 'Yeşil', wireHex: '#818cf8', type: 'CAN', desc: 'Yüksek hızlı CAN Low hattı. Resesif 2.5 V, dominant 1.5 V.', standard: 'ISO 11898-2' },
  { pin: 15, label: '15', short: 'L-Line', name: 'L-Line Başlatma', voltage: '0-12 V', nominalVoltage: '12 V', wireColor: 'Sarı / Yeşil', wireHex: '#eab308', type: 'K-LINE', desc: 'ISO 9141 tek yönlü başlatma ve uyandırma hattı.', standard: 'ISO 9141-2' },
  { pin: 16, label: '16', short: '+12V', name: 'Akü Beslemesi (KL30)', voltage: '12.0-14.4 V', nominalVoltage: '12.6 V DC', wireColor: 'Kırmızı', wireHex: '#f43f5e', type: 'PWR', desc: 'Araç aküsünden sağlanan kesintisiz ana güç beslemesi.', standard: 'ISO 15031' },
];

const J1939_PINS: PinDef[] = [
  { pin: 1, label: 'A', short: 'GND', name: 'Akü Toprağı', voltage: '0.0 V', nominalVoltage: '0.0 V', wireColor: 'Siyah', wireHex: '#1e293b', type: 'GND', desc: 'Akü eksi kutbu ve ana şasi topraklama hattı.', standard: 'SAE J1939-11' },
  { pin: 2, label: 'B', short: '+24V', name: 'Akü Beslemesi', voltage: '12 / 24 V', nominalVoltage: '24.0 V', wireColor: 'Kırmızı', wireHex: '#f43f5e', type: 'PWR', desc: 'Ağır vasıta sürekli ana akü besleme hattı.', standard: 'SAE J1939-11' },
  { pin: 3, label: 'C', short: 'CAN-H', name: 'CAN High', voltage: '2.5-3.5 V', nominalVoltage: '3.5 V Dominant', wireColor: 'Sarı', wireHex: '#38bdf8', type: 'CAN', desc: 'Blendajlı bükümlü çift J1939 CAN High veri hattı.', standard: 'SAE J1939-11' },
  { pin: 4, label: 'D', short: 'CAN-L', name: 'CAN Low', voltage: '1.5-2.5 V', nominalVoltage: '1.5 V Dominant', wireColor: 'Yeşil', wireHex: '#818cf8', type: 'CAN', desc: 'Blendajlı bükümlü çift J1939 CAN Low veri hattı.', standard: 'SAE J1939-11' },
  { pin: 5, label: 'E', short: 'SHLD', name: 'CAN Blendaj Ekranı', voltage: '0.0 V', nominalVoltage: 'Toprak Referansı', wireColor: 'Örgü Blendaj', wireHex: '#94a3b8', type: 'GND', desc: 'Kablo elektrostatik blendaj ve EMI gürültü bastırma.', standard: 'SAE J1939-11' },
  { pin: 6, label: 'F', short: 'J1708+', name: 'J1708 Veri (+)', voltage: '0-5 V', nominalVoltage: '3.8 V', wireColor: 'Turuncu', wireHex: '#f97316', type: 'NC', desc: 'Ağır vasıta ikincil teşhis ve telemetri veri hattı (+).', standard: 'SAE J1708' },
  { pin: 7, label: 'G', short: 'J1708-', name: 'J1708 Veri (-)', voltage: '0-5 V', nominalVoltage: '1.2 V', wireColor: 'Mavi', wireHex: '#3b82f6', type: 'NC', desc: 'Ağır vasıta ikincil teşhis ve telemetri veri hattı (-).', standard: 'SAE J1708' },
  { pin: 8, label: 'H', short: 'CAN2-H', name: 'İkincil CAN High', voltage: '2.5-3.5 V', nominalVoltage: '2.5 V', wireColor: 'Kahverengi', wireHex: '#a855f7', type: 'NC', desc: 'Üreticiye özel ikincil telematik veya gövde CAN hattı.', standard: 'OEM Özel' },
  { pin: 9, label: 'J', short: 'CAN2-L', name: 'İkincil CAN Low', voltage: '1.5-2.5 V', nominalVoltage: '2.5 V', wireColor: 'Gri', wireHex: '#64748b', type: 'NC', desc: 'Üreticiye özel ikincil telematik veya gövde CAN hattı.', standard: 'OEM Özel' },
];

export const PinoutGuideView: React.FC = () => {
  const [connectorType, setConnectorType] = useState<'OBD2' | 'J1939'>('OBD2');
  const [selectedPin, setSelectedPin] = useState<number>(6);
  const [searchQuery, setSearchQuery] = useState('');

  const pins = connectorType === 'OBD2' ? OBD2_PINS : J1939_PINS;
  const current = pins.find((p) => p.pin === selectedPin) || pins[0];

  const filteredPins = pins.filter(
    (p) =>
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.short.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.standard.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden text-text-body font-sans select-none">
      {/* Top Header */}
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/40 px-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold uppercase tracking-wider text-text-hi">
            Pinout Rehberi
          </span>
          <span className="text-text-low">/</span>
          <span className="text-xs text-text-mid">
            {connectorType === 'OBD2' ? 'SAE J1962 (16-Pin Teşhis)' : 'Deutsch HD10-9 (SAE J1939)'}
          </span>
        </div>

        {/* Switcher */}
        <div className="inline-flex rounded border border-border/60 bg-surface-inset p-0.5">
          <button
            type="button"
            onClick={() => {
              setConnectorType('OBD2');
              setSelectedPin(6);
            }}
            className={`rounded px-3 py-0.5 font-mono text-xs transition-colors ${
              connectorType === 'OBD2'
                ? 'bg-accent text-white font-medium'
                : 'text-text-mid hover:text-text-hi'
            }`}
          >
            OBD-II (16-Pin)
          </button>
          <button
            type="button"
            onClick={() => {
              setConnectorType('J1939');
              setSelectedPin(3);
            }}
            className={`rounded px-3 py-0.5 font-mono text-xs transition-colors ${
              connectorType === 'J1939'
                ? 'bg-accent text-white font-medium'
                : 'text-text-mid hover:text-text-hi'
            }`}
          >
            J1939 (9-Pin Deutsch)
          </button>
        </div>
      </div>

      {/* Main 2-Column Split */}
      <div className="grid min-h-0 flex-1 grid-cols-12 overflow-hidden">
        {/* Left Column: Physical Schematic & Scope */}
        <div className="col-span-6 flex flex-col justify-between border-r border-border/40 p-6 overflow-y-auto">
          <div className="space-y-6">
            <div>
              <div className="font-mono text-xs uppercase tracking-wider text-text-low">
                Araç Tarafı Dişi Soket
              </div>
              <div className="mt-1 text-xs text-text-mid">
                İncelemek istediğiniz pine tıklayın.
              </div>
            </div>

            {/* Realistic Schematic Layout */}
            {connectorType === 'OBD2' ? (
              <div className="py-2">
                {/* OBD-II Trapezoid Housing */}
                <div className="mx-auto max-w-[420px] rounded-lg border border-border/60 bg-bg-app/40 p-4">
                  {/* Top Row: 1 - 8 */}
                  <div className="flex justify-between items-center px-2 pb-3">
                    {OBD2_PINS.slice(0, 8).map((p) => {
                      const isSelected = selectedPin === p.pin;
                      return (
                        <button
                          key={p.pin}
                          type="button"
                          onClick={() => setSelectedPin(p.pin)}
                          className={`flex h-10 w-9 flex-col items-center justify-center rounded border transition-colors ${
                            isSelected
                              ? 'border-accent bg-accent text-white font-bold'
                              : 'border-border/60 bg-surface-inset text-text-hi hover:border-border'
                          }`}
                        >
                          <span className="font-mono text-xs">{p.pin}</span>
                          <span
                            className="mt-1 h-1 w-3 rounded-full"
                            style={{ backgroundColor: p.wireHex }}
                          />
                        </button>
                      );
                    })}
                  </div>

                  {/* Divider line */}
                  <div className="border-t border-border/20 my-1" />

                  {/* Bottom Row: 9 - 16 */}
                  <div className="flex justify-between items-center px-4 pt-3">
                    {OBD2_PINS.slice(8, 16).map((p) => {
                      const isSelected = selectedPin === p.pin;
                      return (
                        <button
                          key={p.pin}
                          type="button"
                          onClick={() => setSelectedPin(p.pin)}
                          className={`flex h-10 w-9 flex-col items-center justify-center rounded border transition-colors ${
                            isSelected
                              ? 'border-accent bg-accent text-white font-bold'
                              : 'border-border/60 bg-surface-inset text-text-hi hover:border-border'
                          }`}
                        >
                          <span className="font-mono text-xs">{p.pin}</span>
                          <span
                            className="mt-1 h-1 w-3 rounded-full"
                            style={{ backgroundColor: p.wireHex }}
                          />
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="mt-2 text-center font-mono text-[11px] text-text-low">
                  Üst Sıra: Pin 1-8 · Alt Sıra: Pin 9-16 (Genişten dara trapez)
                </div>
              </div>
            ) : (
              <div className="py-2">
                {/* J1939 Circular Deutsch Shell */}
                <div className="mx-auto flex h-52 w-52 items-center justify-center rounded-full border border-border/60 bg-bg-app/40 relative">
                  {/* Center pin E */}
                  {(() => {
                    const centerPin = J1939_PINS.find((p) => p.label === 'E')!;
                    const isSelected = selectedPin === centerPin.pin;
                    return (
                      <button
                        key={centerPin.pin}
                        type="button"
                        onClick={() => setSelectedPin(centerPin.pin)}
                        className={`absolute flex h-9 w-9 flex-col items-center justify-center rounded-full border transition-colors ${
                          isSelected
                            ? 'border-accent bg-accent text-white font-bold'
                            : 'border-border/60 bg-surface-inset text-text-hi hover:border-border'
                        }`}
                      >
                        <span className="font-mono text-xs font-semibold">{centerPin.label}</span>
                        <span
                          className="h-1 w-2.5 rounded-full"
                          style={{ backgroundColor: centerPin.wireHex }}
                        />
                      </button>
                    );
                  })()}

                  {/* Circular peripheral pins: A, B, C, D, F, G, H, J */}
                  {J1939_PINS.filter((p) => p.label !== 'E').map((p, idx) => {
                    const angleDeg = idx * 45 - 90;
                    const rad = (angleDeg * Math.PI) / 180;
                    const r = 74; // radius
                    const x = Math.round(r * Math.cos(rad));
                    const y = Math.round(r * Math.sin(rad));
                    const isSelected = selectedPin === p.pin;

                    return (
                      <button
                        key={p.pin}
                        type="button"
                        onClick={() => setSelectedPin(p.pin)}
                        style={{
                          transform: `translate(${x}px, ${y}px)`,
                        }}
                        className={`absolute flex h-9 w-9 flex-col items-center justify-center rounded-full border transition-colors ${
                          isSelected
                            ? 'border-accent bg-accent text-white font-bold'
                            : 'border-border/60 bg-surface-inset text-text-hi hover:border-border'
                        }`}
                      >
                        <span className="font-mono text-xs font-semibold">{p.label}</span>
                        <span
                          className="h-1 w-2.5 rounded-full"
                          style={{ backgroundColor: p.wireHex }}
                        />
                      </button>
                    );
                  })}
                </div>

                <div className="mt-2 text-center font-mono text-[11px] text-text-low">
                  Deutsch HD10-9-1939 Dairesel Dağılım
                </div>
              </div>
            )}

            {/* Differential Voltage Level Strip */}
            <div className="border-t border-border/30 pt-4 space-y-2 font-mono text-xs">
              <div className="text-[11px] uppercase tracking-wider text-text-low">
                ISO 11898-2 Diferansiyel Sinyal Seviyeleri
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="p-2 rounded bg-surface-inset border border-border/30">
                  <div className="text-[10px] text-text-low">CAN-H (Dominant)</div>
                  <div className="text-sm font-semibold text-accent">3.5 V</div>
                </div>
                <div className="p-2 rounded bg-surface-inset border border-border/30">
                  <div className="text-[10px] text-text-low">CAN-L (Dominant)</div>
                  <div className="text-sm font-semibold text-text-hi">1.5 V</div>
                </div>
                <div className="p-2 rounded bg-surface-inset border border-border/30">
                  <div className="text-[10px] text-text-low">Resesif (Boşta)</div>
                  <div className="text-sm font-semibold text-text-mid">2.5 V (Δ0V)</div>
                </div>
              </div>
            </div>
          </div>

          <div className="font-mono text-[11px] text-text-low">
            Hat Empedansı: 120 Ω paralel sonlandırma (60 Ω eşdeğer)
          </div>
        </div>

        {/* Right Column: Pin Dossier & Directory */}
        <div className="col-span-6 flex flex-col justify-between p-6 overflow-hidden">
          <div className="space-y-6 flex-1 min-h-0 flex flex-col">
            {/* Selected Pin Dossier */}
            <div className="border-b border-border/30 pb-4">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold text-accent uppercase tracking-wider">
                  Pin {current.label} · {current.short}
                </span>
                <span className="font-mono text-xs text-text-low">{current.standard}</span>
              </div>
              <h3 className="mt-1 text-base font-semibold text-text-hi">{current.name}</h3>
              <p className="mt-1 text-xs text-text-mid leading-relaxed">{current.desc}</p>

              {/* Technical specs table */}
              <div className="mt-4 grid grid-cols-3 gap-3 font-mono text-xs border-t border-border/20 pt-3">
                <div>
                  <span className="text-[11px] text-text-low">Gerilim Aralığı</span>
                  <div className="font-medium text-text-hi">{current.voltage}</div>
                </div>
                <div>
                  <span className="text-[11px] text-text-low">Nominal Değer</span>
                  <div className="font-medium text-text-hi">{current.nominalVoltage}</div>
                </div>
                <div>
                  <span className="text-[11px] text-text-low">Kablo Rengi</span>
                  <div className="flex items-center gap-1.5 font-medium text-text-hi">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: current.wireHex }}
                    />
                    <span>{current.wireColor}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Quick Filter Search & Directory Table */}
            <div className="flex-1 min-h-0 flex flex-col pt-1">
              <div className="flex items-center justify-between pb-2">
                <span className="font-mono text-xs uppercase tracking-wider text-text-low">
                  Pin Dizini ({filteredPins.length})
                </span>

                <div className="relative">
                  <Search className="absolute left-2 top-2 h-3 w-3 text-text-low" />
                  <input
                    type="text"
                    placeholder="Pin ara..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="h-7 w-36 rounded border border-border/60 bg-surface-inset pl-7 pr-2 font-mono text-xs text-text-hi focus:outline-none focus:border-accent focus-visible:ring-1 focus-visible:ring-accent"
                  />
                </div>
              </div>

              {/* Table */}
              <div className="min-h-0 flex-1 overflow-y-auto divide-y divide-border/20 font-mono text-xs">
                {filteredPins.map((p) => {
                  const isSelected = selectedPin === p.pin;
                  return (
                    <div
                      key={p.pin}
                      onClick={() => setSelectedPin(p.pin)}
                      className={`flex items-center justify-between py-2 px-1 cursor-pointer transition-colors ${
                        isSelected ? 'bg-accent/10 font-semibold text-accent' : 'hover:bg-bg-row-hover text-text-hi'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="w-6 text-text-low">{p.label}</span>
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: p.wireHex }}
                        />
                        <span className="truncate max-w-[160px]">{p.name}</span>
                      </div>

                      <div className="flex items-center gap-3 text-right text-[11px] text-text-low">
                        <span>{p.voltage}</span>
                        <span className="w-16 truncate">{p.standard}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
