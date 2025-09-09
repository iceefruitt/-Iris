import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import sqlite3
import yt_dlp
import asyncio
from collections import deque
from discord.errors import ClientException
import time
from discord.ext import tasks
import re
import random
from datetime import datetime, timedelta
import aiohttp
import re
from aiohttp import ClientSession
from discord import app_commands, File
import json

load_dotenv()


intents = discord.Intents.default()
intents.message_content = True
intents.bans = True
intents.members = True
intents.presences = True 


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1398619451875786932, 1294593959313539135
GENIUS_API_TOKEN = "LDgYY-fH14O-Up1uPggFz5MGBQYbFGB4_hnzG5YyDQxeX1rPxhFxN8Kq40p0u_Vi"
MOD_LOG_CHANNEL_ID = 1406252218524631130
PRESENCE_FILE = "bot_presence.json"

bot = commands.Bot(command_prefix=',', intents=intents)

# Create the structure for queueing songs - Dictionary of queues
SONG_QUEUES = {}
NOW_PLAYING_MSGS = {}
VOLUME_LEVELS = {} 
sasified_users = set()


async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

# profanity related functions <------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

profanity = ["nigga, ayuu, PornName"]

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    for term in profanity:
        if term.lower() in message.content.lower():
            warning_count = increase_and_get_warning_count(message.author.id, message.guild.id)
            
            if warning_count >= 3:
                await message.channel.send(f'{message.author.mention}, you have been banned for repeated use of prohibited language.')
                await message.guild.ban(message.author, reason="Repeated use of prohibited language.")
            else:
                await message.channel.send(f'{message.author.mention}, please refrain from using prohibited language. This is your warning {warning_count}/3.')
            
                await message.delete()
            break
    
    await bot.process_commands(message)

@bot.command(name='addprofanity', help='Add a word to the profanity list')
@commands.has_permissions(administrator=True)
async def addprofanity(ctx, *, word: str):
    profanity.append(word.lower())

@bot.command(name='removeprofanity', help='Remove a word from the profanity list')
@commands.has_permissions(administrator=True)
async def removeprofanity(ctx, *, word: str):
    try:
        profanity.remove(word.lower())
    except ValueError:
        await ctx.send(f'{word} is not in the profanity list.')

@bot.tree.command(name="profanitylist", description="Get the current profanity list")
async def profanitylist(interaction: discord.Interaction):
    if profanity:
        await interaction.response.send_message("Current profanity list: " + ", ".join(profanity))
    else:
        await interaction.response.send_message("The profanity list is currently empty.")




# Table related functions <----------------------------------

def create_user_table():
    connection = sqlite3.connect(f'{BASE_DIR}\\users_warning.db')
    cursor = connection.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS "Users_per_guild" (
            "user_id" INTEGER,
            'warning_count' INTEGER,
            'guild_id' INTEGER,
            PRIMARY KEY("user_id", 'guild_id')
        )
    ''')
    connection.commit()
    connection.close()


create_user_table()

def increase_and_get_warning_count(user_id: int, guild_id: int):
    connection = sqlite3.connect(f'{BASE_DIR}\\users_warning.db')
    cursor = connection.cursor()

    cursor.execute('''
        SELECT warning_count
        FROM Users_per_guild
        WHERE (user_id = ?) AND (guild_id = ?);
    ''', (user_id, guild_id))

    result = cursor.fetchone()

    if result == None:
        cursor.execute('''
            INSERT INTO Users_per_guild (user_id, warning_count, guild_id)
            VALUES (?, 1, ?);
        ''', (user_id, guild_id))

        connection.commit()
        connection.close()

        return 1
    
    cursor.execute('''
        UPDATE Users_per_guild
        SET warning_count = ?
        WHERE (user_id = ?) AND (guild_id = ?);
    ''', (result[0] + 1, user_id, guild_id))

    connection.commit()
    connection.close()

    return result[0] + 1

@bot.command(name='warnings', help='Check your warning count')
async def warnings(ctx):
    warning_count = increase_and_get_warning_count(ctx.author.id, ctx.guild.id) - 1
    await ctx.send(f'{ctx.author.mention}, you have {warning_count} warning(s).')


@bot.command(name='resetwarnings', help='Reset your warning count')
@commands.has_permissions(administrator=True)
async def resetwarnings(ctx, member: discord.Member):
    connection = sqlite3.connect(f'{BASE_DIR}\\users_warning.db')
    cursor = connection.cursor()

    cursor.execute('''
        DELETE FROM Users_per_guild
        WHERE (user_id = ?) AND (guild_id = ?);
    ''', (member.id, ctx.guild.id))

    connection.commit()
    connection.close()

    await ctx.send(f'{member.mention}\'s warnings have been reset.')


#Bot events and commands <------------------------------------------------

@bot.command(name="ping", help="Check the bot's latency")
async def ping(ctx):
    await ctx.send(f'Pong! {round(bot.latency * 1000)}ms')


@bot.event
async def on_ready():
    data = load_presence()
    await bot.change_presence(
        status=discord.Status[data["status"]],
        activity=create_activity(data["activity"], data["activity_type"])
    )
    await bot.tree.sync()
    print(f"{bot.user} is online!")



#music commands <------------------------------------------------

SONG_QUEUES = {}
LOOP_FLAGS = {} 
NOW_PLAYING_MSGS = {}
VOLUME_LEVELS = {}  # Store volume levels per guild (0.0 to 1.0)

@bot.command(name="skip", help="Skips the currently playing song.")
async def skip(ctx):
    voice_client = ctx.guild.voice_client

    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await ctx.send("Skipped the current song.")
    else:
        await ctx.send("Not playing anything to skip.")


@bot.command(name="pause", help="Pause the currently playing song.")
async def pause(ctx):
    voice_client = ctx.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await ctx.send("I'm not in a voice channel.")

    # Check if something is actually playing
    if not voice_client.is_playing():
        return await ctx.send("Nothing is currently playing.")

    # Pause the track
    voice_client.pause()
    await ctx.send("Playback paused!")



@bot.command(name="resume", help="Resume the currently paused song.")
async def resume(ctx):
    voice_client = ctx.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await ctx.send("I'm not in a voice channel.")

    # Check if it's actually paused
    if not voice_client.is_paused():
        return await ctx.send("I'm not paused right now.")

    # Resume playback
    voice_client.resume()
    await ctx.send("Playback resumed!")



@bot.command(name="stop", help="Stop playback and clear the queue.")
async def stop(ctx):
    voice_client = ctx.guild.voice_client

    # Not connected
    if not voice_client or not voice_client.is_connected():
        await ctx.send("I'm not connected to any voice channel.")
        return

    # Clear the queue
    guild_id_str = str(ctx.guild.id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    # Stop playback
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    # Try to disconnect, handle timeout
    try:
        await asyncio.wait_for(voice_client.disconnect(), timeout=5)
        await ctx.send("Stopped playback and disconnected!")
    except asyncio.TimeoutError:
        try:
            await voice_client.close()
        except Exception:
            pass
        await ctx.send("Stopped playback, but an error occurred disconnecting from voice (forced disconnect).")
    except ClientException as e:
        await ctx.send(f"Already disconnecting or not connected. ({e})")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")


@bot.command(name="play", help="Play a song or add it to the queue. Usage: ,play [song name or URL]")
async def play(ctx, *, song_query: str):
    voice_channel = ctx.author.voice.channel if ctx.author.voice else None
    if voice_channel is None:
        await ctx.send("You must be in a voice channel.")
        return

    voice_client = ctx.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    query = "ytsearch1: " + song_query
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get("entries", [])

    if not tracks:
        await ctx.send("No results found.")
        return

    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Untitled")

    guild_id = str(ctx.guild.id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append((audio_url, title))

    if voice_client.is_playing() or voice_client.is_paused():
        await ctx.send(f"Added to queue: **{title}**")
    else:
        await ctx.send(f"Now playing: **{title}**")
        await play_next_song(voice_client, guild_id, ctx.channel)


async def play_next_song(voice_client, guild_id, channel):
    guild_id_str = str(guild_id)
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -c:a libopus -b:a 96k",
        }

        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="bin\\ffmpeg\\ffmpeg.exe")

        async def send_now_playing_message():
            prev = NOW_PLAYING_MSGS.get(guild_id_str)
            if prev:
                try:
                    await prev.delete()
                except Exception:
                    pass  # May be already deleted

            new_msg = await channel.send(f"Now playing: **{title}**")
            NOW_PLAYING_MSGS[guild_id_str] = new_msg

        def after_play(error):
            # Remove reference to message if queue is empty after loop handling
            coro = play_next_song(voice_client, guild_id, channel)
            if LOOP_FLAGS.get(guild_id_str, False):
                # When looping: re-queue current song at the FRONT
                SONG_QUEUES[guild_id].appendleft((audio_url, title))
            asyncio.run_coroutine_threadsafe(coro, bot.loop)

        voice_client.play(source, after=after_play)
        await send_now_playing_message()

    else:
        # Cleanup now playing message and loop state if queue is empty!
        msg = NOW_PLAYING_MSGS.pop(guild_id_str, None)
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass
        LOOP_FLAGS.pop(guild_id_str, None)  # <-- remove loop status also!
        if voice_client.is_connected():
            await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()


@bot.command(name="queue", help="Show the current song queue.")
async def queue(ctx):
    guild_id = str(ctx.guild.id)
    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        await ctx.send("The queue is currently empty.")
        return

    queue_list = list(SONG_QUEUES[guild_id])
    message = "Current Queue:\n"
    for idx, (url, title) in enumerate(queue_list, start=1):
        message += f"{idx}. {title}\n"
    
    if len(message) > 2000:
        message = message[:1997] + "..."

    await ctx.send(message)

@bot.command(name="loop", help="Toggle looping the currently playing song in this server.")
async def loop(ctx):
    guild_id = str(ctx.guild.id)
    is_looping = LOOP_FLAGS.get(guild_id, False)
    LOOP_FLAGS[guild_id] = not is_looping
    status = "enabled" if not is_looping else "disabled"
    await ctx.send(f"🔁 Looping is now **{status}** for this server!")

@bot.command(name="unloop", help="Stop looping the current song in this server.")
async def unloop(ctx):
    guild_id = str(ctx.guild.id)
    if LOOP_FLAGS.get(guild_id, False):
        LOOP_FLAGS[guild_id] = False
        await ctx.send("⏹️ Looping **disabled** for this server.")
    else:
        await ctx.send("Looping was already off.")


#@bot.command(name="volume", help="Set or get the playback volume. Usage: ,volume [level 1-100]")
#async def volume(ctx, level: int = None):
#    guild_id = str(ctx.guild.id)
#    if level is None:
#        vol = VOLUME_LEVELS.get(guild_id, 0.5)
#        await ctx.send(f"🔊 Current volume: {int(vol*100)}%")
#   else:
#       if not (1 <= level <= 100):
#            await ctx.send("Please select a volume between 1 and 100.")
#            return
#       VOLUME_LEVELS[guild_id] = level / 100.0
#       await ctx.send(f"🔊 Volume set to {level}%")

@loop.error
async def loop_error(ctx, error):
    await ctx.send(f"An error occurred: {error}")

@unloop.error
async def unloop_error(ctx, error):
    await ctx.send(f"An error occurred: {error}")


@play.error
async def play_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Please provide a song name or URL to play.")
    else:
        await ctx.send(f"An error occurred: {error}")



#Purge command <------------------------------------------------

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("You can only delete between 1 and 100 messages.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to include the command message
    await ctx.send(f"Viped {len(deleted)-1} messages.", delete_after=2)

@purge.error
async def purge_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Messages permission to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Please provide a valid number of messages to delete.")
    else:
        await ctx.send("An error occurred.")

#Role assignment command <------------------------------------------------

def find_role(ctx, query: str):
    # Try by mention first
    if query.startswith("<@&") and query.endswith(">"):
        try:
            role_id = int(query[3:-1])
            role = discord.utils.get(ctx.guild.roles, id=role_id)
            if role:
                return role
        except Exception:
            pass
    # Try exact name (case-insensitive)
    role = discord.utils.get(ctx.guild.roles, name=query)
    if role:
        return role
    # Try exact name (case-insensitive, spaces ignored)
    for r in ctx.guild.roles:
        if r.name.replace(" ", "").lower() == query.replace(" ", "").lower():
            return r
    # Try substring match (case-insensitive, any part of the name)
    for r in ctx.guild.roles:
        if query.lower() in r.name.lower():
            return r
    return None

@bot.command()
@commands.has_permissions(manage_roles=True)
async def giverole(ctx, member: discord.Member, *, role_query: str):
    role = find_role(ctx, role_query)
    if not role:
        await ctx.send(f"Role related to '{role_query}' not found.")
        return
    if role in member.roles:
        await ctx.send(f"{member.mention} already has the {role.name} role.")
        return
    try:
        await member.add_roles(role)
        await ctx.send(f"Gave {role.name} to {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage that role.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, *, role_query: str):
    role = find_role(ctx, role_query)
    if not role:
        await ctx.send(f"Role related to '{role_query}' not found.")
        return
    if role not in member.roles:
        await ctx.send(f"{member.mention} does not have the {role.name} role.")
        return
    try:
        await member.remove_roles(role)
        await ctx.send(f"Removed {role.name} from {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage that role.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@giverole.error
@removerole.error
async def role_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Roles permission to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Couldn't find that member or role.")
    else:
        await ctx.send("An error occurred.")

#Timeout command <------------------------------------------------

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, duration: int, *, reason=None):
    try:
        await member.timeout(discord.utils.utcnow() + discord.timedelta(minutes=duration), reason=reason)
        await ctx.send(f"{member.mention} has been timed out for {duration} minutes. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to timeout {member.mention}: {e}")

@timeout.error
async def timeout_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to timeout members.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Could not find that member or invalid duration.")
    else:
        await ctx.send(f"An error occurred: {error}")

#Untimeout command <------------------------------------------------

@bot.command()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx, member: discord.Member):
    try:
        await member.timeout(None)
        await ctx.send(f"{member.mention} has been untimed out.")
    except Exception as e:
        await ctx.send(f"Failed to untimeout {member.mention}: {e}")

@untimeout.error
async def untimeout_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to untimeout members.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Could not find that member.")
    else:
        await ctx.send(f"An error occurred: {error}")

#Kick command <------------------------------------------------

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    try:
        await member.kick(reason=reason)
        await ctx.send(f"{member.mention} has been kicked. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to kick {member.mention}: {e}")

@kick.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to kick members.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Could not find that member.")
    else:
        await ctx.send(f"An error occurred: {error}")

#Ban command <------------------------------------------------

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    try:
        await member.ban(reason=reason)
        await ctx.send(f"{member.mention} has been banned. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to ban {member.mention}: {e}")

@ban.error
async def ban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to ban members.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Could not find that member.")
    else:
        await ctx.send(f"An error occurred: {error}")

#Unban command <------------------------------------------------

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, member):
    banned_users = await ctx.guild.bans()
    member_name, member_discriminator = member.split('#')

    for ban_entry in banned_users:
        user = ban_entry.user

        if (user.name, user.discriminator) == (member_name, member_discriminator):
            await ctx.guild.unban(user)
            await ctx.send(f"Unbanned {user.mention}")
            return

    await ctx.send(f"User {member} not found in ban list.")

@unban.error
async def unban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have permission to unban members.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid user ID. Please provide a valid integer ID.")
    else:
        await ctx.send(f"❌ An error occurred: {error}")

#Uwufy command <------------------------------------------------

# ------ Uwufy Webhook Helper ------
def uwufy_text(text):
    faces = [' >w<', ' ^^', ' owo', ' UwU', ' (✿◠‿◠)', ' (。UωU。)', ' ~', ' x3', ' rawr~']
    replacements = {
        'l': 'w', 'r': 'w', 'L': 'W', 'R': 'W',
        'no': 'nyo', 'No': 'Nyo', 'mo': 'myo', 'Mo': 'Myo', 'ove': 'uv', 'na': 'nya', 'Na': 'Nya'
    }
    def uwufy_word(word):
        if len(word) > 2 and random.random() < 0.7:
            word = f"{word[0]}-{word[0]}-{word}"
        for k, v in replacements.items():
            word = word.replace(k, v)
        if random.random() < 0.55:
            word += random.choice(faces)
        return word
    return ' '.join(uwufy_word(w) for w in text.split())

UWUFIED_USERS_WEBHOOK = set()

async def get_or_create_webhook(channel, user):
    webhooks = await channel.webhooks()
    for wh in webhooks:
        if wh.user == channel.guild.me and wh.name == f"Uwufy-{user.id}":
            return wh
    # Create new webhook
    return await channel.create_webhook(name=f"Uwufy-{user.id}")

# ------ on_message event for uwufy with webhook ------
@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    # Self-uwufy logic (skip commands here if you want)
    if (message.guild and (message.guild.id, message.author.id) in UWUFIED_USERS_WEBHOOK):
        try:
            await message.delete()
        except discord.errors.Forbidden:
            pass  # No perms to delete

        uwutext = uwufy_text(message.content)
        webhook = await get_or_create_webhook(message.channel, message.author)
        try:
            await webhook.send(
                uwutext,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                allowed_mentions=discord.AllowedMentions.none(),  # Don't ping
                wait=True,
            )
        except Exception as e:
            await message.channel.send(f"Uwufy error: {e}")
        return  # Don't process commands from uwufied users' messages

    await bot.process_commands(message)

# ------ Uwufy command (with error handling) ------
@bot.command(name="uwufy", help="Uwufy a user using webhooks! Their messages will be uwufied and re-sent in their name.")
@commands.has_permissions(administrator=True)
async def uwufy_webhook(ctx, member: discord.Member):
    if member == ctx.author:
        await ctx.send("You cannot uwufy yourself!")
        return
    if member.bot:
        await ctx.send("You cannot uwufy a bot!")
        return
    key = (ctx.guild.id, member.id)
    if key in UWUFIED_USERS_WEBHOOK:
        await ctx.send(f"{member.display_name} is already uwufied!")
    else:
        UWUFIED_USERS_WEBHOOK.add(key)
        await ctx.send(f"{member.display_name} will now be uwufied!")

@uwufy_webhook.error
async def uwufy_webhook_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to uwufy someone.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("User not found! Try mentioning the user or using their exact name.")
    else:
        await ctx.send(f"An error occurred: {error}")

# ------ Unuwufy command ------
@bot.command(name="unuwufy", help="Stop uwufying a user via webhook.")
@commands.has_permissions(administrator=True)
async def unuwufy_webhook(ctx, member: discord.Member):
    if member == ctx.author:
        await ctx.send("You cannot unuwufy yourself!")
        return
    key = (ctx.guild.id, member.id)
    if key in UWUFIED_USERS_WEBHOOK:
        UWUFIED_USERS_WEBHOOK.remove(key)
        await ctx.send(f"{member.display_name} is no longer uwufied.")
    else:
        await ctx.send(f"{member.display_name} is not uwufied.")

@unuwufy_webhook.error
async def unuwufy_webhook_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to unuwufy someone.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("User not found! Try mentioning the user or using their exact name.")
    else:
        await ctx.send(f"An error occurred: {error}")

# ------ Uwufy list command ------
@bot.command(name="uwufylist", help="List currently uwufied users (webhook version).")
@commands.has_permissions(administrator=True)
async def uwufylist_webhook(ctx):
    uwu_users = [ctx.guild.get_member(uid) for (gid, uid) in UWUFIED_USERS_WEBHOOK if gid == ctx.guild.id]
    uwu_mentions = [u.mention for u in uwu_users if u]
    await ctx.send("Uwufied users: " + (", ".join(uwu_mentions) if uwu_mentions else "None!"))

#Server Info command <------------------------------------------------

@bot.command(name="serverinfo", help="Show server statistics and information")
async def serverinfo(ctx):
    guild = ctx.guild

    # Channel counts
    text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
    voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
    categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)])

    # Roles - skip @everyone
    role_count = len([role for role in guild.roles if role.name != "@everyone"])

    # Emojis
    emoji_count = len(guild.emojis)

    # Features (e.g., Community, Verified, etc.)
    features = ', '.join([f.replace('_', ' ').title() for f in guild.features]) or "None"

    # Creation date
    created_at = discord.utils.format_dt(guild.created_at, style="F")

    # Owner
    owner = guild.owner

    # Boosts
    boost_count = guild.premium_subscription_count
    boost_tier = guild.premium_tier

    embed = discord.Embed(
        title=f"Server Info: {guild.name}",
        description=f"ID: `{guild.id}`",
        color=discord.Color.blue(),
        timestamp=ctx.message.created_at
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=str(owner), inline=True)
    embed.add_field(name="Created At", value=created_at, inline=True)
    embed.add_field(name="Members", value=f"🧑 {guild.member_count}", inline=False)
    embed.add_field(name="Channels", value=f"💬 Text: {text_channels}\n🔊 Voice: {voice_channels}\n📂 Categories: {categories}", inline=False)
    embed.add_field(name="Roles", value=f"{role_count}", inline=True)
    embed.add_field(name="Emojis", value=f"{emoji_count}", inline=True)
    embed.add_field(name="Boosts", value=f"Level {boost_tier} / {boost_count} boosts", inline=True)
    embed.add_field(name="Features", value=features, inline=False)
    await ctx.send(embed=embed)

#User info command <------------------------------------------------

@bot.command(name="userinfo", help="Show information about a user.")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    # Status emoji
    status_emojis = {
        discord.Status.online: "🟢 Online",
        discord.Status.idle: "🌙 Idle",
        discord.Status.dnd: "⛔ Do Not Disturb",
        discord.Status.offline: "⚫ Offline"
    }
    status_text = status_emojis.get(member.status, str(member.status).title())

    # Permissions - Get up to 5, add "..." if more
    perms = []
    perms_obj = ctx.channel.permissions_for(member)
    for name, value in perms_obj:
        if value:
            perms.append(name.replace("_", " ").title())
    if not perms:
        perm_str = "None"
    elif len(perms) > 5:
        perm_str = ", ".join(perms[:5]) + ", ..."
    else:
        perm_str = ", ".join(perms)

    embed = discord.Embed(
        title=f"User Info: {member}",
        description=f"Here’s some information about {member.mention}",
        color=member.color if hasattr(member, 'color') else discord.Color.blue(),
        timestamp=ctx.message.created_at
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=f"`{member}`", inline=True)
    if member.nick:
        embed.add_field(name="Nickname", value=f"`{member.nick}`", inline=True)
    embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="Status", value=status_text, inline=True)
    if member.activity:
        embed.add_field(name="Activity", value=str(member.activity), inline=True)
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style='F'), inline=False)
    embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, style='F'), inline=False)
    embed.add_field(name="Top Permissions", value=perm_str, inline=False)
    await ctx.send(embed=embed)

### LOGS ###

#Message delete log <------------------------------------------------

@bot.event
async def on_message_delete(message):
    if message.guild is None or message.author.bot:
        return
    channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🗑️ Message Deleted",
        description=f"In {message.channel.mention} by {message.author.mention}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Content", value=message.content or "*No content*", inline=False)
    embed.set_footer(text=f"User ID: {message.author.id} | Message ID: {message.id}")
    await channel.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.guild is None or before.author.bot or before.content == after.content:
        return
    channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="✏️ Message Edited",
        description=f"In {before.channel.mention} by {before.author.mention}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Before", value=before.content or "*No content*", inline=False)
    embed.add_field(name="After", value=after.content or "*No content*", inline=False)
    embed.set_footer(text=f"User ID: {before.author.id} | Message ID: {before.id}")
    await channel.send(embed=embed)

#Member join/leave log <------------------------------------------------

@bot.event
async def on_member_join(member):
    channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="👋 Member Joined",
        description=f"Welcome {member.mention} to {member.guild.name}!",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User ID", value=f"`{member.id}`")
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style='F'), inline=True)
    await channel.send(embed=embed)

@bot.event
async def on_member_remove(member):
    channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="👋 Member Left",
        description=f"{member.mention} has left {member.guild.name}.",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User ID", value=f"`{member.id}`")
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style='F'), inline=True)
    await channel.send(embed=embed)


#Role Change log <------------------------------------------------

@bot.event
async def on_member_update(before, after):
    if set(before.roles) == set(after.roles):
        return
    channel = bot.get_channel(MOD_LOG_CHANNEL_ID)

    before_role_ids = set(r.id for r in before.roles)
    after_role_ids = set(r.id for r in after.roles)

    gained_roles = [r for r in after.roles if r.id not in before_role_ids]
    lost_roles = [r for r in before.roles if r.id not in after_role_ids]

    for role in gained_roles:
        embed = discord.Embed(
            title="➕ Role Gained",
            description=f"{after.mention} gained {role.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"User ID: {after.id}")
        await channel.send(embed=embed)

    for role in lost_roles:
        embed = discord.Embed(
            title="➖ Role Lost",
            description=f"{after.mention} lost {role.mention}",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"User ID: {after.id}")
        await channel.send(embed=embed)

# Reminder command <------------------------------------------------

user_reminders = {}  # {user_id: [(reminder_time (datetime), message, task_handle), ...]}

def parse_time(text):
    """Parse a string like '1h30m' or '2h' or '40m' to seconds."""
    matches = re.findall(r'(\d+)\s*([hm])', text)
    seconds = 0
    for value, unit in matches:
        if unit == 'h':
            seconds += int(value) * 3600
        elif unit == 'm':
            seconds += int(value) * 60
    return seconds

async def send_reminder(user, message):
    try:
        await user.send(f"⏰ **Reminder:** {message}")
    except Exception:
        pass  # DM failure is ignored

def format_reminder_list(reminders):
    lines = []
    now = datetime.utcnow()
    for idx, (reminder_time, msg, _) in enumerate(reminders, 1):
        remaining = reminder_time - now
        if remaining.total_seconds() <= 0:
            continue
        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        tstr = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
        lines.append(f"`#{idx}` {msg} *(in {tstr})*")
    return "\n".join(lines) or "_No active reminders._"

@bot.group(name="reminder", invoke_without_command=True)
async def reminder(ctx):
    await ctx.send("Usage:\n,reminder set <message> <time>\n,reminder remove <number>\n,reminder list")

@reminder.command(name="set")
async def reminder_set(ctx, *, args):
    # args like: Do something 2h30m OR just 2h30m
    match = re.match(r"(.*?)(\d+\s*[hm].*)$", args.strip())
    if not match:
        await ctx.send("Format: `,reminder set <message> <time>`, e.g. `,reminder set Drink water 45m`")
        return
    message = match.group(1).strip() or "No message"
    timestr = match.group(2).strip()
    seconds = parse_time(timestr)
    if seconds == 0:
        await ctx.send("Couldn't parse a valid time! Use like `10m`, `2h30m`, etc.")
        return
    remind_time = datetime.utcnow() + timedelta(seconds=seconds)

    user_id = ctx.author.id
    user = ctx.author

    if user_id not in user_reminders:
        user_reminders[user_id] = []

    async def reminder_task():
        await asyncio.sleep(seconds)
        await send_reminder(user, message)
        # Remove reminder after it's sent
        user_reminders[user_id] = [r for r in user_reminders[user_id] if r[0] != remind_time]

    task = asyncio.create_task(reminder_task())
    user_reminders[user_id].append((remind_time, message, task))
    number = len(user_reminders[user_id])
    await ctx.send(f"✅ **#{number} Reminder has been set!**\n`{message}` in {timestr}")

@reminder.command(name="list")
async def reminder_list(ctx):
    user_id = ctx.author.id
    if user_id not in user_reminders or not user_reminders[user_id]:
        await ctx.send("_No active reminders set!_")
        return
    out = format_reminder_list(user_reminders[user_id])
    await ctx.send(f"**Your Reminders:**\n{out}")

@reminder.command(name="remove")
async def reminder_remove(ctx, number: int):
    user_id = ctx.author.id
    reminders = user_reminders.get(user_id, [])
    if not (1 <= number <= len(reminders)):
        await ctx.send(f"No reminder with number #{number}. Type `,reminder list` to see all.")
        return
    _, _, task = reminders[number-1]
    task.cancel()
    del reminders[number-1]
    # Renumbering happens automatically (no number stored)
    await ctx.send(f"❌ Removed reminder #{number}!")

@reminder.error
async def reminder_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument. Please check your command format.")
    else:
        await ctx.send(f"An error occurred: {error}")

# SPAM Command <------------------------------------------------

@bot.command(name="spam", help="Spam a user in their DMs with an optional reason.")
@commands.has_permissions(administrator=True)
async def spam(ctx, member: discord.Member, *, reason: str = None):
    # You can change this '10' to maxDMs you want to send
    spam_count = 7    

    # Prepare the message
    server_name = ctx.guild.name if ctx.guild else 'Direct Message'
    if reason:
        msg = f"🔔 {reason}\n(sent from {server_name})"
    else:
        msg = f"🔔 Please check the server **{server_name}**!"

    # Attempt to send the spam messages
    success = 0
    for _ in range(spam_count):
        try:
            await member.send(msg)
            success += 1
        except Exception:
            break  # Probably blocked DMs, stop trying

    await ctx.send(f"✅ Sent **{success}** DM(s) to {member.display_name}.")

# Handle missing permissions gracefully
@spam.error
async def spam_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need administrator permissions to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Could not find that user! Mention them directly i.e., `@user`.")
    else:
        await ctx.send(f"❌ An error occurred: {error}")


#BotBanner <------------------------------------------------

@bot.command(name="setbotbanner", help="Set the bot's profile banner (PNG/JPG/GIF, animated allowed for verified bots). Usage: ,setbotbanner [image_url or attach an image]")
async def setbotbanner(ctx, url: str = None):
    # Restrict to bot owner for safety!
    if ctx.author.id != 1250010443649650702:
        await ctx.send("❌ Only the bot owner can run this command.")
        return

    banner_bytes = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        banner_bytes = await att.read()
    elif url:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    banner_bytes = await resp.read()
    else:
        await ctx.send("❌ Attach an image/GIF or provide a direct image/GIF URL.")
        return

    try:
        await bot.user.edit(banner=banner_bytes)
        await ctx.send("✅ Bot profile banner updated!")
    except Exception as e:
        await ctx.send(f"❌ Couldn't update banner. Error: `{str(e)}`. Are you verified and is the file a valid static/animated image under 10MB?")

#Bot avatar <------------------------------------------------

@bot.command(name="setbotavatar", help="Set the bot's avatar (static image or GIF). Usage: ,setbotavatar [image_url or attach an image]")
async def setbotavatar(ctx, url: str = None):

    if ctx.author.id != 1250010443649650702 and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Only the bot owner or an administrator can run this command.")
        return

    avatar_bytes = None

    # Support direct attachment
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        avatar_bytes = await att.read()
    elif url:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    avatar_bytes = await resp.read()
                else:
                    await ctx.send("❌ Could not download from that URL. Attach an image/GIF or provide a valid direct link.")
                    return
    else:
        await ctx.send("❌ Attach an image/GIF or provide a direct image/GIF URL.")
        return

    try:
        await bot.user.edit(avatar=avatar_bytes)
        await ctx.send("✅ Bot avatar updated! It may take a few seconds to appear.")
    except Exception as e:
        await ctx.send(f"❌ Couldn't update avatar. Error: `{str(e)}`. Are you verified and is the file a valid image/GIF under 10MB?")


#Banner command <------------------------------------------------

@bot.command(name="banner", help="Show the banner of yourself or a mentioned user.")
async def banner(ctx, member: discord.Member=None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id)  # Supports banner fetching
    if user.banner:
        banner_url = user.banner.url if hasattr(user.banner, "url") else f"https://cdn.discordapp.com/banners/{user.id}/{user.banner}.png?size=4096"
        embed = discord.Embed(title=f"{member.display_name}'s Banner", color=discord.Color.from_rgb(0, 0, 0))
        embed.set_image(url=banner_url)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"{member.display_name} does not have a banner set.")


@banner.error
async def banner_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send("Error: Could not find that user. Please mention a valid member.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")


#Pfp command <------------------------------------------------

@bot.command(name="avatar", help="Show the avatar of yourself or a mentioned user.")
async def avatar(ctx, member: discord.Member=None):
    member = member or ctx.author
    avatar_url = member.display_avatar.url
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.from_rgb(0, 0, 0))
    embed.set_image(url=avatar_url)
    await ctx.send(embed=embed)


@avatar.error
async def avatar_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send("Error: Could not find that user. Please mention a valid member.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")
        

#Bot clear command <------------------------------------------------

@bot.command(name="bc", aliases=["botclear"], help="Delete messages sent by bots or starting with ',' in the last 20 messages (instant).")
@commands.has_permissions(manage_messages=True)
async def botclear(ctx):
    def should_delete(msg):
        return msg.author.bot or msg.content.startswith(",")
    deleted = await ctx.channel.purge(limit=20, check=should_delete, bulk=True)
    await ctx.send(f"✅ Deleted {len(deleted)} bot/command message(s).", delete_after=3)

@botclear.error
async def botclear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Messages permission to use this command.")
    else:
        await ctx.send(f"An error occurred: {error}")


#Cattto command <------------------------------------------------

@bot.command(name="cat", help="Sends a random cat photo.")
async def cat(ctx):
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
            if resp.status != 200:
                await ctx.send("Couldn't fetch cat image right now 😿")
                return
            data = await resp.json()
            image_url = data[0]["url"]
            await ctx.send(image_url)

@cat.error
async def cat_error(ctx, error):
    await ctx.send("An error occurred! Try using ,cat.")


#Bot status Holder <------------------------------------------------

def save_presence(status, activity, activity_type):
    data = {
        "status": status,
        "activity": activity,
        "activity_type": activity_type
    }
    with open(PRESENCE_FILE, "w") as f:
        json.dump(data, f)

def load_presence():
    try:
        with open(PRESENCE_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {"status": "online", "activity": None, "activity_type": "playing"}
    return data


#Bot status <------------------------------------------------

@bot.command(name="status", help="Set bot's status (online/dnd/idle/invisible). Usage: ,status dnd")
@commands.has_permissions(administrator=True)
async def status(ctx, mode: str):
    mode = mode.lower()
    valid_modes = {
        "online": discord.Status.online,
        "dnd": discord.Status.dnd,
        "idle": discord.Status.idle,
        "invisible": discord.Status.invisible
    }
    if mode not in valid_modes:
        await ctx.send("❌ Mode must be one of: online, dnd, idle, invisible")
        return
    # Load current activity for persistence
    data = load_presence()
    await bot.change_presence(status=valid_modes[mode],
                             activity=create_activity(data["activity"], data["activity_type"]))
    save_presence(mode, data["activity"], data["activity_type"])
    await ctx.send(f"✅ Bot status set to **{mode}**")


@status.error
async def status_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need administrator permissions to change the bot's status.")
    else:
        await ctx.send(f"❌ An error occurred: {error}")


#Bot written status <------------------------------------------------

def create_activity(name, type_str):
    if not name:
        return None
    types = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening
    }
    activity_type = types.get(type_str, discord.ActivityType.playing)
    return discord.Activity(type=activity_type, name=name)

@bot.command(name="setactivity", help="Set bot's activity. Usage: ,setactivity Chilling in /tonight")
@commands.has_permissions(administrator=True)
async def setactivity(ctx, *, activity: str):
    # Default type is 'playing', change as needed
    activity_type = "watching"
    # You can allow the user to choose, e.g. ,setactivity watching:SomeText
    # For now, just use 'playing'
    data = load_presence()
    await bot.change_presence(status=discord.Status[data["status"]],
                             activity=create_activity(activity, activity_type))
    save_presence(data["status"], activity, activity_type)
    await ctx.send(f"✅ Bot activity set to: **{activity}**")

@setactivity.error
async def setactivity_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need administrator permissions to change the bot's activity.")
    else:
        await ctx.send(f"❌ An error occurred: {error}")


#Error Handler <------------------------------------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        # Get the command name from the user's message
        content = ctx.message.content
        # Remove the prefix, then split and take the first piece as the command name
        attempted_name = content.lstrip(ctx.prefix).split(" ")[0]
        await ctx.send(f'An error occurred: Command "{attempted_name}" is not found')
    else:
        raise error

#Bot run <------------------------------------------------

bot.run(TOKEN)
