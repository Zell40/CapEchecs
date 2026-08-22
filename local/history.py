# -*- coding: utf-8 -*-
"""Archive des parties terminées (JSON)."""
from __future__ import print_function

import json
import os
import time

from supybot import conf, log

MAX_GAMES = 80
MAX_PER_PLAYER = 25


def history_path():
    try:
        data = conf.supybot.directories.data()
    except Exception:
        data = "."
    return os.path.join(str(data), "CapEchecs-history.json")


def _load():
    path = history_path()
    if not os.path.isfile(path):
        return {"games": {}, "by_player": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"games": {}, "by_player": {}}
        data.setdefault("games", {})
        data.setdefault("by_player", {})
        return data
    except (OSError, ValueError) as exc:
        log.warning("CapEchecs: lecture historique: %s", exc)
        return {"games": {}, "by_player": {}}


def _save(data):
    path = history_path()
    tmp = path + ".tmp"
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("CapEchecs: écriture historique: %s", exc)


def _pkey(nick):
    return str(nick or "").strip().lower()


def save_game(record):
    gid = str((record or {}).get("gid") or "")
    if not gid:
        return
    data = _load()
    rec = dict(record)
    rec["gid"] = gid
    rec.setdefault("at", int(time.time()))
    data["games"][gid] = rec
    for nick in (rec.get("white"), rec.get("black")):
        key = _pkey(nick)
        if not key or key == "ia":
            continue
        ids = [x for x in data["by_player"].get(key, []) if x != gid]
        ids.insert(0, gid)
        data["by_player"][key] = ids[:MAX_PER_PLAYER]
    _prune(data)
    _save(data)


save = save_game


def update_review(gid, review_data):
    gid = str(gid or "")
    if not gid:
        return
    data = _load()
    rec = data["games"].get(gid)
    if not rec:
        return
    rec["review"] = review_data or {}
    data["games"][gid] = rec
    _save(data)


def get_game(gid):
    gid = str(gid or "")
    if not gid:
        return None
    return _load()["games"].get(gid)


def list_for(nick, limit=12):
    key = _pkey(nick)
    if not key:
        return []
    data = _load()
    out = []
    for gid in data["by_player"].get(key, [])[: max(1, int(limit or 12))]:
        rec = data["games"].get(gid)
        if rec:
            out.append(rec)
    return out


def _prune(data):
    games = data.get("games") or {}
    if len(games) <= MAX_GAMES:
        return
    ordered = sorted(
        games.values(),
        key=lambda rec: int(rec.get("at") or 0),
        reverse=True,
    )
    keep = {str(rec.get("gid")): rec for rec in ordered[:MAX_GAMES]}
    data["games"] = keep
    players = {}
    for gid, rec in keep.items():
        for nick in (rec.get("white"), rec.get("black")):
            key = _pkey(nick)
            if not key or key == "ia":
                continue
            players.setdefault(key, []).append(gid)
    for key, ids in list(players.items()):
        recs = sorted(
            (keep[g] for g in ids if g in keep),
            key=lambda rec: int(rec.get("at") or 0),
            reverse=True,
        )
        players[key] = [str(rec.get("gid")) for rec in recs[:MAX_PER_PLAYER]]
    data["by_player"] = players
