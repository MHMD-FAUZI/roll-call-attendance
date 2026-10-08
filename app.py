import os
import io
import csv
import string
import random
import datetime
import sqlite3
from flask import (
    Flask, render_template, request, redirect, 
    url_for, flash, session, Response
)
import qrcode
import qrcode.image.svg

app = Flask(__name__)
app.secret_key = "rollcall_cloud_secret_key_998877"

# Authorized Admins and Master Password
AUTHORIZED_ADMINS = {
    "yuszhairil": "Yuszhairil",
    "amira": "Amira",
    "fauzi": "Fauzi"
}
ADMIN_SECRET_PASSWORD = "DFS"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "rollcall.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_passcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passcode TEXT NOT NULL,
            for_date TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            semester TEXT NOT NULL,
            status TEXT DEFAULT 'Present',
            reason TEXT,
            photo TEXT,
            attendance_date TEXT NOT NULL,
            submission_time TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    cursor.execute("PRAGMA table_info(attendance)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'status' not in columns:
        cursor.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    if 'reason' not in columns:
        cursor.execute("ALTER TABLE attendance ADD COLUMN reason TEXT")
    if 'photo' not in columns:
        cursor.execute("ALTER TABLE attendance ADD COLUMN photo TEXT")
    
    today = datetime.date.today().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM daily_passcodes WHERE for_date = ?", (today,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO daily_passcodes (passcode, for_date, is_active) VALUES ('z123', ?, 1)", (today,))
        
    conn.commit()
    conn.close()

init_db()

def generate_random_passcode():
    letter = random.choice(string.ascii_lowercase)
    digits = "".join(random.choices(string.digits, k=3))
    return f"{letter}{digits}"

def get_or_create_today_passcode():
    today = datetime.date.today().strftime("%Y-%m-%d")
    conn = get_db_connection()
    row = conn.execute("SELECT passcode FROM daily_passcodes WHERE for_date = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (today,)).fetchone()
    
    if row:
        code = row['passcode']
        conn.close()
        return code
    
    new_code = generate_random_passcode()
    conn.execute("INSERT OR REPLACE INTO daily_passcodes (passcode, for_date, is_active) VALUES (?, ?, 1)", (new_code, today))
    conn.commit()
    conn.close()
    return new_code

@app.route("/")
def index():
    today_date = datetime.date.today().strftime("%Y-%m-%d")
    return render_template("index.html", today_date=today_date)

@app.route("/absence")
def absence():
    today_date = datetime.date.today().strftime("%Y-%m-%d")
    return render_template("absence.html", today_date=today_date)

@app.route("/submit", methods=["POST"])
def submit_attendance():
    student_name = request.form.get("student_name", "").strip()
    class_name = request.form.get("class_name", "").strip()
    semester = request.form.get("semester", "").strip()
    attendance_date = request.form.get("attendance_date", "").strip()
    user_passcode = request.form.get("passcode", "").strip().lower()
    photo = request.form.get("photo", "").strip()

    if not all([student_name, class_name, semester, attendance_date, user_passcode]):
        flash("Please fill in all required fields in the form.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    today = datetime.date.today().strftime("%Y-%m-%d")
    active_code_row = conn.execute("SELECT passcode FROM daily_passcodes WHERE for_date = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (today,)).fetchone()

    if not active_code_row or user_passcode != active_code_row['passcode'].lower():
        conn.close()
        flash("Invalid daily passcode! Please obtain the correct code from your instructor.", "error")
        return redirect(url_for("index"))

    dup = conn.execute("SELECT id FROM attendance WHERE LOWER(student_name) = LOWER(?) AND LOWER(class_name) = LOWER(?) AND attendance_date = ? LIMIT 1", (student_name, class_name, attendance_date)).fetchone()
    if dup:
        conn.close()
        flash(f"Student '{student_name}' ({class_name}) has already submitted attendance for today.", "warning")
        return redirect(url_for("index"))

    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    conn.execute("INSERT INTO attendance (student_name, class_name, semester, status, reason, photo, attendance_date, submission_time) VALUES (?, ?, ?, 'Present', NULL, ?, ?, ?)", (student_name, class_name, semester, photo if photo else None, attendance_date, now_time))
    conn.commit()
    conn.close()

    return render_template("success.html", student_name=student_name, class_name=class_name, semester=semester, status="Present", reason=None, photo=photo, attendance_date=attendance_date, submission_time=now_time)

@app.route("/submit-absence", methods=["POST"])
def submit_absence():
    student_name = request.form.get("student_name", "").strip()
    class_name = request.form.get("class_name", "").strip()
    semester = request.form.get("semester", "").strip()
    attendance_date = request.form.get("attendance_date", "").strip()
    reason = request.form.get("reason", "").strip()
    photo = request.form.get("photo", "").strip()

    if not all([student_name, class_name, semester, attendance_date, reason]):
        flash("Please fill in all required fields including the reason for absence.", "error")
        return redirect(url_for("absence"))

    conn = get_db_connection()
    dup = conn.execute("SELECT id FROM attendance WHERE LOWER(student_name) = LOWER(?) AND LOWER(class_name) = LOWER(?) AND attendance_date = ? LIMIT 1", (student_name, class_name, attendance_date)).fetchone()
    if dup:
        conn.close()
        flash(f"Student '{student_name}' ({class_name}) already has a record submitted for today.", "warning")
        return redirect(url_for("absence"))

    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    conn.execute("INSERT INTO attendance (student_name, class_name, semester, status, reason, photo, attendance_date, submission_time) VALUES (?, ?, ?, 'Absent', ?, ?, ?, ?)", (student_name, class_name, semester, reason, photo if photo else None, attendance_date, now_time))
    conn.commit()
    conn.close()

    return render_template("success.html", student_name=student_name, class_name=class_name, semester=semester, status="Absent", reason=reason, photo=photo, attendance_date=attendance_date, submission_time=now_time)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        raw_username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        username_key = raw_username.lower()

        if username_key in AUTHORIZED_ADMINS:
            canonical_username = AUTHORIZED_ADMINS[username_key]
            
            if password == ADMIN_SECRET_PASSWORD:
                session["admin_logged_in"] = True
                session["admin_user"] = canonical_username
                flash(f"Welcome back, {canonical_username}!", "success")
                return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password. Access is restricted to authorized administrators.", "error")

    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("You have been successfully logged out.", "info")
    return redirect(url_for("admin_login"))

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        flash("Please log in to access the admin dashboard.", "warning")
        return redirect(url_for("admin_login"))

    today_date = datetime.date.today().strftime("%Y-%m-%d")
    selected_date = request.args.get("filter_date", today_date)
    selected_class = request.args.get("filter_class", "").strip()
    selected_status = request.args.get("filter_status", "").strip()

    conn = get_db_connection()
    current_passcode = get_or_create_today_passcode()

    present_row = conn.execute("SELECT COUNT(*) as count FROM attendance WHERE attendance_date = ? AND (status = 'Present' OR status IS NULL)", (today_date,)).fetchone()
    total_present_today = present_row['count'] if present_row else 0

    absent_row = conn.execute("SELECT COUNT(*) as count FROM attendance WHERE attendance_date = ? AND status = 'Absent'", (today_date,)).fetchone()
    total_absent_today = absent_row['count'] if absent_row else 0

    total_all_row = conn.execute("SELECT COUNT(*) as count FROM attendance").fetchone()
    total_all = total_all_row['count'] if total_all_row else 0

    distinct_classes = conn.execute("SELECT DISTINCT class_name FROM attendance ORDER BY class_name ASC").fetchall()

    query = "SELECT * FROM attendance WHERE attendance_date = ?"
    params = [selected_date]
    if selected_class:
        query += " AND class_name = ?"
        params.append(selected_class)
    if selected_status:
        query += " AND status = ?"
        params.append(selected_status)
    query += " ORDER BY id DESC"
    
    attendance_list = conn.execute(query, tuple(params)).fetchall()
    conn.close()

    return render_template(
        "admin_dashboard.html",
        today_date=today_date,
        selected_date=selected_date,
        selected_class=selected_class,
        selected_status=selected_status,
        current_passcode=current_passcode,
        total_present_today=total_present_today,
        total_absent_today=total_absent_today,
        total_all=total_all,
        distinct_classes=distinct_classes,
        attendance_list=attendance_list
    )

@app.route("/admin/generate-passcode", methods=["POST"])
def generate_passcode():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    action = request.form.get("action", "random")
    today = datetime.date.today().strftime("%Y-%m-%d")

    if action == "custom":
        new_code = request.form.get("custom_code", "").strip()
        if not new_code:
            flash("Please enter a valid custom password.", "error")
            return redirect(url_for("admin_dashboard"))
    else:
        new_code = generate_random_passcode()

    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO daily_passcodes (passcode, for_date, is_active) VALUES (?, ?, 1)", (new_code, today))
    conn.commit()
    conn.close()
    
    flash(f"Today's password has been successfully updated to: {new_code}", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reset-attendance", methods=["POST"])
def reset_attendance():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    conn.execute("DELETE FROM attendance")
    conn.commit()
    conn.close()
    
    flash("All attendance records have been successfully reset to 0!", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete-attendance/<int:record_id>", methods=["POST"])
def delete_attendance(record_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    conn.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()

    flash("Selected attendance record has been deleted successfully.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/export-csv")
def export_csv():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    filter_date = request.args.get("date", datetime.date.today().strftime("%Y-%m-%d"))
    conn = get_db_connection()
    records = conn.execute("SELECT id, student_name, class_name, semester, status, reason, attendance_date, submission_time, created_at FROM attendance WHERE attendance_date = ? ORDER BY id ASC", (filter_date,)).fetchall()
    conn.close()

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["ID", "Student Name", "Class", "Semester", "Status", "Reason (If Absent)", "Date", "Submission Time", "System Recorded Time"])

    for row in records:
        writer.writerow([row['id'], row['student_name'], row['class_name'], row['semester'], row['status'] or 'Present', row['reason'] or '-', row['attendance_date'], row['submission_time'], row
