# -*- coding: utf-8 -*-
"""État d'une partie et application des coups."""
from __future__ import print_function

import time

import chess

from .san_fr import to_san_fr

UNICODE_PIECES = {
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
}

REASON_FR = {
    "mate": "échec et mat",
    "stalemate": "pat",
    "insufficient": "matériel insuffisant",
    "fifty": "règle des 50 coups",
    "threefold": "triple répétition",
    "seventyfive": "règle des 75 coups",
    "fivefold": "répétition quintuple",
    "resign": "abandon",
    "abort": "partie annulée",
    "timeout": "délai d'attente dépassé",
    "flag": "temps écoulé",
    "inactivity": "inactivité",
    "quit": "déconnexion",
    "part": "départ du salon",
    "kick": "exclusion",
    "engine": "erreur du moteur",
    "agree": "nulle acceptée",
}

TERMINATION_TO_REASON = {
    chess.Termination.CHECKMATE: "mate",
    chess.Termination.STALEMATE: "stalemate",
    chess.Termination.INSUFFICIENT_MATERIAL: "insufficient",
    chess.Termination.FIFTY_MOVES: "fifty",
    chess.Termination.THREEFOLD_REPETITION: "threefold",
}
for _name, _reason in (
    ("SEVENTYFIVE_MOVES", "seventyfive"),
    ("FIVEFOLD_REPETITION", "fivefold"),
):
    _term = getattr(chess.Termination, _name, None)
    if _term is not None:
        TERMINATION_TO_REASON[_term] = _reason


class GameState(object):
    def __init__(self, mode, creator, engine=None):
        self.board = chess.Board()
        self.engine = engine
        self.mode = mode  # 'ai' | 'pvp'
        self.creator = creator
        self.players = {"white": None, "black": None}
        self.captured_white = []
        self.captured_black = []
        self.sans_en = []
        self.sans_fr = []
        self.ucis = []
        self.invited = None
        self.waiting_join = False
        self.started_at = time.time()
        self.gid = str(int(self.started_at * 1000))
        self.idle_event = None
        self.wait_event = None
        self.draw_offered_by = None
        self.abort_offered_by = None
        self.last_uci = ""
        self.last_san_fr = ""
        self.last_from = ""
        self.last_to = ""
        self.skill = None
        self.think_time = None
        self.tc = "casual"
        self.clock_base = 0
        self.clock_inc = 0
        self.clocks = {"white": 0.0, "black": 0.0}
        self.clock_stamp = time.time()
        self.clock_event = None
        self.rated = False

    def color_of(self, nick):
        if not nick:
            return None
        low = nick.lower()
        for color, player in self.players.items():
            if player and player.lower() == low:
                return color
        return None

    def nick_for(self, color):
        return self.players.get(color)

    def side_to_move(self):
        return "white" if self.board.turn == chess.WHITE else "black"

    def expected_nick(self):
        return self.players[self.side_to_move()]

    def is_player(self, nick):
        return self.color_of(nick) is not None

    def rename(self, old, new):
        for color, player in list(self.players.items()):
            if player and player.lower() == old.lower():
                self.players[color] = new
        if self.creator and self.creator.lower() == old.lower():
            self.creator = new
        if self.invited and self.invited.lower() == old.lower():
            self.invited = new
        if self.draw_offered_by and self.draw_offered_by.lower() == old.lower():
            self.draw_offered_by = new
        if self.abort_offered_by and self.abort_offered_by.lower() == old.lower():
            self.abort_offered_by = new

    def captured_str(self, color):
        bag = self.captured_white if color == "white" else self.captured_black
        return "".join(bag)

    def captured_unicode(self, color):
        bag = self.captured_white if color == "white" else self.captured_black
        return "".join(UNICODE_PIECES.get(p, p) for p in bag)

    def fen_tag(self):
        return self.board.fen().replace(" ", "_")

    def ply(self):
        return len(self.board.move_stack)

    def opening(self):
        from .openings import opening_name
        return opening_name(self.ucis)

    def opening_family(self):
        from . import openings
        fn = getattr(openings, "opening_family", None)
        if fn:
            return fn(self.ucis)
        name = openings.opening_name(self.ucis) or ""
        if "," in name:
            return name.split(",", 1)[0].strip()
        return name

    def opening_variant(self):
        from . import openings
        fn = getattr(openings, "opening_variant", None)
        if fn:
            return fn(self.ucis)
        name = openings.opening_name(self.ucis) or ""
        if "," in name:
            return name.split(",", 1)[1].strip()
        return ""

    def duration(self):
        return max(0, int(time.time() - self.started_at))


class MoveInfo(object):
    __slots__ = (
        "move", "color", "san_en", "san_fr", "uci",
        "from_sq", "to_sq", "promo", "captured",
        "check", "mate",
    )

    def __init__(self, **kwargs):
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))


def captured_symbol(board, move):
    if not board.is_capture(move):
        return ""
    if board.is_en_passant(move):
        square = chess.square(
            chess.file_index(move.to_square),
            chess.rank_index(move.from_square),
        )
        piece = board.piece_at(square)
        return piece.symbol() if piece else "p" if board.turn == chess.WHITE else "P"
    piece = board.piece_at(move.to_square)
    return piece.symbol() if piece else ""


def apply_move(gs, move):
    """Joue un coup déjà légal et met à jour l'état."""
    board = gs.board
    color = "white" if board.turn == chess.WHITE else "black"
    captured = captured_symbol(board, move)
    san_en = board.san(move)
    info = MoveInfo(
        move=move,
        color=color,
        san_en=san_en,
        san_fr=to_san_fr(san_en),
        uci=move.uci(),
        from_sq=chess.square_name(move.from_square),
        to_sq=chess.square_name(move.to_square),
        promo=chess.piece_symbol(move.promotion).lower() if move.promotion else "",
        captured=captured,
        check=False,
        mate=False,
    )
    board.push(move)
    info.check = board.is_check() and not board.is_checkmate()
    info.mate = board.is_checkmate()

    if captured:
        if color == "white":
            gs.captured_white.append(captured)
        else:
            gs.captured_black.append(captured)

    gs.sans_en.append(san_en)
    gs.sans_fr.append(info.san_fr)
    gs.ucis.append(info.uci)
    gs.last_uci = info.uci
    gs.last_san_fr = info.san_fr
    gs.last_from = info.from_sq
    gs.last_to = info.to_sq
    gs.draw_offered_by = None
    gs.abort_offered_by = None
    return info


def board_outcome(board):
    """Retourne (reason, winner_color|None) ou None si la partie continue."""
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return None
    reason = TERMINATION_TO_REASON.get(outcome.termination, "end")
    winner = None
    if outcome.winner is True:
        winner = "white"
    elif outcome.winner is False:
        winner = "black"
    return reason, winner


def result_string(winner, reason):
    if reason in ("abort",) or (reason == "timeout" and not winner):
        return "*"
    if winner == "white":
        return "1-0"
    if winner == "black":
        return "0-1"
    if winner is None and reason not in ("inactivity", "engine"):
        return "1/2-1/2"
    return "*"


def reason_label(reason):
    return REASON_FR.get(reason, reason or "fin")


def render_board_lines(board, flip=False):
    files = list(range(8))
    ranks = list(range(8, 0, -1))
    if flip:
        files.reverse()
        ranks.reverse()
    header = " ".join("abcdefgh"[f] for f in files)
    lines = ["    " + header]
    for rank in ranks:
        cells = []
        for file_idx in files:
            square = chess.square(file_idx, rank - 1)
            piece = board.piece_at(square)
            if piece:
                cells.append(UNICODE_PIECES.get(piece.symbol(), piece.symbol()))
            else:
                cells.append("·")
        row = " ".join(cells)
        lines.append("%d | %s | %d" % (rank, row, rank))
    lines.append("    " + header)
    return lines
