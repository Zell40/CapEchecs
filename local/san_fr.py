# -*- coding: utf-8 -*-
"""Notation SAN française et parsing des coups (SAN FR, SAN EN, UCI)."""
from __future__ import print_function

import chess

PIECE_FR_TO_EN = {"C": "N", "F": "B", "T": "R", "D": "Q", "R": "K"}
PIECE_EN_TO_FR = {"N": "C", "B": "F", "R": "T", "Q": "D", "K": "R"}
PROMO_FR_TO_EN = {"D": "Q", "T": "R", "F": "B", "C": "N", "Q": "Q", "N": "N", "B": "B", "R": "R"}
PROMO_EN_TO_FR = {"Q": "D", "R": "T", "B": "F", "N": "C"}


def _split_suffix(san):
    suffix = ""
    while san and san[-1] in "+#!?":
        if san[-1] in "+#":
            suffix = san[-1] + suffix
        san = san[:-1]
    return san, suffix


def _castle_en(body):
    if body in ("O-O-O", "0-0-0"):
        return "O-O-O"
    if body in ("O-O", "0-0"):
        return "O-O"
    return None


def parse_san_fr(san_fr, board=None):
    """Traduit un SAN français en SAN anglais (python-chess)."""
    san = (san_fr or "").strip().replace("×", "x")
    if not san:
        raise ValueError("SAN vide")

    body, suffix = _split_suffix(san)
    castle = _castle_en(body)
    if castle:
        return castle + suffix

    promotion = ""
    if "=" in body:
        body, promo = body.split("=", 1)
        promo = (promo or "").strip().upper()[:1]
        promotion = "=" + PROMO_FR_TO_EN.get(promo, promo)
    elif body and body[0] in "abcdefgh" and len(body) >= 3:
        last = body[-1].upper()
        if last in PROMO_FR_TO_EN and body[-2] in "18":
            promotion = "=" + PROMO_FR_TO_EN[last]
            body = body[:-1]

    piece = ""
    if body and body[0] in PIECE_FR_TO_EN:
        piece = PIECE_FR_TO_EN[body[0]]
        body = body[1:]

    capture = "x" in body
    if capture:
        left, right = body.split("x", 1)
        if not piece and not left:
            raise ValueError("Prise de pion sans colonne d'origine")
    else:
        if len(body) < 2:
            raise ValueError("SAN trop court")
        left, right = body[:-2], body[-2:]

    disamb = ""
    if piece:
        disamb = left
    elif capture:
        disamb = left[:1]

    arrival = right[-2:] if len(right) >= 2 else right
    return piece + disamb + ("x" if capture else "") + arrival + promotion + suffix


def to_san_fr(san_en, board=None):
    """Traduit un SAN anglais (python-chess) en SAN français."""
    san = (san_en or "").strip()
    if not san:
        return ""

    body, suffix = _split_suffix(san)
    castle = _castle_en(body)
    if castle:
        return castle + suffix

    promotion = ""
    if "=" in body:
        body, promo = body.split("=", 1)
        promo = (promo or "").strip().upper()[:1]
        promotion = "=" + PROMO_EN_TO_FR.get(promo, promo)

    piece = ""
    if body and body[0] in PIECE_EN_TO_FR:
        piece = PIECE_EN_TO_FR[body[0]]
        body = body[1:]

    capture = "x" in body
    if capture:
        left, right = body.split("x", 1)
    else:
        if len(body) < 2:
            return san
        left, right = body[:-2], body[-2:]

    disamb = left if piece else (left[:1] if capture else "")
    arrival = right[-2:] if len(right) >= 2 else right
    return piece + disamb + ("x" if capture else "") + arrival + promotion + suffix


def _norm_san(text):
    s = (text or "").strip().replace("×", "x").replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    body, suffix = _split_suffix(s)
    return body + suffix, body


def parse_move(text, board):
    """Interprète UCI, SAN EN ou SAN FR en coup légal."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Indique un coup")

    compact = raw.replace(" ", "")
    lowered = compact.lower()

    try:
        move = chess.Move.from_uci(lowered)
        if move in board.legal_moves:
            return move
    except ValueError:
        pass

    for candidate in (raw, compact):
        try:
            return board.parse_san(candidate)
        except ValueError:
            pass

    try:
        translated = parse_san_fr(raw, board)
        return board.parse_san(translated)
    except (ValueError, IndexError):
        pass

    target_full, target_body = _norm_san(compact)
    try:
        _, fr_body = _norm_san(parse_san_fr(raw, board))
    except (ValueError, IndexError):
        fr_body = target_body

    for move in board.legal_moves:
        san = board.san(move)
        fr = to_san_fr(san)
        san_full, san_body = _norm_san(san)
        fr_full, fr_body_legal = _norm_san(fr)
        if target_full in (san_full, fr_full) or target_body in (san_body, fr_body_legal, fr_body):
            return move

    raise ValueError("Coup illégal: %s" % raw)
