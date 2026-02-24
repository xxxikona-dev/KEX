import asyncio
import os
import logging
import sys
import textwrap
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile, BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from dotenv import load_dotenv

# --- ИНИЦИАЛИЗАЦИЯ И ПУТИ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "font.ttf")

# --- ПОЛНАЯ КОНФИГУРАЦИЯ КООРДИНАТ ---
# Параметры width, spacing и lines добавлены для корректного переноса
FIELDS_CONFIG = [
    {"coord": (660, 757.5), "size": 24, "rotate": 0.7, "color": (40, 42, 55)},   # 1. Фамилия
    {"coord": (660, 837), "size": 24, "rotate": 1.2, "color": (40, 42, 55)},     # 2. Имя
    {"coord": (660, 877.5), "size": 24, "rotate": 1.2, "color": (40, 42, 55)},     # 3. Отчество
    {"coord": (710, 917), "size": 24, "rotate": 1.2, "color": (35, 38, 50)},   # 4. Дата рожд.
    {"coord": (660, 999.5), "size": 24, "rotate": 1.7, "color": (40, 42, 55), "width": 20, "spacing": 14.5, "lines": 3}, # 5. Место рожд.
    {"coord": (500, 922.5), "size": 23, "rotate": 0.6, "color": (35, 38, 50)},     # 6. Пол
    {"coord": (535, 358.5), "size": 24, "rotate": 0.7, "color": (45, 45, 60), "width": 22, "spacing": 14.5, "lines": 3}, # 7. Кем выдан
    {"coord": (357, 439), "size": 24, "rotate": -0.3, "color": (40, 40, 55)},  # 8. Дата выд.
    {"coord": (710, 434), "size": 24, "rotate": -0.2, "color": (40, 42, 55)},    # 9. Код подр.
    {"coord": (870, 880), "size": 28, "rotate": -87.0, "color": (150, 30, 30)},  # 10. Номер (НИЖНИЙ)
    {"coord": (860, 455), "size": 28, "rotate": -89.0, "color": (140, 30, 30)}    # 11. Номер (ВЕРХНИЙ)
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

# --- ВНИМАНИЕ: ФУНКЦИИ ОТРИСОВКИ (БЕЗ СОКРАЩЕНИЙ) ---

def draw_centered_text(img, text, font, config):
    """Рисует одну строку текста, центрированную по точке coord с учетом прозрачности и поворота"""
    text = str(text).upper()
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    # Создаем слой с запасом, чтобы края не обрезались при повороте
    txt_layer = Image.new("RGBA", (tw + 200, th + 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    
    # Применяем цвет + прозрачность (Alpha 230)
    fill_color = config["color"] + (230,) 
    d.text(((tw + 200) // 2, (th + 200) // 2), text, font=font, fill=fill_color, anchor="mm")
    
    # Поворот
    if config.get("rotate", 0) != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    # Размытие краев букв для эффекта печати
    txt_layer = txt_layer.filter(ImageFilter.GaussianBlur(radius=0.15))

    lw, lh = txt_layer.size
    # Вычисляем смещение, чтобы центр слоя совпал с coord
    offset_x = int(config["coord"][0] - (lw // 2))
    offset_y = int(config["coord"][1] - (lh // 2))
    
    # Наложение слоя на основное изображение
    img.alpha_composite(txt_layer, (offset_x, offset_y))

def draw_multi_line_centered(img, text, font, config):
    """Разбивает длинный текст на строки и центрирует весь блок по вертикали"""
    text = str(text).upper()
    chars_limit = config.get("width", 30)
    max_lines = config.get("lines", 3)
    
    # Разбиваем по пробелам, не разрывая слова
    lines = textwrap.wrap(text, width=chars_limit, break_long_words=False)[:max_lines]
    
    base_x, base_y = config["coord"]
    line_step = config["size"] + config.get("spacing", 10) 
    
    # Рассчитываем общую высоту блока текста для вертикального центрирования
    total_h = (len(lines) - 1) * line_step
    start_y = base_y - (total_h // 2)

    for i, line in enumerate(lines):
        line_cfg = config.copy()
        # Каждая строка получает свою координату Y
        line_cfg["coord"] = (base_x, start_y + (i * line_step))
        draw_centered_text(img, line, font, line_cfg)

# --- ХЕНДЛЕРЫ СОБЫТИЙ ---

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    if not tpls: return await message.answer("Ошибка: Папка 'templates' пуста!")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    await message.answer_photo(FSInputFile(os.path.join(TEMPLATES_DIR, tpls[0])), 
                               caption=f"Выберите шаблон: {tpls[0]}", reply_markup=kb)
    await state.set_state(Form.browsing_templates)

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    act, idx = call.data.split("_")
    idx = int(idx)
    
    if act == "s":
        await state.update_data(tpl=tpls[idx])
        await call.message.answer(
            "Введите 10 строк данных (каждая с новой строки):\n"
            "1. Фамилия\n2. Имя\n3. Отчество\n4. Дата рожд.\n5. Место рожд.\n"
            "6. Пол\n7. Кем выдан\n8. Дата выд.\n9. Код подр.\n10. Серия и номер",
            parse_mode="HTML"
        )
        await state.set_state(Form.inputting_data)
    else:
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, tpls[new_idx])), caption=f"Шаблон: {tpls[new_idx]}"), 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")]]))
    await call.answer()

@dp.message(Form.inputting_data)
async def process(message: types.Message, state: FSMContext):
    user_lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(user_lines) < 10: 
        return await message.answer(f"Вы ввели только {len(user_lines)} строк. Нужно 10.")
    
    data = await state.get_data()
    await message.answer("⌛ Обработка изображения...")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, data['tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                font = ImageFont.truetype(FONT_PATH, cfg["size"])
                
                # Поля с переносом строк (Место рождения и Кем выдан)
                if i in [4, 6]:
                    draw_multi_line_centered(img, user_lines[i], font, cfg)
                else:
                    draw_centered_text(img, user_lines[i], font, cfg)
                
                # Дублируем номер на верхнюю страницу (Поле 11 берет данные из Поля 10)
                if i == 9:
                    draw_centered_text(img, user_lines[i], font, FIELDS_CONFIG[10])

            # Финальная склейка и легкое общее размытие
            res = img.convert("RGB")
            res = res.filter(ImageFilter.GaussianBlur(radius=0.15)) 
            
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="ready.jpg"), caption="Готово!")
            await state.clear()
    except Exception as e:
        logging.error(f"Error: {e}")
        await message.answer(f"Произошла ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
