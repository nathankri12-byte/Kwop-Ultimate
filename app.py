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
        r=requests.post("https://generativelanguage.googleapis.com/v1beta/models/"+GEMINI_MODEL+":generateContent",headers={"x-goog-api-key":GEMINI_KEY,"Content-Type":"application/json"},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":tokens}},timeout=100)
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
def health():return jsonify(status="ok",supabase_configured=bool(SB_KEY),ai_configured=bool(GEMINI_KEY),secret_key_configured=bool(os.environ.get("SECRET_KEY")),model=GEMINI_MODEL)

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
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}",code):return "Tracking link not found",404
    public_headers={"apikey":SB_KEY,"Authorization":"Bearer "+SB_KEY,"Content-Type":"application/json"}
    try:
        r=requests.post(SB_URL+"/rest/v1/rpc/kwop_track_referral",headers=public_headers,json={"p_code":code},timeout=15)
        if not r.ok:return "Tracking temporarily unavailable",503
        rows=r.json()
        if not rows:return "Tracking link not found",404
        target=rows[0].get("target_url")
        try:dest=valid_url(target,False) if target else "/"
        except ValueError:dest="/"
        return redirect(dest,302)
    except (requests.RequestException,ValueError):return "Tracking temporarily unavailable",503
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


# --- Store catalogue, customer records and orders ---
@app.get("/api/store/products")
@signed
def store_products_get():
    rows=select("kwop_products",{"select":"id,launch_id,name,description,price,currency,stock,sku,image_url,checkout_url,active,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"})
    return jsonify(products=rows)

@app.post("/api/store/products")
@signed
def store_products_add():
    d=request.get_json(silent=True) or {};name=str(d.get("name","")).strip()
    if not name:return err("Product name is required.")
    try:price=round(float(d.get("price",0)),2)
    except (ValueError,TypeError):return err("Enter a valid price.")
    if price<0 or price>10000000:return err("Price is out of range.")
    currency=str(d.get("currency","EUR")).upper()
    if currency not in ("EUR","USD","GBP"):return err("Unsupported currency.")
    stock=None
    if d.get("stock") not in (None,"","unlimited"):
        try:stock=int(d.get("stock"))
        except (ValueError,TypeError):return err("Stock must be a non-negative whole number or unlimited.")
        if stock<0 or stock>10000000:return err("Stock is out of range.")
    launch_id=str(d.get("launch_id","")).strip()
    if not launch_id:return err("Choose a launch page for this product.")
    if not select("kwop_launches",{"select":"id","id":"eq."+launch_id,"user_id":"eq."+session["user_id"],"limit":"1"}):return err("Launch page not found.",404)
    try:
        image_url=valid_url(d.get("image_url",""))
        checkout_url=valid_url(d.get("checkout_url",""))
    except ValueError as e:return err(str(e))
    row=insert("kwop_products",{"user_id":session["user_id"],"launch_id":launch_id,"name":name[:150],"description":str(d.get("description",""))[:1200],"price":price,"currency":currency,"stock":stock,"sku":str(d.get("sku",""))[:80],"image_url":image_url,"checkout_url":checkout_url,"active":bool(d.get("active",True))})
    return jsonify(product=row),201

@app.patch("/api/store/products/<product_id>")
@signed
def store_products_patch(product_id):
    d=request.get_json(silent=True) or {};p={}
    for k,n in (("name",150),("description",1200),("sku",80)):
        if k in d:p[k]=str(d[k]).strip()[:n]
    if "price" in d:
        try:p["price"]=round(float(d["price"]),2)
        except (ValueError,TypeError):return err("Enter a valid price.")
        if p["price"]<0 or p["price"]>10000000:return err("Price is out of range.")
    if "currency" in d:
        p["currency"]=str(d["currency"]).upper()
        if p["currency"] not in ("EUR","USD","GBP"):return err("Unsupported currency.")
    if "stock" in d:
        if d["stock"] in (None,"","unlimited"):p["stock"]=None
        else:
            try:p["stock"]=int(d["stock"])
            except (ValueError,TypeError):return err("Invalid stock.")
            if p["stock"]<0:return err("Stock cannot be negative.")
    for k in ("image_url","checkout_url"):
        if k in d:
            try:p[k]=valid_url(d[k])
            except ValueError as e:return err(str(e))
    if "active" in d:p["active"]=bool(d["active"])
    if not p:return err("No changes supplied.")
    rows=patch("kwop_products",{"id":"eq."+product_id,"user_id":"eq."+session["user_id"]},p)
    if not rows:return err("Product not found.",404)
    return jsonify(product=rows[0])

@app.delete("/api/store/products/<product_id>")
@signed
def store_products_delete(product_id):
    delete("kwop_products",{"id":"eq."+product_id,"user_id":"eq."+session["user_id"]})
    return jsonify(ok=True)

@app.get("/api/store/orders")
@signed
def store_orders_get():
    rows=select("kwop_orders",{"select":"id,launch_id,product_id,customer_id,customer_name,customer_email,quantity,unit_price,total,currency,status,payment_reference,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc","limit":"500"})
    return jsonify(orders=rows)

@app.patch("/api/store/orders/<order_id>")
@signed
def store_orders_patch(order_id):
    d=request.get_json(silent=True) or {};p={}
    if "status" in d:
        if d["status"] not in ("pending","paid","fulfilled","cancelled"):return err("Invalid order status.")
        existing=select("kwop_orders",{"select":"status,product_id,quantity","id":"eq."+order_id,"user_id":"eq."+session["user_id"],"limit":"1"})
        if existing and d["status"]=="cancelled" and existing[0]["status"]!="cancelled" and existing[0].get("product_id"):
            product=select("kwop_products",{"select":"stock","id":"eq."+existing[0]["product_id"],"user_id":"eq."+session["user_id"],"limit":"1"})
            if product and product[0].get("stock") is not None:patch("kwop_products",{"id":"eq."+existing[0]["product_id"],"user_id":"eq."+session["user_id"]},{"stock":int(product[0]["stock"])+int(existing[0]["quantity"])})
        p["status"]=d["status"]
    if "payment_reference" in d:p["payment_reference"]=str(d["payment_reference"])[:180]
    if not p:return err("No changes supplied.")
    rows=patch("kwop_orders",{"id":"eq."+order_id,"user_id":"eq."+session["user_id"]},p)
    if not rows:return err("Order not found.",404)
    return jsonify(order=rows[0])

@app.get("/api/store/customers")
@signed
def store_customers_get():
    rows=select("kwop_customers",{"select":"id,name,email,phone,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc","limit":"500"})
    return jsonify(customers=rows)

@app.post("/api/public-order")
def public_order():
    if not SB_KEY:return err("Ordering is not configured.",503)
    d=request.get_json(silent=True) or {}
    product_id=str(d.get("product_id",""))
    if not re.fullmatch(r"[0-9a-fA-F-]{36}",product_id):return err("Invalid product.")
    try:quantity=int(d.get("quantity",1))
    except (ValueError,TypeError):return err("Invalid quantity.")
    headers={"apikey":SB_KEY,"Authorization":"Bearer "+SB_KEY,"Content-Type":"application/json"}
    try:
        r=requests.post(SB_URL+"/rest/v1/rpc/kwop_create_public_order",headers=headers,json={"p_product_id":product_id,"p_customer_name":str(d.get("name",""))[:120],"p_customer_email":str(d.get("email",""))[:254],"p_quantity":quantity},timeout=20)
        if not r.ok:
            try:message=r.json().get("message") or r.json().get("details") or "Order could not be created."
            except ValueError:message="Order could not be created."
            return err(message,400)
        rows=r.json() or []
        if not rows:return err("Order could not be confirmed.",502)
        return jsonify(ok=True,order_id=rows[0].get("order_id"),total=rows[0].get("total")),201
    except requests.RequestException:return err("Ordering is temporarily unavailable.",503)

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
    if select("kwop_launches",{"select":"id","slug":"eq."+slug,"user_id":"eq."+session["user_id"],"limit":"1"}):slug=slug[:52]+"-"+secrets.token_hex(3)
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
    try:rows=patch("kwop_launches",{"id":"eq."+launch_id,"user_id":"eq."+session["user_id"]},p)
    except RuntimeError as e:
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():return err("That public page URL is already taken. Choose another slug.",409)
        raise
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
    products=select("kwop_products",{"select":"id,name,description,price,currency,stock,image_url,checkout_url","launch_id":"eq."+x["id"],"active":"eq.true","order":"created_at.asc"})
    headline=html_escape(str(copy.get("headline") or x["title"]))
    sub=html_escape(str(copy.get("subheadline") or x.get("description","")))
    body=html_escape(str(copy.get("body") or ""))
    pricing="".join('<article><h2>'+html_escape(str(p.get("name","Plan")))+ '</h2><strong>€'+html_escape(str(p.get("price",0)))+'/mo</strong><p>'+html_escape(str(p.get("description","")))+ '</p><button onclick="signalInterest()">Signal interest</button></article>' for p in plans)
    product_cards="".join('<article class="product">'+('<img src="'+html_escape(str(p.get("image_url")))+'" alt="" loading="lazy">' if p.get("image_url") else '')+'<h2>'+html_escape(str(p.get("name","Product")))+ '</h2><strong>'+html_escape(str(p.get("price",0)))+' '+html_escape(str(p.get("currency","EUR")))+'</strong><p>'+html_escape(str(p.get("description","")))+'</p>'+('<a class="buy" href="'+html_escape(str(p.get("checkout_url")))+'" target="_blank" rel="noopener">Buy now</a>' if p.get("checkout_url") else '<button class="request-order" data-id="'+html_escape(str(p.get("id")))+'">Request order</button>')+'</article>' for p in products)
    product_section=('<h2 class="section-title">Products</h2><section class="plans">'+product_cards+'</section>') if products else ""
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>"""+html_escape(x["title"])+"""</title><style>body{margin:0;background:#09111e;color:#f5f7fb;font:16px/1.6 system-ui}main{max-width:1000px;margin:10vh auto;padding:28px}.hero{text-align:center}.hero h1{font-size:clamp(38px,7vw,68px);line-height:1.04}.muted{color:#aab7c9}.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-top:24px}.section-title{margin:46px 0 0}article{background:#142239;border:1px solid #31425c;border-radius:16px;padding:22px}.product img{width:100%;max-height:170px;object-fit:cover;border-radius:10px;margin-bottom:12px}button,.buy{display:inline-block;background:#c5f36a;color:#10170a;border:0;border-radius:9px;padding:12px 18px;font-weight:700;cursor:pointer;text-decoration:none}.order{display:none;position:fixed;inset:0;background:#000b;place-items:center;padding:20px;z-index:10}.order form{max-width:420px;width:100%;background:#142239;border:1px solid #425574;border-radius:16px;padding:22px}.order input{display:block;width:100%;padding:12px;margin:9px 0;background:#09111e;border:1px solid #425574;border-radius:8px;color:white}.order .close{background:#263a56;color:white;margin-left:8px}.consent{position:fixed;bottom:16px;left:16px;right:16px;max-width:760px;margin:auto;background:#142239;border:1px solid #425574;border-radius:14px;padding:16px;box-shadow:0 12px 50px #0008;z-index:9}.consent p{margin:0 0 10px}.consent button+button{margin-left:8px;background:#263a56;color:white}</style><main><section class="hero"><h1>"""+headline+"""</h1><p class="muted">"""+sub+"""</p><p>"""+body+"""</p><button onclick="signalInterest('main_cta')">I'm interested</button><p id="feedback" class="muted"></p></section><section class="plans">"""+pricing+"""</section>""" + product_section + """</main><aside id="orderDialog" class="order"><form id="orderForm"><h2>Request this product</h2><p class="muted">The seller will receive your order request. Payment is not processed on this page.</p><input id="orderName" placeholder="Your name" maxlength="120" required><input id="orderEmail" type="email" placeholder="Email address" maxlength="254" required><input id="orderQuantity" type="number" min="1" max="50" value="1" required><input id="orderProduct" type="hidden"><p id="orderMessage" class="muted"></p><button type="submit">Send order request</button><button type="button" class="close" onclick="closeOrder()">Cancel</button></form></aside><aside id="analyticsConsent" class="consent"><p><b>Anonymous analytics</b><br>With your consent, this page records anonymous page views, button clicks and scroll positions to create a visitor journey. It never records keystrokes or form contents.</p><button id="acceptAnalytics">Accept analytics</button><button id="declineAnalytics">Decline</button></aside><script>
const launchId="""+json.dumps(x["id"])+""";let sk=sessionStorage.getItem('kwop_public_session');if(!sk){sk=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random());sessionStorage.setItem('kwop_public_session',sk)}
let analyticsAllowed=localStorage.getItem('kwop_analytics_consent')==='yes';if(analyticsAllowed)document.getElementById('analyticsConsent').remove();
function replayEvent(type,label,value,metadata){fetch('/api/public-replay-event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({launch_id:launchId,session_key:sk,event_type:type,label:label||'',event_value:value,page_path:location.pathname,metadata:metadata||{}})}).catch(()=>{})}
function track(type,label){if(!analyticsAllowed)return;fetch('/api/public-event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({launch_id:launchId,session_key:sk,event_type:type,label:label||'',page_path:location.pathname})}).catch(()=>{});replayEvent(type==='page_view'?'page_view':'cta_click',label||'',null,{viewport_width:window.innerWidth})}
function signalInterest(label){track('cta_click',label);document.getElementById('feedback').textContent='Thanks. This demo records anonymous interest only; it does not create an account or take payment.'}
document.addEventListener('click',e=>{if(!analyticsAllowed||e.target.closest('#analyticsConsent,#orderDialog'))return;const el=e.target.closest('a,button,[role="button"]');if(!el||el.matches('input,textarea,select'))return;const label=(el.getAttribute('aria-label')||el.innerText||el.tagName||'').trim().slice(0,100);replayEvent('click',label,null,{element_tag:el.tagName.toLowerCase(),viewport_width:window.innerWidth})});
let replayScrollTimer=null;window.addEventListener('scroll',()=>{if(!analyticsAllowed||replayScrollTimer!==null)return;replayScrollTimer=setTimeout(()=>{replayEvent('scroll','',window.scrollY,{scroll_y:window.scrollY,viewport_width:window.innerWidth});replayScrollTimer=null},700)},{passive:true});
function closeOrder(){document.getElementById('orderDialog').style.display='none'}
document.querySelectorAll('.request-order').forEach(b=>b.onclick=()=>{document.getElementById('orderProduct').value=b.dataset.id;document.getElementById('orderMessage').textContent='';document.getElementById('orderDialog').style.display='grid'});
document.getElementById('orderForm').onsubmit=async(e)=>{e.preventDefault();const m=document.getElementById('orderMessage');m.textContent='Sending…';try{const r=await fetch('/api/public-order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:document.getElementById('orderProduct').value,name:document.getElementById('orderName').value,email:document.getElementById('orderEmail').value,quantity:document.getElementById('orderQuantity').value})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Order failed');m.textContent='Order request received. Reference: '+d.order_id;document.getElementById('orderForm').reset()}catch(err){m.textContent=err.message}};
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


@app.post("/api/public-replay-event")
def public_replay_event():
    if not SB_KEY:return err("Visitor replay is not configured.",503)
    d=request.get_json(silent=True) or {}
    launch_id=str(d.get("launch_id",""))
    if not re.fullmatch(r"[0-9a-fA-F-]{36}",launch_id):return err("Invalid launch.")
    session_key=str(d.get("session_key",""))
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}",session_key):return err("Invalid session.")
    event_type=str(d.get("event_type",""))
    if event_type not in ("page_view","click","cta_click","scroll"):return err("Invalid replay event.")
    metadata=d.get("metadata") if isinstance(d.get("metadata"),dict) else {}
    safe_metadata={"scroll_y":max(0,min(100000,int(metadata.get("scroll_y",0) or 0))) if str(metadata.get("scroll_y","")).isdigit() else None,"viewport_width":max(0,min(10000,int(metadata.get("viewport_width",0) or 0))) if str(metadata.get("viewport_width","")).isdigit() else None,"element_tag":str(metadata.get("element_tag",""))[:12]}
    headers={"apikey":SB_KEY,"Authorization":"Bearer "+SB_KEY,"Content-Type":"application/json"}
    payload={"p_launch_id":launch_id,"p_session_key":session_key,"p_event_type":event_type,"p_page_path":str(d.get("page_path","/"))[:200],"p_label":str(d.get("label",""))[:100],"p_event_value":d.get("event_value"),"p_metadata":safe_metadata}
    try:
        r=requests.post(SB_URL+"/rest/v1/rpc/kwop_record_public_replay",headers=headers,json=payload,timeout=15)
        if not r.ok:
            try:message=r.json().get("message") or "Replay event could not be saved."
            except ValueError:message="Replay event could not be saved."
            return err(message,400)
        return jsonify(ok=True)
    except requests.RequestException:return err("Visitor replay is temporarily unavailable.",503)

@app.get("/api/analytics")
@signed
def analytics_get():
    launches=select("kwop_launches",{"select":"id,slug,title,published","user_id":"eq."+session["user_id"]})
    ids=[x["id"] for x in launches]
    events=select("kwop_public_events",{"select":"launch_id,session_key,event_type,page_path,label,created_at","launch_id":"in.("+",".join(ids)+")","order":"created_at.desc","limit":"1000"}) if ids else []
    summary={"page_views":sum(1 for e in events if e["event_type"]=="page_view"),"cta_clicks":sum(1 for e in events if e["event_type"]=="cta_click"),"pricing_views":sum(1 for e in events if e["event_type"]=="pricing_view"),"unique_visitors":len(set(e["session_key"] for e in events))}
    names={x["id"]:x["title"] for x in launches}
    recent=[{**e,"launch_title":names.get(e["launch_id"],"Launch")} for e in events[:50]]
    sessions=select("kwop_sessions",{"select":"id,session_key,started_at,last_seen_at,duration_seconds,page_views,entry_page,exit_page,replay","user_id":"eq."+session["user_id"],"order":"started_at.desc","limit":"100"})
    return jsonify(summary=summary,launches=launches,recent=recent,sessions=sessions)

@app.get("/api/leads")
@signed
def leads_get():
    return jsonify(leads=select("kwop_leads",{"select":"id,email,name,source,status,score,notes,created_at","user_id":"eq."+session["user_id"],"order":"created_at.desc"}))
@app.post("/api/leads")
@signed
def leads_add():
    d=request.get_json(silent=True) or {};email=str(d.get("email","")).strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):return err("Enter a valid email address.")
    try:score=min(100,max(0,int(d.get("score",0))))
    except (ValueError,TypeError):return err("Score must be between 0 and 100.")
    row=insert("kwop_leads",{"user_id":session["user_id"],"email":email[:254],"name":str(d.get("name",""))[:120],"source":str(d.get("source","manual"))[:100],"status":"new","score":score,"notes":str(d.get("notes",""))[:2000]})
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
    context="\n".join(x["role"]+": "+x["content"][:1200] for x in reversed(history))
    uid=session["user_id"]
    projects=select("kwop_projects",{"select":"name","user_id":"eq."+uid})
    launches=select("kwop_launches",{"select":"title,published","user_id":"eq."+uid})
    leads=select("kwop_leads",{"select":"status,score","user_id":"eq."+uid})
    creators=select("kwop_creators",{"select":"status,views","user_id":"eq."+uid})
    revenue=select("kwop_revenue",{"select":"amount,currency,status","user_id":"eq."+uid})
    sessions=select("kwop_sessions",{"select":"page_views,duration_seconds,entry_page,exit_page","user_id":"eq."+uid,"order":"started_at.desc","limit":"50"})
    paid=sum(float(x.get("amount") or 0) for x in revenue if x.get("status")=="paid")
    workspace_context="\nWorkspace data (counts and manually recorded values, not forecasts): projects="+str(len(projects))+", launch_pages="+str(len(launches))+", published_pages="+str(sum(1 for x in launches if x.get("published")))+", leads="+str(len(leads))+", creators="+str(len(creators))+", recorded_paid_revenue="+str(round(paid,2))+" (currencies may be mixed), visitor_sessions="+str(len(sessions))+", replayed_page_views="+str(sum(int(x.get("page_views") or 0) for x in sessions))+"."
    t,e=ai("You are a practical SaaS launch coach. Use the workspace data below to give specific, honest next steps. Never invent metrics or claim integrations are active."+workspace_context+"\nRecent conversation:\n"+context+"\nUser: "+message,2200)
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



@app.post("/api/ai/build-business")
@signed
def build_business():
    d=request.get_json(silent=True) or {}
    kind=str(d.get("kind","SaaS")).strip()[:40]
    interests=str(d.get("interests","")).strip()[:500]
    audience=str(d.get("audience","")).strip()[:500]
    idea=str(d.get("idea","")).strip()[:1800]
    avoid=str(d.get("avoid","")).strip()[:500]
    hours=str(d.get("hours","60")).strip()[:30]
    budget=str(d.get("budget","0")).strip()[:30]
    skills=str(d.get("skills","Beginner")).strip()[:80]
    goal=str(d.get("goal","Build my first online business")).strip()[:250]
    if not (interests or idea):
        return err("Tell us what you are interested in or describe an idea first.")
    prompt=f"""You are an expert AI business builder. Turn this person's answers into ONE concrete, realistic, launchable {kind} business. Do not give a list of ideas. Pick the strongest idea and make the decisions for them.
Answers:
Interests: {interests}
Target audience: {audience or "Choose a specific paying audience"}
Idea or interests: {idea or "No fixed idea; invent one from the interests"}
Avoid: {avoid or "No stated restrictions"}
Time available: {hours} minutes per day
Budget: {budget}
Experience: {skills}
Goal: {goal}

Return ONLY valid JSON with this exact structure:
{{
"name":"short memorable brand name",
"slug":"short-url-slug",
"tagline":"short promise",
"one_liner":"what the business does in one sentence",
"audience":"specific audience",
"problem":"urgent problem in plain language",
"solution":"clear product solution",
"features":[{{"name":"feature name","description":"benefit"}}],\n"products":[{{"name":"product name","description":"what buyer receives","price":19,"currency":"EUR","sku":"optional short sku"}}],
"pricing":[{{"name":"Free","price":0,"description":"what is included"}},{{"name":"Pro","price":9,"description":"what is included"}}],
"landing":{{"headline":"strong customer-facing headline","subheadline":"clear explanation","body":"2-4 short paragraphs with benefits and how it works"}},
"brand":{{"style":"visual direction","primary":"hex colour","accent":"hex colour"}},
"marketing":{{"positioning":"one sentence","video_hooks":["three original short-video hooks"],"channels":["three realistic channels"]}},
"first_7_days":["seven small specific tasks"],
"brief":"detailed product specification in Markdown, including MVP scope, user journey, page structure, pricing hypothesis, validation and realistic risks"
}}
Make the MVP possible for a solo beginner with the stated time and budget. For Online store, create 3-5 concrete product suggestions and explain inventory/delivery assumptions; for Digital product, create 1-3 digital products; for SaaS / web app, return an empty products array and focus on the software MVP. Do not silently switch the requested business type. Do not present AI product suggestions as real stock or fulfilled orders. Do not promise income, guaranteed customers or market validation. No fake testimonials, statistics or invented competitors. Features must be specific. Keep pricing realistic. If budget is zero, use free tools and a waitlist instead of paid infrastructure. Do not suggest regulated or age-restricted products. Output valid JSON only."""
    t,e=ai(prompt,6500)
    if e:return err(e,503)
    try:
        b=parse_json(t)
        required=("name","slug","tagline","one_liner","audience","problem","solution","features","pricing","landing","brief")
        if not isinstance(b,dict) or any(not b.get(k) for k in required):raise ValueError("Incomplete business plan.")
        name=str(b["name"])[:100]
        slug=re.sub(r"[^a-z0-9]+","-",str(b["slug"]).lower()).strip("-")[:42] or "my-business"
        # Published slugs are globally unique; add a random suffix to avoid cross-account collisions.
        slug=slug+"-"+secrets.token_hex(3)
        features=b["features"][:6] if isinstance(b["features"],list) else []
        pricing=b["pricing"][:3] if isinstance(b["pricing"],list) else []
        plans=[]
        for p in pricing:
            if not isinstance(p,dict):continue
            try:price=max(0,float(p.get("price",0)))
            except (TypeError,ValueError):price=0
            plans.append({"name":str(p.get("name","Plan"))[:60],"price":price,"description":str(p.get("description",""))[:300]})
        if not plans:plans=[{"name":"Free","price":0,"description":"Early access and core features"}]
        landing=b.get("landing") if isinstance(b.get("landing"),dict) else {}
        copy={"headline":str(landing.get("headline") or b["tagline"])[:220],"subheadline":str(landing.get("subheadline") or b["one_liner"])[:500],"body":str(landing.get("body") or b["solution"])[:3000]}
        brief=str(b["brief"])[:18000]
        project=insert("kwop_projects",{"user_id":session["user_id"],"name":name,"brief":brief})
        launch=insert("kwop_launches",{"user_id":session["user_id"],"slug":slug,"title":name,"description":str(b["one_liner"])[:1800],"landing_copy":copy,"plans":plans,"published":True})
        store_products=[]
        for product in (b.get("products") if isinstance(b.get("products"),list) else [])[:5]:
            if not isinstance(product,dict) or not str(product.get("name","")).strip():continue
            try:product_price=max(0,round(float(product.get("price",0)),2))
            except (TypeError,ValueError):product_price=0
            currency=str(product.get("currency","EUR")).upper()
            if currency not in ("EUR","USD","GBP"):currency="EUR"
            store_products.append(insert("kwop_products",{"user_id":session["user_id"],"launch_id":launch["id"],"name":str(product.get("name"))[:150],"description":str(product.get("description",""))[:1200],"price":product_price,"currency":currency,"sku":str(product.get("sku",""))[:80],"active":False}))
        result={"business":{"name":name,"slug":slug,"tagline":str(b["tagline"])[:250],"one_liner":str(b["one_liner"])[:600],"audience":str(b["audience"])[:500],"problem":str(b["problem"])[:1200],"solution":str(b["solution"])[:1500],"features":features,"products":store_products,"pricing":plans,"landing":copy,"brand":b.get("brand",{}),"marketing":b.get("marketing",{}),"first_7_days":b.get("first_7_days",[])[:7],"brief":brief},"project_id":project.get("id") if project else None,"launch_id":launch.get("id") if launch else None,"url":request.host_url.rstrip("/")+"/p/"+slug}
        return jsonify(result),201
    except (ValueError,TypeError,KeyError) as ex:
        return err("The AI could not create a complete business plan. Please try again. "+str(ex),502)

@app.errorhandler(RuntimeError)
def runtime_error(e):return jsonify(error=str(e)),502
@app.errorhandler(404)
def missing(e):return (jsonify(error="Not found."),404) if request.path.startswith("/api/") else ("Not found",404)
@app.errorhandler(413)
def big(e):return jsonify(error="Request too large."),413
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
