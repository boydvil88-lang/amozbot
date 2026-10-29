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
ADMIN_IDS = [7037291902]          # آیدی عددی ادمین‌ها (مثال برای چند ادمین: [7037291902, 123456789])
ADMIN_GROUP_ID = -1000000000000   # آیدی گروه ادمین‌ها (یا داخل گروه دستور /setgroup را بزنید)
COURSE_CHANNEL_ID = "@THERYANC"
COURSE_CHANNEL_LINK = "https://t.me/theRyanc" 
SUPPORT_USERNAME = "@loopfine"
WEB_ADMIN_PASS = "123456"

bot = telebot.TeleBot(TOKEN)
DB_NAME = "lms_database_v6.db"    # دیتابیس نسخه ۶ (مجهز به تایم‌لاین و برد گروه)

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
    # جدول تنظیمات پویا (برای ذخیره آیدی گروه با دستور /setgroup)
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
    # جدول کدهای دانش‌پذیران
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
    # جدول تکالیف (مجهز به deadline_at و group_msg_id برای تابلو زنده گروه)
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
    # جدول ارسال‌ها
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
    # جدول یادآوری‌ها
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
    """دریافت آیدی گروه مدیریت از دیتابیس یا متغیر کانفیگ"""
    row = db_query("SELECT value FROM settings WHERE key='admin_group_id'", fetchone=True)
    if row:
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
    <title>پنل مدیریت تکالیف و امتیازات | Super LMS V6</title>
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
        <h1>📊 داشبورد هوشمند مدیریت دوره و امتیازات (V6)</h1>
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
    return "✅ Super LMS Bot Server V6 is Running!"

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
# ۴. توابع کمکی، تایم‌لاین و تابلو زنده گروه (Group Board)
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
    """بررسی اتمام مهلت ارسال تکلیف"""
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
        return f"⏳ تا `{deadline_str[:16]}` ({hours} ساعت و {mins} دقیقه مانده)"
    except:
        return deadline_str

def build_group_board_text(assign_id):
    """ساخت متن تابلو وضعیت تکلیف در گروه به همراه لیست فرستندگان، زمان و امتیاز"""
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
        f"📢 **تابلو وضعیت تکلیف #{assign_id} ثبت شد**\n"
        f"📌 **عنوان:** {title}\n"
        f"📝 **توضیحات:** {desc}\n"
        f"📂 **نوع فایل:** {TYPE_NAMES.get(allowed_type, 'آزاد')} (حداقل {req_count} فایل)\n"
        f"⏰ **مهلت ارسال:** {dl_text}\n"
        f"────────────────────\n"
        f"👥 **لیست فرستندگان تا این لحظه ({len(subs)} نفر):**\n\n"
    )
    if not subs:
        text += "📭 *هنوز هیچ دانش‌پذیری این تکلیف را ارسال نکرده است.*\n"
    else:
        for idx, (fname, acode, up_time, score, f_json) in enumerate(subs, 1):
            f_cnt = len(json.loads(f_json)) if f_json else 0
            badge = "⚡" if score == 3 else "⭐"
            text += (
                f"👤 **{idx}. {fname}** (کد: `{acode}`)\n"
                f"   └ 🕒 زمان: `{up_time[:16]}` | 📂 `{f_cnt} فایل` | {badge} **{score} امتیاز**\n"
            )
            
    text += f"\n🔍 #تکلیف_{assign_id} #تابلو_تکلیف_{assign_id}"
    return text

def update_or_send_group_board(assign_id):
    """ارسال یا بروزرسانی خودکار تابلو تکلیف در گروه مدیریت"""
    grp_id = get_admin_group()
    if not grp_id:
        return None
    
    assign = db_query("SELECT group_msg_id FROM assignments WHERE id=?", (assign_id,), fetchone=True)
    if not assign:
        return None
    
    board_text = build_group_board_text(assign_id)
    msg_id = assign[0]
    
    if msg_id:
        try:
            bot.edit_message_text(board_text, grp_id, msg_id, parse_mode="Markdown")
            return msg_id
        except Exception as e:
            print(f"Edit board info: {e}")
            return msg_id
    else:
        try:
            sent = bot.send_message(grp_id, board_text, parse_mode="Markdown")
            db_query("UPDATE assignments SET group_msg_id=? WHERE id=?", (sent.message_id, assign_id), commit=True)
            return sent.message_id
        except Exception as e:
            print(f"Send board error: {e}")
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
# ۶. سیستم پوشه‌بندی ۲ لایه‌ای منوهای شیشه‌ای (Inline Folders)
# ==========================================
# --- لایه ۱ و ۲ پنل مدیریت (حداکثر ۲ لایه) ---
def admin_inline_root():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("👥 پوشه ۱: مدیریت دانش‌پذیران و کدها", callback_data="afolder_students"),
        InlineKeyboardButton("📚 پوشه ۲: مدیریت تکالیف و تایم‌لاین", callback_data="afolder_assignments"),
        InlineKeyboardButton("📊 پوشه ۳: گزارشات، پیام همگانی و تنظیمات", callback_data="afolder_reports")
    )
    return markup

def admin_inline_folder_students():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ ساخت کد دانش‌پذیر", callback_data="acmd_add_stu"),
        InlineKeyboardButton("📋 لیست کدها و امتیازات", callback_data="acmd_list_stu")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="afolder_main"))
    return markup

def admin_inline_folder_assignments():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ ثبت تکلیف جدید", callback_data="acmd_new_assign"),
        InlineKeyboardButton("⏰ تغییر مهلت (تایم‌لاین)", callback_data="acmd_edit_deadline"),
        InlineKeyboardButton("📂 دریافت فایل‌های تکلیف", callback_data="acmd_get_files"),
        InlineKeyboardButton("🔄 بروزرسانی تابلو گروه", callback_data="acmd_refresh_boards")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="afolder_main"))
    return markup

def admin_inline_folder_reports():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 آمار و لینک داشبورد", callback_data="acmd_stats"),
        InlineKeyboardButton("📥 خروجی اکسل (CSV)", callback_data="acmd_csv"),
        InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="acmd_broadcast"),
        InlineKeyboardButton("🗑 ریست کل دیتابیس", callback_data="acmd_reset_db")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="afolder_main"))
    return markup

# --- لایه ۱ و ۲ پنل دانش‌پذیر (حداکثر ۲ لایه) ---
def student_inline_root():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📚 پوشه ۱: تکالیف و تمرین‌های دوره", callback_data="sfolder_assignments"),
        InlineKeyboardButton("🗂 پوشه ۲: کارنامه، امتیازات و پشتیبانی", callback_data="sfolder_profile")
    )
    return markup

def student_inline_folder_assignments():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📌 آخرین تکلیف دوره", callback_data="scmd_last_assign"),
        InlineKeyboardButton("📋 لیست همه تکالیف", callback_data="scmd_all_assigns")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="sfolder_main"))
    return markup

def student_inline_folder_profile():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🗂 کارنامه و امتیازات من", callback_data="scmd_report_card"),
        InlineKeyboardButton("👩‍💻 ارتباط با پشتیبانی", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="sfolder_main"))
    return markup

# --- کیبوردهای پایین صفحه برای دسترسی سریع ---
def main_reply_keyboard(is_admin=False):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    if is_admin:
        markup.add(KeyboardButton("👑 باز کردن پنل مدیریت (پوشه‌ها)"))
    else:
        markup.add(KeyboardButton("🎓 باز کردن منوی دانش‌پذیر (پوشه‌ها)"))
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
        "⛔ **مرحله اول: عضویت در کانال دوره**\n\nبرای استفاده از سامانه تکالیف، ابتدا در کانال دوره عضو شوید و سپس دکمه زیر را بزنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

def send_login_prompt(chat_id):
    user_states[chat_id] = {'state': 'WAIT_LOGIN_CODE'}
    bot.send_message(
        chat_id,
        "🔐 **مرحله دوم: احراز هویت دانش‌پذیر**\n\n"
        "لطفاً **کد ورود اختصاصی** خود را (که از استاد دریافت کرده‌اید) ارسال کنید:",
        parse_mode="Markdown"
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
        InlineKeyboardButton("🆘 نیاز به راهنمایی دارم", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}"),
        InlineKeyboardButton("🔙 بازگشت به پوشه تکالیف", callback_data="sfolder_assignments")
    )

    if is_redo:
        prefix = "⚠️ **تکلیف شما توسط استاد رد شد! لطفاً مجدداً ارسال کنید:**\n\n"
    elif is_reminder:
        prefix = "⏰ **یادآوری خودکار تکلیف!** (۱ ساعت گذشت)\n\n"
    else:
        prefix = "📚 **تکلیف دوره:**\n\n"

    type_fa = TYPE_NAMES.get(allowed_type, 'آزاد')
    dl_status = format_deadline_status(deadline_at)
    
    text = (
        f"{prefix}"
        f"📌 **عنوان تمرین (#{assign_id}):** {title}\n"
        f"📝 **توضیحات:**\n{desc}\n\n"
        f"📂 **نوع فایل مجاز:** {type_fa}\n"
        f"🔢 **حداقل تعداد فایل:** {req_count} فایل\n"
        f"⏰ **مهلت ارسال:** {dl_status}\n"
        f"🏆 **امتیاز تحویل در ۲۴ ساعت اول:** ۳ امتیاز (بعد از آن ۱ امتیاز)\n\n"
    )
    if expired and not is_redo:
        text += "⛔ **مهلت ارسال این تکلیف به پایان رسیده و قفل شده است!**"
    else:
        text += "لطفاً وضعیت انجام این تمرین را مشخص کنید:"

    try:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        print(f"Could not send to {chat_id}: {e}")

# ==========================================
# ۸. هندلرهای دستورات، استارت و اتصال گروه (/setgroup)
# ==========================================
@bot.message_handler(commands=['setgroup'])
def set_group_handler(message):
    """ثبت خودکار گروه مدیریت با زدن دستور /setgroup داخل گروه"""
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        return
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "⚠️ این دستور را باید **داخل گروه مدیریت** ارسال کنید!")
        return
    
    grp_id = message.chat.id
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_group_id', ?)", (str(grp_id),), commit=True)
    bot.reply_to(
        message,
        f"✅ **این گروه با موفقیت به عنوان «مرکز مدیریت تکالیف» ثبت شد!**\n"
        f"🆔 آیدی گروه: `{grp_id}`\n\n"
        f"از این لحظه تمام تکالیف، تابلوهای وضعیت فرستندگان و فایل‌های دانش‌پذیران به صورت دسته‌بندی شده در اینجا قرار می‌گیرد.",
        parse_mode="Markdown"
    )

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
        grp_st = f"✅ متصل (`{grp}`)" if grp else "⚠️ متصل نشده (ربات را در گروه ادمین کرده و `/setgroup` بزنید)"
        bot.send_message(
            message.chat.id,
            "👑 **به سامانه مدیریت آموزش (Super LMS V6) خوش آمدید!**",
            reply_markup=main_reply_keyboard(is_admin=True),
            parse_mode="Markdown"
        )
        bot.send_message(
            message.chat.id,
            f"🗂 **پوشه‌های مدیریت سامانه:**\n📡 وضعیت گروه مدیریت: {grp_st}\n\nلطفاً بخش مورد نظر خود را انتخاب کنید:",
            reply_markup=admin_inline_root(),
            parse_mode="Markdown"
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
        f"سلام **{st_name}** عزیز! خوش آمدی 🎓",
        reply_markup=main_reply_keyboard(is_admin=False),
        parse_mode="Markdown"
    )
    bot.send_message(
        message.chat.id,
        f"🗂 **منوی اصلی دانش‌پذیر**\n"
        f"👤 نام: **{st_name}** | 🔑 کد: `{st_code}`\n"
        f"🏆 مجموع امتیازات شما: **{total_pts} امتیاز**\n\n"
        f"لطفاً پوشه مورد نظر خود را انتخاب کنید:",
        reply_markup=student_inline_root(),
        parse_mode="Markdown"
    )

# ==========================================
# ۹. مدیریت تمام دکمه‌های شیشه‌ای (پوشه‌های ۲ لایه و عملیات‌ها)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def master_callback_router(call):
    uid = call.from_user.id
    data = call.data
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    # --- ۱. تایید عضویت کانال ---
    if data == "verify_join":
        if is_subscribed(uid):
            try: bot.delete_message(chat_id, msg_id)
            except: pass
            if is_authenticated(uid):
                st_name, _, _ = get_student_info(uid)
                bot.send_message(chat_id, f"✅ خوش آمدید **{st_name}** عزیز!", reply_markup=main_reply_keyboard(False), parse_mode="Markdown")
                bot.send_message(chat_id, "🗂 **منوی اصلی دانش‌پذیر:**", reply_markup=student_inline_root(), parse_mode="Markdown")
            else:
                send_login_prompt(chat_id)
        else:
            bot.answer_callback_query(call.id, "❌ هنوز در کانال عضو نشده‌اید!", show_alert=True)
        return

    # --- ۲. مسیریابی پوشه‌های ۲ لایه‌ای ادمین ---
    if data.startswith("afolder_"):
        if uid not in ADMIN_IDS: return
        folder = data.replace("afolder_", "")
        if folder == "main":
            grp = get_admin_group()
            grp_st = f"✅ متصل (`{grp}`)" if grp else "⚠️ متصل نشده (`/setgroup` در گروه)"
            bot.edit_message_text(
                f"🗂 **پوشه‌های مدیریت سامانه (لایه ۱):**\n📡 وضعیت گروه مدیریت: {grp_st}\n\nبخش مورد نظر را انتخاب کنید:",
                chat_id, msg_id, reply_markup=admin_inline_root(), parse_mode="Markdown"
            )
        elif folder == "students":
            bot.edit_message_text(
                "👥 **پوشه ۱: مدیریت دانش‌پذیران و کدها (لایه ۲):**\nعملیات مورد نظر را انتخاب کنید:",
                chat_id, msg_id, reply_markup=admin_inline_folder_students(), parse_mode="Markdown"
            )
        elif folder == "assignments":
            bot.edit_message_text(
                "📚 **پوشه ۲: مدیریت تکالیف و تایم‌لاین (لایه ۲):**\nعملیات مورد نظر را انتخاب کنید:",
                chat_id, msg_id, reply_markup=admin_inline_folder_assignments(), parse_mode="Markdown"
            )
        elif folder == "reports":
            bot.edit_message_text(
                "📊 **پوشه ۳: گزارشات، پیام همگانی و تنظیمات (لایه ۲):**\nعملیات مورد نظر را انتخاب کنید:",
                chat_id, msg_id, reply_markup=admin_inline_folder_reports(), parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return

    # --- ۳. دستورات داخل پوشه‌های ادمین ---
    if data.startswith("acmd_"):
        if uid not in ADMIN_IDS: return
        cmd = data.replace("acmd_", "")
        bot.answer_callback_query(call.id)

        if cmd == "add_stu":
            user_states[uid] = {'state': 'ADMIN_ADD_STU_NAME'}
            bot.send_message(chat_id, "👤 لطفاً **نام و نام خانوادگی دانش‌پذیر** را وارد کنید (مثلاً: `احمد رضایی`):", reply_markup=cancel_menu(), parse_mode="Markdown")
        
        elif cmd == "list_stu":
            codes = db_query("SELECT code, student_name, used_by FROM student_codes ORDER BY created_at DESC")
            if not codes:
                bot.send_message(chat_id, "📭 هنوز هیچ کد دانش‌پذیری تعریف نکرده‌اید!")
                return
            msg_list = "📋 **لیست دانش‌پذیران، کدها و امتیازات:**\n\n"
            for c_code, s_name, u_by in codes:
                if u_by:
                    pts = get_student_total_score(u_by)
                    status_str = f"✅ فعال (`{u_by}`) | 🏆 **{pts} امتیاز**"
                else:
                    status_str = "⏳ هنوز وارد نشده"
                msg_list += f"👤 **{s_name}** | 🔑 کد: `{c_code}`\n└ وضعیت: {status_str}\n\n"
            bot.send_message(chat_id, msg_list, reply_markup=admin_inline_folder_students(), parse_mode="Markdown")

        elif cmd == "new_assign":
            user_states[uid] = {'state': 'ADMIN_WAIT_TITLE'}
            bot.send_message(chat_id, "📌 لطفاً **عنوان تکلیف جدید** را وارد کنید (مثلاً: `تکلیف جلسه ۳`):", reply_markup=cancel_menu(), parse_mode="Markdown")

        elif cmd == "edit_deadline":
            assigns = db_query("SELECT id, title, deadline_at FROM assignments ORDER BY id DESC LIMIT 15")
            if not assigns:
                bot.send_message(chat_id, "📭 هنوز تکلیفی ثبت نشده است!")
                return
            markup = InlineKeyboardMarkup(row_width=1)
            for aid, title, dl in assigns:
                dl_short = dl[:16] if dl and dl != "نامحدود" else "نامحدود"
                markup.add(InlineKeyboardButton(f"تکلیف #{aid}: {title} (مهلت: {dl_short})", callback_data=f"editdl_{aid}"))
            markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="afolder_assignments"))
            bot.edit_message_text("⏰ **کدام تکلیف را می‌خواهید تغییر زمان (تمدید/محدود) دهید؟**\nانتخاب کنید:", chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

        elif cmd == "get_files":
            user_states[uid] = {'state': 'ADMIN_WAIT_ASSIGN_ID'}
            bot.send_message(chat_id, "🔢 لطفاً **شماره تکلیف** مورد نظر را (مثلاً `1`) بفرستید:", reply_markup=cancel_menu(), parse_mode="Markdown")

        elif cmd == "refresh_boards":
            grp = get_admin_group()
            if not grp:
                bot.send_message(chat_id, "⚠️ ابتدا ربات را در گروه مدیریت ادمین کنید و دستور `/setgroup` را در گروه بزنید!", parse_mode="Markdown")
                return
            assigns = db_query("SELECT id FROM assignments")
            for (aid,) in assigns:
                update_or_send_group_board(aid)
                time.sleep(0.2)
            bot.send_message(chat_id, "✅ تمام تابلوهای وضعیت تکالیف در گروه مدیریت بروزرسانی شدند!", reply_markup=admin_inline_folder_assignments())

        elif cmd == "stats":
            c_count = db_query("SELECT COUNT(*) FROM student_codes", fetchone=True)[0]
            u_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
            a_count = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
            d_count = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]
            report = (
                f"📊 **گزارش لحظه‌ای سامانه:**\n\n"
                f"🔑 کل کدهای تعریف شده: **{c_count} کد**\n"
                f"👥 دانش‌پذیران وارد شده: **{u_count} نفر**\n"
                f"📚 تعداد تکالیف ثبت شده: **{a_count} مورد**\n"
                f"✅ کل تکالیف تحویل شده: **{d_count} مورد**\n\n"
                f"🌐 **داشبورد تحت وب:**\n"
                f"آدرس سرور خود را به همراه `/admin?pass={WEB_ADMIN_PASS}` باز کنید."
            )
            bot.send_message(chat_id, report, reply_markup=admin_inline_folder_reports(), parse_mode="Markdown")

        elif cmd == "csv":
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
                bot.send_document(chat_id, f, caption="📊 فایل اکسل (CSV) جامع وضعیت تکالیف و امتیازات")
            os.remove(csv_path)

        elif cmd == "broadcast":
            user_states[uid] = {'state': 'ADMIN_WAIT_BROADCAST'}
            bot.send_message(chat_id, "📢 لطفاً **پیام همگانی** خود را (متن، عکس، ویدیو، ویس یا فایل) ارسال کنید:", reply_markup=cancel_menu(), parse_mode="Markdown")

        elif cmd == "reset_db":
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("⚠️ بله، کل اطلاعات پاک شود!", callback_data="confirm_reset_db"))
            markup.add(InlineKeyboardButton("🔙 انصراف", callback_data="afolder_reports"))
            bot.edit_message_text("⚠️ **هشدار جدی:** آیا مطمئن هستید که می‌خواهید تمام اطلاعات دیتابیس را صفر کنید؟", chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")
        return

    # --- ۴. انتخاب تکلیف جهت ویرایش مهلت (تایم‌لاین) توسط ادمین ---
    if data.startswith("editdl_"):
        if uid not in ADMIN_IDS: return
        aid = int(data.replace("editdl_", ""))
        user_states[uid] = {'state': 'ADMIN_EDIT_DEADLINE_HOURS', 'assign_id': aid}
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            f"⏰ **تغییر مهلت ارسال تکلیف شماره #{aid}**\n\n"
            "لطفاً مشخص کنید از **همین لحظه** تا چند ساعت دیگر مهلت ارسال باز باشد؟\n"
            "🔹 یک عدد انگلیسی (مثلاً `24` برای ۲۴ ساعت یا `48` برای دو روز) بفرستید.\n"
            "🔹 برای **نامحدود کردن** عدد `0` را بفرستید.\n"
            "🔹 برای **قفل کردن فوری** عبارت `lock` را بفرستید:",
            reply_markup=cancel_menu(),
            parse_mode="Markdown"
        )
        return

    # --- ۵. انتخاب نوع فایل تکلیف توسط ادمین ---
    if data.startswith("settype_"):
        if uid not in ADMIN_IDS: return
        st_data = user_states.get(uid, {})
        if st_data.get('state') != 'ADMIN_WAIT_TYPE': return

        chosen_type = data.replace("settype_", "")
        user_states[uid]['allowed_type'] = chosen_type
        user_states[uid]['state'] = 'ADMIN_WAIT_COUNT'
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            f"✅ نوع فایل: **{TYPE_NAMES.get(chosen_type)}**\n\n"
            "🔢 حالا مشخص کنید دانش‌پذیر **حداقل چند فایل** باید ارسال کند؟ (یک عدد انگلیسی مثل `1` بفرستید):",
            chat_id, msg_id, parse_mode="Markdown"
        )
        return

    # --- ۶. رد تکلیف توسط ادمین ---
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
        # بروزرسانی تابلو گروه (حذف نام فرد از لیست فرستندگان تایید شده)
        update_or_send_group_board(assign_id)

        assign = db_query("SELECT title, description, allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
        if assign:
            send_assignment_prompt(student_id, assign_id, assign[0], assign[1], assign[2], assign[3], assign[4], is_redo=True)
        
        bot.answer_callback_query(call.id, "🔄 تکلیف رد شد، تابلو گروه آپدیت شد و درخواست ارسال مجدد رفت!", show_alert=True)
        try:
            old_caption = call.message.caption or call.message.text or ""
            bot.edit_message_caption(
                old_caption + "\n\n❌ **توسط ادمین رد شد (امتیاز: ۰) - درخواست ارسال مجدد رفت.**",
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
        bot.edit_message_text("🗑 **تمام اطلاعات دیتابیس با موفقیت پاکسازی و صفر شد!**", chat_id, msg_id, reply_markup=admin_inline_root(), parse_mode="Markdown")
        return

    # ==========================================
    # مسیریابی پوشه‌های ۲ لایه‌ای دانش‌پذیر
    # ==========================================
    if not is_subscribed(uid):
        send_join_wall(chat_id)
        return
    if not is_authenticated(uid):
        send_login_prompt(chat_id)
        return

    if data.startswith("sfolder_"):
        folder = data.replace("sfolder_", "")
        st_name, st_code, _ = get_student_info(uid)
        total_pts = get_student_total_score(uid)
        bot.answer_callback_query(call.id)

        if folder == "main":
            bot.edit_message_text(
                f"🗂 **منوی اصلی دانش‌پذیر (لایه ۱)**\n"
                f"👤 نام: **{st_name}** | 🏆 امتیاز: **{total_pts}**\n\n"
                f"لطفاً پوشه مورد نظر را انتخاب کنید:",
                chat_id, msg_id, reply_markup=student_inline_root(), parse_mode="Markdown"
            )
        elif folder == "assignments":
            bot.edit_message_text(
                "📚 **پوشه ۱: تکالیف و تمرین‌های دوره (لایه ۲):**\nانتخاب کنید:",
                chat_id, msg_id, reply_markup=student_inline_folder_assignments(), parse_mode="Markdown"
            )
        elif folder == "profile":
            bot.edit_message_text(
                "🗂 **پوشه ۲: کارنامه، امتیازات و پشتیبانی (لایه ۲):**\nانتخاب کنید:",
                chat_id, msg_id, reply_markup=student_inline_folder_profile(), parse_mode="Markdown"
            )
        return

    if data.startswith("scmd_"):
        cmd = data.replace("scmd_", "")
        bot.answer_callback_query(call.id)

        if cmd == "last_assign":
            last_assign = db_query("SELECT id, title, description, allowed_type, required_count, deadline_at FROM assignments ORDER BY id DESC LIMIT 1", fetchone=True)
            if not last_assign:
                bot.send_message(chat_id, "📭 هنوز هیچ تکلیفی در سیستم ثبت نشده است!")
            else:
                send_assignment_prompt(chat_id, last_assign[0], last_assign[1], last_assign[2], last_assign[3], last_assign[4], last_assign[5])

        elif cmd == "all_assigns":
            assigns = db_query("SELECT id, title, deadline_at FROM assignments ORDER BY id DESC")
            if not assigns:
                bot.send_message(chat_id, "📭 هنوز تکلیفی ثبت نشده است!")
                return
            markup = InlineKeyboardMarkup(row_width=1)
            for aid, title, dl in assigns:
                sub = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, aid), fetchone=True)
                st_icon = "✅" if (sub and sub[0] == 'DONE') else ("⛔" if is_deadline_passed(dl) else "⏳")
                markup.add(InlineKeyboardButton(f"{st_icon} تکلیف #{aid}: {title}", callback_data=f"viewassign_{aid}"))
            markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="sfolder_assignments"))
            bot.edit_message_text("📋 **لیست کل تکالیف دوره:**\nروی هر تکلیف بزنید تا جزئیات آن باز شود:", chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

        elif cmd == "report_card":
            st_name, st_code, _ = get_student_info(uid)
            total_pts = get_student_total_score(uid)
            my_subs = db_query('''
                SELECT a.id, a.title, s.status, s.score, a.deadline_at
                FROM assignments a
                LEFT JOIN submissions s ON a.id = s.assignment_id AND s.user_id = ?
            ''', (uid,))
            if not my_subs:
                bot.send_message(chat_id, "📭 هنوز تکلیفی تعریف نشده است.")
                return
            
            done_list, pending_list = "", ""
            for aid, title, status, pts, dl in my_subs:
                if status == 'DONE':
                    badge = "⚡" if pts == 3 else "⭐"
                    done_list += f"✅ **تکلیف #{aid} ({title}):** {badge} `{pts} امتیاز`\n"
                elif status == 'REJECTED':
                    pending_list += f"🔄 **تکلیف #{aid} ({title}):** رد شده (نیاز به ارسال مجدد)\n"
                else:
                    lock_txt = " (⛔ مهلت تمام شده)" if is_deadline_passed(dl) else ""
                    pending_list += f"❌ **تکلیف #{aid} ({title}):** انجام نشده{lock_txt}\n"
                    
            rep = (
                f"🗂 **کارنامه دانش‌پذیر:** {st_name} (کد: `{st_code}`)\n"
                f"🏆 **مجموع امتیازات کسب شده:** `{total_pts} امتیاز`\n"
                f"────────────────────\n"
                f"🟢 **تکالیف انجام شده:**\n{done_list if done_list else 'هنوز تکلیفی تحویل نداده‌اید.\n'}\n"
                f"🔴 **تکالیف باقی‌مانده / ناقص:**\n{pending_list if pending_list else '🎉 همه تکالیف را انجام داده‌اید!'}"
            )
            bot.edit_message_text(rep, chat_id, msg_id, reply_markup=student_inline_folder_profile(), parse_mode="Markdown")
        return

    if data.startswith("viewassign_"):
        aid = int(data.replace("viewassign_", ""))
        assign = db_query("SELECT id, title, description, allowed_type, required_count, deadline_at FROM assignments WHERE id=?", (aid,), fetchone=True)
        bot.answer_callback_query(call.id)
        if assign:
            send_assignment_prompt(chat_id, assign[0], assign[1], assign[2], assign[3], assign[4], assign[5])
        return

    # --- ۷. دکمه‌های انجام تکلیف یا تعویق توسط دانش‌آموز ---
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

        # بررسی قفل ددلاین (مگر اینکه استاد درخواست ارسال مجدد REJECTED داده باشد)
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
                f"📤 **ارسال تکلیف شماره #{assign_id}**\n\n"
                f"🔹 **نوع فایل مجاز:** {type_fa}\n"
                f"🔹 **تعداد مورد نیاز:** حداقل {req_count} فایل\n\n"
                f"لطفاً فایل‌های خود را ارسال کنید و در پایان حتماً روی دکمه **«✅ تایید نهایی و ارسال به استاد»** بزنید:",
                reply_markup=student_submission_menu(),
                parse_mode="Markdown"
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
            bot.send_message(chat_id, f"⏳ دقیقاً **۶۰ دقیقه دیگر** دوباره بهت پیام میدم تا تکلیف شماره #{assign_id} رو فراموش نکنی.", parse_mode="Markdown")

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
        bot.send_message(message.chat.id, f"✅ پیام همگانی برای **{sent_cnt} دانش‌پذیر** ارسال شد.", reply_markup=main_reply_keyboard(True), parse_mode="Markdown")
        return

    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return
    if not is_authenticated(uid):
        send_login_prompt(message.chat.id)
        return

    if st != 'COLLECTING_FILES':
        bot.send_message(message.chat.id, "⚠️ برای ارسال تکلیف، ابتدا از پوشه تکالیف روی دکمه **«✅ انجام دادم»** کلیک کنید.", parse_mode="Markdown")
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
        bot.send_message(message.chat.id, f"❌ **فرمت فایل اشتباه است!**\nبرای این تکلیف فقط **{TYPE_NAMES.get(allowed)}** مجاز است:", parse_mode="Markdown")
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
        "🔸 در پایان حتماً روی دکمه **«✅ تایید نهایی و ارسال به استاد»** در پایین صفحه بزنید.",
        reply_markup=student_submission_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# ۱۱. مدیریت پیام‌های متنی
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

    # دکمه‌های باز کردن پوشه‌ها
    if text == "👑 باز کردن پنل مدیریت (پوشه‌ها)" and uid in ADMIN_IDS:
        user_states[uid] = {'state': 'IDLE'}
        bot.send_message(message.chat.id, "🗂 **پوشه‌های مدیریت سامانه (لایه ۱):**", reply_markup=admin_inline_root(), parse_mode="Markdown")
        return

    if text == "🎓 باز کردن منوی دانش‌پذیر (پوشه‌ها)":
        user_states[uid] = {'state': 'IDLE'}
        bot.send_message(message.chat.id, "🗂 **پوشه‌های دانش‌پذیر (لایه ۱):**", reply_markup=student_inline_root(), parse_mode="Markdown")
        return

    # ================= مراحل متنی ادمین =================
    if uid in ADMIN_IDS:
        if text == "🔙 انصراف و بازگشت":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "عملیات لغو شد.", reply_markup=main_reply_keyboard(True))
            bot.send_message(message.chat.id, "🗂 **پوشه‌های مدیریت:**", reply_markup=admin_inline_root(), parse_mode="Markdown")
            return

        if st == 'ADMIN_WAIT_BROADCAST':
            user_states[uid] = {'state': 'IDLE'}
            all_users = db_query("SELECT user_id FROM users")
            sent_cnt = 0
            for (u_id,) in all_users:
                if u_id not in ADMIN_IDS:
                    try:
                        bot.send_message(u_id, f"📢 **اطلاعیه مدیریت دوره:**\n\n{text}", parse_mode="Markdown")
                        sent_cnt += 1
                        time.sleep(0.05)
                    except:
                        pass
            bot.send_message(message.chat.id, f"✅ اطلاعیه برای **{sent_cnt} دانش‌پذیر** ارسال شد.", reply_markup=main_reply_keyboard(True), parse_mode="Markdown")
            return

        elif st == 'ADMIN_ADD_STU_NAME':
            user_states[uid]['stu_name'] = text
            user_states[uid]['state'] = 'ADMIN_ADD_STU_CODE'
            bot.send_message(message.chat.id, f"✅ نام دانش‌پذیر: **{text}**\n\n🔢 حالا **کد ورود اختصاصی** او را وارد کنید (مثلاً `1111111111`):", reply_markup=cancel_menu(), parse_mode="Markdown")
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

            bot.send_message(message.chat.id, f"🎉 دانش‌پذیر **{stu_name}** با کد `{code}` ثبت شد!", reply_markup=main_reply_keyboard(True), parse_mode="Markdown")
            bot.send_message(message.chat.id, "👥 **پوشه مدیریت دانش‌پذیران:**", reply_markup=admin_inline_folder_students(), parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_TITLE':
            user_states[uid]['title'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_DESC'
            bot.send_message(message.chat.id, "📝 عالی! حالا **توضیحات کامل تکلیف** را بنویسید:", reply_markup=cancel_menu(), parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_DESC':
            user_states[uid]['desc'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_TYPE'
            t_markup = InlineKeyboardMarkup(row_width=1)
            for k, v in TYPE_NAMES.items():
                t_markup.add(InlineKeyboardButton(v, callback_data=f"settype_{k}"))
            bot.send_message(message.chat.id, "📂 دانش‌پذیران **چه نوع فایلی** باید ارسال کنند؟", reply_markup=t_markup, parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_COUNT':
            if not text.isdigit() or int(text) < 1:
                bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح بزرگتر از صفر وارد کنید:")
                return
            user_states[uid]['req_count'] = int(text)
            user_states[uid]['state'] = 'ADMIN_WAIT_DEADLINE'
            bot.send_message(
                message.chat.id,
                "⏰ **تعیین تایم‌لاین (مهلت ارسال تکلیف):**\n\n"
                "مشخص کنید دانش‌پذیران **چند ساعت** برای ارسال این تکلیف فرصت دارند؟\n"
                "🔹 یک عدد انگلیسی (مثلاً `24` برای ۱ روز یا `48` برای ۲ روز) بفرستید.\n"
                "🔹 اگر می‌خواهید مهلت **نامحدود** باشد، عدد `0` را بفرستید:",
                reply_markup=cancel_menu(),
                parse_mode="Markdown"
            )
            return

        elif st == 'ADMIN_WAIT_DEADLINE':
            if not text.isdigit() or int(text) < 0:
                bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح (یا `0` برای نامحدود) وارد کنید:")
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
            update_or_send_group_board(assign_id)

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
                f"✅ **تکلیف شماره #{assign_id} ثبت و تابلو آن در گروه ایجاد شد!**\n"
                f"⏰ مهلت ارسال: `{deadline_str}`\n"
                f"📢 اعلان برای **{sent_count} دانش‌پذیر** ارسال گردید.",
                reply_markup=main_reply_keyboard(True),
                parse_mode="Markdown"
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
                bot.send_message(message.chat.id, "⚠️ ورودی نامعتبر! یک عدد به ساعت، `0` برای نامحدود، یا `lock` برای قفل فوری بفرستید:")
                return

            db_query("UPDATE assignments SET deadline_at=? WHERE id=?", (new_dl, aid), commit=True)
            user_states[uid] = {'state': 'IDLE'}
            update_or_send_group_board(aid)

            bot.send_message(
                message.chat.id,
                f"✅ **مهلت ارسال تکلیف #{aid} با موفقیت تغییر کرد!**\n⏰ وضعیت جدید: {format_deadline_status(new_dl)}",
                reply_markup=main_reply_keyboard(True),
                parse_mode="Markdown"
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
                bot.send_message(message.chat.id, f"⚠️ هیچ فایل تحویل شده‌ای برای تکلیف #{aid} یافت نشد.", reply_markup=main_reply_keyboard(True))
                return
            
            bot.send_message(message.chat.id, f"📂 ارسال فایل‌های **{len(subs)} دانش‌پذیر** برای تکلیف #{aid}:", reply_markup=main_reply_keyboard(True), parse_mode="Markdown")
            for stu_id, fname, acode, uname, f_json, pts, up_time in subs:
                f_list = json.loads(f_json) if f_json else []
                for idx, f_item in enumerate(f_list, 1):
                    fid, ftype = f_item['id'], f_item['type']
                    cap = (
                        f"👤 دانش‌پذیر: {fname} (کد: `{acode}`)\n"
                        f"🔢 تکلیف: #{aid} (فایل {idx} از {len(f_list)})\n"
                        f"⭐ امتیاز: **{pts}** | 📅 زمان: `{up_time}`\n\n"
                        f"⚠️ این پیام را پاک نکنید (آرشیو سیستم)"
                    )
                    markup = InlineKeyboardMarkup() if idx == len(f_list) else None
                    if markup:
                        markup.add(InlineKeyboardButton("🔄 رد تکلیف (کسر امتیاز و ارسال مجدد)", callback_data=f"redo_{stu_id}_{aid}"))
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

    # ================= بررسی ورود کد توسط دانش‌آموز =================
    if not is_authenticated(uid):
        entered_code = text.strip()
        code_row = db_query("SELECT student_name, used_by FROM student_codes WHERE code=?", (entered_code,), fetchone=True)
        if not code_row:
            bot.send_message(message.chat.id, "❌ **کد وارد شده نامعتبر است!** مجدداً بررسی و ارسال کنید:", parse_mode="Markdown")
            return
        
        stu_name, used_by = code_row[0], code_row[1]
        if used_by is not None and used_by != uid:
            bot.send_message(message.chat.id, "⛔ **این کد قبلاً توسط دانش‌پذیر دیگری استفاده شده است!**", parse_mode="Markdown")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uname = message.from_user.username if message.from_user.username else "ندارد"
        
        db_query("UPDATE student_codes SET used_by=? WHERE code=?", (uid, entered_code), commit=True)
        db_query("INSERT OR REPLACE INTO users (user_id, username, full_name, access_code, joined_at) VALUES (?, ?, ?, ?, ?)", (uid, uname, stu_name, entered_code, now_str), commit=True)
        user_states[uid] = {'state': 'IDLE'}

        bot.send_message(message.chat.id, f"✅ **احراز هویت انجام شد!** خوش آمدی **{stu_name}** عزیز 🎓", reply_markup=main_reply_keyboard(False), parse_mode="Markdown")
        bot.send_message(message.chat.id, "🗂 **منوی اصلی دانش‌پذیر:**", reply_markup=student_inline_root(), parse_mode="Markdown")
        return

    # ------------ تایید نهایی تکلیف دانش‌آموز، آپدیت تابلو گروه و امتیازدهی ------------
    if st == 'COLLECTING_FILES':
        if text == "🔙 انصراف و پاک کردن فایل‌ها":
            user_states[uid] = {'state': 'IDLE'}
            bot.send_message(message.chat.id, "❌ ارسال تکلیف لغو شد.", reply_markup=main_reply_keyboard(False))
            return

        elif text == "✅ تایید نهایی و ارسال به استاد":
            files = st_data['temp_files']
            req_count = st_data['req_count']
            assign_id = st_data['assign_id']

            if len(files) < req_count:
                bot.send_message(message.chat.id, f"⚠️ شما باید حداقل **{req_count} فایل** بفرستید (تا الان {len(files)} فایل فرستادید).", parse_mode="Markdown")
                return

            now_dt = datetime.now()
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            files_json = json.dumps(files)

            assign_row = db_query("SELECT created_at, deadline_at FROM assignments WHERE id=?", (assign_id,), fetchone=True)
            existing = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id), fetchone=True)

            # بررسی قفل ددلاین در لحظه تایید
            if assign_row and is_deadline_passed(assign_row[1]) and (not existing or existing[0] != 'REJECTED'):
                user_states[uid] = {'state': 'IDLE'}
                bot.send_message(message.chat.id, "⛔ متاسفانه مهلت ارسال این تکلیف به پایان رسیده است!", reply_markup=main_reply_keyboard(False))
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
            speed_msg = "⚡ **(تحویل سریع زیر ۲۴ ساعت!)**" if earned_score == 3 else ""

            bot.send_message(
                message.chat.id,
                f"🎉 **تایید شد! تکلیف شماره #{assign_id} ثبت گردید.**\n\n"
                f"⭐ امتیاز این تکلیف: **{earned_score} امتیاز** {speed_msg}\n"
                f"🏆 مجموع کل امتیازات شما: **{total_pts} امتیاز**",
                reply_markup=main_reply_keyboard(False),
                parse_mode="Markdown"
            )

            # ۱. آپدیت تابلو وضعیت تکلیف در گروه مدیریت (افزودن نام، زمان و امتیاز به لیست فرستندگان)
            board_msg_id = update_or_send_group_board(assign_id)

            # ۲. ارسال فایل‌ها به گروه مدیریت (ریپلای شده روی تابلو همان تکلیف جهت نظم کامل)
            st_name, st_code, uname = get_student_info(uid)
            clean_name = st_name.replace(" ", "_")
            grp_id = get_admin_group()
            target_chats = [grp_id] if grp_id else ADMIN_IDS

            for idx, f_item in enumerate(files, 1):
                fid, ftype = f_item['id'], f_item['type']
                is_last = (idx == len(files))
                
                archive_caption = (
                    f"📥 **فایل تکلیف #{assign_id}** ({idx} از {len(files)})\n"
                    f"👤 **فرستنده:** {st_name} (کد: `{st_code}`)\n"
                    f"🕒 **زمان تحویل:** `{now_str}`\n"
                    f"⭐ **امتیاز دریافتی:** `{earned_score} امتیاز` (مجموع فرد: `{total_pts}`)\n\n"
                    f"🔍 #تکلیف_{assign_id} #{clean_name} #کد_{st_code}\n"
                    f"⚠️ این پیام را پاک نکنید (آرشیو سیستم)"
                )
                
                markup = InlineKeyboardMarkup() if is_last else None
                if markup:
                    markup.add(InlineKeyboardButton("🔄 رد تکلیف (کسر امتیاز و ارسال مجدد)", callback_data=f"redo_{uid}_{assign_id}"))

                for chat_target in target_chats:
                    reply_id = board_msg_id if (grp_id and chat_target == grp_id) else None
                    try:
                        if ftype == 'photo': bot.send_photo(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="Markdown")
                        elif ftype == 'video': bot.send_video(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="Markdown")
                        elif ftype == 'document': bot.send_document(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="Markdown")
                        elif ftype == 'audio': bot.send_audio(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="Markdown")
                        elif ftype == 'voice': bot.send_voice(chat_target, fid, caption=archive_caption, reply_markup=markup, reply_to_message_id=reply_id, parse_mode="Markdown")
                        time.sleep(0.1)
                    except Exception as e:
                        print(f"Error sending archive: {e}")
            return

    # هدایت پیام‌های متفرقه به منوی پوشه‌ای
    if uid in ADMIN_IDS:
        bot.send_message(message.chat.id, "🗂 از پوشه‌های مدیریت زیر انتخاب کنید:", reply_markup=admin_inline_root())
    else:
        bot.send_message(message.chat.id, "🗂 از پوشه‌های زیر انتخاب کنید:", reply_markup=student_inline_root())

print("Super LMS Bot V6 is running...")
bot.infinity_polling(skip_pending=True)
