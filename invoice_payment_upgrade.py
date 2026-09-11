import datetime
from flask import request, redirect, render_template_string, flash, url_for


def register_invoice_payment_upgrade(app, conn, setting, set_setting, create_voucher, money, AC, ROOT, EXPORT):
    """Payment matching for invoices. Links imported bank transactions to invoices and posts the payment once."""
    c = conn()
    c.execute('''CREATE TABLE IF NOT EXISTS invoice_payment_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        transaction_id INTEGER NOT NULL UNIQUE,
        amount REAL NOT NULL,
        matched_at TEXT NOT NULL,
        voucher_id INTEGER,
        UNIQUE(invoice_id, transaction_id)
    )''')
    c.commit()

    def shell(body):
        html = '''<!doctype html><html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",Arial;background:#f5f5f7;color:#171717;margin:0}.side{position:fixed;left:0;top:0;bottom:0;width:225px;background:#111;color:#fff;padding:22px 16px;box-sizing:border-box}.side h2{margin:0 0 22px}.side a{display:block;color:#fff;text-decoration:none;padding:9px;border-radius:8px}.side a:hover{background:#333}.main{margin-left:225px;max-width:1350px;padding:28px}.card{background:#fff;border-radius:16px;padding:22px;margin-bottom:16px;box-shadow:0 2px 12px #0001}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn,button{border:0;background:#111;color:#fff;padding:10px 14px;border-radius:9px;cursor:pointer;text-decoration:none;display:inline-block}.good{background:#e9f8ee;padding:12px;border-radius:10px}.warn{background:#fff7dd;padding:12px;border-radius:10px}input,select{padding:10px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box;width:100%}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #eee;text-align:left}.muted{color:#666}.amount{font-weight:700;font-size:20px}</style></head><body><aside class=side><h2>Min Bokföring</h2><a href="/">Översikt</a><a href="/bank">Bank</a><a href="/invoices">Fakturor</a><a href="/invoice-payments">Betalningar</a><a href="/customers">Kunder</a><a href="/articles">Artiklar</a><a href="/receipts">Kvitton</a><a href="/journal">Verifikationer</a><a href="/vat">Moms</a><a href="/reports">Rapporter</a><a href="/settings">Inställningar</a></aside><main class=main>{% for m in get_flashed_messages() %}<div class=good>{{m}}</div>{% endfor %}{{body|safe}}</main></body></html>'''
        return render_template_string(html, body=body)

    def candidates(invoice):
        c = conn()
        already = {r['transaction_id'] for r in c.execute('SELECT transaction_id FROM invoice_payment_matches').fetchall()}
        rows = c.execute("SELECT * FROM transactions WHERE amount > 0 AND status IN ('new','booked') ORDER BY date DESC, id DESC").fetchall()
        out=[]
        for r in rows:
            if r['id'] in already:
                continue
            amount=round(float(r['amount'] or 0),2)
            diff=round(abs(amount-float(invoice['total'])),2)
            text=f"{r['reference'] or ''} {r['description'] or ''}".lower()
            ref=str(invoice['number']).lower()
            score=100-diff
            if ref in text: score += 100
            if invoice['payment_reference'] and str(invoice['payment_reference']).lower() in text: score += 150
            out.append((score,r,diff))
        return sorted(out,key=lambda x:(-x[0],x[2]))[:20]

    @app.route('/invoice-payments', methods=['GET'])
    def invoice_payments():
        c=conn()
        rows=c.execute('''SELECT i.id,i.number,i.total,i.currency,i.status,c.name customer_name,
                          (SELECT COUNT(*) FROM invoice_payment_matches m WHERE m.invoice_id=i.id) match_count
                          FROM invoices i JOIN customers c ON c.id=i.customer_id
                          WHERE i.status IN ('unpaid','partpaid','overdue') ORDER BY i.due_date,i.number''').fetchall()
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Betalningar</h1><a class=btn href=/invoices>Fakturor</a></div><p class=muted>Här hittar du obetalda fakturor och kan matcha en importerad bankhändelse. Matchningen använder belopp, fakturanummer och betalningsreferens som stöd – du godkänner alltid själv.</p></div><div class=card><table><tr><th>Faktura</th><th>Kund</th><th>Belopp</th><th>Status</th><th></th></tr>{%for r in rows%}<tr><td>#{{r.number}}</td><td>{{r.customer_name}}</td><td class=amount>{{'%.2f'|format(r.total)}} {{r.currency}}</td><td>{{r.status}}</td><td><a class=btn href="/invoice/{{r.id}}/match-payment">Matcha betalning</a></td></tr>{%endfor%}</table></div>''',rows=rows)
        return shell(body)

    @app.route('/invoice/<int:invoice_id>/match-payment', methods=['GET','POST'])
    def invoice_match_payment(invoice_id):
        c=conn(); invoice=c.execute('SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(invoice_id,)).fetchone()
        if not invoice: return 'Fakturan finns inte',404
        if invoice['status'] in ('paid','cancelled','credit'): flash('Fakturan kan inte matchas i detta statusläge.'); return redirect(url_for('invoice_payments'))
        if request.method=='POST':
            try:
                tid=int(request.form.get('transaction_id','0')); tx=c.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone()
                if not tx: raise ValueError('Bankhändelsen finns inte.')
                amount=round(float(tx['amount'] or 0),2); total=round(float(invoice['total']),2)
                if amount <= 0: raise ValueError('Endast en inkommande bankhändelse kan matchas som kundbetalning.')
                if abs(amount-total)>0.01: raise ValueError(f'Beloppet {amount:.2f} kr matchar inte fakturan {total:.2f} kr. Delbetalningar byggs som nästa steg.')
                exists=c.execute('SELECT 1 FROM invoice_payment_matches WHERE transaction_id=?',(tid,)).fetchone()
                if exists: raise ValueError('Bankhändelsen är redan kopplad till en faktura.')
                method=invoice['accounting_method'] or setting('accounting_method','cash')
                if method == 'accrual':
                    lines=[('1930',amount,0.0,''),('1510',0.0,amount,'')]
                else:
                    subtotal=round(float(invoice['subtotal']),2); vat=round(float(invoice['vat']),2)
                    lines=[('1930',amount,0.0,''),('3001',0.0,subtotal,'SALE'),('2611',0.0,vat,'VAT')]
                voucher_id=create_voucher(tx['date'] or datetime.date.today().isoformat(), f'Kundbetalning faktura #{invoice["number"]}', 'invoice_payment', tid, None, lines)
                c.execute('INSERT INTO invoice_payment_matches(invoice_id,transaction_id,amount,matched_at,voucher_id) VALUES(?,?,?,?,?)',(invoice_id,tid,amount,datetime.datetime.now().isoformat(),voucher_id))
                c.execute("UPDATE invoices SET status='paid',paid_at=?,voucher_id=? WHERE id=?",(tx['date'] or datetime.date.today().isoformat(),voucher_id,invoice_id))
                c.commit(); flash(f'Faktura #{invoice["number"]} markerades som betald och bankhändelsen bokfördes.'); return redirect(url_for('invoice_payments'))
            except Exception as e:
                flash(str(e))
        cand=candidates(invoice)
        body=render_template_string('''<div class=card><div class=actions><h1 style="margin-right:auto">Matcha faktura #{{invoice.number}}</h1><a class=btn href="/invoice/{{invoice.id}}">Till fakturan</a></div><p><b>Kund:</b> {{invoice.customer_name}}<br><b>Belopp:</b> <span class=amount>{{'%.2f'|format(invoice.total)}} {{invoice.currency}}</span><br><b>Betalningsreferens:</b> {{invoice.payment_reference or '–'}}</p></div><div class=card><h2>Föreslagna bankhändelser</h2>{%if cand%}<form method=post><table><tr><th></th><th>Datum</th><th>Referens</th><th>Beskrivning</th><th>Belopp</th><th>Avvikelse</th></tr>{%for score,r,diff in cand%}<tr><td><input type=radio name=transaction_id value={{r.id}} required></td><td>{{r.date}}</td><td>{{r.reference or '–'}}</td><td>{{r.description}}</td><td>{{'%.2f'|format(r.amount)}} {{r.currency}}</td><td>{{'%.2f'|format(diff)}} kr</td></tr>{%endfor%}</table><br><button>Godkänn matchning och bokför betalningen</button></form>{%else%}<div class=warn>Inga lämpliga inkommande bankhändelser hittades. Importera banken först.</div>{%endif%}</div>''',invoice=invoice,cand=cand)
        return shell(body)
