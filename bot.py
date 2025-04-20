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
        "transactions": []
    }

for key in ("inventories", "jail_until", "last_steal", "transactions"):
    db.setdefault(key, [] if key == "transactions" else {})


def save_db():
    with open(DB_PATH, 'w') as f:
        json.dump(db, f, indent=2)


def ensure_user(user_id: str):
    db["balances"].setdefault(user_id, 0)
    db["inventories"].setdefault(user_id, _DEFAULT_USER_INV.copy())
    db["jail_until"].setdefault(user_id, None)
    db["last_steal"].setdefault(user_id, None)


def log_transaction(tx_type: str, details: dict):
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "type": tx_type,
        **details
    }
    db["transactions"].append(entry)
    save_db()


def is_admin(member: discord.Member) -> bool:
    return any(str(r.id) in cfg["admin_roles"] for r in member.roles)


def now():
    return datetime.datetime.utcnow()


def is_jailed(user_id: str) -> bool:
    ts = db["jail_until"].get(user_id)
    return bool(ts and datetime.datetime.fromisoformat(ts) > now())


def jail_remaining(user_id: str) -> datetime.timedelta | None:
    ts = db["jail_until"].get(user_id)
    if not ts:
        return None
    rem = datetime.datetime.fromisoformat(ts) - now()
    return rem if rem.total_seconds() > 0 else None


def is_armed(user_id: str) -> bool:
    inv = db["inventories"][user_id]
    return inv["guns"] > 0 and any(inv[k] > 0 for k in ("bullet", "crazy_bullet", "insane_bullet"))


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
    uid = str(target.id)
    ensure_user(uid)
    bal = db["balances"][uid]
    await interaction.response.send_message(f"{target.mention if target!=interaction.user else 'You have'} **{bal}** coins.")

@tree.command(name="give", description="Give coins to another user", guild=guild_obj)
@app_commands.describe(user="Recipient", amount="How many coins")
async def give(interaction: discord.Interaction, user: discord.User, amount: int):
    giver = str(interaction.user.id)
    ensure_user(giver)
    if is_jailed(giver):
        return await interaction.response.send_message(f"🚓 You are in jail for another {jail_remaining(giver)}.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    recipient = str(user.id)
    ensure_user(recipient)
    if db["balances"][giver] < amount:
        return await interaction.response.send_message("You don’t have enough coins.", ephemeral=True)
    db["balances"][giver] -= amount
    db["balances"][recipient] += amount
    log_transaction("give", {"from": giver, "to": recipient, "amount": amount})
    await interaction.response.send_message(f"{interaction.user.mention} gave {amount} coins to {user.mention}.")

BUYABLES = [app_commands.Choice(name=k, value=k) for k in PRICE_TABLE]

@tree.command(name="buy", description="Buy items from the shop", guild=guild_obj)
@app_commands.describe(item="What to buy", quantity="How many (default 1)")
@app_commands.choices(item=BUYABLES)
async def buy(interaction: discord.Interaction, item: app_commands.Choice[str], quantity: int = 1):
    uid = str(interaction.user.id)
    ensure_user(uid)
    if is_jailed(uid):
        return await interaction.response.send_message(f"🚓 You are in jail for another {jail_remaining(uid)}.", ephemeral=True)
    if quantity <= 0:
        return await interaction.response.send_message("Quantity must be positive.", ephemeral=True)
    key = item.value
    cost = PRICE_TABLE[key] * quantity
    if db["balances"][uid] < cost:
        return await interaction.response.send_message("You don’t have enough coins.", ephemeral=True)
    db["balances"][uid] -= cost
    if key == "gun":
        db["inventories"][uid]["guns"] += quantity
    else:
        db["inventories"][uid][key] += quantity
    log_transaction("buy", {"user": uid, "item": key, "quantity": quantity, "cost": cost})
    await interaction.response.send_message(f"🛒 You bought {quantity} {key.replace('_',' ')}(s) for {cost} coins.")

@tree.command(name="steal", description="Attempt to steal 1 coin from someone", guild=guild_obj)
@app_commands.describe(victim="The user you want to steal from")
async def steal(interaction: discord.Interaction, victim: discord.User):
    attacker = str(interaction.user.id)
    victim_id = str(victim.id)
    if attacker == victim_id:
        return await interaction.response.send_message("You can’t steal from yourself.", ephemeral=True)
    ensure_user(attacker)
    ensure_user(victim_id)
    if is_jailed(attacker):
        return await interaction.response.send_message(f"🚓 You are in jail for another {jail_remaining(attacker)}", ephemeral=True)
    last = db["last_steal"][attacker]
    if last and (now() - datetime.datetime.fromisoformat(last)).total_seconds() < 86400:
        return await interaction.response.send_message(f"⏳ You can steal again in {datetime.timedelta(seconds=int(86400 - (now()-datetime.datetime.fromisoformat(last)).total_seconds()))}.", ephemeral=True)
    if db["balances"][victim_id] < 1:
        return await interaction.response.send_message(f"{victim.mention} has no coins to steal.")
    a_armed = is_armed(attacker)
    v_armed = is_armed(victim_id)
    if a_armed:
        consume_bullet(attacker)
    fail = 0.25 if a_armed and not v_armed else 0.75 if v_armed and not a_armed else 0.5
    success = random.random() >= fail
    db["last_steal"][attacker] = now().isoformat()
    if success:
        db["balances"][victim_id] -= 1
        db["balances"][attacker] += 1
        log_transaction("steal_success", {"from": attacker, "to": victim_id})
        await interaction.response.send_message(f"💰 Success! You stole 1 coin from {victim.mention}.")
    else:
        until = now() + datetime.timedelta(days=1)
        db["jail_until"][attacker] = until.isoformat()
        log_transaction("steal_fail", {"attacker": attacker, "victim": victim_id, "jail_until": until.isoformat()})
        await interaction.response.send_message(f"🚓 You failed and got caught! You're in jail until <t:{int(until.timestamp())}:f>.")

@tree.command(name="gencoins", description="Generate coins for a user (admin only)", guild=guild_obj)
@app_commands.describe(user="Target user", amount="How many coins")
async def gencoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    uid = str(user.id)
    ensure_user(uid)
    db["balances"][uid] += amount
    log_transaction("gencoins", {"to": uid, "amount": amount})
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
    taken = min(db["balances"][uid], amount)
    db["balances"][uid] -= taken
    log_transaction("takecoins", {"from": uid, "amount": taken})
    await interaction.response.send_message(f"Taken {taken} coins from {user.mention}.")

@tree.command(name="setcoins", description="Set a user's balance (admin only)", guild=guild_obj)
@app_commands.describe(user="Target user", amount="New balance")
async def setcoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount < 0:
        return await interaction.response.send_message("Balance cannot be negative.", ephemeral=True)
    uid = str(user.id)
    ensure_user(uid)
    db["balances"][uid] = amount
    log_transaction("setcoins", {"user": uid, "amount": amount})
    await interaction.response.send_message(f"{user.mention}’s balance set to **{amount}** coins.")

async def daily_reward_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now_ts = datetime.datetime.utcnow()
        tomorrow = (now_ts + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((tomorrow - now_ts).total_seconds())
        for uid in list(db["balances"]):
            db["balances"][uid] += 1
            log_transaction("daily_reward", {"to": uid, "amount": 1})

@bot.event
async def on_ready():
    await tree.sync(guild=guild_obj)
    bot.loop.create_task(daily_reward_loop())

bot.run(cfg["token"])
