"""FastAPI + HTTPS + UDP discovery + HTTP API"""
import asyncio, io, json, os, re, socket, threading, time, wave
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn
import config


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"


def _ensure_cert(base):
    d = os.path.join(base, "certs"); os.makedirs(d, exist_ok=True)
    crt, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    if os.path.exists(crt) and os.path.exists(key):
        return crt, key
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime, ipaddress
        print("[i] Создаю SSL сертификат...")
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "elena.local")])
        sans = [x509.DNSName("localhost"), x509.DNSName("elena.local")]
        for ip in ["127.0.0.1", get_local_ip()]:
            try: sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except Exception: pass
        now = datetime.datetime.utcnow()
        cert = (x509.CertificateBuilder()
                .subject_name(subj).issuer_name(subj)
                .public_key(k.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .sign(k, hashes.SHA256()))
        with open(key, "wb") as f:
            f.write(k.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PEM,
                encryption_algorithm=serialization.NoEncryption()))
        with open(crt, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        print("[✓] SSL готов")
        return crt, key
    except Exception as e:
        print(f"[!] cert: {e}")
        return None, None


def _ensure_icons(client_dir):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    for size in (192, 512):
        path = os.path.join(client_dir, f"icon-{size}.png")
        if os.path.exists(path):
            continue
        try:
            img = Image.new("RGB", (size, size), (168, 85, 247))
            d = ImageDraw.Draw(img)
            for y in range(size):
                t = y / size
                d.line([(0, y), (size, y)], fill=(
                    int(168*(1-t)+236*t), int(85*(1-t)+72*t), int(247*(1-t)+153*t)))
            cx, cy = size // 2, size // 2; f = int(size * 0.5)
            d.ellipse([cx - f // 2, cy - f // 3, cx + f // 4, cy + f // 2], fill="white")
            d.rectangle([cx + f // 4 - f // 10, cy - f // 2, cx + f // 4, cy + f // 6],
                        fill="white")
            img.save(path)
            print(f"[✓] {path}")
        except Exception as e:
            print(f"[!] icon {size}: {e}")


class Beacon:
    MAGIC = "ELENA_V1"

    def __init__(self, port, service_port, https):
        self.port, self.sport, self.https = port, service_port, https
        self._run = False

    def start(self):
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", self.port)); s.settimeout(1.0)
            last = 0
            while self._run:
                try:
                    data, addr = s.recvfrom(1024)
                    if data.startswith(b"ELENA_WHO"):
                        r = f"{self.MAGIC}|{get_local_ip()}|{self.sport}|{'1' if self.https else '0'}"
                        s.sendto(r.encode(), addr)
                except socket.timeout:
                    pass
                except Exception:
                    pass
                now = time.time()
                if now - last > 3:
                    try:
                        m = f"{self.MAGIC}|{get_local_ip()}|{self.sport}|{'1' if self.https else '0'}"
                        s.sendto(m.encode(), ("255.255.255.255", self.port))
                    except Exception:
                        pass
                    last = now
        except Exception as e:
            print(f"[!] Beacon: {e}")


class Server:
    def __init__(self, music, ai, voice, root_dir, on_event=None):
        self.music = music
        self.ai = ai
        self.voice = voice
        self.root_dir = root_dir
        self.client_dir = os.path.join(root_dir, "client")
        os.makedirs(self.client_dir, exist_ok=True)
        _ensure_icons(self.client_dir)

        self.https = config.SERVER_HTTPS
        self.http_port = config.SERVER_PORT
        self.https_port = config.SERVER_HTTPS_PORT
        self.discovery_port = config.DISCOVERY_PORT

        self._vosk_model = None
        self._vosk_lock = threading.Lock()

        self.cert, self.key = (None, None)
        if self.https:
            self.cert, self.key = _ensure_cert(root_dir)
            if not self.cert:
                self.https = False

        self.app = FastAPI(title="Elena Server")
        self.app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"])
        try:
            self.app.add_middleware(GZipMiddleware, minimum_size=1024)
        except Exception:
            pass

        self.beacon = Beacon(self.discovery_port,
                             self.https_port if self.https else self.http_port,
                             self.https)
        self._register()

    def _register(self):
        app = self.app
        cdir = self.client_dir

        def _file(name, media):
            async def h():
                p = os.path.join(cdir, name)
                if os.path.exists(p):
                    return FileResponse(p, media_type=media)
                return Response(status_code=404)
            return h

        @app.get("/", response_class=HTMLResponse)
        async def index():
            p = os.path.join(cdir, "index.html")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return HTMLResponse(f.read())
            return HTMLResponse("<h1>client/index.html not found</h1>", status_code=404)

        @app.get("/tv", response_class=HTMLResponse)
        async def tv_client():
            p = os.path.join(cdir, "tv.html")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return HTMLResponse(f.read())
            return HTMLResponse("<h1>client/tv.html not found</h1>", status_code=404)

        app.get("/style.css")(_file("style.css", "text/css"))
        app.get("/extra.css")(_file("extra.css", "text/css"))
        app.get("/app.js")(_file("app.js", "application/javascript"))
        app.get("/tv.css")(_file("tv.css", "text/css"))
        app.get("/tv.js")(_file("tv.js", "application/javascript"))
        app.get("/manifest.json")(_file("manifest.json", "application/json"))
        app.get("/icon-192.png")(_file("icon-192.png", "image/png"))
        app.get("/icon-512.png")(_file("icon-512.png", "image/png"))

        @app.get("/api/health")
        async def health():
            return {"ok": True, "ts": time.time()}

        @app.get("/api/info")
        async def info():
            return {
                "name": config.ASSISTANT_NAME,
                "ip": get_local_ip(),
                "http_port": self.http_port,
                "https_port": self.https_port if self.https else None,
                "https": self.https,
                "has_yandex": self.music.client is not None,
            }

        @app.get("/api/state")
        async def state():
            try:
                s = self.music.get_state()
                s["is_speaking"] = bool(self.voice and self.voice.is_speaking)
                return s
            except Exception as e:
                print(f"[!] state: {e}")
                return {"error": str(e)}

        # ==================== STREAM (progressive + Range) ====================
        @app.get("/api/stream")
        async def stream(request: Request):
            path = self.music.current_file
            if not path or not os.path.exists(path):
                return Response(status_code=404)
            ext = Path(path).suffix.lstrip(".").lower()
            mime = {"mp3": "audio/mpeg", "flac": "audio/flac",
                    "m4a": "audio/mp4", "aac": "audio/aac"}.get(ext, "audio/mpeg")

            rng = request.headers.get("range")
            if not rng:
                return FileResponse(path, media_type=mime)

            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m:
                return FileResponse(path, media_type=mime)
            start = int(m.group(1)) if m.group(1) else 0
            end_req = int(m.group(2)) if m.group(2) else None

            total_hint = 0
            try:
                total_hint = int(self.music.download_info.get("total") or 0)
            except Exception:
                pass

            deadline = time.time() + 25
            while time.time() < deadline:
                try:
                    size = os.path.getsize(path)
                except Exception:
                    size = 0
                complete = not self.music.download_info.get("active")
                if end_req is not None:
                    if size > end_req or (complete and size >= start):
                        break
                else:
                    # FIX: было 1_500_000 — теперь 384 КБ, чтобы <audio> стартовал мгновенно
                    if size >= 384_000 or (complete and size > 0):
                        break
                await asyncio.sleep(0.05)   # FIX: было 0.12

            try:
                size = os.path.getsize(path)
            except Exception:
                return Response(status_code=404)
            if start >= size:
                return Response(status_code=416)

            end = end_req if (end_req is not None and end_req < size) else size - 1
            total = size if not self.music.download_info.get("active") \
                else max(size, total_hint, size)

            def gen():
                remaining = end - start + 1
                with open(path, "rb") as f:
                    f.seek(start)
                    while remaining > 0:
                        chunk = f.read(min(131072, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            headers = {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            }
            return StreamingResponse(gen(), status_code=206, media_type=mime,
                                     headers=headers)

        # ==================== DEVICES ====================
        @app.get("/api/devices")
        async def devices():
            try:
                inputs = self.voice.list_input_devices() if self.voice else []
            except Exception:
                inputs = []
            try:
                outputs = self.voice.list_output_devices() if self.voice else []
            except Exception:
                outputs = []
            return {
                "input": inputs,
                "output": outputs,
                "current_input": getattr(config, "SELECTED_MIC_INDEX", None),
                "current_output": getattr(config, "OUTPUT_DEVICE_INDEX", None),
            }

        @app.post("/api/set_device")
        async def set_device(req: Request):
            body = await req.json()
            kind = body.get("kind")
            idx = body.get("index")
            if not self.voice:
                return {"ok": False, "error": "voice unavailable"}
            if kind == "input":
                msg = await asyncio.to_thread(self.voice.set_input_device, idx)
                return {"ok": True, "message": msg}
            if kind == "output":
                msg = await asyncio.to_thread(self.voice.set_output_device, idx)
                return {"ok": True, "message": msg}
            return {"ok": False, "error": "bad kind"}

        # ==================== PLAYLISTS ====================
        @app.get("/api/playlists")
        async def playlists():
            return await asyncio.to_thread(self.music.get_playlists_short)

        @app.get("/api/playlist_tracks")
        async def playlist_tracks(kind: str = Query("")):
            if not kind:
                return {"tracks": []}
            tracks = await asyncio.to_thread(self.music.get_playlist_tracks, kind)
            return {"tracks": tracks}

        @app.post("/api/playlist_play")
        async def playlist_play(req: Request):
            body = await req.json()
            r = await asyncio.to_thread(
                self.music.play_playlist_track,
                body.get("kind"), int(body.get("index", 0)))
            return {"ok": True, "play": r}

        @app.post("/api/playlists/create")
        async def playlists_create(req: Request):
            body = await req.json()
            title = (body.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "empty title"}
            r = await asyncio.to_thread(self.music.create_playlist, title)
            if r:
                return {"ok": True, "playlist": r}
            return {"ok": False, "error": "create failed"}

        @app.post("/api/playlists/add_track")
        async def playlists_add_track(req: Request):
            body = await req.json()
            ok = await asyncio.to_thread(
                self.music.add_track_to_playlist,
                body.get("kind"), body.get("track_id"), body.get("album_id"))
            return {"ok": ok}

        @app.post("/api/playlists/rename")
        async def playlists_rename(req: Request):
            body = await req.json()
            ok = await asyncio.to_thread(
                self.music.rename_playlist, body.get("kind"),
                (body.get("title") or "").strip())
            return {"ok": ok}

        @app.post("/api/play_by_id")
        async def play_by_id(req: Request):
            body = await req.json()
            r = await asyncio.to_thread(
                self.music.play_playlist_by_id,
                body.get("kind"), body.get("uid"))
            return {"ok": True, "play": r} if r else {"ok": False}

        # ==================== CATALOG ====================
        @app.get("/api/landing")
        async def landing():
            return await asyncio.to_thread(self.music.get_for_you)

        # FIX: полный лендинг (плейлист дня, премьера, дежавю и т.д.)
        @app.get("/api/landing_full")
        async def landing_full():
            return await asyncio.to_thread(self.music.get_landing_playlists)

        # FIX: история прослушиваний
        @app.get("/api/history")
        async def history():
            return {"tracks": await asyncio.to_thread(self.music.get_history)}

        @app.get("/api/chart")
        async def chart():
            return await asyncio.to_thread(self.music.get_chart)

        @app.get("/api/new_releases")
        async def new_releases():
            return await asyncio.to_thread(self.music.get_new_releases)

        @app.get("/api/stations")
        async def stations():
            return await asyncio.to_thread(self.music.get_stations)

        # ==================== SEARCH ====================
        @app.get("/api/search")
        async def search(q: str = Query("")):
            if not q.strip():
                return {"tracks": []}
            tracks = await asyncio.to_thread(self.music.search_tracks, q)
            return {"tracks": tracks}

        @app.get("/api/suggest")
        async def suggest(q: str = Query("")):
            sug = await asyncio.to_thread(self.music.search_suggest, q)
            return {"suggestions": sug}

        @app.post("/api/play_track")
        async def play_track(req: Request):
            body = await req.json()
            tid = body.get("id")
            if not tid:
                return {"ok": False, "error": "no id"}
            r = await asyncio.to_thread(self.music.play_search_track, tid)
            return {"ok": True, "play": r}

        # ==================== LIKES ====================
        @app.get("/api/liked")
        async def liked():
            tracks = await asyncio.to_thread(self.music.get_liked_tracks)
            return {"tracks": tracks}

        @app.post("/api/liked/play")
        async def liked_play(req: Request):
            body = await req.json()
            idx = int(body.get("index", 0))
            r = await asyncio.to_thread(self.music.play_liked_at, idx)
            return {"ok": True, "play": r}

        @app.post("/api/like")
        async def like(req: Request):
            body = await req.json()
            tid = body.get("track_id")
            if not tid:
                cur = self.music.current_track
                tid = getattr(cur, "id", None) if cur else None
            if not tid:
                return {"ok": False, "error": "no track"}
            like = bool(body.get("like", True))
            ok = await asyncio.to_thread(self.music.like_track, tid, like)
            return {"ok": ok, "liked": like}

        # ==================== QUEUE ====================
        @app.get("/api/queue")
        async def queue_list():
            return await asyncio.to_thread(self.music.get_queue)

        @app.post("/api/queue/play")
        async def queue_play(req: Request):
            body = await req.json()
            r = await asyncio.to_thread(
                self.music.play_queue_index, int(body.get("index", 0)))
            return {"ok": True, "play": r} if r else {"ok": False}

        @app.post("/api/queue/remove")
        async def queue_remove(req: Request):
            body = await req.json()
            ok = await asyncio.to_thread(
                self.music.remove_queue_index, int(body.get("index", -1)))
            return {"ok": ok}

        # ==================== LYRICS / SIMILAR ====================
        @app.get("/api/lyrics")
        async def lyrics(track_id: str = Query("")):
            if not track_id:
                cur = self.music.current_track
                track_id = str(getattr(cur, "id", "")) if cur else ""
            r = await asyncio.to_thread(self.music.get_lyrics, track_id)
            return {"lyrics": r}

        @app.get("/api/similar")
        async def similar(track_id: str = Query("")):
            if not track_id:
                cur = self.music.current_track
                track_id = str(getattr(cur, "id", "")) if cur else ""
            tracks = await asyncio.to_thread(self.music.get_similar, track_id)
            return {"tracks": tracks}

        # ==================== WAVE ====================
        @app.post("/api/wave/settings")
        async def wave_settings(req: Request):
            body = await req.json()
            w = await asyncio.to_thread(
                self.music.set_wave_settings,
                body.get("mood"), body.get("diversity"), body.get("language"))
            return {"ok": True, "wave": w}

        # FIX: авто-настройки волны (утро/день/вечер/ночь)
        @app.get("/api/wave/auto")
        async def wave_auto():
            return self.music.wave_auto_settings()

        @app.post("/api/wave/feedback")
        async def wave_feedback(req: Request):
            body = await req.json()
            kind = body.get("kind", "more")
            if kind == "more":
                r = await asyncio.to_thread(self.music.wave_more_like)
            elif kind == "block":
                r = await asyncio.to_thread(self.music.wave_dislike_and_block)
            else:
                r = await asyncio.to_thread(self.music.wave_less_like)
            return {"ok": True, "play": r}

        # ==================== ARTIST ====================
        @app.get("/api/artist")
        async def artist(id: str = Query(""), name: str = Query("")):
            info = await asyncio.to_thread(
                self.music.get_artist_info, (id or name or "").strip())
            return {"artist": info}

        @app.post("/api/play_artist_top")
        async def play_artist_top(req: Request):
            body = await req.json()
            r = await asyncio.to_thread(self.music.play_artist_by_id, body.get("id"))
            return {"ok": True, "play": r}

        @app.get("/api/album_tracks")
        async def album_tracks(id: str = Query("")):
            tracks = await asyncio.to_thread(self.music.get_album_tracks, id)
            return {"tracks": tracks}

        @app.post("/api/play_album_track")
        async def play_album_track(req: Request):
            body = await req.json()
            r = await asyncio.to_thread(
                self.music.play_album_track,
                body.get("id"), int(body.get("index", 0)))
            return {"ok": True, "play": r}

        # ==================== ACCOUNTS ====================
        @app.get("/api/accounts")
        async def accounts():
            return self.music.get_accounts()

        @app.post("/api/accounts/add")
        async def accounts_add(req: Request):
            body = await req.json()
            token = (body.get("token") or "").strip()
            if not token:
                return {"ok": False, "error": "empty"}
            r = await asyncio.to_thread(self.music.add_account_from_token, token)
            return r

        @app.post("/api/accounts/switch")
        async def accounts_switch(req: Request):
            body = await req.json()
            ok = await asyncio.to_thread(self.music.switch_account, body.get("uid"))
            return {"ok": ok}

        @app.post("/api/accounts/remove")
        async def accounts_remove(req: Request):
            body = await req.json()
            ok = await asyncio.to_thread(self.music.remove_account, body.get("uid"))
            return {"ok": ok}

        # ==================== PROXY IMAGE ====================
        @app.get("/api/proxy_image")
        async def proxy_image(url: str = Query("")):
            if not url:
                return Response(status_code=404)

            import urllib.request
            import urllib.error

            u = url.strip()
            if not u.startswith("http"):
                u = "https://" + u.lstrip("/")

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://music.yandex.ru/",
            }

            try:
                req = urllib.request.Request(u, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
                    ct = r.headers.get("Content-Type", "image/jpeg")
                    if not ct.startswith("image/"):
                        print(f"[!] proxy_image: got {ct} for {u[:80]}")
                        return Response(status_code=404)
                return Response(
                    content=data, media_type=ct,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            except urllib.error.HTTPError as e:
                print(f"[!] proxy_image HTTP {e.code}: {u[:100]}")
                return Response(status_code=404)
            except Exception as e:
                print(f"[!] proxy_image: {type(e).__name__}: {e} | {u[:100]}")
                return Response(status_code=404)

        @app.post("/api/cmd")
        async def cmd(req: Request):
            try:
                body = await req.json()
            except Exception:
                return {"ok": False, "error": "bad json"}
            action = body.get("action")
            params = body.get("params", {})
            reply, payload = await asyncio.to_thread(self._exec, action, params)
            resp = {"ok": True}
            if reply:
                resp["reply"] = reply
            if payload:
                resp.update(payload)
            try:
                resp["state"] = self.music.get_state()
            except Exception:
                pass
            return resp

        @app.post("/api/voice")
        async def voice(audio: UploadFile = File(...), quiet: int = 0):
            try:
                data = await audio.read()
                text = await asyncio.to_thread(self._stt, data)
                if not text:
                    return {"ok": False, "silent": True}
                clean = self.ai.extract_wake_word(text)
                if clean is None:
                    if quiet:
                        return {"ok": False, "silent": True}
                    clean = text
                else:
                    self.music.activate()
                if clean == "просто_имя":
                    self.voice.speak("Слушаю")
                    return {"ok": True, "text": text, "reply": "Слушаю"}
                print(f"[🎤 client]: {text}")
                reply = await asyncio.to_thread(self._process_voice_text, clean)
                return {"ok": True, "text": text, "reply": reply}
            except Exception as e:
                print(f"[!] voice: {e}")
                return {"ok": False, "error": str(e)}

        @app.post("/api/voice_text")
        async def voice_text(req: Request):
            try:
                body = await req.json()
                text = (body.get("text") or "").strip().lower()
                if not text:
                    return {"ok": False}
                clean = self.ai.extract_wake_word(text)
                if clean is None:
                    clean = text
                ct = self.music.get_track_metadata()
                ctid = ct.get("id") if ct else None
                action, params, reply = await asyncio.to_thread(
                    self.ai.process, clean, current_track_id=ctid,
                    current_track=ct)
                self.music.activate()
                if action != "none":
                    try:
                        self.voice.speak(reply)
                    except Exception:
                        pass
                payload = await asyncio.to_thread(self._exec, action, params)
                resp = {"ok": True, "text": text, "reply": reply}
                if payload and payload[1]:
                    resp.update(payload[1])
                return resp
            except Exception as e:
                print(f"[!] voice_text: {e}")
                return {"ok": False, "error": str(e)}

        @app.get("/api/scan")
        async def scan():
            return await asyncio.to_thread(self._scan_local)

    def _scan_local(self):
        ip = get_local_ip()
        if ip == "127.0.0.1":
            return []
        prefix = ".".join(ip.split(".")[:3]) + "."
        port = self.https_port if self.https else self.http_port
        found = []
        import concurrent.futures, urllib.request, ssl
        ctx = ssl.create_default_context(); ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def try_host(i):
            host = f"{prefix}{i}"
            if host == ip:
                return None
            try:
                scheme = "https" if self.https else "http"
                url = f"{scheme}://{host}:{port}/api/info"
                req = urllib.request.Request(url, headers={"User-Agent": "Elena"})
                with urllib.request.urlopen(req, timeout=0.25,
                                            context=ctx if self.https else None) as r:
                    data = json.loads(r.read())
                    return {"ip": host, "port": port, "https": self.https,
                            "name": data.get("name", "Elena")}
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
            for r in ex.map(try_host, range(1, 255)):
                if r:
                    found.append(r)
        return found

    def _exec(self, action, params):
        m = self.music
        try:
            if action == "toggle":
                m.toggle_play(); return None, None
            if action == "play":
                if not m.is_playing:
                    m.toggle_play()
                return None, None
            if action == "pause":
                if m.is_playing:
                    m.toggle_play()
                return None, None
            if action == "next":
                reason = params.get("reason", "manual")
                src = params.get("track_id") if reason == "ended" else None
                r = m.next_track(src_track_id=src, reason=reason)
                return None, {"play": r} if r else None
            if action == "previous":
                r = m.previous_track(); return None, {"play": r} if r else None
            if action == "seek":
                m.position = max(0.0, float(params.get("seconds", 0)))
                m._play_started_at = time.time() if m.is_playing else None
                return None, {"seek_pos": m.position}
            if action == "seek_time":
                m.position = max(0.0, float(params.get("seconds", 0)))
                m._play_started_at = time.time() if m.is_playing else None
                return None, {"seek_pos": m.position}
            if action == "seek_relative":
                m.position = max(0.0, m._current_position() +
                                 float(params.get("delta", 0)))
                m._play_started_at = time.time() if m.is_playing else None
                return None, {"seek_pos": m.position}
            if action == "volume_set":
                m.set_volume(params.get("level", 70)); return None, None
            if action == "volume_up":
                m.set_volume(m.volume + 10); return None, None
            if action == "volume_down":
                m.set_volume(m.volume - 10); return None, None
            if action == "mute":
                m.set_volume(0); return None, None
            if action == "unmute":
                m.set_volume(m.volume if m.volume > 0 else 50); return None, None
            if action == "set_eq":
                config.EQ_PRESET = params.get("preset", config.EQ_PRESET)
                if "enabled" in params:
                    config.EQ_ENABLED = params["enabled"]
                config.save_state(); return None, None
            if action == "set_quality":
                m.audio_quality = params.get("quality", "lossless")
                config.AUDIO_QUALITY = m.audio_quality
                config.save_state()
                return f"Качество: {m.audio_quality}", None
            if action == "my_wave":
                r = m.play_my_wave(seeds=params.get("seeds"))
                return None, {"play": r} if r else {"reply": "Не удалось"}
            if action == "my_wave_by_track":
                r = m.play_my_wave_by_track(params.get("track_id"))
                return None, {"play": r} if r else {"reply": "Не удалось"}
            if action == "wave_settings":
                w = m.set_wave_settings(mood=params.get("mood"),
                                        diversity=params.get("diversity"),
                                        language=params.get("language"))
                return None, {"wave": w}
            if action == "wave_more":
                r = m.wave_more_like(); return None, {"play": r} if r else None
            if action == "wave_less":
                r = m.wave_less_like(); return None, {"play": r} if r else None
            if action == "wave_block":
                r = m.wave_dislike_and_block()
                return None, {"play": r} if r else None
            if action == "play_liked":
                r = m.play_liked_tracks()
                return None, {"play": r} if r else {"reply": "Нет любимых"}
            if action == "play_random":
                r = m.play_random()
                return None, {"play": r} if r else {"reply": "Не нашла"}
            if action == "shuffle":
                ok = m.shuffle_queue()
                return ("Перемешала" if ok else "Мало треков"), None
            if action == "repeat":
                v = m.set_repeat(params.get("enabled", True))
                return ("Повторяю трек" if v else "Повтор выключен"), None
            if action == "like":
                cur = m.current_track
                tid = params.get("track_id") or (getattr(cur, "id", None) if cur else None)
                ok = m.like_track(tid, like=True)
                return None if ok else "Не получилось", None
            if action == "dislike_next":
                cur = m.current_track
                if cur:
                    m.like_track(getattr(cur, "id"), like=False)
                r = m.next_track(reason="manual")
                return None, {"play": r} if r else None
            if action == "similar":
                cur = m.current_track
                r = m.play_similar(getattr(cur, "id", None)) if cur else None
                return None, {"play": r} if r else {"reply": "Не нашла похожих"}
            if action == "play_landing":
                r = m._play_for_you_key(0)
                return None, {"play": r} if r else {"reply": "Не нашла"}
            if action == "play_premiere":
                r = m._play_for_you_key(1)
                return None, {"play": r} if r else {"reply": "Не нашла"}
            if action == "play_dejavu":
                r = m._play_for_you_key(2)
                return None, {"play": r} if r else {"reply": "Не нашла"}
            if action == "play_chart":
                r = m._play_chart_tracks()
                return None, {"play": r} if r else {"reply": "Чарт недоступен"}
            if action == "play_new_releases":
                r = m._play_new_releases()
                return None, {"play": r} if r else {"reply": "Новинки недоступны"}
            if action == "search_and_play":
                r = m.search_and_play(params.get("query", ""))
                return None, {"play": r} if r else {"reply": "Не найдено"}
            if action == "play_artist":
                r = m.play_artist(params.get("name", ""))
                return None, {"play": r} if r else {"reply": "Не найдено"}
            if action == "queue_track":
                r = m.queue_next_track(params.get("query", ""))
                return ("Добавила" if r else "Не найдено"), None
            if action == "play_playlist_track":
                r = m.play_playlist_track(params.get("kind"),
                                          int(params.get("index", 0)))
                return None, {"play": r} if r else None
            if action == "play_album_track":
                r = m.play_album_track(params.get("id"),
                                       int(params.get("index", 0)))
                return None, {"play": r} if r else None
            if action == "play_artist_top":
                r = m.play_artist_by_id(params.get("id"))
                return None, {"play": r} if r else None
            if action == "change_name":
                nn = params.get("name", "").strip()
                if nn:
                    config.ASSISTANT_NAME = nn
                    nw = nn.lower()
                    if nw not in config.WAKE_WORDS:
                        config.WAKE_WORDS.insert(0, nw)
                    self.ai.wake_words = config.WAKE_WORDS
                    config.save_state()
                    return f"Теперь меня зовут {nn}", None
                return None, None
        except Exception as e:
            print(f"[!] exec {action}: {e}")
            return f"❌ {e}", None
        return None, None

    # ==================== STT (Google → Vosk fallback) ====================
    def _stt(self, wav_bytes):
        import speech_recognition as sr
        r = sr.Recognizer()
        try:
            with sr.AudioFile(io.BytesIO(wav_bytes)) as src:
                audio = r.record(src)
            return r.recognize_google(audio, language=config.LANGUAGE).lower().strip()
        except Exception:
            pass
        return self._stt_vosk(wav_bytes)

    def _vosk_model(self):
        with self._vosk_lock:
            if self._vosk_model is not None:
                return self._vosk_model
            try:
                from vosk import Model
                d = getattr(config, "VOSK_MODEL_DIR_BIG", None) or \
                    getattr(config, "VOSK_MODEL_DIR", None)
                if d and os.path.isdir(d):
                    self._vosk_model = Model(d)
                    print("[✓] Vosk STT готов")
            except Exception as e:
                print(f"[!] vosk stt: {e}")
            return self._vosk_model

    def _stt_vosk(self, wav_bytes):
        model = self._vosk_model()
        if model is None:
            return None
        try:
            from vosk import KaldiRecognizer
            with wave.open(io.BytesIO(wav_bytes), "rb") as w:
                rate = w.getframerate()
                frames = w.readframes(w.getnframes())
            try:
                import audioop
                if rate != 16000:
                    frames, _ = audioop.ratecv(frames, 2, 1, rate, 16000, None)
                    rate = 16000
            except Exception:
                pass
            rec = KaldiRecognizer(model, rate)
            rec.SetWords(False)
            CH = 8000
            for i in range(0, len(frames), CH):
                rec.AcceptWaveform(frames[i:i + CH])
            return json.loads(rec.FinalResult()).get("text", "").lower().strip() or None
        except Exception as e:
            print(f"[!] stt vosk: {e}")
            return None

    def _process_voice_text(self, text):
        clean = self.ai.extract_wake_word(text) or text
        ct = self.music.get_track_metadata()
        ctid = ct.get("id") if ct else None
        action, params, reply = self.ai.process(
            clean, current_track_id=ctid, current_track=ct)
        print(f"[👩] {reply}")
        if action != "none":
            self.voice.speak(reply)
        self._exec(action, params)
        return reply

    def start(self):
        threading.Thread(target=self._run_http, daemon=True).start()
        if self.https:
            threading.Thread(target=self._run_https, daemon=True).start()
        self.beacon.start()

    def _run_http(self):
        cfg = uvicorn.Config(self.app, host=config.SERVER_HOST,
                             port=self.http_port, log_level="warning", access_log=False)
        uvicorn.Server(cfg).run()

    def _run_https(self):
        cfg = uvicorn.Config(
            self.app, host=config.SERVER_HOST, port=self.https_port,
            log_level="warning", access_log=False,
            ssl_certfile=self.cert, ssl_keyfile=self.key)
        uvicorn.Server(cfg).run()

    def wait_ready(self, timeout=8.0):
        import urllib.request
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.http_port}/api/health", timeout=0.3) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(0.2)
        return False