import os, sys, platform, textwrap
APP = "ElenaMusic"

def _pyw():
    e = sys.executable
    if platform.system() == "Windows":
        alt = e.replace("python.exe", "pythonw.exe")
        return alt if os.path.exists(alt) else e
    return e

def _entry():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

def is_enabled():
    p = platform.system()
    if p == "Windows":
        return os.path.exists(_win())
    if p == "Linux":
        return os.path.exists(_linux())
    return False

def set_autostart(on):
    p = platform.system()
    if p == "Windows": _set_win(on)
    elif p == "Linux": _set_linux(on)

def _win():
    d = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                     "Start Menu", "Programs", "Startup")
    return os.path.join(d, f"{APP}.bat")

def _set_win(on):
    p = _win()
    if on:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="cp1251") as f:
            f.write(f'@echo off\nstart "" "{_pyw()}" "{_entry()}"\n')
    elif os.path.exists(p):
        os.remove(p)

def _linux():
    return os.path.expanduser(f"~/.config/autostart/{APP}.desktop")

def _set_linux(on):
    p = _linux()
    if on:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(textwrap.dedent(f"""\
                [Desktop Entry]
                Type=Application
                Name=Elena Music
                Exec={sys.executable} {_entry()}
            """))
    elif os.path.exists(p):
        os.remove(p)