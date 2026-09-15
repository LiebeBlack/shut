# Smart Shutdown Hub — Omniscient Edition (v3)

Gestor de energía heurístico y cronómetro de apagado ultra-avanzado,
diseñado para PCs de bajos recursos (Intel Celeron N4120, 4 GB RAM).
Interfaz "cockpit" en modo oscuro (CustomTkinter), motor de sensores en
hilos daemon, cola de eventos anti-fugas y ejecución segura con ventana
de emergencia de 30 segundos.

![python](https://img.shields.io/badge/python-3.10%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows%2010%2B-blue)
![ci](https://img.shields.io/github/actions/workflow/status/your-user/smart-shutdown-hub/ci.yml?label=CI)

---

## Índice

1. [Novedades v3](#novedades-v3)
2. [Arquitectura](#arquitectura)
3. [Contrato de eventos (event bus)](#contrato-de-eventos)
4. [Módulos](#módulos)
   - [config.py — centro de afinado](#configpy)
   - [events.py — bus anti-fugas](#eventspy)
   - [platform_layer — fallbacks por capacidad](#platform_layer)
   - [sensors — 6 sensores heurísticos](#sensors)
   - [core/smart.py — puntuación ponderada](#coresmartpy)
   - [core/hub.py — Hub de Inteligencia / IA](#corehubpy)
   - [core/engine.py — orquestador + watchdog](#coreenginepy)
   - [core/executor.py — ejecutor seguro](#coreexecutorpy)
   - [core/storage.py — CRUD SQLite](#corestoragepy)
   - [gui — cockpit, overlay, toasts, tray](#gui)
5. [Referencia de configuración](#referencia-de-configuración)
6. [Optimización SSE4.2](#optimización-sse42)
7. [CI/CD — GitHub Actions](#cicd)
8. [Instalación (desarrollo)](#instalación)
9. [Compilar el .exe + instalador](#compilar-el-exe--instalador)
10. [Seguridad](#seguridad)
11. [Notas por plataforma](#notas-por-plataforma)
12. [Verificación (self-test)](#verificación-self-test)

---

## Novedades v3

- 🤖 **Hub de Inteligencia / IA** (`core/hub.py`): asesor local determinista
  siempre disponible + modo remoto opcional (`SSHUB_HUB_URL`) con
  fallback automático. Publica consejos como eventos `ADVICE`; un clic
  en el dashboard aplica la acción recomendada. *Solo asesora, nunca
  ejecuta por sí mismo.*
- 🧮 **SSE4.2**: detección compatible con Windows y afinado adaptativo de
  la cadencia del sensor Smart.
- 📈 **Event bus instrumentado**: contadores `published/consumed/dropped`
  (`EventBus.stats()`), aviso visible en la GUI cuando la cola descarta
  eventos, y nuevo tipo de evento `ADVICE`.
- ⚙️ **CI/CD completo**: `ci.yml` (ruff + compilación + self-test en
  matrix OS×Python) y `build.yml` mejorado (smoke test previo al build,
  Inno Setup vía choco, checksums SHA-256, release automático en tags).
- 🖼 **Icono sin dependencias nativas**: `make_ico.py` cae a PIL puro si
  `cairosvg` no está (CI nunca depende de cairo).

## Arquitectura

```
┌────────────────────────── GUI (Main Thread) ─────────────────────────┐
│  MainWindow (CustomTkinter dark cockpit)                              │
│   Dashboard (riesgo + tiles + consejo IA) · Accordions · Sliders     │
│   Toasts · Menú Ajustes (idioma, dry-run, Hub IA, SSE4.2)            │
│   EmergencyOverlay (30 s, CANCEL + Escape)  ·  Tray (pystray)        │
└──────────────▲────────────────────────────────────────┬──────────────┘
               │ EventType.* (drain 250 ms, cola acotada)│ arm/disarm
┌──────────────┴────────────────────────────────────────▼──────────────┐
│  MonitoringEngine (Daemon Thread + Watchdog)                          │
│   AbsoluteTimer · Idle · Monitor · ProcessWatcher · Network           │
│   ThermalBattery (red de seguridad)  ·  SmartSensor (score ponderado) │
│  IntelligenceHub (asesor local + remoto opcional, thread daemon)      │
└───────────────────────────────────────────────────────────────────────┘
               │ platform_layer (capacidades + fallbacks memorizados)
               ▼
   Win32 ctypes · WMI · comandos nativos de Windows
```

**Anti-fugas:** los sensores y el hub solo **publican** en una
`queue.Queue` acotada (256 eventos, drop-on-full — el más antiguo cae
primero para que la GUI siempre vea el estado más reciente). La GUI
**drena** cada 250 ms (máx. 40 eventos/ciclo) y nunca se bloquea. Cada
hilo duerme entre muestras → CPU ≈ 0 % en reposo.

## Contrato de eventos

| Tipo | Publicador | Payload | Consumidor |
|---|---|---|---|
| `STATE` | sensores / SmartSensor | `source`, más: `remaining_s`, `idle_s`, `off_s`, `down_kbps`, `status`, `temp_c`, `battery_pct`, `plugged`, `score`, `why`, `cpu_pct`, `ram_pct` | GUI (dashboard, tiles) + Hub (`observe`) |
| `OVERLAY` | `ActionExecutor` | `action`, `source`, `profile`, `seconds` | GUI → `EmergencyOverlay` |
| `ABORT` | `ActionExecutor.cancel` | — | GUI (log) |
| `EXECUTED` | `ActionExecutor.finalize` | `action`, `ok` | GUI (log) |
| `LOG` | engine / hub / sensores | `msg` | GUI (telemetría) |
| `ERROR` | engine (watchdog) | `sensor`, `error` | GUI (log) |
| `ADVICE` | `IntelligenceHub` | `action`, `confidence`, `reason`, `origin` (`local`/`ai`) | GUI (dashboard, clic = aplicar) |

`TRIGGER` queda reservado: los sensores llaman al callback del engine
directamente (patrón push, cero latencia).

## Módulos

### config.py
Centro de afinado de rendimiento y heurísticas. Todos los intervalos,
umbrales y rutas viven aquí; no hace falta tocar lógica de sensores para
ajustar la cadencia en CPUs de entrada. Ver [referencia completa](#referencia-de-configuración).

### events.py
`EventBus` — cola acotada thread-safe con:
- `publish(type_, **payload)`: nunca bloquea (drop-on-full).
- `drain(max_items)`: consumo único del GUI.
- `stats()` → `{published, consumed, dropped}` con lock.
- `EventType` enum: `STATE, TRIGGER, OVERLAY, ABORT, EXECUTED, LOG, ERROR, ADVICE`.

### platform_layer
Capa nativa de **Windows** con **cadenas de fallback y memoria de método**
(`_memo`): cada getter prueba métodos de barato a caro y recuerda cuál
funcionó para no repetir rutas pesadas (clave en un Celeron).

| Capacidad | Cadena |
|---|---|
| Idle | `GetLastInputInfo` (Win32) |
| Monitor | `EnumDisplayDevicesW` → `GetSystemMetrics` / `xset q` |
| Térmica | WMI `MSAcpi_ThermalZoneTemperature` → `Win32_TemperatureProbe` |
| Batería | `GetSystemPowerStatus` → `psutil.sensors_battery` |
| Red | `psutil.net_io_counters` |
| Procesos | `psutil.process_iter` → `tasklist`/`ps` |
| Acciones | comandos Windows validados mediante `subprocess` |
| CPU | `cpu_sse4_2()` con fallback seguro |

`probe_capabilities()` se ejecuta una vez al arrancar, queda cacheado y
se vuelca al log.

### sensors
Seis sensores + red de seguridad; todos duermen entre muestras:

1. **AbsoluteTimerSensor** — cuenta regresiva o hora programada `HH:MM`
   (siguiente ocurrencia; valida 24 h). **Dispara una sola vez por armado**:
   al vencer se limpia su deadline para no re-ejecutar la acción cada
   segundo.
2. **IdleSensor** — inactividad real de ratón/teclado (umbral por perfil).
3. **MonitorSensor** — monitor apagado durante `MONITOR_OFF_DEBOUNCE_S`.
4. **ProcessSensor** — vigila un PID por nombre (resolución con backoff
   de 15 s) y dispara si el proceso sale o su CPU queda a ~0 % durante
   `CPU_ZERO_DEBOUNCE_S`.
5. **NetworkSensor** — tráfico de descarga suavizado con EWMA
   (`NET_SMOOTH_ALPHA`); dispara si cae bajo el umbral del perfil durante
   `NET_DEBOUNCE_S`.
6. **ThermalBatterySensor** — red de seguridad siempre activa: térmica
   ≥ umbral del perfil durante `THERMAL_DEBOUNCE_S` → `shutdown`;
   batería ≤ umbral del perfil (por defecto `BATTERY_LOW_PCT`) sin
   cargador durante `BATTERY_DEBOUNCE_S` → `hibernate`.

Todas las excepciones se propagan al engine, que las cuenta, las publica
como `ERROR` y reemplaza el sensor tras 3 fallos (watchdog).

### core/smart.py
Puntuación de riesgo 0-100 con pesos configurables, histéresis
(≥ `SMART_THRESHOLD` durante `SMART_HOLD_S`) y desglose `why` en vivo.
La batería baja es *override* duro → hibernación. El CPU del proceso
vigilado usa un handle `psutil.Process` persistente (el primer sample es
warm-up para no falsear "idle_cpu").

### core/hub.py
Ver [Hub de Inteligencia / IA](#hub-de-inteligencia--ia) más abajo.

### core/engine.py
- `arm(profiles)` construye los sensores (modos `absolute|idle|monitor|
  process|network|smart|hybrid`), elige el perfil smart correcto si hay
  varios, arranca el hilo daemon y limpia el cooldown.
- `_run` hace tick cada `ENGINE_TICK_S`; si `executor.pending` el
  overlay ya tiene el control y no se dispara nada más.
- Watchdog: `SENSOR_MAX_ERRORS` (3) → reemplazo en caliente + `LOG`.
- `disarm(silent)` frena, une el hilo (máx. 3 s) y limpia estado.

### core/executor.py
`request()` valida la acción contra `ACTION_LABELS`, respeta el cooldown
post-cancelación (`TRIGGER_COOLDOWN_S` = 60 s, evita tormentas de
re-disparo de sensores enganchados) y publica `OVERLAY`. `finalize()`
ejecuta de verdad vía `platform_layer` (dry-run = solo log).
`abort_pending_os_shutdown()` es la vía pública de escape (`shutdown /a`).

### core/storage.py
SQLite embebido: conexión por llamada + lock de proceso (sin conexiones
compartidas entre hilos). Tabla `profiles` con migración idempotente
v1→v2 (columna `battery_min`), perfil por defecto auto-sembrado,
export/import JSON con deduplicación por nombre.

### gui
- **main_window.py** — cockpit: dashboard (riesgo + tiles CPU/RAM/Temp/
  Bat + línea de consejo IA clicable), selector de perfil + CRUD,
  accordions (disparo / avanzado / proceso / telemetría), sliders con
  etiqueta en vivo, selector segmentado de acción, botón ARMAR/DESARMAR,
  menú de Ajustes (idioma ES/EN en caliente, iniciar minimizado,
  dry-run, Hub IA, reporte SSE4.2), export/import, console de telemetría
  con auto-trim (400 líneas). Toda la UI es desplazable (rueda, barra o
  PageUp/PageDown/Inicio/Fin), **se reajusta al tamaño de la ventana en
  todo momento** (contenido a ancho completo, sin topes) y soporta
  pantalla completa (F11/⛶, Escape sale).
- **countdown_badge.py** — contador flotante en la **esquina superior
  izquierda** (⏱ MM:SS / HH:MM:SS) con el tiempo restante antes del
  apagado: se activa con el botón ⏱ de la barra superior o desde
  Ajustes; muestra el temporizador armado o la cuenta atrás del overlay
  de emergencia, y se oculta solo al desarmar/salir.
- **overlay.py** — ventana semitransparente, sin bordes, siempre al
  frente, countdown 30 s (se vuelve rojo ≤ 5 s), botón gigante CANCELAR
  + `Escape`, banner 🧪 en modo ensayo. `finalize()` solo al agotarse.
- **toast.py** — notificaciones efímeras abajo-derecha sin robar foco.
- **tray.py** — pystray+PIL (opcionales: sin ellos la app corre y cerrar
  sale). Icono SVG/PIL con fallback dibujado.

## Hub de Inteligencia / IA

`core/hub.py` — capa **consultiva** desacoplada vía event bus:

- **Asesor local** (siempre disponible, determinista, ~0 CPU): mapa
  batería crítica → `hibernate` (95 %), sobrecalentamiento → `shutdown`
  (90 %), riesgo smart ≥ umbral → `shutdown`, red activa → `stay`,
  inactividad prolongada → `sleep`, todo normal → `stay`.
- **Modo remoto opcional** (API REST/SSE-compatible): define
  `SSHUB_HUB_URL` (+ `SSHUB_HUB_KEY` para `Authorization: Bearer`) y el
  hub hará `POST` con `{app, ts, signals, thresholds}` cada
  `HUB_POLL_S` s (thread daemon, timeout `HUB_TIMEOUT_S`, cuerpo máx.
  `HUB_MAX_BODY`). Respuesta esperada:
  ```json
  {"action": "shutdown|hibernate|sleep|reboot|stay",
   "confidence": 0-100,
   "reason": "texto breve"}
  ```
  Cualquier fallo → **fallback automático al asesor local** (se loguea
  una sola vez por corte) y el proceso sigue funcionando.
- Los veredictos se publican como `EventType.ADVICE` deduplicados por
  firma; la GUI los muestra en el dashboard y **un clic aplica la acción
  al selector** (nunca ejecuta por sí mismo — la ejecución siempre pasa
  por el overlay de 30 s).
- Activable desde Ajustes (`hub_enabled`, persistido en settings.json)
  o directamente en el archivo de ajustes.

## Referencia de configuración

Todo en `sshub/config.py` (ajustable sin tocar lógica):

| Constante | Defecto | Significado |
|---|---|---|
| `ENGINE_TICK_S` | 1.0 | heartbeat del engine armado |
| `SENSOR_FAST_TICK_S` / `SENSOR_SLOW_TICK_S` | 1.0 / 10.0 | cadencia sensores rápidos/lentos |
| `GUI_POLL_MS` / `GUI_BATCH_MAX` | 250 / 40 | drain del bus por la GUI |
| `OVERLAY_SECONDS` | 30 | ventana de cancelación |
| `TRIGGER_COOLDOWN_S` | 60 | silencio post-cancelación |
| `SMART_THRESHOLD` / `SMART_HOLD_S` | 70 / 30 | disparo smart (histéresis) |
| `SMART_TICK_S` | 2.0 (3.0 sin SSE4.2) | cadencia telemetría smart |
| `NET_DEBOUNCE_S` / `NET_SMOOTH_ALPHA` | 120 / 0.25 | red (debounce + EWMA) |
| `CPU_ZERO_DEBOUNCE_S` | 30 | proceso a 0 % CPU |
| `THERMAL_DEBOUNCE_S` | 20 | térmica sobre el máximo |
| `BATTERY_LOW_PCT` / `BATTERY_DEBOUNCE_S` | 10 / 15 | red de seguridad de batería |
| `IDLE_POLL_S` / `MONITOR_POLL_S` / `MONITOR_OFF_DEBOUNCE_S` | 5 / 4 / 10 | idle, monitor |
| `HUB_POLL_S` / `HUB_TIMEOUT_S` / `HUB_MAX_BODY` | 5 / 3 / 64 KB | Hub IA |

Variables de entorno: `SSHUB_DRY_RUN=1` (modo ensayo), `SSHUB_LANG`
(es|en), `SSHUB_HUB_URL`, `SSHUB_HUB_KEY`, `SSHUB_OS_TIMEOUT`.

Ajustes de usuario (`settings.json`, escritura atómica): `language`,
`start_minimized`, `dry_run_default`, `hub_enabled`, `network_debounce_s`,
`accent`. Un archivo corrupto se renombra `.corrupt` y se reconstruye.

En Windows, los Intel Celeron, Pentium y Atom activan automáticamente un
perfil de bajo consumo: reduce el sondeo lento y Smart sin afectar la
precisión del temporizador. El resto de equipos usa el perfil estándar.

## Optimización SSE4.2

- **Detección**: `platform_layer.cpu_flags()`/`cpu_sse4_2()` leen
  APIs disponibles en Windows; si no se puede detectar se asume capaz.
  El resultado se reporta en el
  log de arranque y en el menú Ajustes.
- **Afinado adaptativo**: sin SSE4.2 la cadencia del sensor Smart se
  relaja a 3 s para proteger CPUs antiguas; con SSE4.2 se mantiene la
  nominal (2 s).
- **Build**: el empaquetado es **onedir** (carpeta completa en
  `dist/SmartShutdownHub/`): sin auto-extracción a temp, arranque más
  rápido y antivirus más tranquilos.
- El resto de la carga es E/S dormida (hilos con `Event.wait`), no
  instrucciones de punto flotante calientes: el impacto real de SSE4.2
  aquí es arranque y el gate de cadencia, no cómputo por segundo.

## CI/CD

`.github/workflows/ci.yml` — en cada push/PR (rutas de código):

- Matrix **ubuntu-latest / windows-latest × Python 3.11/3.12/3.13**.
- `ruff check` + `py_compile` de todo el paquete.
- **Self-test completo** (`scripts/selftest.py`, 80+ checks) con
  ejecución nativa en Windows;
  `SSHUB_DRY_RUN=1` garantiza que jamás se toca el SO.
- `fail-fast: false` y timeout de 20 min por job.

`.github/workflows/build.yml` — en push a `main`, tag `v*` o `workflow_dispatch`:

1. Instala deps + PyInstaller; genera el icono (PIL, sin cairo).
2. **Smoke test** del código antes de empaquetar.
3. `pyinstaller sshub.spec --clean --noconfirm` → `dist/SmartShutdownHub/`
   (carpeta completa onedir: exe con metadatos de versión y manifiesto DPI
   PerMonitorV2 + `_internal` con libs, temas de customtkinter e icono).
4. Inno Setup vía choco → `installer/SmartShutdownHub-Setup-<versión>.exe`
   (instalador moderno con icono, accesos directos, App Paths y soporte bilingüe).
5. Empaquetado de la carpeta completa en `SmartShutdownHub-Portable.zip`
   + `SHA256SUMS.txt`.
6. Subida de artefactos a GitHub Actions + **publicación automática en GitHub Releases**
   (`softprops/action-gh-release`) con notas generadas.
7. `concurrency` por ref y `timeout-minutes: 30`.

## Instalación (desarrollo)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Dependencias: `customtkinter`, `psutil`, `pystray`, `pillow`, `wmi` y
`pywin32` (opcionales en Python 3.14); `cairosvg` solo para regenerar el
icono desde SVG.

## Compilar el .exe + instalador

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# → dist\SmartShutdownHub\            (carpeta completa, sin consola)
# → dist\SmartShutdownHub-Portable.zip (la carpeta completa en un zip)
# → installer\SmartShutdownHub-Setup-1.0.0.exe (si ISCC está instalado)

iscc scripts\installer.iss
# → installer\SmartShutdownHub-Setup-1.0.0.exe
```

El instalador es por usuario, no requiere administrador y coloca la
aplicación en `%LOCALAPPDATA%\Programs\SmartShutdownHub`.

Al hacer `push` a la rama `main` o empujar un tag `v*`, el workflow de GitHub Actions
genera todos los artefactos (instalador, portable zip, exe y checksums) y publica
la release automáticamente en GitHub.

## Seguridad

- **Dry-run** (`SSHUB_DRY_RUN=1` o toggle en Ajustes) registra en el log
  sin ejecutar nada — también en el overlay (banner 🧪).
- **Ventana de emergencia** de 30 s con CANCELAR gigante + `Esc`.
- **Cooldown** de 60 s tras cancelar: los sensores enganchados no
  re-disparan tormentas.
- **Instancia única** (mutex Win32 / lockfile POSIX, fail-open).
- **Último recurso** en Windows: `shutdown /a` cancela el apagado del SO.
- El Hub IA es consultivo: jamás ejecuta una acción sin pasar por el
  overlay humano.

## Notas por plataforma

- **Windows 10/11**: Win32 vía ctypes; térmica vía WMI (2 rutas);
  batería vía API nativa → psutil; acciones `shutdown`/`rundll32`.
- En otros sistemas operativos, el proceso termina sin inicializar la UI
  ni ejecutar acciones.

## Verificación (self-test)

```bash
SSHUB_DRY_RUN=1 .venv\Scripts\python scripts\selftest.py
# → "ALL CHECKS PASSED" (80+ checks: i18n, settings corruptos,
#   singleton, CRUD+migración SQLite, bus acotado, executor,
#   temporizador fire-once, smart scoring, watchdog real,
#   Hub IA (local + fallback remoto), SSE4.2, y ciclo GUI completo
#   incluyendo overlay, cambio de idioma y aplicar consejo)
```

El self-test usa directorios temporales y `SSHUB_DRY_RUN=1`: es seguro
ejecutarlo en cualquier máquina y es exactamente lo que corre en CI.