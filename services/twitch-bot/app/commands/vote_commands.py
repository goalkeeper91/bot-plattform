"""
Vote Commands Component
Handles voting system for community game development
"""

import logging
import aiohttp
from twitchio.ext import commands

LOGGER = logging.getLogger("VoteCommands")


class VoteCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot
        self.n8n_base_url = "http://n8n:5678/webhook"
        self.active_session_id = None

        # Vote Templates für Quick-Creation
        self.vote_templates = {
            "hero": {
                "category": "hero_class",
                "title": "Choose the first character class!",
                "options": ["Warrior", "Mage", "Rogue", "Ranger"]
            },
            "map": {
                "category": "map_biome",
                "title": "Which biome for the first level?",
                "options": ["Forest", "Desert", "Snow", "Cave"]
            },
            "genre": {
                "category": "game_genre",
                "title": "What type of game should we make?",
                "options": ["RPG", "Platformer", "Roguelike", "Metroidvania"]
            },
            "feature": {
                "category": "game_feature",
                "title": "What feature should we build next?",
                "options": ["Inventory", "Boss Fight", "Multiplayer", "Crafting"]
            }
        }

    # ==========================================================
    # Helper: Check if user is mod or broadcaster
    # ==========================================================

    def is_mod_or_broadcaster(self, ctx: commands.Context) -> bool:
        return ctx.chatter.moderator or ctx.chatter.broadcaster

    # ==========================================================
    # n8n API Calls
    # ==========================================================

    async def create_vote_api(
            self,
            user_id: str,
            category: str,
            title: str,
            options: list[str],
            description: str = ""
    ) -> dict:
        """Call n8n to create a vote session"""
        # Build options array with keys
        formatted_options = [
            {
                "key": str(i + 1),
                "label": option.strip(),
                "data": {}
            }
            for i, option in enumerate(options)
        ]

        payload = {
            "user_id": user_id,
            "category": category,
            "title": title,
            "description": description,
            "options": formatted_options
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{self.n8n_base_url}/vote/create",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.active_session_id = data.get("session_id")
                        LOGGER.info("✅ Vote created: session_id=%s", self.active_session_id)
                        return data
                    else:
                        error_text = await response.text()
                        LOGGER.error("❌ n8n error: %s", error_text)
                        return {"success": False, "error": "n8n request failed"}
        except asyncio.TimeoutError:
            LOGGER.error("❌ n8n timeout")
            return {"success": False, "error": "n8n timeout"}
        except Exception as e:
            LOGGER.error("❌ Vote creation error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def cast_vote_api(
            self,
            session_id: int,
            user_id: str,
            username: str,
            option: str
    ) -> dict:
        """Call n8n to cast a vote"""
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "username": username,
            "option": option
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{self.n8n_base_url}/vote/cast",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return await response.json()
        except Exception as e:
            LOGGER.error("❌ Vote cast error: %s", e)
            return {"success": False, "error": str(e)}

    async def close_vote_api(self, session_id: int) -> dict:
        """Call n8n to close vote and get results"""
        payload = {"session_id": session_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{self.n8n_base_url}/vote/close",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    result = await response.json()
                    self.active_session_id = None
                    LOGGER.info("✅ Vote closed: winner=%s", result.get("winner"))
                    return result
        except Exception as e:
            LOGGER.error("❌ Vote close error: %s", e)
            return {"success": False, "error": str(e)}

    # ==========================================================
    # Commands
    # ==========================================================

    @commands.command(name="vote")
    async def vote_main(self, ctx: commands.Context):
        """
        Main vote command router

        !vote create <category> <options>   - Create vote (mod only)
        !vote <number>                       - Cast vote
        !vote close                          - Close vote (mod only)
        !vote results                        - Show results
        !vote hero/map/genre/feature         - Quick templates (mod only)
        """
        parts = ctx.message.content.split()

        if len(parts) < 2:
            await ctx.send("Usage: !vote <create|close|results|1-9|hero|map|genre>")
            return

        subcommand = parts[1].lower()

        # Route to appropriate handler
        if subcommand == "create":
            await self.vote_create(ctx, *parts[2:])
        elif subcommand == "close":
            await self.vote_close(ctx)
        elif subcommand == "results":
            await self.vote_results(ctx)
        elif subcommand.isdigit():
            # !vote 1, !vote 2, etc.
            await self.vote_cast(ctx, subcommand)
        elif subcommand in self.vote_templates:
            # !vote hero, !vote map, etc.
            await self.vote_quick(ctx, subcommand)
        else:
            await ctx.send(f"Unknown command: {subcommand}")

    async def vote_create(self, ctx: commands.Context, *args):
        """
        !vote create <category> <option1,option2,option3>

        Example:
        !vote create hero "Warrior,Mage,Rogue"
        """
        if not self.is_mod_or_broadcaster(ctx):
            await ctx.send("❌ Only mods can create votes!")
            return

        if len(args) < 2:
            await ctx.send("Usage: !vote create <category> <options>")
            return

        category = args[0]

        # Parse options (comma-separated, may have quotes)
        options_str = " ".join(args[1:]).strip('"')
        option_list = [opt.strip() for opt in options_str.split(',')]

        if len(option_list) < 2:
            await ctx.send("❌ Need at least 2 options!")
            return

        # Create title from category
        title = f"Vote for {category}!"

        # Call n8n
        result = await self.create_vote_api(
            user_id=str(ctx.channel.id),
            category=category,
            title=title,
            options=option_list
        )

        if result.get("success"):
            await ctx.send(f"✅ Vote created! Use !vote 1-{len(option_list)} to vote!")

            # Track in stats
            if hasattr(self.bot, 'stats') and self.bot.stats:
                await self.bot.stats.track_command(
                    command_name="vote_create",
                    channel_id=str(ctx.channel.id),
                    success=True
                )
        else:
            await ctx.send("❌ Failed to create vote. Check if n8n is running.")
            LOGGER.error("Vote creation failed: %s", result.get("error"))

    async def vote_cast(self, ctx: commands.Context, option: str):
        """
        !vote <number>

        Example:
        !vote 1
        """
        if not self.active_session_id:
            # Silent fail - no spam
            return

        # Validate option
        if not option.isdigit() or int(option) < 1 or int(option) > 9:
            return

        # Cast vote
        result = await self.cast_vote_api(
            session_id=self.active_session_id,
            user_id=str(ctx.author.id),
            username=ctx.author.name,
            option=option
        )

        # Silent success to avoid spam
        # Only show errors if it's NOT "already voted"
        if not result.get("success"):
            message = result.get("message", "")
            if "already" not in message.lower():
                await ctx.send(f"@{ctx.author.name} {message}")

    async def vote_close(self, ctx: commands.Context):
        """
        !vote close

        Only mods can close votes
        """
        if not self.is_mod_or_broadcaster(ctx):
            await ctx.send("❌ Only mods can close votes!")
            return

        if not self.active_session_id:
            await ctx.send("❌ No active vote!")
            return

        # Close vote
        result = await self.close_vote_api(self.active_session_id)

        if result.get("success"):
            winner = result.get("winner")
            await ctx.send(f"🏆 Vote closed! Winner: {winner}")

            # Track in stats
            if hasattr(self.bot, 'stats') and self.bot.stats:
                await self.bot.stats.track_command(
                    command_name="vote_close",
                    channel_id=str(ctx.channel.id),
                    success=True
                )
        else:
            await ctx.send("❌ Failed to close vote")

    async def vote_results(self, ctx: commands.Context):
        """
        !vote results

        Show current vote results
        """
        if not self.active_session_id:
            await ctx.send("❌ No active vote!")
            return

        # Get results from backend
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"http://backend-go:8080/api/votes/sessions/{self.active_session_id}/results",
                        timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get("results", [])

                        if results:
                            message = "📊 Current Results: "
                            result_parts = [
                                f"{r['option']}: {r['count']} ({r['percentage']}%)"
                                for r in results[:3]  # Top 3 only
                            ]
                            message += " | ".join(result_parts)
                            await ctx.send(message)
                        else:
                            await ctx.send("📊 No votes yet!")
                    else:
                        await ctx.send("❌ Couldn't fetch results")
        except Exception as e:
            LOGGER.error("❌ Results fetch error: %s", e)
            await ctx.send("❌ Error fetching results")

    async def vote_quick(self, ctx: commands.Context, template_name: str):
        """
        !vote <template>

        Quick vote creation from templates

        Example:
        !vote hero
        !vote map
        !vote genre
        """
        if not self.is_mod_or_broadcaster(ctx):
            await ctx.send("❌ Only mods can create votes!")
            return

        template = self.vote_templates.get(template_name)
        if not template:
            available = ", ".join(self.vote_templates.keys())
            await ctx.send(f"❌ Unknown template. Available: {available}")
            return

        # Create vote from template
        result = await self.create_vote_api(
            user_id=str(ctx.channel.id),
            category=template["category"],
            title=template["title"],
            options=template["options"]
        )

        if result.get("success"):
            await ctx.send(f"✅ {template['title']}")

            # Track in stats
            if hasattr(self.bot, 'stats') and self.bot.stats:
                await self.bot.stats.track_command(
                    command_name=f"vote_template_{template_name}",
                    channel_id=str(ctx.channel.id),
                    success=True
                )
        else:
            await ctx.send("❌ Failed to create vote")