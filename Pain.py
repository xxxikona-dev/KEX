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

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# Создаем базовые папки, если их нет
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)

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
            # Игнорируем комментарии и пустые строки
            line = line.split('#')[0].strip()
            if not line: continue
            try:
                vals = [float(x.strip()) for x in line.split(',')]
                # x, y, size, rotate, r, g, b, alpha, width, spacing, lines, blur
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
            except Exception as e:
                logging.error(f"Ошибка в строке конфига: {line} -> {e}")
                continue
    return config

def get_font_path(category, is_num=False):
    exts = ['.ttf', '.otf', '.TTF', '.OTF']
    folder = os.path.join(FONTS_DIR, category)
    name = "num" if is_num else "1"
    if not os.path.exists(folder): return None
    for ext in exts:
        path = os.path.join(folder, name + ext)
        if os.path.exists(path): return path
    return None

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
    if not categories:
        return await message.answer("📁 Папка templates пуста! Создайте в ней папки-категории.")
    
    kb = [[InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in categories]
    await message.answer("Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(Form.choosing_category)

@dp.callback_query(F.data.startswith("cat_"))
async def choose_cat(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split("_")[1]
    cat_path = os.path.join(TEMPLATES_DIR, category)
    tpls = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    
    if not tpls:
        return await call.answer("❌ В этой категории нет изображений!", show_alert=True)
    
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
    act, idx = call.data.split("_")
    idx = int(idx)
    
    if act == "s":
        await state.update_data(chosen_tpl=tpls[idx])
        await call.message.answer("⌨️ Введите 10 строк данных для заполнения шаблона.")
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
async def process(message: types.Message, state: FSMContext):
    user_lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(user_lines) < 10: 
        return await message.answer(f"⚠️ Вы ввели {len(user_lines)} строк, а нужно 10.")
    
    data = await state.get_data()
    category = data['category']
    config = get_config(category)
    
    if not config or len(config) < 11: 
        return await message.answer("❌ Файл coo.txt отсутствует или заполнен неверно (нужно 11 строк).")

    main_font_p = get_font_path(category, False)
    num_font_p = get_font_path(category, True)
    
    if not main_font_p:
        return await message.answer(f"❌ Основной шрифт (1.ttf/otf) не найден в fonts/{category}/")

    try:
        with Image.open(os.path.join(TEMPLATES_DIR, category, data['chosen_tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
                cfg = config[i]
                # Шрифт для серии/номера (строка 10 в сообщении -> индекс 9)
                f_p = num_font_p if (i == 9 and num_font_p) else main_font_p
                font = ImageFont.truetype(f_p, cfg["size"])
                
                if cfg.get("lines", 1) > 1:
                    draw_multi_line_centered(img, user_lines[i], font, cfg)
                else:
                    draw_centered_text(img, user_lines[i], font, cfg)
                
                # Дубликат номера (11-я строка конфига)
                if i == 9:
                    cfg11 = config[10]
                    font11 = ImageFont.truetype(f_p, cfg11["size"])
                    draw_centered_text(img, user_lines[i], font11, cfg11)

            res = img.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            await message.answer_photo(BufferedInputFile(buf.read(), filename="ready.jpg"), caption="✅ Готово!")
            await state.clear()
    except Exception as e:
        logging.exception("Ошибка при генерации:")
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
