"""
License Server — Render.com pe deploy hone wala, halka server.
Kaam: (1) Machine-lock manzoori, (2) Elaan/Update-info, (3) Leaderboard +
Badges, (4) Online/Activity tracking, (5) Error-reports, (6) Feature-lock —
sab EK jagah.

18 Oct 2026: Naya, professional Admin Dashboard (login form, naam se
feature-ijazat, search, auto-refresh). Tool (client) wale saare /api/...
routes aur data ki shakal BILKUL WAISI hi hai — tool mein kuch nahi badalna.
Data ab mehfooz tareeqe se save hota hai (lock + atomic write + backups).
"""
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

DATA_FILE = Path("/data/license_data.json")
BACKUP_DIR = DATA_FILE.parent / "backups"

# ZAROORI: Behtar ye hai ke password Render ki "Environment" mein
# ADMIN_PASSWORD naam se rakhein (code mein nahi). Wahan na ho to ye wala chalega.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or "Azhar$$78621"

ONLINE_THRESHOLD_MINUTES = 1.5  # Itni der tak "heartbeat" na aaye to "offline" maanenge

BADGE_MILESTONES = [10, 25, 50, 100, 250, 500]

# Tool ke features jin par lock lag sakta hai (naam tool ke code se milte hain)
KNOWN_FEATURES = [
    ("wildlife", "🦁 Wildlife Documentary", "Hook → Kahani → Jorna, AI Shots"),
    ("stock_downloader", "🐾 Stock Downloader", "Pexels / Pixabay se clips download"),
    ("board_explainer", "🧑‍🏫 Board / Chalk Explainer", "Script + voice se chalkboard video"),
    ("avatar_explainer", "🧑‍💼 Avatar Explainer", "Avatar video par hook slides"),
    ("text_to_video", "📝 AI Video Maker — Script to Video", "Single, Fast aur Multi-Browser"),
    ("voice_to_video", "🎙 AI Video Maker — Voice to Video", "Single aur Multi-Browser"),
    ("scenes", "🎬 AI Video Maker — Prompt to Video", "Single aur Multi-Browser"),
    ("stock", "📦 AI Video Maker — Stock Footage", ""),
    ("stock_ai_mix", "🔀 AI Video Maker — Stock + AI Mix", ""),
]

_DATA_LOCK = threading.RLock()
_LAST_BACKUP = {"hour": None}


def _empty_data():
    return {
        "requests": [],
        "users": {},  # fingerprint -> {name, video_count, last_seen, activity, badges}
        "announcement": {"title": "", "body": "", "version": "1.0", "download_url": "", "changelog": []},
        "error_reports": [],
    }


def _load_data():
    with _DATA_LOCK:
        if DATA_FILE.exists():
            try:
                data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = _load_latest_backup()
                if data is None:
                    raise
        else:
            data = _empty_data()
        for k, v in _empty_data().items():
            data.setdefault(k, v)
        return data


def _load_latest_backup():
    try:
        files = sorted(BACKUP_DIR.glob("license_data_*.json"))
        for f in reversed(files):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
    except Exception:
        pass
    return None


def _save_data(data):
    """Mehfooz save: pehle temp file, phir ek jhatke mein asal file ki jagah
    (beech mein server ruk jaye to bhi aadhi/kharab file nahi banti).
    Har ghante ek backup bhi (aakhri 72 rakhte hain)."""
    with _DATA_LOCK:
        text = json.dumps(data, indent=2, ensure_ascii=False)
        tmp = DATA_FILE.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        try:
            hour = datetime.now().strftime("%Y%m%d_%H")
            if _LAST_BACKUP["hour"] != hour:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                (BACKUP_DIR / f"license_data_{hour}.json").write_text(text, encoding="utf-8")
                _LAST_BACKUP["hour"] = hour
                for old in sorted(BACKUP_DIR.glob("license_data_*.json"))[:-72]:
                    old.unlink(missing_ok=True)
        except Exception:
            pass


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

    with _DATA_LOCK:
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

    with _DATA_LOCK:
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

    with _DATA_LOCK:
        data = _load_data()
        user = data["users"].setdefault(fingerprint, {
            "name": "Unnamed", "video_count": 0, "last_seen": "", "activity": "", "badges": [],
        })
        user["video_count"] = user.get("video_count", 0) + 1
        user.setdefault("badges", [])
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


@app.route("/api/changelog")
def get_changelog():
    """Poori update-history — 'Changelog' page ke liye."""
    data = _load_data()
    return jsonify({"changelog": data.get("changelog", [])})


# ---------------------------------------------------------------------------
# ERROR-REPORTS
# ---------------------------------------------------------------------------

@app.route("/api/report_error", methods=["POST"])
def report_error():
    payload = request.get_json(force=True)
    with _DATA_LOCK:
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
# FEATURE-LOCK — tool isi se poochta hai (shakal waisi hi)
# data["feature_locks"] = {"<feature>": {"locked": bool, "allowed_fingerprints": [fp, ...]}}
# Feature list mein na ho / locked=false ho to hamesha khula (fail-open).
# ---------------------------------------------------------------------------

@app.route("/api/check_feature_lock", methods=["POST"])
def check_feature_lock():
    payload = request.get_json(force=True)
    fingerprint = payload.get("fingerprint", "").strip()
    feature = payload.get("feature", "").strip()
    if not feature:
        return jsonify({"locked": False})

    data = _load_data()
    lock_info = data.get("feature_locks", {}).get(feature)
    if not lock_info or not lock_info.get("locked"):
        return jsonify({"locked": False})

    allowed = lock_info.get("allowed_fingerprints", [])
    if fingerprint and fingerprint in allowed:
        return jsonify({"locked": False})

    return jsonify({"locked": True})


@app.route("/")
def home():
    return "License server chal raha hai."


# ===========================================================================
# ADMIN — login
# ===========================================================================
_COOKIE = "adm_session"


def _session_token():
    return hashlib.sha256(("azhar-admin-v1|" + ADMIN_PASSWORD).encode("utf-8")).hexdigest()


def _authed():
    pw = request.args.get("password") or ""
    if pw and hmac.compare_digest(pw, ADMIN_PASSWORD):
        return True
    c = request.cookies.get(_COOKIE, "")
    return bool(c) and hmac.compare_digest(c, _session_token())


def _set_login_cookie(resp):
    secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"
    resp.set_cookie(_COOKIE, _session_token(), max_age=30 * 24 * 3600, httponly=True,
                    samesite="Lax", secure=secure)
    return resp


def _back():
    """Purane links (?password=...) ke baad saaf /admin par wapas."""
    return _set_login_cookie(redirect("/admin"))


def _api_guard():
    if not _authed():
        return jsonify({"error": "Login zaroori hai"}), 401
    if request.method == "POST" and request.headers.get("X-Admin") != "1":
        return jsonify({"error": "Ghalat request"}), 400
    return None


@app.route("/admin", methods=["GET", "POST"])
def admin_dashboard():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if hmac.compare_digest(pw, ADMIN_PASSWORD):
            return _set_login_cookie(redirect("/admin"))
        return Response(LOGIN_HTML.replace("{{ERR}}", "❌ Ghalat password"), mimetype="text/html")
    if not _authed():
        return Response(LOGIN_HTML.replace("{{ERR}}", ""), mimetype="text/html")
    if request.args.get("password"):
        return _back()                        # password URL se hata do
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/admin/logout")
def admin_logout():
    resp = redirect("/admin")
    resp.delete_cookie(_COOKIE)
    return resp


# ===========================================================================
# ADMIN — JSON API (naya dashboard isi se chalta hai)
# ===========================================================================

def _user_label(data, fp, fallback=None):
    u = data["users"].get(fp)
    if u:
        name = u.get("name") or "Unnamed"
        return name if name != "Unnamed" else (u.get("machine_name") or fallback or fp[:12] + "…")
    req = next((r for r in data["requests"] if r.get("fingerprint") == fp), None)
    if req:
        return req.get("machine_name") or fp[:12] + "…"
    return fallback or fp[:12] + "…"


@app.route("/admin/api/state")
def admin_api_state():
    g = _api_guard()
    if g:
        return g
    data = _load_data()
    now = datetime.now()

    users = []
    for fp, u in data["users"].items():
        online = _is_online(u.get("last_seen", ""))
        users.append({
            "fp": fp, "fp_short": fp[:12], "name": u.get("name") or "Unnamed",
            "machine": u.get("machine_name") or "", "online": online, "blocked": bool(u.get("blocked")),
            "online_for": _format_duration(u.get("online_since", u.get("last_seen", ""))) if online else "",
            "offline_for": _format_duration(u.get("last_seen", "")) if not online and u.get("last_seen") else "",
            "activity": u.get("activity", "") if online else "",
            "videos": u.get("video_count", 0), "badges": len(u.get("badges", [])),
        })
    users.sort(key=lambda x: (not x["online"], x["blocked"], -x["videos"], x["name"].lower()))

    requests_ = sorted(data["requests"], key=lambda r: (r.get("status") != "pending", r.get("requested_at", "")),
                       reverse=False)
    requests_ = [r for r in requests_ if r.get("status") == "pending"] + \
                sorted([r for r in data["requests"] if r.get("status") != "pending"],
                       key=lambda r: r.get("requested_at", ""), reverse=True)[:30]

    locks = data.get("feature_locks", {})
    features = []
    seen = set()
    for key, label, desc in KNOWN_FEATURES:
        seen.add(key)
        e = locks.get(key, {})
        features.append({"key": key, "label": label, "desc": desc, "locked": bool(e.get("locked")),
                         "allowed": list(e.get("allowed_fingerprints", []))})
    for key, e in sorted(locks.items()):
        if key in seen:
            continue
        features.append({"key": key, "label": key, "desc": "Custom feature", "locked": bool(e.get("locked")),
                         "allowed": list(e.get("allowed_fingerprints", []))})
    labels = {fp: _user_label(data, fp) for f in features for fp in f["allowed"]}

    errors = []
    day_ago = now - timedelta(days=1)
    errors_24h = 0
    for e in data["error_reports"][:50]:
        try:
            if datetime.fromisoformat(e.get("reported_at", "")) >= day_ago:
                errors_24h += 1
        except Exception:
            pass
        errors.append({"who": _user_label(data, e.get("fingerprint", ""), e.get("machine_name")),
                       "machine": e.get("machine_name", ""), "error": e.get("error_summary", ""),
                       "at": e.get("reported_at", "")})

    return jsonify({
        "stats": {"users": len(users), "online": sum(1 for u in users if u["online"]),
                  "pending": sum(1 for r in data["requests"] if r.get("status") == "pending"),
                  "videos": sum(u["videos"] for u in users), "blocked": sum(1 for u in users if u["blocked"]),
                  "errors_24h": errors_24h},
        "users": users,
        "requests": [{"fp": r["fingerprint"], "fp_short": r["fingerprint"][:12], "machine": r.get("machine_name", ""),
                      "status": r.get("status", ""), "at": r.get("requested_at", "")} for r in requests_],
        "features": features, "labels": labels,
        "announcement": data.get("announcement", {}),
        "changelog": [{"title": c.get("title", ""), "version": c.get("version", ""), "at": c.get("released_at", "")}
                      for c in data.get("changelog", [])[:15]],
        "errors": errors[:30],
        "server_time": now.isoformat(timespec="seconds"),
    })


@app.route("/admin/api/request", methods=["POST"])
def admin_api_request():
    g = _api_guard()
    if g:
        return g
    p = request.get_json(force=True) or {}
    fp, action = (p.get("fp") or "").strip(), p.get("action")
    name = (p.get("name") or "").strip()[:60] or "Unnamed"
    with _DATA_LOCK:
        data = _load_data()
        req = next((r for r in data["requests"] if r["fingerprint"] == fp), None)
        if not req:
            return jsonify({"error": "Request nahi mili"}), 404
        if action == "approve":
            req["status"] = "approved"
            req["approved_at"] = datetime.now().isoformat(timespec="seconds")
            machine_name = req.get("machine_name", "Unknown")
            data["users"].setdefault(fp, {"name": name, "machine_name": machine_name, "video_count": 0,
                                          "last_seen": "", "activity": "", "badges": []})
            data["users"][fp]["name"] = name
            data["users"][fp]["machine_name"] = machine_name
        elif action == "deny":
            req["status"] = "denied"
        else:
            return jsonify({"error": "?"}), 400
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/admin/api/user", methods=["POST"])
def admin_api_user():
    g = _api_guard()
    if g:
        return g
    p = request.get_json(force=True) or {}
    fp, action = (p.get("fp") or "").strip(), p.get("action")
    with _DATA_LOCK:
        data = _load_data()
        u = data["users"].get(fp)
        if u is None:
            return jsonify({"error": "User nahi mila"}), 404
        if action == "rename":
            new = (p.get("name") or "").strip()[:60]
            if not new:
                return jsonify({"error": "Naam khaali hai"}), 400
            u["name"] = new
        elif action == "block":
            u["blocked"] = True
        elif action == "unblock":
            u["blocked"] = False
        else:
            return jsonify({"error": "?"}), 400
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/admin/api/feature", methods=["POST"])
def admin_api_feature():
    """Ek feature ki poori halat ek sath: band/khula + jin ko ijazat hai."""
    g = _api_guard()
    if g:
        return g
    p = request.get_json(force=True) or {}
    feature = (p.get("feature") or "").strip()[:60]
    if not feature:
        return jsonify({"error": "Feature ka naam zaroori hai"}), 400
    allowed = []
    for fp in p.get("allowed") or []:
        fp = str(fp).strip()
        if fp and fp not in allowed:
            allowed.append(fp)
    with _DATA_LOCK:
        data = _load_data()
        locks = data.setdefault("feature_locks", {})
        locks[feature] = {"locked": bool(p.get("locked")), "allowed_fingerprints": allowed}
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/admin/api/feature_delete", methods=["POST"])
def admin_api_feature_delete():
    g = _api_guard()
    if g:
        return g
    feature = ((request.get_json(force=True) or {}).get("feature") or "").strip()
    with _DATA_LOCK:
        data = _load_data()
        if feature in data.get("feature_locks", {}) and feature not in [k for k, _, _ in KNOWN_FEATURES]:
            del data["feature_locks"][feature]
            _save_data(data)
    return jsonify({"ok": True})


@app.route("/admin/api/announcement", methods=["POST"])
def admin_api_announcement():
    g = _api_guard()
    if g:
        return g
    return _save_announcement(request.get_json(force=True) or {})


def _save_announcement(payload):
    new_ann = {
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "version": payload.get("version", "1.0"),
        "download_url": payload.get("download_url", ""),
        "ticker_text": payload.get("ticker_text", ""),
    }
    with _DATA_LOCK:
        data = _load_data()
        data["announcement"] = new_ann
        # ZAROORI: Changelog mein bhi save kar dete hain — poori history rahe.
        data.setdefault("changelog", []).insert(0, {
            **new_ann,
            "released_at": datetime.now().isoformat(timespec="seconds"),
        })
        data["changelog"] = data["changelog"][:50]  # aakhri 50 rakhein
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/admin/api/backup")
def admin_api_backup():
    """Poora data ek file mein download (ehtiyaat ke liye)."""
    if not _authed():
        return "Login zaroori hai", 401
    data = _load_data()
    resp = Response(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json")
    resp.headers["Content-Disposition"] = f"attachment; filename=license_backup_{datetime.now():%Y%m%d_%H%M}.json"
    return resp


# ===========================================================================
# ADMIN — purane links (pehle wala dashboard) — ab bhi kaam karte hain
# ===========================================================================

@app.route("/admin/approve")
def admin_approve():
    if not _authed():
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    name = request.args.get("name", "Unnamed")
    with _DATA_LOCK:
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
    return _back()


@app.route("/admin/deny")
def admin_deny():
    if not _authed():
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    with _DATA_LOCK:
        data = _load_data()
        for r in data["requests"]:
            if r["fingerprint"] == fp:
                r["status"] = "denied"
        _save_data(data)
    return _back()


@app.route("/admin/rename")
def admin_rename():
    if not _authed():
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    new_name = request.args.get("name", "Unnamed")
    with _DATA_LOCK:
        data = _load_data()
        if fp in data["users"]:
            data["users"][fp]["name"] = new_name
            _save_data(data)
    return _back()


@app.route("/admin/toggle_access")
def admin_toggle_access():
    if not _authed():
        return "Ghalat password", 403
    fp = request.args.get("fp", "")
    with _DATA_LOCK:
        data = _load_data()
        if fp in data["users"]:
            data["users"][fp]["blocked"] = not data["users"][fp].get("blocked", False)
            _save_data(data)
    return _back()


@app.route("/admin/set_announcement", methods=["POST"])
def admin_set_announcement():
    if not _authed():
        return jsonify({"error": "Ghalat password"}), 403
    return _save_announcement(request.get_json(force=True) or {})


@app.route("/admin/feature_locks_status")
def admin_feature_locks_status():
    if not _authed():
        return jsonify({"error": "Ghalat password"}), 403
    data = _load_data()
    return jsonify({"feature_locks": data.get("feature_locks", {})})


@app.route("/admin/toggle_feature_lock")
def admin_toggle_feature_lock():
    if not _authed():
        return "Ghalat password", 403
    feature = request.args.get("feature", "").strip()
    if not feature:
        return "Feature ka naam zaroori hai (?feature=...)", 400
    with _DATA_LOCK:
        data = _load_data()
        locks = data.setdefault("feature_locks", {})
        entry = locks.setdefault(feature, {"locked": False, "allowed_fingerprints": []})
        entry["locked"] = not entry.get("locked", False)
        _save_data(data)
    return _back()


@app.route("/admin/add_feature_exception")
def admin_add_feature_exception():
    if not _authed():
        return "Ghalat password", 403
    feature = request.args.get("feature", "").strip()
    fp = request.args.get("fp", "").strip()
    if not feature or not fp:
        return "feature aur fp dono zaroori hain", 400
    with _DATA_LOCK:
        data = _load_data()
        locks = data.setdefault("feature_locks", {})
        entry = locks.setdefault(feature, {"locked": False, "allowed_fingerprints": []})
        allowed = entry.setdefault("allowed_fingerprints", [])
        if fp not in allowed:
            allowed.append(fp)
        _save_data(data)
    return _back()


@app.route("/admin/remove_feature_exception")
def admin_remove_feature_exception():
    if not _authed():
        return "Ghalat password", 403
    feature = request.args.get("feature", "").strip()
    fp = request.args.get("fp", "").strip()
    with _DATA_LOCK:
        data = _load_data()
        locks = data.setdefault("feature_locks", {})
        entry = locks.get(feature)
        if entry and fp in entry.get("allowed_fingerprints", []):
            entry["allowed_fingerprints"].remove(fp)
            _save_data(data)
    return _back()


# ===========================================================================
# HTML
# ===========================================================================
_CSS = r"""
:root{--bg:#070910;--panel:#0f1420;--panel2:#141a29;--line:#252d42;--text:#eef1f7;--muted:#8f98ac;
--a:#795cff;--pink:#e84f9b;--green:#2bd36d;--yellow:#f0b84a;--red:#ef5968;--blue:#4aa3ff}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(circle at 15% -10%,#1d1638 0,#070910 45%);color:var(--text);
font:14px Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
button,input,textarea,select{font:inherit}button{cursor:pointer}
a{color:#a996ff}
"""

LOGIN_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Login</title>
<style>""" + _CSS + r"""
.box{max-width:380px;margin:12vh auto 0;padding:28px;background:linear-gradient(180deg,#141a29,#0f1420);
border:1px solid var(--line);border-radius:18px;box-shadow:0 30px 80px #0008}
.logo{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;font-weight:900;font-size:18px;
background:linear-gradient(135deg,var(--a),var(--pink));margin-bottom:14px}
h1{margin:0 0 4px;font-size:20px}p{color:var(--muted);margin:0 0 18px;font-size:13px}
input{width:100%;padding:12px 14px;border-radius:11px;border:1px solid var(--line);background:#0a0e18;color:var(--text)}
button{width:100%;margin-top:12px;padding:12px;border:0;border-radius:11px;font-weight:800;color:#fff;
background:linear-gradient(135deg,var(--a),var(--pink))}.err{color:var(--red);font-size:13px;margin-top:10px;min-height:18px}
</style></head><body><form class="box" method="post" action="/admin">
<div class="logo">AZ</div><h1>Admin Dashboard</h1><p>AI Azhar Automation — License Server</p>
<input type="password" name="password" placeholder="Admin password" autofocus required>
<button type="submit">🔐 Login</button><div class="err">{{ERR}}</div></form></body></html>"""

DASHBOARD_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Dashboard</title>
<style>""" + _CSS + r"""
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:12px 22px;
background:rgba(7,9,16,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);flex-wrap:wrap}
.logo{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-weight:900;
background:linear-gradient(135deg,var(--a),var(--pink))}
.brand b{display:block;font-size:15px}.brand small{color:var(--muted);font-size:11px}
.sp{flex:1}.muted{color:var(--muted)}.small{font-size:12px}
.btn{border:1px solid var(--line);background:#121827;color:#dfe4ee;border-radius:10px;padding:8px 12px;font-size:13px;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn:hover{border-color:#3a4566}.btn.p{border:0;background:linear-gradient(135deg,var(--a),var(--pink));color:#fff;font-weight:700}
.btn.g{background:#12321f;border-color:#1f5a37;color:#8ff0b5}.btn.r{background:#3a141b;border-color:#5e2230;color:#ffb0bb}
.btn.s{padding:5px 9px;font-size:12px;border-radius:8px}
.wrap{max-width:1250px;margin:0 auto;padding:20px 16px 60px}
.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:18px}
@media(max-width:1000px){.stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:560px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
.stat{background:linear-gradient(180deg,#131a2a,#0f1420);border:1px solid var(--line);border-radius:15px;padding:14px}
.stat .l{color:var(--muted);font-size:11px;letter-spacing:.05em;text-transform:uppercase}
.stat .v{font-size:26px;font-weight:800;margin-top:4px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tab{border:1px solid var(--line);background:#0f1420;color:#c9d0de;border-radius:11px;padding:9px 14px;font-weight:600}
.tab.on{background:linear-gradient(135deg,#2a2150,#2a1530);border-color:#5b4aa8;color:#fff}
.tab .n{display:inline-block;min-width:18px;padding:0 6px;margin-left:6px;border-radius:20px;background:var(--pink);color:#fff;font-size:11px}
.card{background:linear-gradient(180deg,#121827,#0e131f);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}
.card h3{margin:0 0 4px;font-size:15px}
table{width:100%;border-collapse:collapse}th,td{padding:10px 8px;border-bottom:1px solid #1d2436;text-align:left;vertical-align:middle;font-size:13px}
th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
tr:hover td{background:#ffffff05}
.tbl{overflow-x:auto}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:20px;font-size:12px;white-space:nowrap}
.on-p{background:#123222;color:#8ff0b5}.off-p{background:#1a2030;color:#a4adbf}.bl-p{background:#3a141b;color:#ffb0bb}
.pend{background:#3a2c12;color:#ffd48a}.appr{background:#123222;color:#8ff0b5}.den{background:#3a141b;color:#ffb0bb}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor}
.fp{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted)}
input[type=text],input[type=search],textarea{width:100%;padding:9px 11px;border-radius:10px;border:1px solid var(--line);background:#0a0e18;color:var(--text)}
label.f{display:block;font-size:11px;color:var(--muted);margin:12px 0 5px;letter-spacing:.04em;text-transform:uppercase}
.feat{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
@media(max-width:900px){.feat{grid-template-columns:1fr}}
.fc{border:1px solid var(--line);border-radius:16px;padding:14px;background:#0e131f}
.fc.lk{border-color:#5e2230;box-shadow:inset 0 0 0 1px #5e223055}
.fh{display:flex;align-items:flex-start;gap:10px}.fh .t{flex:1}.fh b{font-size:14px}.fh .k{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted)}
.sw{position:relative;width:46px;height:26px;border-radius:20px;background:#2a3246;border:0;flex:none;transition:.2s}
.sw:after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:.2s}
.sw.on{background:var(--red)}.sw.on:after{left:23px}
.fs{margin:8px 0 10px;font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;max-height:160px;overflow-y:auto;padding:2px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#121827;border-radius:20px;padding:5px 10px;font-size:12px;cursor:pointer;user-select:none}
.chip input{accent-color:var(--green);margin:0}.chip.sel{border-color:#1f5a37;background:#12321f;color:#bff5d2}
.chip.gone{border-style:dashed;color:var(--muted)}
.fa{display:flex;gap:8px;align-items:center;margin-top:10px}.dirty{color:var(--yellow);font-size:12px}
.toast{position:fixed;right:18px;bottom:18px;background:#121827;border:1px solid var(--line);border-radius:12px;padding:12px 16px;box-shadow:0 20px 50px #0009;display:none;z-index:50}
.empty{color:var(--muted);text-align:center;padding:18px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.hide{display:none}
</style></head><body>
<div class="top">
  <div class="logo">AZ</div>
  <div class="brand"><b>Admin Dashboard</b><small>AI Azhar Automation — License Server</small></div>
  <div class="sp"></div>
  <span class="muted small" id="upd"></span>
  <a class="btn" href="/admin/api/backup" title="Poora data download">⬇ Backup</a>
  <a class="btn" href="/admin/logout">⏻ Logout</a>
</div>
<div class="wrap">
  <div class="stats" id="stats"></div>
  <div class="tabs" id="tabs">
    <button class="tab on" data-t="team">👥 Team</button>
    <button class="tab" data-t="req">📋 Requests<span class="n hide" id="nReq"></span></button>
    <button class="tab" data-t="feat">🔒 Feature Lock</button>
    <button class="tab" data-t="ann">📢 Elaan / Update</button>
    <button class="tab" data-t="err">⚠️ Errors<span class="n hide" id="nErr"></span></button>
  </div>

  <section id="t-team" class="card">
    <div class="row" style="margin-bottom:8px"><h3 style="flex:1">👥 Team</h3>
      <input type="search" id="uq" placeholder="🔍 Naam ya laptop dhoondein..." style="max-width:280px"></div>
    <div class="tbl"><table><thead><tr><th>Naam</th><th>Status</th><th>Kya kar raha hai</th><th>Videos</th><th>Badges</th><th style="text-align:right">Kaam</th></tr></thead>
    <tbody id="users"></tbody></table></div>
  </section>

  <section id="t-req" class="card hide">
    <h3>📋 Installation Requests</h3>
    <p class="muted small" style="margin:0 0 6px">Naya laptop tool chalata hai to yahan aata hai. Naam likh kar "Manzoor" karein.</p>
    <div class="tbl"><table><thead><tr><th>Laptop</th><th>Waqt</th><th>Status</th><th>Kaam</th></tr></thead><tbody id="reqs"></tbody></table></div>
  </section>

  <section id="t-feat" class="hide">
    <div class="card">
      <h3>🔒 Feature Lock</h3>
      <p class="muted small" style="margin:0">Switch ON = feature <b>sab ke liye band</b>. Phir neeche jin ke naam par ✔ lagayein, sirf <b>unhein ijazat</b> hogi. Har card par <b>💾 Save</b> dabayein. Tool par asar ~30 second mein.</p>
    </div>
    <div class="feat" id="feats"></div>
    <div class="card" style="margin-top:14px">
      <h3>➕ Koi aur feature (custom naam)</h3>
      <div class="row" style="margin-top:8px"><input type="text" id="newF" placeholder="feature ka naam, jaise: my_feature" style="max-width:320px">
      <button class="btn" id="addF">➕ Add</button></div>
    </div>
  </section>

  <section id="t-ann" class="hide">
    <div class="card">
      <h3>📢 Elaan / Update — sab users ke dashboard par</h3>
      <label class="f">Title</label><input type="text" id="aTitle">
      <label class="f">Poora message (HTML chal sakta hai)</label><textarea id="aBody" rows="5"></textarea>
      <div class="row" style="align-items:flex-start">
        <div style="flex:1;min-width:160px"><label class="f">Version number</label><input type="text" id="aVer"></div>
        <div style="flex:3;min-width:240px"><label class="f">Download URL (update ki zip)</label><input type="text" id="aUrl"></div>
      </div>
      <label class="f">📰 Chalti patti (ticker) — khaali chhorein agar nahi chahiye</label><input type="text" id="aTick">
      <p class="muted small" id="verHint"></p>
      <button class="btn p" id="aSave" style="margin-top:6px">📢 Save aur sab ko bhejein</button>
    </div>
    <div class="card"><h3>📜 Pichle elaan</h3><div class="tbl"><table><thead><tr><th>Version</th><th>Title</th><th>Waqt</th></tr></thead><tbody id="clog"></tbody></table></div></div>
  </section>

  <section id="t-err" class="card hide">
    <h3>⚠️ Error Reports (aakhri 30)</h3>
    <div class="tbl"><table><thead><tr><th>Kis ka</th><th>Masla</th><th>Waqt</th></tr></thead><tbody id="errs"></tbody></table></div>
  </section>
</div>
<div class="toast" id="toast"></div>

<script>
let S = null, DIRTY = {}, TAB = 'team';
try { TAB = localStorage.getItem('adm_tab') || 'team'; } catch (e) {}
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function when(iso){ if(!iso) return '-'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString(); }
function toast(msg){ const t = $('toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 2600); }
async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Admin':'1'}, body:JSON.stringify(body||{})});
  if(r.status === 401){ location.reload(); return {}; }
  const j = await r.json().catch(() => ({}));
  if(!r.ok || j.error){ toast('❌ ' + (j.error || 'Masla hua')); throw new Error(j.error); }
  return j;
}
async function load(){
  const r = await fetch('/admin/api/state', {cache:'no-store'});
  if(r.status === 401){ location.reload(); return; }
  S = await r.json();
  render();
  $('upd').textContent = 'Update: ' + new Date().toLocaleTimeString();
}
function render(){
  const st = S.stats;
  $('stats').innerHTML = [['Kul users', st.users], ['🟢 Online', st.online], ['📋 Pending', st.pending],
    ['🎬 Kul videos', st.videos], ['🚫 Band', st.blocked], ['⚠️ Errors (24h)', st.errors_24h]]
    .map(([l, v]) => `<div class="stat"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('');
  $('nReq').textContent = st.pending; $('nReq').classList.toggle('hide', !st.pending);
  $('nErr').textContent = st.errors_24h; $('nErr').classList.toggle('hide', !st.errors_24h);
  renderUsers(); renderReqs(); renderErrs(); renderClog();
  renderFeats();
  if(!$('aTitle')._loaded){ const a = S.announcement || {};
    $('aTitle').value = a.title || ''; $('aBody').value = a.body || ''; $('aVer').value = a.version || '';
    $('aUrl').value = a.download_url || ''; $('aTick').value = a.ticker_text || ''; $('aTitle')._loaded = true;
    $('verHint').textContent = a.version ? `Abhi ka version: ${a.version} — naya update bhejna ho to version number zaroor barhayein, warna users ko "Update Now" nahi dikhega.` : ''; }
}
function renderUsers(){
  const q = ($('uq').value || '').toLowerCase();
  const rows = S.users.filter(u => !q || (u.name + ' ' + u.machine).toLowerCase().includes(q)).map(u => {
    const pill = u.blocked ? `<span class="pill bl-p">🚫 Access band</span>` :
      u.online ? `<span class="pill on-p"><span class="dot"></span>Online · ${esc(u.online_for)}</span>` :
      `<span class="pill off-p"><span class="dot"></span>Offline${u.offline_for ? ' · ' + esc(u.offline_for) + ' se' : ''}</span>`;
    return `<tr><td><b>${esc(u.name)}</b><div class="fp">${esc(u.machine)} · ${esc(u.fp_short)}…</div></td>
      <td>${pill}</td><td class="small">${esc(u.activity) || '<span class="muted">-</span>'}</td>
      <td><b>${u.videos}</b></td><td>${u.badges} 🏆</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn s" data-rename="${esc(u.fp)}" data-name="${esc(u.name)}">✏️ Naam</button>
        ${u.blocked ? `<button class="btn s g" data-unblock="${esc(u.fp)}">✅ Access dein</button>`
                    : `<button class="btn s r" data-block="${esc(u.fp)}" data-name="${esc(u.name)}">🚫 Band</button>`}
      </td></tr>`;
  });
  $('users').innerHTML = rows.join('') || `<tr><td colspan="6" class="empty">Koi user nahi</td></tr>`;
}
function renderReqs(){
  $('reqs').innerHTML = S.requests.map(r => {
    const cls = {pending:'pend', approved:'appr', denied:'den'}[r.status] || 'off-p';
    const act = r.status === 'pending'
      ? `<div class="row"><input type="text" id="nm_${esc(r.fp)}" placeholder="Banday ka naam" style="max-width:170px">
         <button class="btn s g" data-approve="${esc(r.fp)}">✅ Manzoor</button>
         <button class="btn s r" data-deny="${esc(r.fp)}">✖ Rad</button></div>` : '';
    return `<tr><td><b>${esc(r.machine)}</b><div class="fp">${esc(r.fp_short)}…</div></td><td class="small">${when(r.at)}</td>
      <td><span class="pill ${cls}">${esc(r.status.toUpperCase())}</span></td><td>${act}</td></tr>`;
  }).join('') || `<tr><td colspan="4" class="empty">Koi request nahi</td></tr>`;
}
function renderErrs(){
  $('errs').innerHTML = S.errors.map(e => `<tr><td><b>${esc(e.who)}</b><div class="fp">${esc(e.machine)}</div></td>
    <td class="small">${esc(e.error)}</td><td class="small" style="white-space:nowrap">${when(e.at)}</td></tr>`).join('')
    || `<tr><td colspan="3" class="empty">Koi error nahi 🎉</td></tr>`;
}
function renderClog(){
  $('clog').innerHTML = S.changelog.map(c => `<tr><td><b>${esc(c.version)}</b></td><td>${esc(c.title)}</td><td class="small">${when(c.at)}</td></tr>`).join('')
    || `<tr><td colspan="3" class="empty">Abhi koi elaan nahi</td></tr>`;
}
function featState(f){ return DIRTY[f.key] || {locked: f.locked, allowed: [...f.allowed]}; }
function renderFeats(){
  const people = S.users.slice().sort((a, b) => a.name.localeCompare(b.name));
  $('feats').innerHTML = S.features.map(f => {
    const s = featState(f), dirty = !!DIRTY[f.key];
    const known = new Set(people.map(p => p.fp));
    const chips = people.map(p => {
      const on = s.allowed.includes(p.fp);
      return `<label class="chip ${on ? 'sel' : ''}"><input type="checkbox" data-f="${esc(f.key)}" data-fp="${esc(p.fp)}" ${on ? 'checked' : ''}>
        ${esc(p.name)}${p.name === 'Unnamed' ? ' <span class="fp">' + esc(p.machine) + '</span>' : ''}</label>`;
    }).join('') + s.allowed.filter(fp => !known.has(fp)).map(fp =>
      `<label class="chip sel gone" title="Ye laptop ab team list mein nahi"><input type="checkbox" data-f="${esc(f.key)}" data-fp="${esc(fp)}" checked>${esc(S.labels[fp] || fp.slice(0, 12) + '…')}</label>`).join('');
    const n = s.allowed.length;
    const status = s.locked ? `🔴 <b>Band</b> — ${n ? `sirf <b>${n}</b> ko ijazat` : 'kisi ko ijazat nahi'}` : `🟢 <b>Sab ke liye khula</b>`;
    return `<div class="fc ${s.locked ? 'lk' : ''}">
      <div class="fh"><div class="t"><b>${esc(f.label)}</b><div class="k">${esc(f.key)}${f.desc ? ' · ' + esc(f.desc) : ''}</div></div>
        <button class="sw ${s.locked ? 'on' : ''}" data-sw="${esc(f.key)}" title="Sab ke liye band / khula"></button></div>
      <div class="fs">${status}</div>
      <div class="muted small" style="margin-bottom:6px">${s.locked ? '✔ = in ko ijazat (lock ke bawajood chalega):' : 'Ijazat wali list (lock lagne par kaam aayegi):'}</div>
      <div class="chips">${chips || '<span class="muted small">Koi user nahi</span>'}</div>
      <div class="fa"><button class="btn s p" data-save="${esc(f.key)}" ${dirty ? '' : 'disabled style="opacity:.5"'}>💾 Save</button>
        ${dirty ? '<span class="dirty">● Save nahi hua</span>' : ''}
        ${f.desc === 'Custom feature' ? `<span class="sp"></span><button class="btn s r" data-delf="${esc(f.key)}">🗑</button>` : ''}</div>
    </div>`;
  }).join('');
}
function feature(key){ return S.features.find(f => f.key === key); }
function setDirty(key, fn){ const f = feature(key); const s = featState(f); fn(s); DIRTY[key] = s; renderFeats(); }

document.addEventListener('click', async e => {
  const t = e.target.closest('button,[data-t]'); if(!t) return;
  if(t.dataset.t){ TAB = t.dataset.t; try{ localStorage.setItem('adm_tab', TAB); }catch(_){} showTab(); return; }
  if(t.dataset.sw){ setDirty(t.dataset.sw, s => s.locked = !s.locked); return; }
  if(t.dataset.save){ const k = t.dataset.save, s = DIRTY[k]; if(!s) return;
    const f = feature(k);
    if(s.locked && !confirm(`"${f.label}" sab ke liye BAND hoga${s.allowed.length ? `, sirf ${s.allowed.length} ko ijazat` : ' (kisi ko ijazat nahi)'}. Pakka?`)) return;
    await api('/admin/api/feature', {feature:k, locked:s.locked, allowed:s.allowed}); delete DIRTY[k]; toast('✅ Save ho gaya'); load(); return; }
  if(t.dataset.delf){ if(!confirm('Ye custom feature hata dein?')) return; await api('/admin/api/feature_delete', {feature:t.dataset.delf}); delete DIRTY[t.dataset.delf]; load(); return; }
  if(t.dataset.rename){ const n = prompt('Naya naam:', t.dataset.name === 'Unnamed' ? '' : t.dataset.name); if(!n) return;
    await api('/admin/api/user', {fp:t.dataset.rename, action:'rename', name:n}); toast('✅ Naam badal gaya'); load(); return; }
  if(t.dataset.block){ if(!confirm(`${t.dataset.name} ki access BAND karein? Agli dafa tool chalane par ruk jayega.`)) return;
    await api('/admin/api/user', {fp:t.dataset.block, action:'block'}); toast('🚫 Access band'); load(); return; }
  if(t.dataset.unblock){ if(!confirm('Access wapas dein?')) return; await api('/admin/api/user', {fp:t.dataset.unblock, action:'unblock'}); toast('✅ Access mil gayi'); load(); return; }
  if(t.dataset.approve){ const n = ($('nm_' + t.dataset.approve)?.value || '').trim();
    if(!n && !confirm('Naam ke bagair manzoor karein? (baad mein badal sakte hain)')) return;
    await api('/admin/api/request', {fp:t.dataset.approve, action:'approve', name:n}); toast('✅ Manzoor'); load(); return; }
  if(t.dataset.deny){ if(!confirm('Ye request rad karein?')) return; await api('/admin/api/request', {fp:t.dataset.deny, action:'deny'}); load(); return; }
  if(t.id === 'aSave'){ if(!confirm('Ye elaan sab users ko bhejein?')) return;
    await api('/admin/api/announcement', {title:$('aTitle').value, body:$('aBody').value, version:$('aVer').value,
      download_url:$('aUrl').value, ticker_text:$('aTick').value}); toast('📢 Sab ko bhej diya'); $('aTitle')._loaded = false; load(); return; }
  if(t.id === 'addF'){ const k = $('newF').value.trim().replace(/\s+/g, '_'); if(!k) return;
    await api('/admin/api/feature', {feature:k, locked:false, allowed:[]}); $('newF').value = ''; TAB = 'feat'; load(); return; }
});
document.addEventListener('change', e => {
  const c = e.target; if(!c.dataset || !c.dataset.fp) return;
  setDirty(c.dataset.f, s => { s.allowed = s.allowed.filter(x => x !== c.dataset.fp); if(c.checked) s.allowed.push(c.dataset.fp); });
});
$('uq').addEventListener('input', () => S && renderUsers());
function showTab(){
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.t === TAB));
  ['team', 'req', 'feat', 'ann', 'err'].forEach(t => $('t-' + t).classList.toggle('hide', t !== TAB));
}
showTab(); load();
setInterval(() => { if(!Object.keys(DIRTY).length && !document.activeElement.matches('input,textarea')) load(); }, 15000);
</script></body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    app.run(host="0.0.0.0", port=port)
