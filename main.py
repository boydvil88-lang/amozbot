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
TOKEN = "YOUR_BOT_TOKEN"                  # توکن ربات
ADMIN_IDS = [123456789, 987654321]        # آیدی عددی ادمین‌ها (با کاما جدا کنید)
COURSE_CHANNEL_ID = "@YOUR_CHANNEL_ID"    # آیدی کانال دوره برای جوین اجباری
COURSE_CHANNEL_LINK = "https://t.me/YOUR_CHANNEL_ID" 
SUPPORT_USERNAME = "@Support_ID"          # آیدی پشتیبانی دوره
WEB_ADMIN_PASS = "123456"                 # رمز عبور داشبورد وب

bot = telebot.TeleBot(TOKEN)
DB_NAME = "lms_database_v3.db"            # نسخه ۳ دیتابیس (مجهز به سیستم کد ورود)

user_states = {}

TYPE_NAMES = {
    'any': 'آزاد (هر نوع فایلی)',
    'photo': 'فقط عکس 🖼',
    'video': 'فقط ویدیو 🎥',
    'document': 'فقط سند/فایل (PDF, ZIP و...) 📄',
    'audio_voice': 'فقط فایل صوتی یا وویس 🎙'
}

# ==========================================
# ۲. راه‌اندازی و مدیریت پایگاه داده (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # جدول کدهای تعریفی توسط ادمین برای دانش‌پذیران
    c.execute('''CREATE TABLE IF NOT EXISTS student_codes (
                    code TEXT PRIMARY KEY,
                    student_name TEXT,
                    used_by INTEGER DEFAULT NULL,
                    created_at TEXT
                )''')
    # جدول دانش‌پذیران احراز هویت شده
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    access_code TEXT,
                    joined_at TEXT
                )''')
    # جدول تکالیف
    c.execute('''CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    description TEXT,
                    allowed_type TEXT DEFAULT 'any',
                    required_count INTEGER DEFAULT 1,
                    created_at TEXT
                )''')
    # جدول ارسال‌ها
    c.execute('''CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    assignment_id INTEGER,
                    status TEXT,
                    files_json TEXT,
                    snooze_count INTEGER DEFAULT 0,
                    updated_at TEXT,
                    UNIQUE(user_id, assignment_id)
                )''')
    # جدول زمان‌بندی یادآوری‌ها
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

# ==========================================
# ۳. داشبورد مدیریت تحت وب (Flask Web Panel)
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <title>پنل مدیریت تکالیف | 12EHSAN21</title>
    <style>
        body { font-family: Tahoma, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }
        .container { max-width: 1100px; margin: auto; background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
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
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 داشبورد مدیریت و گزارش‌گیری دوره</h1>
        <div class="stats">
            <div class="card"><h3>{{ total_codes }}</h3><p>کل کدهای تعریف شده</p></div>
            <div class="card"><h3>{{ total_users }}</h3><p>دانش‌پذیران وارد شده</p></div>
            <div class="card"><h3>{{ total_assigns }}</h3><p>تکالیف ثبت شده</p></div>
            <div class="card"><h3>{{ total_done }}</h3><p>تکالیف تحویل شده</p></div>
        </div>
        <h2>📋 وضعیت ارسال تکالیف دانش‌پذیران</h2>
        <table>
            <tr>
                <th>نام دانش‌پذیر (ثبتی ادمین)</th>
                <th>کد اختصاصی</th>
                <th>یوزرنیم تلگرام</th>
                <th>تکلیف</th>
                <th>وضعیت</th>
                <th>تعداد فایل</th>
                <th>تعویق</th>
                <th>آخرین بروزرسانی</th>
            </tr>
            {% for row in records %}
            <tr>
                <td>{{ row[0] }}</td>
                <td><code>{{ row[1] }}</code></td>
                <td>@{{ row[2] }}</td>
                <td>#{{ row[3] }} - {{ row[4] }}</td>
                <td class="{{ row[5] }}">{{ row[6] }}</td>
                <td>{{ row[7] }} فایل</td>
                <td>{{ row[8] }} بار</td>
                <td>{{ row[9] }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return "✅ LMS Bot Server V3 (With Auth) is Running! (Designed by 12EHSAN21)"

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
        SELECT u.full_name, u.access_code, u.username, a.id, a.title, s.status, s.files_json, s.snooze_count, s.updated_at
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
        
        f_list = json.loads(r[6]) if r[6] else []
        records.append((r[0], r[1], r[2], r[3], r[4], st_class, st_fa, len(f_list), r[7], r[8]))

    return render_template_string(
        HTML_TEMPLATE, 
        total_codes=total_codes, total_users=total_users, 
        total_assigns=total_assigns, total_done=total_done, records=records
    )

def run_web():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_web, daemon=True).start()

# ==========================================
# ۴. موتور زمان‌بند هوشمند (Job Scheduler)
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
                
                assign = db_query("SELECT title, description, allowed_type, required_count FROM assignments WHERE id=?", (aid,), fetchone=True)
                if assign:
                    send_assignment_prompt(uid, aid, assign[0], assign[1], assign[2], assign[3], is_reminder=True)
        except Exception as e:
            print(f"Scheduler Error: {e}")
        time.sleep(30)

threading.Thread(target=reminder_worker, daemon=True).start()

# ==========================================
# ۵. توابع کمکی، عضویت کانال و احراز هویت با کد
# ==========================================
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
    """بررسی اینکه آیا دانش‌آموز قبلاً کد ورود معتبر وارد کرده است یا خیر"""
    if user_id in ADMIN_IDS:
        return True
    row = db_query("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return True if row else False

def get_student_info(user_id):
    """دریافت نام ثبت شده توسط ادمین و کد دانش‌آموز"""
    row = db_query("SELECT full_name, access_code, username FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row if row else ("ناشناس", "---", "ندارد")

def send_join_wall(chat_id):
    markup = InlineKeyboardMarkup()
    clean_link = COURSE_CHANNEL_LINK if COURSE_CHANNEL_LINK.startswith("http") else f"https://t.me/{COURSE_CHANNEL_ID.replace('@', '')}"
    markup.add(InlineKeyboardButton("📢 ورود به کانال دوره", url=clean_link))
    markup.add(InlineKeyboardButton("✅ عضو شدم (ادامه)", callback_data="verify_join"))
    bot.send_message(
        chat_id,
        "⛔ **مرحله اول: عضویت در کانال دوره**\n\nبرای استفاده از سامانه تکالیف، ابتدا باید در کانال دوره عضو شوید و سپس دکمه زیر را بزنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

def send_login_prompt(chat_id):
    user_states[chat_id] = {'state': 'WAIT_LOGIN_CODE'}
    bot.send_message(
        chat_id,
        "🔐 **مرحله دوم: احراز هویت دانش‌پذیر**\n\n"
        "لطفاً **کد ورود اختصاصی** خود را (که از استاد یا پشتیبانی دریافت کرده‌اید) در همین صفحه تایپ و ارسال کنید:",
        parse_mode="Markdown"
    )

def send_assignment_prompt(chat_id, assign_id, title, desc, allowed_type='any', req_count=1, is_reminder=False, is_redo=False):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ انجام دادم (ارسال فایل تکلیف)", callback_data=f"done_{assign_id}"),
        InlineKeyboardButton("⏳ یک ساعت دیگر انجام می‌دهم", callback_data=f"snooze_{assign_id}"),
        InlineKeyboardButton("🆘 نیاز به راهنمایی و پشتیبانی دارم", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")
    )
    if is_redo:
        prefix = "⚠️ **تکلیف شما توسط استاد رد شد! لطفاً مجدداً با دقت ارسال کنید:**\n\n"
    elif is_reminder:
        prefix = "⏰ **یادآوری خودکار تکلیف!** (۱ ساعت گذشت)\n\n"
    else:
        prefix = "📚 **تکلیف جدید ثبت شد!**\n\n"

    type_fa = TYPE_NAMES.get(allowed_type, 'آزاد')
    text = (
        f"{prefix}"
        f"📌 **عنوان تمرین (#{assign_id}):** {title}\n"
        f"📝 **توضیحات:**\n{desc}\n\n"
        f"📂 **نوع فایل مجاز:** {type_fa}\n"
        f"🔢 **تعداد فایل مورد نیاز:** حداقل {req_count} فایل\n\n"
        f"لطفاً وضعیت انجام این تمرین را مشخص کنید:"
    )
    try:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        print(f"Could not send to {chat_id}: {e}")

# ==========================================
# ۶. منوها و کیبوردها
# ==========================================
def student_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📚 آخرین تکلیف دوره"),
        KeyboardButton("🗂 کارنامه و وضعیت من"),
        KeyboardButton("👩‍💻 ارتباط با پشتیبانی")
    )
    return markup

def student_submission_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        KeyboardButton("✅ تایید نهایی و ارسال به استاد"),
        KeyboardButton("🔙 انصراف و پاک کردن فایل‌ها")
    )
    return markup

def admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 افزودن دانش‌پذیر (ساخت کد)"),
        KeyboardButton("📋 لیست کدهای دانش‌پذیران"),
        KeyboardButton("➕ ثبت و اعلان تکلیف جدید"),
        KeyboardButton("📊 آمار کلی و لینک داشبورد"),
        KeyboardButton("📥 دریافت اکسل گزارش‌ها (CSV)"),
        KeyboardButton("📂 دریافت فایل‌های یک تکلیف")
    )
    return markup

def cancel_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔙 انصراف و بازگشت به پنل"))
    return markup

# ==========================================
# ۷. هندلرهای شروع و کال‌بک‌ها
# ==========================================
@bot.message_handler(commands=['start'])
def start_handler(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return
    
    if uid in ADMIN_IDS:
        user_states[uid] = {'state': 'IDLE'}
        bot.send_message(
            message.chat.id,
            "👑 **به پنل مدیریت سامانه آموزش خوش آمدید!**\nاز دکمه‌های زیر برای تعریف کد دانش‌آموزان و مدیریت تکالیف استفاده کنید:",
            reply_markup=admin_menu(),
            parse_mode="Markdown"
        )
        return

    # بررسی لاگین بودن دانش‌آموز
    if not is_authenticated(uid):
        send_login_prompt(message.chat.id)
        return

    user_states[uid] = {'state': 'IDLE'}
    st_name, st_code, _ = get_student_info(uid)
    bot.send_message(
        message.chat.id,
        f"سلام **{st_name}** عزیز! 🎓\nبه سامانه هوشمند پیگیری تکالیف خوش آمدی.\nاز منوی زیر می‌تونی وضعیت تمرین‌هات رو دنبال کنی:",
        reply_markup=student_menu(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "verify_join")
def verify_join_cb(call):
    uid = call.from_user.id
    if is_subscribed(uid):
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        if is_authenticated(uid):
            st_name, _, _ = get_student_info(uid)
            bot.send_message(call.message.chat.id, f"✅ خوش آمدید **{st_name}** عزیز:", reply_markup=student_menu(), parse_mode="Markdown")
        else:
            send_login_prompt(call.message.chat.id)
    else:
        bot.answer_callback_query(call.id, "❌ شما هنوز در کانال دوره عضو نشده‌اید!", show_alert=True)

# ---------------- انتخاب نوع فایل تکلیف توسط ادمین ----------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("settype_"))
def admin_set_type_cb(call):
    uid = call.from_user.id
    if uid not in ADMIN_IDS:
        return
    st_data = user_states.get(uid, {})
    if st_data.get('state') != 'ADMIN_WAIT_TYPE':
        return

    chosen_type = call.data.replace("settype_", "")
    user_states[uid]['allowed_type'] = chosen_type
    user_states[uid]['state'] = 'ADMIN_WAIT_COUNT'
    
    bot.answer_callback_query(call.id, f"نوع فایل انتخاب شد: {TYPE_NAMES.get(chosen_type)}")
    bot.edit_message_text(
        f"✅ نوع فایل: **{TYPE_NAMES.get(chosen_type)}**\n\n"
        "🔢 حالا مشخص کنید دانش‌پذیر **حداقل چند فایل** باید برای این تکلیف ارسال کند؟ (یک عدد انگلیسی مثل `1` یا `3` بفرستید):",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown"
    )

# ---------------- دکمه ادمین برای رد تکلیف و درخواست ارسال مجدد ----------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("redo_"))
def admin_redo_homework_cb(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    
    _, student_id, assign_id = call.data.split("_")
    student_id, assign_id = int(student_id), int(assign_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    db_query(
        "UPDATE submissions SET status='REJECTED', files_json='[]', updated_at=? WHERE user_id=? AND assignment_id=?",
        (now_str, student_id, assign_id), commit=True
    )

    assign = db_query("SELECT title, description, allowed_type, required_count FROM assignments WHERE id=?", (assign_id,), fetchone=True)
    if assign:
        send_assignment_prompt(student_id, assign_id, assign[0], assign[1], assign[2], assign[3], is_redo=True)
    
    bot.answer_callback_query(call.id, "🔄 تکلیف رد شد و درخواست ارسال مجدد برای دانش‌پذیر رفت!", show_alert=True)
    try:
        old_caption = call.message.caption or call.message.text or ""
        bot.edit_message_caption(
            old_caption + "\n\n❌ **وضعیت: این تکلیف توسط شما رد شد و برای دانش‌آموز درخواست ارسال مجدد رفت.**",
            call.message.chat.id,
            call.message.message_id
        )
    except:
        pass

# ---------------- مدیریت کلیدهای شیشه‌ای تکالیف (دانش‌آموز) ----------------
@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "snooze_")))
def assignment_actions_cb(call):
    uid = call.from_user.id
    if not is_subscribed(uid):
        send_join_wall(call.message.chat.id)
        return
    if not is_authenticated(uid):
        send_login_prompt(call.message.chat.id)
        return

    action, assign_id = call.data.split("_")
    assign_id = int(assign_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if action == "done":
        assign = db_query("SELECT allowed_type, required_count FROM assignments WHERE id=?", (assign_id,), fetchone=True)
        if not assign:
            bot.answer_callback_query(call.id, "این تکلیف یافت نشد!")
            return
        
        allowed_type, req_count = assign[0], assign[1]
        user_states[uid] = {
            'state': 'COLLECTING_FILES',
            'assign_id': assign_id,
            'allowed_type': allowed_type,
            'req_count': req_count,
            'temp_files': []
        }
        type_fa = TYPE_NAMES.get(allowed_type, 'آزاد')
        bot.answer_callback_query(call.id, "آماده دریافت فایل‌های شما...")
        bot.send_message(
            call.message.chat.id,
            f"📤 **ارسال تکلیف شماره #{assign_id}**\n\n"
            f"🔹 **نوع فایل مجاز:** {type_fa}\n"
            f"🔹 **تعداد مورد نیاز:** حداقل {req_count} فایل\n\n"
            f"لطفاً فایل‌های خود را یکی‌یکی ارسال کنید و پس از اتمام، حتماً روی دکمه **«✅ تایید نهایی و ارسال به استاد»** در پایین صفحه بزنید:",
            reply_markup=student_submission_menu(),
            parse_mode="Markdown"
        )

    elif action == "snooze":
        sub = db_query("SELECT snooze_count FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id), fetchone=True)
        if sub:
            new_count = sub[0] + 1
            db_query("UPDATE submissions SET status='SNOOZED', snooze_count=?, updated_at=? WHERE user_id=? AND assignment_id=?", (new_count, now_str, uid, assign_id), commit=True)
        else:
            db_query("INSERT INTO submissions (user_id, assignment_id, status, snooze_count, updated_at) VALUES (?, ?, 'SNOOZED', 1, ?)", (uid, assign_id, now_str), commit=True)

        remind_time = (datetime.now() + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        db_query("INSERT INTO reminders (user_id, assignment_id, remind_at) VALUES (?, ?, ?)", (uid, assign_id, remind_time), commit=True)

        bot.answer_callback_query(call.id, "⏰ یادآوری برای ۱ ساعت دیگر تنظیم شد!", show_alert=True)
        bot.send_message(call.message.chat.id, f"⏳ باشه! دقیقاً **۶۰ دقیقه دیگر** دوباره بهت پیام میدم تا تکلیف شماره #{assign_id} رو فراموش نکنی.", parse_mode="Markdown")

# ==========================================
# ۸. دریافت فایل‌ها در سبد موقت دانش‌آموز
# ==========================================
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def handle_homework_files(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return
    if not is_authenticated(uid):
        send_login_prompt(message.chat.id)
        return

    st_data = user_states.get(uid, {})
    if st_data.get('state') != 'COLLECTING_FILES':
        bot.send_message(
            message.chat.id,
            "⚠️ برای ارسال تکلیف، ابتدا باید روی دکمه **«✅ انجام دادم»** در زیر پیام تکلیف کلیک کنید.",
            parse_mode="Markdown"
        )
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
        bot.send_message(
            message.chat.id,
            f"❌ **فرمت فایل اشتباه است!**\nاستاد برای این تکلیف فقط **{TYPE_NAMES.get(allowed)}** قبول می‌کند. لطفاً فایل درست بفرستید:",
            parse_mode="Markdown"
        )
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
        f"📥 **فایل شماره {curr_count} دریافت شد!** (نیاز: حداقل {req_count} فایل)\n\n"
        "🔸 اگر فایل دیگری دارید بفرستید.\n"
        "🔸 اگر کارتان تمام شده، حتماً روی دکمه **«✅ تایید نهایی و ارسال به استاد»** در کیبورد پایین بزنید.",
        reply_markup=student_submission_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# ۹. مدیریت پیام‌های متنی (لاگین، پنل ادمین و دانشجو)
# ==========================================
@bot.message_handler(content_types=['text'])
def handle_all_text(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return

    text = message.text.strip()
    st_data = user_states.get(uid, {})
    st = st_data.get('state', 'IDLE')

    # ================= دستورات پنل ادمین =================
    if uid in ADMIN_IDS:
        if text == "🔙 انصراف و بازگشت به پنل":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "به پنل مدیریت بازگشتید:", reply_markup=admin_menu())
            return

        # --- بخش تعریف کد و دانش‌آموز جدید توسط ادمین ---
        if text == "👤 افزودن دانش‌پذیر (ساخت کد)":
            user_states[uid] = {'state': 'ADMIN_ADD_STU_NAME'}
            bot.send_message(
                message.chat.id,
                "👤 لطفاً **نام و نام خانوادگی دانش‌پذیر** را وارد کنید (مثلاً: `احمد رضایی`):",
                reply_markup=cancel_admin_menu(),
                parse_mode="Markdown"
            )
            return

        elif st == 'ADMIN_ADD_STU_NAME':
            user_states[uid]['stu_name'] = text
            user_states[uid]['state'] = 'ADMIN_ADD_STU_CODE'
            bot.send_message(
                message.chat.id,
                f"✅ نام دانش‌پذیر: **{text}**\n\n"
                "🔢 حالا **کد ورود اختصاصی** برای این دانش‌پذیر را وارد کنید (مثلاً `1111111111`):",
                reply_markup=cancel_admin_menu(),
                parse_mode="Markdown"
            )
            return

        elif st == 'ADMIN_ADD_STU_CODE':
            code = text.strip()
            stu_name = user_states[uid]['stu_name']
            
            existing_code = db_query("SELECT student_name FROM student_codes WHERE code=?", (code,), fetchone=True)
            if existing_code:
                bot.send_message(message.chat.id, f"⚠️ این کد (`{code}`) قبلاً برای «{existing_code[0]}» ثبت شده است! لطفاً یک کد دیگر وارد کنید:", parse_mode="Markdown")
                return

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            db_query("INSERT INTO student_codes (code, student_name, created_at) VALUES (?, ?, ?)", (code, stu_name, now_str), commit=True)
            user_states[uid] = {'state': 'IDLE'}

            bot.send_message(
                message.chat.id,
                f"🎉 **دانش‌پذیر جدید با موفقیت تعریف شد!**\n\n"
                f"👤 **نام:** {stu_name}\n"
                f"🔑 **کد ورود:** `{code}`\n\n"
                f"حالا می‌توانید این کد را به دانش‌پذیر بدهید تا وارد ربات شود.",
                reply_markup=admin_menu(),
                parse_mode="Markdown"
            )
            return

        elif text == "📋 لیست کدهای دانش‌پذیران":
            codes = db_query("SELECT code, student_name, used_by FROM student_codes ORDER BY created_at DESC")
            if not codes:
                bot.send_message(message.chat.id, "📭 هنوز هیچ کد دانش‌پذیری تعریف نکرده‌اید!")
                return
            
            msg_list = "📋 **لیست دانش‌پذیران و کدهای ورود:**\n\n"
            for c_code, s_name, u_by in codes:
                status_str = f"✅ فعال شده (ID: `{u_by}`)" if u_by else "⏳ هنوز وارد نشده"
                msg_list += f"👤 **{s_name}** | 🔑 کد: `{c_code}`\n└ وضعیت: {status_str}\n\n"
            
            bot.send_message(message.chat.id, msg_list, parse_mode="Markdown")
            return

        # --- بخش ثبت تکلیف جدید ---
        elif text == "➕ ثبت و اعلان تکلیف جدید":
            user_states[uid] = {'state': 'ADMIN_WAIT_TITLE'}
            bot.send_message(message.chat.id, "📌 لطفاً **عنوان تکلیف جدید** را وارد کنید:", reply_markup=cancel_admin_menu(), parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_TITLE':
            user_states[uid]['title'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_DESC'
            bot.send_message(message.chat.id, "📝 عالی! حالا **توضیحات کامل تکلیف** را بنویسید:", reply_markup=cancel_admin_menu(), parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_DESC':
            user_states[uid]['desc'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_TYPE'
            
            t_markup = InlineKeyboardMarkup(row_width=1)
            t_markup.add(
                InlineKeyboardButton("آزاد (هر نوع فایلی) 🗂", callback_data="settype_any"),
                InlineKeyboardButton("فقط عکس 🖼", callback_data="settype_photo"),
                InlineKeyboardButton("فقط ویدیو 🎥", callback_data="settype_video"),
                InlineKeyboardButton("فقط سند/فایل (PDF, ZIP) 📄", callback_data="settype_document"),
                InlineKeyboardButton("فقط صوت یا وویس 🎙", callback_data="settype_audio_voice")
            )
            bot.send_message(message.chat.id, "📂 دانش‌پذیران برای این تکلیف **چه نوع فایلی** باید ارسال کنند؟ انتخاب کنید:", reply_markup=t_markup, parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_COUNT':
            if not text.isdigit() or int(text) < 1:
                bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح و بزرگتر از صفر (مثلاً `1` یا `2`) وارد کنید:")
                return
            
            req_count = int(text)
            title = user_states[uid]['title']
            desc = user_states[uid]['desc']
            allowed_type = user_states[uid]['allowed_type']
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            assign_id = db_query(
                "INSERT INTO assignments (title, description, allowed_type, required_count, created_at) VALUES (?, ?, ?, ?, ?)",
                (title, desc, allowed_type, req_count, now_str), commit=True
            )
            user_states[uid] = {'state': 'IDLE'}

            all_users = db_query("SELECT user_id FROM users")
            sent_count = 0
            for (u_id,) in all_users:
                if u_id not in ADMIN_IDS:
                    send_assignment_prompt(u_id, assign_id, title, desc, allowed_type, req_count)
                    sent_count += 1
                    time.sleep(0.05)

            bot.send_message(
                message.chat.id,
                f"✅ **تکلیف شماره #{assign_id} ثبت شد!**\n"
                f"📂 نوع فایل: {TYPE_NAMES.get(allowed_type)}\n"
                f"🔢 حداقل تعداد: {req_count} فایل\n"
                f"📢 اعلان برای **{sent_count} دانش‌پذیر احراز هویت شده** ارسال گردید.",
                reply_markup=admin_menu(),
                parse_mode="Markdown"
            )
            return

        elif text == "📊 آمار کلی و لینک داشبورد":
            c_count = db_query("SELECT COUNT(*) FROM student_codes", fetchone=True)[0]
            u_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
            a_count = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
            d_count = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]
            s_count = db_query("SELECT SUM(snooze_count) FROM submissions", fetchone=True)[0] or 0
            
            report = (
                f"📊 **گزارش لحظه‌ای سامانه:**\n\n"
                f"🔑 کل کدهای تعریف شده: **{c_count} کد**\n"
                f"👥 دانش‌پذیران وارد شده به بات: **{u_count} نفر**\n"
                f"📚 تعداد تکالیف ثبت شده: **{a_count} مورد**\n"
                f"✅ کل تکالیف تحویل شده: **{d_count} مورد**\n"
                f"⏳ مجموع دفعات تعویق: **{s_count} بار**\n\n"
                f"🌐 **داشبورد تحت وب:**\n"
                f"آدرس سرور رندر خود را به همراه `/admin?pass={WEB_ADMIN_PASS}` باز کنید."
            )
            bot.send_message(message.chat.id, report, parse_mode="Markdown")
            return

        elif text == "📥 دریافت اکسل گزارش‌ها (CSV)":
            rows = db_query('''
                SELECT u.user_id, u.full_name, u.access_code, u.username, a.id, a.title, s.status, s.files_json, s.snooze_count, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                JOIN assignments a ON s.assignment_id = a.id
            ''')
            csv_path = "report.csv"
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["User ID", "Student Name", "Access Code", "Username", "Assignment ID", "Title", "Status", "File Count", "Snooze Count", "Last Updated"])
                for r in rows:
                    f_cnt = len(json.loads(r[7])) if r[7] else 0
                    writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], f_cnt, r[8], r[9]])
            
            with open(csv_path, 'rb') as f:
                bot.send_document(message.chat.id, f, caption="📊 فایل اکسل (CSV) جامع وضعیت تکالیف")
            os.remove(csv_path)
            return

        elif text == "📂 دریافت فایل‌های یک تکلیف":
            user_states[uid] = {'state': 'ADMIN_WAIT_ASSIGN_ID'}
            bot.send_message(message.chat.id, "🔢 لطفاً **شماره تکلیف** مورد نظر را (مثلاً `1`) بفرستید:", reply_markup=cancel_admin_menu(), parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_ASSIGN_ID' and text.isdigit():
            aid = int(text)
            user_states[uid] = {'state': 'IDLE'}
            subs = db_query('''
                SELECT u.user_id, u.full_name, u.access_code, u.username, s.files_json, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                WHERE s.assignment_id=? AND s.status='DONE'
            ''', (aid,))
            
            if not subs:
                bot.send_message(message.chat.id, f"⚠️ هیچ فایل تحویل شده‌ای برای تکلیف شماره #{aid} یافت نشد.", reply_markup=admin_menu())
                return
            
            bot.send_message(message.chat.id, f"📂 در حال ارسال فایل‌های **{len(subs)} دانش‌پذیر** برای تکلیف #{aid}:", reply_markup=admin_menu(), parse_mode="Markdown")
            for stu_id, fname, acode, uname, f_json, up_time in subs:
                f_list = json.loads(f_json) if f_json else []
                for idx, f_item in enumerate(f_list, 1):
                    fid, ftype = f_item['id'], f_item['type']
                    cap = (
                        f"👤 دانش‌پذیر: {fname} (کد: `{acode}`)\n"
                        f"🆔 یوزرنیم: @{uname}\n"
                        f"🔢 تکلیف: #{aid} (فایل {idx} از {len(f_list)})\n"
                        f"📅 تاریخ: {up_time}\n\n"
                        f"⚠️ این پیام را پاک نکنید (آرشیو سیستم)"
                    )
                    markup = None
                    if idx == len(f_list):
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton("🔄 رد تکلیف (درخواست ارسال مجدد)", callback_data=f"redo_{stu_id}_{aid}"))
                    try:
                        if ftype == 'photo': bot.send_photo(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'video': bot.send_video(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'document': bot.send_document(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'audio': bot.send_audio(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'voice': bot.send_voice(message.chat.id, fid, caption=cap, reply_markup=markup, parse_mode="Markdown")
                        time.sleep(0.2)
                    except:
                        pass
            return

    # ================= بررسی مرحله ورود کد توسط دانش‌آموز =================
    if not is_authenticated(uid):
        # کاربر هنوز لاگین نکرده است، پس متن ارسالی او را به عنوان کد ورود بررسی می‌کنیم
        entered_code = text.strip()
        code_row = db_query("SELECT student_name, used_by FROM student_codes WHERE code=?", (entered_code,), fetchone=True)
        
        if not code_row:
            bot.send_message(
                message.chat.id,
                "❌ **کد وارد شده نامعتبر است!**\nلطفاً کد اختصاصی خود را با دقت بررسی کرده و مجدداً ارسال کنید (در صورت مشکل با پشتیبانی تماس بگیرید):",
                parse_mode="Markdown"
            )
            return
        
        stu_name, used_by = code_row[0], code_row[1]
        if used_by is not None and used_by != uid:
            bot.send_message(
                message.chat.id,
                "⛔ **این کد قبلاً توسط دانش‌پذیر دیگری استفاده شده است!**\nهر کد فقط مخصوص یک نفر است.",
                parse_mode="Markdown"
            )
            return

        # ثبت‌نام و قفل کردن کد برای این دانش‌آموز
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        uname = message.from_user.username if message.from_user.username else "ندارد"
        
        db_query("UPDATE student_codes SET used_by=? WHERE code=?", (uid, entered_code), commit=True)
        db_query(
            "INSERT OR REPLACE INTO users (user_id, username, full_name, access_code, joined_at) VALUES (?, ?, ?, ?, ?)",
            (uid, uname, stu_name, entered_code, now_str), commit=True
        )
        user_states[uid] = {'state': 'IDLE'}

        bot.send_message(
            message.chat.id,
            f"✅ **احراز هویت با موفقیت انجام شد!**\n\n"
            f"سلام **{stu_name}** عزیز! 🎓\n"
            f"حساب شما با کد `{entered_code}` فعال شد. از منوی زیر می‌توانید تکالیف خود را پیگیری کنید:",
            reply_markup=student_menu(),
            parse_mode="Markdown"
        )
        return

    # ------------ دکمه‌های مرحله ارسال تکلیف دانش‌آموز ------------
    if st == 'COLLECTING_FILES':
        if text == "🔙 انصراف و پاک کردن فایل‌ها":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "❌ ارسال تکلیف لغو شد و فایل‌های موقت پاک شدند.", reply_markup=student_menu())
            return

        elif text == "✅ تایید نهایی و ارسال به استاد":
            files = st_data['temp_files']
            req_count = st_data['req_count']
            assign_id = st_data['assign_id']

            if len(files) < req_count:
                bot.send_message(
                    message.chat.id,
                    f"⚠️ **تعداد فایل‌ها کامل نیست!**\nبرای این تکلیف باید حداقل **{req_count} فایل** بفرستید، اما شما تا الان **{len(files)} فایل** فرستاده‌اید. لطفاً بقیه فایل‌ها را هم ارسال کنید:",
                    parse_mode="Markdown"
                )
                return

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            files_json = json.dumps(files)

            existing = db_query("SELECT id FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id), fetchone=True)
            if existing:
                db_query(
                    "UPDATE submissions SET status='DONE', files_json=?, updated_at=? WHERE user_id=? AND assignment_id=?",
                    (files_json, now_str, uid, assign_id), commit=True
                )
            else:
                db_query(
                    "INSERT INTO submissions (user_id, assignment_id, status, files_json, updated_at) VALUES (?, ?, 'DONE', ?, ?)",
                    (uid, assign_id, files_json, now_str), commit=True
                )

            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(
                message.chat.id,
                f"🎉 **تایید شد! هر {len(files)} فایل شما برای تکلیف شماره #{assign_id} ثبت و برای استاد ارسال گردید.**",
                reply_markup=student_menu(),
                parse_mode="Markdown"
            )

            # ارسال فایل‌ها برای تمام ادمین‌ها با نام ثبت‌شده توسط ادمین
            st_name, st_code, uname = get_student_info(uid)
            for admin_id in ADMIN_IDS:
                for idx, f_item in enumerate(files, 1):
                    fid, ftype = f_item['id'], f_item['type']
                    is_last = (idx == len(files))
                    
                    archive_caption = (
                        f"📥 **تکلیف #{assign_id}** (فایل {idx} از {len(files)})\n"
                        f"👤 **دانش‌پذیر:** {st_name} (کد: `{st_code}`)\n"
                        f"🆔 **یوزرنیم:** @{uname} | `{uid}`\n"
                        f"📅 **زمان:** {now_str}\n\n"
                        f"⚠️ **این پیام را پاک نکنید (آرشیو سیستم)**"
                    )
                    
                    markup = None
                    if is_last:
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton("🔄 رد تکلیف (درخواست ارسال مجدد)", callback_data=f"redo_{uid}_{assign_id}"))

                    try:
                        if ftype == 'photo': bot.send_photo(admin_id, fid, caption=archive_caption, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'video': bot.send_video(admin_id, fid, caption=archive_caption, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'document': bot.send_document(admin_id, fid, caption=archive_caption, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'audio': bot.send_audio(admin_id, fid, caption=archive_caption, reply_markup=markup, parse_mode="Markdown")
                        elif ftype == 'voice': bot.send_voice(admin_id, fid, caption=archive_caption, reply_markup=markup, parse_mode="Markdown")
                        time.sleep(0.1)
                    except:
                        pass
            return

    # ================= دستورات منوی دانش‌پذیر =================
    if text == "📚 آخرین تکلیف دوره":
        last_assign = db_query("SELECT id, title, description, allowed_type, required_count FROM assignments ORDER BY id DESC LIMIT 1", fetchone=True)
        if not last_assign:
            bot.send_message(message.chat.id, "📭 هنوز هیچ تکلیفی در سیستم ثبت نشده است!")
        else:
            send_assignment_prompt(message.chat.id, last_assign[0], last_assign[1], last_assign[2], last_assign[3], last_assign[4])
        return

    elif text == "🗂 کارنامه و وضعیت من":
        st_name, st_code, _ = get_student_info(uid)
        my_subs = db_query('''
            SELECT a.id, a.title, s.status
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id AND s.user_id = ?
        ''', (uid,))
        if not my_subs:
            bot.send_message(message.chat.id, "📭 هنوز تکلیفی تعریف نشده است.")
            return
        
        rep = f"🗂 **کارنامه دانش‌پذیر: {st_name}** (کد: `{st_code}`)\n\n"
        for aid, title, status in my_subs:
            if status == 'DONE': st_icon = "✅ تحویل داده شده"
            elif status == 'REJECTED': st_icon = "🔄 رد شده (نیاز به ارسال مجدد)"
            elif status == 'SNOOZED': st_icon = "⏳ به تعویق افتاده"
            else: st_icon = "❌ انجام نشده"
            rep += f"🔹 **تکلیف #{aid} ({title}):** {st_icon}\n"
        bot.send_message(message.chat.id, rep, parse_mode="Markdown")
        return

    elif text == "👩‍💻 ارتباط با پشتیبانی":
        bot.send_message(
            message.chat.id,
            f"👩‍💻 برای ارتباط با پشتیبانی دوره به آیدی زیر پیام دهید:\n👉 {SUPPORT_USERNAME}"
        )
        return

    # ================= هدایت پاسخ‌های نامفهوم به پشتیبانی =================
    fallback_markup = InlineKeyboardMarkup()
    fallback_markup.add(InlineKeyboardButton("💬 پیام به پشتیبانی دوره", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}"))
    bot.send_message(
        message.chat.id,
        "🤔 **پیام شما برای سیستم قابل درک نیست!**\n\n"
        "🔸 اگر در حال ارسال فایل تکلیف هستید، پس از فرستادن فایل‌ها روی **«✅ تایید نهایی و ارسال به استاد»** بزنید.\n"
        f"🔸 در صورت نیاز به راهنمایی با پشتیبانی ({SUPPORT_USERNAME}) در ارتباط باشید:",
        reply_markup=fallback_markup,
        parse_mode="Markdown"
    )

print("LMS Bot V3 is running...")
bot.infinity_polling(skip_pending=True)
