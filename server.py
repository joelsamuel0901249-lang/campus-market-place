"""Campus freelance marketplace MVP. Run with: python server.py"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import hashlib, hmac, json, math, os, secrets, sqlite3, time
from contextlib import contextmanager
from datetime import date

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("CAMPUS_MARKET_DB", ROOT / "campus_market.sqlite3"))
PORT = int(os.environ.get("PORT", "8000"))
CATEGORIES = ["Design", "Writing", "Tutoring", "Development", "Photography", "Events", "Other"]
STATUSES = ["open", "in progress", "completed", "cancelled"]

@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE, password TEXT, name TEXT, bio TEXT DEFAULT '', skills TEXT DEFAULT '', links TEXT DEFAULT '[]', created INTEGER);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, expires INTEGER);
        CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), title TEXT, category TEXT, description TEXT, price REAL, unit TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), title TEXT, category TEXT, description TEXT, budget REAL, deadline TEXT, status TEXT DEFAULT 'open', freelancer_id INTEGER REFERENCES users(id), created INTEGER);
        CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id), message TEXT, quote REAL, created INTEGER, UNIQUE(job_id,user_id));
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, job_id INTEGER UNIQUE REFERENCES jobs(id), client_id INTEGER REFERENCES users(id), freelancer_id INTEGER REFERENCES users(id), rating INTEGER, text TEXT, created INTEGER);
        """)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]: return
        now = int(time.time())
        demos = [
            ("maya.chen@campus.edu", "Maya Chen", "Visual communication student who loves turning rough ideas into polished, friendly designs.", "Graphic design, Figma, Illustration", '["https://www.behance.net/"]'),
            ("leo.martin@campus.edu", "Leo Martin", "Computer science student building useful websites and helping classmates get unstuck.", "Web development, Python, React", '[]'),
            ("amira.hassan@campus.edu", "Amira Hassan", "Patient biology tutor. I make tricky topics feel manageable, one session at a time.", "Biology, Chemistry, Tutoring", '[]'),
            ("noah.brooks@campus.edu", "Noah Brooks", "Writer and campus photographer available for events, portraits, and editing.", "Photography, Writing, Editing", '["https://unsplash.com/"]'),
            ("sofia.park@campus.edu", "Sofia Park", "Illustrator and poster designer. Fast turnaround for student clubs and events.", "Illustration, Posters, Canva", '[]')]
        for email,name,bio,skills,links in demos:
            pw=hashlib.pbkdf2_hmac('sha256',b'demo1234',b'campus-demo-salt',120000).hex()
            db.execute("INSERT INTO users(email,password,name,bio,skills,links,created) VALUES(?,?,?,?,?,?,?)",(email,pw,name,bio,skills,links,now))
        services=[(1,"Club event poster design","Design","A polished event poster sized for social media and print. Includes two revision rounds.",28,"per poster"),(2,"Simple portfolio website","Development","A clean, mobile-friendly one-page portfolio deployed with your project info and links.",90,"per project"),(3,"Intro biology tutoring","Tutoring","One-on-one sessions for cell biology, genetics, and exam review. Meet at the library.",18,"per hour"),(4,"Event photos and edits","Photography","Coverage for a student event up to two hours, with a gallery of edited photos.",65,"per event"),(5,"Essay proofreading","Writing","Thoughtful proofreading for clarity, grammar, and flow (up to 1,500 words).",22,"per essay"),(5,"Custom club illustration","Design","A custom digital illustration or mascot graphic for your club's next campaign.",40,"per illustration")]
        for uid,title,cat,desc,price,unit in services: db.execute("INSERT INTO services(user_id,title,category,description,price,unit,created) VALUES(?,?,?,?,?,?,?)",(uid,title,cat,desc,price,unit,now))
        jobs=[(2,"Need a logo for our sustainability club","Design","We are starting a campus sustainability club and need a simple, memorable logo that works on flyers and social posts.",75,"2026-12-05"),(1,"Help setting up a portfolio site","Development","Looking for help getting my design portfolio online. I have the content and visual direction; need someone to build the site.",150,"2026-11-30"),(3,"Chemistry midterm study partner","Tutoring","Looking for a patient tutor to go over reaction mechanisms and practice problems before finals.",60,"2026-12-10"),(4,"Photographer for student showcase","Photography","Our end-of-term showcase is on campus. Need candid photos for our club recap, about 90 minutes.",85,"2026-12-02")]
        for uid,title,cat,desc,budget,deadline in jobs: db.execute("INSERT INTO jobs(user_id,title,category,description,budget,deadline,created) VALUES(?,?,?,?,?,?,?)",(uid,title,cat,desc,budget,deadline,now))

def user_obj(row):
    if not row:return None
    keys=row.keys()
    return {"id":row["id"],"name":row["name"],"email":row["email"] if "email" in keys else "","bio":row["bio"],"skills":row["skills"],"links":json.loads(row["links"])}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'static'),**kwargs)
    def send_json(self, data, code=200):
        body=json.dumps(data).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('Access-Control-Allow-Origin','null'); self.end_headers(); self.wfile.write(body)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>20000: raise ValueError('Request is too large.')
        data=json.loads(self.rfile.read(n) or b'{}')
        if not isinstance(data,dict): raise ValueError('Request body must be a JSON object.')
        return data
    def current_user(self,db):
        token=self.headers.get('Cookie','').replace('campus_session=','').split(';')[0]
        row=db.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(token,int(time.time()))).fetchone() if token else None
        return row
    def require_user(self,db):
        u=self.current_user(db)
        if not u: raise PermissionError('Please sign in to continue.')
        return u
    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/'):
            try:
                with connect() as db:
                    if path=='/api/me': return self.send_json({'user':user_obj(self.current_user(db))})
                    if path=='/api/categories': return self.send_json({'categories':CATEGORIES})
                    if path.startswith('/api/users/'):
                        uid=int(path.rsplit('/',1)[1]); row=db.execute('SELECT id,name,bio,skills,links FROM users WHERE id=?',(uid,)).fetchone()
                        return self.send_json({'user':user_obj(row)} if row else {'error':'Student profile not found.'},200 if row else 404)
                    if path=='/api/services':
                        q=parse_qs(urlparse(self.path).query); kw=q.get('q',[''])[0].lower(); cat=q.get('category',[''])[0]
                        rows=db.execute("SELECT s.*,u.name provider,u.id provider_id FROM services s JOIN users u ON u.id=s.user_id WHERE (?='' OR lower(s.title||' '||s.description||' '||u.name) LIKE ?) AND (?='' OR s.category=?) ORDER BY s.created DESC",(kw,'%'+kw+'%',cat,cat)).fetchall()
                        return self.send_json({'services':[dict(r) for r in rows]})
                    if path=='/api/jobs':
                        q=parse_qs(urlparse(self.path).query); kw=q.get('q',[''])[0].lower(); cat=q.get('category',[''])[0]; status=q.get('status',[''])[0]
                        rows=db.execute("SELECT j.*,u.name poster,u.id poster_id,(SELECT COUNT(*) FROM proposals p WHERE p.job_id=j.id) proposal_count,(SELECT name FROM users f WHERE f.id=j.freelancer_id) freelancer FROM jobs j JOIN users u ON u.id=j.user_id WHERE (?='' OR lower(j.title||' '||j.description||' '||u.name) LIKE ?) AND (?='' OR j.category=?) AND (?='' OR j.status=?) ORDER BY j.created DESC",(kw,'%'+kw+'%',cat,cat,status,status)).fetchall()
                        return self.send_json({'jobs':[dict(r) for r in rows]})
                    if path.startswith('/api/jobs/') and path.endswith('/proposals'):
                        u=self.require_user(db); jid=int(path.split('/')[3]); job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                        if not job:return self.send_json({'error':'Job not found.'},404)
                        if job['user_id']!=u['id']:return self.send_json({'error':'Only the job poster can review proposals.'},403)
                        rows=db.execute('SELECT p.*,u.name,u.skills FROM proposals p JOIN users u ON u.id=p.user_id WHERE p.job_id=? ORDER BY p.created',(jid,)).fetchall()
                        return self.send_json({'proposals':[dict(r) for r in rows]})
                    return self.send_json({'error':'Not found.'},404)
            except (PermissionError,ValueError) as e:return self.send_json({'error':str(e)},401 if isinstance(e,PermissionError) else 400)
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        try:
            data=self.body()
            with connect() as db:
                if path in ('/api/signup','/api/login'):
                    email=str(data.get('email','')).strip().lower(); password=str(data.get('password','')); name=str(data.get('name','')).strip()
                    if path.endswith('signup') and (len(name)<2 or len(name)>80): return self.send_json({'error':'Name must be between 2 and 80 characters.'},400)
                    if '@' not in email or len(email)>180:return self.send_json({'error':'Enter a valid email address.'},400)
                    if path.endswith('signup') and len(password)<8:return self.send_json({'error':'Use a password with at least 8 characters.'},400)
                    if path.endswith('signup'):
                        salt=secrets.token_bytes(16); hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,180000).hex(); stored=salt.hex()+':'+hashed
                        try: db.execute('INSERT INTO users(email,password,name,created) VALUES(?,?,?,?)',(email,stored,name,int(time.time())))
                        except sqlite3.IntegrityError:return self.send_json({'error':'An account with this email already exists.'},409)
                        u=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                    else:
                        u=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                        if not u or not verify(password,u['password']):return self.send_json({'error':'Email or password is incorrect.'},401)
                    token=secrets.token_urlsafe(32); db.execute('INSERT INTO sessions VALUES(?,?,?)',(token,u['id'],int(time.time())+60*60*24*14)); self.send_response(200); self.send_header('Set-Cookie',f'campus_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=1209600'); self.send_header('Content-Type','application/json'); body=json.dumps({'user':user_obj(u)}).encode(); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
                if path=='/api/logout':
                    token=self.headers.get('Cookie','').replace('campus_session=','').split(';')[0]; db.execute('DELETE FROM sessions WHERE token=?',(token,)); self.send_response(200); self.send_header('Set-Cookie','campus_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'{}'); return
                u=self.require_user(db); now=int(time.time())
                if path=='/api/profile':
                    name=required(data,'name',80); bio=clean(data.get('bio',''),500); skills=clean(data.get('skills',''),240); links=data.get('links',[])
                    if not isinstance(links,list) or len(links)>5 or any(not valid_link(x) for x in links):return self.send_json({'error':'Add up to 5 valid http(s) portfolio links.'},400)
                    db.execute('UPDATE users SET name=?,bio=?,skills=?,links=? WHERE id=?',(name,bio,skills,json.dumps(links),u['id'])); return self.send_json({'user':user_obj(db.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
                if path=='/api/services':
                    title=required(data,'title',100); desc=required(data,'description',1000); cat=category(data.get('category')); price=amount(data.get('price')); unit=required(data,'unit',50)
                    db.execute('INSERT INTO services(user_id,title,category,description,price,unit,created) VALUES(?,?,?,?,?,?,?)',(u['id'],title,cat,desc,price,unit,now)); return self.send_json({'ok':True},201)
                if path=='/api/jobs':
                    title=required(data,'title',100); desc=required(data,'description',1500); cat=category(data.get('category')); budget=amount(data.get('budget')); deadline=valid_deadline(data.get('deadline'))
                    db.execute('INSERT INTO jobs(user_id,title,category,description,budget,deadline,created) VALUES(?,?,?,?,?,?,?)',(u['id'],title,cat,desc,budget,deadline,now)); return self.send_json({'ok':True},201)
                if path.startswith('/api/jobs/') and path.endswith('/proposals'):
                    jid=int(path.split('/')[3]); job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                    if not job:return self.send_json({'error':'Job not found.'},404)
                    if job['status']!='open' or job['user_id']==u['id']:return self.send_json({'error':'You cannot propose on this job.'},403)
                    msg=required(data,'message',1000); quote=amount(data.get('quote'))
                    db.execute('INSERT INTO proposals(job_id,user_id,message,quote,created) VALUES(?,?,?,?,?)',(jid,u['id'],msg,quote,now)); return self.send_json({'ok':True},201)
                if path.startswith('/api/jobs/') and path.endswith('/choose'):
                    jid=int(path.split('/')[3]); job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                    if not job or job['user_id']!=u['id']:return self.send_json({'error':'Only the job poster can select a freelancer.'},403)
                    if job['status']!='open':return self.send_json({'error':'This job is no longer open.'},400)
                    pid=int(data.get('proposal_id',0)); prop=db.execute('SELECT * FROM proposals WHERE id=? AND job_id=?',(pid,jid)).fetchone()
                    if not prop:return self.send_json({'error':'Proposal not found.'},404)
                    db.execute("UPDATE jobs SET freelancer_id=?,status='in progress' WHERE id=?",(prop['user_id'],jid)); return self.send_json({'ok':True})
                if path.startswith('/api/jobs/') and path.endswith('/status'):
                    jid=int(path.split('/')[3]); job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                    if not job or job['user_id']!=u['id']:return self.send_json({'error':'Only the job poster may change its status.'},403)
                    status=data.get('status')
                    if status not in STATUSES:return self.send_json({'error':'Choose a valid job status.'},400)
                    if status=='in progress' and not job['freelancer_id']:return self.send_json({'error':'Choose a freelancer before starting the job.'},400)
                    allowed={'open':{'in progress','cancelled'},'in progress':{'completed','cancelled'},'completed':set(),'cancelled':set()}
                    if status!=job['status'] and status not in allowed[job['status']]:return self.send_json({'error':'This status change is not available for the current job.'},400)
                    db.execute('UPDATE jobs SET status=? WHERE id=?',(status,jid)); return self.send_json({'ok':True})
                if path.startswith('/api/jobs/') and path.endswith('/review'):
                    jid=int(path.split('/')[3]); job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                    if not job or job['user_id']!=u['id']:return self.send_json({'error':'Only the job poster can leave a review.'},403)
                    if job['status']!='completed' or not job['freelancer_id']:return self.send_json({'error':'Reviews are available after a job is completed.'},400)
                    rating=int(data.get('rating',0)); review=required(data,'text',500)
                    if rating<1 or rating>5:return self.send_json({'error':'Choose a rating from 1 to 5.'},400)
                    try:db.execute('INSERT INTO reviews(job_id,client_id,freelancer_id,rating,text,created) VALUES(?,?,?,?,?,?)',(jid,u['id'],job['freelancer_id'],rating,review,now))
                    except sqlite3.IntegrityError:return self.send_json({'error':'This job already has a review.'},409)
                    return self.send_json({'ok':True},201)
                return self.send_json({'error':'Not found.'},404)
        except PermissionError as e:return self.send_json({'error':str(e)},401)
        except (ValueError,TypeError,json.JSONDecodeError) as e:return self.send_json({'error':str(e) or 'Please check your input.'},400)
    def do_PUT(self):
        path=urlparse(self.path).path
        try:
            data=self.body()
            with connect() as db:
                u=self.require_user(db)
                if path.startswith('/api/services/'):
                    sid=int(path.rsplit('/',1)[1]); old=db.execute('SELECT * FROM services WHERE id=?',(sid,)).fetchone()
                    if not old:return self.send_json({'error':'Service not found.'},404)
                    if old['user_id']!=u['id']:return self.send_json({'error':'You can only edit your own service listings.'},403)
                    vals=(required(data,'title',100),category(data.get('category')),required(data,'description',1000),amount(data.get('price')),required(data,'unit',50),sid)
                    db.execute('UPDATE services SET title=?,category=?,description=?,price=?,unit=? WHERE id=?',vals); return self.send_json({'ok':True})
                if path.startswith('/api/jobs/'):
                    jid=int(path.rsplit('/',1)[1]); old=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                    if not old:return self.send_json({'error':'Job not found.'},404)
                    if old['user_id']!=u['id']:return self.send_json({'error':'You can only edit your own job requests.'},403)
                    if old['status']!='open':return self.send_json({'error':'Only open job requests can be edited.'},400)
                    title=required(data,'title',100); cat=category(data.get('category')); desc=required(data,'description',1500); budget=amount(data.get('budget')); deadline=valid_deadline(data.get('deadline'))
                    db.execute('UPDATE jobs SET title=?,category=?,description=?,budget=?,deadline=? WHERE id=?',(title,cat,desc,budget,deadline,jid)); return self.send_json({'ok':True})
                return self.send_json({'error':'Not found.'},404)
        except PermissionError as e:return self.send_json({'error':str(e)},401)
        except (ValueError,TypeError,json.JSONDecodeError) as e:return self.send_json({'error':str(e) or 'Please check your input.'},400)
    def log_message(self,fmt,*args): pass

def clean(v,limit):
    if not isinstance(v,str):raise ValueError('Expected text input.')
    v=v.strip()
    if len(v)>limit:raise ValueError(f'Text must be {limit} characters or fewer.')
    return v
def required(data,key,limit):
    v=clean(data.get(key,''),limit)
    if not v:raise ValueError('Please complete all required fields.')
    return v
def amount(v):
    n=float(v)
    if not math.isfinite(n) or n<=0 or n>100000:raise ValueError('Enter an amount between 0.01 and 100,000.')
    return n
def category(v):
    if v not in CATEGORIES:raise ValueError('Choose a valid category.')
    return v
def valid_deadline(v):
    v=required({'deadline':v},'deadline',10)
    try: date.fromisoformat(v)
    except ValueError: raise ValueError('Choose a valid deadline date.')
    return v
def valid_link(v):return isinstance(v,str) and len(v)<300 and v.startswith(('https://','http://'))
def verify(password,stored):
    if ':' in stored:
        salt,expected=stored.split(':',1); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),180000).hex(),expected)
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),b'campus-demo-salt',120000).hex(),stored)

if __name__=='__main__':
    init_db(); print(f'Campus Market running at http://localhost:{PORT}'); ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
