"""
License Server — Render.com pe deploy hone wala, halka server.
Kaam: (1) Machine-lock manzoori, (2) Elaan/Update-info, (3) Leaderboard +
Badges, (4) Online/Activity tracking, (5) Error-reports — sab EK jagah.
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

DATA_FILE = Path("/data/license_data.json")

# ZAROORI: Ye password badal dein — sirf AAP ko pata hona chahiye.
ADMIN_PASSWORD = "Azhar$$78621"

ONLINE_THRESHOLD_MINUTES = 1.5  # Itni der tak "heartbeat" na aaye to "offline" maanenge

BADGE_MILESTONES = [10, 25, 50, 100, 250, 500]


def _load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {
        "requests": [],
        "users": {},  # fingerprint -> {name, video_count, last_seen, activity, badges}
        "announcement": {"title": "", "body": "", "version": "1.0", "download_url": "", "changelog": []},
        "error_reports": [],
    }


def _save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_online(last_seen_str):
    try:
        last_seen = datetime.fromisoformat(last_seen_str)
        return (datetime.now() - last_seen) < timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    except Exception:
        return False


def _format_duration(from_str):
    """'5 minute', '2 ghante 15 minute' jaisi, Roman-Urdu mein, saaf
    duration banata hai — kisi bhi waqt se ABHI TAK ka farq."""
    try:
        from_dt = datetime.fromisoformat(from_str)
    except Exception:
        return "-"
    seconds = int((datetime.now() - from_dt).total_seconds())
    if seconds < 0:
        seconds = 0
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days >= 1:
        return f"{days} din"
    if hours >= 1:
        return f"{hours} ghante {minutes % 60} minute"
    if minutes >= 1:
        return f"{minutes} minute"
    return "abhi abhi"


# ---------------------------------------------------------------------------
# MACHINE-LOCK (manzoori)
# ---------------------------------------------------------------------------

@app.route("/api/check_activation", methods=["POST"])
def check_activation():
    payload = request.get_json(force=True)
    fingerprint = payload.get("fingerprint", "").strip()
    machine_name = payload.get("machine_name", "Unknown Laptop")
    if not fingerprint:
        return jsonify({"status": "error", "message": "Fingerprint missing"}), 400

    data = _load_data()

    # ZAROORI: Agar admin ne is banday ki "Access Deny" ki hui hai, tool
    # ko yahin, shuru mein hi, rok dete hain — chahe pehle "approved" ho.
    if data["users"].get(fingerprint, {}).get("blocked"):
        return jsonify({"status": "blocked"})

    existing = next((r for r in data["requests"] if r["fingerprint"] == fingerprint), None)

    if existing:
        return jsonify({"status": existing["status"]})

    data["requests"].append({
        "fingerprint": fingerprint,
        "machine_name": machine_name,
        "status": "pending",
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_data(data)
    return jsonify({"status": "pending"})


# ---------------------------------------------------------------------------
# HEARTBEAT + ACTIVITY (leaderboard, online-status ke liye)
# ---------------------------------------------------------------------------

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Har laptop, thori thori der mein ye call kare — 'main zinda hun,
    ye kar raha hun'. Naye badge milne pe, wapas bata deta hai."""
    payload = request.get_json(force=True)
    fingerprint = payload.get("fingerprint", "").strip()
    activity = payload.get("activity", "Dashboard")
    if not fingerprint:
        return jsonify({"error": "Fingerprint missing"}), 400

    data = _load_data()
    user = data["users"].setdefault(fingerprint, {
        "name": "Unnamed", "video_count": 0, "last_seen": "", "activity": "", "badges": [],
    })
    # ZAROORI: "kitni der se ONLINE hai" ka hisaab rakhne ke liye — agar
    # pehle OFFLINE tha (ya pehli dafa hai), "online_since" ko ABHI SET
    # karte hain. Agar pehle se hi ONLINE tha, ise NAHI badalte — taake
    # "lagatar kitni der se online hai" sahi rahe.
    was_online = _is_online(user.get("last_seen", ""))
    if not was_online:
        user["online_since"] = datetime.now().isoformat(timespec="seconds")
    user["last_seen"] = datetime.now().isoformat(timespec="seconds")
    user["activity"] = activity
    _save_data(data)
    return jsonify({"ok": True})


@app.route("/api/report_video", methods=["POST"])
def report_video():
    """Video mukammal hone ke baad, laptop ye call kare — ginti barhti hai,
    naya badge mile to bata dete hain."""
    payload = request.get_json(force=True)
    fingerprint = payload.get("fingerprint", "").strip()
    if not fingerprint:
        return jsonify({"error": "Fingerprint missing"}), 400

    data = _load_data()
    user = data["users"].setdefault(fingerprint, {
        "name": "Unnamed", "video_count": 0, "last_seen": "", "activity": "", "badges": [],
    })
    user["video_count"] += 1
    new_badge = None
    if user["video_count"] in BADGE_MILESTONES and user["video_count"] not in user["badges"]:
        user["badges"].append(user["video_count"])
        new_badge = user["video_count"]
    _save_data(data)
    return jsonify({"ok": True, "video_count": user["video_count"], "new_badge": new_badge})


@app.route("/api/leaderboard")
def leaderboard():
    """Sab logon ki ginti, sabse zyada wale pehle — SIRF naam+ginti,
    koi tafseeli date/waqt nahi (privacy)."""
    data = _load_data()
    board = [
        {"name": u.get("name", "Unnamed"), "video_count": u.get("video_count", 0)}
        for u in data["users"].values()
        if u.get("name") and u.get("name") != "Unnamed"
    ]
    board.sort(key=lambda x: -x["video_count"])
    return jsonify({"leaderboard": board[:20]})


# ---------------------------------------------------------------------------
# ELAAN / UPDATE-INFO
# ---------------------------------------------------------------------------

@app.route("/api/announcement")
def get_announcement():
    data = _load_data()
    return jsonify(data.get("announcement", {}))


# ---------------------------------------------------------------------------
# ERROR-REPORTS
# ---------------------------------------------------------------------------

@app.route("/api/report_error", methods=["POST"])
def report_error():
    payload = request.get_json(force=True)
    data = _load_data()
    data["error_reports"].insert(0, {
        "fingerprint": payload.get("fingerprint", ""),
        "machine_name": payload.get("machine_name", "Unknown"),
        "error_summary": payload.get("error_summary", "")[:300],
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    })
    data["error_reports"] = data["error_reports"][:100]  # sirf aakhri 100 rakhein
    _save_data(data)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# ADMIN DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_dashboard():
    password = request.args.get("password", "")
    if password != ADMIN_PASSWORD:
        return "<h2>Ghalat password. URL mein ?password=... add karein.</h2>", 403

    data = _load_data()

    req_rows = ""
    for r in data["requests"]:
        status_color = {"pending": "#e8a33d", "approved": "#2bd36d", "denied": "#e84f9b"}.get(r["status"], "#888")
        name_field = f"<input id='name_{r['fingerprint']}' placeholder='Banday ka naam' style='padding:5px;border-radius:5px;border:1px solid #333;width:120px'>" if r['status'] == 'pending' else ""
        approve_btn = f"""<button onclick="approveWithName('{r['fingerprint']}')" style='background:#2bd36d;color:white;border:none;padding:6px 12px;border-radius:6px;cursor:pointer'>Manzoor</button>
        <a href='/admin/deny?fp={r['fingerprint']}&password={password}'><button style='background:#e84f9b;color:white;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;margin-left:6px'>Rad</button></a>""" if r['status'] == 'pending' else ""
        req_rows += f"""
        <tr>
          <td>{r['machine_name']}</td>
          <td style="font-family:monospace;font-size:11px">{r['fingerprint'][:16]}...</td>
          <td>{r['requested_at']}</td>
          <td style="color:{status_color};font-weight:bold">{r['status'].upper()}</td>
          <td>{name_field} {approve_btn}</td>
        </tr>"""

    user_rows = ""
    for fp, u in sorted(data["users"].items(), key=lambda x: -x[1].get("video_count", 0)):
        online = _is_online(u.get("last_seen", ""))
        blocked = u.get("blocked", False)
        if blocked:
            status_txt = "🚫 Access Band"
        elif online:
            status_txt = f"🟢 Online ({_format_duration(u.get('online_since', u.get('last_seen', '')))} se)"
        else:
            status_txt = f"⚪ Offline ({_format_duration(u.get('last_seen', ''))} se)"
        access_btn = (
            f"<button onclick=\"toggleAccessConfirm('{fp}', true)\" style='background:#2bd36d;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:11px'>✅ Access De Dein</button>"
            if blocked else
            f"<button onclick=\"toggleAccessConfirm('{fp}', false)\" style='background:#e84f9b;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:11px'>🚫 Access Deny</button>"
        )
        user_rows += f"""
        <tr>
          <td>{u.get('name', 'Unnamed')} <span style="font-size:10px;color:#929bb0">({u.get('machine_name', 'Unknown')})</span></td>
          <td>{status_txt}</td>
          <td>{u.get('activity', '-') if online else '-'}</td>
          <td>{u.get('video_count', 0)}</td>
          <td>{len(u.get('badges', []))} 🏆</td>
          <td style="white-space:nowrap">
            <button onclick="renamePrompt('{fp}')" style='background:#293145;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:11px;margin-right:4px'>✏️ Naam Badlein</button>
            {access_btn}
          </td>
        </tr>"""

    def _label_for(e):
        # Fingerprint se, us banday ka NAAM dhoondte hain (agar maloom ho) —
        # taake sirf "DESKTOP-XXXX" na dikhe, balke "MANGU (DESKTOP-XXXX)"
        # jaisa, pehchan-ne-laayak naam dikhe.
        u = data["users"].get(e.get("fingerprint", ""))
        if u and u.get("name") and u["name"] != "Unnamed":
            return f"{u['name']} ({e['machine_name']})"
        return e["machine_name"]

    error_rows = "".join(f"""
        <tr><td>{_label_for(e)}</td><td>{e['error_summary']}</td><td>{e['reported_at']}</td></tr>
    """ for e in data["error_reports"][:20])

    ann = data.get("announcement", {})

    html = f"""
    <html><head><title>Admin Dashboard</title>
    <style>
      body {{ background:#070910; color:#f5f7fb; font-family:Arial; padding:24px; }}
      table {{ width:100%; border-collapse:collapse; margin-top:16px; margin-bottom:30px; }}
      th, td {{ padding:10px; border-bottom:1px solid #293145; text-align:left; font-size:13px; }}
      th {{ color:#929bb0; font-size:12px; }}
      h2 {{ margin-top:30px; }}
      textarea, input {{ background:#111622; border:1px solid #293145; color:#fff; padding:8px; border-radius:6px; width:100%; box-sizing:border-box; }}
      .save-btn {{ background:#795cff; color:white; border:none; padding:10px 18px; border-radius:8px; cursor:pointer; margin-top:10px; }}
    </style>
    </head>
    <body>
      <h1>🔐 Admin Dashboard</h1>

      <h2>📋 Naye Installation Requests</h2>
      <table>
        <tr><th>Laptop Naam</th><th>Fingerprint</th><th>Waqt</th><th>Status</th><th>Kaam</th></tr>
        {req_rows if req_rows else "<tr><td colspan='5'>Koi request nahi hai abhi.</td></tr>"}
      </table>

      <h2>👥 Team (Online/Activity/Leaderboard)</h2>
      <table>
        <tr><th>Naam</th><th>Status</th><th>Kya Kar Raha Hai</th><th>Kitni Videos</th><th>Badges</th><th></th></tr>
        {user_rows if user_rows else "<tr><td colspan='5'>Abhi koi user nahi.</td></tr>"}
      </table>

      <h2>📢 Elaan/Update Likhein</h2>
      <form onsubmit="saveAnnouncement(event)">
        <label>Title</label>
        <input id="annTitle" value="{ann.get('title','')}">
        <label style="margin-top:10px;display:block">Poora Message (HTML chal sakta hai)</label>
        <textarea id="annBody" rows="5">{ann.get('body','')}</textarea>
        <label style="margin-top:10px;display:block">Version Number</label>
        <input id="annVersion" value="{ann.get('version','1.0')}">
        <label style="margin-top:10px;display:block">Download URL (naye update ke liye)</label>
        <input id="annDownload" value="{ann.get('download_url','')}">
        <label style="margin-top:10px;display:block">📰 Chalti Hui Patti (Ticker) — Khaali Chhodein Agar Nahi Chahiye</label>
        <input id="annTicker" value="{ann.get('ticker_text','')}" placeholder="Jaise: Naya update jald aa raha hai! Sabhi videos ki quality behtar ho gayi hai!">
        <button type="submit" class="save-btn">Save aur Sab Ko Bhejein</button>
      </form>

      <h2>⚠️ Error Reports (Aakhri 20)</h2>
      <table>
        <tr><th>Laptop</th><th>Masla</th><th>Waqt</th></tr>
        {error_rows if error_rows else "<tr><td colspan='3'>Koi error report nahi.</td></tr>"}
      </table>

      <script>
        function approveWithName(fp) {{
          const name = document.getElementById('name_' + fp).value || 'Unnamed';
          window.location = '/admin/approve?fp=' + fp + '&password={password}&name=' + encodeURIComponent(name);
        }}
        function renamePrompt(fp) {{
          const newName = prompt('Naya naam likhein:');
          if (newName) {{
            window.location = '/admin/rename?fp=' + fp + '&password={password}&name=' + encodeURIComponent(newName);
          }}
        }}
        function toggleAccessConfirm(fp, currentlyBlocked) {{
          const msg = currentlyBlocked
            ? 'Kya aap is banday ko dobara access DENA chahte hain?'
            : 'Kya aap is banday ki access BAND karna chahte hain? Agli dafa tool chalane par, ruk jayega.';
          if (confirm(msg)) {{
            window.location = '/admin/toggle_access?fp=' + fp + '&password={password}';
          }}
        }}
        async function saveAnnouncement(e) {{
          e.preventDefault();
          await fetch('/admin/set_announcement?password={password}', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
              title: document.getElementById('annTitle').value,
              body: document.getElementById('annBody').value,
              version: document.getElementById('annVersion').value,
              download_url: document.getElementById('annDownload').value,
              ticker_text: document.getElementById('annTicker').value,
            }})
          }});
          alert('Save ho gaya!');
        }}
      </script>
    </body></html>
    """
    return html


@app.route("/admin/approve")
def admin_approve():
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    name = request.args.get("name", "Unnamed")
    data = _load_data()
    machine_name = "Unknown"
    for r in data["requests"]:
        if r["fingerprint"] == fp:
            r["status"] = "approved"
            r["approved_at"] = datetime.now().isoformat(timespec="seconds")
            machine_name = r.get("machine_name", "Unknown")
    data["users"].setdefault(fp, {"name": name, "machine_name": machine_name, "video_count": 0, "last_seen": "", "activity": "", "badges": []})
    data["users"][fp]["name"] = name
    data["users"][fp]["machine_name"] = machine_name
    _save_data(data)
    return f"<script>window.location='/admin?password={request.args.get('password','')}'</script>"


@app.route("/admin/deny")
def admin_deny():
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    data = _load_data()
    for r in data["requests"]:
        if r["fingerprint"] == fp:
            r["status"] = "denied"
    _save_data(data)
    return f"<script>window.location='/admin?password={request.args.get('password','')}'</script>"


@app.route("/admin/rename")
def admin_rename():
    """Kabhi bhi, kisi bhi manzoor-shuda laptop ka naam badal sakte hain —
    'Unnamed' ko sahi naam dene ke liye, ya galti theek karne ke liye."""
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    new_name = request.args.get("name", "Unnamed")
    data = _load_data()
    if fp in data["users"]:
        data["users"][fp]["name"] = new_name
        _save_data(data)
    return f"<script>window.location='/admin?password={request.args.get('password','')}'</script>"


@app.route("/admin/toggle_access")
def admin_toggle_access():
    """Kisi bhi banday ki access, ek click se, band ya wapas de sakte
    hain. Band karne par, us banday ka tool, AGLI DAFA CHALANE PAR,
    khud, "access band hai" dikha kar ruk jayega."""
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    data = _load_data()
    if fp in data["users"]:
        data["users"][fp]["blocked"] = not data["users"][fp].get("blocked", False)
        _save_data(data)
    return f"<script>window.location='/admin?password={request.args.get('password','')}'</script>"


@app.route("/admin/set_announcement", methods=["POST"])
def admin_set_announcement():
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return jsonify({"error": "Ghalat password"}), 403
    payload = request.get_json(force=True)
    data = _load_data()
    new_ann = {
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "version": payload.get("version", "1.0"),
        "download_url": payload.get("download_url", ""),
        "ticker_text": payload.get("ticker_text", ""),
    }
    data["announcement"] = new_ann
    # ZAROORI: Changelog mein bhi save kar dete hain — poori history rahe.
    data.setdefault("changelog", []).insert(0, {
        **new_ann,
        "released_at": datetime.now().isoformat(timespec="seconds"),
    })
    data["changelog"] = data["changelog"][:50]  # aakhri 50 rakhein
    _save_data(data)
    return jsonify({"ok": True})


@app.route("/api/changelog")
def get_changelog():
    """Poori update-history — 'Changelog' page ke liye."""
    data = _load_data()
    return jsonify({"changelog": data.get("changelog", [])})


@app.route("/")
def home():
    return "License server chal raha hai."


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5555))
    app.run(host="0.0.0.0", port=port)
