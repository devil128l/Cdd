import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import httpx
import os
import time
from datetime import datetime, timezone

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
        return {}
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        with open(path, "w") as f:
            json.dump({}, f)
        return {}

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
#         QUEST TYPE CONSTANTS
# =============================================

QUEST_TYPES = {
    0: "unknown",
    1: "stream",       # Stream a game on Discord
    2: "play",         # Play a game
    3: "watch",        # Watch a video
    4: "activity",     # In-app activity
    5: "stream",       # Stream (alt)
    6: "video",        # Video watch
    7: "play",         # Play on console
}

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
#              QUEST API (FIXED)
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
                "discord/1.0.9173 Chrome/124.0.6367.243 "
                "Electron/30.2.0 Safari/537.36"
            ),
            "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiRGlzY29yZCBDbGllbnQifQ==",
            "X-Discord-Locale": "en-US",
        }

    async def validate(self):
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.get(f"{self.BASE}/users/@me", headers=self.headers)
                return r.json() if r.status_code == 200 else None
            except Exception as e:
                print(f"[API] Validate error: {e}")
                return None

    async def get_quests(self):
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.get(f"{self.BASE}/quests/@me", headers=self.headers)
                if r.status_code == 200:
                    data = r.json()
                    # Discord returns { "quests": [...] } or just [...]
                    if isinstance(data, dict) and "quests" in data:
                        return data["quests"]
                    return data if isinstance(data, list) else []
                else:
                    print(f"[API] get_quests status: {r.status_code}")
                    return []
            except Exception as e:
                print(f"[API] get_quests error: {e}")
                return []

    async def enroll(self, quest_id):
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.post(
                    f"{self.BASE}/quests/{quest_id}/enroll",
                    headers=self.headers,
                    json={"location": 0}
                )
                print(f"[API] Enroll {quest_id}: {r.status_code}")
                return r.status_code in [200, 204, 201]
            except Exception as e:
                print(f"[API] Enroll error: {e}")
                return False

    async def video_progress(self, quest_id, timestamp_seconds):
        """Watch quest heartbeat"""
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.post(
                    f"{self.BASE}/quests/{quest_id}/video-progress",
                    headers=self.headers,
                    json={"timestamp": timestamp_seconds}
                )
                return r.status_code in [200, 204]
            except Exception as e:
                print(f"[API] video_progress error: {e}")
                return False

    async def stream_heartbeat(self, quest_id, stream_key, terminal=False):
        """Stream quest heartbeat"""
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.post(
                    f"{self.BASE}/quests/{quest_id}/heartbeat",
                    headers=self.headers,
                    json={"stream_key": stream_key, "terminal": terminal}
                )
                return r.status_code in [200, 204]
            except Exception as e:
                print(f"[API] stream_heartbeat error: {e}")
                return False

    async def play_activity(self, quest_id, application_id):
        """Play quest - Discord uses RPC but we spoof via quests API"""
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                # Trigger by sending a "played" session
                r = await c.post(
                    f"{self.BASE}/quests/{quest_id}/heartbeat",
                    headers=self.headers,
                    json={
                        "stream_key": f"call:{application_id}:1",
                        "terminal": False
                    }
                )
                return r.status_code in [200, 204]
            except Exception as e:
                print(f"[API] play_activity error: {e}")
                return False

    async def claim(self, quest_id):
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.post(
                    f"{self.BASE}/quests/{quest_id}/claim",
                    headers=self.headers
                )
                return r.status_code == 200
            except Exception as e:
                print(f"[API] Claim error: {e}")
                return False

    def parse(self, quest):
        """Parse quest data"""
        cfg = quest.get("config", {})
        msgs = cfg.get("messages", {})
        us = quest.get("user_status", {})

        # Determine quest type
        quest_type_num = cfg.get("type", 0)
        task_config = cfg.get("task_config", {}) or cfg.get("task_config_v2", {}) or {}
        tasks_list = task_config.get("tasks", {})

        # Extract task details
        task_name = "unknown"
        duration = 900  # default 15 min in seconds
        for task_key, task_data in tasks_list.items():
            task_name = task_key.lower()
            duration = task_data.get("target", 900)
            break

        # Determine actual type from task name
        if "stream" in task_name:
            actual_type = "stream"
        elif "play" in task_name or "activity" in task_name or "playtime" in task_name:
            actual_type = "play"
        elif "watch" in task_name or "video" in task_name:
            actual_type = "watch"
        else:
            actual_type = QUEST_TYPES.get(quest_type_num, "unknown")

        duration_min = max(duration // 60, 1)

        return {
            "id": quest.get("id", ""),
            "name": msgs.get("game_title", "Unknown Quest") if isinstance(msgs.get("game_title"), str) else msgs.get("game_title", {}).get("default", "Unknown Quest") if isinstance(msgs.get("game_title"), dict) else "Unknown Quest",
            "description": msgs.get("quest_name", "No description") if isinstance(msgs.get("quest_name"), str) else msgs.get("quest_name", {}).get("default", "No description") if isinstance(msgs.get("quest_name"), dict) else "No description",
            "type": actual_type,
            "task_name": task_name,
            "duration": duration_min,
            "duration_sec": duration,
            "completed": us.get("completed_at") is not None,
            "claimed": us.get("claimed_at") is not None,
            "enrolled": us.get("enrolled_at") is not None,
            "app_id": cfg.get("application_id") or cfg.get("application_ids", [None])[0] if cfg.get("application_ids") else None,
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

def get_type_emoji(quest_type: str) -> str:
    return {
        "stream": "📺",
        "play": "🎮",
        "watch": "👁️",
        "activity": "🎯",
        "video": "🎬",
    }.get(quest_type, "❓")

def owner_only_embed():
    return discord.Embed(
        title="🔒 Bot Owner Only",
        description="This command is restricted to the **bot owner** only.",
        color=0xED4245
    )

def is_autoquest_enabled(user_id: str) -> bool:
    """Check if user has enabled autoquest"""
    settings = load_json("autoquest_settings.json")
    return settings.get(user_id, False)

def set_autoquest(user_id: str, enabled: bool):
    settings = load_json("autoquest_settings.json")
    settings[user_id] = enabled
    save_json("autoquest_settings.json", settings)

async def send_log(guild, title, user, details=""):
    cfg = load_config()
    ch_id = cfg.get("LOG_CHANNEL_ID", 0)
    if ch_id == 0: return
    ch = guild.get_channel(ch_id)
    if not ch: return
    embed = discord.Embed(title=f"📋 {title}", color=0x2F3136, timestamp=datetime.now(timezone.utc))
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
#      UNIVERSAL QUEST COMPLETION ENGINE
# =============================================

async def complete_quest_by_type(api: QuestAPI, info: dict, user_id: str, user_data: dict, progress_callback=None):
    """
    Universal quest completer — handles ALL types:
    - stream, play, watch, activity, video
    """
    quest_id = info["id"]
    quest_type = info["type"]
    duration_sec = info["duration_sec"]
    tid = user_data.get("id", "0")

    # Enroll first
    enrolled = await api.enroll(quest_id)
    print(f"[QUEST] Enrolled '{info['name']}': {enrolled}")

    # Add buffer time
    total_time = duration_sec + 60
    interval = 20  # heartbeat every 20 seconds
    elapsed = 0

    print(f"[QUEST] Type: {quest_type} | Duration: {duration_sec}s | Starting...")

    while elapsed < total_time:
        if user_id in bot.active_tasks and bot.active_tasks[user_id] == "cancelled":
            return False

        success = False

        # STREAM QUESTS
        if quest_type == "stream":
            stream_key = f"call:{info['app_id'] or '0'}:1"
            success = await api.stream_heartbeat(quest_id, stream_key, terminal=False)

        # WATCH / VIDEO QUESTS
        elif quest_type in ["watch", "video"]:
            success = await api.video_progress(quest_id, elapsed)

        # PLAY / ACTIVITY QUESTS
        elif quest_type in ["play", "activity"]:
            app_id = info["app_id"] or "0"
            success = await api.play_activity(quest_id, app_id)

        # UNKNOWN — try all methods
        else:
            stream_key = f"call:{info['app_id'] or '0'}:1"
            success = await api.stream_heartbeat(quest_id, stream_key, terminal=False)
            if not success:
                success = await api.video_progress(quest_id, elapsed)

        if progress_callback:
            await progress_callback(elapsed, total_time, success)

        await asyncio.sleep(interval)
        elapsed += interval

    # Terminal heartbeat
    if quest_type == "stream":
        stream_key = f"call:{info['app_id'] or '0'}:1"
        await api.stream_heartbeat(quest_id, stream_key, terminal=True)
    elif quest_type in ["watch", "video"]:
        await api.video_progress(quest_id, duration_sec)

    # Try to claim
    await asyncio.sleep(3)
    claimed = await api.claim(quest_id)
    print(f"[QUEST] Claimed '{info['name']}': {claimed}")

    return True


# =============================================
#    🤖 AUTO QUEST DETECTOR & COMPLETER
# =============================================

@tasks.loop(minutes=5)
async def auto_quest_loop():
    """Auto detect & complete new quests for premium users (if enabled)"""
    cfg = load_config()
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    if role_id == 0:
        return

    tokens = load_json("tokens.json")
    seen_quests = load_json("seen_quests.json")

    if not tokens:
        return

    print(f"\n{'='*50}")
    print(f"[AUTO] 🔍 Scanning... ({datetime.now().strftime('%H:%M:%S')})")

    for user_id_str, token in list(tokens.items()):
        # Check if autoquest is enabled for this user
        if not is_autoquest_enabled(user_id_str):
            continue

        user_id_int = int(user_id_str)

        # Check premium
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
        user_data = await api.validate()
        if not user_data:
            print(f"[AUTO] ⚠️ Invalid token: {user_id_str}")
            continue

        username = user_data.get("username", "Unknown")
        quests = await api.get_quests()

        if not quests:
            continue

        if user_id_str not in seen_quests:
            seen_quests[user_id_str] = []

        user_seen = seen_quests[user_id_str]
        new_found = 0

        for quest in quests:
            info = api.parse(quest)
            qid = info["id"]

            if info["completed"] or info["claimed"]:
                if qid not in user_seen:
                    user_seen.append(qid)
                continue

            if qid not in user_seen:
                new_found += 1
                print(f"[AUTO] 🆕 NEW: {info['name']} ({info['type']}) for {username}")

                user_seen.append(qid)
                save_json("seen_quests.json", seen_quests)

                # Notify
                type_emoji = get_type_emoji(info["type"])
                detect_embed = discord.Embed(
                    title=f"🆕 New {info['type'].upper()} Quest!",
                    description=f"**{info['name']}**\n{info['description']}",
                    color=0x5865F2,
                    timestamp=datetime.now(timezone.utc)
                )
                detect_embed.add_field(name=f"{type_emoji} Type", value=f"`{info['type']}`", inline=True)
                detect_embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
                detect_embed.add_field(name="🤖 Status", value="`Auto-completing...`", inline=False)
                await send_dm(user_member, detect_embed)

                # Complete
                try:
                    success = await complete_quest_by_type(api, info, user_id_str, user_data)
                    if success:
                        done_embed = discord.Embed(
                            title="✅ Quest Auto-Completed!",
                            description=f"**{info['name']}** has been completed!",
                            color=0x57F287,
                            timestamp=datetime.now(timezone.utc)
                        )
                        done_embed.add_field(name=f"{type_emoji} Type", value=f"`{info['type']}`", inline=True)
                        done_embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
                        await send_dm(user_member, done_embed)

                        for guild in bot.guilds:
                            await send_log(guild, "Auto Quest Completed", user_member, f"{info['name']} ({info['type']})")
                    else:
                        if qid in user_seen:
                            user_seen.remove(qid)
                            save_json("seen_quests.json", seen_quests)
                except Exception as e:
                    print(f"[AUTO] ❌ Error: {e}")
                    if qid in user_seen:
                        user_seen.remove(qid)
                        save_json("seen_quests.json", seen_quests)

                await asyncio.sleep(5)

        if new_found > 0:
            print(f"[AUTO] {username}: {new_found} new quest(s) processed")

    print(f"[AUTO] ✅ Scan complete\n")


@auto_quest_loop.before_loop
async def before_auto_loop():
    await bot.wait_until_ready()
    await asyncio.sleep(30)


# =============================================
#         INTERACTIVE QUEST RUNNER
# =============================================

async def run_quest_interactive(interaction, api, info, msg, user_id, is_prem):
    try:
        user_data = await api.validate()
        if not user_data:
            raise Exception("Invalid token")

        type_emoji = get_type_emoji(info["type"])

        async def update_progress(elapsed, total, hb_success):
            pct = min((elapsed / total) * 100, 100)
            bar_len = 20
            filled = int(bar_len * elapsed // total)
            bar = "█" * filled + "░" * (bar_len - filled)
            remaining = max((total - elapsed) // 60, 0)

            embed = discord.Embed(
                title=f"{type_emoji} Completing: {info['name']}",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Progress", value=f"```\n[{bar}] {pct:.1f}%\n```", inline=False)
            embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
            embed.add_field(name="⏱️ Remaining", value=f"`{remaining} min`", inline=True)
            embed.add_field(name="💓 Heartbeat", value="✅" if hb_success else "❌", inline=True)
            embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
            embed.set_footer(text=f"{'👑 Premium' if is_prem else '🆓 Free'} • ID: {info['id']}")

            try: await msg.edit(embed=embed)
            except: pass

        success = await complete_quest_by_type(api, info, user_id, user_data, progress_callback=update_progress)

        if success:
            embed = discord.Embed(
                title=f"✅ {info['type'].title()} Quest Completed!",
                description=f"**{info['name']}** completed!",
                color=0x57F287,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Progress", value=f"```\n[{'█' * 20}] 100.0%\n```", inline=False)
            embed.add_field(name=f"{type_emoji} Type", value=f"`{info['type']}`", inline=True)
            embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)

            if not is_prem:
                embed.add_field(name="🔑 Token", value="`🗑️ Deleted`", inline=True)
            else:
                embed.add_field(name="🔑 Token", value="`💾 Saved`", inline=True)

            try: await msg.edit(embed=embed)
            except: pass

            await interaction.channel.send(f"🎉 {interaction.user.mention} Quest **{info['name']}** done!")
            if interaction.guild:
                await send_log(interaction.guild, "Quest Completed", interaction.user, f"{info['name']} ({info['type']})")

    except asyncio.CancelledError:
        embed = discord.Embed(title="⏹️ Quest Stopped", color=0xFEE75C)
        try: await msg.edit(embed=embed)
        except: pass
    except Exception as e:
        print(f"[QUEST] Error: {e}")
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
                    description="Your token was deleted after use.\n👑 **Get Premium** for permanent saving + auto quest!",
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
        title="🎮 Quest Completer Bot v8.0",
        description="Supports: 📺 Stream | 🎮 Play | 👁️ Watch | 🎯 Activity",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name="🔑 Token",
        value=(
            "`/settoken` — Save token permanently (**Premium, DM only**)\n"
            "`/removetoken` — Remove saved token\n"
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
            "⚠️ Token deleted after each use\n"
            "⏳ 24h cooldown"
        ),
        inline=False
    )
    embed.add_field(
        name=f"👑 Premium {'(<@&' + str(role_id) + '>)' if role_id else ''}",
        value=(
            "💾 Permanent token\n"
            "⚡ No cooldown\n"
            "`/autoquest` — **Enable/disable auto quest**\n"
            f"🤖 When ON: auto-completes new quests every **{interval} min**"
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Owner Commands 🔒",
        value=(
            "`/setpremiumrole` `/setcooldown` `/setlogchannel`\n"
            "`/setautointerval` `/resetcooldown` `/settings`\n\n"
            f"**Owners:** {owner_mentions}"
        ),
        inline=False
    )
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    await interaction.response.send_message(embed=embed)


# ---------- /autoquest ----------
@bot.tree.command(name="autoquest", description="Enable or disable automatic quest completion (Premium only)")
@app_commands.describe(mode="Turn auto quest ON or OFF")
@app_commands.choices(mode=[
    app_commands.Choice(name="🟢 Enable (ON)", value="on"),
    app_commands.Choice(name="🔴 Disable (OFF)", value="off"),
    app_commands.Choice(name="📊 Check Status", value="status"),
])
async def slash_autoquest(interaction: discord.Interaction, mode: str):
    uid = str(interaction.user.id)

    # Check premium
    is_prem = is_premium(interaction.user) if interaction.guild else False
    if not is_prem:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="👑 Premium Only",
                description=(
                    "**Auto Quest** is a Premium feature!\n\n"
                    "Only premium members can enable automatic quest completion.\n\n"
                    "Get the premium role to unlock this feature."
                ),
                color=0xFEE75C
            ),
            ephemeral=True
        )
        return

    # Check token
    tokens = load_json("tokens.json")
    if uid not in tokens:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ No Saved Token",
                description="Save your token first with `/settoken` in DMs!",
                color=0xED4245
            ),
            ephemeral=True
        )
        return

    if mode == "status":
        enabled = is_autoquest_enabled(uid)
        embed = discord.Embed(
            title="📊 Auto Quest Status",
            color=0x57F287 if enabled else 0xED4245,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="Current Status",
            value="🟢 **ENABLED**" if enabled else "🔴 **DISABLED**",
            inline=False
        )
        if enabled:
            embed.add_field(
                name="What's happening?",
                value=(
                    "✅ Bot is scanning your account every 5 minutes\n"
                    "✅ New quests will be auto-completed\n"
                    "✅ You'll get DM notifications"
                ),
                inline=False
            )
        else:
            embed.add_field(
                name="How to enable?",
                value="Use `/autoquest mode:Enable` to turn it ON",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    enabled = (mode == "on")
    set_autoquest(uid, enabled)

    if enabled:
        embed = discord.Embed(
            title="🟢 Auto Quest ENABLED",
            description=(
                "Automatic quest completion is now **ACTIVE**!\n\n"
                "🤖 Bot will scan your account every **5 minutes**\n"
                "🆕 New quests will be detected automatically\n"
                "✅ Quests will complete without any action from you\n"
                "📩 You'll receive DM notifications"
            ),
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Use /autoquest mode:Disable to turn OFF anytime")
    else:
        embed = discord.Embed(
            title="🔴 Auto Quest DISABLED",
            description=(
                "Automatic quest completion is now **OFF**.\n\n"
                "❌ Bot will no longer auto-complete your quests\n"
                "✋ You must use `/complete` manually\n\n"
                "Use `/autoquest mode:Enable` to turn it back ON"
            ),
            color=0xED4245,
            timestamp=datetime.now(timezone.utc)
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

    if interaction.guild:
        await send_log(
            interaction.guild,
            f"AutoQuest {'Enabled' if enabled else 'Disabled'}",
            interaction.user
        )


# ---------- /settoken ----------
@bot.tree.command(name="settoken", description="Save token permanently (Premium only, DM only)")
@app_commands.describe(token="Your Discord user token")
async def slash_settoken(interaction: discord.Interaction, token: str):
    if interaction.guild:
        await interaction.response.send_message(
            embed=discord.Embed(title="⚠️ Use in DMs only!", description="For security, use `/settoken` in my DMs.", color=0xED4245),
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
                description="Permanent token saving is for Premium users.\nFree users provide token each time.",
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

    embed = discord.Embed(title="✅ Token Saved!", color=0x57F287, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Account", value=f"`{user_data.get('username', 'Unknown')}`", inline=True)
    embed.add_field(name="Token", value=f"`{mask_token(token)}`", inline=False)
    embed.add_field(
        name="🤖 Auto Quest",
        value="Use `/autoquest mode:Enable` in the server to activate auto-completion!",
        inline=False
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------- /removetoken ----------
@bot.tree.command(name="removetoken", description="Remove your stored token")
async def slash_removetoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)
    if uid in tokens:
        del tokens[uid]
        save_json("tokens.json", tokens)
        # Disable autoquest too
        set_autoquest(uid, False)
        # Remove seen
        seen = load_json("seen_quests.json")
        if uid in seen:
            del seen[uid]
            save_json("seen_quests.json", seen)
        embed = discord.Embed(title="✅ Token & Data Removed", description="Auto quest disabled.", color=0x57F287)
    else:
        embed = discord.Embed(title="❌ No Token Found", color=0xED4245)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- /checktoken ----------
@bot.tree.command(name="checktoken", description="Check if your token is valid")
async def slash_checktoken(interaction: discord.Interaction):
    tokens = load_json("tokens.json")
    uid = str(interaction.user.id)
    if uid not in tokens:
        await interaction.response.send_message(embed=discord.Embed(title="❌ No Token", color=0xED4245), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    api = QuestAPI(tokens[uid])
    user_data = await api.validate()
    if user_data:
        auto = is_autoquest_enabled(uid)
        embed = discord.Embed(title="✅ Token Valid", color=0x57F287)
        embed.add_field(name="Account", value=f"`{user_data.get('username', 'Unknown')}`", inline=True)
        embed.add_field(name="Token", value=f"`{mask_token(tokens[uid])}`", inline=True)
        embed.add_field(name="🤖 Auto Quest", value="🟢 ON" if auto else "🔴 OFF", inline=True)
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
        await interaction.followup.send(embed=discord.Embed(title="📋 No Quests Available", color=0x2F3136), ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Available Quests",
        description=f"Found **{len(quests)}** quest(s)",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    for i, q in enumerate(quests, 1):
        info = api.parse(q)
        emoji, status = get_status_emoji(info)
        type_emoji = get_type_emoji(info["type"])
        embed.add_field(
            name=f"#{i} • {info['name']}",
            value=f"{type_emoji} `{info['type']}` • ⏱️ `{info['duration']}m` • {emoji} {status}",
            inline=False
        )
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
        await interaction.response.send_message(embed=discord.Embed(title="❌ Quest Already Running", color=0xED4245), ephemeral=True)
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

    type_emoji = get_type_emoji(info["type"])
    embed = discord.Embed(title=f"🚀 Starting {type_emoji} {info['name']}", color=0x5865F2, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="📊 Type", value=f"`{info['type']}`", inline=True)
    embed.add_field(name="⏱️ Duration", value=f"`{info['duration']} min`", inline=True)
    embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
    msg = await interaction.followup.send(embed=embed, wait=True)

    if interaction.guild:
        await send_log(interaction.guild, "Quest Started", interaction.user, f"{info['name']} ({info['type']})")

    bot.active_tasks[uid] = "running"
    asyncio.create_task(run_quest_interactive(interaction, api, info, msg, uid, is_prem))


# ---------- /completeall ----------
@bot.tree.command(name="completeall", description="Complete all available quests")
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
        await interaction.response.send_message(embed=discord.Embed(title="❌ Token Required", color=0xED4245), ephemeral=True)
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
    for _, info in incomplete:
        type_emoji = get_type_emoji(info["type"])
        embed.add_field(name=f"{type_emoji} {info['name']}", value=f"`{info['type']}` • `{info['duration']}m`", inline=False)
    await interaction.followup.send(embed=embed)

    for quest, info in incomplete:
        if uid in bot.active_tasks and bot.active_tasks[uid] == "cancelled":
            break
        type_emoji = get_type_emoji(info["type"])
        msg = await interaction.channel.send(embed=discord.Embed(title=f"🔄 {type_emoji} {info['name']}", color=0x5865F2))
        bot.active_tasks[uid] = "running"
        await run_quest_interactive(interaction, api, info, msg, uid, is_prem)
        await asyncio.sleep(3)

    await interaction.channel.send(f"🎉 {interaction.user.mention} All done!")


# ---------- /stop ----------
@bot.tree.command(name="stop", description="Stop your running quest")
async def slash_stop(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid in bot.active_tasks:
        bot.active_tasks[uid] = "cancelled"
        await interaction.response.send_message(embed=discord.Embed(title="⏹️ Stopping...", color=0xFEE75C))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="❌ No Active Quest", color=0xED4245), ephemeral=True)


# =============================================
#       OWNER ONLY COMMANDS 🔒
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

@bot.tree.command(name="setcooldown", description="🔒 [OWNER] Set free cooldown hours")
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

@bot.tree.command(name="setautointerval", description="🔒 [OWNER] Set auto-scan interval")
@app_commands.describe(minutes="Minutes (min 3)")
async def slash_setautointerval(interaction: discord.Interaction, minutes: int):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message(embed=owner_only_embed(), ephemeral=True)
        return
    if minutes < 3:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Min 3", color=0xED4245), ephemeral=True)
        return
    cfg = load_config()
    cfg["AUTO_CHECK_INTERVAL"] = minutes
    save_config(cfg)
    auto_quest_loop.change_interval(minutes=minutes)
    await interaction.response.send_message(
        embed=discord.Embed(title="✅ Auto Interval Set", description=f"Every **{minutes} min**", color=0x57F287)
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
    autoquests = load_json("autoquest_settings.json")
    enabled_count = sum(1 for v in autoquests.values() if v)
    role_id = cfg.get("PREMIUM_ROLE_ID", 0)
    log_id = cfg.get("LOG_CHANNEL_ID", 0)
    owners = cfg.get("OWNER_IDS", [])

    embed = discord.Embed(title="⚙️ Bot Settings", color=0x2F3136, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="🛡️ Owners", value=", ".join([f"<@{o}>" for o in owners]) or "None", inline=False)
    embed.add_field(name="👑 Premium Role", value=f"<@&{role_id}>" if role_id else "`Not Set`", inline=True)
    embed.add_field(name="📋 Log Channel", value=f"<#{log_id}>" if log_id else "`Not Set`", inline=True)
    embed.add_field(name="⏱️ Free Cooldown", value=f"`{cfg.get('FREE_COOLDOWN_HOURS', 24)}h`", inline=True)
    embed.add_field(name="🤖 Auto Scan", value=f"Every `{cfg.get('AUTO_CHECK_INTERVAL', 5)}m`", inline=True)
    embed.add_field(name="🔑 Saved Tokens", value=f"`{len(tokens)}`", inline=True)
    embed.add_field(name="🟢 Auto Quest ON", value=f"`{enabled_count}` user(s)", inline=True)
    embed.add_field(name="⏳ Cooldowns", value=f"`{len(cooldowns)}`", inline=True)
    embed.add_field(name="🔄 Active Tasks", value=f"`{len(bot.active_tasks)}`", inline=True)
    embed.add_field(name="👁️ Tracked Quests", value=f"`{sum(len(v) for v in seen.values())}`", inline=True)
    await interaction.response.send_message(embed=embed)


# =============================================
#              BOT EVENTS
# =============================================

@bot.event
async def on_ready():
    cfg = load_config()
    owners = cfg.get("OWNER_IDS", [])
    print(f"""
╔═══════════════════════════════════════════════════════╗
║       Quest Completer Bot v8.0 — ALL TYPES            ║
║                                                       ║
║   Bot:     {bot.user.name:<44}║
║   Servers: {str(len(bot.guilds)):<44}║
║   Owners:  {', '.join([str(o) for o in owners]):<44}║
║                                                       ║
║   📺 Stream | 🎮 Play | 👁️ Watch | 🎯 Activity          ║
║   🤖 /autoquest to toggle auto-complete               ║
║                                                       ║
║   Status:  ✅ Online & Ready                          ║
╚═══════════════════════════════════════════════════════╝
    """)
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="/help | /autoquest")
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
