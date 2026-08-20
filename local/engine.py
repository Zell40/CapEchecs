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


def close_engine(engine):
    if not engine:
        return
    try:
        engine.quit()
    except Exception:
        pass
