#!/usr/bin/env python3
"""
Faux serveur local pour WhatOnEarth (beta de Big Bang Racing, Traplight, 2014).

Le jeu contacte en dur :
    http://PlayDevLB-1049210432.us-west-2.elb.amazonaws.com/...
Ce serveur AWS n'existe plus, donc le jeu reste bloqué sur "connecting to
server...".

Ce script fait DEUX choses en parallèle :

1. Un serveur DNS (port 53) : quand l'iPad demande l'adresse de
   PlayDevLB-1049210432.us-west-2.elb.amazonaws.com, il répond avec l'IP
   de CET ordinateur. Pour toutes les autres adresses (google.com,
   apple.com, etc.), il relaie la demande vers un vrai DNS (8.8.8.8) pour
   que le reste d'internet continue de fonctionner normalement sur l'iPad.

2. Un serveur HTTP (port 80) : imite les réponses de l'API du jeu
   (login, amis, notifications, sauvegarde de maps...) et répond "OK" à
   tout, en affichant dans la console tout ce qu'il reçoit.

3. Persistance des maps : les maps sauvegardées/publiées dans l'éditeur
   sont stockées directement dans une base de données SQLite (whatonearth.db),
   et renvoyées identiques quand le jeu les recharge. Tes créations survivent
   donc d'une session à l'autre dans un fichier unique.

On utilise ici un DNS manuel dans les réglages Wi-Fi de l'iPad plutôt que
le fichier /etc/hosts, car c'est un réglage natif iOS garanti fonctionnel,
contrairement au fichier hosts qui peut être capricieux sur un système
jailbreaké/modifié.

UTILISATION
-----------
1. Lance ce script sur un ordinateur connecté au MÊME réseau Wi-Fi que
   l'iPad, AVEC les droits administrateur (obligatoire, ports 53 et 80
   privilégiés) :
       - macOS/Linux : sudo python3 whatonearth_fake_server.py
       - Windows     : ouvre le terminal "en tant qu'administrateur" puis
                        python whatonearth_fake_server.py

2. Note l'adresse IP locale de cet ordinateur (affichée au démarrage).

3. Sur l'iPad : Réglages > Wi-Fi > (i) à côté de ton réseau > DNS >
   passe de "Automatique" à "Manuel" > ajoute l'IP de ton PC comme seul
   serveur DNS (supprime les autres s'il y en a).

4. Redémarre le Wi-Fi de l'iPad (ou l'iPad entier pour être sûr).

5. Lance le jeu. Regarde la console : tu dois voir une requête DNS pour
   PlayDevLB-... suivie d'une requête HTTP POST vers /v1/player/login.

6. Pense à repasser le DNS de l'iPad sur "Automatique" une fois que tu
   as fini de jouer/tester, sinon l'iPad n'aura plus internet dès que ce
   script ne tournera plus sur ton PC.
"""

import json
import os
import random
import re
import socket
import sqlite3
import struct
import threading
import time
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT = 80
DNS_PORT = 53
TARGET_HOST = "playdevlb-1049210432.us-west-2.elb.amazonaws.com"
UPSTREAM_DNS = ("8.8.8.8", 53)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatonearth.db")

# ---------------------------------------------------------------------
# Base de données SQLite : Initialisation et Connexions
# ---------------------------------------------------------------------

def get_db():
    """Crée une connexion SQLite propre au thread avec accès par nom de colonne."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Permet des lectures/écritures simultanées rapides
    return conn


def init_db():
    """Initialise le schéma de la base de données s'il n'existe pas déjà."""
    with get_db() as conn:
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
    print("[DB] SQLite database initialized at:", DB_PATH)


# ---------------------------------------------------------------------
# Partie HTTP : imite l'API du jeu
# ---------------------------------------------------------------------

def gen_object_id() -> str:
    """Génère un identifiant de 24 caractères hexadécimaux, au format
    MongoDB ObjectId, car le backend original semble utiliser MongoDB
    (on a trouvé des indices comme "$date" dans le code du jeu)."""
    return "".join(random.choice("0123456789abcdef") for _ in range(24))


def build_login_response(request_body: bytes):
    """Construit la réponse de login attendue par le jeu.

    Trouvé en lisant le bytecode IL du jeu directement (désassemblage de
    ServerResponseOk et ParsePlayerData dans Assembly-CSharp.dll) :
    - ServerResponseOk vérifie précisément  dict["status"].Equals("OK")
      (la valeur "OK" en majuscules, PAS "success")
    - ParsePlayerData ne lit que "id" et "name" de façon obligatoire ;
      "acceptNotifications", "facebookId", "gameCenterId" sont optionnels
      (vérifiés via ContainsKey avant lecture)
    """
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

    with get_db() as conn:
        conn.execute("""
            INSERT INTO players (id, name) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name
        """, (oid, submitted_name))

    return {
        "status": "OK",
        "id": oid,
        "name": submitted_name,
        "acceptNotifications": False,
        "facebookId": "",
        "gameCenterId": "",
    }


def extract_leading_json(blob: bytes):
    """Le corps envoyé par le jeu pour sauvegarder une map est un mélange :
    des métadonnées JSON (name, id, description, tags, creatorId...) suivies
    des données de la map compressées (zip), collées ensemble.

    On n'a pas besoin de comprendre le format binaire de la map : on essaie
    juste de décoder le début du blob comme du JSON pour récupérer les
    métadonnées lisibles (utile pour la liste "mes créations"). Si ça
    échoue, on ignore simplement — le stockage/rechargement brut fonctionne
    de toute façon sans ça.
    """
    try:
        text = blob.decode("utf-8", errors="ignore")
        obj, _end = json.JSONDecoder().raw_decode(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return None


def get_game_id_from_path(path: str) -> str:
    """Extrait proprement l'ID de la map depuis l'URL en gérant les différents 
    paramètres d'ID (id, gameId) ainsi que la faute de frappe historique du code 
    source C# dans l'URL de quit (SendQuitData) qui génère '?gameId<id>' sans le signe '='.

    Note : parse_qs() de Python supprime silencieusement tout segment de
    query string sans signe '=' (vérifié empiriquement) — donc pour le cas
    du typo, on ne peut pas compter sur query.keys(), on doit chercher
    directement dans la query brute avec une regex."""
    parsed = urlparse(path)
    query = parse_qs(parsed.query)

    for key in ["gameId", "id"]:
        if key in query and query[key][0]:
            return query[key][0]

    match = re.search(r"gameid=?([A-Za-z0-9]+)", parsed.query, re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def strip_json_header(blob: bytes) -> bytes:
    """Trouve la signature ZIP (PK\x03\x04) et conserve les 4 octets int32 
    de taille qui la précèdent, en éliminant l'en-tête JSON."""
    zip_magic = b"PK\x03\x04"
    zip_pos = blob.find(zip_magic)
    if zip_pos >= 4:
        return blob[zip_pos - 4 :]
    return blob


# --- MINIGAMES (Save, Publish, Load, Delete, List) ---

def save_minigame(path: str, body: bytes, is_publish: bool) -> dict:
    """Stocke le blob binaire épuré et les métadonnées de la map dans SQLite."""
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

    with get_db() as conn:
        conn.execute("""
            INSERT INTO minigames (
                id, name, description, creator_id, creator_name, tags, 
                published, publish_time, data_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                creator_id=excluded.creator_id,
                creator_name=excluded.creator_name,
                tags=excluded.tags,
                published=excluded.published,
                publish_time=excluded.publish_time,
                data_blob=excluded.data_blob
        """, (oid, name, desc, creator_id, creator_name, tags_json, pub_val, pub_time, clean_binary))

    action = "PUBLISH" if is_publish else "SAVE"
    print(f"[{action}] Stored minigame '{name}' as id={oid} ({len(clean_binary)} bytes binary in SQLite)")
    return {"status": "OK", "id": oid}


def load_minigame(path: str):
    """Renvoie le blob brut stocké en base SQLite pour cet id."""
    wanted_id = get_game_id_from_path(path)
    if wanted_id:
        with get_db() as conn:
            row = conn.execute("SELECT data_blob FROM minigames WHERE id = ?", (wanted_id,)).fetchone()
            if row and row["data_blob"]:
                clean_data = strip_json_header(row["data_blob"])
                print(f"[LOAD] Sending clean level binary from SQLite ({len(clean_data)} bytes)")
                return clean_data
    print(f"[LOAD] No saved minigame found in DB for id={wanted_id!r}")
    return None


def delete_minigame(path: str) -> dict:
    """Supprime proprement une map et ses données associées dans SQLite."""
    game_id = get_game_id_from_path(path)
    if game_id:
        with get_db() as conn:
            conn.execute("DELETE FROM minigames WHERE id = ?", (game_id,))
            conn.execute("DELETE FROM comments WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM highscores WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM likes WHERE game_id = ?", (game_id,))
        print(f"[DELETE] Removed minigame id={game_id} from SQLite")
    return {"status": "OK", "id": game_id}


def list_minigames(path: str) -> dict:
    """Construit la liste des métadonnées de maps depuis SQLite."""
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
        params.append(filter_creator)

    entries = []
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
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

def save_screenshot(path: str, body: bytes) -> dict:
    """Sauvegarde les octets de l'image / thumbnail dans la base SQLite."""
    game_id = get_game_id_from_path(path)
    if game_id:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO minigames (id, name, thumbnail_blob) VALUES (?, 'Untitled', ?)
                ON CONFLICT(id) DO UPDATE SET thumbnail_blob=excluded.thumbnail_blob
            """, (game_id, body))
        print(f"[SCREENSHOT] Saved thumbnail for gameId={game_id} ({len(body)} bytes in SQLite)")
    return {"status": "OK"}


def load_screenshot(path: str):
    """Renvoie les octets bruts du thumbnail depuis SQLite."""
    game_id = get_game_id_from_path(path)
    time.sleep(0.15)  # Pause pour laisser l'UI Unity s'initialiser

    if game_id:
        with get_db() as conn:
            row = conn.execute("SELECT thumbnail_blob FROM minigames WHERE id = ?", (game_id,)).fetchone()
            if row and row["thumbnail_blob"]:
                return row["thumbnail_blob"]

    print(f"[SCREENSHOT] No thumbnail found for gameId={game_id!r}, sending fallback PNG")
    return b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'


# --- HIGHSCORES / LEADERBOARDS ---

def save_highscore(path: str) -> dict:
    """Enregistre un score dans SQLite et met à jour les stats de la map."""
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
        with get_db() as conn:
            # Enregistrement ou mise à jour du meilleur temps
            conn.execute("""
                INSERT INTO highscores (game_id, player_id, player_name, time_score, starts, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_id) DO UPDATE SET
                    time_score = CASE WHEN excluded.time_score < highscores.time_score THEN excluded.time_score ELSE highscores.time_score END,
                    player_name = excluded.player_name,
                    starts = highscores.starts + excluded.starts
            """, (game_id, player_id, player_name, score, starts, int(time.time())))

            # Incrémentation des statistiques de la map
            conn.execute("""
                UPDATE minigames 
                SET times_finished = times_finished + 1,
                    times_played = times_played + ?
                WHERE id = ?
            """, (starts, game_id))

        print(f"[HIGHSCORE] Saved score {score} for '{player_name}' on game '{game_id}'")

    if return_scores:
        return find_highscores(path)
    return {"status": "OK", "data": []}


def find_highscores(path: str) -> dict:
    """Récupère et renvoie le tableau des scores trié depuis SQLite."""
    game_id = get_game_id_from_path(path)
    scores = []
    if game_id:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT player_name AS n, time_score AS t 
                FROM highscores 
                WHERE game_id = ? 
                ORDER BY time_score ASC
            """, (game_id,)).fetchall()
            scores = [{"n": r["n"], "t": r["t"]} for r in rows]

    return {"status": "OK", "data": scores}


def quit_highscore(path: str) -> dict:
    """Met à jour le compteur d'essais (times_played) dans SQLite."""
    game_id = get_game_id_from_path(path)
    query = parse_qs(urlparse(path).query)
    starts = int(query.get("starts", [1])[0] or 1)
    if game_id:
        with get_db() as conn:
            conn.execute("""
                UPDATE minigames SET times_played = times_played + ? WHERE id = ?
            """, (starts, game_id))
        print(f"[QUIT] Updated times_played (+{starts}) for game '{game_id}'")
    return {"status": "OK"}


# --- LIKES ---

def save_like(path: str) -> dict:
    """Enregistre un like dans SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    player_id = query.get("playerId", [""])[0] or "default_player"

    if game_id:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO likes (player_id, game_id) VALUES (?, ?)", (player_id, game_id))
            conn.execute("""
                UPDATE minigames 
                SET times_liked = (SELECT COUNT(*) FROM likes WHERE game_id = ?) 
                WHERE id = ?
            """, (game_id, game_id))
        print(f"[LIKE] Player '{player_id}' liked game '{game_id}'")
    return {"status": "OK"}


def find_likes(path: str) -> dict:
    """Récupère la liste des IDs de maps aimées par le joueur."""
    query = parse_qs(urlparse(path).query)
    player_id = query.get("playerId", [""])[0] or "default_player"
    liked_ids = []
    with get_db() as conn:
        rows = conn.execute("SELECT game_id FROM likes WHERE player_id = ?", (player_id,)).fetchall()
        liked_ids = [r["game_id"] for r in rows]
    return {"status": "OK", "data": liked_ids}


# --- COMMENTAIRES ---

def save_comment(path: str) -> dict:
    """Enregistre un commentaire dans SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    player_id = query.get("playerId", [""])[0]
    comment_text = query.get("comment", [""])[0]
    facebook_id = query.get("facebookId", [""])[0]
    gamecenter_id = query.get("gameCenterId", [""])[0]

    if game_id and comment_text:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO comments (id, game_id, player_id, comment, facebook_id, gamecenter_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (gen_object_id(), game_id, player_id, comment_text, facebook_id, gamecenter_id, int(time.time())))
        print(f"[COMMENT] Saved comment for gameId={game_id}: {comment_text!r}")

    return {"status": "OK"}


def find_comments(path: str) -> dict:
    """Récupère les commentaires d'une map depuis SQLite."""
    query = parse_qs(urlparse(path).query)
    game_id = query.get("gameId", [""])[0]
    limit = int(query.get("limit", [0])[0] or 0)

    comments = []
    if game_id:
        sql = "SELECT * FROM comments WHERE game_id = ? ORDER BY created_at DESC"
        params = [game_id]
        if limit > 0:
            sql += f" LIMIT {limit}"
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
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

# Réponse par défaut pour tout le reste (merge, like...). On inclut
# systématiquement "id" et "data" en plus de "status": "OK", car plusieurs
# handlers du jeu (ex: ParseFriendDict pour /player/friend/save,
# LevelSendToServerOk pour /minigame/save) lisent directement dict["data"]
# ou dict["id"] sans vérifier si la clé existe — leur absence fait planter
# l'app.
def generic_response() -> dict:
    return {"status": "OK", "id": gen_object_id(), "data": []}


def pick_response(path: str, body: bytes):
    """Retourne soit un dict JSON à sérialiser, soit des bytes bruts à
    renvoyer tels quels (pour le chargement de map / thumbnails)."""
    p = path.lower()
    if "login" in p:
        return build_login_response(body)

    # Screenshots / Thumbnails
    if "minigame/screenshot/save" in p:
        return save_screenshot(path, body)
    if "minigame/screenshot/find" in p:
        return load_screenshot(path)
    
    #Dodonickey was here :)
    
    # Highscores / Leaderboards
    if "highscore/send" in p:
        return save_highscore(path)
    if "highscore/find" in p:
        return find_highscores(path)
    if "highscore/quit" in p:
        return quit_highscore(path)

    # Likes
    if "minigame/like/save" in p:
        return save_like(path)
    if "minigame/like/find" in p:
        return find_likes(path)

    # Commentaires
    if "minigame/comment/save" in p:
        return save_comment(path)
    if "minigame/comment/find" in p:
        return find_comments(path)

    # Maps CRUD
    if "minigame/delete" in p:
        return delete_minigame(path)
    if "minigame/save" in p:
        return save_minigame(path, body, is_publish=False)
    if "minigame/publish" in p:
        return save_minigame(path, body, is_publish=True)
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
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        print("\n" + "=" * 70)
        print(f"[HTTP] {self.command} {self.path}")
        if body:
            print(f"-- body ({len(body)} bytes) --")
            try:
                print(json.dumps(json.loads(body.decode("utf-8")), indent=2, ensure_ascii=False))
            except Exception:
                print(body[:200].decode("utf-8", errors="replace") + (" ..." if len(body) > 200 else ""))
        print("=" * 70)

        payload = pick_response(self.path, body)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        content_type = "application/octet-stream" if isinstance(payload, bytes) else "application/json"
        print(f"-- response: {content_type}, {len(data)} bytes --")

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
        server.serve_forever()
    except PermissionError:
        print(f"\n[HTTP] Error: can't open port {HTTP_PORT}. Run as administrator.")
    except OSError as e:
        print(f"\n[HTTP] Error: can't open port {HTTP_PORT} ({e}).")
        print("[HTTP] Is another copy of this script already running? Close it first.")


# ---------------------------------------------------------------------
# Partie DNS : répond pour notre hôte, relaie le reste vers un vrai DNS
# ---------------------------------------------------------------------

def parse_qname(data: bytes, offset: int):
    labels = []
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        offset += 1
        labels.append(data[offset:offset + length].decode("ascii", errors="replace"))
        offset += length
    return ".".join(labels), offset


def build_fake_answer(request: bytes, question_end: int, ip_str: str) -> bytes:
    txid = request[0:2]
    flags = struct.pack("!H", 0x8180)  # réponse standard, récursion dispo, pas d'erreur
    counts = struct.pack("!HHHH", 1, 1, 0, 0)  # QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT
    header = txid + flags + counts
    question = request[12:question_end]  # question copiée telle quelle
    answer = (
        struct.pack("!H", 0xC00C)   # pointeur vers le nom (compression DNS)
        + struct.pack("!HH", 1, 1)  # TYPE=A, CLASS=IN
        + struct.pack("!I", 60)     # TTL
        + struct.pack("!H", 4)      # RDLENGTH
        + socket.inet_aton(ip_str)  # RDATA = l'IP du PC
    )
    return header + question + answer


def handle_dns_query(sock, data, addr, fake_ip):
    try:
        qname, name_end = parse_qname(data, 12)
        qtype, _qclass = struct.unpack("!HH", data[name_end:name_end + 4])
        question_end = name_end + 4

        is_target = qname.lower().rstrip(".") == TARGET_HOST
        print(f"[DNS] Query for: {qname} (type={qtype})" + (" <-- TARGET" if is_target else ""))

        if is_target and qtype == 1:  # A record
            response = build_fake_answer(data, question_end, fake_ip)
            sock.sendto(response, addr)
            print(f"[DNS] -> Spoofed response sent: {fake_ip}")
        else:
            # Forward to a real DNS server so the rest of the internet keeps working
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
    except Exception as e:
        print(f"[DNS] Processing error: {e}")


def run_dns_server(fake_ip):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", DNS_PORT))
    except PermissionError:
        print(f"\n[DNS] Error: can't open port {DNS_PORT}. Run as administrator.")
        return
    except OSError as e:
        print(f"\n[DNS] Error: can't open port {DNS_PORT} ({e}).")
        print("[DNS] Is another copy of this script already running? Close it first.")
        return
    while True:
        data, addr = sock.recvfrom(512)
        threading.Thread(target=handle_dns_query, args=(sock, data, addr, fake_ip), daemon=True).start()


# ---------------------------------------------------------------------

def local_ip_hint():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


if __name__ == "__main__":
    init_db()
    ip = local_ip_hint()
    print(f"PC IP: {ip}")
    print("\nWaiting for game requests...\n")

    threading.Thread(target=run_dns_server, args=(ip,), daemon=True).start()
    run_http_server()