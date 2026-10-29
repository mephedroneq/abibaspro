import glob
import io
import os
import time
import random
import sqlite3
import asyncio
from datetime import timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont

# ---------- НАСТРОЙКИ ----------
import os
# ...
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Токен не найден! Укажи DISCORD_BOT_TOKEN в настройках бота на Bothost")
AFK_CHANNEL_IDS = []          # ID каналов, которые не считаем, например [123456789, 987654321]

COINS_PER_MINUTE = 1          # сколько монет даётся за минуту в войсе
CARD_PRICE = 100              # цена одной карточки
CARD_SERIES = "MINESHIELD"    # надпись над ником на карточке
CARD_SEASON = "SEASON IV"     # надпись под ником на карточке

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(BASE_DIR, "background.png")          # фон для /voice
TOP_BG_PATH = os.path.join(BASE_DIR, "top_background.png")  # фон для /voicetop
DB_PATH = os.path.join(BASE_DIR, "voice_time.db")
CARDS_DIR = os.path.join(BASE_DIR, "cards")                 # сюда кладём картинки карточек
FONT_PATH = os.path.join(BASE_DIR, "font.ttf")              # необязательно: свой шрифт
FONT_BOLD_PATH = os.path.join(BASE_DIR, "font_bold.ttf")    # необязательно: свой жирный шрифт

# Цвета редкостей (для карточек-заглушек и сообщений)
RARITY_COLORS = {
    "Обычная": (150, 155, 160),
    "Редкая": (70, 140, 255),
    "Эпическая": (170, 80, 255),
    "Легендарная": (255, 180, 40),
}

# Список карточек. weight = вес шанса (чем больше, тем чаще выпадает).
# Шанс карточки = её weight / сумма всех weight.
# image = имя файла в папке cards (если файла нет, нарисуется заглушка).
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

voice_sessions = {}  # {user_id: время начала отсчёта}
synced = False
CARDS_BY_ID = {c["id"]: c for c in CARDS}


# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
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


def add_time(user_id: int, guild_id: int, seconds: int):
    """Добавляет время в войсе и начисляет за него монеты."""
    coins = seconds * COINS_PER_MINUTE / 60
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO voice_time (user_id, guild_id, total_seconds)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, guild_id) DO UPDATE SET
            total_seconds = total_seconds + ?
    """, (user_id, guild_id, seconds, seconds))
    c.execute("""
        INSERT INTO wallet (user_id, guild_id, coins)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, guild_id) DO UPDATE SET
            coins = coins + ?
    """, (user_id, guild_id, coins, coins))
    conn.commit()
    conn.close()


def get_time(user_id: int, guild_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_seconds FROM voice_time WHERE user_id = ? AND guild_id = ?",
              (user_id, guild_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def get_coins(user_id: int, guild_id: int) -> float:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0.0


def give_coins(user_id: int, guild_id: int, amount: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO wallet (user_id, guild_id, coins)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, guild_id) DO UPDATE SET
            coins = coins + ?
    """, (user_id, guild_id, amount, amount))
    conn.commit()
    conn.close()


def buy_card(user_id: int, guild_id: int, card_id: str, price: int) -> bool:
    """Списывает монеты и выдаёт карточку одной транзакцией. False, если не хватает монет."""
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT coins FROM wallet WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        row = c.fetchone()
        if not row or row[0] < price:
            c.execute("ROLLBACK")
            return False
        c.execute("UPDATE wallet SET coins = coins - ? WHERE user_id = ? AND guild_id = ?",
                  (price, user_id, guild_id))
        c.execute("""
            INSERT INTO cards (user_id, guild_id, card_id, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, guild_id, card_id) DO UPDATE SET
                count = count + 1
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT card_id, count FROM cards WHERE user_id = ? AND guild_id = ? AND count > 0",
              (user_id, guild_id))
    rows = c.fetchall()
    conn.close()
    return {cid: cnt for cid, cnt in rows}


def format_seconds(sec: int) -> str:
    return str(timedelta(seconds=sec))


# ---------- КАРТОЧКИ: ШАНСЫ ----------
def roll_card() -> dict:
    return random.choices(CARDS, weights=[c["weight"] for c in CARDS], k=1)[0]


def card_chance(card: dict) -> float:
    total = sum(c["weight"] for c in CARDS)
    return card["weight"] / total * 100


# ---------- КАРТИНКИ ----------
_font_cache = {}


def _pick_font_file(bold: bool):
    """Ищет .ttf рядом с ботом (и в папке fonts). Подходит Roboto с любыми именами файлов."""
    files = glob.glob(os.path.join(BASE_DIR, "*.ttf")) + glob.glob(os.path.join(BASE_DIR, "fonts", "*.ttf"))
    bad = ("italic", "light", "thin", "medium", "black", "condensed", "semi", "extra")

    def name(p):
        return os.path.basename(p).lower()

    if bold:
        for p in files:
            if "bold" in name(p) and not any(b in name(p) for b in bad):
                return p
    else:
        for p in files:
            if ("regular" in name(p) or name(p) in ("font.ttf",)) and "italic" not in name(p):
                return p
    # запасной вариант: любой подходящий файл (например, один variable-шрифт)
    for p in files:
        if "italic" not in name(p):
            return p
    return None


def load_font(size, bold=False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]

    font = None
    path = _pick_font_file(bold)
    candidates = [path] if path else []
    candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                   else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for n in candidates:
        try:
            font = ImageFont.truetype(n, size)
            # если это variable-шрифт Roboto[wght].ttf, выбираем начертание
            try:
                font.set_variation_by_name("Bold" if bold else "Regular")
            except Exception:
                pass
            break
        except OSError:
            continue

    if font is None:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font
# ---------- СОБЫТИЯ ----------
@tasks.loop(seconds=30)
async def flush_voice_time():
    """Каждые 30 сек записывает накопленное время и монеты в базу."""
    now = time.time()
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc.id in AFK_CHANNEL_IDS:
                continue
            for m in vc.members:
                if m.bot or m.id not in voice_sessions:
                    continue
                seconds = int(now - voice_sessions[m.id])
                if seconds > 0:
                    add_time(m.id, guild.id, seconds)
                    voice_sessions[m.id] = now


@bot.event
async def on_ready():
    global synced
    init_db()
    os.makedirs(CARDS_DIR, exist_ok=True)

    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc.id in AFK_CHANNEL_IDS:
                continue
            for m in vc.members:
                if not m.bot and m.id not in voice_sessions:
                    voice_sessions[m.id] = time.time()

    if not flush_voice_time.is_running():
        flush_voice_time.start()

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
    was_counted = before.channel is not None and before.channel.id not in AFK_CHANNEL_IDS
    will_count = after.channel is not None and after.channel.id not in AFK_CHANNEL_IDS

    if was_counted:
        start = voice_sessions.pop(member.id, None)
        if start:
            seconds = int(now - start)
            if seconds > 0:
                add_time(member.id, member.guild.id, seconds)
                print(f"[-] {member.display_name} +{format_seconds(seconds)}")

    if will_count:
        voice_sessions[member.id] = now
        print(f"[+] {member.display_name} в {after.channel.name}")
    else:
        voice_sessions.pop(member.id, None)


# ---------- КОМАНДЫ: ГОЛОС ----------
@bot.tree.command(name="voice", description="Показать время в голосовых каналах")
@app_commands.describe(member="Участник (по умолчанию вы)")
async def voice_stats(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    member = member or interaction.user

    total = get_time(member.id, interaction.guild.id)
    current = int(time.time() - voice_sessions[member.id]) if member.id in voice_sessions else 0

    avatar = await member.display_avatar.replace(size=256, format="png").read()
    buf = await asyncio.to_thread(make_stats_card, member.display_name, avatar, total + current, current)
    await interaction.followup.send(file=discord.File(buf, "voice.png"))


@bot.tree.command(name="voicetop", description="Топ участников по голосовому времени")
@app_commands.describe(limit="Сколько мест показать (1-5)")
async def voice_top(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 5] = 4):
    await interaction.response.defer()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, total_seconds FROM voice_time
        WHERE guild_id = ?
        ORDER BY total_seconds DESC
        LIMIT ?
    """, (interaction.guild.id, limit))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("Пока нет данных.")
        return

    data = []
    for uid, secs in rows:
        m = interaction.guild.get_member(uid)
        current = int(time.time() - voice_sessions[uid]) if uid in voice_sessions else 0
        data.append((m.display_name if m else f"ID {uid}", secs + current))
    data.sort(key=lambda x: x[1], reverse=True)

    buf = await asyncio.to_thread(make_top_card, data)
    await interaction.followup.send(file=discord.File(buf, "top.png"))


# ---------- КОМАНДЫ: МОНЕТЫ И КАРТОЧКИ ----------
def live_coins(user_id: int, guild_id: int) -> float:
    """Баланс с учётом текущей, ещё не записанной сессии."""
    coins = get_coins(user_id, guild_id)
    if user_id in voice_sessions:
        coins += (time.time() - voice_sessions[user_id]) * COINS_PER_MINUTE / 60
    return coins


@bot.tree.command(name="balance", description="Показать баланс монет")
@app_commands.describe(member="Участник (по умолчанию вы)")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    coins = int(live_coins(member.id, interaction.guild.id))
    cards_count = sum(get_inventory(member.id, interaction.guild.id).values())

    embed = discord.Embed(title="💰 Баланс", description=member.mention, color=discord.Color.gold())
    embed.add_field(name="Монеты", value=str(coins), inline=True)
    embed.add_field(name="Карточек", value=str(cards_count), inline=True)
    embed.set_footer(text=f"{COINS_PER_MINUTE} монет за минуту в голосовом канале")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="shop", description="Цена карточки и шансы выпадения")
async def shop(interaction: discord.Interaction):
    coins = int(live_coins(interaction.user.id, interaction.guild.id))
    lines = []
    for c in sorted(CARDS, key=lambda x: x["weight"], reverse=True):
        lines.append(f"**{c['name']}** — {c['rarity']}, шанс {card_chance(c):.1f}%")

    embed = discord.Embed(title="🃏 Магазин карточек", color=discord.Color.pink())
    embed.add_field(name="Цена", value=f"{CARD_PRICE} монет за одну карточку", inline=False)
    embed.add_field(name="Ваш баланс", value=f"{coins} монет", inline=False)
    embed.add_field(name="Шансы", value="\n".join(lines), inline=False)
    embed.set_footer(text="Открыть карточку: /opencard")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="opencard", description=f"Купить случайную карточку игрока")
async def opencard(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id, guild_id = interaction.user.id, interaction.guild.id

    # учитываем текущую сессию: сначала записываем накопленное
    if user_id in voice_sessions:
        now = time.time()
        seconds = int(now - voice_sessions[user_id])
        if seconds > 0:
            add_time(user_id, guild_id, seconds)
            voice_sessions[user_id] = now

    card = roll_card()
    ok = await asyncio.to_thread(buy_card, user_id, guild_id, card["id"], CARD_PRICE)
    if not ok:
        have = int(get_coins(user_id, guild_id))
        await interaction.followup.send(
            f"Не хватает монет: нужно **{CARD_PRICE}**, у вас **{have}**. "
            f"Сидите в голосовом канале, чтобы заработать больше.")
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

    lines = []
    for c in CARDS:
        if c["id"] in inv:
            lines.append(f"**{c['name']}** — {c['rarity']} ×{inv[c['id']]}")

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


# ---------- ЗАПУСК ----------
bot.run(TOKEN)
