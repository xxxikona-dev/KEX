import asyncio
import os
import logging
import sys
import textwrap
import re
import random
import sqlite3
from io import BytesIO
from datetime import datetime, timedelta
import uuid

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile, BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from dotenv import load_dotenv

# Импорт для CryptoBot API
from cryptopay import CryptoPay
from cryptopax.types import Invoice

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("538436:AAz9j6rKbh84ZUeahJnNfvG82bBjDF1JgOZ")  # Токен от @CryptoBot
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
DB_PATH = os.path.join(BASE_DIR, "users.db")

# ID администраторов (бесконечные токены)
ADMIN_IDS = [5153650495, 7915847801]  # ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ID

# Настройки CryptoBot
CRYPTOBOT_API_URL = "https://pay.crypt.bot/"  # Основной URL
TOKEN_PRICE_USDT = 2  # Цена одного токена в USDT

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, session=AiohttpSession())
dp = Dispatcher()

# Инициализация CryptoBot
crypto = CryptoPay(token=CRYPTOBOT_TOKEN, api_url=CRYPTOBOT_API_URL)

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
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount_tokens INTEGER,
            amount_usdt REAL,
            status TEXT DEFAULT 'pending',
            invoice_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
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

def update_payment_status(payment_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE payments SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE payment_id = ?",
        (status, payment_id)
    )
    conn.commit()
    conn.close()

def get_pending_payment(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT payment_id, amount_tokens, invoice_id FROM payments WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result

# Инициализация БД при запуске
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
    except: return None

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
    
    spacing = 150
    for y in range(-height, height * 2, spacing):
        for x in range(-width, width * 2, spacing * 2):
            text = random.choice(watermark_texts)
            
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            angle = random.randint(-30, 30)
            
            txt_img = Image.new("RGBA", (text_width + 100, text_height + 100), (0, 0, 0, 0))
            txt_draw = ImageDraw.Draw(txt_img)
            
            txt_draw.text((50, 50), text, font=font, fill=(255, 255, 255, random.randint(20, 40)), anchor="mm")
            
            txt_img = txt_img.rotate(angle, expand=1, resample=Image.BICUBIC)
            
            watermark_layer.alpha_composite(txt_img, (x + random.randint(-50, 50), y + random.randint(-50, 50)))
    
    for _ in range(500):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(255, 255, 255, random.randint(30, 70)))
    
    for _ in range(50):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, random.randint(15, 30)), width=random.randint(1, 3))
    
    watermarked = Image.alpha_composite(watermarked, watermark_layer)
    return watermarked

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
    user_id = call.from_user.id
    
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
    
    # Генерируем уникальный ID платежа
    payment_id = str(uuid.uuid4())
    
    try:
        # Создаем инвойс в CryptoBot
        amount_usdt = amount * TOKEN_PRICE_USDT
        invoice = await crypto.create_invoice(
            asset='USDT',
            amount=amount_usdt,
            description=f"Покупка {amount} токенов для бота",
            payload=payment_id  # Важно: этот payload вернется в вебхуке
        )
        
        # Сохраняем запись о платеже
        create_payment_record(user_id, amount, payment_id, invoice.invoice_id)
        
        # Создаем клавиатуру с кнопкой для оплаты
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice.pay_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_payment_{payment_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy_menu")]
        ])
        
        await call.message.edit_text(
            f"<b>💳 Счет на оплату</b>\n\n"
            f"Токенов: <b>{amount}</b>\n"
            f"Сумма: <b>{amount_usdt} USDT</b>\n\n"
            f"ID платежа: <code>{payment_id}</code>\n\n"
            "1. Нажмите кнопку \"Оплатить\"\n"
            "2. Оплатите счет в @CryptoBot\n"
            "3. Нажмите \"Я оплатил\" для проверки",
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
            
            # Возвращаемся в главное меню через 3 секунды
            await asyncio.sleep(3)
            await cmd_start(call.message, state)
        else:
            await call.answer("Платеж еще не обнаружен. Оплатите счет и нажмите снова.", show_alert=True)
            
    except Exception as e:
        logging.error(f"Error checking payment: {e}")
        await call.answer("Ошибка при проверке платежа", show_alert=True)

# Вебхук для CryptoBot (если используете вебхуки вместо polling)
@dp.message(lambda message: message.successful_payment is not None)
async def process_successful_payment(message: types.Message):
    """Обработка успешного платежа (если используется Telegram Payments)"""
    # Для CryptoBot этот метод может не использоваться
    pass

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
    
    await state.update_data(category=category, tpls=tpls)
    
    random_data = generate_random_data()
    await state.update_data(preview_data=random_data)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️", callback_data="p_0"),
        InlineKeyboardButton(text="✅ Выбрать", callback_data="s_0"),
        InlineKeyboardButton(text="➡️", callback_data="n_0")
    ]])
    
    await call.message.answer_photo(
        FSInputFile(os.path.join(cat_path, tpls[0])), 
        caption=f"Категория: <b>{category}</b>\nШаблон: <code>{tpls[0]}</code>", 
        reply_markup=kb, 
        parse_mode="HTML"
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
        new_idx = (idx - 1) % len(tpls) if act == "p" else (idx + 1) % len(tpls)
        await state.update_data(preview_data=generate_random_data())
        
        await call.message.edit_media(
            InputMediaPhoto(
                media=FSInputFile(os.path.join(TEMPLATES_DIR, category, tpls[new_idx])), 
                caption=f"Категория: <b>{category}</b>\nШаблон: <code>{tpls[new_idx]}</code>", 
                parse_mode="HTML"
            ), 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️", callback_data=f"p_{new_idx}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"s_{new_idx}"),
                InlineKeyboardButton(text="➡️", callback_data=f"n_{new_idx}")]])
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
    f1, f2, f_num = get_font_path(category, "1"), get_font_path(category, "2"), get_font_path(category, "num")
    
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
        with Image.open(os.path.join(TEMPLATES_DIR, category, data['chosen_tpl'])) as img:
            img = img.convert("RGBA")
            for i in range(10):
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

            img_with_watermarks = add_watermarks(img)
            
            res = img_with_watermarks.convert("RGB")
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
                caption=f"✅ Готово!\n{balance_msg}"
            )
            await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

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
    
    # Статистика платежей
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

async def main(): 
    # Для использования вебхуков (рекомендуется для продакшена):
    # await bot.set_webhook(url="https://your-domain.com/webhook")
    
    # Для использования polling (для разработки):
    await dp.start_polling(bot)

if __name__ == "__main__": 
    asyncio.run(main())