"""
Traduction de Forge5.

Chaque langue est un fichier locales/<code>.json :
    {
      "name": "English",
      "ui":  { "cle": "texte de l'interface", ... },
      "srv": { "message du serveur avec {variables}": "traduction avec {variables}", ... }
    }

Les messages du serveur sont ecrits une seule fois dans le code ; ils sont
traduits au moment ou ils sont envoyes a l'interface. Un message sans
traduction reste dans sa version d'origine.

Pour ajouter une langue : copier locales/en.json, le traduire, et c'est tout.
"""
import json
import re
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_PREFIXES = ("✖ ", "⚠ ", "✔ ", "→ ", "■ ", "● ", "💥 ", "💡 ", "⟳ ", "▶ ", "💻 ", "🔔 ", "[PC] ")
_cache = {}


def available():
    out = []
    for f in sorted(LOCALES_DIR.glob("*.json")):
        try:
            out.append({"code": f.stem, "name": json.loads(f.read_text(encoding="utf-8")).get("name", f.stem)})
        except (OSError, ValueError):
            pass
    return out


def load(lang):
    if lang in _cache:
        return _cache[lang]
    f = LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    exact, patterns = {}, []
    for src, dst in data.get("srv", {}).items():
        if "{" in src:
            names = []

            def repl(m):
                names.append(m[1])
                return f"(?P<{m[1]}>.*?)"
            rx = re.compile("^" + re.sub(r"\\\{(\w+)\\\}", repl, re.escape(src)) + "$", re.S)
            patterns.append((rx, dst))
        else:
            exact[src] = dst
    _cache[lang] = {"data": data, "exact": exact, "patterns": patterns}
    return _cache[lang]


def ui_strings(lang):
    base = load("fr")["data"].get("ui", {})
    return {**base, **load(lang)["data"].get("ui", {})}


def tr(text, lang, _depth=0):
    """Traduit un message du serveur dans la langue choisie."""
    if not text or not isinstance(text, str) or _depth > 3:
        return text
    cat = load(lang)
    if text in cat["exact"]:
        return cat["exact"][text]
    for rx, dst in cat["patterns"]:
        m = rx.match(text)
        if m:
            vals = {k: tr(v, lang, _depth + 1) for k, v in m.groupdict().items()}
            try:
                return dst.format(**vals)
            except (KeyError, IndexError, ValueError):
                return text
    # Message precede d'un symbole (✖, ⚠…) : on traduit la suite
    for p in _PREFIXES:
        if text.startswith(p) and len(text) > len(p):
            rest = tr(text[len(p):], lang, _depth + 1)
            if rest != text[len(p):]:
                return p + rest
    return text
