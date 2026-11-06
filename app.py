import os, json, secrets, zipfile, io, re, time
from html import escape as html_escape
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, request, jsonify, session, send_file, redirect
import requests

APP_DIR=os.path.dirname(os.path.abspath(__file__))
SB_URL=os.environ.get("SUPABASE_URL","https://tbblvnlqqjmwvxtutrwh.supabase.co").rstrip("/")
SB_KEY=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
GEMINI_KEY=os.environ.get("GEMINI_API_KEY","")
GEMINI_MODEL=os.environ.get("GEMINI_MODEL","gemini-3.8-flash")
app=Flask(__name__,static_folder=None)
app.secret_key=os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE","1")=="1",PERMANENT_SESSION_LIFETIME=1209600,MAX_CONTENT_LENGTH=2*1024*1024)

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"]="nosniff";r.headers["X-Frame-Options"]="DENY"
    r.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    r.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/api/"):r.headers["Cache-Control"]="no-store"
    return r

def err(msg,status=400):return jsonify(error=str(msg)),status
def auth_headers(token=None):
    h={"apikey":SB_KEY,"Content-Type":"application/json","Authorization":"Bearer "+(token or session.get("access_token") or SB_KEY)}
    return h
def save_auth(d):
    u=d.get("user") or {};session["access_token"]=d.get("access_token");session["refresh_token"]=d.get("refresh_token")
    session["user_id"]=u.get("id");session["email"]=u.get("email")
    session["expires_at"]=int(time.time())+int(d.get("expires_in",3600));session.permanent=True
def refresh():
    if not session.get("refresh_token") or not SB_KEY:return False
    try:
        r=requests.post(SB_URL+"/auth/v1/token",params={"grant_type":"refresh_token"},headers=auth_headers(SB_KEY),json={"refresh_token":session["refresh_token"]},timeout=15)
        if not r.ok:session.clear();return False
        save_auth(r.json());return True
    except requests.RequestException:return False
def sb(method,path,params=None,data=None,prefer=None,retry=True):
    if not SB_KEY:raise RuntimeError("Supabase is not configured. Set SUPABASE_PUBLISHABLE_KEY on the server.")
    if session.get("access_token") and session.get("expires_at",0)<time.time()+60:refresh()
    h=auth_headers()
    if prefer:h["Prefer"]=prefer
    try:
        r=requests.request(method,SB_URL+path,params=params,json=data,headers=h,timeout=25)
        if r.status_code==401 and retry and refresh():return sb(method,path,params,data,prefer,False)
        if not r.ok:
            try:
                j=r.json();m=j.get("message") or j.get("msg") or j.get("details") or j.get("error_description") or j.get("error") or r.text
            except ValueError:m=r.text
            raise RuntimeError(str(m)[:400])
        return r.json() if r.content else None
    except requests.RequestException as e:raise RuntimeError("Supabase connection failed: "+str(e))
def select(table,params):return sb("GET","/rest/v1/"+table,params=params) or []
def insert(table,data):
    rows=sb("POST","/rest/v1/"+table,data=data,prefer="return=representation")
    return rows[0] if rows else None
def patch(table,params,data):return sb("PATCH","/rest/v1/"+table,params=params,data=data,prefer="return=representation") or []
def delete(table,params):sb("DELETE","/rest/v1/"+table,params=params,prefer="return=minimal")
def signed(fn):
    @wraps(fn)
    def w(*a,**k):
        if not session.get("user_id") or not session.get("access_token"):return err("Please sign in first.",401)
        return fn(*a,**k)
    return w
def valid_url(v,empty=True):
    v=str(v or "").strip()
    if not v and empty:return ""
    p=urlparse(v)
    if p.scheme not in ("http","https") or not p.netloc or p.username or p.password:raise ValueError("Enter a valid http or https URL.")
    return v
def ai(prompt,tokens=2500):
    if not GEMINI_KEY:return None,"AI is not configured. Add GEMINI_API_KEY in the server environment."
    try:
        r=requests.post("https://generativelanguage.googleapis.com/v1beta/models/"+GEMINI_MODEL+":generateContent",params={"key":GEMINI_KEY},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.7,"maxOutputTokens":tokens}},timeout=100)
        j=r.json()
        if not r.ok:return None,j.get("error",{}).get("message","Gemini request failed.")
        t="\n".join(x.get("text","") for x in j.get("candidates",[{}])[0].get("content",{}).get("parts",[])).strip()
        return (t,None) if t else (None,"AI returned an empty response.")
    except (requests.RequestException,ValueError) as e:return None,"Gemini request failed: "+str(e)
def parse_json(t):
    t=t.strip()
    if t.startswith(chr(96)*3):
        t=t.split("\n",1)[-1]
        if t.endswith(chr(96)*3):t=t[:-3]
    return json.loads(t.strip())

@app.get("/")
def home():return send_file(os.path.join(APP_DIR,"index.html"))
@app.get("/integration.js")
def js():return send_file(os.path.join(APP_DIR,"integration.js"),mimetype="application/javascript")
@app.get("/api/health")
def health():return jsonify(status="ok",supabase_configured=bool(SB_KEY),ai_configured=bool(GEMINI_KEY),model=GEMINI_MODEL)

@app.post("/api/auth/register")
def register():
    d=request.get_json(silent=True) or {};email=str(d.get("email","")).strip().lower();pw=str(d.get("password",""))
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):return err("Enter a valid email address.")
    if len(pw)<10:return err("Use a password with at least 10 characters.")
    if not SB_KEY:return err("Supabase is not configured on the server.",503)
    try:
        r=requests.post(SB_URL+"/auth/v1/signup",headers=auth_headers(SB_KEY),json={"email":email,"password":pw},timeout=20);j=r.json()
        if not r.ok:return err(j.get("msg") or j.get("message") or "Could not create account.",r.status_code)
        if j.get("access_token"):save_auth(j);return jsonify(ok=True,email=email,authenticated=True)
        return jsonify(ok=True,email=email,authenticated=False,message="Account created. Confirm your email, then sign in.")
    except (requests.RequestException,ValueError) as e:return err("Registration failed: "+str(e),503)
@app.post("/api/auth/login")
def login():
    d=request.get_json(silent=True) or {}
    if not SB_KEY:return err("Supabase is not configured on the server.",503)
    try:
        r=requests.post(SB_URL+"/auth/v1/token",params={"grant_type":"password"},headers=auth_headers(SB_KEY),json={"email":str(d.get("email","")).strip().lower(),"password":str(d.get("password",""))},timeout=20);j=r.json()
        if not r.ok:return err(j.get("msg") or j.get("message") or "Login failed. Confirm your email if you just registered.",401)
        save_auth(j);return jsonify(ok=True,email=session.get("email"),authenticated=True)
    except (requests.RequestException,ValueError) as e:return err("Login failed: "+str(e),503)
@app.post("/api/auth/logout")
def logout():
    if session.get("access_token"):
        try:requests.post(SB_URL+"/auth/v1/logout",headers=auth_headers(),timeout=10)
        except requests.RequestException:pass
    session.clear();return jsonify(ok=True)
@app.get("/api/auth/me")
def me():
    if not session.get("access_token"):return jsonify(authenticated=False)
    if session.get("expires_at",0)<time.time()+60 and not refresh():return jsonify(authenticated=False)
    return jsonify(authenticated=True,email=session.get("email"))

@app.post("/api/ai/ideas")
@signed
def ideas():
    d=request.get_json(silent=True) or {}
    p=f"""Create exactly 5 differentiated, realistic micro-SaaS ideas. Interests: {str(d.get('interests',''))[:500]}; audience: {str(d.get('audience',''))[:500]}; time/day: {str(d.get('hours',''))[:20]} minutes; avoid: {str(d.get('avoid',''))[:500]}; goal: {str(d.get('goal',''))[:300]}. For each include name, promise, painful problem, 3 MVP features, target buyer, validation test, competitor risk and feasibility 1-5. Return valid JSON array only."""
    t,e=ai(p,3200)
    if e:return err(e,503)
    try:return jsonify(ideas=parse_json(t))
    except Exception:return jsonify(text=t)
@app.post("/api/ai/brief")
@signed
def brief():
    d=request.get_json(silent=True) or {}
    p=f"""Write a detailed practical SaaS specification in Markdown: positioning, persona, jobs-to-be-done, evidence to validate, user journey, MVP and exclusions, data entities, page UX, pricing hypotheses, landing copy, onboarding, activation/retention metrics, 30-day validation, privacy/security and low-cost architecture. Product {str(d.get('name',''))[:150]}, audience {str(d.get('audience',''))[:500]}, problem {str(d.get('problem',''))[:1800]}, solution {str(d.get('solution',''))[:1800]}, prices {d.get('price1')} and {d.get('price2')} EUR/month. Do not invent statistics or claim validation."""
    t,e=ai(p,5000)
    if e:return err(e,503)
    insert("kwop_projects",{"user_id":session["user_id"],"name":str(d.get("name") or "Untitled")[:120],"brief":t})
    return jsonify(brief=t)
@app.post("/api/ai/video-script")
@signed
def video_script():
    d=request.get_json(silent=True) or {};notes=str(d.get("notes","")).strip();url=str(d.get("url","")).strip()
    if not notes:return err("Add a transcript or description of the video.")
    if url:
        try:valid_url(url,False)
        except ValueError as e:return err(str(e))
    t,e=ai(f"""Analyse only this user-supplied transcript/description; do not claim to watch the URL. Product {str(d.get('product',''))[:150]}, platform {str(d.get('platform','TikTok'))[:40]}, URL context {url[:400]}. Reference: {notes[:7000]}. Return hook mechanics, pacing, ethical adaptation, original 20-30 sec timed script, shot list, CTA, three alternate hooks and test hypothesis.""",3000)
    if e:return err(e,503)
    return jsonify(script=t)
@app.post("/api/ai/roadmap")
@signed
def roadmap():
    d=request.get_json(silent=True) or {};t,e=ai("Create 12 prioritized product features in JSON, with value, effort, priority and acceptance criteria. Brief:\n"+str(d.get("brief",""))[:10000],3500)
    if e:return err(e,503)
    try:return jsonify(roadmap=parse_json(t))
    except Exception:return jsonify(roadmap=t)
@app.post("/api/projects/export")
@signed
def export_project():
    d=request.get_json(silent=True) or {};name=re.sub(r"[^a-zA-Z0-9-]","-",str(d.get("name","my-saas")).lower()).strip("-") or "my-saas";brief=str(d.get("brief",""))[:12000]
    if not brief:return err("Create the product brief first.")
    t,e=ai(f"""Return ONLY valid JSON mapping relative paths to complete file contents for a runnable Flask+SQLite MVP. Include app.py, requirements.txt, templates/index.html and README.md. CRUD must work, validate inputs, responsive accessible UI, no fake payments. Product {name}. Specification: {brief}. No secrets.""",8000)
    if e:return err(e,503)
    try:
        files=parse_json(t)
        if not isinstance(files,dict) or "app.py" not in files or "requirements.txt" not in files:raise ValueError()
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,"w",zipfile.ZIP_DEFLATED) as z:
            for path,body in files.items():
                path=str(path)
                if not path.startswith("/") and ".." not in path.split("/") and isinstance(body,str):z.writestr(name+"/"+path,body)
        mem.seek(0);return send_file(mem,mimetype="application/zip",as_attachment=True,download_name=name+"-mvp.zip")
    except Exception:return err("AI did not return a valid ZIP project. Try a shorter brief.",502)

@app.get("/api/projects")
@signed
def projects():
    return jsonify(projects=select("kwop_projects",{"select":"id,name,brief,created_at,updated_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))
@app.get("/api/creators")
@signed
def get_creators():
    rows=select("kwop_creators",{"select":"id,name,profile_url,status,views,code,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"})
    codes=[x["code"] for x in rows if x.get("code")]
    links=select("kwop_tracking_links",{"select":"code,clicks","code":"in.("+",".join(codes)+")"}) if codes else []
    counts={x["code"]:x.get("clicks",0) for x in links}
    for x in rows:
        x["url"]=x.pop("profile_url",None);x["clicks"]=counts.get(x.get("code"),0)
    return jsonify(creators=rows)
@app.post("/api/creators")
@signed
def add_creator():
    d=request.get_json(silent=True) or {};name=str(d.get("name","")).strip()
    if not name:return err("Creator name is required.")
    try:url=valid_url(d.get("url",""));views=max(0,int(d.get("views",0)))
    except (ValueError,TypeError) as e:return err(str(e) or "Invalid views.")
    status=str(d.get("status","To contact"))
    if status not in ("To contact","Contacted","Interested","Posted","Declined"):return err("Invalid creator status.")
    code=secrets.token_urlsafe(9)
    row=insert("kwop_creators",{"user_id":session["user_id"],"name":name[:120],"profile_url":url or None,"status":status,"views":views,"code":code})
    insert("kwop_tracking_links",{"user_id":session["user_id"],"code":code,"target_url":url or None,"clicks":0})
    row["url"]=row.pop("profile_url",None);row["clicks"]=0
    return jsonify(creator=row,tracking_url=request.host_url.rstrip("/")+"/r/"+code)
@app.patch("/api/creators/<creator_id>")
@signed
def update_creator(creator_id):
    d=request.get_json(silent=True) or {};p={}
    if "status" in d:
        if d["status"] not in ("To contact","Contacted","Interested","Posted","Declined"):return err("Invalid status.")
        p["status"]=d["status"]
    if "views" in d:
        try:p["views"]=max(0,int(d["views"]))
        except (ValueError,TypeError):return err("Views must be a non-negative number.")
    if "url" in d:
        try:p["profile_url"]=valid_url(d["url"])
        except ValueError as e:return err(str(e))
    if not p:return err("No changes supplied.")
    rows=patch("kwop_creators",{"id":"eq."+creator_id,"user_id":"eq."+session["user_id"]},p)
    if not rows:return err("Creator not found.",404)
    if "url" in d:patch("kwop_tracking_links",{"code":"eq."+rows[0]["code"],"user_id":"eq."+session["user_id"]},{"target_url":p.get("profile_url")})
    rows[0]["url"]=rows[0].pop("profile_url",None)
    return jsonify(creator=rows[0])
@app.delete("/api/creators/<creator_id>")
@signed
def remove_creator(creator_id):
    rows=select("kwop_creators",{"select":"code","id":"eq."+creator_id,"user_id":"eq."+session["user_id"]})
    if rows:delete("kwop_tracking_links",{"code":"eq."+rows[0]["code"],"user_id":"eq."+session["user_id"]})
    delete("kwop_creators",{"id":"eq."+creator_id,"user_id":"eq."+session["user_id"]});return jsonify(ok=True)
@app.get("/r/<code>")
def track(code):
    if not SB_KEY:return "Tracking is unavailable",503
    public_headers={"apikey":SB_KEY,"Authorization":"Bearer "+SB_KEY}
    try:
        r=requests.get(SB_URL+"/rest/v1/kwop_tracking_links",params={"select":"code,target_url,clicks","code":"eq."+code,"limit":"1"},headers=public_headers,timeout=15)
        rows=r.json() if r.ok else []
        if not rows:return "Tracking link not found",404
        row=rows[0]
        requests.patch(SB_URL+"/rest/v1/kwop_tracking_links",params={"code":"eq."+code},headers={**public_headers,"Content-Type":"application/json","Prefer":"return=minimal"},json={"clicks":int(row.get("clicks") or 0)+1},timeout=15)
        try:dest=valid_url(row.get("target_url"),False) if row.get("target_url") else "/"
        except ValueError:dest="/"
        return redirect(dest,302)
    except requests.RequestException:return "Tracking temporarily unavailable",503
@app.get("/api/dashboard")
@signed
def dashboard():
    uid=session["user_id"];p=select("kwop_projects",{"select":"id","user_id":"eq."+uid});c=select("kwop_creators",{"select":"views,code","user_id":"eq."+uid});f=select("kwop_features",{"select":"id","user_id":"eq."+uid,"done":"eq.true"})
    codes=[x["code"] for x in c if x.get("code")]
    links=select("kwop_tracking_links",{"select":"code,clicks","code":"in.("+",".join(codes)+")"}) if codes else []
    return jsonify(projects=len(p),creators=len(c),views=sum(int(x.get("views") or 0) for x in c),clicks=sum(int(x.get("clicks") or 0) for x in links),completed_features=len(f))
@app.get("/api/tasks")
@signed
def tasks_get():
    rows=select("kwop_tasks",{"select":"completed","user_id":"eq."+session["user_id"],"limit":"1"})
    return jsonify(completed=(rows[0].get("completed") or []) if rows else [])
@app.put("/api/tasks")
@signed
def tasks_put():
    d=request.get_json(silent=True) or {}
    try:done=sorted(set(int(x) for x in d.get("completed",[]) if 0<=int(x)<30))
    except (ValueError,TypeError):return err("Invalid task list.")
    sb("POST","/rest/v1/kwop_tasks",data={"user_id":session["user_id"],"completed":done},prefer="resolution=merge-duplicates,return=minimal")
    return jsonify(completed=done)
@app.get("/api/profile")
@signed
def profile_get():
    rows=select("kwop_profiles",{"select":"interests,audience,hours,avoid,goal","user_id":"eq."+session["user_id"],"limit":"1"})
    return jsonify(profile=rows[0] if rows else {})
@app.put("/api/profile")
@signed
def profile_put():
    d=request.get_json(silent=True) or {};payload={"user_id":session["user_id"],"interests":str(d.get("interests",""))[:500],"audience":str(d.get("audience",""))[:500],"hours":str(d.get("hours","30"))[:30],"avoid":str(d.get("avoid",""))[:500],"goal":str(d.get("goal",""))[:300]}
    sb("POST","/rest/v1/kwop_profiles",data=payload,prefer="resolution=merge-duplicates,return=minimal");return jsonify(ok=True)
@app.get("/api/features")
@signed
def features_get():
    return jsonify(features=select("kwop_features",{"select":"id,name,priority,done,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))
@app.post("/api/features")
@signed
def features_add():
    d=request.get_json(silent=True) or {};name=str(d.get("name","")).strip();priority=str(d.get("priority","Medium"))
    if not name:return err("Feature name is required.")
    if priority not in ("High","Medium","Low"):return err("Invalid priority.")
    return jsonify(feature=insert("kwop_features",{"user_id":session["user_id"],"name":name[:180],"priority":priority,"done":False}))
@app.patch("/api/features/<feature_id>")
@signed
def features_patch(feature_id):
    d=request.get_json(silent=True) or {};p={}
    if "done" in d:p["done"]=bool(d["done"])
    if "priority" in d:
        if d["priority"] not in ("High","Medium","Low"):return err("Invalid priority.")
        p["priority"]=d["priority"]
    if not p:return err("No changes supplied.")
    if not patch("kwop_features",{"id":"eq."+feature_id,"user_id":"eq."+session["user_id"]},p):return err("Feature not found.",404)
    return jsonify(ok=True)
@app.delete("/api/features/<feature_id>")
@signed
def features_delete(feature_id):
    delete("kwop_features",{"id":"eq."+feature_id,"user_id":"eq."+session["user_id"]});return jsonify(ok=True)


# --- Extended launch, analytics, leads, revenue, email drafts and AI assistant ---
def owned_upsert(table,payload,conflict):
    return sb("POST","/rest/v1/"+table,params={"on_conflict":conflict},data=payload,prefer="resolution=merge-duplicates,return=representation") or []

@app.get("/api/launches")
@signed
def launches_get():
    return jsonify(launches=select("kwop_launches",{"select":"id,slug,title,description,landing_copy,plans,published,created_at,updated_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))

@app.post("/api/launches")
@signed
def launches_add():
    d=request.get_json(silent=True) or {}
    title=str(d.get("title","")).strip()
    if not title:return err("Launch title is required.")
    slug=re.sub(r"[^a-z0-9]+","-",str(d.get("slug") or title).lower()).strip("-")[:60]
    if not slug:return err("Choose a valid public page name.")
    description=str(d.get("description",""))[:1800]
    copy=d.get("landing_copy") if isinstance(d.get("landing_copy"),dict) else {}
    plans=d.get("plans") if isinstance(d.get("plans"),list) else []
    clean_plans=[]
    for p in plans[:4]:
        if isinstance(p,dict):
            try:price=max(0,float(p.get("price",0)))
            except (TypeError,ValueError):price=0
            clean_plans.append({"name":str(p.get("name","Plan"))[:60],"price":price,"description":str(p.get("description",""))[:300]})
    row=insert("kwop_launches",{"user_id":session["user_id"],"slug":slug,"title":title[:150],"description":description,"landing_copy":copy,"plans":clean_plans,"published":False})
    return jsonify(launch=row),201

@app.patch("/api/launches/<launch_id>")
@signed
def launches_patch(launch_id):
    d=request.get_json(silent=True) or {};p={}
    for k,limit in [("title",150),("description",1800),("slug",60)]:
        if k in d:
            value=str(d[k]).strip()
            if not value:return err(k+" cannot be empty.")
            if k=="slug":value=re.sub(r"[^a-z0-9]+","-",value.lower()).strip("-")
            p[k]=value[:limit]
    if "published" in d:p["published"]=bool(d["published"])
    if "landing_copy" in d and isinstance(d["landing_copy"],dict):p["landing_copy"]=d["landing_copy"]
    if "plans" in d and isinstance(d["plans"],list):
        plans=[]
        for x in d["plans"][:4]:
            if not isinstance(x,dict):continue
            try:price=max(0,float(x.get("price",0)))
            except (TypeError,ValueError):price=0
            plans.append({"name":str(x.get("name","Plan"))[:60],"price":price,"description":str(x.get("description",""))[:300]})
        p["plans"]=plans
    if not p:return err("No changes supplied.")
    rows=patch("kwop_launches",{"id":"eq."+launch_id,"user_id":"eq."+session["user_id"]},p)
    if not rows:return err("Launch page not found.",404)
    return jsonify(launch=rows[0])

@app.delete("/api/launches/<launch_id>")
@signed
def launches_delete(launch_id):
    delete("kwop_launches",{"id":"eq."+launch_id,"user_id":"eq."+session["user_id"]})
    return jsonify(ok=True)

@app.get("/p/<slug>")
def public_launch(slug):
    rows=select("kwop_launches",{"select":"id,slug,title,description,landing_copy,plans","slug":"eq."+slug,"published":"eq.true","limit":"1"})
    if not rows:return "Launch page not found",404
    x=rows[0];copy=x.get("landing_copy") or {};plans=x.get("plans") or []
    headline=html_escape(str(copy.get("headline") or x["title"]))
    sub=html_escape(str(copy.get("subheadline") or x.get("description","")))
    body=html_escape(str(copy.get("body") or ""))
    pricing="".join('<article><h2>'+html_escape(str(p.get("name","Plan")))+ '</h2><strong>€'+html_escape(str(p.get("price",0)))+'/mo</strong><p>'+html_escape(str(p.get("description","")))+ '</p><button onclick="signalInterest()">Signal interest</button></article>' for p in plans)
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>"""+html_escape(x["title"])+"""</title><style>body{margin:0;background:#09111e;color:#f5f7fb;font:16px/1.6 system-ui}main{max-width:900px;margin:12vh auto;padding:28px}.hero{text-align:center}.hero h1{font-size:clamp(38px,7vw,68px);line-height:1.04}.muted{color:#aab7c9}.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-top:42px}article{background:#142239;border:1px solid #31425c;border-radius:16px;padding:22px}button{background:#c5f36a;border:0;border-radius:9px;padding:12px 18px;font-weight:700;cursor:pointer}.consent{position:fixed;bottom:16px;left:16px;right:16px;max-width:760px;margin:auto;background:#142239;border:1px solid #425574;border-radius:14px;padding:16px;box-shadow:0 12px 50px #0008;z-index:9}.consent p{margin:0 0 10px}.consent button+button{margin-left:8px;background:#263a56;color:white}</style><main><section class="hero"><h1>"""+headline+"""</h1><p class="muted">"""+sub+"""</p><p>"""+body+"""</p><button onclick="signalInterest('main_cta')">I'm interested</button><p id="feedback" class="muted"></p></section><section class="plans">"""+pricing+"""</section></main><aside id="analyticsConsent" class="consent"><p><b>Anonymous analytics</b><br>This page records page views and button clicks only if you agree. It does not record keystrokes or form contents.</p><button id="acceptAnalytics">Accept analytics</button><button id="declineAnalytics">Decline</button></aside><script>
const launchId="""+json.dumps(x["id"])+""";let sk=sessionStorage.getItem('kwop_public_session');if(!sk){sk=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random());sessionStorage.setItem('kwop_public_session',sk)}
let analyticsAllowed=localStorage.getItem('kwop_analytics_consent')==='yes';if(analyticsAllowed)document.getElementById('analyticsConsent').remove();
function track(type,label){if(!analyticsAllowed)return;fetch('/api/public-event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({launch_id:launchId,session_key:sk,event_type:type,label:label||'',page_path:location.pathname})}).catch(()=>{})}
function signalInterest(label){track('cta_click',label);document.getElementById('feedback').textContent='Thanks. This demo records anonymous interest only; it does not create an account or take payment.'}
document.getElementById('acceptAnalytics').onclick=()=>{analyticsAllowed=true;localStorage.setItem('kwop_analytics_consent','yes');document.getElementById('analyticsConsent').remove();track('page_view','')};
document.getElementById('declineAnalytics').onclick=()=>{localStorage.setItem('kwop_analytics_consent','no');document.getElementById('analyticsConsent').remove()};
if(analyticsAllowed)track('page_view','');</script></html>"""
@app.post("/api/public-event")
def public_event():
    if not SB_KEY:return err("Analytics is not configured.",503)
    d=request.get_json(silent=True) or {}
    if d.get("event_type") not in ("page_view","cta_click","pricing_view"):return err("Invalid event.")
    launch_id=str(d.get("launch_id",""))
    if not re.fullmatch(r"[0-9a-fA-F-]{36}",launch_id):return err("Invalid launch.")
    sk=str(d.get("session_key",""))
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}",sk):return err("Invalid session.")
    payload={"launch_id":launch_id,"session_key":sk,"event_type":d["event_type"],"page_path":str(d.get("page_path","/"))[:200],"label":str(d.get("label",""))[:120]}
    try:
        r=requests.post(SB_URL+"/rest/v1/kwop_public_events",headers={"apikey":SB_KEY,"Authorization":"Bearer "+SB_KEY,"Content-Type":"application/json","Prefer":"return=minimal"},json=payload,timeout=12)
        if not r.ok:return err("Event could not be recorded.",400)
        return jsonify(ok=True)
    except requests.RequestException:return err("Analytics temporarily unavailable.",503)

@app.get("/api/analytics")
@signed
def analytics_get():
    launches=select("kwop_launches",{"select":"id,slug,title,published","user_id":"eq."+session["user_id"]})
    ids=[x["id"] for x in launches]
    events=select("kwop_public_events",{"select":"launch_id,session_key,event_type,page_path,label,created_at","launch_id":"in.("+",".join(ids)+")","order":"created_at.desc","limit":"1000"}) if ids else []
    summary={"page_views":sum(1 for e in events if e["event_type"]=="page_view"),"cta_clicks":sum(1 for e in events if e["event_type"]=="cta_click"),"pricing_views":sum(1 for e in events if e["event_type"]=="pricing_view"),"unique_visitors":len(set(e["session_key"] for e in events))}
    names={x["id"]:x["title"] for x in launches}
    recent=[{**e,"launch_title":names.get(e["launch_id"],"Launch")} for e in events[:50]]
    return jsonify(summary=summary,launches=launches,recent=recent)

@app.get("/api/leads")
@signed
def leads_get():
    return jsonify(leads=select("kwop_leads",{"select":"id,email,name,source,status,score,notes,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))
@app.post("/api/leads")
@signed
def leads_add():
    d=request.get_json(silent=True) or {};email=str(d.get("email","")).strip().lower()
    if not re.match(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",email):return err("Enter a valid email address.")
    row=insert("kwop_leads",{"user_id":session["user_id"],"email":email[:254],"name":str(d.get("name",""))[:120],"source":str(d.get("source","manual"))[:100],"status":"new","score":min(100,max(0,int(d.get("score",0)))),"notes":str(d.get("notes",""))[:2000]})
    return jsonify(lead=row),201
@app.patch("/api/leads/<lead_id>")
@signed
def leads_patch(lead_id):
    d=request.get_json(silent=True) or {};p={}
    if "status" in d:
        if d["status"] not in ("new","qualified","contacted","customer","lost"):return err("Invalid lead status.")
        p["status"]=d["status"]
    if "score" in d:
        try:p["score"]=min(100,max(0,int(d["score"])))
        except (ValueError,TypeError):return err("Score must be between 0 and 100.")
    if "notes" in d:p["notes"]=str(d["notes"])[:2000]
    if not p:return err("No changes supplied.")
    if not patch("kwop_leads",{"id":"eq."+lead_id,"user_id":"eq."+session["user_id"]},p):return err("Lead not found.",404)
    return jsonify(ok=True)
@app.delete("/api/leads/<lead_id>")
@signed
def leads_delete(lead_id):
    delete("kwop_leads",{"id":"eq."+lead_id,"user_id":"eq."+session["user_id"]})
    return jsonify(ok=True)

@app.get("/api/revenue")
@signed
def revenue_get():
    rows=select("kwop_revenue",{"select":"id,amount,currency,source,description,status,occurred_at","user_id":"eq."+session["user_id"],"order":"occurred_at.desc"})
    total=sum(float(x["amount"]) for x in rows if x["status"]=="paid")
    return jsonify(entries=rows,total=round(total,2),count=len(rows))
@app.post("/api/revenue")
@signed
def revenue_add():
    d=request.get_json(silent=True) or {}
    try:amount=round(float(d.get("amount",0)),2)
    except (ValueError,TypeError):return err("Enter a valid amount.")
    if amount<0 or amount>100000000:return err("Amount is out of range.")
    currency=str(d.get("currency","EUR")).upper()
    if currency not in ("EUR","USD","GBP"):return err("Unsupported currency.")
    status=str(d.get("status","paid"))
    if status not in ("paid","pending","refunded"):return err("Invalid revenue status.")
    row=insert("kwop_revenue",{"user_id":session["user_id"],"amount":amount,"currency":currency,"source":str(d.get("source","manual"))[:100],"description":str(d.get("description",""))[:250],"status":status})
    return jsonify(entry=row),201
@app.delete("/api/revenue/<entry_id>")
@signed
def revenue_delete(entry_id):
    delete("kwop_revenue",{"id":"eq."+entry_id,"user_id":"eq."+session["user_id"]})
    return jsonify(ok=True)

@app.get("/api/campaigns")
@signed
def campaigns_get():
    return jsonify(campaigns=select("kwop_campaigns",{"select":"id,name,subject,body,status,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))
@app.post("/api/campaigns")
@signed
def campaigns_add():
    d=request.get_json(silent=True) or {};name=str(d.get("name","")).strip()
    if not name:return err("Campaign name is required.")
    row=insert("kwop_campaigns",{"user_id":session["user_id"],"name":name[:150],"subject":str(d.get("subject",""))[:200],"body":str(d.get("body",""))[:10000],"status":"draft"})
    return jsonify(campaign=row),201
@app.patch("/api/campaigns/<campaign_id>")
@signed
def campaigns_patch(campaign_id):
    d=request.get_json(silent=True) or {};p={}
    for k,n in (("name",150),("subject",200),("body",10000)):
        if k in d:p[k]=str(d[k])[:n]
    if "status" in d:
        if d["status"] not in ("draft","ready","sent"):return err("Invalid campaign status.")
        p["status"]=d["status"]
    if not p:return err("No changes supplied.")
    if not patch("kwop_campaigns",{"id":"eq."+campaign_id,"user_id":"eq."+session["user_id"]},p):return err("Campaign not found.",404)
    return jsonify(ok=True)
@app.delete("/api/campaigns/<campaign_id>")
@signed
def campaigns_delete(campaign_id):
    delete("kwop_campaigns",{"id":"eq."+campaign_id,"user_id":"eq."+session["user_id"]})
    return jsonify(ok=True)

@app.post("/api/ai/campaign")
@signed
def campaign_ai():
    d=request.get_json(silent=True) or {}
    t,e=ai("Write a concise ethical opt-in email campaign for this product. Return JSON with subject, preview, body, CTA. Never invent customer claims or imply a recipient consented. Product: "+str(d.get("product",""))[:200]+". Audience: "+str(d.get("audience",""))[:500]+". Offer: "+str(d.get("offer",""))[:500],1800)
    if e:return err(e,503)
    try:return jsonify(campaign=parse_json(t))
    except Exception:return jsonify(campaign={"body":t})

@app.post("/api/ai/assistant")
@signed
def assistant_chat():
    d=request.get_json(silent=True) or {};message=str(d.get("message","")).strip()
    if not message:return err("Write a question first.")
    if len(message)>3000:return err("Message is too long.")
    history=select("kwop_ai_conversations",{"select":"role,content","user_id":"eq."+session["user_id"],"order":"created_at.desc","limit":"8"})
    context="\\n".join(x["role"]+": "+x["content"][:1200] for x in reversed(history))
    t,e=ai("You are a practical SaaS launch coach. Be specific, honest, avoid invented metrics and avoid claiming integrations are active.\\nRecent conversation:\\n"+context+"\\nUser: "+message,1800)
    if e:return err(e,503)
    insert("kwop_ai_conversations",{"user_id":session["user_id"],"role":"user","content":message})
    insert("kwop_ai_conversations",{"user_id":session["user_id"],"role":"assistant","content":t})
    return jsonify(reply=t)

@app.get("/api/score")
@signed
def score_get():
    uid=session["user_id"]
    projects=select("kwop_projects",{"select":"id,brief","user_id":"eq."+uid})
    features=select("kwop_features",{"select":"id,done","user_id":"eq."+uid})
    creators=select("kwop_creators",{"select":"id,status","user_id":"eq."+uid})
    leads=select("kwop_leads",{"select":"id,status","user_id":"eq."+uid})
    visits=select("kwop_public_events",{"select":"id,event_type","limit":"1000"}) if False else []
    parts={"product":min(25,10*bool(projects)+min(15,sum(1 for x in features if x["done"]))),"distribution":min(25,5*sum(1 for x in creators if x["status"] in ("Contacted","Interested","Posted"))),"validation":min(25,5*len(leads)),"execution":min(25,round(25*sum(1 for x in features if x["done"])/max(1,len(features))))}
    return jsonify(score=sum(parts.values()),breakdown=parts,label="Workspace readiness estimate, not a prediction of revenue.")


@app.errorhandler(RuntimeError)
def runtime_error(e):return jsonify(error=str(e)),502
@app.errorhandler(404)
def missing(e):return (jsonify(error="Not found."),404) if request.path.startswith("/api/") else ("Not found",404)
@app.errorhandler(413)
def big(e):return jsonify(error="Request too large."),413
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
