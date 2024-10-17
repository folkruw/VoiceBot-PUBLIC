import enum
import json
import os

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True  # Enable intents for voice states

bot = commands.Bot(command_prefix='', intents=intents)

with open("token.txt", "r") as file:
    TOKEN = file.read().strip()

DATA_FILE = "voicebot_data.json"


class Actions(enum.Enum):
    ADD = 1
    REMOVE = 2


class ConfigType(enum.Enum):
    BDA = 1
    REF = 2
    INVISIBLE = 3


class Types(enum.Enum):
    ROLE = 1
    MANAGE = 2
    COMMAND = 3
    CITIZENS = 4


class VoiceBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bda_channel_ids, self.ref_channel_ids, self.invisible_channel_ids, self.temporary_channels, self.allowed_roles, self.command_roles, \
            self.manage_roles, self.citizens = self.load_data()

    @staticmethod
    def load_data():
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as file:
                try:
                    data = json.load(file)
                    return (
                        data.get("bda_channel_ids", []),
                        data.get("ref_channel_ids", []),
                        data.get("invisible_channel_ids", []),
                        data.get("temporary_channels", []),
                        data.get("allowed_roles", []),
                        data.get("command_roles", []),
                        data.get("manage_roles", []),
                        data.get("citizens", []),
                    )
                except json.JSONDecodeError:
                    return [], [], [], None, []
        return [], [], [], None, []

    def save_data(self):
        with open(DATA_FILE, 'w') as file:
            json.dump({
                "bda_channel_ids": self.bda_channel_ids,
                "ref_channel_ids": self.ref_channel_ids,
                "invisible_channel_ids": self.invisible_channel_ids,
                "temporary_channels": self.temporary_channels,
                "allowed_roles": self.allowed_roles,
                "command_roles": self.command_roles,
                "manage_roles": self.manage_roles,
                "citizens": self.citizens,
            }, file)

    def is_authorized(self, member):
        if member.guild_permissions.administrator:
            return True
        for role in member.roles:
            if role.id in self.command_roles:
                return True
        return False

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return

        if not (self.bda_channel_ids or self.ref_channel_ids or self.invisible_channel_ids):
            return

        channel_type = None

        if after.channel:
            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False),
            }

            channel_configs = {
                'BDA': {
                    'channel_name': f"🚨️ BDA pour {member.display_name}",
                    'permissions': {
                        "manage": {"connect": True, "view_channel": True, "manage_channels": True},
                        "allowed": {"connect": True, "view_channel": True},
                        "citizens": {"connect": False, "view_channel": True}
                    }
                },
                'REF': {
                    'channel_name': f"🥑 Entretien",
                    'permissions': {
                        member: {"connect": True, "view_channel": True, "manage_channels": True, "manage_roles": True},
                        "manage": {"connect": True, "view_channel": True, "manage_channels": True,
                                   "manage_roles": True},
                        "allowed": {"connect": False, "view_channel": True, "manage_roles": False,
                                    "manage_channels": False},
                        "citizens": {"connect": False, "view_channel": True}
                    }
                },
                'INVISIBLE': {
                    'channel_name': f"🔒・Bureau invisible {member.display_name}",
                    'permissions': {
                        member: {"connect": True, "view_channel": True, "manage_channels": True, "manage_roles": True},
                        "manage": {"connect": True, "view_channel": True, "manage_channels": True,
                                   "manage_roles": True},
                        "allowed": {"connect": False, "view_channel": True, "manage_channels": False,
                                    "manage_roles": False},
                        "citizens": {"connect": False, "view_channel": False}
                    }
                }
            }

            if after.channel.id in self.bda_channel_ids:
                channel_type = 'BDA'
            elif after.channel.id in self.ref_channel_ids:
                channel_type = 'REF'
            elif after.channel.id in self.invisible_channel_ids:
                channel_type = 'INVISIBLE'

            if channel_type:
                config = channel_configs[channel_type]
                channel_name = config['channel_name']
                permissions = config['permissions']

                for role_id in self.manage_roles + self.allowed_roles + self.citizens:
                    role = member.guild.get_role(role_id)
                    if role:
                        if role_id in self.manage_roles:
                            overwrites[role] = discord.PermissionOverwrite(**permissions["manage"])
                        elif role_id in self.allowed_roles:
                            overwrites[role] = discord.PermissionOverwrite(**permissions["allowed"])
                        elif role_id in self.citizens:
                            overwrites[role] = discord.PermissionOverwrite(**permissions["citizens"])

                if member in permissions:
                    overwrites[member] = discord.PermissionOverwrite(**permissions[member])

                temp_channel = None
                if channel_type == "BDA":
                    temp_channel = await member.guild.create_voice_channel(name=channel_name, overwrites=overwrites,
                                                                           reason=f'{channel_type}',
                                                                           category=after.channel.category)

                elif channel_type == "REF" or channel_type == "INVISIBLE":
                    # Clone
                    temp_channel = await after.channel.clone(name=channel_name, reason=f'{channel_type}')
                    await temp_channel.set_permissions(member, **permissions[member])

                    for role_id in self.manage_roles + self.allowed_roles + self.citizens:
                        role = member.guild.get_role(role_id)
                        if role:
                            if role_id in self.manage_roles:
                                await temp_channel.set_permissions(role, **permissions["manage"])
                            elif role_id in self.allowed_roles:
                                await temp_channel.set_permissions(role, **permissions["allowed"])
                            elif role_id in self.citizens:
                                await temp_channel.set_permissions(role, **permissions["citizens"])

                await member.move_to(temp_channel)
                self.temporary_channels.append(temp_channel.id)
                self.save_data()

        if before.channel and before.channel.id in self.temporary_channels and not before.channel.members:
            await before.channel.delete()
            self.temporary_channels.remove(before.channel.id)
            self.save_data()


@bot.tree.command(name="vb_config", description="Configurer les paramètres d'attente (BDA, REF, INVISIBLE)")
async def config_command(interaction: discord.Interaction, action: Actions, type: ConfigType, channel_id: str):
    voice_bot_cog = bot.get_cog('VoiceBot')
    if not voice_bot_cog:
        voice_bot_cog = VoiceBot(bot)
        await bot.add_cog(voice_bot_cog)

    if voice_bot_cog.is_authorized(interaction.user):
        if not channel_id.isdigit() or len(channel_id) not in [18, 19]:
            await interaction.response.send_message("L'ID du salon n'est pas correct.")
            return

        channel = bot.get_channel(int(channel_id))
        if not channel:
            await interaction.response.send_message("Ce salon n'existe pas.")
            return

        if ConfigType(type) not in ConfigType:
            await interaction.response.send_message(
                "Type de configuration invalide (BDA, REF, INVISIBLE).")
            return

        channels = []
        if ConfigType(type) == ConfigType.BDA:
            channels = voice_bot_cog.bda_channel_ids
        elif ConfigType(type) == ConfigType.REF:
            channels = voice_bot_cog.ref_channel_ids
        elif ConfigType(type) == ConfigType.INVISIBLE:
            channels = voice_bot_cog.invisible_channel_ids

        if Actions(action) == Actions.ADD:
            if int(channel_id) not in channels:
                channels.append(int(channel_id))
                voice_bot_cog.save_data()
                await interaction.response.send_message(
                    f"Le salon <#{channel_id}> est maintenant sur la liste des salons d'attentes pour '{type}'")
            else:
                await interaction.response.send_message(f"Le salon <#{channel_id}> est déjà inscrit pour '{type}'.")
        elif Actions(action) == Actions.REMOVE:
            if int(channel_id) in channels:
                channels.remove(int(channel_id))
                voice_bot_cog.save_data()
                await interaction.response.send_message(
                    f"Le salon <#{channel_id}> a été retiré de la liste des salons d'attentes pour '{type}'.")
            else:
                await interaction.response.send_message(f"Le salon <#{channel_id}> n'est pas inscrit pour '{type}'.")
    else:
        await interaction.response.send_message("Vous n'avez pas les permissions de faire cela.")


@bot.tree.command(name="vb_manage", description="Gérer les rôles pour les paramètres (permissions)")
async def manage_command(interaction: discord.Interaction, action: Actions, type: Types, role: discord.Role):
    voice_bot_cog = bot.get_cog('VoiceBot')
    if not voice_bot_cog:
        voice_bot_cog = VoiceBot(bot)
        await bot.add_cog(voice_bot_cog)

    if not voice_bot_cog.is_authorized(interaction.user):
        await interaction.response.send_message("Vous n'avez pas les permissions de faire cela.")
        return

    if Actions(action) not in Actions:
        await interaction.response.send_message("Action invalide. Veuillez utiliser 'ADD' ou 'REMOVE'.")
        return

    if Types(type) not in Types:
        await interaction.response.send_message(
            "Type de rôle invalide. Veuillez utiliser 'role', 'manage', 'command'.")
        return

    roles = []
    if Types(type) == Types.ROLE:
        roles = voice_bot_cog.allowed_roles
    elif Types(type) == Types.MANAGE:
        roles = voice_bot_cog.manage_roles
    elif Types(type) == Types.COMMAND:
        roles = voice_bot_cog.command_roles
    elif Types(type) == Types.CITIZENS:
        roles = voice_bot_cog.citizens

    if Actions(action) == Actions.ADD:
        if role.id not in roles:
            roles.append(role.id)
            voice_bot_cog.save_data()
            await interaction.response.send_message(
                f"Le rôle {role.name} a été ajouté à la liste des rôles pour {type}.")
        else:
            await interaction.response.send_message(
                f"Le rôle {role.name} est déjà dans la liste des rôles pour {type}.")
    elif Actions(action) == Actions.REMOVE:
        if role.id in roles:
            roles.remove(role.id)
            voice_bot_cog.save_data()
            await interaction.response.send_message(
                f"Le rôle {role.name} a été retiré de la liste des rôles pour {type}.")
        else:
            await interaction.response.send_message(
                f"Le rôle {role.name} n'est pas dans la liste des rôles pour {type}.")


@bot.tree.command(name="vb_list_config", description="Liste toutes les configurations et rôles")
async def list_config_command(interaction: discord.Interaction):
    voice_bot_cog = bot.get_cog('VoiceBot')
    if not voice_bot_cog:
        voice_bot_cog = VoiceBot(bot)
        await bot.add_cog(voice_bot_cog)

    if voice_bot_cog.is_authorized(interaction.user):
        config_data = {
            "bda_channel_ids": voice_bot_cog.bda_channel_ids,  # Liste des salons d'attente BDA
            "ref_channel_ids": voice_bot_cog.ref_channel_ids,  # Liste des salons d'attente REF
            "invisible_channel_ids": voice_bot_cog.invisible_channel_ids,  # Liste des salons d'attente bureau invisible
            "temporary_channels": voice_bot_cog.temporary_channels,
            "allowed_roles": [interaction.guild.get_role(role_id).name for role_id in voice_bot_cog.allowed_roles if
                              interaction.guild.get_role(role_id)],
            "command_roles": [interaction.guild.get_role(role_id).name for role_id in voice_bot_cog.command_roles if
                              interaction.guild.get_role(role_id)],
            "manage_roles": [interaction.guild.get_role(role_id).name for role_id in voice_bot_cog.manage_roles if
                             interaction.guild.get_role(role_id)],
        }
        await interaction.response.send_message(f"Configuration: {json.dumps(config_data, indent=2)}")
    else:
        await interaction.response.send_message("Vous n'avez pas les permissions de faire cela.")


@bot.tree.command(name="vb_clear", description="Nettoyer les salons vides")
async def clear_channels_command(interaction: discord.Interaction):
    voice_bot_cog = bot.get_cog('VoiceBot')
    if not voice_bot_cog:
        voice_bot_cog = VoiceBot(bot)
        await bot.add_cog(voice_bot_cog)

    if voice_bot_cog.is_authorized(interaction.user):
        deleted_channels = []

        # Iterate through all channel lists
        all_channel_lists = [voice_bot_cog.temporary_channels]
        for channel_list in all_channel_lists:
            for channel_id in channel_list[:]:
                channel = bot.get_channel(channel_id)
                if channel and not channel.members:
                    await channel.delete()
                    channel_list.remove(channel_id)
                    deleted_channels.append(channel_id)
                elif not channel:
                    channel_list.remove(channel_id)

        voice_bot_cog.save_data()

        if deleted_channels:
            await interaction.response.send_message("Tous les salons vides ont été supprimés.")
        else:
            await interaction.response.send_message("Aucun salon vide trouvé à supprimer.")
    else:
        await interaction.response.send_message("Vous n'avez pas les permissions de faire cela.")


@bot.event
async def on_ready():
    print(f'{bot.user} is connected to the following server(s):\n')
    for guild in bot.guilds:
        print(f'{guild.name} (id: {guild.id})')
    await bot.tree.sync()  # Sync slash commands
    await bot.loop.create_task(setup())  # Add commands


async def setup():
    await bot.wait_until_ready()
    if not bot.get_cog('VoiceBot'):
        await bot.add_cog(VoiceBot(bot))


bot.run(TOKEN)

# Bot invite link for permission scopes:
# Includes : Manage Roles, Mange Channels, View Channels, Send Messages, Move Members
# https://discord.com/oauth2/authorize?client_id=1247881264418263121&permissions=285215760&integration_type=0&scope=bot