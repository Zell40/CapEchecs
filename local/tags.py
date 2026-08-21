# -*- coding: utf-8 -*-
"""
Protocole TAGMSG IRCv3 pour Orbit (même mécanisme que PetitBac).

Le bot envoie des TAGMSG sans corps de message. Les clients texte ignorent
la commande ; Orbit la consomme via on('raw').

Contrat v1
----------
Tags toujours présents :
  +ec=v1          espace de noms échecs (équivalent +pb=v1 du Petit Bac)
  +ev=<event>     nom d'événement
  +gid=<id>       identifiant de partie (ms)

Événements (+ev) et charges utiles (toutes les valeurs sont des chaînes) :

  waiting       mode=duo|invite  creator  invited  timeout
  game_start    mode=ai|pvp  white  black  fen  turn  ply
                skill  tc  clock-w  clock-b  clock-inc  clock-at  rated
  move          nick  color=white|black  san  san-fr  uci
                from  to  promo  captured  check=0|1  mate=0|1
                fen  turn  ply  opening  sans  ucis  clock-w  clock-b  clock-at
  illegal       nick  input  reason=illegal|not-turn|waiting|no-game
  state_sync    status=waiting|playing  mode  white  black  creator  invited
                fen  turn  ply  last-uci  last-san-fr  from  to
                cap-w  cap-b  sans  ucis  waiting=0|1  opening  skill  tc
                clock-w  clock-b  clock-inc  clock-at  rated
  draw_offer    nick
  game_end      result=1-0|0-1|1/2-1/2|*  reason  winner  fen  ply
                opening  sans  ucis  skill  tc  duration  elo-w  elo-b  elo-dw
  elo_sync      nick  account  elo  games  wins  draws  losses
                chesscom  cc-rapid  cc-blitz  cc-bullet  cc-name  cc-title  cc-country
  cc_ask        nick  account  text
  cmd           (client → bot) name  arg|move|uci  — jouer, commencer, elo, lier, …
  cmd_err       name  text

fen : FEN standard, espaces remplacés par `_` (Orbit : split / replace).
sans : SAN FR séparés par des virgules (historique compact).
cap-w / cap-b : symboles python-chess concaténés (PNq…).

Les noms de tags utilisent des tirets (spec IRCv3 : [A-Za-z0-9-]+).
"""
from supybot import ircmsgs, log

NS = "+ec"
NS_VALUE = "v1"
MAX_BYTES = 450


def fen_for_tag(fen):
    return (fen or "").replace(" ", "_")


def _stringify(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def send_event(irc, channel, gid, event_name, **payload):
    """Envoie un TAGMSG structuré. Silencieux pour les clients texte."""
    tags = {
        NS: NS_VALUE,
        "+ev": str(event_name),
        "+gid": str(gid or "0"),
    }
    for key, value in payload.items():
        if value is None:
            continue
        text = _stringify(value)
        if text == "" and key not in ("promo", "captured", "invited", "winner", "sans"):
            continue
        tags["+" + key] = text

    optional = ("sans", "ucis", "opening", "cap-w", "cap-b")
    while True:
        msg = ircmsgs.IrcMsg(command="TAGMSG", args=(channel,), server_tags=tags)
        encoded = str(msg).encode("utf-8")
        if len(encoded) <= MAX_BYTES:
            irc.queueMsg(msg)
            return
        trimmed = False
        for key in optional:
            tag_key = "+" + key
            if tag_key in tags:
                del tags[tag_key]
                trimmed = True
                break
        if not trimmed:
            log.warning(
                "CapEchecs: TAGMSG %s trop long (%d octets), non envoyé",
                event_name, len(encoded),
            )
            return
