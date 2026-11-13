<p align="center"><img src="docs/forge5-icone.png" width="170" alt="Forge5"></p>

# Forge5 — atelier homebrew PS5

**par cedsaill** · v1.0.0 · [English below](#english)

Écris ton code, appuie sur **F5** : Forge5 le compile, le lance — sur la PS5 **ou directement
sur ton PC, sans console** — et affiche sa sortie en direct. Débogueur visuel, gestionnaire
de payloads et gestion de [Launcher5](https://github.com/cedsaill3/Launcher5) inclus.

## Fonctions

### Écrire
- Nouveau projet prêt à compiler (démo : infos console, température, notification, journal noyau),
  ajout et suppression de fichiers
- Éditeur avec coloration syntaxique C/C++, numéros de ligne, erreurs marquées dans la marge
- **Sauvegarde automatique**, et **rechargement automatique** si un fichier est modifié
  dans un autre éditeur (VS Code, Notepad++…)

### Compiler et lancer
- Compilation en un clic avec le SDK PS5 ; erreurs regroupées dans l'onglet **Problèmes**
  (un clic ouvre la bonne ligne)
- Envoi à l'ELF loader de la PS5 (port 9021) et sortie du programme en direct
- **Mode Auto (⟳)** : chaque modification est recompilée et relancée toute seule

### 💻 Mode sans PS5
Choisis **« 💻 PC (sans PS5) »** en haut : ton homebrew est compilé pour PC et tourne
avec une **PS5 simulée** :
- `klog_printf` → onglet « Journal noyau »
- `sceNotificationSend` / `sceKernelSendNotificationRequest` → vraie bulle de notification
- modèle de console, numéro de série, températures, fréquence CPU, firmware, utilisateur
- les fonctions `kernel_*` qui touchent à la mémoire du noyau renvoient une erreur et le
  signalent (elles n'existent que sur une vraie PS5)

Dans ton code, `__FORGE5_SIM__` est défini en mode PC si tu veux un comportement différent.

### 🐞 Débogueur visuel (PS5 et PC)
Points d'arrêt (clic dans la marge ou F9), Continuer (F8), Pas à pas (F10), Entrer (F11),
Sortir (Maj+F11), Pause, Arrêter (Maj+F5), variables locales (en orange quand elles
changent), espions, pile d'appels cliquable, ligne d'un crash (SIGSEGV…) surlignée en rouge.

### 📦 Gestionnaire de payloads
Télécharge depuis leurs **dépôts officiels** les payloads utiles, vérifie que ce sont de
vrais fichiers ELF de la bonne taille, garde leur empreinte SHA-256 et les **met à jour
automatiquement**. Envoi à la PS5 en un clic, avec un voyant indiquant s'ils tournent déjà.

| Payload | Rôle | Port |
|---|---|---|
| elfldr | reçoit et lance les programmes | 9021 |
| gdbsrv | débogueur | 2159 |
| klogsrv | journal noyau | 3232 |
| ftpsrv | transfert de fichiers | 2121 |
| shsrv | terminal à distance | 2323 |
| websrv | serveur web / lanceur de homebrews | 8080 |

### 🚀 Launcher5
Le bouton **🚀 Launcher5** gère le game loader [Launcher5](https://github.com/cedsaill3/Launcher5)
depuis le PC (par FTP, payload `ftpsrv`) :
- **Mon menu** : tous tes jeux avec leur image ; glisser-déposer pour ranger, dossiers,
  renommer, masquer, choisir l'affichage (Cover Flow, Grille, Carrousel)
- **Thème** : galerie de thèmes, nuanciers néon, réglages, **aperçu fidèle** de l'écran de la TV
- **Fonds et musiques** : envoie tes images et tes musiques
- **Installer / mettre à jour** : télécharge la dernière version de Launcher5 et l'envoie à la PS5

### 🌐 Multilingue
Français, English, Español, Deutsch, Italiano, Português — choix en haut à droite,
langue du navigateur détectée au premier lancement.

## Prérequis
| Pour… | Il faut |
|---|---|
| Lancer Forge5 | Windows 10/11, **Python 3.10+**, Chrome ou Edge (fenêtre dédiée, sinon le navigateur par défaut) |
| Compiler pour la PS5 | **WSL (Ubuntu)** + le **SDK PS5** et ses dépendances (étapes ci-dessous) |
| Mode sans PS5 et débogueur | WSL (Ubuntu) avec `build-essential gdb-multiarch gdbserver` |
| Envoyer à la PS5 | PS5 jailbreakée avec un **chargeur ELF** actif (port 9021) |
| Débogueur, journal noyau, Launcher5 | payloads `gdbsrv`, `klogsrv`, `ftpsrv` lancés sur la PS5 — Forge5 les télécharge et les envoie (bouton 📦 Payloads) |

### Installation pas à pas
1. **Python** : installe Python 3.10 ou plus depuis https://www.python.org/downloads/
   (coche « Add python.exe to PATH »).
2. **WSL** : dans un terminal Windows ouvert en administrateur, tape `wsl --install`,
   redémarre le PC, puis ouvre « Ubuntu » et choisis un nom et un mot de passe.
3. **Outils dans Ubuntu** :

       sudo apt update
       sudo apt install bash clang-18 lld-18 make wget unzip build-essential gdb-multiarch gdbserver

4. **SDK PS5** ([ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk)), toujours dans Ubuntu :

       wget https://github.com/ps5-payload-dev/sdk/releases/latest/download/ps5-payload-sdk.zip
       sudo unzip -d /opt ps5-payload-sdk.zip

5. **Forge5** : télécharge `Forge5-v1.0.0.zip` dans les [Releases](../../releases), décompresse-le,
   puis double-clique sur **`Lancer-Forge5.bat`**. Fermer la fenêtre arrête Forge5.
6. **Réglages** (⚙ Paramètres) :
   - **IP de la PS5** : bouton **🔍 Détecter**, Forge5 trouve la console tout seul
   - **Dossier du SDK** : `/opt/ps5-payload-sdk`
   - **Commande de compilation** : `wsl make`
   - **GDB** : `wsl gdb-multiarch`
   - le bouton **Vérifier les mises à jour du SDK** confirme que le SDK est bien trouvé

Internet sert seulement à télécharger les payloads, Launcher5 et à vérifier les mises à jour
du SDK. Forge5 n'écoute que sur ton PC (127.0.0.1) et refuse les requêtes venant d'autres sites.

Testé avec une PS5 CFI-1216A (firmware 13.60) et le SDK v0.45.

## Licence
Forge5 © 2026 cedsaill.

---

## English

**Forge5** is a PS5 homebrew workshop for Windows, by **cedsaill**: write C/C++, press **F5** to
build and run on the PS5 — or on your PC with a simulated PS5 — with live output, a visual
debugger, a payload manager and [Launcher5](https://github.com/cedsaill3/Launcher5) management.
UI in French, English, Spanish, German, Italian and Portuguese.

**Requirements:** Windows 10/11, Python 3.10+, WSL (Ubuntu) with
`clang-18 lld-18 make wget unzip build-essential gdb-multiarch gdbserver`, the
[PS5 payload SDK](https://github.com/ps5-payload-dev/sdk) unzipped to `/opt`, and a jailbroken PS5
with an ELF loader on port 9021.

**Install:** download `Forge5-v1.0.0.zip` from [Releases](../../releases), unzip, double-click
`Lancer-Forge5.bat`, then in Settings: SDK `/opt/ps5-payload-sdk`, build command `wsl make`,
GDB `wsl gdb-multiarch`.
