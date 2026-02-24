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

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

class Form(StatesGroup):
    choosing_category = State()
    browsing_templates = State()
    inputting_data = State()

# --- ФУНКЦИИ ЗАГРУЗКИ ---

def get_categories():
    if not os.path.exists(TEMPLATES_DIR): return []
    return [d for d in os.listdir(TEMPLATES_DIR) if os.path.isdir(os.path.join(TEMPLATES_DIR, d))]

def get_config(category):
    path = os.path.join(TEMPLATES_DIR, category, "coo.txt")
    config = []
    if not os.path.exists(path): return None
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

def get_font_path(category, font_type="1"):
    exts = ['.ttf', '.otf', '.TTF', '.OTF']
    folder = os.path.join(FONTS_DIR, category)
    if not os.path.exists(folder): return None
    for ext in exts:
        path = os.path.join(folder, font_type + ext)
        if os.path.exists(path): return path
    return None

def format_passport_number(text):
    """Исправляет 0000 000000 на 00 00 000000"""
    clean_text = text.replace(" ", "")
    if len(clean_text) == 10 and clean_text.isdigit():
        return f"{clean_text[:2]} {clean_text[2:4]} {clean_text[4:]}"
    return text

# --- ФУНКЦИИ ОТРИСОВКИ ---

def draw_centered_text(img, text, font, config):
    text = str(text).upper()
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    txt_layer = Image.new("RGBA", (tw + 250, th + 250), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    
    alpha = config.get("alpha", 230)
    fill_color = config["color"] + (alpha,) 
    d.text(((tw + 250) // 2, (th + 250) // 2), text, font=font, fill=fill_color, anchor="mm")
    
    if config.get("rotate", 0) != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    
    blur_radius = config.get("blur", 0.15)
    if blur_radius > 0:
        txt_layer = txt_layer.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    lw, lh = txt_layer.size
    offset_x = int(config["coord"][0] - (lw // 2))
    offset_y = int(config["coord"][1] - (lh // 2))
    img.alpha_composite(txt_layer, (offset_x, offset_y))

def draw_multi_line_centered(img, text, font, config):
    text = str(text).upper()
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
        draw_centered_text(img, line, font, line_cfg)

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    categories = get_categories()
    if not categories: return await message.answer("Папка templates пуста!")
    kb = [[InlineKeyboardButton(text=cat, callback_data=f"cat_{cat}")] for cat in categories]
    await message.answer("Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(Form.choosing_category)

@dp.callback_query(F.data.startswith("cat_"))
async def choose_cat(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split("_")[1]
    cat_path = os.path.join(TEMPLATES_DIR, category)
    tpls = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.jpg', '.jpeg'))])
    if not tpls: return await call.answer("Нет фото!", show_alert=True)
    await state.update_data(category=category, tpls=tpls)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    await call.message.answer_photo(FSInputFile(os.path.join(cat_path, tpls[0])), 
                                   caption=f"Категория: {category}\nШаблон: {tpls[0]}", reply_markup=kb)
    await state.set_state(Form.browsing_templates)
    await call.answer()

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category, tpls = data.get("category"), data.get("tpls")
    act, idx = call.data.split("_")
    idx = int(idx)
    if act == "s":
        await state.update_data(chosen_tpl=tpls[idx])
        
        # Инструкция с пояснением и примером
        guide_text = (
            "<b>Введите 10 строк данных для заполнения:</b>\n\n"
            "<blockquote>"
            "1. Фамилия\n"
            "2. Имя\n"
            "3. Отчество\n"
            "4. Дата рождения (ДД.ММ.ГГГГ)\n"
            "5. Место рождения\n"
            "6. Пол (МУЖ. или ЖЕН.)\n"
            "7. Кем выдан документ\n"
            "8. Дата выдачи (ДД.ММ.ГГГГ)\n"
            "9. Код подразделения (000-000)\n"
            "10. Серия и номер (10 цифр)"
            "</blockquote>\n"
            "<b>Пример заполнения:</b>\n"
            "<blockquote>"
            "ИВАНОВ\n"
            "ИВАН\n"
            "ИВАНОВИЧ\n"
            "15.01.1985\n"
            "ГОР. МОСКВА\n"
            "МУЖ.\n"
            "ОТДЕЛОМ УФМС РОССИИ ПО ГОР. МОСКВЕ В РАЙОНЕ ХАМОВНИКИ\n"
            "20.10.2010\n"
            "770-001\n"
            "4510 123456"
            "</blockquote>"
        )
        
        await call.message.answer(guide_text, parse_mode="HTML")
        await state.set_state(Form.inputting_data)
    else:
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(os.path.join(TEMPLATES_DIR, category, tpls[new_idx])), 
            caption=f"Категория: {category}\nШаблон: {tpls[new_idx]}"), 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")]]))
    await call.answer()

@dp.message(Form.inputting_data)
async def process(message: types.Message, state: FSMContext):
    user_lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(user_lines) < 10: return await message.answer(f"⚠️ Нужно ровно 10 строк! Вы ввели: {len(user_lines)}")
    
    data = await state.get_data()
    category = data['category']
    config = get_config(category)
    
    font1_p = get_font_path(category, "1")
    font2_p = get_font_path(category, "2")
    font_num_p = get_font_path(category, "num")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, category, data['chosen_tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = config[i]
                text = user_lines[i]
                
                # Авто-форматирование и выбор шрифта для серии/номера
                if i == 9:
                    text = format_passport_number(text)
                    f_path = font_num_p if font_num_p else font1_p
                # Шрифт для цифр и спецсимволов
                elif font2_p and re.fullmatch(r'[0-9.\-/ ]+', text):
                    f_path = font2_p
                else:
                    f_path = font1_p
                
                font = ImageFont.truetype(f_path, cfg["size"])
                
                if cfg.get("lines", 1) > 1:
                    draw_multi_line_centered(img, text, font, cfg)
                else:
                    draw_centered_text(img, text, font, cfg)
                
                # Дублирование на вторую страницу
                if i == 9:
                    cfg11 = config[10]
                    font11 = ImageFont.truetype(f_path, cfg11["size"])
                    draw_centered_text(img, text, font11, cfg11)

            res = img.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="ready.jpg"), caption="✅ Готово!")
            await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка при обработке: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
