# -*- coding: utf-8 -*-
"""Classement des coups style Chess.com (précision, gaffe, brillant…)."""
from __future__ import print_function

import math

import chess

# Codes courts pour TAGMSG.
BOOK = "bk"
BRILLIANT = "br"
GREAT = "gr"
BEST = "bs"
EXCELLENT = "ex"
GOOD = "gd"
INACCURACY = "in"
MISTAKE = "mi"
BLUNDER = "bl"
MISSED = "ms"

LABELS_FR = {
    BOOK: "Théorique",
    BRILLIANT: "Brillant",
    GREAT: "Superbe",
    BEST: "Meilleur",
    EXCELLENT: "Excellent",
    GOOD: "Bon",
    INACCURACY: "Imprécision",
    MISTAKE: "Erreur",
    BLUNDER: "Gaffe",
    MISSED: "Gain manqué",
}

_PIECE_VAL = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


def win_pct(cp):
    """Chance de gain 0–100 à partir de centipawns (côté au trait)."""
    try:
        cp = int(cp)
    except (TypeError, ValueError):
        cp = 0
    cp = max(-1500, min(1500, cp))
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)


def move_accuracy(epl):
    """Précision 0–100 à partir de la perte de % de gain."""
    epl = max(0.0, float(epl or 0))
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * epl) - 3.1669))


def material(board, color):
    total = 0
    for piece_type, value in _PIECE_VAL.items():
        total += value * len(board.pieces(piece_type, color))
    return total


def is_book(ucis):
    """Vrai tant que la ligne est un préfixe d'une ouverture connue."""
    if not ucis:
        return True
    key = "".join(str(u).lower().replace("-", "") for u in ucis)
    try:
        from .openings import _OPENINGS
    except Exception:
        return False
    for row in _OPENINGS:
        prefix = row[0] if row else ""
        if prefix and prefix.startswith(key):
            return True
    return False


def classify(
    played_uci,
    best_uci,
    best_cp,
    played_cp,
    second_cp=None,
    legal_n=0,
    book=False,
    mat_before=0,
    mat_after=0,
    best_mate=None,
):
    """Retourne (code, epl, accuracy). Scores = POV du joueur qui vient de jouer."""
    best_cp = int(best_cp or 0)
    played_cp = int(played_cp or 0)
    epl = max(0.0, win_pct(best_cp) - win_pct(played_cp))
    acc = round(move_accuracy(epl), 1)
    played = str(played_uci or "")
    best = str(best_uci or "")
    is_best = bool(best) and (played == best or abs(played_cp - best_cp) <= 10)

    if book:
        return BOOK, 0.0, 100.0

    sac = (mat_after <= mat_before - 2) and is_best and played_cp >= -50
    if sac:
        return BRILLIANT, epl, max(acc, 98.0)

    if (
        is_best
        and int(legal_n or 0) >= 3
        and second_cp is not None
        and best_cp - int(second_cp) >= 150
    ):
        return GREAT, epl, max(acc, 97.0)

    winning = best_cp >= 200 or (best_mate is not None and int(best_mate) > 0)
    if winning and not is_best and played_cp < 90 and epl >= 8:
        return MISSED, epl, acc

    if is_best or epl < 0.6:
        return BEST, epl, max(acc, 99.0)
    if epl < 2.0:
        return EXCELLENT, epl, acc
    if epl < 5.0:
        return GOOD, epl, acc
    if epl < 10.0:
        return INACCURACY, epl, acc
    if epl < 20.0:
        return MISTAKE, epl, acc
    return BLUNDER, epl, acc


def side_accuracy(rows, white=True):
    """Moyenne des précisions d'un camp. rows: dicts avec i (0-based), acc."""
    vals = []
    for i, row in enumerate(rows):
        if bool(i % 2 == 0) != bool(white):
            continue
        vals.append(float(row.get("acc") or 0))
    if not vals:
        return None
    return int(round(sum(vals) / float(len(vals))))


def side_counts(rows, white=True):
    out = {BLUNDER: 0, MISTAKE: 0, INACCURACY: 0, GREAT: 0, BRILLIANT: 0, MISSED: 0}
    for i, row in enumerate(rows):
        if bool(i % 2 == 0) != bool(white):
            continue
        code = row.get("cls") or ""
        if code in out:
            out[code] += 1
    return out
