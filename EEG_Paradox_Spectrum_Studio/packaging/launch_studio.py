"""Windowed one-file entry point; also retains Spectrum Studio's CLI options."""
import multiprocessing
import sys


if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        from EEG_Paradox_Spectrum_Studio.runtime import initialize_standalone
        folder=initialize_standalone()
        # Windowed Windows programs have no console streams. Libraries and CLI
        # errors still need real writable streams, retained outside extraction.
        log=(folder/'application.log').open('a',encoding='utf-8',buffering=1)
        if sys.stdout is None:sys.stdout=log
        if sys.stderr is None:sys.stderr=log
        from EEG_Paradox_Spectrum_Studio.__main__ import main
        raise SystemExit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        if len(sys.argv)==1 or '--gui' in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            root=tk.Tk();root.withdraw()
            messagebox.showerror('Spectrum Studio could not start',
                'Please check that your app-data folder is writable. Details are saved in application.log.',parent=root)
            root.destroy()
        raise SystemExit(1)
