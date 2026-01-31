import logging
from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from db.dal import promo_code_dal
from bot.states.admin_states import AdminStates
from bot.keyboards.inline.admin_keyboards import get_back_to_admin_panel_keyboard, get_admin_panel_keyboard
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from bot.middlewares.i18n import JsonI18n

router = Router(name="promo_create_router")


async def create_promo_prompt_handler(callback: types.CallbackQuery,
                                      state: FSMContext, i18n_data: dict,
                                      settings: Settings,
                                      session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error preparing promo creation.",
                              show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    # Step 0: Ask for promo type (bonus_days or discount)
    prompt_text = _(
        "admin_promo_step0_type"
    )

    # Create keyboard for type selection
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_("admin_promo_type_bonus_days"),
            callback_data="promo_type_select:bonus_days"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_promo_type_discount"),
            callback_data="promo_type_select:discount"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=_("admin_back_to_panel"),
            callback_data="admin_action:main"
        )
    )

    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML")
    except Exception as e:
        logging.warning(
            f"Could not edit message for promo type prompt: {e}. Sending new.")
        await callback.message.answer(
            prompt_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML")
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_promo_type_selection)


# Step 0: Process type selection
@router.callback_query(F.data.startswith("promo_type_select:"), StateFilter(AdminStates.waiting_for_promo_type_selection))
async def process_promo_type_selection(callback: types.CallbackQuery,
                                       state: FSMContext,
                                       i18n_data: dict,
                                       settings: Settings):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing type selection.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        promo_type = callback.data.split(":")[-1]  # "bonus_days" or "discount"
        await state.update_data(promo_type=promo_type)

        # Step 1: Ask for promo code
        prompt_text = _(
            "admin_promo_step1_code"
        )

        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
        await callback.answer()
        await state.set_state(AdminStates.waiting_for_promo_code)

    except Exception as e:
        logging.error(f"Error processing promo type selection: {e}")
        await callback.message.answer(_("error_occurred_try_again"))
        await callback.answer()


# Step 1: Process promo code
@router.message(AdminStates.waiting_for_promo_code, F.text)
async def process_promo_code_handler(message: types.Message,
                                    state: FSMContext,
                                    i18n_data: dict,
                                    settings: Settings,
                                    session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await message.reply("Language service error.")
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        code_str = message.text.strip().upper()
        if not (3 <= len(code_str) <= 30 and code_str.isalnum()):
            await message.answer(_(
                "admin_promo_invalid_code_format"
            ))
            return
        
        # Check if code already exists
        existing_promo = await promo_code_dal.get_promo_code_by_code(session, code_str)
        if existing_promo:
            await message.answer(_(
                "admin_promo_code_already_exists"
            ))
            return
        
        await state.update_data(promo_code=code_str)

        # Get promo type from state
        data = await state.get_data()
        promo_type = data.get("promo_type", "bonus_days")

        # Step 2: Ask for bonus days OR discount percentage based on type
        if promo_type == "discount":
            prompt_text = _(
                "admin_promo_step2_discount_percentage",
                code=code_str
            )
            next_state = AdminStates.waiting_for_promo_discount_percentage
        else:
            prompt_text = _(
                "admin_promo_step2_bonus_days",
                code=code_str
            )
            next_state = AdminStates.waiting_for_promo_bonus_days

        await message.answer(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
        await state.set_state(next_state)
        
    except Exception as e:
        logging.error(f"Error processing promo code: {e}")
        await message.answer(_("error_occurred_try_again"))


# Step 2: Process bonus days
@router.message(AdminStates.waiting_for_promo_bonus_days, F.text)
async def process_promo_bonus_days_handler(message: types.Message,
                                          state: FSMContext,
                                          i18n_data: dict,
                                          settings: Settings):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await message.reply("Language service error.")
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        bonus_days = int(message.text.strip())
        if not (1 <= bonus_days <= 365):
            await message.answer(_(
                "admin_promo_invalid_bonus_days"
            ))
            return

        await state.update_data(bonus_days=bonus_days)

        # Step 3: Ask for max activations
        data = await state.get_data()
        prompt_text = _(
            "admin_promo_step3_max_activations",
            code=data.get("promo_code"),
            bonus_days=bonus_days
        )

        await message.answer(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_for_promo_max_activations)

    except ValueError:
        await message.answer(_(
            "admin_promo_invalid_number"
        ))
    except Exception as e:
        logging.error(f"Error processing promo bonus days: {e}")
        await message.answer(_("error_occurred_try_again"))


# Step 2: Process discount percentage
@router.message(AdminStates.waiting_for_promo_discount_percentage, F.text)
async def process_promo_discount_percentage_handler(message: types.Message,
                                                    state: FSMContext,
                                                    i18n_data: dict,
                                                    settings: Settings):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await message.reply("Language service error.")
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        discount_percentage = int(message.text.strip())
        if not (1 <= discount_percentage <= 100):
            await message.answer(
                "❌ Discount percentage must be between 1 and 100."
            )
            return

        await state.update_data(discount_percentage=discount_percentage)

        # Step 3: Ask for max activations
        data = await state.get_data()
        prompt_text = _(
            "admin_promo_step3_max_activations_discount",
            code=data.get("promo_code"),
            discount_percentage=discount_percentage
        )

        await message.answer(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_for_promo_max_activations)

    except ValueError:
        await message.answer(_(
            "admin_promo_invalid_number"
        ))
    except Exception as e:
        logging.error(f"Error processing discount percentage: {e}")
        await message.answer(_("error_occurred_try_again"))


# Step 3: Process max activations
@router.message(AdminStates.waiting_for_promo_max_activations, F.text)
async def process_promo_max_activations_handler(message: types.Message,
                                               state: FSMContext,
                                               i18n_data: dict,
                                               settings: Settings):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await message.reply("Language service error.")
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        max_activations = int(message.text.strip())
        if not (1 <= max_activations <= 10000):
            await message.answer(_(
                "admin_promo_invalid_max_activations"
            ))
            return
        
        await state.update_data(max_activations=max_activations)

        # Step 4: Ask for validity
        data = await state.get_data()
        promo_type = data.get("promo_type", "bonus_days")

        if promo_type == "discount":
            prompt_text = _(
                "admin_promo_step4_validity_discount",
                code=data.get("promo_code"),
                discount_percentage=data.get("discount_percentage"),
                max_activations=max_activations
            )
        else:
            prompt_text = _(
                "admin_promo_step4_validity",
                code=data.get("promo_code"),
                bonus_days=data.get("bonus_days"),
                max_activations=max_activations
            )
        
        # Create keyboard for validity options
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=_("admin_promo_unlimited_validity"),
                callback_data="promo_unlimited_validity"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=_("admin_promo_set_validity_days"),
                callback_data="promo_set_validity"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=_("admin_back_to_panel"),
                callback_data="admin_action:main"
            )
        )
        
        await message.answer(
            prompt_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_for_promo_validity_days)
        
    except ValueError:
        await message.answer(_(
            "admin_promo_invalid_number"
        ))
    except Exception as e:
        logging.error(f"Error processing promo max activations: {e}")
        await message.answer(_("error_occurred_try_again"))


# Step 4: Handle unlimited validity
@router.callback_query(F.data == "promo_unlimited_validity", StateFilter(AdminStates.waiting_for_promo_validity_days))
async def process_promo_unlimited_validity(callback: types.CallbackQuery,
                                          state: FSMContext,
                                          i18n_data: dict,
                                          settings: Settings,
                                          session: AsyncSession):
    await state.update_data(validity_days=None)
    await create_promo_code_final(callback, state, i18n_data, settings, session)


# Step 4: Handle set validity
@router.callback_query(F.data == "promo_set_validity", StateFilter(AdminStates.waiting_for_promo_validity_days))
async def process_promo_set_validity(callback: types.CallbackQuery,
                                    state: FSMContext,
                                    i18n_data: dict,
                                    settings: Settings):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing validity.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    data = await state.get_data()
    promo_type = data.get("promo_type", "bonus_days")

    # Display the correct text based on promo type
    if promo_type == "discount":
        value_info = f"{data.get('discount_percentage')}%"
    else:
        value_info = f"{data.get('bonus_days')} дней"

    prompt_text = f"⏰ Введите количество дней действия промокода (1-365):\n\nКод: <b>{data.get('promo_code')}</b>\n{'Скидка' if promo_type == 'discount' else 'Бонус'}: <b>{value_info}</b>\nМакс. активаций: <b>{data.get('max_activations')}</b>"
    
    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            prompt_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
    await callback.answer()


# Step 4: Process validity days
@router.message(AdminStates.waiting_for_promo_validity_days, F.text)
async def process_promo_validity_days_handler(message: types.Message,
                                             state: FSMContext,
                                             i18n_data: dict,
                                             settings: Settings,
                                             session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await message.reply("Language service error.")
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        validity_days = int(message.text.strip())
        if not (1 <= validity_days <= 365):
            await message.answer(_(
                "admin_promo_invalid_validity_days"
            ))
            return
        
        await state.update_data(validity_days=validity_days)
        await create_promo_code_final(message, state, i18n_data, settings, session)
        
    except ValueError:
        await message.answer(_(
            "admin_promo_invalid_number"
        ))
    except Exception as e:
        logging.error(f"Error processing promo validity days: {e}")
        await message.answer(_("error_occurred_try_again"))


async def create_promo_code_final(callback_or_message,
                                 state: FSMContext,
                                 i18n_data: dict,
                                 settings: Settings,
                                 session: AsyncSession):
    """Final step - create the promo code in database"""
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        data = await state.get_data()
        promo_type = data.get("promo_type", "bonus_days")

        # Prepare promo code data
        promo_data = {
            "code": data["promo_code"],
            "promo_type": promo_type,
            "max_activations": data["max_activations"],
            "current_activations": 0,
            "is_active": True,
            "created_by_admin_id": callback_or_message.from_user.id,
            "created_at": datetime.now(timezone.utc)
        }

        # Set type-specific fields
        if promo_type == "discount":
            promo_data["discount_percentage"] = data["discount_percentage"]
            promo_data["bonus_days"] = None
        else:
            promo_data["bonus_days"] = data["bonus_days"]
            promo_data["discount_percentage"] = None

        # Set validity
        if data.get("validity_days"):
            promo_data["valid_until"] = datetime.now(timezone.utc) + timedelta(days=data["validity_days"])
        else:
            promo_data["valid_until"] = None

        # Create promo code
        created_promo = await promo_code_dal.create_promo_code(session, promo_data)
        await session.commit()

        # Log successful creation
        logging.info(f"Promo code '{data['promo_code']}' ({promo_type}) created with ID {created_promo.promo_code_id}")

        # Success message
        valid_until_str = _("admin_promo_unlimited") if not data.get("validity_days") else f"{data['validity_days']} дней"

        # Format success message based on type
        if promo_type == "discount":
            success_text = _(
                "admin_promo_created_success_discount",
                code=data["promo_code"],
                discount_percentage=data['discount_percentage'],
                max_activations=data["max_activations"],
                valid_until_str=valid_until_str
            )
        else:
            success_text = _(
                "admin_promo_created_success",
                code=data["promo_code"],
                bonus_days=data['bonus_days'],
                max_activations=data["max_activations"],
                valid_until_str=valid_until_str
            )
        
        if hasattr(callback_or_message, 'message'):  # CallbackQuery
            try:
                await callback_or_message.message.edit_text(
                    success_text,
                    reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
                    parse_mode="HTML"
                )
            except Exception:
                await callback_or_message.message.answer(
                    success_text,
                    reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
                    parse_mode="HTML"
                )
            await callback_or_message.answer()
        else:  # Message
            await callback_or_message.answer(
                success_text,
                reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
                parse_mode="HTML"
            )
        
        await state.clear()
        
    except Exception as e:
        logging.error(f"Error creating promo code: {e}")
        error_text = _("error_occurred_try_again")
        
        if hasattr(callback_or_message, 'message'):  # CallbackQuery
            await callback_or_message.message.answer(error_text)
            await callback_or_message.answer()
        else:  # Message
            await callback_or_message.answer(error_text)
        
        await state.clear()


# Cancel promo creation
@router.callback_query(
    F.data == "admin_action:main",
    StateFilter(
        AdminStates.waiting_for_promo_type_selection,
        AdminStates.waiting_for_promo_code,
        AdminStates.waiting_for_promo_bonus_days,
        AdminStates.waiting_for_promo_discount_percentage,
        AdminStates.waiting_for_promo_max_activations,
        AdminStates.waiting_for_promo_validity_days,
    ),
)
async def cancel_promo_creation_state_to_menu(callback: types.CallbackQuery,
                                              state: FSMContext,
                                              settings: Settings,
                                              i18n_data: dict,
                                              session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error cancelling.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        await callback.message.edit_text(
            _(key="admin_panel_title"),
            reply_markup=get_admin_panel_keyboard(i18n, current_lang, settings)
        )
    except Exception:
        await callback.message.answer(
            _(key="admin_panel_title"),
            reply_markup=get_admin_panel_keyboard(i18n, current_lang, settings)
        )
    
    await callback.answer(_("admin_promo_creation_cancelled"))
    await state.clear()
