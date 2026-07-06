import sys
import logging

logger = logging.getLogger("EyeMonitor.WindowTracker")

# Suppress errors on non-Windows platforms
HAS_WIN32 = False
if sys.platform == "win32":
    try:
        import win32gui
        import win32process
        HAS_WIN32 = True
    except ImportError:
        logger.warning("pywin32 not installed, window tracking will be disabled.")

# Common meeting application indicators in window titles/process names
DND_WINDOW_KEYWORDS = [
    "zoom meeting", "microsoft teams", "google meet", "webex", 
    "discord", "skype", "gotomeeting", "slack call", "screen sharing"
]

# Common coding or reading environments
FOCUS_KEYWORDS = {
    "vscode": "Visual Studio Code",
    "visual studio": "Visual Studio",
    "sublime": "Sublime Text",
    "notepad++": "Notepad++",
    "pycharm": "PyCharm",
    "intellij": "IntelliJ IDEA",
    "eclipse": "Eclipse",
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "edge": "Microsoft Edge",
    "excel": "Excel",
    "word": "Word",
    "acrobat": "Adobe Acrobat",
    "pdf": "PDF Reader",
}

def get_active_window() -> str:
    """Get the active window title. Returns empty string on failure or non-Windows."""
    if not HAS_WIN32:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            return title if title else ""
    except Exception as e:
        logger.debug(f"Error getting active window title: {e}")
    return ""

def is_meeting_active() -> bool:
    """Check if the active window title corresponds to a meeting or call."""
    title = get_active_window().lower()
    if not title:
        return False
    
    for kw in DND_WINDOW_KEYWORDS:
        if kw in title:
            return True
    return False

def correlate_focus_app() -> str:
    """Correlate active window title with specific category/name for insights."""
    title = get_active_window().lower()
    if not title:
        return "Unknown"
        
    for kw, label in FOCUS_KEYWORDS.items():
        if kw in title:
            return label
            
    return "Other / Miscellaneous"
