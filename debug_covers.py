"""
Диагностика обложек. Показывает, где реально лежат URL.
Запуск: python debug_covers.py
"""
import config
from yandex_music import Client
from accounts import AccountManager


def _try(obj, name):
    try:
        v = getattr(obj, name, "<NO ATTR>")
        if callable(v):
            try:
                v = v()
            except Exception:
                v = "<err>"
        return v
    except Exception as e:
        return f"<err {e}>"


def main():
    accounts = AccountManager()
    token = accounts.get_active_token() or config.YANDEX_MUSIC_TOKEN
    print(f"[i] Использую токен: {token[:20]}…")
    client = Client(token).init()

    pls = client.users_playlists_list() or []
    print(f"\n========== ПЛЕЙЛИСТЫ: {len(pls)} ==========")
    for pl in pls[:8]:
        print(f"\n--- {pl.title!r} (kind={_try(pl, 'kind')!r}) ---")
        print(f"  cover_uri      = {_try(pl, 'cover_uri')!r}")
        print(f"  cover          = {_try(pl, 'cover')!r}")
        cov = getattr(pl, "cover", None)
        if cov is not None:
            print(f"  cover.uri      = {_try(cov, 'uri')!r}")
        print(f"  og_image       = {_try(pl, 'og_image')!r}")

    # Для сравнения — трек
    sr = client.search("кино")
    tr = sr.tracks.results[0] if sr.tracks and sr.tracks.results else None
    if tr:
        print(f"\n========== ТРЕК (для сравнения) ==========")
        print(f"--- {tr.title!r} ---")
        print(f"  cover_uri      = {_try(tr, 'cover_uri')!r}")
        cov = getattr(tr, "cover", None)
        if cov is not None:
            print(f"  cover.uri      = {_try(cov, 'uri')!r}")

    # Альбомы какого-нибудь артиста
    if sr.artists and sr.artists.results:
        art = sr.artists.results[0]
        print(f"\n========== АЛЬБОМЫ артиста {art.name!r} ==========")
        brief = client.artists_brief_info(art.id)
        for a in (getattr(brief, "albums", None) or [])[:5]:
            print(f"\n--- {a.title!r} ({a.year}) ---")
            print(f"  cover_uri      = {_try(a, 'cover_uri')!r}")
            cov = getattr(a, "cover", None)
            if cov is not None:
                print(f"  cover.uri      = {_try(cov, 'uri')!r}")
            print(f"  og_image       = {_try(a, 'og_image')!r}")


if __name__ == "__main__":
    main()