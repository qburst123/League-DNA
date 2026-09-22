"""One-process launcher for the live League DNA app."""
import argparse
import os
from pathlib import Path
import socket
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import threading
import time
import urllib.request
import webbrowser


def verify_timezone():
    """Windows may have no system IANA database; requirements installs tzdata."""
    try:
        return ZoneInfo('Africa/Nairobi')
    except ZoneInfoNotFoundError:
        command = (f'& "{sys.executable}" -m pip install tzdata' if os.name == 'nt'
                   else f'"{sys.executable}" -m pip install tzdata')
        raise SystemExit(
            'Required timezone data for Africa/Nairobi is missing.\n'
            'Install it in this application Python environment, then start again.\n'
            + ('PowerShell command:\n  ' if os.name == 'nt' else 'Command:\n  ')
            + command + '\nYour stored database has not been modified.'
        ) from None


def main():
    parser=argparse.ArgumentParser(description='Start the League DNA collector and open its browser interface.')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--offline',action='store_true',help='Open the stored database without contacting Betika.')
    parser.add_argument('--data-dir',help='Persistent data directory; defaults to the project data folder.')
    args=parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    verify_timezone()
    if args.offline:os.environ['LEAGUE_OFFLINE']='1'
    if args.data_dir:os.environ['LEAGUE_DATA_DIR']=str(Path(args.data_dir).resolve())
    url=f'http://localhost:{args.port}'
    probe=socket.socket()
    try:probe.bind(('0.0.0.0',args.port))
    except OSError:
        try:
            import json
            with urllib.request.urlopen(url+'/api/workspace',timeout=4) as response:data=json.load(response)
            if data.get('schema')=='league-dna-workspace-1':
                print(f'League DNA is already running at {url}. Not starting a second collector.')
                if not args.no_browser:webbrowser.open(url+'/#gold')
                return
        except Exception:pass
        raise SystemExit(f'Port {args.port} is occupied. Stop the old server, then start again, or use --port with a free port.')
    finally:probe.close()
    if not args.no_browser:
        def open_when_ready():
            for _ in range(30):
                try:
                    with urllib.request.urlopen(url,timeout=1) as response:
                        if response.status==200:webbrowser.open(url+'/#gold');return
                except Exception:time.sleep(.5)
        threading.Thread(target=open_when_ready,daemon=True).start()
    print(f'Starting League DNA at {url} ...\nThe address becomes available after server startup completes.\nKeep this terminal open to collect new results. Ctrl+C stops the collector; stored data is retained.')
    import uvicorn
    uvicorn.run('backend.app:app',host='0.0.0.0',port=args.port,workers=1,log_level='info',access_log=False)


if __name__=='__main__':main()
