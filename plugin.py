# -*- coding: utf-8 -*-
"""Jeu d'échecs IRC (CapEchecs) — commandes, cycle de vie, TAGMSG Orbit."""
from __future__ import print_function

import random
import threading
import time

import chess
from supybot import callbacks, ircmsgs, log, schedule
from supybot.commands import additional, optional, wrap

from .local.engine import close_engine, configure_engine, engine_move, open_engine
from .local.game import (
    GameState,
    UNICODE_PIECES,
    apply_move,
    board_outcome,
    reason_label,
    render_board_lines,
    result_string,
)
from .local.san_fr import parse_move
from .local.tags import send_event

BOLD = "\x02"
RESET = "\x0f"
AI_NICK = "IA"
RESERVED_START = (
    "duo", "ia", "ai",
    "blancs", "blanc", "white",
    "noirs", "noir", "black",
)


def _nick_eq(a, b):
    return bool(a) and bool(b) and a.lower() == b.lower()


def _bold(text):
    return "%s%s%s" % (BOLD, text, RESET)


class CapEchecs(callbacks.Plugin):
    """Parties d'échecs en salon : IA, duo, invitation. TAGMSG +ec=v1 pour Orbit."""

    threaded = True

    def name(self):
        # Nom = dossier du plugin (CapEchecs), pas un collision avec
        # un ancien plugins/JeuEchecs dont la classe aurait été renommée.
        mod = self.__class__.__module__ or "CapEchecs"
        return mod.split(".", 1)[0]

    def __init__(self, irc):
        super(CapEchecs, self).__init__(irc)
        self.games = {}
        self._lock = threading.RLock()

    def die(self):
        with self._lock:
            for channel in list(self.games):
                self._cleanup(channel)
        super(CapEchecs, self).die()

    # ------------------------------------------------------------------
    # Accès / IRC
    # ------------------------------------------------------------------
    def _game_channel(self):
        return self.registryValue("allowedChannel")

    def _in_game_channel(self, irc, msg, silent=False):
        if msg.channel is None:
            if not silent:
                irc.reply("Cette commande s'utilise dans un salon, pas en privé.")
            return False
        wanted = self._game_channel()
        if msg.channel.lower() != wanted.lower():
            if not silent:
                irc.reply("Les parties d'échecs se jouent uniquement sur %s." % wanted)
            return False
        return True

    def _is_op(self, irc, channel, nick):
        chan = irc.state.channels.get(channel) if channel else None
        return bool(chan and chan.isOp(nick))

    def _quiet(self, channel):
        try:
            return bool(self.registryValue("quietChannel", channel))
        except Exception:
            return False

    def _say(self, irc, channel, text, essential=False):
        if essential or not self._quiet(channel):
            irc.queueMsg(ircmsgs.privmsg(channel, text))

    def _notice(self, irc, nick, text):
        irc.queueMsg(ircmsgs.notice(nick, text))

    def _emit(self, irc, channel, gs, event, **payload):
        send_event(irc, channel, gs.gid if gs else "0", event, **payload)

    def _sync_payload(self, gs):
        return {
            "status": "waiting" if gs.waiting_join else "playing",
            "mode": gs.mode,
            "white": gs.players["white"] or "",
            "black": gs.players["black"] or "",
            "creator": gs.creator or "",
            "invited": gs.invited or "",
            "fen": gs.fen_tag(),
            "turn": gs.side_to_move(),
            "ply": gs.ply(),
            "last-uci": gs.last_uci,
            "last-san-fr": gs.last_san_fr,
            "from": gs.last_from,
            "to": gs.last_to,
            "cap-w": gs.captured_str("white"),
            "cap-b": gs.captured_str("black"),
            "sans": ",".join(gs.sans_fr),
            "waiting": "1" if gs.waiting_join else "0",
        }

    def _emit_sync(self, irc, channel, gs):
        self._emit(irc, channel, gs, "state_sync", **self._sync_payload(gs))

    def _emit_move(self, irc, channel, gs, info, nick):
        payload = {
            "nick": nick,
            "color": info.color,
            "san": info.san_en,
            "san-fr": info.san_fr,
            "uci": info.uci,
            "from": info.from_sq,
            "to": info.to_sq,
            "promo": info.promo or "",
            "captured": info.captured or "",
            "check": "1" if info.check else "0",
            "mate": "1" if info.mate else "0",
            "fen": gs.fen_tag(),
            "turn": gs.side_to_move(),
            "ply": gs.ply(),
        }
        self._emit(irc, channel, gs, "move", **payload)

    def _move_line(self, nick, info):
        extra = ""
        if info.captured:
            extra = " (prise %s)" % UNICODE_PIECES.get(info.captured, info.captured)
        flag = ""
        if info.mate:
            flag = " — échec et mat"
        elif info.check:
            flag = " — échec"
        return "%s joue %s%s%s" % (_bold(nick), _bold(info.san_fr), extra, flag)

    # ------------------------------------------------------------------
    # Timers / nettoyage
    # ------------------------------------------------------------------
    def _drop_event(self, name):
        if not name:
            return
        try:
            schedule.removeEvent(name)
        except KeyError:
            pass

    def _cancel_timers(self, gs):
        self._drop_event(gs.idle_event)
        self._drop_event(gs.wait_event)
        gs.idle_event = None
        gs.wait_event = None

    def _arm_idle(self, irc, channel):
        gs = self.games.get(channel)
        if not gs or gs.waiting_join:
            return
        self._drop_event(gs.idle_event)
        secs = int(self.registryValue("inactivitySeconds") or 180)
        name = "CapEchecs.idle.%s" % channel

        def _fire():
            self._end_game(irc, channel, "inactivity")

        schedule.addEvent(_fire, time.time() + secs, name)
        gs.idle_event = name

    def _arm_wait(self, irc, channel):
        gs = self.games.get(channel)
        if not gs or not gs.waiting_join:
            return
        self._drop_event(gs.wait_event)
        secs = int(self.registryValue("duoTimeout") or 120)
        name = "CapEchecs.wait.%s" % channel

        def _fire():
            self._end_game(irc, channel, "timeout")

        schedule.addEvent(_fire, time.time() + secs, name)
        gs.wait_event = name

    def _cleanup(self, channel):
        gs = self.games.pop(channel, None)
        if not gs:
            return
        self._cancel_timers(gs)
        close_engine(gs.engine)
        gs.engine = None

    def _end_game(self, irc, channel, reason, winner=None):
        with self._lock:
            gs = self.games.get(channel)
            if not gs:
                return
            result = result_string(winner, reason)
            self._emit(
                irc,
                channel,
                gs,
                "game_end",
                result=result,
                reason=reason,
                winner=winner or "",
                fen=gs.fen_tag(),
                ply=gs.ply(),
            )
            if winner == "white":
                who = "victoire des Blancs (%s)" % gs.players["white"]
            elif winner == "black":
                who = "victoire des Noirs (%s)" % gs.players["black"]
            elif result == "1/2-1/2":
                who = "nulle"
            else:
                who = "partie stoppée"
            self._say(
                irc,
                channel,
                "Partie terminée — %s (%s). %s" % (who, reason_label(reason), result),
                essential=True,
            )
            self._cleanup(channel)

    def _finish_if_over(self, irc, channel, gs):
        ended = board_outcome(gs.board)
        if not ended:
            return False
        reason, winner = ended
        self._end_game(irc, channel, reason, winner)
        return True

    def _open_ai_engine(self):
        path = self.registryValue("stockfishPath")
        engine = open_engine(path)
        configure_engine(engine, self.registryValue("skillLevel"))
        return engine

    def _announce_launch(self, irc, nick):
        if not self.registryValue("announceMessage"):
            return
        announce_chan = self.registryValue("announceMessageChannel")
        game_chan = self._game_channel()
        text = (
            "\00314Une partie d'\00307\002échecs\002\00314 vient d'être lancée "
            "sur\00303\002 %s\002\00314. Tapez \00303\002/join %s\002\00314 "
            "pour défier %s." % (game_chan, game_chan, nick)
        )
        irc.queueMsg(ircmsgs.privmsg("botserv", "say %s %s" % (announce_chan, text)))

    def _assign_colors(self, gs, a, b, human_color=None):
        if human_color == "white":
            gs.players["white"] = a
            gs.players["black"] = b
        elif human_color == "black":
            gs.players["white"] = b
            gs.players["black"] = a
        elif random.getrandbits(1):
            gs.players["white"] = a
            gs.players["black"] = b
        else:
            gs.players["white"] = b
            gs.players["black"] = a

    def _start_playing(self, irc, channel, gs):
        gs.waiting_join = False
        self._cancel_timers(gs)
        self._emit(
            irc,
            channel,
            gs,
            "game_start",
            mode=gs.mode,
            white=gs.players["white"],
            black=gs.players["black"],
            fen=gs.fen_tag(),
            turn=gs.side_to_move(),
            ply=gs.ply(),
        )
        self._say(
            irc,
            channel,
            "Partie commencée — Blancs : %s — Noirs : %s. Trait aux Blancs."
            % (_bold(gs.players["white"]), _bold(gs.players["black"])),
            essential=True,
        )
        self._arm_idle(irc, channel)

    def _ai_turn(self, irc, channel):
        with self._lock:
            gs = self.games.get(channel)
            if not gs or gs.mode != "ai" or not gs.engine:
                return
            if gs.expected_nick() != AI_NICK:
                return
            gid = gs.gid
            engine = gs.engine
            board = gs.board.copy()
            think = self.registryValue("thinkTime")
        try:
            move = engine_move(engine, board, think)
        except Exception as exc:
            self._end_game(irc, channel, "engine")
            log.warning("CapEchecs: erreur IA: %s", exc)
            return
        with self._lock:
            gs = self.games.get(channel)
            if not gs or gs.gid != gid:
                return
            info = apply_move(gs, move)
            self._emit_move(irc, channel, gs, info, AI_NICK)
            self._say(irc, channel, self._move_line(AI_NICK, info), essential=True)
            if self._finish_if_over(irc, channel, gs):
                return
            self._arm_idle(irc, channel)

    def _player_left(self, irc, nick, channel=None, reason="quit"):
        with self._lock:
            targets = [channel] if channel else list(self.games)
            for chan in targets:
                gs = self.games.get(chan)
                if not gs:
                    continue
                if gs.waiting_join:
                    involved = _nick_eq(nick, gs.creator) or _nick_eq(nick, gs.invited)
                    if involved:
                        self._end_game(irc, chan, reason)
                    continue
                color = gs.color_of(nick)
                if not color:
                    continue
                winner = "black" if color == "white" else "white"
                self._end_game(irc, chan, reason, winner)

    # ------------------------------------------------------------------
    # Commandes joueurs
    # ------------------------------------------------------------------
    def commencer(self, irc, msg, args, mode_or_opponent=None):
        """[<duo|blancs|noirs|pseudo>]

        Démarre une partie contre l'IA, une partie duo, ou invite un joueur.
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        nick = msg.nick
        token = (mode_or_opponent or "").strip()
        key = token.lower()

        with self._lock:
            if channel in self.games:
                irc.reply("Une partie est déjà en cours.")
                return

            if key == "duo":
                gs = GameState("pvp", nick)
                gs.waiting_join = True
                self.games[channel] = gs
                self._emit(
                    irc, channel, gs, "waiting",
                    mode="duo", creator=nick, invited="",
                    timeout=self.registryValue("duoTimeout"),
                )
                self._say(
                    irc, channel,
                    "%s ouvre une partie duo. Tapez !rejoindre pour jouer."
                    % _bold(nick),
                    essential=True,
                )
                self._arm_wait(irc, channel)
                self._announce_launch(irc, nick)
                return

            if token and key not in RESERVED_START and not _nick_eq(token, nick):
                gs = GameState("pvp", nick)
                gs.waiting_join = True
                gs.invited = token
                self.games[channel] = gs
                self._emit(
                    irc, channel, gs, "waiting",
                    mode="invite", creator=nick, invited=token,
                    timeout=self.registryValue("duoTimeout"),
                )
                self._say(
                    irc, channel,
                    "%s invite %s. %s, tapez !rejoindre pour accepter."
                    % (_bold(nick), _bold(token), token),
                    essential=True,
                )
                self._notice(
                    irc, token,
                    "%s t'invite à une partie d'échecs dans %s. Tape !rejoindre."
                    % (nick, channel),
                )
                self._arm_wait(irc, channel)
                self._announce_launch(irc, nick)
                return

            human_color = None
            if key in ("blancs", "blanc", "white"):
                human_color = "white"
            elif key in ("noirs", "noir", "black"):
                human_color = "black"

            try:
                engine = self._open_ai_engine()
            except Exception as exc:
                irc.reply("%s" % exc)
                return

            gs = GameState("ai", nick, engine=engine)
            self.games[channel] = gs
            self._assign_colors(gs, nick, AI_NICK, human_color)
            self._start_playing(irc, channel, gs)
            self._announce_launch(irc, nick)

        if gs.expected_nick() == AI_NICK:
            self._ai_turn(irc, channel)

    commencer = wrap(commencer, [optional("text")])
    co = commencer

    def rejoindre(self, irc, msg, args):
        """Rejoint une partie duo ou accepte une invitation."""
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        nick = msg.nick
        with self._lock:
            gs = self.games.get(channel)
            if not gs or not gs.waiting_join:
                irc.reply("Aucune partie n'attend de joueur.")
                return
            if gs.invited and not _nick_eq(gs.invited, nick):
                irc.reply("Seul %s peut rejoindre cette partie." % gs.invited)
                return
            if _nick_eq(gs.creator, nick):
                irc.reply("Tu as créé cette partie : un autre joueur doit rejoindre.")
                return
            self._assign_colors(gs, gs.creator, nick)
            self._start_playing(irc, channel, gs)

    rejoindre = wrap(rejoindre)
    re = rejoindre

    def jouer(self, irc, msg, args, coup):
        """<coup>

        Joue un coup en SAN français, SAN anglais ou UCI (e2e4).
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        nick = msg.nick
        raw = (coup or "").strip()
        with self._lock:
            gs = self.games.get(channel)
            if not gs:
                irc.reply("Aucune partie en cours.")
                self._emit(irc, channel, None, "illegal", nick=nick, input=raw, reason="no-game")
                return
            if gs.waiting_join:
                irc.reply("La partie n'a pas encore commencé.")
                self._emit(irc, channel, gs, "illegal", nick=nick, input=raw, reason="waiting")
                return
            if not gs.is_player(nick):
                irc.reply("Tu n'es pas dans cette partie.")
                return
            expected = gs.expected_nick()
            if not _nick_eq(expected, nick):
                irc.reply("Ce n'est pas ton tour (%s doit jouer)." % expected)
                self._emit(irc, channel, gs, "illegal", nick=nick, input=raw, reason="not-turn")
                return
            try:
                move = parse_move(raw, gs.board)
            except ValueError as exc:
                irc.reply("%s" % exc)
                self._emit(irc, channel, gs, "illegal", nick=nick, input=raw, reason="illegal")
                return
            info = apply_move(gs, move)
            self._emit_move(irc, channel, gs, info, nick)
            self._say(irc, channel, self._move_line(nick, info), essential=True)
            if self._finish_if_over(irc, channel, gs):
                return
            self._arm_idle(irc, channel)
            need_ai = gs.mode == "ai" and gs.expected_nick() == AI_NICK
        if need_ai:
            self._ai_turn(irc, channel)

    jouer = wrap(jouer, ["text"])
    j = jouer

    def plateau(self, irc, msg, args):
        """Affiche le plateau (vue du joueur si tu es dans la partie)."""
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        gs = self.games.get(channel)
        if not gs:
            irc.reply("Aucune partie en cours.")
            return
        if gs.waiting_join:
            irc.reply("En attente d'un adversaire (%s)." % gs.creator)
            self._emit_sync(irc, channel, gs)
            return
        flip = gs.color_of(msg.nick) == "black"
        for line in render_board_lines(gs.board, flip=flip):
            self._say(irc, channel, line, essential=True)
        cap_w = gs.captured_unicode("white") or "—"
        cap_b = gs.captured_unicode("black") or "—"
        turn_nick = gs.expected_nick() or "?"
        self._say(
            irc,
            channel,
            "Prises Blancs : %s  |  Prises Noirs : %s  |  Trait : %s (%s)"
            % (cap_w, cap_b, "Blancs" if gs.board.turn == chess.WHITE else "Noirs", turn_nick),
            essential=True,
        )
        self._emit_sync(irc, channel, gs)

    plateau = wrap(plateau)
    pl = plateau

    def abandonner(self, irc, msg, args):
        """Abandonne la partie en cours."""
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        with self._lock:
            gs = self.games.get(channel)
            if not gs or gs.waiting_join:
                irc.reply("Aucune partie en cours.")
                return
            color = gs.color_of(msg.nick)
            if not color:
                irc.reply("Tu n'es pas dans cette partie.")
                return
            winner = "black" if color == "white" else "white"
            self._end_game(irc, channel, "resign", winner)

    abandonner = wrap(abandonner)
    ab = abandonner

    def annuler(self, irc, msg, args):
        """Annule une partie en attente, sans coup, ou sur accord des deux joueurs."""
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        nick = msg.nick
        with self._lock:
            gs = self.games.get(channel)
            if not gs:
                irc.reply("Aucune partie en cours.")
                return
            if gs.waiting_join:
                if not (_nick_eq(nick, gs.creator) or _nick_eq(nick, gs.invited) or self._is_op(irc, channel, nick)):
                    irc.reply("Seul le créateur peut annuler cette attente.")
                    return
                self._end_game(irc, channel, "abort")
                return
            if not gs.is_player(nick) and not self._is_op(irc, channel, nick):
                irc.reply("Tu n'es pas dans cette partie.")
                return
            if gs.ply() == 0 or self._is_op(irc, channel, nick):
                self._end_game(irc, channel, "abort")
                return
            if gs.abort_offered_by and not _nick_eq(gs.abort_offered_by, nick):
                self._end_game(irc, channel, "abort")
                return
            gs.abort_offered_by = nick
            self._say(
                irc, channel,
                "%s propose d'annuler. L'adversaire tape !annuler pour confirmer."
                % _bold(nick),
                essential=True,
            )

    annuler = wrap(annuler)

    def nul(self, irc, msg, args):
        """Propose ou accepte une nulle."""
        if not self._in_game_channel(irc, msg):
            return
        channel = msg.channel
        nick = msg.nick
        with self._lock:
            gs = self.games.get(channel)
            if not gs or gs.waiting_join:
                irc.reply("Aucune partie en cours.")
                return
            if not gs.is_player(nick):
                irc.reply("Tu n'es pas dans cette partie.")
                return
            if gs.draw_offered_by and not _nick_eq(gs.draw_offered_by, nick):
                self._end_game(irc, channel, "agree")
                return
            if _nick_eq(gs.draw_offered_by, nick):
                irc.reply("Tu as déjà proposé nulle.")
                return
            gs.draw_offered_by = nick
            self._emit(irc, channel, gs, "draw_offer", nick=nick)
            self._say(
                irc, channel,
                "%s propose nulle. L'adversaire tape !nul pour accepter."
                % _bold(nick),
                essential=True,
            )

    nul = wrap(nul)

    def coups(self, irc, msg, args):
        """Liste les coups joués (SAN français)."""
        if not self._in_game_channel(irc, msg):
            return
        gs = self.games.get(msg.channel)
        if not gs or not gs.sans_fr:
            irc.reply("Aucun coup joué.")
            return
        parts = []
        for i, san in enumerate(gs.sans_fr):
            if i % 2 == 0:
                parts.append("%d.%s" % (i // 2 + 1, san))
            else:
                parts.append(san)
        irc.reply(" ".join(parts))

    coups = wrap(coups)

    def fen(self, irc, msg, args):
        """Affiche le FEN de la position (notice)."""
        if not self._in_game_channel(irc, msg):
            return
        gs = self.games.get(msg.channel)
        if not gs:
            irc.reply("Aucune partie en cours.")
            return
        self._notice(irc, msg.nick, gs.board.fen())
        self._emit_sync(irc, msg.channel, gs)

    fen = wrap(fen)

    def sync(self, irc, msg, args):
        """Renvoie l'état courant en TAGMSG (clients Orbit)."""
        if not self._in_game_channel(irc, msg):
            return
        gs = self.games.get(msg.channel)
        if not gs:
            irc.reply("Aucune partie en cours.")
            return
        self._emit_sync(irc, msg.channel, gs)
        self._notice(irc, msg.nick, "État de la partie envoyé (TAGMSG +ec=v1).")

    sync = wrap(sync)

    def aide(self, irc, msg, args):
        """Affiche les commandes du jeu d'échecs."""
        nick = msg.nick
        channel = msg.channel
        lines = [
            "Échecs — commandes",
            "  !commencer / !co — partie contre l'IA (aléatoire)",
            "  !commencer blancs|noirs — choisir la couleur contre l'IA",
            "  !commencer duo — partie ouverte",
            "  !commencer <pseudo> — invitation",
            "  !rejoindre / !re — rejoindre",
            "  !jouer <coup> / !j — SAN FR (Cf3), SAN EN (Nf3) ou UCI (e2e4)",
            "  !plateau / !pl — plateau",
            "  !coups — historique",
            "  !fen — position FEN (notice)",
            "  !sync — renvoyer l'état Orbit (TAGMSG)",
            "  !nul — proposer / accepter nulle",
            "  !annuler — annuler la partie",
            "  !abandonner / !ab — abandonner",
            "  !aide — cette aide",
        ]
        if channel and self._is_op(irc, channel, nick):
            lines.extend([
                "Opérateur : !echecs config …",
                "  inactivite, duotimeout, salonjeu, message, bienvenue,",
                "  quiet, think, skill, stockfish",
            ])
        # irc.replies : visible dans le salon. Les NOTICE seules ne
        # s'affichent souvent pas dans Orbit et ne comptent pas comme
        # réponse Limnoria (la commande paraît alors muette).
        irc.replies(lines)

    aide = wrap(aide, [])

    @wrap(["something", additional("text")])
    def echecs(self, irc, msg, args, action, rest=None):
        """config [clé] [valeur] — configuration (opérateur)."""
        nick = msg.nick
        channel = msg.channel
        if channel is None:
            irc.reply("Cette commande s'utilise dans un salon.")
            return
        if not self._is_op(irc, channel, nick):
            self._notice(irc, nick, "Il faut être opérateur (@) pour cette commande.")
            return
        if action.lower() != "config":
            irc.reply("Syntaxe : !echecs config …")
            return

        parts = (rest or "").split()
        sub = parts[0].lower() if parts else None
        arg1 = parts[1] if len(parts) > 1 else None
        arg2 = parts[2] if len(parts) > 2 else None

        def _bool(token):
            return token.lower() in ("on", "oui", "true", "1")

        if not sub:
            irc.reply("Inactivité : %s s" % self.registryValue("inactivitySeconds"))
            irc.reply("Timeout duo : %s s" % self.registryValue("duoTimeout"))
            irc.reply("Réflexion IA : %s s" % self.registryValue("thinkTime"))
            irc.reply("Skill IA : %s" % self.registryValue("skillLevel"))
            irc.reply("Stockfish : %s" % self.registryValue("stockfishPath"))
            irc.reply("Salon de jeu : %s" % self.registryValue("allowedChannel"))
            irc.reply("Annonce : %s (%s)" % (
                "on" if self.registryValue("announceMessage") else "off",
                self.registryValue("announceMessageChannel"),
            ))
            irc.reply("Bienvenue : %s" % ("on" if self.registryValue("welcomeMessage") else "off"))
            irc.reply("Quiet : %s" % ("on" if self._quiet(channel) else "off"))
            return

        try:
            if sub == "inactivite" and arg1:
                self.setRegistryValue("inactivitySeconds", int(arg1))
                irc.reply("Inactivité : %s s" % arg1)
                return
            if sub == "duotimeout" and arg1:
                self.setRegistryValue("duoTimeout", int(arg1))
                irc.reply("Timeout duo : %s s" % arg1)
                return
            if sub == "think" and arg1:
                self.setRegistryValue("thinkTime", float(arg1))
                irc.reply("Réflexion IA : %s s" % arg1)
                return
            if sub == "skill" and arg1:
                level = max(0, min(20, int(arg1)))
                self.setRegistryValue("skillLevel", level)
                irc.reply("Skill IA : %s" % level)
                return
            if sub == "stockfish" and arg1:
                self.setRegistryValue("stockfishPath", arg1)
                irc.reply("Stockfish : %s" % arg1)
                return
            if sub == "salonjeu" and arg1:
                self.setRegistryValue("allowedChannel", arg1)
                irc.reply("Salon de jeu : %s" % arg1)
                return
            if sub == "bienvenue" and arg1:
                self.setRegistryValue("welcomeMessage", _bool(arg1))
                irc.reply("Bienvenue : %s" % arg1)
                return
            if sub == "quiet" and arg1:
                self.setRegistryValue("quietChannel", _bool(arg1), channel)
                irc.reply("Quiet : %s" % arg1)
                return
            if sub == "message":
                if not arg1:
                    irc.reply("Annonce : %s (%s)" % (
                        "on" if self.registryValue("announceMessage") else "off",
                        self.registryValue("announceMessageChannel"),
                    ))
                    return
                if arg1.lower() in ("on", "off", "oui", "non", "true", "false"):
                    self.setRegistryValue("announceMessage", _bool(arg1))
                    irc.reply("Annonce : %s" % arg1)
                    return
                if arg1.startswith("#") and arg2:
                    self.setRegistryValue("announceMessageChannel", arg1)
                    self.setRegistryValue("announceMessage", _bool(arg2))
                    irc.reply("Annonce : %s sur %s" % (arg2, arg1))
                    return
        except (TypeError, ValueError):
            irc.reply("Valeur invalide.")
            return
        irc.reply("Syntaxe : !echecs config …")

    # ------------------------------------------------------------------
    # Hooks IRC
    # ------------------------------------------------------------------
    def userJoined(self, irc, channel, nick):
        if nick == irc.nick:
            return
        if channel.lower() != self._game_channel().lower():
            return
        gs = self.games.get(channel)
        if gs:
            self._emit_sync(irc, channel, gs)
        if not self.registryValue("welcomeMessage"):
            return
        self._notice(irc, nick, "Salon d'échecs — !commencer (IA), !commencer duo, !rejoindre, !jouer <coup>, !plateau, !aide")

    def doNick(self, irc, msg):
        old, new = msg.nick, msg.args[0]
        with self._lock:
            for gs in self.games.values():
                gs.rename(old, new)

    def doPart(self, irc, msg):
        self._player_left(irc, msg.nick, channel=msg.args[0], reason="part")

    def doQuit(self, irc, msg):
        self._player_left(irc, msg.nick, reason="quit")

    def doKick(self, irc, msg):
        self._player_left(irc, msg.args[1], channel=msg.args[0], reason="kick")


Class = CapEchecs
