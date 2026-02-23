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

# Загрузка настроек
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")  # Токен берется из настроек хостинга
TEMPLATES_DIR = "templates"
FONT_PATH = "fonts/font.ttf"

# Настройка логирования и сессии
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# Поля для проверки (ровно 10)
REQUIRED_FIELDS = [
    "Фамилия", "Имя", "Отчество", "Дата рождения", 
    "Место рождения", "Пол", "Кем выдан", 
    "Дата выдачи", "Код подразделения", "Серия и номер"
]

class Form(StatesGroup):
    browsing_templates = State()
    inputting_data = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_templates_list():
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
    return sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith('.jpg')])

def get_pagination_keyboard(index):
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_{index}"),
            InlineKeyboardButton(text="✅ Выбрать", callback_data=f"select_{index}"),
            InlineKeyboardButton(text="Далее ➡️", callback_data=f"next_{index}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    templates = get_templates_list()
    if not templates:
        await message.answer("Ошибка: В папке 'templates' нет файлов .jpg")
        return

    index = 0
    photo = FSInputFile(os.path.join(TEMPLATES_DIR, templates[index]))
    
    try:
        await message.answer_photo(
            photo=photo,
            caption=f"Выберите шаблон: {index + 1} из {len(templates)}",
            reply_markup=get_pagination_keyboard(index)
        )
        await state.set_state(Form.browsing_templates)
    except Exception as e:
        await message.answer(f"Ошибка при загрузке фото: {e}")

@dp.callback_query(F.data.startswith(("prev_", "next_")))
async def navigate(callback: types.CallbackQuery, state: FSMContext):
    templates = get_templates_list()
    action, index = callback.data.split("_")
    index = int(index)
    
    new_index = (index - 1) % len(templates) if action == "prev" else (index + 1) % len(templates)

    media = InputMediaPhoto(
        media=FSInputFile(os.path.join(TEMPLATES_DIR, templates[new_index])),
        caption=f"Выберите шаблон: {new_index + 1} из {len(templates)}"
    )
    
    try:
        await callback.message.edit_media(media=media, reply_markup=get_pagination_keyboard(new_index))
    except Exception as e:
        logging.error(f"Ошибка навигации: {e}")
    await callback.answer()

@dp.callback_query(F.data.startswith("select_"))
async def select(callback: types.CallbackQuery, state: FSMContext):
    templates = get_templates_list()
    index = int(callback.data.split("_")[1])
    await state.update_data(chosen_template=templates[index])
    
    await callback.message.answer(
        "✅ Шаблон выбран!\n\n"
        "Теперь пришлите данные **одним сообщением** (10 строк):\n"
        "1. Фамилия\n2. Имя\n3. Отчество\n4. Дата рождения\n5. Место рождения\n"
        "6. Пол\n7. Кем выдан\n8. Дата выдачи\n9. Код подразд.\n10. Серия и номер"
    )
    await state.set_state(Form.inputting_data)
    await callback.answer()

@dp.message(Form.inputting_data)
async def process_data(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    
    if len(lines) < 10:
        await message.answer(f"❌ Нужно 10 строк, а получено {len(lines)}. Попробуйте еще раз.")
        return

    data = await state.get_data()
    template_name = data.get('chosen_template')

    await message.answer("⏳ Генерирую изображение...")

    try:
        img_path = os.path.join(TEMPLATES_DIR, template_name)
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            
            # Загрузка шрифта
            font = ImageFont.truetype(FONT_PATH, 30) if os.path.exists(FONT_PATH) else ImageFont.load_default()
            
            # Координаты (подставь свои реальные!)
            coords = [
                (300, 100), (300, 150), (300, 200), (300, 250), (300, 300),
                (100, 350), (150, 400), (150, 500), (500, 500), (400, 600)
            ]

            for i in range(10):
                draw.text(coords[i], lines[i], fill="black", font=font)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            
            await message.answer_photo(
                photo=BufferedInputFile(buf.read(), filename="result.jpg"),
                caption="Готово! Чтобы начать заново: /start"
            )
            await state.clear()
            
    except Exception as e:
        logging.error(f"Ошибка при обработке: {e}")
        await message.answer(f"Произошла ошибка: {e}")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    if not TOKEN:
        print("Ошибка: Переменная BOT_TOKEN не установлена!")
    else:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            pass
