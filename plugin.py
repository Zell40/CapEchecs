# -*- coding: utf-8 -*-
"""Jeu d'échecs IRC (CapEchecs) — commandes, cycle de vie, TAGMSG Orbit."""
from __future__ import print_function

import random
import threading
import time

import chess
from supybot import callbacks, conf, ircmsgs, log, registry, schedule
from supybot.commands import additional, optional, wrap

from .local.engine import analyse_lines, close_engine, configure_engine, engine_move, open_engine
from .local.game import (
    GameState,
    UNICODE_PIECES,
    apply_move,
    board_outcome,
    reason_label,
    render_board_lines,
    result_string,
)
from .local.orbit import OrbitCmdMixin
from .local import history, ratings, review
from .local.san_fr import parse_move, to_san_fr
from .local.tags import send_event

BOLD = "\x02"
RESET = "\x0f"
AI_NICK = "IA"
RESERVED_START = (
    "duo", "ia", "ai",
    "blancs", "blanc", "white",
    "noirs", "noir", "black",
    "debutant", "facile", "moyen", "difficile", "expert",
    "casual", "illimite", "bullet", "blitz", "rapide",
    "classic", "classique", "rapide15",
)

# skill / think / elo affiché / profondeur max / bruit (0–1, coups plus faibles)
SKILL_PRESETS = {
    "debutant": {"skill": 0, "think": 0.04, "elo": 400, "depth": 1, "noise": 0.65},
    "facile": {"skill": 2, "think": 0.08, "elo": 800, "depth": 2, "noise": 0.35},
    "moyen": {"skill": 8, "think": 0.25, "elo": 1400, "depth": 6, "noise": 0.12},
    "difficile": {"skill": 14, "think": 0.70, "elo": 1800, "depth": 12, "noise": 0.0},
    "expert": {"skill": 20, "think": 1.50, "elo": 2200, "depth": None, "noise": 0.0},
}

# Cadences alignées Chess.com : 15+10 = rapide, pas classique.
TIME_PRESETS = {
    "casual": ("casual", 0, 0),
    "illimite": ("casual", 0, 0),
    "bullet": ("bullet", 60, 0),
    "blitz": ("blitz", 180, 2),
    "rapide": ("rapide", 600, 0),
    "rapide15": ("rapide", 900, 10),
    "classic": ("rapide", 900, 10),
    "classique": ("rapide", 900, 10),
}


def _nick_eq(a, b):
    return bool(a) and bool(b) and a.lower() == b.lower()


def _bold(text):
    return "%s%s%s" % (BOLD, text, RESET)


class CapEchecs(OrbitCmdMixin, callbacks.Plugin):
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
        self._cc_checked = {}
        self._cc_asked = {}
        self._cc_pending = {}
        self._review = None

    def die(self):
        self._cancel_review()
        with self._lock:
            for channel in list(self.games):
                self._cleanup(channel)
        super(CapEchecs, self).die()

    # ------------------------------------------------------------------
    # Accès / IRC
    # ------------------------------------------------------------------
    def _conf(self, name, channel=None):
        """Lit une option sous JeuEchecs ou CapEchecs (selon le dossier chargé)."""
        names = []
        for candidate in (self.name(), "CapEchecs", "JeuEchecs"):
            if candidate and candidate not in names:
                names.append(candidate)
        last_error = None
        for plugin_name in names:
            try:
                group = conf.supybot.plugins.get(plugin_name)
                value = group.get(name)
                if channel is not None:
                    try:
                        return value.get(channel)()
                    except Exception:
                        return value()
                return value()
            except (registry.NonExistentRegistryEntry, AttributeError, KeyError) as exc:
                last_error = exc
                continue
        if name == "allowedChannel":
            return "#Echecs.chat"
        if last_error:
            raise last_error
        return None

    def _set_conf(self, name, value, channel=None):
        names = []
        for candidate in (self.name(), "CapEchecs", "JeuEchecs"):
            if candidate and candidate not in names:
                names.append(candidate)
        for plugin_name in names:
            try:
                group = conf.supybot.plugins.get(plugin_name)
                entry = group.get(name)
                if channel is not None:
                    entry.get(channel).setValue(value)
                else:
                    entry.setValue(value)
                return
            except (registry.NonExistentRegistryEntry, AttributeError, KeyError):
                continue
        if channel is not None:
            self.setRegistryValue(name, value, channel)
        else:
            self.setRegistryValue(name, value)

    def _game_channel(self):
        return self._conf("allowedChannel") or "#Echecs.chat"

    def _msg_channel(self, msg):
        """Salon du message. TAGMSG / Proxy n'ont souvent pas msg.channel (PetitBac lit args[0])."""
        ch = getattr(msg, "channel", None)
        if ch:
            return ch
        if getattr(msg, "args", None):
            a = msg.args[0]
            if a and a[0] in "#&+!":
                return a
        return None

    def _canon_channel(self, channel):
        if not channel:
            return channel
        if channel in self.games:
            return channel
        cl = channel.lower()
        for ch in self.games:
            if ch.lower() == cl:
                return ch
        return channel

    def _in_game_channel(self, irc, msg, silent=False):
        channel = self._msg_channel(msg)
        if channel is None:
            if not silent:
                irc.reply("Cette commande s'utilise dans un salon, pas en privé.")
            return False
        wanted = self._game_channel()
        if channel.lower() != wanted.lower():
            if not silent:
                irc.reply("Les parties d'échecs se jouent uniquement sur %s." % wanted)
            return False
        return True

    def _is_op(self, irc, channel, nick):
        chan = irc.state.channels.get(channel) if channel else None
        return bool(chan and chan.isOp(nick))

    def _quiet(self, channel):
        try:
            return bool(self._conf("quietChannel", channel))
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
            "opening": gs.opening_family() or gs.opening(),
            "opening-var": gs.opening_variant(),
            "waiting": "1" if gs.waiting_join else "0",
            "skill": gs.skill or "",
            "tc": gs.tc or "casual",
            "clock-w": int(max(0, gs.clocks.get("white") or 0)),
            "clock-b": int(max(0, gs.clocks.get("black") or 0)),
            "clock-inc": gs.clock_inc,
            "clock-at": int(gs.clock_stamp),
            "rated": "1" if gs.rated else "0",
        }

    def _emit_sync(self, irc, channel, gs):
        self._emit(irc, channel, gs, "state_sync", **self._sync_payload(gs))
        self._emit_roster(irc, channel, gs)
        self._emit_hist_chunks(irc, channel, gs.gid, gs.ucis, gs.sans_fr)

    def _emit_hist_chunks(self, irc, channel, gid, ucis, sans):
        ucis = list(ucis or [])
        sans = list(sans or [])
        i = 0
        while i < len(ucis):
            self._emit(
                irc, channel, None, "hist_chunk",
                gid=gid,
                **{
                    "from": i + 1,
                    "ucis": ",".join(ucis[i:i + 16]),
                    "sans": ",".join(sans[i:i + 16]),
                }
            )
            i += 16

    def _roster_pack(self, nick, elo_override=None):
        who = str(nick or "")
        if not who or who == AI_NICK:
            return "%s|%s||||||" % (who, elo_override or "")
        rec = ratings.player_record(who)
        return "|".join([
            who,
            str(rec.get("elo") or ""),
            str(rec.get("games") or "0"),
            rec.get("chesscom") or "",
            rec.get("cc-rapid") or "",
            rec.get("cc-blitz") or "",
            rec.get("cc-bullet") or "",
            rec.get("cc-verified") or "0",
        ])

    def _emit_roster(self, irc, channel, gs):
        if not gs:
            return
        self._emit(
            irc, channel, gs, "roster",
            pw=self._roster_pack(
                gs.players.get("white"),
                gs.ai_elo if gs.players.get("white") == AI_NICK else None,
            ),
            pb=self._roster_pack(
                gs.players.get("black"),
                gs.ai_elo if gs.players.get("black") == AI_NICK else None,
            ),
        )

    def _refresh_roster(self, irc, channel):
        gs = self.games.get(channel)
        if gs:
            self._emit_roster(irc, channel, gs)

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
            "opening": gs.opening_family() or gs.opening(),
            "opening-var": gs.opening_variant(),
            "clock-w": int(max(0, gs.clocks.get("white") or 0)),
            "clock-b": int(max(0, gs.clocks.get("black") or 0)),
            "clock-at": int(gs.clock_stamp),
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
        self._drop_event(gs.clock_event)
        gs.idle_event = None
        gs.wait_event = None
        gs.clock_event = None

    def _arm_idle(self, irc, channel):
        gs = self.games.get(channel)
        if not gs or gs.waiting_join:
            return
        self._drop_event(gs.idle_event)
        secs = int(self._conf("inactivitySeconds") or 180)
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
        secs = int(self._conf("duoTimeout") or 120)
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
            payload = {
                "result": result,
                "reason": reason,
                "winner": winner or "",
                "fen": gs.fen_tag(),
                "ply": gs.ply(),
                "opening": gs.opening_family() or gs.opening(),
                "opening-var": gs.opening_variant(),
                "skill": gs.skill or "",
                "tc": gs.tc or "casual",
                "duration": gs.duration(),
                "white": gs.players["white"] or "",
                "black": gs.players["black"] or "",
            }
            elo_line = ""
            white_n = gs.players["white"] or ""
            black_n = gs.players["black"] or ""
            if (
                not gs.waiting_join
                and gs.ply() >= 2
                and result in ("1-0", "0-1", "1/2-1/2")
            ):
                if gs.mode == "ai":
                    human = white_n if white_n != AI_NICK else black_n
                    human_white = white_n != AI_NICK
                    ai_elo = gs.ai_elo or (SKILL_PRESETS.get(gs.skill) or {}).get("elo") or 1400
                    updated = ratings.apply_rated_vs_ai(
                        human, ai_elo, result, human_white,
                        account=self._account_of(irc, human),
                    )
                    if updated:
                        rec_h, delta, ai_shown = updated
                        if human_white:
                            payload["elo-w"] = rec_h["elo"]
                            payload["elo-b"] = ai_shown
                            payload["elo-dw"] = delta
                        else:
                            payload["elo-w"] = ai_shown
                            payload["elo-b"] = rec_h["elo"]
                            payload["elo-dw"] = -delta
                        elo_line = " ELO %s %s (%s%d)." % (
                            human, rec_h["elo"], "+" if delta > 0 else "", delta,
                        )
                elif gs.rated:
                    updated = ratings.apply_rated_game(
                        white_n, black_n, result,
                        self._account_of(irc, white_n),
                        self._account_of(irc, black_n),
                    )
                    if updated:
                        rec_w, rec_b, delta_w = updated
                        payload["elo-w"] = rec_w["elo"]
                        payload["elo-b"] = rec_b["elo"]
                        payload["elo-dw"] = delta_w
                        elo_line = " ELO %s %s / %s %s." % (
                            white_n, rec_w["elo"], black_n, rec_b["elo"],
                        )
            if "elo-w" not in payload:
                rec_w = ratings.player_record(white_n) if white_n and white_n != AI_NICK else None
                rec_b = ratings.player_record(black_n) if black_n and black_n != AI_NICK else None
                payload["elo-w"] = (rec_w or {}).get("elo") or (
                    gs.ai_elo if white_n == AI_NICK else ""
                )
                payload["elo-b"] = (rec_b or {}).get("elo") or (
                    gs.ai_elo if black_n == AI_NICK else ""
                )
            self._emit(irc, channel, gs, "game_end", **payload)
            self._emit_hist_chunks(irc, channel, gs.gid, gs.ucis, gs.sans_fr)
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
                "Partie terminée — %s (%s). %s.%s"
                % (who, reason_label(reason), result, elo_line),
                essential=True,
            )
            ucis = list(gs.ucis)
            sans = list(gs.sans_fr)
            gid = gs.gid
            white = gs.players["white"] or ""
            black = gs.players["black"] or ""
            ply_n = gs.ply()
            archive = None
            if ply_n >= 1:
                archive = {
                    "gid": gid,
                    "at": int(time.time()),
                    "white": white,
                    "black": black,
                    "result": result,
                    "reason": reason or "",
                    "tc": gs.tc or "casual",
                    "skill": gs.skill or "",
                    "opening": gs.opening_family() or gs.opening() or "",
                    "openingVar": gs.opening_variant() or "",
                    "duration": gs.duration(),
                    "fen": gs.fen_tag(),
                    "ucis": ucis,
                    "sans": sans,
                }
            self._cleanup(channel)
        humans = [n for n in (white, black) if n and n != AI_NICK]
        aliases = []
        for who in humans:
            acc = self._account_of(irc, who)
            if acc:
                aliases.append(acc)
        if archive:
            saver = getattr(history, "save_game", None) or getattr(history, "save", None)
            try:
                if saver:
                    saver(archive, aliases)
            except TypeError:
                try:
                    saver(archive)
                except Exception as exc:
                    log.warning("CapEchecs: archive partie: %s", exc)
            except Exception as exc:
                log.warning("CapEchecs: archive partie: %s", exc)
        if ply_n >= 2:
            self._start_review(irc, channel, gid, ucis, sans, white, black)
        for who in humans:
            try:
                self._emit_elo(irc, channel, who, self._account_of(irc, who))
            except Exception:
                pass
            try:
                self._emit_history_list(irc, channel, who)
            except Exception:
                pass

    def _review_event(self):
        return "CapEchecs.review"

    def _cancel_review(self):
        self._drop_event(self._review_event())
        job = self._review
        self._review = None
        if job:
            close_engine(job.get("engine"))

    def _start_review(self, irc, channel, gid, ucis, sans, white, black):
        self._cancel_review()
        path = self._conf("stockfishPath")
        try:
            engine = open_engine(path)
            configure_engine(engine, 20, elo=None)
        except Exception as exc:
            log.warning("CapEchecs: bilan Stockfish: %s", exc)
            self._emit(
                irc, channel, None, "review_done",
                gid=gid, ok="0", text="Analyse indisponible",
            )
            return
        try:
            depth = int(self._conf("reviewDepth") or 12)
        except Exception:
            depth = 12
        self._review = {
            "irc": irc,
            "channel": channel,
            "gid": gid,
            "ucis": [str(u) for u in (ucis or [])],
            "sans": list(sans or []),
            "white": white,
            "black": black,
            "engine": engine,
            "board": chess.Board(),
            "depth": max(8, min(18, depth)),
            "i": 0,
            "rows": [],
            "chunk": [],
        }
        self._emit(
            irc, channel, None, "review_start",
            gid=gid, n=len(ucis or []), status="run",
        )
        schedule.addEvent(self._review_tick, time.time() + 0.05, self._review_event())

    def _emit_review_chunk(self, job, rows):
        if not rows:
            return
        start = rows[0]["ply"]
        self._emit(
            job["irc"], job["channel"], None, "review_chunk",
            gid=job["gid"],
            **{
                "from": start,
                "cls": ",".join(r["cls"] for r in rows),
                "ev": ",".join(str(r["ev"]) for r in rows),
                "bp": ",".join(r["bp"] for r in rows),
                "bs": ",".join(r["bs"] for r in rows),
            }
        )

    def _review_tick(self):
        job = self._review
        if not job:
            return
        irc = job["irc"]
        channel = job["channel"]
        engine = job["engine"]
        board = job["board"]
        ucis = job["ucis"]
        depth = job["depth"]
        batch = 3
        try:
            while batch > 0 and job["i"] < len(ucis):
                batch -= 1
                idx = job["i"]
                raw_uci = ucis[idx]
                try:
                    move = chess.Move.from_uci(raw_uci)
                except ValueError:
                    job["i"] += 1
                    continue
                if move not in board.legal_moves:
                    job["i"] += 1
                    continue
                color = board.turn
                mat_before = review.material(board, color)
                legal_n = len(list(board.legal_moves))
                lines = analyse_lines(engine, board, depth=depth, multipv=2)
                best = lines[0] if lines else None
                second = lines[1] if len(lines) > 1 else None
                best_uci = best["move"].uci() if best and best.get("move") else ""
                best_cp = best["cp"] if best else 0
                best_mate = best.get("mate") if best else None
                second_cp = second["cp"] if second else None
                played_uci = move.uci()
                if best and best.get("move") == move:
                    played_cp = best_cp
                    played_mate = best_mate
                else:
                    probe = board.copy()
                    probe.push(move)
                    after = analyse_lines(engine, probe, depth=depth, multipv=1)
                    if after:
                        played_cp = -int(after[0]["cp"])
                        mate_opp = after[0].get("mate")
                        played_mate = (-int(mate_opp)) if mate_opp else None
                    else:
                        played_cp = best_cp
                        played_mate = best_mate
                try:
                    san_best = ""
                    if best and best.get("move") and best["move"] in board.legal_moves:
                        san_best = to_san_fr(board.san(best["move"]), board)
                except Exception:
                    san_best = best_uci
                board.push(move)
                mat_after = review.material(board, color)
                book = review.is_book(ucis[: idx + 1])
                code, _epl, acc = review.classify(
                    played_uci,
                    best_uci,
                    best_cp,
                    played_cp,
                    second_cp=second_cp,
                    legal_n=legal_n,
                    book=book,
                    mat_before=mat_before,
                    mat_after=mat_after,
                    best_mate=best_mate,
                )
                # Éval affichée = POV Blancs après le coup.
                ev_white = played_cp if color == chess.WHITE else -played_cp
                row = {
                    "ply": idx + 1,
                    "cls": code,
                    "ev": int(ev_white),
                    "bp": best_uci or played_uci,
                    "bs": (san_best or "").replace(",", ""),
                    "acc": acc,
                }
                job["rows"].append(row)
                job["chunk"].append(row)
                job["i"] += 1
                if len(job["chunk"]) >= 3:
                    self._emit_review_chunk(job, job["chunk"])
                    job["chunk"] = []
        except Exception as exc:
            log.warning("CapEchecs: bilan: %s", exc)
            close_engine(engine)
            self._review = None
            self._emit(
                irc, channel, None, "review_done",
                gid=job["gid"], ok="0", text="Analyse interrompue",
            )
            return
        if job["i"] < len(ucis):
            schedule.addEvent(self._review_tick, time.time() + 0.04, self._review_event())
            return
        if job["chunk"]:
            self._emit_review_chunk(job, job["chunk"])
        rows = job["rows"]
        acc_w = review.side_accuracy(rows, True)
        acc_b = review.side_accuracy(rows, False)
        cw = review.side_counts(rows, True)
        cb = review.side_counts(rows, False)
        close_engine(engine)
        self._review = None
        self._emit(
            irc, channel, None, "review_done",
            gid=job["gid"], ok="1",
            **{
                "acc-w": acc_w if acc_w is not None else "",
                "acc-b": acc_b if acc_b is not None else "",
                "w-bl": cw[review.BLUNDER],
                "w-mi": cw[review.MISTAKE],
                "w-in": cw[review.INACCURACY],
                "w-ex": cw.get(review.EXCELLENT, 0),
                "w-gd": cw.get(review.GOOD, 0),
                "w-bs": cw.get(review.BEST, 0),
                "w-gr": cw[review.GREAT],
                "w-br": cw[review.BRILLIANT],
                "w-ms": cw[review.MISSED],
                "b-bl": cb[review.BLUNDER],
                "b-mi": cb[review.MISTAKE],
                "b-in": cb[review.INACCURACY],
                "b-ex": cb.get(review.EXCELLENT, 0),
                "b-gd": cb.get(review.GOOD, 0),
                "b-bs": cb.get(review.BEST, 0),
                "b-gr": cb[review.GREAT],
                "b-br": cb[review.BRILLIANT],
                "b-ms": cb[review.MISSED],
            }
        )
        history.update_review(job["gid"], {
            "cls": [r.get("cls") or "" for r in rows],
            "ev": [int(r.get("ev") or 0) for r in rows],
            "bp": [r.get("bp") or "" for r in rows],
            "bs": [r.get("bs") or "" for r in rows],
            "accW": acc_w,
            "accB": acc_b,
        })

    def _finish_if_over(self, irc, channel, gs):
        ended = board_outcome(gs.board)
        if not ended:
            return False
        reason, winner = ended
        self._end_game(irc, channel, reason, winner)
        return True

    def _open_ai_engine(self, skill=None, elo=None):
        path = self._conf("stockfishPath")
        engine = open_engine(path)
        if skill is None:
            skill = self._conf("skillLevel")
        configure_engine(engine, skill, elo=elo)
        return engine

    def _announce_launch(self, irc, nick):
        if not self._conf("announceMessage"):
            return
        announce_chan = self._conf("announceMessageChannel")
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
        self._cancel_review()
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
            opening="",
            skill=gs.skill or "",
            tc=gs.tc or "casual",
            rated="1" if gs.rated else "0",
            **{
                "clock-w": int(max(0, gs.clocks.get("white") or 0)),
                "clock-b": int(max(0, gs.clocks.get("black") or 0)),
                "clock-inc": gs.clock_inc,
                "clock-at": int(gs.clock_stamp),
            }
        )
        self._emit_roster(irc, channel, gs)
        extra = ""
        if gs.tc and gs.tc != "casual":
            extra = " — %s" % gs.tc
        if gs.skill:
            extra += " — IA %s" % gs.skill
        self._say(
            irc,
            channel,
            "Partie commencée — Blancs : %s — Noirs : %s. Trait aux Blancs.%s"
            % (_bold(gs.players["white"]), _bold(gs.players["black"]), extra),
            essential=True,
        )
        gs.clock_stamp = time.time()
        if gs.clock_base > 0:
            self._arm_clock(irc, channel)
        else:
            self._arm_idle(irc, channel)

    def _apply_clocks(self, gs, color):
        """Consomme le temps du joueur qui vient de jouer. False = drapeau."""
        if not gs or gs.clock_base <= 0:
            return True
        now = time.time()
        elapsed = max(0.0, now - (gs.clock_stamp or now))
        gs.clocks[color] = float(gs.clocks.get(color) or 0) - elapsed
        gs.clock_stamp = now
        if gs.clocks[color] <= 0:
            gs.clocks[color] = 0
            return False
        gs.clocks[color] += float(gs.clock_inc or 0)
        return True

    def _arm_clock(self, irc, channel):
        gs = self.games.get(channel)
        if not gs or gs.waiting_join or gs.clock_base <= 0:
            return
        self._drop_event(gs.clock_event)
        gs.clock_event = None
        side = gs.side_to_move()
        remaining = float(gs.clocks.get(side) or 0)
        if remaining <= 0:
            winner = "black" if side == "white" else "white"
            self._end_game(irc, channel, "flag", winner)
            return
        gid = gs.gid
        name = "CapEchecs.clock.%s" % channel

        def _fire():
            with self._lock:
                cur = self.games.get(channel)
                if not cur or cur.gid != gid:
                    return
                now = time.time()
                left = float(cur.clocks.get(side) or 0) - max(0.0, now - cur.clock_stamp)
                if left > 0.4:
                    self._arm_clock(irc, channel)
                    return
                cur.clocks[side] = 0
                winner = "black" if side == "white" else "white"
                self._end_game(irc, channel, "flag", winner)

        schedule.addEvent(_fire, time.time() + remaining + 0.05, name)
        gs.clock_event = name

    def _setup_clocks(self, gs, tc, base, inc):
        gs.tc = tc or "casual"
        gs.clock_base = int(base or 0)
        gs.clock_inc = int(inc or 0)
        gs.clocks = {
            "white": float(gs.clock_base),
            "black": float(gs.clock_base),
        }
        gs.clock_stamp = time.time()
        gs.rated = gs.mode == "pvp"

    def _parse_start(self, text):
        tokens = [t for t in str(text or "").split() if t]
        skill_name = ""
        skill = None
        think = None
        ai_depth = None
        ai_noise = 0.0
        ai_elo = None
        tc, base, inc = "casual", 0, 0
        human_color = None
        mode = "ai"
        invited = None
        leftover = []
        for tok in tokens:
            key = tok.lower()
            if key in SKILL_PRESETS:
                preset = SKILL_PRESETS[key]
                skill_name = key
                skill = preset["skill"]
                think = preset["think"]
                ai_depth = preset.get("depth")
                ai_noise = float(preset.get("noise") or 0)
                ai_elo = preset.get("elo")
            elif key in TIME_PRESETS:
                tc, base, inc = TIME_PRESETS[key]
            elif key in ("blancs", "blanc", "white"):
                human_color = "white"
            elif key in ("noirs", "noir", "black"):
                human_color = "black"
            elif key == "duo":
                mode = "duo"
            elif key in ("ia", "ai"):
                mode = "ai"
            else:
                leftover.append(tok)
        if leftover:
            invited = leftover[0]
            mode = "invite"
        return {
            "mode": mode,
            "invited": invited,
            "human_color": human_color,
            "skill_name": skill_name,
            "skill": skill,
            "think": think,
            "ai_depth": ai_depth,
            "ai_noise": ai_noise,
            "ai_elo": ai_elo,
            "tc": tc,
            "base": base,
            "inc": inc,
        }

    def _account_of(self, irc, nick, msg=None):
        acc = ""
        if msg is not None:
            acc = getattr(msg, "account", None) or ""
            if not acc:
                tags = {}
                for src in (getattr(msg, "server_tags", None), getattr(msg, "tags", None)):
                    if src:
                        try:
                            tags.update(src if isinstance(src, dict) else dict(src))
                        except Exception:
                            pass
                acc = tags.get("account") or tags.get("+account") or ""
            if not acc and getattr(msg, "command", "") == "JOIN" and len(msg.args) >= 2:
                if msg.args[1] not in ("*", ""):
                    acc = msg.args[1]
        if not acc:
            getter = getattr(irc.state, "getAccount", None)
            if callable(getter):
                try:
                    acc = getter(nick) or ""
                except KeyError:
                    acc = ""
        if not acc:
            mapping = getattr(irc.state, "nicksToAccounts", None) or {}
            acc = mapping.get(nick) or mapping.get(str(nick).lower()) or ""
        acc = str(acc or "").strip()
        if acc in ("*", "0"):
            return ""
        return acc

    def _cc_key(self, nick, account=None):
        return str(account or nick or "").strip().lower()

    def _cc_summary(self, rec):
        extra = []
        if rec.get("cc-title"):
            extra.append(rec["cc-title"])
        if rec.get("cc-name"):
            extra.append(rec["cc-name"])
        if rec.get("cc-blitz"):
            extra.append("blitz %s" % rec["cc-blitz"])
        if rec.get("cc-rapid"):
            extra.append("rapide %s" % rec["cc-rapid"])
        if rec.get("cc-bullet"):
            extra.append("bullet %s" % rec["cc-bullet"])
        if rec.get("cc-league"):
            extra.append(rec["cc-league"])
        if rec.get("cc-country"):
            extra.append(rec["cc-country"])
        return " — ".join(extra)

    def _cc_preview_tags(self, nick, account, rec):
        return ratings.cc_tag_fields(rec)

    def _emit_cc_prompt(self, irc, channel, nick, account, mode, **extra):
        extra.pop("nick", None)
        extra.pop("account", None)
        extra.pop("mode", None)
        payload = {
            "nick": nick,
            "account": account or "",
            "mode": mode,
        }
        payload.update(extra)
        self._emit(irc, channel, None, "cc_prompt", **payload)

    def _cc_chan_reply(self, irc, channel, text):
        try:
            irc.reply(text)
        except Exception:
            self._say(irc, channel, text, essential=True)

    def _emit_cc_err(self, irc, channel, nick, text):
        self._emit(irc, channel, None, "cc_err", nick=nick, text=text)

    def _set_pending(self, nick, account, channel, profile, stats):
        rec = ratings.preview_from_api(profile, stats)
        self._cc_pending[self._cc_key(nick, account)] = {
            "nick": nick,
            "account": account or "",
            "channel": channel,
            "profile": profile,
            "stats": stats,
            "rec": rec,
        }
        return rec

    def _cc_confirm_pending(self, irc, channel, nick, account):
        key = self._cc_key(nick, account)
        pending = self._cc_pending.get(key)
        wait_text = (
            "Vérification du profil…"
            if pending and pending.get("stage") == "verify"
            else "Création du code…"
        )
        self._emit_cc_prompt(irc, channel, nick, account, "wait", text=wait_text)
        ev_key = "CapEchecs.ccverif.%s" % key
        self._drop_event(ev_key)

        def _run():
            self._cc_confirm_pending_run(irc, channel, nick, account)

        schedule.addEvent(_run, time.time() + 0.05, ev_key)

    def _cc_confirm_pending_run(self, irc, channel, nick, account):
        key = self._cc_key(nick, account)
        pending = self._cc_pending.get(key)
        if not pending:
            rec = ratings.player_record(nick, account)
            username = rec.get("chesscom") or ""
            if username and rec.get("cc-verified") != "1":
                try:
                    profile, stats = ratings.peek_chesscom(username)
                except ValueError as exc:
                    self._emit_cc_err(irc, channel, nick, str(exc))
                    irc.reply(str(exc))
                    return
                self._set_pending(nick, account, channel, profile, stats)
                pending = self._cc_pending.get(key)
            if not pending:
                self._emit_cc_err(
                    irc, channel, nick,
                    "Aucun compte Chess.com en attente. Envoie d'abord un pseudo.",
                )
                irc.reply("Aucun compte Chess.com en attente. Envoie d'abord un pseudo.")
                return
        rec = pending.get("rec") or {}
        if pending.get("stage") != "verify":
            pending["stage"] = "verify"
            pending["token"] = ratings.make_cc_token()
            pending["token_at"] = time.time()
            self._emit_cc_prompt(
                irc, channel, nick, account, "verify",
                token=pending["token"],
                text="Colle ce code dans Localisation de ton profil Chess.com.",
                **self._cc_preview_tags(nick, account, rec),
            )
            self._cc_chan_reply(
                irc, channel,
                "Pour prouver que le compte %s est à toi : copie le code du panneau Échecs dans Localisation (profil Chess.com), puis reclique."
                % (pending.get("profile") or {}).get("username", rec.get("chesscom") or ""),
            )
            return
        if time.time() - float(pending.get("token_at") or 0) > 20 * 60:
            pending["token"] = ratings.make_cc_token()
            pending["token_at"] = time.time()
            self._emit_cc_prompt(
                irc, channel, nick, account, "verify",
                token=pending["token"],
                text="Le code a expiré. Voici le nouveau.",
                **self._cc_preview_tags(nick, account, rec),
            )
            return
        username = (pending.get("profile") or {}).get("username") or rec.get("chesscom")
        try:
            profile, stats = ratings.peek_chesscom(username)
        except ValueError as exc:
            self._emit_cc_prompt(
                irc, channel, nick, account, "verify",
                token=pending["token"],
                text=str(exc),
                **self._cc_preview_tags(nick, account, rec),
            )
            return
        if not ratings.profile_has_token(profile, pending.get("token")):
            pending["rec"] = ratings.preview_from_api(profile, stats)
            self._emit_cc_prompt(
                irc, channel, nick, account, "verify",
                token=pending["token"],
                text="Code introuvable sur le profil. Colle-le dans Localisation, attends 2–3 s, puis réessaie.",
                **self._cc_preview_tags(nick, account, pending["rec"]),
            )
            return
        self._cc_pending.pop(key, None)
        rec = ratings.confirm_link(nick, account, profile, stats)
        self._emit_elo(irc, channel, nick, account)
        self._refresh_roster(irc, channel)
        self._emit_cc_prompt(irc, channel, nick, account, "linked", **self._cc_preview_tags(nick, account, rec))
        irc.reply(
            "Compte Chess.com vérifié : %s%s."
            % (rec["chesscom"], (" (%s)" % self._cc_summary(rec)) if self._cc_summary(rec) else "")
        )

    def _cc_reject_pending(self, irc, channel, nick, account):
        self._cc_pending.pop(self._cc_key(nick, account), None)
        self._emit_cc_prompt(irc, channel, nick, account, "missing", text="Indique un autre pseudo Chess.com")
        irc.reply("D'accord, ce n'était pas ton compte. Tape un autre pseudo Chess.com.")

    def _cc_optout(self, irc, channel, nick, account):
        self._cc_pending.pop(self._cc_key(nick, account), None)
        ratings.set_optout(nick, account, True)
        self._emit_cc_prompt(irc, channel, nick, account, "optout")
        irc.reply("Chess.com ne sera plus proposé. Tu pourras le réactiver via l’icône Paramètres ou !lier activer.")

    def _cc_enable(self, irc, channel, nick, account):
        rec = ratings.set_optout(nick, account, False)
        key = "%s:%s" % (str(channel).lower(), str(account or nick).lower())
        self._cc_checked.pop(key, None)
        self._cc_asked.pop(str(account or nick).lower(), None)
        self._emit_elo(irc, channel, nick, account)
        if rec.get("chesscom"):
            self._refresh_roster(irc, channel)
            self._emit_cc_prompt(
                irc, channel, nick, account, "linked",
                **self._cc_preview_tags(nick, account, rec),
            )
        else:
            self._emit_cc_prompt(
                irc, channel, nick, account, "missing",
                text="Indique ton pseudo Chess.com",
            )
        irc.reply("Chess.com est réactivé.")

    def _cc_propose(self, irc, channel, nick, account, username):
        self._emit_cc_prompt(
            irc, channel, nick, account, "wait",
            text="Recherche Chess.com…",
        )
        key = "CapEchecs.ccpeek.%s" % self._cc_key(nick, account)
        self._drop_event(key)

        def _lookup():
            self._cc_propose_lookup(irc, channel, nick, account, username)

        schedule.addEvent(_lookup, time.time() + 0.05, key)

    def _cc_propose_lookup(self, irc, channel, nick, account, username):
        try:
            try:
                profile, stats = ratings.peek_chesscom(username)
            except ValueError as exc:
                text = str(exc)
                self._emit_cc_err(irc, channel, nick, text)
                self._cc_chan_reply(irc, channel, text)
                return
            rec = self._set_pending(nick, account, channel, profile, stats)
            self._emit_cc_prompt(
                irc, channel, nick, account, "preview",
                **self._cc_preview_tags(nick, account, rec),
            )
            summary = self._cc_summary(rec)
            self._cc_chan_reply(
                irc, channel,
                "Compte trouvé : %s%s. Confirme avec !lier oui, ou !lier non pour un autre pseudo."
                % (rec["chesscom"], (" (%s)" % summary) if summary else ""),
            )
        except Exception as exc:
            log.warning("CapEchecs: Chess.com lookup %s: %s", username, exc)
            self._emit_cc_err(irc, channel, nick, "Chess.com est temporairement indisponible.")

    def _elo_tags(self, nick, account=None):
        rec = ratings.player_record(nick, account)
        tags = {
            "nick": nick,
            "account": rec["account"] or account or "",
            "elo": rec["elo"],
            "games": rec["games"],
            "wins": rec["wins"],
            "draws": rec["draws"],
            "losses": rec["losses"],
            "optout": "1" if rec.get("cc_optout") else "0",
            "en-name": rec.get("en-name") or "",
        }
        tags.update(ratings.cc_tag_fields(rec))
        return tags

    def _emit_elo(self, irc, channel, nick, account=None):
        self._emit(irc, channel, None, "elo_sync", **self._elo_tags(nick, account))

    def _hist_cell(self, value):
        return str(value or "").replace("|", "/").replace(";", ",")

    def _emit_history_list(self, irc, channel, nick):
        rows = list(history.list_for(nick, 12) or [])
        acc = self._account_of(irc, nick)
        if acc and acc.lower() != str(nick or "").lower():
            seen = {str(rec.get("gid") or "") for rec in rows}
            for rec in history.list_for(acc, 12) or []:
                gid = str(rec.get("gid") or "")
                if gid and gid not in seen:
                    rows.append(rec)
                    seen.add(gid)
            rows.sort(key=lambda rec: int(rec.get("at") or 0), reverse=True)
            rows = rows[:12]
        if not rows:
            self._emit(irc, channel, None, "history_list", nick=nick, rows="")
            return
        packed = []
        for rec in rows:
            packed.append("|".join((
                self._hist_cell(rec.get("gid")),
                self._hist_cell(rec.get("white")),
                self._hist_cell(rec.get("black")),
                self._hist_cell(rec.get("result")),
                self._hist_cell(rec.get("tc")),
                self._hist_cell(int(rec.get("at") or 0)),
            )))
        step = 4
        for i in range(0, len(packed), step):
            self._emit(
                irc, channel, None, "history_list",
                nick=nick,
                rows=";".join(packed[i:i + step]),
                **{"from": i},
            )

    def _emit_archive(self, irc, channel, rec, nick):
        gid = rec.get("gid") or "0"
        ucis = list(rec.get("ucis") or [])
        sans = list(rec.get("sans") or [])
        self._emit(
            irc, channel, None, "archive",
            nick=nick,
            gid=gid,
            white=rec.get("white") or "",
            black=rec.get("black") or "",
            result=rec.get("result") or "",
            reason=rec.get("reason") or "",
            tc=rec.get("tc") or "casual",
            skill=rec.get("skill") or "",
            opening=rec.get("opening") or "",
            duration=rec.get("duration") or 0,
            fen=rec.get("fen") or "",
            ply=len(ucis),
            **{"opening-var": rec.get("openingVar") or ""},
        )
        i = 0
        while i < len(ucis):
            chunk_u = ucis[i:i + 16]
            chunk_s = sans[i:i + 16]
            self._emit(
                irc, channel, None, "archive_moves",
                nick=nick,
                gid=gid,
                **{"from": i + 1},
                ucis=",".join(chunk_u),
                sans=",".join(chunk_s),
            )
            i += 16
        rev = rec.get("review") or {}
        cls = list(rev.get("cls") or [])
        if not cls:
            return
        evs = list(rev.get("ev") or [])
        bps = list(rev.get("bp") or [])
        bss = list(rev.get("bs") or [])
        self._emit(
            irc, channel, None, "review_start",
            nick=nick, gid=gid, n=len(cls), status="run",
        )
        i = 0
        while i < len(cls):
            sl = slice(i, i + 3)
            self._emit(
                irc, channel, None, "review_chunk",
                nick=nick,
                gid=gid,
                **{
                    "from": i + 1,
                    "cls": ",".join(cls[sl]),
                    "ev": ",".join(str(x) for x in evs[sl]),
                    "bp": ",".join(bps[sl]),
                    "bs": ",".join(bss[sl]),
                }
            )
            i += 3
        rows = [{"cls": c} for c in cls]
        cw = review.side_counts(rows, True)
        cb = review.side_counts(rows, False)
        self._emit(
            irc, channel, None, "review_done",
            nick=nick, gid=gid, ok="1",
            **{
                "acc-w": rev.get("accW") if rev.get("accW") is not None else "",
                "acc-b": rev.get("accB") if rev.get("accB") is not None else "",
                "w-bl": cw.get(review.BLUNDER, 0),
                "w-mi": cw.get(review.MISTAKE, 0),
                "w-in": cw.get(review.INACCURACY, 0),
                "w-ex": cw.get(review.EXCELLENT, 0),
                "w-gd": cw.get(review.GOOD, 0),
                "w-bs": cw.get(review.BEST, 0),
                "w-gr": cw.get(review.GREAT, 0),
                "w-br": cw.get(review.BRILLIANT, 0),
                "w-ms": cw.get(review.MISSED, 0),
                "b-bl": cb.get(review.BLUNDER, 0),
                "b-mi": cb.get(review.MISTAKE, 0),
                "b-in": cb.get(review.INACCURACY, 0),
                "b-ex": cb.get(review.EXCELLENT, 0),
                "b-gd": cb.get(review.GOOD, 0),
                "b-bs": cb.get(review.BEST, 0),
                "b-gr": cb.get(review.GREAT, 0),
                "b-br": cb.get(review.BRILLIANT, 0),
                "b-ms": cb.get(review.MISSED, 0),
            }
        )

    def _on_player_enter(self, irc, channel, nick, account):
        if not account or nick == getattr(irc, "nick", None):
            return
        key = "%s:%s" % (str(channel).lower(), str(account).lower())
        now = time.time()
        if now - self._cc_checked.get(key, 0) < 25:
            return
        self._cc_checked[key] = now
        rec, status = ratings.resolve_on_join(nick, account)
        self._emit_elo(irc, channel, nick, account)
        if status == "error":
            log.warning("CapEchecs: Chess.com indisponible pour %s", account)
            self._emit_cc_err(irc, channel, nick, "Chess.com est temporairement indisponible.")
            return
        if status == "optout":
            self._emit_cc_prompt(irc, channel, nick, account, "optout")
            return
        if status == "linked":
            self._emit_cc_prompt(
                irc, channel, nick, account, "linked",
                **self._cc_preview_tags(nick, account, rec)
            )
            return
        if status == "found":
            profile = rec.get("_profile")
            stats = rec.get("_stats")
            if not profile:
                status = "missing"
            else:
                preview = self._set_pending(nick, account, channel, profile, stats)
                self._emit_cc_prompt(
                    irc, channel, nick, account, "preview",
                    **self._cc_preview_tags(nick, account, preview)
                )
                return
        self._emit_cc_prompt(
            irc, channel, nick, account, "missing",
            text="Aucun compte Chess.com trouvé pour %s" % account,
        )

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
            think = gs.think_time if gs.think_time is not None else self._conf("thinkTime")
            ai_color = gs.side_to_move()
            ai_depth = gs.ai_depth
            ai_noise = gs.ai_noise
        try:
            move = engine_move(engine, board, think, depth=ai_depth, noise=ai_noise)
        except Exception as exc:
            self._end_game(irc, channel, "engine")
            log.warning("CapEchecs: erreur IA: %s", exc)
            return
        with self._lock:
            gs = self.games.get(channel)
            if not gs or gs.gid != gid:
                return
            if not self._apply_clocks(gs, ai_color):
                winner = "black" if ai_color == "white" else "white"
                self._end_game(irc, channel, "flag", winner)
                return
            info = apply_move(gs, move)
            self._emit_move(irc, channel, gs, info, AI_NICK)
            self._say(irc, channel, self._move_line(AI_NICK, info), essential=True)
            if self._finish_if_over(irc, channel, gs):
                return
            if gs.clock_base > 0:
                self._arm_clock(irc, channel)
            else:
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
        """[<duo|ia|pseudo>] [facile|moyen|difficile] [blitz|rapide|bullet] [blancs|noirs]

        Démarre une partie contre l'IA, une partie duo, ou invite un joueur.
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        nick = msg.nick
        opts = self._parse_start(mode_or_opponent)

        with self._lock:
            if channel in self.games:
                irc.reply("Une partie est déjà en cours.")
                return

            if opts["mode"] == "duo":
                gs = GameState("pvp", nick)
                gs.waiting_join = True
                self._setup_clocks(gs, opts["tc"], opts["base"], opts["inc"])
                self.games[channel] = gs
                self._emit(
                    irc, channel, gs, "waiting",
                    mode="duo", creator=nick, invited="",
                    timeout=self._conf("duoTimeout"),
                    tc=gs.tc,
                )
                self._say(
                    irc, channel,
                    "%s ouvre une partie duo%s. Tapez !rejoindre pour jouer."
                    % (_bold(nick), " (%s)" % gs.tc if gs.tc != "casual" else ""),
                    essential=True,
                )
                self._arm_wait(irc, channel)
                self._announce_launch(irc, nick)
                return

            if opts["mode"] == "invite" and opts["invited"] and not _nick_eq(opts["invited"], nick):
                invited = opts["invited"]
                gs = GameState("pvp", nick)
                gs.waiting_join = True
                gs.invited = invited
                self._setup_clocks(gs, opts["tc"], opts["base"], opts["inc"])
                self.games[channel] = gs
                self._emit(
                    irc, channel, gs, "waiting",
                    mode="invite", creator=nick, invited=invited,
                    timeout=self._conf("duoTimeout"),
                    tc=gs.tc,
                )
                self._say(
                    irc, channel,
                    "%s invite %s%s. %s, tapez !rejoindre pour accepter."
                    % (
                        _bold(nick), _bold(invited),
                        " (%s)" % gs.tc if gs.tc != "casual" else "",
                        invited,
                    ),
                    essential=True,
                )
                self._arm_wait(irc, channel)
                self._announce_launch(irc, nick)
                return

            skill = opts["skill"]
            think = opts["think"]
            if skill is None:
                skill = int(self._conf("skillLevel") or 12)
                think = float(self._conf("thinkTime") or 0.5)
            try:
                limit_elo = opts.get("ai_elo")
                if not limit_elo or int(limit_elo) >= 2100:
                    limit_elo = None
                engine = self._open_ai_engine(skill, elo=limit_elo)
            except Exception as exc:
                irc.reply("%s" % exc)
                return

            gs = GameState("ai", nick, engine=engine)
            gs.skill = opts["skill_name"] or ""
            gs.think_time = think
            gs.ai_depth = opts.get("ai_depth")
            gs.ai_noise = float(opts.get("ai_noise") or 0)
            gs.ai_elo = opts.get("ai_elo")
            self._setup_clocks(gs, opts["tc"], opts["base"], opts["inc"])
            gs.rated = True
            self.games[channel] = gs
            self._assign_colors(gs, nick, AI_NICK, opts["human_color"])
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
        channel = self._canon_channel(self._msg_channel(msg))
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
        channel = self._canon_channel(self._msg_channel(msg))
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
            color = gs.side_to_move()
            if not self._apply_clocks(gs, color):
                winner = "black" if color == "white" else "white"
                self._end_game(irc, channel, "flag", winner)
                return
            info = apply_move(gs, move)
            self._emit_move(irc, channel, gs, info, nick)
            self._say(irc, channel, self._move_line(nick, info), essential=True)
            if self._finish_if_over(irc, channel, gs):
                return
            if gs.clock_base > 0:
                self._arm_clock(irc, channel)
            else:
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
        channel = self._canon_channel(self._msg_channel(msg))
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
        channel = self._canon_channel(self._msg_channel(msg))
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
        channel = self._canon_channel(self._msg_channel(msg))
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
        channel = self._canon_channel(self._msg_channel(msg))
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
        channel = self._canon_channel(self._msg_channel(msg))
        gs = self.games.get(channel)
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
        channel = self._canon_channel(self._msg_channel(msg))
        gs = self.games.get(channel)
        if not gs:
            irc.reply("Aucune partie en cours.")
            return
        self._notice(irc, msg.nick, gs.board.fen())
        self._emit_sync(irc, channel, gs)

    fen = wrap(fen)

    def elo(self, irc, msg, args, who=None):
        """[<pseudo>]

        Affiche l'ELO EntreNous et, si lié, les classements Chess.com.
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        target = (who or msg.nick).strip()
        account = self._account_of(irc, target, msg if not who else None)
        rec = ratings.player_record(target, account if not who else None)
        label = rec.get("account") or target
        parts = [
            "%s — ELO EntreNous %s (%s parties, %sV %sN %sD)"
            % (label, rec["elo"], rec["games"], rec["wins"], rec["draws"], rec["losses"])
        ]
        if rec["chesscom"]:
            title = (rec["cc-title"] + " ") if rec.get("cc-title") else ""
            name = rec.get("cc-name") or rec["chesscom"]
            proof = "vérifié" if rec.get("cc-verified") == "1" else "non vérifié"
            parts.append(
                "Chess.com %s%s (%s) — rapide %s, blitz %s, bullet %s"
                % (
                    title,
                    name,
                    proof,
                    rec["cc-rapid"] or "—",
                    rec["cc-blitz"] or "—",
                    rec["cc-bullet"] or "—",
                )
            )
        else:
            parts.append("Aucun compte Chess.com lié (!lier chesscom <pseudo>).")
        irc.reply(" — ".join(parts))
        self._emit_elo(irc, channel, target, account if not who else None)

    elo = wrap(elo, [optional("text")])

    def historique(self, irc, msg, args):
        """Liste tes dernières parties (panneau Orbit)."""
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        nick = msg.nick
        self._emit_history_list(irc, channel, nick)
        irc.noReply()

    historique = wrap(historique)

    def revoir(self, irc, msg, args, gid):
        """<id> — rejoue une ancienne partie dans le panneau."""
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        nick = msg.nick
        rec = history.get_game(gid)
        if not rec:
            self._emit(irc, channel, None, "cmd_err", nick=nick, name="revoir", text="Partie introuvable.")
            irc.noReply()
            return
        involved = (
            _nick_eq(rec.get("white"), nick)
            or _nick_eq(rec.get("black"), nick)
        )
        if not involved:
            self._emit(irc, channel, None, "cmd_err", nick=nick, name="revoir", text="Cette partie ne t'appartient pas.")
            irc.noReply()
            return
        self._emit_archive(irc, channel, rec, nick)
        irc.noReply()

    revoir = wrap(revoir, ["something"])

    def profil(self, irc, msg, args, rest=None):
        """[<nom>]

        Nom affiché sur le classement EntreNous (vide pour revenir au pseudo IRC).
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        nick = msg.nick
        account = self._account_of(irc, nick, msg)
        wanted = " ".join(str(rest or "").split())
        if wanted.lower() in ("reset", "effacer", "aucun", "nick"):
            wanted = ""
        try:
            rec = ratings.set_display_name(nick, account, wanted)
        except ValueError as exc:
            irc.reply(str(exc))
            self._emit(
                irc, channel, None, "cmd_err",
                name="profil", nick=nick, text=str(exc),
            )
            return
        self._emit_elo(irc, channel, nick, account)
        shown = rec.get("en-name") or nick
        irc.reply("Nom de profil : %s." % shown)

    profil = wrap(profil, [optional("text")])

    def lier(self, irc, msg, args, rest=None):
        """[chesscom <pseudo>|oui|non|ignorer|activer]

        Propose un compte Chess.com, confirme, refuse, ou désactive la fonction.
        """
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        nick = msg.nick
        account = self._account_of(irc, nick, msg)
        tokens = [t for t in str(rest or "").split() if t]
        if not tokens:
            irc.reply("Syntaxe : !lier chesscom <pseudo>  |  !lier oui  |  !lier non  |  !lier ignorer")
            return
        action = tokens[0].lower()
        if action in ("oui", "yes", "ok", "confirmer", "confirm", "verif", "vérif", "code"):
            self._cc_confirm_pending(irc, channel, nick, account)
            return
        if action in ("non", "no", "autre"):
            self._cc_reject_pending(irc, channel, nick, account)
            return
        if action in ("ignorer", "skip", "jamais", "off"):
            self._cc_optout(irc, channel, nick, account)
            return
        if action in ("activer", "on", "reactiver"):
            self._cc_enable(irc, channel, nick, account)
            return
        username = tokens[1] if len(tokens) > 1 else tokens[0]
        if action in ("chesscom", "chess.com", "chess"):
            if len(tokens) < 2:
                irc.reply("Syntaxe : !lier chesscom <pseudo>")
                return
            username = tokens[1]
        self._cc_propose(irc, channel, nick, account, username)

    lier = wrap(lier, [optional("text")])

    def sync(self, irc, msg, args):
        """Renvoie l'état courant en TAGMSG (clients Orbit)."""
        if not self._in_game_channel(irc, msg):
            return
        channel = self._canon_channel(self._msg_channel(msg))
        gs = self.games.get(channel)
        if not gs:
            irc.reply("Aucune partie en cours.")
            return
        self._emit_sync(irc, channel, gs)

    sync = wrap(sync)

    def aide(self, irc, msg, args):
        """Affiche les commandes du jeu d'échecs."""
        nick = msg.nick
        channel = self._canon_channel(self._msg_channel(msg))
        lines = [
            "Échecs — commandes",
            "  !commencer / !co — IA (options : facile moyen difficile, blitz rapide bullet, blancs|noirs)",
            "  !commencer duo [blitz|rapide|bullet] — partie ouverte (ELO si cadence)",
            "  !commencer <pseudo> [blitz|…] — invitation",
            "  !rejoindre / !re — rejoindre",
            "  !jouer <coup> / !j — SAN FR (Cf3), SAN EN (Nf3) ou UCI (e2e4)",
            "  !plateau / !pl — plateau",
            "  !historique — tes anciennes parties",
            "  !revoir <id> — revoir une partie",
            "  !coups — coups de la partie en cours",
            "  !elo — classement EntreNous + Chess.com lié",
            "  !lier chesscom <pseudo> — proposer un compte (preuve par code ensuite)",
            "  !lier oui — confirmer le pseudo, puis vérifier le code du profil",
            "  !lier activer — réafficher Chess.com",
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
        channel = self._canon_channel(self._msg_channel(msg))
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
            irc.reply("Inactivité : %s s" % self._conf("inactivitySeconds"))
            irc.reply("Timeout duo : %s s" % self._conf("duoTimeout"))
            irc.reply("Réflexion IA : %s s" % self._conf("thinkTime"))
            irc.reply("Skill IA : %s" % self._conf("skillLevel"))
            irc.reply("Stockfish : %s" % self._conf("stockfishPath"))
            irc.reply("Salon de jeu : %s" % self._conf("allowedChannel"))
            irc.reply("Annonce : %s (%s)" % (
                "on" if self._conf("announceMessage") else "off",
                self._conf("announceMessageChannel"),
            ))
            irc.reply("Bienvenue : %s" % ("on" if self._conf("welcomeMessage") else "off"))
            irc.reply("Quiet : %s" % ("on" if self._quiet(channel) else "off"))
            return

        try:
            if sub == "inactivite" and arg1:
                self._set_conf("inactivitySeconds", int(arg1))
                irc.reply("Inactivité : %s s" % arg1)
                return
            if sub == "duotimeout" and arg1:
                self._set_conf("duoTimeout", int(arg1))
                irc.reply("Timeout duo : %s s" % arg1)
                return
            if sub == "think" and arg1:
                self._set_conf("thinkTime", float(arg1))
                irc.reply("Réflexion IA : %s s" % arg1)
                return
            if sub == "skill" and arg1:
                level = max(0, min(20, int(arg1)))
                self._set_conf("skillLevel", level)
                irc.reply("Skill IA : %s" % level)
                return
            if sub == "stockfish" and arg1:
                self._set_conf("stockfishPath", arg1)
                irc.reply("Stockfish : %s" % arg1)
                return
            if sub == "salonjeu" and arg1:
                self._set_conf("allowedChannel", arg1)
                irc.reply("Salon de jeu : %s" % arg1)
                return
            if sub == "bienvenue" and arg1:
                self._set_conf("welcomeMessage", _bool(arg1))
                irc.reply("Bienvenue : %s" % arg1)
                return
            if sub == "quiet" and arg1:
                self._set_conf("quietChannel", _bool(arg1), channel)
                irc.reply("Quiet : %s" % arg1)
                return
            if sub == "message":
                if not arg1:
                    irc.reply("Annonce : %s (%s)" % (
                        "on" if self._conf("announceMessage") else "off",
                        self._conf("announceMessageChannel"),
                    ))
                    return
                if arg1.lower() in ("on", "off", "oui", "non", "true", "false"):
                    self._set_conf("announceMessage", _bool(arg1))
                    irc.reply("Annonce : %s" % arg1)
                    return
                if arg1.startswith("#") and arg2:
                    self._set_conf("announceMessageChannel", arg1)
                    self._set_conf("announceMessage", _bool(arg2))
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
        account = self._account_of(irc, nick)
        if account:
            self._on_player_enter(irc, self._canon_channel(channel), nick, account)

    def doJoin(self, irc, msg):
        if not msg.args:
            return
        channel = msg.args[0]
        if msg.nick == getattr(irc, "nick", None):
            return
        if channel.lower() != self._game_channel().lower():
            return
        account = self._account_of(irc, msg.nick, msg)
        if account:
            self._on_player_enter(irc, self._canon_channel(channel), msg.nick, account)

    def doAccount(self, irc, msg):
        acc = msg.args[0] if msg.args else ""
        if not acc or acc == "*":
            return
        game = self._game_channel()
        chan = (irc.state.channels or {}).get(game)
        if not chan or msg.nick not in getattr(chan, "users", ()):
            return
        self._on_player_enter(irc, self._canon_channel(game), msg.nick, acc)

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
