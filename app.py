import io
import csv
import string
import random
import socket
import datetime
import mysql.connector
from flask import (
    Flask, render_template, request, redirect, 
    url_for, flash, session, send_file, Response
)
from werkzeug.security import check_password_hash, generate_password_hash
import qrcode
import qrcode.image.svg

# ==========================================
# MYSQL DATABASE CONFIGURATION
# ==========================================
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "F123"          # Your MySQL password
DB_NAME = "rollcall_db"

def get_db_connection(use_database=True):
    config = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }
    if use_database:
        config["database"] = DB_NAME
    return mysql.connector.connect(**config)

def execute_query(sql, params=None, fetchone=False, fetchall=False, commit=False):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        
        if commit:
            connection.commit()
            last_id = cursor.lastrowid
            cursor.close()
            return last_id
        
        if fetchone:
            result = cursor.fetchone()
            cursor.close()
            return result
        
        if fetchall:
            result = cursor.fetchall()
            cursor.close()
            return result
            
        cursor.close()
        return None
    except Exception as e:
        if connection and commit:
            connection.rollback()
        raise e
    finally:
        if connection:
            connection.close()

def auto_upgrade_schema():
    """Ensure the attendance table has status, reason, and photo columns."""
    try:
        conn = get_db_connection(use_database=True)
        cursor = conn.cursor()
        
        # Check if status column exists
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'attendance' AND COLUMN_NAME = 'status';
        """, (DB_NAME,))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `attendance` ADD COLUMN `status` VARCHAR(20) DEFAULT 'Present' AFTER `semester`;")

        # Check if reason column exists
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'attendance' AND COLUMN_NAME = 'reason';
        """, (DB_NAME,))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `attendance` ADD COLUMN `reason` TEXT NULL AFTER `status`;")

        # Check if photo column exists
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'attendance' AND COLUMN_NAME = 'photo';
        """, (DB_NAME,))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `attendance` ADD COLUMN `photo` MEDIUMTEXT NULL AFTER `reason`;")

        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        pass

# ==========================================
# FLASK APPLICATION & ROLLCALL LOGIC
# ==========================================
app = Flask(__name__)
app.secret_key = "rollcall_super_secret_key_12345"

# Authorized Admins and Master Password
AUTHORIZED_ADMINS = {
    "yuszhairil": "Yuszhairil",
    "amira": "Amira",
    "fauzi": "Fauzi"
}
ADMIN_SECRET_PASSWORD = "DFS"

# Upgrade schema if needed
auto_upgrade_schema()

def get_local_wifi_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def generate_random_passcode(pattern_type="letter_digits"):
    if pattern_type == "letter_digits":
        letter = random.choice(string.ascii_lowercase)
        digits = "".join(random.choices(string.digits, k=3))
        return f"{letter}{digits}"
    else:
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choices(chars, k=4))

def get_or_create_today_passcode():
    today = datetime.date.today()
    query = "SELECT passcode FROM daily_passcodes WHERE for_date = %s AND is_active = TRUE ORDER BY id DESC LIMIT 1;"
    row = execute_query(query, (today,), fetchone=True)
    
    if row:
        return row['passcode']
    
    new_code = generate_random_passcode()
    insert_sql = """
        INSERT INTO daily_passcodes (passcode, for_date, is_active)
        VALUES (%s, %s, TRUE)
        ON DUPLICATE KEY UPDATE passcode = VALUES(passcode), is_active = TRUE;
    """
    execute_query(insert_sql, (new_code, today), commit=True)
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

    try:
        today = datetime.date.today()
        passcode_query = """
            SELECT passcode FROM daily_passcodes 
            WHERE for_date = %s AND is_active = TRUE 
            ORDER BY id DESC LIMIT 1;
        """
        active_code_row = execute_query(passcode_query, (today,), fetchone=True)
        
        if not active_code_row or user_passcode != active_code_row['passcode'].lower():
            flash("Invalid daily passcode! Please obtain the correct code from your instructor.", "error")
            return redirect(url_for("index"))

        dup_query = """
            SELECT id FROM attendance 
            WHERE LOWER(student_name) = LOWER(%s) AND LOWER(class_name) = LOWER(%s) AND attendance_date = %s
            LIMIT 1;
        """
        existing = execute_query(dup_query, (student_name, class_name, attendance_date), fetchone=True)
        if existing:
            flash(f"Student '{student_name}' ({class_name}) has already registered for today ({attendance_date}).", "warning")
            return redirect(url_for("index"))

        now_time = datetime.datetime.now().strftime("%H:%M:%S")
        insert_query = """
            INSERT INTO attendance (student_name, class_name, semester, status, reason, photo, attendance_date, submission_time)
            VALUES (%s, %s, %s, 'Present', NULL, %s, %s, %s);
        """
        execute_query(insert_query, (student_name, class_name, semester, photo if photo else None, attendance_date, now_time), commit=True)

        return render_template(
            "success.html",
            student_name=student_name,
            class_name=class_name,
            semester=semester,
            status="Present",
            reason=None,
            photo=photo,
            attendance_date=attendance_date,
            submission_time=now_time
        )

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")
        return redirect(url_for("index"))

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

    try:
        dup_query = """
            SELECT id FROM attendance 
            WHERE LOWER(student_name) = LOWER(%s) AND LOWER(class_name) = LOWER(%s) AND attendance_date = %s
            LIMIT 1;
        """
        existing = execute_query(dup_query, (student_name, class_name, attendance_date), fetchone=True)
        if existing:
            flash(f"Student '{student_name}' ({class_name}) already has a record submitted for today ({attendance_date}).", "warning")
            return redirect(url_for("absence"))

        now_time = datetime.datetime.now().strftime("%H:%M:%S")
        insert_query = """
            INSERT INTO attendance (student_name, class_name, semester, status, reason, photo, attendance_date, submission_time)
            VALUES (%s, %s, %s, 'Absent', %s, %s, %s, %s);
        """
        execute_query(insert_query, (student_name, class_name, semester, reason, photo if photo else None, attendance_date, now_time), commit=True)

        return render_template(
            "success.html",
            student_name=student_name,
            class_name=class_name,
            semester=semester,
            status="Absent",
            reason=reason,
            photo=photo,
            attendance_date=attendance_date,
            submission_time=now_time
        )

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")
        return redirect(url_for("absence"))

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

    try:
        current_passcode = get_or_create_today_passcode()

        present_row = execute_query(
            "SELECT COUNT(*) as count FROM attendance WHERE attendance_date = %s AND (status = 'Present' OR status IS NULL);", 
            (today_date,), 
            fetchone=True
        )
        total_present_today = present_row['count'] if present_row else 0

        absent_row = execute_query(
            "SELECT COUNT(*) as count FROM attendance WHERE attendance_date = %s AND status = 'Absent';", 
            (today_date,), 
            fetchone=True
        )
        total_absent_today = absent_row['count'] if absent_row else 0

        total_all_row = execute_query(
            "SELECT COUNT(*) as count FROM attendance;", 
            fetchone=True
        )
        total_all = total_all_row['count'] if total_all_row else 0

        distinct_classes = execute_query(
            "SELECT DISTINCT class_name FROM attendance ORDER BY class_name ASC;", 
            fetchall=True
        ) or []

        query = "SELECT * FROM attendance WHERE attendance_date = %s"
        params = [selected_date]

        if selected_class:
            query += " AND class_name = %s"
            params.append(selected_class)

        if selected_status:
            query += " AND status = %s"
            params.append(selected_status)

        query += " ORDER BY id DESC;"
        attendance_list = execute_query(query, tuple(params), fetchall=True) or []

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
    except Exception as e:
        flash(f"Error fetching dashboard data: {str(e)}", "error")
        return render_template(
            "admin_dashboard.html",
            today_date=today_date,
            selected_date=selected_date,
            selected_class="",
            selected_status="",
            current_passcode="----",
            total_present_today=0,
            total_absent_today=0,
            total_all=0,
            distinct_classes=[],
            attendance_list=[]
        )

@app.route("/admin/generate-passcode", methods=["POST"])
def generate_passcode():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    action = request.form.get("action", "random")
    today = datetime.date.today()

    if action == "custom":
        new_code = request.form.get("custom_code", "").strip()
        if not new_code:
            flash("Please enter a valid custom password.", "error")
            return redirect(url_for("admin_dashboard"))
    else:
        new_code = generate_random_passcode()

    try:
        sql = """
            INSERT INTO daily_passcodes (passcode, for_date, is_active)
            VALUES (%s, %s, TRUE)
            ON DUPLICATE KEY UPDATE passcode = VALUES(passcode), is_active = TRUE;
        """
        execute_query(sql, (new_code, today), commit=True)
        flash(f"Today's password has been successfully updated to: {new_code}", "success")
    except Exception as e:
        flash(f"Error updating passcode: {str(e)}", "error")

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reset-attendance", methods=["POST"])
def reset_attendance():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    try:
        execute_query("TRUNCATE TABLE attendance;", commit=True)
        flash("All attendance records have been successfully reset to 0!", "success")
    except Exception as e:
        flash(f"Error resetting records: {str(e)}", "error")

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete-attendance/<int:record_id>", methods=["POST"])
def delete_attendance(record_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    try:
        execute_query("DELETE FROM attendance WHERE id = %s;", (record_id,), commit=True)
        flash("Selected attendance record has been deleted successfully.", "info")
    except Exception as e:
        flash(f"Error deleting record: {str(e)}", "error")

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/export-csv")
def export_csv():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    filter_date = request.args.get("date", datetime.date.today().strftime("%Y-%m-%d"))
    
    try:
        records = execute_query(
            "SELECT id, student_name, class_name, semester, status, reason, attendance_date, submission_time, created_at FROM attendance WHERE attendance_date = %s ORDER BY id ASC;",
            (filter_date,),
            fetchall=True
        ) or []

        output = io.StringIO()
        output.write('\ufeff')
        
        writer = csv.writer(output)
        writer.writerow(["ID", "Student Name", "Class", "Semester", "Status", "Reason (If Absent)", "Date", "Submission Time", "System Recorded Time"])

        for row in records:
            writer.writerow([
                row['id'],
                row['student_name'],
                row['class_name'],
                row['semester'],
                row['status'] or 'Present',
                row['reason'] or '-',
                str(row['attendance_date']),
                str(row['submission_time']),
                str(row['created_at'])
            ])

        output.seek(0)
        filename = f"RollCall_Attendance_{filter_date}.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )
    except Exception as e:
        flash(f"Error generating CSV: {str(e)}", "error")
        return redirect(url_for("admin_dashboard"))

@app.route("/qr-code")
def generate_qr_image():
    local_ip = get_local_wifi_ip()
    attendance_url = f"http://{local_ip}:5000"
    
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(attendance_url, image_factory=factory)
    
    img_io = io.BytesIO()
    img.save(img_io)
    img_io.seek(0)

    return Response(img_io.getvalue(), mimetype='image/svg+xml')

if __name__ == "__main__":
    local_ip = get_local_wifi_ip()
    print("\n" + "="*60)
    print("🚀 ROLLCALL SYSTEM STARTED SUCCESSFULLY")
    print("="*60)
    print(f"📱 Access URL (Local Laptop) : http://127.0.0.1:5000")
    print(f"📡 Access URL (Same Wi-Fi)   : http://{local_ip}:5000")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)