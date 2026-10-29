import asyncio
import logging
import os
import random
import string
import aiohttp
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from contextlib import suppress
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineQueryResultArticle,
    InputTextMessageContent,
    WebAppInfo,
    MenuButtonWebApp,
    TelegramObject
)

CANDIDATE_TOKENS = [
    "9170214461:AAGQUq9pYsaI9yZvIqZdftU5ioayoUvLDxY",
    "8761642344:AAGhlq9WXNCwbRL5pMU2JoMvDfDGReOoNtI",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPORT_URL = "https://t.me/ssupppoortt"
WEBAPP_URL = "https://zxcfid.github.io/spacex-app/"

COINS = [
    ("5406841020769936275", "Tether", "USDT"),
    ("5318901904686754959", "Gram", "GRAM"),
    ("5407016676342401484", "Solana", "SOL"),
    ("5406978786140918829", "TRON", "TRX"),
    ("5409133571233319295", "Bitcoin", "BTC"),
    ("5406930321729948822", "Ethereum", "ETH"),
    ("5406581441536495663", "Dogecoin", "DOGE"),
    ("5407128573125366746", "Litecoin", "LTC"),
    ("5406671889252781489", "Binance Coin", "BNB"),
    ("5406575600380974539", "USD Coin", "USDC"),
    ("5407080001340215945", "Tether Gold", "XAUT"),
]

MARKET_RATES = {
    "USDT": 1.00,
    "GRAM": 0.042,
    "SOL": 215.40,
    "TRX": 0.195,
    "BTC": 98450.00,
    "ETH": 3420.00,
    "DOGE": 0.24,
    "LTC": 94.50,
    "BNB": 690.00,
    "USDC": 1.00,
    "XAUT": 2890.00,
}

VALID_TICKERS = {coin[2] for coin in COINS}

ADMINS = set()
FROZEN_USERS = set()  # Заблокированные пользователи (/frez)
USER_LANGUAGES = {}
USER_BALANCES = {}
CHECKS = {}

BANNER_FILE_ID = None
CHECK_PHOTO_FILE_ID = None
BOT_USERNAME = "spacexcrypto_bot"


# Middleware для блокировки замороженных аккаунтов (/frez)
class FreezeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user and user.id in FROZEN_USERS and user.id not in ADMINS:
            lang = USER_LANGUAGES.get(user.id, "ru")
            blocked_msg = {
                "ru": "⛔️ Ваш аккаунт заморожен администратором.",
                "en": "⛔️ Your account has been frozen by an administrator.",
                "zh": "⛔️ 您的账户已被管理员冻结。"
            }.get(lang, "⛔️ Ваш аккаунт заморожен администратором.")

            if isinstance(event, CallbackQuery):
                await event.answer(blocked_msg, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(blocked_msg)
            return
        return await handler(event, data)


async def update_market_rates():
    url = "https://api.bybit.com/v5/market/tickers?category=spot"
    with suppress(Exception):
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                data = await resp.json()
                if data.get("retCode") == 0:
                    for item in data["result"]["list"]:
                        symbol = item.get("symbol", "")
                        if symbol.endswith("USDT"):
                            ticker = symbol[:-4]
                            if ticker in MARKET_RATES:
                                MARKET_RATES[ticker] = float(item["lastPrice"])


def find_image_file(base_name: str) -> str | None:
    for ext in [".jpg", ".jpeg", ".png", ".jpg.jpg", ".png.png", ""]:
        full_path = os.path.join(BASE_DIR, f"{base_name}{ext}")
        if os.path.isfile(full_path):
            return full_path
    return None


def generate_check_code(length: int = 12) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=length))
        if code not in CHECKS:
            return code


def format_amount(val: float | Decimal) -> str:
    """Форматирует любую сумму без лимитов и без научной нотации (1e+...)."""
    try:
        d = Decimal(str(val))
        s = format(d.quantize(Decimal("0.000001")), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    except Exception:
        return str(val)


def ensure_user_balance(user_id: int):
    if user_id not in USER_BALANCES:
        USER_BALANCES[user_id] = {coin[2]: 0.0 for coin in COINS}


def get_user_balance(user_id: int, ticker: str) -> float:
    ensure_user_balance(user_id)
    return USER_BALANCES[user_id].get(ticker, 0.0)


def build_webapp_url(user_id: int, lang: str) -> str:
    ensure_user_balance(user_id)
    params = {"lang": lang}
    for ticker, bal in USER_BALANCES[user_id].items():
        if bal > 0:
            params[ticker] = format_amount(bal)
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}{urlencode(params)}"


async def update_chat_menu_button(bot: Bot, user_id: int, lang: str):
    btn_titles = {"ru": "📈 Торговля", "en": "📈 Trade", "zh": "📈 交易"}
    with suppress(Exception):
        await bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(
                text=btn_titles.get(lang, "📈 Торговля"),
                web_app=WebAppInfo(url=build_webapp_url(user_id, lang))
            )
        )


async def safe_delete(message: Message):
    with suppress(Exception):
        await message.delete()


class ExchangeSG(StatesGroup):
    waiting_for_amount = State()


class CheckSG(StatesGroup):
    waiting_for_amount = State()


class WithdrawSG(StatesGroup):
    waiting_for_address = State()
    waiting_for_amount = State()


TRANSLATIONS = {
    "ru": {
        "caption": (
            "<b>Мультивалютный криптокошелёк.</b>\n\n"
            "Покупайте, продавайте, храните, отправляйте криптовалюту "
            "и платите ею, когда хотите."
        ),
        "wallet_title": "<b>Кошелёк:</b>",
        "deposit_btn": "➕ Пополнить",
        "withdraw_btn": "📤 Вывести",
        "deposit_choose": "Выберите способ пополнения:",
        "deposit_crypto_btn": "💎 Crypto",
        "deposit_rub_btn": "💵 Рубли",
        "deposit_crypto_info": "Выберите криптовалюту или обратитесь в поддержку для получения адреса пополнения.",
        "deposit_rub_info": "Для пополнения баланса рублями обратитесь в поддержку.",
        "withdraw_select_coin": "Выберите валюту для вывода:",
        "withdraw_enter_address": "Введите адрес кошелька (или реквизиты карты) для вывода <b>{coin}</b>:",
        "withdraw_enter_amount": "Введите сумму <b>{coin}</b> для вывода:",
        "withdraw_success": (
            "✅ Заявка на вывод средств успешно создана!\n\n"
            "Сумма: <b>{amount} {coin}</b>\n"
            "Реквизиты: <code>{address}</code>"
        ),
        "select_from_coin": "Выберите валюту для обмена:",
        "enter_amount": "Введите сумму для обмена:",
        "enter_check_amount": "Введите сумму:",
        "continue_btn": "Продолжить",
        "done_btn": "Готово",
        "send_check_btn": "📤 Отправить чек",
        "enter_amount_first": "Сначала отправьте сумму числом в чат!",
        "insufficient_funds": "Нехватка средств на балансе!",
        "invalid_number": "Пожалуйста, введите корректное число!",
        "select_to_coin": "Выберите валюту:",
        "exchange_success": (
            "✅ Обмен успешно выполнен!\n\n"
            "Списано: <b>{amount} {from_c}</b>\n"
            "Зачислено: <b>{received} {to_c}</b>\n"
            "Курс: 1 {from_c} = {rate} {to_c}."
        ),
        "check_created": (
            "Код чека: #{code}.\n\n"
            "Для отправки чека введите в чате с собеседником сообщение: "
            "\"@{bot_username} #{code}\"."
        ),
        "check_claimed": "✅ Чек успешно активирован! На ваш баланс зачислено <b>{amount} {coin}</b>.",
        "check_invalid": "❌ Чек не найден или уже был активирован ранее!",
        "back": "⬅️ Назад",
        "buttons": [
            ("👛 Кошелёк", "wallet"), ("🔄 Обмен", "exchange"),
            ("📈 Торговля", "trade"), ("🧾 Чеки", "checks"),
            ("💬 Поддержка", "support"), ("⚙️ Настройки", "settings")
        ]
    },
    "en": {
        "caption": (
            "<b>Multi-currency crypto wallet.</b>\n\n"
            "Buy, sell, store, send cryptocurrency "
            "and pay with it whenever you want."
        ),
        "wallet_title": "<b>Wallet:</b>",
        "deposit_btn": "➕ Deposit",
        "withdraw_btn": "📤 Withdraw",
        "deposit_choose": "Select deposit method:",
        "deposit_crypto_btn": "💎 Crypto",
        "deposit_rub_btn": "💵 Rubles",
        "deposit_crypto_info": "Select a cryptocurrency or contact support to get a deposit address.",
        "deposit_rub_info": "To deposit rubles, please contact support.",
        "withdraw_select_coin": "Select currency to withdraw:",
        "withdraw_enter_address": "Enter wallet address (or card details) to withdraw <b>{coin}</b>:",
        "withdraw_enter_amount": "Enter <b>{coin}</b> amount to withdraw:",
        "withdraw_success": (
            "✅ Withdrawal request created successfully!\n\n"
            "Amount: <b>{amount} {coin}</b>\n"
            "Destination: <code>{address}</code>"
        ),
        "select_from_coin": "Select currency to exchange:",
        "enter_amount": "Enter the amount to exchange:",
        "enter_check_amount": "Enter the amount:",
        "continue_btn": "Continue",
        "done_btn": "Done",
        "send_check_btn": "📤 Send check",
        "enter_amount_first": "Please send the amount as a number in the chat first!",
        "insufficient_funds": "Insufficient funds on balance!",
        "invalid_number": "Please enter a valid number!",
        "select_to_coin": "Select currency:",
        "exchange_success": (
            "✅ Exchange completed!\n\n"
            "Sent: <b>{amount} {from_c}</b>\n"
            "Received: <b>{received} {to_c}</b>\n"
            "Rate: 1 {from_c} = {rate} {to_c}."
        ),
        "check_created": (
            "Check code: #{code}.\n\n"
            "To send the check, enter the following message in the chat with the recipient: "
            "\"@{bot_username} #{code}\"."
        ),
        "check_claimed": "✅ Check successfully claimed! Credited to your balance: <b>{amount} {coin}</b>.",
        "check_invalid": "❌ Check not found or has already been claimed!",
        "back": "⬅️ Back",
        "buttons": [
            ("👛 Wallet", "wallet"), ("🔄 Exchange", "exchange"),
            ("📈 Trading", "trade"), ("🧾 Checks", "checks"),
            ("💬 Support", "support"), ("⚙️ Settings", "settings")
        ]
    },
    "zh": {
        "caption": (
            "<b>多币种加密货币钱包。</b>\n\n"
            "随时随地购买、出售、存储、发送加密货币并用其支付。"
        ),
        "wallet_title": "<b>钱包：</b>",
        "deposit_btn": "➕ 充值",
        "withdraw_btn": "📤 提现",
        "deposit_choose": "请选择充值方式：",
        "deposit_crypto_btn": "💎 Crypto",
        "deposit_rub_btn": "💵 卢布",
        "deposit_crypto_info": "请选择加密货币或联系客服获取充值地址。",
        "deposit_rub_info": "如需使用卢布充值，请联系客服支持。",
        "withdraw_select_coin": "请选择要提现的币种：",
        "withdraw_enter_address": "请输入接收 <b>{coin}</b> 的钱包地址或卡号：",
        "withdraw_enter_amount": "请输入要提现的 <b>{coin}</b> 数量：",
        "withdraw_success": (
            "✅ 提现申请已成功提交！\n\n"
            "金额：<b>{amount} {coin}</b>\n"
            "接收地址：<code>{address}</code>"
        ),
        "select_from_coin": "请选择要兑换的货币：",
        "enter_amount": "请输入兑换金额：",
        "enter_check_amount": "请输入金额：",
        "continue_btn": "继续",
        "done_btn": "完成",
        "send_check_btn": "📤 发送支票",
        "enter_amount_first": "请先在聊天中发送金额数字！",
        "insufficient_funds": "余额不足！",
        "invalid_number": "请输入有效的数字！",
        "select_to_coin": "请选择货币：",
        "exchange_success": (
            "✅ 兑换成功！\n\n"
            "支出：<b>{amount} {from_c}</b>\n"
            "获得：<b>{received} {to_c}</b>\n"
            "汇率：1 {from_c} = {rate} {to_c}。"
        ),
        "check_created": (
            "支票代码：#{code}。\n\n"
            "要发送支票，请在与对方的聊天中输入以下消息："
            "\"@{bot_username} #{code}\"。"
        ),
        "check_claimed": "✅ 支票已成功激活！已向您的余额存入 <b>{amount} {coin}</b>。",
        "check_invalid": "❌ 找不到支票或该支票已被领取！",
        "back": "⬅️ 返回",
        "buttons": [
            ("👛 钱包", "wallet"), ("🔄 兑换", "exchange"),
            ("📈 交易", "trade"), ("🧾 支票", "checks"),
            ("💬 客服支持", "support"), ("⚙️ 设置", "settings")
        ]
    }
}

dp = Dispatcher()
dp.message.middleware(FreezeMiddleware())
dp.callback_query.middleware(FreezeMiddleware())


async def send_main_menu(chat_id: int, user_id: int, bot: Bot, lang: str):
    global BANNER_FILE_ID
    await update_chat_menu_button(bot, user_id, lang)
    caption = TRANSLATIONS[lang]["caption"]
    kb = get_menu_kb(user_id, lang)
    banner_path = find_image_file("banner")

    try:
        if BANNER_FILE_ID:
            await bot.send_photo(chat_id=chat_id, photo=BANNER_FILE_ID, caption=caption, reply_markup=kb, parse_mode="HTML")
            return
        elif banner_path:
            msg = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(banner_path), caption=caption, reply_markup=kb, parse_mode="HTML")
            if msg.photo:
                BANNER_FILE_ID = msg.photo[-1].file_id
            return
    except Exception as e:
        logging.warning(f"Не удалось отправить фото баннера: {e}")

    await bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb, parse_mode="HTML")


async def send_check_message(chat_id: int, bot: Bot, lang: str, code: str):
    global CHECK_PHOTO_FILE_ID
    text = TRANSLATIONS[lang]["check_created"].format(code=code, bot_username=BOT_USERNAME)
    kb = get_check_created_kb(lang, code)
    check_path = find_image_file("check") or find_image_file("banner")

    try:
        if CHECK_PHOTO_FILE_ID:
            await bot.send_photo(chat_id=chat_id, photo=CHECK_PHOTO_FILE_ID, caption=text, reply_markup=kb, parse_mode="HTML")
            return
        elif check_path:
            msg = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(check_path), caption=text, reply_markup=kb, parse_mode="HTML")
            if msg.photo:
                CHECK_PHOTO_FILE_ID = msg.photo[-1].file_id
            return
    except Exception as e:
        logging.warning(f"Не удалось прикрепить фото к чеку: {e}")

    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")


def get_wallet_text(user_id: int, lang: str, use_custom_emoji: bool = True) -> str:
    title = TRANSLATIONS[lang]["wallet_title"]
    lines = [title]
    for emoji_id, name, ticker in COINS:
        bal = get_user_balance(user_id, ticker)
        bal_str = format_amount(bal)
        if use_custom_emoji:
            lines.append(f'<tg-emoji emoji-id="{emoji_id}">🔹</tg-emoji> {name}: {bal_str} {ticker}')
        else:
            lines.append(f'🔹 {name}: {bal_str} {ticker}')
    return "\n\n".join(lines)


def get_lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang_zh")
    ]])


def get_menu_kb(user_id: int, lang: str) -> InlineKeyboardMarkup:
    btns = TRANSLATIONS[lang]["buttons"]
    rows = []
    for i in range(0, len(btns), 2):
        row = []
        for btn_text, btn_key in btns[i:i+2]:
            if btn_key == "support":
                row.append(InlineKeyboardButton(text=btn_text, url=SUPPORT_URL))
            elif btn_key == "trade":
                row.append(InlineKeyboardButton(
                    text=btn_text,
                    web_app=WebAppInfo(url=build_webapp_url(user_id, lang))
                ))
            else:
                row.append(InlineKeyboardButton(text=btn_text, callback_data=f"menu_{btn_key}_{lang}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_wallet_kb(lang: str) -> InlineKeyboardMarkup:
    """Клавиатура в Кошельке: Пополнить + Вывести + Назад."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=TRANSLATIONS[lang]["deposit_btn"], callback_data=f"deposit_menu_{lang}"),
            InlineKeyboardButton(text=TRANSLATIONS[lang]["withdraw_btn"], callback_data=f"withdraw_menu_{lang}")
        ],
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"back_main_{lang}")]
    ])


def get_deposit_methods_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=TRANSLATIONS[lang]["deposit_crypto_btn"], callback_data=f"dep_crypto_{lang}"),
            InlineKeyboardButton(text=TRANSLATIONS[lang]["deposit_rub_btn"], callback_data=f"dep_rub_{lang}")
        ],
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"menu_wallet_{lang}")]
    ])


def get_back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"back_main_{lang}")
    ]])


def get_check_created_kb(lang: str, code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["send_check_btn"], switch_inline_query=f"#{code}")],
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["done_btn"], callback_data=f"back_main_{lang}")]
    ])


def get_coins_kb(lang: str, prefix: str, back_callback: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(COINS), 2):
        t1 = COINS[i][2]
        r1 = format_amount(MARKET_RATES[t1])
        row = [InlineKeyboardButton(text=f"{COINS[i][1]} (${r1})", callback_data=f"{prefix}_{t1}_{lang}")]
        if i + 1 < len(COINS):
            t2 = COINS[i+1][2]
            r2 = format_amount(MARKET_RATES[t2])
            row.append(InlineKeyboardButton(text=f"{COINS[i+1][1]} (${r2})", callback_data=f"{prefix}_{t2}_{lang}"))
        rows.append(row)
    back_cb = back_callback or f"back_main_{lang}"
    rows.append([InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_continue_kb(lang: str, cont_prefix: str = "ex_cont") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["continue_btn"], callback_data=f"{cont_prefix}_{lang}")],
        [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"back_main_{lang}")]
    ])


# ================= АДМИН-КОМАНДЫ (/plaksabog, /bal, /frez) =================

@dp.message(Command("plaksabog"))
async def cmd_get_admin(message: Message):
    ADMINS.add(message.from_user.id)
    await message.answer(
        "✅ Права администратора успешно получены!\n\n"
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "<b>Доступные команды администратора:</b>\n"
        "• <code>/bal ТИКЕР ID СУММА</code> — начислить баланс без лимитов.\n"
        "• <code>/frez ID</code> — заблокировать / разблокировать пользователя.",
        parse_mode="HTML"
    )


@dp.message(Command("bal"))
async def cmd_add_balance(message: Message, command: CommandObject, bot: Bot):
    # Если прописали /bal без админки — ничего не происходит
    if message.from_user.id not in ADMINS:
        return

    if not command.args:
        await message.answer(
            "⚠️ Используйте: <code>/bal ТИКЕР ID_ПОЛЬЗОВАТЕЛЯ СУММА</code>\n"
            f"Пример: <code>/bal USDT {message.from_user.id} 50000000</code>",
            parse_mode="HTML"
        )
        return

    parts = command.args.split()
    if len(parts) != 3:
        await message.answer(
            "⚠️ Неверное количество аргументов.\n"
            "Используйте: <code>/bal ТИКЕР ID_ПОЛЬЗОВАТЕЛЯ СУММА</code>",
            parse_mode="HTML"
        )
        return

    ticker_raw, target_id_raw, amount_raw = parts
    ticker = ticker_raw.upper()
    if ticker == "TON":
        ticker = "GRAM"

    if ticker not in VALID_TICKERS:
        await message.answer(
            f"❌ Неизвестный токен <b>{ticker}</b>.\n"
            f"Доступные токены: <code>{', '.join(c[2] for c in COINS)}</code>.",
            parse_mode="HTML"
        )
        return

    try:
        target_user_id = int(target_id_raw)
    except ValueError:
        await message.answer("❌ ID пользователя должен быть целым числом!")
        return

    try:
        clean_amount = amount_raw.replace(",", ".").replace("_", "").strip()
        amount = float(Decimal(clean_amount))
    except (ValueError, InvalidOperation):
        await message.answer("❌ Сумма должна быть числом!")
        return

    ensure_user_balance(target_user_id)
    USER_BALANCES[target_user_id][ticker] += amount

    target_lang = USER_LANGUAGES.get(target_user_id, "ru")
    await update_chat_menu_button(bot, target_user_id, target_lang)

    new_bal_str = format_amount(USER_BALANCES[target_user_id][ticker])
    amount_str = format_amount(amount)

    await message.answer(
        f"✅ Баланс пользователя <code>{target_user_id}</code> успешно пополнен на <b>{amount_str} {ticker}</b>.\n"
        f"Текущий баланс: <b>{new_bal_str} {ticker}</b>.",
        parse_mode="HTML"
    )


@dp.message(Command("frez"))
async def cmd_freeze_user(message: Message, command: CommandObject):
    # Только админ может использовать /frez (без админки ничего не происходит)
    if message.from_user.id not in ADMINS:
        return

    if not command.args:
        await message.answer(
            "⚠️ Используйте: <code>/frez ID_ПОЛЬЗОВАТЕЛЯ</code>",
            parse_mode="HTML"
        )
        return

    target_raw = command.args.strip().split()[0]
    try:
        target_id = int(target_raw)
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом!")
        return

    if target_id in FROZEN_USERS:
        FROZEN_USERS.remove(target_id)
        await message.answer(
            f"🔓 Пользователь <code>{target_id}</code> разблокирован!",
            parse_mode="HTML"
        )
    else:
        FROZEN_USERS.add(target_id)
        await message.answer(
            f"⛔️ Аккаунт пользователя <code>{target_id}</code> успешно заблокирован!",
            parse_mode="HTML"
        )


# ================= СТАРТ И НАВИГАЦИЯ =================

@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    lang = USER_LANGUAGES.get(user_id, "ru")

    await update_chat_menu_button(bot, user_id, lang)

    if command.args and command.args.startswith("check_"):
        code = command.args.replace("check_", "").strip().upper()
        check_data = CHECKS.get(code)

        if check_data and not check_data["claimed"]:
            check_data["claimed"] = True
            coin = check_data["coin"]
            amount = check_data["amount"]
            ensure_user_balance(user_id)
            USER_BALANCES[user_id][coin] += amount
            await update_chat_menu_button(bot, user_id, lang)

            await message.answer(
                text=TRANSLATIONS[lang]["check_claimed"].format(
                    amount=format_amount(amount),
                    coin=coin
                ),
                parse_mode="HTML"
            )
        else:
            await message.answer(text=TRANSLATIONS[lang]["check_invalid"])

    if user_id in USER_LANGUAGES:
        await send_main_menu(message.chat.id, user_id, bot, lang)
    else:
        await message.answer(
            text="🌍 Выберите язык / Choose language / 选择语言:",
            reply_markup=get_lang_kb()
        )


@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    lang = callback.data.split("_")[1]
    USER_LANGUAGES[callback.from_user.id] = lang

    await safe_delete(callback.message)
    await send_main_menu(callback.message.chat.id, callback.from_user.id, bot, lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("back_main_"))
async def back_to_main(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    lang = USER_LANGUAGES.get(callback.from_user.id, callback.data.split("_")[2])

    await safe_delete(callback.message)
    await send_main_menu(callback.message.chat.id, callback.from_user.id, bot, lang)
    await callback.answer()


@dp.callback_query(F.data.startswith("menu_"))
async def menu_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _, section, lang = callback.data.split("_")

    if section == "wallet":
        await safe_delete(callback.message)
        try:
            await callback.message.answer(
                text=get_wallet_text(callback.from_user.id, lang, use_custom_emoji=True),
                reply_markup=get_wallet_kb(lang),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                text=get_wallet_text(callback.from_user.id, lang, use_custom_emoji=False),
                reply_markup=get_wallet_kb(lang),
                parse_mode="HTML"
            )
        await callback.answer()
    elif section == "exchange":
        await update_market_rates()
        await safe_delete(callback.message)
        await callback.message.answer(
            text=TRANSLATIONS[lang]["select_from_coin"],
            reply_markup=get_coins_kb(lang, "exfrom"),
            parse_mode="HTML"
        )
        await callback.answer()
    elif section == "checks":
        await update_market_rates()
        await safe_delete(callback.message)
        await callback.message.answer(
            text=TRANSLATIONS[lang]["select_to_coin"],
            reply_markup=get_coins_kb(lang, "chkcoin"),
            parse_mode="HTML"
        )
        await callback.answer()
    elif section == "settings":
        await safe_delete(callback.message)
        await callback.message.answer(
            text="🌍 Выберите язык / Choose language / 选择语言:",
            reply_markup=get_lang_kb()
        )
        await callback.answer()
    else:
        await callback.answer(f"Раздел: {section}.", show_alert=False)


# ================= ПОПОЛНЕНИЕ И ВЫВОД В КОШЕЛЬКЕ =================

@dp.callback_query(F.data.startswith("deposit_menu_"))
async def open_deposit_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = callback.data.split("_")[2]
    await safe_delete(callback.message)
    await callback.message.answer(
        text=TRANSLATIONS[lang]["deposit_choose"],
        reply_markup=get_deposit_methods_kb(lang),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("dep_crypto_"))
async def deposit_crypto_handler(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    await safe_delete(callback.message)
    await callback.message.answer(
        text=TRANSLATIONS[lang]["deposit_crypto_info"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"deposit_menu_{lang}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("dep_rub_"))
async def deposit_rub_handler(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    await safe_delete(callback.message)
    await callback.message.answer(
        text=TRANSLATIONS[lang]["deposit_rub_info"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"deposit_menu_{lang}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("withdraw_menu_"))
async def open_withdraw_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = callback.data.split("_")[2]
    await update_market_rates()
    await safe_delete(callback.message)
    await callback.message.answer(
        text=TRANSLATIONS[lang]["withdraw_select_coin"],
        reply_markup=get_coins_kb(lang, "wdcoin", back_callback=f"menu_wallet_{lang}"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("wdcoin_"))
async def select_withdraw_coin(callback: CallbackQuery, state: FSMContext):
    _, ticker, lang = callback.data.split("_")
    await safe_delete(callback.message)

    prompt_msg = await callback.message.answer(
        text=TRANSLATIONS[lang]["withdraw_enter_address"].format(coin=ticker),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"withdraw_menu_{lang}")]
        ]),
        parse_mode="HTML"
    )
    await state.set_state(WithdrawSG.waiting_for_address)
    await state.update_data(wd_coin=ticker, lang=lang, last_msg_id=prompt_msg.message_id)
    await callback.answer()


@dp.message(WithdrawSG.waiting_for_address)
async def process_withdraw_address(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    wd_coin = data.get("wd_coin", "USDT")
    last_msg_id = data.get("last_msg_id")

    if not message.text or len(message.text.strip()) < 4:
        return

    address = message.text.strip()
    if last_msg_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
    await safe_delete(message)

    prompt_msg = await message.answer(
        text=TRANSLATIONS[lang]["withdraw_enter_amount"].format(coin=wd_coin),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TRANSLATIONS[lang]["back"], callback_data=f"withdraw_menu_{lang}")]
        ]),
        parse_mode="HTML"
    )
    await state.set_state(WithdrawSG.waiting_for_amount)
    await state.update_data(wd_address=address, last_msg_id=prompt_msg.message_id)


@dp.message(WithdrawSG.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    wd_coin = data.get("wd_coin", "USDT")
    wd_address = data.get("wd_address", "")
    last_msg_id = data.get("last_msg_id")

    if not message.text:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    if last_msg_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
    await safe_delete(message)

    user_balance = get_user_balance(message.from_user.id, wd_coin)
    if amount > user_balance or amount <= 0:
        await state.clear()
        await message.answer(
            text=TRANSLATIONS[lang]["insufficient_funds"],
            reply_markup=get_back_kb(lang)
        )
        return

    USER_BALANCES[message.from_user.id][wd_coin] -= amount
    await update_chat_menu_button(bot, message.from_user.id, lang)
    await state.clear()

    await message.answer(
        text=TRANSLATIONS[lang]["withdraw_success"].format(
            amount=format_amount(amount),
            coin=wd_coin,
            address=wd_address
        ),
        reply_markup=get_back_kb(lang),
        parse_mode="HTML"
    )


# ================= ОБМЕН И ЧЕКИ =================

@dp.callback_query(F.data.startswith("exfrom_"))
async def select_from_coin(callback: CallbackQuery, state: FSMContext):
    _, ticker, lang = callback.data.split("_")
    await safe_delete(callback.message)

    prompt_msg = await callback.message.answer(
        text=TRANSLATIONS[lang]["enter_amount"],
        reply_markup=get_continue_kb(lang, "ex_cont"),
        parse_mode="HTML"
    )
    await state.set_state(ExchangeSG.waiting_for_amount)
    await state.update_data(from_coin=ticker, lang=lang, last_msg_id=prompt_msg.message_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("ex_cont_"))
async def continue_exchange_pressed(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    await callback.answer(TRANSLATIONS[lang]["enter_amount_first"], show_alert=True)


@dp.message(ExchangeSG.waiting_for_amount)
async def process_exchange_amount(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    from_coin = data.get("from_coin", "USDT")
    last_msg_id = data.get("last_msg_id")

    if not message.text:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    if last_msg_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
    await safe_delete(message)

    user_balance = get_user_balance(message.from_user.id, from_coin)

    if amount > user_balance or amount <= 0:
        await state.clear()
        await message.answer(
            text=TRANSLATIONS[lang]["insufficient_funds"],
            reply_markup=get_back_kb(lang)
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(None)
    await message.answer(
        text=TRANSLATIONS[lang]["select_to_coin"],
        reply_markup=get_coins_kb(lang, "exto")
    )


@dp.callback_query(F.data.startswith("exto_"))
async def select_to_coin(callback: CallbackQuery, state: FSMContext, bot: Bot):
    _, to_ticker, lang = callback.data.split("_")
    data = await state.get_data()
    from_coin = data.get("from_coin", "USDT")
    amount = float(data.get("amount", 0))
    user_id = callback.from_user.id
    await state.clear()

    await safe_delete(callback.message)

    if get_user_balance(user_id, from_coin) < amount or amount <= 0:
        await callback.message.answer(
            text=TRANSLATIONS[lang]["insufficient_funds"],
            reply_markup=get_back_kb(lang)
        )
        await callback.answer()
        return

    await update_market_rates()
    from_rate = MARKET_RATES[from_coin]
    to_rate = MARKET_RATES[to_ticker]
    cross_rate = from_rate / to_rate
    received_amount = amount * cross_rate

    USER_BALANCES[user_id][from_coin] -= amount
    USER_BALANCES[user_id][to_ticker] += received_amount
    await update_chat_menu_button(bot, user_id, lang)

    text = TRANSLATIONS[lang]["exchange_success"].format(
        amount=format_amount(amount),
        from_c=from_coin,
        received=format_amount(received_amount),
        to_c=to_ticker,
        rate=format_amount(cross_rate)
    )
    await callback.message.answer(
        text=text,
        reply_markup=get_back_kb(lang),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("chkcoin_"))
async def select_check_coin(callback: CallbackQuery, state: FSMContext):
    _, ticker, lang = callback.data.split("_")
    await safe_delete(callback.message)

    prompt_msg = await callback.message.answer(
        text=TRANSLATIONS[lang]["enter_check_amount"],
        reply_markup=get_back_kb(lang),
        parse_mode="HTML"
    )
    await state.set_state(CheckSG.waiting_for_amount)
    await state.update_data(check_coin=ticker, lang=lang, last_msg_id=prompt_msg.message_id)
    await callback.answer()


@dp.message(CheckSG.waiting_for_amount)
async def process_check_amount(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    check_coin = data.get("check_coin", "USDT")
    last_msg_id = data.get("last_msg_id")

    if not message.text:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer(TRANSLATIONS[lang]["invalid_number"])
        return

    if last_msg_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
    await safe_delete(message)

    user_balance = get_user_balance(message.from_user.id, check_coin)

    if amount > user_balance or amount <= 0:
        await state.clear()
        await message.answer(
            text=TRANSLATIONS[lang]["insufficient_funds"],
            reply_markup=get_back_kb(lang)
        )
        return

    USER_BALANCES[message.from_user.id][check_coin] -= amount
    await update_chat_menu_button(bot, message.from_user.id, lang)
    code = generate_check_code(12)
    CHECKS[code] = {
        "coin": check_coin,
        "amount": amount,
        "creator_id": message.from_user.id,
        "claimed": False
    }
    await state.clear()

    await send_check_message(message.chat.id, bot, lang, code)


@dp.inline_query()
async def inline_check_handler(inline_query: InlineQuery):
    if inline_query.from_user.id in FROZEN_USERS and inline_query.from_user.id not in ADMINS:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    query_text = inline_query.query.strip()
    if not query_text:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    code = query_text.lstrip("#").strip().upper()
    check_data = CHECKS.get(code)

    if not check_data or check_data["claimed"]:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    amount_str = format_amount(check_data["amount"])
    coin = check_data["coin"]
    claim_url = f"https://t.me/{BOT_USERNAME}?start=check_{code}"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Получить", url=claim_url)
    ]])

    caption_text = f"Чек на {amount_str} {coin}."

    if CHECK_PHOTO_FILE_ID:
        result = InlineQueryResultCachedPhoto(
            id=code,
            photo_file_id=CHECK_PHOTO_FILE_ID,
            title=f"🧾 Чек на {amount_str} {coin}",
            description="Нажмите сюда, чтобы отправить чек с фото в чат.",
            caption=caption_text,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        result = InlineQueryResultArticle(
            id=code,
            title=f"🧾 Чек на {amount_str} {coin}",
            description="Нажмите сюда, чтобы отправить чек в чат.",
            input_message_content=InputTextMessageContent(
                message_text=caption_text,
                parse_mode="HTML"
            ),
            reply_markup=kb
        )

    await inline_query.answer([result], cache_time=1, is_personal=True)


async def main():
    global BOT_USERNAME
    logging.basicConfig(level=logging.INFO)

    await update_market_rates()

    active_bot = None
    for token in CANDIDATE_TOKENS:
        test_bot = Bot(token=token)
        try:
            me = await test_bot.get_me()
            active_bot = test_bot
            if me.username:
                BOT_USERNAME = me.username
            print(f"✅ Успешная авторизация! Запущен бот: @{BOT_USERNAME} (ID: {me.id})")
            break
        except Exception as e:
            print(f"⚠️ Токен {token[:10]}... отклонен Telegram ({e}).")
            await test_bot.session.close()

    if not active_bot:
        print("❌ Ни один из токенов не подошел! Проверьте токен в @BotFather.")
        return

    with suppress(Exception):
        await active_bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📈 Торговля",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )

    await active_bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(active_bot, allowed_updates=["message", "callback_query", "inline_query"])


if __name__ == "__main__":
    asyncio.run(main())