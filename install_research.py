"""Validate and install the sole research extension before publishing the PWA."""
import ast
from pathlib import Path
import re


def install(root=Path('.')):
    required = ['server.py', 'knowledge.py', 'research_extension.py',
                'web/index.html', 'web/app.js', 'web/web_search_ui.js', 'web/sw.js']
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise SystemExit('Research build inputs missing: ' + ', '.join(missing) +
                         '. Check Docker COPY and .dockerignore together.')
    server = root / 'server.py'
    s = server.read_text(encoding='utf-8')
    marker = "if __name__ == '__main__':"
    addon = 'from research_extension import install as install_research\ninstall_research(Handler, APIError)\n\n'
    if addon not in s:
        if s.count(marker) != 1:
            raise SystemExit('Missing or ambiguous server startup insertion point')
        s = s.replace(marker, addon + marker)
    page = root / 'web/index.html'
    h = page.read_text(encoding='utf-8')
    old = '<script src="/app.js" defer></script>'
    script = '<script src="/web_search_ui.js" defer></script>'
    if script not in h:
        if h.count(old) != 1:
            raise SystemExit('Missing or ambiguous frontend insertion point')
        h = h.replace(old, old + '\n  ' + script)
    for control in ['ai-form', 'page-ai-open', 'ai-question']:
        if f'id="{control}"' not in h:
            raise SystemExit('Missing page AI control: ' + control)
    sw = root / 'web/sw.js'
    w = sw.read_text(encoding='utf-8')
    w, count = re.subn(r"const CACHE = '[^']+';", "const CACHE = 'agent-workshop-shell-v4-research';", w)
    if count != 1:
        raise SystemExit('Missing or ambiguous service worker cache version')
    if "'/web_search_ui.js'" not in w:
        if "'/app.js'," not in w:
            raise SystemExit('Missing service worker script insertion point')
        w = w.replace("'/app.js',", "'/app.js', '/web_search_ui.js',")
    ast.parse(s, filename='server.py')
    for name in ['knowledge.py', 'research_extension.py']:
        ast.parse((root / name).read_text(encoding='utf-8'), filename=name)
    server.write_text(s, encoding='utf-8')
    page.write_text(h, encoding='utf-8')
    sw.write_text(w, encoding='utf-8')


if __name__ == '__main__':
    install()
