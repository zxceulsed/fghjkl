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
BOT_TOKEN = "6374810081:AAG2YazUJqWkPJp8vZw4cwfirodwAj2W6WY"
CHAT_ID = "823388511"  # default notification chat id; can be changed via bot
CHECK_INTERVAL = 240  # seconds (5 min)
# ────────────────────────────────────────────────────────────────────

db = Database()
vinted = VintedClient()
bot = Bot(token=BOT_TOKEN)
router = Router()


class Form(StatesGroup):
    waiting_url = State()
    waiting_chat_id = State()


KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить ссылку"), KeyboardButton(text="Удалить ссылку")],
        [KeyboardButton(text="Все ссылки"), KeyboardButton(text="Статус")],
        [KeyboardButton(text="Изменить Chat ID"), KeyboardButton(text="Показать Chat ID")],
    ],
    resize_keyboard=True,
)

_last_check: float | None = None
_force_check = asyncio.Event()


def _extract_query(url: str) -> str | None:
    try:
        return parse_qs(urlparse(url).query).get("search_text", [None])[0]
    except Exception:
        return None


TG_MSG_LIMIT = 4096


def _split_message(header: str, blocks: list[str], sep: str = "\n\n") -> list[str]:
    """Split blocks into multiple messages respecting Telegram's 4096 char limit."""
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


# ── Handlers ────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    cid = str(message.chat.id)
    if not db.get_chat_id():
        db.set_chat_id(CHAT_ID or cid)
    await message.answer("Vinted Monitor Bot", reply_markup=KB)


# --- add ---

@router.message(F.text == "Добавить ссылку")
async def btn_add(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_url)
    await message.answer(
        "Отправьте ссылку на поиск Vinted:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(Form.waiting_url, F.text == "Отмена")
async def cancel_add(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=KB)


@router.message(Form.waiting_url)
async def recv_url(message: Message, state: FSMContext):
    query = _extract_query(message.text.strip())
    if not query:
        await state.clear()
        await message.answer(
            "Не удалось извлечь запрос. Нужна ссылка вида:\n"
            "https://www.vinted.pl/catalog?search_text=...",
            reply_markup=KB,
        )
        return

    if not db.add_watch(query):
        await state.clear()
        await message.answer(f'"{query}" уже отслеживается.', reply_markup=KB)
        return

    await message.answer(f'Добавлено: "{query}"\nПервичный парсинг...')
    count = await asyncio.to_thread(_initial_parse, query)
    await state.clear()
    await message.answer(
        f"Сохранено {count} товаров. Новые будут приходить в уведомлениях.",
        reply_markup=KB,
    )


def _initial_parse(query: str) -> int:
    results = vinted.search_all(query)
    ids = []
    for items in results.values():
        for item in items:
            iid = item.get("id")
            if iid:
                ids.append(iid)
    if ids:
        db.mark_seen(ids)
    return len(ids)


# --- remove ---

@router.message(F.text == "Удалить ссылку")
async def btn_remove(message: Message, state: FSMContext):
    await state.clear()
    watches = db.get_watches()
    if not watches:
        await message.answer("Нет отслеживаемых запросов.", reply_markup=KB)
        return

    keyboard = [
        [InlineKeyboardButton(text=q, callback_data=f"rm:{q[:60]}")]
        for q in watches
    ]
    await message.answer(
        "Выберите для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.data.startswith("rm:"))
async def cb_remove(callback: CallbackQuery):
    prefix = callback.data[3:]
    full = next((w for w in db.get_watches() if w.startswith(prefix)), prefix)
    if db.remove_watch(full):
        await callback.message.edit_text(f'Удалено: "{full}"')
    else:
        await callback.message.edit_text("Не найдено.")
    await callback.answer()


# --- list ---

@router.message(F.text == "Все ссылки")
async def btn_list(message: Message, state: FSMContext):
    await state.clear()
    watches = db.get_watches()
    if not watches:
        await message.answer("Нет отслеживаемых запросов.", reply_markup=KB)
        return

    lines = []
    for i, q in enumerate(watches, 1):
        encoded = q.replace(" ", "+")
        pl = f"https://www.vinted.pl/catalog?search_text={encoded}"
        fr = f"https://www.vinted.fr/catalog?search_text={encoded}"
        lines.append(f"{i}. {q}\n   PL: {pl}\n   FR: {fr}")

    header = "Отслеживаемые запросы:\n\n"
    chunks = _split_message(header, lines, sep="\n\n")
    for chunk in chunks:
        await message.answer(chunk, reply_markup=KB, disable_web_page_preview=True)


# --- chat id ---

@router.message(F.text == "Изменить Chat ID")
async def btn_change_cid(message: Message, state: FSMContext):
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
    await message.answer("Отменено.", reply_markup=KB)


@router.message(Form.waiting_chat_id)
async def recv_chat_id(message: Message, state: FSMContext):
    import re
    cid = message.text.strip()
    if not re.fullmatch(r"-?\d+", cid):
        await message.answer("Chat ID должен содержать только цифры и '-'. Попробуйте снова:")
        return
    db.set_chat_id(cid)
    await state.clear()
    await message.answer(f"Chat ID: {cid}", reply_markup=KB)


@router.message(F.text == "Показать Chat ID")
async def btn_show_cid(message: Message, state: FSMContext):
    await state.clear()
    cid = db.get_chat_id()
    await message.answer(f"Chat ID: {cid or 'не задан'}", reply_markup=KB)


@router.message(F.text == "Статус")
async def btn_status(message: Message, state: FSMContext):
    await state.clear()
    global _last_check
    if _last_check is None:
        remaining = CHECK_INTERVAL
    else:
        elapsed = time.time() - _last_check
        remaining = max(0, CHECK_INTERVAL - elapsed)
    mins, secs = divmod(int(remaining), 60)
    watches = len(db.get_watches())
    await message.answer(
        f"Отслеживается запросов: {watches}\n"
        f"Следующая проверка через: {mins}м {secs}с",
        reply_markup=KB,
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


# ── Monitoring ──────────────────────────────────────────────────────

async def run_check():
    """Run a single check cycle. Returns total new items found."""
    chat_id = db.get_chat_id()
    if not chat_id:
        return 0

    watches = db.get_watches()
    if not watches:
        return 0

    total_new = 0
    for query in watches:
        try:
            results = await asyncio.to_thread(vinted.search_all, query, 2)
        except Exception:
            logger.exception(f"Monitor error for '{query}'")
            continue

        new_ids = []
        for domain, items in results.items():
            flag = "\U0001f1f5\U0001f1f1" if "vinted.pl" in domain else "\U0001f1eb\U0001f1f7"
            for item in items:
                iid = item.get("id")
                if not iid or db.is_seen(iid):
                    continue

                new_ids.append(iid)
                price = item.get("price", {})
                text = (
                    f"{flag} <b>Новый товар</b>\n\n"
                    f"<b>{item.get('title', '—')}</b>\n"
                    f"Бренд: {item.get('brand_title', '—')}\n"
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
        total_new += len(new_ids)

    if total_new == 0:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="Проверка завершена — новых товаров не найдено.",
            )
        except Exception:
            logger.exception("Status send failed")

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
    dp = Dispatcher()
    dp.include_router(router)

    asyncio.create_task(monitor_loop())

    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
