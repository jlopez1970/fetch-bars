# Massive Capture Tool — Guía de uso

**Script:** `massive_capture.py` (versión actual: **v01.07**)
**Carpeta:** `~/Desktop/scripts/polygon`

Descarga barras de precios (OHLCV) desde **Polygon.io** (ahora llamado **Massive.com**) para muchos tickers en una sola corrida, y genera automáticamente los archivos filtrados y agregados.

Reemplaza a los cuatro scripts anteriores, que había que correr uno por uno:

| Script anterior | Qué hacía | Ahora lo hace… |
|---|---|---|
| `fetch_spy_bars.py` | Descargaba barras de 2 min de un ticker | Para todos los tickers de una vez |
| `filter_bars.py` | Dejaba solo la primera y la última hora del mercado | Automáticamente |
| `aggregate_bars_v01.02.py` | Convertía barras de 2 min a otro timeframe (ej. 30 min) | Automáticamente |
| `aggregate_to_daily_v01.01.py` | Convertía barras de 2 min a barras diarias | Automáticamente |

Los archivos que genera tienen **el mismo formato JSON** que los scripts anteriores, así que se abren en la app de gráficos sin cambios.

---

## 1. Requisitos (una sola vez)

**Python 3** (ya viene en la Mac). No necesita librerías adicionales.

**Clave de API.** Guárdala como variable de entorno para no escribirla cada vez:

```bash
echo 'export POLYGON_API_KEY="TU_CLAVE_AQUI"' >> ~/.zshrc
source ~/.zshrc
```

**Certificados en la red de Charter** (recomendado):

```bash
pip3 install truststore
```

Esto hace que Python use los certificados del llavero de la Mac. Sin esto puede aparecer el error `CERTIFICATE_VERIFY_FAILED ... Missing Authority Key Identifier`.

---

## 2. Uso rápido

Primero hay que entrar a la carpeta:

```bash
cd ~/Desktop/scripts/polygon
```

**Prueba rápida con un solo ticker:**

```bash
python3 massive_capture.py --tickers SPY --from 2026-09-01 --to 2026-09-25 --out test
```

**Lista completa por defecto (41 tickers) para todo el año:**

```bash
python3 massive_capture.py --from 2026-01-01 --to 2026-09-25 --out Used/2026_full
```

**Lista personalizada, incluyendo el euro/dólar:**

```bash
python3 massive_capture.py --tickers USO GLD SLV TLT VIXY XLE IEF IAU EUR/USD --from 2026-01-01 --to 2026-09-25 --out Used/2026_macro
```

---

## 3. Opciones

### Qué tickers descargar

| Opción | Descripción | Ejemplo |
|---|---|---|
| `--tickers` | Lista de tickers separados por espacio | `--tickers SPY QQQ TSLA` |
| `--tickers-file` | Archivo de texto con tickers (uno por línea, o separados por comas; las líneas con `#` se ignoran). También acepta la exportación de TradingView. | `--tickers-file macro.txt` |
| `--tickers-from` | Usa los tickers que ya existen en una carpeta | `--tickers-from Used/2026/2min` |
| *(ninguna)* | Usa la lista por defecto de 41 tickers, la misma de `Used/2026` | |

**Lista exportada de TradingView:** en TradingView, abre el menú de la lista (el nombre arriba del panel), elige **Export list…** y guarda el `.txt` en la carpeta `polygon`. Luego úsalo directamente:

```bash
python3 massive_capture.py --tickers-file mi_lista.txt --from 2024-09-01 --to 2026-09-26 --out Used/2026_macro --merge
```

El script convierte los símbolos de TradingView al formato de Polygon:

| TradingView | Polygon |
|---|---|
| `NASDAQ:AAPL`, `AMEX:SPY`, `NYSE:BRK.B` | `AAPL`, `SPY`, `BRK.B` |
| `FX:EURUSD`, `OANDA:USDJPY` | `C:EURUSD`, `C:USDJPY` |
| `BINANCE:BTCUSD`, `COINBASE:ETHUSD` | `X:BTCUSD`, `X:ETHUSD` |
| `SP:SPX`, `TVC:VIX` | `I:SPX`, `I:VIX` |
| Datos propios de TradingView (`TVC:US10Y`, `TVC:UKOIL`, `TVC:DXY`) | se omiten, con un aviso al inicio |
| `###Nombre de sección` | se ignora |
| Futuros (`CME_MINI:ES1!`) y datos económicos (`ECONOMICS:…`) | se omiten, con un aviso al inicio |

Los índices (`I:…`), algunos pares cripto como `BTCUSDT` pueden no estar incluidos en tu plan. Si es así, aparecen como `no-data` o `error` en el log, y el resto sigue normal.

**Forex, cripto e índices:** el euro/dólar se puede escribir como `EUR/USD`, `C:EURUSD` o `EURUSD=FX`. Cripto se escribe `X:BTCUSD` e índices `I:SPX`.

### Fechas y timeframe

| Opción | Por defecto | Descripción |
|---|---|---|
| `--from` | 1 de enero del año actual | Fecha de inicio (`AAAA-MM-DD`) |
| `--to` | Hoy | Fecha final (`AAAA-MM-DD`) |
| `--tf` | `2` | Timeframe de descarga, en minutos |
| `--agg` | `30 1440` | Timeframes agregados a generar, en minutos (`1440` = diario). Pon `--agg` solo, sin números, para no generar ninguno. |

Ejemplo con 10 min, 30 min, 1 hora y diario: `--agg 10 30 60 1440`

Cada valor de `--agg` debe ser múltiplo de `--tf`, o `1440`.

### Filtro de horario (hora de Nueva York)

| Opción | Por defecto | Descripción |
|---|---|---|
| `--open-start` / `--open-end` | `09:30` / `10:30` | Ventana de la apertura |
| `--close-start` / `--close-end` | `15:00` / `16:00` | Ventana de cierre (*power hour*) |
| `--no-filter` | — | No generar el archivo filtrado |

### Salida y conexión

| Opción | Por defecto | Descripción |
|---|---|---|
| `--out` | `Captures` | Carpeta donde se guarda todo |
| `--key` | variable `POLYGON_API_KEY` o `MASSIVE_API_KEY` | Clave de API |
| `--calls-per-min` | `5` | Llamadas por minuto: `5` para el plan gratis, `0` para no limitar (plan pagado) |
| `--base-url` | `https://api.polygon.io` | Se puede cambiar a `https://api.massive.com` |
| `--merge` | — | Unir la descarga con los archivos existentes del ticker en un solo archivo (sección 6) |
| `--merge-only` | — | Solo unir los archivos existentes, sin descargar |
| `--force` | — | Volver a descargar y regenerar todo, aunque ya exista |
| `--no-indicators` | — | No agregar RSI/MACD (por defecto se agregan después de cada ticker) |
| `--insecure` | — | Desactiva la verificación SSL. **Último recurso**, solo si falla todo lo demás. |

Para ver la ayuda completa o la versión instalada:

```bash
python3 massive_capture.py --help
python3 massive_capture.py --version      # → massive_capture.py v01.07
```

---

## 4. Qué genera

Con `--out Used/2026_macro`, por ejemplo:

```
Used/2026_macro/
├── 2min/                                   ← barras originales descargadas
│   ├── SPY_2min_20260102_0900__20260924_2358.json
│   ├── C_EURUSD_2min_20260101_2200__20260924_2358.json
│   └── filtered/                           ← solo apertura + power hour
│       └── SPY_2min_20260102_0900__20260924_2358_filtered.json
├── 30min/                                  ← agregado a 30 minutos
│   └── SPY_30min_20260102_0900__20260924_2358.json
├── 1day/                                   ← una barra por día
│   └── SPY_1day_20260102_0900__20260924_2358.json
└── capture_log.json                        ← resumen de la corrida
```

### Nombre de los archivos

`TICKER_TIMEFRAME_INICIO__FIN.json`

- **INICIO** y **FIN** son la primera y la última barra recibida, en formato `AAAAMMDD_HHMM` y en **hora UTC** (no hora de Nueva York).
- En forex, el `:` se cambia por `_` en el nombre (`C:EURUSD` → `C_EURUSD`), porque macOS no maneja bien los dos puntos. Dentro del JSON, el ticker sigue siendo `C:EURUSD`.

### Contenido de cada archivo JSON

```json
{
  "ticker": "SPY",
  "timeframe": 2,
  "from": "2026-01-01",
  "to": "2026-09-25",
  "firstBar": "2026-01-02T09:00:00+00:00",
  "lastBar": "2026-09-24T23:58:00+00:00",
  "savedAt": "...",
  "bars": [
    {"t": 1767344400000, "o": 683.9, "h": 686.87, "l": 679.82, "c": 682.97, "v": 88188559, "vw": 682.96, "n": 1250817}
  ]
}
```

| Campo | Significado |
|---|---|
| `t` | Hora de inicio de la barra, en milisegundos Unix (UTC) |
| `o` / `h` / `l` / `c` | Apertura / máximo / mínimo / cierre |
| `v` | Volumen |
| `vw` | Precio promedio ponderado por volumen (VWAP) |
| `n` | Número de transacciones |

Todos los archivos incluyen `toolVersion`, la versión del script que los generó. Los archivos filtrados agregan el campo `filterWindows`, y los agregados agregan `aggregatedFrom`. En los archivos diarios, `timeframe` es `"1D"`.

### Cómo se calculan los agregados

- **Apertura:** la de la primera barra del grupo. **Cierre:** la de la última. **Máximo y mínimo:** los extremos del grupo. **Volumen:** la suma. **VWAP:** el promedio ponderado por volumen.
- Los grupos se arman por **día de trading en hora de Nueva York** y nunca cruzan de un día a otro.
- Los agregados se calculan con **todas** las barras de 2 min, incluidos pre-market y after-hours, no con el archivo filtrado.

### `capture_log.json`

Incluye la versión del script (`toolVersion`) y muestra, por cada ticker, el estado (`ok`, `cached`, `no-data` o `error`), cuántas barras se descargaron, cuántas llamadas a la API se usaron y qué archivos se crearon.

---

## 5. Comportamiento importante

- **Reanudable:** si vuelves a correr el mismo comando, se saltan los tickers que ya están descargados para las mismas fechas y solo se regenera lo que falte. Si la corrida se interrumpe (Ctrl-C, error de red, etc.), basta con correrla otra vez.
- **Límite de la API:** en el plan gratis (5 llamadas por minuto), el script espera automáticamente cuando hace falta. Una corrida de 41 tickers tarda unos 15–20 minutos.
- **Reintentos:** ante errores 429, 5xx o de red, reintenta hasta 5 veces por llamada.
- **Un error no detiene todo:** si falla un ticker, sigue con el siguiente. Al final muestra la lista de los que necesitan atención. La única excepción son los errores de certificado SSL, que detienen la corrida porque afectarían a todos los tickers.
- **Escritura segura:** cada archivo se escribe primero como `.part` y luego se renombra, así que nunca quedan archivos a medio escribir.
- **Datos del día actual:** en el plan gratis normalmente no están disponibles, por lo que los datos llegan hasta el día anterior.

### Notas sobre forex (EUR/USD)

- Se opera casi 24 horas, de domingo en la tarde a viernes en la tarde. El archivo de 2 min es 3–4 veces más grande que el de una acción.
- Las barras diarias se cortan a la **medianoche de Nueva York**. Muchas plataformas de forex cortan a las **5 pm de Nueva York**, así que la apertura y el cierre diarios pueden diferir un poco.
- El archivo filtrado muestra el EUR/USD durante la apertura y el cierre del mercado de acciones de EE. UU.
- Si aparece un error 403 o *"not authorized"*, tu plan no incluye datos de divisas.

---

## 6. Unir datos (merge)

Por defecto, cada rango de fechas genera **archivos separados**. Por ejemplo, si ya tienes 2026 y descargas 2025, quedan dos archivos de SPY.

Con `--merge`, el script descarga el rango nuevo y lo **une con los archivos que ya existen** de ese ticker en la carpeta `--out`, dejando **un solo archivo continuo** por ticker:

```bash
python3 massive_capture.py --from 2025-01-01 --to 2025-12-31 --out Used/2026_full --merge
```

Resultado para SPY:

```
Used/2026_full/2min/SPY_2min_20250102_0900__20260924_2358.json   ← 2025 + 2026 en un archivo
Used/2026_full/_replaced/2min/SPY_2min_20260102_0900__20260924_2358.json   ← archivo anterior
```

- Elimina las barras duplicadas. Si un rango se traslapa, se queda la barra recién descargada.
- Regenera el archivo filtrado y los agregados (30 min, diario) con el rango completo.
- **No borra nada:** los archivos anteriores (y sus filtrados y agregados) se mueven a `_replaced/`. Cuando confirmes que todo está bien, puedes borrar esa carpeta.
- Si el rango pedido ya está cubierto por un archivo existente, no descarga nada.
- El JSON unido incluye el campo `mergedFrom`, con los archivos y descargas que se combinaron.

**Unir archivos que ya tienes, sin descargar:** si en una carpeta ya hay varios archivos del mismo ticker, `--merge-only` los consolida sin usar la API. Si no le pasas tickers, usa todos los que encuentre en la carpeta.

```bash
python3 massive_capture.py --out Used/2026_full --merge-only
```

**Importante:** el merge solo combina archivos que estén en la **misma carpeta `--out`** y con el **mismo `--tf`**. Para unir con datos de otra carpeta, primero copia esos archivos de 2 min a `<out>/2min/`.

---

## 7. Errores comunes

| Error | Solución |
|---|---|
| `no API key` | Define `POLYGON_API_KEY` (sección 1) o usa `--key`. |
| `CERTIFICATE_VERIFY_FAILED` | Ejecuta `pip3 install truststore`. Como último recurso, usa `--insecure`. |
| `HTTP 403` / `NOT_AUTHORIZED` | Tu plan no incluye ese ticker o ese rango de fechas. |
| `no-data` en el log | El ticker no existe o no tuvo operaciones en esas fechas. |
| `--agg values must be multiples...` | Cada valor de `--agg` debe ser múltiplo de `--tf`, o `1440`. |

---

## Historial de versiones

El nombre del archivo es siempre `massive_capture.py`. La versión está dentro del script (`__version__`) y se consulta con `--version`.

| Versión | Cambios |
|---|---|
| v01.00 | Primera versión: une los 4 scripts, descarga varios tickers, es reanudable y guarda un log. |
| v01.01 | Corrige el error de certificados SSL en redes corporativas (`truststore` y `--insecure`). |
| v01.02 | Soporte para forex, cripto e índices (`EUR/USD`, `C:EURUSD`, `X:BTCUSD`, `I:SPX`). |
| v01.03 | `--merge` y `--merge-only`: un solo archivo continuo por ticker; los archivos anteriores se mueven a `_replaced/`. |
| v01.04 | Nombre fijo `massive_capture.py`, opción `--version` y campo `toolVersion` en el log y en cada archivo JSON. |
| v01.05 | `--tickers-file` acepta listas exportadas de TradingView (quita prefijos de bolsa, convierte FX, cripto e índices, ignora secciones y omite futuros). `--merge-only` ya no muestra fechas de descarga. |
| v01.06 | Los símbolos `TVC:` que solo existen en TradingView (bonos, petróleo, DXY) se omiten con aviso; `TVC:VIX` sigue convirtiéndose a `I:VIX`. |
| v01.07 | RSI y MACD se agregan automáticamente después de cada captura o `--merge` (usa `add_indicators.py`, que debe estar en la misma carpeta). Antes, un `--merge` dejaba las barras nuevas sin indicadores y reconstruía 30min/1day sin ellos. `--no-indicators` desactiva este paso. |

---

## Indicadores: `add_indicators.py` (v01.01)

Agrega **RSI** y **MACD** a cada barra de los JSON (no necesita librerías adicionales). Cada barra recibe cuatro campos nuevos; el resto del archivo no cambia, así que la app de gráficos y `--merge` siguen funcionando.

| Campo | Qué es |
|---|---|
| `rsi` | RSI de Wilder (14 por defecto), 2 decimales |
| `macd` | EMA(12) − EMA(26) |
| `macd_signal` | EMA(9) de la línea MACD |
| `macd_hist` | `macd` − `macd_signal` |

```bash
cd ~/Desktop/scripts/polygon
python3 add_indicators.py Used/2026_macro                      # toda la carpeta, en el mismo archivo
python3 add_indicators.py Used/2026_macro/2min/SPY_2min_*.json # solo un archivo
python3 add_indicators.py Used/2026_macro --rsi 9 --macd 8 21 5
python3 add_indicators.py Used/2026_macro --out Used/2026_macro_ind   # escribir copias en otra carpeta
```

- Mismas fórmulas que `chart_v02` / `chart_v03` (calcRSI / calcEMA / calcMACD); los valores coinciden con la gráfica (`stockmarket/chart_current.html`).
- Se calcula solo con barras del horario regular (09:30–16:00 ET), de forma continua entre días. Las barras de pre/post-market quedan en `null` (`--all-hours` para incluirlas). Los archivos diarios usan todas las barras.
- Los archivos `_filtered` copian los valores del archivo completo de 2 min (calcularlos sobre el filtrado daría valores falsos por los huecos).
- El MACD se guarda con 6 decimales (8 si el precio es < 10). La gráfica lo redondea a 4, por eso en EUR/USD muestra 0.
- `--chart-mode` reinicia cada día usando solo el día anterior como calentamiento, igual que la gráfica (en 2 min el resultado es prácticamente idéntico).
- Desde `massive_capture.py` v01.07 ya no hace falta correrlo después de una captura o `--merge`: el script lo hace solo. Sigue sirviendo para otros periodos (`--rsi 9 --macd 8 21 5`) o para archivos creados de otra forma.
- v01.01: al pasar una carpeta completa, ignora las subcarpetas que empiezan con `_` (`_replaced`, `_backup…`).

---

## Datos de 1 segundo alrededor de eventos: `event_seconds_capture.py` (v01.01)

Descarga barras de **1 segundo** solo en ventanas cortas alrededor de los **picos de volumen** (una barra de 2 min con volumen muy alto y un movimiento grande de precio). Sirve para ver qué ticker se mueve primero dentro de esos 2 minutos, sin bajar años de datos por segundo. Usa la clave, el límite de llamadas y el arreglo SSL de `massive_capture.py`, así que debe estar en la misma carpeta.

- Eventos: `Used/2026_macro/analysis/volume_burst_events.csv` (9,912 eventos de 39 tickers, sep 2024 – sep 2026).
- Ventana: 2 min antes del evento hasta 3 min después de la barra de 2 min (7 min en total). Las ventanas que se enciman el mismo día se unen.
- Salida: `Used/2026_seconds/1sec/<TICKER>/<TICKER>_1sec_<AAAAMMDD>.json` (formato normal + `windows` + `events`), `events_used.csv` y `capture_log.json`. Si se interrumpe, se vuelve a correr y salta lo que ya existe.

Solo contar ventanas y llamadas (no descarga nada):

```bash
cd ~/Desktop/scripts/polygon
python3 event_seconds_capture.py --dry-run
```

Prueba con los 50 picos más grandes de QQQ (150 llamadas):

```bash
python3 event_seconds_capture.py --leaders QQQ --top 50 --tickers QQQ SOXL VIXY
```

Todo (plan pagado, ~8,900 llamadas):

```bash
python3 event_seconds_capture.py --calls-per-min 0
```

Si el plan no incluye datos por segundo (error `NOT_AUTHORIZED`), usar barras de 1 minuto con `--res 1m` (se guardan en `1min/`).

| Opción | Por defecto | Descripción |
|---|---|---|
| `--leaders` | QQQ SPY SOXL VIXY EUR/USD YINN SOXX | Tickers cuyos picos de volumen definen las ventanas |
| `--tickers` | los mismos + SLV GLD | Tickers de los que se bajan barras de 1 s |
| `--top` | 0 (todos) | Solo los N picos con más volumen por líder |
| `--from` / `--to` | — | Filtrar eventos por fecha |
| `--before` / `--after` | 120 / 180 | Segundos antes / después |

Los datos por segundo pueden requerir un plan pagado de Polygon/Massive; si el plan no los incluye, la primera llamada da error o no trae datos.
