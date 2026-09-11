import os
from flask import send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def register_invoice_pdf_fix(app, conn, setting, EXPORT):
    """Robust PDF endpoint using sqlite row-key access (not attribute access)."""
    def company():
        return {
            'name': setting('company_name', ''), 'orgnr': setting('company_orgnr', ''),
            'vat_no': setting('company_vat', ''), 'address': setting('company_address', ''),
            'zip': setting('company_zip', ''), 'city': setting('company_city', ''),
            'email': setting('company_email', ''), 'phone': setting('company_phone', ''),
            'bank': setting('company_bank', ''), 'account': setting('company_bank_account', ''),
            'swish': setting('company_swish', ''),
        }

    def invoice_pdf_fixed(invoice_id):
        c = conn()
        i = c.execute('''SELECT i.*, c.name customer_name, c.orgnr, c.vat_no,
                        c.address, c.zip, c.city, c.email
                        FROM invoices i JOIN customers c ON c.id=i.customer_id
                        WHERE i.id=?''', (invoice_id,)).fetchone()
        lines = c.execute('SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id', (invoice_id,)).fetchall()
        if not i:
            return 'Fakturan hittades inte', 404

        co = company()
        number = i['number']
        currency = i['currency'] or 'SEK'
        os.makedirs(EXPORT, exist_ok=True)
        path = os.path.join(EXPORT, f'Faktura_{number}.pdf')
        p = canvas.Canvas(path, pagesize=A4)
        w, h = A4
        p.setTitle(f'Faktura {number}')
        p.setFont('Helvetica-Bold', 22)
        p.drawString(42, h-55, 'FAKTURA')
        p.setFont('Helvetica', 10)
        p.drawRightString(w-42, h-55, f'#{number}')

        y = h-95
        p.setFont('Helvetica-Bold', 10); p.drawString(42, y, co['name'] or 'Säljare')
        p.setFont('Helvetica', 9); y -= 14
        for s in [co['address'], (co['zip']+' '+co['city']).strip(),
                  f'Momsreg.nr: {co["vat_no"]}' if co['vat_no'] else '', co['email'], co['phone']]:
            if s:
                p.drawString(42, y, s); y -= 12

        p.setFont('Helvetica-Bold', 10); p.drawString(320, h-95, 'Kund')
        p.setFont('Helvetica', 9); yy = h-110
        for s in [i['customer_name'], i['address'], (i['zip']+' '+i['city']).strip(),
                  f'Momsreg.nr: {i["buyer_vat_no"] or i["vat_no"]}' if (i['buyer_vat_no'] or i['vat_no']) else '']:
            if s:
                p.drawString(320, yy, s); yy -= 12

        y -= 4
        p.drawString(42, y, f'Fakturadatum: {i["invoice_date"]}')
        p.drawString(220, y, f'Leverans/utförande: {i["delivery_date"] or i["invoice_date"]}')
        p.drawString(440, y, f'Förfallodatum: {i["due_date"]}')
        y -= 18
        p.drawString(42, y, f'Er referens: {i["reference"] or "-"}')
        p.drawString(220, y, f'Vår referens: {i["our_reference"] or "-"}')
        p.drawString(440, y, f'Betalningsref: {i["payment_reference"] or number}')
        y -= 24

        p.setFont('Helvetica-Bold', 9)
        p.drawString(42, y, 'Beskrivning'); p.drawRightString(355, y, 'Antal')
        p.drawRightString(410, y, 'Á-pris'); p.drawRightString(475, y, 'Moms'); p.drawRightString(w-42, y, 'Summa')
        y -= 15; p.setFont('Helvetica', 8)
        for l in lines:
            if y < 100:
                p.showPage(); y = h-50
            p.drawString(42, y, str(l['description'])[:55])
            p.drawRightString(355, y, f'{float(l["quantity"]):g} {l["unit"]}')
            p.drawRightString(410, y, f'{float(l["price_ex_vat"]):.2f}')
            p.drawRightString(475, y, f'{l["vat_rate"]}%')
            p.drawRightString(w-42, y, f'{float(l["line_total"]):.2f}')
            y -= 13

        y -= 12; p.line(300, y, w-42, y); y -= 18
        p.drawRightString(w-42, y, f'Netto: {float(i["subtotal"]):.2f} {currency}'); y -= 14
        p.drawRightString(w-42, y, f'Moms: {float(i["vat"]):.2f} {currency}'); y -= 18
        p.setFont('Helvetica-Bold', 12)
        p.drawRightString(w-42, y, f'ATT BETALA: {float(i["total"]):.2f} {currency}')
        y -= 32; p.setFont('Helvetica-Bold', 9); p.drawString(42, y, 'Betalningsuppgifter')
        p.setFont('Helvetica', 8); y -= 13
        for s in [co['bank'], co['account'], f'Swish: {co["swish"]}' if co['swish'] else '',
                  f'Betalningsreferens: {i["payment_reference"] or number}']:
            if s:
                p.drawString(42, y, s); y -= 11
        p.save()
        return send_file(path, as_attachment=True, download_name=f'Faktura_{number}.pdf')

    app.view_functions['invoice_pdf'] = invoice_pdf_fixed
    app.add_url_rule('/invoice/<int:invoice_id>.pdf', endpoint='invoice_pdf_fixed', view_func=invoice_pdf_fixed)
