#!/usr/bin/env python3
import json
import os
import random
import socket
import struct
import threading
from urllib.parse import urlparse, parse_qs

HTTP_PORT = 80
DNS_PORT = 53
TARGET_HOST = "playdevlb-1049210432.us-west-2.elb.amazonaws.com"
UPSTREAM_DNS = ("8.8.8.8", 53)

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatonearth_saves")
os.makedirs(SAVE_DIR, exist_ok=True)

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
    try:
        parsed = json.loads(request_body.decode("utf-8"))
        if isinstance(parsed, dict) and "name" in parsed:
            submitted_name = parsed["name"]
    except Exception:
        pass

    oid = gen_object_id()
    return {
        "status": "OK",
        "id": oid,
        "name": submitted_name,
        "acceptNotifications": False,
        "facebookId": "",
        "gameCenterId": "",
    }


def split_blob(blob: bytes):
    """Sépare le blob envoyé par le jeu en (métadonnées JSON, données pures
    de la map). Le jeu colle un JSON de métadonnées directement suivi des
    données de map compressées (zip) — mais quand il RECHARGE une map,
    il attend uniquement les données compressées, pas le JSON devant.

    Un vrai serveur aurait extrait ces métadonnées dans sa base de données
    et ne renverrait que les données de la map au chargement ; on
    reproduit ce comportement ici."""
    try:
        text = blob.decode("utf-8", errors="ignore")
        obj, end_idx = json.JSONDecoder().raw_decode(text)
        if isinstance(obj, dict):
            consumed_bytes = len(text[:end_idx].encode("utf-8"))
            return obj, blob[consumed_bytes:]
    except Exception:
        pass
    return None, blob


def save_minigame(path: str, body: bytes) -> dict:
    """Sépare et stocke les métadonnées JSON et les données pures de la
    map, pour pouvoir renvoyer chacune séparément plus tard (métadonnées
    via meta/find, données pures via data/find)."""
    query = parse_qs(urlparse(path).query)
    existing_id = query.get("id", [""])[0]
    oid = existing_id if existing_id else gen_object_id()

    meta, level_bytes = split_blob(body)
    meta = meta or {}
    meta.setdefault("name", "Untitled")
    meta["id"] = oid
    meta.setdefault("creatorId", "offline")

    with open(os.path.join(SAVE_DIR, f"{oid}.bin"), "wb") as f:
        f.write(level_bytes)
    with open(os.path.join(SAVE_DIR, f"{oid}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)

    print(f"[SAVE] Stored minigame '{meta.get('name')}' as id={oid} "
          f"(metadata: {len(body) - len(level_bytes)} bytes, level data: {len(level_bytes)} bytes)")
    return {"status": "OK", "id": oid}


def load_minigame(path: str):
    """Renvoie les données de map précédemment stockées pour cet id (le jeu
    lit la réponse comme des octets bruts, pas du JSON, pour reconstruire
    la map)."""
    query = parse_qs(urlparse(path).query)
    wanted_id = query.get("id", [""])[0]
    file_path = os.path.join(SAVE_DIR, f"{wanted_id}.bin")
    if wanted_id and os.path.isfile(file_path):
        with open(file_path, "rb") as f:
            data = f.read()
        # Compatibilité avec d'anciennes sauvegardes faites avant la
        # séparation métadonnées/données : si un JSON traîne encore au
        # début, on le retire à la volée.
        if data[:1] == b"{":
            _meta, data = split_blob(data)
        return data
    print(f"[LOAD] No saved minigame found for id={wanted_id!r}")
    return None


def list_minigames() -> dict:
    """Construit la liste 'mes créations' à partir des métadonnées stockées
    localement lors des sauvegardes précédentes."""
    entries = []
    for fname in os.listdir(SAVE_DIR):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(SAVE_DIR, fname), "r", encoding="utf-8") as f:
                    entries.append(json.load(f))
            except Exception:
                pass
    return {"status": "OK", "data": entries}


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
    renvoyer tels quels (pour le chargement de map)."""
    p = path.lower()
    if "login" in p:
        return build_login_response(body)
    if "minigame/save" in p or "minigame/publish" in p:
        return save_minigame(path, body)
    if "minigame/data/find" in p:
        raw = load_minigame(path)
        if raw is not None:
            return raw
        return {"status": "ERROR", "data": []}
    if "minigame/meta/find" in p:
        return list_minigames()
    if "find" in p or "list" in p:
        return LIST_RESPONSE
    return generic_response()


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
    ip = local_ip_hint()
    print(f"PC IP: {ip}")
    print("\nWaiting for game requests...\n")

    threading.Thread(target=run_dns_server, args=(ip,), daemon=True).start()
    run_http_server()
