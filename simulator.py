"""
Mode sans PS5 de Forge5.

Compile le projet pour PC (Linux, via WSL sous Windows) avec une couche qui
imite les fonctions de la PS5 (sim/forge5_sim.c), puis le lance ou le debogue
localement. Les notifications PS5 et le journal noyau du programme s'affichent
dans l'interface de Forge5.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent
SIM_DIR = ROOT / "sim"
OUT_DIR = "build-pc"
MARK = "\x1eF5:"

SIGNALS = {
    4: "SIGILL (instruction invalide)", 6: "SIGABRT (abort / assert rate)",
    7: "SIGBUS (erreur de bus)", 8: "SIGFPE (calcul impossible, ex. division par zero)",
    9: "SIGKILL (programme tue)", 11: "SIGSEGV (acces memoire invalide)",
    13: "SIGPIPE (connexion fermee)", 15: "SIGTERM (arrete)",
}


def is_wsl(argv):
    return bool(argv) and Path(argv[0]).name.lower() in ("wsl", "wsl.exe")


def wsl_path(p):
    p = str(p)
    if re.match(r"^[A-Za-z]:[\\/]", p):
        w = PureWindowsPath(p)
        return "/mnt/" + w.drive[0].lower() + "/" + "/".join(w.parts[1:])
    return p.replace("\\", "/")


class Simulator:
    def __init__(self, emit, get_config, stream_command):
        self.emit = emit
        self.cfg = get_config
        self.stream_command = stream_command
        self.proc = None
        self.gdbserver = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Compilation pour PC
    # ------------------------------------------------------------------ #
    def _split(self, key, default):
        return shlex.split(self.cfg().get(key) or default, posix=os.name != "nt")

    def _build_makefile_pc(self, project, pdir):
        """Projet qui a deja sa propre version PC (ex. Launcher5) : make -f Makefile.pc."""
        runner = self._split("sim_cc", "wsl gcc")
        started = time.time()
        self.emit("build", "💻 Mode sans PS5 : ce projet a sa propre version PC (Makefile.pc), compilation…", level="cmd")
        argv = ([runner[0], "--cd", wsl_path(pdir), "-e", "make", "-f", "Makefile.pc"] if is_wsl(runner)
                else ["make", "-f", "Makefile.pc"])
        self.emit("build", "$ make -f Makefile.pc", level="dim")
        rc, errors, warnings = self.stream_command(project, argv, pdir)
        if rc is None:
            self.emit("build", "✖ WSL introuvable : le mode sans PS5 en a besoin (wsl --install).", level="error")
            return None
        if rc != 0:
            self.emit("build", f"✖ Echec de la compilation : {errors} erreur(s), {warnings} avertissement(s)", level="error")
            return None
        # le programme produit est la premiere cible du Makefile.pc
        exe = None
        for line in (pdir / "Makefile.pc").read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^([A-Za-z0-9_\-./]+)\s*:(?!=)", line)
            if m and not m.group(1).startswith("."):
                exe = m.group(1)
                break
        if not exe or not (pdir / exe).is_file():
            self.emit("build", "✖ Programme introuvable apres la compilation (premiere cible du Makefile.pc).", level="error")
            return None
        self.emit("build", f"✔ Compilation PC reussie en {time.time() - started:.1f}s → {exe}", level="ok")
        return exe

    def build(self, project, pdir):
        pdir = Path(pdir)
        if (pdir / "Makefile.pc").is_file():
            return self._build_makefile_pc(project, pdir)
        cc = self._split("sim_cc", "wsl gcc")
        conv = wsl_path if is_wsl(cc) else (lambda p: str(p))
        sources = sorted(
            f.relative_to(pdir).as_posix() for f in pdir.rglob("*")
            if f.is_file() and f.suffix in (".c", ".cpp", ".cc") and OUT_DIR not in f.parts
        )
        if not sources:
            self.emit("build", "✖ Aucun fichier source (.c / .cpp) dans le projet.", level="error")
            return None
        (pdir / OUT_DIR).mkdir(exist_ok=True)
        started = time.time()
        self.emit("build", "💻 Mode sans PS5 : compilation pour PC avec la simulation PS5…", level="cmd")

        # 1) la couche de simulation (recompilee seulement si elle a change)
        sim_obj = pdir / OUT_DIR / "forge5_sim.o"
        sim_src = SIM_DIR / "forge5_sim.c"
        if not sim_obj.exists() or sim_obj.stat().st_mtime < sim_src.stat().st_mtime:
            rc, *_ = self.stream_command(project, cc + ["-g", "-O0", "-c", conv(sim_src), "-o", f"{OUT_DIR}/forge5_sim.o"], pdir)
            if rc is None:
                self.emit("build", f"✖ « {cc[0]} » introuvable. Le mode sans PS5 a besoin de WSL (Ubuntu) avec gcc : "
                                   "wsl --install, puis dans Ubuntu : sudo apt install build-essential gdb-multiarch gdbserver",
                          level="error")
                return None
            if rc != 0:
                self.emit("build", "✖ La couche de simulation n'a pas compile.", level="error")
                return None

        # 2) le projet
        cxx = any(s.endswith((".cpp", ".cc")) for s in sources)
        compiler = self._split("sim_cxx", "wsl g++") if cxx else cc
        exe = f"{OUT_DIR}/{project}"
        argv = compiler + ["-g", "-O0", "-Wall", "-D__FORGE5_SIM__", "-I", conv(SIM_DIR / "include"),
                           *sources, f"{OUT_DIR}/forge5_sim.o", "-o", exe, "-lm", "-lpthread"]
        self.emit("build", "$ " + " ".join(argv), level="dim")
        rc, errors, warnings = self.stream_command(project, argv, pdir)
        dt = time.time() - started
        if rc == 0:
            self.emit("build", f"✔ Compilation PC reussie en {dt:.1f}s → {exe} ({warnings} avertissement(s))", level="ok")
            return exe
        self.emit("build", f"✖ Echec de la compilation : {errors} erreur(s), {warnings} avertissement(s)", level="error")
        return None

    # ------------------------------------------------------------------ #
    # Lecture de la sortie du programme simule
    # ------------------------------------------------------------------ #
    def _handle_line(self, line, debug=False):
        line = line.rstrip("\r\n")
        if line.startswith(MARK):
            kind, _, data = line[len(MARK):].partition(":")
            try:
                text = bytes.fromhex(data).decode("utf-8", "replace")
            except ValueError:
                text = data
            if kind == "klog":
                self.emit("klog", "[PC] " + text,
                          level="error" if re.search(r"panic|fault|crash|abort", text, re.I) else
                          "warn" if text.startswith("[SIM]") else None)
            elif kind == "notify":
                self._notify(text)
            elif kind == "exit":
                self._on_exit(int(text or 0))
            return
        if debug and re.match(r"^(Listening on port|Remote debugging from host|Process .* created; pid|"
                              r"Child exited|Detaching from process|Killing process|Remote side has terminated)", line):
            self.emit("debug", "[gdbserver] " + line, level="dim")
            return
        self.emit("ps5", line)

    def _notify(self, payload):
        title, sub = payload.strip(), ""
        try:
            j = json.loads(payload)
            view = j.get("rawData", {}).get("viewData", {})
            title = view.get("message", {}).get("body") or title
            sub = view.get("subMessage", {}).get("body", "")
        except (ValueError, AttributeError):
            pass
        self.emit("notify", title, sub=sub)
        self.emit("ps5", f"🔔 Notification PS5 : {title}{f' — {sub}' if sub else ''}", level="ok")

    def _on_exit(self, code):
        if code >= 128 and code - 128 in SIGNALS:
            sig = SIGNALS[code - 128]
            self.emit("ps5", f"💥 Crash : {sig}. Clique sur 🐞 Deboguer pour voir la ligne exacte.", level="error")
        elif code == 0:
            self.emit("ps5", "■ Programme termine (code 0).", level="dim")
        else:
            self.emit("ps5", f"■ Programme termine avec le code {code}.", level="warn")

    # ------------------------------------------------------------------ #
    # Lancement
    # ------------------------------------------------------------------ #
    def _launcher(self, pdir, script):
        runner = self._split("sim_cc", "wsl gcc")
        if is_wsl(runner):
            return [runner[0], "--cd", wsl_path(pdir), "-e", "sh", "-c", script], None
        return ["sh", "-c", script], str(pdir)

    def run(self, project, pdir, exe):
        self.stop(quiet=True)
        # Le code de sortie est renvoye a Forge5 (encode en hexadecimal comme les autres messages)
        script = f'./{exe}; c=$?; printf "\\036F5:exit:%s\\n" "$(printf %s "$c" | od -An -tx1 | tr -d " \\n")" >&2'
        argv, cwd = self._launcher(pdir, script)
        self.emit("ps5", f"▶ Lancement sur PC (simulation PS5) : {exe}", level="cmd")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
        except FileNotFoundError:
            self.emit("ps5", "✖ WSL introuvable : le mode sans PS5 en a besoin (wsl --install).", level="error")
            return
        with self.lock:
            self.proc = proc
            self.proc_exe = exe
        self.emit("status", sim_running=True)

        def pump():
            for line in proc.stdout:
                self._handle_line(line)
            proc.wait()
            with self.lock:
                if self.proc is proc:
                    self.proc = None
            self.emit("status", sim_running=False)

        threading.Thread(target=pump, daemon=True).start()

    def stop(self, quiet=False):
        with self.lock:
            proc, self.proc = self.proc, None
            exe = getattr(self, "proc_exe", None)
        if not proc:
            return
        proc.kill()
        runner = self._split("sim_cc", "wsl gcc")
        if is_wsl(runner) and exe:  # tuer aussi le processus cote Linux
            subprocess.run([runner[0], "-e", "pkill", "-f", exe], capture_output=True)
        if not quiet:
            self.emit("ps5", "■ Programme arrete.", level="dim")
        self.emit("status", sim_running=False)

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    # ------------------------------------------------------------------ #
    # Debogage : gdbserver local, le meme protocole que gdbsrv sur PS5
    # ------------------------------------------------------------------ #
    def start_gdbserver(self, pdir):
        self.stop_gdbserver()
        port = int(self.cfg().get("sim_gdb_port") or 2160)
        runner = self._split("sim_cc", "wsl gcc")
        if is_wsl(runner):
            argv, cwd = [runner[0], "--cd", wsl_path(pdir), "-e", "gdbserver", "--multi", f":{port}"], None
        else:
            argv, cwd = ["gdbserver", "--multi", f":{port}"], str(pdir)
        ready = threading.Event()
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
        except FileNotFoundError:
            raise RuntimeError("WSL introuvable : le mode sans PS5 en a besoin.")
        self.gdbserver = proc

        def pump():
            for line in proc.stdout:
                if "Listening on port" in line:
                    ready.set()
                if "gdbserver" in line and "not found" in line:
                    self.emit("debug", "✖ gdbserver introuvable. Dans Ubuntu : sudo apt install gdbserver", level="error")
                    ready.set()
                self._handle_line(line, debug=True)
            ready.set()

        threading.Thread(target=pump, daemon=True).start()
        if not ready.wait(8) or proc.poll() is not None:
            raise RuntimeError("gdbserver n'a pas demarre (installe-le dans Ubuntu : sudo apt install gdbserver).")
        return port

    def stop_gdbserver(self):
        proc, self.gdbserver = self.gdbserver, None
        if proc and proc.poll() is None:
            proc.kill()
            runner = self._split("sim_cc", "wsl gcc")
            if is_wsl(runner):
                subprocess.run([runner[0], "-e", "pkill", "-f", "gdbserver --multi"], capture_output=True)
