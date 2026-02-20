import logging
from aiogram import Router, F, types
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.keyboards.inline.user_keyboards import (
    get_payment_methods_list_keyboard,
    get_payment_method_delete_confirm_keyboard,
    get_payment_method_details_keyboard,
    get_bind_url_keyboard,
)
from bot.services.yookassa_service import YooKassaService
from bot.middlewares.i18n import JsonI18n
from db.dal import user_billing_dal
from db.models import Payment
from sqlalchemy.future import select

router = Router(name="user_subscription_payment_methods_router")


@router.callback_query(F.data == "pm:manage")
async def payment_methods_manage(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    from db.dal.user_billing_dal import list_user_payment_methods
    get_text = _
    methods = await list_user_payment_methods(session, callback.from_user.id)
    cards: List[tuple] = []

    def _is_yoomoney_network(network: Optional[str]) -> bool:
        s = (network or "").lower()
        return "yoomoney" in s or "yoo money" in s or "yoo-money" in s

    def _extract_last4(text: str) -> Optional[str]:
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else None

    def _format_pm_title(network: Optional[str], last4: Optional[str]) -> str:
        if _is_yoomoney_network(network):
            l4 = last4 or _extract_last4(network or "")
            if l4:
                return get_text("payment_method_wallet_title", last4=l4)
            return get_text("payment_method_wallet_title", last4="****")
        if last4:
            network_name = network or get_text("payment_network_card")
            return get_text("payment_method_card_title", network=network_name, last4=last4)
        network_name = network or get_text("payment_network_generic")
        return get_text("payment_method_generic_title", network=network_name)

    for m in methods:
        title = _format_pm_title(m.card_network, m.card_last4)
        cards.append((str(m.method_id), title if not m.is_default else f"⭐ {title}"))

    text = get_text("payment_methods_title")
    if not cards:
        text += "\n\n" + get_text("payment_method_none")

    await callback.message.edit_text(text, reply_markup=get_payment_methods_list_keyboard(cards, 0, current_lang, i18n))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)


@router.callback_query(F.data == "pm:bind")
async def payment_method_bind(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession, yookassa_service: YooKassaService):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    metadata = {"user_id": str(callback.from_user.id), "bind_only": "1"}
    resp = await yookassa_service.create_payment(
        amount=1.00,
        currency="RUB",
        description="Bind card",
        metadata=metadata,
        receipt_email=settings.YOOKASSA_DEFAULT_RECEIPT_EMAIL,
        save_payment_method=True,
        capture=False,
        bind_only=True,
    )
    if not resp or not resp.get("confirmation_url"):
        await callback.answer(_("error_payment_gateway"), show_alert=True)
        return
    await callback.message.edit_text(_("payment_methods_title"), reply_markup=get_bind_url_keyboard(resp["confirmation_url"], current_lang, i18n))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)


@router.callback_query(F.data.startswith("pm:delete_confirm"))
async def payment_method_delete_confirm(callback: types.CallbackQuery, settings: Settings, i18n_data: dict):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    parts = callback.data.split(":", 2)
    pm_id = parts[2] if len(parts) >= 3 else ""
    await callback.message.edit_text(_("payment_method_delete_confirm"), reply_markup=get_payment_method_delete_confirm_keyboard(pm_id, current_lang, i18n))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)


@router.callback_query(F.data.startswith("pm:delete"))
async def payment_method_delete(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    parts = callback.data.split(":", 2)
    pm_id_raw = parts[2] if len(parts) >= 3 else ""
    deleted = False

    try:
        from db.dal.user_billing_dal import (
            delete_user_payment_method,
            delete_user_payment_method_by_provider_id,
            list_user_payment_methods,
        )
        if pm_id_raw:
            if pm_id_raw.isdigit():
                deleted = await delete_user_payment_method(session, callback.from_user.id, int(pm_id_raw))
            else:
                deleted = await delete_user_payment_method_by_provider_id(session, callback.from_user.id, pm_id_raw)
        try:
            legacy_deleted = await user_billing_dal.delete_yk_payment_method(session, callback.from_user.id)
            deleted = deleted or legacy_deleted
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        await session.commit()

        methods = await list_user_payment_methods(session, callback.from_user.id)
        text = _("payment_methods_title")
        cards = []
        for m in methods:
            def _is_yoomoney_network(network: Optional[str]) -> bool:
                s = (network or "").lower()
                return "yoomoney" in s or "yoo money" in s or "yoo-money" in s
            def _extract_last4(text: str) -> Optional[str]:
                digits = "".join(ch for ch in text if ch.isdigit())
                return digits[-4:] if len(digits) >= 4 else None
            def _format_pm_title(network: Optional[str], last4: Optional[str]) -> str:
                if _is_yoomoney_network(network):
                    l4 = last4 or _extract_last4(network or "")
                    if l4:
                        return _("payment_method_wallet_title", last4=l4)
                    return _("payment_method_wallet_title", last4="****")
                if last4:
                    network_name = network or _("payment_network_card")
                    return _("payment_method_card_title", network=network_name, last4=last4)
                network_name = network or _("payment_network_generic")
                return _("payment_method_generic_title", network=network_name)
            title = _format_pm_title(m.card_network, m.card_last4)
            cards.append((str(m.method_id), title if not m.is_default else f"⭐ {title}"))
        if not cards:
            text += "\n\n" + _("payment_method_none")
        msg = _("payment_method_deleted_success") if deleted else _("error_try_again")
        await callback.message.edit_text(f"{msg}\n\n{text}", reply_markup=get_payment_methods_list_keyboard(cards, 0, current_lang, i18n))
        try:
            await callback.answer()
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    except Exception:
        await session.rollback()
        try:
            await callback.answer(_("error_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)


@router.callback_query(F.data.startswith("pm:view"))
async def payment_method_view(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    billing = await user_billing_dal.get_user_billing(session, callback.from_user.id)
    if not billing or not billing.yookassa_payment_method_id:
        from db.dal.user_billing_dal import list_user_payment_methods
        methods = await list_user_payment_methods(session, callback.from_user.id)
        if not methods:
            await callback.answer(_("payment_method_none"), show_alert=True)
            return
        parts = callback.data.split(":", 2)
        pm_id = parts[2] if len(parts) >= 3 else str(methods[0].method_id)
        sel = next((m for m in methods if str(m.method_id) == pm_id or m.provider_payment_method_id == pm_id), methods[0])

        def _is_yoomoney_network(network: Optional[str]) -> bool:
            s = (network or "").lower()
            return "yoomoney" in s or "yoo money" in s or "yoo-money" in s

        def _extract_last4(text: str) -> Optional[str]:
            digits = "".join(ch for ch in text if ch.isdigit())
            return digits[-4:] if len(digits) >= 4 else None

        def _format_pm_title(network: Optional[str], last4: Optional[str]) -> str:
            if _is_yoomoney_network(network):
                l4 = last4 or _extract_last4(network or "")
                if l4:
                    return _("payment_method_wallet_title", last4=l4)
                return _("payment_method_wallet_title", last4="****")
            if last4:
                network_name = network or _("payment_network_card")
                return _("payment_method_card_title", network=network_name, last4=last4)
            network_name = network or _("payment_network_generic")
            return _("payment_method_generic_title", network=network_name)

        title = _format_pm_title(sel.card_network, sel.card_last4)
        added_at = sel.created_at.strftime('%Y-%m-%d') if getattr(sel, 'created_at', None) else "—"
        last_tx = "—"
        try:
            stmt = (
                select(Payment)
                .where(
                    Payment.user_id == callback.from_user.id,
                    Payment.status == 'succeeded',
                    Payment.provider == 'yookassa',
                )
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            lp = result.scalar_one_or_none()
            if lp and lp.created_at:
                last_tx = lp.created_at.strftime('%Y-%m-%d')
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        details = f"{title}\n{_('payment_method_added_at', date=added_at)}\n{_('payment_method_last_tx', date=last_tx)}"
        await callback.message.edit_text(details, reply_markup=get_payment_method_details_keyboard(str(sel.method_id), current_lang, i18n))
        try:
            await callback.answer()
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return

    added_at = billing.created_at.strftime('%Y-%m-%d') if getattr(billing, 'created_at', None) else "—"
    last_tx = "—"
    try:
        stmt = (
            select(Payment)
            .where(
                Payment.user_id == callback.from_user.id,
                Payment.status == 'succeeded',
                Payment.provider == 'yookassa',
            )
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        last_payment = result.scalar_one_or_none()
        if last_payment and last_payment.created_at:
            last_tx = last_payment.created_at.strftime('%Y-%m-%d')
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)

    def _is_yoomoney_network(network: Optional[str]) -> bool:
        s = (network or "").lower()
        return "yoomoney" in s or "yoo money" in s or "yoo-money" in s

    def _extract_last4(text: str) -> Optional[str]:
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else None

    def _format_pm_title(network: Optional[str], last4: Optional[str]) -> str:
        if _is_yoomoney_network(network):
            l4 = last4 or _extract_last4(network or "")
            if l4:
                return _("payment_method_wallet_title", last4=l4)
            return _("payment_method_wallet_title", last4="****")
        if last4:
            network_name = network or _("payment_network_card")
            return _("payment_method_card_title", network=network_name, last4=last4)
        network_name = network or _("payment_network_generic")
        return _("payment_method_generic_title", network=network_name)

    title = _format_pm_title(billing.card_network, billing.card_last4)
    details = f"{title}\n{_('payment_method_added_at', date=added_at)}\n{_('payment_method_last_tx', date=last_tx)}"
    await callback.message.edit_text(details, reply_markup=get_payment_method_details_keyboard(billing.yookassa_payment_method_id, current_lang, i18n))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)


@router.callback_query(F.data.startswith("pm:history"))
async def payment_method_history(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession, yookassa_service: YooKassaService):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not settings.yookassa_autopayments_active:
        try:
            _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
            await callback.answer(_("error_service_unavailable"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    from db.dal import payment_dal
    payments = await payment_dal.get_recent_payment_logs_with_user(session, limit=30, offset=0)
    user_payments = [p for p in payments if p.user_id == callback.from_user.id]

    selected_pm_provider_id: Optional[str] = None
    pm_filter_requested: bool = False
    try:
        split_a, split_b, split_pm_id = callback.data.split(":", 2)
        if split_pm_id:
            pm_filter_requested = True
            if split_pm_id.isdigit():
                from db.dal.user_billing_dal import list_user_payment_methods
                methods = await list_user_payment_methods(session, callback.from_user.id)
                sel = next((m for m in methods if str(m.method_id) == split_pm_id), None)
                if sel and sel.provider_payment_method_id:
                    selected_pm_provider_id = sel.provider_payment_method_id
            else:
                selected_pm_provider_id = split_pm_id
    except Exception:
        selected_pm_provider_id = None
        pm_filter_requested = False

    if pm_filter_requested and not selected_pm_provider_id:
        user_payments = []

    if selected_pm_provider_id:
        filtered: List[Payment] = []
        for p in user_payments:
            if p.provider != 'yookassa':
                continue
            if p.yookassa_payment_id and yookassa_service:
                try:
                    info = await yookassa_service.get_payment_info(p.yookassa_payment_id)
                    pm = (info or {}).get("payment_method") or {}
                    if pm.get("id") == selected_pm_provider_id:
                        filtered.append(p)
                        continue
                except Exception as exc:
                    logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)
        user_payments = filtered

    if not user_payments:
        from bot.keyboards.inline.user_keyboards import get_back_to_payment_method_details_keyboard, get_payment_methods_manage_keyboard
        back_pm_id = ""
        try:
            split_a, split_b, back_pm_id = callback.data.split(":", 2)
        except Exception:
            back_pm_id = ""
        back_markup = (
            get_back_to_payment_method_details_keyboard(back_pm_id, current_lang, i18n)
            if back_pm_id
            else get_payment_methods_manage_keyboard(current_lang, i18n, has_card=True)
        )
        await callback.message.edit_text(_("payment_method_no_history"), reply_markup=back_markup)
        return

    traffic_mode = getattr(settings, "traffic_sale_mode", False)

    def _format_item(p: Payment) -> str:
        if traffic_mode:
            units_val = p.subscription_duration_months or 0
            units_display = str(int(units_val)) if float(units_val).is_integer() else f"{units_val:g}"
            title = p.description or _("traffic_purchase_title", traffic_gb=units_display)
        else:
            title = p.description or _("subscription_purchase_title", months=p.subscription_duration_months or 1)
        date_str = p.created_at.strftime('%Y-%m-%d') if p.created_at else "N/A"
        return f"{date_str} — {title} — {p.amount:.2f} {p.currency}"

    lines = [_format_item(p) for p in user_payments]
    text = _("payment_method_tx_history_title") + "\n\n" + "\n".join(lines)
    try:
        split_a, split_b, split_pm_id_for_back = callback.data.split(":", 2)
    except Exception:
        split_pm_id_for_back = ""
    from bot.keyboards.inline.user_keyboards import get_back_to_payment_method_details_keyboard, get_payment_methods_manage_keyboard
    back_markup = (
        get_back_to_payment_method_details_keyboard(split_pm_id_for_back, current_lang, i18n)
        if split_pm_id_for_back
        else get_payment_methods_manage_keyboard(current_lang, i18n, has_card=True)
    )
    await callback.message.edit_text(text, reply_markup=back_markup)


@router.callback_query(F.data.startswith("pm:list:"))
async def payment_methods_list(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    from db.dal.user_billing_dal import list_user_payment_methods
    cards: List[tuple] = []
    methods = await list_user_payment_methods(session, callback.from_user.id)
    for m in methods:
        def _is_yoomoney_network(network: Optional[str]) -> bool:
            s = (network or "").lower()
            return "yoomoney" in s or "yoo money" in s or "yoo-money" in s
        def _extract_last4(text: str) -> Optional[str]:
            digits = "".join(ch for ch in text if ch.isdigit())
            return digits[-4:] if len(digits) >= 4 else None
        def _format_pm_title(network: Optional[str], last4: Optional[str]) -> str:
            if _is_yoomoney_network(network):
                l4 = last4 or _extract_last4(network or "")
                if l4:
                    return get_text("payment_method_wallet_title", last4=l4)
                return get_text("payment_method_wallet_title", last4="****")
            if last4:
                network_name = network or get_text("payment_network_card")
                return get_text("payment_method_card_title", network=network_name, last4=last4)
            network_name = network or get_text("payment_network_generic")
            return get_text("payment_method_generic_title", network=network_name)
        title = _format_pm_title(m.card_network, m.card_last4)
        cards.append((str(m.method_id), title if not m.is_default else f"⭐ {title}"))

    try:
        _, _, page_str = callback.data.split(":", 2)
        page = int(page_str)
    except Exception:
        page = 0

    text = get_text("payment_methods_title")
    if not cards:
        text += "\n\n" + get_text("payment_method_none")
    await callback.message.edit_text(text, reply_markup=get_payment_methods_list_keyboard(cards, page, current_lang, i18n))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payment_methods.py: %s", exc)

