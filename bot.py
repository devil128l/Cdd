import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import httpx
import os
import time
from datetime import datetime

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
        print("[✅] Synced slash commands")
        auto_quest_loop.start()
        print("[✅] Auto quest detector started")

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
        dur = (
            cfg.get("stream_duration_requirement_minutes", 0)
            or cfg.get("task_duration_minutes", 0)
            or 15
        )
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

def is_bot_owner(user_id: int) -> bool:
    cfg = load_config()
    return user_id in cfg.get("OWNER_IDS", [])

def check_cooldown(user_id: str) -> tuple:
    cooldowns = load_json("cooldowns.json")
    cfg = load_config()
    hours = cfg.get("FREE_COOLDOWN_HOURS", 24)
    if user_id in cooldowns:
        elapsed = time.time() - cooldowns[user_id]
        required = hours * 3600
        if elapsed < required:
            return True, required - elapsed
    return False, 0

def set_cooldown(user_id: str):
    cooldowns = load_json("cooldowns.json")
    cooldowns[user_id] = time.time()
    save_json("cooldowns.json", cooldowns)

def delete_token(user_id: str):
    tokens = load_json("tokens.json")
    if user_id in tokens:
        del tokens[user_id]
        save_json("tokens.json", tokens)

def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    parts = []
    if h > 0: parts.append(f"{h}h")
    if m > 0: parts.append(f"{m}m")
    if s > 0: parts.append(f"{s}s")
    return " ".join(parts) or "0s"

def get_status_emoji(info: dict) -> tuple:
    if info["claimed"]:     return "🎁", "Claimed"
    elif info["completed"]: return "✅", "Completed"
    elif info["enrolled"]:  return "📝", "Enrolled"
    else:                   return "⏳", "Available"

def owner_only_embed():
    return discord.Embed(
        title="🔒 Bot Owner Only",
        description="This command is restricted to the **bot owner** only.",
        color=0xED4245
    )

async def send_log(guild, title, user, details=""):
    cfg = load_config()
    ch_id = cfg.get("LOG_CHANNEL_ID", 0)
    if ch_id == 0: return
    ch = guild.get_channel(ch_id)
    if not ch: return
    embed = discord.Embed(title=f"📋 {title}", color=0x2F3136, timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
    if details:
        embed.add_field(name="Details", value=details, inline=False)
    try: await ch.send(embed=embed)
    except: pass

async def send_dm(user, embed):
    try:
        await user.send(embed=embed)
    except:
        pass

# =============================================
#         QUEST COMPLETION ENGINE
# =============================================

async def complete_single_quest(api: QuestAPI, info: dict, user_id: str) -> bool:
    """Background mein single quest complete karo"""
    try:
        await api.enroll(info["id"])
        user_data = await api.validate()
        tid = user_data.get("id", "0") if user_data else "0"
        stream_key = f"guild:0:channel:0:{tid}"

        total = (info["duration"] + 2) * 60
        interval = 30
        elapsed = 0

        while elapsed < total:
            if user_id in bot.active_tasks and bot.active_tasks[user_id] == "cancelled":
                del bot.active_tasks[user_id]
                return False

            await api.heartbeat(info["id"], stream_key)
            await asyncio.sleep(interval)
            elapsed += interval

        await api.heartbeat(info["id"], stream_key, terminal=True)
        return True

    except Exception as e:
        print(f"[AUTO] ❌ Error: {info['name']} — {e}")
        return False


# =============================================
#    🤖 AUTO QUEST DETECTOR & COMPLETER
# =============================================

@tasks.loop(minutes=5)
async def auto_quest_loop():
    """
    Har 5 minute mein:
    1. Saare premium users ke tokens check karo
    2. Unki quests fetch karo
    3. Naye quests detect karo (jo pehle nahi the)
    4. Naye quests ko AUTOMATICALLY complete karo
    5. User ko DM notification bhejo
    """
    cfg = load_config()
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    if role_id == 0:
        return

    tokens = load_json("tokens.json")
    if not tokens:
        return

    seen_quests = load_json("seen_quests.json")

    print(f"\n{'='*50}")
    print(f"[AUTO] 🔍 Scanning for NEW quests... ({datetime.now().strftime('%H:%M:%S')})")
    print(f"[AUTO] 👥 Checking {len(tokens)} premium user(s)...")
    print(f"{'='*50}")

    for user_id_str, token in list(tokens.items()):
        user_id_int = int(user_id_str)

        # Check premium role in any guild
        is_prem = False
        user_member = None
        for guild in bot.guilds:
            member = guild.get_member(user_id_int)
            if member and any(r.id == role_id for r in member.roles):
                is_prem = True
                user_member = member
                break

        if not is_prem:
            continue

        api = QuestAPI(token)

        # Validate token
        user_data = await api.validate()
        if not user_data:
            print(f"[AUTO] ⚠️ Invalid token for {user_id_str}")
            continue

        username = user_data.get("username", "Unknown")
        quests = await api.get_quests()

        if not quests:
            print(f"[AUTO] ✅ {username} — No quests available")
            continue

        # Initialize user's seen list
        if user_id_str not in seen_quests:
            seen_quests[user_id_str] = []

        user_seen = seen_quests[user_id_str]
        new_quests_found = 0
        completed_count = 0

        for quest in quests:
            info = api.parse(quest)
            quest_id = info["id"]

            # Skip if already completed/claimed
            if info["completed"] or info["claimed"]:
                if quest_id not in user_seen:
                    user_seen.append(quest_id)
                continue

            # Check if this quest is NEW (not seen before)
            if quest_id not in user_seen:
                new_quests_found += 1
                print(f"\n[AUTO] 🆕 NEW QUEST DETECTED!")
                print(f"[AUTO]    User: {username}")
                print(f"[AUTO]    Quest: {info['name']}")
                print(f"[AUTO]    Type: {info['type']}")
                print(f"[AUTO]    Duration: {info['duration']} min")
                print(f"[AUTO]    Starting auto-completion...")

                # Mark as seen BEFORE completing (prevent duplicate)
                user_seen.append(quest_id)
                save_json("seen_quests.json", seen_quests)

                # Send "Quest Detected" DM
                detect_embed = discord.Embed(
                    title="🆕 New Quest Detected!",
                    description=f"A new quest just appeared on your account!\n\n**{info['name']}**",
                    color=0x5865F2,
                    timestamp=datetime.utcnow()
                )
                detect_embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
                detect_embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
                detect_embed.add_field(name="🤖 Status", value="`Auto-completing...`", inline=False)
                detect_embed.set_footer(text="Quest Completer Bot • Premium Auto-Detect")
                await send_dm(user_member, detect_embed)

                # Auto complete
                success = await complete_single_quest(api, info, user_id_str)

                if success:
                    completed_count += 1
                    print(f"[AUTO] ✅ '{info['name']}' COMPLETED for {username}!")

                    # Send "Completed" DM
                    done_embed = discord.Embed(
                        title="✅ Quest Auto-Completed!",
                        description=f"Your new quest has been automatically completed!",
                        color=0x57F287,
                        timestamp=datetime.utcnow()
                    )
                    done_embed.add_field(name="🎮 Quest", value=f"**{info['name']}**", inline=False)
                    done_embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
                    done_embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
                    done_embed.add_field(
                        name="📈 Progress",
                        value=f"```\n[{'█' * 20}] 100%\n```",
                        inline=False
                    )
                    done_embed.set_footer(text="Auto-Detected & Auto-Completed • Premium Feature")
                    await send_dm(user_member, done_embed)

                    # Log in server
                    for guild in bot.guilds:
                        await send_log(
                            guild, "Auto Quest Completed",
                            user_member,
                            f"🆕 New quest **{info['name']}** auto-detected & completed"
                        )
                else:
                    print(f"[AUTO] ❌ Failed to complete '{info['name']}' for {username}")

                    fail_embed = discord.Embed(
                        title="⚠️ Auto-Complete Failed",
                        description=f"Could not complete **{info['name']}**.\nWill retry next cycle.",
                        color=0xED4245
                    )
                    await send_dm(user_member, fail_embed)

                    # Remove from seen so it retries
                    if quest_id in user_seen:
                        user_seen.remove(quest_id)
                        save_json("seen_quests.json", seen_quests)

                await asyncio.sleep(5)

            else:
                # Already seen quest, check if still incomplete
                if not info["completed"] and not info["claimed"]:
                    print(f"[AUTO] 🔄 '{info['name']}' still in progress for {username}, retrying...")
                    success = await complete_single_quest(api, info, user_id_str)
                    if success:
                        completed_count += 1
                        print(f"[AUTO] ✅ '{info['name']}' completed on retry!")

        # Summary
        if new_quests_found > 0:
            print(f"\n[AUTO] 🎉 {username}: {new_quests_found} new quest(s) found, {completed_count} completed!")
        else:
            print(f"[AUTO] ✅ {username}: No new quests (all {len(quests)} already tracked)")

    print(f"\n[AUTO] ✅ Scan complete! Next scan in {cfg.get('AUTO_CHECK_INTERVAL', 5)} min\n")


@auto_quest_loop.before_loop
async def before_auto_loop():
    await bot.wait_until_ready()
    print("[AUTO] ⏳ First scan in 30 seconds...")
    await asyncio.sleep(30)


# =============================================
#              QUEST RUNNER (Interactive)
# =============================================

async def run_quest(interaction, api, info, msg, user_id, is_prem):
    try:
        await api.enroll(info["id"])
        user_data = await api.validate()
        tid = user_data.get("id", "0") if user_data else "0"
        stream_key = f"guild:0:channel:0:{tid}"

        total = (info["duration"] + 2) * 60
        interval = 30
        elapsed = 0

        while elapsed < total:
            if user_id in bot.active_tasks and bot.active_tasks[user_id] == "cancelled":
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
            embed.add_field(name="Progress", value=f"```\n[{bar}] {pct:.1f}%\n```", inline=False)
            embed.add_field(name="⏱️ Remaining", value=f"`{remaining} min`", inline=True)
            embed.add_field(name="💓 Heartbeat", value="✅" if ok else "❌", inline=True)
            embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
            tier_text = "👑 Premium" if is_prem else "🆓 Free (token deleted after)"
            embed.set_footer(text=f"{tier_text} • Quest ID: {info['id']}")

            try: await msg.edit(embed=embed)
            except: pass

            await asyncio.sleep(interval)
            elapsed += interval

        await api.heartbeat(info["id"], stream_key, terminal=True)

        embed = discord.Embed(
            title="✅ Quest Completed!",
            description=f"**{info['name']}** completed!",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Progress", value=f"```\n[{'█' * 20}] 100.0%\n```", inline=False)
        embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)

        if not is_prem:
            embed.add_field(name="🔑 Token", value="`🗑️ Deleted`", inline=True)
        else:
            embed.add_field(name="🔑 Token", value="`💾 Saved`", inline=True)

        try: await msg.edit(embed=embed)
        except: pass

        await interaction.channel.send(f"🎉 {interaction.user.mention} Quest **{info['name']}** done!")

        if interaction.guild:
            await send_log(interaction.guild, "Quest Completed", interaction.user, f"Quest: {info['name']}")

    except asyncio.CancelledError:
        embed = discord.Embed(title="⏹️ Quest Stopped", color=0xFEE75C)
        try: await msg.edit(embed=embed)
        except: pass

    except Exception as e:
        embed = discord.Embed(title="❌ Error", description=f"`{e}`", color=0xED4245)
        try: await msg.edit(embed=embed)
        except: pass

    finally:
        if user_id in bot.active_tasks:
            del bot.active_tasks[user_id]

        if not is_prem:
            delete_token(str(interaction.user.id))
            await send_dm(
                interaction.user,
                discord.Embed(
                    title="🔑 Token Deleted",
                    description="Your token was deleted after use. Provide it again next time.\n👑 **Get Premium** for permanent token + auto-detect!",
                    color=0xFEE75C
                )
            )

# =============================================
#              SLASH COMMANDS
# =============================================

@bot.tree.command(name="help", description="View all commands")
async def slash_help(interaction: discord.Interaction):
    cfg = load_config()
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    owners = cfg.get("OWNER_IDS", [])
    interval = cfg.get("AUTO_CHECK_INTERVAL", 5)
    owner_mentions = ", ".join([f"<@{o}>" for o in owners]) if owners else "Not set"

    embed = discord.Embed(
        title="🎮 Quest Completer Bot v7.0",
        description="Auto-detect & auto-complete new quests!",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.add_field(
        name="🔑 Token",
        value=(
            "`/settoken` — Save token permanently (**Premium, DM only**)\n"
            "`/removetoken` — Remove token\n"
            "`/checktoken` — Check token validity"
        ),
        inline=False
    )
    embed.add_field(
        name="🆓 Free Users",
        value=(
            "`/quests <token>` — View quests\n"
            "`/complete <number> <token>` — Complete quest\n"
            "`/completeall <token>` — Complete all\n"
            "`/stop` — Stop running quest\n\n"
            "⚠️ Token **deleted** after each use\n"
            "⚠️ Must provide token **every time**\n"
            "⏳ 24h cooldown"
        ),
        inline=False
    )
    embed.add_field(
        name=f"👑 Premium {'(<@&' + str(role_id) + '>)' if role_id else ''}",
        value=(
            "💾 Token saved **permanently**\n"
            "⚡ **No cooldown**\n"
            "🆕 **Auto-detects NEW quests**\n"
            f"🤖 **Auto-completes every {interval} min**\n"
            "📩 **DM notification on new quest**\n"
            "🔄 **Retries failed quests automatically**"
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Owner Commands 🔒",
        value=(
            "`/setpremiumrole` • `/setcooldown`\n"
            "`/setlogchannel` • `/setautointerval`\n"
            "`/resetcooldown` • `/settings`\n\n"
            f"**Owners:** {owner_mentions}"
        ),
        inline=False
    )
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    await interaction.response.send_message(embed=embed)


# ---------- /settoken ----------
@bot.tree.command(name="settoken", description="Save token permanently (Premium only, DM only)")
@app_commands.describe(token="Your Discord user token")
async def slash_settoken(interaction: discord.Interaction, token: str):
    if interaction.guild:
        await interaction.response.send_message(
            embed=discord.Embed(title="⚠️ Use in DMs only!", color=0xED4245),
            ephemeral=True
        )
        return

    is_prem = False
    for guild in bot.guilds:
        member = guild.get_member(interaction.user.id)
        if member and is_premium(member):
            is_prem = True
            break

    if not is_prem:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="👑 Premium Only",
                description="Permanent token saving is a **Premium** feature.\nFree users must provide token with each command.",
                color=0xFEE75C
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    api = QuestAPI(token)
    user_data = await api.validate()

    if not user_data:
        await interaction.followup.send(embed=discord.Embed(title="❌ Invalid Token", color=0xED4245), ephemeral=True)
        return

    tokens = load_json("tokens.json")
    tokens[str(interaction.user.id)] = token
    save_json("tokens.json", tokens)

    embed = discord.Embed(title="✅ Token Saved Permanently!", color=0x57F287, timestamp=datetime.utcnow())
    embed.add_field(name="Account", value=f"`{user_data.get('username', 'Unknown')}`", inline=True)
    embed.add_field(name="Token", value=f"`{mask_token(token)}`", inline=False)
    embed.add_field(
        name="🤖 Auto-Detect",
        value="**ENABLED** — New quests will be auto-detected & completed!",
        inline=False
    )
    embed.set_footer(text="You'll get a DM whenever a new quest appears!")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /removetoken ----------
@bot.tree.command(name="removetoken", description="Remove your stored token")
async def slash_removetoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)
    if uid in tokens:
        del tokens[uid]
        save_json("tokens.json", tokens)
        # Also remove seen quests
        seen = load_json("seen_quests.json")
        if uid in seen:
            del seen[uid]
            save_json("seen_quests.json", seen)
        embed = discord.Embed(title="✅ Token & Data Removed", color=0x57F287)
    else:
        embed = discord.Embed(title="❌ No Token Found", color=0xED4245)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- /checktoken ----------
@bot.tree.command(name="checktoken", description="Check if your token is valid")
async def slash_checktoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)
    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(title="❌ No Saved Token", color=0xED4245), ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    api = QuestAPI(tokens[uid])
    user_data = await api.validate()
    if user_data:
        embed = discord.Embed(title="✅ Token Valid", color=0x57F287)
        embed.add_field(name="Account", value=f"`{user_data.get('username', 'Unknown')}`", inline=True)
        embed.add_field(name="Token", value=f"`{mask_token(tokens[uid])}`", inline=True)
        embed.add_field(name="💾 Status", value="`Saved + Auto-Detect Active`", inline=False)
    else:
        embed = discord.Embed(title="❌ Token Invalid", color=0xED4245)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /quests ----------
@bot.tree.command(name="quests", description="View available quests")
@app_commands.describe(token="Your token (free users must provide)")
async def slash_quests(interaction: discord.Interaction, token: str = None):
    uid = str(interaction.user.id)
    tokens = load_json("tokens.json")
    is_prem = is_premium(interaction.user) if interaction.guild else False

    use_token = None
    if is_prem and uid in tokens:
        use_token = tokens[uid]
    elif token:
        use_token = token
    else:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ Token Required",
                description="🆓 Free: `/quests <token>`\n👑 Premium: `/settoken` in DMs",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    api = QuestAPI(use_token)
    quests = await api.get_quests()

    if not quests:
        await interaction.followup.send(embed=discord.Embed(title="📋 No Quests", color=0x2F3136), ephemeral=True)
        return

    embed = discord.Embed(title="📋 Available Quests", description=f"**{len(quests)}** quest(s)", color=0x5865F2, timestamp=datetime.utcnow())
    for i, q in enumerate(quests, 1):
        info = api.parse(q)
        emoji, status = get_status_emoji(info)
        embed.add_field(name=f"#{i} • {info['name']}", value=f"📊 `{info['type']}` • ⏱️ `{info['duration']}m` • {emoji} {status}", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /complete ----------
@bot.tree.command(name="complete", description="Complete a specific quest")
@app_commands.describe(number="Quest number", token="Token (free users)")
async def slash_complete(interaction: discord.Interaction, number: int, token: str = None):
    uid = str(interaction.user.id)
    tokens = load_json("tokens.json")
    is_prem = is_premium(interaction.user) if interaction.guild else False

    use_token = None
    if is_prem and uid in tokens:
        use_token = tokens[uid]
    elif token:
        use_token = token
        if not is_prem:
            tokens[uid] = token
            save_json("tokens.json", tokens)
    else:
        await interaction.response.send_message(
            embed=discord.Embed(title="❌ Token Required", description="🆓 `/complete <#> <token>`\n👑 `/settoken` in DMs", color=0xED4245),
            ephemeral=True
        )
        return

    if uid in bot.active_tasks:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Quest Running", color=0xED4245), ephemeral=True)
        return

    if not is_prem:
        on_cd, remaining = check_cooldown(uid)
        if on_cd:
            delete_token(uid)
            await interaction.response.send_message(
                embed=discord.Embed(title="⏳ Cooldown", description=f"Remaining: **{format_time(remaining)}**", color=0xFEE75C),
                ephemeral=True
            )
            return

    await interaction.response.defer()
    api = QuestAPI(use_token)
    quests = await api.get_quests()

    if not quests or number < 1 or number > len(quests):
        if not is_prem: delete_token(uid)
        await interaction.followup.send(embed=discord.Embed(title="❌ Invalid Number", color=0xED4245))
        return

    info = api.parse(quests[number - 1])
    if info["claimed"] or info["completed"]:
        if not is_prem: delete_token(uid)
        await interaction.followup.send(embed=discord.Embed(title="✅ Already Done", color=0x57F287))
        return

    if not is_prem:
        set_cooldown(uid)

    embed = discord.Embed(title=f"🚀 Starting: {info['name']}", color=0x5865F2, timestamp=datetime.utcnow())
    embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
    embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
    msg = await interaction.followup.send(embed=embed, wait=True)

    if interaction.guild:
        await send_log(interaction.guild, "Quest Started", interaction.user, f"Quest: {info['name']}")

    bot.active_tasks[uid] = "running"
    asyncio.create_task(run_quest(interaction, api, info, msg, uid, is_prem))


# ---------- /completeall ----------
@bot.tree.command(name="completeall", description="Complete all quests")
@app_commands.describe(token="Token (free users)")
async def slash_completeall(interaction: discord.Interaction, token: str = None):
    uid = str(interaction.user.id)
    tokens = load_json("tokens.json")
    is_prem = is_premium(interaction.user) if interaction.guild else False

    use_token = None
    if is_prem and uid in tokens:
        use_token = tokens[uid]
    elif token:
        use_token = token
        if not is_prem:
            tokens[uid] = token
            save_json("tokens.json", tokens)
    else:
        await interaction.response.send_message(
            embed=discord.Embed(title="❌ Token Required", color=0xED4245), ephemeral=True
        )
        return

    if uid in bot.active_tasks:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Quest Running", color=0xED4245), ephemeral=True)
        return

    if not is_prem:
        on_cd, remaining = check_cooldown(uid)
        if on_cd:
            delete_token(uid)
            await interaction.response.send_message(
                embed=discord.Embed(title="⏳ Cooldown", description=f"Remaining: **{format_time(remaining)}**", color=0xFEE75C),
                ephemeral=True
            )
            return

    await interaction.response.defer()
    api = QuestAPI(use_token)
    quests = await api.get_quests()

    if not quests:
        if not is_prem: delete_token(uid)
        await interaction.followup.send(embed=discord.Embed(title="📋 No Quests", color=0x2F3136))
        return

    incomplete = [(q, api.parse(q)) for q in quests if not api.parse(q)["completed"] and not api.parse(q)["claimed"]]
    if not incomplete:
        if not is_prem: delete_token(uid)
        await interaction.followup.send(embed=discord.Embed(title="✅ All Done", color=0x57F287))
        return

    if not is_prem:
        set_cooldown(uid)

    embed = discord.Embed(title="🚀 Completing All", description=f"**{len(incomplete)}** quest(s)", color=0x5865F2)
    await interaction.followup.send(embed=embed)

    for quest, info in incomplete:
        if uid in bot.active_tasks and bot.active_tasks[uid] == "cancelled":
            break
        msg = await interaction.channel.send(embed=discord.Embed(title=f"🔄 {info['name']}", color=0x5865F2))
        bot.active_tasks[uid] = "running"
        await run_quest(interaction, api, info, msg, uid, is_prem)
        await asyncio.sleep(3)

    await interaction.channel.send(f"🎉 {interaction.user.mention} All done!")


# ---------- /stop ----------
@bot.tree.command(name="stop", description="Stop running quest")
async def slash_stop(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid in bot.active_tasks:
        bot.active_tasks[uid] = "cancelled"
        await interaction.response.send_message(embed=discord.Embed(title="⏹️ Stopping...", color=0xFEE75C))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="❌ No Active Quest", color=0xED4245), ephemeral=True)


# =============================================
#       ALL ADMIN — OWNER ONLY 🔒
# =============================================

@bot.tree.command(name="setpremiumrole", description="🔒 [OWNER] Set premium role")
@app_commands.describe(role="Premium role")
async def slash_setpremiumrole(interaction: discord.Interaction, role: discord.Role):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    cfg = load_config()
    cfg["PREMIUM_ROLE_ID"] = role.id
    save_config(cfg)
    await interaction.response.send_message(
        embed=discord.Embed(title="✅ Premium Role Set", description=f"{role.mention}", color=0x57F287)
    )

@bot.tree.command(name="setcooldown", description="🔒 [OWNER] Set free cooldown")
@app_commands.describe(hours="Hours (min 1)")
async def slash_setcooldown(interaction: discord.Interaction, hours: int):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    if hours < 1:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Min 1h", color=0xED4245), ephemeral=True)
        return
    cfg = load_config()
    old = cfg.get("FREE_COOLDOWN_HOURS", 24)
    cfg["FREE_COOLDOWN_HOURS"] = hours
    save_config(cfg)
    await interaction.response.send_message(
        embed=discord.Embed(title="✅ Cooldown Updated", description=f"`{old}h` → `{hours}h`", color=0x57F287)
    )

@bot.tree.command(name="setlogchannel", description="🔒 [OWNER] Set log channel")
@app_commands.describe(channel="Log channel")
async def slash_setlogchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    cfg = load_config()
    cfg["LOG_CHANNEL_ID"] = channel.id
    save_config(cfg)
    await interaction.response.send_message(
        embed=discord.Embed(title="✅ Log Channel Set", description=f"{channel.mention}", color=0x57F287)
    )

@bot.tree.command(name="setautointerval", description="🔒 [OWNER] Set auto-detect interval (minutes)")
@app_commands.describe(minutes="Minutes (min 3)")
async def slash_setautointerval(interaction: discord.Interaction, minutes: int):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    if minutes < 3:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Min 3 min", color=0xED4245), ephemeral=True)
        return
    cfg = load_config()
    cfg["AUTO_CHECK_INTERVAL"] = minutes
    save_config(cfg)
    auto_quest_loop.change_interval(minutes=minutes)
    await interaction.response.send_message(
        embed=discord.Embed(title="✅ Auto Interval Set", description=f"Scanning every **{minutes} minutes**", color=0x57F287)
    )

@bot.tree.command(name="resetcooldown", description="🔒 [OWNER] Reset user cooldown")
@app_commands.describe(user="User")
async def slash_resetcooldown(interaction: discord.Interaction, user: discord.Member):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    cooldowns = load_json("cooldowns.json")
    uid = str(user.id)
    if uid in cooldowns:
        del cooldowns[uid]
        save_json("cooldowns.json", cooldowns)
        await interaction.response.send_message(embed=discord.Embed(title="✅ Reset", description=f"{user.mention}", color=0x57F287))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="ℹ️ No Cooldown", color=0x5865F2), ephemeral=True)

@bot.tree.command(name="settings", description="🔒 [OWNER] View settings")
async def slash_settings(interaction: discord.Interaction):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    cfg = load_config()
    tokens = load_json("tokens.json")
    cooldowns = load_json("cooldowns.json")
    seen = load_json("seen_quests.json")
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    log_id = cfg.get("LOG_CHANNEL_ID", 0)
    owners = cfg.get("OWNER_IDS", [])

    total_seen = sum(len(v) for v in seen.values())

    embed = discord.Embed(title="⚙️ Bot Settings", color=0x2F3136, timestamp=datetime.utcnow())
    embed.add_field(name="🛡️ Owners", value=", ".join([f"<@{o}>" for o in owners]) or "None", inline=False)
    embed.add_field(name="👑 Premium Role", value=f"<@&{role_id}>" if role_id else "`Not Set`", inline=True)
    embed.add_field(name="📋 Log Channel", value=f"<#{log_id}>" if log_id else "`Not Set`", inline=True)
    embed.add_field(name="⏱️ Free Cooldown", value=f"`{cfg.get('FREE_COOLDOWN_HOURS', 24)}h`", inline=True)
    embed.add_field(name="🤖 Auto Scan", value=f"Every `{cfg.get('AUTO_CHECK_INTERVAL', 5)} min`", inline=True)
    embed.add_field(name="🔑 Saved Tokens", value=f"`{len(tokens)}`", inline=True)
    embed.add_field(name="👁️ Tracked Quests", value=f"`{total_seen}` total", inline=True)
    embed.add_field(name="⏳ Cooldowns", value=f"`{len(cooldowns)}`", inline=True)
    embed.add_field(name="🔄 Active Tasks", value=f"`{len(bot.active_tasks)}`", inline=True)
    await interaction.response.send_message(embed=embed)


# =============================================
#              BOT EVENTS
# =============================================

@bot.event
async def on_ready():
    cfg = load_config()
    owners = cfg.get("OWNER_IDS", [])
    interval = cfg.get("AUTO_CHECK_INTERVAL", 5)
    print(f"""
╔═══════════════════════════════════════════════════════╗
║       Quest Completer Bot v7.0 — AUTO DETECT          ║
║                                                       ║
║   Bot:     {bot.user.name:<44}║
║   ID:      {str(bot.user.id):<44}║
║   Servers: {str(len(bot.guilds)):<44}║
║   Owners:  {', '.join([str(o) for o in owners]):<44}║
║                                                       ║
║   🆕 Auto-Detect:  Every {interval} min{" " * 30}║
║   👑 Premium:      Permanent Token + Auto Complete    ║
║   🆓 Free:         Token Per Use + 24h Cooldown       ║
║                                                       ║
║   Status:  ✅ Online & Scanning                       ║
╚═══════════════════════════════════════════════════════╝
    """)
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="/help | Auto-Detect Active")
    )


# =============================================
#              RUN BOT
# =============================================

if __name__ == "__main__":
    token = config.get("BOT_TOKEN", "")
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        token = input("  Enter bot token: ").strip()
        if token:
            config["BOT_TOKEN"] = token
            save_config(config)
        else:
            exit(1)
    bot.run(token)
