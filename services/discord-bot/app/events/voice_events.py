import logging
import discord
import asyncio

logger = logging.getLogger('voice_events')

# In-memory storage for Join-to-Create configs
# In production, this would be loaded from database/backend
jtc_configs = {}
created_channels = {}  # Track which channels were auto-created


def setup_voice_events(bot):
    """Setup voice state event handlers for Join-to-Create"""

    @bot.event
    async def on_voice_state_update(member, before, after):
        """Called when user joins/leaves/moves voice channels"""

        # User joined a channel
        if after.channel and after.channel != before.channel:
            await handle_voice_join(member, after.channel)

        # User left a channel
        if before.channel and before.channel != after.channel:
            await handle_voice_leave(member, before.channel)

    async def handle_voice_join(member, channel):
        """Handle user joining a voice channel"""
        guild_id = channel.guild.id
        channel_id = channel.id

        # Check if this is a Join-to-Create trigger channel
        config = get_jtc_config(guild_id, channel_id)
        if not config:
            return

        if not config.get('enabled', True):
            logger.debug(f"Join-to-Create disabled for channel {channel_id}")
            return

        logger.info(f"👤 {member.name} joined JTC trigger channel in {channel.guild.name}")

        try:
            # Create new voice channel
            category = channel.guild.get_channel(config['category_id'])
            if not category:
                logger.error(f"Category {config['category_id']} not found")
                return

            # Generate channel name
            channel_name = f"{config['channel_name_prefix']} {member.display_name}"

            # Set permissions
            overwrites = {
                channel.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                member: discord.PermissionOverwrite(
                    connect=True,
                    manage_channels=True,
                    move_members=True,
                    mute_members=True,
                    deafen_members=True
                )
            }

            # Private channel - only creator can see
            if config.get('private_channel', False):
                overwrites[channel.guild.default_role] = discord.PermissionOverwrite(
                    connect=False,
                    view_channel=False
                )

            # Create channel
            new_channel = await category.create_voice_channel(
                name=channel_name,
                overwrites=overwrites,
                user_limit=config.get('user_limit', 0)
            )

            # Track that this channel was auto-created
            created_channels[new_channel.id] = {
                'creator': member.id,
                'guild_id': guild_id,
                'created_at': discord.utils.utcnow()
            }

            # Move user to new channel
            await member.move_to(new_channel)

            logger.info(f"✅ Created voice channel: {channel_name} ({new_channel.id})")

        except discord.Forbidden:
            logger.error(f"Missing permissions to create voice channel in {channel.guild.name}")
        except Exception as e:
            logger.error(f"Failed to create voice channel: {e}")

    async def handle_voice_leave(member, channel):
        """Handle user leaving a voice channel"""

        # Check if this was an auto-created channel
        if channel.id not in created_channels:
            return

        # Wait a bit to make sure channel is actually empty
        await asyncio.sleep(2)

        # Check if channel is now empty
        if len(channel.members) == 0:
            try:
                channel_info = created_channels[channel.id]
                await channel.delete(reason="Auto-created channel is now empty")
                del created_channels[channel.id]

                logger.info(f"🗑️ Deleted empty auto-created channel: {channel.name}")

            except discord.Forbidden:
                logger.error(f"Missing permissions to delete channel {channel.name}")
            except Exception as e:
                logger.error(f"Failed to delete channel: {e}")


def get_jtc_config(guild_id, channel_id):
    """Get Join-to-Create config for a channel"""
    # In production, this would query backend/database
    # For now, use in-memory storage
    return jtc_configs.get(guild_id, {}).get(channel_id)


def load_jtc_config(guild_id, channel_id, config):
    """Load Join-to-Create config"""
    if guild_id not in jtc_configs:
        jtc_configs[guild_id] = {}
    jtc_configs[guild_id][channel_id] = config
    logger.info(f"Loaded JTC config for guild {guild_id}, channel {channel_id}")


def unload_jtc_config(guild_id, channel_id):
    """Unload Join-to-Create config"""
    if guild_id in jtc_configs and channel_id in jtc_configs[guild_id]:
        del jtc_configs[guild_id][channel_id]
        logger.info(f"Unloaded JTC config for guild {guild_id}, channel {channel_id}")