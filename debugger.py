"""
Debogueur visuel de Forge5 : pilote GDB en mode machine (GDB/MI) et le
connecte a gdbsrv sur la PS5 (port 2159, https://github.com/ps5-payload-dev/gdbsrv).

Flux (identique au Makefile d'exemple de gdbsrv) :
    set architecture i386:x86-64
    target extended-remote <ps5>:2159
    file <payload.elf>
    remote put <payload.elf> /user/temp/<payload.elf>
    set remote exec-file /user/temp/<payload.elf>
    start
"""
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path, PureWindowsPath

# --------------------------------------------------------------------------- #
# Analyse de la sortie GDB/MI
# --------------------------------------------------------------------------- #
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "e": "\x1b", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


class _P:
    def __init__(self, s):
        self.s, self.i = s, 0

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def string(self):
        assert self.s[self.i] == '"'
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                n = self.s[self.i + 1]
                if n in "01234567":  # octal \ooo
                    j = self.i + 1
                    while j < len(self.s) and j < self.i + 4 and self.s[j] in "01234567":
                        j += 1
                    out.append(chr(int(self.s[self.i + 1:j], 8)))
                    self.i = j
                    continue
                out.append(_ESC.get(n, n))
                self.i += 2
            elif c == '"':
                self.i += 1
                break
            else:
                out.append(c)
                self.i += 1
        raw = "".join(out)
        try:  # GDB echappe les octets UTF-8 un par un
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw

    def value(self):
        c = self.peek()
        if c == '"':
            return self.string()
        if c == "{":
            self.i += 1
            d = {}
            while self.peek() not in ("}", ""):
                k, v = self.result()
                d[k] = v
                if self.peek() == ",":
                    self.i += 1
            self.i += 1
            return d
        if c == "[":
            self.i += 1
            items = []
            while self.peek() not in ("]", ""):
                if self.peek() in '"{[':
                    items.append(self.value())
                else:  # liste de resultats : frame={...},frame={...}
                    items.append(self.result()[1])
                if self.peek() == ",":
                    self.i += 1
            self.i += 1
            return items
        raise ValueError(f"MI inattendu a {self.i}: {self.s[self.i:self.i + 20]!r}")

    def result(self):
        j = self.s.index("=", self.i)
        key = self.s[self.i:j]
        self.i = j + 1
        return key, self.value()


def parse_results(s):
    """'a="1",b={c="2"}' -> {'a': '1', 'b': {'c': '2'}}"""
    p, d = _P(s), {}
    while p.i < len(s):
        k, v = p.result()
        d[k] = v
        if p.peek() == ",":
            p.i += 1
    return d


_RECORD = re.compile(r"^(\d*)([\^*=+~@&])(.*)$")


def parse_line(line):
    """Renvoie (token, type, classe, donnees) ou None pour '(gdb)'."""
    line = line.rstrip("\r\n")
    if not line or line.strip() == "(gdb)":
        return None
    m = _RECORD.match(line)
    if not m:
        return (None, "raw", None, line)
    token, kind, rest = m.groups()
    token = int(token) if token else None
    if kind in "~@&":
        return (token, kind, None, _P(rest).string() if rest.startswith('"') else rest)
    cls, _, data = rest.partition(",")
    return (token, kind, cls, parse_results(data) if data else {})


def mi_quote(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
# Session de debogage
# --------------------------------------------------------------------------- #
class DebugError(Exception):
    pass


class Debugger:
    """Une seule session a la fois. `emit` envoie les evenements a l'interface."""

    def __init__(self, emit, get_config):
        self.emit = emit
        self.cfg = get_config
        self.proc = None
        self.lock = threading.Lock()
        self.token = 0
        self.pending = {}           # token -> [Event, resultat]
        self.breakpoints = {}       # (project) -> {file: set(lines)}
        self.bp_numbers = {}        # (file, line) -> numero GDB
        self.project = None
        self.project_dir = None
        self.state = "idle"         # idle | starting | running | stopped
        self.wsl = False

    # -- chemins (GDB sous WSL voit C:\ comme /mnt/c/) --
    def to_gdb_path(self, p):
        p = str(p)
        if self.wsl and re.match(r"^[A-Za-z]:[\\/]", p):
            w = PureWindowsPath(p)
            return "/mnt/" + w.drive[0].lower() + "/" + "/".join(w.parts[1:])
        return p.replace("\\", "/")

    def from_gdb_path(self, p):
        m = re.match(r"^/mnt/([a-z])/(.*)$", p or "")
        if self.wsl and m:
            return f"{m[1].upper()}:/{m[2]}"
        return p

    def rel_file(self, frame):
        """Chemin du fichier relatif au projet, si le fichier en fait partie."""
        full = self.from_gdb_path(frame.get("fullname") or "")
        if full and self.project_dir:
            try:
                return Path(full).resolve().relative_to(self.project_dir.resolve()).as_posix()
            except (ValueError, OSError):
                pass
        f = frame.get("file")
        if f and self.project_dir and (self.project_dir / f).is_file():
            return Path(f).as_posix()
        return None

    # -- points d'arret (memorises meme sans session) --
    def get_breakpoints(self, project):
        return {f: sorted(ls) for f, ls in self.breakpoints.get(project, {}).items() if ls}

    def toggle_breakpoint(self, project, file, line):
        bps = self.breakpoints.setdefault(project, {}).setdefault(file, set())
        live = self.proc and self.project == project and self.state == "stopped"
        if line in bps:
            bps.discard(line)
            num = self.bp_numbers.pop((file, line), None)
            if live and num:
                self._try(f"-break-delete {num}")
        else:
            bps.add(line)
            if live:
                self._insert_bp(file, line)
        return self.get_breakpoints(project)

    def _insert_bp(self, file, line):
        try:
            r = self.cmd(f"-break-insert -f {mi_quote(f'{Path(file).name}:{line}')}")
            self.bp_numbers[(file, line)] = r.get("bkpt", {}).get("number")
        except DebugError as e:
            self.emit("debug", f"⚠ Point d'arret {file}:{line} non pose : {e}", level="warn")

    # -- communication avec GDB --
    def cmd(self, command, timeout=20):
        if not self.proc:
            raise DebugError("Aucune session de debogage")
        with self.lock:
            self.token += 1
            tok = self.token
            ev = threading.Event()
            self.pending[tok] = [ev, None]
            self.proc.stdin.write(f"{tok}{command}\n")
            self.proc.stdin.flush()
        if not ev.wait(timeout):
            self.pending.pop(tok, None)
            raise DebugError(f"Pas de reponse de GDB a « {command.split()[0]} »")
        cls, data = self.pending.pop(tok)[1]
        if cls == "error":
            raise DebugError(data.get("msg", "erreur GDB"))
        return data

    def _try(self, command):
        try:
            return self.cmd(command)
        except DebugError as e:
            self.emit("debug", f"⚠ {e}", level="warn")
            return None

    def _reader(self, proc):
        console_buf = ""
        for line in proc.stdout:
            try:
                rec = parse_line(line)
            except Exception:  # noqa: BLE001 - ligne MI mal formee
                rec = (None, "raw", None, line.rstrip())
            if rec is None:
                continue
            token, kind, cls, data = rec
            if kind == "^":
                if token in self.pending:
                    self.pending[token][1] = (cls, data)
                    self.pending[token][0].set()
            elif kind == "*":
                if cls == "running":
                    self._set_state("running")
                elif cls == "stopped":
                    threading.Thread(target=self._on_stopped, args=(data,), daemon=True).start()
            elif kind == "@":   # sortie du programme debogue
                for l in data.splitlines():
                    self.emit("ps5", l)
            elif kind == "~":   # console GDB : arrive par morceaux, on reconstitue les lignes
                console_buf += data
                while "\n" in console_buf:
                    txt, console_buf = console_buf.split("\n", 1)
                    if txt.strip():
                        self.emit("debug", txt, level="dim")
            elif kind == "&":   # journal GDB / erreurs
                txt = data.rstrip("\n")
                if txt and not txt.startswith(("set ", "remote ")):
                    self.emit("debug", txt, level="warn")
            elif kind == "raw" and data.strip():
                self.emit("debug", data, level="dim")
        # GDB s'est termine
        for ev, _ in list(self.pending.values()):
            ev.set()
        if self.proc is proc:
            self.proc = None
            self._set_state("idle")
            self.emit("debug", "■ Session de debogage terminee.", level="dim")

    def _set_state(self, state, **extra):
        self.state = state
        self.emit("dbgstate", state=state, **extra)

    # -- cycle de vie --
    def start(self, project, project_dir, elf_rel, overrides=None):
        if self.proc:
            raise DebugError("Une session de debogage est deja ouverte")
        cfg = {**self.cfg(), **(overrides or {})}
        argv = shlex.split(cfg.get("gdb_path") or "gdb-multiarch", posix=os.name != "nt")
        self.wsl = Path(argv[0]).name.lower() in ("wsl", "wsl.exe")
        self.project, self.project_dir = project, Path(project_dir)
        self.bp_numbers = {}
        host, port = cfg["ps5_ip"], int(cfg.get("gdb_port", 2159))
        elf_local = self.to_gdb_path(Path(project_dir) / elf_rel)
        elf_name = Path(elf_rel).name
        remote = f"{(cfg.get('gdb_remote_dir') or '/user/temp').rstrip('/')}/{elf_name}"

        self._set_state("starting")
        self.emit("debug", f"$ {' '.join(argv)} --interpreter=mi3", level="cmd")
        try:
            proc = subprocess.Popen(
                argv + ["--interpreter=mi3", "-q", "-nx"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=str(project_dir) if not self.wsl else None,
            )
        except FileNotFoundError:
            self._set_state("idle")
            raise DebugError(f"GDB introuvable (« {argv[0]} »). Installe gdb-multiarch (gdb 15+) "
                             "ou indique son chemin dans Parametres.")
        self.proc = proc
        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()

        try:
            self.cmd("-gdb-set mi-async on")
            self.cmd("-gdb-set architecture i386:x86-64")
            self.cmd("-gdb-set confirm off")
            if cfg.get("sysroot"):  # mode PC : bibliotheques lues directement sur la machine
                self.cmd(f"-gdb-set sysroot {cfg['sysroot']}")
            self.emit("debug", f"→ Connexion a gdbsrv {host}:{port}…", level="cmd")
            try:
                self.cmd(f"-target-select extended-remote {host}:{port}", timeout=15)
            except DebugError as e:
                raise DebugError(f"Connexion a gdbsrv impossible ({e}). Le payload gdbsrv est-il lance sur la PS5 ?")
            self.emit("debug", "✔ Connecte a la PS5.", level="ok")
            self.cmd(f"-file-exec-and-symbols {mi_quote(elf_local)}")
            self.emit("debug", f"→ Envoi de {elf_name} vers {remote}…", level="cmd")
            self.cmd(f"-target-file-put {mi_quote(elf_local)} {mi_quote(remote)}", timeout=60)
            self.cmd(f"-gdb-set remote exec-file {remote}")
            for file, lines in self.breakpoints.get(project, {}).items():
                for line in sorted(lines):
                    self._insert_bp(file, line)
            self.emit("debug", "▶ Lancement (arret sur main)…", level="cmd")
            self.cmd("-exec-run --start", timeout=30)
        except DebugError as e:
            self.emit("debug", f"✖ {e}", level="error")
            self.stop()
            raise

    def _on_stopped(self, data):
        reason = data.get("reason", "")
        if reason.startswith("exited"):
            code = data.get("exit-code", "0")
            self.emit("debug", f"■ Le programme s'est termine (code {int(code, 8) if code.isdigit() else code}).", level="ok")
            self._set_state("exited")
            threading.Thread(target=self.stop, daemon=True).start()
            return

        frame = data.get("frame", {})
        crash = None
        if reason == "signal-received":
            crash = f"{data.get('signal-name', 'signal')} — {data.get('signal-meaning', '')}".strip(" —")
            self.emit("debug", f"💥 Crash : {crash} dans {frame.get('func', '?')}()", level="error")
        elif reason == "breakpoint-hit":
            self.emit("debug", f"● Point d'arret atteint : {frame.get('func', '?')}() ligne {frame.get('line', '?')}", level="warn")

        stack, variables = [], []
        r = self._try("-stack-list-frames 0 50")
        for f in (r or {}).get("stack", []):
            stack.append({"level": int(f.get("level", 0)), "func": f.get("func", "??"),
                          "file": self.rel_file(f), "src": f.get("file"),
                          "line": int(f["line"]) if f.get("line") else None, "addr": f.get("addr")})
        r = self._try("-stack-list-variables --all-values")
        for v in (r or {}).get("variables", []):
            variables.append({"name": v.get("name"), "value": v.get("value", ""), "arg": v.get("arg") == "1"})

        self._set_state("stopped", reason=reason, crash=crash,
                        file=self.rel_file(frame), line=int(frame["line"]) if frame.get("line") else None,
                        func=frame.get("func"), stack=stack, variables=variables)

    def select_frame(self, level):
        self.cmd(f"-stack-select-frame {int(level)}")
        r = self.cmd("-stack-list-variables --all-values")
        return [{"name": v.get("name"), "value": v.get("value", ""), "arg": v.get("arg") == "1"}
                for v in r.get("variables", [])]

    def evaluate(self, expr):
        if self.state != "stopped":
            raise DebugError("Le programme doit etre en pause")
        return self.cmd(f"-data-evaluate-expression {mi_quote(expr)}").get("value", "")

    def control(self, action):
        commands = {
            "continue": "-exec-continue", "next": "-exec-next", "step": "-exec-step",
            "finish": "-exec-finish", "pause": "-exec-interrupt",
        }
        if action == "stop":
            return self.stop()
        if action not in commands:
            raise DebugError("Action inconnue")
        if action == "pause" and self.state != "running":
            return
        if action != "pause" and self.state != "stopped":
            raise DebugError("Le programme doit etre en pause")
        self.cmd(commands[action])

    def stop(self):
        proc = self.proc
        if not proc:
            return
        try:
            if self.state == "running":
                self._try("-exec-interrupt")
            with self.lock:
                proc.stdin.write("-exec-kill\n-gdb-exit\n")
                proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
