import logging
from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.middlewares.i18n import JsonI18n
from db.dal import ad_dal
from bot.states.admin_states import AdminStates

router = Router(name="admin_ads_router")


PAGE_SIZE = 5


@router.callback_query(F.data == "admin_action:ads")
async def show_ads_menu(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    totals = await ad_dal.get_totals(session)
    total_cost = totals.get("cost", 0.0)
    total_revenue = totals.get("revenue", 0.0)
    overview = _("admin_ads_overview", revenue=f"{total_revenue:.2f}", cost=f"{total_cost:.2f}")

    total_count = await ad_dal.count_campaigns(session)
    if total_count == 0:
        text = overview + "\n\n" + _("admin_ads_empty")
        from bot.keyboards.inline.admin_keyboards import get_ads_menu_keyboard
        reply_markup = get_ads_menu_keyboard(i18n, current_lang)
    else:
        current_page = 0
        total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
        campaigns = await ad_dal.list_campaigns_paged(session, page=current_page, page_size=PAGE_SIZE)
        text = overview + "\n\n" + _("admin_ads_header")
        from bot.keyboards.inline.admin_keyboards import get_ads_list_keyboard
        reply_markup = get_ads_list_keyboard(i18n, current_lang, campaigns, current_page, total_pages)
    await callback.message.edit_text(text, reply_markup=reply_markup)
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/admin/ads.py: %s", exc)


@router.callback_query(F.data.startswith("admin_ads:page:"))
async def ads_list_pagination(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    try:
        page = int(callback.data.split(":")[2])
    except Exception:
        page = 0

    totals = await ad_dal.get_totals(session)
    overview = _("admin_ads_overview", revenue=f"{totals.get('revenue', 0.0):.2f}", cost=f"{totals.get('cost', 0.0):.2f}")
    total_count = await ad_dal.count_campaigns(session)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    campaigns = await ad_dal.list_campaigns_paged(session, page=page, page_size=PAGE_SIZE)
    text = overview + "\n\n" + _("admin_ads_header")
    from bot.keyboards.inline.admin_keyboards import get_ads_list_keyboard
    reply_markup = get_ads_list_keyboard(i18n, current_lang, campaigns, page, total_pages)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        await callback.answer()
    except Exception as e:
        logging.error(f"Failed to paginate ads list: {e}")
        await callback.answer()


@router.callback_query(F.data.startswith("admin_ads:card:"))
async def show_ad_card(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    parts = callback.data.split(":")
    camp_id = int(parts[2])
    back_page = int(parts[3]) if len(parts) > 3 else 0

    camp = await ad_dal.get_campaign_by_id(session, camp_id)
    if not camp:
        await callback.answer(_("admin_promo_not_found"), show_alert=True)
        return
    try:
        stats = await ad_dal.get_campaign_stats(session, camp_id)
    except Exception:
        stats = {"starts": 0, "trials": 0, "payers": 0, "revenue": 0.0}

    text = _(
        "admin_ads_card",
        id=camp.ad_campaign_id,
        source=camp.source,
        start_param=camp.start_param,
        cost=f"{camp.cost:.2f}",
        active=_("csv_yes") if camp.is_active else _("csv_no"),
        starts=stats["starts"],
        trials=stats["trials"],
        payers=stats["payers"],
        revenue=f"{stats['revenue']:.2f}",
    )

    from bot.keyboards.inline.admin_keyboards import get_ad_card_keyboard
    reply_markup = get_ad_card_keyboard(i18n, current_lang, camp.ad_campaign_id, back_page)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logging.error(f"Failed to show ad card: {e}")
        await callback.answer()


@router.callback_query(F.data.startswith("admin_ads:delete:"))
async def ads_delete_prompt(callback: types.CallbackQuery, settings: Settings, i18n_data: dict):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    try:
        _, _, camp_id_str, back_page_str = callback.data.split(":", 3)
        camp_id = int(camp_id_str)
        back_page = int(back_page_str)
    except Exception:
        await callback.answer(i18n.gettext(current_lang, "error_try_again"), show_alert=True)
        return

    from bot.keyboards.inline.admin_keyboards import get_confirmation_keyboard
    confirm_text = i18n.gettext(current_lang, "admin_ads_delete_confirm", id=camp_id)
    kb = get_confirmation_keyboard(
        yes_callback_data=f"admin_ads:delete_confirm:{camp_id}:{back_page}",
        no_callback_data=f"admin_ads:delete_cancel:{camp_id}:{back_page}",
        i18n_instance=i18n,
        lang=current_lang,
    )
    try:
        await callback.message.edit_text(confirm_text, reply_markup=kb)
        await callback.answer()
    except Exception:
        await callback.answer()


@router.callback_query(F.data.startswith("admin_ads:delete_cancel:"))
async def ads_delete_cancel(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    # Return to the ad card view
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    try:
        parts = callback.data.split(":", 3)
        camp_id = int(parts[2])
        back_page = int(parts[3])
    except Exception:
        await callback.answer(_("error_try_again"), show_alert=True)
        return

    camp = await ad_dal.get_campaign_by_id(session, camp_id)
    if not camp:
        await callback.answer(_("admin_ads_not_found"), show_alert=True)
        return
    try:
        stats = await ad_dal.get_campaign_stats(session, camp_id)
    except Exception:
        stats = {"starts": 0, "trials": 0, "payers": 0, "revenue": 0.0}
    text = _(
        "admin_ads_card",
        id=camp.ad_campaign_id,
        source=camp.source,
        start_param=camp.start_param,
        cost=f"{camp.cost:.2f}",
        active=_("csv_yes") if camp.is_active else _("csv_no"),
        starts=stats["starts"],
        trials=stats["trials"],
        payers=stats["payers"],
        revenue=f"{stats['revenue']:.2f}",
    )
    from bot.keyboards.inline.admin_keyboards import get_ad_card_keyboard
    reply_markup = get_ad_card_keyboard(i18n, current_lang, camp.ad_campaign_id, back_page)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
    except Exception:
        await callback.answer()


@router.callback_query(F.data.startswith("admin_ads:delete_confirm:"))
async def ads_delete_confirm(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    try:
        parts = callback.data.split(":", 3)
        camp_id = int(parts[2])
        back_page = int(parts[3])
    except Exception:
        await callback.answer(_("error_try_again"), show_alert=True)
        return

    existed = await ad_dal.delete_campaign(session, camp_id)
    if not existed:
        await callback.answer(_("admin_ads_not_found"), show_alert=True)
        return
    await session.commit()

    # After delete, show list page (may shift due to fewer items)
    totals = await ad_dal.get_totals(session)
    overview = _(
        "admin_ads_overview",
        revenue=f"{totals.get('revenue', 0.0):.2f}",
        cost=f"{totals.get('cost', 0.0):.2f}",
    )
    total_count = await ad_dal.count_campaigns(session)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(back_page, total_pages - 1))
    campaigns = await ad_dal.list_campaigns_paged(session, page=page, page_size=PAGE_SIZE)
    text = overview + "\n\n" + _("admin_ads_header")
    from bot.keyboards.inline.admin_keyboards import get_ads_list_keyboard
    reply_markup = get_ads_list_keyboard(i18n, current_lang, campaigns, page, total_pages)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        await callback.answer(_("admin_ads_deleted_success"), show_alert=True)
    except Exception:
        await callback.answer(_("admin_ads_deleted_success"), show_alert=True)
@router.callback_query(F.data == "admin_action:ads_create")
async def ads_create_start(callback: types.CallbackQuery, state: FSMContext, settings: Settings, i18n_data: dict):
    from bot.states.admin_states import AdminStates
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        await callback.answer("Language error.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_ad_source)
    await callback.message.edit_text(_("admin_ads_create_source_prompt"))
    try:
        await callback.answer()
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/admin/ads.py: %s", exc)


@router.message(
    StateFilter(
        AdminStates.waiting_for_ad_source,
        AdminStates.waiting_for_ad_start_param,
        AdminStates.waiting_for_ad_cost,
    ),
    F.text,
)
async def ads_create_flow(message: types.Message, state: FSMContext, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_state = await state.get_state()
    if current_state not in (
        AdminStates.waiting_for_ad_source.state,
        AdminStates.waiting_for_ad_start_param.state,
        AdminStates.waiting_for_ad_cost.state,
    ):
        return

    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if current_state == AdminStates.waiting_for_ad_source.state:
        source = message.text.strip()
        if not source or len(source) > 64:
            await message.answer(_("admin_ads_invalid_source"))
            return
        await state.update_data(ad_source=source)
        await state.set_state(AdminStates.waiting_for_ad_start_param)
        await message.answer(_("admin_ads_create_start_param_prompt"))
        return

    if current_state == AdminStates.waiting_for_ad_start_param.state:
        start_param = message.text.strip()
        # Allow alnum underscore dash only
        import re as _re
        if not _re.match(r"^[A-Za-z0-9_\-]{2,64}$", start_param):
            await message.answer(_("admin_ads_invalid_start_param"))
            return
        await state.update_data(ad_start_param=start_param)
        await state.set_state(AdminStates.waiting_for_ad_cost)
        await message.answer(_("admin_ads_create_cost_prompt"))
        return

    if current_state == AdminStates.waiting_for_ad_cost.state:
        text = message.text.replace(",", ".").strip()
        try:
            cost = float(text)
            if cost < 0 or cost > 1e8:
                raise ValueError()
        except Exception:
            await message.answer(_("admin_ads_invalid_cost"))
            return

        data = await state.get_data()
        try:
            campaign = await ad_dal.create_campaign(
                session,
                source=data.get("ad_source", "unknown"),
                start_param=data.get("ad_start_param", "NA"),
                cost=cost,
            )
            await session.commit()
        except ValueError as ve:
            await session.rollback()
            if str(ve) == "ad_campaign_start_param_exists":
                await message.answer(_("admin_ads_start_param_exists"))
            else:
                await message.answer(_("error_occurred_try_again"))
            return
        except Exception as e:
            await session.rollback()
            logging.error(f"Failed to create ad campaign: {e}", exc_info=True)
            await message.answer(_("error_occurred_try_again"))
            return

        await state.clear()
        _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key
        await message.answer(
            _(
                "admin_ads_created_success",
                id=campaign.ad_campaign_id,
                source=campaign.source,
                start_param=campaign.start_param,
                cost=f"{campaign.cost:.2f}",
            )
        )
        # Offer back to ads menu
        from bot.keyboards.inline.admin_keyboards import get_ads_menu_keyboard
        await message.answer(_("admin_ads_back_to_menu_hint"), reply_markup=get_ads_menu_keyboard(i18n, current_lang))


