# -*- coding: utf-8 -*-
"""Commandes Orbit reçues en TAGMSG IRCv3 (+ec=v1 ; +ev=cmd)."""
from __future__ import print_function

import re
import time

from supybot import ircmsgs, log

from .tags import send_event


class OrbitCmdMixin(object):
    """Client : @+ec=v1;+ev=cmd;+name=<cmd>;+arg=<...> TAGMSG #salon"""

    def _unescape_irc_tag(self, value):
        if not value:
            return ""
        s = str(value)
        out = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                mapping = {":": ";", "s": " ", "r": "\r", "n": "\n", "\\": "\\"}
                out.append(mapping.get(nxt, nxt))
                i += 2
            else:
                out.append(s[i])
                i += 1
        return "".join(out)

    def _ec_tag(self, tags, *names):
        if not tags:
            return ""
        for name in names:
            for key in (name, "+" + str(name).lstrip("+"), str(name).lstrip("+")):
                val = tags.get(key)
                if val not in (None, ""):
                    return str(val)
        return ""

    def _msg_tag(self, msg, name):
        tags = {}
        for src in (getattr(msg, "server_tags", None), getattr(msg, "tags", None)):
            if not src:
                continue
            try:
                tags.update(src if isinstance(src, dict) else dict(src))
            except Exception:
                continue
        return self._unescape_irc_tag(self._ec_tag(tags, name))

    def _orbit_cmd_once(self, nick, name, arg):
        if not hasattr(self, "_orbit_cmd_seen"):
            self._orbit_cmd_seen = {}
        key = (str(nick or "").lower(), str(name or ""), str(arg or ""))
        now = time.time()
        last = self._orbit_cmd_seen.get(key, 0)
        if now - last < 0.45:
            return False
        self._orbit_cmd_seen[key] = now
        if len(self._orbit_cmd_seen) > 80:
            cutoff = now - 10
            self._orbit_cmd_seen = {
                k: v for k, v in self._orbit_cmd_seen.items() if v >= cutoff
            }
        return True

    def _orbit_proxy(self, irc, msg, channel, tokens):
        text = "!" + " ".join(str(t) for t in tokens)
        fake = ircmsgs.IrcMsg(
            prefix=msg.prefix,
            command="PRIVMSG",
            args=(channel, text),
            server_tags=getattr(msg, "server_tags", None) or {},
        )
        try:
            self.Proxy(irc, fake, tokens)
        except Exception as e:
            log.warning("CapEchecs: orbit cmd %s failed: %s", tokens, e)
            send_event(
                irc, channel, "0", "cmd_err",
                name=tokens[0] if tokens else "",
                text=str(e)[:120],
            )

    def _dispatch_orbit_cmd(self, irc, msg, channel, name, arg):
        name = (name or "").lower().strip()
        arg = (arg or "").strip()
        aliases = {
            "j": "jouer",
            "co": "commencer",
            "re": "rejoindre",
            "pl": "plateau",
            "ab": "abandonner",
            "start": "commencer",
            "move": "jouer",
            "resign": "abandonner",
            "draw": "nul",
            "abort": "annuler",
        }
        name = aliases.get(name, name)
        if not name:
            return

        if name in ("sync", "manche", "etat"):
            gs = self.games.get(channel)
            if gs:
                self._emit_sync(irc, channel, gs)
            else:
                send_event(irc, channel, "0", "cmd_err", name="sync", text="idle")
                try:
                    self._emit_elo(
                        irc, channel, msg.nick,
                        self._account_of(irc, msg.nick, msg),
                    )
                    account = self._account_of(irc, msg.nick, msg)
                    if account:
                        self._on_player_enter(irc, channel, msg.nick, account)
                except Exception:
                    pass
            return

        if name == "aide":
            self._orbit_proxy(irc, msg, channel, ["aide"])
            return

        extra = [p for p in re.split(r"\s+", arg) if p] if arg else []
        if name == "commencer":
            self._orbit_proxy(irc, msg, channel, ["commencer"] + extra)
            return
        if name == "rejoindre":
            self._orbit_proxy(irc, msg, channel, ["rejoindre"])
            return
        if name == "jouer":
            if not arg:
                send_event(irc, channel, "0", "cmd_err", name="jouer", text="empty")
                return
            self._orbit_proxy(irc, msg, channel, ["jouer", arg])
            return
        if name in ("plateau", "abandonner", "annuler", "nul", "coups", "fen", "elo"):
            self._orbit_proxy(irc, msg, channel, [name] + extra)
            return
        if name == "lier":
            self._orbit_proxy(irc, msg, channel, ["lier"] + extra)
            return

        send_event(irc, channel, "0", "cmd_err", name=name, text="unknown")

    def _handle_orbit_tagmsg(self, irc, msg):
        if getattr(msg, "command", "") != "TAGMSG":
            return False
        if msg.nick == getattr(irc, "nick", None):
            return False
        if not msg.args:
            return False
        raw_chan = msg.args[0]
        if not raw_chan or raw_chan[0] not in "#&+!":
            return False
        if self._msg_tag(msg, "+ec") != "v1":
            return False
        wanted = (self._game_channel() or "").lower()
        if raw_chan.lower() != wanted:
            return False
        channel = self._canon_channel(raw_chan)
        ev = self._msg_tag(msg, "+ev").lower()
        if ev != "cmd":
            return False
        name = (self._msg_tag(msg, "+name") or self._msg_tag(msg, "+cmd")).lower()
        arg = self._msg_tag(msg, "+arg") or self._msg_tag(msg, "+text") or self._msg_tag(msg, "+move")
        if name in ("jouer", "j", "move") and not arg:
            uci = self._msg_tag(msg, "+uci")
            frm = self._msg_tag(msg, "+from")
            to = self._msg_tag(msg, "+to")
            promo = self._msg_tag(msg, "+promo")
            if uci:
                arg = uci
            elif frm and to:
                arg = frm + to + (promo or "")
        if not name:
            return False
        if not self._orbit_cmd_once(msg.nick, name, arg):
            return True
        log.info("CapEchecs: TAGMSG cmd %s %r from %s on %s", name, arg, msg.nick, channel)
        try:
            self._dispatch_orbit_cmd(irc, msg, channel, name, arg)
        except Exception as e:
            log.warning("CapEchecs: TAGMSG cmd %s: %s", name, e)
            send_event(irc, channel, "0", "cmd_err", name=name, text=str(e)[:120])
        return True

    def doTagmsg(self, irc, msg):
        self._handle_orbit_tagmsg(irc, msg)

    def doTAGMSG(self, irc, msg):
        self._handle_orbit_tagmsg(irc, msg)

    def inFilter(self, irc, msg):
        if getattr(msg, "command", "") == "TAGMSG":
            self._handle_orbit_tagmsg(irc, msg)
        return msg
