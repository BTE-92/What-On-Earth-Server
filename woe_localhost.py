#!/usr/bin/env python2.5
# -*- coding: utf-8 -*-
"""
Faux serveur local pour WhatOnEarth (beta de Big Bang Racing, Traplight, 2014).
PORTAGE PYTHON 2.5 / iOS (Cydia)

Le jeu contacte en dur :
    http://PlayDevLB-1049210432.us-west-2.elb.amazonaws.com/...
Ce serveur AWS n'existe plus, donc le jeu reste bloque sur "connecting to
server...".

Ce script fait DEUX choses en parallele :

1. Un serveur DNS (port 53) : quand l'appareil demande l'adresse de
   PlayDevLB-1049210432.us-west-2.elb.amazonaws.com, il repond avec l'IP
   voulue (celle de l'appareil qui fait tourner ce script). Pour toutes
   les autres adresses, il relaie la demande vers un vrai DNS (8.8.8.8).

2. Un serveur HTTP (port 80) : imite les reponses de l'API du jeu
   (login, amis, notifications, sauvegarde de maps...) et repond "OK" a
   tout, en affichant dans la console tout ce qu'il recoit.

3. Persistance des maps : les maps sauvegardees/publiees dans l'editeur
   sont stockees dans une base SQLite (whatonearth.db) et renvoyees
   identiques quand le jeu les recharge.

------------------------------------------------------------------------
NOTES DE PORTAGE VERS PYTHON 2.5 (par rapport a l'original Python 3)
------------------------------------------------------------------------
Python 2.5 (2006) n'a quasiment aucune des commodites du script d'origine.
Changements necessaires :

- `json` n'existe pas dans la stdlib avant Python 2.6 -> on essaie
  d'abord `import json`, sinon on retombe sur `simplejson` (a installer
  via Cydia/easy_install si absent).
- `urllib.parse` n'existe pas -> `urlparse`. Et `parse_qs` n'a rejoint le
  module `urlparse` qu'en Python 2.6 -> fallback vers `cgi.parse_qs`.
- `http.server.ThreadingHTTPServer` n'existe pas -> reconstruit a la main
  avec `SocketServer.ThreadingMixIn` + `BaseHTTPServer.HTTPServer`.
- Pas de f-strings -> formatage avec `%`, et `print` redevient une
  instruction (pas une fonction) comme en Python 2.5 pur.
- Pas de litteraux `b"..."` (arrives en 2.6) -> chaines normales, qui
  sont deja des octets en Python 2.
- Pas de type `bytes` distinct -> on distingue "dict a serialiser" de
  "octets bruts" avec `isinstance(payload, dict)` plutot que
  `isinstance(payload, bytes)`.
- `except X as e:` n'existe qu'a partir de 2.6 -> syntaxe `except X, e:`.
- `PermissionError` n'existe pas en Python 2 -> on capture `socket.error`
  et on regarde `e.errno` (via le module `errno`).
- `threading.Thread(..., daemon=True)` (le kwarg) date de Python 3.3 ->
  on utilise `thread.setDaemon(True)`.
- Indexer une chaine (`data[i]`) donne un caractere en Python 2, pas un
  entier comme en Python 3 -> il faut `ord(data[i])` dans le parseur DNS,
  sinon la lecture des labels DNS est silencieusement cassee.
- SQLite : la syntaxe `INSERT ... ON CONFLICT(...) DO UPDATE SET ...`
  (upsert) nécessite SQLite >= 3.24 (2018). Le SQLite lie a un Python de
  2006 est bien plus ancien et ne la comprend pas -> chaque upsert est
  remplace par un SELECT explicite puis un UPDATE ou un INSERT.
- On enleve `PRAGMA journal_mode=WAL` : le mode WAL est arrive dans
  SQLite 3.7 (2010), donc plus tard que la lib liee a un Python 2.5.
- Les blobs binaires (maps, miniatures) sont enregistres avec
  `sqlite3.Binary(...)` et relus avec `str(...)` : en Python 2, stocker
  une chaine "brute" (non-UTF8) telle quelle dans une colonne BLOB sans
  ce wrapper leve `ProgrammingError`.
- Pas de `with conn:` : pour rester simple et ne pas dependre du support
  du protocole context-manager par `sqlite3.Connection` (ni du futur
  `with_statement`, necessaire en 2.5 pour utiliser `with` du tout), on
  ouvre/committe/ferme chaque connexion explicitement en try/finally.

UTILISATION (sur iOS jailbreake, Python 2.5 installe via Cydia)
------------------------------------------------------------------------
1. Copie ce script sur l'appareil (SSH/SCP), et lance-le EN ROOT
   (obligatoire pour les ports privilegies 53 et 80) :
       ssh root@<ip-appareil>
       python2.5 whatonearth_fake_server_py25.py
   (Si le paquet Cydia installe juste "python", remplace par: python)

2. Regle le DNS Wi-Fi de l'appareil (Reglages > Wi-Fi > (i) > DNS >
   Manuel) sur l'IP affichee par le script au demarrage. Si le script
   tourne directement SUR l'appareil qui joue, utilise son adresse
   Wi-Fi locale (pas 127.0.0.1 -- iOS n'aime pas toujours ca en DNS
   manuel), affichee au lancement.

3. Redemarre le Wi-Fi (ou l'appareil).

4. Lance le jeu et regarde la console pour verifier la requete DNS puis
   la requete HTTP de login.

5. Remets le DNS sur "Automatique" une fois fini, sinon plus d'internet
   des que le script ne tourne plus.

Remarque : si `import sqlite3` echoue completement (paquet Python
minimaliste sans les bindings SQLite), il faudra soit installer
`pysqlite2` via easy_install sur l'appareil, soit demander une variante
de ce script qui stocke les donnees dans un simple fichier JSON au lieu
de SQLite.
"""

from __future__ import with_statement  # inutilise, garde par prudence si porte vers 2.6

import errno
import os
import random
import re
import socket
import struct
import threading
import time

try:
    import sqlite3
except ImportError:
    from pysqlite2 import dbapi2 as sqlite3

try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        # Ni `json` (stdlib >= 2.6) ni `simplejson` ne sont disponibles sur
        # ce Python. On utilise une mini-implementation JSON maison, en pur
        # Python, sans aucune dependance externe. Elle ne gere que ce dont
        # ce script a besoin : dumps()/loads() simples et JSONDecoder avec
        # raw_decode() (utilise pour extraire les metadonnees JSON du debut
        # d'un blob binaire de map).

        class _MiniJSONError(ValueError):
            pass

        class _MiniJSONDecoder:
            """Parseur JSON minimal, recursif, ecrit main."""

            def raw_decode(self, s, idx=0):
                idx = self._skip_ws(s, idx)
                value, idx = self._parse_value(s, idx)
                return value, idx

            def decode(self, s):
                value, idx = self.raw_decode(s, 0)
                idx = self._skip_ws(s, idx)
                if idx != len(s):
                    raise _MiniJSONError("Extra data after JSON value at position %d" % idx)
                return value

            def _skip_ws(self, s, idx):
                while idx < len(s) and s[idx] in " \t\n\r":
                    idx += 1
                return idx

            def _parse_value(self, s, idx):
                if idx >= len(s):
                    raise _MiniJSONError("Unexpected end of input")
                ch = s[idx]
                if ch == '{':
                    return self._parse_object(s, idx)
                if ch == '[':
                    return self._parse_array(s, idx)
                if ch == '"':
                    return self._parse_string(s, idx)
                if ch == '-' or ch.isdigit():
                    return self._parse_number(s, idx)
                if s[idx:idx + 4] == "true":
                    return True, idx + 4
                if s[idx:idx + 5] == "false":
                    return False, idx + 5
                if s[idx:idx + 4] == "null":
                    return None, idx + 4
                raise _MiniJSONError("Unexpected character %r at position %d" % (ch, idx))

            def _parse_object(self, s, idx):
                obj = {}
                idx += 1  # skip '{'
                idx = self._skip_ws(s, idx)
                if idx < len(s) and s[idx] == '}':
                    return obj, idx + 1
                while True:
                    idx = self._skip_ws(s, idx)
                    if idx >= len(s) or s[idx] != '"':
                        raise _MiniJSONError("Expected string key at position %d" % idx)
                    key, idx = self._parse_string(s, idx)
                    idx = self._skip_ws(s, idx)
                    if idx >= len(s) or s[idx] != ':':
                        raise _MiniJSONError("Expected ':' at position %d" % idx)
                    idx += 1
                    idx = self._skip_ws(s, idx)
                    value, idx = self._parse_value(s, idx)
                    obj[key] = value
                    idx = self._skip_ws(s, idx)
                    if idx >= len(s):
                        raise _MiniJSONError("Unterminated object")
                    if s[idx] == ',':
                        idx += 1
                        continue
                    if s[idx] == '}':
                        return obj, idx + 1
                    raise _MiniJSONError("Expected ',' or '}' at position %d" % idx)

            def _parse_array(self, s, idx):
                arr = []
                idx += 1  # skip '['
                idx = self._skip_ws(s, idx)
                if idx < len(s) and s[idx] == ']':
                    return arr, idx + 1
                while True:
                    idx = self._skip_ws(s, idx)
                    value, idx = self._parse_value(s, idx)
                    arr.append(value)
                    idx = self._skip_ws(s, idx)
                    if idx >= len(s):
                        raise _MiniJSONError("Unterminated array")
                    if s[idx] == ',':
                        idx += 1
                        continue
                    if s[idx] == ']':
                        return arr, idx + 1
                    raise _MiniJSONError("Expected ',' or ']' at position %d" % idx)

            def _parse_string(self, s, idx):
                idx += 1  # skip opening quote
                start = idx
                chars = []
                while True:
                    if idx >= len(s):
                        raise _MiniJSONError("Unterminated string")
                    ch = s[idx]
                    if ch == '"':
                        chars.append(s[start:idx])
                        return "".join(chars), idx + 1
                    if ch == '\\':
                        chars.append(s[start:idx])
                        idx += 1
                        if idx >= len(s):
                            raise _MiniJSONError("Unterminated escape")
                        esc = s[idx]
                        if esc == 'u':
                            hex_digits = s[idx + 1:idx + 5]
                            chars.append(unichr(int(hex_digits, 16)))
                            idx += 5
                            start = idx
                            continue
                        mapping = {
                            '"': '"', '\\': '\\', '/': '/', 'b': '\b',
                            'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
                        }
                        chars.append(mapping.get(esc, esc))
                        idx += 1
                        start = idx
                        continue
                    idx += 1

            def _parse_number(self, s, idx):
                start = idx
                if s[idx] == '-':
                    idx += 1
                while idx < len(s) and s[idx].isdigit():
                    idx += 1
                is_float = False
                if idx < len(s) and s[idx] == '.':
                    is_float = True
                    idx += 1
                    while idx < len(s) and s[idx].isdigit():
                        idx += 1
                if idx < len(s) and s[idx] in 'eE':
                    is_float = True
                    idx += 1
                    if idx < len(s) and s[idx] in '+-':
                        idx += 1
                    while idx < len(s) and s[idx].isdigit():
                        idx += 1
                text = s[start:idx]
                if is_float:
                    return float(text), idx
                return int(text), idx

        def _mini_indent_strs(indent, level):
            if indent is None:
                return u"", u"", u""
            return u"\n", u" " * (indent * (level + 1)), u" " * (indent * level)

        def _mini_escape_string(s, ensure_ascii):
            if isinstance(s, str):
                s = s.decode("utf-8", "replace")
            out = [u'"']
            for ch in s:
                o = ord(ch)
                if ch == u'"':
                    out.append(u'\\"')
                elif ch == u'\\':
                    out.append(u'\\\\')
                elif ch == u'\n':
                    out.append(u'\\n')
                elif ch == u'\r':
                    out.append(u'\\r')
                elif ch == u'\t':
                    out.append(u'\\t')
                elif o < 0x20:
                    out.append(u'\\u%04x' % o)
                elif o > 0x7e and ensure_ascii:
                    if o > 0xFFFF:
                        o -= 0x10000
                        out.append(u'\\u%04x\\u%04x' % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
                    else:
                        out.append(u'\\u%04x' % o)
                else:
                    out.append(ch)
            out.append(u'"')
            return u"".join(out)

        def _mini_encode(obj, parts, indent, ensure_ascii, level):
            if obj is None:
                parts.append(u"null")
            elif obj is True:
                parts.append(u"true")
            elif obj is False:
                parts.append(u"false")
            elif isinstance(obj, (int, long)):
                parts.append(unicode(obj))
            elif isinstance(obj, float):
                parts.append(unicode(repr(obj)))
            elif isinstance(obj, (str, unicode)):
                parts.append(_mini_escape_string(obj, ensure_ascii))
            elif isinstance(obj, (list, tuple)):
                if not obj:
                    parts.append(u"[]")
                    return
                nl, pad, pad_close = _mini_indent_strs(indent, level)
                parts.append(u"[")
                for i, item in enumerate(obj):
                    if i > 0:
                        parts.append(u",")
                    parts.append(nl + pad)
                    _mini_encode(item, parts, indent, ensure_ascii, level + 1)
                parts.append(nl + pad_close + u"]")
            elif isinstance(obj, dict):
                if not obj:
                    parts.append(u"{}")
                    return
                nl, pad, pad_close = _mini_indent_strs(indent, level)
                parts.append(u"{")
                items = obj.items()
                for i in range(len(items)):
                    k, v = items[i]
                    if i > 0:
                        parts.append(u",")
                    parts.append(nl + pad)
                    parts.append(_mini_escape_string(k if isinstance(k, (str, unicode)) else str(k), ensure_ascii))
                    parts.append(u": ")
                    _mini_encode(v, parts, indent, ensure_ascii, level + 1)
                parts.append(nl + pad_close + u"}")
            else:
                raise _MiniJSONError("Cannot serialize object of type %s" % type(obj))

        class _MiniJSONModule:
            JSONDecoder = _MiniJSONDecoder

            def dumps(self, obj, indent=None, ensure_ascii=True):
                parts = []
                _mini_encode(obj, parts, indent, ensure_ascii, 0)
                result = u"".join(parts)
                if ensure_ascii:
                    return result.encode("ascii")
                return result.encode("utf-8")

            def loads(self, s):
                if isinstance(s, str):
                    s = s.decode("utf-8")
                return _MiniJSONDecoder().decode(s)

        json = _MiniJSONModule()
        print "[JSON] Neither 'json' nor 'simplejson' found -- using built-in pure-Python fallback."

from urlparse import urlparse
try:
    from urlparse import parse_qs
except ImportError:
    from cgi import parse_qs

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import SocketServer

HTTP_PORT = 80
DNS_PORT = 53
TARGET_HOST = "playdevlb-1049210432.us-west-2.elb.amazonaws.com"
LOCAL_IP = "127.0.0.1"
UPSTREAM_DNS = ("8.8.8.8", 53)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatonearth.db")


class ThreadingHTTPServer(SocketServer.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        """Surcharge indispensable pour Python 2.5 (ThreadingMixIn n'honore pas daemon_threads)."""
        t = threading.Thread(target=self.process_request_thread,
                             args=(request, client_address))
        t.setDaemon(True)
        t.start()


# ---------------------------------------------------------------------
# Utilities pour l'encodage et affichage compatible Python 2.5.1
# ---------------------------------------------------------------------

def safe_str(val):
    """Securise la conversion d'un objet (str ou unicode) en chaine brute utf-8
    pour eviter tout crash d'affichage console (UnicodeEncodeError)."""
    if isinstance(val, unicode):
        return val.encode("utf-8", "replace")
    if isinstance(val, str):
        return val
    return str(val)


def to_db_str(val):
    """Encode explicitement en UTF-8 pour SQLite afin d'eviter les soucis d'8-bit bytestring
    lorsque conn.text_factory est actif."""
    if isinstance(val, unicode):
        return val.encode("utf-8", "ignore")
    if isinstance(val, str):
        return val
    if val is None:
        return ""
    return str(val)


# ---------------------------------------------------------------------
# Base de donnees SQLite : Initialisation et Connexions
# ---------------------------------------------------------------------

def get_db():
    """Cree une connexion SQLite propre au thread avec acces par nom de colonne.
    On force le text_factory a 'str' pour eviter tout crash sur caracteres accentues."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.text_factory = str  # REGLAGE CRITIQUE : desactive la coercition ASCII automatique
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialise le schema de la base de donnees s'il n'existe pas deja."""
    conn = get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            facebook_id TEXT DEFAULT '',
            gamecenter_id TEXT DEFAULT '',
            accept_notifications INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS minigames (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            creator_id TEXT,
            creator_name TEXT,
            tags TEXT DEFAULT '["TestTag1","TestTag2"]',
            published INTEGER DEFAULT 0,
            publish_time INTEGER,
            times_played INTEGER DEFAULT 0,
            times_finished INTEGER DEFAULT 0,
            times_liked INTEGER DEFAULT 0,
            is_new INTEGER DEFAULT 1,
            is_classic INTEGER DEFAULT 0,
            data_blob BLOB,
            thumbnail_blob BLOB
        );

        CREATE TABLE IF NOT EXISTS highscores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            player_name TEXT NOT NULL,
            time_score INTEGER NOT NULL,
            starts INTEGER DEFAULT 1,
            created_at INTEGER,
            UNIQUE(game_id, player_id) ON CONFLICT REPLACE
        );

        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            facebook_id TEXT DEFAULT '',
            gamecenter_id TEXT DEFAULT '',
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS likes (
            player_id TEXT NOT NULL,
            game_id TEXT NOT NULL,
            PRIMARY KEY (player_id, game_id)
        );
        """)
        conn.commit()
    finally:
        conn.close()
    print "[DB] SQLite database initialized at: %s" % safe_str(DB_PATH)


# ---------------------------------------------------------------------
# Partie HTTP : imite l'API du jeu
# ---------------------------------------------------------------------

def gen_object_id():
    """Genere un identifiant de 24 caracteres hexadecimaux, au format
    MongoDB ObjectId."""
    return "".join([random.choice("0123456789abcdef") for _ in range(24)])


def build_login_response(request_body):
    """Construit la reponse de login attendue par le jeu."""
    submitted_name = "Player"
    submitted_id = None
    try:
        parsed = json.loads(request_body.decode("utf-8"))
        if isinstance(parsed, dict):
            if "name" in parsed:
                submitted_name = parsed["name"]
            if "id" in parsed and parsed["id"]:
                submitted_id = parsed["id"]
    except Exception:
        pass

    oid = submitted_id if submitted_id else gen_object_id()

    conn = get_db()
    try:
        cur = conn.execute("SELECT id FROM players WHERE id = ?", (to_db_str(oid),))
        if cur.fetchone():
            conn.execute("UPDATE players SET name = ? WHERE id = ?", (to_db_str(submitted_name), to_db_str(oid)))
        else:
            conn.execute("INSERT INTO players (id, name) VALUES (?, ?)", (to_db_str(oid), to_db_str(submitted_name)))
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "OK",
        "id": oid,
        "name": submitted_name,
        "acceptNotifications": False,
        "facebookId": "",
        "gameCenterId": "",
    }


def extract_leading_json(blob):
    """Essaie de decoder le debut du blob comme du JSON pour recuperer
    les metadonnees lisibles (name, id, description, tags, creatorId...)."""
    try:
        text = blob.decode("utf-8", "ignore")
        obj, _end = json.JSONDecoder().raw_decode(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return None


def get_game_id_from_path(path):
    """Extrait l'ID de la map depuis l'URL (parametres id/gameId, plus la
    faute de frappe historique '?gameId<id>' sans signe '=')."""
    parsed = urlparse(path)
    query = parse_qs(parsed.query)

    for key in ["gameId", "id"]:
        if key in query and query[key][0]:
            return query[key][0]

    match = re.search(r"gameid=?([A-Za-z0-9]+)", parsed.query, re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def strip_json_header(blob):
    """Trouve la signature ZIP (PK\\x03\\x04) et conserve les 4 octets
    int32 de taille qui la precedent, en eliminant l'en-tete JSON."""
    zip_magic = "PK\x03\x04"
    zip_pos = blob.find(zip_magic)
    if zip_pos >= 4:
        return blob[zip_pos - 4:]
    return blob


# --- MINIGAMES (Save, Publish, Load, Delete, List) ---

def save_minigame(path, body, is_publish):
    """Stocke le blob binaire epure et les metadonnees de la map dans SQLite."""
    game_id = get_game_id_from_path(path)
    oid = game_id if game_id else gen_object_id()

    meta = extract_leading_json(body) or {}
    name = meta.get("name", "Untitled")
    desc = meta.get("description", "")
    creator_id = meta.get("creatorId", "offline_player")
    creator_name = meta.get("creatorName", "Player")
    tags_json = json.dumps(meta.get("tags", ["TestTag1", "TestTag2"]))

    pub_val = 1 if is_publish else 0
    pub_time = int(time.time() * 1000) if is_publish else None
    clean_binary = strip_json_header(body)

    conn = get_db()
    try:
        cur = conn.execute("SELECT id FROM minigames WHERE id = ?", (to_db_str(oid),))
        if cur.fetchone():
            conn.execute("""
                UPDATE minigames SET
                    name = ?, description = ?, creator_id = ?, creator_name = ?,
                    tags = ?, published = ?, publish_time = ?, data_blob = ?
                WHERE id = ?
            """, (to_db_str(name), to_db_str(desc), to_db_str(creator_id), to_db_str(creator_name),
                  to_db_str(tags_json), pub_val, pub_time, sqlite3.Binary(clean_binary), to_db_str(oid)))
        else:
            conn.execute("""
                INSERT INTO minigames (
                    id, name, description, creator_id, creator_name, tags,
                    published, publish_time, data_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (to_db_str(oid), to_db_str(name), to_db_str(desc), to_db_str(creator_id),
                  to_db_str(creator_name), to_db_str(tags_json), pub_val, pub_time,
                  sqlite3.Binary(clean_binary)))
        conn.commit()
    finally:
        conn.close()

    action = "PUBLISH" if is_publish else "SAVE"
    print "[%s] Stored minigame '%s' as id=%s (%d bytes binary in SQLite)" % (
        action, safe_str(name), safe_str(oid), len(clean_binary))
    return {"status": "OK", "id": oid}


def load_minigame(path):
    """Renvoie le blob brut stocke en base SQLite pour cet id."""
    wanted_id = get_game_id_from_path(path)
    if wanted_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT data_blob FROM minigames WHERE id = ?", (to_db_str(wanted_id),)).fetchone()
        finally:
            conn.close()
        if row and row["data_blob"]:
            clean_data = strip_json_header(str(row["data_blob"]))
            print "[LOAD] Sending clean level binary from SQLite (%d bytes)" % len(clean_data)
            return clean_data
    print "[LOAD] No saved minigame found in DB for id=%r" % safe_str(wanted_id)
    return None


def delete_minigame(path):
    """Supprime proprement une map et ses donnees associees dans SQLite."""
    game_id = get_game_id_from_path(path)
    if game_id:
        conn = get_db()
        try:
            conn.execute("DELETE FROM minigames WHERE id = ?", (to_db_str(game_id),))
            conn.execute("DELETE FROM comments WHERE game_id = ?", (to_db_str(game_id),))
            conn.execute("DELETE FROM highscores WHERE game_id = ?", (to_db_str(game_id),))
            conn.execute("DELETE FROM likes WHERE game_id = ?", (to_db_str(game_id),))
            conn.commit()
        finally:
            conn.close()
        print "[DELETE] Removed minigame id=%s from SQLite" % safe_str(game_id)
    return {"status": "OK", "id": game_id}


def list_minigames(path):
    """Construit la liste des metadonnees de maps depuis SQLite."""
    query = parse_qs(urlparse(path).query)
    filter_published = query.get("published", [None])[0]
    filter_creator = query.get("creatorId", [None])[0] or query.get("playerId", [None])[0]

    sql = "SELECT * FROM minigames WHERE 1=1"
    params = []

    if filter_published is not None:
        target_pub = 1 if filter_published.lower() == "true" else 0
        sql += " AND published = ?"
        params.append(target_pub)

    if filter_creator is not None:
        sql += " AND creator_id = ?"
        params.append(to_db_str(filter_creator))

    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    entries = []
    for r in rows:
        item = {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "creatorId": r["creator_id"],
            "creatorName": r["creator_name"],
            "tags": json.loads(r["tags"] or '["TestTag1", "TestTag2"]'),
            "timesPlayed": r["times_played"],
            "timesFinished": r["times_finished"],
            "timesLiked": r["times_liked"],
            "new": bool(r["is_new"]),
            "classic": bool(r["is_classic"]),
            "published": bool(r["published"]),
        }
        if r["published"] and r["publish_time"]:
            item["publishTime"] = {"$date": r["publish_time"]}
        entries.append(item)

    return {"status": "OK", "data": entries}


# --- SCREENSHOTS / THUMBNAILS ---

def save_screenshot(path, body):
    """Sauvegarde les octets de l'image / thumbnail dans la base SQLite."""
    game_id = get_game_id_from_path(path)
    if game_id:
        conn = get_db()
        try:
            cur = conn.execute("SELECT id FROM minigames WHERE id = ?", (to_db_str(game_id),))
            if cur.fetchone():
                conn.execute("UPDATE minigames SET thumbnail_blob = ? WHERE id = ?",
                             (sqlite3.Binary(body), to_db_str(game_id)))
            else:
                conn.execute("INSERT INTO minigames (id, name, thumbnail_blob) VALUES (?, 'Untitled', ?)",
                             (to_db_str(game_id), sqlite3.Binary(body)))
            conn.commit()
        finally:
            conn.close()
        print "[SCREENSHOT] Saved thumbnail for gameId=%s (%d bytes in SQLite)" % (safe_str(game_id), len(body))
    return {"status": "OK"}


def load_screenshot(path):
    """Renvoie les octets bruts du thumbnail depuis SQLite."""
    game_id = get_game_id_from_path(path)
    time.sleep(0.15)  # Pause pour laisser l'UI Unity s'initialiser

    if game_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT thumbnail_blob FROM minigames WHERE id = ?", (to_db_str(game_id),)).fetchone()
        finally:
            conn.close()
        if row and row["thumbnail_blob"]:
            return str(row["thumbnail_blob"])

    print "[SCREENSHOT] No thumbnail found for gameId=%r, sending fallback PNG" % safe_str(game_id)
    return ("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            "\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q"
            "\x00\x00\x00\x00IEND\xaeB`\x82")


# --- HIGHSCORES / LEADERBOARDS ---

def save_highscore(path):
    """Enregistre un score dans SQLite et met a jour les stats de la map."""
    query = parse_qs(urlparse(path).query)
    game_id = get_game_id_from_path(path)
    player_id = query.get("playerId", [""])[0] or "offline"
    player_name = query.get("name", ["Player"])[0]
    score_str = query.get("time", ["0"])[0]
    starts = int(query.get("starts", [1])[0] or 1)
    return_scores = query.get("returnScores", ["false"])[0].lower() == "true"

    try:
        score = int(score_str)
    except ValueError:
        score = 0

    if game_id:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT time_score, starts FROM highscores WHERE game_id = ? AND player_id = ?",
                (to_db_str(game_id), to_db_str(player_id))
            ).fetchone()
            if row:
                new_time = score if score < row["time_score"] else row["time_score"]
                new_starts = row["starts"] + starts
                conn.execute("""
                    UPDATE highscores SET time_score = ?, player_name = ?, starts = ?
                    WHERE game_id = ? AND player_id = ?
                """, (new_time, to_db_str(player_name), new_starts, to_db_str(game_id), to_db_str(player_id)))
            else:
                conn.execute("""
                    INSERT INTO highscores (game_id, player_id, player_name, time_score, starts, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (to_db_str(game_id), to_db_str(player_id), to_db_str(player_name), score, starts, int(time.time())))

            conn.execute("""
                UPDATE minigames
                SET times_finished = times_finished + 1,
                    times_played = times_played + ?
                WHERE id = ?
            """, (starts, to_db_str(game_id)))
            conn.commit()
        finally:
            conn.close()

        print "[HIGHSCORE] Saved score %d for '%s' on game '%s'" % (score, safe_str(player_name), safe_str(game_id))

    if return_scores:
        return find_highscores(path)
    return {"status": "OK", "data": []}


def find_highscores(path):
    """Recupere et renvoie le tableau des scores tries depuis SQLite."""
    game_id = get_game_id_from_path(path)
    scores = []
    if game_id:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT player_name AS n, time_score AS t
                FROM highscores
                WHERE game_id = ?
                ORDER BY time_score ASC
            """, (to_db_str(game_id),)).fetchall()
        finally:
            conn.close()
        scores = [{"n": r["n"], "t": r["t"]} for r in rows]

    return {"status": "OK", "data": scores}


def quit_highscore(path):
    """Met a jour le compteur d'essais (times_played) dans SQLite."""
    game_id = get_game_id_from_path(path)
    query = parse_qs(urlparse(path).query)
    starts = int(query.get("starts", [1])[0] or 1)
    if game_id:
        conn = get_db()
        try:
            conn.execute("UPDATE minigames SET times_played = times_played + ? WHERE id = ?", (starts, to_db_str(game_id)))
            conn.commit()
        finally:
            conn.close()
        print "[QUIT] Updated times_played (+%d) for game '%s'" % (starts, safe_str(game_id))
    return {"status": "OK"}


# --- LIKES ---

def save_like(path):
    """Enregistre un like dans SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    player_id = query.get("playerId", [""])[0] or "default_player"

    if game_id:
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO likes (player_id, game_id) VALUES (?, ?)", (to_db_str(player_id), to_db_str(game_id)))
            conn.execute("""
                UPDATE minigames
                SET times_liked = (SELECT COUNT(*) FROM likes WHERE game_id = ?)
                WHERE id = ?
            """, (to_db_str(game_id), to_db_str(game_id)))
            conn.commit()
        finally:
            conn.close()
        print "[LIKE] Player '%s' liked game '%s'" % (safe_str(player_id), safe_str(game_id))
    return {"status": "OK"}


def find_likes(path):
    """Recupere la liste des IDs de maps aimees par le joueur."""
    query = parse_qs(urlparse(path).query)
    player_id = query.get("playerId", [""])[0] or "default_player"
    conn = get_db()
    try:
        rows = conn.execute("SELECT game_id FROM likes WHERE player_id = ?", (to_db_str(player_id),)).fetchall()
    finally:
        conn.close()
    liked_ids = [r["game_id"] for r in rows]
    return {"status": "OK", "data": liked_ids}


# --- COMMENTAIRES ---

def save_comment(path):
    """Enregistre un commentaire dans SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    player_id = query.get("playerId", [""])[0]
    comment_text = query.get("comment", [""])[0]
    facebook_id = query.get("facebookId", [""])[0]
    gamecenter_id = query.get("gameCenterId", [""])[0]

    if game_id and comment_text:
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO comments (id, game_id, player_id, comment, facebook_id, gamecenter_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (to_db_str(gen_object_id()), to_db_str(game_id), to_db_str(player_id), to_db_str(comment_text),
                  to_db_str(facebook_id), to_db_str(gamecenter_id), int(time.time())))
            conn.commit()
        finally:
            conn.close()
        print "[COMMENT] Saved comment for gameId=%s: %r" % (safe_str(game_id), safe_str(comment_text))

    return {"status": "OK"}


def find_comments(path):
    """Recupere les commentaires d'une map depuis SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    limit = int(query.get("limit", [0])[0] or 0)

    comments = []
    if game_id:
        sql = "SELECT * FROM comments WHERE game_id = ? ORDER BY created_at DESC"
        params = [to_db_str(game_id)]
        if limit > 0:
            sql += " LIMIT %d" % limit
        conn = get_db()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        for r in rows:
            comments.append({
                "id": r["id"],
                "gameId": r["game_id"],
                "playerId": r["player_id"],
                "comment": r["comment"],
                "facebookId": r["facebook_id"],
                "gameCenterId": r["gamecenter_id"],
                "date": r["created_at"]
            })

    return {"status": "OK", "data": comments}


LIST_RESPONSE = {
    "status": "OK",
    "data": [],
}


def generic_response():
    return {"status": "OK", "id": gen_object_id(), "data": []}


def pick_response(path, body):
    """Retourne soit un dict JSON a serialiser, soit une chaine d'octets
    bruts a renvoyer telle quelle (pour le chargement de map / thumbnails)."""
    p = path.lower()
    if "login" in p:
        return build_login_response(body)

    if "minigame/screenshot/save" in p:
        return save_screenshot(path, body)
    if "minigame/screenshot/find" in p:
        return load_screenshot(path)

    # Dodonickey was here :)

    if "highscore/send" in p:
        return save_highscore(path)
    if "highscore/find" in p:
        return find_highscores(path)
    if "highscore/quit" in p:
        return quit_highscore(path)

    if "minigame/like/save" in p:
        return save_like(path)
    if "minigame/like/find" in p:
        return find_likes(path)

    if "minigame/comment/save" in p:
        return save_comment(path)
    if "minigame/comment/find" in p:
        return find_comments(path)

    if "minigame/delete" in p:
        return delete_minigame(path)
    if "minigame/save" in p:
        return save_minigame(path, body, False)
    if "minigame/publish" in p:
        return save_minigame(path, body, True)
    if "minigame/data/find" in p:
        raw = load_minigame(path)
        if raw is not None:
            return raw
        return {"status": "ERROR", "data": []}
    if "minigame/meta/find" in p:
        return list_minigames(path)

    if "find" in p or "list" in p:
        return LIST_RESPONSE
    return generic_response()


class FakeApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        length = int(self.headers.getheader("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else ""

        print "\n" + "=" * 70
        print "[HTTP] %s %s" % (self.command, safe_str(self.path))
        if body:
            print "-- body (%d bytes) --" % len(body)
            try:
                # Utilisation d'ensure_ascii=True par defaut pour l'affichage console afin de prevenir
                # tout crash si des caracteres etranges sont envoyes par le client.
                dumped = json.dumps(json.loads(body.decode("utf-8", "ignore")), indent=2, ensure_ascii=True)
                print safe_str(dumped)
            except Exception:
                preview = body[:200].decode("utf-8", "replace")
                if len(body) > 200:
                    preview += " ..."
                print safe_str(preview)
        print "=" * 70

        payload = pick_response(self.path, body)
        if isinstance(payload, dict):
            data = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        else:
            data = payload
            content_type = "application/octet-stream"
        print "-- response: %s, %d bytes --" % (content_type, len(data))

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def log_message(self, format, *args):
        pass


def run_http_server():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), FakeApiHandler)
    except socket.error, e:
        err_val = None
        if hasattr(e, 'errno'):
            err_val = e.errno
        elif len(e.args) > 0:
            err_val = e.args[0]

        if err_val == errno.EACCES:
            print "\n[HTTP] Error: can't open port %d. Run as root." % HTTP_PORT
        else:
            print "\n[HTTP] Error: can't open port %d (%s)." % (HTTP_PORT, safe_str(e))
            print "[HTTP] Is another copy of this script already running? Close it first."
        return
    server.serve_forever()


# ---------------------------------------------------------------------
# Partie DNS : repond pour notre hote, relaie le reste vers un vrai DNS
# ---------------------------------------------------------------------

def parse_qname(data, offset):
    labels = []
    while True:
        length = ord(data[offset])
        if length == 0:
            offset += 1
            break
        offset += 1
        labels.append(data[offset:offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), offset


def build_fake_answer(request, question_end, ip_str):
    txid = request[0:2]
    flags = struct.pack("!H", 0x8180)  # reponse standard, recursion dispo, pas d'erreur
    counts = struct.pack("!HHHH", 1, 1, 0, 0)  # QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT
    header = txid + flags + counts
    question = request[12:question_end]  # question copiee telle quelle
    answer = (
        struct.pack("!H", 0xC00C)   # pointeur vers le nom (compression DNS)
        + struct.pack("!HH", 1, 1)  # TYPE=A, CLASS=IN
        + struct.pack("!I", 60)     # TTL
        + struct.pack("!H", 4)      # RDLENGTH
        + socket.inet_aton(ip_str)  # RDATA = l'IP cible
    )
    return header + question + answer


def handle_dns_query(sock, data, addr, fake_ip):
    try:
        qname, name_end = parse_qname(data, 12)
        qtype, _qclass = struct.unpack("!HH", data[name_end:name_end + 4])
        question_end = name_end + 4

        is_target = qname.lower().rstrip(".") == TARGET_HOST
        if is_target:
            print "[DNS] Query for: %s (type=%d) <-- TARGET" % (safe_str(qname), qtype)
        else:
            print "[DNS] Query for: %s (type=%d)" % (safe_str(qname), qtype)

        if is_target and qtype == 1:  # A record
            response = build_fake_answer(data, question_end, fake_ip)
            sock.sendto(response, addr)
            print "[DNS] -> Spoofed response sent: %s" % safe_str(fake_ip)
        else:
            # Relais vers un vrai DNS pour que le reste continue de marcher
            upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            upstream.settimeout(3)
            try:
                upstream.sendto(data, UPSTREAM_DNS)
                response, _ = upstream.recvfrom(512)
                sock.sendto(response, addr)
            except socket.timeout:
                pass
            finally:
                upstream.close()
    except Exception, e:
        print "[DNS] Processing error: %s" % safe_str(e)


def run_dns_server(fake_ip):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", DNS_PORT))
    except socket.error, e:
        err_val = None
        if hasattr(e, 'errno'):
            err_val = e.errno
        elif len(e.args) > 0:
            err_val = e.args[0]

        if err_val == errno.EACCES:
            print "\n[DNS] Error: can't open port %d. Run as root." % DNS_PORT
        else:
            print "\n[DNS] Error: can't open port %d (%s)." % (DNS_PORT, safe_str(e))
            print "[DNS] Is another copy of this script already running? Close it first."
        return
    while True:
        data, addr = sock.recvfrom(512)
        t = threading.Thread(target=handle_dns_query, args=(sock, data, addr, fake_ip))
        t.setDaemon(True)
        t.start()


# ---------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    ip = LOCAL_IP
    print "Device IP set to: %s (Localhost)" % safe_str(ip)
    print "\nWaiting for game requests...\n"

    dns_thread = threading.Thread(target=run_dns_server, args=(ip,))
    dns_thread.setDaemon(True)
    dns_thread.start()
    run_http_server()