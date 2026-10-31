import os
import time
import csv
import json
import sqlite3
import threading
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request, render_template_string

# ==========================================
# ۱. تنظیمات اصلی سیستم (Configuration)
# ==========================================
TOKEN = "9064316900:AAGJYseRH6G53osbsJ1F51ueV6dAyw2ajQU"
ADMIN_IDS = [7037291902]          # آیدی عددی ادمین‌ها (مثال: [7037291902, 123456789])
ADMIN_GROUP_ID = -1000000000000   # آیدی گروه (با زدن /setgroup داخل گروه، خودکار تنظیم می‌شود)
COURSE_CHANNEL_ID = "@THERYANC"
COURSE_CHANNEL_LINK = "https://t.me/theRyanc" 
SUPPORT_USERNAME = "@loopfine"
WEB_ADMIN_PASS = "123456"

bot = telebot.TeleBot(TOKEN)
DB_NAME = "lms_database_v7.db"

user_states = {}

TYPE_NAMES = {
    'any': 'آزاد (هر نوع فایلی) 🗂',
    'photo': 'فقط عکس 🖼',
    'video': 'فقط ویدیو 🎥',
    'document': 'فقط سند/فایل (PDF, ZIP) 📄',
    'audio_voice': 'فقط صوت یا وویس 🎙'
}

# ==========================================
# ۲. راه‌اندازی و مدیریت پایگاه داده (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS student_codes (
                    code TEXT PRIMARY KEY,
                    student_name TEXT,
                    used_by INTEGER DEFAULT NULL,
                    created_at TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    access_code TEXT,
                    joined_at TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    description TEXT,
                    allowed_type TEXT DEFAULT 'any',
                    required_count INTEGER DEFAULT 1,
                    created_at TEXT,
                    deadline_at TEXT DEFAULT NULL,
                    group_msg_id INTEGER DEFAULT NULL
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    assignment_id INTEGER,
                    status TEXT,
                    files_json TEXT,
                    score INTEGER DEFAULT 0,
                    snooze_count INTEGER DEFAULT 0,
                    updated_at TEXT,
                    UNIQUE(user_id, assignment_id)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    assignment_id INTEGER,
                    remind_at TEXT,
                    sent INTEGER DEFAULT 0
                )''')
    conn.commit()
    conn.close()

init_db()

def db_query(query, args=(), fetchone=False, commit=False):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, args)
    if commit:
        conn.commit()
        last_id = c.lastrowid
        conn.close()
        return last_id
    res = c.fetchone() if fetchone else c.fetchall()
    conn.close()
    return res

for adm in ADMIN_IDS:
    db_query("DELETE FROM users WHERE user_id=?", (adm,), commit=True)

def get_admin_group():
    row = db_query("SELECT value FROM settings WHERE key='admin_group_id'", fetchone=True)
    if row and row[0]:
        return int(row[0])
    return ADMIN_GROUP_ID if ADMIN_GROUP_ID != -1000000000000 else None

# ==========================================
# ۳. داشبورد مدیریت تحت وب (Flask Web Panel)
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <title>پنل مدیریت تکالیف و امتیازات | Super LMS V7</title>
    <style>
        body { font-family: Tahoma, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }
        .container { max-width: 1150px; margin: auto; background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h1, h2 { color: #eab308; }
        .stats { display: flex; gap: 15px; margin-bottom: 25px; }
        .card { background: #334155; padding: 15px; border-radius: 8px; flex: 1; text-align: center; }
        .card h3 { margin: 0; font-size: 28px; color: #38bdf8; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; background: #0f172a; margin-bottom: 30px; }
        th, td { border: 1px solid #334155; padding: 10px; text-align: center; }
        th { background: #1e293b; color: #eab308; }
        .done { color: #4ade80; font-weight: bold; }
        .snooze { color: #facc15; }
        .rejected { color: #f87171; font-weight: bold; }
        .score-badge { background: #eab308; color: #0f172a; padding: 3px 8px; border-radius: 6px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 داشبورد هوشمند مدیریت دوره و امتیازات (V7)</h1>
        <div class="stats">
            <div class="card"><h3>{{ total_codes }}</h3><p>کل کدهای تعریف شده</p></div>
            <div class="card"><h3>{{ total_users }}</h3><p>دانش‌پذیران فعال</p></div>
            <div class="card"><h3>{{ total_assigns }}</h3><p>تکالیف ثبت شده</p></div>
            <div class="card"><h3>{{ total_done }}</h3><p>تکالیف تحویل شده</p></div>
        </div>
        <h2>📋 جزئیات ارسال تکالیف و امتیازات</h2>
        <table>
            <tr>
                <th>نام دانش‌پذیر</th>
                <th>کد اختصاصی</th>
                <th>یوزرنیم</th>
                <th>تکلیف</th>
                <th>وضعیت</th>
                <th>امتیاز دریافتی</th>
                <th>تعداد فایل</th>
                <th>تعویق</th>
                <th>زمان ثبت</th>
            </tr>
            {% for row in records %}
            <tr>
                <td>{{ row[0] }}</td>
                <td><code>{{ row[1] }}</code></td>
                <td>@{{ row[2] }}</td>
                <td>#{{ row[3] }} - {{ row[4] }}</td>
                <td class="{{ row[5] }}">{{ row[6] }}</td>
                <td><span class="score-badge">⭐ {{ row[7] }} امتیاز</span></td>
                <td>{{ row[8] }} فایل</td>
                <td>{{ row[9] }} بار</td>
                <td>{{ row[10] }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return "✅ Super LMS Bot Server V7 is Running!"

@app.route('/admin')
def web_admin():
    pwd = request.args.get('pass')
    if pwd != WEB_ADMIN_PASS:
        return "<h2 style='color:red;text-align:center;margin-top:50px;'>⛔ دسترسی غیرمجاز! رمز عبور را وارد کنید: /admin?pass=YOUR_PASS</h2>", 403
    
    total_codes = db_query("SELECT COUNT(*) FROM student_codes", fetchone=True)[0]
    total_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    total_assigns = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
    total_done = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]

    records_raw = db_query('''
        SELECT u.full_name, u.access_code, u.username, a.id, a.title, s.status, s.score, s.files_json, s.snooze_count, s.updated_at
        FROM submissions s
        JOIN users u ON s.user_id = u.user_id
        JOIN assignments a ON s.assignment_id = a.id
        ORDER BY s.updated_at DESC
    ''')
    
    records = []
    for r in records_raw:
        if r[5] == 'DONE': st_class, st_fa = 'done', 'تحویل شده ✅'
        elif r[5] == 'REJECTED': st_class, st_fa = 'rejected', 'رد شده 🔄'
        else: st_class, st_fa = 'snooze', 'در انتظار / تعویق ⏳'
        
        f_list = json.loads(r[7]) if r[7] else []
        records.append((r[0], r[1], r[2], r[3], r[4], st_class, st_fa, r[6], len(f_list), r[8], r[9]))

    return render_template_string(
        HTML_TEMPLATE, 
        total_codes=total_codes, total_users=total_users, 
        total_assigns=total_assigns, total_done=total_done, records=records
    )

def run_web():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_web, daemon=True).start()

# ==========================================
# ۴. توابع کمکی، تایم‌لاین و تابلو زنده گروه (با فرمت امن HTML)
# ==========================================
def clean_html(text):
    if not text: return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def is_subscribed(user_id):
    if user_id in ADMIN_IDS:
        return True
    if COURSE_CHANNEL_ID == "@YOUR_CHANNEL_ID" or not COURSE_CHANNEL_ID.startswith("@"):
        return True
    try:
        member = bot.get_chat_member(COURSE_CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return True

def is_authenticated(user_id):
    if user_id in ADMIN_IDS:
        return True
    row = db_query("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return True if row else False

def get_student_info(user_id):
    row = db_query("SELECT full_name, access_code, username FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row if row else ("ناشناس", "---", "ندارد")

def get_student_total_score(user_id):
    row = db_query("SELECT SUM(score) FROM submissions WHERE user_id=? AND status='DONE'", (user_id,), fetchone=True)
    return row[0] if row and row[0] else 0

def is_deadline_passed(deadline_str):
    if not deadline_str or deadline_str == "نامحدود":
        return False
    try:
        dl_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M:%S")
        return datetime.now() > dl_dt
    except:
        return False

def format_deadline_status(deadline_str):
    if not deadline_str or deadline_str == "نامحدود":
        return "♾ نامحدود"
    try:
        dl_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.now()
        if now_dt > dl_dt:
            return f"⛔ منقضی شده ({deadline_str[:16]})"
        diff = dl_dt - now_dt
        hours = int(diff.total_seconds() // 3600)
        mins = int((diff.total_seconds() % 3600) // 60)
        return f"⏳ تا {deadline_str[:16]} ({hours} ساعت و {mins} دقیقه مانده)"
    except:
        return deadline_str

def build_group_board_html(assign_id):
    """ساخت متن تابلو وضعیت تکلیف در گروه با فرمت HTML (ضد خطا برای هشتگ‌ها)"""
    assign = db_query("SELECT title, description, allowed_type, required_count, created_at, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
    if not assign:
        return "تکلیف یافت نشد."
    title, desc, allowed_type, req_count, created_at, deadline_at = assign
    
    subs = db_query('''
        SELECT u.full_name, u.access_code, s.updated_at, s.score, s.files_json
        FROM submissions s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.assignment_id=? AND s.status='DONE'
        ORDER BY s.updated_at ASC
    ''', (assign_id,))
    
    dl_text = format_deadline_status(deadline_at)
    text = (
        f"📢 <b>{clean_html(title)} (تکلیف #{assign_id}) ثبت شد</b>\n"
        f"📝 <b>توضیحات:</b> {clean_html(desc)}\n"
        f"📂 <b>نوع فایل:</b> {TYPE_NAMES.get(allowed_type, 'آزاد')} (حداقل {req_count} فایل)\n"
        f"⏰ <b>مهلت ارسال:</b> {clean_html(dl_text)}\n"
        f"────────────────────\n"
        f"👥 <b>فرستنده ها ({len(subs)} نفر):</b>\n\n"
    )
    if not subs:
        text += "📭 <i>هنوز هیچ دانش‌پذیری این تکلیف را ارسال نکرده است.</i>\n"
    else:
        for idx, (fname, acode, up_time, score, f_json) in enumerate(subs, 1):
            f_cnt = len(json.loads(f_json)) if f_json else 0
            badge = "⚡" if score == 3 else "⭐"
            text += (
                f"👤 <b>{idx}. {clean_html(fname)}</b> (کد: <code>{clean_html(acode)}</code>)\n"
                f"   └ 🕒 تایم فایل: <code>{up_time[:16]}</code> | 📂 {f_cnt} فایل | {badge} <b>امتیاز: {score}</b>\n\n"
            )
            
    text += f"🔍 #تکلیف_{assign_id} #تابلو_تکلیف_{assign_id}"
    return text

def update_or_send_group_board(assign_id):
    """ارسال یا بروزرسانی تابلو تکلیف در گروه مدیریت (اگر پیام پاک شده باشد، مجدد می‌فرستد)"""
    grp_id = get_admin_group()
    if not grp_id:
        return None
    
    assign = db_query("SELECT group_msg_id FROM assignments WHERE id=?", (assign_id,), fetchone=True)
    if not assign:
        return None
    
    board_html = build_group_board_html(assign_id)
    msg_id = assign[0]
    
    if msg_id:
        try:
            bot.edit_message_text(board_html, grp_id, msg_id, parse_mode="HTML")
            return msg_id
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return msg_id
            print(f"Board edit failed, sending new board: {e}")
    
    # اگر قبلاً تابلو نداشته یا پیام قبلی پاک شده، یک تابلو جدید در گروه می‌فرستد
    try:
        sent = bot.send_message(grp_id, board_html, parse_mode="HTML")
        db_query("UPDATE assignments SET group_msg_id=? WHERE id=?", (sent.message_id, assign_id), commit=True)
        return sent.message_id
    except Exception as e:
        print(f"Send board error to group {grp_id}: {e}")
        return None

# ==========================================
# ۵. موتور زمان‌بند هوشمند (Job Scheduler)
# ==========================================
def reminder_worker():
    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due_reminders = db_query(
                "SELECT id, user_id, assignment_id FROM reminders WHERE sent=0 AND remind_at <= ?", 
                (now_str,)
            )
            for rem_id, uid, aid in due_reminders:
                db_query("UPDATE reminders SET sent=1 WHERE id=?", (rem_id,), commit=True)
                sub = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, aid), fetchone=True)
                if sub and sub[0] == 'DONE':
                    continue
                
                assign = db_query("SELECT title, description, allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (aid,), fetchone=True)
                if assign and not is_deadline_passed(assign[4]):
                    send_assignment_prompt(uid, aid, assign[0], assign[1], assign[2], assign[3], assign[4], is_reminder=True)
        except Exception as e:
            print(f"Scheduler Error: {e}")
        time.sleep(30)

threading.Thread(target=reminder_worker, daemon=True).start()

# ==========================================
# ۶. کیبوردهای پوشه‌بندی شده در پایین چت (ReplyKeyboard - حداکثر ۲ لایه)
# ==========================================
# --- لایه ۱ و ۲ پنل مدیریت (پایین صفحه چت) ---
def admin_reply_root():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        KeyboardButton("👥 پوشه مدیریت دانش‌پذیران"),
        KeyboardButton("📚 پوشه مدیریت تکالیف و تایم‌لاین"),
        KeyboardButton("📊 پوشه گزارشات، همگانی و تنظیمات")
    )
    return markup

def admin_reply_folder_students():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("➕ ساخت کد دانش‌پذیر"),
        KeyboardButton("📋 لیست کدها و امتیازات")
    )
    markup.add(KeyboardButton("🔙 بازگشت به منوی اصلی"))
    return markup

def admin_reply_folder_assignments():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("➕ ثبت تکلیف جدید"),
        KeyboardButton("⏰ تغییر مهلت (تایم‌لاین)"),
        KeyboardButton("📂 دریافت فایل‌های تکلیف"),
        KeyboardButton("🔄 بروزرسانی تابلو گروه")
    )
    markup.add(KeyboardButton("🔙 بازگشت به منوی اصلی"))
    return markup

def admin_reply_folder_reports():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📊 آمار و لینک داشبورد"),
        KeyboardButton("📥 خروجی اکسل (CSV)"),
        KeyboardButton("📢 ارسال پیام همگانی"),
        KeyboardButton("🗑 ریست کل دیتابیس")
    )
    markup.add(KeyboardButton("🔙 بازگشت به منوی اصلی"))
    return markup

# --- لایه ۱ و ۲ پنل دانش‌پذیر (پایین صفحه چت) ---
def student_reply_root():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        KeyboardButton("📚 پوشه تکالیف و تمرین‌ها"),
        KeyboardButton("🗂 پوشه کارنامه و پشتیبانی")
    )
    return markup

def student_reply_folder_assignments():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📌 آخرین تکلیف دوره"),
        KeyboardButton("📋 لیست همه تکالیف")
    )
    markup.add(KeyboardButton("🔙 بازگشت به منوی اصلی"))
    return markup

def student_reply_folder_profile():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🗂 کارنامه و امتیازات من"),
        KeyboardButton("👩‍💻 ارتباط با پشتیبانی")
    )
    markup.add(KeyboardButton("🔙 بازگشت به منوی اصلی"))
    return markup

def student_submission_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        KeyboardButton("✅ تایید نهایی و ارسال به استاد"),
        KeyboardButton("🔙 انصراف و پاک کردن فایل‌ها")
    )
    return markup

def cancel_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔙 انصراف و بازگشت"))
    return markup

# ==========================================
# ۷. توابع ارسال پیام‌های سیستمی
# ==========================================
def send_join_wall(chat_id):
    markup = InlineKeyboardMarkup()
    clean_link = COURSE_CHANNEL_LINK if COURSE_CHANNEL_LINK.startswith("http") else f"https://t.me/{COURSE_CHANNEL_ID.replace('@', '')}"
    markup.add(InlineKeyboardButton("📢 ورود به کانال دوره", url=clean_link))
    markup.add(InlineKeyboardButton("✅ عضو شدم (ادامه)", callback_data="verify_join"))
    bot.send_message(
        chat_id,
        "⛔ <b>مرحله اول: عضویت در کانال دوره</b>\n\nبرای استفاده از سامانه تکالیف، ابتدا در کانال دوره عضو شوید و سپس دکمه زیر را بزنید:",
        reply_markup=markup,
        parse_mode="HTML"
    )

def send_login_prompt(chat_id):
    user_states[chat_id] = {'state': 'WAIT_LOGIN_CODE'}
    bot.send_message(
        chat_id,
        "🔐 <b>مرحله دوم: احراز هویت دانش‌پذیر</b>\n\n"
        "لطفاً <b>کد ورود اختصاصی</b> خود را (که از استاد دریافت کرده‌اید) ارسال کنید:",
        parse_mode="HTML"
    )

def send_assignment_prompt(chat_id, assign_id, title, desc, allowed_type='any', req_count=1, deadline_at=None, is_reminder=False, is_redo=False):
    expired = is_deadline_passed(deadline_at)
    markup = InlineKeyboardMarkup(row_width=1)
    
    if not expired or is_redo:
        markup.add(
            InlineKeyboardButton("✅ انجام دادم (ارسال فایل تکلیف)", callback_data=f"done_{assign_id}"),
            InlineKeyboardButton("⏳ یک ساعت دیگر یادآوری کن", callback_data=f"snooze_{assign_id}")
        )
    markup.add(
        InlineKeyboardButton("🆘 نیاز به راهنمایی دارم", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")
    )

    if is_redo:
        prefix = "⚠️ <b>تکلیف شما توسط استاد رد شد! لطفاً مجدداً ارسال کنید:</b>\n\n"
    elif is_reminder:
        prefix = "⏰ <b>یادآوری خودکار تکلیف!</b> (۱ ساعت گذشت)\n\n"
    else:
        prefix = "📚 <b>تکلیف دوره:</b>\n\n"

    type_fa = TYPE_NAMES.get(allowed_type, 'آزاد')
    dl_status = format_deadline_status(deadline_at)
    
    text = (
        f"{prefix}"
        f"📌 <b>عنوان تمرین (#{assign_id}):</b> {clean_html(title)}\n"
        f"📝 <b>توضیحات:</b>\n{clean_html(desc)}\n\n"
        f"📂 <b>نوع فایل مجاز:</b> {type_fa}\n"
        f"🔢 <b>حداقل تعداد فایل:</b> {req_count} فایل\n"
        f"⏰ <b>مهلت ارسال:</b> {clean_html(dl_status)}\n"
        f"🏆 <b>امتیاز تحویل در ۲۴ ساعت اول:</b> ۳ امتیاز (بعد از آن ۱ امتیاز)\n\n"
    )
    if expired and not is_redo:
        text += "⛔ <b>مهلت ارسال این تکلیف به پایان رسیده و قفل شده است!</b>"
    else:
        text += "لطفاً وضعیت انجام این تمرین را از دکمه‌های زیر مشخص کنید:"

    try:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        print(f"Could not send to {chat_id}: {e}")

# ==========================================
# ۸. هندلرهای دستورات، استارت و اتصال گروه (/setgroup)
# ==========================================
@bot.message_handler(commands=['setgroup'])
def set_group_handler(message):
    """ثبت گروه مدیریت و ارسال فوری تابلوهای تکالیف موجود به گروه"""
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        bot.reply_to(message, f"⛔ آیدی شما (<code>{uid}</code>) در لیست ادمین‌های ربات نیست!", parse_mode="HTML")
        return
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "⚠️ این دستور را باید <b>داخل گروه مدیریت</b> ارسال کنید!", parse_mode="HTML")
        return
    
    grp_id = message.chat.id
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_group_id', ?)", (str(grp_id),), commit=True)
    bot.reply_to(
        message,
        f"✅ <b>این گروه با موفقیت به عنوان «مرکز مدیریت تکالیف» ثبت شد!</b>\n"
        f"🆔 آیدی گروه: <code>{grp_id}</code>\n\n"
        f"⏳ در حال ساخت و همگام‌سازی تابلوهای تکالیف در این گروه...",
        parse_mode="HTML"
    )
    # پاک کردن آیدی پیام‌های گروه قبلی و ارسال تابلو برای تکالیف موجود در همین گروه
    db_query("UPDATE assignments SET group_msg_id=NULL", commit=True)
    assigns = db_query("SELECT id FROM assignments ORDER BY id ASC")
    for (aid,) in assigns:
        update_or_send_group_board(aid)
        time.sleep(0.3)

@bot.message_handler(commands=['start'])
def start_handler(message):
    if message.chat.type in ['group', 'supergroup']:
        return
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return
    
    if uid in ADMIN_IDS:
        user_states[uid] = {'state': 'IDLE'}
        grp = get_admin_group()
        grp_st = f"✅ متصل (<code>{grp}</code>)" if grp else "⚠️ متصل نشده (ربات را در گروه ادمین کرده و /setgroup بزنید)"
        bot.send_message(
            message.chat.id,
            f"👑 <b>به پنل مدیریت سامانه آموزش خوش آمدید!</b>\n"
            f"📡 وضعیت گروه مدیریت: {grp_st}\n\n"
            f"👇 از <b>لیست دکمه‌های پایین صفحه (پوشه‌ها)</b> بخش مورد نظر را انتخاب کنید:",
            reply_markup=admin_reply_root(),
            parse_mode="HTML"
        )
        return

    if not is_authenticated(uid):
        send_login_prompt(message.chat.id)
        return

    user_states[uid] = {'state': 'IDLE'}
    st_name, st_code, _ = get_student_info(uid)
    total_pts = get_student_total_score(uid)
    bot.send_message(
        message.chat.id,
        f"سلام <b>{clean_html(st_name)}</b> عزیز! خوش آمدی 🎓\n"
        f"🔑 کد اختصاصی: <code>{clean_html(st_code)}</code>\n"
        f"🏆 مجموع امتیازات شما: <b>{total_pts} امتیاز</b>\n\n"
        f"👇 از <b>دکمه‌های پایین صفحه (پوشه‌ها)</b> بخش مورد نظر خود را انتخاب کنید:",
        reply_markup=student_reply_root(),
        parse_mode="HTML"
    )

# ==========================================
# ۹. مدیریت کال‌بک‌های عملیات روی پیام‌ها (انجام تکلیف، رد تکلیف، تغییر مهلت)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def master_callback_router(call):
    uid = call.from_user.id
    data = call.data
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if data == "verify_join":
        if is_subscribed(uid):
            try: bot.delete_message(chat_id, msg_id)
            except: pass
            if is_authenticated(uid):
                st_name, _, _ = get_student_info(uid)
                bot.send_message(chat_id, f"✅ خوش آمدید <b>{clean_html(st_name)}</b> عزیز!\nاز منوی پایین صفحه استفاده کنید:", reply_markup=student_reply_root(), parse_mode="HTML")
            else:
                send_login_prompt(chat_id)
        else:
            bot.answer_callback_query(call.id, "❌ هنوز در کانال عضو نشده‌اید!", show_alert=True)
        return

    if data.startswith("editdl_"):
        if uid not in ADMIN_IDS: return
        aid = int(data.replace("editdl_", ""))
        user_states[uid] = {'state': 'ADMIN_EDIT_DEADLINE_HOURS', 'assign_id': aid}
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            f"⏰ <b>تغییر مهلت ارسال تکلیف شماره #{aid}</b>\n\n"
            "مشخص کنید از <b>همین لحظه</b> تا چند ساعت دیگر مهلت ارسال باز باشد؟\n"
            "🔹 یک عدد انگلیسی (مثلاً <code>24</code> برای ۲۴ ساعت) بفرستید.\n"
            "🔹 برای <b>نامحدود کردن</b> عدد <code>0</code> را بفرستید.\n"
            "🔹 برای <b>قفل کردن فوری</b> عبارت <code>lock</code> را بفرستید:",
            reply_markup=cancel_menu(),
            parse_mode="HTML"
        )
        return

    if data.startswith("settype_"):
        if uid not in ADMIN_IDS: return
        st_data = user_states.get(uid, {})
        if st_data.get('state') != 'ADMIN_WAIT_TYPE': return

        chosen_type = data.replace("settype_", "")
        user_states[uid]['allowed_type'] = chosen_type
        user_states[uid]['state'] = 'ADMIN_WAIT_COUNT'
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            f"✅ نوع فایل: <b>{TYPE_NAMES.get(chosen_type)}</b>\n\n"
            "🔢 حالا مشخص کنید دانش‌پذیر <b>حداقل چند فایل</b> باید ارسال کند؟ (یک عدد انگلیسی مثل <code>1</code> بفرستید):",
            chat_id, msg_id, parse_mode="HTML"
        )
        return

    if data.startswith("redo_"):
        if uid not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ فقط ادمین‌های ثبت شده اجازه رد کردن تکلیف را دارند!", show_alert=True)
            return
        _, student_id, assign_id = data.split("_")
        student_id, assign_id = int(student_id), int(assign_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db_query(
            "UPDATE submissions SET status='REJECTED', files_json='[]', score=0, updated_at=? WHERE user_id=? AND assignment_id=?",
            (now_str, student_id, assign_id), commit=True
        )
        update_or_send_group_board(assign_id)

        assign = db_query("SELECT title, description, allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
        if assign:
            send_assignment_prompt(student_id, assign_id, assign[0], assign[1], assign[2], assign[3], assign[4], is_redo=True)
        
        bot.answer_callback_query(call.id, "🔄 تکلیف رد شد، تابلو گروه آپدیت شد و درخواست ارسال مجدد رفت!", show_alert=True)
        try:
            old_caption = call.message.caption or call.message.text or ""
            bot.edit_message_caption(
                old_caption + "\n\n❌ وضعیت: توسط ادمین رد شد (امتیاز: ۰) - درخواست ارسال مجدد رفت.",
                chat_id, msg_id
            )
        except:
            pass
        return

    if data == "confirm_reset_db":
        if uid not in ADMIN_IDS: return
        for tbl in ["student_codes", "users", "assignments", "submissions", "reminders", "sqlite_sequence"]:
            try: db_query(f"DELETE FROM {tbl}", commit=True)
            except: pass
        bot.answer_callback_query(call.id, "✅ دیتابیس صفر شد!", show_alert=True)
        bot.edit_message_text("🗑 <b>تمام اطلاعات دیتابیس با موفقیت پاکسازی و صفر شد!</b>", chat_id, msg_id, parse_mode="HTML")
        return

    if not is_subscribed(uid):
        send_join_wall(chat_id)
        return
    if not is_authenticated(uid):
        send_login_prompt(chat_id)
        return

    if data.startswith("viewassign_"):
        aid = int(data.replace("viewassign_", ""))
        assign = db_query("SELECT id, title, description, allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (aid,), fetchone=True)
        bot.answer_callback_query(call.id)
        if assign:
            send_assignment_prompt(chat_id, assign[0], assign[1], assign[2], assign[3], assign[4], assign[5])
        return

    if data.startswith(("done_", "snooze_")):
        action, assign_id = data.split("_")
        assign_id = int(assign_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        assign = db_query("SELECT allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
        if not assign:
            bot.answer_callback_query(call.id, "این تکلیف یافت نشد!")
            return
        allowed_type, req_count, deadline_at = assign
        sub = db_query("SELECT status, snooze_count FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id), fetchone=True)

        if is_deadline_passed(deadline_at) and (not sub or sub[0] != 'REJECTED'):
            bot.answer_callback_query(call.id, "⛔ مهلت ارسال این تکلیف به پایان رسیده و قفل شده است!", show_alert=True)
            return

        if action == "done":
            user_states[uid] = {
                'state': 'COLLECTING_FILES',
                'assign_id': assign_id,
                'allowed_type': allowed_type,
                'req_count': req_count,
                'temp_files': []
            }
            type_fa = TYPE_NAMES.get(allowed_type, 'آزاد')
            bot.answer_callback_query(call.id, "آماده دریافت فایل‌ها...")
            bot.send_message(
                chat_id,
                f"📤 <b>ارسال تکلیف شماره #{assign_id}</b>\n\n"
                f"🔹 <b>نوع فایل مجاز:</b> {type_fa}\n"
                f"🔹 <b>تعداد مورد نیاز:</b> حداقل {req_count} فایل\n\n"
                f"لطفاً فایل‌های خود را ارسال کنید و در پایان حتماً روی دکمه <b>«✅ تایید نهایی و ارسال به استاد»</b> در پایین صفحه بزنید:",
                reply_markup=student_submission_menu(),
                parse_mode="HTML"
            )
        elif action == "snooze":
            if sub:
                new_count = sub[1] + 1
                db_query("UPDATE submissions SET status='SNOOZED', snooze_count=?, updated_at=? WHERE user_id=? AND assignment_id=?", (new_count, now_str, uid, assign_id), commit=True)
            else:
                db_query("INSERT INTO submissions (user_id, assignment_id, status, snooze_count, updated_at) VALUES (?, ?, 'SNOOZED', 1, ?)", (uid, assign_id, now_str), commit=True)

            remind_time = (datetime.now() + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
            db_query("INSERT INTO reminders (user_id, assignment_id, remind_at) VALUES (?, ?, ?)", (uid, assign_id, remind_time), commit=True)

            bot.answer_callback_query(call.id, "⏰ یادآوری برای ۱ ساعت دیگر تنظیم شد!", show_alert=True)
            bot.send_message(chat_id, f"⏳ دقیقاً <b>۶۰ دقیقه دیگر</b> دوباره بهت پیام میدم تا تکلیف شماره #{assign_id} رو فراموش نکنی.", parse_mode="HTML")

# ==========================================
# ۱۰. دریافت فایل‌ها (تکلیف دانشجو یا پیام همگانی ادمین)
# ==========================================
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def handle_media_files(message):
    if message.chat.type in ['group', 'supergroup']:
        return
    uid = message.from_user.id
    st_data = user_states.get(uid, {})
    st = st_data.get('state', 'IDLE')

    if uid in ADMIN_IDS and st == 'ADMIN_WAIT_BROADCAST':
        user_states[uid] = {'state': 'IDLE'}
        all_users = db_query("SELECT user_id FROM users")
        sent_cnt = 0
        for (u_id,) in all_users:
            if u_id not in ADMIN_IDS:
                try:
                    bot.copy_message(u_id, message.chat.id, message.message_id)
                    sent_cnt += 1
                    time.sleep(0.05)
                except:
                    pass
        bot.send_message(message.chat.id, f"✅ پیام همگانی برای <b>{sent_cnt} دانش‌پذیر</b> ارسال شد.", reply_markup=admin_reply_folder_reports(), parse_mode="HTML")
        return

    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return
    if not is_authenticated(uid):
        send_login_prompt(message.chat.id)
        return

    if st != 'COLLECTING_FILES':
        bot.send_message(message.chat.id, "⚠️ برای ارسال تکلیف، ابتدا از پوشه تکالیف روی دکمه <b>«✅ انجام دادم»</b> زیر پیام تکلیف کلیک کنید.", parse_mode="HTML")
        return

    allowed = st_data['allowed_type']
    ftype = message.content_type

    valid = False
    if allowed == 'any': valid = True
    elif allowed == 'photo' and ftype == 'photo': valid = True
    elif allowed == 'video' and ftype == 'video': valid = True
    elif allowed == 'document' and ftype == 'document': valid = True
    elif allowed == 'audio_voice' and ftype in ['audio', 'voice']: valid = True

    if not valid:
        bot.send_message(message.chat.id, f"❌ <b>فرمت فایل اشتباه است!</b>\nبرای این تکلیف فقط <b>{TYPE_NAMES.get(allowed)}</b> مجاز است:", parse_mode="HTML")
        return

    if ftype == 'photo': fid = message.photo[-1].file_id
    elif ftype == 'video': fid = message.video.file_id
    elif ftype == 'document': fid = message.document.file_id
    elif ftype == 'audio': fid = message.audio.file_id
    elif ftype == 'voice': fid = message.voice.file_id

    st_data['temp_files'].append({'id': fid, 'type': ftype})
    curr_count = len(st_data['temp_files'])
    req_count = st_data['req_count']

    bot.send_message(
        message.chat.id,
        f"📥 <b>فایل شماره {curr_count} دریافت شد!</b> (نیاز: حداقل {req_count} فایل)\n\n"
        "🔸 اگر فایل دیگری دارید بفرستید.\n"
        "🔸 در پایان حتماً روی دکمه <b>«✅ تایید نهایی و ارسال به استاد»</b> در پایین صفحه بزنید.",
        reply_markup=student_submission_menu(),
        parse_mode="HTML"
    )

# ==========================================
# ۱۱. مدیریت پیام‌های متنی و کیبوردهای پوشه‌ای پایین چت
# ==========================================
@bot.message_handler(content_types=['text'])
def handle_all_text(message):
    if message.chat.type in ['group', 'supergroup']:
        return
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return

    text = message.text.strip()
    st_data = user_states.get(uid, {})
    st = st_data.get('state', 'IDLE')

    # ================= دستورات و پوشه‌های ادمین (در پایین چت) =================
    if uid in ADMIN_IDS:
        if text in ["🔙 انصراف و بازگشت", "🔙 بازگشت به منوی اصلی"]:
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "🗂 <b>منوی اصلی مدیریت (پوشه‌ها):</b>", reply_markup=admin_reply_root(), parse_mode="HTML")
            return

        # --- باز کردن پوشه‌های لایه ۱ ادمین ---
        if text == "👥 پوشه مدیریت دانش‌پذیران":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "👥 <b>پوشه مدیریت دانش‌پذیران:</b>\nاز دکمه‌های پایین انتخاب کنید:", reply_markup=admin_reply_folder_students(), parse_mode="HTML")
            return

        elif text == "📚 پوشه مدیریت تکالیف و تایم‌لاین":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "📚 <b>پوشه مدیریت تکالیف و تایم‌لاین:</b>\nاز دکمه‌های پایین انتخاب کنید:", reply_markup=admin_reply_folder_assignments(), parse_mode="HTML")
            return

        elif text == "📊 پوشه گزارشات، همگانی و تنظیمات":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "📊 <b>پوشه گزارشات، همگانی و تنظیمات:</b>\nاز دکمه‌های پایین انتخاب کنید:", reply_markup=admin_reply_folder_reports(), parse_mode="HTML")
            return

        # --- دکمه‌های داخل پوشه ۱ (دانش‌پذیران) ---
        elif text == "➕ ساخت کد دانش‌پذیر":
            user_states[uid] = {'state': 'ADMIN_ADD_STU_NAME'}
            bot.send_message(message.chat.id, "👤 لطفاً <b>نام و نام خانوادگی دانش‌پذیر</b> را وارد کنید (مثلاً: <code>احمد رضایی</code>):", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif text == "📋 لیست کدها و امتیازات":
            codes = db_query("SELECT code, student_name, used_by FROM student_codes ORDER BY created_at DESC")
            if not codes:
                bot.send_message(message.chat.id, "📭 هنوز هیچ کد دانش‌پذیری تعریف نکرده‌اید!")
                return
            msg_list = "📋 <b>لیست دانش‌پذیران، کدها و امتیازات:</b>\n\n"
            for c_code, s_name, u_by in codes:
                if u_by:
                    pts = get_student_total_score(u_by)
                    status_str = f"✅ فعال (<code>{u_by}</code>) | 🏆 <b>{pts} امتیاز</b>"
                else:
                    status_str = "⏳ هنوز وارد نشده"
                msg_list += f"👤 <b>{clean_html(s_name)}</b> | 🔑 کد: <code>{clean_html(c_code)}</code>\n└ وضعیت: {status_str}\n\n"
            bot.send_message(message.chat.id, msg_list, reply_markup=admin_reply_folder_students(), parse_mode="HTML")
            return

        # --- دکمه‌های داخل پوشه ۲ (تکالیف و تایم‌لاین) ---
        elif text == "➕ ثبت تکلیف جدید":
            user_states[uid] = {'state': 'ADMIN_WAIT_TITLE'}
            bot.send_message(message.chat.id, "📌 لطفاً <b>عنوان تکلیف جدید</b> را وارد کنید (مثلاً: <code>تکلیف جلسه ۳</code>):", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif text == "⏰ تغییر مهلت (تایم‌لاین)":
            assigns = db_query("SELECT id, title, deadline_at FROM assignments ORDER BY id DESC LIMIT 15")
            if not assigns:
                bot.send_message(message.chat.id, "📭 هنوز تکلیفی ثبت نشده است!")
                return
            markup = InlineKeyboardMarkup(row_width=1)
            for aid, title, dl in assigns:
                dl_short = dl[:16] if dl and dl != "نامحدود" else "نامحدود"
                markup.add(InlineKeyboardButton(f"تکلیف #{aid}: {title} (مهلت: {dl_short})", callback_data=f"editdl_{aid}"))
            bot.send_message(message.chat.id, "⏰ <b>کدام تکلیف را می‌خواهید تغییر زمان (تمدید/محدود) دهید؟</b>\nانتخاب کنید:", reply_markup=markup, parse_mode="HTML")
            return

        elif text == "📂 دریافت فایل‌های تکلیف":
            user_states[uid] = {'state': 'ADMIN_WAIT_ASSIGN_ID'}
            bot.send_message(message.chat.id, "🔢 لطفاً <b>شماره تکلیف</b> مورد نظر را (مثلاً <code>1</code>) بفرستید:", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif text == "🔄 بروزرسانی تابلو گروه":
            grp = get_admin_group()
            if not grp:
                bot.send_message(message.chat.id, "⚠️ ابتدا ربات را در گروه مدیریت ادمین کنید و دستور <code>/setgroup</code> را داخل گروه بفرستید!", parse_mode="HTML")
                return
            assigns = db_query("SELECT id FROM assignments ORDER BY id ASC")
            for (aid,) in assigns:
                update_or_send_group_board(aid)
                time.sleep(0.2)
            bot.send_message(message.chat.id, "✅ تمام تابلوهای وضعیت تکالیف در گروه مدیریت بروزرسانی شدند!", reply_markup=admin_reply_folder_assignments())
            return

        # --- دکمه‌های داخل پوشه ۳ (گزارشات و تنظیمات) ---
        elif text == "📊 آمار و لینک داشبورد":
            c_count = db_query("SELECT COUNT(*) FROM student_codes", fetchone=True)[0]
            u_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
            a_count = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
            d_count = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]
            grp = get_admin_group()
            grp_st = f"متصل (<code>{grp}</code>)" if grp else "متصل نشده (/setgroup)"
            report = (
                f"📊 <b>گزارش لحظه‌ای سامانه:</b>\n\n"
                f"📡 گروه مدیریت: {grp_st}\n"
                f"🔑 کل کدهای تعریف شده: <b>{c_count} کد</b>\n"
                f"👥 دانش‌پذیران وارد شده: <b>{u_count} نفر</b>\n"
                f"📚 تعداد تکالیف ثبت شده: <b>{a_count} مورد</b>\n"
                f"✅ کل تکالیف تحویل شده: <b>{d_count} مورد</b>\n\n"
                f"🌐 <b>داشبورد تحت وب:</b>\n"
                f"آدرس سرور خود را به همراه <code>/admin?pass={WEB_ADMIN_PASS}</code> باز کنید."
            )
            bot.send_message(message.chat.id, report, reply_markup=admin_reply_folder_reports(), parse_mode="HTML")
            return

        elif text == "📥 خروجی اکسل (CSV)":
            rows = db_query('''
                SELECT u.user_id, u.full_name, u.access_code, u.username, a.id, a.title, s.status, s.score, s.files_json, s.snooze_count, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                JOIN assignments a ON s.assignment_id = a.id
            ''')
            csv_path = "report.csv"
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["User ID", "Student Name", "Access Code", "Username", "Assignment ID", "Title", "Status", "Score", "File Count", "Snooze Count", "Last Updated"])
                for r in rows:
                    f_cnt = len(json.loads(r[8])) if r[8] else 0
                    writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], f_cnt, r[9], r[10]])
            with open(csv_path, 'rb') as f:
                bot.send_document(message.chat.id, f, caption="📊 فایل اکسل (CSV) جامع وضعیت تکالیف و امتیازات")
            os.remove(csv_path)
            return

        elif text == "📢 ارسال پیام همگانی":
            user_states[uid] = {'state': 'ADMIN_WAIT_BROADCAST'}
            bot.send_message(message.chat.id, "📢 لطفاً <b>پیام همگانی</b> خود را (متن، عکس، ویدیو، ویس یا فایل) ارسال کنید:", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif text == "🗑 ریست کل دیتابیس":
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("⚠️ بله، کل اطلاعات پاک شود!", callback_data="confirm_reset_db"))
            bot.send_message(message.chat.id, "⚠️ <b>هشدار جدی:</b> آیا مطمئن هستید که می‌خواهید تمام اطلاعات دیتابیس را صفر کنید؟", reply_markup=markup, parse_mode="HTML")
            return

        # --- پردازش ورودی‌های مرحله‌ای ادمین ---
        elif st == 'ADMIN_WAIT_BROADCAST':
            user_states[uid] = {'state': 'IDLE'}
            all_users = db_query("SELECT user_id FROM users")
            sent_cnt = 0
            for (u_id,) in all_users:
                if u_id not in ADMIN_IDS:
                    try:
                        bot.send_message(u_id, f"📢 <b>اطلاعیه مدیریت دوره:</b>\n\n{clean_html(text)}", parse_mode="HTML")
                        sent_cnt += 1
                        time.sleep(0.05)
                    except:
                        pass
            bot.send_message(message.chat.id, f"✅ اطلاعیه برای <b>{sent_cnt} دانش‌پذیر</b> ارسال شد.", reply_markup=admin_reply_folder_reports(), parse_mode="HTML")
            return

        elif st == 'ADMIN_ADD_STU_NAME':
            user_states[uid]['stu_name'] = text
            user_states[uid]['state'] = 'ADMIN_ADD_STU_CODE'
            bot.send_message(message.chat.id, f"✅ نام دانش‌پذیر: <b>{clean_html(text)}</b>\n\n🔢 حالا <b>کد ورود اختصاصی</b> او را وارد کنید (مثلاً <code>1111111111</code>):", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif st == 'ADMIN_ADD_STU_CODE':
            code = text.strip()
            stu_name = user_states[uid]['stu_name']
            existing_code = db_query("SELECT student_name FROM student_codes WHERE code=?", (code,), fetchone=True)
            if existing_code:
                bot.send_message(message.chat.id, f"⚠️ این کد قبلاً برای «{existing_code[0]}» ثبت شده است! کد دیگری بفرستید:")
                return

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db_query("INSERT INTO student_codes (code, student_name, created_at) VALUES (?, ?, ?)", (code, stu_name, now_str), commit=True)
            user_states[uid] = {'state': 'IDLE'}

            bot.send_message(message.chat.id, f"🎉 دانش‌پذیر <b>{clean_html(stu_name)}</b> با کد <code>{clean_html(code)}</code> ثبت شد!", reply_markup=admin_reply_folder_students(), parse_mode="HTML")
            return

        elif st == 'ADMIN_WAIT_TITLE':
            user_states[uid]['title'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_DESC'
            bot.send_message(message.chat.id, "📝 عالی! حالا <b>توضیحات کامل تکلیف</b> را بنویسید:", reply_markup=cancel_menu(), parse_mode="HTML")
            return

        elif st == 'ADMIN_WAIT_DESC':
            user_states[uid]['desc'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_TYPE'
            t_markup = InlineKeyboardMarkup(row_width=1)
            for k, v in TYPE_NAMES.items():
                t_markup.add(InlineKeyboardButton(v, callback_data=f"settype_{k}"))
            bot.send_message(message.chat.id, "📂 دانش‌پذیران <b>چه نوع فایلی</b> باید ارسال کنند؟", reply_markup=t_markup, parse_mode="HTML")
            return

        elif st == 'ADMIN_WAIT_COUNT':
            if not text.isdigit() or int(text) < 1:
                bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح بزرگتر از صفر وارد کنید:")
                return
            user_states[uid]['req_count'] = int(text)
            user_states[uid]['state'] = 'ADMIN_WAIT_DEADLINE'
            bot.send_message(
                message.chat.id,
                "⏰ <b>تعیین تایم‌لاین (مهلت ارسال تکلیف):</b>\n\n"
                "مشخص کنید دانش‌پذیران <b>چند ساعت</b> برای ارسال این تکلیف فرصت دارند؟\n"
                "🔹 یک عدد انگلیسی (مثلاً <code>24</code> برای ۱ روز یا <code>48</code> برای ۲ روز) بفرستید.\n"
                "🔹 اگر می‌خواهید مهلت <b>نامحدود</b> باشد، عدد <code>0</code> را بفرستید:",
                reply_markup=cancel_menu(),
                parse_mode="HTML"
            )
            return

        elif st == 'ADMIN_WAIT_DEADLINE':
            if not text.isdigit() or int(text) < 0:
                bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح (یا 0 برای نامحدود) وارد کنید:")
                return
            
            hours = int(text)
            now_dt = datetime.now()
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            deadline_str = (now_dt + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S") if hours > 0 else "نامحدود"

            title = user_states[uid]['title']
            desc = user_states[uid]['desc']
            allowed_type = user_states[uid]['allowed_type']
            req_count = user_states[uid]['req_count']

            assign_id = db_query(
                "INSERT INTO assignments (title, description, allowed_type, required_count, created_at, deadline_at) VALUES (?, ?, ?, ?, ?, ?)",
                (title, desc, allowed_type, req_count, now_str, deadline_str), commit=True
            )
            user_states[uid] = {'state': 'IDLE'}

            # ۱. ایجاد تابلو وضعیت زنده این تکلیف در گروه مدیریت
            board_id = update_or_send_group_board(assign_id)
            grp_note = "✅ تابلو وضعیت در گروه مدیریت ارسال شد." if board_id else "⚠️ گروه مدیریت هنوز تنظیم نشده است (/setgroup را در گروه بزنید)."

            # ۲. ارسال اعلان به دانش‌پذیران
            all_users = db_query("SELECT user_id FROM users")
            sent_count = 0
            for (u_id,) in all_users:
                if u_id not in ADMIN_IDS:
                    send_assignment_prompt(u_id, assign_id, title, desc, allowed_type, req_count, deadline_str)
                    sent_count += 1
                    time.sleep(0.05)

            bot.send_message(
                message.chat.id,
                f"✅ <b>تکلیف شماره #{assign_id} ثبت شد!</b>\n"
                f"⏰ مهلت ارسال: <code>{deadline_str}</code>\n"
                f"📡 {grp_note}\n"
                f"📢 اعلان برای <b>{sent_count} دانش‌پذیر</b> ارسال گردید.",
                reply_markup=admin_reply_folder_assignments(),
                parse_mode="HTML"
            )
            return

        elif st == 'ADMIN_EDIT_DEADLINE_HOURS':
            aid = st_data['assign_id']
            if text.lower() == 'lock':
                new_dl = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            elif text.isdigit():
                hrs = int(text)
                new_dl = (datetime.now() + timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S") if hrs > 0 else "نامحدود"
            else:
                bot.send_message(message.chat.id, "⚠️ ورودی نامعتبر! یک عدد به ساعت، 0 برای نامحدود، یا lock برای قفل فوری بفرستید:")
                return

            db_query("UPDATE assignments SET deadline_at=? WHERE id=?", (new_dl, aid), commit=True)
            user_states[uid] = {'state': 'IDLE'}
            update_or_send_group_board(aid)

            bot.send_message(
                message.chat.id,
                f"✅ <b>مهلت ارسال تکلیف #{aid} تغییر کرد و تابلو گروه آپدیت شد!</b>\n⏰ وضعیت جدید: {clean_html(format_deadline_status(new_dl))}",
                reply_markup=admin_reply_folder_assignments(),
                parse_mode="HTML"
            )
            return

        elif st == 'ADMIN_WAIT_ASSIGN_ID' and text.isdigit():
            aid = int(text)
            user_states[uid] = {'state': 'IDLE'}
            subs = db_query('''
                SELECT u.user_id, u.full_name, u.access_code, u.username, s.files_json, s.score, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                WHERE s.assignment_id=? AND s.status='DONE'
            ''', (aid,))
            
            if not subs:
                bot.send_message(message.chat.id, f"⚠️ هیچ فایل تحویل شده‌ای برای تکلیف #{aid} یافت نشد.", reply_markup=admin_reply_folder_assignments())
                return
            
            bot.send_message(message.chat.id, f"📂 ارسال فایل‌های <b>{len(subs)} دانش‌پذیر</b> برای تکلیف #{aid}:", reply_markup=admin_reply_folder_assignments(), parse_mode="HTML")
            for stu_id, fname, acode, uname, f_json, pts, up_time in subs:
                f_list = json.loads(f_json) if f_json else []
                for idx, f_item in enumerate(f_list, 1):
                    fid, ftype = f_item['id'], f_item['type']
                    cap = (
                        f"👤 دانش‌پذیر: {clean_html(fname)} (کد: <code>{clean_html(acode)}</code>)\n"
                        f"🔢 تکلیف: #{aid} (فایل {idx} از {len(f_list)})\n"
                        f"⭐ امتیاز: <b>{pts}</b> | 📅 زمان: <code>{up_time}</code>\n\n"
                        f"⚠️ این پیام را پاک نکنید (آرشیو سیستم)"
                    )
                    markup = InlineKeyboardMarkup() if idx == len(f_list) else None
                    if markup:
                        markup.add(InlineKeyboardButton("🔄 رد تکلیف (کسر امتیاز و ارسال مجدد)", callback_data=f"redo_{stu_id}_{aid}"))
                    try:
                        if ftype == 'photo': bot.send_photo(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="HTML")
                        elif ftype == 'video': bot.send_video(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="HTML")
                        elif ftype == 'document': bot.send_document(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="HTML")
                        elif ftype == 'audio': bot.send_audio(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="HTML")
                        elif ftype == 'voice': bot.send_voice(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="HTML")
                        time.sleep(0.2)
                    except:
                        pass
            return

    # ================= بررسی ورود کد توسط دانش‌آموز =================
    if not is_authenticated(uid):
        entered_code = text.strip()
        code_row = db_query("SELECT student_name, used_by FROM student_codes WHERE code=?", (entered_code,), fetchone=True)
        if not code_row:
            bot.send_message(message.chat.id, "❌ <b>کد وارد شده نامعتبر است!</b> مجدداً بررسی و ارسال کنید:", parse_mode="HTML")
            return
        
        stu_name, used_by = code_row[0], code_row[1]
        if used_by is not None and used_by != uid:
            bot.send_message(message.chat.id, "⛔ <b>این کد قبلاً توسط دانش‌پذیر دیگری استفاده شده است!</b>", parse_mode="HTML")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uname = message.from_user.username if message.from_user.username else "ندارد"
        
        db_query("UPDATE student_codes SET used_by=? WHERE code=?", (uid, entered_code), commit=True)
        db_query("INSERT OR REPLACE INTO users (user_id, username, full_name, access_code, joined_at) VALUES (?, ?, ?, ?, ?)", (uid, uname, stu_name, entered_code, now_str), commit=True)
        user_states[uid] = {'state': 'IDLE'}

        bot.send_message(
            message.chat.id,
            f"✅ <b>احراز هویت انجام شد!</b> خوش آمدی <b>{clean_html(stu_name)}</b> عزیز 🎓\n"
            f"👇 از دکمه‌های پوشه‌ای پایین صفحه استفاده کنید:",
            reply_markup=student_reply_root(),
            parse_mode="HTML"
        )
        return

    # ------------ تایید نهایی تکلیف دانش‌آموز، ارسال به گروه و آپدیت تابلو ------------
    if st == 'COLLECTING_FILES':
        if text == "🔙 انصراف و پاک کردن فایل‌ها":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "❌ ارسال تکلیف لغو شد.", reply_markup=student_reply_folder_assignments())
            return

        elif text == "✅ تایید نهایی و ارسال به استاد":
            files = st_data['temp_files']
            req_count = st_data['req_count']
            assign_id = st_data['assign_id']

            if len(files) < req_count:
                bot.send_message(message.chat.id, f"⚠️ شما باید حداقل <b>{req_count} فایل</b> بفرستید (تا الان {len(files)} فایل فرستادید).", parse_mode="HTML")
                return

            now_dt = datetime.now()
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            files_json = json.dumps(files)

            assign_row = db_query("SELECT created_at, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
            existing = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id), fetchone=True)

            if assign_row and is_deadline_passed(assign_row[1]) and (not existing or existing[0] != 'REJECTED'):
                user_states[uid] = {'state': 'IDLE'}
                bot.send_message(message.chat.id, "⛔ متاسفانه مهلت ارسال این تکلیف به پایان رسیده است!", reply_markup=student_reply_root())
                return

            earned_score = 1
            if assign_row and assign_row[0]:
                try:
                    fmt = "%Y-%m-%d %H:%M:%S" if len(assign_row[0]) > 16 else "%Y-%m-%d %H:%M"
                    created_dt = datetime.strptime(assign_row[0], fmt)
                    if (now_dt - created_dt).total_seconds() <= 86400:
                        earned_score = 3
                except:
                    earned_score = 1

            if existing:
                if existing[0] == 'REJECTED':
                    earned_score = 1
                db_query(
                    "UPDATE submissions SET status='DONE', files_json=?, score=?, updated_at=? WHERE user_id=? AND assignment_id=?",
                    (files_json, earned_score, now_str, uid, assign_id), commit=True
                )
            else:
                db_query(
                    "INSERT INTO submissions (user_id, assignment_id, status, files_json, score, updated_at) VALUES (?, ?, 'DONE', ?, ?, ?)",
                    (uid, assign_id, files_json, earned_score, now_str), commit=True
                )

            user_states[uid] = {'state': 'IDLE'}
            total_pts = get_student_total_score(uid)
            speed_msg = "⚡ <b>(تحویل سریع زیر ۲۴ ساعت!)</b>" if earned_score == 3 else ""

            bot.send_message(
                message.chat.id,
                f"🎉 <b>تایید شد! تکلیف شماره #{assign_id} ثبت گردید.</b>\n\n"
                f"⭐ امتیاز این تکلیف: <b>{earned_score} امتیاز</b> {speed_msg}\n"
                f"🏆 مجموع کل امتیازات شما: <b>{total_pts} امتیاز</b>",
                reply_markup=student_reply_root(),
                parse_mode="HTML"
            )

            # ۱. بروزرسانی تابلو تکلیف در گروه مدیریت (افزودن نام فرستنده، زمان و امتیاز)
            board_msg_id = update_or_send_group_board(assign_id)

            # ۲. ارسال فایل‌های دانشجو به گروه مدیریت (با فرمت امن HTML و فالبک خودکار)
            st_name, st_code, uname = get_student_info(uid)
            clean_tag_name = "".join(c if c.isalnum() else "_" for c in st_name)
            grp_id = get_admin_group()
            target_chats = [grp_id] if grp_id else ADMIN_IDS

            for idx, f_item in enumerate(files, 1):
                fid, ftype = f_item['id'], f_item['type']
                is_last = (idx == len(files))
                
                archive_caption = (
                    f"📥 <b>فایل تکلیف #{assign_id}</b> (فایل {idx} از {len(files)})\n"
                    f"👤 <b>فرستنده:</b> {clean_html(st_name)} (کد: <code>{clean_html(st_code)}</code>)\n"
                    f"🆔 <b>یوزرنیم:</b> @{clean_html(uname)} | <code>{uid}</code>\n"
                    f"🕒 <b>تایم فایل:</b> <code>{now_str}</code>\n"
                    f"⭐ <b>امتیاز فرد در این تکلیف:</b> {earned_score} امتیاز (کل: {total_pts})\n\n"
                    f"🔍 #تکلیف_{assign_id} #{clean_tag_name} #کد_{clean_html(st_code)}\n"
                    f"⚠️ این پیام را پاک نکنید (آرشیو سیستم)"
                )
                
                markup = InlineKeyboardMarkup() if is_last else None
                if markup:
                    markup.add(InlineKeyboardButton("🔄 رد تکلیف (کسر امتیاز و ارسال مجدد)", callback_data=f"redo_{uid}_{assign_id}"))

                for chat_target in target_chats:
                    reply_id = board_msg_id if (grp_id and chat_target == grp_id and board_msg_id) else None
                    try:
                        if ftype == 'photo': bot.send_photo(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="HTML")
                        elif ftype == 'video': bot.send_video(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="HTML")
                        elif ftype == 'document': bot.send_document(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="HTML")
                        elif ftype == 'audio': bot.send_audio(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="HTML")
                        elif ftype == 'voice': bot.send_voice(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="HTML")
                    except Exception as e:
                        # اگر پیام تابلو در گروه پاک شده بود، بدون ریپلای فایل را در گروه می‌فرستد تا هیچ فایلی از دست نرود
                        try:
                            if ftype == 'photo': bot.send_photo(chat_target, fid, caption=archive_caption, reply_markup=markup, parse_mode="HTML")
                            elif ftype == 'video': bot.send_video(chat_target, fid, caption=archive_caption, reply_markup=markup, parse_mode="HTML")
                            elif ftype == 'document': bot.send_document(chat_target, fid, caption=archive_caption, reply_markup=markup, parse_mode="HTML")
                            elif ftype == 'audio': bot.send_audio(chat_target, fid, caption=archive_caption, reply_markup=markup, parse_mode="HTML")
                            elif ftype == 'voice': bot.send_voice(chat_target, fid, caption=archive_caption, reply_markup=markup, parse_mode="HTML")
                        except Exception as e2:
                            print(f"Error sending archive to group: {e2}")
                    time.sleep(0.1)
            return

    # ================= دستورات و پوشه‌های دانش‌پذیر (در پایین چت) =================
    if text == "🔙 بازگشت به منوی اصلی":
        bot.send_message(message.chat.id, "🗂 <b>منوی اصلی دانش‌پذیر (پوشه‌ها):</b>", reply_markup=student_reply_root(), parse_mode="HTML")
        return

    elif text == "📚 پوشه تکالیف و تمرین‌ها":
        bot.send_message(message.chat.id, "📚 <b>پوشه تکالیف و تمرین‌ها:</b>\nاز دکمه‌های پایین انتخاب کنید:", reply_markup=student_reply_folder_assignments(), parse_mode="HTML")
        return

    elif text == "🗂 پوشه کارنامه و پشتیبانی":
        bot.send_message(message.chat.id, "🗂 <b>پوشه کارنامه و پشتیبانی:</b>\nاز دکمه‌های پایین انتخاب کنید:", reply_markup=student_reply_folder_profile(), parse_mode="HTML")
        return

    elif text == "📌 آخرین تکلیف دوره":
        last_assign = db_query("SELECT id, title, description, allowed_type, required_count, deadline_at FROM assignments ORDER BY id DESC LIMIT 1", fetchone=True)
        if not last_assign:
            bot.send_message(message.chat.id, "📭 هنوز هیچ تکلیفی در سیستم ثبت نشده است!")
        else:
            send_assignment_prompt(message.chat.id, last_assign[0], last_assign[1], last_assign[2], last_assign[3], last_assign[4], last_assign[5])
        return

    elif text == "📋 لیست همه تکالیف":
        assigns = db_query("SELECT id, title, deadline_at FROM assignments ORDER BY id DESC")
        if not assigns:
            bot.send_message(message.chat.id, "📭 هنوز تکلیفی ثبت نشده است!")
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for aid, title, dl in assigns:
            sub = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, aid), fetchone=True)
            st_icon = "✅" if (sub and sub[0] == 'DONE') else ("⛔" if is_deadline_passed(dl) else "⏳")
            markup.add(InlineKeyboardButton(f"{st_icon} تکلیف #{aid}: {title}", callback_data=f"viewassign_{aid}"))
        bot.send_message(message.chat.id, "📋 <b>لیست کل تکالیف دوره:</b>\nروی هر تکلیف بزنید تا باز شود:", reply_markup=markup, parse_mode="HTML")
        return

    elif text == "🗂 کارنامه و امتیازات من":
        st_name, st_code, _ = get_student_info(uid)
        total_pts = get_student_total_score(uid)
        my_subs = db_query('''
            SELECT a.id, a.title, s.status, s.score, a.deadline_at
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id AND s.user_id = ?
        ''', (uid,))
        if not my_subs:
            bot.send_message(message.chat.id, "📭 هنوز تکلیفی تعریف نشده است.")
            return
        
        done_list, pending_list = "", ""
        for aid, title, status, pts, dl in my_subs:
            if status == 'DONE':
                badge = "⚡" if pts == 3 else "⭐"
                done_list += f"✅ <b>تکلیف #{aid} ({clean_html(title)}):</b> {badge} <code>{pts} امتیاز</code>\n"
            elif status == 'REJECTED':
                pending_list += f"🔄 <b>تکلیف #{aid} ({clean_html(title)}):</b> رد شده (نیاز به ارسال مجدد)\n"
            else:
                lock_txt = " (⛔ مهلت تمام شده)" if is_deadline_passed(dl) else ""
                pending_list += f"❌ <b>تکلیف #{aid} ({clean_html(title)}):</b> انجام نشده{lock_txt}\n"
                
        rep = (
            f"🗂 <b>کارنامه دانش‌پذیر:</b> {clean_html(st_name)} (کد: <code>{clean_html(st_code)}</code>)\n"
            f"🏆 <b>مجموع امتیازات کسب شده:</b> <code>{total_pts} امتیاز</code>\n"
            f"────────────────────\n"
            f"🟢 <b>تکالیف انجام شده:</b>\n{done_list if done_list else 'هنوز تکلیفی تحویل نداده‌اید.\n'}\n"
            f"🔴 <b>تکالیف باقی‌مانده / ناقص:</b>\n{pending_list if pending_list else '🎉 همه تکالیف را انجام داده‌اید!'}"
        )
        bot.send_message(message.chat.id, rep, reply_markup=student_reply_folder_profile(), parse_mode="HTML")
        return

    elif text == "👩‍💻 ارتباط با پشتیبانی":
        bot.send_message(message.chat.id, f"👩‍💻 جهت ارتباط با پشتیبانی دوره به آیدی زیر پیام دهید:\n👉 {SUPPORT_USERNAME}", reply_markup=student_reply_folder_profile())
        return

    # هدایت پیام‌های نامفهوم به منوی پایین چت
    bot.send_message(message.chat.id, "👇 لطفاً از <b>دکمه‌های پوشه‌ای پایین صفحه</b> استفاده کنید:", reply_markup=student_reply_root(), parse_mode="HTML")

print("Super LMS Bot V7 is running...")
bot.infinity_polling(skip_pending=True)
