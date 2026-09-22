# -*- coding: utf-8 -*-
"""core/supervisor.py — supervisa y relanza los agentes (todos MENOS Joker).

Motivación
----------
Joker es el PID 1 del contenedor (su entrypoint es ``agent/run.py``). El resto de
agentes NO arrancan solos al reiniciar Docker. Este módulo resuelve dos cosas:

1. **Comando de reinicio**: arranca/para/reinicia cualquier agente (o todos menos
   Joker) desde ``configAgentes.json`` — sin tocar código por agente.
2. **Auto-arranque**: un *watcher* (proceso ligero con pidfile-lock) que garantiza
   que cada agente definido esté vivo; si se cae, lo relanza. Joker lo lanza al
   arrancar, de modo que un reinicio de Docker levanta Joker → watcher → resto.

Reglas:
  - Nunca gestiona a Joker (``_default`` del JSON): ni lo mata ni lo arranca.
  - Un agente puede excluirse con ``"_supervisar": false`` en su entrada.
  - Servicios extra (p. ej. el traductor) se declaran en ``"servicios_extra"``.

Uso CLI:
  python3 -m core.supervisor --estado        # estado de todos
  python3 -m core.supervisor --reiniciar     # reinicia todos menos Joker
  python3 -m core.supervisor --reiniciar horas_extras
  python3 -m core.supervisor --watch         # watcher (lo lanza Joker solo)
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

try:
    from . import agentconfig as _ac
except Exception:  # noqa: BLE001  (si se importa suelto / ejecutado directo)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import agentconfig as _ac

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../agent
LOCK_FILE = os.path.join(AGENT_DIR, "logs", "supervisor.lock")
WATCH_OUT = os.path.join(AGENT_DIR, "logs", "supervisor.out")
GUARD_FILE = os.path.join(AGENT_DIR, "logs", "guardian.lock")
GUARD_OUT = os.path.join(AGENT_DIR, "logs", "guardian.out")
INTERVALO = float(os.environ.get("TW_SUPERVISOR_INTERVALO", "15") or 15)


# ─────────────────────────────────────────────────────────────── lectura de /proc
def _procesos():
    """Devuelve [(pid, [argv...])] de todos los procesos vivos (lee /proc)."""
    res = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                raw = fh.read()
        except Exception:  # noqa: BLE001 (proceso que desaparece)
            continue
        cmd = [p.decode("utf-8", "replace") for p in raw.split(b"\x00") if p]
        if cmd:
            res.append((int(name), cmd))
    return res


def _pids_agente(agent_id):
    """PIDs del daemon ``run.py --agent <id>`` (NO confunde con Joker, que va sin --agent)."""
    res = []
    for pid, cmd in _procesos():
        if not any("run.py" in c for c in cmd):
            continue
        if "--agent" in cmd and agent_id in cmd:
            res.append(pid)
    return res


def _pids_extra(u):
    """PIDs de un servicio extra, identificado por (cwd real, script).

    No basta con el nombre del script: la consola (5000) y la web social
    (5070) son ambas `app.py` y colisionaban, y el watcher mataba la
    consola como "duplicado". Casamos tupla (cwd normalizado, script).
    """
    cmd_u = (u.get("cmd") or "").split()
    if not cmd_u:
        return []
    script = cmd_u[-1]
    carpeta = u.get("cwd") or u.get("carpeta") or ""
    if carpeta and not os.path.isabs(carpeta):
        carpeta = os.path.join(_ac.repo_root(), carpeta)
    cwd_ref = os.path.realpath(carpeta) if carpeta else None
    res = []
    for pid, cmd in _procesos():
        if not any(c == script or c.endswith("/" + script) for c in cmd):
            continue
        if cwd_ref is None:
            res.append(pid)
            continue
        try:
            cwd = os.path.realpath(f"/proc/{pid}/cwd")
        except Exception:  # noqa: BLE001
            continue
        if cwd == cwd_ref:
            res.append(pid)
    return res


# ─────────────────────────────────────────────────────────── inventario de unidades
def _config():
    with open(_ac.config_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def unidad_agentes(excluir_default=True):
    """Agentes a supervisar (todas las entradas 'agentes' menos Joker/_default)."""
    data = _config()
    ags = data.get("agentes") or {}
    default = data.get("_default")
    res = []
    for aid, e in ags.items():
        if not isinstance(e, dict):
            continue
        if excluir_default and aid == default:
            continue
        if e.get("_supervisar") is False:
            continue
        res.append({"tipo": "agente", "id": aid, "name": e.get("name", aid),
                    "carpeta": e.get("carpeta", ""), "entry": e})
    return res


def unidad_extras():
    data = _config()
    res = []
    for sid, e in (data.get("servicios_extra") or {}).items():
        if not isinstance(e, dict) or e.get("_supervisar") is False:
            continue
        res.append({"tipo": "extra", "id": sid, "name": e.get("name", sid),
                    "cmd": e.get("cmd", ""), "cwd": e.get("cwd", ""),
                    "log": e.get("log", f"logs/{sid}.out")})
    return res


def unidades(excluir_default=True):
    return unidad_agentes(excluir_default) + unidad_extras()


def _pids(u):
    return _pids_agente(u["id"]) if u["tipo"] == "agente" else _pids_extra(u)


def _log_daemon(u):
    """Ruta del log de consola (daemon.out) para una unidad."""
    if u["tipo"] == "agente" and u.get("carpeta"):
        return os.path.join(_ac.repo_root(), u["carpeta"], "logs", "daemon.out")
    return os.path.join(_ac.repo_root(), u.get("log", f"logs/{u['id']}.out"))


# ──────────────────────────────────────────────────────────── arrancar / parar
def arrancar(u):
    """Arranca una unidad si no está viva. Devuelve el PID o None."""
    pids = _pids(u)
    if pids:
        return pids[0]
    log = _log_daemon(u)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    if u["tipo"] == "agente":
        cmd = ["python3", "-u", "run.py", "--agent", u["id"]]
        cwd = AGENT_DIR
    else:
        cmd = u["cmd"].split() if isinstance(u["cmd"], str) else list(u["cmd"])
        cwd = os.path.join(_ac.repo_root(), u.get("cwd", "")) or _ac.repo_root()
    with open(log, "ab") as out:
        out.write(f"\n=== supervisor: arrancando {u['id']} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===\n".encode())
        p = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    return p.pid


def detener(u, timeout=8.0):
    """Para (SIGTERM → SIGKILL) los procesos de una unidad. Devuelve nº matados."""
    n = 0
    for pid in _pids(u):
        try:
            os.kill(pid, signal.SIGTERM)
            n += 1
        except ProcessLookupError:
            pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not _pids(u):
            return n
        time.sleep(0.3)
    for pid in _pids(u):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return n


def reiniciar(u):
    detener(u)
    return arrancar(u)


# ──────────────────────────────────────────────────────────────── watcher
def _pid_vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def _vivo_por_lock(path, marca):
    """Pid del proceso cuyo pidfile indica y cuya cmdline contiene ``marca``."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pid = int((fh.read() or "0").strip())
    except Exception:  # noqa: BLE001
        return 0
    if pid and _pid_vivo(pid):
        for p, cmd in _procesos():
            if p == pid and any(marca in c for c in cmd):
                return pid
    return 0


def watcher_vivo():
    """¿Hay un watcher activo? (pidfile + proceso vivo + es un supervisor)."""
    return _vivo_por_lock(LOCK_FILE, "supervisor")


def guardian_vivo():
    """¿Hay un guardián activo? (vigila y relanza al watcher si este cae)."""
    return _vivo_por_lock(GUARD_FILE, "--guardian")


def _adquirir_lock(path=LOCK_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return os.getpid()


def ciclo(verbose=False):
    """Un ciclo: asegura 1 proceso por unidad (y mata duplicados)."""
    acciones = []
    for u in unidades():
        pids = _pids(u)
        if not pids:
            pid = arrancar(u)
            acciones.append(f"↑ arrancado {u['id']} (pid {pid})")
        elif len(pids) > 1:
            # duplicado (riesgo de conflicto en Telegram): deja el 1º, mata el resto
            for extra in pids[1:]:
                try:
                    os.kill(extra, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            acciones.append(f"⚠ {u['id']}: duplicado, maté {pids[1:]}")
    if verbose:
        for a in acciones:
            print(a, flush=True)
    return acciones


def watch():
    """Bucle del watcher (bounded a 1 instancia vía pidfile-lock).

    Además de mantener vivos a los agentes, garantiza que exista un **guardián**;
    y el guardián garantiza que exista el watcher. Se cubren mutuamente: si
    cualquiera de los dos se cae, el otro lo relanza en <= INTERVALO segundos.
    """
    if watcher_vivo():
        print(f"[supervisor] ya hay un watcher vivo (pid {watcher_vivo()}); salgo.")
        return
    _adquirir_lock(LOCK_FILE)
    print(f"[supervisor] watcher pid {os.getpid()} · intervalo {INTERVALO}s · "
          f"unidades: {[u['id'] for u in unidades()]}", flush=True)
    try:
        g = ensure_guardian()
        print(f"[supervisor] guardián asegurado (pid {g})", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[supervisor] no pude arrancar el guardián: {e}", flush=True)
    while True:
        try:
            ciclo(verbose=True)
            if not guardian_vivo():
                print(f"[supervisor] guardián caído → relanzado (pid {ensure_guardian()})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[supervisor] error en ciclo: {e}", flush=True)
        time.sleep(INTERVALO)


def _limpiar_lock(path=LOCK_FILE, vivo=None):
    """Si el lock no corresponde a un proceso vivo, lo borra (evita pids fantasma)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pid = int((fh.read() or "0").strip())
    except Exception:  # noqa: BLE001
        return
    if pid and not (vivo or watcher_vivo)():
        try:
            os.unlink(path)
        except OSError:
            pass


def _limpiar_lock_obsoleto():
    _limpiar_lock(LOCK_FILE, watcher_vivo)


def _lanzar_detached(flag, out_file, cabecera):
    """Lanza ``core/supervisor.py <flag>`` detached y devuelve el pid."""
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "ab") as out:
        out.write(f"\n=== supervisor: {cabecera} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===\n".encode())
        p = subprocess.Popen([sys.executable, "-u",
                              os.path.join(AGENT_DIR, "core", "supervisor.py"), flag],
                             cwd=AGENT_DIR, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    return p.pid


def ensure_watcher():
    """Arranca el watcher DETACHED si no hay uno vivo. Devuelve el pid del proceso."""
    pid = watcher_vivo()
    if pid:
        return pid
    _limpiar_lock_obsoleto()
    return _lanzar_detached("--watch", WATCH_OUT, "watcher arrancado")


def ensure_guardian():
    """Arranca el guardián DETACHED si no hay uno vivo. Devuelve el pid del proceso."""
    pid = guardian_vivo()
    if pid:
        return pid
    _limpiar_lock(GUARD_FILE, guardian_vivo)
    return _lanzar_detached("--guardian", GUARD_OUT, "guardián arrancado")


def guardian():
    """Bucle del guardián (bounded a 1 instancia vía pidfile-lock).

    Su único trabajo: si el watcher murió, relanzarlo. Así el auto-arranque del
    supervisor es perpetuo, aunque muera el propio watcher.
    """
    if guardian_vivo():
        print(f"[guardian] ya hay un guardián vivo (pid {guardian_vivo()}); salgo.")
        return
    _adquirir_lock(GUARD_FILE)
    print(f"[guardian] pid {os.getpid()} · vigilando el watcher cada {INTERVALO}s", flush=True)
    while True:
        try:
            if not watcher_vivo():
                _limpiar_lock_obsoleto()
                w = ensure_watcher()
                print(f"[guardian] watcher caído → relanzado (pid {w})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[guardian] error: {e}", flush=True)
        time.sleep(INTERVALO)


# ──────────────────────────────────────────────────────────── estado / CLI
def estado():
    filas = []
    for u in unidades():
        pids = _pids(u)
        st = "✅ vivo" if pids else "❌ caído"
        filas.append(f"  - {u['id']:<16} {u['tipo']:<7} {st}  {('pids=' + str(pids)) if pids else ''}")
    w = watcher_vivo()
    filas.append(f"  watcher: {'✅ pid ' + str(w) if w else '❌ no activo'}")
    g = guardian_vivo()
    filas.append(f"  guardián: {'✅ pid ' + str(g) if g else '❌ no activo'}")
    return "\n".join(filas)


def main():
    args = sys.argv[1:]
    if not args or "--ayuda" in args or "-h" in args or "--help" in args:
        print(__doc__)
        print("\n--list        lista unidades gestionadas")
        print("--estado      muestra estado (vivos/caídos + watcher)")
        print("--reiniciar [ID...]  reinicia todos menos Joker, o los ID indicados")
        print("--arrancar [ID...]   arranca los que falten")
        print("--detener  [ID...]   detiene TODAS (o las indicadas) — NO toca a Joker")
        print("--watch       corre el watcher en primer plano (lo lanza Joker solo)")
        print("--guardian    corre el guardián: relanza el watcher si este cae (auto-arranque)")
        return
    if "--list" in args:
        for u in unidades():
            print(f"  - {u['id']:<16} {u['tipo']:<7} {u.get('name', '')}")
        return
    if "--estado" in args:
        print(estado())
        return
    if "--watch" in args:
        watch()
        return
    if "--guardian" in args:
        guardian()
        return
    ids = [a for a in args if not a.startswith("-")]
    objetivo = unidades()
    if ids:
        objetivo = [u for u in objetivo if u["id"] in ids]
        faltan = set(ids) - {u["id"] for u in objetivo}
        if faltan:
            print(f"⚠ no encontrados (o son Joker): {', '.join(sorted(faltan))}")
    if "--detener" in args:
        for u in objetivo:
            print(f"⏹ {u['id']}: {detener(u)} proceso(s)")
    elif "--arrancar" in args:
        for u in objetivo:
            print(f"▶ {u['id']}: pid {arrancar(u)}")
    else:  # por defecto reiniciar
        for u in objetivo:
            print(f"↻ {u['id']}: pid {reiniciar(u)}")
        print("watcher:", ("pid " + str(watcher_vivo())) if watcher_vivo()
              else "no activo (arráncalo con --watch o deja que Joker lo lance)")


if __name__ == "__main__":
    main()
