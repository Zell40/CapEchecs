# -*- coding: utf-8 -*-
from supybot import conf, registry


def configure(advanced):
    conf.registerPlugin("CapEchecs", True)


CapEchecs = conf.registerPlugin("CapEchecs")

conf.registerGlobalValue(
    CapEchecs,
    "allowedChannel",
    registry.String(
        "#Echecs.chat",
        """Salon autorisé pour le jeu CapEchecs.""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "stockfishPath",
    registry.String("/usr/games/stockfish", """Chemin vers le moteur Stockfish."""),
)

conf.registerGlobalValue(
    CapEchecs,
    "inactivitySeconds",
    registry.Integer(
        180,
        """Durée d'inactivité avant arrêt automatique (en secondes).""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "thinkTime",
    registry.Float(0.5, """Temps de réflexion de l'IA (en secondes)."""),
)

conf.registerGlobalValue(
    CapEchecs,
    "skillLevel",
    registry.Integer(
        20,
        """Niveau Stockfish (0-20). 20 = force maximale, limité par thinkTime.""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "duoTimeout",
    registry.Integer(
        120,
        """Secondes avant annulation d'une partie duo/invitation sans adversaire.""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "announceMessage",
    registry.Boolean(
        True,
        """Active ou désactive l'annonce globale de début de partie.""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "announceMessageChannel",
    registry.String(
        "#EntreNous.chat",
        """Salon où envoyer les annonces globales via BotServ.""",
    ),
)

conf.registerGlobalValue(
    CapEchecs,
    "welcomeMessage",
    registry.Boolean(
        True,
        """Notice de bienvenue aux utilisateurs qui rejoignent le salon de jeu.""",
    ),
)

conf.registerChannelValue(
    CapEchecs,
    "quietChannel",
    registry.Boolean(
        False,
        """Réduit les PRIVMSG redondants. Les TAGMSG Orbit et les messages essentiels restent.""",
    ),
)
