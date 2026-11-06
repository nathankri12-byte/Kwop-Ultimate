import os, json, sqlite3, secrets, zipfile, io, re
from functools import wraps
from flask import Flask, request, jsonify, session, send_file, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(APP_DIR, "kwop.db"))
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this-secret")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE","0")=="1", MAX_CONTENT_LENGTH=2*1024*1024)

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS creators(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, url TEXT, status TEXT DEFAULT 'To contact', views INTEGER DEFAULT 0, code TEXT UNIQUE NOT NULL, clicks INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, brief TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS features(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, priority TEXT DEFAULT 'Medium', done INTEGER DEFAULT 0);
        """)

init_db()

def signed_in(fn):
    @wraps(fn)
    def inner(*a, **kw):
        if not session.get("user_id"):
            return jsonify(error="Please sign in first."), 401
        return fn(*a, **kw)
    return inner

def ai(prompt, max_tokens=1800):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None, "AI is not configured. Add a free Gemini API key as GEMINI_API_KEY in the server environment."
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = requests.post(url, params={"key": key}, json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.75,"maxOutputTokens":max_tokens}}, timeout=90)
        data = r.json()
        if not r.ok:
            return None, data.get("error",{}).get("message",f"Gemini request failed ({r.status_code})")
        parts = data.get("candidates",[{}])[0].get("content",{}).get("parts",[])
        text = "\n".join(p.get("text","") for p in parts).strip()
        return (text, None) if text else (None, "The AI returned an empty response.")
    except requests.RequestException as e:
        return None, f"Could not reach Gemini: {e}"

@app.get("/")
def home():
    return app.send_static_file("index.html")

@app.get("/integration.js")
def integration_js():
    return send_file(os.path.join(APP_DIR, "integration.js"), mimetype="application/javascript")

@app.get("/api/health")
def health():
    return jsonify(status="ok", ai_configured=bool(os.environ.get("GEMINI_API_KEY")), model=os.environ.get("GEMINI_MODEL","gemini-3.8-flash"))

@app.post("/api/auth/register")
def register():
    data=request.get_json(silent=True) or {}
    email=str(data.get("email","")).strip().lower()
    pw=str(data.get("password",""))
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):
        return jsonify(error="Enter a valid email address."),400
    if len(pw)<10:
        return jsonify(error="Use a password with at least 10 characters."),400
    try:
        with db() as c:
            cur=c.execute("INSERT INTO users(email,password) VALUES(?,?)",(email,generate_password_hash(pw)))
            session["user_id"]=cur.lastrowid
        return jsonify(ok=True,email=email)
    except sqlite3.IntegrityError:
        return jsonify(error="An account with this email already exists."),409

@app.post("/api/auth/login")
def login():
    data=request.get_json(silent=True) or {}
    email=str(data.get("email","")).strip().lower()
    pw=str(data.get("password",""))
    with db() as c:
        u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not u or not check_password_hash(u["password"],pw):
        return jsonify(error="Email or password is incorrect."),401
    session["user_id"]=u["id"]
    return jsonify(ok=True,email=email)

@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/auth/me")
def me():
    if not session.get("user_id"):
        return jsonify(authenticated=False)
    with db() as c:
        u=c.execute("SELECT email FROM users WHERE id=?",(session["user_id"],)).fetchone()
    return jsonify(authenticated=bool(u),email=u["email"] if u else None)

@app.post("/api/ai/ideas")
@signed_in
def ideas():
    d=request.get_json(silent=True) or {}
    prompt=f"""Act as a pragmatic SaaS product strategist. Create exactly 5 differentiated, realistic micro-SaaS ideas.
Interests: {d.get('interests','not specified')}
Audience: {d.get('audience','not specified')}
Time per day: {d.get('hours','not specified')} minutes
Avoid: {d.get('avoid','not specified')}
Goal: {d.get('goal','first customers')}
For each idea give name, promise, painful problem, 3 MVP features, target buyer, validation test, competitor/differentiation risk, feasibility score 1-5. Prefer narrow problems and achievable MVPs. Return valid JSON array only."""
    out,err=ai(prompt,2600)
    if err:
        return jsonify(error=err),503
    try:
        fence=chr(96)*3
        clean=out.strip().removeprefix(fence+"json").removeprefix(fence).removesuffix(fence).strip()
        return jsonify(ideas=json.loads(clean))
    except Exception:
        return jsonify(text=out)

@app.post("/api/ai/brief")
@signed_in
def brief():
    d=request.get_json(silent=True) or {}
    prompt=f"""Create a detailed, practical SaaS product specification in Markdown. Include positioning, customer persona, jobs-to-be-done, problem evidence to validate, core user journey, MVP scope, out-of-scope list, database entities, page-by-page UX, pricing hypotheses, landing page copy, onboarding, activation metric, retention metric, 30-day validation plan, privacy/security checklist and low-cost technical architecture.
Product: {d.get('name')}
Audience: {d.get('audience')}
Problem: {d.get('problem')}
Solution: {d.get('solution')}
Prices: Starter EUR {d.get('price1')}/month; Pro EUR {d.get('price2')}/month.
Be specific, avoid invented market statistics and do not claim validation that has not happened."""
    out,err=ai(prompt,4200)
    if err:
        return jsonify(error=err),503
    with db() as c:
        c.execute("INSERT INTO projects(user_id,name,brief) VALUES(?,?,?)",(session["user_id"],d.get("name","Untitled"),out))
    return jsonify(brief=out)

@app.post("/api/ai/video-script")
@signed_in
def video_script():
    d=request.get_json(silent=True) or {}
    notes=d.get("notes","").strip()
    if not notes:
        return jsonify(error="Add a transcript or a description of the video."),400
    prompt=f"""Analyse the supplied reference-video transcript/description and create an original short-form video concept for {d.get('product','the product')} on {d.get('platform','TikTok')}.
Reference URL (context only; do not claim to have watched it): {d.get('url','')}
Reference transcript/description: {notes}
Return: hook mechanics, pacing and visual pattern; ethical adaptation advice; original 20-30 second script with timestamps, spoken words and on-screen text; shot list; CTA; three alternative hooks; test hypothesis. Never state that you watched the URL; analyse only supplied text."""
    out,err=ai(prompt,2600)
    if err:
        return jsonify(error=err),503
    return jsonify(script=out)

@app.post("/api/ai/roadmap")
@signed_in
def roadmap():
    d=request.get_json(silent=True) or {}
    out,err=ai("Create a prioritised roadmap in JSON with 12 features, user value, effort, priority and acceptance criteria. Product brief:\n"+str(d.get("brief","")),2800)
    if err:
        return jsonify(error=err),503
    return jsonify(roadmap=out)

@app.post("/api/projects/export")
@signed_in
def export_project():
    d=request.get_json(silent=True) or {}
    name=re.sub(r"[^a-zA-Z0-9-]","-",str(d.get("name","my-saas")).lower()).strip("-") or "my-saas"
    brief=str(d.get("brief",""))
    prompt=f"""Generate a compact, genuinely runnable MVP web application. Return ONLY valid JSON mapping relative file paths to full UTF-8 file contents. Include app.py, requirements.txt, templates/index.html, README.md. Use Flask and SQLite, server-side validation, responsive accessible UI, working CRUD for the core workflow, no fake payment claims. Product: {name}. Specification: {brief[:12000]}. Keep code concise and runnable. No secrets."""
    out,err=ai(prompt,7500)
    if err:
        return jsonify(error=err),503
    try:
        fence=chr(96)*3
        clean=out.strip().removeprefix(fence+"json").removeprefix(fence).removesuffix(fence).strip()
        files=json.loads(clean)
        if not isinstance(files,dict) or "app.py" not in files or "requirements.txt" not in files:
            raise ValueError("Required files missing")
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,"w",zipfile.ZIP_DEFLATED) as z:
            for path,content in files.items():
                if path.startswith("/") or ".." in path.split("/"):
                    continue
                z.writestr(f"{name}/{path}",str(content))
        mem.seek(0)
        return send_file(mem,mimetype="application/zip",as_attachment=True,download_name=f"{name}-mvp.zip")
    except Exception as e:
        return jsonify(error="AI did not return a valid project bundle. Try a shorter brief.",detail=str(e),sample=out[:800]),502

@app.get("/api/projects")
@signed_in
def projects():
    with db() as c:
        rows=c.execute("SELECT id,name,created_at FROM projects WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    return jsonify(projects=[dict(x) for x in rows])

@app.get("/api/creators")
@signed_in
def get_creators():
    with db() as c:
        rows=c.execute("SELECT id,name,url,status,views,code,clicks FROM creators WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    return jsonify(creators=[dict(x) for x in rows])

@app.post("/api/creators")
@signed_in
def add_creator():
    d=request.get_json(silent=True) or {}
    name=str(d.get("name","")).strip()
    if not name:
        return jsonify(error="Creator name is required."),400
    code=secrets.token_urlsafe(7)
    with db() as c:
        cur=c.execute("INSERT INTO creators(user_id,name,url,status,views,code) VALUES(?,?,?,?,?,?)",(session["user_id"],name,d.get("url",""),d.get("status","To contact"),max(0,int(d.get("views",0))),code))
        row=c.execute("SELECT id,name,url,status,views,code,clicks FROM creators WHERE id=?",(cur.lastrowid,)).fetchone()
    return jsonify(creator=dict(row),tracking_url=request.host_url.rstrip("/")+"/r/"+code)

@app.patch("/api/creators/<int:creator_id>")
@signed_in
def update_creator(creator_id):
    d=request.get_json(silent=True) or {}
    with db() as c:
        cur=c.execute("UPDATE creators SET status=COALESCE(?,status),views=COALESCE(?,views) WHERE id=? AND user_id=?",(d.get("status"),d.get("views"),creator_id,session["user_id"]))
        if not cur.rowcount:
            return jsonify(error="Creator not found."),404
        row=c.execute("SELECT id,name,url,status,views,code,clicks FROM creators WHERE id=?",(creator_id,)).fetchone()
    return jsonify(creator=dict(row))

@app.delete("/api/creators/<int:creator_id>")
@signed_in
def delete_creator(creator_id):
    with db() as c:
        c.execute("DELETE FROM creators WHERE id=? AND user_id=?",(creator_id,session["user_id"]))
    return jsonify(ok=True)

@app.get("/r/<code>")
def track_click(code):
    with db() as c:
        row=c.execute("SELECT url FROM creators WHERE code=?",(code,)).fetchone()
    if not row:
        return "Tracking link not found",404
    with db() as c:
        c.execute("UPDATE creators SET clicks=clicks+1 WHERE code=?",(code,))
    target=row["url"]
    return redirect(target if target and target.startswith(("https://","http://")) else "/")

@app.get("/api/dashboard")
@signed_in
def dashboard():
    uid=session["user_id"]
    with db() as c:
        p=c.execute("SELECT COUNT(*) n FROM projects WHERE user_id=?",(uid,)).fetchone()["n"]
        cr=c.execute("SELECT COUNT(*) n,COALESCE(SUM(views),0) views,COALESCE(SUM(clicks),0) clicks FROM creators WHERE user_id=?",(uid,)).fetchone()
        f=c.execute("SELECT COUNT(*) n FROM features WHERE user_id=? AND done=1",(uid,)).fetchone()["n"]
    return jsonify(projects=p,creators=cr["n"],views=cr["views"],clicks=cr["clicks"],completed_features=f)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
