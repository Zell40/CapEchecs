"""
Plugin Limnoria : jeu d'échecs (CapEchecs).
"""

import supybot
import supybot.world as world
from importlib import reload

__version__ = "2.0.0"
__author__ = supybot.Author("Zell", "Zell", "zell@entrenous.chat")
__contributors__ = {}
__url__ = "https://github.com/Zell40/CapEchecs"

from . import config
from . import plugin
from .local import engine, game, san_fr, tags

# Rechargement des modules internes lors d'un `reload CapEchecs`
reload(san_fr)
reload(engine)
reload(game)
reload(tags)
reload(plugin)

if world.testing:
    from . import test

Class = plugin.Class
configure = config.configure
