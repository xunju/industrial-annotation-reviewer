"""Run one or all local annotation reviewers without a fixed Python path."""
import argparse
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
TOOLS = {
    'round': ('round_table_review', 8874),
    'square': ('square_table_review', 8875),
    'parts': ('annotation_quality_review', 8876),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', choices=['all', *TOOLS], default='all')
    parser.add_argument('--dataset', type=Path, help='Image and LabelMe JSON directory')
    parser.add_argument('--demo', action='store_true', help='Use generated synthetic examples')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, help='Override port when starting a single tool')
    args = parser.parse_args()
    if args.port is not None and (args.tool == 'all' or not 1 <= args.port <= 65535):
        parser.error('--port requires one tool and a port between 1 and 65535')
    if args.demo and args.dataset:
        parser.error('--demo and --dataset are mutually exclusive')
    selected = list(TOOLS) if args.tool == 'all' else [args.tool]
    if 'square' in selected and any(importlib.util.find_spec(m) is None for m in ('numpy', 'cv2')):
        parser.error('Square-table preview needs dependencies: python -m pip install -r requirements.txt')
    if args.dataset and not args.dataset.expanduser().is_dir():
        parser.error('Dataset directory does not exist')
    if args.demo:
        from examples.generate_demo import generate
        generate(ROOT / 'demo_data')
    processes = []
    try:
        # Check every port before starting; never open an unrelated existing server.
        for name in selected:
            port = args.port or TOOLS[name][1]
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        for name in selected:
            folder, default_port = TOOLS[name]
            port = args.port or default_port
            command = [sys.executable, '-u', str(ROOT / folder / 'server.py'),
                       '--port', str(port), '--no-browser']
            dataset = ROOT / 'demo_data' / name if args.demo else args.dataset
            if dataset:
                command += ['--dataset', str(dataset.expanduser().resolve())]
            process = subprocess.Popen(command, cwd=ROOT / folder)
            processes.append(process)
            url = f'http://127.0.0.1:{port}/'
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError(f'{name} server exited during startup')
                try:
                    with urllib.request.urlopen(url + 'api/config', timeout=.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError(f'{name} server did not become ready')
            print(f'{name}: {url}', flush=True)
            if not args.no_browser:
                webbrowser.open(url)
        print('Keep this terminal open. Press Ctrl+C to stop all started reviewers.', flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(.3)
        raise RuntimeError('A reviewer exited unexpectedly')
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == '__main__':
    sys.exit(main())
