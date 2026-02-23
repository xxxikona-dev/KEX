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

# --- НАСТРОЙКИ ПУТЕЙ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "font.ttf")

# --- КОНФИГУРАЦИЯ ПОЛЕЙ ---
# Координаты настроены под фото 1.jpg
# (X, Y, Размер, Поворот, Цвет RGB, Ширина для переноса)
FIELDS_CONFIG = [
    {"coord": (475, 532), "size": 27, "rotate": -1.5, "color": (40, 42, 55)},   # 1. Фамилия
    {"coord": (475, 575), "size": 27, "rotate": -1.5, "color": (40, 42, 55)},   # 2. Имя
    {"coord": (475, 615), "size": 27, "rotate": -1.5, "color": (40, 42, 55)},   # 3. Отчество
    {"coord": (585, 642), "size": 25, "rotate": -1.2, "color": (35, 38, 50)},   # 4. Дата рожд.
    {"coord": (615, 695), "size": 21, "rotate": -1.0, "color": (40, 42, 55), "width": 28}, # 5. Место рожд. (ЦЕНТР)
    {"coord": (485, 642), "size": 25, "rotate": -1.2, "color": (35, 38, 50)},   # 6. Пол
    {"coord": (480, 165), "size": 23, "rotate": -1.8, "color": (45, 45, 60), "width": 40}, # 7. Кем выдан (ЦЕНТР)
    {"coord": (315, 292), "size": 25, "rotate": -1.5, "color": (40, 40, 55)},   # 8. Дата выд.
    {"coord": (565, 318), "size": 26, "rotate": -1.5, "color": (30, 45, 110)},  # 9. Код подр. (синий)
    {"coord": (825, 420), "size": 38, "rotate": -91.5, "color": (140, 30, 30)}, # 10. Номер (БОКОВОЙ)
    {"coord": (450, 55), "size": 34, "rotate": -1.0, "color": (130, 30, 30)}    # 11. Номер (ВЕРХНИЙ ДУБЛЬ)
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

# --- ФУНКЦИИ ОТРИСОВКИ ---

def draw_single_text(img, text, font, config):
    """Рисует обычную строку с поворотом"""
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # Создаем холст для текста с запасом
    txt_layer = Image.new("RGBA", (tw + 40, th + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    d.text((20, 10), text, font=font, fill=config["color"])
    
    if config["rotate"] != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    img.paste(txt_layer, (int(config["coord"][0]), int(config["coord"][1])), txt_layer)

def draw_multi_line_text(img, text, font, config):
    """Рисует текст с переносом и центрированием строк между собой"""
    chars_limit = config.get("width", 30)
    lines = textwrap.wrap(text, width=chars_limit)[:3]
    
    current_y = config["coord"][1]
    center_x = config["coord"][0]

    for line in lines:
        bbox = font.getbbox(line)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        start_x = center_x - (w // 2) # Выравнивание по центру оси X
        
        txt_layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_layer)
        d.text((20, 10), line, font=font, fill=config["color"])
        
        if config["rotate"] != 0:
            txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
        
        img.paste(txt_layer, (int(start_x), int(current_y)), txt_layer)
        current_y += h + 8

# --- ХЕНДЛЕРЫ ---

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
    if len(user_lines) < 10: return await message.answer(f"Надо 10 строк, а получено {len(user_lines)}")
    
    data = await state.get_data()
    await message.answer("⏳ Генерирую...")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, data['tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                text = user_lines[i]
                
                try: font = ImageFont.truetype(FONT_PATH, cfg["size"])
                except: font = ImageFont.load_default()
                
                # Поля 5 (индекс 4) и 7 (индекс 6) рисуем с переносом
                if i in [4, 6]:
                    draw_multi_line_text(img, text, font, cfg)
                else:
                    draw_single_text(img, text, font, cfg)
                
                # ДУБЛИРОВАНИЕ НОМЕРА (10-я строка дублируется по 11-м координатам)
                if i == 9:
                    cfg_v2 = FIELDS_CONFIG[10]
                    try: font_v2 = ImageFont.truetype(FONT_PATH, cfg_v2["size"])
                    except: font_v2 = ImageFont.load_default()
                    draw_single_text(img, text, font_v2, cfg_v2)

            # ЭФФЕКТЫ РЕАЛИЗМА
            res = img.convert("RGB")
            res = res.filter(ImageFilter.GaussianBlur(radius=0.35)) # Мягкость текста
            
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=92)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="ready.jpg"), caption="Готово!")
            await state.clear()
    except Exception as e:
        logging.error(e)
        await message.answer(f"Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
