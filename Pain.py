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

# --- НАСТРОЙКИ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "font.ttf")

# --- ТВОИ КООРДИНАТЫ (X, Y - ЦЕНТР СТРОКИ) ---
# Цвет (45, 47, 60) — реалистичный антрацит
FIELDS_CONFIG = [
    {"coord": (660, 750), "size": 32, "rotate": -1.5, "color": (40, 42, 55)},   # 1. Фамилия
    {"coord": (660, 854), "size": 32, "rotate": -1.5, "color": (40, 42, 55)},   # 2. Имя
    {"coord": (660, 874), "size": 32, "rotate": -1.5, "color": (40, 42, 55)},   # 3. Отчество
    {"coord": (710, 914), "size": 28, "rotate": -1.2, "color": (35, 38, 50)},   # 4. Дата рожд.
    {"coord": (660, 960), "size": 24, "rotate": -1.0, "color": (40, 42, 55), "width": 25}, # 5. Место рожд.
    {"coord": (500, 920), "size": 28, "rotate": -1.2, "color": (35, 38, 50)},   # 6. Пол
    {"coord": (546, 371), "size": 24, "rotate": -1.8, "color": (45, 45, 60), "width": 45}, # 7. Кем выдан
    {"coord": (357, 437), "size": 28, "rotate": -1.5, "color": (40, 40, 55)},   # 8. Дата выд.
    {"coord": (710, 430), "size": 30, "rotate": -1.5, "color": (30, 45, 120)},  # 9. Код подр.
    {"coord": (870, 880), "size": 42, "rotate": -91.0, "color": (150, 30, 30)}, # 10. Номер (НИЖНИЙ)
    {"coord": (860, 455), "size": 38, "rotate": -1.0, "color": (140, 30, 30)}    # 11. Номер (ВЕРХНИЙ)
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

# --- УМНАЯ ОТРИСОВКА С ЯКОРЕМ В ЦЕНТРЕ ---

def draw_centered_text(img, text, font, config):
    """Рисует текст, расширяющийся от центральной точки (anchor='mm')"""
    # 1. Создаем временный слой для текста
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    # Слой чуть больше текста, чтобы при повороте не обрезало
    txt_layer = Image.new("RGBA", (tw + 100, th + 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    
    # 2. Рисуем текст в центре слоя. 
    # 'mm' — горизонтальный и вертикальный центр текста в указанной точке
    d.text(((tw + 100) // 2, (th + 100) // 2), text, font=font, fill=config["color"], anchor="mm")
    
    # 3. Поворачиваем слой вокруг его центра
    if config["rotate"] != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    # 4. Накладываем слой так, чтобы его центр совпал с точкой в FIELDS_CONFIG
    lw, lh = txt_layer.size
    offset_x = config["coord"][0] - (lw // 2)
    offset_y = config["coord"][1] - (lh // 2)
    
    img.paste(txt_layer, (int(offset_x), int(offset_y)), txt_layer)

def draw_multi_line_centered(img, text, font, config):
    """Разбивает текст на строки и центрирует каждую строку"""
    chars_limit = config.get("width", 30)
    lines = textwrap.wrap(text, width=chars_limit)[:3]
    
    base_x, base_y = config["coord"]
    line_step = config["size"] + 8 # Расстояние между строками

    for i, line in enumerate(lines):
        line_cfg = config.copy()
        # Каждая новая строка смещается ниже центральной точки
        line_cfg["coord"] = (base_x, base_y + (i * line_step))
        draw_centered_text(img, line, font, line_cfg)

# --- ГЛАВНАЯ ЛОГИКА ---

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    if not tpls: return await message.answer("Папка 'templates' пуста!")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    await message.answer_photo(FSInputFile(os.path.join(TEMPLATES_DIR, tpls[0])), 
                               caption=f"Шаблон: {tpls[0]}", reply_markup=kb)
    await state.set_state(Form.browsing_templates)

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    act, idx = call.data.split("_")
    idx = int(idx)
    
    if act == "s":
        await state.update_data(tpl=tpls[idx])
        await call.message.answer("Пришли 10 строк данных (каждая с новой строки).")
        await state.set_state(Form.inputting_data)
    else:
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
            InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
            InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")
        ]])
        await call.message.edit_media(InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, tpls[new_idx])), 
                                                      caption=f"Шаблон: {tpls[new_idx]}"), reply_markup=kb)
    await call.answer()

@dp.message(Form.inputting_data)
async def process(message: types.Message, state: FSMContext):
    user_lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(user_lines) < 10: return await message.answer(f"Надо 10 строк, получено {len(user_lines)}")
    
    data = await state.get_data()
    await message.answer("✒️ Заполняю бланк...")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, data['tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                text = user_lines[i]
                
                try: font = ImageFont.truetype(FONT_PATH, cfg["size"])
                except: font = ImageFont.load_default()
                
                if i in [4, 6]: # Место рождения и Кем выдан
                    draw_multi_line_centered(img, text, font, cfg)
                else:
                    draw_centered_text(img, text, font, cfg)
                
                # ДУБЛИРОВАНИЕ НОМЕРА (10-я строка на 11-ю точку)
                if i == 9:
                    cfg_v2 = FIELDS_CONFIG[10]
                    try: font_v2 = ImageFont.truetype(FONT_PATH, cfg_v2["size"])
                    except: font_v2 = ImageFont.load_default()
                    draw_centered_text(img, text, font_v2, cfg_v2)

            # ФИНАЛЬНЫЕ ЭФФЕКТЫ
            res = img.convert("RGB")
            res = res.filter(ImageFilter.GaussianBlur(radius=0.4)) # Размытие для реализма
            
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="ready.jpg"))
            await state.clear()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
