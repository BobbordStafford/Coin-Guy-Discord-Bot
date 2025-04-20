import json
import random
import discord
import asyncio
import datetime
from discord import app_commands
from discord.ext import commands

with open('config.json') as f:
    cfg = json.load(f)

DB_PATH = 'db.json'

_DEFAULT_USER_INV = {
    "guns": 0,
    "bullet": 0,
    "crazy_bullet": 0,
    "insane_bullet": 0,
}

PRICE_TABLE = {
    "gun": 30,
    "bullet": 2,
    "crazy_bullet": 4,
    "insane_bullet": 8,
}

try:
    with open(DB_PATH) as f:
        db = json.load(f)
except FileNotFoundError:
    db = {
        "balances": {},
        "inventories": {},
        "jail_until": {},
        "last_steal": {},
    }

for key in ("inventories", "jail_until", "last_steal"):
    db.setdefault(key, {})


def save_db():
    with open(DB_PATH, 'w') as f:
        json.dump(db, f, indent=2)


def ensure_user(user_id: str):
    db["balances"].setdefault(user_id, 0)
    db["inventories"].setdefault(user_id, _DEFAULT_USER_INV.copy())
    db["jail_until"].setdefault(user_id, None)
    db["last_steal"].setdefault(user_id, None)


def is_admin(member: discord.Member) -> bool:
    return any(str(r.id) in cfg["admin_roles"] for r in member.roles)


def now():
    return datetime.datetime.utcnow()


def is_jailed(user_id: str) -> bool:
    ts = db["jail_until"].get(user_id)
    if not ts:
        return False
    return datetime.datetime.fromisoformat(ts) > now()


def jail_remaining(user_id: str) -> datetime.timedelta | None:
    ts = db["jail_until"].get(user_id)
    if not ts:
        return None
    dt = datetime.datetime.fromisoformat(ts)
    rem = dt - now()
    return rem if rem.total_seconds() > 0 else None


def is_armed(user_id: str) -> bool:
    inv = db["inventories"][user_id]
    has_gun = inv["guns"] > 0
    has_bullets = any(inv[k] > 0 for k in ("bullet", "crazy_bullet", "insane_bullet"))
    return has_gun and has_bullets


def consume_bullet(user_id: str):
    inv = db["inventories"][user_id]
    for k in ("insane_bullet", "crazy_bullet", "bullet"):
        if inv[k] > 0:
            inv[k] -= 1
            return k
    return None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
guild_obj = discord.Object(id=cfg["guild_id"])

@tree.command(name="balance", description="Check your balance or another user's", guild=guild_obj)
@app_commands.describe(user="(optional) user to check")
async def balance(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    ensure_user(str(target.id))
    bal = db["balances"][str(target.id)]
    if target == interaction.user:
        await interaction.response.send_message(f"You have **{bal}** coins.")
    else:
        await interaction.response.send_message(f"{target.mention} has **{bal}** coins.")

@tree.command(name="give", description="Give coins to another user", guild=guild_obj)
@app_commands.describe(user="Recipient", amount="How many coins")
async def give(interaction: discord.Interaction, user: discord.User, amount: int):
    giver_id = str(interaction.user.id)
    ensure_user(giver_id)
    if is_jailed(giver_id):
        rem = jail_remaining(giver_id)
        return await interaction.response.send_message(f"🚓 You are in jail for another {rem}.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    ensure_user(str(user.id))
    if db["balances"][giver_id] < amount:
        return await interaction.response.send_message("You don’t have enough coins.", ephemeral=True)
    db["balances"][giver_id] -= amount
    db["balances"][str(user.id)] += amount
    save_db()
    await interaction.response.send_message(f"{interaction.user.mention} gave {amount} coins to {user.mention}.")

BUYABLES = [
    app_commands.Choice(name="gun", value="gun"),
    app_commands.Choice(name="bullet", value="bullet"),
    app_commands.Choice(name="crazy_bullet", value="crazy_bullet"),
    app_commands.Choice(name="insane_bullet", value="insane_bullet"),
]

@tree.command(name="buy", description="Buy items from the shop", guild=guild_obj)
@app_commands.describe(item="What to buy", quantity="How many (default 1)")
@app_commands.choices(item=BUYABLES)
async def buy(interaction: discord.Interaction, item: app_commands.Choice[str], quantity: int = 1):
    uid = str(interaction.user.id)
    ensure_user(uid)
    if is_jailed(uid):
        rem = jail_remaining(uid)
        return await interaction.response.send_message(f"🚓 You are in jail for another {rem}.", ephemeral=True)
    if quantity <= 0:
        return await interaction.response.send_message("Quantity must be positive.", ephemeral=True)
    item_key = item.value
    cost_per = PRICE_TABLE[item_key]
    total_cost = cost_per * quantity
    if db["balances"][uid] < total_cost:
        return await interaction.response.send_message("You don’t have enough coins.", ephemeral=True)
    db["balances"][uid] -= total_cost
    if item_key == "gun":
        db["inventories"][uid]["guns"] += quantity
    else:
        db["inventories"][uid][item_key] += quantity
    save_db()
    await interaction.response.send_message(f"🛒 You bought {quantity} **{item_key.replace('_', ' ')}(s)** for {total_cost} coins.")

@tree.command(name="steal", description="Attempt to steal 1 coin from someone", guild=guild_obj)
@app_commands.describe(victim="The user you want to steal from")
async def steal(interaction: discord.Interaction, victim: discord.User):
    attacker_id = str(interaction.user.id)
    victim_id = str(victim.id)
    if attacker_id == victim_id:
        return await interaction.response.send_message("You can’t steal from yourself.", ephemeral=True)
    ensure_user(attacker_id)
    ensure_user(victim_id)
    if is_jailed(attacker_id):
        rem = jail_remaining(attacker_id)
        return await interaction.response.send_message(f"🚓 You are in jail for another {rem}", ephemeral=True)
    last = db["last_steal"][attacker_id]
    if last and (now() - datetime.datetime.fromisoformat(last)).total_seconds() < 86400:
        remaining = 86400 - (now() - datetime.datetime.fromisoformat(last)).total_seconds()
        return await interaction.response.send_message(f"⏳ You can steal again in {datetime.timedelta(seconds=int(remaining))}.", ephemeral=True)
    if db["balances"][victim_id] < 1:
        return await interaction.response.send_message(f"{victim.mention} has no coins to steal.")
    attacker_armed = is_armed(attacker_id)
    victim_armed = is_armed(victim_id)
    if attacker_armed:
        consume_bullet(attacker_id)
    if attacker_armed and not victim_armed:
        fail_chance = 0.25
    elif victim_armed and not attacker_armed:
        fail_chance = 0.75
    else:
        fail_chance = 0.5
    success = random.random() >= fail_chance
    db["last_steal"][attacker_id] = now().isoformat()
    if success:
        db["balances"][victim_id] -= 1
        db["balances"][attacker_id] += 1
        save_db()
        await interaction.response.send_message(f"💰 Success! You stole 1 coin from {victim.mention}.")
    else:
        release_time = now() + datetime.timedelta(days=1)
        db["jail_until"][attacker_id] = release_time.isoformat()
        save_db()
        await interaction.response.send_message(f"🚓 You failed and got caught! You're in jail until <t:{int(release_time.timestamp())}:f>.")

@tree.command(name="gencoins", description="Generate coins for a user (admin only)", guild=guild_obj)
@app_commands.describe(user="Target user", amount="How many coins")
async def gencoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    ensure_user(str(user.id))
    db["balances"][str(user.id)] += amount
    save_db()
    await interaction.response.send_message(f"Generated {amount} coins for {user.mention}.")

@tree.command(name="takecoins", description="Take coins from a user (admin only)", guild=guild_obj)
@app_commands.describe(user="Target user", amount="How many coins")
async def takecoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    uid = str(user.id)
    ensure_user(uid)
    db["balances"][uid] = max(0, db["balances"][uid] - amount)
    save_db()
    await interaction.response.send_message(f"Taken {amount} coins from {user.mention}.")

@tree.command(name="setcoins", description="Set a user's balance (admin only)", guild=guild_obj)
@app_commands.describe(user="Target user", amount="New balance")
async def setcoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount < 0:
        return await interaction.response.send_message("Balance cannot be negative.", ephemeral=True)
    ensure_user(str(user.id))
    db["balances"][str(user.id)] = amount
    save_db()
    await interaction.response.send_message(f"{user.mention}’s balance set to **{amount}** coins.")

async def daily_reward_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now_ts = datetime.datetime.utcnow()
        tomorrow = (now_ts + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait = (tomorrow - now_ts).total_seconds()
        await asyncio.sleep(wait)
        for uid in list(db["balances"]):
            db["balances"][uid] += 1
        save_db()
        print(f"Awarded daily coin to {len(db['balances'])} users at {tomorrow}")

@bot.event
async def on_ready():
    await tree.sync(guild=guild_obj)
    print(f"Bot ready — {bot.user} (ID {bot.user.id})")
    bot.loop.create_task(daily_reward_loop())

bot.run(cfg["token"])
