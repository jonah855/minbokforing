import json
from flask import request, jsonify


def register_invoice_customer_fix(app, conn):
    """Allow creating a new customer directly from Ny faktura without breaking the existing invoice flow."""

    @app.post('/invoice/create-customer-inline')
    def invoice_create_customer_inline():
        data = request.get_json(silent=True) or request.form
        name = str(data.get('name', '')).strip()
        if not name:
            return jsonify({'ok': False, 'error': 'Kundnamn saknas.'}), 400

        def val(key):
            value = data.get(key, '')
            return str(value).strip() if value is not None else ''

        c = conn()
        cols = {r['name'] for r in c.execute('PRAGMA table_info(customers)').fetchall()}
        fields = ['name']
        values = [name]
        for key in ['orgnr', 'vat_no', 'address', 'zip', 'city', 'email', 'phone']:
            if key in cols:
                fields.append(key)
                values.append(val(key))
        placeholders = ','.join('?' for _ in fields)
        try:
            cur = c.execute(
                f"INSERT INTO customers ({','.join(fields)}) VALUES ({placeholders})",
                tuple(values),
            )
            c.commit()
            return jsonify({'ok': True, 'id': cur.lastrowid, 'name': name})
        except Exception as exc:
            c.rollback()
            return jsonify({'ok': False, 'error': f'Kunden kunde inte sparas: {exc}'}), 400

    # Inject a small amount of JS after the existing invoice form is rendered.
    # The existing page intentionally marks register-filled fields readonly; this
    # upgrade makes them editable and adds a "Ny kund" flow while preserving the
    # current invoice endpoint and accounting logic.
    original_after = app.after_request_funcs.get(None, [])[:]

    def inject_inline_customer(response):
        try:
            if response.content_type and 'text/html' in response.content_type and request.path == '/invoices/new':
                body = response.get_data(as_text=True)
                script = r'''<script id="mb-inline-customer-fix">
(function(){
  const sel=document.getElementById('customer_id');
  if(!sel) return;
  if(!Array.from(sel.options).some(o=>o.value==='__new__')){
    const o=document.createElement('option'); o.value='__new__'; o.textContent='+ Ny kund'; sel.appendChild(o);
  }
  const ids=['customer_name','customer_orgnr','buyer_vat_no','customer_address','customer_zip','customer_city','customer_email','customer_phone'];
  function editable(isNew){ids.forEach(id=>{const e=document.getElementById(id);if(e){e.readOnly=!isNew;e.removeAttribute('readonly');if(!isNew)e.readOnly=true;}});}
  const old=window.selectCustomer;
  window.selectCustomer=function(id){
    if(id==='__new__'){
      ids.forEach(id=>{const e=document.getElementById(id);if(e){e.readOnly=false;e.removeAttribute('readonly');if(id==='buyer_vat_no') e.value=''; else e.value='';}});
      return;
    }
    if(typeof old==='function') old(id);
    editable(false);
  };
  const form=document.getElementById('f');
  if(form){
    const oldSubmit=window.submitForm;
    form.addEventListener('submit', async function(ev){
      if(sel.value!=='__new__') return;
      ev.preventDefault();
      const get=id=>(document.getElementById(id)?.value||'').trim();
      if(!get('customer_name')){alert('Skriv kundens namn.');return;}
      const payload={name:get('customer_name'),orgnr:get('customer_orgnr'),vat_no:get('buyer_vat_no'),address:get('customer_address'),zip:get('customer_zip'),city:get('customer_city'),email:get('customer_email'),phone:get('customer_phone')};
      try{
        const r=await fetch('/invoice/create-customer-inline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await r.json();
        if(!d.ok){alert(d.error||'Kunden kunde inte sparas.');return;}
        const opt=document.createElement('option');opt.value=String(d.id);opt.textContent=d.name;sel.appendChild(opt);sel.value=String(d.id);
        editable(false);
        if(typeof oldSubmit==='function' && !oldSubmit.call(form)) return;
        form.submit();
      }catch(e){alert('Kunden kunde inte sparas. Kontrollera anslutningen och försök igen.');}
    },true);
  }
})();
</script>'''
                if '</body>' in body:
                    body = body.replace('</body>', script + '</body>', 1)
                else:
                    body += script
                response.set_data(body)
        except Exception:
            pass
        return response

    app.after_request(inject_inline_customer)
