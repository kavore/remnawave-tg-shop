import logging
import csv
import io
from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings, get_settings
from db.dal import promo_code_dal
from db.models import PromoCode, PromoCodeActivation
from bot.states.admin_states import AdminStates
from bot.keyboards.inline.admin_keyboards import get_back_to_admin_panel_keyboard, get_admin_panel_keyboard
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from bot.middlewares.i18n import JsonI18n

router = Router(name="promo_manage_router")


def get_promo_status_emoji_and_text(promo: PromoCode, i18n: JsonI18n, current_lang: str):
    """Determine promo code status and return emoji + text"""
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    
    if promo.valid_until and promo.valid_until < datetime.now(timezone.utc):
        return "⏰", _("admin_promo_status_expired")
    elif promo.current_activations >= promo.max_activations:
        return "🔄", _("admin_promo_status_used_up")
    elif promo.is_active:
        return "✅", _("admin_promo_status_active")
    else:
        return "🚫", _("admin_promo_status_inactive")


async def get_promo_detail_text_and_keyboard(promo_id: int, session: AsyncSession, i18n: JsonI18n, current_lang: str):
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    promo = await promo_code_dal.get_promo_code_by_id(session, promo_id)
    if not promo:
        return None, None

    status_emoji, status = get_promo_status_emoji_and_text(promo, i18n, current_lang)

    validity = _("admin_promo_valid_indefinitely")
    if promo.valid_until:
        validity = promo.valid_until.strftime("%d.%m.%Y %H:%M")

    created = promo.created_at.strftime("%d.%m.%Y %H:%M") if promo.created_at else "N/A"

    # Determine promo type and value to display
    promo_type = getattr(promo, "promo_type", "bonus_days")
    if promo_type == "discount":
        type_name = _("admin_promo_type_discount")
        value_line = _("admin_promo_card_discount_percentage", percentage=promo.discount_percentage)
    else:
        type_name = _("admin_promo_type_bonus_days")
        value_line = _("admin_promo_card_bonus_days", days=promo.bonus_days)

    text = "\n".join([
        _("admin_promo_card_title", code=promo.code),
        _("admin_promo_card_type", type=type_name),
        value_line,
        _("admin_promo_card_activations", current=promo.current_activations, max=promo.max_activations),
        _("admin_promo_card_validity", validity=validity),
        _("admin_promo_card_status", status=status),
        _("admin_promo_card_created", created=created),
        _("admin_promo_card_created_by", creator=promo.created_by_admin_id)
    ])

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_("admin_promo_edit_button"), callback_data=f"promo_edit_select:{promo_id}"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_toggle_status_button"), callback_data=f"promo_toggle:{promo_id}"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_view_activations_button"), callback_data=f"promo_activations:{promo_id}:0"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_delete_button"), callback_data=f"promo_delete:{promo_id}"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_back_to_list_button"), callback_data="admin_action:promo_management"))

    return text, builder.as_markup()


async def view_promo_codes_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing request.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    promo_models = await promo_code_dal.get_all_active_promo_codes(session, limit=20, offset=0)
    if not promo_models:
        text = f"{_('admin_active_promos_list_header')}\n\n{_('admin_no_active_promos')}"
    else:
        promo_lines = [_("admin_active_promos_list_header"), ""]
        for p in promo_models:
            status_emoji = get_promo_status_emoji_and_text(p, i18n, current_lang)[0]
            promo_type = getattr(p, "promo_type", "bonus_days")
            if promo_type == "discount":
                value_display = f"💰 {p.discount_percentage}%"
            else:
                value_display = f"🎁 {p.bonus_days}д"
            validity_display = p.valid_until.strftime('%d.%m.%Y') if p.valid_until else _('admin_promo_valid_indefinitely')
            promo_lines.append(
                f"{status_emoji} <code>{p.code}</code> | {value_display} | 📊 {p.current_activations}/{p.max_activations} | ⏰ {validity_display}"
            )
        text = "\n".join(promo_lines)
    
    await callback.message.edit_text(text, reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n), parse_mode="HTML")
    await callback.answer()


async def promo_management_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession, page: int = 0):
    current_lang = i18n_data.get("current_language", "ru")
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing request.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    page_size = 10  # Количество промокодов на странице
    offset = page * page_size
    
    # Получаем общее количество промокодов
    total_count = await promo_code_dal.get_promo_codes_count(session)
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    
    promo_models = await promo_code_dal.get_all_promo_codes_with_details(session, limit=page_size, offset=offset)
    if not promo_models and page == 0:
        await callback.message.edit_text(_("admin_promo_management_empty"), reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n), parse_mode="HTML")
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for promo in promo_models:
        status_emoji, status_text = get_promo_status_emoji_and_text(promo, i18n, current_lang)
        button_text = f"{status_emoji} {promo.code} ({promo.current_activations}/{promo.max_activations})"
        builder.row(InlineKeyboardButton(text=button_text, callback_data=f"promo_detail:{promo.promo_code_id}"))
    
    # Добавляем кнопки пагинации если есть больше одной страницы
    if total_pages > 1:
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(InlineKeyboardButton(text=_("prev_page_button"), callback_data=f"promo_management:{page-1}"))
        if page < total_pages - 1:
            pagination_buttons.append(InlineKeyboardButton(text=_("next_page_button"), callback_data=f"promo_management:{page+1}"))
        
        if pagination_buttons:
            builder.row(*pagination_buttons)
    
    # Добавляем кнопки экспорта и возврата
    builder.row(InlineKeyboardButton(text=_("admin_promo_export_csv_button"), callback_data="promo_export_all"))
    builder.row(InlineKeyboardButton(text=_("back_to_admin_panel_button"), callback_data="admin_action:main"))
    
    # Формируем заголовок с информацией о страницах
    title = _("admin_promo_management_title")
    if total_pages > 1:
        title += f"\n{_('admin_promo_list_page_info', current=page+1, total=total_pages, count=total_count)}"
    
    await callback.message.edit_text(title, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("promo_management:"))
async def promo_management_pagination_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession):
    try:
        page = int(callback.data.split(":")[1])
        await promo_management_handler(callback, i18n_data, settings, session, page)
    except (ValueError, IndexError):
        await callback.answer("Error processing pagination.", show_alert=True)


@router.callback_query(F.data.startswith("promo_detail:"))
async def promo_detail_handler(callback: types.CallbackQuery, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        await callback.answer("Error processing request.", show_alert=True)
        return
    
    try:
        promo_id = int(callback.data.split(":")[1])
        text, keyboard = await get_promo_detail_text_and_keyboard(promo_id, session, i18n, current_lang)
        if text:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.answer(i18n.gettext(current_lang, "admin_promo_not_found"), show_alert=True)
    except (ValueError, IndexError):
        await callback.answer(i18n.gettext(current_lang, "admin_promo_not_found"), show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("promo_toggle:"))
async def promo_toggle_handler(callback: types.CallbackQuery, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return await callback.answer("Language service error.", show_alert=True)
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    
    try:
        promo_id = int(callback.data.split(":")[1])
        promo = await promo_code_dal.get_promo_code_by_id(session, promo_id)
        if not promo:
            return await callback.answer(_("admin_promo_not_found"), show_alert=True)

        new_status = not promo.is_active
        if await promo_code_dal.update_promo_code(session, promo_id, {"is_active": new_status}):
            await session.commit()
            status_text = _("admin_promo_status_activated") if new_status else _("admin_promo_status_deactivated")
            await callback.answer(_("admin_promo_toggle_success", code=promo.code, status=status_text))
            
            text, keyboard = await get_promo_detail_text_and_keyboard(promo_id, session, i18n, current_lang)
            if text:
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.answer(_("error_occurred_try_again"), show_alert=True)
    except (ValueError, IndexError):
        await callback.answer(_("admin_promo_not_found"), show_alert=True)


@router.callback_query(F.data.startswith("promo_activations:"))
async def promo_activations_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return await callback.answer("Error processing request.", show_alert=True)
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        parts = callback.data.split(":")
        promo_id = int(parts[1])
        page = int(parts[2])
        page_size = settings.LOGS_PAGE_SIZE

        promo = await promo_code_dal.get_promo_code_by_id(session, promo_id)
        if not promo:
            return await callback.answer(_("admin_promo_not_found"), show_alert=True)

        total_activations = await promo_code_dal.count_promo_activations_by_code_id(session, promo_id)
        activations = await promo_code_dal.get_promo_activations_by_code_id(session, promo_id, limit=page_size, offset=page * page_size)
        
        builder = InlineKeyboardBuilder()
        if not activations:
            text = _("admin_promo_no_activations", code=promo.code)
        else:
            text = _("admin_promo_activations_header", code=promo.code) + "\n\n"
            text += "\n".join([_("admin_promo_activation_item", user_id=a.user_id, date=a.activated_at.strftime("%d.%m.%Y %H:%M")) for a in activations])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"promo_activations:{promo_id}:{page-1}"))
        if (page + 1) * page_size < total_activations:
            nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"promo_activations:{promo_id}:{page+1}"))
        if nav_buttons:
            builder.row(*nav_buttons)

        builder.row(InlineKeyboardButton(text=_("admin_promo_export_csv_button"), callback_data=f"promo_export:{promo_id}"))
        builder.row(InlineKeyboardButton(text=_("admin_promo_back_to_detail_button"), callback_data=f"promo_detail:{promo_id}"))

        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except (ValueError, IndexError):
        await callback.answer(_("admin_promo_not_found"), show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("promo_export:"))
async def promo_export_activations_handler(callback: types.CallbackQuery, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return await callback.answer("Error processing request.", show_alert=True)
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    export_lang = "en"

    try:
        promo_id = int(callback.data.split(":")[1])
        promo = await promo_code_dal.get_promo_code_by_id(session, promo_id)
        if not promo:
            return await callback.answer(_("admin_promo_not_found"), show_alert=True)

        activations = await promo_code_dal.get_promo_activations_by_code_id(session, promo_id)
        if not activations:
            return await callback.answer(_("admin_promo_no_activations", code=promo.code), show_alert=True)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Activation Date"])
        for act in activations:
            writer.writerow([act.user_id, act.activated_at.strftime("%Y-%m-%d %H:%M:%S")])
        
        output.seek(0)
        file = types.BufferedInputFile(output.getvalue().encode('utf-8'), filename=f"promo_{promo.code}_activations.csv")
        # Force English caption for exports
        await callback.message.answer_document(
            file,
            caption=i18n.gettext(export_lang, "admin_promo_export_caption", code=promo.code)
        )

    except (ValueError, IndexError):
        await callback.answer(_("admin_promo_not_found"), show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "promo_export_all")
async def promo_export_all_handler(callback: types.CallbackQuery, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return await callback.answer("Error processing request.", show_alert=True)
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    export_lang = "en"

    try:
        await callback.answer(i18n.gettext(export_lang, "admin_promo_export_all_generating"), show_alert=True)
        
        # Получаем все промокоды
        all_promos = await promo_code_dal.get_all_promo_codes_with_details(session, limit=10000, offset=0)
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # CSV headers (forced to English)
        writer.writerow([
            i18n.gettext(export_lang, "admin_promo_csv_code"),
            "Type",
            i18n.gettext(export_lang, "admin_promo_csv_bonus_days"),
            "Discount %",
            i18n.gettext(export_lang, "admin_promo_csv_max_activations"),
            i18n.gettext(export_lang, "admin_promo_csv_current_activations"),
            i18n.gettext(export_lang, "admin_promo_csv_status"),
            i18n.gettext(export_lang, "admin_promo_csv_is_active"),
            i18n.gettext(export_lang, "admin_promo_csv_valid_until"),
            i18n.gettext(export_lang, "admin_promo_csv_created_at"),
            i18n.gettext(export_lang, "admin_promo_csv_created_by_admin_id"),
        ])

        for promo in all_promos:
            # Определяем статус
            status_emoji, status_text = get_promo_status_emoji_and_text(promo, i18n, export_lang)

            # Determine promo type and values
            promo_type = getattr(promo, "promo_type", "bonus_days")
            bonus_days_val = promo.bonus_days if promo_type == "bonus_days" else "N/A"
            discount_val = promo.discount_percentage if promo_type == "discount" else "N/A"

            # Формируем данные для CSV
            row = [
                promo.code,
                promo_type,
                bonus_days_val,
                discount_val,
                promo.max_activations,
                promo.current_activations,
                status_text,
                i18n.gettext(export_lang, "csv_yes") if promo.is_active else i18n.gettext(export_lang, "csv_no"),
                promo.valid_until.strftime("%Y-%m-%d %H:%M:%S") if promo.valid_until else i18n.gettext(export_lang, "admin_promo_valid_indefinitely"),
                promo.created_at.strftime("%Y-%m-%d %H:%M:%S") if promo.created_at else "N/A",
                promo.created_by_admin_id or "N/A"
            ]
            writer.writerow(row)
        
        output.seek(0)
        
        # Создаем файл для отправки
        filename = f"promo_codes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file = types.BufferedInputFile(
            output.getvalue().encode('utf-8-sig'),  # BOM для корректного отображения в Excel
            filename=filename
        )
        
        caption = i18n.gettext(export_lang, "admin_promo_export_all_caption", count=len(all_promos))
        await callback.message.answer_document(file, caption=caption)
        
    except Exception as e:
        await callback.answer(f"❌ Export error: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("promo_delete:"))
async def promo_delete_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return await callback.answer("Language service error.", show_alert=True)
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        promo_id = int(callback.data.split(":")[1])
        promo = await promo_code_dal.delete_promo_code(session, promo_id)
        if promo:
            await session.commit()
            await callback.answer(_("admin_promo_deleted_success", code=promo.code), show_alert=True)
            await promo_management_handler(callback, i18n_data, settings, session, 0)
        else:
            await callback.answer(_("admin_promo_not_found"), show_alert=True)
    except (ValueError, IndexError):
        await callback.answer(_("admin_promo_not_found"), show_alert=True)


# --- Promo Edit Handlers ---
@router.callback_query(F.data.startswith("promo_edit_select:"))
async def promo_edit_select_handler(callback: types.CallbackQuery, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang:
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    promo_id = int(callback.data.split(":")[1])

    # Get promo to check type
    promo = await promo_code_dal.get_promo_code_by_id(session, promo_id)
    if not promo:
        await callback.answer(_("admin_promo_not_found"), show_alert=True)
        return

    promo_type = getattr(promo, "promo_type", "bonus_days")

    builder = InlineKeyboardBuilder()
    # Show appropriate edit option based on type
    if promo_type == "discount":
        builder.row(InlineKeyboardButton(text=_("admin_promo_edit_discount_percentage"), callback_data=f"promo_edit_field:discount_percentage:{promo_id}"))
    else:
        builder.row(InlineKeyboardButton(text=_("admin_promo_edit_bonus_days"), callback_data=f"promo_edit_field:bonus_days:{promo_id}"))

    builder.row(InlineKeyboardButton(text=_("admin_promo_edit_max_activations"), callback_data=f"promo_edit_field:max_activations:{promo_id}"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_edit_validity"), callback_data=f"promo_edit_field:valid_until:{promo_id}"))
    builder.row(InlineKeyboardButton(text=_("admin_promo_back_to_detail_button"), callback_data=f"promo_detail:{promo_id}"))

    await callback.message.edit_text(_("admin_promo_edit_select_field"), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("promo_edit_field:"))
async def promo_edit_field_handler(callback: types.CallbackQuery, state: FSMContext, i18n_data: dict, session: AsyncSession):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not callback.message or not current_lang: return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    
    action, field, promo_id_str = callback.data.split(":")
    await state.update_data(promo_id=int(promo_id_str), field_to_edit=field)

    prompts = {
        "bonus_days": "admin_promo_prompt_bonus_days",
        "discount_percentage": "admin_promo_prompt_discount_percentage",
        "max_activations": "admin_promo_prompt_max_activations",
        "valid_until": "admin_promo_prompt_validity_days"
    }

    prompt_key = prompts.get(field, "error_occurred_try_again")
    prompt_text = _(prompt_key)

    await state.set_state(AdminStates.waiting_for_promo_edit_details)
    await callback.message.edit_text(prompt_text)
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_promo_edit_details))
async def process_promo_edit_details(message: types.Message, state: FSMContext, session: AsyncSession, i18n_data: dict):
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    current_lang = i18n_data.get("current_language")
    if not i18n or not message or not current_lang: return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    
    data = await state.get_data()
    promo_id = data.get("promo_id")
    field = data.get("field_to_edit")
    
    try:
        value = message.text
        update_data = {}

        if field == "bonus_days":
            update_data["bonus_days"] = int(value)
        elif field == "discount_percentage":
            discount_pct = int(value)
            if not (1 <= discount_pct <= 100):
                await message.answer("❌ Discount percentage must be between 1 and 100.")
                return
            update_data["discount_percentage"] = discount_pct
        elif field == "max_activations":
            update_data["max_activations"] = int(value)
        elif field == "valid_until":
            if value.lower() in ['0', 'вечно', 'бессрочно', 'indefinite']:
                 update_data["valid_until"] = None
            else:
                days = int(value)
                update_data["valid_until"] = datetime.now(timezone.utc) + timedelta(days=days)

        if await promo_code_dal.update_promo_code(session, promo_id, update_data):
            await session.commit()
            await message.answer(_("admin_promo_edit_success"))

            # Reset state and show updated details
            await state.clear()
            text, keyboard = await get_promo_detail_text_and_keyboard(promo_id, session, i18n, current_lang)
            if text:
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.answer(_("error_occurred_try_again"))
            await state.clear()

    except (ValueError, TypeError):
        await message.answer(_("admin_promo_invalid_input"))
        # Don't clear state, let them try again


async def manage_promo_codes_handler(callback: types.CallbackQuery, i18n_data: dict, settings: Settings, session: AsyncSession):
    await promo_management_handler(callback, i18n_data, settings, session)
