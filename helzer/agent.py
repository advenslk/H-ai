from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import discord

from .context import actor, discord_context, scope_for
from .gemini import GeminiProvider, GeminiQuotaError
from .emoji_tools import attachment_parts, emoji_tool_specs, execute_emoji_tool
from .image_tools import execute_image_tool, image_tool_specs
from .memory import MemoryStore
from .prompts import build_system_prompt
from .tools import HIGH_RISK, execute, tool_specs
from .triggers import should_respond, strip_trigger

log = logging.getLogger("helzer.agent")

MUTATING_TOOLS = {
    "send_message", "send_dm", "timeout_member", "ban_member", "kick_member", "unban_member",
    "add_role", "remove_role", "create_role", "create_channel", "delete_channel", "rename_channel",
    "lock_channel", "unlock_channel", "set_slowmode", "purge_messages", "assign_role_all", "add_custom_emoji",
}
TOOL_HINTS = ("lock", "unlock", "channel", "dm", "direct message", "message", "send", "role", "timeout", "kick", "ban", "unban", "purge", "delete", "remove", "add", "create", "rename", "slowmode", "slow mode", "server", "member", "permission", "permissions", "emoji", "emojis", "custom emoji", "custom emojis", "sticker", "generate image", "generate a", "create an image", "create image", "design", "logo", "poster", "banner", "thumbnail", "graphic")


class ConfirmationView(discord.ui.View):
    def __init__(self, agent, key: str, title: str):
        super().__init__(timeout=90)
        self.agent, self.key, self.title = agent, key, title

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        pending = self.agent.pending.get(self.key)
        if not pending:
            await interaction.followup.send("That confirmation expired.", ephemeral=True); return
        if interaction.user.id != pending["requester_id"]:
            await interaction.followup.send("Only the requester can confirm this action.", ephemeral=True); return
        self.agent.pending.pop(self.key, None)
        await interaction.followup.send(self.agent.result_text(await self.agent.execute_pending(pending)), ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = self.agent.pending.get(self.key)
        if pending and interaction.user.id != pending["requester_id"]:
            await interaction.response.send_message("Only the requester can cancel this action.", ephemeral=True); return
        self.agent.pending.pop(self.key, None)
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()


class HelzerAgent:
    def __init__(self, bot, settings):
        self.bot = bot
        self.settings = settings
        self.gemini = GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_thinking_level)
        self.fast_gemini = GeminiProvider(settings.gemini_api_key, settings.gemini_fast_model, settings.gemini_fast_thinking_level)
        self.memory = MemoryStore(settings.database_path, settings.max_memory_messages)
        self.pending: dict[str, dict[str, Any]] = {}
        self._rate_lock = asyncio.Lock()
        self._last_request: dict[int, float] = {}
        self._tool_specs = tool_specs() + emoji_tool_specs() + image_tool_specs()
        self._generated_files: dict[int, list[dict[str, str]]] = {}

    def authorized(self, member: discord.Member) -> bool:
        if member.id in self.settings.owner_ids or member.guild_permissions.administrator: return True
        return bool({r.id for r in member.roles} & self.settings.allowed_role_ids)

    @staticmethod
    def _needs_tools(prompt: str) -> bool:
        return any(hint in prompt.casefold() for hint in TOOL_HINTS)

    @staticmethod
    def _normalize_tool_args(message, name: str, args: dict[str, Any]) -> dict[str, Any]:
        args = dict(args)
        if name in {"lock_channel", "unlock_channel", "set_slowmode", "purge_messages"}:
            channel = getattr(message, "channel", None)
            if channel is not None:
                current_id = getattr(channel, "id", None)
                if not args.get("channel_id") or str(args["channel_id"]) != str(current_id):
                    args["channel_id"] = str(current_id)
        return args

    async def _generate(self, provider, contents, system, tools):
        try:
            return await provider.generate(contents, system, tools)
        except GeminiQuotaError:
            if provider is self.fast_gemini and self.gemini is not self.fast_gemini:
                log.warning("Fast Gemini quota exhausted; trying primary model=%s", self.gemini.model)
                return await self.gemini.generate(contents, system, tools)
            raise

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not should_respond(message, self.bot) or (self.settings.dm_only and message.guild is not None): return
        prompt = strip_trigger(message.content, self.bot).strip() or "Talk naturally with me and help me with what I need."
        async with self._rate_lock:
            now = time.monotonic()
            if now - self._last_request.get(message.author.id, 0) < 1.0: return
            self._last_request[message.author.id] = now
        started = time.perf_counter()
        async with message.channel.typing():
            try: result = await self.respond(message, prompt)
            except GeminiQuotaError:
                log.exception("All configured Gemini models are out of quota")
                result = "Gemini quota is currently exhausted. Please try again after the quota resets or configure another Gemini project/API key."
            except Exception:
                log.exception("Message processing failed: user=%s guild=%s channel=%s prompt=%r", getattr(message.author, "id", None), getattr(getattr(message, "guild", None), "id", None), getattr(getattr(message, "channel", None), "id", None), prompt)
                result = "Gemini is temporarily unavailable. Please try again in a moment."
        log.info("Request completed: user=%s total_latency=%.2fs", getattr(message.author, "id", None), time.perf_counter() - started)
        if isinstance(result, discord.ui.View):
            await message.reply("This action changes the server or sends a message. Confirm it below.", view=result, mention_author=False)
        else:
            files = self._generated_files.pop(getattr(message, "id", 0), [])
            if files:
                discord_files = [discord.File(item["path"], filename=item["filename"]) for item in files]
                await message.reply(result, files=discord_files, mention_author=False)
                for item in files:
                    try:
                        os.remove(item["path"])
                    except OSError:
                        pass
            else:
                await self._send_text(message, result)

    async def _send_text(self, message, text: str):
        if len(text) <= 1900: await message.reply(text, mention_author=False); return
        for start in range(0, len(text), 1900): await message.reply(text[start:start + 1900], mention_author=False)

    async def respond(self, message, prompt: str):
        user = actor(message); requester_id = getattr(user, "id", 0); scope = scope_for(message)
        history = await self.memory.recent(scope)
        contents: list[Any] = [await discord_to_gemini_content(role, content) for role, content in history[-8:]]
        contents.append(await discord_to_gemini_content("user", discord_context(message) + "\nUser request: " + prompt, getattr(message, "attachments", None)))
        guild = getattr(message, "guild", None)
        needs_tools = self._needs_tools(prompt)
        tools = self._tool_specs if needs_tools else []
        provider = self.fast_gemini
        system = build_system_prompt(self.settings.timezone, guild.name if guild else None)
        for _ in range(self.settings.max_tool_rounds):
            response = await self._generate(provider, contents, system, tools)
            calls = provider.function_calls(response)
            if not calls:
                answer = provider.text(response) or "I’m here. Tell me what you need."
                await self.memory.add(scope, requester_id, "user", prompt, time.time())
                await self.memory.add(scope, requester_id, "assistant", answer, time.time())
                return answer
            contents.append(response.candidates[0].content)
            for call in calls:
                name = call.name; args = self._normalize_tool_args(message, name, dict(call.args or {}))
                if name in MUTATING_TOOLS and (not guild or not isinstance(user, discord.Member) or not self.authorized(user)):
                    contents.append(provider.function_result(name, {"ok": False, "error": "Requester is not authorized for server actions."})); continue
                if name in HIGH_RISK:
                    key = f"{getattr(message, 'id', requester_id)}:{name}:{time.time_ns()}"
                    self.pending[key] = {"message": message, "requester_id": requester_id, "name": name, "args": args}
                    return ConfirmationView(self, key, f"{name}: {', '.join(f'{k}={v}' for k, v in args.items())}")
                try:
                    if name == "add_custom_emoji":
                        result = await execute_emoji_tool(message, args)
                    elif name == "generate_image":
                        result = await execute_image_tool(message, args, self.gemini.client)
                        if result.get("ok"):
                            self._generated_files.setdefault(getattr(message, "id", 0), []).append({"path": result["path"], "filename": result["filename"]})
                    else:
                        result = await execute(message, name, args, self.bot)
                except Exception as exc: result = {"ok": False, "error": str(exc)}
                contents.append(provider.function_result(name, result))
        return "I stopped the action chain because it reached the safety limit."

    async def execute_pending(self, pending: dict[str, Any]):
        try:
            if pending["name"] == "add_custom_emoji":
                return await execute_emoji_tool(pending["message"], pending["args"])
            return await execute(pending["message"], pending["name"], pending["args"], self.bot)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def result_text(result: dict[str, Any]) -> str:
        return f"Done. `{result.get('action', 'action completed')}` completed successfully." if result.get("ok") else f"I couldn't complete that: {result.get('error', 'unknown error')}"


async def discord_to_gemini_content(role: str, text: str, attachments=None):
    from google.genai import types
    parts = [types.Part.from_text(text=text)]
    if role == "user" and attachments:
        parts.extend(await attachment_parts(attachments))
    return types.Content(role="user" if role == "user" else "model", parts=parts)
