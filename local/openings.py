# -*- coding: utf-8 -*-
"""Noms d'ouvertures (plus long préfixe UCI connu)."""
from __future__ import print_function

# Préfixes UCI (sans espaces) → nom FR. Le plus long gagne.
_OPENINGS = (
    ("e2e4e7e5g1f3b8c6f1b5a7a6b5a4g8f6e1g1", "Espagnole, Morphy"),
    ("e2e4e7e5g1f3b8c6f1b5a7a6b5a4", "Ruy Lopez, Morphy"),
    ("e2e4e7e5g1f3b8c6f1b5g8f6", "Espagnole, Berlin"),
    ("e2e4e7e5g1f3b8c6f1b5", "Ruy Lopez (Espagnole)"),
    ("e2e4e7e5g1f3b8c6f1c4g8f6", "Italienne, deux cavaliers"),
    ("e2e4e7e5g1f3b8c6f1c4f8c5", "Giuoco Piano"),
    ("e2e4e7e5g1f3b8c6f1c4", "Partie italienne"),
    ("e2e4e7e5g1f3b8c6d2d4", "Écossaise"),
    ("e2e4e7e5g1f3b8c6c2c3", "Ponziani"),
    ("e2e4e7e5g1f3b8c6", "Partie du cavalier"),
    ("e2e4e7e5g1f3g8f6", "Russe (Petroff)"),
    ("e2e4e7e5f1c4", "Partie du fou"),
    ("e2e4e7e5f2f4", "Gambit du roi"),
    ("e2e4e7e5d2d4", "Centre"),
    ("e2e4e7e5", "Ouverture ouverte"),
    ("e2e4c7c5g1f3d7d6d2d4c5d4f3d4g8f6b1c3a7a6", "Sicilienne Najdorf"),
    ("e2e4c7c5g1f3d7d6d2d4c5d4f3d4g8f6b1c3g7g6", "Sicilienne Dragon"),
    ("e2e4c7c5g1f3d7d6d2d4c5d4f3d4g8f6b1c3e7e6", "Sicilienne Scheveningen"),
    ("e2e4c7c5g1f3b8c6d2d4c5d4f3d4g7g6", "Sicilienne Dragon accéléré"),
    ("e2e4c7c5g1f3b8c6", "Sicilienne (cavalier)"),
    ("e2e4c7c5g1f3e7e6", "Sicilienne, Kan / Taimanov"),
    ("e2e4c7c5g1f3d7d6", "Sicilienne classique"),
    ("e2e4c7c5c2c3", "Sicilienne Alapine"),
    ("e2e4c7c5b1c3", "Sicilienne fermée"),
    ("e2e4c7c5", "Sicilienne"),
    ("e2e4e7e6d2d4d7d5b1c3f8b4", "Française Winawer"),
    ("e2e4e7e6d2d4d7d5b1c3g8f6", "Française classique"),
    ("e2e4e7e6d2d4d7d5e4e5", "Française avance"),
    ("e2e4e7e6d2d4d7d5e4d5", "Française d'échange"),
    ("e2e4e7e6", "Française"),
    ("e2e4c7c6d2d4d7d5b1c3", "Caro-Kann classique"),
    ("e2e4c7c6d2d4d7d5e4e5", "Caro-Kann avance"),
    ("e2e4c7c6", "Caro-Kann"),
    ("e2e4d7d5", "Scandinave"),
    ("e2e4g8f6", "Alekhine"),
    ("e2e4d7d6", "Pirc"),
    ("e2e4g7g6", "Moderne"),
    ("e2e4b8c6", "Nimzowitsch"),
    ("e2e4", "Ouverture du pion roi"),
    ("d2d4g8f6c2c4g7g6b1c3f8g7", "Est-indienne"),
    ("d2d4g8f6c2c4g7g6g2g3", "Est-indienne fianchetto"),
    ("d2d4g8f6c2c4e7e6b1c3f8b4", "Nimzo-indienne"),
    ("d2d4g8f6c2c4e7e6g1f3b7b6", "Ouest-indienne"),
    ("d2d4g8f6c2c4e7e6g1f3d7d5", "Orthodoxe / Catalane"),
    ("d2d4g8f6c2c4c7c5", "Benoni"),
    ("d2d4g8f6c2c4d7d6", "Est-indienne ancienne"),
    ("d2d4g8f6c2c4", "Indienne"),
    ("d2d4d7d5c2c4e7e6", "Gambit dame refusé"),
    ("d2d4d7d5c2c4c7c6", "Slave"),
    ("d2d4d7d5c2c4d5c4", "Gambit dame accepté"),
    ("d2d4d7d5c2c4", "Gambit dame"),
    ("d2d4d7d5g1f3g8f6", "Système London tardif"),
    ("d2d4d7d5", "Fermée (d4 d5)"),
    ("d2d4f7f5", "Hollandaise"),
    ("d2d4", "Ouverture du pion dame"),
    ("c2c4e7e5", "Anglaise, Sicilienne inversée"),
    ("c2c4g8f6", "Anglaise"),
    ("c2c4", "Anglaise"),
    ("g1f3d7d5", "Réti"),
    ("g1f3", "Réti / Barcza"),
    ("g2g3", "Larsen / fianchetto"),
    ("b2b3", "Larsen"),
    ("b1c3", "Van Geet"),
)


def opening_name(ucis):
    """Retourne le nom d'ouverture pour une liste UCI, ou une chaîne vide."""
    if not ucis:
        return ""
    key = "".join(str(u).lower() for u in ucis)
    best = ""
    best_len = 0
    for prefix, name in _OPENINGS:
        plen = len(prefix)
        if plen > best_len and key.startswith(prefix):
            best = name
            best_len = plen
    return best
