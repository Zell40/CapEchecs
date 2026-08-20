# CapEchecs

Plugin [Limnoria](https://limnoria.net/) pour jouer aux **échecs** sur IRC
(SAN français, Stockfish, TAGMSG IRCv3 pour Orbit).

Tu n’as **pas** à installer chaque fichier un par un. Limnoria charge le
**dossier** `CapEchecs/` comme un seul plugin.

## Structure (ce que Limnoria attend)

À la racine, seulement les fichiers standards d’un plugin :

| Fichier / dossier | Rôle |
|---|---|
| `__init__.py` | Point d’entrée (Limnoria charge ça) |
| `config.py` | Options (`config plugins.CapEchecs ...`) |
| `plugin.py` | Classe principale du jeu |
| `test.py` | Tests Limnoria |
| `local/` | Code interne (SAN FR, moteur, TAGMSG). Tu n’as rien à y toucher pour installer. |

## Installation

### 1. Placer le plugin dans le dossier plugins du bot

Le bot Limnoria a un répertoire `plugins/` (souvent à côté de ton fichier `.conf`).

**Option A — clone Git (recommandé, pour `git pull` ensuite) :**

```bash
cd /chemin/vers/ton/bot/plugins
git clone https://github.com/Zell40/CapEchecs.git CapEchecs
```

Le dossier **doit** s’appeler `CapEchecs` (c’est le nom du plugin).

**Option B — copie manuelle :**

Copie tout le dossier `CapEchecs/` dans `plugins/` du bot. Résultat attendu :

```
bot/
  plugins/
    CapEchecs/
      __init__.py
      config.py
      plugin.py
      local/
```

### 2. Dire à Limnoria où chercher les plugins

Dans la config du bot (`supybot.directories.plugins`), le dossier parent doit être listé. Exemple :

```
config supybot.directories.plugins /chemin/vers/ton/bot/plugins
```

### 3. Charger le plugin

Dans IRC, en tant que propriétaire du bot :

```
load CapEchecs
```

Pour recharger après une mise à jour :

```
reload CapEchecs
```

Si tu avais l’ancien plugin `JeuEchecs` :

```
unload JeuEchecs
load CapEchecs
```

### 4. Mise à jour depuis GitHub

```bash
cd /chemin/vers/ton/bot/plugins/CapEchecs
git pull origin main
```

Puis dans IRC : `reload CapEchecs`.

## Première config utile

```
config plugins.CapEchecs.allowedChannel #Echecs.chat
config plugins.CapEchecs.stockfishPath /usr/games/stockfish
```

Le jeu ne démarre que sur le salon défini dans `allowedChannel`.

Dépendance Python : `python-chess` (`pip install chess`). Stockfish doit être installé sur la machine du bot.

## Commandes

- `!commencer` / `!co` — partie contre l’IA
- `!commencer blancs|noirs` — choisir la couleur
- `!commencer duo` — partie ouverte
- `!commencer <pseudo>` — invitation
- `!rejoindre` / `!re` — rejoindre
- `!jouer <coup>` / `!j` — SAN FR (`Cf3`), SAN EN (`Nf3`) ou UCI (`e2e4`)
- `!plateau` / `!pl` — plateau
- `!coups` — historique
- `!fen` — FEN (notice)
- `!sync` — renvoyer l’état Orbit (TAGMSG)
- `!nul` / `!annuler` / `!abandonner` — nulle, annulation, abandon
- `!aide` — aide
- `!echecs config …` — configuration (opérateur)

## TAGMSG IRCv3 (Orbit)

Même mécanisme que PetitBac (`TAGMSG` + tags client), avec l’espace de noms **`+ec=v1`**.

Les clients texte ignorent `TAGMSG`. Un plugin Orbit écoute `on('raw')` :

```javascript
if (String(msg.command).toUpperCase() !== 'TAGMSG') return;
if (tagVal(tags, '+ec') !== 'v1') return;
```

Événements (`+ev`) : `waiting`, `game_start`, `move`, `illegal`, `state_sync`, `draw_offer`, `game_end`.

Le FEN est envoyé avec des `_` à la place des espaces. Contrat détaillé : docstring de `local/tags.py`.

Pour un salon surtout Orbit :

```
config plugins.CapEchecs.quietChannel #Echecs.chat True
```
