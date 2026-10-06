"""
Резервный парсер команд (не используется в новом GUI-режиме,
оставлен для обратной совместимости).
"""
from fuzzywuzzy import fuzz


class CommandParser:
    def __init__(self):
        self.commands = {
            "play":     {"keywords": ["играй", "включи", "запусти", "продолжи"], "action": "play"},
            "pause":    {"keywords": ["пауза", "стоп", "останови"], "action": "pause"},
            "next":     {"keywords": ["следующий", "дальше", "скип"], "action": "next"},
            "previous": {"keywords": ["предыдущий", "назад", "верни"], "action": "previous"},
        }

    def parse(self, text):
        text = text.lower().strip()
        if not text:
            return {"action": "unknown", "params": {}}

        for _, cmd in self.commands.items():
            for kw in cmd["keywords"]:
                if kw in text:
                    return {"action": cmd["action"], "params": {}}

        best, best_score = None, 0
        for _, cmd in self.commands.items():
            for kw in cmd["keywords"]:
                s = fuzz.ratio(text, kw)
                if s > best_score and s > 70:
                    best_score, best = s, cmd["action"]
        if best:
            return {"action": best, "params": {}}
        return {"action": "search_and_play", "params": {"query": text}}