import os, io, csv, json, re, sqlite3, hashlib, datetime, threading, zipfile, shutil, subprocess, sys
import structlog
from decimal import Decimal, InvalidOperation
from flask import Flask, request, redirect, render_template_string, flash, url_for, send_file
try: import webview
except Exception: webview=None
try:
    from PIL import Image
    import pytesseract
except Exception: Image=None; pytesseract=None
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT=os.path.dirname(os.path.abspath(__file__))
DATA=os.path.join(ROOT,"Data"); RECEIPTS=os.path.join(DATA,"Kvitton"); EXPORT=os.path.join(DATA,"Export"); BACKUPS=os.path.join(DATA,"Backups")
for p in (DATA,RECEIPTS,EXPORT,BACKUPS): os.makedirs(p,exist_ok=True)
DB=os.path.join(DATA,"bokforing.db")
app=Flask(__name__); app.secret_key="v13-local"

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.dev.ConsoleRenderer(),
    ],
)
log=structlog.get_logger("min_bokforing")
log.info("application_started", data_directory=DATA)
VISION_OCR_SOURCE=os.path.join(ROOT, "ocr_macos.swift")
VISION_OCR_BINARY=os.path.join(ROOT, ".ocr_macos")
VISION_OCR_LOCK=threading.Lock()
VISION_OCR_READY=None

@app.before_request
def log_request():
    log.info(
        "http_request",
        method=request.method,
        path=request.path,
        content_length=request.content_length or 0,
    )

# K1 chart supplied by BAS concept for sole proprietors using simplified annual accounts.
# The app deliberately keeps the chart editable instead of pretending every company uses identical accounts.
ACCOUNTS=[
("1930","Företagskonto"),("2010","Eget kapital"),("2013","Egna uttag"),("2018","Egna insättningar"),
("2019","Årets resultat"),("2611","Utgående moms 25 %"),("2612","Utgående moms 12 %"),
("2613","Utgående moms 6 %"),("2641","Debiterad ingående moms"),("2650","Redovisningskonto för moms"),
("3001","Försäljning varor 25 %"),("3041","Försäljning tjänster 25 %"),("4010","Inköp varor/material"),
("5410","Förbrukningsinventarier"),("5460","Förbrukningsmaterial"),("5800","Resor"),
("5910","Annonsering"),("6212","Mobiltelefon"),("6230","Datakommunikation"),("6540","IT-tjänster"),
("6570","Bankkostnader"),("6990","Övriga externa kostnader"),("1220","Inventarier"),("7832","Avskrivningar")
]
AC=dict(ACCOUNTS)

HTML="""<!doctype html><html lang=sv><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display',Arial;background:#f5f5f7;color:#171717;margin:0}
nav{background:#111;color:#fff;padding:14px 18px;position:sticky;top:0;z-index:5;overflow:auto;white-space:nowrap}
nav a{color:white;text-decoration:none;margin-right:14px;font-size:14px}main{max-width:1250px;margin:22px auto;padding:0 16px}
.card{background:white;border-radius:16px;padding:20px;margin:14px 0;box-shadow:0 2px 12px #0001}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{font-size:24px;font-weight:700}.muted{color:#666}
button{border:0;background:#111;color:#fff;padding:10px 14px;border-radius:9px;cursor:pointer}button.red{background:#9b1c1c}
input,select,textarea{padding:9px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box}textarea{width:100%;min-height:90px}
table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}
.good,.bad,.warn{padding:12px;border-radius:10px}.good{background:#e9f8ee}.bad{background:#fff0f0}.warn{background:#fff7dd}
.pill{display:inline-block;padding:4px 8px;border-radius:10px;background:#eee;font-size:12px}.actions{display:flex;gap:8px;flex-wrap:wrap}
@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}
</style></head><body>
<nav><b>Min Bokföring v13</b>　<a href="/">Översikt</a><a href="/bank">Händelser</a><a href="/journal">Verifikationer</a><a href="/manual">Ny verifikation</a><a href="/receipts">Kvitton</a><a href="/vat">Moms</a><a href="/reports">Rapporter</a><a href="/close">Bokslut</a><a href="/ne">NE/SRU</a><a href="/settings">Inställningar</a><a href="/backup">Backup</a></nav>
<main>{% for m in get_flashed_messages() %}<div class=card good>{{m}}</div>{% endfor %}{{body|safe}}</main></body></html>"""

def conn():
    log.debug("database_opening", database=DB)
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,tx_key TEXT UNIQUE,date TEXT,reference TEXT,description TEXT,
      amount REAL,balance REAL,currency TEXT,source TEXT,status TEXT DEFAULT 'new',
      suggested_account TEXT,suggested_vat INTEGER DEFAULT 25,receipt_id INTEGER,voucher_id INTEGER,raw TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS receipts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,filename TEXT,path TEXT,sha256 TEXT UNIQUE,date TEXT,supplier TEXT,
      total REAL,vat REAL,currency TEXT,ocr_text TEXT,matched_tx INTEGER,created TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS vouchers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,ver_no INTEGER UNIQUE,date TEXT,text TEXT,source TEXT,
      receipt_id INTEGER,transaction_id INTEGER,status TEXT DEFAULT 'posted',created TEXT,hash TEXT,prev_hash TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lines(
      id INTEGER PRIMARY KEY AUTOINCREMENT,voucher_id INTEGER,account TEXT,debit REAL,credit REAL,vat_code TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,action TEXT,object_type TEXT,object_id INTEGER,details TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS corrections(
      id INTEGER PRIMARY KEY AUTOINCREMENT,old_voucher INTEGER,new_voucher INTEGER,reason TEXT,created TEXT)""")
    c.commit(); log.debug("database_ready"); return c

def setting(k,default=""):
    c=conn(); r=c.execute("select v from settings where k=?",(k,)).fetchone()
    return r["v"] if r else default

def set_setting(k,v):
    c=conn(); c.execute("insert into settings(k,v) values(?,?) on conflict(k) do update set v=excluded.v",(k,str(v))); c.commit()
    log.info("setting_saved", setting=k)

def audit(action,obj,oid,details):
    c=conn(); c.execute("insert into audit(created,action,object_type,object_id,details) values(?,?,?,?,?)",
                         (datetime.datetime.now().isoformat(),action,obj,oid,details)); c.commit()
    log.info("audit_recorded", action=action, object_type=obj, object_id=oid)

def money(v):
    s=str(v or "").strip().replace(" ","").replace(" ","")
    if not s:return None
    s=re.sub(r"[^0-9,.\-+]","",s)
    if "," in s and "." in s:
        s=s.replace(".","").replace(",",".") if s.rfind(",")>s.rfind(".") else s.replace(",","")
    elif "," in s:s=s.replace(",",".")
    try:return float(Decimal(s))
    except InvalidOperation:return None

def next_ver(c): return c.execute("select coalesce(max(ver_no),0)+1 n from vouchers").fetchone()["n"]

def create_voucher(date,text,source,transaction_id,receipt_id,lines):
    d=round(sum(float(x[1]) for x in lines),2); cr=round(sum(float(x[2]) for x in lines),2)
    if abs(d-cr)>0.009: raise ValueError("Debet och kredit måste balansera.")
    c=conn(); v=next_ver(c)
    prev=c.execute("select hash from vouchers order by id desc limit 1").fetchone()
    prev_hash=prev["hash"] if prev else ""
    payload=json.dumps([v,date,text,source,transaction_id,receipt_id,lines,prev_hash],ensure_ascii=False,separators=(",",":"))
    h=hashlib.sha256(payload.encode()).hexdigest()
    c.execute("""insert into vouchers(ver_no,date,text,source,receipt_id,transaction_id,status,created,hash,prev_hash)
                 values(?,?,?,?,?,?,?,?,?,?)""",
              (v,date,text,source,receipt_id,transaction_id,"posted",datetime.datetime.now().isoformat(),h,prev_hash))
    vid=c.execute("select last_insert_rowid() id").fetchone()["id"]
    for a,de,cr,vat in lines:
        c.execute("insert into lines(voucher_id,account,debit,credit,vat_code) values(?,?,?,?,?)",(vid,a,de,cr,vat))
    if transaction_id:
        c.execute("update transactions set status='booked',voucher_id=?,receipt_id=coalesce(?,receipt_id) where id=?",(vid,receipt_id,transaction_id))
    if receipt_id:
        c.execute("update receipts set matched_tx=? where id=?",(transaction_id,receipt_id))
    c.commit(); audit("BOKFÖR","voucher",vid,f"V{v}: {text}")
    log.info("voucher_created", voucher_id=vid, voucher_number=v, source=source, line_count=len(lines))
    return vid

def suggest(desc,amount):
    d=desc.lower()
    if amount>0 and "swish" in d:return ("Möjlig försäljning","3001",25)
    if any(x in d for x in ("bokio","loopia","lovable")):return ("IT-tjänst","6540",25)
    if "hallandstrafiken" in d:return ("Resa/transport","5800",25)
    if any(x in d for x in ("telia","tele2","telenor","tre")):return ("Telekommunikation","6212",25)
    if "bank" in d:return ("Bankkostnad","6570",0)
    if any(x in d for x in ("ica","coop","willys")):return ("Kontrollera privat/affärsmässig kostnad","5460",25)
    return ("Behöver granskas","6990",25)

def parse_bank(data):
    text=None
    for enc in ("cp1252","iso-8859-1","utf-8-sig","utf-8"):
        try:text=data.decode(enc);break
        except UnicodeDecodeError:pass
    if text is None:raise ValueError("CSV kunde inte läsas.")
    rows=list(csv.reader(io.StringIO(text),delimiter=","))
    hi=None
    for i,r in enumerate(rows[:30]):
        n=[re.sub(r"[^a-z0-9]","",x.lower().replace("å","a").replace("ä","a").replace("ö","o")) for x in r]
        if "bokfordag" in n and "belopp" in n and "bokfortsaldo" in n:hi=i;break
    if hi is None:raise ValueError("Hittade inte kolumnrubrikerna från din bank.")
    h=rows[hi]
    def idx(w):
        z=re.sub(r"[^a-z0-9]","",w.lower().replace("å","a").replace("ä","a").replace("ö","o"))
        for i,x in enumerate(h):
            if re.sub(r"[^a-z0-9]","",x.lower().replace("å","a").replace("ä","a").replace("ö","o"))==z:return i
        return None
    ib,ir,ide,ia,isal,ic=[idx(x) for x in ("Bokföringsdag","Referens","Beskrivning","Belopp","Bokfört saldo","Valuta")]
    out=[];errs=[]
    for ln,r in enumerate(rows[hi+1:],hi+2):
        if not any(x.strip() for x in r):continue
        g=lambda i:r[i].strip() if i is not None and i<len(r) else ""
        dt=g(ib);amt=money(g(ia))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$",dt) or amt is None:errs.append(f"Rad {ln}");continue
        out.append((dt,g(ir),g(ide) or "Banktransaktion",amt,money(g(isal)),g(ic) or "SEK"))
    return out,errs

def ensure_vision_ocr():
    """Build the local macOS Vision helper once, only when it is needed."""
    global VISION_OCR_READY
    if VISION_OCR_READY is not None:
        return VISION_OCR_READY
    with VISION_OCR_LOCK:
        if VISION_OCR_READY is not None:
            return VISION_OCR_READY
        if sys.platform != "darwin":
            log.info("vision_ocr_skipped", reason="not_macos")
            VISION_OCR_READY=False
            return False
        if os.path.isfile(VISION_OCR_BINARY):
            log.info("vision_ocr_ready", source="existing_binary")
            VISION_OCR_READY=True
            return True
        if not os.path.isfile(VISION_OCR_SOURCE):
            log.error("vision_ocr_unavailable", reason="helper_source_missing")
            VISION_OCR_READY=False
            return False
        log.info("vision_ocr_build_started")
        try:
            result=subprocess.run(
                ["xcrun", "swiftc", VISION_OCR_SOURCE, "-o", VISION_OCR_BINARY,
                 "-framework", "Vision"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.exception("vision_ocr_build_failed")
            VISION_OCR_READY=False
            return False
        if result.returncode != 0:
            log.error(
                "vision_ocr_build_failed",
                return_code=result.returncode,
                compiler_output=result.stderr[-1000:],
            )
            VISION_OCR_READY=False
            return False
        log.info("vision_ocr_build_completed")
        VISION_OCR_READY=True
        return True

def vision_ocr(path):
    if not ensure_vision_ocr():
        return ""
    log.info("vision_ocr_started", file_extension=os.path.splitext(path)[1].lower())
    try:
        result=subprocess.run(
            [VISION_OCR_BINARY, path], capture_output=True, text=True,
            timeout=60, check=False,
        )
        if result.returncode != 0:
            log.error("vision_ocr_failed", return_code=result.returncode, error_output=result.stderr[-1000:])
            return ""
        payload=json.loads(result.stdout)
        text=payload.get("text", "")
        if not isinstance(text, str):
            log.error("vision_ocr_failed", reason="invalid_response")
            return ""
        log.info("vision_ocr_completed", character_count=len(text))
        return text
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        log.exception("vision_ocr_failed")
        return ""

def receipt_ocr(path):
    text=vision_ocr(path)
    if text:
        return text
    if Image is None or pytesseract is None:
        log.warning("ocr_unavailable", image_available=Image is not None, tesseract_available=pytesseract is not None)
        return ""
    log.info("tesseract_ocr_started", file_extension=os.path.splitext(path)[1].lower())
    try:
        text=pytesseract.image_to_string(Image.open(path),lang="swe+eng")
        log.info("tesseract_ocr_completed", character_count=len(text))
        return text
    except Exception:
        log.exception("tesseract_ocr_failed", file_extension=os.path.splitext(path)[1].lower())
        return ""

def extract_receipt(text):
    total=vat=0;dt=""
    log.debug("receipt_extraction_started", character_count=len(text))
    for pat in (r"(?:total|summa|att betala|belopp)\D{0,20}(\d+[,.]\d{2})",r"(\d+[,.]\d{2})\s*(?:SEK|kr)"):
        m=re.search(pat,text,re.I)
        if m: total=money(m.group(1)) or 0;break
    m=re.search(r"(?:moms|vat)\D{0,15}(\d+[,.]\d{2})",text,re.I)
    if m:vat=money(m.group(1)) or 0
    m=re.search(r"(\d{4}[-/.]\d{2}[-/.]\d{2})",text)
    if m:dt=m.group(1).replace("/","-").replace(".","-")
    log.info("receipt_extraction_completed", total_found=bool(total), vat_found=bool(vat), date_found=bool(dt))
    return total,vat,dt

def verify_chain():
    c=conn(); rows=c.execute("select * from vouchers order by id").fetchall(); prev=""
    for v in rows:
        lines=c.execute("select account,debit,credit,vat_code from lines where voucher_id=? order by id",(v["id"],)).fetchall()
        payload=json.dumps([v["ver_no"],v["date"],v["text"],v["source"],v["transaction_id"],v["receipt_id"],
                            [(x["account"],x["debit"],x["credit"],x["vat_code"]) for x in lines],prev],
                           ensure_ascii=False,separators=(",",":"))
        h=hashlib.sha256(payload.encode()).hexdigest()
        if h!=v["hash"] or v["prev_hash"]!=prev:return False,v["ver_no"]
        prev=h
    return True,None

def totals():
    c=conn(); rows=c.execute("""select account,sum(debit-credit) bal from lines l join vouchers v on v.id=l.voucher_id
                                where v.status='posted' group by account""").fetchall()
    vals={r["account"]:r["bal"] for r in rows}
    sales=-sum(v for a,v in vals.items() if a.startswith("30"))
    costs=sum(v for a,v in vals.items() if a.startswith(("4","5","6","7","8")))
    return sales,costs,sales-costs,vals

@app.route("/")
def home():
    c=conn(); todo=c.execute("select count(*) n from transactions where status='new'").fetchone()["n"]
    booked=c.execute("select count(*) n from vouchers").fetchone()["n"]; rec=c.execute("select count(*) n from receipts").fetchone()["n"]
    ok,bad=verify_chain()
    body=render_template_string("""<div class=card><h1>Min Bokföring v13</h1><p class=muted>Regelstyrd bokföring för svensk enskild firma, med tydlig kontroll före bokföring.</p>
    <div class=grid><div><div class=stat>{{todo}}</div><div class=muted>bankhändelser att granska</div></div><div><div class=stat>{{booked}}</div><div class=muted>verifikationer</div></div><div><div class=stat>{{rec}}</div><div class=muted>kvitton</div></div><div><div class=stat>{{"OK" if ok else "FEL"}}</div><div class=muted>verifikationskedja</div></div></div></div>
    <div class=card><h2>V13 lagkontroller</h2><ul><li>Välj redovisningsregelverk i Inställningar.</li><li>Konteringsförslag måste granskas innan bokföring.</li><li>Bokförda verifikationer kan inte redigeras eller raderas.</li><li>Rättelser görs genom en ny korrigeringsverifikation.</li><li>Verifikationerna får en hash-kedja för att upptäcka efterhandsändringar.</li><li>NE/SRU markeras som underlag tills aktuell Skatteverket-specifikation är verifierad.</li></ul></div>""",todo=todo,booked=booked,rec=rec,ok=ok)
    return render_template_string(HTML,body=body)

@app.route("/settings",methods=["GET","POST"])
def settings():
    if request.method=="POST":
        set_setting("regelverk",request.form.get("regelverk","K1"))
        set_setting("vat_period",request.form.get("vat_period","year"))
        set_setting("vat_method",request.form.get("vat_method","cash"))
        set_setting("turnover_limit",request.form.get("turnover_limit","3000000"))
        flash("Inställningarna sparades.")
        return redirect(url_for("settings"))
    body=render_template_string("""<div class=card><h1>Inställningar</h1><form method=post>
    <label>Regelverk</label><br><select name=regelverk><option value=K1 {%if rule=="K1"%}selected{%endif%}>K1 – förenklat årsbokslut</option><option value=AB {%if rule=="AB"%}selected{%endif%}>Årsbokslut (K2/K3-spår)</option></select>
    <p><label>Momsperiod</label><br><select name=vat_period><option value=year>År</option><option value=quarter>Kvartal</option><option value=month>Månad</option></select></p>
    <p><label>Bokföringsmetod</label><br><select name=vat_method><option value=cash>Kontantmetoden/bokslutsmetoden</option><option value=accrual>Faktureringsmetoden</option></select></p>
    <p><label>Omsättningsgräns för K1-kontroll</label><br><input name=turnover_limit value="{{limit}}"> kr</p><button>Spara</button></form>
    <div class=warn>Välj K1 bara om företaget uppfyller villkoren för förenklat årsbokslut. Appen stoppar inte dig från att välja fel – ansvaret ligger hos företagaren.</div></div>""",
    rule=setting("regelverk","K1"),limit=setting("turnover_limit","3000000"))
    return render_template_string(HTML,body=body)

@app.route("/import",methods=["GET","POST"])
def imp():
    if request.method=="POST":
        f=request.files.get("file")
        try:items,errs=parse_bank(f.read())
        except Exception as e:return render_template_string(HTML,body=f"<div class=card bad>{e}</div>")
        c=conn();new=skip=0
        for dt,ref,desc,amt,bal,cur in items:
            key="CSV:"+hashlib.sha256(f"{dt}|{ref}|{desc}|{amt}|{bal}".encode()).hexdigest()
            _,acc,vat=suggest(desc,amt)
            try:c.execute("""insert into transactions(tx_key,date,reference,description,amount,balance,currency,source,status,suggested_account,suggested_vat,raw)
                values(?,?,?,?,?,?,?,?,?,?,?,?)""",(key,dt,ref,desc,amt,bal,cur,"CSV: "+f.filename,"new",acc,vat,""));new+=1
            except sqlite3.IntegrityError:skip+=1
        c.commit();audit("IMPORTERA","bank",0,f"{f.filename}: {new} nya, {skip} dubbletter")
        log.info("bank_import_completed", imported_count=new, duplicate_count=skip, invalid_row_count=len(errs))
        flash(f"Import klar: {new} nya, {skip} dubbletter, {len(errs)} felrader.");return redirect(url_for("bank"))
    body="""<div class=card><h1>Importera bank</h1><form method=post enctype=multipart/form-data>
    <input type=file name=file accept=.csv required><button>Importera CSV</button></form><p class=muted>Importeraren är anpassad för CSV-formatet du använde i tidigare versioner.</p></div>"""
    return render_template_string(HTML,body=body)

@app.route("/bank")
def bank():
    c=conn();rows=c.execute("select * from transactions order by status='new' desc,date,id").fetchall()
    body=render_template_string("""<div class=card><h1>Händelser</h1><a href=/import>＋ Importera CSV</a></div>
    {%for r in rows%}<div class=card><b>{{r.date}}</b> · {{r.description}} · <b>{{"%.2f"|format(r.amount)}} {{r.currency}}</b> <span class=pill>{{r.status}}</span>
    <p class=muted>{{r.reference}}</p>{%if r.status!="booked"%}<form method=post action=/book/{{r.id}}><select name=account>{%for a,n in accounts.items()%}<option value={{a}} {%if a==r.suggested_account%}selected{%endif%}>{{a}} – {{n}}</option>{%endfor%}</select>
    <select name=vat><option value=25 {%if r.suggested_vat==25%}selected{%endif%}>25 %</option><option value=12>12 %</option><option value=6>6 %</option><option value=0>Ingen moms</option></select>
    <button>Granska & bokför</button></form>{%endif%}</div>{%else%}<div class=card>Inga händelser.</div>{%endfor%}""",rows=rows,accounts=AC)
    return render_template_string(HTML,body=body)

@app.route("/book/<int:tid>",methods=["POST"])
def book(tid):
    c=conn();t=c.execute("select * from transactions where id=?",(tid,)).fetchone()
    if not t or t["status"]=="booked":return redirect(url_for("bank"))
    acc=request.form.get("account","6990");vat=int(request.form.get("vat","0"));amt=abs(t["amount"])
    if t["amount"]<0:
        if vat in (25,12,6):
            rate=1+vat/100; net=round(amt/rate,2); vm=round(amt-net,2)
            lines=[(acc,net,0,""),("2641",vm,0,str(vat)),("1930",0,amt,"")]
        else:lines=[(acc,amt,0,""),("1930",0,amt,"")]
    else:
        if vat in (25,12,6):
            rate=1+vat/100;net=round(amt/rate,2);vm=round(amt-net,2);va={25:"2611",12:"2612",6:"2613"}[vat]
            lines=[("1930",amt,0,""),("3001",0,net,str(vat)),(va,0,vm,str(vat))]
        else:lines=[("1930",amt,0,""),("3001",0,amt,"")]
    try:create_voucher(t["date"],t["description"],"bank",tid,t["receipt_id"],lines);flash("Bokförd. Verifikationen är nu låst.")
    except Exception as e:flash("Fel: "+str(e))
    return redirect(url_for("bank"))

@app.route("/manual",methods=["GET","POST"])
def manual_voucher():
    if request.method=="POST":
        date=request.form.get("date","").strip()
        text=request.form.get("text","").strip()
        debit_account=request.form.get("debit_account","")
        credit_account=request.form.get("credit_account","")
        amount=money(request.form.get("amount",""))
        if not date or not text or debit_account not in AC or credit_account not in AC or amount is None or amount <= 0:
            flash("Fyll i datum, beskrivning, två konton och ett belopp större än 0.")
            return redirect(url_for("manual_voucher"))
        try:
            voucher_id=create_voucher(
                date, text, "manual", None, None,
                [(debit_account, amount, 0, ""), (credit_account, 0, amount, "")]
            )
            log.info("manual_voucher_created", voucher_id=voucher_id)
            flash("Manuell verifikation sparad och låst.")
            return redirect(url_for("journal"))
        except Exception as e:
            log.exception("manual_voucher_failed")
            flash("Kunde inte spara verifikationen: "+str(e))
            return redirect(url_for("manual_voucher"))
    body=render_template_string("""<div class=card><h1>Ny manuell verifikation</h1>
    <p class=muted>Skapa en enkel verifikation med ett debetkonto och ett kreditkonto. Kontrollera alltid konteringen innan du sparar.</p>
    <form method=post>
    <p><label>Datum</label><br><input type=date name=date value="{{today}}" required></p>
    <p><label>Beskrivning</label><br><input name=text required></p>
    <p><label>Debetkonto</label><br><select name=debit_account required>{%for a,n in accounts.items()%}<option value="{{a}}">{{a}} – {{n}}</option>{%endfor%}</select></p>
    <p><label>Kreditkonto</label><br><select name=credit_account required>{%for a,n in accounts.items()%}<option value="{{a}}">{{a}} – {{n}}</option>{%endfor%}</select></p>
    <p><label>Belopp inkl. moms</label><br><input name=amount inputmode=decimal placeholder="0,00" required> kr</p>
    <button>Spara verifikation</button></form></div>""",
    accounts=AC,today=datetime.date.today().isoformat())
    return render_template_string(HTML,body=body)

@app.route("/receipts",methods=["GET","POST"])
def receipts():
    c=conn()
    if request.method=="POST":
        f=request.files.get("receipt")
        if not f:
            log.warning("receipt_upload_missing_file")
            return redirect(url_for("receipts"))
        data=f.read();sha=hashlib.sha256(data).hexdigest();ext=os.path.splitext(f.filename)[1].lower() or ".jpg";path=os.path.join(RECEIPTS,sha+ext)
        log.info("receipt_upload_received", byte_count=len(data), file_extension=ext, content_hash_prefix=sha[:12])
        open(path,"wb").write(data);log.info("receipt_file_saved", byte_count=len(data), file_extension=ext)
        text=receipt_ocr(path) if ext not in (".pdf",) else "";total,vat,dt=extract_receipt(text)
        try:
            c.execute("""insert into receipts(filename,path,sha256,date,total,vat,currency,ocr_text,created)
                         values(?,?,?,?,?,?,?,?,?)""",(f.filename,path,sha,dt,total,vat,"SEK",text,datetime.datetime.now().isoformat()));c.commit()
        except sqlite3.IntegrityError:
            log.warning("receipt_duplicate", content_hash_prefix=sha[:12])
            flash("Samma kvitto finns redan.")
        else:
            log.info("receipt_record_created", content_hash_prefix=sha[:12], ocr_character_count=len(text))
            if text:
                flash("Kvitto sparat. OCR-text hittades – kontrollera datum, summa och moms.")
            else:
                flash("Kvitto sparat, men OCR kunde inte läsa texten. Du kan ändå skapa en manuell verifikation.")
        return redirect(url_for("receipts"))
    rows=c.execute("select * from receipts order by id desc").fetchall()
    body=render_template_string("""<div class=card><h1>Kvitton</h1><form method=post enctype=multipart/form-data><input type=file name=receipt accept="image/*,.pdf" capture="environment" required> <button>Spara kvitto</button></form>
    <p class=muted>På Mac kan OCR kräva Tesseract. På mobil/enheter med kamera kan filfältet erbjuda kameran.</p></div>
    {%for r in rows%}<div class=card><b>{{r.filename}}</b><p>Datum: {{r.date or "–"}} · Summa: {{r.total or "–"}} · Moms: {{r.vat or "–"}}</p><p class=muted>OCR: {{"Text hittades" if r.ocr_text else "Ingen text kunde läsas"}}</p>
    {%if r.ocr_text%}<details><summary>OCR-text</summary><pre>{{r.ocr_text[:1500]}}</pre></details>{%endif%}</div>{%endfor%}""",rows=rows)
    return render_template_string(HTML,body=body)

@app.route("/journal")
def journal():
    c=conn();vs=c.execute("select * from vouchers order by ver_no desc").fetchall();ok,bad=verify_chain()
    body=render_template_string("""<div class=card><h1>Verifikationer</h1><p class={{"good" if ok else "bad"}}>Hashkedja: {{"OK" if ok else "FEL vid V"+bad|string}}</p>
    <p>Bokförda poster är låsta. Använd korrigeringsverifikation om något blev fel.</p></div>
    {%for v in vs%}<div class=card><b>V{{v.ver_no}}</b> · {{v.date}} · {{v.text}} <span class=pill>{{v.source}}</span>
    <table><tr><th>Konto</th><th>Debet</th><th>Kredit</th><th>Moms</th></tr>{%for l in lines(v.id)%}<tr><td>{{l.account}} – {{accounts.get(l.account,"")}}</td><td>{{"%.2f"|format(l.debit)}}</td><td>{{"%.2f"|format(l.credit)}}</td><td>{{l.vat_code}}</td></tr>{%endfor%}</table>
    <form method=post action=/reverse/{{v.id}}><input name=reason placeholder="Orsak till rättelse" required><button class=red>Skapa korrigeringsverifikation</button></form></div>{%else%}<div class=card>Inga verifikationer.</div>{%endfor%}""",
    vs=vs,lines=lambda x:c.execute("select * from lines where voucher_id=?",(x,)).fetchall(),accounts=AC,ok=ok,bad=bad)
    return render_template_string(HTML,body=body)

@app.route("/reverse/<int:vid>",methods=["POST"])
def reverse(vid):
    reason=request.form.get("reason","Rättelse")
    c=conn();v=c.execute("select * from vouchers where id=?",(vid,)).fetchone()
    if not v:return redirect(url_for("journal"))
    lines=c.execute("select account,debit,credit,vat_code from lines where voucher_id=?",(vid,)).fetchall()
    newlines=[(x["account"],x["credit"],x["debit"],x["vat_code"]) for x in lines]
    try:
        nv=create_voucher(datetime.date.today().isoformat(),"Rättelse av V"+str(v["ver_no"])+": "+reason,"correction",None,v["receipt_id"],newlines)
        c=conn();c.execute("insert into corrections(old_voucher,new_voucher,reason,created) values(?,?,?,?)",(vid,nv,reason,datetime.datetime.now().isoformat()));c.commit()
        flash(f"Korrigeringsverifikation V{c.execute('select ver_no from vouchers where id=?',(nv,)).fetchone()['ver_no']} skapad.")
    except Exception as e:flash("Fel: "+str(e))
    return redirect(url_for("journal"))

@app.route("/vat")
def vat():
    c=conn()
    out25=c.execute("select coalesce(sum(credit-debit),0) x from lines where account='2611'").fetchone()["x"]
    out12=c.execute("select coalesce(sum(credit-debit),0) x from lines where account='2612'").fetchone()["x"]
    out6=c.execute("select coalesce(sum(credit-debit),0) x from lines where account='2613'").fetchone()["x"]
    inp=c.execute("select coalesce(sum(debit-credit),0) x from lines where account='2641'").fetchone()["x"];net=out25+out12+out6-inp
    body=render_template_string("""<div class=card><h1>Moms</h1><div class=grid><div><div class=stat>{{"%.2f"|format(o25)}}</div><div class=muted>utgående 25 %</div></div><div><div class=stat>{{"%.2f"|format(o12)}}</div><div class=muted>utgående 12 %</div></div><div><div class=stat>{{"%.2f"|format(o6)}}</div><div class=muted>utgående 6 %</div></div><div><div class=stat>{{"%.2f"|format(inp)}}</div><div class=muted>ingående</div></div></div>
    <h2>Netto moms: {{"%.2f"|format(net)}} kr</h2><div class=warn>V13 räknar moms från bokförda momskoder. Den ersätter inte kontroll av momsdeklarationens samtliga rutor, EU-handel, import, omvänd skattskyldighet eller periodisering.</div></div>""",o25=out25,o12=out12,o6=out6,inp=inp,net=net)
    return render_template_string(HTML,body=body)

@app.route("/reports")
def reports():
    sales,costs,result,vals=totals();c=conn();rows=sorted(vals.items())
    body=render_template_string("""<div class=card><h1>Rapporter</h1><div class=grid><div><div class=stat>{{"%.2f"|format(sales)}}</div><div class=muted>intäkter</div></div><div><div class=stat>{{"%.2f"|format(costs)}}</div><div class=muted>kostnader</div></div><div><div class=stat>{{"%.2f"|format(result)}}</div><div class=muted>resultat</div></div><div><a href=/reports.pdf>PDF</a></div></div>
    <table><tr><th>Konto</th><th>Saldo</th></tr>{%for a,b in rows%}<tr><td>{{a}} – {{accounts.get(a,"")}}</td><td>{{"%.2f"|format(b)}}</td></tr>{%endfor%}</table></div>""",sales=sales,costs=costs,result=result,rows=rows,accounts=AC)
    return render_template_string(HTML,body=body)

@app.route("/reports.pdf")
def reports_pdf():
    sales,costs,result,vals=totals();path=os.path.join(EXPORT,"Rapporter.pdf");p=canvas.Canvas(path,pagesize=A4);w,h=A4
    p.setFont("Helvetica-Bold",18);p.drawString(50,h-55,"Min Bokföring v13 – rapport");p.setFont("Helvetica",10);y=h-90
    for a,b in sorted(vals.items()):
        p.drawString(50,y,f"{a} {AC.get(a,'')}");p.drawRightString(w-50,y,f"{b:.2f}");y-=14
        if y<50:p.showPage();y=h-50
    p.save();return send_file(path,as_attachment=True,download_name="Rapporter.pdf")

@app.route("/close")
def close():
    sales,costs,result,vals=totals();c=conn()
    body=render_template_string("""<div class=card><h1>Bokslutskontroll</h1><div class=grid><div><div class=stat>{{"%.2f"|format(sales)}}</div><div class=muted>intäkter</div></div><div><div class=stat>{{"%.2f"|format(costs)}}</div><div class=muted>kostnader</div></div><div><div class=stat>{{"%.2f"|format(result)}}</div><div class=muted>bokfört resultat</div></div><div><div class=stat>{{v}}</div><div class=muted>verifikationer</div></div></div>
    <h2>Obligatoriska kontrollpunkter</h2><ul><li>Bankkonto avstämt mot bankutdrag.</li><li>Alla affärshändelser och verifikationer kontrollerade.</li><li>Alla kvitton/fakturor sparade.</li><li>Varulager, kundfordringar och leverantörsskulder kontrollerade om de finns.</li><li>Anläggningstillgångar och avskrivningar kontrollerade.</li><li>Moms stämd.</li><li>Eget kapital, egna uttag/insättningar kontrollerade.</li><li>Skatter och avgifter som hör till verksamheten kontrollerade.</li></ul>
    <div class=warn>V13 är en kontrollmotor, inte en garanti för att varje bokslutspost är korrekt. K1 innehåller särskilda regler för bl.a. lager och avskrivningar som måste prövas utifrån företagets faktiska situation.</div></div>""",sales=sales,costs=costs,result=result,v=c.execute("select count(*) n from vouchers").fetchone()["n"])
    return render_template_string(HTML,body=body)

@app.route("/ne")
def ne():
    sales,costs,result,vals=totals()
    goods=sum(v for a,v in vals.items() if a.startswith("4"));ext=sum(v for a,v in vals.items() if a.startswith(("5","6","7")));fin=sum(v for a,v in vals.items() if a.startswith("8"))
    body=render_template_string("""<div class=card><h1>NE-underlag</h1><table><tr><th>Post</th><th>Belopp</th></tr>
    <tr><td>R1 – intäkter</td><td>{{"%.2f"|format(sales)}}</td></tr><tr><td>R5 – varor/material</td><td>{{"%.2f"|format(goods)}}</td></tr>
    <tr><td>R6 – övriga externa kostnader</td><td>{{"%.2f"|format(ext)}}</td></tr><tr><td>R8 – finansiella poster</td><td>{{"%.2f"|format(fin)}}</td></tr>
    <tr><th>Bokfört resultat</th><th>{{"%.2f"|format(result)}}</th></tr></table><br><a href=/sru>Skapa SRU-underlag</a>
    <div class=warn>V13 visar bara ett granskningsunderlag. Ett korrekt NE kräver även balansposter och skattemässiga uppgifter/justeringar där sådana finns.</div></div>""",sales=sales,goods=goods,ext=ext,fin=fin,result=result)
    return render_template_string(HTML,body=body)

@app.route("/sru")
def sru():
    sales,costs,result,vals=totals();goods=sum(v for a,v in vals.items() if a.startswith("4"));ext=sum(v for a,v in vals.items() if a.startswith(("5","6","7")));fin=sum(v for a,v in vals.items() if a.startswith("8"))
    # Deliberately no claim that these lines are production-ready SRU field codes.
    info="#GEN#\n#PROGRAM Min Bokföring v13\n#FORMAT PC8\n#FNAMN NE-underlag\n#NAMN Företag\n#EOF#\n"
    blank=f"#BLANKETT NE\n#R1 {sales:.2f}\n#R5 {goods:.2f}\n#R6 {ext:.2f}\n#R8 {fin:.2f}\n#EOF#\n"
    open(os.path.join(EXPORT,"INFO.SRU"),"w",encoding="cp1252",errors="replace").write(info)
    open(os.path.join(EXPORT,"BLANKETTER.SRU"),"w",encoding="cp1252",errors="replace").write(blank)
    flash("SRU-underlag skapades i Data/Export. Det måste verifieras mot aktuell Skatteverket-specifikation innan inlämning.")
    return redirect(url_for("ne"))

@app.route("/backup")
def backup():
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S");path=os.path.join(BACKUPS,f"Min_Bokforing_backup_{stamp}.zip")
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(DB):z.write(DB,"bokforing.db")
        for root,dirs,files in os.walk(RECEIPTS):
            for f in files:z.write(os.path.join(root,f),os.path.relpath(os.path.join(root,f),DATA))
    flash("Backup skapad: "+os.path.basename(path))
    return redirect(url_for("settings"))

if __name__=="__main__":
    conn()
    if webview:
        import werkzeug.serving
        threading.Thread(target=lambda:werkzeug.serving.run_simple("127.0.0.1",5000,app,threaded=True,use_reloader=False),daemon=True).start()
        webview.create_window("Min Bokföring v13","http://127.0.0.1:5000",width=1280,height=850,resizable=True)
        webview.start()
    else:app.run(host="127.0.0.1",port=5000)
