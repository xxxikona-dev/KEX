import asyncio
import os
import logging
import sys
import textwrap
import re
import random
import sqlite3
import hmac
import hashlib
from io import BytesIO
from datetime import datetime, timedelta
import uuid
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile, BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from dotenv import load_dotenv
from aiocryptopay import AioCryptoPay, Networks

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
CRYPTOBOT_WEBHOOK_SECRET = os.getenv("CRYPTOBOT_WEBHOOK_SECRET", "default_secret")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-domain.com/webhook/cryptobot")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
DB_PATH = os.path.join(BASE_DIR, "users.db")

# ID администраторов (бесконечные токены)
ADMIN_IDS = [5153650495]  # Добавьте свои ID

# Настройки CryptoBot
TOKEN_PRICE_USDT = 2  # Цена одного токена в USDT

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

# Инициализация CryptoBot
crypto = None
if CRYPTOBOT_TOKEN:
    try:
        # Используем основную сеть CryptoBot
        crypto = AioCryptoPay(token=CRYPTOBOT_TOKEN, network=Networks.MAIN_NET)
        print("✅ CryptoBot API инициализирован")
    except Exception as e:
        print(f"❌ Ошибка инициализации CryptoBot: {e}")
        crypto = None

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id TEXT UNIQUE,
            user_id INTEGER,
            amount_tokens INTEGER,
            amount_usdt REAL,
            status TEXT DEFAULT 'pending',
            invoice_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            payment_id TEXT,
            user_id INTEGER,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_user_tokens(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT tokens FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def add_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, tokens) VALUES (?, 0)", (user_id,))
    conn.commit()
    conn.close()

def deduct_token(user_id):
    if user_id in ADMIN_IDS:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tokens = tokens - 1 WHERE user_id = ? AND tokens > 0", (user_id,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def add_tokens(user_id, amount):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tokens = tokens + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def create_payment_record(user_id, tokens_amount, payment_id, invoice_id):
    usdt_amount = tokens_amount * TOKEN_PRICE_USDT
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO payments (payment_id, user_id, amount_tokens, amount_usdt, invoice_id) VALUES (?, ?, ?, ?, ?)",
        (payment_id, user_id, tokens_amount, usdt_amount, invoice_id)
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

def save_invoice(invoice_id, payment_id, user_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO invoices (invoice_id, payment_id, user_id, status) VALUES (?, ?, ?, ?)",
        (invoice_id, payment_id, user_id, status)
    )
    conn.commit()
    conn.close()

def get_payment_by_invoice(invoice_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT payment_id, user_id, amount_tokens, status FROM payments WHERE invoice_id = ?",
        (invoice_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result

def get_user_by_payment(payment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, amount_tokens FROM payments WHERE payment_id = ?",
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
    choosing_tokens_amount = State()

# --- ФУНКЦИИ ЗАГРУЗКИ ---
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
                    "blur": vals[11] if len(vals) > 11 else 0.25
                })
        return config
    except Exception as e:
        print(f"Ошибка загрузки конфига: {e}")
        return None

def get_font_path(category, font_type="1"):
    exts = ['.ttf', '.otf', '.TTF', '.OTF']
    folder = os.path.join(FONTS_DIR, category)
    if not os.path.exists(folder): return None
    for ext in exts:
        path = os.path.join(folder, font_type + ext)
        if os.path.exists(path): return path
    return None

def format_passport_number(text):
    clean = text.replace(" ", "")
    if len(clean) == 10 and clean.isdigit():
        return f"{clean[:2]} {clean[2:4]} {clean[4:]}"
    return text

# --- ГЕНЕРАЦИЯ РАНДОМНЫХ ДАННЫХ ---
def generate_random_data():
    first_names = ["АЛЕКСАНДР", "ДМИТРИЙ", "МАКСИМ", "СЕРГЕЙ", "АНДРЕЙ", "АЛЕКСЕЙ", "ИВАН", "ЕВГЕНИЙ", "МИХАИЛ", "ВЛАДИМИР"]
    last_names = ["ИВАНОВ", "ПЕТРОВ", "СИДОРОВ", "СМИРНОВ", "КУЗНЕЦОВ", "ПОПОВ", "ВАСИЛЬЕВ", "ЗАЙЦЕВ", "СОКОЛОВ", "МИХАЙЛОВ"]
    patronymics = ["АЛЕКСАНДРОВИЧ", "ДМИТРИЕВИЧ", "МАКСИМОВИЧ", "СЕРГЕЕВИЧ", "АНДРЕЕВИЧ", "АЛЕКСЕЕВИЧ", "ИВАНОВИЧ", "ЕВГЕНЬЕВИЧ", "МИХАЙЛОВИЧ", "ВЛАДИМИРОВИЧ"]
    birth_places = ["ГОР. МОСКВА", "ГОР. САНКТ-ПЕТЕРБУРГ", "ГОР. НОВОСИБИРСК", "ГОР. ЕКАТЕРИНБУРГ", "ГОР. КАЗАНЬ", "ГОР. НИЖНИЙ НОВГОРОД", "ГОР. ЧЕЛЯБИНСК", "ГОР. САМАРА", "ГОР. ОМСК", "ГОР. РОСТОВ-НА-ДОНУ"]
    issued_by = [
        "ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ГОР. МОСКВЫ",
        "УПРАВЛЕНИЕМ ВНУТРЕННИХ ДЕЛ ПО ЦАО",
        "ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ГОР. САНКТ-ПЕТЕРБУРГА",
        "МВД ПО РЕСПУБЛИКЕ ТАТАРСТАН",
        "ГУ МВД ПО КРАСНОДАРСКОМУ КРАЮ"
    ]
    
    year = random.randint(1950, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    birth_date = f"{day:02d}.{month:02d}.{year}"
    
    issue_year = year + random.randint(18, 45)
    issue_date = f"{random.randint(1, 28):02d}.{random.randint(1, 12):02d}.{issue_year}"
    
    return [
        random.choice(last_names),
        random.choice(first_names),
        random.choice(patronymics),
        birth_date,
        random.choice(birth_places),
        random.choice(["МУЖ.", "ЖЕН."]),
        random.choice(issued_by),
        issue_date,
        f"{random.randint(100, 999):03d}-{random.randint(100, 999):03d}",
        f"{random.randint(1000, 9999)} {random.randint(100000, 999999)}"
    ]

# --- ВОДЯНЫЕ ЗНАКИ ---
def add_watermarks(image):
    watermarked = image.copy().convert("RGBA")
    watermark_layer = Image.new("RGBA", watermarked.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark_layer)
    
    watermark_texts = ["DEMO", "SAMPLE", "NOT VALID", "ТЕСТ", "ОБРАЗЕЦ", "DEMO VERSION"]
    
    try:
        font_path = os.path.join(FONTS_DIR, "arial.ttf")
        if not os.path.exists(font_path):
            font_files = []
            for root, dirs, files in os.walk(FONTS_DIR):
                for file in files:
                    if file.endswith(('.ttf', '.TTF', '.otf', '.OTF')):
                        font_files.append(os.path.join(root, file))
            font_path = font_files[0] if font_files else None
        
        if font_path:
            font_size = 40
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    
    width, height = watermarked.size
    
    spacing = 100
    opacity_range = (40, 70)
    
    for y in range(-height, height * 2, spacing):
        for x in range(-width, width * 2, spacing * 2):
            text = random.choice(watermark_texts)
            
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            angle = random.randint(-30, 30)
            
            txt_img = Image.new("RGBA", (text_width + 100, text_height + 100), (0, 0, 0, 0))
            txt_draw = ImageDraw.Draw(txt_img)
            
            txt_draw.text((50, 50), text, font=font, 
                         fill=(255, 255, 255, random.randint(opacity_range[0], opacity_range[1])), 
                         anchor="mm")
            
            txt_img = txt_img.rotate(angle, expand=1, resample=Image.BICUBIC)
            
            watermark_layer.alpha_composite(txt_img, (x + random.randint(-50, 50), y + random.randint(-50, 50)))
    
    for _ in range(500):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(255, 255, 255, random.randint(50, 90)))
    
    for _ in range(50):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], 
                 fill=(255, 255, 255, random.randint(25, 45)),
                 width=random.randint(1, 3))
    
    watermarked = Image.alpha_composite(watermarked, watermark_layer)
    return watermarked

# --- ФУНКЦИЯ ДЛЯ СОЗДАНИЯ ПРЕДПРОСМОТРА ---
async def create_preview_with_watermark(category, template_name, random_data):
    try:
        config = get_config(category)
        if not config:
            return None
            
        f1, f2, f_num = get_font_path(category, "1"), get_font_path(category, "2"), get_font_path(category, "num")
        if not f1:
            return None
        
        template_path = os.path.join(TEMPLATES_DIR, category, template_name)
        with Image.open(template_path) as img:
            img = img.convert("RGBA")
            
            for i in range(min(10, len(config))):
                cfg = config[i]
                text = random_data[i]
                if i == 9:
                    text = format_passport_number(text)
                    curr_f = f_num if f_num else f1
                elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text): 
                    curr_f = f2
                else: 
                    curr_f = f1
                
                font = ImageFont.truetype(curr_f, cfg["size"])
                process_field(img, text, font, cfg)
                if i == 9 and len(config) > 10:
                    process_field(img, text, font, config[10])
            
            img_with_watermarks = add_watermarks(img)
            
            res = img_with_watermarks.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            return buf
    except Exception as e:
        logging.error(f"Ошибка создания предпросмотра: {e}")
        return None

# --- ЭФФЕКТЫ РЕАЛИЗМА ---
def add_noise_to_layer(layer, intensity=12):
    width, height = layer.size
    pixels = layer.load()
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > 0:
                noise = random.randint(-intensity, intensity)
                new_a = max(0, min(255, a + noise))
                pixels[x, y] = (r, g, b, new_a)
    return layer

# --- ОТРИСОВКА ---
def draw_text_on_layer(img, text, font, config):
    text = str(text).upper()
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    txt_layer = Image.new("RGBA", (tw + 400, th + 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_layer)
    fill_color = config["color"] + (config.get("alpha", 225),) 
    d.text(((tw + 400) // 2, (th + 400) // 2), text, font=font, fill=fill_color, anchor="mm")
    
    txt_layer = add_noise_to_layer(txt_layer)
    if config.get("rotate", 0) != 0:
        txt_layer = txt_layer.rotate(config["rotate"], expand=True, resample=Image.BICUBIC)
    if config.get("blur", 0) > 0:
        txt_layer = txt_layer.filter(ImageFilter.GaussianBlur(radius=config["blur"]))

    lw, lh = txt_layer.size
    offset_x = int(config["coord"][0] - (lw // 2))
    offset_y = int(config["coord"][1] - (lh // 2))
    img.alpha_composite(txt_layer, (offset_x, offset_y))

def process_field(img, text, font, config):
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

# --- ХЕНДЛЕРЫ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    user_id = message.from_user.id
    add_user(user_id)
    
    tokens = get_user_tokens(user_id)
    if user_id in ADMIN_IDS:
        balance_text = "♾️ Бесконечно (админ)"
    else:
        balance_text = f"💰 Баланс: {tokens} токенов"
    
    categories = get_categories()
    if not categories: 
        return await message.answer("Папка templates пуста!")
    
    buy_button = InlineKeyboardButton(text="💎 Купить токены", callback_data="buy_menu")
    
    kb = [[InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in categories]
    kb.append([buy_button])
    
    await message.answer(
        f"<b>Выберите категорию:</b>\n{balance_text}", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), 
        parse_mode="HTML"
    )
    await state.set_state(Form.choosing_category)

@dp.callback_query(F.data == "buy_menu")
async def buy_menu(call: types.CallbackQuery, state: FSMContext):
    if not crypto:
        await call.message.edit_text(
            "<b>❌ Платежная система временно недоступна</b>\n\n"
            "Пожалуйста, обратитесь к администратору для покупки токенов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_categories")
            ]]),
            parse_mode="HTML"
        )
        await call.answer()
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 токен (2 USDT)", callback_data="buy_1")],
        [InlineKeyboardButton(text="5 токенов (10 USDT)", callback_data="buy_5")],
        [InlineKeyboardButton(text="10 токенов (20 USDT)", callback_data="buy_10")],
        [InlineKeyboardButton(text="25 токенов (50 USDT)", callback_data="buy_25")],
        [InlineKeyboardButton(text="50 токенов (100 USDT)", callback_data="buy_50")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_categories")]
    ])
    
    await call.message.edit_text(
        "<b>💎 Покупка токенов</b>\n\n"
        f"Цена: <b>{TOKEN_PRICE_USDT} USDT</b> за 1 токен\n\n"
        "Выберите количество токенов для покупки:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.set_state(Form.choosing_tokens_amount)
    await call.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(call: types.CallbackQuery, state: FSMContext):
    amount = int(call.data.split("_")[1])
    user_id = call.from_user.id
    
    if not crypto:
        await call.message.edit_text(
            "❌ Платежная система недоступна. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="buy_menu")
            ]])
        )
        await call.answer()
        return
    
    # Генерируем уникальный ID платежа
    payment_id = str(uuid.uuid4())
    
    try:
        # Создаем инвойс в CryptoBot
        amount_usdt = amount * TOKEN_PRICE_USDT
        
        # Создаем счет с помощью aiocryptopay
        invoice = await crypto.create_invoice(
            asset='USDT',
            amount=amount_usdt,
            description=f"Покупка {amount} токенов",
            payload=payment_id,  # Важно: этот payload вернется в webhook
            expired_in=3600  # Счет действителен 1 час
        )
        
        # Сохраняем информацию о счете
        save_invoice(invoice.invoice_id, payment_id, user_id, 'active')
        create_payment_record(user_id, amount, payment_id, invoice.invoice_id)
        
        # Создаем клавиатуру с кнопкой для оплаты
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice.pay_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment_{payment_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy_menu")]
        ])
        
        await call.message.edit_text(
            f"<b>💳 Счет на оплату</b>\n\n"
            f"Токенов: <b>{amount}</b>\n"
            f"Сумма: <b>{amount_usdt} USDT</b>\n\n"
            f"ID платежа: <code>{payment_id}</code>\n\n"
            f"⏰ Счет действителен 1 час\n\n"
            "1. Нажмите кнопку \"Оплатить\"\n"
            "2. Оплатите счет в @CryptoBot\n"
            "3. Нажмите \"Проверить оплату\" или дождитесь автоматического зачисления",
            reply_markup=kb,
            parse_mode="HTML"
        )
        
        await state.set_state(Form.waiting_payment)
        
    except Exception as e:
        logging.error(f"Error creating invoice: {e}")
        await call.message.edit_text(
            "❌ Ошибка при создании счета. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="buy_menu")
            ]])
        )
    
    await call.answer()

@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment(call: types.CallbackQuery, state: FSMContext):
    payment_id = call.data.replace("check_payment_", "")
    user_id = call.from_user.id
    
    if not crypto:
        await call.answer("Платежная система недоступна", show_alert=True)
        return
    
    try:
        # Получаем информацию о платеже из БД
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT amount_tokens, invoice_id, status FROM payments WHERE payment_id = ? AND user_id = ?",
            (payment_id, user_id)
        )
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await call.answer("Платеж не найден!", show_alert=True)
            return
        
        amount_tokens, invoice_id, status = result
        
        if status == "completed":
            await call.answer("Платеж уже обработан! Токены зачислены.", show_alert=True)
            await cmd_start(call.message, state)
            return
        
        # Проверяем статус инвойса в CryptoBot
        invoices = await crypto.get_invoices(invoice_ids=[invoice_id])
        
        if invoices and invoices[0].status == 'paid':
            # Платеж подтвержден
            update_payment_status(payment_id, "completed")
            add_tokens(user_id, amount_tokens)
            
            new_balance = get_user_tokens(user_id)
            
            await call.message.edit_text(
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"Зачислено: <b>{amount_tokens} токенов</b>\n"
                f"Новый баланс: <b>{new_balance} токенов</b>\n\n"
                f"Спасибо за покупку!",
                parse_mode="HTML"
            )
            
            await asyncio.sleep(3)
            await cmd_start(call.message, state)
        else:
            await call.answer("❌ Платеж еще не обнаружен. Оплатите счет и нажмите снова.", show_alert=True)
            
    except Exception as e:
        logging.error(f"Error checking payment: {e}")
        await call.answer("Ошибка при проверке платежа", show_alert=True)

@dp.callback_query(F.data == "back_to_categories")
async def back_to_categories(call: types.CallbackQuery, state: FSMContext):
    await cmd_start(call.message, state)
    await call.answer()

@dp.callback_query(F.data.startswith("cat_"))
async def choose_cat(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split("_")[1]
    cat_path = os.path.join(TEMPLATES_DIR, category)
    tpls = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    if not tpls: 
        return await call.answer("Нет фото!", show_alert=True)
    
    await state.update_data(category=category, tpls=tpls, current_index=0)
    
    random_data = generate_random_data()
    await state.update_data(preview_data=random_data)
    
    preview_buf = await create_preview_with_watermark(category, tpls[0], random_data)
    
    if preview_buf:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️", callback_data="p_0"),
            InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
            InlineKeyboardButton(text="➡️", callback_data="n_0")
        ]])
        
        await call.message.answer_photo(
            BufferedInputFile(preview_buf.read(), filename="preview.jpg"),
            caption=f"📋 <b>Категория:</b> {category}\n"
                    f"🖼 <b>Шаблон:</b> {tpls[0]}\n\n"
                    f"<i>⚠️ На предпросмотре водяные знаки и тестовые данные</i>\n"
                    f"<i>✅ Готовый результат будет без водяных знаков</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️", callback_data="p_0"),
            InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
            InlineKeyboardButton(text="➡️", callback_data="n_0")
        ]])
        
        await call.message.answer_photo(
            FSInputFile(os.path.join(cat_path, tpls[0])),
            caption=f"📋 <b>Категория:</b> {category}\n🖼 <b>Шаблон:</b> {tpls[0]}",
            reply_markup=kb,
            parse_mode="HTML"
        )
    
    await state.set_state(Form.browsing_templates)
    await call.answer()

@dp.callback_query(F.data.startswith(("p_", "n_", "s_")))
async def nav_callback(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category, tpls = data.get("category"), data.get("tpls")
    current_index = data.get("current_index", 0)
    
    if not category or not tpls: 
        return await call.answer("Сессия истекла! Введите /start", show_alert=True)
    
    act, idx = call.data.split("_")
    idx = int(idx)
    
    if act == "s":
        await state.update_data(chosen_tpl=tpls[idx])
        
        user_id = call.from_user.id
        tokens = get_user_tokens(user_id)
        
        if user_id not in ADMIN_IDS and tokens < 1:
            await call.message.answer(
                "❌ У вас недостаточно токенов!\n\n"
                f"1 токен = {TOKEN_PRICE_USDT} USDT\n"
                "Купить можно в главном меню.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="💎 Купить токены", callback_data="buy_menu")
                ]])
            )
            await state.set_state(Form.choosing_category)
            await call.answer()
            return
        
        random_data = data.get("preview_data", generate_random_data())
        
        guide = (
            "<b>Введите 10 строк данных для заполнения:</b>\n\n"
            "<blockquote>"
            "1. Фамилия\n2. Имя\n3. Отчество\n4. Дата рождения (ДД.ММ.ГГГГ)\n"
            "5. Место рождения\n6. Пол (МУЖ. или ЖЕН.)\n7. Кем выдан документ\n"
            "8. Дата выдачи (ДД.ММ.ГГГГ)\n9. Код подразделения (000-000)\n10. Серия и номер"
            "</blockquote>\n"
            "<b>Пример заполнения (можно отредактировать):</b>\n"
            "<blockquote>"
            f"{random_data[0]}\n"
            f"{random_data[1]}\n"
            f"{random_data[2]}\n"
            f"{random_data[3]}\n"
            f"{random_data[4]}\n"
            f"{random_data[5]}\n"
            f"{random_data[6]}\n"
            f"{random_data[7]}\n"
            f"{random_data[8]}\n"
            f"{random_data[9]}"
            "</blockquote>"
        )
        
        await call.message.answer(guide, parse_mode="HTML")
        
        ready_text = "\n".join(random_data)
        await call.message.answer(
            f"<b>Готовые данные для копирования:</b>\n<code>{ready_text}</code>",
            parse_mode="HTML"
        )
        
        await state.set_state(Form.inputting_data)
    else:
        if act == "p":
            new_idx = (idx - 1) % len(tpls)
        else:
            new_idx = (idx + 1) % len(tpls)
        
        await state.update_data(current_index=new_idx)
        
        random_data = generate_random_data()
        await state.update_data(preview_data=random_data)
        
        preview_buf = await create_preview_with_watermark(category, tpls[new_idx], random_data)
        
        if preview_buf:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")
            ]])
            
            await call.message.delete()
            await call.message.answer_photo(
                BufferedInputFile(preview_buf.read(), filename="preview.jpg"),
                caption=f"📋 <b>Категория:</b> {category}\n"
                        f"🖼 <b>Шаблон:</b> {tpls[new_idx]}\n\n"
                        f"<i>⚠️ На предпросмотре водяные знаки и тестовые данные</i>\n"
                        f"<i>✅ Готовый результат будет без водяных знаков</i>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")
            ]])
            
            await call.message.edit_media(
                InputMediaPhoto(
                    media=FSInputFile(os.path.join(TEMPLATES_DIR, category, tpls[new_idx])),
                    caption=f"📋 <b>Категория:</b> {category}\n🖼 <b>Шаблон:</b> {tpls[new_idx]}",
                    parse_mode="HTML"
                ),
                reply_markup=kb
            )
    await call.answer()

@dp.message(Form.inputting_data)
async def process_data(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    if len(lines) < 10: 
        return await message.answer(f"⚠️ Нужно 10 строк! Сейчас {len(lines)}")
    
    data = await state.get_data()
    category = data['category']
    config = get_config(category)
    if not config:
        return await message.answer("❌ Ошибка загрузки конфигурации шаблона")
    
    f1, f2, f_num = get_font_path(category, "1"), get_font_path(category, "2"), get_font_path(category, "num")
    
    if not f1:
        return await message.answer("❌ Не найден шрифт для категории")
    
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        tokens = get_user_tokens(user_id)
        if tokens < 1:
            await message.answer(
                "❌ Недостаточно токенов!\n\n"
                f"1 токен = {TOKEN_PRICE_USDT} USDT\n"
                "Купить можно в главном меню.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="💎 Купить токены", callback_data="buy_menu")
                ]])
            )
            await state.clear()
            return

    try:
        template_path = os.path.join(TEMPLATES_DIR, category, data['chosen_tpl'])
        with Image.open(template_path) as img:
            img = img.convert("RGBA")
            for i in range(min(10, len(config))):
                cfg = config[i]
                text = lines[i]
                if i == 9:
                    text = format_passport_number(text)
                    curr_f = f_num if f_num else f1
                elif f2 and re.fullmatch(r'[0-9.\-/ ]+', text): 
                    curr_f = f2
                else: 
                    curr_f = f1
                
                font = ImageFont.truetype(curr_f, cfg["size"])
                process_field(img, text, font, cfg)
                if i == 9 and len(config) > 10:
                    process_field(img, text, font, config[10])

            res = img.convert("RGB")
            buf = BytesIO()
            res.save(buf, format="JPEG", quality=95)
            buf.seek(0)
            
            if user_id not in ADMIN_IDS:
                deduct_token(user_id)
                new_balance = get_user_tokens(user_id)
                balance_msg = f"✅ Токен списан. Остаток: {new_balance}"
            else:
                balance_msg = "✨ (админский режим)"
            
            await message.answer_photo(
                BufferedInputFile(buf.read(), filename="result.jpg"),
                caption=f"✅ Готово! Без водяных знаков.\n{balance_msg}"
            )
            await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        logging.error(f"Error processing image: {e}")

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    user_id = message.from_user.id
    add_user(user_id)
    tokens = get_user_tokens(user_id)
    
    if user_id in ADMIN_IDS:
        await message.answer("♾️ У вас бесконечное количество токенов (админ)")
    else:
        await message.answer(f"💰 Ваш баланс: {tokens} токенов\n\n1 токен = {TOKEN_PRICE_USDT} USDT")

@dp.message(Command("add_tokens"))
async def cmd_add_tokens(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды")
        return
    
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: /add_tokens <user_id> <количество>")
        return
    
    try:
        target_id = int(args[1])
        amount = int(args[2])
        
        if amount <= 0:
            await message.answer("Количество должно быть положительным")
            return
        
        add_tokens(target_id, amount)
        new_balance = get_user_tokens(target_id)
        
        await message.answer(f"✅ Добавлено {amount} токенов пользователю {target_id}\nНовый баланс: {new_balance}")
    except ValueError:
        await message.answer("Неверный формат чисел")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(tokens) FROM users")
    total_tokens = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT user_id, tokens FROM users ORDER BY tokens DESC LIMIT 10")
    top_users = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*), SUM(amount_usdt) FROM payments WHERE status = 'completed'")
    payments_stats = cursor.fetchone()
    total_payments = payments_stats[0] or 0
    total_usdt = payments_stats[1] or 0
    
    conn.close()
    
    stats_text = f"📊 <b>Статистика</b>\n\n"
    stats_text += f"👥 Всего пользователей: {total_users}\n"
    stats_text += f"💎 Всего токенов: {total_tokens}\n"
    stats_text += f"💳 Всего платежей: {total_payments}\n"
    stats_text += f"💰 Всего USDT: {total_usdt:.2f}\n\n"
    stats_text += "<b>Топ-10 пользователей:</b>\n"
    
    for i, (uid, tokens) in enumerate(top_users, 1):
        stats_text += f"{i}. ID: {uid} — {tokens} токенов\n"
    
    await message.answer(stats_text, parse_mode="HTML")

# --- WEBHOOK ДЛЯ CRYPTOBOT ---
async def cryptobot_webhook(request):
    """Обработка webhook уведомлений от CryptoBot"""
    try:
        # Проверяем подпись запроса (если настроено)
        signature = request.headers.get('crypto-pay-api-signature')
        body = await request.text()
        
        # Здесь можно добавить проверку подписи с CRYPTOBOT_WEBHOOK_SECRET
        
        data = await request.json()
        logging.info(f"Получен webhook от CryptoBot: {data}")
        
        # Проверяем тип уведомления
        if data.get('event') == 'invoice_paid':
            # Получаем данные инвойса
            invoice = data.get('payload', {})
            invoice_id = invoice.get('invoice_id')
            payload = invoice.get('payload')  # это наш payment_id
            status = invoice.get('status')
            
            if payload and status == 'paid':
                # Обновляем статус платежа
                update_payment_status(payload, 'completed')
                
                # Получаем информацию о платеже
                payment_info = get_user_by_payment(payload)
                if payment_info:
                    user_id, amount_tokens = payment_info
                    
                    # Начисляем токены
                    add_tokens(user_id, amount_tokens)
                    
                    # Пробуем уведомить пользователя
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ <b>Оплата подтверждена!</b>\n\n"
                            f"Зачислено: <b>{amount_tokens} токенов</b>\n"
                            f"Новый баланс: <b>{get_user_tokens(user_id)} токенов</b>",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"Не удалось уведомить пользователя {user_id}: {e}")
        
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Ошибка в webhook: {e}")
        return web.Response(status=500, text="Error")

async def on_startup(app):
    """Действия при запуске приложения"""
    # Устанавливаем webhook для бота (если используете)
    webhook_url = f"{WEBHOOK_URL}/bot"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    logging.info(f"Webhook для бота установлен: {webhook_url}")

async def on_shutdown(app):
    """Действия при остановке приложения"""
    await bot.delete_webhook()
    await bot.session.close()

def main():
    """Запуск приложения с aiohttp"""
    app = web.Application()
    
    # Настраиваем webhook для бота
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook/bot")
    
    # Добавляем эндпоинт для webhook от CryptoBot
    app.router.add_post('/webhook/cryptobot', cryptobot_webhook)
    
    # Настраиваем startup и shutdown
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Запускаем сервер
    web.run_app(app, host='0.0.0.0', port=8080)

if __name__ == "__main__":
    if WEBHOOK_URL and WEBHOOK_URL != "https://your-domain.com/webhook/cryptobot":
        # Запуск с webhook
        main()
    else:
        # Запуск с polling (для разработки)
        asyncio.run(dp.start_polling(bot))