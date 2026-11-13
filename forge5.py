"""
Forge5 - atelier de developpement homebrew PS5.

Lance un petit serveur local (127.0.0.1 uniquement) et ouvre l'interface
dans sa propre fenetre. Aucune dependance externe : Python 3.10+ suffit.

    py forge5.py              # demarre l'atelier
    py forge5.py --navigateur # dans un onglet du navigateur
    py forge5.py --no-open    # sans rien ouvrir

(c) 2026 cedsaill
"""
import argparse
import json
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

from debugger import Debugger, DebugError
from simulator import Simulator
import i18n
from payloads import PayloadManager
from launcher5 import Launcher5

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
TEMPLATES_DIR = ROOT / "templates"

DEFAULT_CONFIG = {
    "ps5_ip": "192.168.1.50",
    "elf_port": 9021,       # ps5-payload-elfldr
    "klog_port": 3232,      # klogsrv
    "sdk_path": "",         # dossier ps5-payload-sdk (PS5_PAYLOAD_SDK)
    "workspace": "",            # vide = dossier « projets » a cote de Forge5
    "build_command": "make",
    "gdb_path": "gdb-multiarch",  # GDB 15+ (ou "wsl gdb-multiarch")
    "gdb_port": 2159,           # gdbsrv
    "gdb_remote_dir": "/user/temp",  # ou l'ELF est copie sur la PS5
    # Mode sans PS5 (simulation sur PC, via WSL sous Windows)
    "target": "ps5",            # "ps5" ou "pc"
    "lang": "",                 # langue de l'interface ("" = celle du navigateur)
    "payloads_auto_update": True,  # mise a jour automatique des payloads
    "sim_cc": "wsl gcc",
    "sim_cxx": "wsl g++",
    "sim_gdb": "wsl gdb-multiarch",
    "sim_gdb_port": 2160,
    "ftp_port": 2121,           # ftpsrv (gestion de Launcher5)
}

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
_cfg_lock = threading.Lock()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg):
    with _cfg_lock:   # ecriture sure : fichier temporaire puis remplacement
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)


CONFIG = load_config()

# --------------------------------------------------------------------------- #
# Bus d'evenements (diffuse vers l'interface via Server-Sent Events)
# --------------------------------------------------------------------------- #
_subscribers = []
_sub_lock = threading.Lock()


def emit(channel, text="", **extra):
    """channel: build | ps5 | klog | system | status | diag"""
    text = i18n.tr(text, CONFIG.get("lang") or "fr")
    event = {"channel": channel, "text": text, "t": time.time(), **extra}
    with _sub_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


# --------------------------------------------------------------------------- #
# Projets
# --------------------------------------------------------------------------- #
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
EDITABLE = {".c", ".h", ".cpp", ".hpp", ".txt", ".md", ".mk", ".cmake", ".json", ".s", ".S"}


def workspace():
    ws = Path(CONFIG["workspace"]) if CONFIG.get("workspace") else ROOT / "projets"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def project_dir(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("Nom de projet invalide")
    p = workspace() / name
    if not p.is_dir():
        raise FileNotFoundError(f"Projet introuvable : {name}")
    return p


def safe_file(project, rel):
    base = project_dir(project).resolve()
    target = (base / rel).resolve()
    if base not in target.parents and target != base:
        raise PermissionError("Chemin hors du projet")
    return target


def list_projects():
    out = []
    for p in sorted(workspace().iterdir()):
        if p.is_dir() and NAME_RE.match(p.name):
            files = [
                str(f.relative_to(p)).replace("\\", "/")
                for f in sorted(p.rglob("*"))
                if f.is_file() and (f.suffix in EDITABLE or f.name == "Makefile")
            ]
            out.append({"name": p.name, "files": files, "elf": find_elf(p)})
    return out


def list_templates():
    return sorted(t.name for t in TEMPLATES_DIR.iterdir() if t.is_dir())


def create_project(name, template):
    if not NAME_RE.match(name or ""):
        raise ValueError("Nom invalide (lettres, chiffres, - et _ uniquement)")
    src = TEMPLATES_DIR / template
    if not src.is_dir():
        raise FileNotFoundError("Modele introuvable")
    dst = workspace() / name
    if dst.exists():
        raise FileExistsError("Ce projet existe deja")
    shutil.copytree(src, dst)
    # Remplace le nom du modele par celui du projet
    for f in dst.rglob("*"):
        if f.is_file() and (f.suffix in EDITABLE or f.name == "Makefile"):
            txt = f.read_text(encoding="utf-8")
            f.write_text(txt.replace("__PROJECT__", name), encoding="utf-8")
    emit("system", f"Projet « {name} » cree a partir du modele « {template} ».")


def find_elf(pdir):
    elfs = sorted(Path(pdir).rglob("*.elf"), key=lambda f: f.stat().st_mtime, reverse=True)
    return str(elfs[0].relative_to(pdir)).replace("\\", "/") if elfs else None


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #
# clang : "main.c:12:5: error: use of undeclared identifier 'x'"
DIAG_RE = re.compile(r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+):(?P<col>\d+):\s*(?P<sev>error|warning|note):\s*(?P<msg>.*)$")

_busy = threading.Lock()


def build(project):
    pdir = project_dir(project)
    env = os.environ.copy()
    if CONFIG["sdk_path"]:
        env["PS5_PAYLOAD_SDK"] = CONFIG["sdk_path"]
    elif "PS5_PAYLOAD_SDK" not in env:
        emit("build", "⚠ Chemin du SDK non defini (Parametres). La compilation risque d'echouer.", level="warn")

    cmd = CONFIG["build_command"]
    argv = shlex.split(cmd, posix=os.name != "nt")
    # SDK installe dans Ubuntu (WSL) : les variables Windows n'y passent pas,
    # on transmet donc le chemin du SDK directement a make.
    if CONFIG["sdk_path"] and argv and Path(argv[0]).name.lower() in ("wsl", "wsl.exe") \
            and "make" in argv and not any(a.startswith("PS5_PAYLOAD_SDK=") for a in argv):
        argv.append(f"PS5_PAYLOAD_SDK={CONFIG['sdk_path']}")
    emit("build", f"$ {' '.join(argv)}   (dans {pdir.name}/)", level="cmd")
    started = time.time()
    rc, errors, warnings = stream_command(project, argv, pdir, env)
    if rc is None:
        emit("build", f"✖ Commande introuvable : « {cmd.split()[0]} ». Installe make / le SDK, ou change la commande dans Parametres.", level="error")
        return False
    dt = time.time() - started
    if rc == 0:
        elf = find_elf(pdir)
        emit("build", f"✔ Compilation reussie en {dt:.1f}s"
                      f"{f' → {elf}' if elf else ''} ({warnings} avertissement(s))", level="ok")
        return True
    emit("build", f"✖ Echec de la compilation (code {rc}) : "
                  f"{errors} erreur(s), {warnings} avertissement(s)", level="error")
    return False


UNDEF_RE = re.compile(r"undefined reference to [`'](\w+)'")


def stream_command(project, argv, cwd, env=None):
    """Lance une commande de compilation et diffuse sa sortie, en reperant les
    erreurs du compilateur. Renvoie (code, erreurs, avertissements), code=None si
    la commande est introuvable."""
    errors = warnings = 0
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return None, 0, 0
    seen_undef = set()
    for line in proc.stdout:
        line = line.rstrip("\n")
        m = DIAG_RE.match(line)
        if m:
            sev = m["sev"]
            errors += sev == "error"
            warnings += sev == "warning"
            emit("diag", line, project=project, file=m["file"].replace("\\", "/"),
                 line=int(m["line"]), col=int(m["col"]), severity=sev, msg=m["msg"])
            emit("build", line, level=sev, file=m["file"].replace("\\", "/"), line_no=int(m["line"]))
            continue
        emit("build", line)
        u = UNDEF_RE.search(line)
        if u and u[1] not in seen_undef:
            seen_undef.add(u[1])
            errors += 1
            if CONFIG.get("target") == "pc":
                emit("build", f"💡 « {u[1]} » est une fonction PS5 que le mode PC ne simule pas encore. "
                              f"Entoure son appel de #ifndef __FORGE5_SIM__ … #endif, ou teste sur la PS5.",
                     level="warn")
    proc.wait()
    return proc.returncode, errors, warnings


# --------------------------------------------------------------------------- #
# Envoi a la PS5 (ps5-payload-elfldr, port 9021)
# --------------------------------------------------------------------------- #
def deploy(project):
    pdir = project_dir(project)
    elf_rel = find_elf(pdir)
    if not elf_rel:
        emit("ps5", "✖ Aucun fichier .elf trouve. Compile d'abord.", level="error")
        return False
    data = (pdir / elf_rel).read_bytes()
    if data[:4] != b"\x7fELF":
        emit("ps5", f"✖ {elf_rel} n'est pas un ELF valide.", level="error")
        return False

    host, port = CONFIG["ps5_ip"], int(CONFIG["elf_port"])
    emit("ps5", f"→ Envoi de {elf_rel} ({len(data) / 1024:.1f} Ko) vers {host}:{port}…", level="cmd")
    try:
        s = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        emit("ps5", f"✖ Connexion impossible a {host}:{port} ({e}). "
                    "La PS5 est-elle allumee, jailbreakee, avec l'ELF loader actif ?", level="error")
        return False
    try:
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
    except OSError as e:
        s.close()
        emit("ps5", f"✖ Connexion impossible a {host}:{port} ({e}). "
                    "La PS5 est-elle allumee, jailbreakee, avec l'ELF loader actif ?", level="error")
        return False
    emit("ps5", "✔ Payload envoye. Sortie du programme :", level="ok")

    # La sortie est lue en arriere-plan : un programme qui tourne longtemps
    # (serveur, boucle de jeu…) ne bloque plus Forge5.
    def pump():
        with s:
            s.settimeout(None)
            buf = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    emit("ps5", line.decode("utf-8", "replace").rstrip("\r"))
            if buf:
                emit("ps5", buf.decode("utf-8", "replace"))
        emit("ps5", "■ Fin du programme (connexion fermee).", level="dim")

    threading.Thread(target=pump, daemon=True).start()
    return True


def run_job(kind, project):
    if not _busy.acquire(blocking=False):
        emit("system", "Une tache est deja en cours…", level="warn")
        return
    try:
        emit("status", busy=True, job=kind)
        if CONFIG.get("target") == "pc":
            pdir = project_dir(project)
            if kind == "deploy":
                emit("ps5", "En mode sans PS5, utilise ▶ Lancer : le programme tourne sur ton PC.", level="warn")
                return
            exe = SIM.build(project, pdir)
            if exe and kind == "run":
                SIM.run(project, pdir, exe)
            return
        if kind == "build":
            build(project)
        elif kind == "deploy":
            deploy(project)
        elif kind == "run":
            if build(project):
                deploy(project)
    except Exception as e:  # noqa: BLE001 - on remonte tout a l'interface
        emit("system", f"Erreur : {e}", level="error")
    finally:
        emit("status", busy=False, job=None)
        _busy.release()


# --------------------------------------------------------------------------- #
# Journal noyau (klogsrv, port 3232)
# --------------------------------------------------------------------------- #
class Klog:
    def __init__(self):
        self.stop_evt = threading.Event()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_evt.set()

    def _loop(self):
        """Reste connecte tant qu'on ne l'arrete pas : se reconnecte tout seul
        si la PS5 redemarre ou si le Wi-Fi coupe."""
        emit("status", klog=True)
        warned = False
        while not self.stop_evt.is_set():
            host, port = CONFIG["ps5_ip"], int(CONFIG["klog_port"])
            if not warned:
                emit("klog", f"Connexion au journal noyau {host}:{port}…", level="cmd")
            try:
                with socket.create_connection((host, port), timeout=5) as s:
                    s.settimeout(0.5)
                    emit("klog", "✔ Connecte.", level="ok")
                    warned = False
                    buf = b""
                    while not self.stop_evt.is_set():
                        try:
                            chunk = s.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            emit("klog", "Connexion perdue. Reconnexion automatique…", level="warn")
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            text = line.decode("utf-8", "replace").rstrip("\r")
                            lvl = "error" if re.search(r"panic|fault|crash|signal|abort", text, re.I) else None
                            emit("klog", text, level=lvl)
            except OSError as e:
                if not warned:
                    emit("klog", f"✖ Impossible de se connecter ({e}). klogsrv est-il lance sur la PS5 ? "
                                 "Nouvel essai toutes les 3 s…", level="error")
                    warned = True
            self.stop_evt.wait(3)
        emit("klog", "■ Journal noyau arrete.", level="dim")
        emit("status", klog=False)


KLOG = Klog()
DBG = Debugger(lambda ch, text="", **kw: emit(ch, text, **kw), lambda: CONFIG)
L5 = Launcher5(CONFIG, lambda ch, text="", **kw: emit(ch, text, **kw))
PAYLOADS = PayloadManager(lambda ch, text="", **kw: emit(ch, text, **kw), lambda: CONFIG)
SIM = Simulator(lambda ch, text="", **kw: emit(ch, text, **kw), lambda: CONFIG,
                lambda project, argv, cwd, env=None: stream_command(project, argv, cwd, env))


def debug_job(project):
    if not _busy.acquire(blocking=False):
        emit("system", "Une tache est deja en cours…", level="warn")
        return
    pc = CONFIG.get("target") == "pc"
    elf = pdir = None
    try:
        pdir = project_dir(project)
        emit("status", busy=True, job="debug")
        elf = SIM.build(project, pdir) if pc else (find_elf(pdir) if build(project) else None)
    except Exception as e:  # noqa: BLE001 - projet introuvable, etc.
        emit("debug", f"✖ {e}", level="error")
    finally:
        emit("status", busy=False, job=None)
        _busy.release()
    if not elf:
        if not pc:
            emit("debug", "✖ Aucun .elf apres la compilation.", level="error")
        return
    overrides = None
    if pc:
        SIM.stop(quiet=True)
        try:
            port = SIM.start_gdbserver(pdir)
        except RuntimeError as e:
            emit("debug", f"✖ {e}", level="error")
            return
        emit("debug", "💻 Debogage sur PC (simulation PS5).", level="cmd")
        overrides = {"ps5_ip": "127.0.0.1", "gdb_port": port, "gdb_remote_dir": "/tmp", "sysroot": "/",
                     "gdb_path": CONFIG.get("sim_gdb") or "wsl gdb-multiarch"}
    try:
        DBG.start(project, pdir, elf, overrides=overrides)
    except DebugError as e:
        emit("debug", f"✖ {e}", level="error")
    if pc:  # arreter gdbserver a la fin de la session
        def cleanup():
            while DBG.proc is not None:
                time.sleep(0.5)
            SIM.stop_gdbserver()
        threading.Thread(target=cleanup, daemon=True).start()


# --------------------------------------------------------------------------- #
# Surveillance des fichiers + mode Auto (rechargement a chaud)
# --------------------------------------------------------------------------- #
SOURCE_EXT = {".c", ".h", ".cpp", ".hpp", ".s", ".S", ".mk", ".cmake"}
WATCH = {"project": None}       # projet en mode Auto
_auto_timer = {"t": None}


def _is_tracked(f):
    return f.suffix in EDITABLE or f.name == "Makefile"


IGNORED_DIRS = {"build-pc", "build", "node_modules", "__pycache__"}


def _snapshot():
    snap = {}
    ws = workspace()
    for pdir in ws.iterdir():
        if not (pdir.is_dir() and NAME_RE.match(pdir.name)):
            continue
        for dirpath, dirnames, filenames in os.walk(pdir):
            # dossiers generes ou volumineux : inutile de les surveiller
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in filenames:
                f = Path(dirpath) / name
                try:
                    if _is_tracked(f):
                        snap[(pdir.name, f.relative_to(pdir).as_posix())] = f.stat().st_mtime_ns
                except OSError:
                    pass
    return snap


def _schedule_auto(project):
    """Attend 0,6 s sans nouvelle modif (plusieurs fichiers enregistres d'un coup),
    puis compile et renvoie a la PS5."""
    if _auto_timer["t"]:
        _auto_timer["t"].cancel()

    def go():
        if WATCH["project"] != project or DBG.state != "idle":
            return
        if _busy.locked():  # une compilation tourne deja : on reessaie juste apres
            return _schedule_auto(project)
        emit("system", "⟳ Modification detectee : recompilation et envoi automatiques…", level="cmd")
        run_job("run", project)

    _auto_timer["t"] = threading.Timer(0.6, go)
    _auto_timer["t"].start()


def file_watcher():
    prev = _snapshot()
    while True:
        time.sleep(0.8)
        try:
            cur = _snapshot()
        except OSError:
            continue
        changes = []
        for key, m in cur.items():
            if key not in prev:
                changes.append((*key, "added"))
            elif prev[key] != m:
                changes.append((*key, "changed"))
        for key in prev.keys() - cur.keys():
            changes.append((*key, "removed"))
        prev = cur
        for project, file, kind in changes:
            emit("fs", project=project, file=file, kind=kind)
        auto = {p for p, f, k in changes
                if p == WATCH["project"] and (Path(f).suffix in SOURCE_EXT or Path(f).name == "Makefile")}
        for p in auto:
            _schedule_auto(p)


# --------------------------------------------------------------------------- #
# Gestion des fichiers d'un projet
# --------------------------------------------------------------------------- #
def new_file(project, rel):
    rel = (rel or "").strip().replace("\\", "/")
    f = safe_file(project, rel)
    if not (f.suffix in EDITABLE or f.name == "Makefile"):
        raise ValueError("Extension non prise en charge (.c, .h, .cpp, .txt, .md…)")
    if f.exists():
        raise FileExistsError("Ce fichier existe deja")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#pragma once\n\n" if f.suffix in (".h", ".hpp") else
                 f"// {f.name}\n" if f.suffix in (".c", ".cpp") else "", encoding="utf-8")
    return f.relative_to(project_dir(project)).as_posix()


def delete_file(project, rel):
    f = safe_file(project, rel)
    if not f.is_file():
        raise FileNotFoundError("Fichier introuvable")
    f.unlink()


# --------------------------------------------------------------------------- #
# Detection de la PS5 sur le reseau local
# --------------------------------------------------------------------------- #
def _local_ipv4s():
    ips = set()
    try:
        # Adresse de la carte reseau principale (aucune donnee n'est envoyee)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    private = re.compile(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")
    return sorted(ip for ip in ips if private.match(ip))


def discover_ps5():
    """Cherche sur le reseau local les appareils dont l'ELF loader (port 9021) repond."""
    from concurrent.futures import ThreadPoolExecutor
    port = int(CONFIG.get("elf_port", 9021))
    mine = _local_ipv4s()
    targets = []
    for ip in mine:
        base = ip.rsplit(".", 1)[0]
        targets += [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" not in mine]
    with ThreadPoolExecutor(max_workers=96) as pool:
        hits = [ip for ip, ok in zip(targets, pool.map(lambda h: port_open(h, port, 0.35), targets)) if ok]
    found = []
    for ip in hits:
        found.append({"ip": ip, "gdbsrv": port_open(ip, 2159, 0.4), "klogsrv": port_open(ip, 3232, 0.4)})
    return {"found": found, "networks": [ip.rsplit(".", 1)[0] + ".0/24" for ip in mine]}


# --------------------------------------------------------------------------- #
# Mises a jour du SDK (https://github.com/ps5-payload-dev/sdk)
# --------------------------------------------------------------------------- #
def sdk_info():
    import urllib.request
    info = {"local": None, "latest": None}
    sdk = CONFIG.get("sdk_path") or os.environ.get("PS5_PAYLOAD_SDK", "")
    if sdk:
        found = (Path(sdk) / "toolchain" / "prospero.mk").is_file()
        if not found and sdk.startswith("/") and os.name == "nt":  # SDK installe dans Ubuntu (WSL)
            try:
                found = subprocess.run(["wsl", "-e", "test", "-f", f"{sdk}/toolchain/prospero.mk"],
                                       capture_output=True, timeout=15).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        info["local"] = {"path": sdk, "found": found}
    req = urllib.request.Request("https://api.github.com/repos/ps5-payload-dev/sdk/releases/latest",
                                 headers={"User-Agent": "Forge5", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read())
        info["latest"] = {"tag": j.get("tag_name"), "date": (j.get("published_at") or "")[:10],
                          "url": j.get("html_url")}
    except Exception as e:  # noqa: BLE001
        info["error"] = f"Impossible de joindre GitHub ({e})"
    return info


def port_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def console_status():
    host = CONFIG["ps5_ip"]
    return {
        "elfldr": port_open(host, CONFIG["elf_port"]),
        "klog": port_open(host, CONFIG["klog_port"]) if not KLOG.running else True,
        "klog_streaming": KLOG.running,
    }


# --------------------------------------------------------------------------- #
# Serveur HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"Forge5/{VERSION}"

    def log_message(self, *args):  # silence
        pass

    # -- utilitaires --
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=600")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _err(self, e):
        code = 404 if isinstance(e, FileNotFoundError) else 400
        self._json({"error": i18n.tr(str(e), CONFIG.get("lang") or "fr")}, code)

    def _same_origin(self):
        # Empeche un site web tiers de piloter l'atelier (CSRF)
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).hostname in ("127.0.0.1", "localhost")

    def _local_host(self):
        # Protection contre le « DNS rebinding » : seul l'acces local est accepte
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def parse_request(self):
        if not super().parse_request():
            return False
        if not self._local_host():
            self.send_error(403, "Forbidden")
            return False
        return True

    # -- GET --
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                body = (ROOT / "ui.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/favicon.png":
                self._bytes((ROOT / "docs" / "forge5-icone.png").read_bytes(), "image/png")
            elif path == "/api/events":
                self._sse()
            elif path == "/api/config":
                self._json({**CONFIG, "version": VERSION})
            elif path == "/api/projects":
                self._json({"projects": list_projects(), "templates": list_templates()})
            elif path == "/api/breakpoints":
                project = parse_qs(urlparse(self.path).query).get("project", [""])[0]
                self._json(DBG.get_breakpoints(project))
            elif path == "/api/watch":
                self._json({"watch": WATCH["project"], "klog": KLOG.running, "sim_running": SIM.running})
            elif path == "/api/i18n":
                q = parse_qs(urlparse(self.path).query)
                lang = q.get("lang", [CONFIG.get("lang") or "fr"])[0]
                langs = i18n.available()
                if lang not in {l["code"] for l in langs}:
                    lang = "fr"
                self._json({"lang": lang, "languages": langs, "ui": i18n.ui_strings(lang)})
            elif path == "/api/payloads":
                self._json({**PAYLOADS.status(), "active": PAYLOADS.ps5_active(port_open)})
            elif path == "/api/discover":
                self._json(discover_ps5())
            elif path == "/api/sdk":
                self._json(sdk_info())
            elif path == "/api/debug/state":
                self._json({"state": DBG.state, "project": DBG.project})
            elif path == "/api/status":
                self._json(console_status())
            elif path == "/api/l5/state":
                self._json(L5.state())
            elif path in ("/api/l5/icon", "/api/l5/file"):
                q = parse_qs(urlparse(self.path).query)
                if path == "/api/l5/icon":
                    data = L5.icon(q.get("id", [""])[0])
                    name = "icon.png"
                else:
                    name = q.get("name", [""])[0]
                    data = L5.get_file(q.get("dir", [""])[0], name)
                if data is None:
                    return self._json({"error": "introuvable"}, 404)
                ext = name.rsplit(".", 1)[-1].lower()
                ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
                         "bmp": "image/bmp", "json": "application/json", "ogg": "audio/ogg", "mp3": "audio/mpeg",
                         "flac": "audio/flac", "wav": "audio/wav"}.get(ext, "application/octet-stream")
                self._bytes(data, ctype)
            elif path.startswith("/api/file/"):
                _, _, _, project, rel = path.split("/", 4)
                f = safe_file(unquote(project), unquote(rel))
                self._json({"content": f.read_text(encoding="utf-8", errors="replace")})
            else:
                self._json({"error": "introuvable"}, 404)
        except Exception as e:  # noqa: BLE001
            self._err(e)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue(maxsize=5000)
        with _sub_lock:
            _subscribers.append(q)
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with _sub_lock:
                _subscribers.remove(q)

    # -- POST --
    def do_POST(self):
        if not self._same_origin():
            return self._json({"error": "origine refusee"}, 403)
        path = urlparse(self.path).path
        try:
            if path == "/api/l5/upload":   # fichier brut (image, musique, theme)
                q = parse_qs(urlparse(self.path).query)
                n = int(self.headers.get("Content-Length") or 0)
                if n > 80 * 1024 * 1024:
                    raise ValueError("Fichier vide ou trop gros (80 Mo au maximum).")
                raw = self.rfile.read(n)
                return self._json({"name": L5.upload(q.get("dir", [""])[0], q.get("name", [""])[0], raw)})
            data = self._body()
            if path == "/api/config":
                for k in DEFAULT_CONFIG:
                    if k in data:
                        CONFIG[k] = data[k]
                save_config(CONFIG)
                if len(data) > 1:  # enregistrement de la fenetre Parametres (pas un simple interrupteur)
                    emit("system", "Parametres enregistres.", level="ok")
                self._json(CONFIG)
            elif path == "/api/projects":
                tpls = list_templates()
                create_project(data.get("name"), data.get("template") or (tpls[0] if tpls else ""))
                self._json({"ok": True})
            elif path.startswith("/api/file/"):
                _, _, _, project, rel = path.split("/", 4)
                f = safe_file(unquote(project), unquote(rel))
                f.write_text(data.get("content", ""), encoding="utf-8", newline="")
                self._json({"ok": True})
            elif path == "/api/newfile":
                self._json({"file": new_file(data.get("project"), data.get("file"))})
            elif path == "/api/deletefile":
                delete_file(data.get("project"), data.get("file"))
                self._json({"ok": True})
            elif path == "/api/watch":
                project = data.get("project")
                if project:
                    project_dir(project)
                WATCH["project"] = project or None
                emit("status", watch=WATCH["project"])
                emit("system", f"⟳ Mode Auto active pour « {project} » : chaque modification est "
                               "recompilee et envoyee a la PS5." if project else "Mode Auto desactive.",
                     level="ok" if project else "dim")
                self._json({"watch": WATCH["project"]})
            elif path in ("/api/build", "/api/deploy", "/api/run"):
                project = data.get("project")
                project_dir(project)  # valide
                threading.Thread(target=run_job, args=(path.rsplit("/", 1)[1], project), daemon=True).start()
                self._json({"ok": True})
            elif path == "/api/breakpoints":
                project_dir(data.get("project"))  # valide
                safe_file(data["project"], data["file"])
                self._json(DBG.toggle_breakpoint(data["project"], data["file"], int(data["line"])))
            elif path == "/api/debug/start":
                project_dir(data.get("project"))
                threading.Thread(target=debug_job, args=(data["project"],), daemon=True).start()
                self._json({"ok": True})
            elif path == "/api/debug/control":
                threading.Thread(target=self._dbg_control, args=(data.get("action"),), daemon=True).start()
                self._json({"ok": True})
            elif path == "/api/debug/frame":
                self._json({"variables": DBG.select_frame(data.get("level", 0))})
            elif path == "/api/debug/eval":
                try:
                    self._json({"value": DBG.evaluate(data.get("expr", ""))})
                except DebugError as e:
                    self._json({"error": i18n.tr(str(e), CONFIG.get("lang") or "fr")})
            elif path == "/api/payloads/check":
                threading.Thread(target=PAYLOADS.check, daemon=True,
                                 kwargs={"auto_download": bool(CONFIG.get("payloads_auto_update", True))}).start()
                self._json({"ok": True})
            elif path == "/api/payloads/download":
                threading.Thread(target=PAYLOADS.download, args=(data.get("id"),), daemon=True).start()
                self._json({"ok": True})
            elif path == "/api/payloads/send":
                threading.Thread(target=PAYLOADS.send, args=(data.get("id"),), daemon=True).start()
                self._json({"ok": True})
            elif path == "/api/l5/menu":
                L5.save_menu(data.get("menu"))
                self._json({"ok": True})
            elif path == "/api/l5/theme":
                self._json(L5.save_theme(data.get("theme"), apply=bool(data.get("apply", True)),
                                         save_as=bool(data.get("save_as"))))
            elif path == "/api/l5/delete":
                L5.delete(data.get("dir"), data.get("name"))
                self._json({"ok": True})
            elif path == "/api/l5/install":
                L5.install()
                self._json({"ok": True})
            elif path == "/api/sim/stop":
                SIM.stop()
                self._json({"ok": True})
            elif path == "/api/klog/start":
                if CONFIG.get("target") == "pc":
                    emit("klog", "💻 Mode sans PS5 : le journal noyau de ton programme (klog_printf) s'affiche ici "
                                 "automatiquement, pas besoin de klogsrv.", level="ok")
                    return self._json({"ok": True})
                KLOG.start()
                self._json({"ok": True})
            elif path == "/api/klog/stop":
                KLOG.stop()
                self._json({"ok": True})
            else:
                self._json({"error": "introuvable"}, 404)
        except Exception as e:  # noqa: BLE001
            self._err(e)


def _dbg_control(_self, action):
    try:
        DBG.control(action)
    except DebugError as e:
        emit("debug", f"⚠ {e}", level="warn")


Handler._dbg_control = _dbg_control


def find_window_browser():
    """Chrome, Edge ou Brave : ils savent afficher une page dans une fenetre d'application."""
    names = [("Google", "Chrome", "Application", "chrome.exe"),
             ("Microsoft", "Edge", "Application", "msedge.exe"),
             ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
    roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for n in names:
        for r in roots:
            if r and Path(r, *n).is_file():
                return str(Path(r, *n))
    for exe in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def open_window(url, on_close=None):
    """Ouvre Forge5 dans sa propre fenetre (sans barre d'adresse ni onglets).
    Quand la fenetre est fermee, on_close est appele (arret de Forge5)."""
    exe = find_window_browser()
    if not exe:
        webbrowser.open(url)
        return
    profile = ROOT / ".fenetre"   # profil a part : une vraie fenetre, independante du navigateur
    proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,950",
                             "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"])
    if on_close:
        def wait():
            proc.wait()
            on_close()
        threading.Thread(target=wait, daemon=True).start()


def main():
    import sys
    if sys.stdout is None:   # lance sans console (pyw) : le journal va dans un fichier
        sys.stdout = sys.stderr = open(ROOT / "forge5.log", "w", encoding="utf-8", buffering=1)
    ap = argparse.ArgumentParser(description="Forge5 - atelier homebrew PS5")
    ap.add_argument("--port", type=int, default=5150)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--navigateur", action="store_true", help="ouvrir dans le navigateur au lieu d'une fenetre")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    show = webbrowser.open if args.navigateur else open_window

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # Forge5 tourne deja : on ouvre simplement sa fenetre
        print("Forge5 est deja lance : ouverture de sa fenetre.")
        if not args.no_open:
            show(url)
        return
    srv.daemon_threads = True
    workspace()
    threading.Thread(target=file_watcher, daemon=True).start()
    threading.Thread(target=PAYLOADS.auto_loop, daemon=True).start()
    print(f"Forge5 demarre sur {url}  (Ctrl+C pour quitter)")
    if not args.no_open:
        if args.navigateur:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        else:
            # fermer la fenetre arrete Forge5
            stop = lambda: threading.Thread(target=srv.shutdown, daemon=True).start()
            threading.Timer(0.3, lambda: open_window(url, on_close=stop)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nAu revoir.")


if __name__ == "__main__":
    main()
