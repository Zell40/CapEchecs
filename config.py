# -*- coding: utf-8 -*-
"""
Options Limnoria.

Le dossier sur le serveur peut encore s'appeler JeuEchecs alors que le
projet s'appelle CapEchecs. Limnoria range la config sous le nom du
dossier (self.name()), donc on enregistre les mêmes clés sous les deux noms.
"""
from supybot import conf, registry

_PKG = __name__.split(".", 1)[0]
_PLUGIN_NAMES = []
for _name in (_PKG, "CapEchecs", "JeuEchecs"):
    if _name and _name not in _PLUGIN_NAMES:
        _PLUGIN_NAMES.append(_name)


def _has(plugin, name):
    try:
        plugin.get(name)
        return True
    except registry.NonExistentRegistryEntry:
        return False


def _register_values(plugin):
    def glob(name, spec):
        if not _has(plugin, name):
            conf.registerGlobalValue(plugin, name, spec)

    def chan(name, spec):
        if not _has(plugin, name):
            conf.registerChannelValue(plugin, name, spec)

    glob(
        "allowedChannel",
        registry.String("#Echecs.chat", """Salon autorisé pour le jeu CapEchecs."""),
    )
    glob(
        "stockfishPath",
        registry.String("/usr/games/stockfish", """Chemin vers le moteur Stockfish."""),
    )
    glob(
        "inactivitySeconds",
        registry.Integer(180, """Durée d'inactivité avant arrêt automatique (en secondes)."""),
    )
    glob("thinkTime", registry.Float(0.5, """Temps de réflexion de l'IA (en secondes)."""))
    glob(
        "skillLevel",
        registry.Integer(
            20,
            """Niveau Stockfish (0-20). 20 = force maximale, limité par thinkTime.""",
        ),
    )
    glob(
        "duoTimeout",
        registry.Integer(
            120,
            """Secondes avant annulation d'une partie duo/invitation sans adversaire.""",
        ),
    )
    glob(
        "announceMessage",
        registry.Boolean(True, """Active ou désactive l'annonce globale de début de partie."""),
    )
    glob(
        "announceMessageChannel",
        registry.String("#EntreNous.chat", """Salon où envoyer les annonces globales via BotServ."""),
    )
    glob(
        "welcomeMessage",
        registry.Boolean(
            True,
            """Notice de bienvenue aux utilisateurs qui rejoignent le salon de jeu.""",
        ),
    )
    glob(
        "reviewDepth",
        registry.Integer(
            12,
            """Profondeur Stockfish pour le bilan de partie (8–18).""",
        ),
    )
    chan(
        "quietChannel",
        registry.Boolean(
            False,
            """Réduit les PRIVMSG redondants. Les TAGMSG Orbit et les messages essentiels restent.""",
        ),
    )
    return plugin


def configure(advanced):
    for name in _PLUGIN_NAMES:
        conf.registerPlugin(name, True)


_groups = {}
for _name in _PLUGIN_NAMES:
    _groups[_name] = _register_values(conf.registerPlugin(_name))

CapEchecs = _groups.get("CapEchecs") or _groups[_PKG]
