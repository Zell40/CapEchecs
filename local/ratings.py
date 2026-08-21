# -*- coding: utf-8 -*-
"""ELO EntreNous (JSON) + lecture Chess.com (API publique)."""
from __future__ import print_function

import json
import os
import time

try:
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import Request, urlopen

from supybot import conf, log

START_ELO = 1200
K_NEW = 32
K_OLD = 16


def ratings_path():
    try:
        data = conf.supybot.directories.data()
    except Exception:
        data = "."
    return os.path.join(str(data), "CapEchecs-ratings.json")


def _load():
    path = ratings_path()
    if not os.path.isfile(path):
        return {"players": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"players": {}}
        data.setdefault("players", {})
        return data
    except (OSError, ValueError) as exc:
        log.warning("CapEchecs: lecture ELO: %s", exc)
        return {"players": {}}


def _save(data):
    path = ratings_path()
    tmp = path + ".tmp"
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("CapEchecs: écriture ELO: %s", exc)


def _key(nick):
    return str(nick or "").strip().lower()


def player_record(nick):
    data = _load()
    rec = data["players"].get(_key(nick)) or {}
    return {
        "nick": nick,
        "elo": int(rec.get("elo") or START_ELO),
        "games": int(rec.get("games") or 0),
        "wins": int(rec.get("wins") or 0),
        "draws": int(rec.get("draws") or 0),
        "losses": int(rec.get("losses") or 0),
        "chesscom": rec.get("chesscom") or "",
        "cc-rapid": rec.get("cc_rapid") or "",
        "cc-blitz": rec.get("cc_blitz") or "",
        "cc-bullet": rec.get("cc_bullet") or "",
    }


def _ensure(data, nick):
    key = _key(nick)
    if key not in data["players"]:
        data["players"][key] = {
            "elo": START_ELO,
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "chesscom": "",
        }
    return data["players"][key]


def expected_score(ra, rb):
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def apply_rated_game(white, black, result):
    """result: 1-0 | 0-1 | 1/2-1/2. Retourne (rec_w, rec_b, delta_w)."""
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if not white or not black:
        return None
    if white.lower() == black.lower():
        return None
    data = _load()
    rw = _ensure(data, white)
    rb = _ensure(data, black)
    ea = expected_score(rw["elo"], rb["elo"])
    eb = 1.0 - ea
    if result == "1-0":
        sa, sb = 1.0, 0.0
        rw["wins"] += 1
        rb["losses"] += 1
    elif result == "0-1":
        sa, sb = 0.0, 1.0
        rw["losses"] += 1
        rb["wins"] += 1
    else:
        sa, sb = 0.5, 0.5
        rw["draws"] += 1
        rb["draws"] += 1
    ka = K_NEW if rw["games"] < 20 else K_OLD
    kb = K_NEW if rb["games"] < 20 else K_OLD
    dw = int(round(ka * (sa - ea)))
    db = int(round(kb * (sb - eb)))
    rw["elo"] = max(100, rw["elo"] + dw)
    rb["elo"] = max(100, rb["elo"] + db)
    rw["games"] += 1
    rb["games"] += 1
    rw["updated"] = int(time.time())
    rb["updated"] = int(time.time())
    _save(data)
    return player_record(white), player_record(black), dw


def link_chesscom(nick, username):
    user = str(username or "").strip().lstrip("@")
    if not user:
        raise ValueError("Indique un pseudo Chess.com")
    url = "https://api.chess.com/pub/player/%s/stats" % user
    req = Request(url, headers={"User-Agent": "EntreNous-CapEchecs/1.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise ValueError("Chess.com introuvable pour %s (%s)" % (user, exc))
    rapid = ((payload.get("chess_rapid") or {}).get("last") or {}).get("rating") or ""
    blitz = ((payload.get("chess_blitz") or {}).get("last") or {}).get("rating") or ""
    bullet = ((payload.get("chess_bullet") or {}).get("last") or {}).get("rating") or ""
    data = _load()
    rec = _ensure(data, nick)
    rec["chesscom"] = user
    rec["cc_rapid"] = str(rapid)
    rec["cc_blitz"] = str(blitz)
    rec["cc_bullet"] = str(bullet)
    _save(data)
    return player_record(nick)
