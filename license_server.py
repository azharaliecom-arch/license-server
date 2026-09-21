"""
License Server — Render.com pe deploy hone wala, bohot HALKA server.
Kaam: Naye laptop-installations ko "manzoori" dena, aur pehle-se-manzoor-shuda
laptops ko confirm karna. Koi video-processing yahan bilkul nahi hoti.
"""
import json
import secrets
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

DATA_FILE = Path("license_data.json")

# ZAROORI: Ye password badal dein — sirf AAP ko pata hona chahiye, admin
# dashboard kholne ke liye istemal hoga.
ADMIN_PASSWORD = "change_this_password_123"


def _load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"requests": []}


def _save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@app.route("/api/check_activation", methods=["POST"])
def check_activation():
    """Customer ka tool ye endpoint istemal karta hai — 'kya mera fingerprint
    manzoor hai?' poochne ke liye. Pehli dafa: ek "pending" request bana
    deta hai. Agar pehle se manzoor ho chuka ho: "approved" wapas karta hai."""
    payload = request.get_json(force=True)
    fingerprint = payload.get("fingerprint", "").strip()
    machine_name = payload.get("machine_name", "Unknown Laptop")
    if not fingerprint:
        return jsonify({"status": "error", "message": "Fingerprint missing"}), 400

    data = _load_data()
    existing = next((r for r in data["requests"] if r["fingerprint"] == fingerprint), None)

    if existing:
        return jsonify({"status": existing["status"]})

    # Naya fingerprint — "pending" request bana dete hain.
    data["requests"].append({
        "fingerprint": fingerprint,
        "machine_name": machine_name,
        "status": "pending",
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_data(data)
    return jsonify({"status": "pending"})


@app.route("/admin")
def admin_dashboard():
    """Chhota, saada admin-dashboard — sab pending/approved requests dikhata
    hai, "Manzoor Karein" button ke sath. Password se mehfooz hai."""
    password = request.args.get("password", "")
    if password != ADMIN_PASSWORD:
        return "<h2>Ghalat password. URL mein ?password=... add karein.</h2>", 403

    data = _load_data()
    rows = ""
    for i, r in enumerate(data["requests"]):
        status_color = {"pending": "#e8a33d", "approved": "#2bd36d", "denied": "#e84f9b"}.get(r["status"], "#888")
        rows += f"""
        <tr>
          <td>{r['machine_name']}</td>
          <td style="font-family:monospace;font-size:11px">{r['fingerprint'][:20]}...</td>
          <td>{r['requested_at']}</td>
          <td style="color:{status_color};font-weight:bold">{r['status'].upper()}</td>
          <td>
            {"<a href='/admin/approve?fp=" + r['fingerprint'] + "&password=" + password + "'><button style='background:#2bd36d;color:white;border:none;padding:6px 12px;border-radius:6px;cursor:pointer'>Manzoor</button></a>" if r['status'] == 'pending' else ""}
            {"<a href='/admin/deny?fp=" + r['fingerprint'] + "&password=" + password + "'><button style='background:#e84f9b;color:white;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;margin-left:6px'>Rad</button></a>" if r['status'] == 'pending' else ""}
          </td>
        </tr>"""

    html = f"""
    <html><head><title>License Admin</title>
    <style>
      body {{ background:#070910; color:#f5f7fb; font-family:Arial; padding:24px; }}
      table {{ width:100%; border-collapse:collapse; margin-top:16px; }}
      th, td {{ padding:10px; border-bottom:1px solid #293145; text-align:left; }}
      th {{ color:#929bb0; font-size:12px; }}
    </style></head>
    <body>
      <h2>🔐 License Requests</h2>
      <table>
        <tr><th>Laptop Naam</th><th>Fingerprint</th><th>Waqt</th><th>Status</th><th>Kaam</th></tr>
        {rows if rows else "<tr><td colspan='5'>Koi request nahi hai abhi.</td></tr>"}
      </table>
    </body></html>
    """
    return html


@app.route("/admin/approve")
def admin_approve():
    if request.args.get("password", "") != ADMIN_PASSWORD:
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    data = _load_data()
    for r in data["requests"]:
        if r["fingerprint"] == fp:
            r["status"] = "approved"
            r["approved_at"] = datetime.now().isoformat(timespec="seconds")
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


@app.route("/")
def home():
    return "License server chal raha hai."


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5555))
    app.run(host="0.0.0.0", port=port)
