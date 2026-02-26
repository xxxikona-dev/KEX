import asyncio
import os
import logging
import sys
import textwrap
import re
import random
import sqlite3
from io import BytesIO
from datetime import datetime
import uuid
import unicodedata

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile, BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from dotenv import load_dotenv

# Импорт для CryptoBot API
try:
    from aiocryptopay import AioCryptoPay, Networks
    CRYPTOPAY_AVAILABLE = True
except ImportError:
    print("⚠️ Библиотека aiocryptopay не установлена. Установите: pip install aiocryptopay")
    CRYPTOPAY_AVAILABLE = False

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
DB_PATH = os.path.join(BASE_DIR, "payments.db")

# ID администраторов (бесплатное создание фото)
ADMIN_IDS = [5153650495, 8225633174]  # Добавьте ID админов

# Цена одной генерации в USDT
PRICE_PER_PHOTO = 1  # 1 USDT за фото

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

# Инициализация CryptoBot
crypto = None
if CRYPTOBOT_TOKEN and CRYPTOPAY_AVAILABLE:
    try:
        crypto = AioCryptoPay(token=CRYPTOBOT_TOKEN, network=Networks.MAIN_NET)
        print("✅ CryptoBot API инициализирован")
    except Exception as e:
        print(f"❌ Ошибка инициализации CryptoBot: {e}")
        crypto = None

# --- БАЗА ДАННЫХ ДЛЯ ПЛАТЕЖЕЙ ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT DEFAULT 'pending',
            invoice_id TEXT,
            category TEXT,
            template TEXT,
            user_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def create_payment_record(payment_id, user_id, invoice_id, category, template, user_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO payments (payment_id, user_id, invoice_id, category, template, user_data, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (payment_id, user_id, invoice_id, category, template, user_data)
    )
    conn.commit()
    conn.close()
    return payment_id

def update_payment_status(payment_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE payments SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE payment_id = ?",
        (status, payment_id)
    )
    conn.commit()
    conn.close()

def get_payment_by_id(payment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, category, template, user_data, status FROM payments WHERE payment_id = ?",
        (payment_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result

# Инициализация БД
init_db()

class Form(StatesGroup):
    choosing_category = State()
    browsing_templates = State()
    inputting_data = State()
    waiting_payment = State()

# --- ФУНКЦИИ ЗАГРУЗКИ ---
def get_categories():
    if not os.path.exists(TEMPLATES_DIR): return []
    return [d for d in os.listdir(TEMPLATES_DIR) if os.path.isdir(os.path.join(TEMPLATES_DIR, d))]

def parse_config_line(line):
    """Парсит строку конфигурации"""
    vals = [float(x.strip()) for x in line.split(',')]
    return {
        "coord": (vals[0], vals[1]),
        "size": int(vals[2]),
        "rotate": vals[3],
        "color": (int(vals[4]), int(vals[5]), int(vals[6])),
        "alpha": int(vals[7]),
        "width": int(vals[8]),
        "spacing": vals[9],
        "lines": int(vals[10]),
        "blur": vals[11] if len(vals) > 11 else 0.25
    }

def get_config(category):
    """
    Загружает конфигурацию из coo.txt
    Если есть строка 'scode', то следующая строка берется как конфигурация для scode
    """
    path = os.path.join(TEMPLATES_DIR, category, "coo.txt")
    config = []
    scode_config = None
    has_scode = False
    
    if not os.path.exists(path): return None, None, False
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            scode_line_num = -1
            
            for i, line in enumerate(lines):
                line_num = i + 1
                clean_line = line.split('#')[0].strip()
                
                if not clean_line:
                    continue
                
                # Проверяем, есть ли на этой строке слово scode
                if clean_line.lower() == 'scode':
                    has_scode = True
                    scode_line_num = line_num
                    continue
                
                # Парсим конфигурацию
                cfg = parse_config_line(clean_line)
                
                # Если это строка сразу после scode, сохраняем как scode_config
                if has_scode and line_num == scode_line_num + 1:
                    scode_config = cfg
                else:
                    config.append(cfg)
        
        return config, scode_config, has_scode
    except Exception as e:
        print(f"Ошибка загрузки конфига: {e}")
        return None, None, False

def get_font_path(category, font_type="1"):
    """Получает путь к шрифту"""
    exts = ['.ttf', '.otf', '.TTF', '.OTF']
    folder = os.path.join(FONTS_DIR, category)
    
    if os.path.exists(folder):
        for ext in exts:
            path = os.path.join(folder, font_type + ext)
            if os.path.exists(path):
                return path
        
        for file in os.listdir(folder):
            if file.startswith(font_type) and any(file.lower().endswith(ext.lower()) for ext in exts):
                return os.path.join(folder, file)
    
    return None

def format_passport_number(text):
    clean = text.replace(" ", "")
    if len(clean) == 10 and clean.isdigit():
        return f"{clean[:2]} {clean[2:4]} {clean[4:]}"
    return text

# --- ФУНКЦИЯ ДЛЯ ТРАНСЛИТЕРАЦИИ ---
def transliterate_to_english(text):
    """
    Преобразует русские буквы в английские
    """
    translit_dict = {
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
        'Ж': 'ZH', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'KH', 'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SHCH',
        'Ы': 'Y', 'Э': 'E', 'Ю': 'YU', 'Я': 'YA',
        'а': 'A', 'б': 'B', 'в': 'V', 'г': 'G', 'д': 'D', 'е': 'E', 'ё': 'E',
        'ж': 'ZH', 'з': 'Z', 'и': 'I', 'й': 'Y', 'к': 'K', 'л': 'L', 'м': 'M',
        'н': 'N', 'о': 'O', 'п': 'P', 'р': 'R', 'с': 'S', 'т': 'T', 'у': 'U',
        'ф': 'F', 'х': 'KH', 'ц': 'TS', 'ч': 'CH', 'ш': 'SH', 'щ': 'SHCH',
        'ы': 'Y', 'э': 'E', 'ю': 'YU', 'я': 'YA'
    }
    
    result = []
    for char in text:
        if char in translit_dict:
            result.append(translit_dict[char])
        else:
            result.append(char)
    
    text = ''.join(result)
    text = re.sub(r'[^A-Za-z0-9]', '', text)
    
    return text.upper()

# --- ФУНКЦИЯ ДЛЯ ГЕНЕРАЦИИ SCODE СТРОК ---
def generate_scode_lines(data):
    """
    Генерирует две строки в формате scode из 10 строк данных
    """
    # Извлекаем данные
    lastname = transliterate_to_english(data[0])
    firstname = transliterate_to_english(data[1])
    patronymic = transliterate_to_english(data[2])
    birth_date = data[3].strip()
    gender = data[5].strip().upper()
    issue_date = data[7].strip()
    department_code = re.sub(r'[^0-9]', '', data[8])
    passport_number = re.sub(r'[^0-9]', '', data[9])
    
    # Парсим даты
    birth_day, birth_month, birth_year = birth_date.split('.')
    issue_day, issue_month, issue_year = issue_date.split('.')
    
    # Первая строка
    if len(lastname) > 9:
        lastname = lastname[:9]
    if len(firstname) > 7:
        firstname = firstname[:7]
    if len(patronymic) > 8:
        patronymic = patronymic[:8]
    
    line1 = f"PNRUS{lastname}<<{firstname}<{patronymic}3"
    line1 = line1.ljust(44, '<')
    
    # Вторая строка
    if len(passport_number) > 10:
        passport_number = passport_number[:10]
    elif len(passport_number) < 10:
        passport_number = passport_number.ljust(10, '0')
    
    birth_short = f"{birth_year[-2:]}{birth_month}{birth_day}"
    issue_short = f"{issue_year[-2:]}{issue_month}{issue_day}"
    
    if len(department_code) > 6:
        department_code = department_code[:6]
    elif len(department_code) < 6:
        department_code = department_code.ljust(6, '0')
    
    random_digits = f"{random.randint(0, 99):02d}"
    
    line2 = f"{passport_number}RUS{birth_short}{gender[0]}{'<' * 7}7{issue_short}{department_code}<{random_digits}"
    line2 = line2.ljust(44, '<')
    
    return [line1, line2]

# --- ГЕНЕРАЦИЯ РАНДОМНЫХ ДАННЫХ ---
def generate_random_data():
    first_names = ["АЛЕКСАНДР", "ДМИТРИЙ", "МАКСИМ", "СЕРГЕЙ", "АНДРЕЙ", "АЛЕКСЕЙ", "ИВАН", "ЕВГЕНИЙ", "МИХАИЛ", "ВЛАДИМИР"]
    last_names = ["ИВАНОВ", "ПЕТРОВ", "СИДОРОВ", "СМИРНОВ", "КУЗНЕЦОВ", "ПОПОВ", "ВАСИЛЬЕВ", "ЗАЙЦЕВ", "СОКОЛОВ", "МИХАЙЛОВ"]
    patronymics = ["АЛЕКСАНДРОВИЧ", "ДМИТРИЕВИЧ", "МАКСИМОВИЧ", "СЕРГЕЕВИЧ", "АНДРЕЕВИЧ", "АЛЕКСЕЕВИЧ", "ИВАНОВИЧ", "ЕВГЕНЬЕВИЧ", "МИХАЙЛОВИЧ", "ВЛАДИМИРОВИЧ"]
    birth_places = ["ГОР. МОСКВА", "ГОР. САНКТ-ПЕТЕРБУРГ", "ГОР. НОВОСИБИРСК", "ГОР. ЕКАТЕРИНБУРГ", "ГОР. КАЗАНЬ"]
    issued_by = [
        "ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ГОР. МОСКВЫ",
        "УПРАВЛЕНИЕМ ВНУТРЕННИХ ДЕЛ ПО ЦАО",
        "ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ГОР. САНКТ-ПЕТЕРБУРГА"
    ]
    
    year = random.randint(1970, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    birth_date = f"{day:02d}.{month:02d}.{year}"
    
    issue_year = year + random.randint(18, 25)
    issue_date = f"{random.randint(1, 28):02d}.{random.randint(1, 12):02d}.{issue_year}"
    
    passport_num = f"{random.randint(1000, 9999)}{random.randint(100000, 999999)}"
    
    return [
        random.choice(last_names)[:9],
        random.choice(first_names)[:7],
        random.choice(patronymics)[:8],
        birth_date,
        random.choice(birth_places),
        random.choice(["М", "Ж"]),
        random.choice(issued_by),
        issue_date,
        f"{random.randint(100, 999):03d}{random.randint(100, 999):03d}",
        passport_num[:10]
    ]

# --- ВОДЯНЫЕ ЗНАКИ ---
def add_watermarks(image):
    """Добавляет водяные знаки на изображение"""
    watermarked = image.copy().convert("RGBA")
    watermark_layer = Image.new("RGBA", watermarked.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark_layer)
    
    watermark_texts = ["DEMO", "SAMPLE", "NOT VALID", "ТЕСТ", "ОБРАЗЕЦ"]
    
    try:
        font_path = os.path.join(FONTS_DIR, "arial.ttf")
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, 40)
        else:
            font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    
    width, height = watermarked.size
    spacing = 150
    
    for y in range(-height, height * 2, spacing):
        for x in range(-width, width * 2, spacing * 2):
            text = random.choice(watermark_texts)
            angle = random.randint(-30, 30)
            
            txt_img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
            txt_draw = ImageDraw.Draw(txt_img)
            txt_draw.text((150, 50), text, font=font, fill=(255, 255, 255, 80), anchor="mm")
            txt_img = txt_img.rotate(angle, expand=1, resample=Image.BICUBIC)
            
            watermark_layer.alpha_composite(txt_img, (x + random.randint(-50, 50), y + random.randint(-50, 50)))
    
    for _ in range(300):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(255, 255, 255, 80))
    
    watermarked = Image.alpha_composite(watermarked, watermark_layer)
    return watermarked

# --- ЭФФЕКТЫ РЕАЛИЗМА ---
def add_noise_to_layer(layer, intensity=8):
    """Добавляет небольшой шум к слою"""
    width, height = layer.size
    pixels = layer.load()
    for y in range(height):
        for x in range(width):
            if x < width and y < height:
                try:
                    r, g, b, a = pixels[x, y]
                    if a > 0:
                        noise = random.randint(-intensity, intensity)
                        new_a = max(0, min(255, a + noise))
                        pixels[x, y] = (r, g, b, new_a)
                except:
                    pass
    return layer

# --- ИСПРАВЛЕННАЯ ОТРИСОВКА ТЕКСТА ---
def draw_text_on_layer(img, text, font, config):
    """Рисует текст на изображении"""
    text = str(text).upper()
    
    # Создаем временное изображение для текста
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    # Добавляем отступы
    padding = 20
    txt_layer = Image.new("RGBA", (tw + padding*2, th + padding*2), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    
    # Цвет текста с альфа-каналом
    fill_color = config["color"] + (config.get("alpha", 255),)
    
    # Рисуем текст по центру временного слоя
    d.text((padding + tw//2, padding + th//2), text, font=font, fill=fill_color, anchor="mm")
    
    # Добавляем шум
    txt_layer = add_noise_to_layer(txt_layer)
    
    # Поворот
    if config.get("rotate", 0) != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
    
    # Размытие
    if config.get("blur", 0) > 0:
        txt_layer = txt_layer.filter(ImageFilter.GaussianBlur(radius=config["blur"]))
    
    # Вставляем на основное изображение
    lw, lh = txt_layer.size
    offset_x = int(config["coord"][0] - (lw // 2))
    offset_y = int(config["coord"][1] - (lh // 2))
    
    img.alpha_composite(txt_layer, (offset_x, offset_y))
    return img

def process_field(img, text, font, config):
    """Обрабатывает поле с учетом многострочности"""
    text = text.upper()
    
    if config.get("lines", 1) > 1:
        chars_limit = config.get("width", 30)
        max_lines = config.get("lines", 3)
        lines = textwrap.wrap(text, width=chars_limit, break_long_words=False)[:max_lines]
        
        base_x, base_y = config["coord"]
        line_step = config["size"] + config.get("spacing", 10)
        start_y = base_y - line_step
        
        for i, line in enumerate(lines):
            line_cfg = config.copy()
            line_cfg["coord"] = (base_x, start_y + (i * line_step))
            draw_text_on_layer(img, line, font, line_cfg)
    else:
        draw_text_on_layer(img, text, font, config)

# --- ФУНКЦИЯ ДЛЯ СОЗДАНИЯ ПРЕДПРОСМОТРА ---
async def create_preview_with_watermark(category, template_name, random_data, scode_config=None, has_scode=False):
    try:
        config, _, _ = get_config(category)
        if not config:
            return None
        
        f1 = get_font_path(category, "1")
        f2 = get_font_path(category, "2")
        f3 = get_font_path(category, "3")
        f_num = get_font_path(category, "num")
        
        if not f1:
            return None
        
        template_path = os.path.join(TEMPLATES_DIR, category, template_name)
        with Image.open(template_path) as img:
            img = img.convert("RGBA")
            
            # Основные поля
            for i, cfg in enumerate(config):
                if i < len(random_data):
                    text = str(random_data[i])
                    
                    if i == 9:
                        curr_f = f_num if f_num else f1
                    elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text):
                        curr_f = f2
                    else:
                        curr_f = f1
                    
                    font = ImageFont.truetype(curr_f, cfg["size"])
                    draw_text_on_layer(img, text, font, cfg)
            
            # SCODE строки
            if has_scode and scode_config and f3:
                scode_lines = generate_scode_lines(random_data)
                line_height = scode_config["size"] + scode_config.get("spacing", 10)
                
                for j, line in enumerate(scode_lines):
                    line_cfg = scode_config.copy()
                    if j == 1:
                        line_cfg["coord"] = (scode_config["coord"][0], scode_config["coord"][1] + line_height)
                    
                    font = ImageFont.truetype(f3, line_cfg["size"])
                    draw_text_on_layer(img, line, font, line_cfg)
            
            img_with_watermarks = add_watermarks(img)
            res = img_with_watermarks.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            return buf
    except Exception as e:
        logging.error(f"Ошибка создания предпросмотра: {e}")
        return None

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    categories = get_categories()
    if not categories:
        return await message.answer("❌ Папка templates пуста!")
    
    kb = [[InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in categories]
    
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        price_text = "🆓 Бесплатно (админ)"
    else:
        price_text = f"💰 Стоимость: {PRICE_PER_PHOTO} USDT за фото"
    
    await message.answer(
        f"<b>Выберите категорию документа:</b>\n\n{price_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await state.set_state(Form.choosing_category)

@dp.callback_query(F.data.startswith("cat_"))
async def choose_cat(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split("_")[1]
    cat_path = os.path.join(TEMPLATES_DIR, category)
    tpls = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    
    if not tpls:
        return await call.answer("❌ Нет шаблонов!", show_alert=True)
    
    config, scode_config, has_scode = get_config(category)
    
    await state.update_data(
        category=category,
        tpls=tpls,
        current_index=0,
        scode_config=scode_config,
        has_scode=has_scode
    )
    
    random_data = generate_random_data()
    await state.update_data(preview_data=random_data)
    
    preview_buf = await create_preview_with_watermark(category, tpls[0], random_data, scode_config, has_scode)
    
    if preview_buf:
        total_templates = len(tpls)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️", callback_data="p_0"),
            InlineKeyboardButton(text=f"✅ Выбрать (1/{total_templates})", callback_data="s_0"),
            InlineKeyboardButton(text="➡️", callback_data="n_0")
        ]])
        
        await call.message.answer_photo(
            BufferedInputFile(preview_buf.read(), filename="preview.jpg"),
            caption=f"📋 <b>Категория:</b> {category}\n"
                    f"🖼 <b>Шаблон:</b> {tpls[0]} (1/{total_templates})\n\n"
                    f"<i>⚠️ На предпросмотре водяные знаки и тестовые данные</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    
    await state.set_state(Form.browsing_templates)
    await call.answer()

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category, tpls = data.get("category"), data.get("tpls")
    scode_config = data.get("scode_config")
    has_scode = data.get("has_scode", False)
    
    if not category or not tpls:
        return await call.answer("Сессия истекла! Введите /start", show_alert=True)
    
    act, idx = call.data.split("_")
    idx = int(idx)
    total_templates = len(tpls)
    
    if act == "s":
        await state.update_data(chosen_tpl=tpls[idx])
        
        random_data = data.get("preview_data", generate_random_data())
        
        guide = (
            "<b>Введите 10 строк данных для заполнения:</b>\n\n"
            "<blockquote>"
            "1. Фамилия\n2. Имя\n3. Отчество\n4. Дата рождения (ДД.ММ.ГГГГ)\n"
            "5. Место рождения\n6. Пол (М или Ж)\n7. Кем выдан документ\n"
            "8. Дата выдачи (ДД.ММ.ГГГГ)\n9. Код подразделения (000-000)\n10. Серия и номер (10 цифр)"
            "</blockquote>\n\n"
            "<b>Пример заполнения:</b>\n"
            "<blockquote>"
            f"{random_data[0]}\n{random_data[1]}\n{random_data[2]}\n{random_data[3]}\n"
            f"{random_data[4]}\n{random_data[5]}\n{random_data[6]}\n{random_data[7]}\n"
            f"{random_data[8]}\n{random_data[9]}"
            "</blockquote>"
        )
        
        await call.message.answer(guide, parse_mode="HTML")
        await state.set_state(Form.inputting_data)
    else:
        if act == "p":
            new_idx = (idx - 1) % total_templates
        else:
            new_idx = (idx + 1) % total_templates
        
        await state.update_data(current_index=new_idx)
        
        random_data = generate_random_data()
        await state.update_data(preview_data=random_data)
        
        preview_buf = await create_preview_with_watermark(category, tpls[new_idx], random_data, scode_config, has_scode)
        
        if preview_buf:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text=f"✅ Выбрать ({new_idx+1}/{total_templates})", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")
            ]])
            
            await call.message.delete()
            await call.message.answer_photo(
                BufferedInputFile(preview_buf.read(), filename="preview.jpg"),
                caption=f"📋 <b>Категория:</b> {category}\n"
                        f"🖼 <b>Шаблон:</b> {tpls[new_idx]} ({new_idx+1}/{total_templates})\n\n"
                        f"<i>⚠️ На предпросмотре водяные знаки и тестовые данные</i>",
                reply_markup=kb,
                parse_mode="HTML"
            )
    
    await call.answer()

@dp.message(Form.inputting_data)
async def process_data(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(lines) < 10:
        return await message.answer(f"⚠️ Нужно 10 строк! Сейчас {len(lines)}")
    
    data = await state.get_data()
    category = data['category']
    template = data['chosen_tpl']
    scode_config = data.get('scode_config')
    has_scode = data.get('has_scode', False)
    
    user_data = "\n".join(lines)
    user_id = message.from_user.id
    
    # Админы получают фото бесплатно
    if user_id in ADMIN_IDS:
        try:
            config, _, _ = get_config(category)
            if not config:
                await message.answer("❌ Ошибка загрузки конфигурации")
                return
            
            f1 = get_font_path(category, "1")
            f2 = get_font_path(category, "2")
            f3 = get_font_path(category, "3")
            f_num = get_font_path(category, "num")
            
            if not f1:
                await message.answer("❌ Не найден основной шрифт")
                return
            
            template_path = os.path.join(TEMPLATES_DIR, category, template)
            with Image.open(template_path) as img:
                img = img.convert("RGBA")
                
                # Основные поля
                for i, cfg in enumerate(config):
                    if i < len(lines):
                        text = str(lines[i])
                        
                        if i == 9:
                            curr_f = f_num if f_num else f1
                        elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text):
                            curr_f = f2
                        else:
                            curr_f = f1
                        
                        font = ImageFont.truetype(curr_f, cfg["size"])
                        draw_text_on_layer(img, text, font, cfg)
                
                # SCODE строки
                if has_scode and scode_config and f3:
                    scode_lines = generate_scode_lines(lines)
                    line_height = scode_config["size"] + scode_config.get("spacing", 10)
                    
                    for j, line in enumerate(scode_lines):
                        line_cfg = scode_config.copy()
                        if j == 1:
                            line_cfg["coord"] = (scode_config["coord"][0], scode_config["coord"][1] + line_height)
                        
                        font = ImageFont.truetype(f3, line_cfg["size"])
                        draw_text_on_layer(img, line, font, line_cfg)
                
                res = img.convert("RGB")
                buf = BytesIO()
                res.save(buf, format="JPEG", quality=95)
                buf.seek(0)
                
                await message.answer_photo(
                    BufferedInputFile(buf.read(), filename="result.jpg"),
                    caption="✅ Ваше фото готово! (Админский режим)"
                )
                
                await state.clear()
                return
        except Exception as e:
            await message.answer(f"❌ Ошибка: {str(e)}")
            return
    
    # Обычные пользователи - оплата
    if not crypto:
        await message.answer("❌ Платежная система недоступна. Попробуйте позже.")
        return
    
    payment_id = str(uuid.uuid4())
    
    try:
        invoice = await crypto.create_invoice(
            asset='USDT',
            amount=PRICE_PER_PHOTO,
            description=f"Генерация фото в категории {category}",
            payload=payment_id
        )
        
        if hasattr(invoice, 'pay_url'):
            pay_url = invoice.pay_url
        elif hasattr(invoice, 'url'):
            pay_url = invoice.url
        else:
            raise Exception("Не удалось найти URL для оплаты")
        
        if hasattr(invoice, 'invoice_id'):
            invoice_id = invoice.invoice_id
        elif hasattr(invoice, 'id'):
            invoice_id = invoice.id
        else:
            raise Exception("Не удалось найти ID инвойса")
        
        create_payment_record(payment_id, user_id, invoice_id, category, template, user_data)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_payment_{payment_id}")]
        ])
        
        await message.answer(
            f"<b>💳 Счет на оплату</b>\n\n"
            f"Сумма: <b>{PRICE_PER_PHOTO} USDT</b>\n\n"
            f"ID платежа: <code>{payment_id}</code>\n\n"
            "1. Нажмите кнопку \"Оплатить\"\n"
            "2. Оплатите счет в @CryptoBot\n"
            "3. Нажмите \"Я оплатил\" для получения фото",
            reply_markup=kb,
            parse_mode="HTML"
        )
        
        await state.set_state(Form.waiting_payment)
        
    except Exception as e:
        logging.error(f"Error creating invoice: {e}")
        await message.answer(f"❌ Ошибка при создании счета: {str(e)}\n\nПопробуйте позже.")

@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment(call: types.CallbackQuery, state: FSMContext):
    payment_id = call.data.replace("check_payment_", "")
    user_id = call.from_user.id
    
    if not crypto:
        await call.answer("Платежная система недоступна", show_alert=True)
        return
    
    try:
        payment_info = get_payment_by_id(payment_id)
        
        if not payment_info:
            await call.answer("Платеж не найден!", show_alert=True)
            return
        
        db_user_id, category, template, user_data, status = payment_info
        
        if db_user_id != user_id:
            await call.answer("Это не ваш платеж!", show_alert=True)
            return
        
        if status == "completed":
            await call.answer("Платеж уже обработан!", show_alert=True)
            return
        
        invoice_id = None
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT invoice_id FROM payments WHERE payment_id = ?", (payment_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            invoice_id = result[0]
        
        if invoice_id:
            try:
                invoices = await crypto.get_invoices(invoice_ids=[invoice_id])
                invoice = invoices[0] if invoices else None
            except:
                invoice = None
            
            if invoice and getattr(invoice, 'status', None) == 'paid':
                update_payment_status(payment_id, "completed")
                
                data_lines = user_data.split('\n')
                config, scode_config, has_scode = get_config(category)
                
                if not config:
                    await call.message.edit_text("❌ Ошибка загрузки конфигурации")
                    return
                
                f1 = get_font_path(category, "1")
                f2 = get_font_path(category, "2")
                f3 = get_font_path(category, "3")
                f_num = get_font_path(category, "num")
                
                if not f1:
                    await call.message.edit_text("❌ Не найден основной шрифт")
                    return
                
                template_path = os.path.join(TEMPLATES_DIR, category, template)
                with Image.open(template_path) as img:
                    img = img.convert("RGBA")
                    
                    for i, cfg in enumerate(config):
                        if i < len(data_lines):
                            text = str(data_lines[i])
                            
                            if i == 9:
                                curr_f = f_num if f_num else f1
                            elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text):
                                curr_f = f2
                            else:
                                curr_f = f1
                            
                            font = ImageFont.truetype(curr_f, cfg["size"])
                            draw_text_on_layer(img, text, font, cfg)
                    
                    if has_scode and scode_config and f3:
                        scode_lines = generate_scode_lines(data_lines)
                        line_height = scode_config["size"] + scode_config.get("spacing", 10)
                        
                        for j, line in enumerate(scode_lines):
                            line_cfg = scode_config.copy()
                            if j == 1:
                                line_cfg["coord"] = (scode_config["coord"][0], scode_config["coord"][1] + line_height)
                            
                            font = ImageFont.truetype(f3, line_cfg["size"])
                            draw_text_on_layer(img, line, font, line_cfg)
                    
                    res = img.convert("RGB")
                    buf = BytesIO()
                    res.save(buf, format="JPEG", quality=95)
                    buf.seek(0)
                    
                    await call.message.delete()
                    await call.message.answer_photo(
                        BufferedInputFile(buf.read(), filename="result.jpg"),
                        caption="✅ Ваше фото готово! Спасибо за покупку."
                    )
                    
                    await state.clear()
                    return
        
        await call.answer("❌ Платеж еще не обнаружен. Оплатите счет и нажмите снова.", show_alert=True)
            
    except Exception as e:
        logging.error(f"Error checking payment: {e}")
        await call.answer(f"Ошибка при проверке платежа: {str(e)}", show_alert=True)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM payments")
    total_payments = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM payments WHERE status = 'completed'")
    completed_payments = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = f"📊 <b>Статистика платежей</b>\n\n"
    stats_text += f"💰 Всего платежей: {total_payments}\n"
    stats_text += f"✅ Успешных: {completed_payments}"
    
    await message.answer(stats_text, parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())