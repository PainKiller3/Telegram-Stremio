from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from Backend import db
from Backend.config import Telegram
from Backend.helper.jellyfin import (
    find_jellyfin_user,
    get_jellyfin_public_url,
    reset_jellyfin_user_password,
)
from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER


def _is_admin(user_id: int) -> bool:
    settings = SettingsManager.current()
    admins = list(settings.approver_ids or [])
    if Telegram.OWNER_ID:
        admins.append(Telegram.OWNER_ID)
    return user_id in admins


# ── /jellyfin: Status & Dashboard ─────────────────────────────────────────────
@Client.on_message(filters.command("jellyfin") & filters.private)
async def jellyfin_dashboard(client: Client, message: Message):
    user_id = message.from_user.id
    linked_username = await db.get_linked_jellyfin_username(user_id)
    public_url = get_jellyfin_public_url()

    if not linked_username:
        text = (
            "🎬 <b>Jellyfin Streaming Account</b>\n\n"
            "You haven't linked your Jellyfin account yet!\n\n"
            "📌 <b>To link your account:</b>\n"
            "<code>/linkjellyfin &lt;your_username&gt;</code>\n\n"
            "<i>Example:</i> <code>/linkjellyfin alex</code>\n\n"
            "Don't have an account yet? Ask the admin or register on the signup portal."
        )
        buttons = []
        if public_url:
            buttons.append([InlineKeyboardButton("🌐 Open Media Player", url=public_url)])
        return await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    text = (
        "🎬 <b>Your Jellyfin Account</b>\n\n"
        f"👤 <b>Username:</b> <code>{linked_username}</code>\n"
        f"🌐 <b>Server:</b> <a href='{public_url}'>{public_url}</a>\n\n"
        "🔑 <b>Need to reset your password?</b>\n"
        "Send: <code>/resetjellyfin &lt;new_password&gt;</code>\n"
        "<i>Example:</i> <code>/resetjellyfin Pass@1234</code>"
    )
    buttons = [
        [InlineKeyboardButton("🔑 Reset Password", callback_data="jf_reset_help")],
        [InlineKeyboardButton("🌐 Open Media Player", url=public_url)],
        [InlineKeyboardButton("🔗 Unlink Account", callback_data="jf_unlink")]
    ]
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )


# ── /linkjellyfin <username>: Link Account ────────────────────────────────────
@Client.on_message(filters.command("linkjellyfin") & filters.private)
async def link_jellyfin_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text(
            "⚠️ <b>Please specify your Jellyfin username:</b>\n"
            "<code>/linkjellyfin &lt;your_username&gt;</code>\n\n"
            "<i>Example:</i> <code>/linkjellyfin alex</code>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    target_name = parts[1].strip()

    # 1. Verify user exists in Jellyfin
    jf_user = await find_jellyfin_user(target_name)
    if not jf_user:
        return await message.reply_text(
            f"❌ User <code>{target_name}</code> was not found on the Jellyfin server.\n\n"
            "Please check the spelling and try again.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    # 2. Block linking Administrator accounts for safety
    if (jf_user.get("Policy") or {}).get("IsAdministrator"):
        return await message.reply_text(
            "⚠️ <b>Security Notice:</b> Administrator accounts cannot be linked to the Telegram bot.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    # 3. Link in MongoDB
    actual_name = jf_user.get("Name") or target_name
    success = await db.link_jellyfin_user(user_id, actual_name)
    if not success:
        return await message.reply_text(
            f"❌ The username <code>{actual_name}</code> is already linked to another Telegram user.\n"
            "If this is your account, please contact the administrator.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    public_url = get_jellyfin_public_url()
    await message.reply_text(
        "🎉 <b>Successfully Linked!</b>\n\n"
        f"Your Telegram account is now linked to: <b>{actual_name}</b>\n\n"
        "🔑 <b>Self-Service Password Reset:</b>\n"
        "If you ever forget your password, simply send:\n"
        "<code>/resetjellyfin &lt;new_password&gt;</code>\n"
        "and your password will update instantly!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Open Media Player", url=public_url)]
        ]),
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )


# ── /resetjellyfin <new_password>: Reset Password ────────────────────────────
@Client.on_message(filters.command("resetjellyfin") & filters.private)
async def reset_jellyfin_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    linked_username = await db.get_linked_jellyfin_username(user_id)

    if not linked_username:
        return await message.reply_text(
            "⚠️ <b>You haven't linked a Jellyfin account yet!</b>\n\n"
            "First link your account by sending:\n"
            "<code>/linkjellyfin &lt;your_username&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text(
            f"🔑 <b>Resetting password for:</b> <code>{linked_username}</code>\n\n"
            "Please provide your new password:\n"
            "<code>/resetjellyfin &lt;new_password&gt;</code>\n\n"
            "<i>Example:</i> <code>/resetjellyfin MySecretPass2026</code>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    new_password = parts[1].strip()
    if len(new_password) < 4:
        return await message.reply_text(
            "⚠️ <b>Password too short:</b> Must be at least 4 characters.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    # Verify user still exists in Jellyfin
    jf_user = await find_jellyfin_user(linked_username)
    if not jf_user:
        return await message.reply_text(
            f"❌ User <code>{linked_username}</code> could not be found on Jellyfin.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    if (jf_user.get("Policy") or {}).get("IsAdministrator"):
        return await message.reply_text(
            "⚠️ Administrator passwords cannot be reset via Telegram.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    # Perform password reset via REST API
    user_id_str = jf_user.get("Id")
    success = await reset_jellyfin_user_password(user_id_str, new_password)
    if not success:
        return await message.reply_text(
            "❌ <b>Failed to reset password.</b>\n"
            "Please check if the Jellyfin server is reachable or contact the administrator.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    public_url = get_jellyfin_public_url()
    await message.reply_text(
        "✅ <b>Password Reset Successfully!</b>\n\n"
        f"👤 <b>Username:</b> <code>{linked_username}</code>\n"
        f"🔑 <b>New Password:</b> <code>{new_password}</code>\n"
        f"🌐 <b>Server:</b> <a href='{public_url}'>{public_url}</a>\n\n"
        "You can now log in with your new password!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Open Media Player Now", url=public_url)]
        ]),
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )


# ── /unlinkjellyfin: Unlink Account ───────────────────────────────────────────
@Client.on_message(filters.command("unlinkjellyfin") & filters.private)
async def unlink_jellyfin_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    linked_username = await db.get_linked_jellyfin_username(user_id)
    if not linked_username:
        return await message.reply_text(
            "You do not have any linked Jellyfin account.",
            quote=True
        )

    await db.unlink_jellyfin_user(user_id)
    await message.reply_text(
        f"Unlinked account <code>{linked_username}</code> from your Telegram profile.",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )


# ── Admin: /admin_resetjellyfin <username> <new_password> ─────────────────────
@Client.on_message(filters.command("admin_resetjellyfin") & filters.private)
async def admin_reset_jellyfin_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return await message.reply_text(
            "<b>Usage (Admin):</b>\n"
            "<code>/admin_resetjellyfin &lt;username&gt; &lt;new_password&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    target_name = parts[1].strip()
    new_password = parts[2].strip()

    jf_user = await find_jellyfin_user(target_name)
    if not jf_user:
        return await message.reply_text(
            f"❌ User <code>{target_name}</code> not found in Jellyfin.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    user_id_str = jf_user.get("Id")
    success = await reset_jellyfin_user_password(user_id_str, new_password)
    if success:
        await message.reply_text(
            f"✅ [Admin] Password for <code>{target_name}</code> successfully updated to <code>{new_password}</code>.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
    else:
        await message.reply_text(
            f"❌ [Admin] Failed to update password for <code>{target_name}</code>.",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )


# ── Callbacks for UI ──────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^jf_"))
async def jellyfin_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data == "jf_reset_help":
        linked_username = await db.get_linked_jellyfin_username(user_id)
        if not linked_username:
            return await query.answer("No account linked!", show_alert=True)
        await query.answer()
        await query.message.reply_text(
            f"🔑 <b>To reset your password for {linked_username}:</b>\n\n"
            "Send the command:\n"
            "<code>/resetjellyfin &lt;new_password&gt;</code>\n\n"
            "<i>Example:</i> <code>/resetjellyfin MyNewPassword123</code>",
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "jf_unlink":
        await db.unlink_jellyfin_user(user_id)
        await query.answer("Account unlinked successfully!", show_alert=True)
        try:
            await query.message.edit_text(
                "Account unlinked. You can link a new account anytime with:\n<code>/linkjellyfin &lt;username&gt;</code>",
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass
