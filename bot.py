from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse, parse_qs

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from vinted import VintedClient
from db import Database

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────
BOT_TOKEN = "8791687514:AAHJ1zceedGvmfqY6f9FK_b-S54E5R9nBCM"
ADMIN_ID = 1087422106
CHECK_INTERVAL = 240   # seconds
# ────────────────────────────────────────────────────────────────────

db = Database()
vinted = VintedClient()
bot = Bot(token=BOT_TOKEN)
router = Router()


class Form(StatesGroup):
    waiting_url = State()
    waiting_chat_id = State()
    waiting_add_user = State()
    waiting_remove_user = State()


KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить ссылку"), KeyboardButton(text="Удалить ссылку")],
        [KeyboardButton(text="Все ссылки"), KeyboardButton(text="Статус")],
        [KeyboardButton(text="Изменить Chat ID"), KeyboardButton(text="Показать Chat ID")],
    ],
    resize_keyboard=True,
)

KB_ADMIN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить ссылку"), KeyboardButton(text="Удалить ссылку")],
        [KeyboardButton(text="Все ссылки"), KeyboardButton(text="Статус")],
        [KeyboardButton(text="Изменить Chat ID"), KeyboardButton(text="Показать Chat ID")],
        [KeyboardButton(text="Добавить пользователя"), KeyboardButton(text="Удалить пользователя")],
        [KeyboardButton(text="Список пользователей")],
    ],
    resize_keyboard=True,
)


def _get_kb(user_id: int) -> ReplyKeyboardMarkup:
    return KB_ADMIN if user_id == ADMIN_ID else KB


def _is_authorized(user_id: int) -> bool:
    return user_id == ADMIN_ID or db.is_allowed(user_id)

_last_check: float | None = None
_force_check: asyncio.Event | None = None


def _extract_url_info(url: str) -> tuple[str, str] | None:
    """Extract (domain, search_text) from a Vinted URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname
        if not domain or "vinted" not in domain:
            return None
        query = parse_qs(parsed.query).get("search_text", [None])[0]
        if not query:
            return None
        return (domain, query)
    except Exception:
        return None


TG_MSG_LIMIT = 4096


def _split_message(header: str, blocks: list[str], sep: str = "\n\n") -> list[str]:
    chunks = []
    current = header
    for block in blocks:
        addition = (sep + block) if current != header else block
        if len(current) + len(addition) > TG_MSG_LIMIT:
            chunks.append(current)
            current = block
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def _domain_flag(domain: str) -> str:
    if "vinted.pl" in domain:
        return "\U0001f1f5\U0001f1f1"
    elif "vinted.fr" in domain:
        return "\U0001f1eb\U0001f1f7"
    return "\U0001f310"


# ── Handlers ────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    if not _is_authorized(uid):
        await message.answer(
            "У вас нет доступа к этому боту.\n"
            "Напишите администратору: @wazzamp"
        )
        return
    # set default chat_id to the current chat if not set
    if not db.get_chat_id(uid):
        db.set_chat_id(uid, str(message.chat.id))
    await message.answer("Vinted Monitor Bot", reply_markup=_get_kb(uid))


# --- access check middleware ---

async def _check_access(message: Message) -> bool:
    uid = message.from_user.id
    if not _is_authorized(uid):
        await message.answer(
            "У вас нет доступа к этому боту.\n"
            "Напишите администратору: @wazzamp"
        )
        return False
    return True


# --- add ---

@router.message(F.text == "Добавить ссылку")
async def btn_add(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.set_state(Form.waiting_url)
    await message.answer(
        "Отправьте ссылку на поиск Vinted\n"
        "(например https://www.vinted.pl/catalog?search_text=...):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(Form.waiting_url, F.text == "Отмена")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=_get_kb(message.from_user.id))


@router.message(Form.waiting_url)
async def recv_url(message: Message, state: FSMContext):
    uid = message.from_user.id
    kb = _get_kb(uid)
    info = _extract_url_info(message.text.strip())
    if not info:
        await state.clear()
        await message.answer(
            "Не удалось извлечь запрос. Нужна ссылка вида:\n"
            "https://www.vinted.pl/catalog?search_text=...",
            reply_markup=kb,
        )
        return

    domain, query = info

    if not db.add_watch(uid, domain, query):
        await state.clear()
        await message.answer(
            f'{_domain_flag(domain)} "{query}" на {domain} уже отслеживается.',
            reply_markup=kb,
        )
        return

    await message.answer(f'{_domain_flag(domain)} Добавлено: "{query}" на {domain}\nПервичный парсинг...')
    count = await asyncio.to_thread(_initial_parse, domain, query)
    await state.clear()
    await message.answer(
        f"Сохранено {count} товаров. Новые будут приходить в уведомлениях.",
        reply_markup=kb,
    )


def _initial_parse(domain: str, query: str) -> int:
    items = vinted.search(domain, query)
    ids = [item.get("id") for item in items if item.get("id")]
    if ids:
        db.mark_seen(ids)
    return len(ids)


# --- remove ---

@router.message(F.text == "Удалить ссылку")
async def btn_remove(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.clear()
    uid = message.from_user.id
    watches = db.get_watches(uid)
    if not watches:
        await message.answer("Нет отслеживаемых запросов.", reply_markup=_get_kb(uid))
        return

    keyboard = [
        [InlineKeyboardButton(
            text=f"{_domain_flag(domain)} {query} ({domain})",
            callback_data=f"rm:{wid}",
        )]
        for wid, domain, query in watches
    ]
    await message.answer(
        "Выберите для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.data.startswith("rm:"))
async def cb_remove(callback: CallbackQuery):
    uid = callback.from_user.id
    wid = int(callback.data[3:])
    if db.remove_watch_by_id(uid, wid):
        await callback.message.edit_text("Удалено.")
    else:
        await callback.message.edit_text("Не найдено.")
    await callback.answer()


# --- list ---

@router.message(F.text == "Все ссылки")
async def btn_list(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.clear()
    uid = message.from_user.id
    kb = _get_kb(uid)
    watches = db.get_watches(uid)
    if not watches:
        await message.answer("Нет отслеживаемых запросов.", reply_markup=kb)
        return

    lines = []
    for i, (wid, domain, query) in enumerate(watches, 1):
        encoded = query.replace(" ", "+")
        url = f"https://{domain}/catalog?search_text={encoded}"
        lines.append(f"{i}. {_domain_flag(domain)} {query}\n   {url}")

    header = "Отслеживаемые запросы:\n\n"
    chunks = _split_message(header, lines, sep="\n\n")
    for chunk in chunks:
        await message.answer(chunk, reply_markup=kb, disable_web_page_preview=True)


# --- chat id ---

@router.message(F.text == "Изменить Chat ID")
async def btn_change_cid(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.set_state(Form.waiting_chat_id)
    await message.answer(
        "Отправьте новый Chat ID:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(Form.waiting_chat_id, F.text == "Отмена")
async def cancel_chat_id(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=_get_kb(message.from_user.id))


@router.message(Form.waiting_chat_id)
async def recv_chat_id(message: Message, state: FSMContext):
    import re
    uid = message.from_user.id
    cid = message.text.strip()
    if not re.fullmatch(r"-?\d+", cid):
        await message.answer("Chat ID должен содержать только цифры и '-'. Попробуйте снова:")
        return
    db.set_chat_id(uid, cid)
    await state.clear()
    await message.answer(f"Chat ID: {cid}", reply_markup=_get_kb(uid))


@router.message(F.text == "Показать Chat ID")
async def btn_show_cid(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.clear()
    uid = message.from_user.id
    cid = db.get_chat_id(uid)
    await message.answer(f"Chat ID: {cid or 'не задан'}", reply_markup=_get_kb(uid))


@router.message(F.text == "Статус")
async def btn_status(message: Message, state: FSMContext):
    if not await _check_access(message):
        return
    await state.clear()
    uid = message.from_user.id
    global _last_check
    if _last_check is None:
        remaining = CHECK_INTERVAL
    else:
        elapsed = time.time() - _last_check
        remaining = max(0, CHECK_INTERVAL - elapsed)
    mins, secs = divmod(int(remaining), 60)
    watches = len(db.get_watches(uid))
    await message.answer(
        f"Ваших запросов: {watches}\n"
        f"Следующая проверка через: {mins}м {secs}с",
        reply_markup=_get_kb(uid),
    )
    await message.answer(
        "Запустить проверку вручную?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Запустить проверку", callback_data="force_check")]
        ]),
    )


@router.callback_query(F.data == "force_check")
async def cb_force_check(callback: CallbackQuery):
    await callback.answer("Проверка запущена...")
    await callback.message.edit_text("Проверка запущена...")
    _force_check.set()


# --- admin: user management ---

def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@router.message(F.text == "Добавить пользователя")
async def btn_add_user(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(Form.waiting_add_user)
    await message.answer(
        "Отправьте User ID пользователя:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(Form.waiting_add_user, F.text == "Отмена")
async def cancel_add_user(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=KB_ADMIN)


@router.message(Form.waiting_add_user)
async def recv_add_user(message: Message, state: FSMContext):
    import re
    uid_str = message.text.strip()
    if not re.fullmatch(r"\d+", uid_str):
        await message.answer("User ID должен содержать только цифры. Попробуйте снова:")
        return
    uid = int(uid_str)
    if db.add_allowed_user(uid):
        await state.clear()
        await message.answer(f"Пользователь {uid} добавлен в белый список.", reply_markup=KB_ADMIN)
    else:
        await state.clear()
        await message.answer(f"Пользователь {uid} уже в белом списке.", reply_markup=KB_ADMIN)


@router.message(F.text == "Удалить пользователя")
async def btn_remove_user(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    users = db.get_allowed_users()
    if not users:
        await message.answer("Белый список пуст.", reply_markup=KB_ADMIN)
        return
    keyboard = [
        [InlineKeyboardButton(text=str(uid), callback_data=f"rmu:{uid}")]
        for uid in users
    ]
    await message.answer(
        "Выберите пользователя для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.data.startswith("rmu:"))
async def cb_remove_user(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.")
        return
    uid = int(callback.data[4:])
    if db.remove_allowed_user(uid):
        await callback.message.edit_text(f"Пользователь {uid} удалён из белого списка.")
    else:
        await callback.message.edit_text("Не найдено.")
    await callback.answer()


@router.message(F.text == "Список пользователей")
async def btn_list_users(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    users = db.get_allowed_users()
    if not users:
        await message.answer("Белый список пуст.", reply_markup=KB_ADMIN)
        return
    lines = [str(uid) for uid in users]
    await message.answer(
        "Белый список:\n" + "\n".join(lines),
        reply_markup=KB_ADMIN,
    )


# ── Monitoring ──────────────────────────────────────────────────────

async def run_check():
    """Run a single check cycle for all users."""
    all_watches = db.get_all_watches()
    if not all_watches:
        return 0

    # Group watches by user_id
    user_watches: dict[int, list[tuple[str, str]]] = {}
    for _wid, user_id, domain, search_text in all_watches:
        user_watches.setdefault(user_id, []).append((domain, search_text))

    total_new = 0
    for user_id, watches in user_watches.items():
        chat_id = db.get_chat_id(user_id)
        if not chat_id:
            chat_id = str(user_id)

        user_new = 0
        for domain, query in watches:
            try:
                items = await asyncio.to_thread(vinted.search, domain, query, max_pages=2)
            except Exception:
                logger.exception(f"Monitor error for '{query}' on {domain}")
                continue

            flag = _domain_flag(domain)
            new_ids = []
            for item in items:
                iid = item.get("id")
                if not iid or db.is_seen(iid):
                    continue

                new_ids.append(iid)
                price = item.get("price", {})
                size = item.get("size_title") or "—"
                status = item.get("status") or "—"
                text = (
                    f"{flag} <b>Новый товар</b>\n\n"
                    f"<b>{item.get('title', '—')}</b>\n"
                    f"Бренд: {item.get('brand_title', '—')}\n"
                    f"Размер: {size}\n"
                    f"Состояние: {status}\n"
                    f"Цена: {price.get('amount', '?')} {price.get('currency_code', '')}"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть", url=item.get("url", ""))]
                ])
                photo_url = (item.get("photo") or {}).get("url")
                try:
                    if photo_url:
                        await bot.send_photo(
                            chat_id=chat_id, photo=photo_url,
                            caption=text, parse_mode="HTML", reply_markup=kb,
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id, text=text,
                            parse_mode="HTML", reply_markup=kb,
                        )
                except Exception:
                    logger.exception("Send failed")

            if new_ids:
                db.mark_seen(new_ids)
            user_new += len(new_ids)

        if user_new == 0:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Проверка завершена — новых товаров не найдено.",
                )
            except Exception:
                logger.exception("Status send failed")

        total_new += user_new

    return total_new


async def monitor_loop():
    global _last_check
    while True:
        try:
            await asyncio.wait_for(_force_check.wait(), timeout=CHECK_INTERVAL)
        except asyncio.TimeoutError:
            pass
        _force_check.clear()
        _last_check = time.time()
        await run_check()


# ── Main ────────────────────────────────────────────────────────────

async def main():
    global _force_check
    _force_check = asyncio.Event()

    dp = Dispatcher()
    dp.include_router(router)

    asyncio.create_task(monitor_loop())

    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
