import asyncio
import os
import logging
import sys
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

# --- КОНФИГУРАЦИЯ ТЕКСТА (X, Y, Размер, Поворот, Цвет RGB) ---
FIELDS_CONFIG = [
    {"coord": (300, 100), "size": 45, "rotate": 0, "color": (0, 0, 0)},       # 1. Фамилия
    {"coord": (300, 160), "size": 45, "rotate": 0, "color": (0, 0, 0)},       # 2. Имя
    {"coord": (300, 220), "size": 45, "rotate": 0, "color": (0, 0, 0)},       # 3. Отчество
    {"coord": (300, 280), "size": 35, "rotate": 0, "color": (30, 30, 30)},    # 4. Дата рожд.
    {"coord": (300, 340), "size": 30, "rotate": 0, "color": (0, 0, 0)},       # 5. Место рожд.
    {"coord": (100, 400), "size": 35, "rotate": 0, "color": (0, 0, 0)},       # 6. Пол
    {"coord": (150, 460), "size": 25, "rotate": 0, "color": (40, 40, 40)},    # 7. Кем выдан
    {"coord": (150, 550), "size": 35, "rotate": 0, "color": (0, 0, 0)},       # 8. Дата выд.
    {"coord": (500, 550), "size": 35, "rotate": 0, "color": (0, 0, 150)},     # 9. Код подр.
    {"coord": (400, 650), "size": 50, "rotate": 0, "color": (180, 0, 0)}      # 10. Серия/Номер
]

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

def get_templates_list():
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
    return sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(('.jpg', '.jpeg'))])

def get_nav_keyboard(index):
    buttons = [[
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_{index}"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data=f"select_{index}"),
        InlineKeyboardButton(text="Далее ➡️", callback_data=f"next_{index}")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    templates = get_templates_list()
    if not templates:
        await message.answer("В папке 'templates' нет JPG файлов!")
        return
    
    idx = 0
    photo = FSInputFile(os.path.join(TEMPLATES_DIR, templates[idx]))
    await message.answer_photo(
        photo=photo,
        caption=f"Шаблон: {templates[idx]}\n{idx+1} из {len(templates)}",
        reply_markup=get_nav_keyboard(idx)
    )
    await state.set_state(Form.browsing_templates)

@dp.callback_query(F.data.startswith(("prev_", "next_")))
async def navigate(callback: types.CallbackQuery, state: FSMContext):
    templates = get_templates_list()
    action, idx = callback.data.split("_")
    idx = int(idx)
    new_idx = (idx - 1) % len(templates) if action == "prev" else (idx + 1) % len(templates)
    
    media = InputMediaPhoto(
        media=FSInputFile(os.path.join(TEMPLATES_DIR, templates[new_idx])),
        caption=f"Шаблон: {templates[new_idx]}\n{new_idx+1} из {len(templates)}"
    )
    await callback.message.edit_media(media=media, reply_markup=get_nav_keyboard(new_idx))
    await callback.answer()

@dp.callback_query(F.data.startswith("select_"))
async def select_tpl(callback: types.CallbackQuery, state: FSMContext):
    templates = get_templates_list()
    idx = int(callback.data.split("_")[1])
    await state.update_data(chosen_template=templates[idx])
    await callback.message.answer("✅ Отправь 10 строк данных (каждая с новой строки):")
    await state.set_state(Form.inputting_data)
    await callback.answer()

@dp.message(Form.inputting_data)
async def process_data(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(lines) < 10:
        await message.answer(f"Нужно 10 строк! Получено: {len(lines)}")
        return

    data = await state.get_data()
    template_path = os.path.join(TEMPLATES_DIR, data['chosen_template'])

    try:
        with Image.open(template_path) as img:
            img = img.convert("RGBA")
            
            for i in range(10):
                cfg = FIELDS_CONFIG[i]
                text = lines[i]
                
                # Явная загрузка шрифта с защитой от ошибок
                try:
                    font = ImageFont.truetype(FONT_PATH, cfg["size"])
                except:
                    logging.error(f"Шрифт не найден по пути {FONT_PATH}! Использую стандарт.")
                    font = ImageFont.load_default()

                # Отрисовка текста на прозрачном слое для поворота
                bbox = font.getbbox(text)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                
                txt_layer = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
                d = ImageDraw.Draw(txt_layer)
                d.text((5, 5), text, font=font, fill=cfg["color"])
                
                if cfg["rotate"] != 0:
                    txt_layer = txt_layer.rotate(cfg["rotate"], expand=True, resample=Image.BICUBIC)
                
                img.paste(txt_layer, cfg["coord"], txt_layer)

            # Сохранение
            res = img.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            
            await message.answer_photo(
                photo=BufferedInputFile(buf.read(), filename="result.jpg"),
                caption="Готово! /start для нового"
            )
            await state.clear()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        logging.error(e)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    if not TOKEN:
        print("НЕТ ТОКЕНА!")
    else:
        asyncio.run(main())
