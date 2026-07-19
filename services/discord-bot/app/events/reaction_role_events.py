import logging

logger = logging.getLogger('reaction_role_events')

# In-memory cache of reaction-role mappings per guild, pushed via Redis
# RELOAD_CONFIGS (config_type: "reaction_roles") - see
# listeners/redis_listener.py.
# guild_id -> list[{"channel_id": int, "message_id": int, "emoji": str, "role_id": int, "removable": bool}]
reaction_role_configs = {}


def set_reaction_roles(guild_id, roles):
    """Called by listeners/redis_listener.py's reload_configs(). Full
    replace, mirroring join_to_create's semantics - a row removed on the
    dashboard must also disappear here, not linger until the next process
    restart. An empty list clears all reaction-roles for the guild."""
    reaction_role_configs[guild_id] = [
        {
            "channel_id": int(r["channel_id"]),
            "message_id": int(r["message_id"]),
            "emoji": r["emoji"],
            "role_id": int(r["role_id"]),
            "removable": bool(r.get("removable", True)),
        }
        for r in roles
    ]
    logger.info(f"✅ {len(reaction_role_configs[guild_id])} Reaction-Role(s) geladen für Guild {guild_id}")


def _emoji_matches(payload_emoji, configured_emoji: str) -> bool:
    """Unicode emoji (the common case, e.g. "✅") are matched by their
    literal character via str(payload_emoji), unchanged from before. Custom
    server emojis are hard to type correctly as <:name:id> by hand (exact
    name, animated "a:" prefix) and the name can change if someone renames
    the emoji later, so also accept just the numeric emoji ID here (visible
    via Discord's right-click "ID kopieren" on the emoji, or by typing
    \\:emojiname: in a message) and match on payload_emoji.id instead."""
    configured_emoji = configured_emoji.strip()
    if configured_emoji.isdigit() and payload_emoji.id is not None:
        return str(payload_emoji.id) == configured_emoji
    return str(payload_emoji) == configured_emoji


def _find_matching_config(guild_id, channel_id, message_id, payload_emoji):
    for config in reaction_role_configs.get(guild_id, []):
        if config["channel_id"] != channel_id or config["message_id"] != message_id:
            continue
        if _emoji_matches(payload_emoji, config["emoji"]):
            return config
    return None


def setup_reaction_role_events(bot):
    """Assigns/revokes a role when a member reacts/unreacts with a
    configured emoji on a configured message (e.g. accepting the rules -
    add-only, removable=False - or picking a game-interest role -
    removable=True). ONE unified on_raw_reaction_add and ONE
    on_raw_reaction_remove handler covering every mapping for every guild:
    @bot.event replaces the whole attribute, so a second competing
    registration elsewhere in the codebase would silently make this one
    dead code - this is the only place either event is registered. Uses
    raw events since the target message may not be in the bot's message
    cache."""

    @bot.event
    async def on_raw_reaction_add(payload):
        if payload.member is None or payload.member.bot:
            return

        config = _find_matching_config(payload.guild_id, payload.channel_id, payload.message_id, payload.emoji)
        if not config:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(config["role_id"])
        if not role:
            logger.error(f"❌ Rolle {config['role_id']} nicht gefunden in Guild {payload.guild_id}")
            return

        try:
            await payload.member.add_roles(role, reason="Reaction Role")
            logger.info(f"✅ Rolle '{role.name}' vergeben an {payload.member.name}")
        except Exception as e:
            logger.error(f"❌ Rollenvergabe fehlgeschlagen: {e}")

    @bot.event
    async def on_raw_reaction_remove(payload):
        # Unlike reaction_add, discord.py's RawReactionActionEvent for
        # MESSAGE_REACTION_REMOVE never carries `member` - the gateway
        # payload omits it. Resolve by hand: cache lookup first (cheap,
        # populated via the `members` intent - already enabled in
        # discord_bot.py, but also double-check it's toggled ON in the
        # Discord Developer Portal's Privileged Gateway Intents, since that
        # can't be verified from code), falling back to an API fetch for a
        # member who isn't cached.
        config = _find_matching_config(payload.guild_id, payload.channel_id, payload.message_id, payload.emoji)
        if not config or not config["removable"]:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception as e:
                logger.error(f"❌ Mitglied {payload.user_id} nicht auflösbar: {e}")
                return
        if member.bot:
            return

        role = guild.get_role(config["role_id"])
        if not role:
            logger.error(f"❌ Rolle {config['role_id']} nicht gefunden in Guild {payload.guild_id}")
            return

        try:
            await member.remove_roles(role, reason="Reaction entfernt")
            logger.info(f"✅ Rolle '{role.name}' entfernt von {member.name}")
        except Exception as e:
            logger.error(f"❌ Rollenentzug fehlgeschlagen: {e}")
