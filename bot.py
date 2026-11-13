import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import httpx
import os
import time
from datetime import datetime, timedelta

# =============================================
#              DATA MANAGEMENT
# =============================================

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
    with open(path, "r") as f:
        return json.load(f)

def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def save_config(data):
    with open("config.json", "w") as f:
        json.dump(data, f, indent=4)

config = load_config()

# =============================================
#              BOT SETUP
# =============================================

intents = discord.Intents.all()

class QuestBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.active_tasks = {}
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"[✅] Synced slash commands")

bot = QuestBot()

# =============================================
#              QUEST API
# =============================================

class QuestAPI:
    BASE = "https://discord.com/api/v9"

    def __init__(self, token: str):
        self.token = token

    @property
    def headers(self):
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }

    async def validate(self):
        async with httpx.AsyncClient() as c:
            try:
                r = await c.get(f"{self.BASE}/users/@me", headers=self.headers)
                return r.json() if r.status_code == 200 else None
            except:
                return None

    async def get_quests(self):
        async with httpx.AsyncClient() as c:
            try:
                r = await c.get(f"{self.BASE}/users/@me/quests", headers=self.headers)
                return r.json() if r.status_code == 200 else []
            except:
                return []

    async def enroll(self, quest_id):
        async with httpx.AsyncClient() as c:
            try:
                r = await c.post(
                    f"{self.BASE}/users/@me/quests/{quest_id}/enroll",
                    headers=self.headers
                )
                return r.status_code in [200, 204]
            except:
                return False

    async def heartbeat(self, quest_id, stream_key="", terminal=False):
        async with httpx.AsyncClient() as c:
            try:
                r = await c.post(
                    f"{self.BASE}/users/@me/quests/{quest_id}/heartbeat",
                    headers=self.headers,
                    json={"stream_key": stream_key, "terminal": terminal}
                )
                return r.status_code in [200, 204]
            except:
                return False

    async def claim(self, quest_id):
        async with httpx.AsyncClient() as c:
            try:
                r = await c.post(
                    f"{self.BASE}/users/@me/quests/{quest_id}/claim-reward",
                    headers=self.headers
                )
                return r.status_code == 200
            except:
                return False

    def parse(self, quest):
        cfg = quest.get("config", {})
        msgs = cfg.get("messages", {})
        us = quest.get("user_status", {})
        dur = cfg.get("stream_duration_requirement_minutes", 0) or \
              cfg.get("task_duration_minutes", 0) or 15

        return {
            "id": quest.get("id", ""),
            "name": msgs.get("game_title", {}).get("default", "Unknown Quest"),
            "description": msgs.get("quest_name", {}).get("default", "No description"),
            "type": cfg.get("type", "unknown"),
            "duration": dur,
            "completed": us.get("completed_at") is not None,
            "claimed": us.get("claimed_at") is not None,
            "enrolled": us.get("enrolled_at") is not None,
            "app_id": cfg.get("application_id"),
            "progress": us.get("progress", {}),
            "reward_code_platform": cfg.get("reward_code_platform", {})
        }

# =============================================
#              HELPER FUNCTIONS
# =============================================

def mask_token(token: str) -> str:
    if len(token) > 12:
        return token[:5] + "•" * (len(token) - 9) + token[-4:]
    return "•" * len(token)

def is_premium(member: discord.Member) -> bool:
    cfg = load_config()
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    if role_id == 0:
        return False
    return any(r.id == role_id for r in member.roles)

def check_cooldown(user_id: str) -> tuple[bool, float]:
    """Returns (on_cooldown, remaining_seconds)"""
    cooldowns = load_json("cooldowns.json")
    cfg = load_config()
    hours = cfg.get("FREE_COOLDOWN_HOURS", 24)

    if user_id in cooldowns:
        last_use = cooldowns[user_id]
        elapsed = time.time() - last_use
        required = hours * 3600
        if elapsed < required:
            return True, required - elapsed
    return False, 0

def set_cooldown(user_id: str):
    cooldowns = load_json("cooldowns.json")
    cooldowns[user_id] = time.time()
    save_json("cooldowns.json", cooldowns)

def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    parts = []
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    if s > 0:
        parts.append(f"{s}s")
    return " ".join(parts) or "0s"

def get_status_emoji(info: dict) -> tuple[str, str]:
    if info["claimed"]:
        return "🎁", "Claimed"
    elif info["completed"]:
        return "✅", "Completed"
    elif info["enrolled"]:
        return "📝", "Enrolled"
    else:
        return "⏳", "Available"

async def send_log(guild: discord.Guild, title: str, user: discord.User, details: str = ""):
    cfg = load_config()
    ch_id = cfg.get("LOG_CHANNEL_ID", 0)
    if ch_id == 0:
        return
    ch = guild.get_channel(ch_id)
    if not ch:
        return

    embed = discord.Embed(
        title=f"📋 {title}",
        color=0x2F3136,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
    if details:
        embed.add_field(name="Details", value=details, inline=False)
    try:
        await ch.send(embed=embed)
    except:
        pass

# =============================================
#              QUEST RUNNER
# =============================================

async def run_quest(
    interaction: discord.Interaction,
    api: QuestAPI,
    info: dict,
    msg: discord.WebhookMessage,
    user_id: str
):
    try:
        await api.enroll(info["id"])

        user_data = await api.validate()
        tid = user_data.get("id", "0") if user_data else "0"
        stream_key = f"guild:0:channel:0:{tid}"

        total = (info["duration"] + 2) * 60
        interval = 30
        elapsed = 0

        while elapsed < total:
            if user_id not in bot.active_tasks:
                raise asyncio.CancelledError()

            ok = await api.heartbeat(info["id"], stream_key)

            pct = min((elapsed / total) * 100, 100)
            bar_len = 20
            filled = int(bar_len * elapsed // total)
            bar = "█" * filled + "░" * (bar_len - filled)
            remaining = max((total - elapsed) // 60, 0)

            embed = discord.Embed(
                title=f"🎮 Completing: {info['name']}",
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Progress",
                value=f"```\n[{bar}] {pct:.1f}%\n```",
                inline=False
            )
            embed.add_field(name="⏱️ Remaining", value=f"`{remaining} min`", inline=True)
            embed.add_field(name="💓 Heartbeat", value="✅ Sent" if ok else "❌ Failed", inline=True)
            embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
            embed.set_footer(text=f"Quest ID: {info['id']}")

            try:
                await msg.edit(embed=embed)
            except:
                pass

            await asyncio.sleep(interval)
            elapsed += interval

        # Terminal heartbeat
        await api.heartbeat(info["id"], stream_key, terminal=True)

        # Done
        embed = discord.Embed(
            title=f"✅ Quest Completed!",
            description=f"**{info['name']}** has been completed successfully!",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(
            name="Progress",
            value=f"```\n[{'█' * 20}] 100.0%\n```",
            inline=False
        )
        embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
        embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)

        try:
            await msg.edit(embed=embed)
        except:
            pass

        await interaction.channel.send(
            f"🎉 {interaction.user.mention} Your quest **{info['name']}** is now complete!"
        )

        if interaction.guild:
            await send_log(interaction.guild, "Quest Completed", interaction.user, f"Quest: {info['name']}")

    except asyncio.CancelledError:
        embed = discord.Embed(
            title="⏹️ Quest Stopped",
            description=f"**{info['name']}** was stopped.",
            color=0xFEE75C
        )
        try:
            await msg.edit(embed=embed)
        except:
            pass

    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred: `{e}`",
            color=0xED4245
        )
        try:
            await msg.edit(embed=embed)
        except:
            pass

    finally:
        if user_id in bot.active_tasks:
            del bot.active_tasks[user_id]

# =============================================
#              SLASH COMMANDS
# =============================================

# ---------- /help ----------
@bot.tree.command(name="help", description="View all bot commands and info")
async def slash_help(interaction: discord.Interaction):
    cfg = load_config()
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)

    embed = discord.Embed(
        title="🎮 Quest Completer Bot",
        description="Complete Discord quests easily with this bot!",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="🔑 Token Management",
        value=(
            "`/settoken` — Set your Discord token (**DM only**)\n"
            "`/removetoken` — Remove your stored token\n"
            "`/checktoken` — Verify if your token is valid"
        ),
        inline=False
    )

    embed.add_field(
        name="🆓 Free Commands",
        value=(
            "`/quests` — View all available quests\n"
            "`/questinfo` — View detailed quest info\n"
            "`/complete` — Complete a quest (**once per 24h**)\n"
            "`/completeall` — Complete all quests (**once per 24h**)"
        ),
        inline=False
    )

    embed.add_field(
        name=f"👑 Premium Commands {'(<@&' + str(role_id) + '>)' if role_id else ''}",
        value=(
            "`/complete` — Complete quests **unlimited**\n"
            "`/completeall` — Complete all quests **unlimited**\n"
            "`/stop` — Stop a running quest\n\n"
            "**No cooldown for premium users!**"
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Admin Commands",
        value=(
            "`/setpremiumrole` — Set the premium role\n"
            "`/setlogchannel` — Set the log channel\n"
            "`/setcooldown` — Set free user cooldown\n"
            "`/settings` — View current bot settings\n"
            "`/resetcooldown` — Reset a user's cooldown"
        ),
        inline=False
    )

    embed.set_footer(text="⚠️ Always set your token via DM for security!")
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    await interaction.response.send_message(embed=embed)


# ---------- /settoken ----------
@bot.tree.command(name="settoken", description="Set your Discord token (DM only for security)")
@app_commands.describe(token="Your Discord user token")
async def slash_settoken(interaction: discord.Interaction, token: str):
    # If used in server, warn
    if interaction.guild:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Security Warning!",
                description=(
                    "**Never send your token in a server!**\n\n"
                    "Please use `/settoken` in my **DMs** instead.\n\n"
                    "If you just sent your token here, **change it immediately!**"
                ),
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    api = QuestAPI(token)
    user_data = await api.validate()

    if not user_data:
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Invalid Token",
                description="This token is not valid. Please double-check and try again.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    tokens = load_json("tokens.json")
    tokens[str(interaction.user.id)] = token
    save_json("tokens.json", tokens)

    username = user_data.get("username", "Unknown")
    uid = user_data.get("id", "Unknown")

    embed = discord.Embed(
        title="✅ Token Saved Successfully!",
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Account", value=f"`{username}`", inline=True)
    embed.add_field(name="ID", value=f"`{uid}`", inline=True)
    embed.add_field(name="Token", value=f"`{mask_token(token)}`", inline=False)
    embed.set_footer(text="You can now use quest commands in the server!")

    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /removetoken ----------
@bot.tree.command(name="removetoken", description="Remove your stored token")
async def slash_removetoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid in tokens:
        del tokens[uid]
        save_json("tokens.json", tokens)
        embed = discord.Embed(
            title="✅ Token Removed",
            description="Your token has been deleted from the database.",
            color=0x57F287
        )
    else:
        embed = discord.Embed(
            title="❌ No Token Found",
            description="You don't have a token stored. Use `/settoken` first.",
            color=0xED4245
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- /checktoken ----------
@bot.tree.command(name="checktoken", description="Check if your stored token is valid")
async def slash_checktoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Token Found",
                description="Use `/settoken` in DMs to set your token first.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    api = QuestAPI(tokens[uid])
    user_data = await api.validate()

    if user_data:
        embed = discord.Embed(title="✅ Token is Valid", color=0x57F287)
        embed.add_field(name="Account", value=f"`{user_data.get('username', 'Unknown')}`", inline=True)
        embed.add_field(name="Token", value=f"`{mask_token(tokens[uid])}`", inline=True)
    else:
        embed = discord.Embed(
            title="❌ Token is Invalid / Expired",
            description="Please set a new token with `/settoken` in DMs.",
            color=0xED4245
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /quests ----------
@bot.tree.command(name="quests", description="View all available quests on your account")
async def slash_quests(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Token",
                description="Set your token first with `/settoken` in DMs.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer()

    api = QuestAPI(tokens[uid])
    quests = await api.get_quests()

    if not quests:
        await interaction.followup.send(
            embed=discord.Embed(
                title="📋 No Quests Available",
                description="There are no quests available on your account right now.",
                color=0x2F3136
            )
        )
        return

    embed = discord.Embed(
        title="📋 Available Quests",
        description=f"Found **{len(quests)}** quest(s) on your account.",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    for i, q in enumerate(quests, 1):
        info = api.parse(q)
        emoji, status = get_status_emoji(info)

        embed.add_field(
            name=f"#{i} • {info['name']}",
            value=(
                f"```{info['description']}```\n"
                f"📊 Type: `{info['type']}` • "
                f"⏱️ Duration: `{info['duration']} min`\n"
                f"📌 Status: {emoji} {status}"
            ),
            inline=False
        )

    premium = is_premium(interaction.user) if interaction.guild else False
    tier = "👑 Premium — Unlimited" if premium else "🆓 Free — Once per 24h"
    embed.set_footer(text=f"Your tier: {tier} • Use /complete <number> to start")

    await interaction.followup.send(embed=embed)


# ---------- /questinfo ----------
@bot.tree.command(name="questinfo", description="View detailed information about a specific quest")
@app_commands.describe(number="Quest number from /quests list")
async def slash_questinfo(interaction: discord.Interaction, number: int):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Token",
                description="Set your token first with `/settoken` in DMs.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer()

    api = QuestAPI(tokens[uid])
    quests = await api.get_quests()

    if not quests or number < 1 or number > len(quests):
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Invalid Quest Number",
                description=f"Use `/quests` to see valid quest numbers (1-{len(quests)}).",
                color=0xED4245
            )
        )
        return

    info = api.parse(quests[number - 1])
    emoji, status = get_status_emoji(info)

    color_map = {"🎁": 0xFEE75C, "✅": 0x57F287, "📝": 0x5865F2, "⏳": 0x95A5A6}

    embed = discord.Embed(
        title=f"🎮 {info['name']}",
        description=f"```{info['description']}```",
        color=color_map.get(emoji, 0x2F3136),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
    embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
    embed.add_field(name="📌 Status", value=f"{emoji} {status}", inline=True)
    embed.add_field(name="🆔 Quest ID", value=f"`{info['id']}`", inline=True)
    embed.add_field(name="🎯 App ID", value=f"`{info['app_id'] or 'N/A'}`", inline=True)

    if info["progress"]:
        embed.add_field(
            name="📈 Progress Data",
            value=f"```json\n{json.dumps(info['progress'], indent=2)[:500]}```",
            inline=False
        )

    await interaction.followup.send(embed=embed)


# ---------- /complete ----------
@bot.tree.command(name="complete", description="Complete a specific quest")
@app_commands.describe(number="Quest number from /quests list")
async def slash_complete(interaction: discord.Interaction, number: int):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Token",
                description="Set your token first with `/settoken` in DMs.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    # Check active task
    if uid in bot.active_tasks:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ Quest Already Running",
                description="You already have a quest in progress. Use `/stop` first.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    # Cooldown check for free users
    premium = is_premium(interaction.user) if interaction.guild else False
    if not premium:
        on_cd, remaining = check_cooldown(uid)
        if on_cd:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Cooldown Active",
                    description=(
                        f"Free users can complete quests **once every 24 hours**.\n\n"
                        f"⏱️ Time remaining: **{format_time(remaining)}**\n\n"
                        f"👑 **Get Premium** to remove this cooldown!"
                    ),
                    color=0xFEE75C
                ),
                ephemeral=True
            )
            return

    await interaction.response.defer()

    api = QuestAPI(tokens[uid])
    quests = await api.get_quests()

    if not quests or number < 1 or number > len(quests):
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Invalid Quest Number",
                description=f"Use `/quests` to see valid numbers (1-{len(quests) if quests else 0}).",
                color=0xED4245
            )
        )
        return

    info = api.parse(quests[number - 1])

    if info["claimed"]:
        await interaction.followup.send(
            embed=discord.Embed(
                title="🎁 Already Claimed",
                description=f"**{info['name']}** reward has already been claimed.",
                color=0xFEE75C
            )
        )
        return

    if info["completed"]:
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Already Completed",
                description=f"**{info['name']}** is already completed.",
                color=0x57F287
            )
        )
        return

    # Set cooldown for free users
    if not premium:
        set_cooldown(uid)

    # Start embed
    embed = discord.Embed(
        title=f"🚀 Starting Quest: {info['name']}",
        description=f"```{info['description']}```",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
    embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
    embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
    embed.add_field(name="📌 Status", value="🔄 Initializing...", inline=False)

    msg = await interaction.followup.send(embed=embed, wait=True)

    if interaction.guild:
        await send_log(interaction.guild, "Quest Started", interaction.user, f"Quest: {info['name']}")

    task = asyncio.create_task(run_quest(interaction, api, info, msg, uid))
    bot.active_tasks[uid] = task


# ---------- /completeall ----------
@bot.tree.command(name="completeall", description="Complete all available quests")
async def slash_completeall(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)

    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Token",
                description="Set your token first with `/settoken` in DMs.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    if uid in bot.active_tasks:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ Quest Already Running",
                description="You already have a quest in progress. Use `/stop` first.",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    premium = is_premium(interaction.user) if interaction.guild else False
    if not premium:
        on_cd, remaining = check_cooldown(uid)
        if on_cd:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Cooldown Active",
                    description=(
                        f"Free users can use this **once every 24 hours**.\n\n"
                        f"⏱️ Time remaining: **{format_time(remaining)}**\n\n"
                        f"👑 **Get Premium** to remove this cooldown!"
                    ),
                    color=0xFEE75C
                ),
                ephemeral=True
            )
            return

    await interaction.response.defer()

    api = QuestAPI(tokens[uid])
    quests = await api.get_quests()

    if not quests:
        await interaction.followup.send(
            embed=discord.Embed(
                title="📋 No Quests",
                description="No quests available on your account.",
                color=0x2F3136
            )
        )
        return

    incomplete = [(q, api.parse(q)) for q in quests
                  if not api.parse(q)["completed"] and not api.parse(q)["claimed"]]

    if not incomplete:
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ All Done",
                description="All quests are already completed!",
                color=0x57F287
            )
        )
        return

    if not premium:
        set_cooldown(uid)

    embed = discord.Embed(
        title="🚀 Completing All Quests",
        description=f"**{len(incomplete)}** quest(s) will be completed one by one.",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    for _, info in incomplete:
        emoji, status = get_status_emoji(info)
        embed.add_field(
            name=info["name"],
            value=f"⏱️ `{info['duration']} min` • 📊 `{info['type']}`",
            inline=False
        )
    embed.set_footer(text=f"Requested by {interaction.user.name}")

    await interaction.followup.send(embed=embed)

    if interaction.guild:
        await send_log(
            interaction.guild, "Complete All Started",
            interaction.user, f"{len(incomplete)} quest(s)"
        )

    for quest, info in incomplete:
        if uid in bot.active_tasks:
            try:
                await bot.active_tasks[uid]
            except:
                pass

        msg = await interaction.channel.send(
            embed=discord.Embed(
                title=f"🔄 Starting: {info['name']}",
                color=0x5865F2
            )
        )

        task = asyncio.create_task(run_quest(interaction, api, info, msg, uid))
        bot.active_tasks[uid] = task

        try:
            await task
        except:
            pass

        await asyncio.sleep(3)

    await interaction.channel.send(
        f"🎉 {interaction.user.mention} All quests have been completed!"
    )


# ---------- /stop ----------
@bot.tree.command(name="stop", description="Stop your currently running quest")
async def slash_stop(interaction: discord.Interaction):
    uid = str(interaction.user.id)

    if uid in bot.active_tasks:
        bot.active_tasks[uid].cancel()
        del bot.active_tasks[uid]

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏹️ Quest Stopped",
                description="Your running quest has been stopped.",
                color=0xFEE75C
            )
        )

        if interaction.guild:
            await send_log(interaction.guild, "Quest Stopped", interaction.user)
    else:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Active Quest",
                description="You don't have any quest running.",
                color=0xED4245
            ),
            ephemeral=True
        )


# =============================================
#              ADMIN COMMANDS
# =============================================

# ---------- /setpremiumrole ----------
@bot.tree.command(name="setpremiumrole", description="Set the premium role for unlimited quest access")
@app_commands.describe(role="The role to set as premium")
@app_commands.default_permissions(administrator=True)
async def slash_setpremiumrole(interaction: discord.Interaction, role: discord.Role):
    cfg = load_config()
    cfg["PREMIUM_ROLE_ID"] = role.id
    save_config(cfg)

    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Premium Role Updated",
            description=f"Premium role set to {role.mention}\n\nUsers with this role get **unlimited** quest completions.",
            color=0x57F287
        )
    )

    await send_log(interaction.guild, "Premium Role Updated", interaction.user, f"Role: {role.name} (`{role.id}`)")


# ---------- /setlogchannel ----------
@bot.tree.command(name="setlogchannel", description="Set the channel for bot activity logs")
@app_commands.describe(channel="The channel for logs")
@app_commands.default_permissions(administrator=True)
async def slash_setlogchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = load_config()
    cfg["LOG_CHANNEL_ID"] = channel.id
    save_config(cfg)

    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Log Channel Updated",
            description=f"Logs will now be sent to {channel.mention}",
            color=0x57F287
        )
    )


# ---------- /setcooldown ----------
@bot.tree.command(name="setcooldown", description="Set the cooldown hours for free users")
@app_commands.describe(hours="Cooldown in hours (default: 24)")
@app_commands.default_permissions(administrator=True)
async def slash_setcooldown(interaction: discord.Interaction, hours: int):
    if hours < 1:
        await interaction.response.send_message("❌ Minimum 1 hour.", ephemeral=True)
        return

    cfg = load_config()
    cfg["FREE_COOLDOWN_HOURS"] = hours
    save_config(cfg)

    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Cooldown Updated",
            description=f"Free users can now use quest commands once every **{hours} hours**.",
            color=0x57F287
        )
    )


# ---------- /resetcooldown ----------
@bot.tree.command(name="resetcooldown", description="Reset a user's cooldown timer")
@app_commands.describe(user="The user to reset cooldown for")
@app_commands.default_permissions(administrator=True)
async def slash_resetcooldown(interaction: discord.Interaction, user: discord.Member):
    cooldowns = load_json("cooldowns.json")
    uid = str(user.id)

    if uid in cooldowns:
        del cooldowns[uid]
        save_json("cooldowns.json", cooldowns)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Cooldown Reset",
                description=f"Cooldown for {user.mention} has been reset.",
                color=0x57F287
            )
        )
    else:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="ℹ️ No Cooldown",
                description=f"{user.mention} doesn't have an active cooldown.",
                color=0x5865F2
            ),
            ephemeral=True
        )


# ---------- /settings ----------
@bot.tree.command(name="settings", description="View current bot configuration")
@app_commands.default_permissions(administrator=True)
async def slash_settings(interaction: discord.Interaction):
    cfg = load_config()
    tokens = load_json("tokens.json")
    cooldowns = load_json("cooldowns.json")

    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    log_id = cfg.get("LOG_CHANNEL_ID", 0)
    cd_hours = cfg.get("FREE_COOLDOWN_HOURS", 24)

    embed = discord.Embed(
        title="⚙️ Bot Settings",
        color=0x2F3136,
        timestamp=datetime.utcnow()
    )
    embed.add_field(
        name="👑 Premium Role",
        value=f"<@&{role_id}>" if role_id else "`Not Set`",
        inline=True
    )
    embed.add_field(
        name="📋 Log Channel",
        value=f"<#{log_id}>" if log_id else "`Not Set`",
        inline=True
    )
    embed.add_field(
        name="⏱️ Free Cooldown",
        value=f"`{cd_hours} hours`",
        inline=True
    )
    embed.add_field(
        name="🔑 Stored Tokens",
        value=f"`{len(tokens)}` user(s)",
        inline=True
    )
    embed.add_field(
        name="🔄 Active Quests",
        value=f"`{len(bot.active_tasks)}` running",
        inline=True
    )
    embed.add_field(
        name="⏳ Active Cooldowns",
        value=f"`{len(cooldowns)}` user(s)",
        inline=True
    )

    await interaction.response.send_message(embed=embed)


# =============================================
#              BOT EVENTS
# =============================================

@bot.event
async def on_ready():
    print(f"""
╔═══════════════════════════════════════════════════╗
║          Discord Quest Completer Bot              ║
║                                                   ║
║   Bot:     {bot.user.name:<40}║
║   ID:      {str(bot.user.id):<40}║
║   Servers: {str(len(bot.guilds)):<40}║
║   Time:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<40}║
║                                                   ║
║   Status:  ✅ Online & Ready                       ║
╚═══════════════════════════════════════════════════╝
    """)

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/help | Quest Completer"
        )
    )


# =============================================
#              RUN BOT
# =============================================

if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════╗
    ║                                            ║
    ║      Quest Completer Bot v3.0              ║
    ║      Premium + Free Tier System            ║
    ║                                            ║
    ╚════════════════════════════════════════════╝
    """)

    token = config.get("BOT_TOKEN", "")

    if not token or token == "YOUR_BOT_TOKEN_HERE":
        print("  [❌] BOT_TOKEN not found in config.json!")
        token = input("  Enter your bot token: ").strip()
        if token:
            config["BOT_TOKEN"] = token
            save_config(config)
        else:
            print("  [❌] No token provided. Exiting...")
            exit(1)

    bot.run(token)
