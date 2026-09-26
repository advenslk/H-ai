from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
import discord

HIGH_RISK = {"timeout_member", "ban_member", "kick_member", "unban_member", "delete_channel", "purge_messages", "create_channel", "lock_channel", "unlock_channel", "assign_role_all", "remove_custom_emojis"}


def tool_specs() -> list[dict[str, Any]]:
    def fn(name, description, properties, required=()):
        parameters = {"type": "object", "properties": properties}
        if required: parameters["required"] = list(required)
        return {"type": "function", "name": name, "description": description, "parameters": parameters}
    discord_id = {"type": "string", "description": "Discord snowflake ID as an exact decimal string. Never round or convert it to a floating-point number."}
    return [
        fn("server_info", "Get useful information about the current Discord server.", {}),
        fn("list_custom_emojis", "List all custom emojis currently in the current Discord server.", {}),
        fn("remove_custom_emojis", "Delete custom emojis from the current Discord server. Use all=true for every emoji, or names/emoji_ids for selected emojis.", {"all": {"type": "boolean"}, "names": {"type": "array", "items": {"type": "string"}}, "emoji_ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}),
        fn("member_info", "Get information about a server member by exact Discord user ID.", {"user_id": discord_id}, ["user_id"]),
        fn("send_message", "Send a message to a Discord channel by exact Discord channel ID.", {"channel_id": discord_id, "content": {"type": "string"}}, ["channel_id", "content"]),
        fn("send_dm", "Send a direct message to a Discord user by exact Discord user ID.", {"user_id": discord_id, "content": {"type": "string"}}, ["user_id", "content"]),
        fn("timeout_member", "Timeout a member by exact Discord user ID for a number of minutes.", {"user_id": discord_id, "minutes": {"type": "integer"}, "reason": {"type": "string"}}, ["user_id", "minutes"]),
        fn("ban_member", "Ban a member by exact Discord user ID.", {"user_id": discord_id, "reason": {"type": "string"}}, ["user_id"]),
        fn("kick_member", "Kick a member by exact Discord user ID.", {"user_id": discord_id, "reason": {"type": "string"}}, ["user_id"]),
        fn("unban_member", "Unban a user by exact Discord user ID.", {"user_id": discord_id, "reason": {"type": "string"}}, ["user_id"]),
        fn("add_role", "Add a role to a member by exact Discord IDs.", {"user_id": discord_id, "role_id": discord_id}, ["user_id", "role_id"]),
        fn("remove_role", "Remove a role from a member by exact Discord IDs.", {"user_id": discord_id, "role_id": discord_id}, ["user_id", "role_id"]),
        fn("assign_role_all", "Assign a role to every member in the current server, including bots. Existing role holders are skipped. This can take time because Discord rate-limits member role changes.", {"role_id": discord_id, "reason": {"type": "string"}}, ["role_id"]),
        fn("create_role", "Create a server role.", {"name": {"type": "string"}, "reason": {"type": "string"}}, ["name"]),
        fn("create_channel", "Create a text channel.", {"name": {"type": "string"}, "category_id": discord_id, "reason": {"type": "string"}}, ["name"]),
        fn("delete_channel", "Delete a Discord channel by exact channel ID.", {"channel_id": discord_id, "reason": {"type": "string"}}, ["channel_id"]),
        fn("rename_channel", "Rename a Discord channel by exact channel ID.", {"channel_id": discord_id, "name": {"type": "string"}}, ["channel_id", "name"]),
        fn("lock_channel", "Prevent @everyone from sending messages in a text channel by exact channel ID.", {"channel_id": discord_id}, ["channel_id"]),
        fn("unlock_channel", "Allow @everyone to send messages in a text channel by exact channel ID.", {"channel_id": discord_id}, ["channel_id"]),
        fn("set_slowmode", "Set a text channel slowmode in seconds by exact channel ID.", {"channel_id": discord_id, "seconds": {"type": "integer"}}, ["channel_id", "seconds"]),
        fn("purge_messages", "Delete recent messages from a text channel by exact channel ID.", {"channel_id": discord_id, "amount": {"type": "integer"}}, ["channel_id", "amount"]),
    ]


def _guild(message):
    if message.guild is None: raise ValueError("This action requires a server context.")
    return message.guild


def _member(guild, user_id: int):
    member = guild.get_member(user_id)
    if member is None: raise ValueError("That member is not available in this server.")
    return member


async def execute(message, name: str, args: dict[str, Any], bot=None) -> dict[str, Any]:
    if name == "send_dm":
        client = bot or getattr(message, "client", None)
        if client is None: raise ValueError("Discord client is unavailable.")
        user_id = int(str(args["user_id"]).strip())
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            await user.send(args["content"])
            return {"ok": True, "action": "dm_sent", "user_id": str(user.id)}
        except discord.NotFound as exc:
            if getattr(exc, "code", None) == 10013: raise ValueError("Discord could not find that user. Check the user ID.") from exc
            raise ValueError("Discord could not find the requested DM recipient.") from exc
        except discord.Forbidden as exc:
            raise ValueError("Discord refused the DM. The recipient may block DMs/message requests or have blocked the bot.") from exc
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == 50007: raise ValueError("Discord cannot deliver a DM to that user. Their DM/privacy settings may block it.") from exc
            raise ValueError(f"Discord rejected the DM (HTTP {exc.status}, code {getattr(exc, 'code', 'unknown')}).") from exc

    guild = _guild(message)
    if name == "server_info":
        return {"ok": True, "id": str(guild.id), "name": guild.name, "member_count": guild.member_count, "roles": [{"id": str(r.id), "name": r.name} for r in guild.roles if r.name != "@everyone"], "channels": [{"id": str(c.id), "name": c.name, "type": str(c.type)} for c in guild.channels]}
    if name == "member_info":
        m = _member(guild, int(str(args["user_id"]).strip()))
        return {"ok": True, "id": str(m.id), "name": str(m), "display_name": m.display_name, "bot": m.bot, "roles": [{"id": str(r.id), "name": r.name} for r in m.roles if r.name != "@everyone"], "joined_at": m.joined_at.isoformat() if m.joined_at else None}
    if name == "send_message":
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if not isinstance(channel, discord.abc.Messageable): raise ValueError("Channel not found.")
        await channel.send(args["content"])
        return {"ok": True, "action": "message_sent", "channel_id": str(channel.id)}
    if name == "assign_role_all":
        role_id = int(str(args["role_id"]).strip())
        role = guild.get_role(role_id)
        if role is None: raise ValueError("Role not found. Check the role ID.")
        if role.is_default(): raise ValueError("The @everyone role cannot be assigned this way.")
        me = guild.me
        if me is None: raise ValueError("The bot member is unavailable in this server.")
        if not me.guild_permissions.manage_roles: raise ValueError("The bot needs Manage Roles permission.")
        if not role.is_assignable(): raise ValueError("That role is not assignable. Move the bot's highest role above the target role and check Manage Roles.")

        members = list(guild.members)
        assigned = 0
        skipped = 0
        failed = 0
        for index, member in enumerate(members, start=1):
            if role in member.roles:
                skipped += 1
                continue
            try:
                await member.add_roles(role, reason=args.get("reason") or "Bulk role assignment through Helzer")
                assigned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                failed += 1
                if bot:
                    import logging
                    logging.getLogger("helzer.tools").warning("Bulk role assignment failed for member=%s: %s", member.id, exc)
            if index % 25 == 0:
                await asyncio.sleep(0)

        return {"ok": True, "action": "assign_role_all", "role_id": str(role.id), "role_name": role.name, "total_members": len(members), "assigned": assigned, "skipped": skipped, "failed": failed}
    if name == "timeout_member":
        member = _member(guild, int(str(args["user_id"]).strip())); minutes = max(1, min(int(args["minutes"]), 40320))
        await member.timeout(timedelta(minutes=minutes), reason=args.get("reason") or "Requested through Helzer")
        return {"ok": True, "action": "timeout_member", "user_id": str(member.id), "minutes": minutes}
    if name in {"ban_member", "kick_member", "add_role", "remove_role"}:
        member = _member(guild, int(str(args["user_id"]).strip())); reason = args.get("reason") or "Requested through Helzer"
        if name == "ban_member": await guild.ban(member, reason=reason, delete_message_seconds=0)
        elif name == "kick_member": await guild.kick(member, reason=reason)
        else:
            role = guild.get_role(int(str(args["role_id"]).strip()))
            if role is None: raise ValueError("Role not found.")
            if name == "add_role": await member.add_roles(role, reason=reason)
            else: await member.remove_roles(role, reason=reason)
        return {"ok": True, "action": name, "user_id": str(member.id)}
    if name == "unban_member":
        user_id = int(str(args["user_id"]).strip())
        await guild.unban(discord.Object(id=user_id), reason=args.get("reason") or "Requested through Helzer")
        return {"ok": True, "action": "unban_member", "user_id": str(user_id)}
    if name == "create_role":
        role = await guild.create_role(name=args["name"], reason=args.get("reason") or "Created through Helzer")
        return {"ok": True, "action": "create_role", "role_id": str(role.id), "name": role.name}
    if name == "create_channel":
        category = guild.get_channel(int(str(args["category_id"]).strip())) if args.get("category_id") else None
        channel = await guild.create_text_channel(args["name"], category=category, reason=args.get("reason") or "Created through Helzer")
        return {"ok": True, "action": "create_channel", "channel_id": str(channel.id), "name": channel.name}
    if name == "delete_channel":
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if channel is None: raise ValueError("Channel not found.")
        await channel.delete(reason=args.get("reason") or "Deleted through Helzer")
        return {"ok": True, "action": "delete_channel", "channel_id": str(args["channel_id"])}
    if name == "rename_channel":
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if channel is None: raise ValueError("Channel not found.")
        await channel.edit(name=args["name"], reason="Renamed through Helzer")
        return {"ok": True, "action": "rename_channel", "channel_id": str(channel.id), "name": channel.name}
    if name in {"lock_channel", "unlock_channel"}:
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if not isinstance(channel, discord.TextChannel): raise ValueError("Text channel not found.")
        overwrite = channel.overwrites_for(guild.default_role); overwrite.send_messages = name == "unlock_channel"
        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Updated through Helzer")
        return {"ok": True, "action": name, "channel_id": str(channel.id)}
    if name == "set_slowmode":
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if not isinstance(channel, discord.TextChannel): raise ValueError("Text channel not found.")
        await channel.edit(slowmode_delay=max(0, min(int(args["seconds"]), 21600)))
        return {"ok": True, "action": "set_slowmode", "channel_id": str(channel.id)}
    if name == "purge_messages":
        channel = guild.get_channel(int(str(args["channel_id"]).strip()))
        if not isinstance(channel, discord.TextChannel): raise ValueError("Text channel not found.")
        amount = max(1, min(int(args["amount"]), 100)); deleted = await channel.purge(limit=amount)
        return {"ok": True, "action": "purge_messages", "deleted": len(deleted), "channel_id": str(channel.id)}
    raise ValueError(f"Unknown tool: {name}")
