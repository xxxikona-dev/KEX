import asyncio
import os
import logging
import sys
import textwrap
import re
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile, BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from dotenv import load_dotenv

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# Создаем папки, если их нет
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)

bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    choosing_category = State()
    browsing_templates = State()
    inputting_data = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_categories():
    if not os.path.exists(TEMPLATES_DIR): return []
    return [d for d in os.listdir(TEMPLATES_DIR) if os.path.isdir(os.path.join(TEMPLATES_DIR, d))]

def get_config(category):
    path = os.path.join(TEMPLATES_DIR, category, "coo.txt")
    config = []
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split('#')[0].strip()
                if not line: continue
                vals = [float(x.strip()) for x in line.split(',')]
                config.append({
                    "coord": (vals[0], vals[1]),
                    "size": int(vals[2]),
                    "rotate": vals[3],
                    "color": (int(vals[4]), int(vals[5]), int(vals[6])),
                    "alpha": int(vals[7]),
                    "width": int(vals[8]),
                    "spacing": vals[9],
                    "lines": int(vals[10]),
                    "blur": vals[11] if len(vals) > 11 else 0.15
                })
        return config
    except Exception as e:
        logging.error(f"Ошибка чтения coo.txt: {e}")
        return None

def get_font_path(category, font_type="1"):
    """Ищет шрифты 1.ttf, 2.ttf или num.otf в папке категории"""
    exts = ['.ttf', '.otf', '.TTF', '.OTF']
    folder = os.path.join(FONTS_DIR, category)
    if not os.path.exists(folder): return None
    for ext in exts:
        path = os.path.join(folder, font_type + ext)
        if os.path.exists(path): return path
    return None

def format_passport_number(text):
    """Превращает 0000000000 или 0000 000000 в 00  00  000000"""
    clean = text.replace(" ", "")
    if len(clean) == 10 and clean.isdigit():
        return f"{clean[:2]}  {clean[2:4]}  {clean[4:]}"
    return text

# --- ОТРИСОВКА ---

def draw_text_on_layer(img, text, font, config):
    text = str(text).upper()
    # Создаем временный слой для текста (с запасом под поворот)
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    txt_layer = Image.new("RGBA", (tw + 300, th + 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    
    fill_color = config["color"] + (config.get("alpha", 230),) 
    d.text(((tw + 300) // 2, (th + 300) // 2), text, font=font, fill=fill_color, anchor="mm")
    
    # Поворот
    if config.get("rotate", 0) != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    # Размытие
    if config.get("blur", 0) > 0:
        txt_layer = txt_layer.filter(ImageFilter.GaussianBlur(radius=config["blur"]))

    # Наложение
    lw, lh = txt_layer.size
    offset_x = int(config["coord"][0] - (lw // 2))
    offset_y = int(config["coord"][1] - (lh // 2))
    img.alpha_composite(txt_layer, (offset_x, offset_y))

def process_field(img, text, font, config):
    if config.get("lines", 1) > 1:
        chars_limit = config.get("width", 30)
        max_lines = config.get("lines", 3)
        lines = textwrap.wrap(text, width=chars_limit, break_long_words=False)[:max_lines]
        
        base_x, base_y = config["coord"]
        line_step = config["size"] + config.get("spacing", 10) 
        total_h = (len(lines) - 1) * line_step
        start_y = base_y - (total_h // 2)

        for i, line in enumerate(lines):
            line_cfg = config.copy()
            line_cfg["coord"] = (base_x, start_y + (i * line_step))
            draw_text_on_layer(img, line, font, line_cfg)
    else:
        draw_text_on_layer(img, text, font, config)

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    categories = get_categories()
    if not categories:
        return await message.answer("❌ Папка 'templates' пуста!")
    
    kb = [[InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in categories]
    await message.answer("<b>Выберите категорию:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await state.set_state(Form.choosing_category)

@dp.callback_query(F.data.startswith("cat_"))
async def choose_cat(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split("_")[1]
    cat_path = os.path.join(TEMPLATES_DIR, category)
    tpls = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    
    if not tpls:
        return await call.answer("❌ В папке нет фото!", show_alert=True)
    
    await state.update_data(category=category, tpls=tpls)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    
    await call.message.answer_photo(
        FSInputFile(os.path.join(cat_path, tpls[0])),
        caption=f"Категория: <b>{category}</b>\nШаблон: <code>{tpls[0]}</code>",
        reply_markup=kb, parse_mode="HTML"
    )
    await state.set_state(Form.browsing_templates)
    await call.answer()

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category, tpls = data.get("category"), data.get("tpls")
    
    if not category or not tpls:
        return await call.answer("Сессия истекла! Введите /start", show_alert=True)

    act, idx = call.data.split("_")
    idx = int(idx)
    
    if act == "s":
        await state.update_data(chosen_tpl=tpls[idx])
        guide = (
            "<b>Введите 10 строк данных:</b>\n\n"
            "<blockquote>1. Фамилия\n2. Имя\n3. Отчество\n4. Дата рожд.\n5. Место рожд.\n"
            "6. Пол\n7. Кем выдан\n8. Дата выд.\n9. Код подр.\n10. Серия и номер</blockquote>\n"
            "<b>Пример заполнения:</b>\n"
            "<blockquote>ИВАНОВ\nИВАН\nИВАНОВИЧ\n01.01.1990\nГОР. МОСКВА\nМУЖ.\n"
            "ОТДЕЛОМ УФМС РОССИИ\n10.10.2015\n770-001\n4510 123456</blockquote>"
        )
        await call.message.answer(guide, parse_mode="HTML")
        await state.set_state(Form.inputting_data)
    else:
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, category, tpls[new_idx])), 
            caption=f"Категория: <b>{category}</b>\nШаблон: <code>{tpls[new_idx]}</code>", parse_mode="HTML"), 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")]]))
    await call.answer()

@dp.message(Form.inputting_data)
async def process_data(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(lines) < 10:
        return await message.answer(f"⚠️ Нужно 10 строк, вы ввели {len(lines)}")
    
    data = await state.get_data()
    category = data['category']
    config = get_config(category)
    if not config: return await message.answer("❌ Ошибка: coo.txt не найден!")

    f1 = get_font_path(category, "1")
    f2 = get_font_path(category, "2")
    f_num = get_font_path(category, "num")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, category, data['chosen_tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = config[i]
                text = lines[i]

                # Логика подбора шрифта
                if i == 9: # Серия/Номер
                    text = format_passport_number(text)
                    curr_font_p = f_num if f_num else f1
                elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text): # Цифры/Даты
                    curr_font_p = f2
                else: # Буквы
                    curr_font_p = f1
                
                font = ImageFont.truetype(curr_font_p, cfg["size"])
                process_field(img, text, font, cfg)

                # Дублирование номера (11-я строка конфига)
                if i == 9 and len(config) > 10:
                    process_field(img, text, font, config[10])

            res = img.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="res.jpg"), caption="✅ Готово!")
            await state.clear()
    except Exception as e:
        logging.exception("Ошибка генерации")
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
