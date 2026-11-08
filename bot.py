import io
import os
import glob
import hashlib
import time
import random
import sqlite3
import asyncio
from datetime import timedelta

import numpy as np
import discord
from discord.ext import commands, tasks
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont

# ---------- НАСТРОЙКИ ----------
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Токен не найден! Укажи DISCORD_BOT_TOKEN в настройках бота на Bothost")
TOKEN = TOKEN.strip().strip('"').strip("'").strip()
print(f"[ТОКЕН] найден, длина: {len(TOKEN)} (обычно около 70-75)", flush=True)

# --- AFK ---
AFK_CHANNEL_ID = 0            # ID AFK-комнаты, куда перекидывать (0 = взять "Неактивный канал" из настроек сервера)
AFK_CHANNEL_IDS = []          # дополнительные каналы, где время не считается, например [123456789]
AFK_TIMEOUT = 15 * 60         # через сколько секунд неактивности перекидывать в AFK (15 минут)

TICK_SECONDS = 15             # как часто бот проверяет голосовые каналы
MAX_CREDIT = 60               # максимум секунд, которые можно начислить за один раз (защита от лагов)

COINS_PER_MINUTE = 1          # сколько монет даётся за минуту в войсе
CARD_PRICE = 100              # цена одной карточки
CARD_SERIES = "MINESHIELD"    # надпись над ником на карточке
CARD_SEASON = "SEASON IV"     # надпись под ником на карточке

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(BASE_DIR, "background.png")          # фон для /voice
TOP_BG_PATH = os.path.join(BASE_DIR, "top_background.png")  # фон для /voicetop
DB_PATH = os.getenv("DB_PATH") or os.path.join(BASE_DIR, "voice_time.db")

# ID приватного текстового канала для бэкапов базы (вставь число сюда или задай переменной BACKUP_CHANNEL_ID).
# Бот будет сам хранить там копию базы и восстанавливать её после перезапуска / новой заливки на хостинг.
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID") or 0)
CARDS_DIR = os.path.join(BASE_DIR, "cards")                 # сюда кладём картинки карточек

# --- Таблица лидеров (/voicetop) ---
# Все координаты заданы в системе фона 1935x813 (как на макете) и сами масштабируются под размер файла.
# Для точного совпадения с макетом нужен чистый фон top_background.png (без текста и линий в рамке).
TOP_OUT_W = 1600              # ширина итоговой картинки
TOP_RANK_X = 346              # левый край номера места
TOP_NAME_X = 455              # левый край ника
TOP_TIME_RIGHT = 1481         # правый край времени
TOP_BASELINE_1 = 334          # базовая линия первой строки (при 4 местах)
TOP_PITCH = 82                # расстояние между строками (при 5 местах автоматически уменьшается)
TOP_CENTER_Y = 441.5          # вертикальный центр блока строк
TOP_LINE_CENTER_X = 940       # центр разделительных линий
TOP_LINE_X = (320, 1570)      # где начинаются и заканчиваются линии
TOP_LINE_COLOR = (185, 70, 235)
TOP_RANK_COLOR = (125, 80, 250)
TOP_NAME_COLOR = (255, 255, 255)
TOP_TIME_COLOR = (125, 80, 250)
TOP_DX = 0                    # общий сдвиг таблицы (плюс = вправо)
TOP_DY = 0                    # общий сдвиг таблицы (плюс = вниз)

# Цвета редкостей (для карточек-заглушек и сообщений)
RARITY_COLORS = {
    "Обычная": (150, 155, 160),
    "Редкая": (70, 140, 255),
    "Эпическая": (170, 80, 255),
    "Легендарная": (255, 180, 40),
}

# weight = вес шанса. Шанс карточки = её weight / сумма всех weight.
CARDS = [
    {"id": "bez_ls", "name": "Bez_LS", "rarity": "Обычная", "weight": 60, "image": "bez_ls.png"},
    {"id": "abibaspro", "name": "abibaspro", "rarity": "Редкая", "weight": 30, "image": "abibaspro.png"},
    {"id": "nick3", "name": "Ник 3", "rarity": "Эпическая", "weight": 9, "image": "nick3.png"},
    {"id": "nick4", "name": "Ник 4", "rarity": "Легендарная", "weight": 1, "image": "nick4.png"},
]
# --------------------------------

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

voice_sessions = {}   # {user_id: время последнего начисления} (только те, кому сейчас идёт время)
session_start = {}    # {user_id: начало текущей сессии} (для показа в /voice)
last_active = {}      # {user_id: время последней активности}
synced = False
restored = False
CARDS_BY_ID = {c["id"]: c for c in CARDS}


# ---------- БАЗА ДАННЫХ ----------
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    return conn


def init_db():
    conn = _connect()
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""
        CREATE TABLE IF NOT EXISTS voice_time (
            user_id INTEGER,
            guild_id INTEGER,
            total_seconds INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, guild_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallet (
            user_id INTEGER,
            guild_id INTEGER,
            coins REAL DEFAULT 0,
            PRIMARY KEY (user_id, guild_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            user_id INTEGER,
            guild_id INTEGER,
            card_id TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, guild_id, card_id)
        )
    """)
    conn.commit()
    conn.close()


def add_time_batch(items):
    """items: [(user_id, guild_id, seconds)]. Одна транзакция на всех сразу."""
    if not items:
        return
    time_rows = [(u, g, s, s) for u, g, s in items]
    coin_rows = [(u, g, s * COINS_PER_MINUTE / 60, s * COINS_PER_MINUTE / 60) for u, g, s in items]
    conn = _connect()
    try:
        with conn:
            conn.executemany("""
                INSERT INTO voice_time (user_id, guild_id, total_seconds) VALUES (?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET total_seconds = total_seconds + ?
            """, time_rows)
            conn.executemany("""
                INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = coins + ?
            """, coin_rows)
    finally:
        conn.close()


def get_time(user_id: int, guild_id: int) -> int:
    conn = _connect()
    row = conn.execute("SELECT total_seconds FROM voice_time WHERE user_id = ? AND guild_id = ?",
                       (user_id, guild_id)).fetchone()
    conn.close()
    return row[0] if row else 0


def get_coins(user_id: int, guild_id: int) -> float:
    conn = _connect()
    row = conn.execute("SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?",
                       (user_id, guild_id)).fetchone()
    conn.close()
    return row[0] if row else 0.0


def give_coins(user_id: int, guild_id: int, amount: float):
    conn = _connect()
    with conn:
        conn.execute("""
            INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = coins + ?
        """, (user_id, guild_id, amount, amount))
    conn.close()


def get_top(guild_id: int, limit: int):
    conn = _connect()
    rows = conn.execute("""
        SELECT user_id, total_seconds FROM voice_time
        WHERE guild_id = ? ORDER BY total_seconds DESC LIMIT ?
    """, (guild_id, limit)).fetchall()
    conn.close()
    return rows


def buy_card(user_id: int, guild_id: int, card_id: str, price: int) -> bool:
    """Списывает монеты и выдаёт карточку одной транзакцией. False, если не хватает монет."""
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c = conn.cursor()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        row = c.fetchone()
        if not row or row[0] < price:
            c.execute("ROLLBACK")
            return False
        c.execute("UPDATE wallet SET coins = coins - ? WHERE user_id = ? AND guild_id = ?",
                  (price, user_id, guild_id))
        c.execute("""
            INSERT INTO cards (user_id, guild_id, card_id, count) VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, guild_id, card_id) DO UPDATE SET count = count + 1
        """, (user_id, guild_id, card_id))
        c.execute("COMMIT")
        return True
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def get_inventory(user_id: int, guild_id: int) -> dict:
    conn = _connect()
    rows = conn.execute("SELECT card_id, count FROM cards WHERE user_id = ? AND guild_id = ? AND count > 0",
                        (user_id, guild_id)).fetchall()
    conn.close()
    return {cid: cnt for cid, cnt in rows}


def set_time(user_id: int, guild_id: int, seconds: int, coins: float = None):
    """Жёстко задаёт общее время (и при желании баланс монет)."""
    conn = _connect()
    with conn:
        conn.execute("""
            INSERT INTO voice_time (user_id, guild_id, total_seconds) VALUES (?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET total_seconds = ?
        """, (user_id, guild_id, seconds, seconds))
        if coins is not None:
            conn.execute("""
                INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = ?
            """, (user_id, guild_id, coins, coins))
    conn.close()


def format_seconds(sec: int) -> str:
    return str(timedelta(seconds=sec))


# ---------- КАРТОЧКИ: ШАНСЫ ----------
def roll_card() -> dict:
    return random.choices(CARDS, weights=[c["weight"] for c in CARDS], k=1)[0]


def card_chance(card: dict) -> float:
    total = sum(c["weight"] for c in CARDS)
    return card["weight"] / total * 100


# ---------- ШРИФТЫ ----------
_font_cache = {}
_logged = set()

SYSTEM_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]
SYSTEM_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
]


def _local_fonts(bold: bool):
    """Возвращает список (путь, настоящий_ли_жирный) в порядке приоритета."""
    files = []
    for pattern in ("*.ttf", "*.otf", os.path.join("fonts", "*.ttf"), os.path.join("fonts", "*.otf")):
        files += glob.glob(os.path.join(BASE_DIR, pattern))
    files = sorted(set(files))

    def name(p):
        return os.path.basename(p).lower()

    bad = ("italic", "light", "thin", "medium", "black", "condensed", "semi", "extra")
    result = []

    def add(p, genuine):
        if p not in [r[0] for r in result]:
            result.append((p, genuine))

    if bold:
        for p in files:
            if name(p) == "font_bold.ttf":
                add(p, True)
        for p in files:
            if "bold" in name(p) and not any(b in name(p) for b in bad):
                add(p, True)
        for p in files:
            if "italic" not in name(p):
                add(p, False)
    else:
        for p in files:
            if name(p) == "font.ttf":
                add(p, True)
        for p in files:
            if "regular" in name(p) and "italic" not in name(p):
                add(p, True)
        for p in files:
            if "italic" not in name(p) and "bold" not in name(p):
                add(p, True)
        for p in files:
            add(p, True)
    return result


def _has_cyrillic(font) -> bool:
    """Если у шрифта нет кириллицы, разные буквы рисуются одинаковыми квадратиками."""
    try:
        a, b = font.getmask("Ж"), font.getmask("Щ")
        return a.size != b.size or a.tobytes() != b.tobytes()
    except Exception:
        return False


def load_font(size, bold=False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]

    candidates = _local_fonts(bold)
    sys_list = SYSTEM_BOLD if bold else SYSTEM_REGULAR
    candidates += [(p, True) for p in sys_list]
    if bold:
        candidates += [(p, False) for p in SYSTEM_REGULAR]

    font = None
    for path, genuine in candidates:
        try:
            f = ImageFont.truetype(path, size)
        except Exception:
            continue
        faux = bold and not genuine
        try:  # variable-шрифты (Roboto[wght].ttf и т.п.)
            f.set_variation_by_name("Bold" if bold else "Regular")
            if bold:
                faux = False
        except Exception:
            pass
        if not _has_cyrillic(f):
            continue
        f._faux = faux
        font = f
        if (path, bold) not in _logged:
            _logged.add((path, bold))
            print(f"[ШРИФТ] {'bold' if bold else 'regular'}: {path}{' (+обводка)' if faux else ''}", flush=True)
        break

    if font is None:
        print(f"[ШРИФТ] ВНИМАНИЕ: не найден шрифт с кириллицей ({'bold' if bold else 'regular'}). "
              f"Положи font.ttf и font_bold.ttf рядом с ботом!", flush=True)
        try:
            font = ImageFont.load_default(size)
        except TypeError:
            font = ImageFont.load_default()
        try:
            font._faux = False
        except Exception:
            pass

    _font_cache[key] = font
    return font


def txt(d, xy, text, font, fill, anchor=None):
    """Рисует текст; если жирного шрифта нет, имитирует его обводкой."""
    kw = {}
    if getattr(font, "_faux", False):
        kw["stroke_width"] = max(1, round(getattr(font, "size", 20) / 40))
        kw["stroke_fill"] = fill
    if anchor:
        kw["anchor"] = anchor
    d.text(xy, text, font=font, fill=fill, **kw)


# ---------- КАРТИНКИ ----------
_bg_cache = {}


def get_bg(path: str, width: int):
    """Фон читается и ресайзится один раз, дальше берётся копия из кэша."""
    key = (path, width)
    if key not in _bg_cache:
        bg = Image.open(path).convert("RGBA")
        s = width / bg.width
        img = bg.resize((width, int(bg.height * s)), Image.LANCZOS)
        _bg_cache[key] = (img, s)
    img, s = _bg_cache[key]
    return img.copy(), s


def format_hm(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    return f"{h} ч {rem // 60} мин"


def circle_avatar(data: bytes, size: int):
    img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    img.putalpha(mask)
    return img


def make_stats_card(name: str, avatar_bytes: bytes, total: int, current: int) -> io.BytesIO:
    img, s = get_bg(BG_PATH, 1000)

    def S(v):
        return int(v * s)

    d = ImageDraw.Draw(img)

    box = (S(600), S(335), S(1380), S(610))
    fill = img.crop((S(330), S(340), S(580), S(560))).resize((1, 1), Image.BOX).getpixel((0, 0))
    d.rectangle(box, fill=fill)

    av_size = S(230)
    av = circle_avatar(avatar_bytes, av_size)
    ax, ay = S(340), S(330)
    img.paste(av, (ax, ay), av)
    d.ellipse((ax, ay, ax + av_size, ay + av_size), outline=(130, 80, 255), width=3)

    txt(d, (S(633), S(270)), name[:22], load_font(S(64), True), (255, 255, 255))
    txt(d, (S(633), S(360)), "Время в голосовых каналах", load_font(S(50)), (135, 125, 175))
    txt(d, (S(633), S(440)), format_hm(total), load_font(S(105), True), (110, 70, 240))
    if current > 0:
        txt(d, (S(633), S(570)), f"в т.ч. текущая сессия: {format_hm(current)}",
            load_font(S(34)), (135, 125, 175))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf


def make_top_card(rows) -> io.BytesIO:
    img, s = get_bg(TOP_BG_PATH, 1935)   # рисуем в «родном» размере макета, потом уменьшаем

    def S(v):
        return int(round(v * s))

    rows = rows[:5]
    n = len(rows)
    pitch = TOP_PITCH if n <= 4 else 70
    first_center = TOP_CENTER_Y - (n - 1) * pitch / 2 + TOP_DY
    baselines = [first_center + 16 + i * pitch for i in range(n)]

    # --- разделительные линии между строками (светлее в центре, к краям гаснут) ---
    if n > 1:
        arr = np.array(img.convert("RGB"), dtype=np.float32)
        x_a, x_b = S(TOP_LINE_X[0] + TOP_DX), S(TOP_LINE_X[1] + TOP_DX)
        xs = np.arange(x_a, x_b)
        cx = (TOP_LINE_CENTER_X + TOP_DX) * s
        alpha_x = np.exp(-((xs - cx) / (330 * s)) ** 2)
        color = np.array(TOP_LINE_COLOR, dtype=np.float32)
        for i in range(n - 1):
            y = S(baselines[i] + 25)
            for dy, w in ((-1, 0.12), (0, 1.0), (1, 0.35)):
                yy = y + dy
                if 0 <= yy < arr.shape[0]:
                    a = (alpha_x * w)[:, None]
                    arr[yy, x_a:x_b] = arr[yy, x_a:x_b] * (1 - a) + color * a
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")

    d = ImageDraw.Draw(img)
    f_rank = load_font(S(45), True)
    f_name = load_font(S(44))
    f_time = load_font(S(43), True)

    for i, ((name, secs), base) in enumerate(zip(rows, baselines), 1):
        y = S(base)
        txt(d, (S(TOP_RANK_X + TOP_DX), y), f"{i}.", f_rank, TOP_RANK_COLOR, anchor="ls")
        txt(d, (S(TOP_NAME_X + TOP_DX), y), name[:20], f_name, TOP_NAME_COLOR, anchor="ls")
        txt(d, (S(TOP_TIME_RIGHT + TOP_DX), y), format_hm(secs), f_time, TOP_TIME_COLOR, anchor="rs")

    out_h = int(img.height * TOP_OUT_W / img.width)
    img = img.convert("RGB").resize((TOP_OUT_W, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def make_card_image(card: dict) -> io.BytesIO:
    """Картинка карточки: берётся из cards/<image>, а если файла нет, рисуется заглушка."""
    path = os.path.join(CARDS_DIR, card.get("image") or "")
    if card.get("image") and os.path.isfile(path):
        img = Image.open(path).convert("RGBA")
        img.thumbnail((800, 1100))
    else:
        W, H = 600, 840
        color = RARITY_COLORS.get(card["rarity"], (150, 155, 160))
        img = Image.new("RGBA", (W, H), (30, 20, 35, 255))
        d = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d.line((0, y, W, y), fill=(int(255 - 60 * t), int(170 - 60 * t), int(205 - 40 * t), 255))
        d.rounded_rectangle((8, 8, W - 8, H - 8), radius=40, outline=color, width=10)
        txt(d, (W // 2, 330), card["name"][:1].upper(), load_font(280, True), (255, 255, 255, 140), anchor="mm")
        txt(d, (W // 2, 60), card["rarity"].upper(), load_font(30, True), color, anchor="mm")
        d.rounded_rectangle((50, 600, W - 50, 790), radius=22, fill=(255, 205, 225), outline=(230, 190, 90), width=6)
        txt(d, (W // 2, 640), CARD_SERIES, load_font(26, True), (200, 80, 130), anchor="mm")
        txt(d, (W // 2, 700), card["name"][:16], load_font(60, True), (205, 70, 125), anchor="mm")
        txt(d, (W // 2, 760), CARD_SEASON, load_font(24, True), (200, 80, 130), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ---------- БЭКАП БАЗЫ В DISCORD ----------
BACKUP_FILENAME = "voice_time.db"
BACKUP_KEEP = 3            # сколько последних копий хранить в канале
_last_backup_hash = None


def _db_score(path: str):
    """Сколько данных в базе (сумма секунд в войсе). None, если файл битый."""
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT COALESCE(SUM(total_seconds), 0) FROM voice_time").fetchone()
        conn.close()
        return row[0]
    except Exception:
        return None


def _snapshot_db(dst: str):
    """Безопасная копия базы (даже если в неё сейчас пишут)."""
    src = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(dst)
    src.backup(dest)
    dest.close()
    src.close()


def _remove_quiet(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


async def _get_backup_channel():
    if not BACKUP_CHANNEL_ID:
        return None
    ch = bot.get_channel(BACKUP_CHANNEL_ID)
    if ch is None:
        ch = await bot.fetch_channel(BACKUP_CHANNEL_ID)
    return ch


async def restore_db_from_discord():
    """При старте: если в канале есть копия базы с большим количеством данных, чем локальная, берём её."""
    if not BACKUP_CHANNEL_ID:
        print("[БЭКАП] BACKUP_CHANNEL_ID не задан, база хранится только на хостинге", flush=True)
        return
    tmp = DB_PATH + ".restore"
    try:
        ch = await _get_backup_channel()
        att = None
        async for msg in ch.history(limit=50):
            if msg.author.id != bot.user.id:
                continue
            att = next((a for a in msg.attachments if a.filename == BACKUP_FILENAME), None)
            if att:
                break
        if not att:
            print("[БЭКАП] в канале пока нет копий базы", flush=True)
            return

        data = await att.read()
        with open(tmp, "wb") as f:
            f.write(data)

        remote = _db_score(tmp)
        local = _db_score(DB_PATH) if os.path.isfile(DB_PATH) else None
        if remote is None:
            print("[БЭКАП] копия в канале повреждена, пропускаю", flush=True)
        elif local is None or remote > local:
            # старые WAL-файлы нужно убрать, иначе они испортят восстановленную базу
            _remove_quiet(DB_PATH + "-wal", DB_PATH + "-shm")
            os.replace(tmp, DB_PATH)
            print(f"[БЭКАП] база восстановлена из Discord (было {local}, стало {remote} сек)", flush=True)
        else:
            print(f"[БЭКАП] локальная база актуальна ({local} сек), восстановление не нужно", flush=True)
    except Exception as e:
        print(f"[БЭКАП] ошибка восстановления: {e!r}", flush=True)
    finally:
        _remove_quiet(tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal")


async def backup_db_to_discord(force: bool = False) -> bool:
    """Заливает копию базы в канал и удаляет старые копии."""
    global _last_backup_hash
    if not BACKUP_CHANNEL_ID or not os.path.isfile(DB_PATH):
        return False
    tmp = DB_PATH + ".backup"
    try:
        await asyncio.to_thread(_snapshot_db, tmp)
        with open(tmp, "rb") as f:
            data = f.read()

        h = hashlib.md5(data).hexdigest()
        if not force and h == _last_backup_hash:
            return True

        ch = await _get_backup_channel()
        await ch.send(content=f"Бэкап базы · {time.strftime('%d.%m.%Y %H:%M:%S')} UTC",
                      file=discord.File(io.BytesIO(data), BACKUP_FILENAME))
        _last_backup_hash = h

        old = []
        async for msg in ch.history(limit=50):
            if msg.author.id == bot.user.id and msg.attachments:
                old.append(msg)
        for msg in old[BACKUP_KEEP:]:
            try:
                await msg.delete()
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[БЭКАП] ошибка бэкапа: {e!r}", flush=True)
        return False
    finally:
        _remove_quiet(tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal")


@tasks.loop(minutes=3)
async def backup_loop():
    await backup_db_to_discord()


@bot.tree.command(name="backup", description="Сделать бэкап базы прямо сейчас (только админы)")
@app_commands.default_permissions(administrator=True)
async def backup_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    if not BACKUP_CHANNEL_ID:
        await interaction.response.send_message("BACKUP_CHANNEL_ID не задан в коде.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok = await backup_db_to_discord(force=True)
    await interaction.followup.send("Бэкап сохранён в канал." if ok else
                                    "Не получилось. Проверь права бота в канале и логи.", ephemeral=True)


# ---------- УЧЁТ ГОЛОСА ----------
def excluded_channels(guild: discord.Guild) -> set:
    ids = set(AFK_CHANNEL_IDS)
    if AFK_CHANNEL_ID:
        ids.add(AFK_CHANNEL_ID)
    if guild.afk_channel:
        ids.add(guild.afk_channel.id)
    return ids


def afk_target(guild: discord.Guild):
    if AFK_CHANNEL_ID:
        ch = guild.get_channel(AFK_CHANNEL_ID)
        if isinstance(ch, discord.VoiceChannel):
            return ch
    return guild.afk_channel


def is_deaf(vs) -> bool:
    return bool(vs and (vs.self_deaf or vs.deaf))


def is_muted(vs) -> bool:
    return bool(vs and (vs.self_mute or vs.mute or vs.self_deaf or vs.deaf))


def pending_seconds(user_id: int, now: float = None) -> int:
    """Сколько секунд накоплено, но ещё не записано в базу."""
    last = voice_sessions.get(user_id)
    if last is None:
        return 0
    return int(min((now or time.time()) - last, MAX_CREDIT))


@tasks.loop(seconds=TICK_SECONDS)
async def voice_tick():
    """
    Раз в TICK_SECONDS:
    - начисляет время только тем, кто не оглушён и сидит в канале НЕ один
      (рядом должен быть хотя бы один другой живой неоглушённый человек);
    - если человек в муте/оглушении дольше AFK_TIMEOUT, перекидывает его в AFK-канал.
    """
    now = time.time()
    credits = []
    to_move = []
    active = set()

    for guild in bot.guilds:
        skip = excluded_channels(guild)
        for vc in guild.voice_channels:
            if vc.id in skip:
                continue
            humans = [m for m in vc.members if not m.bot and m.voice]
            for m in humans:
                vs = m.voice

                # --- активность ---
                # Пока микрофон открыт, считаем, что человек может говорить (бот не слышит голос).
                # Таймер неактивности идёт только пока он в муте/оглушении.
                if not is_muted(vs) or vs.self_stream or vs.self_video:
                    last_active[m.id] = now
                elif now - last_active.setdefault(m.id, now) >= AFK_TIMEOUT:
                    to_move.append(m)
                    continue

                # --- начисление ---
                if is_deaf(vs):
                    continue
                has_company = any(o.id != m.id and not is_deaf(o.voice) for o in humans)
                if not has_company:
                    continue

                active.add(m.id)
                last = voice_sessions.get(m.id)
                if last is None:
                    voice_sessions[m.id] = now
                    session_start[m.id] = now
                else:
                    sec = int(min(now - last, MAX_CREDIT))
                    if sec > 0:
                        credits.append((m.id, guild.id, sec))
                        voice_sessions[m.id] = now

    # те, кому время больше не идёт (остались одни, оглушились, вышли), сбрасываются
    for uid in [u for u in voice_sessions if u not in active]:
        voice_sessions.pop(uid, None)
        session_start.pop(uid, None)

    if credits:
        await asyncio.to_thread(add_time_batch, credits)

    for m in to_move:
        target = afk_target(m.guild)
        if not target:
            continue
        try:
            await m.move_to(target, reason=f"Неактивность в голосовом канале {AFK_TIMEOUT // 60} мин")
            last_active[m.id] = now
            voice_sessions.pop(m.id, None)
            session_start.pop(m.id, None)
            print(f"[AFK] {m.display_name} перемещён в {target.name}")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[AFK] не удалось переместить {m.display_name}: {e}")


@voice_tick.before_loop
async def _before_tick():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global synced, restored
    if not restored:
        await restore_db_from_discord()   # сначала подтягиваем базу из Discord
        restored = True
    init_db()
    os.makedirs(CARDS_DIR, exist_ok=True)

    if not voice_tick.is_running():
        voice_tick.start()

    if BACKUP_CHANNEL_ID and not backup_loop.is_running():
        backup_loop.start()

    if not synced:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        synced = True

    print(f"Бот {bot.user} запущен. Учёт голосового времени активен.")


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = time.time()
    last_active[member.id] = now  # любое изменение (вход, мут, стрим, камера) = активность

    # при выходе/смене канала сразу дописываем накопленное
    if before.channel != after.channel:
        last = voice_sessions.pop(member.id, None)
        session_start.pop(member.id, None)
        if last and before.channel:
            sec = int(min(now - last, MAX_CREDIT))
            if sec > 0:
                await asyncio.to_thread(add_time_batch, [(member.id, before.channel.guild.id, sec)])
                print(f"[-] {member.display_name} +{format_seconds(sec)}")


@bot.listen("on_message")
async def _activity_from_messages(message: discord.Message):
    if not message.author.bot and message.guild:
        last_active[message.author.id] = time.time()


# ---------- КОМАНДЫ: ГОЛОС ----------
@bot.tree.command(name="voice", description="Показать время в голосовых каналах")
@app_commands.describe(member="Участник (по умолчанию вы)")
async def voice_stats(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    member = member or interaction.user
    last_active[interaction.user.id] = time.time()

    now = time.time()
    total = get_time(member.id, interaction.guild.id) + pending_seconds(member.id, now)
    current = int(now - session_start[member.id]) if member.id in session_start else 0

    avatar = await member.display_avatar.replace(size=256, format="png").read()
    buf = await asyncio.to_thread(make_stats_card, member.display_name, avatar, total, current)
    await interaction.followup.send(file=discord.File(buf, "voice.png"))


@bot.tree.command(name="voicetop", description="Топ участников по голосовому времени")
@app_commands.describe(limit="Сколько мест показать (1-5)")
async def voice_top(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 5] = 4):
    await interaction.response.defer()
    last_active[interaction.user.id] = time.time()

    rows = await asyncio.to_thread(get_top, interaction.guild.id, limit)
    if not rows:
        await interaction.followup.send("Пока нет данных.")
        return

    now = time.time()
    data = []
    for uid, secs in rows:
        m = interaction.guild.get_member(uid)
        data.append((m.display_name if m else f"ID {uid}", secs + pending_seconds(uid, now)))
    data.sort(key=lambda x: x[1], reverse=True)

    buf = await asyncio.to_thread(make_top_card, data)
    await interaction.followup.send(file=discord.File(buf, "top.png"))


# ---------- КОМАНДЫ: МОНЕТЫ И КАРТОЧКИ ----------
def live_coins(user_id: int, guild_id: int) -> float:
    """Баланс с учётом ещё не записанных секунд."""
    return get_coins(user_id, guild_id) + pending_seconds(user_id) * COINS_PER_MINUTE / 60


@bot.tree.command(name="balance", description="Показать баланс монет")
@app_commands.describe(member="Участник (по умолчанию вы)")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    last_active[interaction.user.id] = time.time()
    coins = int(live_coins(member.id, interaction.guild.id))
    cards_count = sum(get_inventory(member.id, interaction.guild.id).values())

    embed = discord.Embed(title="💰 Баланс", description=member.mention, color=discord.Color.gold())
    embed.add_field(name="Монеты", value=str(coins), inline=True)
    embed.add_field(name="Карточек", value=str(cards_count), inline=True)
    embed.set_footer(text=f"{COINS_PER_MINUTE} монет за минуту в голосовом канале (не в одиночку)")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="shop", description="Цена карточки и шансы выпадения")
async def shop(interaction: discord.Interaction):
    last_active[interaction.user.id] = time.time()
    coins = int(live_coins(interaction.user.id, interaction.guild.id))
    lines = [f"**{c['name']}** — {c['rarity']}, шанс {card_chance(c):.1f}%"
             for c in sorted(CARDS, key=lambda x: x["weight"], reverse=True)]

    embed = discord.Embed(title="🃏 Магазин карточек", color=discord.Color.pink())
    embed.add_field(name="Цена", value=f"{CARD_PRICE} монет за одну карточку", inline=False)
    embed.add_field(name="Ваш баланс", value=f"{coins} монет", inline=False)
    embed.add_field(name="Шансы", value="\n".join(lines), inline=False)
    embed.set_footer(text="Открыть карточку: /opencard")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="opencard", description="Купить случайную карточку игрока")
async def opencard(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id, guild_id = interaction.user.id, interaction.guild.id
    last_active[user_id] = time.time()

    # сначала записываем накопленное, чтобы баланс был актуальным
    sec = pending_seconds(user_id)
    if sec > 0:
        await asyncio.to_thread(add_time_batch, [(user_id, guild_id, sec)])
        voice_sessions[user_id] = time.time()

    card = roll_card()
    ok = await asyncio.to_thread(buy_card, user_id, guild_id, card["id"], CARD_PRICE)
    if not ok:
        have = int(get_coins(user_id, guild_id))
        await interaction.followup.send(
            f"Не хватает монет: нужно **{CARD_PRICE}**, у вас **{have}**. "
            f"Сидите в голосовом канале (не в одиночку), чтобы заработать больше.")
        return

    buf = await asyncio.to_thread(make_card_image, card)
    left = int(get_coins(user_id, guild_id))
    owned = get_inventory(user_id, guild_id).get(card["id"], 1)
    dup = " (дубликат)" if owned > 1 else " (новая!)"
    await interaction.followup.send(
        content=(f"{interaction.user.mention} открыл карточку: **{card['name']}** — "
                 f"{card['rarity']}{dup}\nШанс выпадения: {card_chance(card):.1f}% · "
                 f"Осталось монет: {left}"),
        file=discord.File(buf, "card.png"))


@bot.tree.command(name="collection", description="Показать коллекцию карточек")
@app_commands.describe(member="Участник (по умолчанию вы)")
async def collection(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    inv = get_inventory(member.id, interaction.guild.id)

    if not inv:
        await interaction.response.send_message(f"У {member.display_name} пока нет карточек.")
        return

    lines = [f"**{c['name']}** — {c['rarity']} ×{inv[c['id']]}" for c in CARDS if c["id"] in inv]

    embed = discord.Embed(title="🗂️ Коллекция", description=member.mention + "\n\n" + "\n".join(lines),
                          color=discord.Color.pink())
    embed.set_footer(text=f"Собрано {len(lines)} из {len(CARDS)}")
    await interaction.response.send_message(embed=embed)


async def card_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=c["name"], value=c["id"])
            for c in CARDS if current.lower() in c["name"].lower()][:25]


@bot.tree.command(name="showcard", description="Показать свою карточку")
@app_commands.describe(card="Какую карточку показать")
@app_commands.autocomplete(card=card_autocomplete)
async def showcard(interaction: discord.Interaction, card: str):
    data = CARDS_BY_ID.get(card)
    if not data:
        await interaction.response.send_message("Такой карточки нет.", ephemeral=True)
        return
    if get_inventory(interaction.user.id, interaction.guild.id).get(card, 0) <= 0:
        await interaction.response.send_message("У вас нет этой карточки.", ephemeral=True)
        return

    await interaction.response.defer()
    buf = await asyncio.to_thread(make_card_image, data)
    await interaction.followup.send(
        content=f"{interaction.user.mention}: **{data['name']}** — {data['rarity']}",
        file=discord.File(buf, "card.png"))


@bot.tree.command(name="addcoins", description="Выдать монеты участнику (только админы)")
@app_commands.describe(member="Кому выдать", amount="Сколько монет (можно отрицательное)")
@app_commands.default_permissions(administrator=True)
async def addcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    give_coins(member.id, interaction.guild.id, amount)
    await interaction.response.send_message(
        f"{member.mention}: {amount:+d} монет. Баланс: {int(get_coins(member.id, interaction.guild.id))}")
    await backup_db_to_discord(force=True)


@bot.tree.command(name="setvoice", description="Задать время в войсе участнику (только админы)")
@app_commands.describe(member="Кому", hours="Часы", minutes="Минуты",
                       coins="Монеты (если не указать, баланс не меняется)")
@app_commands.default_permissions(administrator=True)
async def setvoice(interaction: discord.Interaction, member: discord.Member,
                   hours: int, minutes: int = 0, coins: int = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    seconds = hours * 3600 + minutes * 60
    set_time(member.id, interaction.guild.id, seconds, coins)
    # сбрасываем отсчёт, чтобы не добавилось лишнего
    if member.id in voice_sessions:
        voice_sessions[member.id] = time.time()
    await interaction.response.send_message(
        f"{member.mention}: время задано {hours} ч {minutes} мин."
        + (f" Монеты: {coins}." if coins is not None else ""))
    await backup_db_to_discord(force=True)


# ---------- ЗАПУСК ----------
bot.run(TOKEN)
