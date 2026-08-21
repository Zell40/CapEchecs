# -*- coding: utf-8 -*-
"""Encapsulation Stockfish (UCI)."""
from __future__ import print_function

import os
import random

import chess.engine

from supybot import log


def open_engine(path):
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "Stockfish introuvable au chemin configuré: %r" % (path,)
        )
    return chess.engine.SimpleEngine.popen_uci(path)


def _option_names(engine):
    try:
        return engine.options or {}
    except Exception:
        return {}


def configure_engine(engine, skill_level=None, elo=None):
    """Applique Skill Level et, si possible, UCI_LimitStrength / UCI_Elo."""
    if engine is None:
        return
    options = _option_names(engine)
    cfg = {}
    if elo is not None and "UCI_LimitStrength" in options:
        cfg["UCI_LimitStrength"] = True
        if "UCI_Elo" in options:
            opt = options.get("UCI_Elo")
            lo = int(getattr(opt, "min", 1320) or 1320)
            hi = int(getattr(opt, "max", 3190) or 3190)
            cfg["UCI_Elo"] = max(lo, min(hi, int(elo)))
    elif elo is None and "UCI_LimitStrength" in options:
        cfg["UCI_LimitStrength"] = False
    if skill_level is not None and "Skill Level" in options:
        cfg["Skill Level"] = int(skill_level)
    if not cfg:
        return
    try:
        engine.configure(cfg)
    except Exception as exc:
        log.warning("CapEchecs: options Stockfish ignorées (%s): %s", cfg, exc)


def engine_move(engine, board, think_time, depth=None, noise=0.0):
    """Choisit un coup. `noise` (0–1) fait volontairement jouer plus faible."""
    if engine is None:
        raise RuntimeError("Moteur absent")
    legal = list(board.legal_moves)
    if not legal:
        raise RuntimeError("Aucun coup légal")
    think = max(0.02, float(think_time or 0.08))
    noise = max(0.0, min(1.0, float(noise or 0)))
    cap = int(depth) if depth else None

    if noise > 0.0 and random.random() < noise:
        move = _noisy_move(engine, board, legal, cap, noise)
        if move is not None:
            return move

    kwargs = {"time": think}
    if cap:
        kwargs["depth"] = max(1, cap)
    result = engine.play(board, chess.engine.Limit(**kwargs))
    if result is None or result.move is None:
        raise RuntimeError("Le moteur n'a renvoyé aucun coup")
    return result.move


def _noisy_move(engine, board, legal, depth, noise):
    """Tirage parmi des lignes plus faibles, parfois un coup légal au hasard."""
    if noise >= 0.5 and random.random() < 0.42:
        return random.choice(legal)
    mpv = min(8, len(legal))
    lines = analyse_lines(engine, board, depth=max(1, int(depth or 2)), multipv=mpv)
    moves = [row["move"] for row in lines if row.get("move")]
    if not moves:
        return random.choice(legal)
    # Plus le bruit est haut, plus on pioche loin du meilleur coup.
    span = max(1, int(round((len(moves) - 1) * min(1.0, 0.35 + noise))))
    idx = min(len(moves) - 1, random.randint(0, span))
    if idx == 0 and len(moves) > 1 and random.random() < noise:
        idx = random.randint(1, len(moves) - 1)
    return moves[idx]


def analyse_lines(engine, board, depth=12, multipv=2):
    """Retourne des lignes [{move, cp, mate}] du point de vue du trait."""
    if engine is None or board is None or board.is_game_over():
        return []
    limit = chess.engine.Limit(depth=max(1, int(depth or 12)))
    try:
        raw = engine.analyse(board, limit, multipv=max(1, int(multipv)))
    except TypeError:
        raw = engine.analyse(board, limit)
    except Exception:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    lines = []
    for info in raw:
        if not info:
            continue
        move = None
        pv = info.get("pv") or []
        if pv:
            move = pv[0]
        score = info.get("score")
        if score is None:
            continue
        pov = score.pov(board.turn)
        mate = pov.mate()
        if mate is not None:
            cp = 10000 - min(abs(int(mate)), 80) * 20
            if mate < 0:
                cp = -cp
        else:
            cp = pov.score(mate_score=10000)
            if cp is None:
                continue
            cp = int(cp)
        lines.append({"move": move, "cp": cp, "mate": mate})
    return lines


def close_engine(engine):
    if not engine:
        return
    try:
        engine.quit()
    except Exception:
        pass
