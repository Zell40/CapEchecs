# -*- coding: utf-8 -*-
"""Encapsulation Stockfish (UCI)."""
from __future__ import print_function

import os

import chess.engine


def open_engine(path):
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "Stockfish introuvable au chemin configuré: %r" % (path,)
        )
    return chess.engine.SimpleEngine.popen_uci(path)


def configure_engine(engine, skill_level=None):
    if engine is None or skill_level is None:
        return
    try:
        engine.configure({"Skill Level": int(skill_level)})
    except Exception:
        pass


def engine_move(engine, board, think_time):
    limit = chess.engine.Limit(time=float(think_time or 0.5))
    result = engine.play(board, limit)
    if result is None or result.move is None:
        raise RuntimeError("Le moteur n'a renvoyé aucun coup")
    return result.move


def analyse_lines(engine, board, depth=12, multipv=2):
    """Retourne des lignes [{move, cp, mate}] du point de vue du trait."""
    if engine is None or board is None or board.is_game_over():
        return []
    limit = chess.engine.Limit(depth=max(6, int(depth or 12)))
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
