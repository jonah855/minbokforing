import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import send_file, abort


def register_invoice_hardening(app, conn, setting, EXPORT):
    """Small compatibility layer that hardens the invoice PDF endpoint without replacing the invoice module."""

    def company():
        return {
            'name': setting('company_name', ''),
            'orgnr': setting('company_orgnr', ''),
            'vat_no': setting('company_vat', ''),
            'address': setting('company_address', ''),
            'zip': setting('company_zip', ''),
            'city': setting('company_city', ''),
            'email': setting('company_email', ''),
            'phone': setting('company_phone', ''),
            'bank': setting('company_bank', ''),
            'account': setting('company_bank_account', ''),
            'swish': setting('company_swish', ''),
        }

    def pdf(invoice_id):
        c = conn()
        invoice = c.execute(
            '''SELECT i.*, c.name AS customer_name, c.orgnr AS customer_orgnr,
                      c.address AS customer_address, c.zip AS customer_zip,
                      c.city AS customer_city, c.vat_no AS customer_vat_no
               FROM invoices i JOIN customers c ON c.id=i.customer_id
               WHERE i.id=?''', (invoice_id,)
        ).fetchone()
        if not invoice:
            abort(404)
        lines = c.execute(
            'SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id', (invoice_id,)
        ).fetchall()

        co = company()
        os.makedirs(EXPORT, exist_ok=True)
        number = invoice['number']
        path = os.path.join(EXPORT, f'Faktura_{number}.pdf')
        pdfc = canvas.Canvas(path, pagesize=A4)
        width, height = A4
        y = height - 50

        def text(value, x, yy, size=10, bold=False):
            pdfc.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
            pdfc.drawString(x, yy, str(value or ''))

        text(co['name'] or 'Företag', 45, y, 18, True)
        text('FAKTURA', width - 160, y, 20, True)
        y -= 28
        for value in [co['address'], f"{co['zip']} {co['city']}", f"Org.nr: {co['orgnr']}", f"Momsreg.nr: {co['vat_no']}"]:
            if value:
                text(value, 45, y)
                y -= 14

        y -= 8
        text(f"Fakturanummer: {number}", width - 250, y, 10, True)
        y -= 14
        text(f"Fakturadatum: {invoice['invoice_date']}", width - 250, y)
        y -= 14
        text(f"Förfallodatum: {invoice['due_date']}", width - 250, y)
        y -= 14
        if invoice['delivery_date']:
            text(f"Leveransdatum: {invoice['delivery_date']}", width - 250, y)

        y -= 30
        text('Kund', 45, y, 11, True)
        y -= 16
        for value in [invoice['customer_name'], invoice['customer_address'], f"{invoice['customer_zip']} {invoice['customer_city']}", f"Org.nr: {invoice['customer_orgnr']}", f"Momsreg.nr: {invoice['customer_vat_no']}"]:
            if value:
                text(value, 45, y)
                y -= 14

        y -= 12
        pdfc.line(45, y, width - 45, y)
        y -= 18
        text('Beskrivning', 45, y, 10, True)
        text('Antal', 330, y, 10, True)
        text('À-pris', 390, y, 10, True)
        text('Moms', 455, y, 10, True)
        text('Summa', 505, y, 10, True)
        y -= 16

        for line in lines:
            desc = str(line['description'] or '')
            if len(desc) > 42:
                desc = desc[:39] + '...'
            qty = float(line['quantity'] or 0)
            price = float(line['price_ex_vat'] or 0)
            rate = int(line['vat_rate'] or 0)
            total = float(line['line_total'] or 0)
            text(desc, 45, y)
            text(f"{qty:g} {line['unit'] or 'st'}", 330, y)
            text(f"{price:.2f}", 390, y)
            text(f"{rate}%", 455, y)
            text(f"{total:.2f}", 505, y)
            y -= 16
            if y < 170:
                pdfc.showPage()
                y = height - 50

        y -= 10
        pdfc.line(390, y, width - 45, y)
        y -= 20
        text(f"Delsumma: {float(invoice['subtotal'] or 0):.2f} {invoice['currency'] or 'SEK'}", 390, y)
        y -= 16
        text(f"Moms: {float(invoice['vat'] or 0):.2f} {invoice['currency'] or 'SEK'}", 390, y)
        y -= 20
        text(f"ATT BETALA: {float(invoice['total'] or 0):.2f} {invoice['currency'] or 'SEK'}", 390, y, 14, True)

        y -= 35
        if invoice['reference']:
            text(f"Er referens: {invoice['reference']}", 45, y)
            y -= 14
        if invoice['our_reference']:
            text(f"Vår referens: {invoice['our_reference']}", 45, y)
            y -= 14
        text(f"Betalningsreferens: {invoice['payment_reference'] or number}", 45, y)
        y -= 16
        if co['account']:
            text(f"Betalning till: {co['account']}", 45, y)
        elif co['bank']:
            text(f"Bank: {co['bank']}", 45, y)
        if co['swish']:
            y -= 14
            text(f"Swish: {co['swish']}", 45, y)

        pdfc.save()
        return send_file(path, mimetype='application/pdf', as_attachment=False, download_name=f'Faktura_{number}.pdf')

    # The existing invoice module names this endpoint invoice_pdf_upgrade.
    # Replace only its view function, keeping its URL/routing intact.
    if 'invoice_pdf_upgrade' in app.view_functions:
        app.view_functions['invoice_pdf_upgrade'] = pdf
    else:
        app.add_url_rule('/invoice/<int:invoice_id>.pdf', endpoint='invoice_pdf_upgrade_hardened', view_func=pdf)
