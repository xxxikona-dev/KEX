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

# --- КОНФИГУРАЦИЯ ---
# (X, Y, Размер, Поворот, Цвет)
FIELDS_CONFIG = [
    {"coord": (485, 485), "size": 30, "rotate": -1.2, "color": (40, 42, 55)},   # 1. Фамилия
    {"coord": (485, 550), "size": 30, "rotate": -1.2, "color": (40, 42, 55)},   # 2. Имя (ПОДНЯТО)
    {"coord": (485, 595), "size": 30, "rotate": -1.2, "color": (40, 42, 55)},   # 3. Отчество
    {"coord": (585, 635), "size": 28, "rotate": -1.0, "color": (35, 38, 50)},   # 4. Дата рожд.
    {"coord": (485, 685), "size": 21, "rotate": -1.0, "color": (40, 42, 55), "width": 28}, # 5. Место рожд.
    {"coord": (410, 640), "size": 28, "rotate": -1.0, "color": (35, 38, 50)},   # 6. Пол
    {"coord": (270, 185), "size": 23, "rotate": -1.5, "color": (45, 45, 60), "width": 45}, # 7. Кем выдан (ВЫШЕ)
    {"coord": (260, 315), "size": 27, "rotate": -1.3, "color": (40, 40, 55)},   # 8. Дата выд.
    {"coord": (580, 312), "size": 28, "rotate": -1.3, "color": (40, 40, 55)},   # 9. Код подр.
    {"coord": (820, 640), "size": 40, "rotate": -90.5, "color": (150, 30, 30)}, # 10. Номер НИЗ
    {"coord": (740, 305), "size": 36, "rotate": -1.5, "color": (140, 30, 30)}   # 11. Номер ВЕРХ (ПОВЕРНУТ)
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

def draw_simple_text(img, text, font, config):
    """Обычная отрисовка без сложного центрирования"""
    bbox = font.getbbox(text)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    txt_layer = Image.new("RGBA", (tw + 100, th + 100), (0, 0, 0, 0))
    ImageDraw.Draw(txt_layer).text((50, 50), text, font=font, fill=config["color"])
    
    if config["rotate"] != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    img.paste(txt_layer, (int(config["coord"][0]), int(config["coord"][1])), txt_layer)

def draw_multi_line(img, text, font, config):
    """Деление на строки для 'Кем выдан' и 'Место рождения'"""
    lines = textwrap.wrap(text, width=config.get("width", 30))[:3]
    curr_x, curr_y = config["coord"]
    
    for i, line in enumerate(lines):
        line_cfg = config.copy()
        line_cfg["coord"] = (curr_x, curr_y + (i * (config["size"] + 6)))
        draw_simple_text(img, line, font, line_cfg)

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    if not tpls: return await message.answer("Папка templates пуста!")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    await message.answer_photo(FSInputFile(os.path.join(TEMPLATES_DIR, tpls[0])), caption=f"Выбор: {tpls[0]}", reply_markup=kb)
    await state.set_state(Form.browsing_templates)

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    tpls = sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])
    act, idx = call.data.split("_")
    idx = int(idx)
    if act == "s":
        await state.update_data(tpl=tpls[idx])
        await call.message.answer("Пришли 10 строк данных.")
        await state.set_state(Form.inputting_data)
    else:
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        await call.message.edit_media(InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, tpls[new_idx])), caption=f"Выбор: {tpls[new_idx]}"), 
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                          InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                                          InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                                          InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")]]))

@dp.message(Form.inputting_data)
async def process(message: types.Message, state: FSMContext):
    user_lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(user_lines) < 10: return await message.answer(f"Нужно 10 строк!")
    
    data = await state.get_data()
    try:
        with Image.open(os.path.join(TEMPLATES_DIR, data['tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                try: font = ImageFont.truetype(FONT_PATH, cfg["size"])
                except: font = ImageFont.load_default()
                
                if i in [4, 6]:
                    draw_multi_line(img, user_lines[i], font, cfg)
                else:
                    draw_simple_text(img, user_lines[i], font, cfg)
                
                if i == 9: # Дубликат наверх
                    draw_simple_text(img, user_lines[i], font, FIELDS_CONFIG[10])

            res = img.convert("RGB")
            res = res.filter(ImageFilter.GaussianBlur(radius=0.3)) 
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="res.jpg"))
            await state.clear()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
