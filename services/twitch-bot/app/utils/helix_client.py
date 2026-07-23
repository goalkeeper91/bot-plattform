"""
Thin wrapper around the Twitch Helix REST API for the built-in chat commands
(!uptime, !title, !game, !followage, !shoutout). Calls the REST endpoints
directly rather than relying on twitchio's internal object model, since the
exact Helix routes are stable/documented while library-internal method names
can change between versions.
"""
import logging

import aiohttp

LOGGER = logging.getLogger("HelixClient")

HELIX_BASE = "https://api.twitch.tv/helix"


class HelixInsufficientScopeError(Exception):
    """Raised when Twitch rejects a call because the stored token is missing
    a required OAuth scope (or is otherwise invalid) - the caller should ask
    the broadcaster to reconnect Twitch to mint a token with the new scope."""


class HelixClient:
    def __init__(self, client_id: str):
        self.client_id = client_id

    def _headers(self, token: str) -> dict:
        return {
            "Client-Id": self.client_id,
            "Authorization": f"Bearer {token}",
        }

    async def _get(self, path: str, token: str, params: dict | None = None) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    f"{HELIX_BASE}{path}",
                    headers=self._headers(token),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status == 401:
                    raise HelixInsufficientScopeError(await response.text())
                response.raise_for_status()
                return await response.json()

    async def _patch(self, path: str, token: str, params: dict, json_body: dict) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                    f"{HELIX_BASE}{path}",
                    headers=self._headers(token),
                    params=params,
                    json=json_body,
                    timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status == 401:
                    raise HelixInsufficientScopeError(await response.text())
                response.raise_for_status()

    async def get_streams(self, user_id: str, token: str) -> dict | None:
        data = await self._get("/streams", token, params={"user_id": user_id})
        items = data.get("data", [])
        return items[0] if items else None

    async def get_channel_info(self, broadcaster_id: str, token: str) -> dict | None:
        data = await self._get("/channels", token, params={"broadcaster_id": broadcaster_id})
        items = data.get("data", [])
        return items[0] if items else None

    async def modify_channel_info(
            self, broadcaster_id: str, token: str, *, title: str | None = None, game_id: str | None = None
    ) -> None:
        body: dict = {}
        if title is not None:
            body["title"] = title
        if game_id is not None:
            body["game_id"] = game_id
        await self._patch("/channels", token, params={"broadcaster_id": broadcaster_id}, json_body=body)

    async def get_games(self, name: str, token: str) -> dict | None:
        data = await self._get("/games", token, params={"name": name})
        items = data.get("data", [])
        return items[0] if items else None

    async def get_users(self, login: str, token: str) -> dict | None:
        data = await self._get("/users", token, params={"login": login})
        items = data.get("data", [])
        return items[0] if items else None

    async def get_channel_follower(self, broadcaster_id: str, token: str, user_id: str) -> dict | None:
        data = await self._get(
            "/channels/followers",
            token,
            params={"broadcaster_id": broadcaster_id, "moderator_id": broadcaster_id, "user_id": user_id},
        )
        items = data.get("data", [])
        return items[0] if items else None
