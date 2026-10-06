"""Скачивает Vosk-модели: маленькую для wake-word + большую для команд."""
import os, sys, urllib.request, zipfile, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(BASE, "models")

# small — быстрая, для wake-word (45 МБ)
SMALL = {
    "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
    "zip_name": "vosk-model-small-ru-0.22",
    "target": "vosk-small-ru",
}
# big — точная, для команд (700 МБ)
BIG = {
    "url": "https://alphacephei.com/vosk/models/vosk-model-ru-0.22.zip",
    "zip_name": "vosk-model-ru-0.22",
    "target": "vosk-ru",
}


def _download_one(spec):
    target = os.path.join(MODELS, spec["target"])
    if os.path.isdir(target) and os.listdir(target):
        print(f"[✓] Уже есть: {target}")
        return
    os.makedirs(MODELS, exist_ok=True)
    zip_path = os.path.join(MODELS, spec["zip_name"] + ".zip")
    print(f"[↓] Скачиваю {spec['url']}")
    with urllib.request.urlopen(spec["url"]) as r, open(zip_path, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk: break
            f.write(chunk); done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(
                    f"\r    {pct}% ({done//1024//1024} / {total//1024//1024} МБ)")
                sys.stdout.flush()
    print()
    print("[i] Распаковываю…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(MODELS)
    src = os.path.join(MODELS, spec["zip_name"])
    if os.path.isdir(src):
        if os.path.isdir(target): shutil.rmtree(target)
        os.rename(src, target)
    os.remove(zip_path)
    print(f"[✓] Готово: {target}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or "small" in args:
        _download_one(SMALL)
    if not args or "big" in args:
        print()
        print("=" * 60)
        print("  Большая модель ~700 МБ. Скачиваем? (y/n)")
        ans = input("  > ").strip().lower()
        if ans == "y":
            _download_one(BIG)