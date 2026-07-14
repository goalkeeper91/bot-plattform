import logging

logger = logging.getLogger('rule_role_events')

# In-memory cache of reaction-role config per guild, pushed via Redis
# RELOAD_CONFIGS (config_type: "rule_role") - see listeners/redis_listener.py.
# guild_id -> {"channel_id": int, "message_id": int, "emoji": str, "role_id": int}
rule_role_configs = {}


def set_rule_role_config(guild_id, config):
    """Called by listeners/redis_listener.py's reload_configs(). config=None
    (or falsy) clears/disables the reaction-role for that guild."""
    if config:
        rule_role_configs[guild_id] = {
            "channel_id": int(config["channel_id"]),
            "message_id": int(config["message_id"]),
            "emoji": config["emoji"],
            "role_id": int(config["role_id"]),
        }
        logger.info(f"✅ Rule-role Config gesetzt für Guild {guild_id}")
    else:
        rule_role_configs.pop(guild_id, None)
        logger.info(f"🛑 Rule-role Config entfernt für Guild {guild_id}")


def setup_rule_role_events(bot):
    """Assigns a configured role when a member reacts with the configured
    emoji on the configured message in the configured channel (e.g.
    accepting the server rules). Uses the raw event since the target message
    may not be in the bot's message cache."""

    @bot.event
    async def on_raw_reaction_add(payload):
        if payload.member is None or payload.member.bot:
            return

        config = rule_role_configs.get(payload.guild_id)
        if not config:
            return
        if payload.channel_id != config["channel_id"] or payload.message_id != config["message_id"]:
            return
        if str(payload.emoji) != config["emoji"]:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(config["role_id"])
        if not role:
            logger.error(f"❌ Rolle {config['role_id']} nicht gefunden in Guild {payload.guild_id}")
            return

        try:
            await payload.member.add_roles(role, reason="Regeln akzeptiert")
            logger.info(f"✅ Rolle '{role.name}' vergeben an {payload.member.name}")
        except Exception as e:
            logger.error(f"❌ Rollenvergabe fehlgeschlagen: {e}")
