"""
Gestionnaire de payloads de Forge5.

Telecharge les payloads utiles au developpement depuis leurs depots
officiels (releases GitHub de ps5-payload-dev), verifie qu'il s'agit bien
de fichiers ELF, garde une empreinte SHA-256, cherche les mises a jour
automatiquement et envoie un payload a la PS5 en un clic.
"""
import hashlib
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "payloads"
STATE_FILE = STORE / "state.json"
API = "https://api.github.com/repos/{repo}/releases/latest"
CHECK_EVERY = 6 * 3600          # verification automatique toutes les 6 h

# Seuls ces depots et ces fichiers sont acceptes.
CATALOG = [
    {"id": "elfldr",  "name": "elfldr",  "repo": "ps5-payload-dev/elfldr",  "asset": "elfldr-ps5.elf",  "port": 9021, "send": False},
    {"id": "gdbsrv",  "name": "gdbsrv",  "repo": "ps5-payload-dev/gdbsrv",  "asset": "gdbsrv-ps5.elf",  "port": 2159, "send": True},
    {"id": "klogsrv", "name": "klogsrv", "repo": "ps5-payload-dev/klogsrv", "asset": "klogsrv-ps5.elf", "port": 3232, "send": True},
    {"id": "ftpsrv",  "name": "ftpsrv",  "repo": "ps5-payload-dev/ftpsrv",  "asset": "ftpsrv-ps5.elf",  "port": 2121, "send": True},
    {"id": "shsrv",   "name": "shsrv",   "repo": "ps5-payload-dev/shsrv",   "asset": "shsrv-ps5.elf",   "port": 2323, "send": True},
    {"id": "websrv",  "name": "websrv",  "repo": "ps5-payload-dev/websrv",  "asset": "websrv-ps5.elf",  "port": 8080, "send": True},
]
BY_ID = {p["id"]: p for p in CATALOG}


class PayloadManager:
    def __init__(self, emit, get_config):
        self.emit = emit
        self.cfg = get_config
        self.lock = threading.Lock()
        self.latest = {}            # id -> {tag, date, url, size}
        self.last_check = None
        self.checking = False
        STORE.mkdir(exist_ok=True)
        self.state = self._load()

    # ------------------------------------------------------------------ #
    def _load(self):
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)

    def path(self, pid):
        return STORE / pid / BY_ID[pid]["asset"]

    def _request(self, url, timeout=15):
        return urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "Forge5", "Accept": "application/vnd.github+json"}), timeout=timeout)

    # ------------------------------------------------------------------ #
    # Etat pour l'interface
    # ------------------------------------------------------------------ #
    def status(self):
        items = []
        for p in CATALOG:
            inst = self.state.get(p["id"])
            if inst and not self.path(p["id"]).is_file():
                inst = None
            lat = self.latest.get(p["id"])
            items.append({
                **{k: p[k] for k in ("id", "name", "port", "send", "repo")},
                "installed": inst,
                "latest": lat,
                "update": bool(lat and (not inst or inst.get("tag") != lat.get("tag"))),
            })
        return {"items": items, "last_check": self.last_check, "checking": self.checking,
                "auto": bool(self.cfg().get("payloads_auto_update", True)),
                "updates": sum(1 for i in items if i["update"] and i["installed"])}

    # ------------------------------------------------------------------ #
    # Mises a jour
    # ------------------------------------------------------------------ #
    def check(self, auto_download=False):
        if self.checking:
            return
        self.checking = True
        self.emit("payloads", **self.status())
        errors = 0
        for p in CATALOG:
            try:
                with self._request(API.format(repo=p["repo"])) as r:
                    j = json.loads(r.read())
                asset = next((a for a in j.get("assets", []) if a.get("name") == p["asset"]), None)
                if asset:
                    self.latest[p["id"]] = {"tag": j.get("tag_name"), "date": (j.get("published_at") or "")[:10],
                                            "url": asset["browser_download_url"], "size": asset.get("size")}
            except Exception as e:  # noqa: BLE001
                errors += 1
                last_error = e
        self.last_check = time.strftime("%Y-%m-%d %H:%M")
        self.checking = False
        if errors == len(CATALOG):
            self.emit("system", f"⚠ Verification des mises a jour impossible ({last_error})", level="warn")
        st = self.status()
        if st["updates"]:
            if auto_download:
                for it in st["items"]:
                    if it["update"] and it["installed"]:
                        self.download(it["id"])
            else:
                self.emit("system", f"📦 Payloads : {st['updates']} mise(s) a jour disponible(s).", level="warn")
        self.emit("payloads", **self.status())

    def download(self, pid):
        p = BY_ID.get(pid)
        if not p:
            raise ValueError("Payload inconnu")
        with self.lock:
            if pid not in self.latest:
                self.check()
            lat = self.latest.get(pid)
            if not lat:
                self.emit("system", f"✖ Telechargement de {p['name']} impossible (GitHub injoignable)", level="error")
                return False
            # Securite : uniquement depuis le depot officiel du payload
            if not lat["url"].startswith(f"https://github.com/{p['repo']}/releases/download/"):
                self.emit("system", f"✖ Telechargement de {p['name']} impossible (adresse inattendue)", level="error")
                return False
            try:
                with self._request(lat["url"], timeout=60) as r:
                    data = r.read(20 * 1024 * 1024 + 1)
            except Exception as e:  # noqa: BLE001
                self.emit("system", f"✖ Telechargement de {p['name']} impossible ({e})", level="error")
                return False
            if data[:4] != b"\x7fELF" or (lat.get("size") and len(data) != lat["size"]):
                self.emit("system", f"✖ Telechargement de {p['name']} impossible (fichier invalide)", level="error")
                return False
            dest = self.path(pid)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            old = (self.state.get(pid) or {}).get("tag")
            self.state[pid] = {"tag": lat["tag"], "date": lat["date"], "size": len(data),
                               "sha256": hashlib.sha256(data).hexdigest(),
                               "downloaded": time.strftime("%Y-%m-%d %H:%M")}
            self._save()
        if old and old != lat["tag"]:
            self.emit("system", f"📦 {p['name']} mis a jour : {old} → {lat['tag']}", level="ok")
        else:
            self.emit("system", f"📦 {p['name']} {lat['tag']} telecharge.", level="ok")
        self.emit("payloads", **self.status())
        return True

    def auto_loop(self):
        time.sleep(3)
        while True:
            try:
                self.check(auto_download=bool(self.cfg().get("payloads_auto_update", True)))
            except Exception:  # noqa: BLE001
                self.checking = False
            time.sleep(CHECK_EVERY)

    # ------------------------------------------------------------------ #
    # Envoi a la PS5
    # ------------------------------------------------------------------ #
    def send(self, pid):
        p = BY_ID.get(pid)
        if not p:
            raise ValueError("Payload inconnu")
        f = self.path(pid)
        if not f.is_file():
            self.emit("ps5", f"✖ {p['name']} n'est pas encore telecharge.", level="error")
            return False
        cfg = self.cfg()
        host, port = cfg["ps5_ip"], int(cfg.get("elf_port", 9021))
        data = f.read_bytes()
        self.emit("ps5", f"→ Envoi du payload {p['name']} vers {host}:{port}…", level="cmd")
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(data)
                s.shutdown(socket.SHUT_WR)
                # Les payloads serveurs restent actifs : on lit seulement leurs premiers messages
                s.settimeout(2)
                buf = b""
                end = time.time() + 2.5
                while time.time() < end:
                    try:
                        chunk = s.recv(4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                for line in buf.decode("utf-8", "replace").splitlines():
                    if line.strip():
                        self.emit("ps5", line)
        except OSError as e:
            self.emit("ps5", f"✖ Connexion impossible a {host}:{port} ({e}). La PS5 est-elle allumee, "
                             "jailbreakee, avec l'ELF loader actif ?", level="error")
            return False
        self.emit("ps5", f"✔ {p['name']} envoye a la PS5.", level="ok")
        return True

    def ps5_active(self, port_open):
        host = self.cfg()["ps5_ip"]
        return {p["id"]: port_open(host, p["port"], 0.4) for p in CATALOG}
