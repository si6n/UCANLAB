import React, { useState } from 'react';
import { 
  Share2, 
  Info, 
  Zap, 
  CheckCircle2 
} from 'lucide-react';

interface PinDef {
  pin: number;
  name: string;
  voltage: string;
  type: 'CAN' | 'GND' | 'PWR' | 'K-LINE' | 'NC';
  desc: string;
}

export const PinoutGuideView: React.FC = () => {
  const [selectedPin, setSelectedPin] = useState<number>(6);
  const [connectorType, setConnectorType] = useState<'OBD2' | 'J1939'>('OBD2');

  const obd2Pins: PinDef[] = [
    { pin: 1, name: 'Vendor Option', voltage: '0-12V', type: 'NC', desc: 'Üreticiye özel OEM haberleşme ve teşhis hattı.' },
    { pin: 2, name: 'J1850 Bus+', voltage: '0-7V', type: 'NC', desc: 'SAE J1850 PWM/VPW Bus High hattı (Eski Amerikan araçları).' },
    { pin: 3, name: 'Vendor Option', voltage: 'N/A', type: 'NC', desc: 'Üreticiye özel teşhis sinyali.' },
    { pin: 4, name: 'Chassis Ground', voltage: '0V', type: 'GND', desc: 'Araç Gövde/Şasi Topraklaması.' },
    { pin: 5, name: 'Signal Ground', voltage: '0V', type: 'GND', desc: 'Sensör ve Sinyal Referans Topraklaması.' },
    { pin: 6, name: 'CAN High (ISO 11898-2)', voltage: '2.5V - 3.5V', type: 'CAN', desc: 'Yüksek Hızlı CAN Bus High Sinyal Hattı. Multimetrede referans şasiye göre resesif durumda 2.5V, dominant durumda 3.5V ölçülür.' },
    { pin: 7, name: 'K-Line (ISO 9141-2)', voltage: '0-12V', type: 'K-LINE', desc: 'Tek hatlı çift yönlü eski teşhis hattı (K-Line).' },
    { pin: 8, name: 'Vendor Option', voltage: '12V', type: 'NC', desc: 'Üreticiye özel kontak veya ateşleme tespiti.' },
    { pin: 9, name: 'Vendor Option', voltage: 'N/A', type: 'NC', desc: 'Üreticiye özel teşhis hattı.' },
    { pin: 10, name: 'J1850 Bus-', voltage: '0-5V', type: 'NC', desc: 'SAE J1850 PWM Bus Low hattı.' },
    { pin: 11, name: 'Vendor Option', voltage: 'N/A', type: 'NC', desc: 'Üreticiye özel.' },
    { pin: 12, name: 'Vendor Option', voltage: 'N/A', type: 'NC', desc: 'Üreticiye özel.' },
    { pin: 13, name: 'Vendor Option', voltage: 'N/A', type: 'NC', desc: 'Üreticiye özel programlama pini.' },
    { pin: 14, name: 'CAN Low (ISO 11898-2)', voltage: '1.5V - 2.5V', type: 'CAN', desc: 'Yüksek Hızlı CAN Bus Low Sinyal Hattı. Multimetrede referans şasiye göre resesif durumda 2.5V, dominant durumda 1.5V ölçülür.' },
    { pin: 15, name: 'L-Line (ISO 9141-2)', voltage: '0-12V', type: 'K-LINE', desc: 'Eski ISO 9141 başlatma hattı.' },
    { pin: 16, name: 'Battery Power (+12V)', voltage: '+12V / +24V', type: 'PWR', desc: 'Sürekli Araç Akü Artı Kutbu (+BATT).' },
  ];

  const currentPinInfo = obd2Pins.find(p => p.pin === selectedPin) || obd2Pins[5];

  return (
    <div className="p-4 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-card flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-lg bg-brand-50 border border-brand-200 flex items-center justify-center text-brand-600">
            <Share2 className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-slate-900">Konnektör Pinout & Sinyal Bağlantı Rehberi</h2>
            <p className="text-xs text-slate-500">OBD-II (J1962), J1939 Deutsch 9-Pin ve 120Ω Sonlandırma Standartları</p>
          </div>
        </div>

        <div className="inline-flex bg-slate-100 p-0.5 rounded-lg border border-slate-200 text-xs">
          <button
            onClick={() => setConnectorType('OBD2')}
            className={`px-3 py-1 rounded-md font-semibold transition-all ${
              connectorType === 'OBD2' ? 'bg-white text-brand-600 shadow-xs' : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            OBD-II (16-Pin J1962)
          </button>
          <button
            onClick={() => setConnectorType('J1939')}
            className={`px-3 py-1 rounded-md font-semibold transition-all ${
              connectorType === 'J1939' ? 'bg-white text-brand-600 shadow-xs' : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            J1939 Deutsch 9-Pin
          </button>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left Column: Interactive Socket Diagram */}
        <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
          <div className="text-xs font-bold text-slate-900 uppercase tracking-wider">
            SAE J1962 (OBD-II) Soket Yerleşimi
          </div>

          {/* Socket Shell Representation */}
          <div className="bg-slate-900 border-2 border-slate-700 rounded-xl p-6 text-center shadow-inner space-y-3">
            {/* Top Row: Pin 1 to 8 */}
            <div className="grid grid-cols-8 gap-2">
              {obd2Pins.slice(0, 8).map((p) => (
                <button
                  key={p.pin}
                  onClick={() => setSelectedPin(p.pin)}
                  className={`p-2.5 rounded-lg border font-mono text-xs font-bold transition-all ${
                    selectedPin === p.pin
                      ? 'ring-2 ring-brand-400 bg-brand-600 text-white border-white'
                      : p.type === 'CAN'
                      ? 'bg-brand-900/60 border-brand-500 text-brand-300 hover:bg-brand-800'
                      : p.type === 'PWR'
                      ? 'bg-rose-900/60 border-rose-500 text-rose-300 hover:bg-rose-800'
                      : p.type === 'GND'
                      ? 'bg-slate-800 border-slate-600 text-slate-300 hover:bg-slate-700'
                      : 'bg-slate-950 border-slate-800 text-slate-500 hover:bg-slate-900'
                  }`}
                >
                  <div className="text-xs opacity-75">#{p.pin}</div>
                  <div>{p.type}</div>
                </button>
              ))}
            </div>

            {/* Bottom Row: Pin 9 to 16 */}
            <div className="grid grid-cols-8 gap-2">
              {obd2Pins.slice(8, 16).map((p) => (
                <button
                  key={p.pin}
                  onClick={() => setSelectedPin(p.pin)}
                  className={`p-2.5 rounded-lg border font-mono text-xs font-bold transition-all ${
                    selectedPin === p.pin
                      ? 'ring-2 ring-brand-400 bg-brand-600 text-white border-white'
                      : p.type === 'CAN'
                      ? 'bg-brand-900/60 border-brand-500 text-brand-300 hover:bg-brand-800'
                      : p.type === 'PWR'
                      ? 'bg-rose-900/60 border-rose-500 text-rose-300 hover:bg-rose-800'
                      : p.type === 'GND'
                      ? 'bg-slate-800 border-slate-600 text-slate-300 hover:bg-slate-700'
                      : 'bg-slate-950 border-slate-800 text-slate-500 hover:bg-slate-900'
                  }`}
                >
                  <div className="text-xs opacity-75">#{p.pin}</div>
                  <div>{p.type}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 120 Ohm Termination Rule Card */}
          <div className="bg-brand-50 border border-brand-200 rounded-xl p-3.5 flex items-start space-x-3 text-xs text-brand-900">
            <Info className="w-5 h-5 text-brand-600 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div className="font-bold">120Ω Sonlandırma Direnci Kuralı:</div>
              <p className="text-brand-800 leading-relaxed">
                ISO 11898 standardına göre CAN-H ve CAN-L sinyal hatlarının fiziksel iki ucunda 120Ω paralel direnç bulunmalıdır. Sistem kapalıyken Pin 6 ile Pin 14 arasında multimetre ile ölçülen eşdeğer direnç <strong>60Ω</strong> olmalıdır.
              </p>
            </div>
          </div>
        </div>

        {/* Right Column: Selected Pin Detailed Info */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-card space-y-4">
          <div className="text-xs font-bold text-slate-900 uppercase tracking-wider">
            Seçili Pin Özellikleri
          </div>

          <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-base font-bold text-brand-600">
                Pin #{currentPinInfo.pin}
              </span>
              <span className={`text-xs px-2.5 py-1 rounded-md font-bold ${
                currentPinInfo.type === 'CAN' ? 'bg-brand-100 text-brand-800' : currentPinInfo.type === 'PWR' ? 'bg-rose-100 text-rose-800' : 'bg-slate-200 text-slate-800'
              }`}>
                {currentPinInfo.type}
              </span>
            </div>

            <div>
              <div className="text-xs font-bold text-slate-800">{currentPinInfo.name}</div>
              <div className="text-xs font-mono font-semibold text-signal-600 mt-0.5">
                Voltaj: {currentPinInfo.voltage}
              </div>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed border-t border-slate-200 pt-2 font-sans">
              {currentPinInfo.desc}
            </p>
          </div>

          <div className="space-y-2 pt-2 text-xs text-slate-600">
            <div className="font-bold text-slate-800">Doğrulama İpuçları:</div>
            <div className="flex items-center space-x-2">
              <CheckCircle2 className="w-4 h-4 text-signal-600" />
              <span>Kontak açıkken Pin 16'da +12V akü voltajı okunmalıdır.</span>
            </div>
            <div className="flex items-center space-x-2">
              <CheckCircle2 className="w-4 h-4 text-signal-600" />
              <span>Pin 4 ve Pin 5 şasiye &lt; 0.1V dirençle bağlı olmalıdır.</span>
            </div>
            <div className="flex items-center space-x-2">
              <Zap className="w-4 h-4 text-amber-500" />
              <span>CAN-H ve CAN-L sinyalleri diferansiyel çift olarak bükülmüş olmalıdır.</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
