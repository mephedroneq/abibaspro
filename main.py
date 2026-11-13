import io
import os
import glob
import json
import math
import time
import hashlib
import random
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import discord
from discord.ext import commands, tasks
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv is not None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------- НАСТРОЙКИ ----------
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Токен не найден! Укажи DISCORD_BOT_TOKEN в настройках бота на Bothost или в .env")
TOKEN = TOKEN.strip().strip('"').strip("'").strip()
print(f"[ТОКЕН] найден, длина: {len(TOKEN)}", flush=True)

# --- AFK ---
AFK_CHANNEL_ID = 0            # ID AFK-комнаты, куда перекидывать (0 = взять "Неактивный канал" из настроек сервера)
AFK_CHANNEL_IDS = []          # дополнительные каналы, где время не считается, например [123456789]
AFK_TIMEOUT = 15 * 60         # через сколько секунд неактивности перекидывать в AFK (15 минут)

TICK_SECONDS = 15             # как часто бот проверяет голосовые каналы
MAX_CREDIT = 60               # максимум секунд, которые можно начислить за один раз (защита от лагов)

# --- ЭКОНОМИКА И ОПЫТ ---
VOICE_COIN_INTERVAL = 5 * 60  # каждые 5 минут в войсе выдаётся награда
VOICE_COINS_MIN = 5           # минимум монет за 5 минут в войсе
VOICE_COINS_MAX = 20          # максимум монет за 5 минут в войсе

MSG_COINS_MIN = 5             # минимум монет за сообщение
MSG_COINS_MAX = 20            # максимум монет за сообщение
MSG_REWARD_COOLDOWN = 60      # кулдаун на получение монет/XP за сообщения (в секундах)

XP_PER_VOICE_MINUTE = 10      # XP за каждую минуту в голосовом канале
XP_PER_MESSAGE = 15           # XP за сообщение (с учётом кулдауна)
COIN_UNIT = "монет"

# --- ПРЕДУПРЕЖДЕНИЯ ---
MAX_WARNS = 3
AUTO_BAN_ON_MAX_WARNS = True  # авто-бан при 3 активных предупреждениях (если у бота есть права)

# --- КАРТОЧКИ ---
CARD_PRICE = 100              # цена одной карточки
CARD_SERIES = "NILSPACE ARCHIVES"    # надпись над ником на карточке
CARD_SEASON = "ALPHA ANOMALY"     # надпись под ником на карточке

BG_PATH = os.path.join(BASE_DIR, "background.png")          # фон для /profile (1536x1024)
TOP_BG_PATH = os.path.join(BASE_DIR, "top_background.png")  # фон для /top (1967x799)
DB_PATH = os.getenv("DB_PATH") or os.path.join(BASE_DIR, "voice_time.db")
JSON_BACKUP_PATH = DB_PATH + ".backup.json"
CARDS_DIR = os.path.join(BASE_DIR, "cards")

# --- БЭКАП В DISCORD (ДЛЯ ПЕРЕЗАГРУЗОК / ПЕРЕЗАЛИВОК НА BOTHOST) ---
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID") or 0)
AUTO_CREATE_BACKUP_CHANNEL = True   # если BACKUP_CHANNEL_ID=0, бот сам найдёт/создаст скрытый канал для бэкапов
BACKUP_CHANNEL_NAME = "bot-db-backup"
BACKUP_CHANNEL_MARKER = "[ABIBAS_DB_BACKUP]"
BACKUP_FILENAME = "voice_time.db"
BACKUP_KEEP = 3
BACKUP_LOOP_SECONDS = 60
BACKUP_DEBOUNCE_SECONDS = 5

# --- Таблица лидеров (/top) ---
TOP_OUT_W = 1600
TOP_RANK_X = 300
TOP_NAME_X = 400
TOP_SUB_RIGHT = 1320
TOP_VAL_RIGHT = 1670
TOP_PITCH = 66
TOP_PITCH_5 = 56
TOP_CENTER_Y = 450
TOP_LINE_CENTER_X = 985
TOP_LINE_X = (290, 1680)
TOP_LINE_COLOR = (170, 30, 30)
TOP_RANK_COLOR = (215, 45, 45)
TOP_NAME_COLOR = (255, 255, 255)
TOP_SUB_COLOR = (165, 165, 175)
TOP_VAL_COLOR = (235, 235, 235)
TOP_DX = 0
TOP_DY = 0

RARITY_COLORS = {
    "Обычная": (150, 155, 160),
    "Редкая": (70, 140, 255),
    "Эпическая": (170, 80, 255),
    "Легендарная": (255, 180, 40),
    "Мифическая": (220, 80, 255),
}

CARDS = [
    {
        "id": "abibaspro",
        "name": "abibaspro",
        "rarity": "Легендарная",
        "weight": 2,
        "image": "abibaspro.png",
        "desc": "Хранитель мира и ядро сервера (Супер редкая)",
        "quote": "«Его присутствие ощущается в каждом блоке этого мира...»"
    },
    {
        "id": "_keral_black_",
        "name": "_keral_black_",
        "rarity": "Мифическая",
        "weight": 1,
        "image": "keral_black.png",
        "desc": "Странник пустоты (Супер редкая аномалия)",
        "quote": "«Тень, смотрящая из невидимых чанков пустоты...»"
    },
    {
        "id": "nil_tree",
        "name": "Древо Безмолвия",
        "rarity": "Эпическая",
        "weight": 12,
        "image": "nil_tree.png",
        "desc": "Объект #001 · Лес туманной памяти (NilSpace ARG)",
        "quote": "«Оно стоит на границе симуляции с первой альфа-версии...»"
    },
    {
        "id": "nil_monolith",
        "name": "Монолиты Тумана",
        "rarity": "Легендарная",
        "weight": 6,
        "image": "nil_monolith.png",
        "desc": "Объект #089 · Сектор нулевой высоты (NilSpace ARG)",
        "quote": "«Геометрия, порождённая критическим сбоем генератора чанков.»"
    },
    {
        "id": "nil_pillars",
        "name": "Бесконечные Колонны",
        "rarity": "Редкая",
        "weight": 22,
        "image": "nil_pillars.png",
        "desc": "Объект #042 · Лабиринт булыжника (NilSpace ARG)",
        "quote": "«Колонны уходят в бесконечность. Звук шагов здесь эхом не возвращается.»"
    },
    {
        "id": "nil_watcher",
        "name": "Заблудший Наблюдатель",
        "rarity": "Эпическая",
        "weight": 14,
        "image": "",
        "desc": "Сущность с белыми глазами в тумане",
        "quote": "«Оно не нападает, оно лишь фиксирует твои действия...»"
    },
    {
        "id": "nil_signal",
        "name": "nil_space.dat",
        "rarity": "Редкая",
        "weight": 22,
        "image": "",
        "desc": "Архивный файл сохранения симуляции",
        "quote": "«Файл повреждён. Попытка чтения вызывает артефакты экрана.»"
    },
    {
        "id": "nil_chunk",
        "name": "Потерянный Чанк",
        "rarity": "Обычная",
        "weight": 40,
        "image": "",
        "desc": "Аномалия генерации · Ошибка 0x404",
        "quote": "«Пустота между мирами. Не заглядывай вниз.»"
    },
    {
        "id": "nil_echo",
        "name": "Эхо Симуляции",
        "rarity": "Обычная",
        "weight": 35,
        "image": "",
        "desc": "Скрытый аудио-сигнал в недрах мира",
        "quote": "«Звуки ломающихся блоков там, где никогда не ступала нога игрока.»"
    },
]
CARDS_BY_ID = {c["id"]: c for c in CARDS}
# --------------------------------

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

voice_sessions = {}   # {user_id: время последнего начисления}
session_start = {}    # {user_id: начало текущей сессии}
last_active = {}      # {user_id: время последней активности}
voice_afk_start = {}  # {user_id: время начала нахождения без разговора / в AFK}

_db_lock = threading.Lock()
_last_backup_hash = None
_backup_debounce_task = None
_active_backup_channel_id = BACKUP_CHANNEL_ID


# ---------- УРОВНИ И ФОРМАТИРОВАНИЕ ----------
def xp_needed_for_level(level: int) -> int:
    """На 6 уровне требуется 580 XP (как в макете 470 / 580 XP)."""
    if level <= 0:
        return 100
    return 100 + level * 80


def fmt_num(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def format_hm(sec: int) -> str:
    h, rem = divmod(max(0, int(sec)), 3600)
    return f"{h} ч {rem // 60} м"


def format_seconds(sec: int) -> str:
    return str(timedelta(seconds=max(0, int(sec))))


# ---------- БАЗА ДАННЫХ ----------
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _bump_revision(conn_or_cur, now: float = None):
    ts = str(int(now or time.time()))
    conn_or_cur.execute("""
        INSERT INTO bot_meta (key, value) VALUES ('revision', '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(COALESCE(CAST(value AS INTEGER), 0) + 1 AS TEXT)
    """)
    conn_or_cur.execute("""
        INSERT INTO bot_meta (key, value) VALUES ('updated_at', ?)
        ON CONFLICT(key) DO UPDATE SET value = ?
    """, (ts, ts))


def _ensure_column(c, table: str, column: str, ddl: str):
    cols = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _apply_xp_delta(c, user_id: int, guild_id: int, xp_gain: int):
    if xp_gain <= 0:
        return
    row = c.execute(
        "SELECT xp, level FROM levels WHERE user_id = ? AND guild_id = ?",
        (user_id, guild_id),
    ).fetchone()
    xp, level = (row[0], row[1]) if row else (0, 0)
    xp += xp_gain
    while xp >= xp_needed_for_level(level):
        xp -= xp_needed_for_level(level)
        level += 1
    c.execute("""
        INSERT INTO levels (user_id, guild_id, xp, level) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, guild_id) DO UPDATE SET xp = ?, level = ?
    """, (user_id, guild_id, xp, level, xp, level))


def init_db():
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with _db_lock:
        conn = _connect()
        try:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("""
                CREATE TABLE IF NOT EXISTS voice_time (
                    user_id INTEGER,
                    guild_id INTEGER,
                    total_seconds INTEGER DEFAULT 0,
                    coin_progress INTEGER DEFAULT 0,
                    xp_progress INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            _ensure_column(c, "voice_time", "coin_progress", "coin_progress INTEGER DEFAULT 0")
            _ensure_column(c, "voice_time", "xp_progress", "xp_progress INTEGER DEFAULT 0")

            c.execute("""
                CREATE TABLE IF NOT EXISTS wallet (
                    user_id INTEGER,
                    guild_id INTEGER,
                    coins REAL DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    user_id INTEGER,
                    guild_id INTEGER,
                    count INTEGER DEFAULT 0,
                    last_reward_at REAL DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS levels (
                    user_id INTEGER,
                    guild_id INTEGER,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
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
            c.execute("""
                CREATE TABLE IF NOT EXISTS marriages (
                    guild_id INTEGER,
                    user_id INTEGER,
                    partner_id INTEGER NOT NULL,
                    married_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    active INTEGER DEFAULT 1,
                    removed_at REAL,
                    removed_by INTEGER
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_warnings_user
                ON warnings (guild_id, user_id, active)
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    profile_bg TEXT DEFAULT 'bg_nil_tree'
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    afk_channel_id INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS bot_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Миграция опыта для аккаунтов из старой базы (у кого есть войс-время, но ещё нет строки в levels)
            legacy_users = c.execute("""
                SELECT v.user_id, v.guild_id, v.total_seconds
                FROM voice_time v
                LEFT JOIN levels l ON l.user_id = v.user_id AND l.guild_id = v.guild_id
                WHERE l.user_id IS NULL AND v.total_seconds >= 60
            """).fetchall()
            for uid, gid, secs in legacy_users:
                legacy_xp = (secs // 60) * XP_PER_VOICE_MINUTE
                _apply_xp_delta(c, uid, gid, legacy_xp)

            conn.commit()
        finally:
            conn.close()


def _export_json_snapshot_unlocked(conn) -> dict:
    def rows_as_dicts(sql):
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    return {
        "exported_at": int(time.time()),
        "bot_meta": rows_as_dicts("SELECT key, value FROM bot_meta"),
        "voice_time": rows_as_dicts("SELECT user_id, guild_id, total_seconds, coin_progress, xp_progress FROM voice_time"),
        "wallet": rows_as_dicts("SELECT user_id, guild_id, coins FROM wallet"),
        "messages": rows_as_dicts("SELECT user_id, guild_id, count, last_reward_at FROM messages"),
        "levels": rows_as_dicts("SELECT user_id, guild_id, xp, level FROM levels"),
        "cards": rows_as_dicts("SELECT user_id, guild_id, card_id, count FROM cards"),
        "marriages": rows_as_dicts("SELECT guild_id, user_id, partner_id, married_at FROM marriages"),
        "warnings": rows_as_dicts("SELECT id, guild_id, user_id, moderator_id, reason, created_at, active, removed_at, removed_by FROM warnings"),
    }


def save_local_json_snapshot():
    if not os.path.isfile(DB_PATH):
        return
    with _db_lock:
        conn = _connect()
        try:
            data = _export_json_snapshot_unlocked(conn)
        finally:
            conn.close()
    tmp = JSON_BACKUP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, JSON_BACKUP_PATH)


def restore_from_json_if_needed():
    """Резервное восстановление из локального JSON, если .db потеряна, а JSON остался."""
    if not os.path.isfile(JSON_BACKUP_PATH):
        return
    if os.path.isfile(DB_PATH) and _db_score(DB_PATH) not in (None, (0, 0)):
        return
    try:
        with open(JSON_BACKUP_PATH, "r", encoding="utf-8") as f:
            snap = json.load(f)
    except Exception:
        return
    init_db()
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                for r in snap.get("voice_time", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO voice_time (user_id, guild_id, total_seconds, coin_progress, xp_progress) VALUES (?, ?, ?, ?, ?)",
                        (r["user_id"], r["guild_id"], r["total_seconds"], r.get("coin_progress", 0), r.get("xp_progress", 0)),
                    )
                for r in snap.get("wallet", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)",
                        (r["user_id"], r["guild_id"], r["coins"]),
                    )
                for r in snap.get("messages", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO messages (user_id, guild_id, count, last_reward_at) VALUES (?, ?, ?, ?)",
                        (r["user_id"], r["guild_id"], r["count"], r.get("last_reward_at", 0)),
                    )
                for r in snap.get("levels", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO levels (user_id, guild_id, xp, level) VALUES (?, ?, ?, ?)",
                        (r["user_id"], r["guild_id"], r["xp"], r["level"]),
                    )
                for r in snap.get("cards", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO cards (user_id, guild_id, card_id, count) VALUES (?, ?, ?, ?)",
                        (r["user_id"], r["guild_id"], r["card_id"], r["count"]),
                    )
                for r in snap.get("marriages", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO marriages (guild_id, user_id, partner_id, married_at) VALUES (?, ?, ?, ?)",
                        (r["guild_id"], r["user_id"], r["partner_id"], r["married_at"]),
                    )
                for r in snap.get("warnings", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO warnings (id, guild_id, user_id, moderator_id, reason, created_at, active, removed_at, removed_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["id"], r["guild_id"], r["user_id"], r["moderator_id"], r["reason"], r["created_at"], r["active"], r.get("removed_at"), r.get("removed_by")),
                    )
                for r in snap.get("bot_meta", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)",
                        (r["key"], r["value"]),
                    )
        finally:
            conn.close()
    print("[БЭКАП] база восстановлена из локального JSON-снимка", flush=True)


def add_time_batch(items):
    """items: [(user_id, guild_id, seconds)]. Начисляет время, монеты (5-20 каждые 5 мин) и XP."""
    if not items:
        return
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                c = conn.cursor()
                for user_id, guild_id, sec in items:
                    if sec <= 0:
                        continue
                    row = c.execute(
                        "SELECT total_seconds, coin_progress, xp_progress FROM voice_time WHERE user_id = ? AND guild_id = ?",
                        (user_id, guild_id),
                    ).fetchone()
                    total_sec, coin_prog, xp_prog = row if row else (0, 0, 0)
                    total_sec += sec
                    coin_prog += sec
                    xp_prog += sec

                    coins_gain = 0
                    while coin_prog >= VOICE_COIN_INTERVAL:
                        coin_prog -= VOICE_COIN_INTERVAL
                        coins_gain += random.randint(VOICE_COINS_MIN, VOICE_COINS_MAX)

                    xp_mins = xp_prog // 60
                    xp_prog = xp_prog % 60
                    xp_gain = xp_mins * XP_PER_VOICE_MINUTE

                    c.execute("""
                        INSERT INTO voice_time (user_id, guild_id, total_seconds, coin_progress, xp_progress)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET
                            total_seconds = ?, coin_progress = ?, xp_progress = ?
                    """, (user_id, guild_id, total_sec, coin_prog, xp_prog, total_sec, coin_prog, xp_prog))

                    if coins_gain > 0:
                        c.execute("""
                            INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                            ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = coins + ?
                        """, (user_id, guild_id, coins_gain, coins_gain))

                    if xp_gain > 0:
                        _apply_xp_delta(c, user_id, guild_id, xp_gain)

                _bump_revision(c)
        finally:
            conn.close()


def record_message(user_id: int, guild_id: int, now: float) -> tuple[int, int]:
    """Увеличивает счётчик сообщений и с кулдауном начисляет 5-20 монет + XP."""
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                c = conn.cursor()
                row = c.execute(
                    "SELECT count, last_reward_at FROM messages WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                ).fetchone()
                count, last_reward = row if row else (0, 0.0)
                count += 1
                coins_gain = 0
                if now - (last_reward or 0.0) >= MSG_REWARD_COOLDOWN:
                    coins_gain = random.randint(MSG_COINS_MIN, MSG_COINS_MAX)
                    last_reward = now
                    c.execute("""
                        INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = coins + ?
                    """, (user_id, guild_id, coins_gain, coins_gain))
                    _apply_xp_delta(c, user_id, guild_id, XP_PER_MESSAGE)

                c.execute("""
                    INSERT INTO messages (user_id, guild_id, count, last_reward_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, guild_id) DO UPDATE SET count = ?, last_reward_at = ?
                """, (user_id, guild_id, count, last_reward, count, last_reward))
                _bump_revision(c, now)
                return count, coins_gain
        finally:
            conn.close()


def get_time(user_id: int, guild_id: int) -> int:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT total_seconds FROM voice_time WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()


def get_coins(user_id: int, guild_id: int) -> float:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            ).fetchone()
            return float(row[0]) if row else 0.0
        finally:
            conn.close()


def get_messages_count(user_id: int, guild_id: int) -> int:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT count FROM messages WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()


def give_coins(user_id: int, guild_id: int, amount: float):
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                    ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = coins + ?
                """, (user_id, guild_id, amount, amount))
                _bump_revision(conn)
        finally:
            conn.close()


def set_time(user_id: int, guild_id: int, seconds: int, coins: float = None):
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO voice_time (user_id, guild_id, total_seconds, coin_progress, xp_progress)
                    VALUES (?, ?, ?, 0, 0)
                    ON CONFLICT(user_id, guild_id) DO UPDATE SET total_seconds = ?
                """, (user_id, guild_id, seconds, seconds))
                if coins is not None:
                    conn.execute("""
                        INSERT INTO wallet (user_id, guild_id, coins) VALUES (?, ?, ?)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET coins = ?
                    """, (user_id, guild_id, coins, coins))
                _bump_revision(conn)
        finally:
            conn.close()


def set_messages(user_id: int, guild_id: int, count: int, xp_total: int = None):
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO messages (user_id, guild_id, count, last_reward_at) VALUES (?, ?, ?, 0)
                    ON CONFLICT(user_id, guild_id) DO UPDATE SET count = ?
                """, (user_id, guild_id, count, count))
                if xp_total is not None:
                    xp = max(0, xp_total)
                    level = 0
                    while xp >= xp_needed_for_level(level):
                        xp -= xp_needed_for_level(level)
                        level += 1
                    conn.execute("""
                        INSERT INTO levels (user_id, guild_id, xp, level) VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET xp = ?, level = ?
                    """, (user_id, guild_id, xp, level, xp, level))
                _bump_revision(conn)
        finally:
            conn.close()


def buy_card(user_id: int, guild_id: int, card_id: str, price: int) -> bool:
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        c = conn.cursor()
        try:
            c.execute("PRAGMA busy_timeout=15000")
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
            _bump_revision(c)
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
    with _db_lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT card_id, count FROM cards WHERE user_id = ? AND guild_id = ? AND count > 0",
                (user_id, guild_id),
            ).fetchall()
            return {cid: cnt for cid, cnt in rows}
        finally:
            conn.close()


def get_partner(guild_id: int, user_id: int):
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT partner_id, married_at FROM marriages WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return row if row else None
        finally:
            conn.close()


def create_marriage(guild_id: int, user1: int, user2: int) -> tuple[bool, str]:
    now = time.time()
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        c = conn.cursor()
        try:
            c.execute("PRAGMA busy_timeout=15000")
            c.execute("BEGIN IMMEDIATE")
            r1 = c.execute("SELECT partner_id FROM marriages WHERE guild_id = ? AND user_id = ?", (guild_id, user1)).fetchone()
            if r1:
                c.execute("ROLLBACK")
                return False, "first_married"
            r2 = c.execute("SELECT partner_id FROM marriages WHERE guild_id = ? AND user_id = ?", (guild_id, user2)).fetchone()
            if r2:
                c.execute("ROLLBACK")
                return False, "second_married"
            c.execute("INSERT INTO marriages (guild_id, user_id, partner_id, married_at) VALUES (?, ?, ?, ?)",
                      (guild_id, user1, user2, now))
            c.execute("INSERT INTO marriages (guild_id, user_id, partner_id, married_at) VALUES (?, ?, ?, ?)",
                      (guild_id, user2, user1, now))
            _bump_revision(c, now)
            c.execute("COMMIT")
            return True, "ok"
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def delete_marriage(guild_id: int, user_id: int):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        c = conn.cursor()
        try:
            c.execute("PRAGMA busy_timeout=15000")
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT partner_id FROM marriages WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
            if not row:
                c.execute("ROLLBACK")
                return None
            partner_id = row[0]
            c.execute("DELETE FROM marriages WHERE guild_id = ? AND user_id IN (?, ?)", (guild_id, user_id, partner_id))
            _bump_revision(c)
            c.execute("COMMIT")
            return partner_id
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> tuple[int, int]:
    now = time.time()
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (guild_id, user_id, moderator_id, reason.strip()[:200], now))
                warn_id = c.lastrowid
                active_cnt = c.execute(
                    "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                    (guild_id, user_id),
                ).fetchone()[0]
                _bump_revision(c, now)
                return warn_id, active_cnt
        finally:
            conn.close()


def remove_warning(guild_id: int, user_id: int, moderator_id: int, warn_id: int = None) -> tuple[bool, int]:
    now = time.time()
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                c = conn.cursor()
                if warn_id is not None:
                    row = c.execute(
                        "SELECT id FROM warnings WHERE id = ? AND guild_id = ? AND user_id = ? AND active = 1",
                        (warn_id, guild_id, user_id),
                    ).fetchone()
                else:
                    row = c.execute(
                        "SELECT id FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                        (guild_id, user_id),
                    ).fetchone()
                if not row:
                    return False, c.execute(
                        "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                        (guild_id, user_id),
                    ).fetchone()[0]
                target_id = row[0]
                c.execute(
                    "UPDATE warnings SET active = 0, removed_at = ?, removed_by = ? WHERE id = ?",
                    (now, moderator_id, target_id),
                )
                active_cnt = c.execute(
                    "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                    (guild_id, user_id),
                ).fetchone()[0]
                _bump_revision(c, now)
                return True, active_cnt
        finally:
            conn.close()


def clear_warnings(guild_id: int, user_id: int, moderator_id: int) -> int:
    now = time.time()
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                c = conn.cursor()
                res = c.execute(
                    "UPDATE warnings SET active = 0, removed_at = ?, removed_by = ? WHERE guild_id = ? AND user_id = ? AND active = 1",
                    (now, moderator_id, guild_id, user_id),
                )
                if res.rowcount > 0:
                    _bump_revision(c, now)
                return res.rowcount
        finally:
            conn.close()


def get_warnings(guild_id: int, user_id: int, limit: int = 10) -> tuple[int, list]:
    with _db_lock:
        conn = _connect()
        try:
            active_cnt = conn.execute(
                "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                (guild_id, user_id),
            ).fetchone()[0]
            rows = conn.execute("""
                SELECT id, moderator_id, reason, created_at, active
                FROM warnings
                WHERE guild_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ?
            """, (guild_id, user_id, limit)).fetchall()
            return active_cnt, rows
        finally:
            conn.close()



def get_user_bg(user_id: int) -> str:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT profile_bg FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
            return row[0] if row and row[0] else "bg_nil_tree"
        finally:
            conn.close()


def set_user_bg(user_id: int, bg_name: str):
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO user_settings (user_id, profile_bg) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET profile_bg = ?
                """, (user_id, bg_name, bg_name))
        finally:
            conn.close()


def get_guild_afk_channel_id(guild_id: int) -> int:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT afk_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
            return int(row[0]) if row and row[0] else 0
        finally:
            conn.close()


def set_guild_afk_channel_id(guild_id: int, channel_id: int):
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO guild_settings (guild_id, afk_channel_id) VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET afk_channel_id = ?
                """, (guild_id, channel_id, channel_id))
        finally:
            conn.close()


def add_card_to_inventory(user_id: int, guild_id: int, card_id: str, count: int = 1) -> int:
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO cards (user_id, guild_id, card_id, count) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, guild_id, card_id) DO UPDATE SET count = count + ?
                """, (user_id, guild_id, card_id, count, count))
                _bump_revision(conn)
                row = conn.execute("SELECT count FROM cards WHERE user_id = ? AND guild_id = ? AND card_id = ?",
                                   (user_id, guild_id, card_id)).fetchone()
                return row[0] if row else count
        finally:
            conn.close()


def remove_card_from_inventory(user_id: int, guild_id: int, card_id: str, count: int = 1) -> tuple[bool, int]:
    with _db_lock:
        conn = _connect()
        try:
            with conn:
                row = conn.execute("SELECT count FROM cards WHERE user_id = ? AND guild_id = ? AND card_id = ?",
                                   (user_id, guild_id, card_id)).fetchone()
                if not row or row[0] <= 0:
                    return False, 0
                cur_count = row[0]
                new_count = max(0, cur_count - count)
                if new_count == 0:
                    conn.execute("DELETE FROM cards WHERE user_id = ? AND guild_id = ? AND card_id = ?",
                                 (user_id, guild_id, card_id))
                else:
                    conn.execute("UPDATE cards SET count = ? WHERE user_id = ? AND guild_id = ? AND card_id = ?",
                                 (new_count, user_id, guild_id, card_id))
                _bump_revision(conn)
                return True, new_count
        finally:
            conn.close()

def get_profile_data(user_id: int, guild_id: int) -> dict:
    with _db_lock:
        conn = _connect()
        try:
            vt = conn.execute("SELECT total_seconds FROM voice_time WHERE user_id = ? AND guild_id = ?",
                              (user_id, guild_id)).fetchone()
            wl = conn.execute("SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?",
                              (user_id, guild_id)).fetchone()
            ms = conn.execute("SELECT count FROM messages WHERE user_id = ? AND guild_id = ?",
                              (user_id, guild_id)).fetchone()
            lv = conn.execute("SELECT xp, level FROM levels WHERE user_id = ? AND guild_id = ?",
                              (user_id, guild_id)).fetchone()
            mr = conn.execute("SELECT partner_id, married_at FROM marriages WHERE guild_id = ? AND user_id = ?",
                              (guild_id, user_id)).fetchone()
            cc = conn.execute("SELECT COALESCE(SUM(count), 0) FROM cards WHERE user_id = ? AND guild_id = ?",
                              (user_id, guild_id)).fetchone()
            active_warns = conn.execute(
                "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                (guild_id, user_id),
            ).fetchone()[0]
            warn_rows = conn.execute("""
                SELECT id, moderator_id, reason, created_at, active
                FROM warnings
                WHERE guild_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT 3
            """, (guild_id, user_id)).fetchall()
        finally:
            conn.close()

    xp, level = (lv[0], lv[1]) if lv else (0, 0)
    return {
        "total_seconds": int(vt[0]) if vt else 0,
        "coins": int(wl[0]) if wl else 0,
        "messages": int(ms[0]) if ms else 0,
        "xp": int(xp),
        "level": int(level),
        "xp_need": xp_needed_for_level(int(level)),
        "partner_id": int(mr[0]) if mr else None,
        "married_at": float(mr[1]) if mr else None,
        "cards_count": int(cc[0]) if cc else 0,
        "active_warns": int(active_warns),
        "warnings": warn_rows,
    }


def get_top_users(guild_id: int) -> list[dict]:
    """Возвращает агрегированную статистику всех участников сервера из базы."""
    with _db_lock:
        conn = _connect()
        try:
            users = {}

            def entry(uid):
                if uid not in users:
                    users[uid] = {
                        "user_id": uid,
                        "coins": 0,
                        "messages": 0,
                        "total_seconds": 0,
                        "level": 0,
                        "xp": 0,
                    }
                return users[uid]

            for uid, coins in conn.execute("SELECT user_id, coins FROM wallet WHERE guild_id = ?", (guild_id,)):
                entry(uid)["coins"] = int(coins or 0)
            for uid, cnt in conn.execute("SELECT user_id, count FROM messages WHERE guild_id = ?", (guild_id,)):
                entry(uid)["messages"] = int(cnt or 0)
            for uid, sec in conn.execute("SELECT user_id, total_seconds FROM voice_time WHERE guild_id = ?", (guild_id,)):
                entry(uid)["total_seconds"] = int(sec or 0)
            for uid, xp, lvl in conn.execute("SELECT user_id, xp, level FROM levels WHERE guild_id = ?", (guild_id,)):
                e = entry(uid)
                e["xp"] = int(xp or 0)
                e["level"] = int(lvl or 0)
            return list(users.values())
        finally:
            conn.close()


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
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]
SYSTEM_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "DejaVuSans.ttf",
]


def _local_fonts(bold: bool):
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
    try:
        a, b = font.getmask("Ж"), font.getmask("Щ")
        return a.size != b.size or a.tobytes() != b.tobytes()
    except Exception:
        return False


def load_font(size: int, bold: bool = False):
    size = max(8, int(size))
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
        try:
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
            print(f"[ШРИФТ] {'bold' if bold else 'regular'}: {path}{' (+жирность)' if faux else ''}", flush=True)
        break

    if font is None:
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


def txt(d: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor=None):
    """Рисует текст; аккуратно имитирует жирный шрифт без размытия мелких букв."""
    kw = {}
    if anchor:
        kw["anchor"] = anchor
    faux = getattr(font, "_faux", False)
    size = getattr(font, "size", 20)
    if faux and size >= 34:
        kw["stroke_width"] = max(1, round(size / 42))
        kw["stroke_fill"] = fill
        d.text(xy, text, font=font, fill=fill, **kw)
    elif faux:
        x, y = xy
        d.text((x, y), text, font=font, fill=fill, **kw)
        d.text((x + 1, y), text, font=font, fill=fill, **kw)
    else:
        d.text(xy, text, font=font, fill=fill, **kw)


def fit_text(d: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int,
             min_size: int = 14, bold: bool = False):
    """Подбирает размер шрифта под ширину и обрезает слишком длинную строку с '…'."""
    for sz in range(start_size, min_size - 1, -1):
        f = load_font(sz, bold=bold)
        bbox = d.textbbox((0, 0), text, font=f)
        if bbox[2] - bbox[0] <= max_width:
            return text, f
    f = load_font(min_size, bold=bold)
    trimmed = text
    while len(trimmed) > 1:
        candidate = trimmed + "…"
        bbox = d.textbbox((0, 0), candidate, font=f)
        if bbox[2] - bbox[0] <= max_width:
            return candidate, f
        trimmed = trimmed[:-1]
    return text[:1], f


# ---------- КАРТИНКИ ----------
_bg_cache = {}


def get_bg(path: str, width: int = None):
    key = (path, width)
    if key not in _bg_cache:
        bg = Image.open(path).convert("RGBA")
        if width is not None and width != bg.width:
            s = width / bg.width
            img = bg.resize((width, int(bg.height * s)), Image.LANCZOS)
        else:
            s = bg.width / 1536.0 if path == BG_PATH else 1.0
            img = bg
        _bg_cache[key] = (img, s)
    img, s = _bg_cache[key]
    return img.copy(), s


def circle_avatar(data: bytes, size: int) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


def rounded_avatar(data: bytes, size: int, radius: int) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _draw_segmented_ring(base_img: Image.Image, cx: int, cy: int,
                         r_inner: int, r_outer: int, progress: float, s: float):
    """Рисует динамическое 8-сегментное кольцо уровня поверх шаблона с 4x сглаживанием."""
    progress = max(0.0, min(1.0, progress))
    pad = int(round((r_outer + 10) * s))
    box_size = pad * 2
    scale = 4
    hi_size = box_size * scale

    c_hi = hi_size // 2
    ro = int(round(r_outer * s * scale))
    ri = int(round(r_inner * s * scale))
    track_bbox = (c_hi - ro, c_hi - ro, c_hi + ro, c_hi + ro)

    # 1. Тёмная подложка под кольцом, чтобы полностью перекрыть статичные сектора фона
    ro_bg = ro + 3 * scale
    ri_bg = ri - 3 * scale
    cover = Image.new("RGBA", (hi_size, hi_size), (10, 8, 10, 240))
    cover_mask = Image.new("L", (hi_size, hi_size), 0)
    cmd = ImageDraw.Draw(cover_mask)
    cmd.ellipse((c_hi - ro_bg, c_hi - ro_bg, c_hi + ro_bg, c_hi + ro_bg), fill=255)
    cmd.ellipse((c_hi - ri_bg, c_hi - ri_bg, c_hi + ri_bg, c_hi + ri_bg), fill=0)
    cover.putalpha(cover_mask)

    # 2. Цветной слой кольца (серый трек + красная дуга прогресса)
    ring_rgb = Image.new("RGBA", (hi_size, hi_size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring_rgb)
    rd.pieslice(track_bbox, start=0, end=360, fill=(64, 64, 70, 255))
    if progress > 0.001:
        end_deg = -90.0 + 360.0 * progress
        rd.pieslice(track_bbox, start=-90.0, end=end_deg, fill=(215, 42, 58, 255))

    # 3. Альфа-маска в режиме "L" (гарантирует прозрачный центр и 8 радиальных зазоров)
    ring_mask = Image.new("L", (hi_size, hi_size), 0)
    md = ImageDraw.Draw(ring_mask)
    md.ellipse(track_bbox, fill=255)
    md.ellipse((c_hi - ri, c_hi - ri, c_hi + ri, c_hi + ri), fill=0)

    gap_half = math.radians(2.2)
    r_cut = ro + 4 * scale
    for k in range(8):
        ang = math.radians(k * 45.0)
        poly = [
            (c_hi, c_hi),
            (c_hi + r_cut * math.cos(ang - gap_half), c_hi + r_cut * math.sin(ang - gap_half)),
            (c_hi + r_cut * math.cos(ang + gap_half), c_hi + r_cut * math.sin(ang + gap_half)),
        ]
        md.polygon(poly, fill=0)

    ring_rgb.putalpha(ring_mask)
    combined = Image.alpha_composite(cover, ring_rgb)
    ring_layer = combined.resize((box_size, box_size), Image.LANCZOS)
    base_img.alpha_composite(ring_layer, (int(round(cx * s)) - pad, int(round(cy * s)) - pad))


def _draw_filled_star(d: ImageDraw.ImageDraw, cx: float, cy: float,
                      r_out: float, r_in: float, fill, outline=None, width: int = 2):
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=width)


def make_profile_card(
    name: str,
    user_id: int,
    avatar_bytes: bytes,
    stats: dict,
    current_voice_session: int = 0,
    partner_name: str = None,
    partner_avatar_bytes: bytes = None,
    mod_names: dict = None,
    bg_key: str = None,
) -> io.BytesIO:
    """Рендерит карточку профиля с прозрачным фоном и идеальным выравниванием."""
    W, H = 1440, 920
    # 1. Полностью прозрачный фон (canvas)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    PANEL_BG = (17, 21, 32, 235)
    PANEL_BORDER = (85, 75, 135, 180)
    ACCENT_PURPLE = (165, 95, 255)
    TEXT_WHITE = (255, 255, 255)
    TEXT_MUTED = (165, 175, 200)
    TEXT_DIM = (125, 135, 160)

    pad_x = 35

    # 1. Верхний статус-бар
    top_y = 30
    d.rounded_rectangle([pad_x, top_y, W - pad_x, top_y + 55], radius=14, fill=(19, 23, 36, 225), outline=PANEL_BORDER, width=2)
    txt(d, (pad_x + 25, top_y + 27), "NILSPACE ARCHIVES // СУБЪЕКТ СЕТИ", load_font(21, True), ACCENT_PURPLE, anchor="lm")
    txt(d, (W - pad_x - 25, top_y + 27), "● СТАТУС: АКТИВЕН", load_font(19, True), (95, 230, 155), anchor="rm")

    # 2. Основной блок пользователя
    usr_y1, usr_y2 = 105, 385
    d.rounded_rectangle([pad_x, usr_y1, W - pad_x, usr_y2], radius=18, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    av_sz = 210
    av_x, av_y = pad_x + 30, usr_y1 + 35
    if avatar_bytes:
        try:
            av_img = rounded_avatar(avatar_bytes, av_sz, 16)
        except Exception:
            av_img = Image.new("RGBA", (av_sz, av_sz), (60, 90, 140, 255))
    else:
        av_img = Image.new("RGBA", (av_sz, av_sz), (60, 90, 140, 255))
    img.paste(av_img, (av_x, av_y), av_img)
    d.rounded_rectangle([av_x, av_y, av_x + av_sz, av_y + av_sz], radius=16, outline=ACCENT_PURPLE, width=3)

    info_x = av_x + av_sz + 35
    clean_name, f_name = fit_text(d, name, 600, 44, 26, bold=True)
    txt(d, (info_x, usr_y1 + 38), clean_name, f_name, TEXT_WHITE, anchor="lt")
    txt(d, (info_x, usr_y1 + 92), f"ID: {user_id}", load_font(20), TEXT_MUTED, anchor="lt")

    badge_w, badge_h = 190, 32
    badge_y = usr_y1 + 126
    d.rounded_rectangle([info_x, badge_y, info_x + badge_w, badge_y + badge_h], radius=8, fill=(130, 55, 240, 80), outline=ACCENT_PURPLE, width=1)
    txt(d, (info_x + badge_w // 2, badge_y + badge_h // 2), "ИССЛЕДОВАТЕЛЬ", load_font(17, True), (235, 205, 255), anchor="mm")

    lvl_w, lvl_h = 210, 115
    lvl_x = W - pad_x - 30 - lvl_w
    lvl_y = usr_y1 + 35
    d.rounded_rectangle([lvl_x, lvl_y, lvl_x + lvl_w, lvl_y + lvl_h], radius=14, fill=(24, 28, 44, 240), outline=ACCENT_PURPLE, width=2)
    txt(d, (lvl_x + lvl_w // 2, lvl_y + 35), "УРОВЕНЬ", load_font(19, True), (185, 165, 240), anchor="mm")
    txt(d, (lvl_x + lvl_w // 2, lvl_y + 75), str(stats.get("level", 0)), load_font(44, True), TEXT_WHITE, anchor="mm")

    xp = stats.get("xp", 0)
    xp_need = max(1, stats.get("xp_need", 100))
    pct = min(1.0, max(0.0, xp / xp_need))

    bar_x1 = info_x
    bar_x2 = W - pad_x - 30
    bar_y = usr_y1 + 225

    txt(d, (bar_x1, bar_y - 12), f"Опыт: {fmt_num(xp)} / {fmt_num(xp_need)} XP ({int(pct*100)}%)", load_font(19, True), (225, 230, 245), anchor="lb")
    txt(d, (bar_x2, bar_y - 12), f"До след. уровня: {fmt_num(max(0, xp_need - xp))} XP", load_font(17), TEXT_MUTED, anchor="rb")

    bar_h = 24
    d.rounded_rectangle([bar_x1, bar_y, bar_x2, bar_y + bar_h], radius=12, fill=(30, 35, 52, 240), outline=(65, 75, 105), width=1)
    fill_w = max(bar_h, int((bar_x2 - bar_x1) * pct))
    if xp > 0:
        d.rounded_rectangle([bar_x1, bar_y, min(bar_x2, bar_x1 + fill_w), bar_y + bar_h], radius=12, fill=ACCENT_PURPLE)

    # 3. 5 Плашек статистики
    grid_y1, grid_y2 = 405, 595
    grid_w = W - 2 * pad_x
    num_cards = 5
    card_spacing = 16
    c_w = (grid_w - (num_cards - 1) * card_spacing) // num_cards

    coins_val = f"{fmt_num(stats.get('coins', 0))} монет"
    msg_val = fmt_num(stats.get("messages", 0))
    voice_val = format_hm(stats.get("total_seconds", 0))
    cards_val = f"{stats.get('cards_count', 0)} шт"

    stat_boxes = [
        ("БАЛАНС", coins_val, (255, 215, 80), "🪙"),
        ("СООБЩЕНИЯ", msg_val, (110, 205, 255), "💬"),
        ("В ВОЙСЕ", voice_val, (130, 240, 160), "🎙️"),
        ("КАРТОЧЕК", cards_val, (255, 130, 200), "🎴"),
    ]

    for i, (title, val_str, val_col, icon) in enumerate(stat_boxes):
        cx1 = pad_x + i * (c_w + card_spacing)
        cx2 = cx1 + c_w
        d.rounded_rectangle([cx1, grid_y1, cx2, grid_y2], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)
        txt(d, (cx1 + c_w // 2, grid_y1 + 45), f"{icon} {title}", load_font(18, True), TEXT_MUTED, anchor="mm")
        
        val_fitted, f_val = fit_text(d, val_str, c_w - 20, 25, 16, bold=True)
        txt(d, (cx1 + c_w // 2, grid_y1 + 115), val_fitted, f_val, val_col, anchor="mm")
        
        if title == "В ВОЙСЕ" and current_voice_session > 0:
            sess_str = f"сессия: {format_hm(current_voice_session)}"
            txt(d, (cx1 + c_w // 2, grid_y1 + 155), sess_str, load_font(15), (245, 105, 125), anchor="mm")

    # 5-я карточка: Партнёр
    p_x1 = pad_x + 4 * (c_w + card_spacing)
    p_x2 = p_x1 + c_w
    d.rounded_rectangle([p_x1, grid_y1, p_x2, grid_y2], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)
    txt(d, (p_x1 + c_w // 2, grid_y1 + 45), "💍 ПАРТНЁР", load_font(18, True), TEXT_MUTED, anchor="mm")
    if partner_name:
        if partner_avatar_bytes:
            try:
                pav = circle_avatar(partner_avatar_bytes, 44)
                img.paste(pav, (p_x1 + 15, grid_y1 + 95), pav)
                d.ellipse([p_x1 + 15, grid_y1 + 95, p_x1 + 59, grid_y1 + 139], outline=(220, 70, 120), width=2)
                p_clean, f_p = fit_text(d, partner_name, c_w - 75, 21, 15, bold=True)
                txt(d, (p_x1 + 68, grid_y1 + 117), p_clean, f_p, (255, 185, 215), anchor="lm")
            except Exception:
                p_clean, f_p = fit_text(d, partner_name, c_w - 20, 23, 16, bold=True)
                txt(d, (p_x1 + c_w // 2, grid_y1 + 115), p_clean, f_p, (255, 185, 215), anchor="mm")
        else:
            p_clean, f_p = fit_text(d, partner_name, c_w - 20, 23, 16, bold=True)
            txt(d, (p_x1 + c_w // 2, grid_y1 + 115), p_clean, f_p, (255, 185, 215), anchor="mm")
    else:
        txt(d, (p_x1 + c_w // 2, grid_y1 + 105), "Нет пары", load_font(23, True), TEXT_MUTED, anchor="mm")
        txt(d, (p_x1 + c_w // 2, grid_y1 + 145), "/marry", load_font(16), TEXT_DIM, anchor="mm")

    # 4. Блок предупреждений
    w_y1, w_y2 = 615, 890
    d.rounded_rectangle([pad_x, w_y1, W - pad_x, w_y2], radius=18, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    active_warns = min(3, max(0, stats.get("active_warns", 0)))
    warn_col = (95, 230, 155) if active_warns == 0 else (245, 75, 90)

    txt(d, (pad_x + 35, w_y1 + 45), "⚠️ ПРЕДУПРЕЖДЕНИЯ", load_font(22, True), TEXT_WHITE, anchor="lm")

    right_x = W - pad_x - 35
    txt(d, (right_x, w_y1 + 45), f"АКТИВНЫХ: {active_warns} / {MAX_WARNS}", load_font(20, True), warn_col, anchor="rm")

    dots_start_x = right_x - 210
    for idx in range(MAX_WARNS):
        dx = dots_start_x - (2 - idx) * 36
        dy = w_y1 + 45
        fill_col = (235, 55, 75) if idx < active_warns else (40, 45, 65)
        d.ellipse([dx - 11, dy - 11, dx + 11, dy + 11], fill=fill_col, outline=(255, 110, 130) if idx < active_warns else (75, 85, 115), width=2)

    d.line([(pad_x + 35, w_y1 + 85), (W - pad_x - 35, w_y1 + 85)], fill=(55, 65, 95, 160), width=1)

    warnings_list = stats.get("warnings") or []
    if not warnings_list:
        txt(d, (W // 2, w_y1 + 175), "История предупреждений чиста · Замечаний не зафиксировано", load_font(21), TEXT_MUTED, anchor="mm")
    else:
        for i, (w_id, mod_id, reason, created_at, is_active) in enumerate(warnings_list[:3]):
            dt_str = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%d.%m.%Y")
            mod_label = (mod_names or {}).get(mod_id) or f"ID {mod_id}"
            status_tag = " [АКТИВНО]" if is_active else " [СНЯТО]"
            # ПРИЧИНА УДАЛЕНА: выводим аккуратную запись без текста причины
            line = f"• Предупреждение #{w_id} от {dt_str} — Модератор: {mod_label}{status_tag}"
            col = (255, 190, 195) if is_active else TEXT_MUTED
            txt(d, (pad_x + 35, w_y1 + 125 + i * 44), line, load_font(19), col, anchor="lm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def make_top_card(rows: list[dict], category: str = "balance", names: dict = None) -> io.BytesIO:
    """Рендерит картинку /top на top_background.png."""
    img, _ = get_bg(TOP_BG_PATH, 1967)
    s = 1.0
    names = names or {}

    def S(v):
        return int(round(v * s))

    rows = rows[:5]
    n = len(rows)
    pitch = TOP_PITCH if n <= 4 else TOP_PITCH_5
    first_center = TOP_CENTER_Y - (n - 1) * pitch / 2 + TOP_DY
    baselines = [first_center + 14 + i * pitch for i in range(n)]

    if n > 1:
        arr = np.array(img.convert("RGB"), dtype=np.float32)
        x_a, x_b = S(TOP_LINE_X[0] + TOP_DX), S(TOP_LINE_X[1] + TOP_DX)
        xs = np.arange(x_a, x_b)
        cx = (TOP_LINE_CENTER_X + TOP_DX) * s
        alpha_x = np.exp(-((xs - cx) / (420 * s)) ** 2)
        color = np.array(TOP_LINE_COLOR, dtype=np.float32)
        for i in range(n - 1):
            y = S(baselines[i] + pitch * 0.3)
            for dy, w in ((-1, 0.12), (0, 1.0), (1, 0.35)):
                yy = y + dy
                if 0 <= yy < arr.shape[0]:
                    a = (alpha_x * w)[:, None]
                    arr[yy, x_a:x_b] = arr[yy, x_a:x_b] * (1 - a) + color * a
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")

    d = ImageDraw.Draw(img)
    titles = {
        "balance": "ТОП ПО БАЛАНСУ",
        "messages": "ТОП ПО СООБЩЕНИЯМ",
        "voice": "ТОП ПО ГОЛОСОВОМУ ВРЕМЕНИ",
        "level": "ТОП ПО УРОВНЮ",
    }
    txt(d, (S(1725), S(268)), titles.get(category, "ТОП ПО БАЛАНСУ"),
        load_font(S(26), True), (225, 70, 75), anchor="rs")

    f_rank = load_font(S(38), True)
    f_name = load_font(S(36))
    f_sub = load_font(S(24))
    f_val = load_font(S(36), True)

    for i, (row, base) in enumerate(zip(rows, baselines), 1):
        y = S(base)
        uid = row["user_id"]
        display = names.get(uid) or f"ID {uid}"
        txt(d, (S(TOP_RANK_X + TOP_DX), y), f"{i}.", f_rank, TOP_RANK_COLOR, anchor="ls")
        clean_disp, f_n = fit_text(d, display, S(480), S(36), S(22), bold=False)
        txt(d, (S(TOP_NAME_X + TOP_DX), y), clean_disp, f_n, TOP_NAME_COLOR, anchor="ls")

        if category == "balance":
            val_text = f"{fmt_num(row['coins'])} {COIN_UNIT}"
            sub_text = f"{fmt_num(row['messages'])} сообщ. · {format_hm(row['total_seconds'])}"
        elif category == "messages":
            val_text = f"{fmt_num(row['messages'])} сообщ."
            sub_text = f"{fmt_num(row['coins'])} {COIN_UNIT} · {format_hm(row['total_seconds'])}"
        elif category == "voice":
            val_text = format_hm(row["total_seconds"])
            sub_text = f"{fmt_num(row['coins'])} {COIN_UNIT} · {fmt_num(row['messages'])} сообщ."
        else:
            val_text = f"Ур. {row['level']} ({fmt_num(row['xp'])} XP)"
            sub_text = f"{fmt_num(row['coins'])} {COIN_UNIT} · {fmt_num(row['messages'])} сообщ."

        txt(d, (S(TOP_SUB_RIGHT + TOP_DX), y), sub_text, f_sub, TOP_SUB_COLOR, anchor="rs")
        txt(d, (S(TOP_VAL_RIGHT + TOP_DX), y), val_text, f_val, TOP_VAL_COLOR, anchor="rs")

    out_h = int(img.height * TOP_OUT_W / img.width)
    img = img.convert("RGB").resize((TOP_OUT_W, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def make_card_image(card: dict) -> io.BytesIO:
    path = os.path.join(CARDS_DIR, card.get("image") or "")
    color = RARITY_COLORS.get(card.get("rarity", "Обычная"), (150, 155, 160))

    if card.get("image") and os.path.isfile(path):
        img = Image.open(path).convert("RGBA")
        # Сохраняем карточку в высоком качестве
        img.thumbnail((1000, 1400), Image.Resampling.LANCZOS)
    else:
        # Процедурная генерация карточки в стиле NilSpace ARG
        W, H = 800, 1120
        img = Image.new("RGBA", (W, H), (14, 16, 22, 255))
        d = ImageDraw.Draw(img)

        # Фоновый градиент пустоты
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=(int(18 + 15 * t), int(14 + 10 * t), int(26 + 25 * t), 255))

        # Scanlines
        for y in range(0, H, 4):
            d.line([(0, y), (W, y)], fill=(0, 0, 0, 40), width=1)

        # Рамка карточки с цветом редкости
        d.rounded_rectangle([20, 20, W - 20, H - 20], radius=24, outline=color, width=6)
        d.rounded_rectangle([30, 30, W - 30, H - 30], radius=18, outline=(color[0]//2, color[1]//2, color[2]//2, 160), width=2)

        # Заголовок серии
        txt(d, (W // 2, 70), "NILSPACE ARCHIVES // CLASSIFIED", load_font(24, True), (180, 185, 205), anchor="mm")
        txt(d, (W // 2, 105), card.get("desc", "Секретный объект аномалии"), load_font(20), (140, 145, 170), anchor="mm")
        d.line([(80, 135), (W - 80, 135)], fill=color, width=2)

        # Большой символ по центру
        txt(d, (W // 2, H // 2 - 40), card["name"][:1].upper(), load_font(220, True), (255, 255, 255, 45), anchor="mm")

        # Нижний информационный блок
        d.rectangle([50, H - 320, W - 50, H - 50], fill=(12, 14, 20, 220), outline=color, width=2)
        txt(d, (W // 2, H - 270), card["name"][:20], load_font(40, True), (255, 255, 255), anchor="mm")
        txt(d, (W // 2, H - 215), f"★ {card['rarity'].upper()} КАРТОЧКА ★", load_font(26, True), color, anchor="mm")

        quote = card.get("quote", "«Архивная запись зашифрована протоколом симуляции...»")
        txt(d, (W // 2, H - 155), quote, load_font(19), (195, 200, 215), anchor="mm")
        txt(d, (W // 2, H - 95), "[АРХИВ NILSPACE]", load_font(18, True), (120, 125, 145), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ---------- БЭКАП И СОХРАНЕНИЕ БАЗЫ ПРИ ПЕРЕЗАПУСКЕ ----------
def _db_score(path: str):
    """Оценка актуальности базы по (revision, суммарная активность). None, если файл битый."""
    try:
        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not tables:
            conn.close()
            return None

        rev = 0
        if "bot_meta" in tables:
            row = conn.execute("SELECT value FROM bot_meta WHERE key = 'revision'").fetchone()
            if row and row[0] and str(row[0]).isdigit():
                rev = int(row[0])

        score = 0
        if "voice_time" in tables:
            score += int(conn.execute("SELECT COALESCE(SUM(total_seconds), 0) FROM voice_time").fetchone()[0] or 0)
        if "wallet" in tables:
            score += int((conn.execute("SELECT COALESCE(SUM(coins), 0) FROM wallet").fetchone()[0] or 0) * 10)
        if "messages" in tables:
            score += int((conn.execute("SELECT COALESCE(SUM(count), 0) FROM messages").fetchone()[0] or 0) * 10)
        if "levels" in tables:
            score += int(conn.execute("SELECT COALESCE(SUM(xp + level * 1000), 0) FROM levels").fetchone()[0] or 0)
        if "cards" in tables:
            score += int((conn.execute("SELECT COALESCE(SUM(count), 0) FROM cards").fetchone()[0] or 0) * 100)
        if "marriages" in tables:
            score += int(conn.execute("SELECT COUNT(*) FROM marriages").fetchone()[0] or 0) * 100
        if "warnings" in tables:
            score += int(conn.execute("SELECT COUNT(*) FROM warnings").fetchone()[0] or 0) * 100

        conn.close()
        return (rev, score)
    except Exception:
        return None


def _snapshot_db(dst: str):
    """Потокобезопасная согласованная копия базы (сбрасывая WAL)."""
    with _db_lock:
        src = _connect()
        dest = sqlite3.connect(dst)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            src.backup(dest)
        finally:
            dest.close()
            src.close()


def _checkpoint_db_sync():
    if not os.path.isfile(DB_PATH):
        return
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()


def _remove_quiet(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


async def _resolve_backup_channel(bot_ref):
    global _active_backup_channel_id
    cid = _active_backup_channel_id or BACKUP_CHANNEL_ID
    if cid:
        ch = bot_ref.get_channel(cid)
        if ch is None:
            try:
                ch = await bot_ref.fetch_channel(cid)
            except Exception:
                ch = None
        if ch is not None:
            _active_backup_channel_id = ch.id
            return ch

    # Автопоиск существующего канала бэкапа (полезно при полном сбросе контейнера на Bothost)
    for guild in bot_ref.guilds:
        for ch in guild.text_channels:
            if (ch.topic and BACKUP_CHANNEL_MARKER in ch.topic) or ch.name == BACKUP_CHANNEL_NAME:
                _active_backup_channel_id = ch.id
                print(f"[БЭКАП] найден канал бэкапа #{ch.name} ({ch.id})", flush=True)
                return ch

    if AUTO_CREATE_BACKUP_CHANNEL and bot_ref.guilds:
        guild = bot_ref.guilds[0]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True, read_message_history=True
            ),
        }
        try:
            ch = await guild.create_text_channel(
                BACKUP_CHANNEL_NAME,
                overwrites=overwrites,
                topic=f"{BACKUP_CHANNEL_MARKER} Автосохранение базы данных бота при перезагрузках",
                reason="Автосоздание скрытого канала для сохранения аккаунтов при перезапусках",
            )
            _active_backup_channel_id = ch.id
            print(f"[БЭКАП] создан скрытый канал #{ch.name} ({ch.id})", flush=True)
            return ch
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[БЭКАП] не удалось автосоздать канал бэкапа (задайте BACKUP_CHANNEL_ID вручную): {e}", flush=True)

    return None


async def restore_db_from_discord(bot_ref):
    global _last_backup_hash
    ch = await _resolve_backup_channel(bot_ref)
    if not ch:
        print("[БЭКАП] канал бэкапа не найден, работаем с локальной базой", flush=True)
        return
    tmp = DB_PATH + ".restore"
    try:
        att = None
        async for msg in ch.history(limit=50):
            if msg.author.id != bot_ref.user.id:
                continue
            att = next((a for a in msg.attachments if a.filename == BACKUP_FILENAME), None)
            if att:
                break
        if not att:
            print("[БЭКАП] в канале пока нет сохранённых копий базы", flush=True)
            return

        data = await att.read()
        with open(tmp, "wb") as f:
            f.write(data)

        remote = _db_score(tmp)
        local = _db_score(DB_PATH) if os.path.isfile(DB_PATH) else None
        if remote is None:
            print("[БЭКАП] копия в канале повреждена, пропускаю", flush=True)
        elif local is None or remote > local:
            with _db_lock:
                _remove_quiet(DB_PATH + "-wal", DB_PATH + "-shm")
                os.replace(tmp, DB_PATH)
            if remote[0] > 0:
                _last_backup_hash = hashlib.md5(data).hexdigest()
            print(f"[БЭКАП] база восстановлена из Discord (было {local}, стало {remote})", flush=True)
        else:
            print(f"[БЭКАП] локальная база актуальна ({local}), восстановление не нужно", flush=True)
    except Exception as e:
        print(f"[БЭКАП] ошибка восстановления: {e!r}", flush=True)
    finally:
        _remove_quiet(tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal")


async def backup_db_to_discord(bot_ref, force: bool = False) -> bool:
    global _last_backup_hash
    if not os.path.isfile(DB_PATH):
        return False
    ch = await _resolve_backup_channel(bot_ref)
    if not ch:
        return False

    tmp = DB_PATH + ".backup"
    try:
        await asyncio.to_thread(_snapshot_db, tmp)
        await asyncio.to_thread(save_local_json_snapshot)
        with open(tmp, "rb") as f:
            data = f.read()

        h = hashlib.md5(data).hexdigest()
        if not force and h == _last_backup_hash:
            return True

        score = _db_score(tmp)
        rev = score[0] if score else 0
        await ch.send(
            content=f"Бэкап базы · rev `{rev}` · {time.strftime('%d.%m.%Y %H:%M:%S')} UTC",
            file=discord.File(io.BytesIO(data), BACKUP_FILENAME),
        )
        _last_backup_hash = h

        old = []
        async for msg in ch.history(limit=50):
            if msg.author.id == bot_ref.user.id and msg.attachments:
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


def schedule_backup():
    """Планирует быстрый бэкап через BACKUP_DEBOUNCE_SECONDS без спама."""
    global _backup_debounce_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _backup_debounce_task and not _backup_debounce_task.done():
        return

    async def _worker():
        await asyncio.sleep(BACKUP_DEBOUNCE_SECONDS)
        await backup_db_to_discord(bot, force=False)

    _backup_debounce_task = loop.create_task(_worker())


def flush_active_voice_sessions_sync():
    """Сбрасывает все незаписанные голосовые секунды в SQLite перед выключением или чтением."""
    now = time.time()
    credits = []
    for uid, last in list(voice_sessions.items()):
        sec = int(min(now - last, MAX_CREDIT))
        guild_id = None
        for g in bot.guilds:
            m = g.get_member(uid)
            if m and m.voice and m.voice.channel:
                guild_id = g.id
                break
        if sec > 0 and guild_id is not None:
            credits.append((uid, guild_id, sec))
            voice_sessions[uid] = now
    if credits:
        add_time_batch(credits)


# ---------- БОТ С ГРАЦИОЗНЫМ СОХРАНЕНИЕМ ПРИ ОСТАНОВКЕ ----------
class AbibasBot(commands.Bot):
    async def close(self):
        print("[ОСТАНОВКА] Сохраняю активные сессии и базу данных...", flush=True)
        try:
            await asyncio.to_thread(flush_active_voice_sessions_sync)
            await asyncio.to_thread(_checkpoint_db_sync)
            await asyncio.to_thread(save_local_json_snapshot)
            await backup_db_to_discord(self, force=True)
        except Exception as e:
            print(f"[ОСТАНОВКА] ошибка финального сохранения: {e!r}", flush=True)
        await super().close()


bot = AbibasBot(command_prefix=commands.when_mentioned, intents=intents)
synced = False
restored = False


@tasks.loop(seconds=BACKUP_LOOP_SECONDS)
async def backup_loop():
    await backup_db_to_discord(bot, force=False)


# ---------- УЧЁТ ГОЛОСА ----------
def excluded_channels(guild: discord.Guild) -> set:
    ids = set(AFK_CHANNEL_IDS)
    if AFK_CHANNEL_ID:
        ids.add(AFK_CHANNEL_ID)
    g_cid = get_guild_afk_channel_id(guild.id)
    if g_cid:
        ids.add(g_cid)
    if guild.afk_channel:
        ids.add(guild.afk_channel.id)
    for ch in guild.voice_channels:
        n = ch.name.lower()
        if "afk" in n or "афк" in n:
            ids.add(ch.id)
    return ids


def afk_target(guild: discord.Guild):
    # 1. Индивидуально настроенный канал через /setafkchannel
    g_cid = get_guild_afk_channel_id(guild.id)
    if g_cid:
        ch = guild.get_channel(g_cid)
        if isinstance(ch, discord.VoiceChannel):
            return ch
    # 2. Глобальный ID из настроек бота
    if AFK_CHANNEL_ID:
        ch = guild.get_channel(AFK_CHANNEL_ID)
        if isinstance(ch, discord.VoiceChannel):
            return ch
    # 3. Официальный неактивный канал Discord в настройках сервера
    if guild.afk_channel:
        return guild.afk_channel
    # 4. Авто-поиск любого голосового канала со словом 'afk' или 'афк'
    for ch in guild.voice_channels:
        n = ch.name.lower()
        if "afk" in n or "афк" in n:
            return ch
    return None


def is_deaf(vs) -> bool:
    return bool(vs and (vs.self_deaf or vs.deaf))


def is_muted(vs) -> bool:
    return bool(vs and (vs.self_mute or vs.mute or vs.self_deaf or vs.deaf))


def pending_seconds(user_id: int, now: float = None) -> int:
    last = voice_sessions.get(user_id)
    if last is None:
        return 0
    return int(min((now or time.time()) - last, MAX_CREDIT))


@tasks.loop(seconds=TICK_SECONDS)
async def voice_tick():
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
                is_silent = is_muted(vs) or is_deaf(vs)
                has_company = any(o.id != m.id and not is_deaf(o.voice) and not is_muted(o.voice) for o in humans)

                # Условие AFK без разговора: микрофон замьючен, либо сидит в канале один без говорящих собеседников (и не стримит)
                if is_silent or (not has_company and not vs.self_stream and not vs.self_video):
                    afk_started = voice_afk_start.setdefault(m.id, now)
                    if now - afk_started >= AFK_TIMEOUT:
                        to_move.append(m)
                        continue
                else:
                    # Человек говорит, не замьючен и не один — активен!
                    voice_afk_start.pop(m.id, None)
                    last_active[m.id] = now

                if is_deaf(vs):
                    continue
                if not any(o.id != m.id and not is_deaf(o.voice) for o in humans):
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

    for uid in [u for u in voice_sessions if u not in active]:
        voice_sessions.pop(uid, None)
        session_start.pop(uid, None)

    if credits:
        await asyncio.to_thread(add_time_batch, credits)

    for m in to_move:
        target = afk_target(m.guild)
        voice_afk_start.pop(m.id, None)
        voice_sessions.pop(m.id, None)
        session_start.pop(m.id, None)
        if target:
            try:
                await m.move_to(target, reason=f"АФК без разговора {AFK_TIMEOUT // 60} мин")
                print(f"[AFK] {m.display_name} перемещён в {target.name} (15 минут без разговора)", flush=True)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"[AFK] не удалось переместить {m.display_name}: {e}", flush=True)


@voice_tick.before_loop
async def _before_tick():
    await bot.wait_until_ready()


@backup_loop.before_loop
async def _before_backup():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global synced, restored
    if not restored:
        restore_from_json_if_needed()
        await restore_db_from_discord(bot)
        restored = True
    init_db()
    os.makedirs(CARDS_DIR, exist_ok=True)

    if not voice_tick.is_running():
        voice_tick.start()

    if not backup_loop.is_running():
        backup_loop.start()

    if not synced:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        synced = True

    print(f"Бот {bot.user} запущен. Профили, войс, сообщения и автосохранение активны.", flush=True)


@bot.event
async def on_guild_join(guild: discord.Guild):
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = time.time()
    # Если участник отключился от войса:
    if not after.channel:
        voice_afk_start.pop(member.id, None)
        voice_sessions.pop(member.id, None)
        session_start.pop(member.id, None)
    # Если участник включил микрофон (размьютился):
    elif after.self_mute is False and after.mute is False:
        voice_afk_start.pop(member.id, None)
        last_active[member.id] = now

    if before.channel != after.channel:
        voice_afk_start.pop(member.id, None)
        last = voice_sessions.pop(member.id, None)
        session_start.pop(member.id, None)
        if last and before.channel:
            sec = int(min(now - last, MAX_CREDIT))
            if sec > 0:
                await asyncio.to_thread(add_time_batch, [(member.id, before.channel.guild.id, sec)])
                schedule_backup()


@bot.listen("on_message")
async def _activity_from_messages(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    now = time.time()
    last_active[message.author.id] = now
    _, coins_gain = await asyncio.to_thread(record_message, message.author.id, message.guild.id, now)
    if coins_gain > 0:
        schedule_backup()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ОШИБКА КОМАНДЫ] {error!r}", flush=True)
    msg = "Команда доступна только на сервере." if isinstance(error, app_commands.NoPrivateMessage) else "Произошла ошибка при выполнении команды."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ---------- КОМАНДЫ: ПРОФИЛЬ И ТОП ----------
@bot.tree.command(name="profile", description="Показать карточку профиля участника")
@app_commands.guild_only()
@app_commands.describe(member="Участник (по умолчанию вы)")
async def profile_cmd(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    member = member or interaction.user
    last_active[interaction.user.id] = time.time()

    # Сбросим накопленные секунды пользователя перед отрисовкой, чтобы баланс/XP были точными
    now = time.time()
    sec = pending_seconds(member.id, now)
    if sec > 0:
        await asyncio.to_thread(add_time_batch, [(member.id, interaction.guild.id, sec)])
        voice_sessions[member.id] = now

    stats = await asyncio.to_thread(get_profile_data, member.id, interaction.guild.id)
    current = int(now - session_start[member.id]) if member.id in session_start else 0

    avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()

    partner_name = None
    partner_avatar_bytes = None
    if stats["partner_id"]:
        p_member = interaction.guild.get_member(stats["partner_id"])
        if p_member is None:
            try:
                p_member = await bot.fetch_user(stats["partner_id"])
            except Exception:
                p_member = None
        if p_member:
            partner_name = p_member.display_name
            try:
                partner_avatar_bytes = await p_member.display_avatar.replace(size=128, format="png").read()
            except Exception:
                partner_avatar_bytes = None
        else:
            partner_name = f"ID {stats['partner_id']}"

    mod_names = {}
    for _, mod_id, _, _, _ in stats["warnings"]:
        if mod_id not in mod_names:
            mm = interaction.guild.get_member(mod_id)
            if mm:
                mod_names[mod_id] = mm.display_name
            else:
                try:
                    u = await bot.fetch_user(mod_id)
                    mod_names[mod_id] = u.display_name
                except Exception:
                    mod_names[mod_id] = f"ID {mod_id}"

    user_bg = await asyncio.to_thread(get_user_bg, member.id)
    buf = await asyncio.to_thread(
        make_profile_card,
        member.display_name,
        member.id,
        avatar_bytes,
        stats,
        current,
        partner_name,
        partner_avatar_bytes,
        mod_names,
        user_bg,
    )
    await interaction.followup.send(file=discord.File(buf, "profile.png"))


@bot.tree.command(name="top", description="Топ участников (по умолчанию по балансу)")
@app_commands.guild_only()
@app_commands.describe(
    category="По какому показателю сортировать",
    limit="Сколько мест показать (1-5)",
)
@app_commands.choices(category=[
    app_commands.Choice(name="Баланс", value="balance"),
    app_commands.Choice(name="Сообщения", value="messages"),
    app_commands.Choice(name="Время в войсе", value="voice"),
    app_commands.Choice(name="Уровень", value="level"),
])
async def top_cmd(
    interaction: discord.Interaction,
    category: str = "balance",
    limit: app_commands.Range[int, 1, 5] = 5,
):
    await interaction.response.defer()
    last_active[interaction.user.id] = time.time()

    await asyncio.to_thread(flush_active_voice_sessions_sync)
    rows = await asyncio.to_thread(get_top_users, interaction.guild.id)
    if not rows:
        await interaction.followup.send("Пока нет данных.")
        return

    if category == "balance":
        rows.sort(key=lambda r: (r["coins"], r["messages"], r["total_seconds"]), reverse=True)
    elif category == "messages":
        rows.sort(key=lambda r: (r["messages"], r["coins"], r["total_seconds"]), reverse=True)
    elif category == "voice":
        rows.sort(key=lambda r: (r["total_seconds"], r["coins"], r["messages"]), reverse=True)
    else:
        rows.sort(key=lambda r: (r["level"], r["xp"], r["coins"]), reverse=True)

    top_rows = rows[:limit]
    names = {}
    for r in top_rows:
        uid = r["user_id"]
        m = interaction.guild.get_member(uid)
        if m:
            names[uid] = m.display_name
        else:
            try:
                u = await bot.fetch_user(uid)
                names[uid] = u.display_name
            except Exception:
                names[uid] = f"ID {uid}"

    buf = await asyncio.to_thread(make_top_card, top_rows, category, names)
    await interaction.followup.send(file=discord.File(buf, "top.png"))


# ---------- КОМАНДЫ: ПАРЫ (/marry, /divorce) ----------
class MarryProposalView(discord.ui.View):
    def __init__(self, guild_id: int, proposer: discord.Member, target: discord.Member):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.proposer = proposer
        self.target = target
        self.done = False
        self.message = None

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                f"Ответить на предложение может только {self.target.mention}.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="💍 Принять", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True

        ok, reason = await asyncio.to_thread(
            create_marriage, self.guild_id, self.proposer.id, self.target.id
        )
        if not ok:
            msg = (
                f"{self.proposer.mention} уже состоит в паре."
                if reason == "first_married"
                else f"{self.target.mention} уже состоит в паре."
            )
            embed = discord.Embed(title="💔 Не удалось создать пару", description=msg, color=discord.Color.red())
            await interaction.response.edit_message(embed=embed, view=self)
            return

        embed = discord.Embed(
            title="💖 Новая пара!",
            description=f"{self.proposer.mention} и {self.target.mention} теперь пара!\nОтображается в блоке партнёра в `/profile`.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        schedule_backup()

    @discord.ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="💔 Предложение отклонено",
            description=f"{self.target.mention} отклонил(а) предложение от {self.proposer.mention}.",
            color=discord.Color.dark_gray(),
        )
        await interaction.response.edit_message(embed=embed, view=self)


class DivorceConfirmView(discord.ui.View):
    def __init__(self, guild_id: int, user: discord.Member, partner_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.user = user
        self.partner_id = partner_id
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Подтвердить может только автор команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💔 Расторгнуть пару", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        pid = await asyncio.to_thread(delete_marriage, self.guild_id, self.user.id)
        if pid is None:
            await interaction.response.edit_message(content="Вы уже не состоите в паре.", embed=None, view=self)
            return
        embed = discord.Embed(
            title="💔 Пара распущена",
            description=f"{self.user.mention} и <@{pid}> больше не в паре.",
            color=discord.Color.dark_red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        schedule_backup()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Отменено.", embed=None, view=self)


@bot.tree.command(name="marry", description="Предложить участнику стать парой (отображается в /profile)")
@app_commands.guild_only()
@app_commands.describe(member="Кому сделать предложение")
async def marry_cmd(interaction: discord.Interaction, member: discord.Member):
    last_active[interaction.user.id] = time.time()
    if member.bot:
        await interaction.response.send_message("Нельзя создать пару с ботом.", ephemeral=True)
        return
    if member.id == interaction.user.id:
        await interaction.response.send_message("Нельзя создать пару с самим собой.", ephemeral=True)
        return

    p1 = await asyncio.to_thread(get_partner, interaction.guild.id, interaction.user.id)
    if p1:
        await interaction.response.send_message(
            f"Вы уже в паре с <@{p1[0]}>. Сначала используйте `/divorce`.", ephemeral=True
        )
        return
    p2 = await asyncio.to_thread(get_partner, interaction.guild.id, member.id)
    if p2:
        await interaction.response.send_message(
            f"{member.display_name} уже состоит в паре с <@{p2[0]}>.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="💍 Предложение пары",
        description=f"{interaction.user.mention} предлагает {member.mention} стать парой!\nНажмите кнопку ниже, чтобы ответить.",
        color=discord.Color.red(),
    )
    view = MarryProposalView(interaction.guild.id, interaction.user, member)
    await interaction.response.send_message(content=member.mention, embed=embed, view=view)
    view.message = await interaction.original_response()


@bot.tree.command(name="divorce", description="Расторгнуть текущую пару")
@app_commands.guild_only()
async def divorce_cmd(interaction: discord.Interaction):
    last_active[interaction.user.id] = time.time()
    partner = await asyncio.to_thread(get_partner, interaction.guild.id, interaction.user.id)
    if not partner:
        await interaction.response.send_message("Вы сейчас не состоите в паре.", ephemeral=True)
        return
    embed = discord.Embed(
        title="💔 Расторжение пары",
        description=f"Вы уверены, что хотите расторгнуть пару с <@{partner[0]}>?",
        color=discord.Color.orange(),
    )
    view = DivorceConfirmView(interaction.guild.id, interaction.user, partner[0])
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------- КОМАНДЫ: МОНЕТЫ И КАРТОЧКИ ----------
@bot.tree.command(name="balance", description="Показать баланс, сообщения, войс и карточки")
@app_commands.guild_only()
@app_commands.describe(member="Участник (по умолчанию вы)")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    last_active[interaction.user.id] = time.time()

    now = time.time()
    sec = pending_seconds(member.id, now)
    if sec > 0:
        await asyncio.to_thread(add_time_batch, [(member.id, interaction.guild.id, sec)])
        voice_sessions[member.id] = now

    stats = await asyncio.to_thread(get_profile_data, member.id, interaction.guild.id)
    partner_txt = f"<@{stats['partner_id']}>" if stats["partner_id"] else "Нет пары"

    embed = discord.Embed(title="💰 Баланс и статистика", description=member.mention, color=discord.Color.gold())
    embed.add_field(name="Монеты", value=f"{fmt_num(stats['coins'])} {COIN_UNIT}", inline=True)
    embed.add_field(name="Сообщения", value=fmt_num(stats["messages"]), inline=True)
    embed.add_field(name="В войсе", value=format_hm(stats["total_seconds"]), inline=True)
    embed.add_field(name="Уровень", value=f"{stats['level']} ({stats['xp']}/{stats['xp_need']} XP)", inline=True)
    embed.add_field(name="Карточек", value=str(stats["cards_count"]), inline=True)
    embed.add_field(name="Партнёр", value=partner_txt, inline=True)
    embed.set_footer(
        text=f"Войс: {VOICE_COINS_MIN}-{VOICE_COINS_MAX} монет / 5 мин · Сообщения: {MSG_COINS_MIN}-{MSG_COINS_MAX} монет (КД {MSG_REWARD_COOLDOWN}с)"
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="shop", description="Цена карточки и шансы выпадения")
@app_commands.guild_only()
async def shop(interaction: discord.Interaction):
    last_active[interaction.user.id] = time.time()
    coins = int(await asyncio.to_thread(get_coins, interaction.user.id, interaction.guild.id))
    lines = [f"**{c['name']}** — {c['rarity']}, шанс {card_chance(c):.1f}%"
             for c in sorted(CARDS, key=lambda x: x["weight"], reverse=True)]

    embed = discord.Embed(title="🃏 Магазин карточек", color=discord.Color.pink())
    embed.add_field(name="Цена", value=f"{CARD_PRICE} монет за одну карточку", inline=False)
    embed.add_field(name="Ваш баланс", value=f"{fmt_num(coins)} монет", inline=False)
    embed.add_field(name="Шансы", value="\n".join(lines), inline=False)
    embed.set_footer(text="Открыть карточку: /opencard")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="opencard", description="Купить случайную карточку игрока")
@app_commands.guild_only()
async def opencard(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id, guild_id = interaction.user.id, interaction.guild.id
    last_active[user_id] = time.time()

    sec = pending_seconds(user_id)
    if sec > 0:
        await asyncio.to_thread(add_time_batch, [(user_id, guild_id, sec)])
        voice_sessions[user_id] = time.time()

    card = roll_card()
    ok = await asyncio.to_thread(buy_card, user_id, guild_id, card["id"], CARD_PRICE)
    if not ok:
        have = int(await asyncio.to_thread(get_coins, user_id, guild_id))
        await interaction.followup.send(
            f"Не хватает монет: нужно **{CARD_PRICE}**, у вас **{fmt_num(have)}**. "
            f"Общайтесь в чатах или сидите в войсе (не в одиночку), чтобы заработать больше.")
        return

    buf = await asyncio.to_thread(make_card_image, card)
    left = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    owned = (await asyncio.to_thread(get_inventory, user_id, guild_id)).get(card["id"], 1)
    dup = " (дубликат)" if owned > 1 else " (новая!)"
    await interaction.followup.send(
        content=(f"{interaction.user.mention} открыл карточку: **{card['name']}** — "
                 f"{card['rarity']}{dup}\nШанс выпадения: {card_chance(card):.1f}% · "
                 f"Осталось монет: {fmt_num(left)}"),
        file=discord.File(buf, "card.png"))
    schedule_backup()


@bot.tree.command(name="collection", description="Показать коллекцию карточек")
@app_commands.guild_only()
@app_commands.describe(member="Участник (по умолчанию вы)")
async def collection(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    inv = await asyncio.to_thread(get_inventory, member.id, interaction.guild.id)

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
@app_commands.guild_only()
@app_commands.describe(card="Какую карточку показать")
@app_commands.autocomplete(card=card_autocomplete)
async def showcard(interaction: discord.Interaction, card: str):
    data = CARDS_BY_ID.get(card)
    if not data:
        await interaction.response.send_message("Такой карточки нет.", ephemeral=True)
        return
    inv = await asyncio.to_thread(get_inventory, interaction.user.id, interaction.guild.id)
    if inv.get(card, 0) <= 0:
        await interaction.response.send_message("У вас нет этой карточки.", ephemeral=True)
        return

    await interaction.response.defer()
    buf = await asyncio.to_thread(make_card_image, data)
    await interaction.followup.send(
        content=f"{interaction.user.mention}: **{data['name']}** — {data['rarity']}",
        file=discord.File(buf, "card.png"))


# ---------- КОМАНДЫ: ПРЕДУПРЕЖДЕНИЯ ----------
@bot.tree.command(name="warns", description="Посмотреть предупреждения участника")
@app_commands.guild_only()
@app_commands.describe(member="Участник (по умолчанию вы)")
async def warns_cmd(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    active_cnt, rows = await asyncio.to_thread(get_warnings, interaction.guild.id, member.id, 10)
    if not rows:
        await interaction.response.send_message(f"У {member.mention} история предупреждений пуста (0/{MAX_WARNS}).")
        return

    lines = []
    for wid, mod_id, reason, created_at, active in rows:
        dt = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        mark = "🔴 Активно" if active else "⚪ Снято"
        lines.append(f"**#{wid}** · {mark} · `{dt}`\nПричина: {reason} (модератор: <@{mod_id}>)")

    embed = discord.Embed(
        title=f"⚠️ Предупреждения ({active_cnt} / {MAX_WARNS})",
        description=f"{member.mention}\n\n" + "\n\n".join(lines),
        color=discord.Color.red() if active_cnt > 0 else discord.Color.dark_gray(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warn", description="Выдать предупреждение участнику (модераторы/админы)")
@app_commands.guild_only()
@app_commands.describe(member="Кому выдать", reason="Причина предупреждения")
@app_commands.default_permissions(moderate_members=True)
async def warn_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "Нарушение правил"):
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.moderate_members):
        await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        return
    if member.bot:
        await interaction.response.send_message("Нельзя выдать предупреждение боту.", ephemeral=True)
        return

    wid, active_cnt = await asyncio.to_thread(add_warning, interaction.guild.id, member.id, interaction.user.id, reason)
    extra = ""
    if active_cnt >= MAX_WARNS and AUTO_BAN_ON_MAX_WARNS:
        try:
            await member.ban(reason=f"Достигнуто {MAX_WARNS}/{MAX_WARNS} активных предупреждений. Последнее: {reason}")
            extra = f"\n🔨 Участник забанен автоматически ({active_cnt}/{MAX_WARNS})."
        except (discord.Forbidden, discord.HTTPException):
            extra = f"\n⚠️ Достигнут лимит {MAX_WARNS}/{MAX_WARNS}, но у бота нет прав для бана."

    await interaction.response.send_message(
        f"⚠️ {member.mention} получил(а) предупреждение **#{wid}** (`{active_cnt}/{MAX_WARNS}`)\n"
        f"Причина: **{reason}**{extra}"
    )
    schedule_backup()


@bot.tree.command(name="unwarn", description="Снять активное предупреждение (модераторы/админы)")
@app_commands.guild_only()
@app_commands.describe(member="У кого снять", warn_id="ID предупреждения (если не указать — последнее)")
@app_commands.default_permissions(moderate_members=True)
async def unwarn_cmd(interaction: discord.Interaction, member: discord.Member, warn_id: int = None):
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.moderate_members):
        await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        return
    ok, active_cnt = await asyncio.to_thread(remove_warning, interaction.guild.id, member.id, interaction.user.id, warn_id)
    if not ok:
        await interaction.response.send_message("Подходящего активного предупреждения не найдено.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅ С {member.mention} снято предупреждение. Активных сейчас: **{active_cnt}/{MAX_WARNS}**."
    )
    schedule_backup()


@bot.tree.command(name="clearwarns", description="Снять все активные предупреждения участника (только админы)")
@app_commands.guild_only()
@app_commands.describe(member="У кого снять")
@app_commands.default_permissions(administrator=True)
async def clearwarns_cmd(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    cnt = await asyncio.to_thread(clear_warnings, interaction.guild.id, member.id, interaction.user.id)
    await interaction.response.send_message(f"✅ У {member.mention} снято активных предупреждений: **{cnt}**.")
    if cnt:
        schedule_backup()



# ---------- КОМАНДЫ: КАСТОМИЗАЦИЯ И AFK НАСТРОЙКИ ----------
@bot.tree.command(name="profilebg", description="🎨 Выбрать атмосферный стиль фона для карточки профиля")
@app_commands.guild_only()
@app_commands.describe(theme="Стиль фона")
@app_commands.choices(theme=[
    app_commands.Choice(name="Древо Безмолвия (NilSpace ARG - по умолчанию)", value="bg_nil_tree"),
    app_commands.Choice(name="Бесконечные Колонны (NilSpace ARG)", value="bg_nil_pillars"),
    app_commands.Choice(name="Монолиты Тумана (NilSpace ARG)", value="bg_nil_monolith"),
    app_commands.Choice(name="Классический фон", value="default"),
])
async def profilebg_cmd(interaction: discord.Interaction, theme: app_commands.Choice[str]):
    await asyncio.to_thread(set_user_bg, interaction.user.id, theme.value)
    names = {
        "bg_nil_tree": "🌲 Древо Безмолвия (NilSpace ARG)",
        "bg_nil_pillars": "🏛️ Бесконечные Колонны (NilSpace ARG)",
        "bg_nil_monolith": "🌫️ Монолиты Тумана (NilSpace ARG)",
        "default": "🏙️ Классический фон",
    }
    await interaction.response.send_message(
        f"✅ Стиль фона профиля успешно изменён на: **{names.get(theme.value, theme.name)}**!\
Посмотреть: `/profile`",
        ephemeral=True
    )


@bot.tree.command(name="setafkchannel", description="👑 Установить голосовой AFK-канал для сервера (администраторы)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="Голосовой канал, куда перемещать AFK участников")
async def setafkchannel_cmd(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только администраторы могут настраивать AFK-канал.", ephemeral=True)
        return
    if channel is None:
        await asyncio.to_thread(set_guild_afk_channel_id, interaction.guild.id, 0)
        await interaction.response.send_message("ℹ️ Настройка сброшена. Бот будет использовать системный AFK-канал или поиск по слову 'AFK'.")
    else:
        await asyncio.to_thread(set_guild_afk_channel_id, interaction.guild.id, channel.id)
        await interaction.response.send_message(f"✅ AFK-канал успешно установлен: {channel.mention}!\
Участники без разговора будут перемещаться сюда через 15 минут.")


# ---------- КОМАНДЫ: АДМИНИСТРИРОВАНИЕ КАРТОЧЕК ----------
@bot.tree.command(name="givecard", description="👑 Выдать карточку игроку (только администраторы)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="Кому выдать карточку", card="Какую карточку", count="Количество штук")
@app_commands.autocomplete(card=card_autocomplete)
async def givecard_cmd(interaction: discord.Interaction, member: discord.Member, card: str, count: int = 1):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        return
    data = CARDS_BY_ID.get(card)
    if not data:
        await interaction.response.send_message("Такой карточки не существует.", ephemeral=True)
        return
    count = max(1, min(count, 100))
    new_total = await asyncio.to_thread(add_card_to_inventory, member.id, interaction.guild.id, card, count)
    schedule_backup()
    await interaction.response.send_message(
        f"👑 Администратор {interaction.user.mention} выдал карточку **{data['name']}** ({data['rarity']}) "
        f"участнику {member.mention} (+{count} шт.)! Всего у игрока: **{new_total}** шт."
    )


@bot.tree.command(name="takecard", description="👑 Забрать карточку у игрока (только администраторы)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="У кого забрать карточку", card="Какую карточку", count="Количество штук")
@app_commands.autocomplete(card=card_autocomplete)
async def takecard_cmd(interaction: discord.Interaction, member: discord.Member, card: str, count: int = 1):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        return
    data = CARDS_BY_ID.get(card)
    if not data:
        await interaction.response.send_message("Такой карточки не существует.", ephemeral=True)
        return
    count = max(1, min(count, 100))
    ok, remaining = await asyncio.to_thread(remove_card_from_inventory, member.id, interaction.guild.id, card, count)
    schedule_backup()
    if not ok:
        await interaction.response.send_message(f"У участника {member.mention} нет карточки **{data['name']}**.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"👑 Администратор {interaction.user.mention} изъял карточку **{data['name']}** (-{count} шт.) "
            f"у {member.mention}. Осталось: **{remaining}** шт."
        )


# ---------- КОМАНДЫ: КАЗИНО И АЗАРТНЫЕ ИГРЫ ----------
class CrashGameView(discord.ui.View):
    def __init__(self, user: discord.Member, bet: int, guild_id: int):
        super().__init__(timeout=60)
        self.user = user
        self.bet = bet
        self.guild_id = guild_id
        self.multiplier = 1.00
        self.cashed_out = False
        self.crashed = False

    @discord.ui.button(label="💰 Забрать куш!", style=discord.ButtonStyle.success)
    async def cash_out(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
        if btn_interaction.user.id != self.user.id:
            await btn_interaction.response.send_message("Это не ваша игра!", ephemeral=True)
            return
        if self.cashed_out or self.crashed:
            await btn_interaction.response.send_message("Игра уже завершена!", ephemeral=True)
            return

        self.cashed_out = True
        self.stop()
        winnings = int(self.bet * self.multiplier)
        await asyncio.to_thread(give_coins, self.user.id, self.guild_id, winnings)
        schedule_backup()

        new_bal = int(await asyncio.to_thread(get_coins, self.user.id, self.guild_id))
        button.disabled = True
        button.label = f"Выигрыш: {fmt_num(winnings)} монет!"
        embed = discord.Embed(
            title="🚀 РАКЕТА УСПЕШНО ПОКИНУЛА ОРБИТУ!",
            description=(
                f"{self.user.mention} вовремя нажал кнопку и забрал выигрыш!\n\n"
                f"📈 Итоговый множитель: **x{self.multiplier:.2f}**\n"
                f"💰 Ставка: **{fmt_num(self.bet)}** монет\n"
                f"🎉 Выигрыш: **+{fmt_num(winnings)}** монет!\n"
                f"💳 Новый баланс: **{fmt_num(new_bal)}** монет"
            ),
            color=discord.Color.green()
        )
        await btn_interaction.response.edit_message(embed=embed, view=self)


@bot.tree.command(name="crash", description="🚀 Краш-игра: следи за множителем и забери куш вовремя!")
@app_commands.guild_only()
@app_commands.describe(bet="Сумма ставки в монетах")
async def crash_cmd(interaction: discord.Interaction, bet: int):
    user_id, guild_id = interaction.user.id, interaction.guild.id
    if bet < 10:
        await interaction.response.send_message("Минимальная ставка — 10 монет.", ephemeral=True)
        return

    cur_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    if bet > cur_bal:
        await interaction.response.send_message(
            f"Недостаточно монет! Ваша ставка: **{fmt_num(bet)}**, а баланс: **{fmt_num(cur_bal)}** монет.",
            ephemeral=True
        )
        return

    # Списываем ставку
    await asyncio.to_thread(give_coins, user_id, guild_id, -bet)
    schedule_backup()

    # Точка краша (случайный коэффициент с шансом взрыва в любой момент)
    crash_point = round(random.expovariate(0.65) + 1.05, 2)
    view = CrashGameView(interaction.user, bet, guild_id)

    embed = discord.Embed(
        title="🚀 Запуск ракеты...",
        description=(
            f"Игрок: {interaction.user.mention}\n"
            f"Ставка: **{fmt_num(bet)}** монет\n\n"
            f"📈 Текущий множитель: **x1.00**\n"
            f"Нажмите зелёную кнопку, чтобы забрать выигрыш до взрыва!"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()

    mult = 1.00
    while mult < crash_point:
        await asyncio.sleep(1.4)
        if view.cashed_out:
            return
        mult = round(mult + random.uniform(0.15, 0.45), 2)
        if mult >= crash_point:
            break
        view.multiplier = mult
        embed.description = (
            f"Игрок: {interaction.user.mention}\n"
            f"Ставка: **{fmt_num(bet)}** монет\n\n"
            f"📈 Текущий множитель: **x{mult:.2f}**\n"
            f"Потенциальный куш: **{fmt_num(int(bet * mult))}** монет!\n\n"
            f"Нажмите кнопку внизу, пока ракета не взорвалась!"
        )
        try:
            await msg.edit(embed=embed, view=view)
        except Exception:
            break

    if not view.cashed_out:
        view.crashed = True
        view.stop()
        new_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
        for item in view.children:
            item.disabled = True
        embed_loss = discord.Embed(
            title="💥 КРАШ! РАКЕТА ВЗОРВАЛАСЬ!",
            description=(
                f"{interaction.user.mention} не успел забрать куш!\n\n"
                f"💥 Взрыв произошёл на отметке: **x{crash_point:.2f}**\n"
                f"💸 Потеряно: **-{fmt_num(bet)}** монет\n"
                f"💳 Текущий баланс: **{fmt_num(new_bal)}** монет"
            ),
            color=discord.Color.red()
        )
        try:
            await msg.edit(embed=embed_loss, view=view)
        except Exception:
            pass


@bot.tree.command(name="slots", description="🎰 Игровой автомат: испытай удачу и сорви джекпот!")
@app_commands.guild_only()
@app_commands.describe(bet="Сумма ставки в монетах")
async def slots_cmd(interaction: discord.Interaction, bet: int):
    user_id, guild_id = interaction.user.id, interaction.guild.id
    if bet < 5:
        await interaction.response.send_message("Минимальная ставка — 5 монет.", ephemeral=True)
        return

    cur_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    if bet > cur_bal:
        await interaction.response.send_message(
            f"Недостаточно монет! Ваша ставка: **{fmt_num(bet)}**, а баланс: **{fmt_num(cur_bal)}** монет.",
            ephemeral=True
        )
        return

    # Списываем ставку
    await asyncio.to_thread(give_coins, user_id, guild_id, -bet)

    symbols = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    weights = [30, 25, 20, 14, 8, 3]

    r1 = random.choices(symbols, weights=weights, k=1)[0]
    r2 = random.choices(symbols, weights=weights, k=1)[0]
    r3 = random.choices(symbols, weights=weights, k=1)[0]

    # Расчет выигрыша
    if r1 == r2 == r3 == "7️⃣":
        mult = 15.0
        title = "🔥 СУПЕР ДЖЕКПОТ 777! 🔥"
    elif r1 == r2 == r3 == "💎":
        mult = 10.0
        title = "💎 БРИЛЛИАНТОВЫЙ КУШ! 💎"
    elif r1 == r2 == r3 == "🔔":
        mult = 6.0
        title = "🔔 ЗОЛОТОЙ ЗВОН! 🔔"
    elif r1 == r2 == r3:
        mult = 4.0
        title = "🎉 ТРИ В РЯД! 🎉"
    elif r1 == r2 or r2 == r3:
        mult = 1.5
        title = "✨ ПАРА! Небольшой выигрыш!"
    else:
        mult = 0.0
        title = "💀 НЕ ПОВЕЗЛО..."

    winnings = int(bet * mult)
    if winnings > 0:
        await asyncio.to_thread(give_coins, user_id, guild_id, winnings)
    schedule_backup()

    new_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    diff = winnings - bet

    color = discord.Color.gold() if winnings > bet else (discord.Color.green() if winnings == bet else discord.Color.red())
    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name="Результат барабанов",
        value=f"```\n[  {r1}  |  {r2}  |  {r3}  ]\n```",
        inline=False
    )
    if winnings > 0:
        embed.add_field(name="Множитель", value=f"x{mult:.1f}", inline=True)
        embed.add_field(name="Выигрыш", value=f"+{fmt_num(winnings)} монет", inline=True)
        embed.add_field(name="Прибыль", value=f"+{fmt_num(diff)} монет", inline=True)
    else:
        embed.add_field(name="Итог", value=f"Потеряно: -{fmt_num(bet)} монет", inline=True)

    embed.add_field(name="Баланс", value=f"{fmt_num(new_bal)} монет", inline=False)
    embed.set_footer(text=f"Игрок: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="coinflip", description="🪙 Подбросить монетку (Орёл или Решка, выигрыш x2)")
@app_commands.guild_only()
@app_commands.describe(bet="Сумма ставки", choice="Орёл или Решка")
@app_commands.choices(choice=[
    app_commands.Choice(name="Орёл", value="orel"),
    app_commands.Choice(name="Решка", value="reshka"),
])
async def coinflip_cmd(interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
    user_id, guild_id = interaction.user.id, interaction.guild.id
    if bet < 5:
        await interaction.response.send_message("Минимальная ставка — 5 монет.", ephemeral=True)
        return

    cur_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    if bet > cur_bal:
        await interaction.response.send_message(
            f"Недостаточно монет! Ваша ставка: **{fmt_num(bet)}**, баланс: **{fmt_num(cur_bal)}** монет.",
            ephemeral=True
        )
        return

    await asyncio.to_thread(give_coins, user_id, guild_id, -bet)

    flip = random.choice(["orel", "reshka"])
    side_name = "Орёл 🦅" if flip == "orel" else "Решка 🪙"
    won = (flip == choice.value)

    if won:
        winnings = bet * 2
        await asyncio.to_thread(give_coins, user_id, guild_id, winnings)
        color = discord.Color.green()
        result_text = f"🎉 **Вы угадали!** Выпал **{side_name}**!\
Выигрыш: **+{fmt_num(winnings)}** монет (прибыль +{fmt_num(bet)})"
    else:
        color = discord.Color.red()
        result_text = f"💀 **Увы, мимо!** Выпал **{side_name}**.\
Вы выбрали **{choice.name}** и потеряли **{fmt_num(bet)}** монет."

    schedule_backup()
    new_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))

    embed = discord.Embed(title="🪙 Бросок монетки", description=result_text, color=color)
    embed.add_field(name="Текущий баланс", value=f"{fmt_num(new_bal)} монет")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dice", description="🎲 Игра в кости против казино (2d6 против 2d6)")
@app_commands.guild_only()
@app_commands.describe(bet="Сумма ставки")
async def dice_cmd(interaction: discord.Interaction, bet: int):
    user_id, guild_id = interaction.user.id, interaction.guild.id
    if bet < 5:
        await interaction.response.send_message("Минимальная ставка — 5 монет.", ephemeral=True)
        return

    cur_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    if bet > cur_bal:
        await interaction.response.send_message(
            f"Недостаточно монет! Ваша ставка: **{fmt_num(bet)}**, баланс: **{fmt_num(cur_bal)}** монет.",
            ephemeral=True
        )
        return

    await asyncio.to_thread(give_coins, user_id, guild_id, -bet)

    p1, p2 = random.randint(1, 6), random.randint(1, 6)
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    p_sum = p1 + p2
    d_sum = d1 + d2

    dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

    if p_sum > d_sum:
        winnings = bet * 2
        await asyncio.to_thread(give_coins, user_id, guild_id, winnings)
        res = f"🎉 **Победа!** Вы обошли казино!\
Выигрыш: **+{fmt_num(winnings)}** монет (прибыль: +{fmt_num(bet)})"
        color = discord.Color.green()
    elif p_sum == d_sum:
        await asyncio.to_thread(give_coins, user_id, guild_id, bet)
        res = "🤝 **Ничья!** Очки равны — ставка возвращена в полном объёме."
        color = discord.Color.light_grey()
    else:
        res = f"💀 **Поражение!** Казино набрало больше очков.\
Потеряно: **-{fmt_num(bet)}** монет."
        color = discord.Color.red()

    schedule_backup()
    new_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))

    embed = discord.Embed(title="🎲 Игра в кости", description=res, color=color)
    embed.add_field(
        name=f"Ваш бросок ({p_sum})",
        value=f"{dice_emojis[p1]} `{p1}` + {dice_emojis[p2]} `{p2}`",
        inline=True
    )
    embed.add_field(
        name=f"Бросок казино ({d_sum})",
        value=f"{dice_emojis[d1]} `{d1}` + {dice_emojis[d2]} `{d2}`",
        inline=True
    )
    embed.add_field(name="Баланс", value=f"{fmt_num(new_bal)} монет", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="roulette", description="🎡 Европейская рулетка: ставка на цвет, чётность или точное число")
@app_commands.guild_only()
@app_commands.describe(
    bet="Сумма ставки",
    bet_type="Тип ставки",
    number="Точное число (0-36, только если выбран тип ставки 'Точное число')"
)
@app_commands.choices(bet_type=[
    app_commands.Choice(name="Красное (выигрыш x2)", value="red"),
    app_commands.Choice(name="Чёрное (выигрыш x2)", value="black"),
    app_commands.Choice(name="Зеро / 0 (выигрыш x14)", value="zero"),
    app_commands.Choice(name="Чётное (выигрыш x2)", value="even"),
    app_commands.Choice(name="Нечётное (выигрыш x2)", value="odd"),
    app_commands.Choice(name="Точное число 0-36 (выигрыш x36)", value="number"),
])
async def roulette_cmd(interaction: discord.Interaction, bet: int, bet_type: app_commands.Choice[str], number: int = None):
    user_id, guild_id = interaction.user.id, interaction.guild.id
    if bet < 5:
        await interaction.response.send_message("Минимальная ставка — 5 монет.", ephemeral=True)
        return

    if bet_type.value == "number" and (number is None or number < 0 or number > 36):
        await interaction.response.send_message("Для ставки на точное число укажите параметр `number` от 0 до 36.", ephemeral=True)
        return

    cur_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))
    if bet > cur_bal:
        await interaction.response.send_message(
            f"Недостаточно монет! Ваша ставка: **{fmt_num(bet)}**, баланс: **{fmt_num(cur_bal)}** монет.",
            ephemeral=True
        )
        return

    await asyncio.to_thread(give_coins, user_id, guild_id, -bet)

    # Европейская рулетка 0-36
    roll = random.randint(0, 36)
    red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

    if roll == 0:
        roll_color = "🟢 Зеро"
    elif roll in red_numbers:
        roll_color = "🔴 Красное"
    else:
        roll_color = "⚫ Чёрное"

    won = False
    multiplier = 0.0

    if bet_type.value == "red" and roll in red_numbers:
        won = True
        multiplier = 2.0
    elif bet_type.value == "black" and roll not in red_numbers and roll != 0:
        won = True
        multiplier = 2.0
    elif bet_type.value == "zero" and roll == 0:
        won = True
        multiplier = 14.0
    elif bet_type.value == "even" and roll != 0 and roll % 2 == 0:
        won = True
        multiplier = 2.0
    elif bet_type.value == "odd" and roll % 2 != 0:
        won = True
        multiplier = 2.0
    elif bet_type.value == "number" and roll == number:
        won = True
        multiplier = 36.0

    winnings = int(bet * multiplier)
    if won:
        await asyncio.to_thread(give_coins, user_id, guild_id, winnings)
        color = discord.Color.green()
        result_desc = f"🎉 **ПОБЕДА!** Вы выиграли **+{fmt_num(winnings)}** монет (множитель x{multiplier:.0f})!"
    else:
        color = discord.Color.red()
        result_desc = f"💀 **ПРОИГРЫШ!** Ставка в размере **{fmt_num(bet)}** монет сгорела."

    schedule_backup()
    new_bal = int(await asyncio.to_thread(get_coins, user_id, guild_id))

    embed = discord.Embed(title="🎡 Колесо Рулетки", description=result_desc, color=color)
    embed.add_field(name="Выпавший сектор", value=f"**{roll}** ({roll_color})", inline=True)
    embed.add_field(name="Ваша ставка", value=f"{fmt_num(bet)} монет на **{bet_type.name}**", inline=True)
    embed.add_field(name="Текущий баланс", value=f"{fmt_num(new_bal)} монет", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="games", description="🎰 Меню всех азартных игр и казино")
@app_commands.guild_only()
async def games_cmd(interaction: discord.Interaction):
    cur_bal = int(await asyncio.to_thread(get_coins, interaction.user.id, interaction.guild.id))
    embed = discord.Embed(
        title="🎰 Казино и Мини-игры сервера",
        description=f"Испытай свою удачу и подними монеты!\
Ваш баланс: **{fmt_num(cur_bal)}** монет\
",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="🚀 `/crash [ставка]`",
        value="Следи за ракетой и растущим множителем! Нажми кнопку, чтобы забрать куш до взрыва.",
        inline=False
    )
    embed.add_field(
        name="🎰 `/slots [ставка]`",
        value="Классический игровой автомат! Сорви куш до **x15** на трёх семёрках 7️⃣7️⃣7️⃣ или алмазах 💎💎💎.",
        inline=False
    )
    embed.add_field(
        name="🪙 `/coinflip [ставка] [орёл/решка]`",
        value="Угадай сторону монетки и удвой свою ставку (**x2**).",
        inline=False
    )
    embed.add_field(
        name="🎲 `/dice [ставка]`",
        value="Бросок костей против казино (2d6 против 2d6). Победа удваивает ставку (**x2**), при ничьей ставка возвращается.",
        inline=False
    )
    embed.add_field(
        name="🎡 `/roulette [ставка] [тип]`",
        value="Европейская рулетка: ставь на Красное/Чёрное (x2), Чёт/Нечёт (x2), Зеро (x14) или точное число (до **x36**!).",
        inline=False
    )
    embed.set_footer(text="Помните об ответственности: ставки могут принести как богатство, так и банкротство!")
    await interaction.response.send_message(embed=embed)

# ---------- АДМИН-КОМАНДЫ И БЭКАП ----------
@bot.tree.command(name="addcoins", description="Выдать или списать монеты участнику (только админы)")
@app_commands.guild_only()
@app_commands.describe(member="Кому выдать", amount="Сколько монет (можно отрицательное)")
@app_commands.default_permissions(administrator=True)
async def addcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    await asyncio.to_thread(give_coins, member.id, interaction.guild.id, amount)
    new_bal = int(await asyncio.to_thread(get_coins, member.id, interaction.guild.id))
    await interaction.response.send_message(
        f"{member.mention}: {amount:+d} монет. Баланс: {fmt_num(new_bal)}"
    )
    await backup_db_to_discord(bot, force=True)


@bot.tree.command(name="setvoice", description="Задать время в войсе участнику (только админы)")
@app_commands.guild_only()
@app_commands.describe(member="Кому", hours="Часы", minutes="Минуты",
                       coins="Монеты (если не указать, баланс не меняется)")
@app_commands.default_permissions(administrator=True)
async def setvoice(interaction: discord.Interaction, member: discord.Member,
                   hours: int, minutes: int = 0, coins: int = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    seconds = max(0, hours * 3600 + minutes * 60)
    await asyncio.to_thread(set_time, member.id, interaction.guild.id, seconds, coins)
    if member.id in voice_sessions:
        voice_sessions[member.id] = time.time()
    await interaction.response.send_message(
        f"{member.mention}: время задано {hours} ч {minutes} мин."
        + (f" Монеты: {fmt_num(coins)}." if coins is not None else "")
    )
    await backup_db_to_discord(bot, force=True)


@bot.tree.command(name="setmessages", description="Задать количество сообщений (и опционально XP) участнику (только админы)")
@app_commands.guild_only()
@app_commands.describe(member="Кому", count="Количество сообщений", xp="Общий XP (опционально)")
@app_commands.default_permissions(administrator=True)
async def setmessages(interaction: discord.Interaction, member: discord.Member, count: int, xp: int = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    await asyncio.to_thread(set_messages, member.id, interaction.guild.id, max(0, count), xp)
    await interaction.response.send_message(
        f"{member.mention}: сообщений задано **{fmt_num(max(0, count))}**."
        + (f" XP пересчитан из **{fmt_num(max(0, xp))}**." if xp is not None else "")
    )
    await backup_db_to_discord(bot, force=True)


@bot.tree.command(name="backup", description="Сделать бэкап базы прямо сейчас (только админы)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def backup_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await asyncio.to_thread(flush_active_voice_sessions_sync)
    ok = await backup_db_to_discord(bot, force=True)
    ch = await _resolve_backup_channel(bot)
    where = f" в <#{ch.id}>" if ch else ""
    await interaction.followup.send(
        f"Бэкап сохранён{where}." if ok else "Не получилось. Проверь права бота в канале бэкапа и логи.",
        ephemeral=True,
    )


@bot.tree.command(name="setbackupchannel", description="Привязать канал для автосохранения базы (только админы)")
@app_commands.guild_only()
@app_commands.describe(channel="Текстовый канал (по умолчанию текущий)")
@app_commands.default_permissions(administrator=True)
async def setbackupchannel_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global _active_backup_channel_id
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("Нужен текстовый канал.", ephemeral=True)
        return
    _active_backup_channel_id = target.id
    try:
        topic = target.topic or ""
        if BACKUP_CHANNEL_MARKER not in topic:
            await target.edit(topic=(f"{BACKUP_CHANNEL_MARKER} {topic}").strip()[:1024])
    except (discord.Forbidden, discord.HTTPException):
        pass
    await interaction.response.defer(ephemeral=True)
    ok = await backup_db_to_discord(bot, force=True)
    await interaction.followup.send(
        f"Канал <#{target.id}> привязан для автобэкапа. Тестовый бэкап: {'успешно' if ok else 'ошибка прав'}.",
        ephemeral=True,
    )


# ---------- ЗАПУСК ----------
if __name__ == "__main__":
    bot.run(TOKEN)
