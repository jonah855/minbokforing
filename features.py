import os, re, sqlite3, datetime, json, hashlib, html
from flask import request, redirect, render_template_string, flash, url_for, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def register_features(app, conn, setting, set_setting, create_voucher, money, AC, ROOT, EXPORT):
    """Registers the second-stage business modules while reusing the existing ledger engine."""

    def ensure_schema():
        c=conn()
        c.executescript('''
        CREATE TABLE IF NOT EXISTS customers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, orgnr TEXT, address TEXT,
          zip TEXT, city TEXT, email TEXT, phone TEXT, reference TEXT, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS articles(
          id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, name TEXT NOT NULL, description TEXT,
          price_ex_vat REAL NOT NULL DEFAULT 0, vat_rate INTEGER NOT NULL DEFAULT 25, unit TEXT DEFAULT 'st');
        CREATE TABLE IF NOT EXISTS invoices(
          id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER UNIQUE NOT NULL, customer_id INTEGER NOT NULL,
          invoice_date TEXT NOT NULL, due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
          notes TEXT, subtotal REAL NOT NULL DEFAULT 0, vat REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
          created TEXT NOT NULL, sent_at TEXT, paid_at TEXT, voucher_id INTEGER);
        CREATE TABLE IF NOT EXISTS invoice_lines(
          id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, article_id INTEGER,
          description TEXT NOT NULL, quantity REAL NOT NULL DEFAULT 1, unit TEXT DEFAULT 'st',
          price_ex_vat REAL NOT NULL DEFAULT 0, vat_rate INTEGER NOT NULL DEFAULT 25, discount REAL NOT NULL DEFAULT 0,
          line_total REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS quotes(
          id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER UNIQUE NOT NULL, customer_id INTEGER NOT NULL,
          quote_date TEXT NOT NULL, valid_until TEXT, status TEXT NOT NULL DEFAULT 'draft', notes TEXT,
          subtotal REAL NOT NULL DEFAULT 0, vat REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quote_lines(
          id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL, article_id INTEGER,
          description TEXT NOT NULL, quantity REAL NOT NULL DEFAULT 1, unit TEXT DEFAULT 'st',
          price_ex_vat REAL NOT NULL DEFAULT 0, vat_rate INTEGER NOT NULL DEFAULT 25, discount REAL NOT NULL DEFAULT 0,
          line_total REAL NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_invoice_status ON invoices(status);
        CREATE INDEX IF NOT EXISTS idx_invoice_customer ON invoices(customer_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_amount ON transactions(amount);
        ''')
        c.commit()
    ensure_schema()

    def shell(body):
        # Reuse the v13 page shell from app.py without duplicating the entire application.
        template='''<!doctype html><html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",Arial;background:#f5f5f7;color:#171717;margin:0}.side{position:fixed;left:0;top:0;bottom:0;width:220px;background:#111;color:#fff;padding:22px 16px;box-sizing:border-box}.side h2{margin:0 0 22px}.side a{display:block;color:#fff;text-decoration:none;padding:9px;border-radius:8px}.side a:hover{background:#333}.main{margin-left:220px;max-width:1300px;padding:28px}.card{background:#fff;border-radius:16px;padding:22px;margin-bottom:16px;box-shadow:0 2px 12px #0001}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{font-size:27px;font-weight:700}.muted{color:#666}button,.btn{border:0;background:#111;color:#fff;padding:10px 14px;border-radius:9px;cursor:pointer;text-decoration:none;display:inline-block}button.red{background:#9b1c1c}.danger{background:#fff0f0}.warn{background:#fff7dd;padding:12px;border-radius:10px}.good{background:#e9f8ee;padding:12px;border-radius:10px}input,select,textarea{padding:10px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box;width:100%;margin-top:5px}textarea{min-height:90px}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #eee;text-align:left}.row{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.actions{display:flex;gap:8px;flex-wrap:wrap}@media(max-width:850px){.side{position:static;width:auto}.main{margin:0;padding:16px}.grid,.row{grid-template-columns:1fr}}</style></head><body><aside class=side><h2>Min Bokföring</h2><a href="/">Översikt</a><a href="/bank">Bank</a><a href="/receipts">Kvitton</a><a href="/journal">Verifikationer</a><a href="/customers">Kunder</a><a href="/articles">Artiklar</a><a href="/invoices">Fakturor</a><a href="/quotes">Offerter</a><a href="/vat">Moms</a><a href="/reports">Rapporter</a><a href="/close">Årsbokslut</a><a href="/ne">NE/SRU</a><a href="/settings">Inställningar</a></aside><main class=main>{% for m in get_flashed_messages() %}<div class=good>{{m}}</div>{% endfor %}{{body|safe}}</main></body></html>'''
        return render_template_string(template, body=body)

    def next_number(table, start):
        c=conn(); r=c.execute(f'SELECT COALESCE(MAX(number),?)+1 n FROM {table}',(start-1,)).fetchone(); return r['n']

    def invoice_totals(lines):
        subtotal=vat=0
        clean=[]
        for x in lines:
            qty=float(x.get('quantity',1) or 1); price=float(x.get('price_ex_vat',0) or 0); disc=float(x.get('discount',0) or 0); rate=int(x.get('vat_rate',25) or 0)
            base=round(qty*price*(1-disc/100),2); v=round(base*rate/100,2); subtotal+=base; vat+=v
            clean.append((x.get('article_id'),x.get('description','').strip(),qty,x.get('unit','st'),price,rate,disc,base))
        return round(subtotal,2),round(vat,2),round(subtotal+vat,2),clean

    @app.route('/customers',methods=['GET','POST'])
    def customers():
        c=conn()
        if request.method=='POST':
            name=request.form.get('name','').strip()
            if not name: flash('Kundnamn krävs.'); return redirect(url_for('customers'))
            c.execute('INSERT INTO customers(name,orgnr,address,zip,city,email,phone,reference,created) VALUES(?,?,?,?,?,?,?,?,?)',(
                name,request.form.get('orgnr','').strip(),request.form.get('address','').strip(),request.form.get('zip','').strip(),request.form.get('city','').strip(),request.form.get('email','').strip(),request.form.get('phone','').strip(),request.form.get('reference','').strip(),datetime.datetime.now().isoformat()))
            c.commit(); flash('Kunden skapades.'); return redirect(url_for('customers'))
        rows=c.execute('SELECT * FROM customers ORDER BY name').fetchall()
        body=render_template_string('''<div class=card><h1>Kunder</h1><form method=post><div class=row><p><label>Namn<input name=name required></label></p><p><label>Org.nr/personuppgift<input name=orgnr></label></p><p><label>Adress<input name=address></label></p><p><label>Postnummer<input name=zip></label></p><p><label>Ort<input name=city></label></p><p><label>E-post<input type=email name=email></label></p><p><label>Telefon<input name=phone></label></p><p><label>Referens<input name=reference></label></p></div><button>Skapa kund</button></form></div><div class=card><table><tr><th>Kund</th><th>Org.nr</th><th>Kontakt</th><th></th></tr>{%for r in rows%}<tr><td>{{r.name}}</td><td>{{r.orgnr or '–'}}</td><td>{{r.email or '–'}}</td><td><a class=btn href="/invoices/new?customer={{r.id}}">Ny faktura</a></td></tr>{%endfor%}</table></div>''',rows=rows)
        return shell(body)

    @app.route('/articles',methods=['GET','POST'])
    def articles():
        c=conn()
        if request.method=='POST':
            name=request.form.get('name','').strip(); price=money(request.form.get('price','0'))
            if not name or price is None or price<0: flash('Artikel och pris måste vara giltiga.'); return redirect(url_for('articles'))
            c.execute('INSERT INTO articles(number,name,description,price_ex_vat,vat_rate,unit) VALUES(?,?,?,?,?,?)',(
                request.form.get('number','').strip(),name,request.form.get('description','').strip(),price,int(request.form.get('vat_rate','25')),request.form.get('unit','st').strip() or 'st'))
            c.commit(); flash('Artikeln skapades.'); return redirect(url_for('articles'))
        rows=c.execute('SELECT * FROM articles ORDER BY number,name').fetchall()
        body=render_template_string('''<div class=card><h1>Artiklar</h1><form method=post><div class=row><p><label>Artikelnummer<input name=number></label></p><p><label>Benämning<input name=name required></label></p><p><label>Beskrivning<input name=description></label></p><p><label>Pris exkl. moms<input name=price inputmode=decimal required></label></p><p><label>Moms<select name=vat_rate><option>25</option><option>12</option><option>6</option><option>0</option></select></label></p><p><label>Enhet<input name=unit value=st></label></p></div><button>Skapa artikel</button></form></div><div class=card><table><tr><th>Nr</th><th>Artikel</th><th>Pris exkl. moms</th><th>Moms</th></tr>{%for r in rows%}<tr><td>{{r.number or '–'}}</td><td>{{r.name}}</td><td>{{'%.2f'|format(r.price_ex_vat)}} kr</td><td>{{r.vat_rate}} %</td></tr>{%endfor%}</table></div>''',rows=rows)
        return shell(body)

    @app.route('/invoices')
    def invoices():
        c=conn(); q=request.args.get('q','').strip(); status=request.args.get('status','')
        sql='SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE 1=1'; args=[]
        if q: sql+=' AND (c.name LIKE ? OR CAST(i.number AS TEXT) LIKE ?)'; args += [f'%{q}%',f'%{q}%']
        if status: sql+=' AND i.status=?'; args.append(status)
        rows=c.execute(sql+' ORDER BY i.number DESC',args).fetchall()
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Fakturor</h1><a class=btn href=/invoices/new>+ Ny faktura</a></div><form><div class=row><input name=q placeholder="Sök kund eller fakturanummer" value="{{q}}"><select name=status><option value="">Alla statusar</option>{%for s in ['draft','sent','unpaid','partpaid','paid','overdue','credit','cancelled']%}<option value={{s}} {%if status==s%}selected{%endif%}>{{s}}</option>{%endfor%}</select></div><br><button>Filtrera</button></form></div><div class=card><table><tr><th>Faktura</th><th>Kund</th><th>Datum</th><th>Förfallo</th><th>Belopp</th><th>Status</th><th></th></tr>{%for r in rows%}<tr><td>#{{r.number}}</td><td>{{r.customer_name}}</td><td>{{r.invoice_date}}</td><td>{{r.due_date}}</td><td>{{'%.2f'|format(r.total)}} kr</td><td>{{r.status}}</td><td><a class=btn href="/invoice/{{r.id}}">Öppna</a></td></tr>{%endfor%}</table></div>''',rows=rows,q=q,status=status)
        return shell(body)

    @app.route('/invoices/new',methods=['GET','POST'])
    def invoice_new():
        c=conn(); customers=c.execute('SELECT * FROM customers ORDER BY name').fetchall(); articles=c.execute('SELECT * FROM articles ORDER BY name').fetchall()
        if request.method=='POST':
            cid=int(request.form.get('customer_id','0')); date=request.form.get('invoice_date','').strip(); due=request.form.get('due_date','').strip()
            if not c.execute('SELECT 1 FROM customers WHERE id=?',(cid,)).fetchone() or not date or not due: flash('Välj kund och fyll i datumen.'); return redirect(url_for('invoice_new'))
            raw=request.form.get('lines_json','[]')
            try: lines=json.loads(raw); subtotal,vat,total,clean=invoice_totals(lines)
            except Exception: flash('Fakturaraderna kunde inte läsas.'); return redirect(url_for('invoice_new'))
            num=next_number('invoices',1000); now=datetime.datetime.now().isoformat()
            c.execute('INSERT INTO invoices(number,customer_id,invoice_date,due_date,status,notes,subtotal,vat,total,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(num,cid,date,due,'draft',request.form.get('notes',''),subtotal,vat,total,now)); iid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
            for a,d,q,u,p,r,disc,lt in clean:c.execute('INSERT INTO invoice_lines(invoice_id,article_id,description,quantity,unit,price_ex_vat,vat_rate,discount,line_total) VALUES(?,?,?,?,?,?,?,?,?)',(iid,a,d,q,u,p,r,disc,lt))
            c.commit(); flash(f'Faktura #{num} skapad som utkast.'); return redirect(url_for('invoice_view',invoice_id=iid))
        today=datetime.date.today(); due=today+datetime.timedelta(days=30)
        body=render_template_string('''<div class=card><h1>Ny faktura</h1>{%if not customers%}<div class=warn>Skapa en kund först i kundregistret.</div>{%else%}<form method=post id=f><div class=row><p><label>Kund<select name=customer_id required>{%for c in customers%}<option value={{c.id}} {%if request.args.get('customer')|int==c.id%}selected{%endif%}>{{c.name}}</option>{%endfor%}</select></label></p><p><label>Fakturadatum<input type=date name=invoice_date value={{today}} required></label></p><p><label>Förfallodatum<input type=date name=due_date value={{due}} required></label></p><p><label>Notering<textarea name=notes></textarea></label></p></div><h3>Fakturarader</h3><div id=lines></div><input type=hidden name=lines_json id=lj><div class=actions><button type=button onclick=addLine()>+ Lägg till rad</button><button onclick=submitForm()>Skapa faktura</button></div></form><script>const arts={{arts|safe}};let ls=[];function addLine(){ls.push({article_id:null,description:'',quantity:1,unit:'st',price_ex_vat:0,vat_rate:25,discount:0});render()}function render(){document.getElementById('lines').innerHTML=ls.map((x,i)=>`<div class="row card"><input placeholder="Beskrivning" value="${x.description}" oninput="ls[${i}].description=this.value"><input placeholder="Antal" type=number step=0.01 value="${x.quantity}" oninput="ls[${i}].quantity=this.value"><input placeholder="Pris exkl moms" type=number step=0.01 value="${x.price_ex_vat}" oninput="ls[${i}].price_ex_vat=this.value"><select onchange="ls[${i}].vat_rate=this.value"><option ${x.vat_rate==25?'selected':''}>25</option><option ${x.vat_rate==12?'selected':''}>12</option><option ${x.vat_rate==6?'selected':''}>6</option><option ${x.vat_rate==0?'selected':''}>0</option></select></div>`).join('')}function submitForm(){document.getElementById('lj').value=JSON.stringify(ls)}addLine()</script>{%endif%}</div>''',customers=customers,arts=json.dumps([dict(x) for x in articles],ensure_ascii=False),today=today.isoformat(),due=due.isoformat())
        return shell(body)

    @app.route('/invoice/<int:invoice_id>')
    def invoice_view(invoice_id):
        c=conn(); i=c.execute('SELECT i.*,c.name customer_name,c.orgnr,c.address,c.zip,c.city,c.email,c.phone FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone()
        if not i: return 'Fakturan hittades inte',404
        lines=c.execute('SELECT * FROM invoice_lines WHERE invoice_id=?',(invoice_id,)).fetchall()
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Faktura #{{i.number}}</h1><a class=btn href="/invoice/{{i.id}}.pdf">PDF</a>{%if i.status=='draft'%}<form method=post action="/invoice/{{i.id}}/send"><button>Markera skickad</button></form>{%endif%}{%if i.status not in ['paid','cancelled']%}<form method=post action="/invoice/{{i.id}}/paid"><button>Markera betald</button></form>{%endif%}</div><p><b>{{i.customer_name}}</b><br>{{i.address or ''}}<br>{{i.zip or ''}} {{i.city or ''}}<br>{{i.email or ''}}</p><p>Fakturadatum: {{i.invoice_date}} · Förfallodatum: {{i.due_date}} · Status: <b>{{i.status}}</b></p><table><tr><th>Beskrivning</th><th>Antal</th><th>Pris exkl.</th><th>Moms</th><th>Summa</th></tr>{%for l in lines%}<tr><td>{{l.description}}</td><td>{{l.quantity}} {{l.unit}}</td><td>{{'%.2f'|format(l.price_ex_vat)}} kr</td><td>{{l.vat_rate}} %</td><td>{{'%.2f'|format(l.line_total)}} kr</td></tr>{%endfor%}</table><p>Netto: <b>{{'%.2f'|format(i.subtotal)}} kr</b><br>Moms: <b>{{'%.2f'|format(i.vat)}} kr</b><br><strong>Total: {{'%.2f'|format(i.total)}} kr</strong></p></div>''',i=i,lines=lines)
        return shell(body)

    @app.route('/invoice/<int:invoice_id>/send',methods=['POST'])
    def invoice_send(invoice_id):
        c=conn(); i=c.execute('SELECT * FROM invoices WHERE id=?',(invoice_id,)).fetchone()
        if not i:return redirect(url_for('invoices'))
        c.execute("UPDATE invoices SET status='unpaid',sent_at=? WHERE id=?",(datetime.datetime.now().isoformat(),invoice_id));c.commit(); flash(f'Faktura #{i.number} markerad som skickad/obetald.'); return redirect(url_for('invoice_view',invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>/paid',methods=['POST'])
    def invoice_paid(invoice_id):
        c=conn(); i=c.execute('SELECT * FROM invoices WHERE id=?',(invoice_id,)).fetchone()
        if not i:return redirect(url_for('invoices'))
        if i.status=='paid':return redirect(url_for('invoice_view',invoice_id=invoice_id))
        # Cash method: when payment is confirmed, book bank against sales/VAT. This is deliberately explicit user action.
        net=i.subtotal; vat=i.vat; lines=[('1930',i.total,0,''),('3001',0,net,'25' if vat else ''),('2611',0,vat,'25' if vat else '')]
        try:
            vid=create_voucher(i.invoice_date,f'Betalning faktura #{i.number}','invoice_payment',None,None,lines)
            c.execute("UPDATE invoices SET status='paid',paid_at=?,voucher_id=? WHERE id=?",(datetime.datetime.now().isoformat(),vid,invoice_id)); c.commit(); flash('Fakturan markerades betald och betalningen bokfördes.')
        except Exception as e: flash('Kunde inte bokföra betalningen: '+str(e))
        return redirect(url_for('invoice_view',invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>.pdf')
    def invoice_pdf(invoice_id):
        c=conn();i=c.execute('SELECT i.*,c.name customer_name,c.orgnr,c.address,c.zip,c.city,c.email FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone(); lines=c.execute('SELECT * FROM invoice_lines WHERE invoice_id=?',(invoice_id,)).fetchall()
        if not i:return 'Not found',404
        path=os.path.join(EXPORT,f'Faktura_{i.number}.pdf'); p=canvas.Canvas(path,pagesize=A4);w,h=A4
        p.setFont('Helvetica-Bold',22);p.drawString(45,h-55,'FAKTURA');p.setFont('Helvetica',10);p.drawRightString(w-45,h-55,f'#{i.number}')
        y=h-95;p.drawString(45,y,'Kund: '+i.customer_name);y-=16
        for s in [i.address,i.zip+' '+i.city if i.zip or i.city else '',i.email or '']:
            if s:p.drawString(45,y,s);y-=14
        y-=12;p.drawString(45,y,f'Fakturadatum: {i.invoice_date}');p.drawString(300,y,f'Förfallodatum: {i.due_date}');y-=28
        p.setFont('Helvetica-Bold',10);p.drawString(45,y,'Beskrivning');p.drawRightString(390,y,'Antal');p.drawRightString(480,y,'Pris');p.drawRightString(w-45,y,'Summa');y-=16;p.setFont('Helvetica',9)
        for l in lines:
            p.drawString(45,y,l.description[:60]);p.drawRightString(390,y,f'{l.quantity:g}');p.drawRightString(480,y,f'{l.price_ex_vat:.2f}');p.drawRightString(w-45,y,f'{l.line_total:.2f}');y-=14
        y-=12;p.drawRightString(w-45,y,f'Netto: {i.subtotal:.2f} kr');y-=16;p.drawRightString(w-45,y,f'Moms: {i.vat:.2f} kr');y-=18;p.setFont('Helvetica-Bold',12);p.drawRightString(w-45,y,f'TOTAL: {i.total:.2f} kr');p.save();return send_file(path,as_attachment=True,download_name=f'Faktura_{i.number}.pdf')

    @app.route('/quotes',methods=['GET','POST'])
    def quotes():
        c=conn();customers=c.execute('SELECT * FROM customers ORDER BY name').fetchall()
        if request.method=='POST':
            cid=int(request.form.get('customer_id','0'));date=request.form.get('quote_date','');valid=request.form.get('valid_until','');
            try:lines=json.loads(request.form.get('lines_json','[]')); subtotal,vat,total,clean=invoice_totals(lines)
            except Exception:flash('Offertdata kunde inte läsas.');return redirect(url_for('quotes'))
            num=next_number('quotes',2000);c.execute('INSERT INTO quotes(number,customer_id,quote_date,valid_until,status,notes,subtotal,vat,total,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(num,cid,date,valid,'draft',request.form.get('notes',''),subtotal,vat,total,datetime.datetime.now().isoformat()));qid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
            for a,d,q,u,p,r,disc,lt in clean:c.execute('INSERT INTO quote_lines(quote_id,article_id,description,quantity,unit,price_ex_vat,vat_rate,discount,line_total) VALUES(?,?,?,?,?,?,?,?,?)',(qid,a,d,q,u,p,r,disc,lt))
            c.commit();flash(f'Offert #{num} skapad.');return redirect(url_for('quotes'))
        rows=c.execute('SELECT q.*,c.name customer_name FROM quotes q JOIN customers c ON c.id=q.customer_id ORDER BY q.number DESC').fetchall();articles=c.execute('SELECT * FROM articles ORDER BY name').fetchall();today=datetime.date.today();valid=today+datetime.timedelta(days=30)
        body=render_template_string('''<div class=card><h1>Offerter</h1>{%if customers%}<form method=post><div class=row><select name=customer_id required>{%for c in customers%}<option value={{c.id}}>{{c.name}}</option>{%endfor%}</select><input type=date name=quote_date value={{today}} required><input type=date name=valid_until value={{valid}}><textarea name=notes placeholder=Notering></textarea></div><div id=ql></div><input type=hidden name=lines_json id=qj><button type=button onclick=add()>+ Rad</button> <button onclick=send()>Skapa offert</button></form><script>let ls=[];function add(){ls.push({description:'',quantity:1,unit:'st',price_ex_vat:0,vat_rate:25,discount:0});r()}function r(){ql.innerHTML=ls.map((x,i)=>`<div class=row><input placeholder=Beskrivning oninput="ls[${i}].description=this.value"><input type=number step=.01 value=1 oninput="ls[${i}].quantity=this.value"><input type=number step=.01 value=0 oninput="ls[${i}].price_ex_vat=this.value"><select onchange="ls[${i}].vat_rate=this.value"><option>25</option><option>12</option><option>6</option><option>0</option></select></div>`).join('')}function send(){qj.value=JSON.stringify(ls)}add()</script>{%else%}<div class=warn>Skapa en kund först.</div>{%endif%}</div><div class=card><table><tr><th>Offert</th><th>Kund</th><th>Datum</th><th>Status</th><th>Total</th></tr>{%for q in rows%}<tr><td>#{{q.number}}</td><td>{{q.customer_name}}</td><td>{{q.quote_date}}</td><td>{{q.status}}</td><td>{{'%.2f'|format(q.total)}} kr</td></tr>{%endfor%}</table></div>''',customers=customers,rows=rows,articles=articles,today=today.isoformat(),valid=valid.isoformat())
        return shell(body)

    @app.route('/match')
    def match_payments():
        c=conn(); invoices=c.execute("SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.status IN ('sent','unpaid','overdue') ORDER BY i.due_date").fetchall(); tx=c.execute("SELECT * FROM transactions WHERE status='new' ORDER BY date").fetchall(); suggestions=[]
        for t in tx:
            for i in invoices:
                score=0
                if abs(abs(t['amount'])-i['total'])<0.01:score+=5
                if str(i['number']) in (t['reference'] or ''):score+=10
                if i['customer_name'].lower() in (t['description'] or '').lower():score+=4
                if score:suggestions.append((t,i,score))
        body=render_template_string('''<div class=card><h1>Betalningsmatchning</h1><p class=muted>Förslag baseras på fakturanummer/OCR-referens, belopp, kund och banktext. Ingen matchning godkänns automatiskt.</p>{%for t,i,s in suggestions%}<div class=card><b>{{t.date}}</b> · {{t.description}} · {{'%.2f'|format(t.amount)}} kr<br>Förslag: <b>faktura #{{i.number}} – {{i.customer_name}}</b> <span class=muted>träffpoäng {{s}}</span><form method=post action=/match/{{t.id}}/{{i.id}}><button>Godkänn matchning</button></form></div>{%else%}<p>Inga säkra förslag just nu.</p>{%endfor%}</div>''',suggestions=suggestions)
        return shell(body)

    @app.route('/match/<int:tid>/<int:iid>',methods=['POST'])
    def approve_match(tid,iid):
        c=conn();t=c.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone();i=c.execute('SELECT * FROM invoices WHERE id=?',(iid,)).fetchone()
        if not t or not i:flash('Matchningen kunde inte hittas.');return redirect(url_for('match_payments'))
        if abs(abs(t['amount'])-i['total'])>0.01:flash('Beloppet stämmer inte exakt. Matchningen stoppades.');return redirect(url_for('match_payments'))
        try:
            net=i['subtotal'];vat=i['vat'];lines=[('1930',abs(t['amount']),0,''),('3001',0,net,'25' if vat else ''),('2611',0,vat,'25' if vat else '')]
            vid=create_voucher(t['date'],f'Betalning faktura #{i["number"]}','bank_invoice_match',tid,None,lines)
            c.execute("UPDATE invoices SET status='paid',paid_at=?,voucher_id=? WHERE id=?",(datetime.datetime.now().isoformat(),vid,iid));c.commit();flash('Betalningen matchades och bokfördes.')
        except Exception as e:flash('Matchningen kunde inte bokföras: '+str(e))
        return redirect(url_for('match_payments'))

    @app.route('/audit')
    def audit_view():
        c=conn();rows=c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 300').fetchall();body=render_template_string('''<div class=card><h1>Auditlogg</h1><p class=muted>Senaste viktiga ändringar och bokföringshändelser.</p><table><tr><th>Tid</th><th>Åtgärd</th><th>Objekt</th><th>Detalj</th></tr>{%for r in rows%}<tr><td>{{r.created}}</td><td>{{r.action}}</td><td>{{r.object_type}} #{{r.object_id}}</td><td>{{r.details}}</td></tr>{%endfor%}</table></div>''',rows=rows);return shell(body)

    @app.route('/system')
    def system_dashboard():
        c=conn();todo=c.execute("SELECT count(*) n FROM transactions WHERE status='new'").fetchone()['n'];receipts=c.execute("SELECT count(*) n FROM receipts WHERE matched_tx IS NULL").fetchone()['n'];unpaid=c.execute("SELECT count(*) n FROM invoices WHERE status IN ('sent','unpaid','overdue')").fetchone()['n'];overdue=c.execute("SELECT count(*) n FROM invoices WHERE status IN ('sent','unpaid') AND due_date < ?",(datetime.date.today().isoformat(),)).fetchone()['n'];
        body=render_template_string('''<div class=card><h1>Ekonomiöversikt</h1><div class=grid><div><div class=stat>{{todo}}</div><div class=muted>bankhändelser att granska</div></div><div><div class=stat>{{receipts}}</div><div class=muted>kvitton utan matchning</div></div><div><div class=stat>{{unpaid}}</div><div class=muted>obetalda fakturor</div></div><div><div class=stat>{{overdue}}</div><div class=muted>förfallna fakturor</div></div></div></div><div class=card><h2>Snabblänkar</h2><div class=actions><a class=btn href=/bank>Granska bank</a><a class=btn href=/receipts>Granska kvitton</a><a class=btn href=/match>Matcha betalningar</a><a class=btn href=/invoices/new>Skapa faktura</a><a class=btn href=/audit>Auditlogg</a></div></div>''',todo=todo,receipts=receipts,unpaid=unpaid,overdue=overdue);return shell(body)

    # Upgrade the original home route into the new dashboard while preserving all existing routes.
    app.view_functions['home']=system_dashboard
