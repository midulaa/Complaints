# app.py
"""
AI-Assisted Anonymous Complaint Platform (single-file)
Features:
- Login restricted to users with email ending in @sece.ac.in
- File a complaint; complaint is 'classified' by a lightweight AI (keyword+heuristics)
- Complaints stored in SQLite (file: complaints.db)
- Admin dashboard shows all complaints with statuses (New, In Progress, Resolved)
- Anonymous notifications to head of category can be sent via SMTP (optional; configure below)
- Real-time-like updates via client polling (every 5 seconds)
- Dashboard shows percentages and ranking of categories by resolution speed
- Single file. Requires: Flask (pip install Flask)
Optional: to actually send emails, install and configure an SMTP server (or use Gmail SMTP).
"""

from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, g
import sqlite3
import re
from datetime import datetime
import time
import math
import os
import smtplib
from email.message import EmailMessage

# ---------- CONFIG ----------
APP_SECRET_KEY = "change_this_secret"  # change before deploy
DATABASE = "complaints.db"
ALLOWED_DOMAIN = "@sece.ac.in"
ADMIN_PASSWORD = "admin123"  # simple admin auth; change it
# Mapping category -> head email (used to send anonymous notification)
CATEGORY_HEAD_EMAIL = {
    "Infrastructure": "infra-head@sece.ac.in",
    "Academic": "academic-head@sece.ac.in",
    "Harassment": "safety-head@sece.ac.in",
    "Facilities": "facilities-head@sece.ac.in",
    "IT/Network": "it-head@sece.ac.in",
    "Other": "principal@sece.ac.in",
}
# SMTP settings: if you want the app to send a notification email set ENABLE_SMTP True and fill SMTP_*.
ENABLE_SMTP = False
SMTP_HOST = "smtp.example.com"
SMTP_PORT = 587
SMTP_USER = "no-reply@example.com"
SMTP_PASSWORD = "smtp-password"
FROM_EMAIL = "no-reply@sece.ac.in"
# ---------- END CONFIG ----------

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

# ------------ Database helpers ------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        need_init = not os.path.exists(DATABASE)
        db = g._database = sqlite3.connect(DATABASE, check_same_thread=False)
        db.row_factory = sqlite3.Row
        if need_init:
            init_db(db)
    return db

def init_db(db):
    cur = db.cursor()
    cur.execute("""
    CREATE TABLE complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        title TEXT,
        body TEXT,
        status TEXT,
        timestamp INTEGER,
        updated_at INTEGER,
        resolved_at INTEGER,
        predicted_confidence REAL,
        anonymous_sent INTEGER DEFAULT 0
    )""")
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

# ------------ Simple AI classifier (heuristic + keywords) ------------
# This is a lightweight, explainable "AI" that recognizes complaint type.
# You can extend with ML later; for now it's deterministic and reliable for many cases.

CATEGORY_KEYWORDS = {
    "Infrastructure": ["roof", "leak", "electric", "power", "broken", "door", "window", "stairs", "lift", "elevator", "floor", "ceiling", "light", "water", "plumbing", "pipe", "road", "fence"],
    "Academic": ["exam", "grade", "marks", "professor", "teacher", "syllabus", "course", "timetable", "assignment", "lecture", "faculty", "attendance", "question paper", "cheating"],
    "Harassment": ["harass", "harassment", "bully", "bullying", "molest", "abuse", "threat", "unsafe", "discrimination", "sexual", "assault", "misconduct", "creepy"],
    "Facilities": ["canteen", "food", "washroom", "toilet", "clean", "hygiene", "lab", "equipment", "bench", "fan", "ac", "water cooler", "parking", "bus"],
    "IT/Network": ["network", "wifi", "internet", "server", "email", "login", "website", "portal", "database", "printer", "computer", "desktop", "laptop", "slow"],
}

ALL_CATEGORIES = list(CATEGORY_KEYWORDS.keys()) + ["Other"]

def classify_complaint(text):
    """
    Returns (category, confidence_score)
    confidence_score between 0 and 1 (higher means stronger match)
    """
    s = text.lower()
    scores = {cat: 0 for cat in ALL_CATEGORIES}
    words = re.findall(r"\w+", s)
    # count keyword hits
    for cat, keys in CATEGORY_KEYWORDS.items():
        for k in keys:
            if k in s:
                scores[cat] += 2
        # also word-level matches
        for w in words:
            for k in keys:
                if abs(len(w) - len(k)) <= 2 and k.startswith(w[:3]):
                    scores[cat] += 0.2
    # simple heuristics for harassment
    if any(token in s for token in ["harass", "bully", "sexual", "assault", "threat"]):
        scores["Harassment"] += 5

    # fallback: Other
    best_cat = "Other"
    best_score = 0
    for cat, sc in scores.items():
        if sc > best_score:
            best_score = sc
            best_cat = cat
    # confidence: normalize by heuristic maximum
    confidence = min(1.0, best_score / 6.0)
    return best_cat, round(confidence, 3)

# ------------ Utilities ------------
def now_ts():
    return int(time.time())

def send_anonymous_email(category, title, body):
    """
    Send an anonymous notification to the category head.
    The notification does NOT include any user-identifying information.
    This function is optional and uses SMTP; configure ENABLE_SMTP to True to send.
    Returns True if sent or if SMTP disabled (we'll treat as success), False if failed.
    """
    head = CATEGORY_HEAD_EMAIL.get(category, None)
    if not head:
        return False
    subject = f"[Anonymous Complaint] {category} - {title}"
    message = f"""An anonymous complaint has been filed and classified as *{category}*.

Title: {title}

Complaint:
{body}

Please check the admin portal for updates and resolution actions.
This message intentionally contains no sender-identifying data.
-- SECE Complaints System
"""
    if not ENABLE_SMTP:
        print("[SMTP DISABLED] Would send to:", head)
        print("Subject:", subject)
        print(message)
        return True
    try:
        msg = EmailMessage()
        msg["From"] = FROM_EMAIL
        msg["To"] = head
        msg["Subject"] = subject
        msg.set_content(message)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print("SMTP send failed:", e)
        return False

# ------------ Routes & API ------------
@app.route("/")
def index():
    user = session.get("user_email")
    return render_template_string(INDEX_HTML, user=user, allowed_domain=ALLOWED_DOMAIN)

@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip()
    # simple email pattern check
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return render_template_string(INDEX_HTML, error="Please enter a valid email.", user=None, allowed_domain=ALLOWED_DOMAIN)
    if not email.lower().endswith(ALLOWED_DOMAIN):
        return render_template_string(INDEX_HTML, error=f"Login restricted to {ALLOWED_DOMAIN} addresses only.", user=None, allowed_domain=ALLOWED_DOMAIN)
    session["user_email"] = email
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "user_email" not in session:
        return redirect(url_for("index"))
    return render_template_string(DASH_HTML, user=session["user_email"])

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    # simple admin login (password only)
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        else:
            return render_template_string(ADMIN_LOGIN_HTML, error="Incorrect password")
    return render_template_string(ADMIN_LOGIN_HTML, error=None)

@app.route("/admin/panel")
def admin_panel():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    return render_template_string(ADMIN_PANEL_HTML)

@app.route("/api/complaint/create", methods=["POST"])
def create_complaint():
    if "user_email" not in session:
        return jsonify({"ok": False, "error": "Not logged in"}), 403
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not body:
        return jsonify({"ok": False, "error": "Complaint cannot be empty"}), 400
    # classify
    category, confidence = classify_complaint(body + " " + title)
    ts = now_ts()
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    INSERT INTO complaints (category, title, body, status, timestamp, updated_at, predicted_confidence)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (category, title, body, "New", ts, ts, confidence))
    db.commit()
    complaint_id = cur.lastrowid
    # send anonymous notification to category head (optional)
    sent = send_anonymous_email(category, title or "No title", body)
    cur.execute("UPDATE complaints SET anonymous_sent = ? WHERE id = ?", (1 if sent else 0, complaint_id))
    db.commit()
    return jsonify({"ok": True, "id": complaint_id, "category": category, "confidence": confidence})

@app.route("/api/complaint/list")
def list_complaints():
    # admin-protected: only admin can list all
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "admin-only"}), 403
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM complaints ORDER BY timestamp DESC")
    rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({k: (r[k] if r[k] is not None else "") for k in r.keys()})
    return jsonify({"ok": True, "complaints": out})

@app.route("/api/complaint/update_status", methods=["POST"])
def update_status():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "admin-only"}), 403
    cid = int(request.form.get("id", 0))
    new_status = request.form.get("status", "")
    if new_status not in ("New", "In Progress", "Resolved"):
        return jsonify({"ok": False, "error": "invalid status"}), 400
    db = get_db()
    cur = db.cursor()
    ts = now_ts()
    if new_status == "Resolved":
        cur.execute("UPDATE complaints SET status=?, updated_at=?, resolved_at=? WHERE id=?", (new_status, ts, ts, cid))
    else:
        cur.execute("UPDATE complaints SET status=?, updated_at=? WHERE id=?", (new_status, ts, cid))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/dashboard/stats")
def dashboard_stats():
    # admin-only
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "admin-only"}), 403
    db = get_db()
    cur = db.cursor()
    # totals by status
    cur.execute("SELECT status, COUNT(*) as cnt FROM complaints GROUP BY status")
    status_rows = cur.fetchall()
    status_counts = {r["status"]: r["cnt"] for r in status_rows}
    # totals by category
    cur.execute("SELECT category, COUNT(*) as cnt FROM complaints GROUP BY category")
    cat_rows = cur.fetchall()
    cat_counts = {r["category"]: r["cnt"] for r in cat_rows}
    # resolution speed by category: average resolution time (resolved_at - timestamp)
    cur.execute("SELECT category, AVG(resolved_at - timestamp) as avg_seconds, COUNT(*) as total_resolved FROM complaints WHERE status='Resolved' GROUP BY category")
    speed_rows = cur.fetchall()
    speed_stats = []
    for r in speed_rows:
        avg_sec = r["avg_seconds"] or 0
        total = r["total_resolved"]
        speed_stats.append({"category": r["category"], "avg_seconds": int(avg_sec), "resolved_count": total})
    # compute ranking: categories with smallest avg_seconds rank higher
    speed_stats = sorted(speed_stats, key=lambda x: x["avg_seconds"] if x["avg_seconds"]>0 else 10**9)
    # transform seconds to human readable days/hours
    def pretty(sec):
        if not sec or sec <= 0:
            return "-"
        days = sec // 86400
        hours = (sec % 86400) // 3600
        mins = (sec % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    for s in speed_stats:
        s["avg_readable"] = pretty(s["avg_seconds"])
    return jsonify({
        "ok": True,
        "status_counts": status_counts,
        "category_counts": cat_counts,
        "speed_stats": speed_stats
    })
# List complaints for students (all complaints are shown; admin-only info hidden)
@app.route("/api/mycomplaints")
def my_complaints():
    if "user_email" not in session:
        return jsonify({"ok": False, "error": "Not logged in"}), 403
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM complaints ORDER BY timestamp DESC")
    rows = cur.fetchall()
    out = [{k: (r[k] if r[k] is not None else "") for k in r.keys()} for r in rows]
    return jsonify({"ok": True, "complaints": out})

# Dashboard stats for students
@app.route("/api/mydashboard")
def my_dashboard():
    if "user_email" not in session:
        return jsonify({"ok": False, "error": "Not logged in"}), 403
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT status, COUNT(*) as cnt FROM complaints GROUP BY status")
    status_rows = cur.fetchall()
    status_counts = {r["status"]: r["cnt"] for r in status_rows}
    cur.execute("SELECT category, COUNT(*) as cnt FROM complaints GROUP BY category")
    cat_rows = cur.fetchall()
    cat_counts = {r["category"]: r["cnt"] for r in cat_rows}
    return jsonify({
        "ok": True,
        "status_counts": status_counts,
        "category_counts": cat_counts
    })


# ------------- Templates -------------
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <title>SECE Complaints — Login</title>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Bootstrap CSS CDN -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: linear-gradient(135deg,#a1c4fd 0%, #c2e9fb 50%, #fbc2eb 100%); min-height:100vh; }
    .card { border-radius: 18px; box-shadow: 0 10px 30px rgba(16,24,40,0.12);}
    .logo { font-weight:700; letter-spacing:1px; }
    .small-note{font-size:0.9rem; color:#3b3b3b;}
  </style>
</head>
<body>
  <div class="container py-5">
    <div class="row justify-content-center">
      <div class="col-md-9 col-lg-7">
        <div class="card p-4">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
              <div class="logo">SECE Complaints Portal</div>
              <div class="small-note">Anonymous, AI-assisted triage • Secure access for {{allowed_domain}} users</div>
            </div>
            <div>
              <a href="/admin" class="btn btn-outline-dark btn-sm">Admin</a>
            </div>
          </div>
          <div class="row g-3">
            <div class="col-md-6">
              <h5 class="mb-2">Sign in</h5>
              {% if error %}
                <div class="alert alert-danger">{{error}}</div>
              {% endif %}
              <form method="post" action="/login">
                <div class="mb-2">
                  <label class="form-label">Institution Email</label>
                  <input name="email" autofocus required class="form-control" placeholder="you@sece.ac.in">
                </div>
                <div>
                  <button class="btn btn-primary">Sign in with SECE email</button>
                </div>
                <div class="mt-3 text-muted small-note">Only institutional emails ending with {{allowed_domain}} are allowed. We keep files anonymous when notifying heads.</div>
              </form>
            </div>
            <div class="col-md-6">
              <h6 class="text-muted">How it works</h6>
              <ol>
                <li>Sign in using your institutional email.</li>
                <li>File your complaint; our AI suggests a category.</li>
                <li>Admin reviews and updates status in real-time.</li>
                <li>Heads are notified anonymously; resolution stats and rankings visible.</li>
              </ol>
              <div class="mt-3">
                <a href="/dashboard" class="btn btn-success">Go to dashboard (if already signed in)</a>
              </div>
            </div>
          </div>
          <div class="mt-3 text-end text-muted small-note">Designed to be secure & respectful — user identities are not forwarded.</div>
        </div>
        <div class="text-center mt-3 text-muted small-note">For demo: admin password is <code>admin123</code> (change in code before deploy).</div>
      </div>
    </div>
  </div>
</body>
</html>
"""

DASH_HTML = """
<!doctype html>
<html lang="en">
<head>
  <title>SECE Complaints — File a complaint</title>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Bootstrap CSS + Chart.js -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    body { background: linear-gradient(160deg,#fff7ed 0%, #fef3c7 50%); min-height:100vh; }
    .card { border-radius: 14px; box-shadow:0 8px 20px rgba(16,24,40,0.08);}
    .brand { font-weight:800; letter-spacing:0.6px; color:#1f2937; }
    .chip { font-size:0.85rem; padding:0.25rem 0.6rem; border-radius:999px; background:rgba(0,0,0,0.05);}
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-lg navbar-light" style="background:transparent;">
    <div class="container">
      <a class="navbar-brand brand" href="/">SECE Complaints</a>
      <div>
        <span class="chip me-2">Signed in as {{user}}</span>
        <a href="/logout" class="btn btn-outline-secondary btn-sm">Sign out</a>
      </div>
    </div>
  </nav>

  <div class="container py-4">
    <div class="row g-4">
      <div class="col-lg-7">
        <div class="card p-3">
          <h5>File a Complaint</h5>
          <form id="complaintForm">
            <div class="mb-2">
              <label class="form-label">Title (optional)</label>
              <input name="title" class="form-control" placeholder="Short summary e.g., 'AC broken in Lab 3'">
            </div>
            <div class="mb-2">
              <label class="form-label">Describe your complaint</label>
              <textarea name="body" rows="6" required class="form-control" placeholder="Write your complaint in detail..."></textarea>
            </div>
            <button class="btn btn-primary">Submit Anonymously</button>
          </form>

          <div id="resultCard" class="mt-3" style="display:none;">
            <div class="alert alert-success">
              Complaint filed! Automatically classified as <strong id="predCat"></strong> with confidence <span id="predConf"></span>.
              Admin will review; department head notified anonymously.
            </div>
          </div>

          <div class="mt-3 text-muted small">Your email is used only to grant access — it will not be forwarded to the department heads.</div>
        </div>

        <div class="card p-3 mt-3">
          <h5>Live status board</h5>
          <div id="liveBoard">
            <div class="text-muted">Loading recent complaints...</div>
          </div>
        </div>

      </div>

      <div class="col-lg-5">
        <div class="card p-3">
          <h6>Quick Stats</h6>
          <div class="row g-2">
            <div class="col-12">
              <canvas id="statusChart" height="160"></canvas>
            </div>
            <div class="col-12 mt-2">
              <h6 class="mb-1">Category ranking (by avg resolution time)</h6>
              <div id="rankingList" class="list-group"></div>
            </div>
          </div>
        </div>

        <div class="card p-3 mt-3">
          <h6>Privacy & Process</h6>
          <ul>
            <li>Complaints are anonymous when sent to category heads.</li>
            <li>Admin can update status (New / In Progress / Resolved).</li>
            <li>Our AI suggests a category — admin may re-categorize if needed.</li>
          </ul>
        </div>
      </div>
    </div>
  </div>

<script>
document.addEventListener("DOMContentLoaded", function(){
  const form = document.getElementById("complaintForm");
  const resultCard = document.getElementById("resultCard");
  const predCat = document.getElementById("predCat");
  const predConf = document.getElementById("predConf");
  const liveBoard = document.getElementById("liveBoard");
  const statusChartCtx = document.getElementById("statusChart").getContext('2d');
  let statusChart = new Chart(statusChartCtx, {
    type: 'doughnut',
    data: { labels: ['New','In Progress','Resolved'], datasets: [{ data: [1,1,1], label: 'Status' }] },
    options: { responsive: true, maintainAspectRatio:false }
  });

  // Student-friendly dashboard fetch
  function refreshDashboard(){
    fetch('/api/mydashboard').then(r=>r.json()).then(data=>{
      if(!data.ok) return;
      const sc = data.status_counts || {};
      const newVal = sc['New'] || 0, inProgress = sc['In Progress'] || 0, resolved = sc['Resolved'] || 0;
      statusChart.data.datasets[0].data = [newVal, inProgress, resolved];
      statusChart.update();
      // ranking
      const ranking = data.speed_stats || [];
      const rankingDiv = document.getElementById("rankingList");
      rankingDiv.innerHTML = "";
      if(ranking.length === 0){
        rankingDiv.innerHTML = '<div class="text-muted small">No resolved complaints yet to compute ranking.</div>';
      } else {
        ranking.forEach((r, idx) => {
          const el = document.createElement("div");
          el.className = "list-group-item d-flex justify-content-between align-items-center";
          el.innerHTML = '<div><strong>' + (idx+1) + '. ' + r.category + '</strong><br><small class="text-muted">Avg: ' + r.avg_readable + " • Resolved: " + r.resolved_count + '</small></div>';
          rankingDiv.appendChild(el);
        });
      }
    }).catch(e=>console.error(e));
  }

  // Student-friendly live board fetch
  function refreshLiveBoard(){
    fetch('/api/mycomplaints').then(r=>r.json()).then(data=>{
      if(!data.ok) return;
      const arr = data.complaints || [];
      const html = [];
      if(arr.length===0){
        liveBoard.innerHTML = '<div class="text-muted">No complaints yet.</div>';
        return;
      }
      html.push('<div class="list-group">');
      arr.slice(0,20).forEach(c => {
        const ts = new Date(c.timestamp*1000).toLocaleString();
        const updated = new Date(c.updated_at*1000).toLocaleString();
        html.push(`<div class="list-group-item">
          <div class="d-flex justify-content-between">
            <div><strong>${c.category}</strong> <small class="text-muted">[${c.id}]</small></div>
            <div><span class="badge ${c.status==='Resolved'?'bg-success': c.status==='In Progress'?'bg-warning':'bg-secondary'}">${c.status}</span></div>
          </div>
          <div class="text-muted small">${c.title || '(no title)'} — ${c.body.slice(0,160)}${c.body.length>160?'...':''}</div>
          <div class="text-muted small mt-1">Filed: ${ts} • Updated: ${updated} • Confidence: ${c.predicted_confidence || 0}</div>
        </div>`);
      });
      html.push('</div>');
      liveBoard.innerHTML = html.join("");
    }).catch(e=>console.error(e));
  }

  form.addEventListener("submit", function(ev){
    ev.preventDefault();
    const fd = new FormData(form);
    fetch('/api/complaint/create', { method:'POST', body: fd }).then(r=>r.json()).then(data=>{
      if(!data.ok){
        alert("Error: " + (data.error || "unknown"));
        return;
      }
      predCat.textContent = data.category;
      predConf.textContent = data.confidence;
      resultCard.style.display = 'block';
      form.reset();
      refreshLiveBoard();
      refreshDashboard();
    }).catch(e=>{ console.error(e); alert("Network error"); });
  });

  // initial
  refreshLiveBoard();
  refreshDashboard();
  // poll every 5s
  setInterval(()=>{ refreshLiveBoard(); refreshDashboard(); }, 5000);
});
</script>
</body>
</html>
"""

ADMIN_LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <title>Admin Login — SECE</title>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>body{background:linear-gradient(135deg,#fdfbfb,#ebedee);} .card{margin-top:80px;border-radius:12px;padding:18px;}</style>
</head>
<body>
  <div class="container d-flex justify-content-center">
    <div class="col-md-5">
      <div class="card">
        <h5>Admin Access</h5>
        {% if error %}
          <div class="alert alert-danger">{{error}}</div>
        {% endif %}
        <form method="post">
          <div class="mb-2"><label>Password</label><input name="password" type="password" class="form-control" required></div>
          <button class="btn btn-primary">Sign in</button>
          <a href="/" class="btn btn-link">Back</a>
        </form>
      </div>
    </div>
  </div>
</body>
</html>
"""

ADMIN_PANEL_HTML = """
<!doctype html>
<html lang="en">
<head>
  <title>Admin Panel — SECE</title>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    body{background:linear-gradient(180deg,#eef2ff,#fff);}
    .card{border-radius:12px;}
  </style>
</head>
<body>
<nav class="navbar navbar-light bg-transparent">
  <div class="container">
    <a class="navbar-brand" href="/">SECE Complaints (Admin)</a>
    <div>
      <a href="/logout" class="btn btn-outline-secondary btn-sm">Logout</a>
    </div>
  </div>
</nav>

<div class="container py-3">
  <div class="row g-3">
    <div class="col-lg-8">
      <div class="card p-3">
        <h5>All Complaints</h5>
        <div id="complaintsList">Loading...</div>
      </div>
    </div>
    <div class="col-lg-4">
      <div class="card p-3">
        <h6>Stats</h6>
        <canvas id="statusChart" height="220"></canvas>
        <div class="mt-2" id="speedBox"></div>
      </div>
    </div>
  </div>
</div>

<script>
function fetchComplaints(){
  fetch('/api/complaint/list').then(r=>r.json()).then(data=>{
    if(!data.ok){ document.getElementById('complaintsList').innerHTML = 'Error'; return; }
    const arr = data.complaints;
    if(arr.length===0){ document.getElementById('complaintsList').innerHTML = '<div class="text-muted">No complaints yet.</div>'; return; }
    let html = '<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr><th>ID</th><th>Category</th><th>Title</th><th>Body</th><th>Status</th><th>Actions</th></tr></thead><tbody>';
    arr.forEach(c=>{
      const body = c.body.length>120? c.body.slice(0,120)+'...': c.body;
      html += `<tr>
        <td>${c.id}</td>
        <td>${c.category}<br><small class="text-muted">conf:${c.predicted_confidence}</small></td>
        <td>${c.title || '(no title)'}</td>
        <td title="${c.body.replace(/"/g, '&quot;')}">${body}</td>
        <td><span class="badge ${c.status==='Resolved'?'bg-success': c.status==='In Progress'?'bg-warning':'bg-secondary'}">${c.status}</span></td>
        <td>
          <button class="btn btn-sm btn-outline-primary" onclick="setStatus(${c.id}, 'In Progress')">In Progress</button>
          <button class="btn btn-sm btn-success" onclick="setStatus(${c.id}, 'Resolved')">Resolve</button>
        </td>
      </tr>`;
    });
    html += '</tbody></table></div>';
    document.getElementById('complaintsList').innerHTML = html;
  });
}

function setStatus(id, status){
  const fd = new FormData();
  fd.append('id', id);
  fd.append('status', status);
  fetch('/api/complaint/update_status', {method:'POST', body:fd}).then(r=>r.json()).then(data=>{
    if(data.ok){
      fetchComplaints(); fetchStats();
    } else {
      alert("Error updating status: " + (data.error || 'unknown'));
    }
  });
}

let statusChart;
function fetchStats(){
  fetch('/api/dashboard/stats').then(r=>r.json()).then(data=>{
    if(!data.ok) return;
    const sc = data.status_counts || {};
    const newVal = sc['New'] || 0, inProgress = sc['In Progress'] || 0, resolved = sc['Resolved'] || 0;
    if(!statusChart){
      const ctx = document.getElementById('statusChart').getContext('2d');
      statusChart = new Chart(ctx, {
        type:'doughnut',
        data:{ labels:['New','In Progress','Resolved'], datasets:[{ data:[newVal,inProgress,resolved] }]},
        options:{maintainAspectRatio:false}
      });
    } else {
      statusChart.data.datasets[0].data = [newVal,inProgress,resolved]; statusChart.update();
    }
    const speed = data.speed_stats || [];
    let html = '<h6 class="mt-2">Category resolution times</h6>';
    if(speed.length===0) html += '<div class="text-muted small">No resolved complaints yet</div>';
    else {
      html += '<ul class="list-group small mt-2">';
      speed.forEach(s=>{
        html += `<li class="list-group-item d-flex justify-content-between align-items-center">${s.category} <span class="badge bg-light text-dark">${s.avg_readable}</span></li>`;
      });
      html += '</ul>';
    }
    document.getElementById('speedBox').innerHTML = html;
  });
}

document.addEventListener("DOMContentLoaded", function(){
  fetchComplaints(); fetchStats();
  setInterval(()=>{ fetchComplaints(); fetchStats(); }, 5000);
});
</script>
</body>
</html>
"""

# ------------- Run -------------
if __name__ == "__main__":
    with app.app_context():   # <-- create application context
        db = get_db()         # safe now
        print("Starting app. DB:", DATABASE)
    app.run(debug=True, host="0.0.0.0", port=5000)

