import threading
import app
from features import register_features
from invoice_upgrade import register_invoice_upgrade
from invoice_hardening import register_invoice_hardening
from nav_upgrade import register_navigation_upgrade
from invoice_payment_upgrade import register_invoice_payment_upgrade
from invoice_pdf_fix import register_invoice_pdf_fix
from ui_consistency import register_ui_consistency

register_features(app.app, app.conn, app.setting, app.set_setting, app.create_voucher, app.money, app.AC, app.ROOT, app.EXPORT)
register_invoice_upgrade(app.app, app.conn, app.setting, app.set_setting, app.create_voucher, app.money, app.AC, app.ROOT, app.EXPORT)
register_invoice_hardening(app.app, app.conn, app.setting, app.EXPORT)
register_invoice_payment_upgrade(app.app, app.conn, app.setting, app.set_setting, app.create_voucher, app.money, app.AC, app.ROOT, app.EXPORT)
register_invoice_pdf_fix(app.app, app.conn, app.setting, app.EXPORT)
register_navigation_upgrade()
register_ui_consistency(app.app)

if __name__ == '__main__':
    app.conn()
    if app.webview:
        import werkzeug.serving
        threading.Thread(target=lambda: werkzeug.serving.run_simple('127.0.0.1',5000,app.app,threaded=True,use_reloader=False),daemon=True).start()
        app.webview.create_window('Min Bokföring','http://127.0.0.1:5000',width=1400,height=900,resizable=True)
        app.webview.start()
    else:
        app.app.run(host='127.0.0.1',port=5000)
