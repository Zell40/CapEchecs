# -*- coding: utf-8 -*-
"""Tests de notation (python-chess, sans Limnoria)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import chess
from local.san_fr import parse_move, parse_san_fr, to_san_fr
from local.game import apply_move, board_outcome
from local.game import GameState


class SanFrTests(unittest.TestCase):
    def test_castle_with_check(self):
        self.assertEqual(parse_san_fr("O-O+"), "O-O+")
        self.assertEqual(parse_san_fr("O-O-O#"), "O-O-O#")
        self.assertEqual(parse_san_fr("0-0"), "O-O")
        self.assertEqual(to_san_fr("O-O+"), "O-O+")
        self.assertEqual(to_san_fr("O-O-O#"), "O-O-O#")

    def test_pieces(self):
        self.assertEqual(parse_san_fr("Cf3"), "Nf3")
        self.assertEqual(parse_san_fr("Fxe5"), "Bxe5")
        self.assertEqual(parse_san_fr("Td1"), "Rd1")
        self.assertEqual(parse_san_fr("Dd8+"), "Qd8+")
        self.assertEqual(parse_san_fr("Re2"), "Ke2")
        self.assertEqual(to_san_fr("Nf3"), "Cf3")
        self.assertEqual(to_san_fr("Bxe5"), "Fxe5")
        self.assertEqual(to_san_fr("Rd1"), "Td1")
        self.assertEqual(to_san_fr("Ke2"), "Re2")

    def test_pawn_and_promo(self):
        self.assertEqual(parse_san_fr("e4"), "e4")
        self.assertEqual(parse_san_fr("exd5"), "exd5")
        self.assertEqual(parse_san_fr("e8=D"), "e8=Q")
        self.assertEqual(parse_san_fr("e8D"), "e8=Q")
        self.assertEqual(to_san_fr("e8=Q#"), "e8=D#")

    def test_disambiguation(self):
        self.assertEqual(parse_san_fr("Cbd2"), "Nbd2")
        self.assertEqual(to_san_fr("Nbd2"), "Cbd2")

    def test_parse_move_start(self):
        board = chess.Board()
        self.assertEqual(parse_move("e4", board), chess.Move.from_uci("e2e4"))
        self.assertEqual(parse_move("e2e4", board), chess.Move.from_uci("e2e4"))
        self.assertEqual(parse_move("Cf3", board), chess.Move.from_uci("g1f3"))
        self.assertEqual(parse_move("Nf3", board), chess.Move.from_uci("g1f3"))

    def test_scholars_mate_fr(self):
        gs = GameState("ai", "test")
        for san in ("e4", "e5", "Dh5", "Cc6", "Fc4", "Cf6", "Dxf7#"):
            move = parse_move(san, gs.board)
            apply_move(gs, move)
        self.assertTrue(gs.board.is_checkmate())
        reason, winner = board_outcome(gs.board)
        self.assertEqual(reason, "mate")
        self.assertEqual(winner, "white")
        self.assertEqual(gs.sans_fr[-1], "Dxf7#")


if __name__ == "__main__":
    unittest.main()
