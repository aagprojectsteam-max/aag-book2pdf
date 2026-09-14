"""Keep desktop dispatch alive until the instrumented installed viewer reports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    source=Path(sys.argv[1]).resolve()
    destination=Path(sys.argv[2]).resolve()
    hook=Path(__file__).resolve().parents[1]/'artifacts/desktop-hook'
    hook.mkdir(parents=True,exist_ok=True)
    (hook/'viewer_acceptance.py').write_text(Path(__file__).with_name('viewer_acceptance.py').read_text())
    (hook/'sitecustomize.py').write_text('from viewer_acceptance import install_hook\ninstall_hook()\n')
    env=dict(os.environ,BOOK2PDF_ACCEPTANCE_DIR=str(destination),PYTHONPATH=str(hook),QT_QPA_PLATFORM='xcb')
    subprocess.run(['gio','open',str(source)],env=env,check=True)
    deadline=time.monotonic()+310
    evidence=destination/'viewer-evidence.json'
    while not evidence.exists() and time.monotonic()<deadline:
        time.sleep(.25)
    if not evidence.exists():
        raise RuntimeError('Desktop dispatch did not produce viewer evidence')
    result=json.loads(evidence.read_text())
    assert not result['errors'],result
    print('DESKTOP_DEFAULT_DISPATCH=PASS')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
