import app as core


def register_navigation_upgrade():
    """Add invoice navigation to the shared v13 navigation without replacing the core app."""
    nav_old = '<a href="/receipts">Kvitton</a>'
    nav_new = nav_old + '<a href="/invoices">Fakturor</a><a href="/customers">Kunder</a><a href="/articles">Artiklar</a>'
    if nav_old in core.HTML and 'href="/invoices"' not in core.HTML:
        core.HTML = core.HTML.replace(nav_old, nav_new)
