# Kaynak bağlantı canlılığı (link liveness)

- Taranan: **200** URL (toplam alıntılanan: 9695)
- Sonuç: `{'live': 135, 'unverified': 18, 'dead': 13, 'redirect': 34}` · süre 28.4 s

## Ölü bağlantılar

| Durum | Alıntı | URL |
|---|---|---|
| HTTP 404 | 3 | `https://www.nmea.org/Assets/20190613%20windlass%20amendment` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3153` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3339` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P1954` |
| HTTP 404 | 1 | `https://carberry.pro/obd/U0372` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P11D7` |
| HTTP 500 | 1 | `https://j1939hub.com/general-network/spn-1387-fmi-0/` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P104A` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3368` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3374` |
| HTTP 500 | 1 | `https://j1939hub.com/general-network/spn-523002-fmi-0/` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3242` |
| HTTP 404 | 1 | `https://carberry.pro/obd/P3116` |

## Doğrulanamayan (403/405/429 ve yönlendirme — ölü demek değil)

| Durum | Alıntı | URL |
|---|---|---|
| HTTP 403 | 6 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2017-cascadia-dd15-diagnosing-spn-3510-and-spn-723-engine-codes` |
| HTTP 403 | 6 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2011-western-star-4900-dd15-spn-3597-fmi-4-proportional-valve-issue` |
| HTTP 429 | 5 | `https://web.archive.org/web/20170609110901/https://www.nmea.org/Assets/20160715%20corrigenda%20entertainment%20pgns%20.pdf` |
| HTTP 403 | 5 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2014-freightliner-cascadia-dd15-spn-70-fmi-19-engine-code-explained` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2017-volvo-diagnosing-spn-5747-fmi-12-and-spn-5835-fmi-2-codes` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2013-peterbilt-386-paccar-12-9l-diagnosing-sa49-spn-77-fmi-3-code` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2020-freightliner-m2-cummins-understanding-spn-4375-fmi-2-spn-4331-fmi-16-codes` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2007-international-8600-electrical-issues-and-egr-code-spn-27-fix` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2017-peterbilt-px9-diagnosing-spn-3222-fmi-5-engine-code` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/maxxforce-11-13-2010-2012-crank-no-start-spn-636-fmi-10-issue` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2019-international-lonestar-ac-issue-diagnosing-spn-2033-and-spn-2058-codes` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2011-international-4300-diagnosing-spn-158-fmi-17-engine-stall-issue` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2013-international-4000-injector-coil-fault-codes-spn-3654-3659` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2014-entegra-aspire-cummins-450-spn-157-fmi-18-fault-code-issue` |
| HTTP 403 | 4 | `https://www.justanswer.com/medium-and-heavy-truck/topics/freightliner-cascadia-2016-engine-derate-codes-spn-4814-fmi-4-spn-3480-fmi-14` |
| HTTP 403 | 2 | `https://www.justanswer.com/medium-and-heavy-truck/topics/2014-caterpillar-ct660-spn-111-fmi-31-engine-diagnostic-issue` |
| HTTP 403 | 2 | `https://www.justanswer.com/medium-and-heavy-truck/topics/international-lt625-2022-spn1328-and-spn157-fault-code-issues` |
| HTTP 308 | 1 | `https://obd2hub.com/en/P021A.html` |
| HTTP 308 | 1 | `https://openlaborproject.com/dtc-codes/p3801/` |
| HTTP 308 | 1 | `https://openlaborproject.com/dtc-codes/b0110/` |
| HTTP 308 | 1 | `https://openlaborproject.com/dtc-codes/p1493/` |
| HTTP 308 | 1 | `https://truckfaultcode.com/fault-codes/navistar-aware-spn-706-fmi-13-analog-input-6-out-of-calibration-grey-connector-pin-6` |
| HTTP 308 | 1 | `https://openlaborproject.com/dtc-codes/u0237/` |
| HTTP 308 | 1 | `https://obd2hub.com/en/B1371.html` |
| HTTP 308 | 1 | `https://openlaborproject.com/dtc-codes/c0245/` |
