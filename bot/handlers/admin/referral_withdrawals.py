import logging
from typing import Optional, List
from aiogram import Router, F, types
from aiogram.utils.markdown import hcode
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from db.dal import referral_withdrawal_dal, user_dal
from db.models import ReferralWithdrawRequest
from bot.middlewares.i18n import JsonI18n

router = Router(name="admin_referral_withdrawals_router")


def _format_request_text(req: ReferralWithdrawRequest, i18n: JsonI18n, lang: str, settings: Settings) -> str:
    _ = lambda key, **kwargs: i18n.gettext(lang, key, **kwargs)
    status_emoji = "⏳" if req.status == "pending" else ("✅" if req.status == "paid" else "❌")
    user_info = f"{req.user_id}"
    if req.user and req.user.username:
        user_info += f" (@{req.user.username})"
    elif req.user and req.user.first_name:
        user_info += f" ({req.user.first_name})"

    created_at = req.created_at.strftime('%Y-%m-%d %H:%M') if req.created_at else "N/A"
    contact_preview = (req.contact or "").strip()
    if len(contact_preview) > 200:
        contact_preview = contact_preview[:200] + "…"

    return (
        f"{status_emoji} <b>#{req.request_id}</b> {req.amount:.2f} {settings.DEFAULT_CURRENCY_SYMBOL}\n"
        f"👤 {user_info}\n"
        f"📅 {created_at}\n"
        f"📞 {hcode(contact_preview)}\n"
        f"📋 {req.status}"
    )


async def view_withdraw_requests_handler(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
    page: int = 0,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing request.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    page_size = 5
    total_count = await referral_withdrawal_dal.count_requests(session, status="pending")
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    requests = await referral_withdrawal_dal.list_requests(
        session, status="pending", limit=page_size, offset=page * page_size
    )

    if not requests and page == 0:
        await callback.message.edit_text(
            _("admin_no_withdraw_requests"),
            reply_markup=InlineKeyboardBuilder().button(
                text=_("back_to_admin_panel_button"),
                callback_data="admin_section:stats_monitoring",
            ).as_markup(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    text_parts: List[str] = [_("admin_withdraw_requests_title")]
    text_parts.append(
        _("admin_withdraw_requests_pagination",
          shown=len(requests),
          total=total_count,
          current_page=page + 1,
          total_pages=total_pages)
        + "\n"
    )

    for idx, req in enumerate(requests, 1):
        text_parts.append(
            f"<b>{page * page_size + idx}.</b> {_format_request_text(req, i18n, current_lang, settings)}"
        )
        text_parts.append("")

    builder = InlineKeyboardBuilder()

    for req in requests:
        builder.row(
            InlineKeyboardButton(
                text=_("admin_withdraw_request_mark_paid_button"),
                callback_data=f"withdraw_request:pay:{req.request_id}:{page}",
            ),
            InlineKeyboardButton(
                text=_("admin_withdraw_request_reject_button"),
                callback_data=f"withdraw_request:reject:{req.request_id}:{page}",
            ),
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"withdraw_requests_page:{page - 1}")
        )
    nav_buttons.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=f"withdraw_requests_page:{page + 1}")
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(
            text=_("admin_refresh_withdraw_requests"),
            callback_data=f"withdraw_requests_page:{page}",
        ),
        InlineKeyboardButton(
            text=_("back_to_admin_panel_button"),
            callback_data="admin_section:stats_monitoring",
        ),
    )

    await callback.message.edit_text(
        "\n".join(text_parts),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdraw_requests_page:"))
async def withdraw_requests_page_handler(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
):
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Error processing pagination.", show_alert=True)
        return
    await view_withdraw_requests_handler(callback, i18n_data, settings, session, page)


@router.callback_query(F.data.startswith("withdraw_request:"))
async def withdraw_request_action_handler(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await callback.answer("Language error.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Error processing request.", show_alert=True)
        return

    action = parts[1]
    try:
        request_id = int(parts[2])
        page = int(parts[3])
    except ValueError:
        await callback.answer("Error processing request.", show_alert=True)
        return

    req = await referral_withdrawal_dal.get_request_by_id(session, request_id)
    if not req:
        await callback.answer(_("admin_withdraw_request_not_found"), show_alert=True)
        return

    if req.status != "pending":
        await callback.answer(_("admin_withdraw_request_not_pending"), show_alert=True)
        return

    admin_id = callback.from_user.id if callback.from_user else None

    if action == "pay":
        await referral_withdrawal_dal.update_request_status(
            session,
            request_id,
            status="paid",
            processed_by_admin_id=admin_id,
        )
        await callback.answer(
            _("admin_withdraw_request_marked_paid", request_id=request_id),
            show_alert=True,
        )
    elif action == "reject":
        await referral_withdrawal_dal.update_request_status(
            session,
            request_id,
            status="rejected",
            processed_by_admin_id=admin_id,
        )
        try:
            await user_dal.adjust_referral_balance(session, req.user_id, req.amount)
        except Exception as e_refund:
            logging.error(
                "Failed to refund referral balance for request %s: %s",
                request_id,
                e_refund,
            )
        await callback.answer(
            _("admin_withdraw_request_rejected", request_id=request_id),
            show_alert=True,
        )
    else:
        await callback.answer("Unknown action.", show_alert=True)
        return

    await view_withdraw_requests_handler(callback, i18n_data, settings, session, page)
