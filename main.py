import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import aiosqlite
import os
from dotenv import load_dotenv

# Load the bot token from environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True  # Required to fetch members

bot = commands.Bot(command_prefix="!", intents=intents)


# Initialize database and ensure tables exist
async def init_db():
    async with aiosqlite.connect("voice_times.db") as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS voice_times (
                user_id INTEGER PRIMARY KEY,
                total_time REAL,
                join_time TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS role_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER,
                required_time REAL
            )"""
        )
        await db.commit()


# Utility to interact with the database
async def fetch_one(query: str, *params):
    async with aiosqlite.connect("voice_times.db") as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchone()


async def execute_query(query: str, *params):
    async with aiosqlite.connect("voice_times.db") as db:
        await db.execute(query, params)
        await db.commit()


# Function to get user data from the database
async def get_user_data(user_id: int):
    return await fetch_one(
        "SELECT total_time, join_time FROM voice_times WHERE user_id = ?", user_id
    )


# Function to update total time for a user
async def update_total_time(user_id: int, total_time: float):
    await execute_query(
        "REPLACE INTO voice_times (user_id, total_time, join_time) VALUES (?, ?, ?)",
        user_id,
        total_time,
        None,
    )


# Function to update a user's join time
async def update_join_time(user_id: int, join_time: str):
    await execute_query(
        "REPLACE INTO voice_times (user_id, total_time, join_time) VALUES (?, (SELECT total_time FROM voice_times WHERE user_id = ?), ?)",
        user_id,
        user_id,
        join_time,
    )


# Reset a user's join time
async def reset_join_time(user_id: int):
    await execute_query(
        "UPDATE voice_times SET join_time = NULL WHERE user_id = ?", user_id
    )


# Delete a user's voice time data
async def delete_user_data(user_id: int):
    await execute_query("DELETE FROM voice_times WHERE user_id = ?", user_id)


# Reset all voice times
async def reset_all_voice_times():
    await execute_query("DELETE FROM voice_times")


# Fetch all role thresholds
async def get_role_thresholds():
    async with aiosqlite.connect("voice_times.db") as db:
        async with db.execute(
            "SELECT role_id, required_time FROM role_thresholds"
        ) as cursor:
            return await cursor.fetchall()


# Add a role threshold
async def add_role_threshold(role_id: int, required_time: float):
    await execute_query(
        "INSERT INTO role_thresholds (role_id, required_time) VALUES (?, ?)",
        role_id,
        required_time,
    )


# Remove a role threshold
async def remove_role_threshold(role_id: int):
    await execute_query("DELETE FROM role_thresholds WHERE role_id = ?", role_id)


# Event to track voice state changes
@bot.event
async def on_voice_state_update(
    member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
):
    user_id = member.id
    current_time = datetime.datetime.now()

    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        await update_join_time(user_id, current_time.isoformat())

    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        data = await get_user_data(user_id)
        if data and data[1] is not None:  # If join_time exists
            join_time = datetime.datetime.fromisoformat(data[1])
            time_spent = (current_time - join_time).total_seconds()
            total_time = (data[0] or 0) + time_spent

            await update_total_time(user_id, total_time)
            await reset_join_time(user_id)

            # Check for role thresholds
            await check_and_assign_roles(member, total_time)


# Check if the user qualifies for any roles based on time
async def check_and_assign_roles(member: discord.Member, total_time: float):
    role_thresholds = await get_role_thresholds()
    for role_id, required_time in role_thresholds:
        role = member.guild.get_role(role_id)
        if total_time >= required_time * 60 and role and role not in member.roles:
            await member.add_roles(role)
            await member.send(
                f"Congrats! You've been given the **{role.name}** role for spending {required_time} minutes in voice chat!"
            )


# Command to check voice time
@bot.tree.command(
    name="mytime", description="Check your or another user's voice chat time"
)
@app_commands.describe(member="The member whose time you want to check")
async def mytime(interaction: discord.Interaction, member: discord.Member = None):
    if member is None:
        member = interaction.user
    user_id = member.id
    current_time = datetime.datetime.now()

    data = await get_user_data(user_id)
    if data is None:
        await interaction.response.send_message(
            f"{member} hasn't spent any time in voice channels yet."
        )
        return

    total_time = data[0] or 0  # Accumulated time
    join_time = data[1]  # If currently in a voice channel

    if join_time:
        join_time = datetime.datetime.fromisoformat(join_time)
        total_time += (current_time - join_time).total_seconds()

    hours, remainder = divmod(total_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.response.send_message(
        f"{member}, you've spent **{int(hours)} hours**, **{int(minutes)} minutes**, and **{int(seconds)} seconds** in voice chat."
    )


# Command to delete a user's voice chat time
@bot.tree.command(name="delete_time", description="Delete a user's voice chat time")
@app_commands.describe(member="The member whose voice chat time you want to delete")
async def delete_time(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You don't have permission to delete voice chat times.", ephemeral=True
        )
        return

    if await get_user_data(member.id) is None:
        await interaction.response.send_message(
            f"{member} has no voice chat time recorded."
        )
    else:
        await delete_user_data(member.id)
        await interaction.response.send_message(
            f"Deleted voice chat time for {member}."
        )


# Command to add a role threshold
@bot.tree.command(
    name="add_threshold", description="Set a role and its required time threshold"
)
@app_commands.describe(
    role="The role to assign", time_in_minutes="Time in minutes required for the role"
)
@commands.has_permissions(administrator=True)
async def add_threshold(
    interaction: discord.Interaction, role: discord.Role, time_in_minutes: int
):
    await add_role_threshold(role.id, time_in_minutes)
    await interaction.response.send_message(
        f"Threshold added: {role.mention} will be assigned after {time_in_minutes} minutes in voice chat."
    )


# Command to remove a role threshold
@bot.tree.command(name="remove_threshold", description="Remove a role's time threshold")
@app_commands.describe(role="The role to remove the threshold for")
@commands.has_permissions(administrator=True)
async def remove_threshold(interaction: discord.Interaction, role: discord.Role):
    await remove_role_threshold(role.id)
    await interaction.response.send_message(
        f"Threshold for {role.mention} has been removed."
    )


# Command to list all role thresholds
@bot.tree.command(name="list_thresholds", description="List all role time thresholds")
@commands.has_permissions(administrator=True)
async def list_thresholds(interaction: discord.Interaction):
    thresholds = await get_role_thresholds()
    if not thresholds:
        await interaction.response.send_message("No thresholds set.")
    else:
        threshold_list = "\n".join(
            f"<@&{role_id}>: {required_time} minutes"
            for role_id, required_time in thresholds
        )
        await interaction.response.send_message(
            f"Current role thresholds:\n{threshold_list}"
        )


# Start or stop automated messages
auto_message_settings = {"channel_id": None, "message": None}


@tasks.loop(hours=12)
async def send_automated_message():
    channel_id = auto_message_settings["channel_id"]
    message = auto_message_settings["message"]
    if channel_id and message:
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(message)


@bot.command(name="start_auto_message")
@commands.has_permissions(administrator=True)
async def start_auto_message(ctx, channel: discord.TextChannel, *, message: str):
    auto_message_settings["channel_id"] = channel.id
    auto_message_settings["message"] = message
    send_automated_message.start()
    await ctx.send(
        f"Automated message set for {channel.mention}. Message will be sent every 12 hours."
    )


@bot.command(name="stop_auto_message")
@commands.has_permissions(administrator=True)
async def stop_auto_message(ctx):
    send_automated_message.stop()
    await ctx.send("Automated messaging stopped.")


# Sync bot commands when ready
@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


# Run the bot
bot.run(BOT_TOKEN)
