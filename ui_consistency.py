from flask import request


def register_ui_consistency(app):
    """One visual system for every page, including legacy v13 routes and newer modules."""
    css = r'''
<style id="minbokforing-consistent-ui">
:root{--mb-bg:#f5f5f7;--mb-surface:#fff;--mb-text:#171717;--mb-muted:#6b7280;--mb-border:#e5e7eb;--mb-accent:#111;--mb-sidebar:238px;--mb-radius:14px}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--mb-bg);color:var(--mb-text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif;font-size:15px;line-height:1.45}
body{min-height:100vh}
/* Normalize both the old top navigation and newer side navigation into one sidebar. */
body>nav,.side{position:fixed!important;left:0!important;top:0!important;bottom:0!important;width:var(--mb-sidebar)!important;height:100vh!important;margin:0!important;padding:22px 14px!important;background:#111!important;color:#fff!important;border:0!important;border-radius:0!important;box-shadow:none!important;overflow-y:auto!important;overflow-x:hidden!important;z-index:100!important;white-space:normal!important}
body>nav{display:block!important}
body>nav b,.side h2{display:block!important;margin:0 8px 24px!important;padding:0!important;color:#fff!important;font-size:19px!important;font-weight:750!important;letter-spacing:-.02em}
body>nav a,.side a{display:block!important;color:#d9d9dc!important;text-decoration:none!important;margin:2px 0!important;padding:9px 11px!important;border-radius:9px!important;font-size:14px!important;line-height:1.35!important;transition:background .15s ease,color .15s ease!important}
body>nav a:hover,.side a:hover{background:#262626!important;color:#fff!important}
body>nav a:focus-visible,.side a:focus-visible{outline:2px solid #fff;outline-offset:1px}
body>main,.main{margin-left:var(--mb-sidebar)!important;max-width:none!important;width:auto!important;min-height:100vh!important;padding:30px 34px 48px!important}
body>main>*,.main>*,main .card{max-width:1320px}
h1{font-size:28px!important;line-height:1.2!important;letter-spacing:-.025em!important;margin:0 0 18px!important}
h2{font-size:21px!important;letter-spacing:-.015em!important}
h3{font-size:17px!important}
.card{background:var(--mb-surface)!important;border:1px solid rgba(0,0,0,.045)!important;border-radius:var(--mb-radius)!important;padding:22px!important;margin:0 0 16px!important;box-shadow:0 2px 12px rgba(0,0,0,.055)!important}
.grid{gap:14px!important}
.row{gap:14px!important}
.actions{gap:8px!important;align-items:center}
button,.btn{background:#111!important;color:#fff!important;border:1px solid #111!important;border-radius:9px!important;padding:10px 14px!important;min-height:40px!important;font-weight:600!important;text-decoration:none!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;cursor:pointer!important}
button:hover,.btn:hover{background:#2b2b2b!important}
button.red,.red{background:#9b1c1c!important;border-color:#9b1c1c!important}
input,select,textarea{background:#fff!important;color:#171717!important;border:1px solid #d1d5db!important;border-radius:9px!important;padding:10px 11px!important;outline:none!important;box-shadow:none!important}
input:focus,select:focus,textarea:focus{border-color:#777!important;box-shadow:0 0 0 3px rgba(0,0,0,.07)!important}
table{background:#fff!important;border-collapse:separate!important;border-spacing:0!important;overflow:hidden!important}
th{background:#fafafa!important;color:#555!important;font-weight:650!important;font-size:12px!important;text-transform:none!important}
th,td{padding:11px 10px!important;border-bottom:1px solid var(--mb-border)!important}
tbody tr:last-child td{border-bottom:0!important}
.good,.bad,.warn{border-radius:10px!important;padding:12px 14px!important;margin-bottom:12px!important}
.pill{border-radius:999px!important;padding:5px 9px!important;background:#eee!important}
.muted{color:var(--mb-muted)!important}
@media(max-width:850px){
 :root{--mb-sidebar:0px}
 body>nav,.side{position:sticky!important;top:0!important;bottom:auto!important;width:100%!important;height:auto!important;max-height:none!important;padding:12px 12px 10px!important;display:block!important;white-space:nowrap!important;overflow-x:auto!important;overflow-y:hidden!important}
 body>nav b,.side h2{display:inline-block!important;margin:0 18px 0 4px!important;vertical-align:middle!important;font-size:17px!important}
 body>nav a,.side a{display:inline-block!important;margin:0 3px!important;padding:8px 9px!important;vertical-align:middle!important}
 body>main,.main{margin-left:0!important;padding:18px 14px 32px!important}
 .grid,.row{grid-template-columns:1fr!important}
 .card{padding:17px!important}
 table{display:block!important;overflow-x:auto!important;white-space:nowrap!important}
}
</style>'''

    @app.after_request
    def _consistent_ui(response):
        content_type = response.headers.get('Content-Type','')
        if 'text/html' not in content_type:
            return response
        body = response.get_data(as_text=True)
        if 'id="minbokforing-consistent-ui"' not in body and '</head>' in body:
            body = body.replace('</head>', css + '</head>', 1)
            response.set_data(body)
        return response
