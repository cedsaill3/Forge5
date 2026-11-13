"""
Forge5 - gestion de Launcher5 depuis le PC.

Tout passe par le serveur FTP de la PS5 (ftpsrv, port 2121) : Forge5 lit et
ecrit les fichiers du launcher dans /data/launcher5. Le launcher surveille ces
fichiers et se met a jour tout seul sur la TV (menu, dossiers, theme).

(c) 2026 cedsaill
"""
import ftplib
import io
import json
import re
import socket
import threading
from pathlib import Path

DATA = "/data/launcher5"
DIRS = {"fonds": ("png", "jpg", "jpeg", "webp", "bmp"),
        "musique": ("ogg", "mp3", "flac", "wav", "mod", "xm", "it", "s3m"),
        "themes": ("json",)}
MAX_UPLOAD = 80 * 1024 * 1024
# Version compilee a cote (dossier launcher/ voisin) si elle existe, sinon la derniere
# version publiee de Launcher5, telechargee a chaque installation.
LAUNCHER_ELF = Path(__file__).resolve().parent.parent / "launcher" / "build" / "launcher5.elf"
LAUNCHER_URL = "https://github.com/cedsaill3/Launcher5/releases/latest/download/launcher5.elf"

# Themes fournis avec le launcher (memes valeurs que launcher/src/theme.c)
BUILTIN = [
    # nom, couleur, couleur2, haut, bas, arriere-plan, illustration, halos, particules, forme, sens, coins
    ("Néon", "#00e5ff", "#ff2bd6", "#05081a", "#170a33", "illustration", 45, 100, "peu", "points", "montent", "arrondis"),
    ("Glitch", "#ff00c8", "#00fff0", "#07000f", "#14002a", "illustration", 40, 100, "beaucoup", "etincelles", "montent", "droits"),
    ("Acid", "#c6ff00", "#ff2bd6", "#050805", "#101a05", "illustration", 40, 100, "peu", "etincelles", "montent", "droits"),
    ("Blood", "#ff1f3d", "#8a0012", "#0a0000", "#220005", "illustration", 50, 90, "peu", "points", "tombent", "droits"),
    ("Terminal", "#33ff66", "#00a33a", "#000000", "#000a03", "degrade", 20, 50, "beaucoup", "points", "tombent", "droits"),
    ("Cyberpunk", "#fcee0a", "#00f0ff", "#08080d", "#1d1608", "illustration", 45, 100, "peu", "etincelles", "montent", "droits"),
    ("Synthwave", "#ff3cac", "#2b86c5", "#0d0221", "#2d0b3f", "degrade", 30, 100, "beaucoup", "etincelles", "montent", "arrondis"),
    ("Inferno", "#ff4d1a", "#ffb300", "#100503", "#2e0b05", "illustration", 50, 100, "beaucoup", "etincelles", "montent", "arrondis"),
    ("Toxic", "#39ff14", "#00ffa3", "#030d06", "#0a2214", "illustration", 45, 90, "peu", "points", "montent", "arrondis"),
    ("Matrix", "#00ff41", "#008f11", "#000000", "#001a07", "degrade", 20, 60, "beaucoup", "points", "tombent", "droits"),
    ("Royal", "#b26bff", "#ffd166", "#0b0517", "#221038", "illustration", 45, 100, "peu", "points", "montent", "tres_arrondis"),
    ("Glace", "#7fdbff", "#e8f7ff", "#05101a", "#10293d", "illustration", 45, 80, "beaucoup", "flocons", "tombent", "tres_arrondis"),
    ("Sakura", "#ff8fb8", "#ffd1e0", "#1a0710", "#3a1424", "illustration", 40, 80, "beaucoup", "flocons", "tombent", "tres_arrondis"),
    ("Océan", "#00b4d8", "#48cae4", "#020c1b", "#03254a", "illustration", 50, 80, "peu", "flocons", "montent", "arrondis"),
    ("Noir et or", "#d4af37", "#fff1c1", "#050505", "#15120a", "illustration", 35, 60, "peu", "etincelles", "montent", "droits"),
    ("Minuit", "#c9d1dc", "#5b6b82", "#06080c", "#10141c", "illustration", 55, 30, "aucune", "points", "montent", "arrondis"),
    ("Cinéma", "#e50914", "#ffffff", "#000000", "#0b0b0b", "illustration", 75, 40, "aucune", "points", "montent", "arrondis"),
]
CHOICES = {
    "arriere_plan": ("illustration", "degrade", "image"),
    "particules": ("aucune", "peu", "beaucoup"),
    "forme_particules": ("points", "etincelles", "flocons"),
    "sens": ("montent", "tombent"),
    "coins": ("droits", "arrondis", "tres_arrondis"),
    "horloge": ("24h", "12h", "masquee"),
}


def builtin_themes():
    out = []
    for n, c, c2, top, bot, bg, art, glow, part, form, sens, corners in BUILTIN:
        out.append({"nom": n, "couleur": c, "couleur2": c2, "fond_haut": top, "fond_bas": bot,
                    "arriere_plan": bg, "image": "", "illustration": art, "halos": glow, "particules": part,
                    "forme_particules": form, "sens": sens, "coins": corners, "horloge": "24h",
                    "musique": "", "volume": 60})
    return out


def safe_name(name, kind):
    """Un simple nom de fichier, avec une extension autorisee pour ce dossier."""
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not name or name.startswith(".") or len(name) > 120:
        raise ValueError("Nom de fichier invalide.")
    if kind not in DIRS:
        raise ValueError("Dossier inconnu.")
    if name.rsplit(".", 1)[-1].lower() not in DIRS[kind]:
        raise ValueError("Ce type de fichier n'est pas accepté ici.")
    return name


def clean_theme(t):
    """Theme tel que le launcher le lit (valeurs bornees, couleurs #rrggbb)."""
    if not isinstance(t, dict):
        raise ValueError("Thème invalide.")
    base = builtin_themes()[0]
    out = {}
    out["nom"] = str(t.get("nom") or "Sans nom")[:46]
    for k in ("couleur", "couleur2", "fond_haut", "fond_bas"):
        v = str(t.get(k, base[k]))
        out[k] = v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else base[k]
    for k, allowed in CHOICES.items():
        v = str(t.get(k, base.get(k, allowed[0])))
        out[k] = v if v in allowed else allowed[0]
    for k in ("illustration", "halos", "volume"):
        try:
            out[k] = max(0, min(100, int(t.get(k, base[k]))))
        except (TypeError, ValueError):
            out[k] = base[k]
    out["image"] = str(t.get("image") or "").split("/")[-1][:120]
    m = str(t.get("musique") or "")
    out["musique"] = m if m == "*" else m.split("/")[-1][:120]
    # ordre identique a celui du launcher
    order = ["nom", "couleur", "couleur2", "fond_haut", "fond_bas", "arriere_plan", "image", "illustration", "halos",
             "particules", "forme_particules", "sens", "coins", "horloge", "musique", "volume"]
    return {k: out[k] for k in order}


def theme_file_name(name):
    s = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    return (s or "theme") + ".json"


def clean_menu(m):
    """Verifie la structure du menu avant de l'envoyer a la PS5."""
    if not isinstance(m, dict) or not isinstance(m.get("root"), list):
        raise ValueError("Menu invalide.")

    def nodes(arr, depth):
        if depth > 8:
            raise ValueError("Trop de dossiers imbriqués (8 au maximum).")
        out = []
        for n in arr:
            if isinstance(n, dict) and isinstance(n.get("app"), str):
                out.append({"app": n["app"][:39]})
            elif isinstance(n, dict) and isinstance(n.get("folder"), str):
                col = str(n.get("color", "#3d8bfd"))
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", col):
                    col = "#3d8bfd"
                out.append({"folder": n["folder"][:95] or "Dossier", "color": col.lower(),
                            "items": nodes(n.get("items") or [], depth + 1)})
        return out

    view = m.get("view") if m.get("view") in ("flow", "grid", "classic") else "flow"
    out = {"version": 1, "theme": str(m.get("theme", ""))[:31], "folders": bool(m.get("folders")), "view": view,
           "root": nodes(m["root"], 0),
           "hidden": [str(h)[:39] for h in m.get("hidden", []) if isinstance(h, str)],
           "names": {str(k)[:39]: str(v)[:95] for k, v in (m.get("names") or {}).items() if str(v).strip()},
           "stats": {str(k)[:39]: {"n": int(v.get("n") or 0), "last": int(v.get("last") or 0)}
                     for k, v in (m.get("stats") or {}).items()
                     if isinstance(v, dict) and isinstance(v.get("n", 0), int) and isinstance(v.get("last", 0), int)}
                     if isinstance(m.get("stats"), dict) else {}}
    return out


class Launcher5:
    def __init__(self, config, emit):
        self.config = config          # dictionnaire de configuration de Forge5 (lu a chaque fois)
        self.emit = emit
        self.lock = threading.Lock()  # une seule connexion FTP a la fois
        self.icons = {}

    # -- FTP --
    def _ftp(self):
        ip = self.config.get("ps5_ip")
        port = int(self.config.get("ftp_port") or 2121)
        try:
            f = ftplib.FTP()
            f.connect(ip, port, timeout=8)
            f.login()
            return f
        except (OSError, ftplib.Error):
            raise ConnectionError(f"PS5 injoignable en FTP ({ip}:{port}) : envoie le payload ftpsrv depuis le gestionnaire de payloads.")

    @staticmethod
    def _read(f, path):
        buf = io.BytesIO()
        try:
            f.retrbinary(f"RETR {path}", buf.write)
        except ftplib.error_perm:
            return None
        return buf.getvalue()

    @staticmethod
    def _write(f, path, data):
        f.storbinary(f"STOR {path}", io.BytesIO(data))

    @staticmethod
    def _list(f, path):
        lines = []
        try:
            f.retrlines(f"LIST {path}", lines.append)
        except ftplib.error_perm:
            return []
        out = []
        for ln in lines:
            parts = ln.split(None, 8)
            if len(parts) == 9 and not ln.startswith("d") and not parts[8].startswith("."):
                try:
                    size = int(parts[4])
                except ValueError:
                    size = 0
                out.append({"name": parts[8], "size": size})
        return sorted(out, key=lambda x: x["name"].lower())

    @staticmethod
    def _mkdir(f, path):
        try:
            f.mkd(path)
        except ftplib.error_perm:
            pass

    @staticmethod
    def _json(data):
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # -- lecture de tout l'etat --
    def state(self):
        with self.lock:
            f = self._ftp()
            try:
                menu = self._json(self._read(f, f"{DATA}/menu.json"))
                catalog = self._json(self._read(f, f"{DATA}/catalog.json"))
                theme = self._json(self._read(f, f"{DATA}/theme.json"))
                files = {k: [x for x in self._list(f, f"{DATA}/{k}") if x["name"].rsplit(".", 1)[-1].lower() in v]
                         for k, v in DIRS.items()}
                user_themes = []
                for x in files["themes"]:
                    t = self._json(self._read(f, f"{DATA}/themes/{x['name']}"))
                    if isinstance(t, dict):
                        user_themes.append({"file": x["name"], "theme": clean_theme(t)})
            finally:
                f.quit()
        if menu is None:
            raise FileNotFoundError("Launcher5 n'est pas encore installé sur la PS5 (aucun menu trouvé dans /data/launcher5).")
        if catalog is None:
            catalog = []
            self.emit("ps5", "⚠ Liste des jeux absente : ouvre Launcher5 une fois sur la PS5 avec cette version.", level="warn")
        if catalog:
            self.icons = {a["id"]: a.get("icon", "") for a in catalog if isinstance(a, dict) and a.get("id")}
        return {"menu": menu, "catalog": catalog, "theme": clean_theme(theme) if isinstance(theme, dict) else None,
                "files": files, "user_themes": user_themes, "builtin": builtin_themes(),
                "elf": True}

    # -- images et fichiers --
    def icon(self, app_id):
        path = self.icons.get(app_id)
        if not path or not path.startswith("/"):
            return None
        with self.lock:
            f = self._ftp()
            try:
                return self._read(f, path)
            finally:
                f.quit()

    def get_file(self, kind, name):
        name = safe_name(name, kind)
        with self.lock:
            f = self._ftp()
            try:
                data = self._read(f, f"{DATA}/{kind}/{name}")
            finally:
                f.quit()
        if data is None:
            raise FileNotFoundError("Fichier introuvable sur la PS5.")
        return data

    def upload(self, kind, name, data):
        name = safe_name(name, kind)
        if not data or len(data) > MAX_UPLOAD:
            raise ValueError("Fichier vide ou trop gros (80 Mo au maximum).")
        with self.lock:
            f = self._ftp()
            try:
                self._mkdir(f, f"{DATA}/{kind}")
                self._write(f, f"{DATA}/{kind}/{name}", data)
            finally:
                f.quit()
        self.emit("ps5", f"✔ {name} envoyé dans {kind}/ ({len(data) // 1024} Ko).", level="ok")
        return name

    def delete(self, kind, name):
        name = safe_name(name, kind)
        with self.lock:
            f = self._ftp()
            try:
                try:
                    f.delete(f"{DATA}/{kind}/{name}")
                except ftplib.error_reply:
                    pass  # certains serveurs repondent 226 au lieu de 250
            finally:
                f.quit()
        self.emit("ps5", f"■ {name} supprimé de {kind}/.", level="dim")

    # -- menu et theme --
    def save_menu(self, menu):
        data = json.dumps(clean_menu(menu), ensure_ascii=False, indent=2).encode("utf-8")
        with self.lock:
            f = self._ftp()
            try:
                self._write(f, f"{DATA}/menu.json", data)
            finally:
                f.quit()
        self.emit("ps5", "✔ Menu envoyé à la PS5 : le launcher se met à jour tout seul.", level="ok")

    def save_theme(self, theme, apply=True, save_as=False):
        t = clean_theme(theme)
        data = json.dumps(t, ensure_ascii=False, indent=2).encode("utf-8")
        file = None
        with self.lock:
            f = self._ftp()
            try:
                if save_as:
                    file = theme_file_name(t["nom"])
                    self._mkdir(f, f"{DATA}/themes")
                    self._write(f, f"{DATA}/themes/{file}", data)
                if apply:
                    self._write(f, f"{DATA}/theme.json", data)
            finally:
                f.quit()
        if save_as:
            self.emit("ps5", f"✔ Thème « {t['nom']} » enregistré dans themes/{file}.", level="ok")
        if apply:
            self.emit("ps5", f"✔ Thème « {t['nom']} » appliqué sur la PS5.", level="ok")
        return {"file": file, "theme": t}

    # -- installation / mise a jour du launcher --
    def _launcher_elf(self):
        if LAUNCHER_ELF.is_file():
            return LAUNCHER_ELF.read_bytes()
        self.emit("ps5", "→ Téléchargement de la dernière version de Launcher5…", level="cmd")
        import urllib.request
        try:
            req = urllib.request.Request(LAUNCHER_URL, headers={"User-Agent": "Forge5"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read(64 * 1024 * 1024 + 1)
        except Exception:  # noqa: BLE001
            raise ConnectionError("Téléchargement de Launcher5 impossible : vérifie ta connexion Internet.")
        if data[:4] != b"\x7fELF" or len(data) > 64 * 1024 * 1024:
            raise ValueError("Le fichier Launcher5 téléchargé n'est pas valide.")
        return data

    def install(self):
        data = self._launcher_elf()
        ip, port = self.config.get("ps5_ip"), int(self.config.get("elf_port") or 9021)
        try:
            with socket.create_connection((ip, port), timeout=10) as s:
                s.sendall(data)
        except OSError:
            raise ConnectionError(f"ELF loader injoignable ({ip}:{port}).")
        self.emit("ps5", f"✔ Launcher5 envoyé à la PS5 ({len(data) // 1024} Ko) : il s'installe et s'ouvre.", level="ok")
