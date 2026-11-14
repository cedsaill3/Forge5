"""
Forge5 - jaquettes depuis SteamGridDB (https://www.steamgriddb.com).

Recherche d'un jeu, liste de ses jaquettes verticales, puis telechargement
de celle choisie. Il faut une cle d'API gratuite (compte SteamGridDB,
Preferences > API), enregistree dans les reglages de Forge5.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.steamgriddb.com/api/v2"
AGENT = "Forge5"
MAX_IMAGE = 15 * 1024 * 1024


class SteamGridDB:
    def __init__(self, get_key):
        self.get_key = get_key          # lit la cle dans la configuration a chaque appel

    def _get(self, path, params=None):
        key = (self.get_key() or "").strip()
        if not key:
            raise PermissionError("Ajoute d'abord ta clé SteamGridDB (bouton 🔑).")
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError("Clé SteamGridDB refusée : vérifie-la (bouton 🔑).") from None
            if e.code == 404:
                return []
            raise ConnectionError("SteamGridDB ne répond pas correctement, réessaie plus tard.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ConnectionError("SteamGridDB injoignable : vérifie ta connexion Internet.") from None
        if not data.get("success"):
            raise ConnectionError("SteamGridDB ne répond pas correctement, réessaie plus tard.")
        return data.get("data") or []

    def search(self, term):
        term = (term or "").strip()[:100]
        if not term:
            return []
        # « Jeu™ : Édition ® » -> « Jeu Edition » : la recherche marche mieux sans symboles
        term = re.sub(r"[™®©:]", " ", term)
        games = self._get("/search/autocomplete/" + urllib.parse.quote(term, safe=""))
        return [{"id": g.get("id"), "name": g.get("name", ""), "year": _year(g.get("release_date"))}
                for g in games if isinstance(g, dict) and isinstance(g.get("id"), int)][:20]

    def covers(self, game_id):
        game_id = int(game_id)
        grids = self._get(f"/grids/game/{game_id}", {
            "dimensions": "600x900,342x482,660x930", "types": "static",
            "mimes": "image/png,image/jpeg", "nsfw": "false", "humor": "false"})
        return [{"url": g["url"], "thumb": g.get("thumb") or g["url"], "w": g.get("width"), "h": g.get("height"),
                 "score": g.get("score", 0)}
                for g in grids if isinstance(g, dict) and _is_cdn(g.get("url"))][:60]

    def download(self, url):
        """Image choisie : uniquement depuis les serveurs de SteamGridDB, PNG ou JPEG."""
        if not _is_cdn(url):
            raise ValueError("Adresse d'image refusée.")
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(MAX_IMAGE + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ConnectionError("Téléchargement de la jaquette impossible, réessaie.") from None
        if len(data) > MAX_IMAGE:
            raise ValueError("Image trop grosse.")
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, "png"
        if data[:3] == b"\xff\xd8\xff":
            return data, "jpg"
        raise ValueError("Format d'image non pris en charge (PNG ou JPEG seulement).")


def _year(ts):
    try:
        return time.gmtime(int(ts)).tm_year if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _is_cdn(url):
    try:
        u = urllib.parse.urlparse(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "steamgriddb.com" or host.endswith(".steamgriddb.com"))
