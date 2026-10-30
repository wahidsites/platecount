"""PlateCount backend: Flask + SQLite. Serves the app page and the /api routes."""
import os, re, json, sqlite3, secrets
from datetime import date
from functools import wraps

import requests
from flask import Flask, request, jsonify, send_from_directory, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "platecount.db")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DAILY_AI_LIMIT = 30  # photo/description analyses per user per day (protects your free quota)
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024


def gemini_key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(os.path.join(BASE, "gemini_key.txt")) as f:
            return f.read().strip()
    except OSError:
        return ""


# ---------- database ----------
SCHEMA = """
create table if not exists users(
  id integer primary key, username text unique, pw text,
  weight real, height real, age integer, sex text, act real, goal text,
  target integer, protein integer);
create table if not exists tokens(token text primary key, user_id integer);
create table if not exists weights(user_id integer, day text, kg real);
create table if not exists food(
  id integer primary key, user_id integer, day text, name text,
  cal real, p real, c real, f real);
create index if not exists food_idx on food(user_id, day);
create table if not exists usage(user_id integer, day text, n integer, primary key(user_id, day));
"""
with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def need_user(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        h = request.headers.get("Authorization", "")
        t = h[7:] if h.startswith("Bearer ") else ""
        row = db().execute(
            "select u.* from tokens t join users u on u.id=t.user_id where t.token=?", (t,)
        ).fetchone()
        if not row:
            return jsonify(error="Please log in again"), 401
        g.user = row
        return fn(*a, **k)
    return wrapper


def profile(u):
    return {k: u[k] for k in ("weight", "height", "age", "sex", "act", "goal", "target", "protein")}


def new_token(uid):
    t = secrets.token_urlsafe(32)
    db().execute("insert into tokens values(?,?)", (t, uid))
    db().commit()
    return t


def num(v, lo, hi, name):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not lo <= x <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return x


# ---------- pages ----------
@app.get("/")
def home():
    return send_from_directory(BASE, "index.html")


# ---------- accounts ----------
@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    name, pw = str(d.get("username", "")).strip().lower(), str(d.get("password", ""))
    if not re.fullmatch(r"[a-z0-9_]{3,20}", name):
        return jsonify(error="Username: 3-20 letters, numbers or underscore"), 400
    if len(pw) < 6:
        return jsonify(error="Password needs at least 6 characters"), 400
    try:
        cur = db().execute("insert into users(username,pw) values(?,?)", (name, generate_password_hash(pw)))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="That username is taken"), 409
    return jsonify(token=new_token(cur.lastrowid))


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = db().execute("select * from users where username=?", (str(d.get("username", "")).strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["pw"], str(d.get("password", ""))):
        return jsonify(error="Wrong username or password"), 401
    return jsonify(token=new_token(u["id"]))


@app.post("/api/logout")
@need_user
def logout():
    t = request.headers["Authorization"][7:]
    db().execute("delete from tokens where token=?", (t,))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/me")
@need_user
def me():
    return jsonify(username=g.user["username"], profile=profile(g.user))


# ---------- personal calorie limit (only when the user asks) ----------
@app.post("/api/target")
@need_user
def target():
    d = request.get_json(silent=True) or {}
    try:
        w = num(d.get("weight"), 30, 300, "Weight")
        h = num(d.get("height"), 100, 250, "Height")
        a = num(d.get("age"), 12, 100, "Age")
        act = num(d.get("act"), 1.2, 1.9, "Activity")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    sex = "f" if d.get("sex") == "f" else "m"
    goal = d.get("goal") if d.get("goal") in ("cut", "keep", "bulk") else "keep"
    bmr = 10 * w + 6.25 * h - 5 * a + (5 if sex == "m" else -161)  # Mifflin-St Jeor
    tdee = bmr * act
    kcal = max(1200, round((tdee + {"cut": -500, "keep": 0, "bulk": 300}[goal]) / 10) * 10)
    protein = round(w * 1.8)
    c = db()
    last = c.execute("select kg from weights where user_id=? order by rowid desc limit 1", (g.user["id"],)).fetchone()
    if not last or last["kg"] != w:
        c.execute("insert into weights values(?,?,?)", (g.user["id"], date.today().isoformat(), w))
    c.execute("update users set weight=?,height=?,age=?,sex=?,act=?,goal=?,target=?,protein=? where id=?",
              (w, h, a, sex, act, goal, kcal, protein, g.user["id"]))
    c.commit()
    return jsonify(profile=dict(weight=w, height=h, age=a, sex=sex, act=act, goal=goal, target=kcal, protein=protein),
                   maintenance=round(tdee))


# ---------- food log ----------
@app.get("/api/food")
@need_user
def food_list():
    day = request.args.get("day", "")
    if not DAY_RE.match(day):
        return jsonify(error="Bad day"), 400
    rows = db().execute("select id,name,cal,p,c,f from food where user_id=? and day=? order by id",
                        (g.user["id"], day)).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@app.post("/api/food")
@need_user
def food_add():
    d = request.get_json(silent=True) or {}
    day = str(d.get("day", ""))
    try:
        vals = [num(d.get(k, 0), 0, 10000, k) for k in ("cal", "p", "c", "f")]
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not DAY_RE.match(day):
        return jsonify(error="Bad day"), 400
    db().execute("insert into food(user_id,day,name,cal,p,c,f) values(?,?,?,?,?,?,?)",
                 (g.user["id"], day, (str(d.get("name", "")).strip() or "Meal")[:80], *vals))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/food/<int:fid>")
@need_user
def food_del(fid):
    db().execute("delete from food where id=? and user_id=?", (fid, g.user["id"]))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/history")
@need_user
def history():
    rows = db().execute("select day, sum(cal) t from food where user_id=? group by day order by day desc limit 14",
                        (g.user["id"],)).fetchall()
    return jsonify({r["day"]: r["t"] for r in rows})


# ---------- photo / description -> calories ----------
@app.post("/api/analyze")
@need_user
def analyze():
    key = gemini_key()
    if not key:
        return jsonify(error="Server has no Gemini key yet (see gemini_key.txt)"), 500
    d = request.get_json(silent=True) or {}
    desc, img, day = str(d.get("desc", ""))[:500], d.get("image"), str(d.get("day", ""))
    if not DAY_RE.match(day):
        return jsonify(error="Bad day"), 400
    if not desc and not img:
        return jsonify(error="Send a photo or a description"), 400

    uid, c = g.user["id"], db()
    used = c.execute("select n from usage where user_id=? and day=?", (uid, day)).fetchone()
    if used and used["n"] >= DAILY_AI_LIMIT:
        return jsonify(error=f"Daily limit of {DAILY_AI_LIMIT} analyses reached. Enter calories by hand."), 429
    c.execute("insert into usage values(?,?,1) on conflict(user_id,day) do update set n=n+1", (uid, day))
    c.commit()

    u = g.user
    eaten = c.execute("select coalesce(sum(cal),0) s from food where user_id=? and day=?", (uid, day)).fetchone()["s"]
    ctx = (f"The user weighs {u['weight'] or 'unknown'} kg, goal: {u['goal'] or 'unknown'}, "
           f"daily limit {u['target'] or 'not set'} kcal, already eaten today {round(eaten)} kcal.")
    prompt = (f'You are a nutrition estimator. Estimate the food in this meal (photo and/or description). '
              f'Judge portion sizes carefully. {ctx} Description: "{desc or "none"}". '
              'Reply ONLY with JSON: {"name":string,"cal":number,"p":number,"c":number,"f":number,"note":string}. '
              "cal is kcal, p/c/f are grams for the whole portion. note is one short sentence on how this meal "
              "fits this user's weight and goal.")
    parts = [{"text": prompt}]
    if img:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img}})
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json"}},
            timeout=45)
        j = r.json()
        if r.status_code != 200:
            return jsonify(error=j.get("error", {}).get("message", f"AI error {r.status_code}")[:200]), 502
        txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        out = json.loads(re.sub(r"```json|```", "", txt).strip())
        return jsonify(name=str(out.get("name", "Meal"))[:80],
                       cal=float(out.get("cal", 0)), p=float(out.get("p", 0)),
                       c=float(out.get("c", 0)), f=float(out.get("f", 0)),
                       note=str(out.get("note", ""))[:200])
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return jsonify(error="Could not read the AI answer. Try again or enter calories by hand."), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
