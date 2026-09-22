"""Build a data-bearing web app and a self-contained, offline-capable HTML file.
Run `npm --prefix frontend run build` first. Every embedded record comes from the
running local collector's /api/workspace endpoint or a previously exported copy.
"""
from pathlib import Path
import argparse
import base64
import json
import mimetypes
import re
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent


def build(snapshot=None,standalone=None):
    if snapshot:
        data=json.loads(Path(snapshot).read_text())
    else:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/workspace',timeout=30) as response:
            data=json.load(response)
    assert data['schema']=='league-dna-workspace-1'
    assert data['overview']['source']['domain']=='virtuals.betika.com'
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    bootstrap=f'<script id="league-data">window.__LEAGUE_BOOTSTRAP__={encoded};</script>'
    web=ROOT/'web';index=web/'index.html';html=index.read_text()
    # Idempotent for repeated packaging; a new Vite build replaces index.html.
    html=re.sub(r'<script id="league-data">.*?</script>','',html,flags=re.S)
    html=html.replace('<head>','<head>'+bootstrap,1)
    index.write_text(html)
    def asset_url(match):
        path=match.group(2).strip()
        candidate=web/path.lstrip('/') if path.startswith('/') else web/'assets'/path
        if candidate.exists() and candidate.is_file():
            mime=mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
            return 'url("data:'+mime+';base64,'+base64.b64encode(candidate.read_bytes()).decode()+'")'
        return match.group(0)
    def style(match):
        tag=match.group(0);url=re.search(r'href="([^"]+)"',tag).group(1)
        path=web/url.lstrip('/')
        css=re.sub(r"url\((['\"]?)([^)'\"]+)\1\)",asset_url,path.read_text())
        return '<style>'+css+'</style>'
    standalone_html=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>',style,html)
    def script(match):
        tag=match.group(0);url=re.search(r'src="([^"]+)"',tag).group(1)
        js=(web/url.lstrip('/')).read_text().replace('</script','<\\/script')
        return '<script type="module">'+js+'</script>'
    standalone_html=re.sub(r'<script\b[^>]*src="[^"]+"[^>]*>\s*</script>',script,standalone_html)
    standalone_html=re.sub(r'<link\b[^>]*rel="modulepreload"[^>]*>','',standalone_html)
    destination=Path(standalone or ROOT/'League-DNA.html');destination.write_text(standalone_html)
    assert not re.search(r'<script\b[^>]*src=',standalone_html)
    assert not re.search(r'<link\b[^>]*rel="stylesheet"',standalone_html)
    print(json.dumps({'standalone':str(destination),'bytes':destination.stat().st_size,'captured_at':data['captured_at'],
                      'incoming_teams':len(data['roster']),'current_final_matches':data['diagnostics']['current_final_matches'],
                      'records':len(data['results']),'source':'Betika captured records only'},indent=2))
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot');parser.add_argument('--standalone');args=parser.parse_args()
    build(args.snapshot,args.standalone)
