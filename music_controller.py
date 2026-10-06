"""
Менеджер Яндекс Музыки.
Качает файлы (параллельно по Range), управляет очередью и серверным тактом,
стримит клиентам. Полный функционал «Моей волны» с фидбеком ротора.
"""
import os
import time
import random
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from yandex_music import Client
import config
from accounts import AccountManager


_WAVE_FALLBACK = {
    "mood:happy":      "веселая музыка",
    "mood:sad":        "грустная музыка",
    "mood:calm":       "спокойная музыка",
    "mood:romantic":   "романтичная музыка",
    "mood:energetic":  "энергичная музыка",
    "mood:partying":   "вечеринка музыка",
    "genre:allrock":   "рок",
    "genre:pop":       "поп",
    "genre:hiphop":    "хип-хоп",
    "genre:metal":     "метал",
    "genre:jazz":      "джаз",
    "genre:classical": "классическая музыка",
    "genre:electro":   "электронная музыка",
    "genre:indie":     "инди",
    "activity:workout":"музыка для тренировки",
    "activity:sleep":  "музыка для сна",
    "activity:relax":  "музыка для отдыха",
    "activity:work":   "музыка для работы",
}


# ==================== SAFE HELPERS ====================
def _safe_artists(t):
    try:
        lst = getattr(t, "artists", None) or []
        if not isinstance(lst, (list, tuple)):
            return ""
        names = []
        for a in lst:
            n = getattr(a, "name", None)
            if n:
                names.append(str(n))
        return ", ".join(names)
    except Exception:
        return ""


def _first_artist_id(t):
    try:
        lst = getattr(t, "artists", None) or []
        for a in lst:
            aid = getattr(a, "id", None)
            if aid:
                return str(aid)
    except Exception:
        pass
    return None


def _first_album_id(t):
    try:
        lst = getattr(t, "albums", None) or []
        for a in lst:
            aid = getattr(a, "id", None)
            if aid:
                return str(aid)
    except Exception:
        pass
    return None


def _extract_cover(obj, size="400x400"):
    def _norm(u):
        if not u:
            return None
        s = str(u)
        if not s.startswith("http"):
            s = "https://" + s.lstrip("/")
        if "%%" in s:
            s = s.replace("%%", size)
        return s

    for attr in ("cover_uri", "og_image"):
        try:
            uri = getattr(obj, attr, None)
            if callable(uri):
                try: uri = uri()
                except Exception: uri = None
            s = _norm(uri)
            if s: return s
        except Exception:
            pass
    try:
        cov = getattr(obj, "cover", None)
        if cov is not None:
            u = getattr(cov, "uri", None)
            if callable(u):
                try: u = u()
                except Exception: u = None
            s = _norm(u)
            if s: return s
    except Exception:
        pass
    return None


def _safe_cover(t, size="200x200"):
    return _extract_cover(t, size)


def _safe_duration(t):
    try:
        return (getattr(t, "duration_ms", 0) or 0) / 1000.0
    except Exception:
        return 0.0


def _tid(t):
    try:
        return str(getattr(t, "id", "") or "")
    except Exception:
        return ""


# ==================== CONTROLLER ====================
class MusicController:
    QUALITY_LABEL = {
        "lossless": "Lossless",
        "high": "MP3 320",
        "balanced": "AAC 192",
        "efficient": "AAC 64",
    }

    def __init__(self):
        self.client = None
        self.volume = 70
        self.queue = []
        self.current_index = 0
        self.current_track = None
        self.current_file = None

        self.is_playing = False
        self.position = 0.0
        self._play_started_at = None
        self.repeat_one = False

        self._last_advance_ts = 0.0
        self._last_advance_from = None

        self._track_cache = {}
        self._artist_cache = {}
        self._play_lock = threading.RLock()
        self._search_cache = {}
        self._liked_cache = []
        self._liked_ids = set()
        self._liked_ids_loaded = False
        self._pl_raw_cache = {}
        self._pl_tracks_cache = {}
        self._album_cache = {}
        self._artist_info_cache = {}
        self._prefetching = set()
        # FIX: кэш информации о кодеке для каждого трека
        self._codec_cache = {}

        self.audio_quality = config.AUDIO_QUALITY
        self.actual_quality = None
        self.activation_ts = 0.0

        self.download_info = {"active": False, "total": 0, "done": 0}

        # FIX: увеличен пул соединений для параллельных Range-загрузок
        self._http = requests.Session()
        try:
            ad = HTTPAdapter(
                pool_connections=32, pool_maxsize=32, max_retries=3,
                pool_block=False,
            )
            self._http.mount("https://", ad)
            self._http.mount("http://", ad)
        except Exception:
            pass

        self.wave = None
        self._wave_lock = threading.Lock()

        self.accounts = AccountManager()
        self._on_state_change = None

        base = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = os.path.join(base, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self._init_yandex()

        threading.Thread(target=self._clock_loop, daemon=True).start()
        threading.Thread(target=self._wave_refill_loop, daemon=True).start()

    def set_state_callback(self, cb):
        self._on_state_change = cb

    def _notify(self):
        if self._on_state_change:
            try:
                self._on_state_change()
            except Exception as e:
                print(f"[!] cb: {e}")

    def activate(self):
        self.activation_ts = time.time()
        print("[⚡] Активация")
        self._notify()

    def _init_yandex(self):
        token = self.accounts.get_active_token() or config.YANDEX_MUSIC_TOKEN
        try:
            if not token or "ВАШ" in token:
                print("[i] Нет токена — войдите через UI")
                self.client = None
                return
            self.client = Client(token).init()
            me = self.client.me
            login = getattr(me.account, "login", "user")
            display = getattr(me.account, "display_name", login)
            avatar = None
            try:
                if getattr(me.account, "cover", None):
                    cover = me.account.cover
                    if hasattr(cover, "uri") and cover.uri:
                        avatar = "https://" + cover.uri.replace("%%", "200x200")
            except Exception:
                pass
            print(f"[✓] Яндекс Музыка ({display})")
            uid = str(me.account.uid)
            if uid not in self.accounts.accounts:
                self.accounts.add(token, {
                    "uid": me.account.uid,
                    "login": login,
                    "display_name": display,
                    "avatar_url": avatar,
                })
        except Exception as e:
            print(f"[!] Yandex: {e}")
            self.client = None

    # ==================== BATCH FETCH ====================
    def _fetch_full_tracks(self, ids):
        """Параллельно тянет полные треки пачками по 100. Использует кэш."""
        out = {}
        if not self.client or not ids:
            return out

        miss = []
        for i in ids:
            try:
                n = int(i)
            except Exception:
                continue
            cached = self._track_cache.get(n)
            if cached is not None:
                out[str(n)] = cached
            else:
                miss.append(n)

        if not miss:
            return out

        BATCH = 100
        chunks = [miss[i:i + BATCH] for i in range(0, len(miss), BATCH)]

        def fetch(chunk):
            try:
                return list(self.client.tracks(chunk) or [])
            except Exception as e:
                print(f"[!] batch: {e}")
                return []

        # FIX: 4 воркера вместо 6 — меньше 429, но всё ещё параллельно
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(fetch, chunks):
                for t in result:
                    if t is not None:
                        tid = str(t.id)
                        out[tid] = t
                        try:
                            self._track_cache[int(t.id)] = t
                        except Exception:
                            pass
        return out

    def _enrich(self, shorts, limit=None):
        if not shorts:
            return []
        if limit:
            shorts = shorts[:limit]
        ids = []
        for s in shorts:
            tid = getattr(s, "id", None)
            if tid:
                ids.append(str(tid))
        full_map = self._fetch_full_tracks(ids)

        out = []
        for s in shorts:
            tid = str(getattr(s, "id", None) or "")
            if not tid:
                continue
            t = full_map.get(tid) or s
            out.append(self._track_dict(t))
            if t is not s:
                try:
                    self._track_cache[int(tid)] = t
                except Exception:
                    pass
        return out

    def _track_dict(self, t):
        return {
            "id": _tid(t),
            "title": getattr(t, "title", None) or "—",
            "artists": _safe_artists(t) or "—",
            "artist_id": _first_artist_id(t),
            "album_id": _first_album_id(t),
            "duration": _safe_duration(t),
            "cover_url": _safe_cover(t),
        }

    # ==================== META / STATE ====================
    def get_track_metadata(self):
        t = self.current_track
        if not t:
            return None
        artists = _safe_artists(t) or "—"
        cover = _safe_cover(t, "600x600")
        album = ""
        try:
            if getattr(t, "albums", None):
                album = t.albums[0].title or ""
        except Exception:
            pass
        return {
            "id": getattr(t, "id", None),
            "title": getattr(t, "title", None) or "—",
            "artists": artists,
            "artist_id": _first_artist_id(t),
            "album": album,
            "cover_url": cover,
            "duration": _safe_duration(t),
            "quality": self.QUALITY_LABEL.get(
                self.actual_quality or self.audio_quality, "?"),
        }

    def _track_duration(self):
        return _safe_duration(self.current_track) if self.current_track else 0.0

    def _current_position(self):
        if not self.is_playing or self._play_started_at is None:
            return self.position
        d = self._track_duration()
        p = self.position + (time.time() - self._play_started_at)
        return min(p, d) if d > 0 else p

    def get_state(self):
        return {
            "track": self.get_track_metadata(),
            "queue_len": len(self.queue),
            "index": self.current_index,
            "is_playing": self.is_playing,
            "position": round(self._current_position(), 2),
            "volume": self.volume,
            "has_stream": bool(self.current_file and os.path.exists(self.current_file)),
            "assistant_name": config.ASSISTANT_NAME,
            "activation_ts": self.activation_ts,
            "eq_preset": config.EQ_PRESET,
            "eq_enabled": config.EQ_ENABLED,
            "repeat": self.repeat_one,
            "is_liked": self.is_liked(_tid(self.current_track)),
            "wave": self.wave,
            "download": dict(self.download_info),
        }

    # ==================== CLOCK ====================
    def _clock_loop(self):
        while True:
            time.sleep(1)
            try:
                if self.is_playing and self.current_track:
                    d = self._track_duration()
                    if d > 0 and self._current_position() >= d + 1.5:
                        t = self.current_track
                        self._wave_feedback("trackFinished", t)
                        self._play_audio_feedback(t, finished=True)
                        if self.repeat_one:
                            with self._play_lock:
                                self.position = 0.0
                                self._play_started_at = time.time()
                            continue
                        print(f"[⏭ auto] конец трека: {getattr(t, 'title', '?')}")
                        self.next_track(reason="auto")
            except Exception as e:
                print(f"[!] clock: {e}")

    # ==================== DOWNLOAD (parallel Range) ====================
    def _get_codecs(self, track):
        """FIX: кэшируем результат — не дёргаем API повторно."""
        tid = _tid(track)
        if tid in self._codec_cache:
            return self._codec_cache[tid]
        try:
            info = track.get_download_info()
        except Exception:
            return []
        out = []
        for i in info:
            c = getattr(i, "codec", None)
            b = getattr(i, "bitrate_in_kbps", None)
            if c:
                out.append((c, b, i))
        out.sort(key=lambda x: (
            {"flac": 0, "mp3": 1, "aac": 2, "he-aac": 3}.get(x[0], 99),
            -(x[1] or 0)
        ))
        self._codec_cache[tid] = out
        return out

    def _pick_codec(self, track, prefer=None):
        av = self._get_codecs(track)
        if not av:
            return None, None
        items = [(c, b) for c, b, _ in av]
        if prefer:
            want = {
                "lossless": ("flac", None),
                "high": ("mp3", 320),
                "balanced": ("aac", 192),
                "efficient": ("aac", 64),
            }.get(prefer)
            if want:
                wc, wb = want
                for c, b in items:
                    if c == wc and (wb is None or b == wb):
                        return (c, b)
                for c, b in items:
                    if c == wc:
                        return (c, b)
        return items[0]

    def _codec_info(self, track, codec, bitrate):
        for c, b, info in self._get_codecs(track):
            if c == codec and (bitrate is None or b == bitrate):
                return info
        return None

    def _cached_file(self, tid):
        if not tid:
            return None
        for ext in ("flac", "mp3", "m4a", "aac"):
            p = os.path.join(self.cache_dir, f"{tid}.{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 1024:
                return p
        return None

    def _download(self, track):
        tid = getattr(track, "id", None)
        cached = self._cached_file(tid)
        if cached:
            ext = cached.rsplit(".", 1)[-1]
            self.actual_quality = {
                "flac": "lossless", "mp3": "high",
                "m4a": "balanced", "aac": "efficient",
            }.get(ext, "efficient")
            return cached

        codec, bitrate = self._pick_codec(track, self.audio_quality)
        if not codec:
            raise RuntimeError("Нет кодека")
        ext = {"flac": "flac", "mp3": "mp3",
               "aac": "m4a", "he-aac": "m4a"}.get(codec, "mp3")
        path = os.path.join(self.cache_dir, f"{tid}.{ext}")

        url = None
        try:
            info = self._codec_info(track, codec, bitrate)
            if info is None:
                info = self._codec_info(track, codec, None)
            if info is not None:
                url = getattr(info, "direct_link", None)
        except Exception as e:
            print(f"[!] dl info: {e}")

        t0 = time.time()
        ok = False
        if url:
            ok = self._parallel_download(url, path)
        if not ok:
            try:
                kwargs = {"codec": codec}
                if bitrate is not None:
                    kwargs["bitrate_in_kbps"] = bitrate
                track.download(path, **kwargs)
            except Exception as e:
                print(f"[!] download fallback: {e}")
                raise

        size = 0
        try:
            size = os.path.getsize(path)
        except Exception:
            pass
        self.download_info = {"active": False, "total": size, "done": size}
        print(f"[⇩] {getattr(track, 'title', tid)}: "
              f"{size // 1024 // 1024} МБ за {time.time() - t0:.1f} сек")

        self.actual_quality = {
            "flac": "lossless", "mp3": "high", "m4a": "balanced"
        }.get(ext, "efficient")

        try:
            files = sorted(
                [os.path.join(self.cache_dir, f)
                 for f in os.listdir(self.cache_dir)],
                key=lambda p: os.path.getmtime(p), reverse=True)
            for old in files[30:]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass
        return path

    def _parallel_download(self, url, path, workers=None):
        workers = workers or getattr(config, "DOWNLOAD_WORKERS", 16)
        try:
            r0 = self._http.get(url, headers={"Range": "bytes=0-0"},
                                timeout=15, stream=True)
            total = 0
            accepts = (r0.status_code == 206)
            cr = r0.headers.get("Content-Range", "")
            if accepts and "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[-1])
                except Exception:
                    total = 0
            elif r0.status_code == 200:
                try:
                    total = int(r0.headers.get("Content-Length", 0))
                except Exception:
                    total = 0
            r0.close()

            if not total:
                return False
            if not accepts or total < 3_000_000:
                r = self._http.get(url, timeout=90)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)
                return True

            self.download_info = {"active": True, "total": total, "done": 0}
            with open(path, "wb") as f:
                f.truncate(total)

            chunk = max(1_000_000, total // (workers * 6))
            ranges = []
            pos = 0
            while pos < total:
                end = min(pos + chunk, total) - 1
                ranges.append((pos, end))
                pos = end + 1

            done_lock = threading.Lock()
            done_bytes = [0]

            def dl(ab):
                a, b = ab
                rr = self._http.get(url, headers={"Range": f"bytes={a}-{b}"},
                                    timeout=90)
                rr.raise_for_status()
                data = rr.content
                with done_lock:
                    with open(path, "r+b") as f:
                        f.seek(a)
                        f.write(data)
                    done_bytes[0] += len(data)
                    self.download_info["done"] = done_bytes[0]

            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(dl, ranges))

            return os.path.getsize(path) == total
        except Exception as e:
            print(f"[!] par dl: {type(e).__name__}: {e}")
            return False

    # ==================== PREFETCH ====================
    def _prefetch_next(self):
        """FIX: префетчим PREFETCH_AHEAD треков вперёд, а не только 1."""
        if not self.queue:
            return
        ahead = getattr(config, "PREFETCH_AHEAD", 3)
        for delta in range(1, ahead + 1):
            idx = self.current_index + delta
            if idx >= len(self.queue):
                break
            nt = self.queue[idx]
            tid = getattr(nt, "id", None)
            if not tid or tid in self._prefetching:
                continue
            if self._cached_file(tid):
                continue
            if not hasattr(nt, "get_download_info"):
                continue
            self._prefetching.add(tid)
            threading.Thread(target=self._prefetch_worker,
                             args=(nt,), daemon=True).start()

    def _prefetch_worker(self, track):
        try:
            self._download(track)
            print(f"[⇩] prefetched: {getattr(track, 'title', '?')}")
        except Exception as e:
            print(f"[!] prefetch: {e}")
        finally:
            try:
                self._prefetching.discard(getattr(track, "id", None))
            except Exception:
                pass

    # ==================== PLAYBACK ====================
    def _start_track(self, track):
        if not hasattr(track, "get_download_info"):
            tid = getattr(track, "id", None)
            full = self._track_cache.get(tid) if tid else None
            if not full and tid:
                try:
                    full = self.client.tracks([int(tid)])
                    full = full[0] if full else None
                    if full:
                        self._track_cache[int(tid)] = full
                except Exception:
                    full = None
            if full:
                track = full
            else:
                print("[!] не могу получить полный трек")
                return None
        try:
            path = self._download(track)
        except Exception as e:
            print(f"[!] download: {e}")
            return None
        self.current_track = track
        self.current_file = path
        self.position = 0.0
        self._play_started_at = time.time()
        self.is_playing = True
        self._notify()

        self._wave_feedback("trackStarted", track)
        self._play_audio_feedback(track)

        self._prefetch_next()

        return {
            "type": "play",
            "stream_url": f"/api/stream?t={int(time.time())}",
            "position": 0.0,
            "track": self.get_track_metadata(),
        }

    def _play_current(self):
        if not self.queue or self.current_index >= len(self.queue):
            return None
        item = self.queue[self.current_index]
        return self._start_track(item)

    def next_track(self, src_track_id=None, reason="manual"):
        with self._play_lock:
            if not self.queue:
                return None
            cur = self.current_track
            cur_id = _tid(cur)
            now = time.time()

            # FIX: анти-дребезг (3 сек)
            if (now - self._last_advance_ts < 3.0
                    and self._last_advance_from == cur_id):
                return None

            # FIX: для авто-перехода ждём реального окончания трека
            #      (защита от «проскоков» при обрыве стрима)
            if reason == "auto" and cur is not None:
                d = _safe_duration(cur)
                if d > 0 and self._current_position() < d - 0.3:
                    return None

            if cur and src_track_id and str(src_track_id) != cur_id:
                return None
            self._last_advance_ts = now
            self._last_advance_from = cur_id

            if cur and reason in ("manual", "ended"):
                self._wave_feedback("skip", cur)

            if self.current_index + 1 < len(self.queue):
                self.current_index += 1
                return self._play_current()
            if cur:
                self._append_similar(cur)
                if self.current_index + 1 < len(self.queue):
                    self.current_index += 1
                    return self._play_current()
            return None

    def previous_track(self):
        with self._play_lock:
            if not self.queue:
                return None
            if self.current_index > 0:
                self.current_index -= 1
                return self._play_current()
            return None

    def toggle_play(self):
        if self.is_playing:
            self.position = self._current_position()
            self._play_started_at = None
            self.is_playing = False
        else:
            self._play_started_at = time.time()
            self.is_playing = True
        self._notify()
        return {"type": "toggle", "playing": self.is_playing}

    def set_repeat(self, enabled):
        self.repeat_one = bool(enabled)
        return self.repeat_one

    def shuffle_queue(self):
        with self._play_lock:
            if len(self.queue) - (self.current_index + 1) < 2:
                return False
            rest = self.queue[self.current_index + 1:]
            random.shuffle(rest)
            self.queue = self.queue[:self.current_index + 1] + rest
            return True

    def set_volume(self, v):
        self.volume = max(0, min(100, int(v)))
        self._notify()
        return {"type": "volume", "value": self.volume}

    # ==================== QUEUE ====================
    def get_queue(self, limit=100):
        out = []
        start = self.current_index
        for i, t in enumerate(self.queue[start:start + limit]):
            d = self._track_dict(t)
            d["index"] = start + i
            d["current"] = (i == 0)
            out.append(d)
        return out

    def play_queue_index(self, idx):
        with self._play_lock:
            if 0 <= idx < len(self.queue):
                self.current_index = idx
                return self._play_current()
        return None

    def remove_queue_index(self, idx):
        with self._play_lock:
            if 0 <= idx < len(self.queue) and idx != self.current_index:
                del self.queue[idx]
                if idx < self.current_index:
                    self.current_index -= 1
                return True
        return False

    # ==================== LIKES ====================
    def _ensure_liked_ids(self):
        if self._liked_ids_loaded or not self.client:
            return
        try:
            likes = self.client.users_likes_tracks()
            shorts = self._to_shorts(likes)
            self._liked_ids = {str(getattr(t, "id", "")) for t in shorts
                               if getattr(t, "id", None)}
            self._liked_ids_loaded = True
        except Exception as e:
            print(f"[!] liked ids: {e}")

    def is_liked(self, tid):
        if not tid:
            return False
        self._ensure_liked_ids()
        return str(tid) in self._liked_ids

    def like_track(self, tid, like=True):
        if not self.client or not tid:
            return False
        try:
            tid_i = int(tid)
            if like:
                self.client.users_likes_tracks_add([tid_i])
            else:
                self.client.users_likes_tracks_remove([tid_i])
            if like:
                self._liked_ids.add(str(tid))
            else:
                self._liked_ids.discard(str(tid))
            if self.current_track and _tid(self.current_track) == str(tid):
                self._wave_feedback("like" if like else "unlike",
                                    self.current_track)
            return True
        except Exception as e:
            print(f"[!] like: {e}")
            return False

    # ==================== YANDEX API ====================
    def _to_shorts(self, c):
        if c is None:
            return []
        if hasattr(c, "tracks") and c.tracks is not None:
            return list(c.tracks)
        if isinstance(c, (list, tuple)):
            return list(c)
        try:
            return list(c)
        except Exception:
            return []

    def _station_tracks(self, seed):
        attempts = [
            lambda: self.client.rotor_station_tracks(seed, settings2=True),
            lambda: self.client.rotor_station_tracks(seed, settings2=False),
        ]
        last_err = None
        for fn in attempts:
            try:
                st = fn()
                tracks = [s.track for s in (st.sequence or []) if s.track]
                if tracks:
                    return tracks
            except Exception as e:
                last_err = e
        if last_err:
            print(f"[!] station {seed}: {last_err}")
        return []

    def _wave_feedback(self, ftype, track=None):
        w = self.wave
        if not w or not self.client:
            return
        try:
            kwargs = {"station_id": w["station"], "type": ftype,
                      "timestamp": int(time.time() * 1000)}
            if track is not None:
                kwargs["track_id"] = _tid(track)
                aid = _first_album_id(track)
                if aid:
                    kwargs["album_id"] = aid
            try:
                self.client.rotor_station_feedback(**kwargs)
            except TypeError:
                kwargs.pop("timestamp", None)
                self.client.rotor_station_feedback(**kwargs)
        except Exception as e:
            print(f"[!] wave fb {ftype}: {e}")

    def _play_audio_feedback(self, track, finished=False):
        if not self.client or track is None:
            return
        try:
            kwargs = {
                "track_id": getattr(track, "id"),
                "from_": "elena-app",
                "album_id": _first_album_id(track),
            }
            if finished:
                kwargs["to_"] = "elena-end"
                try:
                    kwargs["total_played_seconds"] = int(_safe_duration(track))
                except Exception:
                    pass
            try:
                self.client.play_audio(**kwargs)
            except TypeError:
                self.client.play_audio(
                    track_id=kwargs["track_id"],
                    from_="elena-app",
                    album_id=kwargs.get("album_id"),
                )
        except Exception as e:
            print(f"[!] play_audio: {e}")

    def set_wave_settings(self, mood=None, diversity=None, language=None):
        w = self.wave
        if not w:
            w = {"station": "user:onmywave", "settings": {}}
        st = dict(w.get("settings") or {})
        if mood:
            st["mood"] = mood
        if diversity:
            st["diversity"] = diversity
        if language:
            st["language"] = language
        with self._wave_lock:
            self.wave = {"station": w["station"], "settings": st}
        if self.client:
            try:
                self.client.rotor_station_settings2(w["station"], st)
                print(f"[✓] wave settings: {st}")
            except Exception as e:
                print(f"[!] wave settings: {e}")
        return self.wave

    def wave_more_like(self):
        self._wave_feedback("like", self.current_track)
        return self.next_track(reason="manual")

    def wave_less_like(self):
        self._wave_feedback("dislike", self.current_track)
        if self.current_track:
            self.like_track(_tid(self.current_track), like=False)
        return self.next_track(reason="manual")

    # FIX: полный фидбек ротора «не нравится и больше не включать»
    def wave_dislike_and_block(self):
        cur = self.current_track
        if cur and self.client:
            try:
                sid = self.wave["station"] if self.wave else "user:onmywave"
                try:
                    self.client.rotor_station_feedback(
                        station_id=sid, type="dislike",
                        track_id=_tid(cur),
                        timestamp=int(time.time() * 1000))
                except TypeError:
                    self.client.rotor_station_feedback(
                        station_id=sid, type="dislike", track_id=_tid(cur))
            except Exception as e:
                print(f"[!] wave block: {e}")
        return self.next_track(reason="manual")

    def _wave_refill_loop(self):
        ahead = getattr(config, "WAVE_REFILL_AHEAD", 5)
        batch = getattr(config, "WAVE_REFILL_BATCH", 20)
        while True:
            time.sleep(5)
            try:
                w = self.wave
                if not w or not self.client:
                    continue
                remaining = len(self.queue) - (self.current_index + 1)
                if remaining > ahead:
                    continue
                tracks = self._station_tracks(w["station"])
                if not tracks:
                    continue
                existing = {_tid(t) for t in self.queue}
                new = [t for t in tracks
                       if _tid(t) and _tid(t) not in existing][:batch]
                if new:
                    with self._play_lock:
                        self.queue.extend(new)
                    print(f"[🌊] волна: +{len(new)} треков "
                          f"(всего {len(self.queue)})")
            except Exception as e:
                print(f"[!] wave refill: {e}")

    def play_my_wave(self, seeds=None, settings=None):
        if not self.client:
            return None
        seeds = seeds or ["user:onmywave"]
        seed = seeds[0]
        tracks = self._station_tracks(seed)
        rotor = bool(tracks)

        if not tracks:
            fb = _WAVE_FALLBACK.get(seed)
            if not fb and ":" in seed:
                fb = seed.split(":", 1)[1]
            if fb:
                print(f"[i] wave fallback: поиск '{fb}'")
                try:
                    sr = self.client.search(fb)
                    if sr.tracks and sr.tracks.results:
                        tracks = list(sr.tracks.results)
                except Exception as e:
                    print(f"[!] fallback search: {e}")

        if not tracks:
            return None
        with self._wave_lock:
            self.wave = {"station": seed, "settings": settings or {}} if rotor \
                else None
        if rotor:
            self._wave_feedback("radioStarted")
        tracks = tracks[:config.MY_WAVE_MAX_TRACKS]
        with self._play_lock:
            self.queue = tracks
            self.current_index = 0
            return self._play_current()

    def play_my_wave_by_track(self, tid):
        if not self.client:
            return None
        tracks = self._station_tracks(f"track:{tid}")
        if not tracks:
            return None
        with self._wave_lock:
            self.wave = {"station": f"track:{tid}", "settings": {}}
        self._wave_feedback("radioStarted")
        with self._play_lock:
            self.queue = tracks
            self.current_index = 0
            return self._play_current()

    def stop_wave(self):
        with self._wave_lock:
            self.wave = None

    def play_artist(self, name):
        if not self.client:
            return None
        try:
            sr = self.client.search(name)
            if not sr.artists or not sr.artists.results:
                return self.search_and_play(name)
            artist = sr.artists.results[0]
            tracks = self._get_artist_tracks(artist)
            if not tracks:
                return None
            with self._play_lock:
                self.stop_wave()
                self.queue = tracks
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] artist: {e}")
            return None

    def play_artist_by_id(self, artist_id):
        if not self.client or not artist_id:
            return None
        try:
            artists = self.client.artists([int(artist_id)])
            artist = artists[0] if artists else None
            if not artist:
                return None
            tracks = self._get_artist_tracks(artist)
            if not tracks:
                return None
            with self._play_lock:
                self.stop_wave()
                self.queue = tracks
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] play_artist_by_id: {e}")
            return None

    def _get_artist_tracks(self, artist):
        if artist.id in self._artist_cache:
            return self._artist_cache[artist.id]
        tracks, seen = [], set()
        try:
            top = self.client.artists_tracks(artist.id)
            for t in (getattr(top, "tracks", None) or []):
                tid = getattr(t, "id", None)
                if tid and tid not in seen:
                    seen.add(tid)
                    tracks.append(t)
        except Exception:
            pass
        self._artist_cache[artist.id] = tracks
        return tracks

    def search_and_play(self, query):
        if not self.client:
            return None
        try:
            sr = self.client.search(query)
            tracks = []
            if sr.best and sr.best.type == "track":
                tracks.append(sr.best.result)
            if sr.tracks and sr.tracks.results:
                for t in sr.tracks.results:
                    if not tracks or t.id != tracks[0].id:
                        tracks.append(t)
            if not tracks:
                if sr.artists and sr.artists.results:
                    return self.play_artist(sr.artists.results[0].name)
                return None
            with self._play_lock:
                self.stop_wave()
                self.queue = tracks
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] search: {e}")
            return None

    def search_tracks(self, query, limit=40):
        if not self.client:
            return []
        try:
            sr = self.client.search(query)
            out = []
            if sr.tracks and sr.tracks.results:
                for t in sr.tracks.results[:limit]:
                    tid = getattr(t, "id", None)
                    if not tid:
                        continue
                    self._search_cache[str(tid)] = t
                    self._track_cache[int(tid)] = t
                    out.append(self._track_dict(t))
            return out
        except Exception as e:
            print(f"[!] search_tracks: {e}")
            return []

    def search_suggest(self, q):
        if not self.client or not q.strip():
            return []
        try:
            sug = self.client.search_suggest(q)
            out = []
            for s in (getattr(sug, "suggestions", None) or [])[:8]:
                txt = getattr(s, "text", None)
                if txt:
                    out.append(str(txt))
            return out
        except Exception as e:
            print(f"[!] suggest: {e}")
            return []

    def play_search_track(self, track_id):
        tid = str(track_id)
        item = self._search_cache.get(tid)
        if not item and tid.isdigit():
            item = self._track_cache.get(int(tid))
        if not item:
            try:
                tracks = self.client.tracks([int(tid)])
                item = tracks[0] if tracks else None
                if item:
                    self._track_cache[int(tid)] = item
            except Exception:
                item = None
        if not item:
            return None
        with self._play_lock:
            self.stop_wave()
            self.queue = [item]
            self.current_index = 0
            return self._play_current()

    def play_random(self):
        if not self.client:
            return None
        try:
            self._ensure_liked_ids()
            pool = []
            try:
                likes = self._to_shorts(self.client.users_likes_tracks())
                pool = [t for t in likes if getattr(t, "id", None)]
            except Exception:
                pass
            if len(pool) < 5:
                ch = self.get_chart(50)
                if ch:
                    try:
                        tracks = self.client.tracks([int(x["id"]) for x in ch])
                        pool = [t for t in tracks if t]
                    except Exception:
                        pass
            if not pool:
                return None
            t = random.choice(pool)
            self._track_cache[int(t.id)] = t
            return self.play_search_track(t.id)
        except Exception as e:
            print(f"[!] random: {e}")
            return None

    def _play_for_you_key(self, idx):
        items = self.get_for_you()
        if not items or idx >= len(items):
            return None
        it = items[idx]
        kind = it.get("kind")
        uid = it.get("uid")
        if not kind or not uid:
            return None
        return self.play_playlist_by_id(kind, uid)

    def _play_chart_tracks(self):
        if not self.client:
            return None
        try:
            ch = self.get_chart(40)
            if not ch:
                return None
            items = []
            for d in ch:
                t = self._track_cache.get(int(d["id"])) if d["id"].isdigit() else None
                if t is not None:
                    items.append(t)
            if not items:
                return None
            with self._play_lock:
                self.stop_wave()
                self.queue = items
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] play chart: {e}")
            return None

    def _play_new_releases(self):
        if not self.client:
            return None
        try:
            rels = self.get_new_releases()
            if not rels:
                return None
            return self.play_album_track(rels[0]["id"], 0)
        except Exception as e:
            print(f"[!] play new releases: {e}")
            return None

    def queue_next_track(self, q):
        if not self.client:
            return None
        try:
            sr = self.client.search(q)
            t = sr.best.result if sr.best and sr.best.type == "track" else (
                sr.tracks.results[0] if sr.tracks and sr.tracks.results else None
            )
            if not t:
                return None
            with self._play_lock:
                if not self.queue:
                    self.queue = [t]
                    self.current_index = 0
                    return self._play_current()
                self.queue.insert(self.current_index + 1, t)
            return {"type": "queue_added", "title": t.title}
        except Exception:
            return None

    def _append_similar(self, track):
        if not self.client or not track:
            return 0
        try:
            st = self.client.rotor_station_tracks(f"track:{track.id}", settings2=True)
            new = [s.track for s in st.sequence if s.track]
            existing = {_tid(t) for t in self.queue}
            uniq = [t for t in new if _tid(t) not in existing]
            self.queue.extend(uniq[:config.SIMILAR_BATCH_SIZE])
            return len(uniq[:config.SIMILAR_BATCH_SIZE])
        except Exception:
            return 0

    # ==================== SIMILAR / LYRICS ====================
    def get_similar(self, track_id):
        if not self.client or not track_id:
            return []
        try:
            st = self.client.tracks_similar(int(track_id))
            tracks = getattr(st, "tracks", None)
            if tracks is None and isinstance(st, (list, tuple)):
                tracks = st
            out = []
            for t in (tracks or [])[:20]:
                if t:
                    self._track_cache[int(getattr(t, "id"))] = t
                    out.append(self._track_dict(t))
            return out
        except Exception as e:
            print(f"[!] similar: {e}")
            return []

    def play_similar(self, track_id):
        tracks = self.get_similar(track_id)
        if not tracks:
            return None
        items = []
        for d in tracks:
            t = self._track_cache.get(int(d["id"])) if d["id"].isdigit() else None
            if t is not None:
                items.append(t)
        if not items:
            return None
        with self._play_lock:
            self.stop_wave()
            self.queue = items
            self.current_index = 0
            return self._play_current()

    def get_lyrics(self, track_id):
        if not self.client or not track_id:
            return None
        try:
            lyr = self.client.tracks_lyrics(int(track_id))
            if isinstance(lyr, (list, tuple)):
                lyr = lyr[0] if lyr else None
            if lyr is None:
                return None
            text = getattr(lyr, "full_lyrics", None) or getattr(lyr, "lyrics", None)
            if not text:
                return None
            return {"text": text}
        except Exception as e:
            print(f"[!] lyrics: {e}")
            return None

    # ==================== CATALOG ====================
    def get_for_you(self):
        out = []
        if not self.client:
            return out
        try:
            land = self.client.landing(blocks="playlists")
            for block in (getattr(land, "blocks", None) or []):
                if getattr(block, "type", "") != "playlists":
                    continue
                for ent in (getattr(block, "entities", None) or []):
                    gp = getattr(ent, "data", None)
                    if gp is None:
                        continue
                    pl = getattr(gp, "data", None)
                    title = getattr(gp, "title", None) or (
                        getattr(pl, "title", "") if pl else "")
                    if not title:
                        continue
                    cover = _extract_cover(gp, "300x300") or (
                        _extract_cover(pl, "300x300") if pl else None)
                    out.append({
                        "title": title,
                        "description": getattr(gp, "description", "") or "",
                        "cover_url": cover,
                        "kind": str(getattr(pl, "kind", "")) if pl else "",
                        "uid": getattr(pl, "uid", None) if pl else None,
                        "track_count": int(getattr(pl, "track_count", 0) or 0)
                        if pl else 0,
                    })
        except Exception as e:
            print(f"[!] for_you: {e}")
        return out

    # FIX: полный лендинг — все блоки (плейлисты дня/премьеры/дежавю/etc.)
    def get_landing_playlists(self):
        out = []
        if not self.client:
            return out
        try:
            land = self.client.landing(blocks="playlists,chart,new_releases")
            for block in (getattr(land, "blocks", None) or []):
                btype = getattr(block, "type", "")
                for ent in (getattr(block, "entities", None) or []):
                    gp = getattr(ent, "data", None)
                    if gp is None:
                        continue
                    pl = getattr(gp, "data", None)
                    out.append({
                        "block": btype,
                        "title": getattr(gp, "title", "") or "",
                        "description": getattr(gp, "description", "") or "",
                        "cover_url": _extract_cover(gp, "300x300"),
                        "kind": str(getattr(pl, "kind", "")) if pl else "",
                        "uid": getattr(pl, "uid", None) if pl else None,
                    })
        except Exception as e:
            print(f"[!] landing_full: {e}")
        return out

    # FIX: история прослушиваний
    def get_history(self, limit=50):
        if not self.client:
            return []
        try:
            h = self.client.music_history()
            out = []
            for item in (getattr(h, "history_tracks", None) or [])[:limit]:
                t = getattr(item, "track", None)
                if t:
                    out.append(self._track_dict(t))
            return out
        except Exception as e:
            print(f"[!] history: {e}")
            return []

    # FIX: авто-настройки волны по времени суток (как Алиса)
    def wave_auto_settings(self):
        import datetime
        h = datetime.datetime.now().hour
        if 5 <= h < 11:
            return {"mood": "fun"}
        if 11 <= h < 18:
            return {"mood": "energetic"}
        if 18 <= h < 23:
            return {"mood": "calm"}
        return {"mood": "sad"}

    def get_chart(self, limit=40):
        if not self.client:
            return []
        try:
            ch = self.client.chart()
            items = getattr(ch, "tracks", None) or []
            out = []
            for it in items[:limit]:
                t = getattr(it, "track", None)
                if not t:
                    continue
                tid = getattr(t, "id", None)
                if tid:
                    self._track_cache[int(tid)] = t
                out.append(self._track_dict(t))
            return out
        except Exception as e:
            print(f"[!] chart: {e}")
            return []

    def get_new_releases(self):
        if not self.client:
            return []
        try:
            nr = self.client.new_releases()
            out = []
            for al in (getattr(nr, "albums", None) or [])[:30]:
                out.append({
                    "id": str(getattr(al, "id", "")),
                    "title": getattr(al, "title", "—") or "—",
                    "year": getattr(al, "year", None),
                    "cover_url": _extract_cover(al, "300x300"),
                    "track_count": int(getattr(al, "track_count", 0) or 0),
                })
            return out
        except Exception as e:
            print(f"[!] new_releases: {e}")
            return []

    def get_stations(self):
        if not self.client:
            return []
        try:
            sts = self.client.rotor_stations_list()
            out = []
            seen = set()
            for s in (sts or []):
                sid = str(getattr(s, "id", "") or "")
                if not sid or sid in seen:
                    continue
                if not sid.split(":")[0] in ("genre", "mood", "activity",
                                             "epoch", "playlist"):
                    continue
                seen.add(sid)
                icon = getattr(s, "icon", None)
                img = None
                if icon is not None and getattr(icon, "url", None):
                    try:
                        img = "https://" + icon.url.replace("%%", "200x200")
                    except Exception:
                        img = None
                name = getattr(s, "name_for_from", None) or \
                    getattr(s, "name", None) or sid
                out.append({"id": sid, "name": str(name), "image": img})
            return out[:80]
        except Exception as e:
            print(f"[!] stations: {e}")
            return []

    # ==================== LIKED ====================
    def get_liked_tracks(self, limit=None):
        if not self.client:
            return []
        try:
            likes = self.client.users_likes_tracks()
            shorts = self._to_shorts(likes)
            print(f"[✓] liked raw: {len(shorts)}")
            self._liked_cache = shorts
            self._liked_ids = {str(getattr(t, "id", "")) for t in shorts
                               if getattr(t, "id", None)}
            self._liked_ids_loaded = True
            out = self._enrich(shorts, limit=limit)
            print(f"[✓] liked tracks: {len(out)}")
            return out
        except Exception as e:
            print(f"[!] liked: {e}")
            traceback.print_exc()
            return []

    def play_liked_at(self, index):
        shorts = self._liked_cache
        if not shorts or index >= len(shorts):
            return None
        with self._play_lock:
            self.stop_wave()
            self.queue = shorts
            self.current_index = index
            return self._play_current()

    def play_liked_tracks(self):
        if not self.client:
            return None
        try:
            likes = self.client.users_likes_tracks()
            shorts = self._to_shorts(likes)
            if not shorts:
                return None
            self._liked_cache = shorts
            with self._play_lock:
                self.stop_wave()
                self.queue = shorts
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] liked: {e}")
            return None

    # ==================== PLAYLISTS ====================
    def get_user_playlists(self):
        if not self.client:
            return []
        try:
            return self.client.users_playlists_list() or []
        except Exception as e:
            print(f"[!] users_playlists_list: {e}")
            return []

    def get_playlists_short(self):
        if not self.client:
            return []
        try:
            playlists = self.client.users_playlists_list()
        except Exception as e:
            print(f"[!] playlists_short: {e}")
            return []
        out = []
        for pl in (playlists or []):
            try:
                cover = _extract_cover(pl, "300x300")
                tc = getattr(pl, "track_count", None)
                if callable(tc):
                    try:
                        tc = tc()
                    except Exception:
                        tc = 0
                kind = getattr(pl, "kind", None)
                out.append({
                    "kind": str(kind) if kind is not None else "",
                    "title": getattr(pl, "title", None) or "Без названия",
                    "track_count": int(tc or 0),
                    "cover_url": cover,
                })
            except Exception as e:
                print(f"[!] pl short: {e}")
                continue
        with_cover = sum(1 for p in out if p["cover_url"])
        print(f"[✓] playlists short: {len(out)}, с обложками: {with_cover}")
        return out

    def get_playlist_tracks(self, kind, limit=None):
        if not self.client:
            return []
        kind = str(kind)
        if kind in self._pl_tracks_cache:
            return self._pl_tracks_cache[kind]

        pl = None
        for p in self.get_user_playlists():
            if str(p.kind) == kind:
                pl = p
                break
        if not pl:
            print(f"[!] playlist {kind} not found")
            return []

        try:
            uid = pl.owner.uid if pl.owner else self.client.me.account.uid
            full = self.client.users_playlists(pl.kind, uid)
            raw = self._to_shorts(full)
            print(f"[✓] playlist {pl.title} raw: {len(raw)}")
            self._pl_raw_cache[kind] = raw
            tracks = self._enrich(raw, limit=limit)
            self._pl_tracks_cache[kind] = tracks
            print(f"[✓] playlist {pl.title}: {len(tracks)} tracks")
            return tracks
        except Exception as e:
            print(f"[!] pl tracks {kind}: {e}")
            traceback.print_exc()
            return []

    def play_playlist_track(self, kind, index):
        shorts = self._pl_raw_cache.get(str(kind))
        if not shorts and self.client:
            try:
                for pl in self.get_user_playlists():
                    if str(pl.kind) == str(kind):
                        uid = pl.owner.uid if pl.owner else self.client.me.account.uid
                        full = self.client.users_playlists(pl.kind, uid)
                        shorts = self._to_shorts(full)
                        self._pl_raw_cache[str(kind)] = shorts
                        break
            except Exception:
                pass
        if not shorts or index >= len(shorts):
            return None
        with self._play_lock:
            self.stop_wave()
            self.queue = shorts
            self.current_index = index
            return self._play_current()

    def play_playlist_by_id(self, kind, uid):
        if not self.client:
            return None
        try:
            full = self.client.users_playlists(int(kind), int(uid))
            raw = self._to_shorts(full)
            if not raw:
                return None
            with self._play_lock:
                self.stop_wave()
                self.queue = raw
                self.current_index = 0
                return self._play_current()
        except Exception as e:
            print(f"[!] play_playlist_by_id: {e}")
            return None

    def create_playlist(self, title):
        if not self.client:
            return None
        try:
            pl = self.client.users_playlists_create(title)
            return {
                "kind": str(getattr(pl, "kind", "")),
                "title": getattr(pl, "title", title) or title,
                "track_count": int(getattr(pl, "track_count", 0) or 0),
                "cover_url": _extract_cover(pl, "300x300"),
            }
        except Exception as e:
            print(f"[!] create pl: {e}")
            return None

    def add_track_to_playlist(self, kind, track_id, album_id=None):
        if not self.client:
            return False
        try:
            if not album_id:
                t = self._track_cache.get(int(track_id))
                if t is None:
                    ts = self.client.tracks([int(track_id)])
                    t = ts[0] if ts else None
                    if t:
                        self._track_cache[int(track_id)] = t
                album_id = _first_album_id(t) if t else None
            if not album_id:
                return False
            self.client.users_playlists_insert_track(
                int(kind), int(track_id), int(album_id))
            self._pl_tracks_cache.pop(str(kind), None)
            self._pl_raw_cache.pop(str(kind), None)
            return True
        except Exception as e:
            print(f"[!] pl add: {e}")
            return False

    def rename_playlist(self, kind, name):
        if not self.client:
            return False
        try:
            self.client.users_playlists_name(int(kind), name)
            return True
        except Exception as e:
            print(f"[!] pl rename: {e}")
            return False

    # ==================== ARTIST PAGE ====================
    def get_artist_info(self, ident):
        if not self.client or not ident:
            return None
        key = str(ident).strip().lower()
        if key in self._artist_info_cache:
            return self._artist_info_cache[key]
        try:
            artist = None
            if key.isdigit():
                try:
                    artists = self.client.artists([int(key)])
                    artist = artists[0] if artists else None
                except Exception:
                    artist = None
            if not artist:
                sr = self.client.search(str(ident))
                if sr.artists and sr.artists.results:
                    artist = sr.artists.results[0]
            if not artist:
                return None

            info = {
                "id": str(artist.id),
                "name": artist.name or "—",
                "cover_url": None,
                "banner_url": None,
                "description": "",
                "genres": [],
                "counts": {"tracks": 0, "albums": 0, "likes": 0},
                "albums": [],
                "top_tracks": [],
                "videos": [],
            }

            try:
                cov = getattr(artist, "cover", None)
                if cov and getattr(cov, "uri", None):
                    info["cover_url"] = "https://" + cov.uri.replace("%%", "600x600")
                elif getattr(artist, "og_image", None):
                    info["cover_url"] = artist.og_image
            except Exception:
                pass

            info["description"] = (getattr(artist, "description", "") or "")[:600]
            g = getattr(artist, "genres", None) or []
            info["genres"] = [str(x) for x in g][:5]

            try:
                c = getattr(artist, "counts", None)
                if c:
                    info["counts"] = {
                        "tracks": int(getattr(c, "tracks", 0) or 0),
                        "albums": int(getattr(c, "direct_albums", 0) or 0),
                        "likes": int(getattr(c, "likes", 0) or 0),
                    }
            except Exception:
                pass

            try:
                brief = self.client.artists_brief_info(artist.id)
                if brief:
                    bc = getattr(brief, "cover", None)
                    if bc and getattr(bc, "uri", None):
                        info["banner_url"] = "https://" + bc.uri.replace("%%", "1000x400")
                    for a in (getattr(brief, "albums", None) or [])[:24]:
                        try:
                            cover = _extract_cover(a, "400x400")
                            info["albums"].append({
                                "id": str(a.id),
                                "title": getattr(a, "title", "—") or "—",
                                "year": getattr(a, "year", None),
                                "cover_url": cover,
                                "track_count": int(getattr(a, "track_count", 0) or 0),
                                "type": getattr(a, "type", "album") or "album",
                            })
                        except Exception:
                            continue
            except Exception as e:
                print(f"[!] artist brief: {e}")

            try:
                top = self.client.artists_tracks(artist.id)
                for t in (getattr(top, "tracks", None) or [])[:20]:
                    tid = getattr(t, "id", None)
                    if not tid:
                        continue
                    self._search_cache[str(tid)] = t
                    self._track_cache[int(tid)] = t
                    info["top_tracks"].append(self._track_dict(t))
            except Exception as e:
                print(f"[!] artist top: {e}")

            try:
                vids = self.client.artists_videos(artist.id)
                for v in (getattr(vids, "videos", None) or [])[:12]:
                    info["videos"].append({
                        "id": str(getattr(v, "id", "")),
                        "title": getattr(v, "title", "—") or "—",
                        "thumbnail": getattr(v, "thumbnail", None),
                        "duration": int(getattr(v, "duration", 0) or 0),
                        "url": getattr(v, "url", None),
                    })
            except Exception:
                pass

            self._artist_info_cache[key] = info
            return info
        except Exception as e:
            print(f"[!] artist_info: {e}")
            traceback.print_exc()
            return None

    def get_album_tracks(self, album_id, limit=None):
        if not self.client or not album_id:
            return []
        aid = str(album_id)
        if aid in self._album_cache:
            return self._album_cache[aid]
        try:
            full = self.client.albums_with_tracks(int(aid))
            tracks = []
            for v in (full.volumes or []):
                for t in (v or []):
                    if not t:
                        continue
                    tid = getattr(t, "id", None)
                    if not tid:
                        continue
                    self._search_cache[str(tid)] = t
                    self._track_cache[int(tid)] = t
                    tracks.append(self._track_dict(t))
                    if limit and len(tracks) >= limit:
                        break
            self._album_cache[aid] = tracks
            return tracks
        except Exception as e:
            print(f"[!] album_tracks: {e}")
            return []

    def play_album_track(self, album_id, index):
        if not self.client:
            return None
        try:
            full = self.client.albums_with_tracks(int(album_id))
        except Exception:
            return None
        all_tracks = []
        for v in (full.volumes or []):
            for t in (v or []):
                if t:
                    all_tracks.append(t)
        if not all_tracks or index >= len(all_tracks):
            return None
        with self._play_lock:
            self.stop_wave()
            self.queue = all_tracks
            self.current_index = index
            return self._play_current()

    # ==================== ACCOUNTS ====================
    def add_account_from_token(self, token):
        try:
            c = Client(token).init()
            me = c.me
            login = getattr(me.account, "login", "user")
            display = getattr(me.account, "display_name", login)
            avatar = None
            try:
                if getattr(me.account, "cover", None) and me.account.cover.uri:
                    avatar = "https://" + me.account.cover.uri.replace("%%", "200x200")
            except Exception:
                pass
            uid = self.accounts.add(token, {
                "uid": me.account.uid,
                "login": login,
                "display_name": display,
                "avatar_url": avatar,
            })
            self.accounts.switch(uid)
            self._init_yandex()
            return {"ok": True, "uid": uid, "display_name": display}
        except Exception as e:
            print(f"[!] add_account: {e}")
            return {"ok": False, "error": str(e)}

    def switch_account(self, uid):
        if self.accounts.switch(uid):
            self._init_yandex()
            for c in (self._artist_cache, self._pl_raw_cache, self._pl_tracks_cache,
                      self._search_cache, self._track_cache, self._album_cache,
                      self._artist_info_cache, self._codec_cache):
                c.clear()
            self._liked_cache = []
            self._liked_ids = set()
            self._liked_ids_loaded = False
            return True
        return False

    def remove_account(self, uid):
        return self.accounts.remove(uid)

    def get_accounts(self):
        return self.accounts.list()

    def cleanup(self):
        pass