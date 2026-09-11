import os, datetime, json, sqlite3
from flask import request, redirect, render_template_string, flash, url_for, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

STATUS_LABELS = {
    'draft': 'Utkast', 'unpaid': 'Obetald', 'partpaid': 'Delbetald',
    'paid': 'Betald', 'overdue': 'Förfallen', 'credit': 'Kreditfaktura',
    'cancelled': 'Makulerad'
}


def register_invoice_upgrade(app, conn, setting, set_setting, create_voucher, money, AC, ROOT, EXPORT):
    """Complete invoicing layer using the existing SQLite accounting engine."""

    def ensure_schema():
        c = conn()
        additions = [
            ('customers', 'vat_no', 'TEXT'),
            ('invoices', 'delivery_date', 'TEXT'),
            ('invoices', 'buyer_vat_no', 'TEXT'),
            ('invoices', 'reference', 'TEXT'),
            ('invoices', 'our_reference', 'TEXT'),
            ('invoices', 'marking', 'TEXT'),
            ('invoices', 'invoice_fee', 'REAL DEFAULT 0'),
            ('invoices', 'invoice_discount', 'REAL DEFAULT 0'),
            ('invoices', 'payment_terms_days', 'INTEGER DEFAULT 30'),
            ('invoices', 'currency', "TEXT DEFAULT 'SEK'"),
            ('invoices', 'issued_at', 'TEXT'),
            ('invoices', 'cancelled_at', 'TEXT'),
            ('invoices', 'payment_reference', 'TEXT'),
            ('invoices', 'accounting_method', "TEXT DEFAULT 'cash'"),
        ]
        for table, col, typ in additions:
            cols = {r['name'] for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
            if col not in cols:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
        c.commit()

    ensure_schema()

    def company():
        return {
            'name': setting('company_name', ''), 'orgnr': setting('company_orgnr', ''),
            'vat_no': setting('company_vat', ''), 'address': setting('company_address', ''),
            'zip': setting('company_zip', ''), 'city': setting('company_city', ''),
            'email': setting('company_email', ''), 'phone': setting('company_phone', ''),
            'bank': setting('company_bank', ''), 'account': setting('company_bank_account', ''),
            'swish': setting('company_swish', ''),
        }

    def calc_lines(lines, invoice_discount=0.0, invoice_fee=0.0):
        clean, subtotal, vat = [], 0.0, 0.0
        invoice_discount = float(invoice_discount or 0)
        invoice_fee = float(invoice_fee or 0)
        if not 0 <= invoice_discount <= 100 or invoice_fee < 0:
            raise ValueError('Kontrollera fakturarabatt och faktureringsavgift.')
        for x in lines:
            desc = str(x.get('description', '')).strip()
            if not desc:
                raise ValueError('Alla fakturarader måste ha en beskrivning.')
            try:
                qty = float(x.get('quantity', 0) or 0)
                price = float(x.get('price_ex_vat', 0) or 0)
                disc = float(x.get('discount', 0) or 0)
                rate = int(x.get('vat_rate', 25) or 0)
            except (ValueError, TypeError):
                raise ValueError('Kontrollera antal, pris, rabatt och momssats.')
            if qty <= 0 or price < 0 or not 0 <= disc <= 100 or rate not in (0, 6, 12, 25):
                raise ValueError('Kontrollera antal, pris, rabatt och momssats.')
            base = round(qty * price * (1 - disc / 100), 2)
            clean.append((x.get('article_id'), desc, qty, str(x.get('unit', 'st') or 'st'), price, rate, disc, base))
            subtotal += base
        discount_amount = round(subtotal * invoice_discount / 100, 2)
        # Invoice-level discount is allocated proportionally across VAT rates below.
        taxable_after_discount = round(subtotal - discount_amount, 2)
        fee = round(invoice_fee, 2)
        if subtotal:
            for idx, row in enumerate(clean):
                share = row[7] / subtotal
                row_base = round(row[7] - discount_amount * share + fee * share, 2)
                clean[idx] = row[:7] + (row_base,)
        elif fee:
            clean.append((None, 'Faktureringsavgift', 1.0, 'st', fee, 25, 0.0, fee))
        for row in clean:
            vat += round(row[7] * row[5] / 100, 2)
        taxable_after_discount = round(taxable_after_discount + fee, 2)
        return taxable_after_discount, round(vat, 2), round(taxable_after_discount + vat, 2), clean

    def tax_summary(lines):
        out = {}
        for l in lines:
            rate = int(l['vat_rate'])
            out.setdefault(rate, {'base': 0.0, 'vat': 0.0})
            out[rate]['base'] += float(l['line_total'])
            out[rate]['vat'] += round(float(l['line_total']) * rate / 100, 2)
        for v in out.values():
            v['base'] = round(v['base'], 2); v['vat'] = round(v['vat'], 2)
        return out

    def invoice_validation(i, lines, c):
        co = company(); problems = []
        required_company = [
            ('företagsnamn', co['name']), ('säljaradress', co['address']),
            ('säljarens postnummer/ort', (co['zip'] + ' ' + co['city']).strip()),
            ('säljarens momsregistreringsnummer', co['vat_no'])
        ]
        for label, value in required_company:
            if not value:
                problems.append(f'Saknar {label}. Fyll i Fakturainställningar.')
        if not i['customer_name']: problems.append('Kundnamn saknas.')
        if not i['address'] or not i['city']:
            problems.append('Kundens fullständiga adress måste finnas på en fullständig faktura.')
        if not i['invoice_date'] or not i['due_date']:
            problems.append('Fakturadatum och förfallodatum krävs.')
        if not lines: problems.append('Fakturan måste innehålla minst en fakturarad.')
        return problems

    def invoice_page(body):
        shell = '''<!doctype html><html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",Arial;background:#f5f5f7;color:#171717;margin:0}.side{position:fixed;left:0;top:0;bottom:0;width:225px;background:#111;color:#fff;padding:22px 16px;box-sizing:border-box}.side h2{margin:0 0 22px}.side a{display:block;color:#fff;text-decoration:none;padding:9px;border-radius:8px}.side a:hover{background:#333}.main{margin-left:225px;max-width:1400px;padding:28px}.card{background:#fff;border-radius:16px;padding:22px;margin-bottom:16px;box-shadow:0 2px 12px #0001}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.row{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.actions{display:flex;gap:8px;flex-wrap:wrap}.muted{color:#666}.good{background:#e9f8ee;padding:12px;border-radius:10px}.bad{background:#fff0f0;padding:12px;border-radius:10px}.warn{background:#fff7dd;padding:12px;border-radius:10px}.pill{display:inline-block;padding:5px 9px;border-radius:12px;background:#eee;font-size:12px}button,.btn{border:0;background:#111;color:#fff;padding:10px 14px;border-radius:9px;cursor:pointer;text-decoration:none;display:inline-block}button.red{background:#9b1c1c}input,select,textarea{padding:10px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box;width:100%;margin-top:5px;font:inherit}textarea{min-height:80px}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #eee;text-align:left}.section-title{display:flex;align-items:center;gap:10px;margin:4px 0 16px}.section-title h2{margin:0}.summary{margin-left:auto;min-width:300px}.total{font-size:24px;font-weight:700}.help{font-size:12px;color:#666;margin-top:4px}.line-card{border:1px solid #eee;box-shadow:none}.danger-text{color:#9b1c1c}@media(max-width:850px){.side{position:static;width:auto}.main{margin:0;padding:16px}.grid,.row{grid-template-columns:1fr}.summary{margin-left:0;min-width:0}}
</style></head><body><aside class="side"><h2>Min Bokföring</h2><a href="/">Översikt</a><a href="/bank">Bank</a><a href="/receipts">Kvitton</a><a href="/journal">Verifikationer</a><a href="/customers">Kunder</a><a href="/articles">Artiklar</a><a href="/invoices">Fakturor</a><a href="/quotes">Offerter</a><a href="/vat">Moms</a><a href="/reports">Rapporter</a><a href="/invoice-settings">Fakturainställningar</a><a href="/settings">Inställningar</a></aside><main class="main">{% for m in get_flashed_messages() %}<div class="good">{{m}}</div>{% endfor %}{{body|safe}}</main></body></html>'''
        return render_template_string(shell, body=body)

    @app.route('/invoice-settings', methods=['GET', 'POST'])
    def invoice_settings():
        if request.method == 'POST':
            fields = ['company_name','company_orgnr','company_vat','company_address','company_zip','company_city','company_email','company_phone','company_bank','company_bank_account','company_swish','accounting_method']
            for k in fields: set_setting(k, request.form.get(k, '').strip())
            flash('Fakturainställningarna sparades.')
            return redirect(url_for('invoice_settings'))
        co = company(); co['accounting_method'] = setting('accounting_method', 'cash')
        body = render_template_string('''<div class="card"><h1>Fakturainställningar</h1><p class="muted">Företagets uppgifter används automatiskt på fakturor. Fyll i uppgifterna här en gång så slipper du skriva dem på varje faktura.</p><form method=post><div class=row><label>Företagsnamn<input name=company_name value="{{co.name}}" required></label><label>Organisationsnummer/personnummer<input name=company_orgnr value="{{co.orgnr}}"></label><label>Säljarens momsregistreringsnummer<input name=company_vat value="{{co.vat_no}}" placeholder="SE...01" required></label><label>Adress<input name=company_address value="{{co.address}}" required></label><label>Postnummer<input name=company_zip value="{{co.zip}}" required></label><label>Ort<input name=company_city value="{{co.city}}" required></label><label>E-post<input type=email name=company_email value="{{co.email}}"></label><label>Telefon<input name=company_phone value="{{co.phone}}"></label><label>Bankuppgift<input name=company_bank value="{{co.bank}}"></label><label>Bankkonto/IBAN<input name=company_bank_account value="{{co.account}}"></label><label>Swish<input name=company_swish value="{{co.swish}}"></label><label>Bokföringsmetod<select name=accounting_method><option value=cash {%if co.accounting_method=='cash'%}selected{%endif%}>Bokslutsmetoden (kontantmetoden)</option><option value=accrual {%if co.accounting_method=='accrual'%}selected{%endif%}>Faktureringsmetoden</option></select></label></div><br><button>Spara</button></form></div>''', co=co)
        return invoice_page(body)

    @app.route('/invoices', methods=['GET'])
    def invoices_upgrade():
        c=conn(); q=request.args.get('q','').strip(); status=request.args.get('status','')
        sql='SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE 1=1'; args=[]
        if q: sql += ' AND (c.name LIKE ? OR CAST(i.number AS TEXT) LIKE ?)'; args += [f'%{q}%', f'%{q}%']
        if status: sql += ' AND i.status=?'; args.append(status)
        rows=c.execute(sql+' ORDER BY i.number DESC',args).fetchall()
        today=datetime.date.today().isoformat(); view=[]
        for r in rows:
            d=dict(r); d['display_status']='overdue' if d['status'] in ('unpaid','sent') and d['due_date'] < today else d['status']; view.append(d)
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Fakturor</h1><a class=btn href=/invoices/new>+ Ny faktura</a><a class=btn href=/invoice-settings>Fakturainställningar</a></div><form><div class=row><input name=q placeholder="Sök kund eller fakturanummer" value="{{q}}"><select name=status><option value="">Alla statusar</option>{%for s,l in labels.items()%}<option value={{s}} {%if status==s%}selected{%endif%}>{{l}}</option>{%endfor%}</select></div><br><button>Filtrera</button></form></div><div class=card><table><tr><th>Faktura</th><th>Kund</th><th>Datum</th><th>Förfallo</th><th>Belopp</th><th>Status</th><th></th></tr>{%for r in rows%}<tr><td>#{{r.number}}</td><td>{{r.customer_name}}</td><td>{{r.invoice_date}}</td><td>{{r.due_date}}</td><td>{{'%.2f'|format(r.total)}} {{r.currency}}</td><td><span class=pill>{{labels.get(r.display_status,r.display_status)}}</span></td><td><a class=btn href="/invoice/{{r.id}}">Öppna</a></td></tr>{%endfor%}</table></div>''', rows=view,q=q,status=status,labels=STATUS_LABELS)
        return invoice_page(body)

    @app.route('/invoices/new', methods=['GET','POST'])
    def invoice_new_upgrade():
        c=conn(); customers=c.execute('SELECT * FROM customers ORDER BY name').fetchall(); articles=c.execute('SELECT * FROM articles ORDER BY name').fetchall()
        if request.method == 'POST':
            try:
                cid=int(request.form.get('customer_id','0')); customer=c.execute('SELECT * FROM customers WHERE id=?',(cid,)).fetchone()
                if not customer: raise ValueError('Välj en kund.')
                date=request.form.get('invoice_date','').strip(); due=request.form.get('due_date','').strip(); delivery=request.form.get('delivery_date','').strip() or date
                if not date or not due: raise ValueError('Fakturadatum och förfallodatum krävs.')
                try: terms=int(request.form.get('payment_terms_days','30') or 30)
                except ValueError: raise ValueError('Betalningsvillkoret måste vara ett helt antal dagar.')
                if terms < 0 or terms > 365: raise ValueError('Betalningsvillkoret måste vara mellan 0 och 365 dagar.')
                invoice_discount=float(request.form.get('invoice_discount','0') or 0); invoice_fee=float(request.form.get('invoice_fee','0') or 0)
                lines=json.loads(request.form.get('lines_json','[]'))
                subtotal,vat,total,clean=calc_lines(lines, invoice_discount, invoice_fee)
                num=c.execute('SELECT COALESCE(MAX(number),999)+1 n FROM invoices').fetchone()['n']
                now=datetime.datetime.now().isoformat(); cur=request.form.get('currency','SEK').strip().upper() or 'SEK'
                c.execute('''INSERT INTO invoices(number,customer_id,invoice_date,due_date,status,notes,subtotal,vat,total,created,delivery_date,buyer_vat_no,reference,our_reference,marking,invoice_fee,invoice_discount,payment_terms_days,currency,payment_reference,accounting_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (num,cid,date,due,'draft',request.form.get('notes','').strip(),subtotal,vat,total,now,delivery,request.form.get('buyer_vat_no','').strip(),request.form.get('reference','').strip(),request.form.get('our_reference','').strip(),request.form.get('marking','').strip(),invoice_fee,invoice_discount,terms,cur,str(num),setting('accounting_method','cash')))
                iid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
                for a,d,q,u,p,r,disc,lt in clean:
                    c.execute('INSERT INTO invoice_lines(invoice_id,article_id,description,quantity,unit,price_ex_vat,vat_rate,discount,line_total) VALUES(?,?,?,?,?,?,?,?,?)',(iid,a,d,q,u,p,r,disc,lt))
                c.commit(); flash(f'Faktura #{num} skapad som utkast.'); return redirect(url_for('invoice_view_upgrade',invoice_id=iid))
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                flash(str(e)); return redirect(url_for('invoice_new_upgrade'))
        today=datetime.date.today(); due=today+datetime.timedelta(days=30)
        arts=json.dumps([dict(a) for a in articles],ensure_ascii=False)
        cust=json.dumps([dict(x) for x in customers],ensure_ascii=False)
        selected=request.args.get('customer','0')
        body=render_template_string('''
<div class="actions" style="margin-bottom:16px"><div style="margin-right:auto"><h1>Ny faktura</h1><p class="muted">Fyll i fakturan steg för steg. Uppgifter från kund- och artikelregister fylls i automatiskt men kan ändras i utkastet.</p></div><a class=btn href="/invoices">Till fakturor</a></div>
<form method=post id=f onsubmit="return submitForm()">
<div class=card><div class=section-title><h2>1. Kund och mottagare</h2></div><div class=row>
<label>Sök och välj kund<select id=customer_id name=customer_id required onchange="selectCustomer(this.value)"><option value="">Välj kund...</option>{%for c in customers%}<option value={{c.id}} {%if selected|int==c.id%}selected{%endif%}>{{c.name}}</option>{%endfor%}</select></label>
<label>Kundens namn<input id=customer_name name=customer_name readonly></label>
<label>Organisationsnummer/personuppgifter<input id=customer_orgnr name=customer_orgnr readonly></label>
<label>Kundens momsregistreringsnummer<input id=buyer_vat_no name=buyer_vat_no></label>
<label>Adress<input id=customer_address name=customer_address readonly></label><label>Postnummer<input id=customer_zip name=customer_zip readonly></label>
<label>Ort<input id=customer_city name=customer_city readonly></label><label>E-post<input id=customer_email name=customer_email readonly></label>
<label>Telefon<input id=customer_phone name=customer_phone readonly></label>
</div><p class=help>Välj en befintlig kund för att fylla kunduppgifterna automatiskt. Ändringar av kundregistret görs under Kunder.</p></div>
<div class=card><div class=section-title><h2>2. Fakturauppgifter</h2></div><div class=row>
<label>Fakturadatum<input type=date name=invoice_date value={{today}} required></label><label>Leverans-/utförandedatum<input type=date name=delivery_date value={{today}} required></label>
<label>Förfallodatum<input type=date name=due_date id=due_date value={{due}} required></label><label>Betalningsvillkor (dagar)<input type=number min=0 max=365 name=payment_terms_days id=terms value=30 required oninput="updateDue()"></label>
<label>Er referens<input name=reference></label><label>Vår referens<input name=our_reference></label>
<label>Märkning<input name=marking placeholder="T.ex. projektnummer, ordernummer eller kostnadsställe"></label><label>Valuta<select name=currency><option>SEK</option><option>EUR</option><option>USD</option><option>NOK</option><option>DKK</option></select></label>
<label>Faktureringsavgift, exkl. moms<input type=number min=0 step=0.01 name=invoice_fee value=0></label><label>Fakturarabatt, %<input type=number min=0 max=100 step=0.01 name=invoice_discount value=0></label>
<label style="grid-column:1/-1">Övrig information/notering<textarea name=notes placeholder="Exempelvis leveransinformation eller annan relevant text på fakturan"></textarea></label>
</div></div>
<div class=card><div class=section-title><h2>3. Fakturarader</h2><button type=button onclick="addLine()">+ Lägg till rad</button></div><div id=lines></div><input type=hidden name=lines_json id=lj></div>
<div class="card summary"><h2>4. Sammanfattning</h2><table><tr><td>Rader före fakturarabatt</td><td id=sub>0,00</td></tr><tr><td>Fakturarabatt</td><td id=disc>0,00</td></tr><tr><td>Faktureringsavgift</td><td id=fee>0,00</td></tr><tr><td>Totalt exkl. moms</td><td id=net>0,00</td></tr><tr><td>Moms</td><td id=vat>0,00</td></tr><tr><td class=total>ATT BETALA</td><td class=total id=total>0,00 SEK</td></tr></table></div>
<div class=actions><button type=button class=btn onclick="window.scrollTo({top:0,behavior:'smooth'})">Till början</button><button type=submit>Spara som utkast</button></div>
</form>
<script>
const arts={{arts|safe}}; const customersData={{cust|safe}}; let ls=[];
const moneyFmt=v=>Number(v||0).toLocaleString('sv-SE',{minimumFractionDigits:2,maximumFractionDigits:2});
function esc(v){return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function selectCustomer(id){const c=customersData.find(x=>String(x.id)===String(id)); if(!c)return; document.getElementById('customer_name').value=c.name||'';document.getElementById('customer_orgnr').value=c.orgnr||'';document.getElementById('buyer_vat_no').value=c.vat_no||'';document.getElementById('customer_address').value=c.address||'';document.getElementById('customer_zip').value=c.zip||'';document.getElementById('customer_city').value=c.city||'';document.getElementById('customer_email').value=c.email||'';document.getElementById('customer_phone').value=c.phone||'';}
function addLine(a=null){ls.push(a||{article_id:null,description:'',quantity:1,unit:'st',price_ex_vat:0,vat_rate:25,discount:0});render()}
function choose(i,id){const a=arts.find(x=>String(x.id)===String(id));if(a){ls[i].article_id=a.id;ls[i].description=a.name||'';ls[i].price_ex_vat=a.price_ex_vat||0;ls[i].vat_rate=a.vat_rate||25;ls[i].unit=a.unit||'st';render()}}
function render(){document.getElementById('lines').innerHTML=ls.map((x,i)=>`<div class="card line-card"><div class=actions><b>Rad ${i+1}</b><button type=button class=red onclick="ls.splice(${i},1);render()">Ta bort rad</button></div><div class=row><label>Artikel<select onchange="choose(${i},this.value)"><option value="">Manuell rad</option>${arts.map(a=>`<option value="${a.id}" ${String(a.id)===String(x.article_id)?'selected':''}>${esc(a.number?a.number+' – ':'')}${esc(a.name)}</option>`).join('')}</select></label><label>Artikelnummer<input value="${esc(x.article_number||'')}" oninput="ls[${i}].article_number=this.value"></label><label>Beskrivning<input value="${esc(x.description)}" oninput="ls[${i}].description=this.value"></label><label>Antal<input type=number min=0.01 step=0.01 value="${x.quantity}" oninput="ls[${i}].quantity=this.value;summary()"></label><label>Enhet<input value="${esc(x.unit)}" oninput="ls[${i}].unit=this.value"></label><label>Á-pris exkl. moms<input type=number min=0 step=0.01 value="${x.price_ex_vat}" oninput="ls[${i}].price_ex_vat=this.value;summary()"></label><label>Moms<select onchange="ls[${i}].vat_rate=this.value;summary()"><option value=25 ${x.vat_rate==25?'selected':''}>25 %</option><option value=12 ${x.vat_rate==12?'selected':''}>12 %</option><option value=6 ${x.vat_rate==6?'selected':''}>6 %</option><option value=0 ${x.vat_rate==0?'selected':''}>0 %</option></select></label><label>Rabatt på raden, %<input type=number min=0 max=100 step=0.01 value="${x.discount}" oninput="ls[${i}].discount=this.value;summary()"></label></div><p class=help>Summa rad exkl. moms: <b id="line-total-${i}">0,00</b></p></div>`).join('');summary()}
function summary(){let sub=0,vat=0;ls.forEach((x,i)=>{let base=Number(x.quantity||0)*Number(x.price_ex_vat||0)*(1-Number(x.discount||0)/100);sub+=base;vat+=base*Number(x.vat_rate||0)/100;const el=document.getElementById('line-total-'+i);if(el)el.textContent=moneyFmt(base)});let d=sub*Number(document.querySelector('[name=invoice_discount]').value||0)/100;let fee=Number(document.querySelector('[name=invoice_fee]').value||0);let net=sub-d+fee;document.getElementById('sub').textContent=moneyFmt(sub);document.getElementById('disc').textContent='-'+moneyFmt(d);document.getElementById('fee').textContent=moneyFmt(fee);document.getElementById('net').textContent=moneyFmt(net);document.getElementById('vat').textContent=moneyFmt(vat);document.getElementById('total').textContent=moneyFmt(net+vat)+' '+document.querySelector('[name=currency]').value}
function updateDue(){const days=Math.max(0,Math.min(365,Number(document.getElementById('terms').value||0)));const d=new Date(document.querySelector('[name=invoice_date]').value+'T00:00:00');if(!isNaN(d)){d.setDate(d.getDate()+days);document.getElementById('due_date').value=d.toISOString().slice(0,10)}}
function submitForm(){if(!document.getElementById('customer_id').value){alert('Välj en kund.');return false}if(!ls.length){alert('Lägg till minst en fakturarad.');return false}document.getElementById('lj').value=JSON.stringify(ls);return true}
document.querySelector('[name=invoice_discount]').addEventListener('input',summary);document.querySelector('[name=invoice_fee]').addEventListener('input',summary);document.querySelector('[name=currency]').addEventListener('change',summary);document.querySelector('[name=invoice_date]').addEventListener('change',updateDue);
{%if selected and selected|int>0%}selectCustomer('{{selected}}');{%endif%}addLine();
</script>
''', customers=customers, arts=arts, cust=cust, today=today.isoformat(), due=due.isoformat(), selected=selected)
        return invoice_page(body)

    @app.route('/invoice/<int:invoice_id>')
    def invoice_view_upgrade(invoice_id):
        c=conn(); i=c.execute('SELECT i.*,c.name customer_name,c.orgnr,c.vat_no,c.address,c.zip,c.city,c.email,c.phone FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone()
        if not i: return 'Fakturan hittades inte',404
        lines=c.execute('SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id',(invoice_id,)).fetchall()
        display=dict(i); display['status_label']=STATUS_LABELS.get(i['status'],i['status'])
        if i['status'] in ('unpaid','sent') and i['due_date'] < datetime.date.today().isoformat(): display['status_label']='Förfallen'
        problems=invoice_validation(i,lines,c) if i['status']=='draft' else []
        ts=tax_summary(lines)
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Faktura #{{i.number}}</h1><a class=btn href="/invoice/{{i.id}}.pdf">PDF</a>{%if i.status=='draft'%}<a class=btn href="/invoice/{{i.id}}/edit">Redigera</a><form method=post action="/invoice/{{i.id}}/issue"><button>Kontrollera & utfärda</button></form>{%endif%}{%if i.status in ['unpaid','sent','overdue','partpaid']%}<form method=post action="/invoice/{{i.id}}/paid"><button>Registrera betalning</button></form>{%endif%}{%if i.status not in ['paid','cancelled']%}<form method=post action="/invoice/{{i.id}}/cancel"><button class=red>Makulerad</button></form>{%endif%}</div><p><span class=pill>{{i.status_label}}</span></p>{%if problems%}<div class=bad><b>Fakturan kan inte utfärdas ännu:</b><ul>{%for p in problems%}<li>{{p}}</li>{%endfor%}</ul></div>{%endif%}<div class=row><div><h3>Säljare</h3><p>{{co.name}}<br>{{co.address}}<br>{{co.zip}} {{co.city}}<br>Org.nr: {{co.orgnr}}<br>Momsreg.nr: {{co.vat_no}}{%if co.email%}<br>{{co.email}}{%endif%}{%if co.phone%}<br>{{co.phone}}{%endif%}</p></div><div><h3>Kund</h3><p>{{i.customer_name}}<br>{{i.address or ''}}<br>{{i.zip or ''}} {{i.city or ''}}{%if i.orgnr%}<br>Org.nr: {{i.orgnr}}{%endif%}{%if i.vat_no or i.buyer_vat_no%}<br>Momsreg.nr: {{i.buyer_vat_no or i.vat_no}}{%endif%}</p></div></div><p><b>Fakturadatum:</b> {{i.invoice_date}} · <b>Leverans/utförande:</b> {{i.delivery_date or i.invoice_date}} · <b>Förfallodatum:</b> {{i.due_date}} · <b>Betalningsvillkor:</b> {{i.payment_terms_days or 30}} dagar<br><b>Er referens:</b> {{i.reference or '–'}} · <b>Vår referens:</b> {{i.our_reference or '–'}} · <b>Märkning:</b> {{i.marking or '–'}} · <b>Betalningsreferens:</b> {{i.payment_reference}}</p><table><tr><th>Artikel</th><th>Beskrivning</th><th>Antal</th><th>Enhet</th><th>Á-pris exkl.</th><th>Rabatt</th><th>Moms</th><th>Summa exkl.</th></tr>{%for l in lines%}<tr><td>{{l.article_id or '–'}}</td><td>{{l.description}}</td><td>{{'%.2f'|format(l.quantity)}}</td><td>{{l.unit}}</td><td>{{'%.2f'|format(l.price_ex_vat)}} {{i.currency}}</td><td>{{'%.2f'|format(l.discount)}} %</td><td>{{l.vat_rate}} %</td><td>{{'%.2f'|format(l.line_total)}} {{i.currency}}</td></tr>{%endfor%}</table><div class=card><table>{%for rate,v in ts.items()%}<tr><td>Beskattningsunderlag {{rate}} %</td><td>{{'%.2f'|format(v.base)}} {{i.currency}}</td><td>Moms {{rate}} %</td><td>{{'%.2f'|format(v.vat)}} {{i.currency}}</td></tr>{%endfor%}{%if i.invoice_discount%}<tr><td>Fakturarabatt</td><td colspan=3>{{'%.2f'|format(i.invoice_discount)}} %</td></tr>{%endif%}{%if i.invoice_fee%}<tr><td>Faktureringsavgift</td><td colspan=3>{{'%.2f'|format(i.invoice_fee)}} {{i.currency}}</td></tr>{%endif%}<tr><td colspan=3><b>Totalt exkl. moms</b></td><td><b>{{'%.2f'|format(i.subtotal)}} {{i.currency}}</b></td></tr><tr><td colspan=3><b>Moms totalt</b></td><td><b>{{'%.2f'|format(i.vat)}} {{i.currency}}</b></td></tr><tr><td colspan=3 class=total>ATT BETALA</td><td class=total>{{'%.2f'|format(i.total)}} {{i.currency}}</td></tr></table></div>{%if i.notes%}<div class=card><h3>Notering</h3><p>{{i.notes}}</p></div>{%endif%}{%if co.bank or co.account or co.swish%}<div class=warn><b>Betalningsuppgifter</b><br>{{co.bank}}{%if co.account%} · {{co.account}}{%endif%}{%if co.swish%}<br>Swish: {{co.swish}}{%endif%}<br>Betalningsreferens: {{i.payment_reference}}</div>{%endif%}</div>''',i=display,lines=lines,co=company(),problems=problems,ts=ts)
        return invoice_page(body)

    @app.route('/invoice/<int:invoice_id>/edit', methods=['GET','POST'])
    def invoice_edit(invoice_id):
        c=conn(); i=c.execute('SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone()
        if not i:return 'Fakturan hittades inte',404
        if i['status'] != 'draft': flash('Endast utkast kan redigeras.'); return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
        customers=c.execute('SELECT * FROM customers ORDER BY name').fetchall(); articles=c.execute('SELECT * FROM articles ORDER BY name').fetchall()
        if request.method=='POST':
            try:
                lines=json.loads(request.form.get('lines_json','[]')); subtotal,vat,total,clean=calc_lines(lines,float(request.form.get('invoice_discount','0') or 0),float(request.form.get('invoice_fee','0') or 0))
                terms=int(request.form.get('payment_terms_days','30') or 30)
                if not 0 <= terms <= 365: raise ValueError('Betalningsvillkoret måste vara mellan 0 och 365 dagar.')
                c.execute('''UPDATE invoices SET customer_id=?,invoice_date=?,delivery_date=?,due_date=?,buyer_vat_no=?,reference=?,our_reference=?,marking=?,invoice_fee=?,invoice_discount=?,payment_terms_days=?,currency=?,notes=?,subtotal=?,vat=?,total=? WHERE id=?''',
                    (int(request.form['customer_id']),request.form['invoice_date'],request.form.get('delivery_date') or request.form['invoice_date'],request.form['due_date'],request.form.get('buyer_vat_no',''),request.form.get('reference',''),request.form.get('our_reference',''),request.form.get('marking',''),float(request.form.get('invoice_fee','0') or 0),float(request.form.get('invoice_discount','0') or 0),terms,request.form.get('currency','SEK'),request.form.get('notes',''),subtotal,vat,total,invoice_id))
                c.execute('DELETE FROM invoice_lines WHERE invoice_id=?',(invoice_id,))
                for a,d,q,u,p,r,disc,lt in clean:c.execute('INSERT INTO invoice_lines(invoice_id,article_id,description,quantity,unit,price_ex_vat,vat_rate,discount,line_total) VALUES(?,?,?,?,?,?,?,?,?)',(invoice_id,a,d,q,u,p,r,disc,lt))
                c.commit(); flash('Fakturan uppdaterades.'); return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
            except Exception as e: flash('Kunde inte spara fakturan: '+str(e))
        lines=[dict(x) for x in c.execute('SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id',(invoice_id,)).fetchall()]; arts=[dict(x) for x in articles]
        body=render_template_string('''<div class=card><h1>Redigera faktura #{{i.number}}</h1><form method=post><div class=row><label>Kund<select name=customer_id>{%for x in customers%}<option value={{x.id}} {%if x.id==i.customer_id%}selected{%endif%}>{{x.name}}</option>{%endfor%}</select></label><label>Kundens momsregistreringsnummer<input name=buyer_vat_no value="{{i.buyer_vat_no or ''}}"></label><label>Fakturadatum<input type=date name=invoice_date value={{i.invoice_date}}></label><label>Leverans/utförande<input type=date name=delivery_date value={{i.delivery_date or i.invoice_date}}></label><label>Förfallodatum<input type=date name=due_date value={{i.due_date}}></label><label>Betalningsvillkor<input type=number name=payment_terms_days value={{i.payment_terms_days or 30}}></label><label>Er referens<input name=reference value="{{i.reference or ''}}"></label><label>Vår referens<input name=our_reference value="{{i.our_reference or ''}}"></label><label>Märkning<input name=marking value="{{i.marking or ''}}"></label><label>Faktureringsavgift<input type=number step=.01 min=0 name=invoice_fee value={{i.invoice_fee or 0}}></label><label>Fakturarabatt %<input type=number step=.01 min=0 max=100 name=invoice_discount value={{i.invoice_discount or 0}}></label><label>Valuta<select name=currency><option {%if i.currency=='SEK'%}selected{%endif%}>SEK</option><option {%if i.currency=='EUR'%}selected{%endif%}>EUR</option><option {%if i.currency=='USD'%}selected{%endif%}>USD</option><option {%if i.currency=='NOK'%}selected{%endif%}>NOK</option><option {%if i.currency=='DKK'%}selected{%endif%}>DKK</option></select></label><label style="grid-column:1/-1">Notering<textarea name=notes>{{i.notes or ''}}</textarea></label></div><div id=lines></div><input type=hidden name=lines_json id=lj><div class=actions><button type=button onclick=addLine()>+ Lägg till rad</button><button onclick=submitForm()>Spara</button></div></form><script>let ls={{lines|tojson}};function esc(v){return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}function addLine(){ls.push({description:'',quantity:1,unit:'st',price_ex_vat:0,vat_rate:25,discount:0});render()}function render(){lines.innerHTML=ls.map((x,i)=>`<div class=card><div class=actions><b>Rad ${i+1}</b><button type=button class=red onclick="ls.splice(${i},1);render()">Ta bort rad</button></div><div class=row><label>Beskrivning<input value="${esc(x.description)}" oninput="ls[${i}].description=this.value"></label><label>Antal<input type=number step=.01 value="${x.quantity}" oninput="ls[${i}].quantity=this.value"></label><label>Enhet<input value="${esc(x.unit)}" oninput="ls[${i}].unit=this.value"></label><label>Á-pris exkl. moms<input type=number step=.01 value="${x.price_ex_vat}" oninput="ls[${i}].price_ex_vat=this.value"></label><label>Moms<select onchange="ls[${i}].vat_rate=this.value"><option value=25 ${x.vat_rate==25?'selected':''}>25 %</option><option value=12 ${x.vat_rate==12?'selected':''}>12 %</option><option value=6 ${x.vat_rate==6?'selected':''}>6 %</option><option value=0 ${x.vat_rate==0?'selected':''}>0 %</option></select></label><label>Rabatt %<input type=number step=.01 value="${x.discount}" oninput="ls[${i}].discount=this.value"></label></div></div>`).join('')}function submitForm(){lj.value=JSON.stringify(ls);return true}render()</script></div>''',i=i,customers=customers,lines=lines)
        return invoice_page(body)

    @app.route('/invoice/<int:invoice_id>/issue', methods=['POST'])
    def invoice_issue(invoice_id):
        c=conn(); i=c.execute('SELECT i.*,c.name customer_name,c.address,c.city FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone(); lines=c.execute('SELECT * FROM invoice_lines WHERE invoice_id=?',(invoice_id,)).fetchall()
        if not i:return redirect(url_for('invoices_upgrade'))
        problems=invoice_validation(i,lines,c)
        if problems:
            for p in problems: flash(p)
            return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
        try:
            method=i['accounting_method'] or setting('accounting_method','cash'); vid=None
            if method == 'accrual':
                summary=tax_summary(lines); entries=[('1510',i.total,0,'')]
                for rate,v in summary.items():
                    acct='3001' if rate==25 else ('3002' if rate==12 else ('3003' if rate==6 else '3004')); vatacct='2611' if rate==25 else ('2612' if rate==12 else ('2613' if rate==6 else None))
                    entries.append((acct,0,v['base'],str(rate) if rate else ''))
                    if vatacct and v['vat']: entries.append((vatacct,0,v['vat'],str(rate)))
                vid=create_voucher(i['invoice_date'],f'Faktura #{i["number"]} till {i["customer_name"]}','invoice_issued',None,None,entries)
            c.execute("UPDATE invoices SET status='unpaid',issued_at=?,sent_at=?,voucher_id=coalesce(?,voucher_id) WHERE id=?",(datetime.datetime.now().isoformat(),datetime.datetime.now().isoformat(),vid,invoice_id));c.commit();flash(f'Faktura #{i["number"]} kontrollerad och utfärdad.')
        except Exception as e: flash('Fakturan kunde inte utfärdas: '+str(e))
        return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>/paid', methods=['POST'])
    def invoice_paid_upgrade(invoice_id):
        c=conn(); i=c.execute('SELECT * FROM invoices WHERE id=?',(invoice_id,)).fetchone()
        if not i:return redirect(url_for('invoices_upgrade'))
        if i['status']=='paid': return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
        try:
            method=i['accounting_method'] or setting('accounting_method','cash')
            if method == 'accrual': entries=[('1930',i['total'],0,''),('1510',0,i['total'],'')]
            else:
                summary=tax_summary(c.execute('SELECT * FROM invoice_lines WHERE invoice_id=?',(invoice_id,)).fetchall()); entries=[('1930',i['total'],0,'')]
                for rate,v in summary.items():
                    acct='3001' if rate==25 else ('3002' if rate==12 else ('3003' if rate==6 else '3004')); vatacct='2611' if rate==25 else ('2612' if rate==12 else ('2613' if rate==6 else None)); entries.append((acct,0,v['base'],str(rate) if rate else ''))
                    if vatacct and v['vat']: entries.append((vatacct,0,v['vat'],str(rate)))
            vid=create_voucher(datetime.date.today().isoformat(),f'Betalning faktura #{i["number"]}','invoice_payment',None,None,entries)
            c.execute("UPDATE invoices SET status='paid',paid_at=?,voucher_id=coalesce(voucher_id,?) WHERE id=?",(datetime.datetime.now().isoformat(),vid,invoice_id));c.commit();flash('Betalningen bokfördes och fakturan markerades som betald.')
        except Exception as e: flash('Betalningen kunde inte bokföras: '+str(e))
        return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>/cancel', methods=['POST'])
    def invoice_cancel_upgrade(invoice_id):
        c=conn(); i=c.execute('SELECT * FROM invoices WHERE id=?',(invoice_id,)).fetchone()
        if not i:return redirect(url_for('invoices_upgrade'))
        if i['status']=='paid': flash('En betald faktura kan inte bara makuleras. Använd kreditfaktura/korrigering.'); return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
        if i['status']=='cancelled': return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))
        c.execute("UPDATE invoices SET status='cancelled',cancelled_at=? WHERE id=?",(datetime.datetime.now().isoformat(),invoice_id));c.commit();flash(f'Faktura #{i["number"]} makulerades och numret behålls i serien.');return redirect(url_for('invoice_view_upgrade',invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>.pdf')
    def invoice_pdf_upgrade(invoice_id):
        c=conn(); i=c.execute('SELECT i.*,c.name customer_name,c.orgnr,c.vat_no,c.address,c.zip,c.city,c.email FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone(); lines=c.execute('SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id',(invoice_id,)).fetchall()
        if not i:return 'Fakturan hittades inte',404
        co=company(); path=os.path.join(EXPORT,f'Faktura_{i.number}.pdf'); p=canvas.Canvas(path,pagesize=A4); w,h=A4; p.setTitle(f'Faktura {i.number}')
        p.setFont('Helvetica-Bold',22);p.drawString(42,h-55,'FAKTURA');p.setFont('Helvetica',10);p.drawRightString(w-42,h-55,f'#{i.number}')
        y=h-95;p.setFont('Helvetica-Bold',10);p.drawString(42,y,co['name'] or 'Säljare');p.setFont('Helvetica',9);y-=14
        for s in [co['address'],(co['zip']+' '+co['city']).strip(),f'Org.nr: {co["orgnr"]}' if co['orgnr'] else '',f'Momsreg.nr: {co["vat_no"]}' if co['vat_no'] else '',co['email'],co['phone']]:
            if s:p.drawString(42,y,s);y-=12
        p.setFont('Helvetica-Bold',10);p.drawString(320,h-95,'Kund');p.setFont('Helvetica',9);yy=h-110
        for s in [i['customer_name'],i['address'],(i['zip']+' '+i['city']).strip(),f'Org.nr: {i["orgnr"]}' if i['orgnr'] else '',f'Momsreg.nr: {i["buyer_vat_no"] or i["vat_no"]}' if (i['buyer_vat_no'] or i['vat_no']) else '']:
            if s:p.drawString(320,yy,s);yy-=12
        p.setFont('Helvetica',9);y-=4;p.drawString(42,y,f'Fakturadatum: {i["invoice_date"]}');p.drawString(190,y,f'Leverans/utförande: {i["delivery_date"] or i["invoice_date"]}');p.drawString(410,y,f'Förfallodatum: {i["due_date"]}');y-=18
        p.drawString(42,y,f'Er referens: {i["reference"] or "-"}');p.drawString(190,y,f'Vår referens: {i["our_reference"] or "-"}');p.drawString(350,y,f'Märkning: {i["marking"] or "-"}');y-=24
        p.setFont('Helvetica-Bold',9);p.drawString(42,y,'Beskrivning');p.drawRightString(340,y,'Antal');p.drawRightString(395,y,'Á-pris');p.drawRightString(455,y,'Moms');p.drawRightString(w-42,y,'Summa');y-=15;p.setFont('Helvetica',8)
        for l in lines:
            if y<100:p.showPage();y=h-50
            p.drawString(42,y,l['description'][:48]);p.drawRightString(340,y,f'{l["quantity"]:g} {l["unit"]}');p.drawRightString(395,y,f'{l["price_ex_vat"]:.2f}');p.drawRightString(455,y,f'{l["vat_rate"]}%');p.drawRightString(w-42,y,f'{l["line_total"]:.2f}');y-=13
        y-=12;p.line(300,y,w-42,y);y-=18
        if i['invoice_discount']:p.drawRightString(w-42,y,f'Fakturarabatt: {i["invoice_discount"]:.2f}%');y-=14
        if i['invoice_fee']:p.drawRightString(w-42,y,f'Faktureringsavgift: {i["invoice_fee"]:.2f} {i["currency"]}');y-=14
        p.drawRightString(w-42,y,f'Netto: {i["subtotal"]:.2f} {i["currency"]}');y-=14;p.drawRightString(w-42,y,f'Moms: {i["vat"]:.2f} {i["currency"]}');y-=18;p.setFont('Helvetica-Bold',12);p.drawRightString(w-42,y,f'ATT BETALA: {i["total"]:.2f} {i["currency"]}')
        y-=32;p.setFont('Helvetica-Bold',9);p.drawString(42,y,'Betalningsuppgifter');p.setFont('Helvetica',8);y-=13
        for s in [co['bank'],co['account'],f'Swish: {co["swish"]}' if co['swish'] else '',f'Betalningsreferens: {i["payment_reference"] or i["number"]}']:
            if s:p.drawString(42,y,s);y-=11
        if i['notes']: y-=8;p.drawString(42,y,f'Notering: {i["notes"][:110]}')
        p.save();return send_file(path,as_attachment=True,download_name=f'Faktura_{i.number}.pdf')

    app.view_functions['invoices'] = invoices_upgrade
    app.view_functions['invoice_new'] = invoice_new_upgrade
    app.view_functions['invoice_view'] = invoice_view_upgrade
    app.view_functions['invoice_send'] = invoice_issue
    app.view_functions['invoice_paid'] = invoice_paid_upgrade
    app.view_functions['invoice_pdf'] = invoice_pdf_upgrade
