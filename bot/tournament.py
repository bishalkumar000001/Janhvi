import os
import random
from datetime import datetime, timezone
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

import database as db
from models import OWNER_ID
from utils import display_name_from_db

TOURNAMENT_GROUP_ID = os.environ.get("TOURNAMENT_GROUP_ID", "").strip()
TOURNAMENT_GROUP_LINK = os.environ.get("TOURNAMENT_GROUP_LINK", "").strip()


def _is_owner(user_id: int) -> bool:
    return OWNER_ID and user_id == OWNER_ID


def _fmt_start(value):
    if not value:
        return "Not specified"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d %b %Y, %I:%M %p")
    return str(value)


async def _active_tournament():
    return await db.get_active_tournament()


async def _send_tournament_info(bot, chat_id, tournament):
    players = tournament.get("players", [])
    max_players = tournament.get("max_players")
    max_text = str(max_players) if max_players else "Unlimited"
    status = tournament.get("status", "registration")
    current = tournament.get("current_round", 0)
    text = (
        f"🏆 <b>{escape(tournament['title'])}</b>\n\n"
        f"🎁 <b>Prize:</b> {escape(tournament.get('prize') or 'Not announced')}\n"
        f"⏰ <b>Start:</b> {escape(_fmt_start(tournament.get('start_at')))}\n"
        f"👥 <b>Players:</b> {len(players)}/{max_text}\n"
        f"🎯 <b>Status:</b> {status.title()}\n"
        f"🔢 <b>Round:</b> {current or 'Registration'}\n\n"
        f"📜 <b>Rules:</b>\n{escape(tournament.get('rules') or 'Single-elimination Bingo tournament.')}"
    )
    if TOURNAMENT_GROUP_LINK:
        text += f"\n\n📢 <b>Official Group:</b> {escape(TOURNAMENT_GROUP_LINK)}"
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


def _wizard_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Tournament Name", callback_data="tw:title")],
        [InlineKeyboardButton("🎁 Prize", callback_data="tw:prize"),
         InlineKeyboardButton("⏰ Start Time", callback_data="tw:start")],
        [InlineKeyboardButton("👥 Max Players", callback_data="tw:max"),
         InlineKeyboardButton("📜 Rules", callback_data="tw:rules")],
        [InlineKeyboardButton("👀 Preview", callback_data="tw:preview")],
        [InlineKeyboardButton("✅ Create Tournament", callback_data="tw:create")],
        [InlineKeyboardButton("❌ Cancel", callback_data="tw:cancel")],
    ])


def _wizard_text(data):
    return (
        "🏆 <b>Create Tournament</b>\n\n"
        f"🏆 <b>Name:</b> {escape(data.get('title') or 'Not set')}\n"
        f"🎁 <b>Prize:</b> {escape(data.get('prize') or 'Not set')}\n"
        f"⏰ <b>Start:</b> {escape(data.get('start_at') or 'Not set')}\n"
        f"👥 <b>Max Players:</b> {escape(str(data.get('max_players') or 'Unlimited'))}\n"
        f"📜 <b>Rules:</b> {escape(data.get('rules') or 'Not set')}\n\n"
        "Tap a button to edit a field, then press <b>Create Tournament</b>."
    )


async def _send_tournament_wizard(update, context):
    await update.message.reply_text(
        _wizard_text(context.user_data["tournament_wizard"]),
        parse_mode="HTML",
        reply_markup=_wizard_keyboard(),
    )


async def handle_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(query.from_user.id):
        await query.answer("Only the bot owner can use this.", show_alert=True)
        return
    if query.message.chat.type != "private":
        await query.answer("Use tournament controls in bot DM.", show_alert=True)
        return
    data = query.data
    if not data.startswith("tw:"):
        return
    action = data.split(":", 1)[1]

    if action == "cancel":
        context.user_data.pop("tournament_wizard", None)
        context.user_data.pop("tournament_wizard_step", None)
        await query.edit_message_text("❌ Tournament creation cancelled.")
        await query.answer()
        return

    wizard = context.user_data.setdefault("tournament_wizard", {
        "title": "", "prize": "", "start_at": "", "max_players": "",
        "rules": "Single-elimination Bingo tournament. Winners advance to the next round."
    })

    prompts = {
        "title": "🏆 Send the tournament name:",
        "prize": "🎁 Send the prize (cash, Premium, NFT, Telegram IDs, etc.):",
        "start": "⏰ Send the tournament start date/time:",
        "max": "👥 Send maximum players, or type <code>0</code> for unlimited:",
        "rules": "📜 Send the tournament rules:",
    }
    if action in prompts:
        context.user_data["tournament_wizard_step"] = action
        await query.answer()
        await query.message.reply_text(prompts[action], parse_mode="HTML")
        return

    if action == "preview":
        await query.answer()
        await query.message.reply_text(_wizard_text(wizard), parse_mode="HTML", reply_markup=_wizard_keyboard())
        return

    if action == "create":
        if not wizard.get("title") or not wizard.get("prize"):
            await query.answer("Set Tournament Name and Prize first.", show_alert=True)
            return
        if await _active_tournament():
            await query.answer("An active tournament already exists.", show_alert=True)
            return
        if not TOURNAMENT_GROUP_ID:
            await query.answer("TOURNAMENT_GROUP_ID is not configured.", show_alert=True)
            return
        max_players = None
        raw_max = str(wizard.get("max_players") or "").strip()
        if raw_max and raw_max != "0":
            try:
                max_players = int(raw_max)
                if max_players < 2:
                    raise ValueError
            except ValueError:
                await query.answer("Max players must be 0 or a number >= 2.", show_alert=True)
                return
        created = await db.create_tournament(
            title=wizard["title"].strip(),
            prize=wizard["prize"].strip(),
            start_at=wizard.get("start_at") or None,
            max_players=max_players,
            rules=wizard.get("rules") or "Single-elimination Bingo tournament.",
            group_id=TOURNAMENT_GROUP_ID,
        )
        context.user_data.pop("tournament_wizard", None)
        context.user_data.pop("tournament_wizard_step", None)
        await query.edit_message_text(
            f"✅ <b>Tournament Created!</b>\n\n"
            f"🏆 {escape(created['title'])}\n"
            f"🎁 {escape(created['prize'])}\n"
            f"⏰ {escape(_fmt_start(created.get('start_at')))}\n"
            f"👥 {created.get('max_players') or 'Unlimited'} players\n\n"
            f"Players can now use /join in DM after joining the official group.\n"
            f"Use <code>/tournament announce</code> to announce it.",
            parse_mode="HTML",
        )
        await query.answer("Tournament created!")
        return
    await query.answer()


async def handle_tournament_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != "private":
        return False
    if not _is_owner(update.effective_user.id):
        return False
    step = context.user_data.get("tournament_wizard_step")
    if not step or "tournament_wizard" not in context.user_data:
        return False
    value = (update.message.text or "").strip()
    if not value:
        await update.message.reply_text("❌ Please send text.")
        return True
    wizard = context.user_data["tournament_wizard"]
    if step == "max":
        if value.lower() in ("0", "unlimited", "∞"):
            wizard["max_players"] = ""
        else:
            try:
                n = int(value)
                if n < 2:
                    raise ValueError
                wizard["max_players"] = str(n)
            except ValueError:
                await update.message.reply_text("❌ Max players must be 0 (unlimited) or a number >= 2.")
                return True
    else:
        wizard[step] = value
    context.user_data.pop("tournament_wizard_step", None)
    await update.message.reply_text(_wizard_text(wizard), parse_mode="HTML", reply_markup=_wizard_keyboard())
    return True


async def cmd_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if not _is_owner(user.id):
        await update.message.reply_text("🚫 Only the bot owner can manage tournaments.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Tournament management commands work only in the bot DM.")
        return

    if not args:
        t = await _active_tournament()
        if not t:
            await update.message.reply_text(
                "🏆 <b>Tournament Control</b>\n\n"
                "/tournament create Title | Prize | Start time | Max players | Rules\n"
                "/tournament info\n"
                "/tournament announce\n"
                "/tournament start\n"
                "/tournament cancel\n"
                "/round — create the next knockout round after the current round is complete\n\n"
                "Players register with /join.", parse_mode="HTML")
            return
        await _send_tournament_info(context.bot, user.id, t)
        return

    action = args[0].lower()
    if action == "create":
        if await _active_tournament():
            await update.message.reply_text("❌ An active tournament already exists. Cancel it first.")
            return
        payload = update.message.text.partition("create")[2].strip()
        if not payload:
            context.user_data["tournament_wizard"] = {
                "title": "", "prize": "", "start_at": "", "max_players": "",
                "rules": "Single-elimination Bingo tournament. Winners advance to the next round."
            }
            await _send_tournament_wizard(update, context)
            return
        parts = [p.strip() for p in payload.split("|")]
        if len(parts) < 2:
            await update.message.reply_text(
                "Usage:\n/tournament create Title | Prize | Start time | Max players | Rules\n\n"
                "Example:\n/tournament create Velocity Grand Bingo | ₹50,000 Cash + Premium + NFT | 25 Aug 2026 8:00 PM | 64 | Single elimination"
            )
            return
        title = parts[0]
        prize = parts[1]
        start_at = parts[2] if len(parts) > 2 and parts[2] else None
        max_players = None
        if len(parts) > 3 and parts[3]:
            try:
                max_players = int(parts[3])
            except ValueError:
                await update.message.reply_text("❌ Max players must be a number.")
                return
            if max_players < 2:
                await update.message.reply_text("❌ Max players must be at least 2.")
                return
        rules = parts[4] if len(parts) > 4 and parts[4] else "Single-elimination Bingo tournament. Winners advance to the next round."
        if not TOURNAMENT_GROUP_ID:
            await update.message.reply_text("❌ TOURNAMENT_GROUP_ID is not configured.")
            return
        t = await db.create_tournament(
            title=title, prize=prize, start_at=start_at, max_players=max_players,
            rules=rules, group_id=TOURNAMENT_GROUP_ID,
        )
        await update.message.reply_text(
            f"✅ Tournament created: <b>{escape(title)}</b>\n\n"
            f"Players can now join with /join in DM after joining the official group.\n"
            f"Use /tournament announce to publish it.", parse_mode="HTML")
        return

    t = await _active_tournament()
    if not t:
        await update.message.reply_text("❌ No active tournament.")
        return

    if action == "info":
        await _send_tournament_info(context.bot, user.id, t)
    elif action == "announce":
        text = (
            f"🏆 <b>{escape(t['title'])}</b> 🏆\n\n"
            f"🎁 <b>PRIZE:</b> {escape(t.get('prize') or 'TBA')}\n"
            f"⏰ <b>START:</b> {escape(_fmt_start(t.get('start_at')))}\n"
            f"👥 <b>PLAYERS:</b> {len(t.get('players', []))}/{t.get('max_players') or '∞'}\n\n"
            f"📜 <b>RULES</b>\n{escape(t.get('rules') or 'Single elimination.')}\n\n"
            f"1️⃣ Join the official Telegram group.\n"
            f"2️⃣ Open the bot in DM.\n"
            f"3️⃣ Send <code>/join</code>.\n\n"
            f"🎮 The bot automatically shuffles players and creates every 2-player Bingo room."
        )
        if TOURNAMENT_GROUP_LINK:
            text += f"\n\n🔗 {escape(TOURNAMENT_GROUP_LINK)}"
        # Send the tournament announcement to every registered player in DM,
        # and ONLY to the official Bingo/tournament group. Do not broadcast this
        # announcement to other groups where the bot is installed.
        user_ids = list(dict.fromkeys(await db.get_all_user_ids()))
        sent_users = failed_users = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
                sent_users += 1
            except Exception:
                failed_users += 1

        sent_group = 0
        try:
            await context.bot.send_message(chat_id=t["group_id"], text=text, parse_mode="HTML")
            sent_group = 1
        except Exception:
            pass

        await update.message.reply_text(
            f"📢 <b>Tournament announcement sent!</b>\n\n"
            f"👤 Player DMs: <b>{sent_users}</b> sent, <b>{failed_users}</b> failed\n"
            f"🏆 Official Bingo Group: <b>{'Sent' if sent_group else 'Failed'}</b>",
            parse_mode="HTML",
        )
    elif action == "start":
        await start_tournament(context, t)
        await update.message.reply_text("🚀 Tournament start command processed.")
    elif action == "cancel":
        await db.update_tournament(t["id"], status="cancelled")
        await update.message.reply_text("🛑 Tournament cancelled.")
    else:
        await update.message.reply_text("Unknown action. Use /tournament for the control panel.")


async def cmd_join_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private":
        await update.message.reply_text("📩 Open the bot in DM and send /join.")
        return
    tournament = await _active_tournament()
    if not tournament or tournament.get("status") != "registration":
        await update.message.reply_text("❌ Tournament registration is not open.")
        return
    if user.id in tournament.get("players", []):
        await update.message.reply_text("✅ You are already registered for this tournament.")
        return

    try:
        member = await context.bot.get_chat_member(tournament["group_id"], user.id)
        if member.status in ("left", "kicked"):
            raise ValueError
    except Exception:
        link = TOURNAMENT_GROUP_LINK or "the official group"
        keyboard = None
        if TOURNAMENT_GROUP_LINK:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Official Group", url=TOURNAMENT_GROUP_LINK)]])
        await update.message.reply_text(
            f"🚫 You must join the official Telegram group first.\n\n🔗 {link}\n\nThen send /join again.",
            reply_markup=keyboard,
        )
        return

    max_players = tournament.get("max_players")
    if max_players and len(tournament.get("players", [])) >= max_players:
        await update.message.reply_text("❌ Tournament is full.")
        return

    await db.create_user(user.id, user.username, user.first_name)
    ok = await db.add_tournament_player(tournament["id"], user.id, max_players)
    if not ok:
        await update.message.reply_text("❌ Registration failed or the tournament just became full.")
        return
    registered_count = len(tournament.get("players", [])) + 1
    await update.message.reply_text(
        f"✅ <b>You are registered!</b>\n\n🏆 {escape(tournament['title'])}\n"
        f"👥 Registered players: {registered_count}/{max_players or '∞'}",
        parse_mode="HTML")

    # Announce every successful registration in the official tournament group.
    player_name = display_name_from_db({
        "first_name": user.first_name or "",
        "username": user.username or "",
    })
    group_name = escape(tournament.get("title") or "Tournament")
    try:
        await context.bot.send_message(
            chat_id=tournament["group_id"],
            text=(
                f"🎟️ <b>New Tournament Player Joined!</b>\n\n"
                f"🏆 <b>{group_name}</b>\n"
                f"👤 <b>{escape(player_name)}</b>\n"
                f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
                f"👥 <b>Registered:</b> {registered_count}/{max_players or '∞'}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def start_tournament(context, tournament):
    if tournament.get("status") != "registration":
        return
    players = list(tournament.get("players", []))
    if len(players) < 2:
        await context.bot.send_message(chat_id=OWNER_ID, text="❌ At least 2 players are required to start.")
        return
    if tournament.get("max_players") and len(players) > tournament["max_players"]:
        return

    for pid in players:
        if await db.is_player_in_active_room(pid):
            await context.bot.send_message(chat_id=OWNER_ID, text=f"❌ Player {pid} is already in an active Bingo room. Finish that game before starting the tournament.")
            return
    await db.update_tournament(tournament["id"], status="active")
    await _create_round(context, tournament["id"], 1, players)


async def cmd_round(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_owner(user.id):
        await update.message.reply_text("🚫 Only the bot owner can use /round.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 Use /round in the bot DM.")
        return
    t = await _active_tournament()
    if not t or t.get("status") != "active":
        await update.message.reply_text("❌ No active tournament is waiting for a round.")
        return
    current = t.get("current_round", 0)
    rounds = t.get("rounds", [])
    if not rounds:
        await update.message.reply_text("❌ Round 1 has not been started. Use /tournament start.")
        return
    current_data = next((r for r in rounds if r["round"] == current), None)
    if not current_data:
        await update.message.reply_text("❌ Current round data is missing.")
        return
    if any(m.get("status") == "playing" for m in current_data.get("matches", [])):
        await update.message.reply_text("⏳ Current round is still running. Wait until every Bingo match has a winner.")
        return
    if any(m.get("winner") is None for m in current_data.get("matches", [])):
        await update.message.reply_text("⏳ Some matches have not finished yet.")
        return

    next_players = [m["winner"] for m in current_data.get("matches", []) if m.get("winner")]
    next_players.extend(current_data.get("byes", []))
    if len(next_players) == 1:
        await _finish_tournament(context, t, next_players[0])
        await update.message.reply_text("🏆 The tournament has a champion!")
        return
    await _create_round(context, t["id"], current + 1, next_players)
    await update.message.reply_text(f"🎮 Round {current + 1} rooms created automatically.")


async def _create_round(context, tournament_id, round_no, players):
    tournament = await db.get_tournament(tournament_id)
    random.shuffle(players)
    byes = []
    if len(players) % 2:
        byes.append(players.pop())

    matches = []
    for index in range(0, len(players), 2):
        p1_id, p2_id = players[index], players[index + 1]
        p1 = await db.get_user(p1_id)
        p2 = await db.get_user(p2_id)
        if not p1 or not p2:
            continue
        placeholder = await context.bot.send_message(
            chat_id=tournament["group_id"],
            text=(f"🏆 <b>{escape(tournament['title'])}</b>\n"
                   f"🎯 <b>Round {round_no}</b> — Match {len(matches) + 1}\n\n"
                   f"👤 {escape(display_name_from_db(p1))}\n"
                   f"⚔️ VS\n"
                   f"👤 {escape(display_name_from_db(p2))}\n\n"
                   f"🎲 Players already joined — starting automatically..."),
            parse_mode="HTML",
        )
        room_id = await db.create_room(
            chat_id=tournament["group_id"],
            room_number=f"T{round_no}-{len(matches) + 1}",
            player1_id=p1_id,
            room_message_id=placeholder.message_id,
        )
        await db.join_room(room_id, p2_id)
        await db.update_room(room_id, tournament_id=tournament_id, tournament_round=round_no, tournament_match=len(matches) + 1)
        matches.append({"room_id": room_id, "player1": p1_id, "player2": p2_id, "winner": None, "status": "playing"})
        from game import start_game_countdown
        asyncio_task = start_game_countdown(context, room_id, tournament["group_id"], p1, p2, placeholder.message_id)
        import asyncio
        asyncio.create_task(asyncio_task)

    await db.append_tournament_round(tournament_id, round_no, matches, byes)
    await db.update_tournament(tournament_id, current_round=round_no)
    if byes:
        names = []
        for pid in byes:
            p = await db.get_user(pid)
            names.append(display_name_from_db(p) if p else str(pid))
        await context.bot.send_message(
            chat_id=tournament["group_id"],
            text=f"🎟️ <b>Round {round_no} BYE:</b> {escape(', '.join(names))}\nThey advance automatically to the next round.",
            parse_mode="HTML",
        )


async def handle_tournament_match_result(context, room, winner_id):
    tournament_id = room.get("tournament_id")
    if not tournament_id:
        return
    t = await db.get_tournament(tournament_id)
    if not t or t.get("status") != "active":
        return
    await db.set_tournament_match_winner(tournament_id, room["id"], winner_id)
    winner = await db.get_user(winner_id)
    if winner:
        await context.bot.send_message(
            chat_id=t["group_id"],
            text=(f"🏆 <b>Tournament Winner — Round {room.get('tournament_round')}</b>\n\n"
                  f"🥇 {escape(display_name_from_db(winner))} advances!\n"
                  f"🎯 Match {room.get('tournament_match')} completed."),
            parse_mode="HTML")


async def _finish_tournament(context, tournament, winner_id):
    await db.update_tournament(tournament["id"], status="finished", champion=winner_id)
    winner = await db.get_user(winner_id)
    name = display_name_from_db(winner) if winner else str(winner_id)
    text = (
        f"👑 <b>TOURNAMENT CHAMPION!</b> 👑\n\n"
        f"🏆 <b>{escape(tournament['title'])}</b>\n"
        f"🥇 Champion: <b>{escape(name)}</b>\n\n"
        f"🎁 <b>Prize:</b> {escape(tournament.get('prize') or 'TBA')}\n\n"
        f"🎉 Congratulations!"
    )
    await context.bot.send_message(chat_id=tournament["group_id"], text=text, parse_mode="HTML")
    try:
        await context.bot.send_message(chat_id=winner_id, text=text, parse_mode="HTML")
    except Exception:
        pass
