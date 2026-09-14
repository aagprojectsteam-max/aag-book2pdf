"""Console companion using the same engine and frozen worker dispatch."""
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    import sys
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    if sys.argv[1:2] == ['--self-test']:
        from book2pdf.selftest import main
        raise SystemExit(main(sys.argv[2:]))
    from book2pdf.cli import main
    raise SystemExit(main())
