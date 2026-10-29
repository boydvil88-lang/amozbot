import os
import time
import csv
import sqlite3
import threading
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request, render_template_string

# ==========================================
# ۱. تنظیمات اصلی سیستم (Configuration)
# ==========================================
TOKEN = "9213797247:AAHVD9o6rHFdsVDP3qHmf2T3CWOAQzehbmA"  # توکن ربات
ADMIN_IDS = [7037291902]  # آیدی عددی ادمین (یا ادمین‌ها)؛ برای دریافت آیدی خود از ربات @userinfobot استفاده کنید
COURSE_CHANNEL_ID = "@THERYANC"  # آیدی کانال دوره برای جوین اجباری (ربات باید در کانال ادمین باشد)
COURSE_CHANNEL_LINK = "https://t.me/theRyanc"
SUPPORT_USERNAME = "@Loopfine"  # آیدی پشتیبانی دوره (مثلا @ehsan_support)
WEB_ADMIN_PASS = "123456"  # رمز عبور برای ورود به داشبورد تحت وب

bot = telebot.TeleBot(TOKEN)
DB_NAME = "lms_database.db"

# وضعیت‌های موقت کاربران در حافظه برای هدایت مراحل ارسال فایل
user_states = {}


# ==========================================
# ۲. راه‌اندازی و مدیریت پایگاه داده (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # جدول دانش‌پذیران
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    joined_at TEXT
                )''')
    # جدول تکالیف تعریف شده توسط استاد/ادمین
    c.execute('''CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    description TEXT,
                    created_at TEXT
                )''')
    # جدول وضعیت انجام و آرشیو فایل‌های هر دانش‌پذیر
    c.execute('''CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    assignment_id INTEGER,
                    status TEXT,
                    file_id TEXT,
                    file_type TEXT,
                    snooze_count INTEGER DEFAULT 0,
                    updated_at TEXT,
                    UNIQUE(user_id, assignment_id)
                )''')
    # جدول زمان‌بندی یادآوری‌ها (Job Scheduler)
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
        .container { max-width: 1000px; margin: auto; background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h1, h2 { color: #eab308; }
        .stats { display: flex; gap: 15px; margin-bottom: 25px; }
        .card { background: #334155; padding: 15px; border-radius: 8px; flex: 1; text-align: center; }
        .card h3 { margin: 0; font-size: 28px; color: #38bdf8; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; background: #0f172a; }
        th, td { border: 1px solid #334155; padding: 10px; text-align: center; }
        th { background: #1e293b; color: #eab308; }
        .done { color: #4ade80; font-weight: bold; }
        .snooze { color: #facc15; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 داشبورد مدیریت و گزارش‌گیری دوره</h1>
        <div class="stats">
            <div class="card"><h3>{{ total_users }}</h3><p>کل دانش‌پذیران</p></div>
            <div class="card"><h3>{{ total_assigns }}</h3><p>تکالیف ثبت شده</p></div>
            <div class="card"><h3>{{ total_done }}</h3><p>تکالیف تحویل شده</p></div>
            <div class="card"><h3>{{ total_snoozes }}</h3><p>دفعات تعویق (یادآوری)</p></div>
        </div>
        <h2>📋 وضعیت ارسال تکالیف دانش‌پذیران</h2>
        <table>
            <tr>
                <th>نام دانش‌پذیر</th>
                <th>یوزرنیم</th>
                <th>شماره و عنوان تکلیف</th>
                <th>وضعیت</th>
                <th>نوع فایل</th>
                <th>تعداد تعویق</th>
                <th>آخرین بروزرسانی</th>
            </tr>
            {% for row in records %}
            <tr>
                <td>{{ row[0] }}</td>
                <td>@{{ row[1] }}</td>
                <td>#{{ row[2] }} - {{ row[3] }}</td>
                <td class="{{ 'done' if row[4]=='انجام شده' else 'snooze' }}">{{ row[4] }}</td>
                <td>{{ row[5] or '---' }}</td>
                <td>{{ row[6] }} بار</td>
                <td>{{ row[7] }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
"""


@app.route('/')
def index():
    return "✅ LMS Bot Server is Running! (Designed by 12EHSAN21)"


@app.route('/admin')
def web_admin():
    pwd = request.args.get('pass')
    if pwd != WEB_ADMIN_PASS:
        return "<h2 style='color:red;text-align:center;margin-top:50px;'>⛔ دسترسی غیرمجاز! رمز عبور را در آدرس وارد کنید: /admin?pass=YOUR_PASS</h2>", 403

    total_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    total_assigns = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
    total_done = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]
    total_snoozes = db_query("SELECT SUM(snooze_count) FROM submissions", fetchone=True)[0] or 0

    records_raw = db_query('''
        SELECT u.full_name, u.username, a.id, a.title, s.status, s.file_type, s.snooze_count, s.updated_at
        FROM submissions s
        JOIN users u ON s.user_id = u.user_id
        JOIN assignments a ON s.assignment_id = a.id
        ORDER BY s.updated_at DESC
    ''')

    records = []
    for r in records_raw:
        status_fa = "انجام شده" if r[4] == 'DONE' else "در انتظار / به تعویق افتاده"
        records.append((r[0], r[1], r[2], r[3], status_fa, r[5], r[6], r[7]))

    return render_template_string(
        HTML_TEMPLATE,
        total_users=total_users, total_assigns=total_assigns,
        total_done=total_done, total_snoozes=total_snoozes, records=records
    )


def run_web():
    app.run(host='0.0.0.0', port=8080)


threading.Thread(target=run_web, daemon=True).start()


# ==========================================
# ۴. موتور زمان‌بند هوشمند (Job Scheduler)
# ==========================================
def reminder_worker():
    """بررسی مداوم دیتابیس برای ارسال یادآوری‌های ۶۰ دقیقه‌ای"""
    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due_reminders = db_query(
                "SELECT id, user_id, assignment_id FROM reminders WHERE sent=0 AND remind_at <= ?",
                (now_str,)
            )
            for rem_id, uid, aid in due_reminders:
                # علامت‌گذاری به عنوان ارسال شده
                db_query("UPDATE reminders SET sent=1 WHERE id=?", (rem_id,), commit=True)

                # بررسی اینکه آیا کاربر در این فاصله تکلیف را انجام داده یا نه
                sub = db_query("SELECT status FROM submissions WHERE user_id=? AND assignment_id=?", (uid, aid),
                               fetchone=True)
                if sub and sub[0] == 'DONE':
                    continue

                assign = db_query("SELECT title, description FROM assignments WHERE id=?", (aid,), fetchone=True)
                if assign:
                    send_assignment_prompt(uid, aid, assign[0], assign[1], is_reminder=True)
        except Exception as e:
            print(f"Scheduler Error: {e}")
        time.sleep(30)


threading.Thread(target=reminder_worker, daemon=True).start()


# ==========================================
# ۵. توابع کمکی و بررسی عضویت (Auth)
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


def send_join_wall(chat_id):
    markup = InlineKeyboardMarkup()
    clean_link = COURSE_CHANNEL_LINK if COURSE_CHANNEL_LINK.startswith(
        "http") else f"https://t.me/{COURSE_CHANNEL_ID.replace('@', '')}"
    markup.add(InlineKeyboardButton("📢 ورود به کانال دوره", url=clean_link))
    markup.add(InlineKeyboardButton("✅ عضو شدم (ورود به بات)", callback_data="verify_join"))
    bot.send_message(
        chat_id,
        "⛔ **احراز هویت دانش‌پذیر**\n\nبرای استفاده از سامانه تکالیف، ابتدا باید در کانال دوره عضو باشید. پس از عضویت، روی دکمه زیر کلیک کنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


def register_user(user):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    uname = user.username if user.username else "ندارد"
    fname = f"{user.first_name or ''} {user.last_name or ''}".strip()
    db_query(
        "INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
        (user.id, uname, fname, now),
        commit=True
    )


def send_assignment_prompt(chat_id, assign_id, title, desc, is_reminder=False):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ انجام دادم (ارسال فایل تکلیف)", callback_data=f"done_{assign_id}"),
        InlineKeyboardButton("⏳ یک ساعت دیگر انجام می‌دهم", callback_data=f"snooze_{assign_id}"),
        InlineKeyboardButton("🆘 نیاز به راهنمایی و پشتیبانی دارم",
                             url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}")
    )
    prefix = "⏰ **یادآوری خودکار تکلیف!** (۱ ساعت گذشت)\n\n" if is_reminder else "📚 **تکلیف جدید ثبت شد!**\n\n"
    text = (
        f"{prefix}"
        f"📌 **عنوان تمرین (#{assign_id}):** {title}\n"
        f"📝 **توضیحات:**\n{desc}\n\n"
        f"لطفاً وضعیت انجام این تمرین را از طریق کلیدهای زیر مشخص کنید:"
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


def admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("➕ ثبت و اعلان تکلیف جدید"),
        KeyboardButton("📊 آمار کلی و لینک داشبورد"),
        KeyboardButton("📥 دریافت اکسل گزارش‌ها (CSV)"),
        KeyboardButton("📂 دریافت فایل‌های یک تکلیف")
    )
    return markup


# ==========================================
# ۷. هندلرهای ربات تلگرام
# ==========================================
@bot.message_handler(commands=['start'])
def start_handler(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return

    register_user(message.from_user)
    user_states[uid] = {'state': 'IDLE'}

    if uid in ADMIN_IDS:
        bot.send_message(
            message.chat.id,
            "👑 **به پنل مدیریت سامانه آموزش خوش آمدید!**\nاز منوی زیر برای مدیریت تکالیف و دانشجویان استفاده کنید:",
            reply_markup=admin_menu(),
            parse_mode="Markdown"
        )
    else:
        bot.send_message(
            message.chat.id,
            f"سلام {message.from_user.first_name} عزیز! 🎓\nبه سامانه هوشمند پیگیری تکالیف خوش آمدی.\nاز منوی زیر می‌تونی وضعیت تمرین‌هات رو دنبال کنی:",
            reply_markup=student_menu()
        )


@bot.callback_query_handler(func=lambda call: call.data == "verify_join")
def verify_join_cb(call):
    if is_subscribed(call.from_user.id):
        register_user(call.from_user)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(call.message.chat.id, "✅ عضویت شما تایید شد! خوش آمدید:", reply_markup=student_menu())
    else:
        bot.answer_callback_query(call.id, "❌ شما هنوز در کانال دوره عضو نشده‌اید!", show_alert=True)


# ---------------- مدیریت کلیدهای شیشه‌ای تکالیف ----------------
@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "snooze_")))
def assignment_actions_cb(call):
    uid = call.from_user.id
    if not is_subscribed(uid):
        send_join_wall(call.message.chat.id)
        return

    action, assign_id = call.data.split("_")
    assign_id = int(assign_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if action == "done":
        user_states[uid] = {'state': 'WAIT_HOMEWORK_FILE', 'assign_id': assign_id}
        bot.answer_callback_query(call.id, "لطفاً فایل تکلیف را ارسال کنید.")
        bot.send_message(
            call.message.chat.id,
            f"📤 **ارسال تکلیف شماره #{assign_id}**\n\n"
            "لطفاً همین الان فایل تمرین خود را (به صورت **عکس، ویدیو، فایل صوتی/وویس یا سند PDF/ZIP**) در همین صفحه ارسال کنید:",
            parse_mode="Markdown"
        )

    elif action == "snooze":
        # ثبت وضعیت تعویق در دیتابیس
        sub = db_query("SELECT snooze_count FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id),
                       fetchone=True)
        if sub:
            new_count = sub[0] + 1
            db_query(
                "UPDATE submissions SET status='SNOOZED', snooze_count=?, updated_at=? WHERE user_id=? AND assignment_id=?",
                (new_count, now_str, uid, assign_id), commit=True)
        else:
            db_query(
                "INSERT INTO submissions (user_id, assignment_id, status, snooze_count, updated_at) VALUES (?, ?, 'SNOOZED', 1, ?)",
                (uid, assign_id, now_str), commit=True)

        # تنظیم یادآوری برای دقیقاً ۶۰ دقیقه دیگر
        remind_time = (datetime.now() + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        db_query("INSERT INTO reminders (user_id, assignment_id, remind_at) VALUES (?, ?, ?)",
                 (uid, assign_id, remind_time), commit=True)

        bot.answer_callback_query(call.id, "⏰ یادآوری برای ۱ ساعت دیگر تنظیم شد!", show_alert=True)
        bot.send_message(call.message.chat.id,
                         f"⏳ باشه! دقیقاً **۶۰ دقیقه دیگر** دوباره بهت پیام میدم تا تکلیف شماره #{assign_id} رو فراموش نکنی. موفق باشی!",
                         parse_mode="Markdown")


# ---------------- دریافت و آرشیو انواع فایل تکلیف ----------------
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def handle_homework_files(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return

    st = user_states.get(uid, {}).get('state')
    if st != 'WAIT_HOMEWORK_FILE':
        bot.send_message(
            message.chat.id,
            "⚠️ برای ارسال تکلیف، ابتدا باید روی دکمه **«✅ انجام دادم»** در زیر پیام همان تکلیف کلیک کنید.\n(می‌توانید از دکمه «📚 آخرین تکلیف دوره» استفاده کنید).",
            parse_mode="Markdown"
        )
        return

    assign_id = user_states[uid]['assign_id']

    # استخراج شناسه فایل بر اساس نوع آن
    ftype = message.content_type
    if ftype == 'photo':
        fid = message.photo[-1].file_id
    elif ftype == 'video':
        fid = message.video.file_id
    elif ftype == 'document':
        fid = message.document.file_id
    elif ftype == 'audio':
        fid = message.audio.file_id
    elif ftype == 'voice':
        fid = message.voice.file_id

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ذخیره در دیتابیس
    existing = db_query("SELECT id FROM submissions WHERE user_id=? AND assignment_id=?", (uid, assign_id),
                        fetchone=True)
    if existing:
        db_query(
            "UPDATE submissions SET status='DONE', file_id=?, file_type=?, updated_at=? WHERE user_id=? AND assignment_id=?",
            (fid, ftype, now_str, uid, assign_id), commit=True
        )
    else:
        db_query(
            "INSERT INTO submissions (user_id, assignment_id, status, file_id, file_type, updated_at) VALUES (?, ?, 'DONE', ?, ?, ?)",
            (uid, assign_id, fid, ftype, now_str), commit=True
        )

    user_states[uid] = {'state': 'IDLE'}
    bot.send_message(
        message.chat.id,
        f"🎉 **خسته نباشی! تکلیف شماره #{assign_id} با موفقیت ثبت و آرشیو شد.**",
        reply_markup=student_menu(),
        parse_mode="Markdown"
    )

    # ارسال نسخه آرشیوی منظم برای ادمین‌ها
    uname = f"@{message.from_user.username}" if message.from_user.username else "بدون یوزرنیم"
    archive_caption = (
        f"📥 **تکلیف جدید دریافت شد!**\n"
        f"🔢 **شماره تکلیف:** #{assign_id}\n"
        f"👤 **نام دانش‌پذیر:** {message.from_user.first_name}\n"
        f"🆔 **یوزرنیم:** {uname}\n"
        f"🔢 **آیدی عددی:** `{uid}`\n"
        f"📅 **زمان ارسال:** {now_str}"
    )
    for admin_id in ADMIN_IDS:
        try:
            if ftype == 'photo':
                bot.send_photo(admin_id, fid, caption=archive_caption, parse_mode="Markdown")
            elif ftype == 'video':
                bot.send_video(admin_id, fid, caption=archive_caption, parse_mode="Markdown")
            elif ftype == 'document':
                bot.send_document(admin_id, fid, caption=archive_caption, parse_mode="Markdown")
            elif ftype == 'audio':
                bot.send_audio(admin_id, fid, caption=archive_caption, parse_mode="Markdown")
            elif ftype == 'voice':
                bot.send_voice(admin_id, fid, caption=archive_caption, parse_mode="Markdown")
        except:
            pass


# ---------------- مدیریت پیام‌های متنی (پنل ادمین، دانشجو و پاسخ نامفهوم) ----------------
@bot.message_handler(content_types=['text'])
def handle_all_text(message):
    uid = message.from_user.id
    if not is_subscribed(uid):
        send_join_wall(message.chat.id)
        return

    register_user(message.from_user)
    text = message.text.strip()
    st = user_states.get(uid, {}).get('state', 'IDLE')

    # ================= دستورات پنل ادمین =================
    if uid in ADMIN_IDS:
        if text == "➕ ثبت و اعلان تکلیف جدید":
            user_states[uid] = {'state': 'ADMIN_WAIT_TITLE'}
            bot.send_message(message.chat.id,
                             "📌 لطفاً **عنوان تکلیف جدید** را وارد کنید (مثلاً: تمرین جلسه اول پایتون):",
                             parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_TITLE':
            user_states[uid]['title'] = text
            user_states[uid]['state'] = 'ADMIN_WAIT_DESC'
            bot.send_message(message.chat.id,
                             "📝 عالی! حالا **توضیحات کامل تکلیف** را بنویسید (به محض ارسال، برای تمام دانشجویان دوره اعلان می‌رود):",
                             parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_DESC':
            title = user_states[uid]['title']
            desc = text
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            assign_id = db_query("INSERT INTO assignments (title, description, created_at) VALUES (?, ?, ?)",
                                 (title, desc, now_str), commit=True)
            user_states[uid] = {'state': 'IDLE'}

            # ارسال اعلان خودکار به تمام دانش‌پذیران
            all_users = db_query("SELECT user_id FROM users")
            sent_count = 0
            for (u_id,) in all_users:
                send_assignment_prompt(u_id, assign_id, title, desc)
                sent_count += 1
                time.sleep(0.05)  # جلوگیری از محدودیت اسپم تلگرام

            bot.send_message(message.chat.id,
                             f"✅ **تکلیف شماره #{assign_id} ثبت شد** و اعلان آن با موفقیت برای **{sent_count} دانش‌پذیر** ارسال گردید!",
                             reply_markup=admin_menu(), parse_mode="Markdown")
            return

        elif text == "📊 آمار کلی و لینک داشبورد":
            u_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
            a_count = db_query("SELECT COUNT(*) FROM assignments", fetchone=True)[0]
            d_count = db_query("SELECT COUNT(*) FROM submissions WHERE status='DONE'", fetchone=True)[0]
            s_count = db_query("SELECT SUM(snooze_count) FROM submissions", fetchone=True)[0] or 0

            report = (
                f"📊 **گزارش لحظه‌ای سامانه:**\n\n"
                f"👥 کل دانش‌پذیران ثبت‌نامی: **{u_count} نفر**\n"
                f"📚 تعداد تکالیف تعریف شده: **{a_count} مورد**\n"
                f"✅ کل تکالیف تحویل داده شده: **{d_count} مورد**\n"
                f"⏳ مجموع دفعات تعویق (Snooze): **{s_count} بار**\n\n"
                f"🌐 **داشبورد تحت وب:**\n"
                f"آدرس سرور خود را به همراه `/admin?pass={WEB_ADMIN_PASS}` در مرورگر باز کنید."
            )
            bot.send_message(message.chat.id, report, parse_mode="Markdown")
            return

        elif text == "📥 دریافت اکسل گزارش‌ها (CSV)":
            rows = db_query('''
                SELECT u.user_id, u.full_name, u.username, a.id, a.title, s.status, s.file_type, s.snooze_count, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                JOIN assignments a ON s.assignment_id = a.id
            ''')
            csv_path = "report.csv"
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["User ID", "Full Name", "Username", "Assignment ID", "Title", "Status", "File Type",
                                 "Snooze Count", "Last Updated"])
                writer.writerows(rows)

            with open(csv_path, 'rb') as f:
                bot.send_document(message.chat.id, f, caption="📊 فایل اکسل (CSV) جامع وضعیت تکالیف دانش‌پذیران")
            os.remove(csv_path)
            return

        elif text == "📂 دریافت فایل‌های یک تکلیف":
            user_states[uid] = {'state': 'ADMIN_WAIT_ASSIGN_ID'}
            bot.send_message(message.chat.id,
                             "🔢 لطفاً **شماره تکلیف** مورد نظر را (فقط به صورت عدد، مثلاً `1`) ارسال کنید تا تمام فایل‌های ارسالی دانشجویان برای آن تکلیف برایتان ارسال شود:",
                             parse_mode="Markdown")
            return

        elif st == 'ADMIN_WAIT_ASSIGN_ID' and text.isdigit():
            aid = int(text)
            user_states[uid] = {'state': 'IDLE'}
            subs = db_query('''
                SELECT u.full_name, u.username, s.file_id, s.file_type, s.updated_at
                FROM submissions s
                JOIN users u ON s.user_id = u.user_id
                WHERE s.assignment_id=? AND s.status='DONE'
            ''', (aid,))

            if not subs:
                bot.send_message(message.chat.id, f"⚠️ هیچ فایل تحویل شده‌ای برای تکلیف شماره #{aid} یافت نشد.")
                return

            bot.send_message(message.chat.id, f"📂 در حال ارسال **{len(subs)} فایل** مربوط به تکلیف #{aid}:",
                             parse_mode="Markdown")
            for fname, uname, fid, ftype, up_time in subs:
                cap = f"👤 دانش‌پذیر: {fname} (@{uname})\n🔢 تکلیف: #{aid}\n📅 تاریخ: {up_time}"
                try:
                    if ftype == 'photo':
                        bot.send_photo(message.chat.id, fid, caption=cap)
                    elif ftype == 'video':
                        bot.send_video(message.chat.id, fid, caption=cap)
                    elif ftype == 'document':
                        bot.send_document(message.chat.id, fid, caption=cap)
                    elif ftype == 'audio':
                        bot.send_audio(message.chat.id, fid, caption=cap)
                    elif ftype == 'voice':
                        bot.send_voice(message.chat.id, fid, caption=cap)
                    time.sleep(0.2)
                except:
                    pass
            return

    # ================= دستورات منوی دانش‌پذیر =================
    if text == "📚 آخرین تکلیف دوره":
        last_assign = db_query("SELECT id, title, description FROM assignments ORDER BY id DESC LIMIT 1", fetchone=True)
        if not last_assign:
            bot.send_message(message.chat.id, "📭 هنوز هیچ تکلیفی در سیستم ثبت نشده است!")
        else:
            send_assignment_prompt(message.chat.id, last_assign[0], last_assign[1], last_assign[2])
        return

    elif text == "🗂 کارنامه و وضعیت من":
        my_subs = db_query('''
            SELECT a.id, a.title, s.status, s.snooze_count
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id AND s.user_id = ?
        ''', (uid,))
        if not my_subs:
            bot.send_message(message.chat.id, "📭 هنوز تکلیفی تعریف نشده است.")
            return

        rep = "🗂 **وضعیت تکالیف شما در دوره:**\n\n"
        for aid, title, status, scount in my_subs:
            st_icon = "✅ تحویل داده شده" if status == 'DONE' else (
                "⏳ به تعویق افتاده" if status == 'SNOOZED' else "❌ انجام نشده")
            rep += f"🔹 **تکلیف #{aid} ({title}):** {st_icon}\n"
        bot.send_message(message.chat.id, rep, parse_mode="Markdown")
        return

    elif text == "👩‍💻 ارتباط با پشتیبانی":
        bot.send_message(
            message.chat.id,
            f"👩‍💻 در صورتی که در انجام تمرین‌ها سوالی دارید یا به راهنمایی نیاز دارید، می‌توانید مستقیماً به آیدی پشتیبانی دوره پیام دهید:\n👉 {SUPPORT_USERNAME}"
        )
        return

    # ================= هدایت پاسخ‌های نامفهوم به پشتیبانی (بند ۳ مستند) =================
    fallback_markup = InlineKeyboardMarkup()
    fallback_markup.add(
        InlineKeyboardButton("💬 پیام به پشتیبانی دوره", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}"))
    bot.send_message(
        message.chat.id,
        "🤔 **پیام شما برای سیستم قابل درک نیست!**\n\n"
        "🔸 اگر قصد ارسال تکلیف را دارید، ابتدا از منوی پایین روی **«📚 آخرین تکلیف دوره»** بزنید و گزینه **«✅ انجام دادم»** را انتخاب کنید.\n"
        f"🔸 اگر سوالی دارید یا نیاز به راهنمایی دارید، با پشتیبانی دوره ({SUPPORT_USERNAME}) در ارتباط باشید:",
        reply_markup=fallback_markup,
        parse_mode="Markdown"
    )


print("LMS Bot is running...")
bot.infinity_polling(skip_pending=True)
