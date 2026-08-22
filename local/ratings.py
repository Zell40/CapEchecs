# -*- coding: utf-8 -*-
"""ELO EntreNous (JSON) + lecture Chess.com (API publique)."""
from __future__ import print_function

import json
import os
import random
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
DISPLAY_RE = re.compile(r"^[\w \-'.]{2,24}$", re.UNICODE)
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


def _flag_verified(rec):
    rec = rec or {}
    value = rec.get("cc_verified")
    if value is None:
        value = rec.get("cc-verified")
    return "1" if value in (1, True, "1") else "0"


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
        "cc-league": "",
        "cc-tac": "",
        "cc-rapid-best": "",
        "cc-rapid-rec": "",
        "cc-blitz-best": "",
        "cc-blitz-rec": "",
        "cc-bullet-best": "",
        "cc-bullet-rec": "",
        "en-name": "",
        "cc-verified": "0",
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
        "cc-league": rec.get("cc_league") or rec.get("cc-league") or "",
        "cc-tac": rec.get("cc_tac") or rec.get("cc-tac") or "",
        "cc-rapid-best": rec.get("cc_rapid_best") or rec.get("cc-rapid-best") or "",
        "cc-rapid-rec": rec.get("cc_rapid_rec") or rec.get("cc-rapid-rec") or "",
        "cc-blitz-best": rec.get("cc_blitz_best") or rec.get("cc-blitz-best") or "",
        "cc-blitz-rec": rec.get("cc_blitz_rec") or rec.get("cc-blitz-rec") or "",
        "cc-bullet-best": rec.get("cc_bullet_best") or rec.get("cc-bullet-best") or "",
        "cc-bullet-rec": rec.get("cc_bullet_rec") or rec.get("cc-bullet-rec") or "",
        "en-name": rec.get("display") or rec.get("en-name") or "",
        "cc-verified": _flag_verified(rec),
        "cc_fetched_at": int(rec.get("cc_fetched_at") or 0),
        "cc_optout": bool(int(rec.get("cc_optout") or 0)),
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


def _touch_player(data, nick, account=None):
    """Une seule fiche joueur, partagée entre le nick IRC et le compte Anope."""
    rec = None
    for key in _lookup_keys(nick, account):
        if key in data["players"]:
            rec = data["players"][key]
            break
    if rec is None:
        rec = _ensure(data, account or nick)
    rec["nick"] = nick or rec.get("nick") or ""
    if account:
        rec["account"] = account
    for key in _lookup_keys(nick, account):
        data["players"][key] = rec
    return rec


def expected_score(ra, rb):
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def apply_rated_game(white, black, result, white_account=None, black_account=None):
    """result: 1-0 | 0-1 | 1/2-1/2. Retourne (rec_w, rec_b, delta_w)."""
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if not white or not black:
        return None
    if white.lower() == black.lower():
        return None
    if white.lower() == "ia" or black.lower() == "ia":
        return None
    data = _load()
    rw = _touch_player(data, white, white_account)
    rb = _touch_player(data, black, black_account)
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


def apply_rated_vs_ai(nick, ai_elo, result, human_is_white, account=None):
    """Met à jour l'ELO du joueur face à une IA de niveau `ai_elo`."""
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if not nick or str(nick).lower() == "ia":
        return None
    try:
        ai_elo = int(ai_elo)
    except (TypeError, ValueError):
        ai_elo = 1400
    data = _load()
    rec = _touch_player(data, nick, account)
    if human_is_white:
        sa = 1.0 if result == "1-0" else 0.0 if result == "0-1" else 0.5
    else:
        sa = 1.0 if result == "0-1" else 0.0 if result == "1-0" else 0.5
    ea = expected_score(rec["elo"], ai_elo)
    k = K_NEW if rec["games"] < 20 else K_OLD
    dw = int(round(k * (sa - ea)))
    rec["elo"] = max(100, rec["elo"] + dw)
    rec["games"] += 1
    if sa == 1.0:
        rec["wins"] += 1
    elif sa == 0.0:
        rec["losses"] += 1
    else:
        rec["draws"] += 1
    rec["updated"] = int(time.time())
    _save(data)
    return player_record(nick, account), dw, ai_elo


def make_cc_token():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "EN-" + "".join(random.choice(alphabet) for _ in range(4))


def profile_has_token(profile, token):
    token = str(token or "").strip().upper()
    if not token:
        return False
    loc = str((profile or {}).get("location") or "").upper()
    compact = loc.replace(" ", "").replace("-", "")
    return token in loc or token.replace("-", "") in compact


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
        "league": payload.get("league") or "",
        "location": payload.get("location") or "",
    }


def _cc_cat(block):
    block = block or {}
    last = (block.get("last") or {}).get("rating")
    best = (block.get("best") or {}).get("rating")
    rec = block.get("record") or {}
    rec_s = ""
    if rec:
        rec_s = "%s-%s-%s" % (
            int(rec.get("win") or 0),
            int(rec.get("loss") or 0),
            int(rec.get("draw") or 0),
        )
    return {
        "last": str(last) if last not in (None, "") else "",
        "best": str(best) if best not in (None, "") else "",
        "rec": rec_s,
    }


def fetch_chesscom_stats(username):
    user = str(username or "").strip().lstrip("@")
    url = "https://api.chess.com/pub/player/%s/stats" % quote(user)
    payload, code = _http_json(url)
    empty = {"last": "", "best": "", "rec": ""}
    if not payload or (code and code >= 400):
        return {"rapid": empty, "blitz": empty, "bullet": empty, "tactics": ""}
    rapid = _cc_cat(payload.get("chess_rapid"))
    blitz = _cc_cat(payload.get("chess_blitz"))
    bullet = _cc_cat(payload.get("chess_bullet"))
    tactics = ((payload.get("tactics") or {}).get("highest") or {}).get("rating") or ""
    return {
        "rapid": rapid,
        "blitz": blitz,
        "bullet": bullet,
        "tactics": str(tactics) if tactics not in (None, "") else "",
    }


def _cat_get(stats, key, field):
    block = (stats or {}).get(key)
    if isinstance(block, dict):
        return str(block.get(field) or "")
    if field == "last":
        return str(block or "")
    return ""


def _store_link(nick, account, profile, stats, verified=None):
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
    old_user = str(rec.get("chesscom") or "").lower()
    new_user = str(profile.get("username") or "").lower()
    rec["chesscom"] = profile["username"]
    rec["cc_name"] = profile.get("name") or ""
    rec["cc_title"] = profile.get("title") or ""
    rec["cc_url"] = profile.get("url") or ""
    rec["cc_avatar"] = profile.get("avatar") or ""
    rec["cc_country"] = profile.get("country") or ""
    rec["cc_league"] = profile.get("league") or ""
    rec["cc_tac"] = (stats or {}).get("tactics") or ""
    rec["cc_rapid"] = _cat_get(stats, "rapid", "last")
    rec["cc_blitz"] = _cat_get(stats, "blitz", "last")
    rec["cc_bullet"] = _cat_get(stats, "bullet", "last")
    rec["cc_rapid_best"] = _cat_get(stats, "rapid", "best")
    rec["cc_blitz_best"] = _cat_get(stats, "blitz", "best")
    rec["cc_bullet_best"] = _cat_get(stats, "bullet", "best")
    rec["cc_rapid_rec"] = _cat_get(stats, "rapid", "rec")
    rec["cc_blitz_rec"] = _cat_get(stats, "blitz", "rec")
    rec["cc_bullet_rec"] = _cat_get(stats, "bullet", "rec")
    rec["cc_fetched_at"] = int(time.time())
    rec["cc_optout"] = 0
    if verified is True:
        rec["cc_verified"] = 1
    elif verified is False or old_user != new_user:
        rec["cc_verified"] = 0
    rec.setdefault("cc_verified", 0)
    primary = _key(account or nick)
    data["players"][primary] = rec
    nick_key = _key(nick)
    if nick_key and nick_key != primary:
        data["players"][nick_key] = dict(rec)
    _save(data)
    return player_record(nick, account)


def peek_chesscom(username):
    """Lit l'API sans rien enregistrer. Lève ValueError si absent / invalide."""
    profile = fetch_chesscom_profile(username)
    stats = fetch_chesscom_stats(profile["username"])
    return profile, stats


def link_chesscom(nick, username, account=None):
    profile, stats = peek_chesscom(username)
    return _store_link(nick, account, profile, stats)


def preview_from_api(profile, stats):
    return {
        "chesscom": profile.get("username") or "",
        "cc-name": profile.get("name") or "",
        "cc-title": profile.get("title") or "",
        "cc-url": profile.get("url") or "",
        "cc-country": profile.get("country") or "",
        "cc-league": profile.get("league") or "",
        "cc-tac": (stats or {}).get("tactics") or "",
        "cc-rapid": _cat_get(stats, "rapid", "last"),
        "cc-blitz": _cat_get(stats, "blitz", "last"),
        "cc-bullet": _cat_get(stats, "bullet", "last"),
        "cc-rapid-best": _cat_get(stats, "rapid", "best"),
        "cc-blitz-best": _cat_get(stats, "blitz", "best"),
        "cc-bullet-best": _cat_get(stats, "bullet", "best"),
        "cc-rapid-rec": _cat_get(stats, "rapid", "rec"),
        "cc-blitz-rec": _cat_get(stats, "blitz", "rec"),
        "cc-bullet-rec": _cat_get(stats, "bullet", "rec"),
    }


def cc_tag_fields(rec):
    rec = rec or {}
    keys = (
        "chesscom", "cc-name", "cc-title", "cc-country", "cc-league", "cc-tac",
        "cc-rapid", "cc-blitz", "cc-bullet",
        "cc-rapid-best", "cc-blitz-best", "cc-bullet-best",
        "cc-rapid-rec", "cc-blitz-rec", "cc-bullet-rec",
        "cc-verified",
    )
    out = {}
    for key in keys:
        if key == "cc-verified":
            out[key] = _flag_verified(rec)
            continue
        out[key] = rec.get(key) or ""
    return out


def confirm_link(nick, account, profile, stats):
    return _store_link(nick, account, profile, stats, verified=True)


def valid_display_name(name):
    s = " ".join(str(name or "").split())
    if not s:
        return True
    if len(s) < 2 or len(s) > 24:
        return False
    return bool(DISPLAY_RE.match(s))


def set_display_name(nick, account, name):
    """Enregistre le nom affiché EntreNous. Chaîne vide = revenir au nick IRC."""
    s = " ".join(str(name or "").split())
    if s and not valid_display_name(s):
        raise ValueError("Nom invalide (2–24 caractères, lettres, chiffres, espaces).")
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
    rec["display"] = s
    primary = _key(account or nick)
    data["players"][primary] = rec
    nick_key = _key(nick)
    if nick_key and nick_key != primary:
        data["players"][nick_key] = dict(rec)
    _save(data)
    return player_record(nick, account)


def set_optout(nick, account=None, opted=True):
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
    rec["cc_optout"] = 1 if opted else 0
    primary = _key(account or nick)
    data["players"][primary] = rec
    nick_key = _key(nick)
    if nick_key and nick_key != primary:
        data["players"][nick_key] = dict(rec)
    _save(data)
    return player_record(nick, account)


def resolve_on_join(nick, account):
    """Ne stocke rien. Retourne (record_ou_apercu, status).

    linked  — déjà confirmé
    optout  — l'utilisateur a refusé la fonctionnalité
    found   — compte Anope trouvé sur Chess.com (en attente de confirmation)
    missing — pas de compte correspondant
    error   — API indisponible
    """
    rec = player_record(nick, account)
    if rec.get("cc_optout"):
        return rec, "optout"
    if rec.get("chesscom"):
        stale = time.time() - (rec.get("cc_fetched_at") or 0) > REFRESH_AFTER
        incomplete = not (
            rec.get("cc-league") or rec.get("cc-rapid-best") or rec.get("cc-rapid-rec")
            or rec.get("cc-blitz-best") or rec.get("cc-bullet-best")
        )
        if stale or incomplete:
            try:
                rec = link_chesscom(nick, rec["chesscom"], account)
            except ValueError:
                pass
        return rec, "linked"
    if account and valid_chesscom_user(account):
        try:
            profile, stats = peek_chesscom(account)
            preview = dict(rec)
            preview.update(preview_from_api(profile, stats))
            preview["_profile"] = profile
            preview["_stats"] = stats
            return preview, "found"
        except ValueError as exc:
            text = str(exc)
            if "Aucun compte" in text or "invalide" in text:
                return rec, "missing"
            return rec, "error"
    return rec, "missing"
