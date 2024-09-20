import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import sqlite3
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

# Set up the database connection
conn = sqlite3.connect("voice_times.db")
c = conn.cursor()

# Create the table to store voice times if it doesn't exist
c.execute(
    """CREATE TABLE IF NOT EXISTS voice_times (
                user_id INTEGER PRIMARY KEY,
                total_time REAL,
                join_time TEXT
            )"""
)
conn.commit()

# Create the table to store role thresholds
c.execute(
    """CREATE TABLE IF NOT EXISTS role_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER,
                required_time REAL
            )"""
)
conn.commit()

# Variables for automated messaging
auto_message_channel_id = None
auto_message_content = None


# Function to get a user's data from the database
def get_user_data(user_id):
    c.execute(
        "SELECT total_time, join_time FROM voice_times WHERE user_id = ?", (user_id,)
    )
    return c.fetchone()


# Function to update a user's total time in the database
def update_total_time(user_id, total_time):
    c.execute(
        "REPLACE INTO voice_times (user_id, total_time, join_time) VALUES (?, ?, ?)",
        (user_id, total_time, None),
    )
    conn.commit()


# Function to update a user's join time in the database
def update_join_time(user_id, join_time):
    c.execute(
        "REPLACE INTO voice_times (user_id, total_time, join_time) VALUES (?, (SELECT total_time FROM voice_times WHERE user_id = ?), ?)",
        (user_id, user_id, join_time),
    )
    conn.commit()


# Function to reset a user's join time
def reset_join_time(user_id):
    c.execute("UPDATE voice_times SET join_time = NULL WHERE user_id = ?", (user_id,))
    conn.commit()


# Function to delete a specific user's voice chat time
def delete_user_data(user_id):
    c.execute("DELETE FROM voice_times WHERE user_id = ?", (user_id,))
    conn.commit()


# Function to reset all users' voice chat time
def reset_all_voice_times():
    c.execute("DELETE FROM voice_times")
    conn.commit()


# Function to get all role thresholds from the database
def get_role_thresholds():
    c.execute("SELECT role_id, required_time FROM role_thresholds")
    return c.fetchall()


# Function to add a new role threshold
def add_role_threshold(role_id, required_time):
    c.execute(
        "INSERT INTO role_thresholds (role_id, required_time) VALUES (?, ?)",
        (role_id, required_time),
    )
    conn.commit()


# Function to remove a role threshold
def remove_role_threshold(role_id):
    c.execute("DELETE FROM role_thresholds WHERE role_id = ?", (role_id,))
    conn.commit()


# Event to handle voice state updates (joining/leaving voice channels)
@bot.event
async def on_voice_state_update(member, before, after):
    user_id = member.id
    current_time = datetime.datetime.now()

    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        # Update their join time in the database
        update_join_time(user_id, current_time.isoformat())

    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        # Fetch the user's join time
        data = get_user_data(user_id)
        if data and data[1] is not None:
            join_time = datetime.datetime.fromisoformat(data[1])
            time_spent = current_time - join_time

            # Add the time spent to their total time
            total_time = (data[0] or 0) + time_spent.total_seconds()
            update_total_time(user_id, total_time)

            # Reset their join time
            reset_join_time(user_id)

            # Check if the user has hit any thresholds
            await check_and_assign_roles(member, total_time)


# Function to check and assign roles based on time spent
async def check_and_assign_roles(member, total_time):
    role_thresholds = get_role_thresholds()
    for role_id, required_time in role_thresholds:
        required_seconds = required_time * 60  # Convert to seconds
        role = member.guild.get_role(role_id)
        if total_time >= required_seconds and role and role not in member.roles:
            await member.add_roles(role)
            await member.send(
                f"Congrats! You've been given the **{role.name}** role for spending {required_time} minutes in voice chat!"
            )


# Command to check your own voice chat time
@bot.tree.command(
    name="mytime", description="Check your or another user's voice chat time"
)
@app_commands.describe(member="The member whose time you want to check")
async def mytime(interaction: discord.Interaction, member: discord.Member = None):
    if member is None:
        member = interaction.user  # Default to the user who invoked the command
    user_id = member.id
    current_time = datetime.datetime.now()

    # Fetch the user's data
    data = get_user_data(user_id)

    # If no data found, respond accordingly
    if data is None:
        await interaction.response.send_message(
            f"{member.mention} hasn't spent any time in voice channels yet."
        )
        return

    total_time = data[0] or 0  # Accumulated time
    join_time = data[1]  # Current join time if they are in a voice channel

    # If they are currently in a voice channel, add the time since they joined
    if join_time is not None:
        join_time = datetime.datetime.fromisoformat(join_time)
        total_time += (current_time - join_time).total_seconds()

    # Format the total time
    hours, remainder = divmod(total_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.response.send_message(
        f"{member.mention}, you've spent **{int(hours)} hours**, **{int(minutes)} minutes**, and **{int(seconds)} seconds** in voice chat."
    )


# Command to delete a specific user's voice chat time
@bot.tree.command(name="delete_time", description="Delete a user's voice chat time")
@app_commands.describe(member="The member whose voice chat time you want to delete")
async def delete_time(interaction: discord.Interaction, member: discord.Member):
    # Check if the user has administrator permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You do not have permission to delete voice chat times.", ephemeral=True
        )
        return

    user_id = member.id

    # Check if the user has data in the database
    if get_user_data(user_id) is None:
        await interaction.response.send_message(
            f"{member.mention} does not have any voice chat time recorded."
        )
        return

    # Delete the user's voice chat time
    delete_user_data(user_id)
    await interaction.response.send_message(
        f"Deleted voice chat time for {member.mention}."
    )


@tasks.loop(hours=12)
async def send_automated_message():
    global auto_message_channel_id, auto_message_content
    if auto_message_channel_id and auto_message_content:
        channel = bot.get_channel(auto_message_channel_id)
        if channel:
            await channel.send(auto_message_content)


# Prefix command to start the automated messaging
@bot.command(name="start_auto_message")
@commands.has_permissions(administrator=True)
async def start_auto_message(ctx, channel: discord.TextChannel, *, message: str):
    global auto_message_channel_id, auto_message_content
    auto_message_channel_id = channel.id
    auto_message_content = message
    send_automated_message.start()  # Start the task
    await ctx.send(
        f"Automated messaging started. Message will be sent to {channel.mention} every 12 hours."
    )


# Prefix command to stop the automated messaging
@bot.command(name="stop_auto_message")
@commands.has_permissions(administrator=True)
async def stop_auto_message(ctx):
    send_automated_message.stop()  # Stop the task
    await ctx.send("Automated messaging has been stopped.")


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
    add_role_threshold(role.id, time_in_minutes)
    await interaction.response.send_message(
        f"Threshold added: {role.mention} will be assigned after {time_in_minutes} minutes in voice chat."
    )


# Command to remove a role threshold
@bot.tree.command(name="remove_threshold", description="Remove a role's time threshold")
@app_commands.describe(role="The role to remove the threshold for")
@commands.has_permissions(administrator=True)
async def remove_threshold(interaction: discord.Interaction, role: discord.Role):
    remove_role_threshold(role.id)
    await interaction.response.send_message(
        f"Threshold for {role.mention} has been removed."
    )


# Command to list all role thresholds
@bot.tree.command(name="list_thresholds", description="List all role time thresholds")
@commands.has_permissions(administrator=True)
async def list_thresholds(interaction: discord.Interaction):
    thresholds = get_role_thresholds()
    if not thresholds:
        await interaction.response.send_message("No thresholds set.")
        return
    threshold_list = "\n".join(
        [
            f"<@&{role_id}>: {required_time} minutes"
            for role_id, required_time in thresholds
        ]
    )
    await interaction.response.send_message(
        f"Current role thresholds:\n{threshold_list}"
    )


# Command to reset all users' voice chat time (restricted to server owner)
@bot.tree.command(
    name="reset_all_times", description="Reset everyone's voice chat time"
)
async def reset_all_times(interaction: discord.Interaction):
    if (
        interaction.user.id == interaction.guild.owner_id
    ):  # Check if the user is the server owner
        reset_all_voice_times()
        await interaction.response.send_message(
            "All users' voice chat times have been reset."
        )
    else:
        await interaction.response.send_message(
            "Only the server owner can reset all users' voice chat times.",
            ephemeral=True,
        )


# Ensure bot syncs slash commands
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


# Run the bot
bot.run(BOT_TOKEN)
