import sqlite3
import json
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')
YOUR_PERSONAL_NUMBER = os.getenv('YOUR_PERSONAL_NUMBER')

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# --- DATABASE SETUP ---
def get_db_connection():
    conn = sqlite3.connect('planner.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            is_recurring BOOLEAN DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            time_context TEXT NOT NULL,
            due_date TEXT NOT NULL 
        )
    ''')
    # NEW: The History Vault
    conn.execute('''
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            date_logged TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- THE BACKGROUND SCHEDULERS ---
def check_and_send_reminders():
    current_time = datetime.now().strftime("%I:%M %p")
    conn = get_db_connection()
    tasks = conn.execute("SELECT * FROM tasks WHERE status = 'pending' AND scheduled_time = ?", (current_time,)).fetchall()
    
    for task in tasks:
        message_body = f"⏰ *REMINDER:* It is {current_time}. Time to: {task['task_name']}"
        try:
            twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER, body=message_body, to=YOUR_PERSONAL_NUMBER
            )
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task['id'],))
            conn.commit()
        except Exception as e:
            pass
    conn.close()

def check_daily_reminders():
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    conn = get_db_connection()
    reminders = conn.execute("SELECT * FROM reminders WHERE due_date = ? OR due_date = ?", 
                             (today.strftime("%Y-%m-%d"), tomorrow.strftime("%Y-%m-%d"))).fetchall()
    
    for item in reminders:
        is_today = item['due_date'] == today.strftime("%Y-%m-%d")
        status = "TODAY" if is_today else "TOMORROW"
        message_body = f"⚠️ *INNERTAI ALERT:* {status} is the deadline for: {item['item_name']} ({item['time_context']})"
        try:
            twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER, body=message_body, to=YOUR_PERSONAL_NUMBER
            )
        except Exception as e:
            pass
    conn.close()

# UPGRADED: The Midnight Reset now saves history before wiping
def midnight_reset():
    conn = get_db_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Archive today's tasks into the history vault
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    for t in tasks:
        final_status = 'completed' if t['status'] == 'completed' else 'missed'
        conn.execute("INSERT INTO task_history (task_name, final_status, date_logged) VALUES (?, ?, ?)", 
                     (t['task_name'], final_status, today))

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


# --- APP ROUTES ---
@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/tasks', methods=['GET'])
def get_tasks():
    conn = get_db_connection()
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    reminders = conn.execute("SELECT * FROM reminders").fetchall()
    conn.close()
    
    tasks_list = [{"id": t["id"], "task_name": t["task_name"], "scheduled_time": t["scheduled_time"], "is_recurring": bool(t["is_recurring"]), "status": t["status"]} for t in tasks]
    reminders_list = [{"id": r["id"], "item_name": r["item_name"], "time_context": r["time_context"], "due_date": r["due_date"]} for r in reminders]
    
    return jsonify({"status": "success", "tasks": tasks_list, "reminders": reminders_list})

@app.route('/complete_task/<int:task_id>', methods=['POST'])
def complete_task(task_id):
    conn = get_db_connection()
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/complete_reminder/<int:reminder_id>', methods=['POST'])
def complete_reminder(reminder_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/edit_task/<int:task_id>', methods=['POST'])
def edit_task(task_id):
    data = request.json
    conn = get_db_connection()
    conn.execute("UPDATE tasks SET task_name = ?, scheduled_time = ? WHERE id = ?", 
                 (data['task_name'], data['scheduled_time'], task_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/toggle_recurring/<int:task_id>', methods=['POST'])
def toggle_recurring(task_id):
    conn = get_db_connection()
    task = conn.execute("SELECT is_recurring FROM tasks WHERE id = ?", (task_id,)).fetchone()
    new_status = 1 if task['is_recurring'] == 0 else 0
    conn.execute("UPDATE tasks SET is_recurring = ? WHERE id = ?", (new_status, task_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "is_recurring": bool(new_status)})

# NEW: The AI Analytics Route
@app.route('/analyze', methods=['GET'])
def analyze_productivity():
    conn = get_db_connection()
    
    # Calculate lifetime + today's stats
    hist_comp = conn.execute("SELECT COUNT(*) FROM task_history WHERE final_status = 'completed'").fetchone()[0]
    today_comp = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed'").fetchone()[0]
    total_completed = hist_comp + today_comp
    
    total_missed = conn.execute("SELECT COUNT(*) FROM task_history WHERE final_status = 'missed'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
    
    conn.close()

    total_tracked = total_completed + total_missed
    win_rate = int((total_completed / total_tracked) * 100) if total_tracked > 0 else 0

    system_prompt = f"""
    You are Innertai OS, a high-performance productivity AI.
    The user's stats:
    - Total Tasks Completed: {total_completed}
    - Total Tasks Missed (failed): {total_missed}
    - Pending Today: {pending}
    - Overall Win Rate: {win_rate}%

    Write a punchy, 2-sentence aggressive but motivating insight based on these numbers. 
    Act like a tough-love mentor for an agency owner. No hashtags, no emojis, no quotes.
    """
    
    try:
        response = model.generate_content(system_prompt)
        return jsonify({
            "status": "success",
            "completed": total_completed,
            "missed": total_missed,
            "win_rate": win_rate,
            "insight": response.text.strip()
        })
    except Exception as e:
        return jsonify({"status": "error"})

@app.route('/plan', methods=['POST'])
def plan_day():
    data = request.json
    user_input = data.get('tasks', '')
    
    system_prompt = f"""
    You are an automated scheduling assistant for Innertai OS. 
    Current date: {datetime.now().strftime("%Y-%m-%d")}.
    
    Categorize into "schedule" (time-based) and "reminders" (date-based).
    If the user implies a task should happen every day/daily, set "is_recurring" to true. Otherwise false.

    Output ONLY valid JSON:
    {{
        "schedule": [
            {{"task_name": "Read 10 pages", "scheduled_time": "08:00 AM", "is_recurring": true}}
        ],
        "reminders": [
            {{"item_name": "Submit Project", "time_context": "Due 11:59 PM", "due_date": "2026-04-15"}}
        ]
    }}
    
    User input: {user_input}
    """
    
    try:
        response = model.generate_content(system_prompt)
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.replace("```", "").strip()
            
        parsed_data = json.loads(raw_text)
        
        conn = get_db_connection()
        for item in parsed_data.get('schedule', []):
            is_rec = 1 if item.get('is_recurring', False) else 0
            conn.execute('INSERT INTO tasks (task_name, scheduled_time, is_recurring) VALUES (?, ?, ?)', 
                         (item['task_name'], item['scheduled_time'], is_rec))
            
        for item in parsed_data.get('reminders', []):
            conn.execute('INSERT INTO reminders (item_name, time_context, due_date) VALUES (?, ?, ?)', 
                         (item['item_name'], item['time_context'], item.get('due_date', '')))
                         
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Timeline updated."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, port=5000)