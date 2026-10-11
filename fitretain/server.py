#!/usr/bin/env python3
"""FitRetain: lightweight multi-tenant gym retention SaaS (Python stdlib only)."""
import http.server, socketserver, sqlite3, json, os, secrets, hashlib, hmac, urllib.parse, datetime, re, threading, time
from http.cookies import SimpleCookie

BASE=os.path.dirname(os.path.abspath(__file__)); DB=os.environ.get('FITRETAIN_DB',os.path.join(BASE,'fitretain.db')); PORT=int(os.environ.get('PORT','8080'))
SESSIONS={}; LOCK=threading.RLock(); SESSION_TTL=60*60*12
RATE_LIMITS={}


def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def generate_token(length=22):
  alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'; return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_checkin_code(length=6):
  return ''.join(secrets.choice('0123456789') for _ in range(length))

def normalize_phone(value):
  return re.sub(r'\D','',str(value or ''))


def public_base_url(host=None):
  if os.environ.get('PUBLIC_BASE_URL'):
   return os.environ['PUBLIC_BASE_URL'].rstrip('/')
  if host:
   if '://' in host: return host.rstrip('/')
   proto='https' if os.environ.get('USE_HTTPS')=='1' else 'http'
   host=host.replace('localhost','127.0.0.1')
   return f'{proto}://{host.rstrip('/')}'
  return f'http://127.0.0.1:{PORT}'


def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS gyms(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, qr_token TEXT, qr_generated_at TEXT, created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY, gym_id INTEGER NOT NULL REFERENCES gyms(id) ON DELETE CASCADE, name TEXT NOT NULL, phone TEXT NOT NULL, plan TEXT NOT NULL, start_date TEXT NOT NULL, expiry_date TEXT NOT NULL, fee INTEGER NOT NULL CHECK(fee>=0), status TEXT NOT NULL DEFAULT 'active', notes TEXT NOT NULL DEFAULT '', checkin_code TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY, gym_id INTEGER NOT NULL REFERENCES gyms(id) ON DELETE CASCADE, member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE, attended_on TEXT NOT NULL, demo INTEGER NOT NULL DEFAULT 0, UNIQUE(member_id,attended_on));
  CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, gym_id INTEGER NOT NULL REFERENCES gyms(id) ON DELETE CASCADE, member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE, amount INTEGER NOT NULL CHECK(amount>=0), paid_on TEXT NOT NULL, method TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS members_gym_expiry ON members(gym_id,expiry_date); CREATE INDEX IF NOT EXISTS payments_gym_date ON payments(gym_id,paid_on); CREATE INDEX IF NOT EXISTS attendance_gym_date ON attendance(gym_id,attended_on);''')
  gym_cols={row['name'] for row in c.execute('PRAGMA table_info(gyms)')}
  member_cols={row['name'] for row in c.execute('PRAGMA table_info(members)')}
  if 'qr_token' not in gym_cols:
   c.execute('ALTER TABLE gyms ADD COLUMN qr_token TEXT')
  if 'qr_generated_at' not in gym_cols:
   c.execute('ALTER TABLE gyms ADD COLUMN qr_generated_at TEXT')
  if 'checkin_code' not in member_cols:
   c.execute('ALTER TABLE members ADD COLUMN checkin_code TEXT NOT NULL DEFAULT ""')
  try:
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_gyms_qr_token ON gyms(qr_token) WHERE qr_token IS NOT NULL AND qr_token != ""')
  except sqlite3.IntegrityError:
   pass
  for row in c.execute('SELECT id FROM gyms WHERE qr_token IS NULL OR qr_token=""'):
   token=generate_token(); c.execute('UPDATE gyms SET qr_token=? WHERE id=?',(token,row['id']))
  for row in c.execute('SELECT id FROM members WHERE checkin_code IS NULL OR checkin_code=""'):
   c.execute('UPDATE members SET checkin_code=? WHERE id=?',(generate_checkin_code(),row['id']))

def now(): return datetime.datetime.now().astimezone().isoformat(timespec='seconds')
def today(): return datetime.date.today().isoformat()
def password_hash(pw,salt=None):
 salt=salt or secrets.token_bytes(16); return salt.hex()+':'+hashlib.pbkdf2_hmac('sha256',pw.encode(),salt,240000).hex()
def verify(pw,stored):
 try: salt,expected=stored.split(':'); actual=hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),240000).hex(); return hmac.compare_digest(actual,expected)
 except Exception:return False


def check_rate_limit(ip, route, max_calls=12, seconds=60):
  now_ts=time.monotonic(); key=(ip,route); bucket=RATE_LIMITS.setdefault(key,[])
  bucket[:] = [ts for ts in bucket if now_ts-ts < seconds]
  if len(bucket) >= max_calls:
   return False
  bucket.append(now_ts); return True


class Handler(http.server.BaseHTTPRequestHandler):
 server_version='FitRetain/1.0'
 def log_message(self,*a): pass
 def send_json(self,obj,status=200):
  b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b)
 def body(self):
  try:
   n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(min(n,1000000)) or b'{}')
  except Exception: raise ValueError('Invalid JSON request body')
 def session(self):
  ck=SimpleCookie(self.headers.get('Cookie','')); sid=ck.get('fitretain_session')
  if not sid:return None
  with LOCK:
   v=SESSIONS.get(sid.value)
   if not v:return None
   if v['expires']<datetime.datetime.now().timestamp(): SESSIONS.pop(sid.value,None); return None
   v['expires']=datetime.datetime.now().timestamp()+SESSION_TTL; return {'id':v['gym_id'],'name':v['name'],'email':v['email'],'csrf':v['csrf'],'sid':sid.value}
 def auth(self):
  s=self.session()
  if not s:self.send_json({'error':'Please sign in to continue.'},401); return None
  if self.command not in ('GET','HEAD') and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']): self.send_json({'error':'Security token expired. Refresh and try again.'},403); return None
  return s
 
 def build_public_url(self, token):
  host=self.headers.get('Host') or f'127.0.0.1:{PORT}'
  base=public_base_url(host)
  return f'{base.rstrip("/")}/checkin/{token}'
 
 def render_public_checkin_page(self, gym_name, token):
  safe_name = str(gym_name or 'Gym').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
  token_12 = token[:12]
  return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_name} Check-in</title>
  <style>
    * {{box-sizing:border-box}}
    body {{margin:0;font-family:Arial,sans-serif;background:linear-gradient(180deg,#f5f7fb 0%,#edf6e7 100%);color:#182230}}
    .wrap {{max-width:480px;margin:56px auto;padding:20px}}
    .card {{background:#fff;border-radius:18px;box-shadow:0 18px 45px rgba(20,33,28,.12);padding:28px 22px}}
    h1 {{margin:0 0 10px;font-size:28px}}
    .subtitle {{color:#5d6b7b;margin:0 0 18px;line-height:1.5}}
    .field {{display:flex;flex-direction:column;gap:8px;margin-top:16px}}
    label {{font-size:13px;font-weight:700;color:#243742}}
    input {{padding:12px 14px;border:1px solid #dfe6ed;border-radius:10px;font-size:16px}}
    button {{margin-top:20px;width:100%;border:0;border-radius:12px;padding:14px 18px;background:#14211c;color:#f4f9ef;font-weight:700;font-size:16px}}
    .badge {{background:#eaf7d8;color:#2c5d1a;padding:8px 12px;border-radius:999px;display:inline-block;font-size:12px;font-weight:700;margin-bottom:12px}}
    .notice {{margin-top:16px;padding:12px 14px;border-radius:10px;background:#f5f7fb;color:#3a4d5d;font-size:13px;line-height:1.5}}
    #message {{margin-top:14px;padding:12px 14px;border-radius:10px;display:none;font-size:14px}}
    .success {{display:block;background:#e8f8ec;color:#175a35}}
    .error {{display:block;background:#fdecec;color:#8b262f}}
    .warning {{display:block;background:#fff4db;color:#7a4e00}}
    .meta {{font-size:12px;color:#66768a;margin-top:10px}}
    .tiny {{font-size:11px;color:#5b6876;margin-top:18px;line-height:1.5}}
    @media(max-width:480px) {{
      body {{overflow-x:hidden}}
      .wrap {{width:100%;margin:0 auto;padding:12px}}
      .card {{padding:22px 18px;border-radius:15px}}
      h1 {{font-size:clamp(23px,7vw,28px);overflow-wrap:anywhere}}
      .subtitle {{font-size:14px}}
      input {{width:100%;min-height:46px}}
      button {{min-height:48px}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="badge">FitRetain Smart Check-in</div>
      <h1>{safe_name}</h1>
      <p class="subtitle">Check in from your phone. Your gym uses a secure member verification code for this public check-in point.</p>
      <form id="checkinForm">
        <div class="field">
          <label for="phone">Registered mobile number</label>
          <input id="phone" name="phone" inputmode="tel" autocomplete="tel" maxlength="15" placeholder="e.g. 919876543210" required>
        </div>
        <div class="field">
          <label for="checkin_code">Member check-in code</label>
          <input id="checkin_code" name="checkin_code" inputmode="numeric" autocomplete="one-time-code" maxlength="12" placeholder="6-digit code" required>
        </div>
        <button type="submit">Check in</button>
      </form>
      <div id="message"></div>
      <div class="notice">This public check-in uses a secure member verification code instead of an SMS OTP flow because SMS delivery is not configured in this app. The code is provided by the gym and is checked server-side before attendance is recorded.</div>
      <div class="meta">Gym ID: {token_12}...</div>
      <div class="tiny">No app installation or login is required. Use the QR code at your gym entrance and complete the quick verification on your phone.</div>
    </div>
  </div>
  <script>
    const form = document.getElementById('checkinForm');
    const msg = document.getElementById('message');
    function show(type, text) {{
      msg.className = type;
      msg.textContent = text;
      msg.style.display = 'block';
    }}
    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      const payload = {{
        phone: document.getElementById('phone').value.trim(),
        checkin_code: document.getElementById('checkin_code').value.trim(),
        gym_token: '{token}'
      }};
      show('warning', 'Checking membership…');
      try {{
        const res = await fetch('/api/public/checkin', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        const data = await res.json();
        if (!res.ok) {{
          show('error', data.message || data.error || 'Unable to check in.');
          return;
        }}
        show('success', data.message || 'Check-in recorded successfully.');
        form.reset();
      }} catch (error) {{
        show('error', 'Something went wrong. Please try again in a minute.');
      }}
    }});
  </script>
</body>
</html>"""
 
 def do_GET(self):
  path=urllib.parse.urlparse(self.path).path
  if path=='/' or path=='/index.html':
   try: b=open(os.path.join(BASE,'index.html'),'rb').read()
   except: self.send_error(404); return
   self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
  if path.startswith('/checkin/'):
   token=path.split('/checkin/',1)[1].strip('/')
   if not token:
    self.send_error(404); return
   with db() as c:
    gym=c.execute('SELECT id,name,qr_token FROM gyms WHERE qr_token=?',(token,)).fetchone()
   if not gym:
    self.send_error(404); return
   page=self.render_public_checkin_page(gym['name'], token)
   b=page.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
  if path=='/api/me':
   s=self.session(); self.send_json({'user':({k:s[k] for k in ('id','name','email','csrf')} if s else None)}); return
  if path.startswith('/api/'):
   s=self.auth()
   if not s: return
   try:self.api_get(path,s)
   except Exception:self.send_json({'error':'Unable to load data. Please try again.'},500)
   return
  self.send_error(404)
 
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path
  try:d=self.body()
  except ValueError as e:self.send_json({'error':str(e)},400); return
  if path=='/api/public/checkin':
   self.public_checkin(d); return
  if path in ('/api/register','/api/login'):
   self.login_register(path,d); return
  if path=='/api/logout':
   s=self.auth()
   if not s: return
   with LOCK: SESSIONS.pop(s['sid'],None)
   self.send_response(200); self.send_header('Set-Cookie','fitretain_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{"ok":true}'); return
  s=self.auth()
  if not s: return
  try:self.api_post(path,s,d)
  except ValueError as e:self.send_json({'error':str(e)},400)
  except sqlite3.IntegrityError:self.send_json({'error':'That record conflicts with existing data. Check duplicate attendance or required fields.'},409)
  except Exception:self.send_json({'error':'Something went wrong while saving. Please try again.'},500)
 
 def login_register(self,path,d):
  email=str(d.get('email','')).strip().lower(); pw=str(d.get('password','')); name=str(d.get('name','')).strip(); gym=str(d.get('gym','')).strip()
  if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):self.send_json({'error':'Enter a valid email address.'},400); return
  if len(pw)<10:self.send_json({'error':'Password must be at least 10 characters.'},400); return
  with db() as c:
   if path.endswith('register'):
    if not name or not gym:self.send_json({'error':'Your name and gym name are required.'},400); return
    try:
     c.execute('INSERT INTO gyms(name,email,password_hash,qr_token,qr_generated_at,created_at) VALUES(?,?,?,?,?,?)',(gym,email,password_hash(pw),generate_token(),now(),now()))
     row=c.execute('SELECT * FROM gyms WHERE email=?',(email,)).fetchone()
    except sqlite3.IntegrityError:self.send_json({'error':'An account with this email already exists. Sign in instead.'},409); return
   else:
    row=c.execute('SELECT * FROM gyms WHERE email=?',(email,)).fetchone();
    if not row or not verify(pw,row['password_hash']):self.send_json({'error':'Email or password is incorrect.'},401); return
    if not row['qr_token']:
      token=generate_token(); c.execute('UPDATE gyms SET qr_token=? WHERE id=?',(token,row['id'])); row=c.execute('SELECT * FROM gyms WHERE id=?',(row['id'],)).fetchone()
   sid=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
   with LOCK:SESSIONS[sid]={'gym_id':row['id'],'name':row['name'],'email':row['email'],'csrf':csrf,'expires':datetime.datetime.now().timestamp()+SESSION_TTL}
  self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Set-Cookie',f'fitretain_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}'); self.end_headers(); self.wfile.write(json.dumps({'user':{'id':row['id'],'name':row['name'],'email':row['email'],'csrf':csrf}}).encode())
 
 def public_checkin(self,d):
  ip=self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[0].strip()
  if not check_rate_limit(ip,'/api/public/checkin',max_calls=20,seconds=60):
   self.send_json({'status':'error','message':'Too many check-in attempts. Please wait a moment and try again.'},429); return
  gym_token=str(d.get('gym_token','')).strip(); phone=normalize_phone(d.get('phone','')); code=str(d.get('checkin_code','')).strip()
  if not gym_token or not phone or not code:
   self.send_json({'status':'error','message':'Please provide your phone number and member check-in code.'},400); return
  with db() as c:
   gym=c.execute('SELECT * FROM gyms WHERE qr_token=?',(gym_token,)).fetchone();
   if not gym:
    self.send_json({'status':'error','message':'This QR code is not valid for a gym in FitRetain.'},404); return
   member=c.execute('SELECT * FROM members WHERE gym_id=? AND phone=?',(gym['id'],phone)).fetchone();
   if not member:
    self.send_json({'status':'error','message':'We could not match that phone number to an active member in this gym.'},401); return
   if member['checkin_code'] != code:
    self.send_json({'status':'error','message':'The membership check-in code is incorrect.'},401); return
   if member['status'] != 'active' or datetime.date.fromisoformat(member['expiry_date']) < datetime.date.today():
    self.send_json({'status':'error','message':'This membership has expired and cannot check in today.'},403); return
   existing=c.execute('SELECT 1 FROM attendance WHERE member_id=? AND attended_on=?',(member['id'],today())).fetchone();
   if existing:
    self.send_json({'status':'error','message':'This member already checked in today.'},409); return
   c.execute('INSERT INTO attendance(gym_id,member_id,attended_on) VALUES(?,?,?)',(gym['id'],member['id'],today()));
   self.send_json({'status':'success','message':f'{member["name"]} checked in successfully for {gym["name"]}.'})
 
 def api_get(self,path,s):
  q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); gym=s['id']
  with db() as c:
   if path=='/api/qr':
    row=c.execute('SELECT * FROM gyms WHERE id=?',(gym,)).fetchone();
    token=row['qr_token'] or generate_token();
    if not row['qr_token']:
      c.execute('UPDATE gyms SET qr_token=?,qr_generated_at=? WHERE id=?',(token,now(),gym))
    self.send_json({'token':token,'public_url':self.build_public_url(token),'gym_name':row['name']}); return
   if path=='/api/members':
    rows=c.execute('SELECT m.*, (SELECT MAX(attended_on) FROM attendance a WHERE a.member_id=m.id AND a.gym_id=m.gym_id) last_visit, (SELECT COUNT(*) FROM attendance a WHERE a.member_id=m.id AND a.gym_id=m.gym_id AND attended_on>=?) visits_30 FROM members m WHERE gym_id=? ORDER BY expiry_date',( (datetime.date.today()-datetime.timedelta(days=30)).isoformat(),gym)).fetchall(); self.send_json({'members':[dict(r) for r in rows]}); return
   if path=='/api/payments':
    rows=c.execute('SELECT p.*,m.name member_name FROM payments p JOIN members m ON m.id=p.member_id AND m.gym_id=p.gym_id WHERE p.gym_id=? ORDER BY paid_on DESC,p.id DESC LIMIT 500',(gym,)).fetchall(); self.send_json({'payments':[dict(r) for r in rows]}); return
   if path=='/api/attendance':
    day=q.get('date',[today()])[0]; rows=c.execute('SELECT a.*,m.name member_name,m.phone FROM attendance a JOIN members m ON m.id=a.member_id AND m.gym_id=a.gym_id WHERE a.gym_id=? AND attended_on=? ORDER BY a.id DESC',(gym,day)).fetchall(); self.send_json({'date':day,'attendance':[dict(r) for r in rows]}); return
   if path=='/api/dashboard':
    d=today(); soon=(datetime.date.today()+datetime.timedelta(days=7)).isoformat(); month=d[:7]
    total=c.execute('SELECT COUNT(*) n FROM members WHERE gym_id=? AND status="active" AND expiry_date>=?',(gym,d)).fetchone()['n']; members=c.execute('SELECT * FROM members WHERE gym_id=?',(gym,)).fetchall(); pay=c.execute('SELECT COALESCE(SUM(amount),0) n FROM payments WHERE gym_id=? AND substr(paid_on,1,7)=?',(gym,month)).fetchone()['n']; alltime=c.execute('SELECT COALESCE(SUM(amount),0) n FROM payments WHERE gym_id=?',(gym,)).fetchone()['n']; exp=c.execute('SELECT COUNT(*) n FROM members WHERE gym_id=? AND expiry_date BETWEEN ? AND ?',(gym,d,soon)).fetchone()['n']; expired=c.execute('SELECT COUNT(*) n FROM members WHERE gym_id=? AND expiry_date<?',(gym,d)).fetchone()['n']; at_risk=[]
    for m in members:
     last=c.execute('SELECT MAX(attended_on) x FROM attendance WHERE gym_id=? AND member_id=?',(gym,m['id'])).fetchone()['x']; days=(datetime.date.today()-datetime.date.fromisoformat(last)).days if last else 999
     if m['expiry_date']>=d and (days>=7 or (datetime.date.fromisoformat(m['expiry_date'])-datetime.date.today()).days<=7): at_risk.append({'id':m['id'],'name':m['name'],'phone':m['phone'],'expiry_date':m['expiry_date'],'days_absent':days,'reason':'No visit for '+(str(days)+' days' if days<999 else 'no recorded visits') if days>=7 else 'Membership expires soon'})
    daily=[]
    for i in range(6,-1,-1):
     dt=(datetime.date.today()-datetime.timedelta(days=i)).isoformat(); daily.append({'date':dt,'count':c.execute('SELECT COUNT(*) n FROM attendance WHERE gym_id=? AND attended_on=?',(gym,dt)).fetchone()['n']})
    revenue=[]
    for i in range(5,-1,-1):
     dt=(datetime.date.today().replace(day=1)-datetime.timedelta(days=1)); y=dt.year; mo=dt.month
     for _ in range(i): dt=dt.replace(day=1)-datetime.timedelta(days=1); y=dt.year; mo=dt.month
     key=f'{y:04d}-{mo:02d}'; revenue.append({'month':key,'amount':c.execute('SELECT COALESCE(SUM(amount),0) n FROM payments WHERE gym_id=? AND substr(paid_on,1,7)=?',(gym,key)).fetchone()['n']})
    self.send_json({'stats':{'active':total,'members':len(members),'revenue_month':pay,'revenue_total':alltime,'expiring':exp,'expired':expired,'at_risk':len(at_risk)},'at_risk':at_risk[:10],'attendance_week':daily,'revenue_months':revenue,'demo_notice':any(m['demo'] for m in members)}); return
   self.send_json({'error':'Not found'},404)
 
 def api_post(self,path,s,d):
  gym=s['id']; required=lambda k: str(d.get(k,'')).strip()
  with db() as c:
   if path=='/api/members':
    name=required('name'); phone=normalize_phone(required('phone')); plan=required('plan'); start=required('start_date'); expiry=required('expiry_date')
    if not name or len(name)>100:raise ValueError('Member name is required (maximum 100 characters).')
    if len(phone)<10 or len(phone)>15:raise ValueError('Enter a valid phone number with country code if needed.')
    if not plan:raise ValueError('Select a membership plan.')
    try: sd=datetime.date.fromisoformat(start); ed=datetime.date.fromisoformat(expiry); fee=int(d.get('fee',0))
    except Exception:raise ValueError('Enter valid membership dates and fee.')
    if ed<sd or fee<0 or fee>100000000:raise ValueError('Expiry must be on or after the start date; fee must be non-negative.')
    checkin_code = str(d.get('checkin_code') or generate_checkin_code())
    c.execute('INSERT INTO members(gym_id,name,phone,plan,start_date,expiry_date,fee,status,notes,checkin_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(gym,name,phone,plan,start,expiry,fee,'active',required('notes')[:1000],checkin_code,now())); self.send_json({'ok':True,'id':c.execute('SELECT last_insert_rowid() id').fetchone()['id']}); return
   if path.startswith('/api/members/') and path.endswith('/renew'):
    try:mid=int(path.split('/')[-2]); expiry=datetime.date.fromisoformat(required('expiry_date')); fee=int(d.get('fee',0)); paid=datetime.date.fromisoformat(required('paid_on') or today())
    except Exception:raise ValueError('Enter a valid renewal expiry date, amount and payment date.')
    m=c.execute('SELECT * FROM members WHERE id=? AND gym_id=?',(mid,gym)).fetchone();
    if not m: self.send_json({'error':'Member not found.'},404); return
    if expiry<=datetime.date.fromisoformat(m['expiry_date']) or fee<0:raise ValueError('New expiry must be later than current expiry and amount cannot be negative.')
    c.execute('UPDATE members SET expiry_date=?,status="active",plan=? WHERE id=? AND gym_id=?',(expiry.isoformat(),required('plan') or m['plan'],mid,gym))
    if fee>0:c.execute('INSERT INTO payments(gym_id,member_id,amount,paid_on,method,note,created_at) VALUES(?,?,?,?,?,?,?)',(gym,mid,fee,paid.isoformat(),required('method') or 'Cash','Membership renewal',now()))
    self.send_json({'ok':True}); return
   if path.startswith('/api/members/') and path.endswith('/delete'):
    try:mid=int(path.split('/')[-2])
    except:raise ValueError('Invalid member.')
    cur=c.execute('DELETE FROM members WHERE id=? AND gym_id=?',(mid,gym));
    if not cur.rowcount:self.send_json({'error':'Member not found.'},404); return
    self.send_json({'ok':True}); return
   if path=='/api/attendance':
    try:mid=int(d.get('member_id')); day=datetime.date.fromisoformat(required('date') or today()).isoformat()
    except:raise ValueError('Select a valid member and date.')
    if day>today():raise ValueError('Attendance date cannot be in the future.')
    if not c.execute('SELECT id FROM members WHERE id=? AND gym_id=?',(mid,gym)).fetchone():raise ValueError('Choose a member from your gym.')
    c.execute('INSERT INTO attendance(gym_id,member_id,attended_on) VALUES(?,?,?)',(gym,mid,day)); self.send_json({'ok':True}); return
   if path=='/api/payments':
    try:mid=int(d.get('member_id')); amount=int(d.get('amount')); day=datetime.date.fromisoformat(required('paid_on') or today()).isoformat()
    except:raise ValueError('Select a member and enter a valid amount and payment date.')
    method=required('method')
    if amount<=0 or amount>100000000 or day>today():raise ValueError('Amount must be positive and payment date cannot be in the future.')
    if method not in ('Cash','UPI','Card','Bank transfer','Other'):raise ValueError('Select a valid payment method.')
    if not c.execute('SELECT id FROM members WHERE id=? AND gym_id=?',(mid,gym)).fetchone():raise ValueError('Choose a member from your gym.')
    c.execute('INSERT INTO payments(gym_id,member_id,amount,paid_on,method,note,created_at) VALUES(?,?,?,?,?,?,?)',(gym,mid,amount,day,method,required('note')[:300],now())); self.send_json({'ok':True}); return
   self.send_json({'error':'Not found'},404)

init()
with socketserver.ThreadingTCPServer(('0.0.0.0',PORT),Handler) as httpd:
 httpd.daemon_threads=True; print(f'FitRetain running at http://127.0.0.1:{PORT}'); httpd.serve_forever()
