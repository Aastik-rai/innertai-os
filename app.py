import sqlite3
import json
import os
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# A secret key is required to encrypt user sessions securely
app.secret_key = os.getenv('SECRET_KEY', 'innertai-super-secret-key-123') 

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash')
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(
        timeout=20_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    ),
) if GEMINI_API_KEY else None
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def generate_with_gemini(prompt, json_output=False):
    """Generate text with Gemini and fail with a useful configuration error."""
    if gemini_client is None:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")

    config = {"response_mime_type": "application/json"} if json_output else None
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=config,
    )

    if not response.text:
        raise RuntimeError("Gemini returned an empty response")

    return response.text.strip()


def parse_timed_task_locally(user_input):
    """Fallback for simple tasks such as 'call Sam at 1:08 AM today'."""
    time_match = re.search(
        r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b",
        user_input,
        flags=re.IGNORECASE,
    )
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return None

    meridiem = time_match.group(3).replace('.', '').upper()
    scheduled_time = f"{hour:02d}:{minute:02d} {meridiem}"
    task_name = user_input[:time_match.start()] + user_input[time_match.end():]
    task_name = re.sub(
        r"^\s*(?:please\s+)?(?:remind me to|remember to|add(?: a task to)?)\s+",
        "",
        task_name,
        flags=re.IGNORECASE,
    )
    task_name = re.sub(r"\b(?:today|tomorrow)\b", "", task_name, flags=re.IGNORECASE)
    task_name = re.sub(r"\s+", " ", task_name).strip(" ,.-")
    if not task_name:
        return None

    is_recurring = bool(re.search(r"\b(?:daily|every day|everyday)\b", user_input, re.IGNORECASE))
    return {
        "schedule": [{
            "task_name": task_name,
            "scheduled_time": scheduled_time,
            "is_recurring": is_recurring,
        }],
        "reminders": [],
    }


def save_plan(parsed_data, user_id):
    conn = get_db_connection()
    try:
        for item in parsed_data.get('schedule', []):
            is_rec = 1 if item.get('is_recurring', False) else 0
            conn.execute(
                'INSERT INTO tasks (user_id, task_name, scheduled_time, is_recurring) VALUES (?, ?, ?, ?)',
                (user_id, item['task_name'], item['scheduled_time'], is_rec),
            )
        for item in parsed_data.get('reminders', []):
            conn.execute(
                'INSERT INTO reminders (user_id, item_name, time_context, due_date) VALUES (?, ?, ?, ?)',
                (user_id, item['item_name'], item['time_context'], item.get('due_date', '')),
            )
        conn.commit()
    finally:
        conn.close()

# --- DATABASE SETUP (UPGRADED FOR MULTI-USER) ---
def get_db_connection():
    conn = sqlite3.connect('planner.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    conn = get_db_connection()
    # 1. NEW: Users Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone_number TEXT NOT NULL
        )
    ''')
    # 2. UPGRADED: Added user_id to all tables
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            is_recurring BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            time_context TEXT NOT NULL,
            due_date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            date_logged TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- THE BACKGROUND SCHEDULERS (PHASE 2 UPGRADE) ---
def check_and_send_reminders():
    current_time = datetime.now().strftime("%I:%M %p")
    conn = get_db_connection()
    
    # NEW: Join tasks with users to get the specific phone number for the task owner
    tasks = conn.execute('''
        SELECT tasks.id, tasks.task_name, users.phone_number 
        FROM tasks 
        JOIN users ON tasks.user_id = users.id 
        WHERE tasks.status = 'pending' AND tasks.scheduled_time = ?
    ''', (current_time,)).fetchall()
    
    for task in tasks:
        user_phone = task['phone_number']
        # Ensure it has the whatsapp: prefix for Twilio
        if not user_phone.startswith('whatsapp:'):
            user_phone = f"whatsapp:{user_phone}"
            
        message_body = f"⏰ *REMINDER:* It is {current_time}. Time to: {task['task_name']}"
        try:
            twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER, body=message_body, to=user_phone
            )
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task['id'],))
            conn.commit()
        except Exception as e:
            print(f"Failed to send to {user_phone}: {str(e)}")
    conn.close()

def check_daily_reminders():
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    conn = get_db_connection()
    
    reminders = conn.execute('''
        SELECT reminders.*, users.phone_number 
        FROM reminders 
        JOIN users ON reminders.user_id = users.id 
        WHERE reminders.due_date = ? OR reminders.due_date = ?
    ''', (today.strftime("%Y-%m-%d"), tomorrow.strftime("%Y-%m-%d"))).fetchall()
    
    for item in reminders:
        is_today = item['due_date'] == today.strftime("%Y-%m-%d")
        status = "TODAY" if is_today else "TOMORROW"
        
        user_phone = item['phone_number']
        if not user_phone.startswith('whatsapp:'):
            user_phone = f"whatsapp:{user_phone}"
            
        message_body = f"⚠️ *INNERTAI ALERT:* {status} is the deadline for: {item['item_name']} ({item['time_context']})"
        try:
            twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER, body=message_body, to=user_phone
            )
        except Exception as e:
            pass
    conn.close()

def midnight_reset():
    conn = get_db_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Archive today's tasks into the history vault (preserving user_id)
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    for t in tasks:
        final_status = 'completed' if t['status'] == 'completed' else 'missed'
        conn.execute("INSERT INTO task_history (user_id, task_name, final_status, date_logged) VALUES (?, ?, ?, ?)", 
                     (t['user_id'], t['task_name'], final_status, today))

    # 2. Proceed with normal wipe & reset
    conn.execute("DELETE FROM tasks WHERE is_recurring = 0")
    conn.execute("UPDATE tasks SET status = 'pending' WHERE is_recurring = 1")
    conn.execute("DELETE FROM reminders WHERE due_date < ?", (today,))
    conn.commit()
    conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(func=midnight_reset, trigger="cron", hour=0, minute=0)
scheduler.add_job(func=check_daily_reminders, trigger="cron", hour=8, minute=0)
scheduler.add_job(func=check_and_send_reminders, trigger="cron", second=0)
scheduler.start()


# --- AUTHENTICATION ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (data['username'],)).fetchone()
        conn.close()
        
        # Verify the hashed password matches what they typed
        if user and check_password_hash(user['password'], data['password']):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Invalid username or password"}), 401
        
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    hashed_password = generate_password_hash(data['password'])
    phone = data.get('phone_number', '') 
    
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (username, password, phone_number) VALUES (?, ?, ?)',
                     (data['username'], hashed_password, phone))
        conn.commit()
        # Automatically log them in after registering
        user = conn.execute('SELECT * FROM users WHERE username = ?', (data['username'],)).fetchone()
        session['user_id'] = user['id']
        session['username'] = user['username']
        conn.close()
        return jsonify({"status": "success"})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"status": "error", "message": "Username already exists"}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- APP ROUTES (PROTECTED) ---
@app.route('/', methods=['GET'])
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'])

@app.route('/tasks', methods=['GET'])
def get_tasks():
    if 'user_id' not in session: return jsonify({"status": "error", "message": "Unauthorized"}), 401
    user_id = session['user_id']
    
    conn = get_db_connection()
    # ONLY fetch tasks belonging to the logged-in user
    tasks = conn.execute("SELECT * FROM tasks WHERE user_id = ?", (user_id,)).fetchall()
    reminders = conn.execute("SELECT * FROM reminders WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    
    tasks_list = [{"id": t["id"], "task_name": t["task_name"], "scheduled_time": t["scheduled_time"], "is_recurring": bool(t["is_recurring"]), "status": t["status"]} for t in tasks]
    reminders_list = [{"id": r["id"], "item_name": r["item_name"], "time_context": r["time_context"], "due_date": r["due_date"]} for r in reminders]
    
    return jsonify({"status": "success", "tasks": tasks_list, "reminders": reminders_list})

@app.route('/complete_task/<int:task_id>', methods=['POST'])
def complete_task(task_id):
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    conn = get_db_connection()
    # Ensure they can only complete their own tasks
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ? AND user_id = ?", (task_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/complete_reminder/<int:reminder_id>', methods=['POST'])
def complete_reminder(reminder_id):
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    conn = get_db_connection()
    conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/toggle_recurring/<int:task_id>', methods=['POST'])
def toggle_recurring(task_id):
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    conn = get_db_connection()
    task = conn.execute("SELECT is_recurring FROM tasks WHERE id = ? AND user_id = ?", (task_id, session['user_id'])).fetchone()
    if task:
        new_status = 1 if task['is_recurring'] == 0 else 0
        conn.execute("UPDATE tasks SET is_recurring = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/analyze', methods=['GET'])
def analyze_productivity():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    user_id = session['user_id']
    conn = get_db_connection()
    
    hist_comp = conn.execute("SELECT COUNT(*) FROM task_history WHERE final_status = 'completed' AND user_id = ?", (user_id,)).fetchone()[0]
    today_comp = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed' AND user_id = ?", (user_id,)).fetchone()[0]
    total_completed = hist_comp + today_comp
    
    total_missed = conn.execute("SELECT COUNT(*) FROM task_history WHERE final_status = 'missed' AND user_id = ?", (user_id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending' AND user_id = ?", (user_id,)).fetchone()[0]
    conn.close()

    total_tracked = total_completed + total_missed
    win_rate = int((total_completed / total_tracked) * 100) if total_tracked > 0 else 0

    system_prompt = f"""
    You are Innertai OS. The user's stats: Total Completed: {total_completed}, Missed: {total_missed}, Pending: {pending}, Win Rate: {win_rate}%.
    Write a punchy, 2-sentence aggressive but motivating insight. No emojis, no quotes.
    """
    
    try:
        insight = generate_with_gemini(system_prompt)
        return jsonify({"status": "success", "completed": total_completed, "missed": total_missed, "win_rate": win_rate, "insight": insight})
    except Exception as e:
        app.logger.exception("Gemini productivity analysis failed")
        return jsonify({"status": "error", "message": str(e)}), 502

@app.route('/plan', methods=['POST'])
def plan_day():
    if 'user_id' not in session: return jsonify({"status": "error", "message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    user_input = data.get('tasks', '').strip()
    if not user_input:
        return jsonify({"status": "error", "message": "Please enter at least one task."}), 400
    user_id = session['user_id']
    
    system_prompt = f"""
    Categorize into "schedule" (time-based) and "reminders" (date-based). Current date: {datetime.now().strftime("%Y-%m-%d")}.
    If everyday/daily implies, set "is_recurring" to true.
    Output ONLY valid JSON: {{"schedule": [{{"task_name": "Read", "scheduled_time": "08:00 AM", "is_recurring": true}}], "reminders": []}}
    User input: {user_input}
    """
    used_fallback = False
    try:
        raw_text = generate_with_gemini(system_prompt, json_output=True)
        
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.replace("```", "").strip()
            
        parsed_data = json.loads(raw_text)
    except Exception as e:
        app.logger.exception("Gemini task planning failed")
        parsed_data = parse_timed_task_locally(user_input)
        used_fallback = parsed_data is not None
        if parsed_data is None:
            return jsonify({
                "status": "error",
                "message": "AI planning is unavailable. Try a task with an explicit time, such as 'Call Kapil at 1:08 AM'."
            }), 502

    try:
        save_plan(parsed_data, user_id)
    except (KeyError, TypeError, sqlite3.DatabaseError):
        app.logger.exception("Saving the generated plan failed")
        return jsonify({
            "status": "error",
            "message": "The generated task could not be saved. Please try again."
        }), 500

    message = "Task added using local time parsing." if used_fallback else "Task added."
    return jsonify({"status": "success", "message": message, "fallback": used_fallback})

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, port=5000)
