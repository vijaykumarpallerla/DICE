import subprocess
import sys
import os

def build():
    print("Checking dependencies...")
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller is not installed. Installing now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("PyInstaller installed successfully.")

    import PyInstaller.__main__

    # Define build parameters
    # Note: Semicolon (;) is used as a path separator on Windows for --add-data
    params = [
        'app.py',
        '--onefile',
        '--name=web_dice',
        '--add-data=index.html;.',
        '--add-data=login.html;.',
        '--add-data=google.json;.',
        '--hidden-import=google_auth_oauthlib.flow',
        '--hidden-import=google.oauth2.credentials',
        '--hidden-import=google.auth.transport.requests',
        '--clean'
    ]

    print("Building web_dice.exe with PyInstaller...")
    PyInstaller.__main__.run(params)
    print("\nBuild complete! The executable can be found in the 'dist' directory as 'web_dice.exe'.")

if __name__ == '__main__':
    build()
