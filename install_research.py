"""Build-time, idempotent extension installation for the compressed PWA release."""
from pathlib import Path
server = Path('server.py')
s = server.read_text(encoding='utf-8')
marker = "if __name__ == '__main__':"
addon = 'from research_extension import install as install_research\ninstall_research(Handler, APIError)\n\n'
if addon not in s:
    if marker not in s:
        raise SystemExit('No startup insertion point; refusing to patch server')
    server.write_text(s.replace(marker, addon + marker), encoding='utf-8')
page = Path('web/index.html')
h = page.read_text(encoding='utf-8')
old = '<script src="/app.js" defer></script>'
new = old + '\n  <script src="/web_search_ui.js" defer></script>'
if new not in h:
    if old not in h:
        raise SystemExit('Missing frontend insertion point')
    page.write_text(h.replace(old, new), encoding='utf-8')
sw = Path('web/sw.js')
w = sw.read_text(encoding='utf-8')
if "'/web_search_ui.js'" not in w:
    w = w.replace("const CACHE = 'agent-workshop-shell-v2';", "const CACHE = 'agent-workshop-shell-v3-research';")
    w = w.replace("'/app.js',", "'/app.js', '/web_search_ui.js',")
    sw.write_text(w, encoding='utf-8')
