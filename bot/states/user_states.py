from aiogram.fsm.state import State, StatesGroup


class UserPromoStates(StatesGroup):
    waiting_for_promo_code = State()


class UserReferralWithdrawStates(StatesGroup):
    waiting_for_withdraw_amount = State()
    waiting_for_withdraw_contact = State()
