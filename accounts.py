"""Менеджер аккаунтов Яндекс Музыки."""
import json, os, threading

_BASE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_BASE, "accounts.json")


class AccountManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.accounts = {}   # uid -> {uid, login, display_name, token, avatar_url}
        self.active_uid = None
        self._load()

    def _load(self):
        if not os.path.exists(_FILE):
            return
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for a in data.get("accounts", []):
                if a.get("uid"):
                    self.accounts[str(a["uid"])] = a
            self.active_uid = data.get("active")
        except Exception as e:
            print(f"[!] accounts load: {e}")

    def _save(self):
        try:
            with open(_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "active": self.active_uid,
                    "accounts": list(self.accounts.values()),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] accounts save: {e}")

    def list(self):
        with self._lock:
            return {
                "active": self.active_uid,
                "accounts": [
                    {k: v for k, v in a.items() if k != "token"}
                    for a in self.accounts.values()
                ],
            }

    def get_active_token(self):
        with self._lock:
            if self.active_uid and self.active_uid in self.accounts:
                return self.accounts[self.active_uid]["token"]
        return None

    def add(self, token, client_info):
        uid = str(client_info["uid"])
        with self._lock:
            self.accounts[uid] = {
                "uid": uid,
                "login": client_info.get("login") or "user",
                "display_name": client_info.get("display_name") or "Пользователь",
                "avatar_url": client_info.get("avatar_url"),
                "token": token,
            }
            if not self.active_uid:
                self.active_uid = uid
            self._save()
        return uid

    def switch(self, uid):
        with self._lock:
            if str(uid) in self.accounts:
                self.active_uid = str(uid)
                self._save()
                return True
        return False

    def remove(self, uid):
        uid = str(uid)
        with self._lock:
            if uid in self.accounts:
                del self.accounts[uid]
                if self.active_uid == uid:
                    self.active_uid = next(iter(self.accounts), None)
                self._save()
                return True
        return False