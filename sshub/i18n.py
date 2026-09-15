"""Bilingual string table (ES primary / EN fallback) with locale autodetect.

Every user-visible string lives here so the UI can switch language at
runtime without restarts. `t()` never raises: a missing key falls back
to English, then to the key itself.
"""

from __future__ import annotations

import locale
import os

STRINGS: dict[str, dict[str, str]] = {
    "es": {
        "app_tagline": "Centro de energía heurístico",
        "armed": "ARMADO",
        "disarmed": "DESARMADO",
        "profile": "Perfil",
        "new_profile": "Nuevo perfil",
        "profile_exists": "Ese perfil ya existe.",
        "confirm_delete": "¿Eliminar perfil '{name}'?",
        "blocked_delete": "Debe existir al menos un perfil.",
        "sec_triggers": "Condiciones de disparo",
        "sec_advanced": "Parámetros avanzados",
        "sec_process": "Proceso vigilado (PID)",
        "sec_telemetry": "Telemetría en vivo",
        "mode_absolute": "Temporizador / Hora programada",
        "mode_idle": "Inactividad de periféricos",
        "mode_monitor": "Monitor apagado / suspendido",
        "mode_process": "Proceso terminado (PID watcher)",
        "mode_network": "Red inactiva (KB/s)",
        "mode_smart": "Smart (puntuación ponderada)",
        "mode_hybrid": "Híbrido (todas las condiciones)",
        "countdown_min": "Cuenta regresiva (min):",
        "at_time": "Hora (HH:MM):",
        "idle_min": "Inactividad (min):",
        "net_threshold": "Umbral de red (KB/s):",
        "debounce": "Tolerancia / Debounce (s):",
        "thermal_max": "Temperatura máxima (°C):",
        "battery_min": "Batería mínima (%):",
        "none_proc": "— ninguno —",
        "action_label": "Acción al disparar",
        "act_shutdown": "Apagar",
        "act_reboot": "Reiniciar",
        "act_sleep": "Suspender",
        "act_hibernate": "Hibernar",
        "arm": "▶  ARMAR MONITOREO",
        "disarm": "■  DESARMAR",
        "menu": "☰ Menú",
        "fullscreen": "⛶  Pantalla completa",
        "exit_fullscreen": "⛶  Salir de pantalla completa",
        "new": "+  Nuevo",
        "delete": "Eliminar",
        "export": "Exportar perfiles…",
        "import": "Importar perfiles…",
        "language": "Idioma",
        "start_minimized": "Iniciar minimizado en bandeja",
        "dry_run": "Modo ensayo (no ejecutar acciones reales)",
        "emergency": "⚠ ACCIÓN INMINENTE",
        "cancel": "✖  CANCELAR",
        "cancel_hint": "Esc también cancela",
        "fired_by": "Disparado por: {src}",
        "overlay_log": "⚠ Overlay: {s}s para cancelar…",
        "aborted": "✖ Acción cancelada por el usuario.",
        "executed": "☠ Ejecutado: {action}",
        "arm_log": "▶ Armado: {name} [{mode}] → {action}",
        "armed_profiles": "▶ Armado: {names}",
        "no_active_profiles": "Ningún perfil activo",
        "watchdog_restarted": "🛡 Watchdog: sensor {sensor} reiniciado",
        "proc_list_err": "⚠ No se pudo listar procesos",
        "disarm_log": "■ Desarmado.",
        "net_val": "⇩ {v} KB/s",
        "smart_score": "🧠 Riesgo {v}%",
        "watching_pid": "Vigilando PID {pid} ({name})",
        "proc_not_found": "Proceso no encontrado: {name}",
        "sensor_err": "⚠ {sensor}: {error}",
        "fallback_used": "Sensor {sensor}: {why} — usando fallback",
        "watchdog": "🛡 Watchdog: sensor {sensor} reiniciado ({n} fallos)",
        "minimized": "Minimizado a la bandeja…",
        "exported": "Perfiles exportados → {path}",
        "imported": "Perfiles importados: {n}",
        "import_err": "Archivo de perfiles inválido.",
        "cpu": "CPU",
        "ram": "RAM",
        "temp": "Temp",
        "batt": "Bat",
        "no_battery": "CA",
        "os_abort_hint": "Último recurso: `shutdown /a` en consola",
        "tray_tip": "Smart Shutdown Hub",
        "tray_show": "Mostrar panel",
        "tray_quit": "Salir",
        "settings": "Ajustes",
        "single_instance": "Ya hay una instancia en ejecución.",
        "bad_time": "Hora inválida, usa HH:MM (ej. 22:30): '{t}'",
        "hub": "Asistente IA (Hub)",
        "countdown_badge":
        "⏱ Tiempo restante en pantalla (esquina superior izquierda)",
        "badge_short": "Contador",
        "clear": "Limpiar",
        "badge_toggle": "⏱ Contador en pantalla: {state}",
        "advice_label": "🤖 Consejo",
        "advice_applied": "Consejo aplicado: {action}",
        "advice_low_batt": "Batería crítica — hibernar",
        "advice_hot": "CPU sobrecalentada — apagar",
        "advice_smart": "Riesgo alto ({score}%) — apagar",
        "advice_busy": "Red activa ({net} KB/s) — continuar",
        "advice_idle": "Inactividad {s}s — suspender",
        "advice_all_ok": "Todo normal — continuar",
        "hub_fallback": "🤖 Hub IA no disponible ({error}) — usando asesor local",
        "bus_drop": "⚠ Cola de eventos llena: {n} descartados",
        "sse_report": "CPU SSE4.2: {ok}",
    },
    "en": {
        "app_tagline": "Heuristic energy center",
        "armed": "ARMED",
        "disarmed": "DISARMED",
        "profile": "Profile",
        "new_profile": "New profile",
        "profile_exists": "That profile already exists.",
        "confirm_delete": "Delete profile '{name}'?",
        "blocked_delete": "At least one profile must exist.",
        "sec_triggers": "Trigger conditions",
        "sec_advanced": "Advanced parameters",
        "sec_process": "Watched process (PID)",
        "sec_telemetry": "Live telemetry",
        "mode_absolute": "Timer / Scheduled time",
        "mode_idle": "Peripheral idle",
        "mode_monitor": "Monitor off / asleep",
        "mode_process": "Process exited (PID watcher)",
        "mode_network": "Idle network (KB/s)",
        "mode_smart": "Smart (weighted score)",
        "mode_hybrid": "Hybrid (all conditions)",
        "countdown_min": "Countdown (min):",
        "at_time": "Time (HH:MM):",
        "idle_min": "Idle (min):",
        "net_threshold": "Network threshold (KB/s):",
        "debounce": "Tolerance / Debounce (s):",
        "thermal_max": "Max temperature (°C):",
        "battery_min": "Min battery (%):",
        "none_proc": "— none —",
        "action_label": "Action when triggered",
        "act_shutdown": "Shutdown",
        "act_reboot": "Reboot",
        "act_sleep": "Sleep",
        "act_hibernate": "Hibernate",
        "arm": "▶  ARM MONITORING",
        "disarm": "■  DISARM",
        "menu": "☰ Menu",
        "fullscreen": "⛶  Fullscreen",
        "exit_fullscreen": "⛶  Exit fullscreen",
        "new": "+  New",
        "delete": "Delete",
        "export": "Export profiles…",
        "import": "Import profiles…",
        "language": "Language",
        "start_minimized": "Start minimized to tray",
        "dry_run": "Dry run (never issue real actions)",
        "emergency": "⚠ ACTION IMMINENT",
        "cancel": "✖  CANCEL",
        "cancel_hint": "Escape also cancels",
        "fired_by": "Fired by: {src}",
        "overlay_log": "⚠ Overlay: {s}s to cancel…",
        "aborted": "✖ Action cancelled by user.",
        "executed": "☠ Executed: {action}",
        "arm_log": "▶ Armed: {name} [{mode}] → {action}",
        "armed_profiles": "▶ Armed: {names}",
        "no_active_profiles": "No active profiles",
        "watchdog_restarted": "🛡 Watchdog: sensor {sensor} restarted",
        "proc_list_err": "⚠ Could not list processes",
        "disarm_log": "■ Disarmed.",
        "net_val": "⇩ {v} KB/s",
        "smart_score": "🧠 Risk {v}%",
        "watching_pid": "Watching PID {pid} ({name})",
        "proc_not_found": "Process not found: {name}",
        "sensor_err": "⚠ {sensor}: {error}",
        "fallback_used": "Sensor {sensor}: {why} — using fallback",
        "watchdog": "🛡 Watchdog: sensor {sensor} restarted ({n} failures)",
        "minimized": "Minimized to tray…",
        "exported": "Profiles exported → {path}",
        "imported": "Profiles imported: {n}",
        "import_err": "Invalid profiles file.",
        "cpu": "CPU",
        "ram": "RAM",
        "temp": "Temp",
        "batt": "Bat",
        "no_battery": "AC",
        "os_abort_hint": "Last resort: `shutdown /a` in console",
        "tray_tip": "Smart Shutdown Hub",
        "tray_show": "Show panel",
        "tray_quit": "Quit",
        "settings": "Settings",
        "single_instance": "An instance is already running.",
        "bad_time": "Invalid time, use HH:MM (e.g. 22:30): '{t}'",
        "hub": "AI assistant (Hub)",
        "countdown_badge": "⏱ On-screen remaining time (top-left corner)",
        "badge_short": "Badge",
        "clear": "Clear",
        "badge_toggle": "⏱ On-screen countdown: {state}",
        "advice_label": "🤖 Advice",
        "advice_applied": "Advice applied: {action}",
        "advice_low_batt": "Critical battery — hibernate",
        "advice_hot": "CPU overheating — shutdown",
        "advice_smart": "High risk ({score}%) — shutdown",
        "advice_busy": "Network busy ({net} KB/s) — keep running",
        "advice_idle": "Idle {s}s — sleep",
        "advice_all_ok": "All nominal — keep running",
        "hub_fallback": "🤖 AI hub unavailable ({error}) — using local advisor",
        "bus_drop": "⚠ Event queue full: {n} dropped",
        "sse_report": "CPU SSE4.2: {ok}",
    },
}

_current = "es"


def detect_language() -> str:
    """Pick ES/EN from env or OS locale. Never raises.

    `locale.getdefaultlocale` was removed in Python 3.13, so it is only
    tried as a best-effort; LC_ALL/LANG are the reliable fallbacks.
    """
    env = os.getenv("SSHUB_LANG", "").lower()
    if env in STRINGS:
        return env
    try:
        loc = locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = os.environ.get("LC_ALL", "") or os.environ.get("LANG", "")
    return "es" if str(loc).lower().startswith("es") else "en"


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in STRINGS else "es"


def t(key: str, **fmt) -> str:
    """Translate with graceful ES → EN → key fallback."""
    txt = STRINGS.get(_current, {}).get(key) or STRINGS["en"].get(key) or key
    try:
        return txt.format(**fmt) if fmt else txt
    except (KeyError, IndexError):
        return txt
