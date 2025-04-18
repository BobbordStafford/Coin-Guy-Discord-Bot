import json
import discord
import asyncio
import datetime
from discord import app_commands
from discord.ext import commands

with open('config.json') as f:
    cfg = json.load(f)

DB_PATH = 'db.json'
def save_db():
    with open(DB_PATH) as f:
        json.dump(db, f, indent=2)

try:
    with open(DB_PATH) as f:
        db = json.load(f)
except FileNotFoundError:
    db = {"balances": {}}
    save_db()


def ensure_balance(user_id: str):
    db["balances"].setdefault(user_id, 0)


def is_admin(member: discord.Member) -> bool:
    return any(str(r.id) in cfg["admin_roles"] for r in member.roles)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@tree.command(
    name="balance",
    description="Check your balance or another user's",
    guild=discord.Object(id=cfg["guild_id"])
)
@app_commands.describe(user="(optional) user to check")
async def balance(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user
    ensure_balance(str(target.id))
    bal = db["balances"][str(target.id)]
    if target == interaction.user:
        await interaction.response.send_message(f"You have **{bal}** coins.")
    else:
        await interaction.response.send_message(f"{target.mention} has **{bal}** coins.")

@tree.command(
    name="give",
    description="Give coins to another user",
    guild=discord.Object(id=cfg["guild_id"])
)
@app_commands.describe(user="Recipient", amount="How many coins")
async def give(interaction: discord.Interaction, user: discord.User, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    giver_id = str(interaction.user.id)
    ensure_balance(giver_id)
    ensure_balance(str(user.id))
    if db["balances"][giver_id] < amount:
        return await interaction.response.send_message("You don’t have enough coins.", ephemeral=True)
    db["balances"][giver_id] -= amount
    db["balances"][str(user.id)] += amount
    save_db()
    await interaction.response.send_message(f"{interaction.user.mention} gave {amount} coins to {user.mention}.")

@tree.command(
    name="gencoins",
    description="Generate coins for a user (admin only)",
    guild=discord.Object(id=cfg["guild_id"])
)
@app_commands.describe(user="Target user", amount="How many coins")
async def gencoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    ensure_balance(str(user.id))
    db["balances"][str(user.id)] += amount
    save_db()
    await interaction.response.send_message(f"Generated {amount} coins for {user.mention}.")

@tree.command(
    name="takecoins",
    description="Take coins from a user (admin only)",
    guild=discord.Object(id=cfg["guild_id"])
)
@app_commands.describe(user="Target user", amount="How many coins")
async def takecoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
    uid = str(user.id)
    ensure_balance(uid)
    db["balances"][uid] = max(0, db["balances"][uid] - amount)
    save_db()
    await interaction.response.send_message(f"Taken {amount} coins from {user.mention}.")

@tree.command(
    name="setcoins",
    description="Set a user's balance (admin only)",
    guild=discord.Object(id=cfg["guild_id"])
)
@app_commands.describe(user="Target user", amount="New balance")
async def setcoins(interaction: discord.Interaction, user: discord.User, amount: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    if amount < 0:
        return await interaction.response.send_message("Balance cannot be negative.", ephemeral=True)
    db["balances"][str(user.id)] = amount
    save_db()
    await interaction.response.send_message(f"{user.mention}’s balance set to **{amount}** coins.")

async def daily_reward_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.datetime.now()
        tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait = (tomorrow - now).total_seconds()
        await asyncio.sleep(wait)
        for uid in list(db["balances"]):
            db["balances"][uid] += 1
        save_db()
        print(f"Awarded daily coin to {len(db['balances'])} users at {tomorrow}")

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=cfg["guild_id"]))
    print(f"Bot ready — {bot.user} (ID {bot.user.id})")
    bot.loop.create_task(daily_reward_loop())

bot.run(cfg["token"])