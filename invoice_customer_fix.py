from flask import request, jsonify
import datetime


def register_invoice_customer_fix(app, conn):
    """Improve customer handling on Ny faktura and make invoice PDFs download."""

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
        try:
            cols = {r['name'] for r in c.execute('PRAGMA table_info(customers)').fetchall()}
            fields = ['name']
            values = [name]

            # Fill all supported customer fields exposed by the invoice form.
            for key in [
                'orgnr', 'vat_no', 'address', 'zip', 'city', 'email', 'phone',
                'reference', 'contact_person', 'customer_number', 'customer_no',
                'country', 'country_code', 'notes'
            ]:
                if key in cols:
                    fields.append(key)
                    values.append(val(key))

            # Existing databases require customers.created to be non-null.
            if 'created' in cols:
                fields.append('created')
                values.append(datetime.datetime.now().isoformat(timespec='seconds'))

            # Avoid accidentally creating an identical customer twice.
            existing = c.execute(
                'SELECT id, name FROM customers WHERE name = ? LIMIT 1',
                (name,)
            ).fetchone()
            if existing:
                return jsonify({
                    'ok': True,
                    'id': existing['id'],
                    'name': existing['name'],
                    'existing': True,
                })

            placeholders = ','.join('?' for _ in fields)
            cur = c.execute(
                f"INSERT INTO customers ({','.join(fields)}) VALUES ({placeholders})",
                tuple(values),
            )
            c.commit()
            return jsonify({'ok': True, 'id': cur.lastrowid, 'name': name, 'existing': False})
        except Exception as exc:
            c.rollback()
            return jsonify({'ok': False, 'error': f'Kunden kunde inte sparas: {exc}'}), 400
        finally:
            c.close()

    def inject_invoice_upgrades(response):
        try:
            if not response.content_type or 'text/html' not in response.content_type:
                return response
            body = response.get_data(as_text=True)

            if request.path == '/invoices/new':
                script = r'''<script id="mb-inline-customer-fix">
(function(){
  const sel=document.getElementById('customer_id');
  if(!sel) return;

  // "Välj kund" is always available and is the initial selection.
  const first=sel.options[0];
  if(first && !first.value){
    first.textContent='Välj kund';
  } else if(!Array.from(sel.options).some(o=>o.value==='')){
    const choose=document.createElement('option');
    choose.value=''; choose.textContent='Välj kund';
    sel.insertBefore(choose,sel.firstChild);
  }

  // Always offer a dedicated new-customer choice.
  if(!Array.from(sel.options).some(o=>o.value==='__new__')){
    const o=document.createElement('option');
    o.value='__new__'; o.textContent='+ Ny kund'; sel.appendChild(o);
  }

  const ids=['customer_name','customer_orgnr','buyer_vat_no','customer_address','customer_zip','customer_city','customer_email','customer_phone'];

  function field(id){ return document.getElementById(id); }
  function get(id){ const e=field(id); return (e && e.value || '').trim(); }

  function setEditable(isNew){
    ids.forEach(function(id){
      const e=field(id);
      if(!e) return;
      e.readOnly=!isNew;
      if(isNew) e.removeAttribute('readonly');
      else e.setAttribute('readonly','readonly');
    });
  }

  function clearNewFields(){
    ids.forEach(function(id){
      const e=field(id);
      if(e){ e.readOnly=false; e.removeAttribute('readonly'); e.value=''; }
    });
  }

  const oldSelect=window.selectCustomer;
  window.selectCustomer=function(id){
    if(id==='__new__'){
      clearNewFields();
      return;
    }
    if(typeof oldSelect==='function') oldSelect(id);
    setEditable(false);
  };

  // Add a visible "Spara kund" button next to the customer section.
  function addSaveButton(){
    if(document.getElementById('mb-save-customer')) return;
    const target=field('customer_phone') || field('customer_email') || field('customer_city') || field('customer_address');
    if(!target) return;
    const label=target.closest('label');
    const wrap=document.createElement('div');
    wrap.id='mb-save-customer-wrap';
    wrap.style.marginTop='12px';
    const button=document.createElement('button');
    button.type='button';
    button.id='mb-save-customer';
    button.textContent='Spara kund';
    button.style.display='none';
    button.style.background='#16794b';
    button.style.padding='11px 18px';
    button.style.borderRadius='9px';
    button.style.color='#fff';
    button.style.border='0';
    button.style.cursor='pointer';
    wrap.appendChild(button);
    if(label && label.parentNode) label.parentNode.appendChild(wrap);
    else target.parentNode.appendChild(wrap);

    button.addEventListener('click',async function(){
      if(sel.value!=='__new__'){
        alert('Välj "+ Ny kund" först om du vill skapa en ny kund.');
        return;
      }
      const name=get('customer_name');
      if(!name){ alert('Skriv kundens namn.'); field('customer_name')?.focus(); return; }

      button.disabled=true;
      button.textContent='Sparar kund...';
      const payload={
        name:name,
        orgnr:get('customer_orgnr'),
        vat_no:get('buyer_vat_no'),
        address:get('customer_address'),
        zip:get('customer_zip'),
        city:get('customer_city'),
        email:get('customer_email'),
        phone:get('customer_phone')
      };
      try{
        const r=await fetch('/invoice/create-customer-inline',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify(payload)
        });
        const d=await r.json();
        if(!d.ok){ alert(d.error||'Kunden kunde inte sparas.'); return; }

        // Add/select the saved customer without submitting the invoice.
        let opt=Array.from(sel.options).find(o=>o.value===String(d.id));
        if(!opt){
          opt=document.createElement('option');
          opt.value=String(d.id);
          opt.textContent=d.name;
          sel.appendChild(opt);
        }
        sel.value=String(d.id);
        setEditable(false);
        button.style.display='none';
        button.textContent='Spara kund';
        alert(d.existing ? 'Kunden finns redan och har valts.' : 'Kunden har sparats.');
      }catch(e){
        alert('Kunden kunde inte sparas. Kontrollera anslutningen och försök igen.');
      }finally{
        button.disabled=false;
      }
    });

    window.mbUpdateCustomerSaveButton=function(){
      button.style.display=sel.value==='__new__' ? 'inline-block' : 'none';
    };
  }

  addSaveButton();
  if(!window.mbUpdateCustomerSaveButton){
    setTimeout(addSaveButton,50);
  }

  sel.addEventListener('change',function(){
    if(typeof window.selectCustomer==='function') window.selectCustomer(sel.value);
    if(typeof window.mbUpdateCustomerSaveButton==='function') window.mbUpdateCustomerSaveButton();
  });

  // Start in "Välj kund" mode. Do not auto-create a customer when the invoice is submitted.
  if(sel.value==='__new__') clearNewFields();
  else if(!sel.value) setEditable(true);
  else setEditable(false);
  if(typeof window.mbUpdateCustomerSaveButton==='function') window.mbUpdateCustomerSaveButton();
})();
</script>'''
                body = body.replace('</body>', script + '</body>', 1) if '</body>' in body else body + script

            # The embedded desktop webview can show a blank PDF viewer page.
            # Force the existing same-origin invoice PDF link to download instead.
            if request.path.startswith('/invoice/') and not request.path.endswith('.pdf'):
                body = body.replace('href="/invoice/', 'download="Faktura.pdf" href="/invoice/', 1)
                body = body.replace("href='/invoice/", "download='Faktura.pdf' href='/invoice/", 1)

            response.set_data(body)
        except Exception:
            pass
        return response

    app.after_request(inject_invoice_upgrades)
