import React, { useState } from 'react';
import { Info, Ruler } from 'lucide-react';
import { L } from '../mechanic/text';
import { Card, CardHeader, Chip, Segmented, Tone, cx } from './ui';

/**
 * Pin rehberi — connector reference for the three sockets the vehicle
 * catalog knows (obd2_16pin, deutsch_9pin, n2k_micro_c).
 *
 * Only what the standards assign is stated. The previous guide gave every
 * OBD-II pin a wire colour (there is no standard one), labelled a "CAN-H
 * yellow" pin with a blue swatch and invented pin roles ("flash yetkisi"
 * on pin 13); manufacturer pins are now called just that.
 */

type Kind = 'can' | 'power' | 'ground' | 'serial' | 'oem' | 'shield';

interface Pin {
  id: string;
  name: () => string;
  kind: Kind;
  detail: () => string;
  /** Only where the standard fixes a colour (DeviceNet/NMEA 2000, J1939-11/15 CAN pair). */
  colour?: () => string;
}

interface Connector {
  id: 'obd2' | 'deutsch9' | 'n2k';
  title: () => string;
  standard: string;
  used: () => string;
  layout: string[][];
  layoutNote: () => string;
  pins: Pin[];
}

const OEM = (id: string): Pin => ({
  id,
  name: () => L('Üreticiye bırakılmış', 'Manufacturer discretionary'),
  kind: 'oem',
  detail: () =>
    L(
      'Standart bir görev atamaz; üretici kendi hattı için kullanabilir. Görevini üreticinin şemasından doğrulayın.',
      'The standard assigns no function; the manufacturer may use it. Check its role in the manufacturer’s wiring diagram.',
    ),
});

const OBD2: Connector = {
  id: 'obd2',
  title: () => L('OBD-II, 16 pin', 'OBD-II, 16-pin'),
  standard: 'SAE J1962 / ISO 15031-3',
  used: () => L('Otomobil ve hafif ticari; bazı yeni kamyonlarda da bulunur.', 'Cars and light commercial vehicles; also on some newer trucks.'),
  layout: [
    ['1', '2', '3', '4', '5', '6', '7', '8'],
    ['9', '10', '11', '12', '13', '14', '15', '16'],
  ],
  layoutNote: () =>
    L(
      'Geniş kenar üstte: üst sıra 1–8, alt sıra 9–16. Soket ve kablo ucu birbirinin aynası olduğundan yönü soketin üzerindeki numaralarla doğrulayın.',
      'Wide edge up: top row 1–8, bottom row 9–16. The socket and the cable end are mirror images, so confirm the direction with the numbers moulded on the socket.',
    ),
  pins: [
    OEM('1'),
    { id: '2', name: () => 'SAE J1850 Bus +', kind: 'serial', detail: () => L('J1850 PWM ve VPW (eski Ford/GM araçlar).', 'J1850 PWM and VPW (older Ford/GM vehicles).') },
    OEM('3'),
    { id: '4', name: () => L('Şasi toprağı', 'Chassis ground'), kind: 'ground', detail: () => L('Gövde toprağı.', 'Body ground.') },
    { id: '5', name: () => L('Sinyal toprağı', 'Signal ground'), kind: 'ground', detail: () => L('Teşhis hatlarının referans toprağı.', 'Reference ground for the diagnostic lines.') },
    { id: '6', name: () => 'CAN High', kind: 'can', detail: () => L('ISO 15765-4 teşhis CAN hattı (yüksek).', 'ISO 15765-4 diagnostic CAN (high).') },
    { id: '7', name: () => L('K hattı', 'K-line'), kind: 'serial', detail: () => 'ISO 9141-2 / ISO 14230-4 (KWP2000).' },
    OEM('8'),
    OEM('9'),
    { id: '10', name: () => 'SAE J1850 Bus −', kind: 'serial', detail: () => L('Yalnız J1850 PWM.', 'J1850 PWM only.') },
    OEM('11'),
    OEM('12'),
    OEM('13'),
    { id: '14', name: () => 'CAN Low', kind: 'can', detail: () => L('ISO 15765-4 teşhis CAN hattı (düşük).', 'ISO 15765-4 diagnostic CAN (low).') },
    { id: '15', name: () => L('L hattı', 'L-line'), kind: 'serial', detail: () => L('ISO 9141-2 / ISO 14230-4 başlatma hattı (çoğu araçta yok).', 'ISO 9141-2 / ISO 14230-4 init line (absent on most vehicles).') },
    { id: '16', name: () => L('Akü +', 'Battery +'), kind: 'power', detail: () => L('Sürekli akü beslemesi (kontaktan bağımsız).', 'Unswitched battery supply.') },
  ],
};

const DEUTSCH9: Connector = {
  id: 'deutsch9',
  title: () => L('Deutsch, 9 pin', 'Deutsch, 9-pin'),
  standard: 'SAE J1939-13',
  used: () => L('Kamyon, otobüs, iş makinesi. Siyah gövde: 250 kbit/s; yeşil gövde: 250 ve 500 kbit/s araçlar.', 'Trucks, buses, construction. Black body: 250 kbit/s; green body: 250 and 500 kbit/s vehicles.'),
  layout: [['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J']],
  layoutNote: () =>
    L(
      'Pinler harfle işaretlidir (I harfi kullanılmaz). Çizim yalnız harfleri gösterir, dairesel yerleşimi değil; harfi soketin üzerinden okuyun.',
      'Pins are lettered (no letter I). The drawing shows the letters, not the circular arrangement; read the letter on the socket.',
    ),
  pins: [
    { id: 'A', name: () => L('Akü − (toprak)', 'Battery − (ground)'), kind: 'ground', detail: () => L('Akü eksi.', 'Battery negative.') },
    { id: 'B', name: () => L('Akü +', 'Battery +'), kind: 'power', detail: () => L('Sürekli akü beslemesi (12 V veya 24 V sistem).', 'Unswitched battery supply (12 V or 24 V system).') },
    { id: 'C', name: () => 'CAN High (J1939)', kind: 'can', detail: () => L('Ana J1939 hattı.', 'Main J1939 bus.'), colour: () => L('Sarı (J1939-11/-15)', 'Yellow (J1939-11/-15)') },
    { id: 'D', name: () => 'CAN Low (J1939)', kind: 'can', detail: () => L('Ana J1939 hattı.', 'Main J1939 bus.'), colour: () => L('Yeşil (J1939-11/-15)', 'Green (J1939-11/-15)') },
    { id: 'E', name: () => L('CAN blendajı', 'CAN shield'), kind: 'shield', detail: () => L('Blendajlı (J1939-11) kablolarda; J1939-15 araçlarda boş olabilir.', 'On shielded (J1939-11) wiring; may be empty on J1939-15 vehicles.') },
    { id: 'F', name: () => 'J1708 +', kind: 'serial', detail: () => L('Eski J1587/J1708 teşhis hattı; yoksa üreticiye bırakılmış.', 'Legacy J1587/J1708 diagnostics; otherwise manufacturer use.') },
    { id: 'G', name: () => 'J1708 −', kind: 'serial', detail: () => L('Eski J1587/J1708 teşhis hattı; yoksa üreticiye bırakılmış.', 'Legacy J1587/J1708 diagnostics; otherwise manufacturer use.') },
    { ...OEM('H'), detail: () => L('Üreticiye bırakılmış; bazı araçlarda ikinci bir CAN hattının yüksek ucu.', 'Manufacturer use; on some vehicles the high side of a second CAN bus.') },
    { ...OEM('J'), detail: () => L('Üreticiye bırakılmış; bazı araçlarda ikinci bir CAN hattının düşük ucu.', 'Manufacturer use; on some vehicles the low side of a second CAN bus.') },
  ],
};

const N2K: Connector = {
  id: 'n2k',
  title: () => 'NMEA 2000 Micro-C',
  standard: 'NMEA 2000 (DeviceNet Micro-C)',
  used: () => L('Tekne omurgası; T-bağlantısından takılır.', 'Boat backbone; plugs into a T-connector.'),
  layout: [['1', '2', '3', '4', '5']],
  layoutNote: () => L('5 pinli dairesel soket; numaralar soketin üzerindedir.', '5-pin circular socket; the numbers are on the socket.'),
  pins: [
    { id: '1', name: () => L('Blendaj', 'Shield'), kind: 'shield', detail: () => L('Kablo blendajı.', 'Cable shield.'), colour: () => L('Çıplak tel', 'Bare wire') },
    { id: '2', name: () => 'NET-S (+12 V)', kind: 'power', detail: () => L('Omurga beslemesi.', 'Backbone supply.'), colour: () => L('Kırmızı', 'Red') },
    { id: '3', name: () => 'NET-C (0 V)', kind: 'ground', detail: () => L('Omurga beslemesinin dönüşü.', 'Backbone supply return.'), colour: () => L('Siyah', 'Black') },
    { id: '4', name: () => 'NET-H (CAN High)', kind: 'can', detail: () => L('250 kbit/s CAN.', '250 kbit/s CAN.'), colour: () => L('Beyaz', 'White') },
    { id: '5', name: () => 'NET-L (CAN Low)', kind: 'can', detail: () => L('250 kbit/s CAN.', '250 kbit/s CAN.'), colour: () => L('Mavi', 'Blue') },
  ],
};

const CONNECTORS: Record<Connector['id'], Connector> = { obd2: OBD2, deutsch9: DEUTSCH9, n2k: N2K };

const KIND: Record<Kind, { tone: Tone; label: () => string; cls: string }> = {
  can: { tone: 'accent', label: () => 'CAN', cls: 'border-accent bg-accent-soft text-accent' },
  power: { tone: 'danger', label: () => L('Besleme', 'Power'), cls: 'border-danger-border bg-danger-soft text-del' },
  ground: { tone: 'neutral', label: () => L('Toprak', 'Ground'), cls: 'border-border-strong bg-bg-row-hover text-text-hi' },
  serial: { tone: 'warn', label: () => L('Eski seri hat', 'Legacy serial'), cls: 'border-warn-border bg-warn-soft text-warn' },
  shield: { tone: 'neutral', label: () => L('Blendaj', 'Shield'), cls: 'border-border-strong text-text-mid' },
  oem: { tone: 'neutral', label: () => L('Üretici', 'Manufacturer'), cls: 'border-dashed border-border-strong text-text-low' },
};

export const PinoutView: React.FC = () => {
  const [cid, setCid] = useState<Connector['id']>('obd2');
  const conn = CONNECTORS[cid];
  const [selected, setSelected] = useState<string>('6');
  const pin = conn.pins.find((p) => p.id === selected) ?? conn.pins.find((p) => p.kind === 'can') ?? conn.pins[0];

  const choose = (id: Connector['id']) => {
    setCid(id);
    setSelected(CONNECTORS[id].pins.find((p) => p.kind === 'can')?.id ?? CONNECTORS[id].pins[0].id);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto" data-testid="pinout-view">
      <div>
        <Segmented
          testId="connector"
          value={cid}
          onChange={choose}
          options={[
            { value: 'obd2', label: OBD2.title() },
            { value: 'deutsch9', label: DEUTSCH9.title() },
            { value: 'n2k', label: N2K.title() },
          ]}
        />
      </div>
      <div className="grid grid-cols-1 content-start gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Card testId="pinout-connector">
          <CardHeader title={conn.title()} hint={`${conn.standard} · ${conn.used()}`} />
          <div className="flex flex-col items-center gap-4 p-6">
            <div
              className={cx(
                'flex flex-col gap-2 border-2 border-border-strong p-4',
                cid === 'obd2' ? 'rounded-b-[2.5rem] rounded-t-xl px-6' : 'rounded-3xl',
              )}
            >
              {conn.layout.map((row, i) => (
                <div key={i} className={cx('flex justify-center gap-2', cid === 'obd2' && i === 1 && 'px-3')}>
                  {row.map((id) => {
                    const p = conn.pins.find((x) => x.id === id);
                    if (!p) return null;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setSelected(id)}
                        aria-pressed={pin.id === id}
                        data-testid={`pin-${id}`}
                        title={p.name()}
                        className={cx(
                          'flex h-10 w-10 items-center justify-center rounded-full border-2 font-mono text-[13px] font-semibold transition-transform',
                          KIND[p.kind].cls,
                          pin.id === id && 'scale-110 ring-2 ring-accent ring-offset-2 ring-offset-bg-card',
                        )}
                      >
                        {id}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
            <p className="max-w-xl text-center text-[12px] text-text-mid">{conn.layoutNote()}</p>
            <div className="flex flex-wrap justify-center gap-1.5">
              {(Object.keys(KIND) as Kind[])
                .filter((k) => conn.pins.some((p) => p.kind === k))
                .map((k) => (
                  <Chip key={k} tone={KIND[k].tone}>
                    {KIND[k].label()}
                  </Chip>
                ))}
            </div>
          </div>
        </Card>

        <div className="flex min-w-0 flex-col gap-3">
          <Card testId="pinout-detail">
            <CardHeader title={`${L('Pin', 'Pin')} ${pin.id} · ${pin.name()}`}>
              <Chip tone={KIND[pin.kind].tone}>{KIND[pin.kind].label()}</Chip>
            </CardHeader>
            <div className="flex flex-col gap-2 px-5 py-4 text-[13px]">
              <p className="text-text-body">{pin.detail()}</p>
              {pin.colour && (
                <p className="text-text-mid">
                  {L('Kablo rengi (standart)', 'Wire colour (standard)')}: <b className="text-text-hi">{pin.colour()}</b>
                </p>
              )}
            </div>
          </Card>
          <Card testId="pinout-all">
            <CardHeader title={L('Tüm pinler', 'All pins')} />
            <ul className="divide-y divide-border-whisper px-5 py-1 text-[12.5px]">
              {conn.pins.map((p) => (
                <li key={p.id}>
                  <button type="button" onClick={() => setSelected(p.id)} className="flex w-full items-center gap-3 py-2 text-left">
                    <span className="w-6 font-mono font-semibold text-text-hi">{p.id}</span>
                    <span className={cx('flex-1', p.kind === 'oem' ? 'text-text-low' : 'text-text-body')}>{p.name()}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <Card testId="pinout-checks">
        <CardHeader
          title={L('Hat kontrolü (multimetre)', 'Bus check (multimeter)')}
          hint={L('ISO 11898-2 yüksek hızlı CAN için tipik değerler.', 'Typical values for ISO 11898-2 high-speed CAN.')}
        />
        <div className="grid grid-cols-1 gap-4 p-5 text-[12.5px] md:grid-cols-3">
          <div className="flex gap-2">
            <Ruler className="mt-0.5 h-4 w-4 flex-none text-accent" />
            <p className="text-text-body">
              <b className="text-text-hi">{L('Direnç, kontak kapalı', 'Resistance, ignition off')}</b>
              <br />
              {L(
                'CAN High – CAN Low arası ≈ 60 Ω (iki adet 120 Ω sonlandırma). ≈ 120 Ω: bir sonlandırma yok ya da hat kopuk. Çok düşük: kısa devre veya fazladan sonlandırma.',
                'CAN High to CAN Low ≈ 60 Ω (two 120 Ω terminations). ≈ 120 Ω: one termination missing or the bus is broken. Much lower: short or extra termination.',
              )}
            </p>
          </div>
          <div className="flex gap-2">
            <Ruler className="mt-0.5 h-4 w-4 flex-none text-accent" />
            <p className="text-text-body">
              <b className="text-text-hi">{L('Gerilim, kontak açık', 'Voltage, ignition on')}</b>
              <br />
              {L(
                'Boşta iki hat da ≈ 2,5 V. Trafik varken multimetre ortalamayı gösterir: CAN High biraz 2,5 V üstünde, CAN Low biraz altında.',
                'Idle, both lines ≈ 2.5 V. With traffic a multimeter shows the average: CAN High slightly above 2.5 V, CAN Low slightly below.',
              )}
            </p>
          </div>
          <div className="flex gap-2">
            <Info className="mt-0.5 h-4 w-4 flex-none text-accent" />
            <p className="text-text-body">
              <b className="text-text-hi">{L('Ölçüm uygulamada değil', 'Not measured by the app')}</b>
              <br />
              {L(
                'Bu değerleri uygulama ölçmez; adaptör yalnız dinler. Ölçümü multimetre ya da osiloskopla siz yaparsınız.',
                'The app does not measure these; the adapter only listens. Use a multimeter or an oscilloscope.',
              )}
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
};
