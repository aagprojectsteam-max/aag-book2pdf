"""Frozen entry point: dispatch spawned workers before importing Qt/the app."""
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    import os
    import sys
    # Windows windowed executables have no console handles.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if sys.argv[1:2] == ['--self-test']:
        from book2pdf.selftest import main
        raise SystemExit(main(sys.argv[2:], gui=True))
    from book2pdf.gui.app import main
    raise SystemExit(main())
