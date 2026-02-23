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
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# --- НАСТРОЙКИ ПУТЕЙ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "font.ttf")

# --- КОНФИГУРАЦИЯ ПОЛЕЙ ---
# coord: (x, y), size: размер, rotate: угол, color: цвет, width: макс. символов в строке
FIELDS_CONFIG = [
    {"coord": (485, 520), "size": 32, "rotate": -1, "color": (20, 20, 20)},      # 1. Фамилия
    {"coord": (485, 565), "size": 32, "rotate": -1, "color": (20, 20, 20)},      # 2. Имя
    {"coord": (485, 605), "size": 32, "rotate": -1, "color": (20, 20, 20)},      # 3. Отчество
    {"coord": (550, 645), "size": 28, "rotate": -1, "color": (20, 20, 20)},      # 4. Дата рожд.
    {"coord": (620, 680), "size": 24, "rotate": -1, "color": (20, 20, 20), "width": 25}, # 5. Место рожд. (центр блока)
    {"coord": (480, 645), "size": 28, "rotate": -1, "color": (20, 20, 20)},      # 6. Пол
    {"coord": (480, 160), "size": 26, "rotate": -1, "color": (30, 30, 30), "width": 35}, # 7. Кем выдан (центр верхней части)
    {"coord": (310, 285), "size": 28, "rotate": -1, "color": (20, 20, 20)},      # 8. Дата выд.
    {"coord": (550, 315), "size": 28, "rotate": -1, "color": (0, 0, 120)},      # 9. Код подр.
    {"coord": (820, 450), "size": 40, "rotate": -90, "color": (150, 0, 0)}      # 10. Серия/Номер (вертикально справа)
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def draw_multi_line_text(img, text, font, config):
    """Рисует текст, разбитый на строки, с центрированием строк друг относительно друга"""
    chars_limit = config.get("width", 30)
    lines = textwrap.wrap(text, width=chars_limit)[:3] # Максимум 3 строки
    
    current_y = config["coord"][1]
    center_x = config["coord"][0]

    for line in lines:
        bbox = font.getbbox(line)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        
        # Центрируем строку относительно координаты X
        start_x = center_x - (w // 2)
        
        txt_layer = Image.new("RGBA", (w + 20, h + 20), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_layer)
        d.text((10, 5), line, font=font, fill=config["color"])
        
        if config["rotate"] != 0:
            txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
        
        img.paste(txt_layer, (int(start_x), int(current_y)), txt_layer)
        current_y += h + 12 # Межстрочный интервал

def get_templates():
    return sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])

def get_kb(idx):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data=f"p_{idx}"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{idx}"),
        InlineKeyboardButton(text="➡️", callback_data=f"n_{idx}")
    ]])

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    tpls = get_templates()
    if not tpls: return await message.answer("Папка 'templates' пуста!")
    await message.answer_photo(FSInputFile(os.path.join(TEMPLATES_DIR, tpls[0])), 
                               caption=f"Выбор: {tpls[0]}", reply_markup=get_kb(0))
    await state.set_state(Form.browsing_templates)

@dp.callback_query(F.data.startswith(("p_", "n_")))
async def nav(call: types.CallbackQuery):
    tpls = get_templates()
    act, idx = call.data.split("_")
    new_idx = (int(idx) - 1) % len(tpls) if act == "p" else (int(idx) + 1) % len(tpls)
    await call.message.edit_media(InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, tpls[new_idx])), 
                                                  caption=f"Выбор: {tpls[new_idx]}"), reply_markup=get_kb(new_idx))

@dp.callback_query(F.data.startswith("s_"))
async def sel(call: types.CallbackQuery, state: FSMContext):
    tpls = get_templates()
    await state.update_data(tpl=tpls[int(call.data.split("_")[1])])
    await call.message.answer("Пришли 10 строк данных (каждая с новой строки):")
    await state.set_state(Form.inputting_data)

@dp.message(Form.inputting_data)
async def process(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(lines) < 10: return await message.answer(f"Надо 10 строк, а тут {len(lines)}")
    
    data = await state.get_data()
    await message.answer("✍️ Рисую...")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, data['tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                try:
                    font = ImageFont.truetype(FONT_PATH, cfg["size"])
                except:
                    font = ImageFont.load_default()
                
                # Поля 5 (индекс 4) и 7 (индекс 6) рисуем с переносом
                if i in [4, 6]:
                    draw_multi_line_text(img, lines[i], font, cfg)
                else:
                    # Обычные поля
                    bbox = font.getbbox(lines[i])
                    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
                    txt = Image.new("RGBA", (tw+20, th+20), (0,0,0,0))
                    ImageDraw.Draw(txt).text((10,5), lines[i], font=font, fill=cfg["color"])
                    if cfg["rotate"] != 0: txt = txt.rotate(cfg["rotate"], expand=True)
                    img.paste(txt, cfg["coord"], txt)

            out = img.convert("RGB")
            buf = BytesIO()
            out.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="res.jpg"))
            await state.clear()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
