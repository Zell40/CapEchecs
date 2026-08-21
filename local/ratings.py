# -*- coding: utf-8 -*-
"""ELO EntreNous (JSON) + lecture Chess.com (API publique)."""
from __future__ import print_function

import json
import os
import re
import time

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

try:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import HTTPError, Request, urlopen

from supybot import conf, log

START_ELO = 1200
K_NEW = 32
K_OLD = 16
REFRESH_AFTER = 6 * 3600
CC_USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,25}$")
CC_UA = "EntreNous-CapEchecs/1.0 (https://entrenous.chat)"


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


def valid_chesscom_user(username):
    return bool(CC_USER_RE.match(str(username or "").strip().lstrip("@")))


def _lookup_keys(nick, account=None):
    keys = []
    for value in (account, nick):
        key = _key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _empty_record(nick):
    return {
        "nick": nick,
        "account": "",
        "elo": START_ELO,
        "games": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "chesscom": "",
        "cc-rapid": "",
        "cc-blitz": "",
        "cc-bullet": "",
        "cc-name": "",
        "cc-title": "",
        "cc-url": "",
        "cc-avatar": "",
        "cc-country": "",
    }


def _public_record(nick, rec):
    rec = rec or {}
    return {
        "nick": rec.get("nick") or nick,
        "account": rec.get("account") or "",
        "elo": int(rec.get("elo") or START_ELO),
        "games": int(rec.get("games") or 0),
        "wins": int(rec.get("wins") or 0),
        "draws": int(rec.get("draws") or 0),
        "losses": int(rec.get("losses") or 0),
        "chesscom": rec.get("chesscom") or "",
        "cc-rapid": rec.get("cc_rapid") or rec.get("cc-rapid") or "",
        "cc-blitz": rec.get("cc_blitz") or rec.get("cc-blitz") or "",
        "cc-bullet": rec.get("cc_bullet") or rec.get("cc-bullet") or "",
        "cc-name": rec.get("cc_name") or rec.get("cc-name") or "",
        "cc-title": rec.get("cc_title") or rec.get("cc-title") or "",
        "cc-url": rec.get("cc_url") or rec.get("cc-url") or "",
        "cc-avatar": rec.get("cc_avatar") or rec.get("cc-avatar") or "",
        "cc-country": rec.get("cc_country") or rec.get("cc-country") or "",
        "cc_fetched_at": int(rec.get("cc_fetched_at") or 0),
    }


def player_record(nick, account=None):
    data = _load()
    rec = None
    for key in _lookup_keys(nick, account):
        rec = data["players"].get(key)
        if rec:
            break
    return _public_record(nick, rec)


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


def _http_json(url):
    req = Request(url, headers={"User-Agent": CC_UA, "Accept": "application/json"})
    try:
        resp = urlopen(req, timeout=8)
        try:
            raw = resp.read().decode("utf-8")
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return json.loads(raw), 200
    except HTTPError as exc:
        return None, int(getattr(exc, "code", 0) or 0)
    except Exception as exc:
        log.warning("CapEchecs: Chess.com %s: %s", url, exc)
        return None, 0


def fetch_chesscom_profile(username):
    user = str(username or "").strip().lstrip("@")
    if not valid_chesscom_user(user):
        raise ValueError("Pseudo Chess.com invalide")
    url = "https://api.chess.com/pub/player/%s" % quote(user)
    payload, code = _http_json(url)
    if code == 404:
        raise ValueError("Aucun compte Chess.com nommé %s" % user)
    if not payload or (code and code >= 400):
        raise ValueError("Chess.com indisponible (%s)" % (code or "réseau"))
    country = str(payload.get("country") or "")
    if "/country/" in country:
        country = country.rsplit("/", 1)[-1]
    return {
        "username": payload.get("username") or user,
        "name": payload.get("name") or "",
        "title": payload.get("title") or "",
        "url": payload.get("url") or "",
        "avatar": payload.get("avatar") or "",
        "country": country,
    }


def fetch_chesscom_stats(username):
    user = str(username or "").strip().lstrip("@")
    url = "https://api.chess.com/pub/player/%s/stats" % quote(user)
    payload, code = _http_json(url)
    if not payload or (code and code >= 400):
        return {"rapid": "", "blitz": "", "bullet": ""}
    rapid = ((payload.get("chess_rapid") or {}).get("last") or {}).get("rating") or ""
    blitz = ((payload.get("chess_blitz") or {}).get("last") or {}).get("rating") or ""
    bullet = ((payload.get("chess_bullet") or {}).get("last") or {}).get("rating") or ""
    return {"rapid": str(rapid), "blitz": str(blitz), "bullet": str(bullet)}


def _store_link(nick, account, profile, stats):
    data = _load()
    rec = None
    for key in _lookup_keys(nick, account):
        if key in data["players"]:
            rec = data["players"][key]
            break
    if rec is None:
        rec = _ensure(data, account or nick)
    rec["nick"] = nick
    if account:
        rec["account"] = account
    rec["chesscom"] = profile["username"]
    rec["cc_name"] = profile.get("name") or ""
    rec["cc_title"] = profile.get("title") or ""
    rec["cc_url"] = profile.get("url") or ""
    rec["cc_avatar"] = profile.get("avatar") or ""
    rec["cc_country"] = profile.get("country") or ""
    rec["cc_rapid"] = stats.get("rapid") or ""
    rec["cc_blitz"] = stats.get("blitz") or ""
    rec["cc_bullet"] = stats.get("bullet") or ""
    rec["cc_fetched_at"] = int(time.time())
    primary = _key(account or nick)
    data["players"][primary] = rec
    nick_key = _key(nick)
    if nick_key and nick_key != primary:
        data["players"][nick_key] = dict(rec)
    _save(data)
    return player_record(nick, account)


def link_chesscom(nick, username, account=None):
    user = str(username or "").strip().lstrip("@")
    if not user:
        raise ValueError("Indique un pseudo Chess.com")
    profile = fetch_chesscom_profile(user)
    stats = fetch_chesscom_stats(profile["username"])
    return _store_link(nick, account, profile, stats)


def resolve_on_join(nick, account):
    """Essaie le compte Anope, puis le lien déjà stocké.

    Retourne (record, status) avec status :
      linked  — déjà connu, éventuellement rafraîchi
      anope   — trouvé via le nom de compte Anope
      missing — rien trouvé, il faut demander le pseudo
      error   — API indisponible
    """
    rec = player_record(nick, account)
    if rec.get("chesscom"):
        stale = time.time() - (rec.get("cc_fetched_at") or 0) > REFRESH_AFTER
        if stale:
            try:
                rec = link_chesscom(nick, rec["chesscom"], account)
            except ValueError:
                pass
        return rec, "linked"
    if account and valid_chesscom_user(account):
        try:
            rec = link_chesscom(nick, account, account)
            return rec, "anope"
        except ValueError as exc:
            text = str(exc)
            if "introuvable" in text or "Aucun compte" in text or "invalide" in text:
                return rec, "missing"
            return rec, "error"
    return rec, "missing"
