"""Парсер команд + LLM. Без эмодзи для плейлистов."""
import re, config


WAVE_MAP = {
    "грустное":"mood:sad","грустно":"mood:sad","печальное":"mood:sad",
    "весёлое":"mood:happy","весело":"mood:happy","радостное":"mood:happy",
    "энергичное":"mood:energetic","бодрое":"mood:energetic",
    "спокойное":"mood:calm","расслабленное":"mood:calm",
    "романтичное":"mood:romantic","романтика":"mood:romantic",
    "рок":"genre:allrock","поп":"genre:pop","рэп":"genre:hiphop",
    "хип-хоп":"genre:hiphop","джаз":"genre:jazz",
    "классика":"genre:classical","метал":"genre:metal","инди":"genre:indie",
    "электроника":"genre:electro",
    "тренировка":"activity:workout","спорт":"activity:workout",
    "работа":"activity:work","учёба":"activity:study","учеба":"activity:study",
    "сон":"activity:sleep","засыпаю":"activity:sleep",
    "отдых":"activity:relax","медитация":"activity:meditation",
    "вечеринка":"activity:partying","танцы":"activity:partying",
}

NUM_WORDS = {
    "ноль": 0, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
}

GREETINGS = ["Привет! Чем могу помочь?", "Привет-привет!", "Слушаю тебя!"]
WHAT_CAN = ("Я Лена — твой музыкальный ассистент. Могу включить трек, плейлист, "
            "мою волну или станцию, перемешать, лайкнуть, показать текст песни, "
            "перемотать, поменять громкость.")


class AIBrain:
    def __init__(self):
        self.client = None
        self.wake_words = list(config.WAKE_WORDS)
        try:
            import openai
            self.client = openai.OpenAI(base_url=config.OLLAMA_URL, api_key="ollama")
        except Exception:
            pass

    def extract_wake_word(self, text):
        t = text.lower().strip()
        self.wake_words = list(config.WAKE_WORDS)
        found = None
        for ww in self.wake_words:
            p = r"\b" + re.escape(ww) + r"\b"
            if re.search(p, t):
                found = ww
                t = re.sub(p, "", t).strip(" ,.!?")
                break
        if not found:
            return None
        t = re.sub(r"^(эй|хей|привет|пожалуйста|слушай|а|ну|давай)\s+",
                   "", t).strip(" ,.!?")
        return t if t else "просто_имя"

    # ==================== LLM ====================
    def _llm_classify(self, text):
        if not self.client:
            return None
        try:
            r = self.client.chat.completions.create(
                model=config.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content":
                     "Ты классификатор намерений. Ответь ОДНИМ словом: "
                     "MUSIC — если пользователь хочет включить/найти/поставить "
                     "музыку, трек, плейлист, артиста или управлять плеером. "
                     "CHAT — если это разговор, шутка, вопрос, приветствие, "
                     "благодарность или что-то ещё."},
                    {"role": "user", "content": text},
                ],
                max_tokens=4, temperature=0, timeout=4,
            )
            ans = (r.choices[0].message.content or "").strip().upper()
            if "MUSIC" in ans: return "MUSIC"
            if "CHAT" in ans:  return "CHAT"
        except Exception as e:
            print(f"[!] classify: {e}")
        return None

    def _llm_chat(self, text):
        if not self.client:
            return "Извини, я сейчас не могу ответить."
        try:
            r = self.client.chat.completions.create(
                model=config.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content":
                     f"Ты — {config.ASSISTANT_NAME}, дружелюбный музыкальный "
                     f"ассистент. Отвечай коротко (до 2 предложений), "
                     f"по-русски, тепло и живо."},
                    {"role": "user", "content": text},
                ],
                max_tokens=120, temperature=0.85, timeout=10,
            )
            return (r.choices[0].message.content or "").strip() or "Хм…"
        except Exception as e:
            print(f"[!] chat: {e}")
            return "Извини, не могу сейчас ответить."

    # ==================== HELPERS ====================
    @staticmethod
    def _numbers_to_digits(t):
        words = t.split()
        out, total = [], 0
        for w in words:
            v = NUM_WORDS.get(w)
            if v is not None:
                total += v
            else:
                if total:
                    out.append(str(total)); total = 0
                out.append(w)
        if total:
            out.append(str(total))
        return " ".join(out)

    # ==================== PROCESS ====================
    def process(self, text, current_track_id=None, current_track=None):
        t = text.lower().strip()
        if not t or t == "просто_имя":
            return "chat", {}, "Слушаю"

        if re.search(r"\b(шутк|пошути|анекдот|рассмеши)", t):
            return "chat", {}, self._llm_chat("Расскажи короткую шутку")
        if re.search(r"\b(спасибо|благодарю|спс)\b", t):
            return "chat", {}, "Всегда рада помочь!"
        if re.search(r"как\s+(у\s+тебя\s+)?дела", t):
            return "chat", {}, "Отлично! Готова включать музыку."
        if re.search(r"^(привет|здравствуй|хай|прив|доброе утро|"
                     r"добрый день|добрый вечер)\b", t):
            import random
            return "chat", {}, random.choice(GREETINGS)
        if re.search(r"^(кто\s+ты|что\s+ты\s+умеешь|расскажи\s+о\s+себе)", t):
            return "chat", {}, WHAT_CAN

        for p in [r"теперь\s+тебя\s+зовут\s+(.+)", r"тво[её]\s+новое\s+имя\s+(.+)",
                  r"переименую\s+тебя\s+в\s+(.+)", r"смени\s+имя\s+на\s+(.+)"]:
            m = re.search(p, t)
            if m:
                n = m.group(1).strip(" .,!?")[:30]
                return "change_name", {"name": n}, f"Теперь меня зовут {n}"

        # ---- что играет ----
        if re.search(r"(что\s+за\s+трек|кто\s+по[её]т|что\s+играет|"
                     r"что\s+сейчас|какая\s+песня)", t):
            if current_track and current_track.get("title"):
                art = current_track.get("artists") or "неизвестный артист"
                return "none", {}, f"Сейчас играет: {art} — {current_track['title']}"
            return "none", {}, "Сейчас ничего не играет"

        # ---- настройки волны ----
        if re.search(r"(повеселее|весёл\w*|весел\w*)\s*(волна|волну)?", t) or \
           "веселее" in t:
            return "wave_settings", {"mood": "fun"}, "Делаю волну повеселее"
        if re.search(r"(пободрее|энергичнее|бодрее)", t):
            return "wave_settings", {"mood": "energetic"}, "Добавляю энергии"
        if re.search(r"(поспокойнее|спокойнее|спокойн\w*\s*волна)", t):
            return "wave_settings", {"mood": "calm"}, "Успокаиваю волну"
        if re.search(r"грустн\w*\s*волна|волну\s+грустн", t):
            return "wave_settings", {"mood": "sad"}, "Грустная волна"
        if re.search(r"(что-нибудь\s+)?знаком\w*|только\s+любимое", t):
            return "wave_settings", {"diversity": "favorite"}, "Знакомая музыка"
        if re.search(r"новенькое|что-нибудь\s+новое|открытий|открывай\s+новое", t):
            return "wave_settings", {"diversity": "discover"}, "Открываю новое"
        if re.search(r"только\s+русск\w*|лишь\s+русск", t):
            return "wave_settings", {"language": "russian"}, "Только русское"
        if re.search(r"без\s+русск\w*|без\s+русского", t):
            return "wave_settings", {"language": "notrussian"}, "Без русского"
        if re.search(r"только\s+иностр\w*|зарубежн\w*\s*музыка", t):
            return "wave_settings", {"language": "foreign"}, "Иностранная музыка"

        # ---- лайк/дизлайк ----
        if re.search(r"(лайкн\w*|поставь\s+лайк|добавь\s+в\s+любим\w*|"
                     r"мне\s+нравится\s+(этот\s+)?трек|залайкай)", t):
            return "like", {}, "Добавила в любимые"
        if re.search(r"(дизлайк|не\s+нравится|скучн\w*\s*трек|убери\s+из\s+любимых)", t):
            return "dislike_next", {}, "Поняла, переключаю"

        # ---- перемотки ----
        if "перемотай" in t or "поставь на" in t:
            m = re.search(r"(\d+)\s*[:.]\s*(\d+)", t)
            if m:
                return "seek_time", {"seconds": int(m.group(1))*60+int(m.group(2))}, "Перематываю"
            m = re.search(r"(\d+)\s*(?:минут\w*|мин\w*)\s*(?:и\s*)?(\d+)?", t)
            if m:
                s = int(m.group(1))*60 + (int(m.group(2)) if m.group(2) else 0)
                return "seek_time", {"seconds": s}, f"{s} сек"
            m = re.search(r"(\d+)\s*(?:секунд|сек)", t)
            if m:
                return "seek_time", {"seconds": int(m.group(1))}, f"{m.group(1)} сек"
            if "к началу" in t:
                return "seek_time", {"seconds": 0}, "С начала"

        m = re.search(r"(вперёд|вперед)\s+(?:на\s+)?(\d+)", t)
        if m: return "seek_relative", {"delta": int(m.group(2))}, "Вперёд"
        m = re.search(r"назад\s+(?:на\s+)?(\d+)", t)
        if m: return "seek_relative", {"delta": -int(m.group(2))}, "Назад"

        # ---- текст песни ----
        if re.search(r"(текст\s+(песни|трека)|покажи\s+текст|что\s+по[её]тся)", t):
            return "lyrics", {}, "Открываю текст"

        # ---- похожие ----
        if re.search(r"(похожие|похожих|что-нибудь\s+в\s+этом\s+духе)", t):
            return "similar", {}, "Ищу похожие"

        # ---- перемешать / повтор ----
        if re.search(r"(перемеша|вперемешку|шаффл|вразброс)", t):
            return "shuffle", {}, "Перемешиваю"
        if re.search(r"(выключи|отключи|убери)\s+повтор|не\s+повторяй", t):
            return "repeat", {"enabled": False}, "Повтор выключен"
        if re.search(r"(повторяй|повтор\s+трека|повтори\s+этот|повтор)", t):
            return "repeat", {"enabled": True}, "Повторяю этот трек"

        # ---- громкость (числительные) ----
        t2 = self._numbers_to_digits(t)
        m = re.search(r"(?:громкость|звук)\s+(?:на\s+)?(\d+)", t2)
        if m:
            return "volume_set", {"level": int(m.group(1))*10}, f"Громкость {m.group(1)}"
        if re.search(r"включи\s+звук|верни\s+звук|убери\s+без\s+звука|со\s+звуком", t):
            return "unmute", {}, "Звук включён"
        if any(x in t for x in ["громче", "погромче"]) and "волна" not in t:
            return "volume_up", {}, "Громче"
        if any(x in t for x in ["тише", "потише"]) and "сделай" not in t:
            return "volume_down", {}, "Тише"
        if "без звука" in t: return "mute", {}, "Звук выкл"

        # ---- плей/пауза/скип ----
        if re.search(r"следующ|дальше|скип|пропусти", t):
            return "next", {}, "Следующий"
        if re.search(r"предыдущ|назад трек|верни", t):
            return "previous", {}, "Предыдущий"

        m = re.search(r"добавь\s+в\s+очередь\s+(.+)", t)
        if m:
            return "queue_track", {"query": m.group(1).strip()}, "Добавила"

        # ---- подборки / чарт / новинки ----
        if re.search(r"плейлист\s+дня|плейлист\s+на\s+день", t):
            return "play_landing", {}, "Плейлист дня"
        if re.search(r"премьер", t):
            return "play_premiere", {}, "Премьеры"
        if re.search(r"дежавю|дежа-вю", t):
            return "play_dejavu", {}, "Дежавю"
        if re.search(r"(включи\s+)?чарт|популярное\s+сейчас|топ\s+сейчас", t):
            return "play_chart", {}, "Чарт"
        if re.search(r"новинк", t):
            return "play_new_releases", {}, "Новинки"
        if re.search(r"(включи\s+)?станцию\s+(.+)", t):
            m = re.search(r"станцию\s+(.+)", t)
            seed = self._station_seed(m.group(1))
            if seed:
                return "my_wave", {"seeds": [seed]}, f"Станция {m.group(1).strip()}"
        if re.search(r"включи\s+что-нибудь|поставь\s+что-нибудь|любую\s+музыку|"
                     r"случайн\w*\s+трек|включи\s+любой", t):
            return "play_random", {}, "Включаю случайный трек"

        if re.search(r"мою?\s+волну", t):
            if current_track_id and ("по треку" in t or "похожие" in t):
                return "my_wave_by_track", {"track_id": current_track_id}, "Волна по треку"
            return "my_wave", {}, "Моя волна"

        for ru, seed in WAVE_MAP.items():
            if re.search(rf"(?:включи|поставь|сыграй)\s+{re.escape(ru)}", t) or \
               re.search(rf"хочу\s+{re.escape(ru)}", t):
                return "my_wave", {"seeds":[seed]}, f"Волна {ru}"

        if re.search(r"включи\s+(?:музыку|радио)", t):
            return "my_wave", {}, "Волна"

        m = re.search(r"(?:включи|поставь|сыграй)\s+(?:исполнителя|группу|артиста|песни|треки)\s+(.+)", t)
        if m:
            return "play_artist", {"name": m.group(1).strip()}, f"Включаю {m.group(1)}"

        m = re.search(r"(?:включи|поставь|сыграй)\s+плейлист\s+(.+)", t)
        if m:
            return "play_playlist", {"name": m.group(1).strip()}, f"Плейлист «{m.group(1)}»"

        if any(x in t for x in ["любимые","лайки","мне нравится"]):
            return "play_liked", {}, "Любимые"

        if "качество" in t:
            if "превосходн" in t or "lossless" in t or "без потерь" in t:
                return "set_quality", {"quality":"lossless"}, "Lossless"
            if "высок" in t or "320" in t:
                return "set_quality", {"quality":"high"}, "High"

        if any(x in t for x in ["пауза","стоп","останови","замолчи","тихо!"]):
            return "pause", {}, "Пауза"
        if t in ["играй","продолжи","плей","play"]:
            return "play", {}, "Продолжаю"

        for prefix in ["включи песню","включи трек","поставь песню","включи",
                       "поставь","найди","сыграй","запусти"]:
            if t.startswith(prefix):
                q = t[len(prefix):].strip()
                if q:
                    q = re.sub(r"^(мне|нам|пожалуйста)\s+", "", q).strip()
                    return "search_and_play", {"query": q}, f"Включаю {q}"

        intent = self._llm_classify(t)
        if intent == "CHAT":
            return "chat", {}, self._llm_chat(t)
        return "search_and_play", {"query": t}, f"Ищу {t}"

    @staticmethod
    def _station_seed(q):
        q = q.strip().lower()
        for ru, seed in WAVE_MAP.items():
            if ru in q:
                return seed
        return f"genre:{q.replace(' ', '')}" if q else None
