# Ce fichier est trop long pour une seule réponse ici.
# Je vais l'écrire par morceaux. Voici la première partie (structure de base + classe VoiceBot + gestion du JSON + commandes de base + on_ready + lancement)
# Les vues UI arrivent juste après dans le message suivant.

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction, SelectOption, ButtonStyle
import json
import os
import aiohttp
import enum

# Fichiers de données
TOKEN_FILE = "token.txt"
DATA_FILE = "voicebot_data.json"
TEMPLATE_FILE = "channel_templates.json"
WEBHOOKS_FILE = "webhooks.json"

# Chargement du token
with open(TOKEN_FILE, "r", encoding="utf-8") as file:
    TOKEN = file.read().strip()

# Intents
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

# Création du bot
bot = commands.Bot(command_prefix="!", intents=intents)


# Fonctions utilitaires
def load_json(file_path, default={}):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return default
    return default


def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def load_webhooks():
    return load_json(WEBHOOKS_FILE).get("default", [])


async def log_to_webhook(message: str):
    webhooks = load_webhooks()
    async with aiohttp.ClientSession() as session:
        for url in webhooks:
            try:
                await session.post(url, json={"content": f"[VoiceBot] {message}"})
            except Exception as e:
                print(f"Webhook logging failed for {url}: {e}")


class Actions(enum.Enum):
    ADD = 1
    REMOVE = 2


class Types(enum.Enum):
    ROLE = 1
    GESTION = 2
    COMMAND = 3
    CITIZENS = 4


class VoiceBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_json(DATA_FILE, {})
        self.templates = load_json(TEMPLATE_FILE, {})

        self.temporary_channels = self.data.get("temporary_channels", [])
        self.roles_config = {k: v for k, v in self.data.items() if k.endswith("_roles")}
        self.command_roles = self.data.get("command_roles", [])
        self.citizens = self.data.get("citizens", [])

    def save_data(self):
        data = {
            "temporary_channels": self.temporary_channels,
            "command_roles": self.command_roles,
            "citizens": self.citizens,
        }
        data.update(self.roles_config)
        save_json(DATA_FILE, data)

    def save_templates(self):
        save_json(TEMPLATE_FILE, self.templates)

    def is_authorized(self, member):
        return member.guild_permissions.administrator or any(role.id in self.command_roles for role in member.roles)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return

        if before.channel and before.channel.id in self.temporary_channels and not before.channel.members:
            await before.channel.delete()
            self.temporary_channels.remove(before.channel.id)
            self.save_data()
            await log_to_webhook(f"Salon temporaire supprimé : {before.channel.name} (ID: {before.channel.id})")

        if not after.channel:
            return

        matched_template = None
        for template in self.templates.values():
            if after.channel.id in template.get("trigger_channel_ids", []):
                matched_template = template
                break

        if not matched_template:
            return

        channel_name = matched_template.get("channel_name_template", "Salon - {username}").format(
            username=member.display_name)
        permissions = matched_template.get("permissions", {})

        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False)
        }

        for key, role_ids in self.roles_config.items():
            for role_id in role_ids:
                role = member.guild.get_role(role_id)
                if role and key.replace("_roles", "") in permissions:
                    perms = permissions.get(key.replace("_roles", ""), {})
                    overwrites[role] = discord.PermissionOverwrite(**perms)

        for role_id in self.citizens:
            role = member.guild.get_role(role_id)
            if role and "citizens" in permissions:
                perms = permissions.get("citizens", {})
                overwrites[role] = discord.PermissionOverwrite(**perms)

        if "member" in permissions:
            overwrites[member] = discord.PermissionOverwrite(**permissions["member"])

        temp_channel = await after.channel.clone(name=channel_name, reason="Auto salon via template")
        await temp_channel.edit(overwrites=overwrites)
        await member.move_to(temp_channel)
        self.temporary_channels.append(temp_channel.id)
        self.save_data()
        await log_to_webhook(
            f"Salon créé pour {member.display_name} via template → {temp_channel.name} (ID: {temp_channel.id})")


async def get_voice_bot():
    voice_bot = bot.get_cog("VoiceBot")
    if voice_bot is None:
        voice_bot = VoiceBot(bot)
        await bot.add_cog(voice_bot)
    return voice_bot


@bot.tree.command(name="vb_add_role", description="Ajouter un rôle à une catégorie personnalisée")
@app_commands.describe(category="Nom de la catégorie", role="Rôle à ajouter")
async def vb_add_role(interaction: discord.Interaction, category: str, role: discord.Role):
    voice_bot = await get_voice_bot()
    if not voice_bot.is_authorized(interaction.user):
        await interaction.response.send_message("Permission refusée.", ephemeral=True)
        return

    if not category.endswith("_roles"):
        category += "_roles"

    if category not in voice_bot.roles_config:
        voice_bot.roles_config[category] = []

    if role.id not in voice_bot.roles_config[category]:
        voice_bot.roles_config[category].append(role.id)
        voice_bot.save_data()
        await interaction.response.send_message(f"✅ Rôle `{role.name}` ajouté à `{category}`.", ephemeral=True)
        await log_to_webhook(
            f"{interaction.user.display_name} a ajouté le rôle {role.name} ({role.id}) dans {category}")
    else:
        await interaction.response.send_message(f"⚠️ Le rôle `{role.name}` est déjà présent dans `{category}`.",
                                                ephemeral=True)


@bot.tree.command(name="vb_remove_role", description="Retirer un rôle d'une catégorie personnalisée")
@app_commands.describe(category="Nom de la catégorie", role="Rôle à retirer")
async def vb_remove_role(interaction: discord.Interaction, category: str, role: discord.Role):
    voice_bot = await get_voice_bot()
    if not voice_bot.is_authorized(interaction.user):
        await interaction.response.send_message("Permission refusée.", ephemeral=True)
        return

    if not category.endswith("_roles"):
        category += "_roles"

    if category not in voice_bot.roles_config or role.id not in voice_bot.roles_config[category]:
        await interaction.response.send_message(f"⚠️ Le rôle `{role.name}` n'est pas dans `{category}`.",
                                                ephemeral=True)
        return

    voice_bot.roles_config[category].remove(role.id)
    voice_bot.save_data()
    await interaction.response.send_message(f"✅ Rôle `{role.name}` retiré de `{category}`.", ephemeral=True)
    await log_to_webhook(f"{interaction.user.display_name} a retiré le rôle {role.name} ({role.id}) de {category}")


class RoleCategoryDropdown(ui.Select):
    def __init__(self, voice_bot, parent):
        categories = list(voice_bot.roles_config.keys())
        for extra in ["command_roles", "citizens"]:
            if extra not in categories:
                categories.append(extra)
        options = [SelectOption(label=cat, value=cat) for cat in categories]
        super().__init__(placeholder="Choisir une catégorie", options=options)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        view = AddRoleSelectView(self.parent.voice_bot, interaction.user, selected_category=selected)
        await interaction.response.edit_message(
            content=f"Catégorie sélectionnée : `{selected}`",
            view=view
        )


class RoleMultiSelect(ui.Select):
    def __init__(self, parent, selected_category):
        guild = parent.user.guild
        existing_ids = parent.voice_bot.roles_config.get(selected_category, [])
        options = [
            SelectOption(label=role.name, value=str(role.id), default=(role.id in existing_ids))
            for role in guild.roles if not role.is_default()
        ]
        super().__init__(
            placeholder="Select roles to adjust",
            options=options,
            min_values=0,
            max_values=len(options)
        )
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent.selected_roles = [int(v) for v in self.values]
        await interaction.response.send_message(
            f"{len(self.values)} role(s) selected.",
            ephemeral=True
        )


class AddRoleSelectView(ui.View):
    def __init__(self, voice_bot, user, selected_category: str = None):
        super().__init__(timeout=120)
        self.voice_bot = voice_bot
        self.user = user
        self.selected_category = selected_category
        self.selected_roles: list[int] = []

        # always show category dropdown
        self.add_item(RoleCategoryDropdown(voice_bot, self))
        # if category chosen, show roles multiselect
        if selected_category:
            self.add_item(RoleMultiSelect(self, selected_category))
        # confirmation button
        self.add_item(ConfirmAddButton(self, voice_bot))


@bot.tree.command(name="vb_add_roles_menu", description="Add or adjust roles in a category via menu")
async def vb_add_roles_menu(interaction: discord.Interaction):
    voice_bot = await get_voice_bot()
    if not voice_bot.is_authorized(interaction.user):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    view = AddRoleSelectView(voice_bot, interaction.user)
    await interaction.response.send_message(
        "Add or adjust roles in a category:",
        view=view,
        ephemeral=True
    )


class ConfirmAddButton(ui.Button):
    def __init__(self, parent, voice_bot):
        super().__init__(label="✅ Confirmer", style=ButtonStyle.success)
        self.parent = parent
        self.voice_bot = voice_bot

    async def callback(self, interaction: Interaction):
        category = self.parent.selected_category
        role_ids = self.parent.selected_roles or []

        if not category:
            await interaction.response.send_message("❌ Sélectionnez d'abord une catégorie.", ephemeral=True)
            return

        if not category.endswith("_roles"):
            category += "_roles"

        existing = self.voice_bot.roles_config.get(category, [])
        to_add = [rid for rid in role_ids if rid not in existing]
        to_remove = [rid for rid in existing if rid not in role_ids]

        for rid in to_add:
            existing.append(rid)
        for rid in to_remove:
            existing.remove(rid)

        self.voice_bot.roles_config[category] = existing
        self.voice_bot.save_data()

        added = len(to_add)
        removed = len(to_remove)
        await interaction.response.send_message(
            f"✅ {added} rôle(s) ajouté(s), {removed} rôle(s) retiré(s) de `{category}`.",
            ephemeral=True
        )
        await log_to_webhook(
            f"{interaction.user.display_name} a ajouté {added} et retiré {removed} rôle(s) dans {category}"
        )


class TemplateSelect(ui.Select):
    def __init__(self, templates, view_ref):
        options = [SelectOption(label=name, value=name) for name in templates] + [
            SelectOption(label="🔧 Nouveau modèle", value="__new__")]
        super().__init__(placeholder="Choisir un modèle à configurer...", min_values=1, max_values=1, options=options)
        self.view_ref = view_ref

    async def callback(self, interaction):
        self.view_ref.selected_template = self.values[0]
        view = TemplateConfigView(self.view_ref.templates, interaction.user, selected_template=self.values[0])
        await interaction.response.edit_message(content="Modèle chargé :", view=view)


class TriggerChannelModal(ui.Modal, title="Définir les salons déclencheurs"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        self.input = ui.TextInput(label="Salon(s) déclencheur(s)", placeholder="123456789012345678, 987654321098765432",
                                  max_length=200)
        self.add_item(self.input)

    async def on_submit(self, interaction):
        ids = [int(cid.strip()) for cid in self.input.value.split(',') if cid.strip().isdigit()]
        self.view_ref.trigger_ids = ids
        await interaction.response.send_message(f"Salons déclencheurs mis à jour : {', '.join(map(str, ids))}",
                                                ephemeral=True)


class ChannelNameModal(ui.Modal, title="Définir le nom du salon"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        self.input = ui.TextInput(label="Nom du salon (template)", placeholder="Ex: 🔒 Bureau - {username}",
                                  default="Salon - {username}", max_length=100)
        self.add_item(self.input)

    async def on_submit(self, interaction):
        self.view_ref.channel_name = self.input.value
        await interaction.response.send_message(f"Nom du salon mis à jour : `{self.input.value}`", ephemeral=True)


class TriggerChannelButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="✏️ Définir les salons déclencheurs", style=discord.ButtonStyle.primary)
        self.view_ref = view_ref

    async def callback(self, interaction):
        await interaction.response.send_modal(TriggerChannelModal(self.view_ref))


class ChannelNameButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="✏️ Définir le nom du salon", style=discord.ButtonStyle.primary)
        self.view_ref = view_ref

    async def callback(self, interaction):
        await interaction.response.send_modal(ChannelNameModal(self.view_ref))


class TemplateNameModal(ui.Modal, title="Nom du modèle"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        self.input = ui.TextInput(
            label="Nom du modèle",
            placeholder="Ex: staff_only, help, invisible...",
            default=self.view_ref.template_name or "",
            max_length=100
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.template_name = self.input.value.strip()
        await interaction.response.send_message(f"✅ Nom du modèle défini : `{self.input.value}`", ephemeral=True)


class TemplateNameButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="✏️ Nom du modèle", style=discord.ButtonStyle.secondary)
        self.view_ref = view_ref

    async def callback(self, interaction):
        await interaction.response.send_modal(TemplateNameModal(self.view_ref))


class ChannelSelect(discord.ui.Select):
    def __init__(self, view_ref, guild: discord.Guild, selected_ids: list[int]):
        options = []
        for channel in guild.voice_channels:
            options.append(SelectOption(
                label=channel.name,
                value=str(channel.id),
                default=str(channel.id) in map(str, selected_ids)
            ))
        super().__init__(placeholder="Sélectionne les salons déclencheurs", options=options, min_values=0,
                         max_values=len(options))
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        self.view_ref.trigger_ids = [int(v) for v in self.values]
        await interaction.response.send_message(f"{len(self.values)} salon(s) sélectionné(s) comme déclencheurs.",
                                                ephemeral=True)


class PermissionSelect(ui.Select):
    def __init__(self, category, view_ref, existing_perms=None):
        perms = ["connect", "view_channel", "manage_channels", "manage_roles"]
        options = [SelectOption(label=perm, value=perm, default=(existing_perms or {}).get(perm, False)) for perm in
                   perms]
        super().__init__(placeholder=f"Permissions pour {category}...", options=options, min_values=0,
                         max_values=len(perms))
        self.category = category
        self.view_ref = view_ref

    async def callback(self, interaction):
        self.view_ref.perms[self.category] = self.values
        await interaction.response.send_message(f"Permissions définies pour `{self.category}`.", ephemeral=True)


# Ajoutez ceci après la définition de PermissionSelect
class PermissionsConfigView(ui.View):
    def __init__(self, template_view):
        super().__init__(timeout=600)
        self.template_view = template_view
        vb = bot.get_cog('VoiceBot')
        # Intégrer toutes les catégories de rôles dynamiquement
        for rc in vb.roles_config.keys():
            cat = rc.replace('_roles', '')
            self.template_view.perms.setdefault(cat, [])
        self.template_view.perms.setdefault('citizens', [])

        self.permission_keys = list(self.template_view.perms.keys())
        self.page = 0
        self.per_page = 2  # deux catégories par page pour respecter la limite de lignes
        self.refresh_page()

    def refresh_page(self):
        for child in list(self.children):
            self.remove_item(child)

        start = self.page * self.per_page
        page_keys = self.permission_keys[start:start + self.per_page]
        # Afficher label puis select sur lignes distinctes
        for idx, key in enumerate(page_keys):
            base_row = idx * 2
            # Label
            label_btn = ui.Button(label=f"{key}", style=ButtonStyle.secondary, disabled=True)
            label_btn.row = base_row
            self.add_item(label_btn)
            # Select full-width
            existing_list = self.template_view.perms.get(key, [])
            existing_map = {perm: True for perm in existing_list}
            select = PermissionSelect(key, self.template_view, existing_map)
            select.row = base_row + 1
            self.add_item(select)

        # Navigation et finish en dernière ligne (row 4)
        nav_row = self.per_page * 2
        if self.page > 0:
            prev_btn = ui.Button(label="◀️ Prev", style=ButtonStyle.secondary)

            async def prev_cb(interaction: Interaction):
                self.page -= 1
                self.refresh_page()
                await interaction.response.edit_message(view=self)

            prev_btn.callback = prev_cb
            prev_btn.row = nav_row
            self.add_item(prev_btn)

        if (self.page + 1) * self.per_page < len(self.permission_keys):
            next_btn = ui.Button(label="Next ▶️", style=ButtonStyle.secondary)

            async def next_cb(interaction: Interaction):
                self.page += 1
                self.refresh_page()
                await interaction.response.edit_message(view=self)

            next_btn.callback = next_cb
            next_btn.row = nav_row
            self.add_item(next_btn)

        finish_btn = ui.Button(label="✅ Finish", style=ButtonStyle.success)

        async def finish_cb(interaction: Interaction):
            await interaction.response.send_message("✅ Permissions enregistrées pour toutes les catégories.",
                                                    ephemeral=True)
            self.stop()

        finish_btn.callback = finish_cb
        finish_btn.row = nav_row
        self.add_item(finish_btn)


# Modifiez PermissionsButton pour ouvrir cette vue éphémère pour ouvrir cette vue éphémère
class PermissionsButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="✏️ Permissions", style=ButtonStyle.primary)
        self.view_ref = view_ref

    async def callback(self, interaction: Interaction):
        await interaction.response.send_message(
            "Configurer permissions pour toutes les catégories :",
            view=PermissionsConfigView(self.view_ref),
            ephemeral=True
        )


class SaveTemplateButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="💾 Sauvegarder", style=discord.ButtonStyle.success)
        self.view_ref = view_ref

    async def callback(self, interaction):
        voice_bot = await get_voice_bot()
        template_name = self.view_ref.template_name or self.view_ref.selected_template
        if not template_name or template_name == "__new__":
            template_name = f"template_{len(voice_bot.templates) + 1}"

        voice_bot.templates[template_name] = {
            "name": template_name,
            "trigger_channel_ids": self.view_ref.trigger_ids,
            "channel_name_template": self.view_ref.channel_name,
            "permissions": {
                key: {perm: True for perm in perms} for key, perms in self.view_ref.perms.items()
            }
        }
        voice_bot.save_templates()
        await interaction.response.send_message(f"✅ Modèle `{template_name}` sauvegardé.", ephemeral=True)
        await log_to_webhook(f"{self.view_ref.interaction_user.display_name} a sauvegardé le modèle `{template_name}`")


class PermissionsModal(ui.Modal, title="Configure Permissions (JSON)"):
    perms_input = ui.TextInput(
        label="Permissions JSON",
        style=discord.TextStyle.long,
        placeholder='{"role": ["connect","view_channel"], "member": ["connect"]}',
    )

    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        # Le TextInput étant déjà déclaré en class variable, il est automatiquement ajouté.
        # On se contente de modifier sa valeur par défaut ici :
        self.perms_input.default = json.dumps(self.view_ref.perms, ensure_ascii=False, indent=2)

    async def on_submit(self, interaction: Interaction):
        try:
            data = json.loads(self.perms_input.value)
            self.view_ref.perms = {k: v for k, v in data.items() if isinstance(v, list)}
            await interaction.response.send_message("✅ Permissions mises à jour.", ephemeral=True)
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ JSON invalide.", ephemeral=True)


class TemplateConfigView(ui.View):
    def __init__(self, templates, interaction_user, selected_template=None):
        super().__init__(timeout=600)
        self.templates = templates
        self.interaction_user = interaction_user
        self.selected_template = selected_template or next(iter(templates), None)
        self.template_data = templates.get(self.selected_template, {})
        self.template_name = self.template_data.get("name", self.selected_template)
        self.channel_name = self.template_data.get("channel_name_template", "Salon - {username}")
        self.trigger_ids = self.template_data.get("trigger_channel_ids", [])
        self.perms = {
            key: list(perm_dict.keys())
            for key, perm_dict in self.template_data.get("permissions", {}).items()
        }

        # row 0: template selector
        select_template = TemplateSelect(self.templates, self)
        select_template.row = 0
        self.add_item(select_template)

        # row 1: name and channel buttons
        name_btn = TemplateNameButton(self)
        name_btn.row = 1
        self.add_item(name_btn)
        name_channel = ChannelNameButton(self)
        name_channel.row = 1
        self.add_item(name_channel)

        # row 2: trigger channels select
        if interaction_user.guild:
            chan_select = ChannelSelect(self, interaction_user.guild, self.trigger_ids)
            chan_select.row = 2
            self.add_item(chan_select)

        # row 3: permissions modal button
        perm_btn = PermissionsButton(self)
        perm_btn.row = 3
        self.add_item(perm_btn)

        # row 4: action buttons (max row index is 4)
        if self.selected_template in self.templates:
            delete_btn = DeleteTemplateButton(self)
            delete_btn.row = 4
            self.add_item(delete_btn)
            duplicate_btn = DuplicateTemplateButton(self)
            duplicate_btn.row = 4
            self.add_item(duplicate_btn)
        reset_btn = ResetTemplateButton(self)
        reset_btn.row = 4
        self.add_item(reset_btn)
        save_btn = SaveTemplateButton(self)
        save_btn.row = 4
        self.add_item(save_btn)


@bot.tree.command(name="vb_config_template", description="Configurer un modèle vocal visuellement")
async def config_template_command(interaction: discord.Interaction):
    voice_bot = await get_voice_bot()
    if not voice_bot.is_authorized(interaction.user):
        await interaction.response.send_message("Permission refusée.", ephemeral=True)
        return

    view = TemplateConfigView(voice_bot.templates, interaction.user)
    await interaction.response.send_message("Configurer un modèle avec les composants ci-dessous :", view=view,
                                            ephemeral=True)


class PermissionPagePrevButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="◀️", style=ButtonStyle.secondary, row=4)
        self.view_ref = view_ref

    async def callback(self, interaction):
        if self.view_ref.permission_page > 0:
            self.view_ref.permission_page -= 1
            self.view_ref.refresh_permissions_page()
            await interaction.response.edit_message(view=self.view_ref)


class PermissionPageNextButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="▶️", style=ButtonStyle.secondary, row=4)
        self.view_ref = view_ref

    async def callback(self, interaction):
        max_page = (len(self.view_ref.permission_keys) - 1) // self.view_ref.per_page
        if self.view_ref.permission_page < max_page:
            self.view_ref.permission_page += 1
            self.view_ref.refresh_permissions_page()
            await interaction.response.edit_message(view=self.view_ref)


class DeleteTemplateButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="🗑️ Supprimer", style=ButtonStyle.danger)
        self.view_ref = view_ref

    async def callback(self, interaction):
        voice_bot = await get_voice_bot()
        if self.view_ref.selected_template in voice_bot.templates:
            del voice_bot.templates[self.view_ref.selected_template]
            voice_bot.save_templates()
            await interaction.response.edit_message(content=f"❌ Modèle supprimé.", view=None)
            await log_to_webhook(
                f"{interaction.user.display_name} a supprimé le modèle `{self.view_ref.selected_template}`")


class ResetTemplateButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="🔄 Réinitialiser", style=ButtonStyle.secondary)
        self.view_ref = view_ref

    async def callback(self, interaction):
        refreshed_view = TemplateConfigView(self.view_ref.templates, interaction.user,
                                            selected_template=self.view_ref.selected_template)
        await interaction.response.edit_message(content="🔄 Champs réinitialisés.", view=refreshed_view)


class DuplicateTemplateModal(ui.Modal, title="Nom du nouveau modèle"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        self.input = ui.TextInput(label="Nom du nouveau modèle", placeholder="Ex: template_clone")
        self.add_item(self.input)

    async def on_submit(self, interaction):
        new_name = self.input.value.strip()
        voice_bot = await get_voice_bot()
        if new_name in voice_bot.templates:
            await interaction.response.send_message("❌ Ce nom de modèle existe déjà.", ephemeral=True)
            return

        voice_bot.templates[new_name] = {
            "name": new_name,
            "trigger_channel_ids": self.view_ref.trigger_ids,
            "channel_name_template": self.view_ref.channel_name,
            "permissions": {
                key: {perm: True for perm in perms} for key, perms in self.view_ref.perms.items()
            }
        }
        voice_bot.save_templates()
        await interaction.response.send_message(f"✅ Modèle `{new_name}` dupliqué avec succès.", ephemeral=True)
        await log_to_webhook(f"{interaction.user.display_name} a dupliqué le modèle en `{new_name}`")


class DuplicateTemplateButton(ui.Button):
    def __init__(self, view_ref):
        super().__init__(label="📋 Dupliquer", style=ButtonStyle.secondary)
        self.view_ref = view_ref

    async def callback(self, interaction):
        await interaction.response.send_modal(DuplicateTemplateModal(self.view_ref))


@bot.event
async def on_ready():
    print(f"{bot.user} connecté à : {[f'{g.name} (ID: {g.id})' for g in bot.guilds]}")
    await bot.tree.sync()
    await get_voice_bot()
    await log_to_webhook(f"Bot lancé sur {len(bot.guilds)} serveur(s).")


bot.run(TOKEN)
