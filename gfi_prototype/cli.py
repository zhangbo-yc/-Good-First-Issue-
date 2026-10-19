"""CLI for the local portal; keep launch paths independent of the shell cwd."""
import argparse
import sysconfig
from pathlib import Path

from gfi_prototype.portal import Portal
from gfi_prototype.portal_server import create_server
from gfi_prototype.settings import load_project_env


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the newcomer-facing GFI HTML portal locally.')
    parser.add_argument('--port', type=int, default=8503)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Project workspace containing data/ and optional .env; defaults to the source root.')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    web_root = root / 'web'
    if not web_root.is_dir():
        web_root = Path(__file__).resolve().parents[1] / 'web'
    if not web_root.is_dir():
        # Wheel installs keep static files outside the Python module directory.
        web_root = Path(sysconfig.get_path('data')) / 'share/gfi-prototype/web'
    load_project_env(root)
    portal = Portal(root)
    server = create_server(portal, web_root, host=args.host, port=args.port)
    print(f'GFI newcomer portal: http://{args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
