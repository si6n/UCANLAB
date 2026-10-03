# NOTICE — canboat (intake kopyası)

`data/intake/pgn/` altındaki `pgn_layout` kayıtları **CANboat** projesinin
Apache-2.0 lisanslı J1939 PGN alan düzeni dosyalarından **birebir** alıntılanmıştır.
Apache-2.0, bu türev kullanım için NOTICE korunmasını zorunlu kılar; bu dosya
`data/licenses/NOTICE.canboat` ile aynı metni taşır ve intake kuyruğunun atıf
yükümlülüğünü yerine getirir.

    CANboat
    (C) 2009-2026, Kees Verruijt, Harlingen, The Netherlands.
    Licensed under the Apache License, Version 2.0.

    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

## Kaynak ve kanıt

| Alan | Değer |
|---|---|
| Repo | `canboat/canboat` |
| Pinli commit | `f7f088b49d58f5b4a0feb9b29c288b0ae18a7880` |
| Kaynak yol | `database/j1939/pgns/*.yaml` (85 dosya, 65.882 bayt) |
| Staged kayıt | 84 (DM1 `065226-activeTroubleCodes.yaml` zaten vendor edildiği için dışarıda bırakıldı) |
| Üreten araç | `scripts/intake_scan_sources.py --stage --apply` |
| Kayıt başına kanıt | `source.snapshot.sha256` + `bytes` (upstream dosyanın birebir özeti) |
| Satır kanıtı | `data/intake/MANIFEST.md` (kayıt özeti) |
| Tarama raporu | `docs/audit/intake_source_scan_2026-10-02.md` |

Alan adları, `description` metinleri, bit genişlikleri, birim ve çözünürlük
değerleri birebir kopyalanmıştır; **hiçbir alan uydurulmamış, düzeltilmemiş veya
zenginleştirilmemiştir**. `pgn_layout` kayıtları `data/diagnostics/` içine
**yazılmamıştır**; terfi kararı `data/intake/README.md` § "Entegrasyon adımları"
ile ayrıdır.
