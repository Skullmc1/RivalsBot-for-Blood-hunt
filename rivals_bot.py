"""Marvel Rivals Blood Hunt auto-farmer.

Works in the background: it reads the game window with Windows Graphics Capture and
sends clicks/keys straight to the game window, so the game does not need focus.
(The game window must not be minimized - behind other windows is fine.)

Usage:
    python rivals_bot.py capture [name]   # grab reference images (guided if no name)
    python rivals_bot.py check            # show which images are visible right now
    python rivals_bot.py list             # list required / captured images
    python rivals_bot.py test-input       # check the game accepts background input
    python rivals_bot.py run              # start the bot loop

Hold F8 at any time to stop the bot.
"""

import argparse
import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import psutil
import pydirectinput
from windows_capture import WindowsCapture

# When built as an .exe, keep templates/ and config.json next to the exe.
ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
CONFIG_PATH = ROOT / "config.json"

# name -> (screen to have open in the game, what to draw the box around)
CAPTURE_STEPS = {
    "play_menu":          ("The PLAY menu (where the START button is)",
                           "Some text/icon that is ONLY on this screen, e.g. the START button"),
    "play_tab":           ("The main lobby / home screen",
                           "The PLAY button/tab you click to open the play menu"),
    "mode_button":        ("The PLAY menu",
                           "The button you click to open the game-mode list (skip if there isn't one)"),
    "bloodhunt_selected": ("The PLAY menu with Blood Hunt selected",
                           "The 'Blood Hunt' mode name/label shown as the current mode"),
    "bloodhunt_option":   ("The game-mode list (where you choose a mode)",
                           "The Blood Hunt card/entry you click to choose it"),
    "start_button":       ("The PLAY menu",
                           "The START button that begins matchmaking"),
    "hero_select":        ("Character select (at the start of a match)",
                           "A header/text that is ONLY on character select, not a hero portrait"),
    "thor":               ("Character select",
                           "Thor's portrait/icon in the hero list"),
    "confirm":            ("Character select, after clicking Thor",
                           "The CONFIRM button"),
    "in_match":           ("In a match, playing",
                           "A HUD element always visible in-match, e.g. your ability icons (no numbers)"),
    "wave_end":           ("In a match, right after a wave ends (white countdown)",
                           "The white 'VAMPIRES INCOMING' text (not the number under it)"),
    "wave_start":         ("In a match, in the last 5 s before a wave (yellow countdown)",
                           "The yellow 'VAMPIRES INCOMING' text (not the number under it)"),
    "continue":           ("The DEFEAT/VICTORY result screens",
                           "The 'SPACE  NEXT' hint at the bottom right"),
    "post_match":         ("The lobby after the result screens",
                           "The ONE MORE ROUND button"),
    # Relic cleanup (Forge > Scrap)
    "prepare_button":     ("The PLAY menu", "The PREPARE link above CHANGE MODE"),
    "forge_tab":          ("Blood Hunt > HERO screen", "The FORGE tab at the top (not selected)"),
    "scrap_tab":          ("Forge > FORGE (another sub-tab open)", "The SCRAP sub-tab (white, not selected)"),
    "scrap_tab_selected": ("Forge > SCRAP", "The SCRAP sub-tab (yellow, selected)"),
    "select_all":         ("Forge > SCRAP", "The SELECT ALL button"),
    "deselect_all":       ("Forge > SCRAP after SELECT ALL", "The DESELECT ALL button"),
    "smelt_button":       ("Forge > SCRAP", "The SMELT button text"),
    "smelt_dialog":       ("The SMELT GEAR confirmation", "The SMELT GEAR title"),
    "dont_show_unticked": ("The SMELT GEAR confirmation", "The empty DON'T SHOW AGAIN TODAY box"),
    "dont_show_ticked":   ("The SMELT GEAR confirmation", "The ticked DON'T SHOW AGAIN TODAY box"),
    "confirm_smelt":      ("The SMELT GEAR confirmation", "The CONFIRM button text"),
    "acquired":           ("After smelting", "The ACQUIRED title"),
}
CLEANUP_IMAGES = ["prepare_button", "forge_tab", "scrap_tab", "scrap_tab_selected", "select_all", "deselect_all",
                  "smelt_button", "smelt_dialog", "dont_show_unticked", "dont_show_ticked",
                  "confirm_smelt", "acquired"]
OPTIONAL = {"mode_button", "bloodhunt_option", *CLEANUP_IMAGES}

DEFAULT_CONFIG = {
    "process_names": ["Marvel-Win64-Shipping.exe"],
    # "background" = send input to the game window without focusing it.
    # "foreground" = focus the game and use real mouse/keyboard input (fallback).
    "input_mode": "background",
    "match_threshold": 0.82,
    "skill_key": "f",
    "wave_on": 0.75,         # countdown counts as showing above this score
    "wave_off": 0.6,         # ...and as gone below this
    "wave_gone_time": 0.4,   # seconds it must stay gone before pressing
    "skill_verify_time": 2.5,  # after pressing, wait this long for the icon to light up
    # Where the skill (F) icon is, as fractions of the game window (x1, y1, x2, y2).
    "skill_icon_region": [0.868, 0.906, 0.8875, 0.9406],
    # Relic cleanup: every N finished matches, smelt all (unlocked) relics. 0 = never.
    "cleanup_every": 8,
    "queue_timeout": 600,
    "hero_select_timeout": 90,
    "match_timeout": 3600,
    "poll_interval": 0.25,
    "stop_key_vk": 0x77,  # F8
}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coords on scaled displays
except Exception:
    user32.SetProcessDPIAware()

HWND = ctypes.c_void_p
user32.PostMessageW.argtypes = [HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetForegroundWindow.restype = HWND
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
user32.IsIconic.argtypes = [HWND]
user32.SetForegroundWindow.argtypes = [HWND]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

WM_ACTIVATE, WM_ACTIVATEAPP, WM_NCACTIVATE, WM_SETFOCUS = 0x0006, 0x001C, 0x0086, 0x0007
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
NAMED_KEYS = {"esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09,
              **{f"f{i}": 0x6F + i for i in range(1, 13)}}

pydirectinput.FAILSAFE = True  # foreground mode: slam the mouse into a screen corner to abort
pydirectinput.PAUSE = 0.05


class StopBot(Exception):
    pass


class InputBlocked(Exception):
    pass


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))  # also adds any new settings
    return cfg


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


# ---------------------------------------------------------------- game window

def find_game_pid(cfg):
    names = {n.lower() for n in cfg["process_names"]}
    for p in psutil.process_iter(["name", "pid"]):
        if (p.info["name"] or "").lower() in names:
            return p.info["pid"]
    return None


def find_window_for_pid(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


class Game:
    """The game window: background screenshots and input."""

    def __init__(self, cfg, hwnd):
        self.cfg = cfg
        self.hwnd = hwnd
        self._frame = None
        self._lock = threading.Lock()
        self._control = None
        self._warned_minimized = False

    # -- screenshots

    def _start_capture(self):
        cap = WindowsCapture(cursor_capture=False, draw_border=False,
                             minimum_update_interval=50, window_hwnd=self.hwnd)

        @cap.event
        def on_frame_arrived(frame, _control):
            img = frame.frame_buffer[:, :, :3].copy()
            with self._lock:
                self._frame = img

        @cap.event
        def on_closed():
            pass

        self._control = cap.start_free_threaded()

    def stop(self):
        if self._control:
            try:
                self._control.stop()
            except Exception:
                pass
            self._control = None

    def frame(self):
        """Latest picture of the game window (BGR)."""
        if self._control is None:
            self._start_capture()
        while user32.IsIconic(self.hwnd):
            if not self._warned_minimized:
                log("The game is minimized - restore it (it can stay behind other windows).")
                self._warned_minimized = True
            time.sleep(0.5)
        self._warned_minimized = False
        end = time.time() + 5
        while True:
            with self._lock:
                f = self._frame
            if f is not None:
                return f
            if time.time() > end:
                raise RuntimeError("Got no picture from the game window.")
            time.sleep(0.05)

    def _client_offset(self):
        """Offset of the window's client area inside the captured frame."""
        r = wintypes.RECT()
        dwmapi.DwmGetWindowAttribute(self.hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))  # extended frame bounds
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return pt.x - r.left, pt.y - r.top

    # -- input

    def _post(self, msg, wparam, lparam):
        kernel32.SetLastError(0)
        if not user32.PostMessageW(self.hwnd, msg, wparam, lparam):
            if kernel32.GetLastError() == 5:
                raise InputBlocked()

    def _fake_activate(self):
        # Make the game believe it is the active window so it doesn't drop the input.
        self._post(WM_ACTIVATEAPP, 1, 0)
        self._post(WM_NCACTIVATE, 1, 0)
        self._post(WM_ACTIVATE, 1, 0)
        self._post(WM_SETFOCUS, 0, 0)
        time.sleep(0.05)

    def _focus(self):
        if user32.GetForegroundWindow() != self.hwnd:
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.5)

    def click(self, pos):
        ox, oy = self._client_offset()
        x, y = pos[0] - ox, pos[1] - oy
        if self.cfg["input_mode"] == "foreground":
            self._focus()
            pt = wintypes.POINT(x, y)
            user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
            pydirectinput.moveTo(pt.x, pt.y)
            time.sleep(0.1)
            pydirectinput.click(pt.x, pt.y)
            return
        # The game reads hover from the real cursor, so park the cursor on the target for a
        # split second (no focus change), send the click to the window, then put it back.
        lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        old = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(old))
        pt = wintypes.POINT(x, y)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        user32.SetCursorPos(pt.x, pt.y)
        try:
            time.sleep(0.1)
            self._fake_activate()
            self._post(WM_MOUSEMOVE, 0, lp)
            time.sleep(0.15)
            self._post(WM_LBUTTONDOWN, 0x0001, lp)
            time.sleep(0.08)
            self._post(WM_LBUTTONUP, 0, lp)
            time.sleep(0.05)
        finally:
            user32.SetCursorPos(old.x, old.y)

    def press(self, key):
        if self.cfg["input_mode"] == "foreground":
            self._focus()
            pydirectinput.press(key)
            return
        key = key.lower()
        vk = NAMED_KEYS.get(key) or (user32.VkKeyScanW(ord(key)) & 0xFF)
        scan = user32.MapVirtualKeyW(vk, 0)
        self._fake_activate()
        self._post(WM_KEYDOWN, vk, 1 | (scan << 16))
        time.sleep(0.08)
        self._post(WM_KEYUP, vk, 1 | (scan << 16) | (0xC0 << 24))

def open_game(cfg):
    pid = find_game_pid(cfg)
    if not pid:
        log("Marvel Rivals is not open. Start the game and try again.")
        return None
    hwnd = find_window_for_pid(pid)
    if not hwnd:
        log("Marvel Rivals is running but its window wasn't found yet. Try again in a moment.")
        return None
    return Game(cfg, hwnd)


game = None  # set by the commands that need the game


# ---------------------------------------------------------------- vision

_templates = {}


def template(name):
    if name not in _templates:
        path = TEMPLATE_DIR / f"{name}.png"
        _templates[name] = cv2.imread(str(path)) if path.exists() else None
    return _templates[name]


def has_template(name):
    return template(name) is not None


def match_score(name, screen):
    """Return (score, centre) of the best match of the template in the screenshot."""
    tpl = template(name)
    if tpl is None or tpl.shape[0] > screen.shape[0] or tpl.shape[1] > screen.shape[1]:
        return 0.0, None
    _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED))
    h, w = tpl.shape[:2]
    return score, (loc[0] + w // 2, loc[1] + h // 2)


def locate(name, cfg, screen=None):
    """Return (x, y) centre of the template in the game frame if visible, else None."""
    score, pos = match_score(name, game.frame() if screen is None else screen)
    return pos if score >= cfg["match_threshold"] else None


# ---------------------------------------------------------------- control helpers

def check_stop(cfg):
    if user32.GetAsyncKeyState(cfg["stop_key_vk"]) & 0x8000:
        raise StopBot()


def sleep(seconds, cfg):
    end = time.time() + seconds
    while time.time() < end:
        check_stop(cfg)
        time.sleep(min(0.1, max(0, end - time.time())))


def click(pos, cfg):
    game.click(pos)
    sleep(0.4, cfg)


def press(key, cfg):
    game.press(key)
    sleep(0.05, cfg)


def wait_for(names, cfg, timeout, label=None):
    """Wait until any of `names` is visible. Returns (name, pos) or (None, None) on timeout."""
    names = [n for n in ([names] if isinstance(names, str) else names) if has_template(n)]
    if label:
        log(f"Waiting for {label}...")
    end = time.time() + timeout
    while time.time() < end:
        check_stop(cfg)
        screen = game.frame()
        for n in names:
            pos = locate(n, cfg, screen)
            if pos:
                return n, pos
        time.sleep(cfg["poll_interval"])
    return None, None


def click_until_gone(name, cfg, timeout, tries=4, settle=4):
    """Click a button and make sure it took effect (the button goes away); click again if not."""
    for attempt in range(tries):
        _, pos = wait_for(name, cfg, timeout if attempt == 0 else 2)
        if not pos:
            return attempt > 0  # gone after an earlier click
        if attempt:
            log("The click didn't register - clicking again.")
        click(pos, cfg)
        end = time.time() + settle
        while time.time() < end:
            if not locate(name, cfg):
                return True
            sleep(cfg["poll_interval"], cfg)
    return False


def click_when_visible(name, cfg, timeout, label=None):
    _, pos = wait_for(name, cfg, timeout, label)
    if not pos:
        return False
    click(pos, cfg)
    return True


# ---------------------------------------------------------------- bot steps

def ensure_play_menu(cfg):
    if locate("play_menu", cfg):
        log("On the play menu.")
        return True
    log("Not on the play menu - clicking PLAY.")
    if not click_when_visible("play_tab", cfg, 15):
        log("Couldn't find the PLAY button.")
        return False
    if wait_for("play_menu", cfg, 15)[1]:
        log("On the play menu.")
        return True
    log("Play menu didn't appear.")
    return False


def ensure_blood_hunt(cfg):
    if locate("bloodhunt_selected", cfg):
        log("Blood Hunt is already selected.")
        return True
    log("Blood Hunt not selected - selecting it.")
    if has_template("mode_button") and not locate("bloodhunt_option", cfg):
        click_when_visible("mode_button", cfg, 10)
    if not click_when_visible("bloodhunt_option", cfg, 15):
        log("Couldn't find the Blood Hunt option.")
        return False
    if wait_for("bloodhunt_selected", cfg, 10)[1]:
        log("Blood Hunt selected.")
        return True
    log("Blood Hunt selection didn't stick.")
    return False


def pick_thor(cfg):
    if not wait_for("hero_select", cfg, cfg["queue_timeout"], "a match (character select)")[1]:
        log("Timed out waiting for character select.")
        return False
    log("Character select - picking Thor.")
    if not click_when_visible("thor", cfg, cfg["hero_select_timeout"]):
        log("Couldn't find Thor.")
        return False
    if click_until_gone("confirm", cfg, 15):
        log("Thor confirmed.")
    else:
        log("CONFIRM didn't clearly register - carrying on (the game auto-locks the pick).")
    return True


def play_match(cfg):
    if not wait_for("in_match", cfg, cfg["hero_select_timeout"] + 120, "the match to load")[1]:
        log("Timed out waiting for the match to load.")
        return False
    # Between waves the game shows a 15 s "VAMPIRES INCOMING" countdown (white, then yellow
    # for the last 5 s). It appears the moment a wave ends and disappears the moment the
    # next one starts. Thor's skill is a toggle with a limited duration and a ~10 s
    # cooldown, so: switch it OFF when a wave ends (it recharges during the break) and
    # ON the instant the next wave starts. The bot reads the skill icon (yellow = on) and
    # only presses when the skill is in the wrong state, so a press never undoes itself.
    # The countdown is drawn over the map, so its match score varies with the background:
    # it counts as "showing" above wave_on and only as "gone" after staying below wave_off.
    key = cfg["skill_key"]
    wave = 0
    countdown = False
    turn_off = False
    gone_since = None
    last_press = last_end_check = float("-inf")
    end_screens = ["continue", "post_match"]
    deadline = time.time() + cfg["match_timeout"]
    log("Waiting for the first wave countdown...")
    while time.time() < deadline:
        check_stop(cfg)
        screen = game.frame()
        now = time.time()
        if now - last_end_check >= 1:
            last_end_check = now
            if any(locate(n, cfg, screen) for n in end_screens):
                log("Match over.")
                return True

        score = countdown_score(screen)
        if score >= cfg["wave_on"]:
            if not countdown and wave:
                log(f"Wave {wave} ended.")
                turn_off = True
            countdown, gone_since = True, None
        elif countdown and score < cfg["wave_off"]:
            gone_since = gone_since or now
            if now - gone_since >= cfg["wave_gone_time"]:
                countdown, turn_off = False, False
                wave += 1
                log(f"Wave {wave} started.")

        if now - last_press >= cfg["skill_verify_time"]:
            active = skill_active(screen, cfg)
            if countdown and turn_off:
                if active:
                    press(key, cfg)
                    last_press = now
                    log(f"Switched the skill off ({key.upper()}) so it recharges.")
                else:
                    turn_off = False
            elif wave and not countdown and not active and locate("in_match", cfg, screen):
                press(key, cfg)
                last_press = now
                log(f"Switched the skill on ({key.upper()}).")
        time.sleep(0.03)
    log("Match timed out.")
    return False


def countdown_score(screen):
    """Match score of the "VAMPIRES INCOMING" countdown (white or yellow), top of the screen only."""
    top = screen[: screen.shape[0] // 8]
    return max(match_score("wave_end", top)[0], match_score("wave_start", top)[0])


def skill_active(screen, cfg):
    """True while the skill icon is lit up yellow (skill switched on)."""
    h, w = screen.shape[:2]
    x1, y1, x2, y2 = cfg["skill_icon_region"]
    icon = cv2.cvtColor(screen[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)], cv2.COLOR_BGR2HSV)
    yellow = (icon[..., 0] >= 18) & (icon[..., 0] <= 35) & (icon[..., 1] > 120) & (icon[..., 2] > 150)
    return yellow.mean() > 0.08


def clear_end_screens(cfg):
    """SPACE through the result screens, then ESC from the post-match lobby to the play menu."""
    log("Clearing end-of-match screens.")
    end = time.time() + 120
    while time.time() < end:
        screen = game.frame()
        if locate("play_menu", cfg, screen):
            log("Back on the play menu.")
            return
        if locate("continue", cfg, screen):
            press("space", cfg)
        elif locate("post_match", cfg, screen):
            press("esc", cfg)
        sleep(1.5, cfg)
    log("Didn't get back to the play menu.")


# ---------------------------------------------------------------- relic cleanup

def click_until(pos, cfg, check, what, tries=4, settle=3.0):
    """Click and wait until check(screen) is true; click again if the click didn't register."""
    for attempt in range(tries):
        if attempt:
            log(f"{what}: the click didn't register - clicking again.")
        click(pos, cfg)
        end = time.time() + settle
        while time.time() < end:
            if check(game.frame()):
                return True
            sleep(0.2, cfg)
    log(f"{what}: gave up after {tries} clicks.")
    return False


def visible(name, threshold=0.82):
    """check() helper: True when the template is on screen."""
    return lambda screen: match_score(name, screen)[0] >= threshold


def leave_to_play_menu(cfg):
    for _ in range(4):
        press("esc", cfg)
        if wait_for("play_menu", cfg, 4)[1]:
            return True
    log("Couldn't get back to the play menu.")
    return False


def open_scrap(cfg):
    """Play menu -> PREPARE -> FORGE -> SCRAP. Returns the SELECT ALL position, or None."""
    _, pos = wait_for("prepare_button", cfg, 10)
    if not pos or not click_until(pos, cfg, visible("forge_tab"), "PREPARE", settle=4):
        return None
    _, pos = wait_for("forge_tab", cfg, 5)
    # FORGE reopens whichever sub-tab was used last, so make sure SCRAP is the one showing.
    if not pos or not click_until(pos, cfg, lambda sc: visible("scrap_tab")(sc) or visible("scrap_tab_selected")(sc)
                                  or visible("select_all")(sc), "FORGE tab", settle=4):
        return None
    _, sel = wait_for("select_all", cfg, 2)
    if not sel:
        _, tab = wait_for(["scrap_tab", "scrap_tab_selected"], cfg, 3)
        if not tab or not click_until(tab, cfg, visible("select_all"), "SCRAP tab", settle=4):
            return None
        _, sel = wait_for("select_all", cfg, 3)
    return sel


def cleanup_relics(cfg):
    """Forge > Scrap: SELECT ALL, SMELT, confirm. Locked relics are never selected by the game.
    Every click is checked (the game sometimes drops one) and retried."""
    missing = [n for n in CLEANUP_IMAGES if not has_template(n)]
    if missing:
        log("Can't clean up relics - missing images: " + ", ".join(missing))
        return False
    log("Relic cleanup: opening Forge > Scrap...")
    if not locate("play_menu", cfg) and not ensure_play_menu(cfg):
        return False
    sel_pos = open_scrap(cfg)
    if not sel_pos:
        log("Couldn't open Forge > Scrap.")
        leave_to_play_menu(cfg)
        return False
    sleep(1, cfg)

    # Once relics are selected the button reads DESELECT ALL. (Don't look for ticks on the
    # first relics: locked/equipped relics come first and never get selected.)
    selected = visible("deselect_all", 0.92)
    if not selected(game.frame()) and not click_until(sel_pos, cfg, selected, "SELECT ALL", tries=2):
        log("Nothing to smelt (no unlocked relics).")
        return leave_to_play_menu(cfg)

    log("Smelting all relics...")
    _, smelt_pos = wait_for("smelt_button", cfg, 5)
    if not smelt_pos or not click_until(smelt_pos, cfg, lambda sc: visible("smelt_dialog")(sc)
                                        or visible("acquired")(sc), "SMELT", settle=4):
        leave_to_play_menu(cfg)
        return False

    if locate("smelt_dialog", cfg):
        # The "don't show again today" box sits at a fixed spot below the dialog title.
        tx, ty = locate("smelt_dialog", cfg)
        box = (tx - 189, ty + 639)

        def box_ticked(sc):
            area = sc[box[1] - 30:box[1] + 30, box[0] - 30:box[0] + 30]
            return match_score("dont_show_ticked", area)[0] > match_score("dont_show_unticked", area)[0]

        if not box_ticked(game.frame()):
            click_until(box, cfg, box_ticked, "Don't show again today", tries=3, settle=2)
        _, confirm = wait_for("confirm_smelt", cfg, 5)
        if not confirm or not click_until(confirm, cfg, visible("acquired"), "CONFIRM", settle=6):
            leave_to_play_menu(cfg)
            return False

    log("Smelted.")
    for _ in range(5):
        press("space", cfg)
        sleep(1.2, cfg)
        if not locate("acquired", cfg):
            break
    return leave_to_play_menu(cfg)


# ---------------------------------------------------------------- commands

ADMIN_HELP = ("Windows blocked input to the game because it runs with higher privileges.\n"
              "Run this terminal as Administrator (right-click -> Run as administrator) and try again.")


def cmd_run(args):
    global game
    cfg = load_config()
    missing = [n for n in CAPTURE_STEPS if n not in OPTIONAL and not has_template(n)]
    if missing:
        log("Missing images: " + ", ".join(missing))
        log("Capture them with:  python rivals_bot.py capture")
        return 1

    if args.cleanup_every is not None:
        cfg["cleanup_every"] = args.cleanup_every
    game = open_game(cfg)
    if not game:
        return 1
    log(f"Marvel Rivals is running ({cfg['input_mode']} mode). Hold F8 to stop.")
    if not is_admin():
        log("Warning: not running as Administrator - the game will probably ignore input.")

    games = 0
    try:
        while args.games == 0 or games < args.games:
            if not find_game_pid(cfg):
                log("Marvel Rivals closed - stopping.")
                return 1
            if not (ensure_play_menu(cfg) and ensure_blood_hunt(cfg)):
                log("Retrying in 5s...")
                sleep(5, cfg)
                continue
            log("Clicking START.")
            if not click_until_gone("start_button", cfg, 15):
                log("Couldn't find START - retrying.")
                continue
            if pick_thor(cfg) and play_match(cfg):
                games += 1
                log(f"Matches finished: {games}")
            clear_end_screens(cfg)
            wait_for("play_menu", cfg, 30, "the play menu")
            if cfg["cleanup_every"] and games and games % cfg["cleanup_every"] == 0:
                cleanup_relics(cfg)
    except StopBot:
        log("F8 pressed - stopped.")
    except InputBlocked:
        log(ADMIN_HELP)
        return 1
    except pydirectinput.FailSafeException:
        log("Mouse moved to a screen corner - stopped (failsafe).")
    finally:
        game.stop()
    return 0


def cmd_cleanup(args):
    """Run the relic cleanup once (start on the play menu)."""
    global game
    cfg = load_config()
    game = open_game(cfg)
    if not game:
        return 1
    try:
        return 0 if cleanup_relics(cfg) else 1
    except StopBot:
        log("F8 pressed - stopped.")
        return 1
    except InputBlocked:
        log(ADMIN_HELP)
        return 1
    finally:
        game.stop()


def cmd_test_input(_args):
    """Press ESC on the play menu and check the game reacts, then go back with PLAY."""
    global game
    cfg = load_config()
    game = open_game(cfg)
    if not game:
        return 1
    try:
        if not locate("play_menu", cfg):
            log("Open the PLAY menu in the game first (it can stay in the background), then rerun.")
            return 1
        log(f"Game is on the play menu. Sending ESC ({cfg['input_mode']} mode)...")
        press("esc", cfg)
        end = time.time() + 5
        while time.time() < end and locate("play_menu", cfg):
            time.sleep(0.2)
        if locate("play_menu", cfg):
            log("FAILED: the game ignored the key press.")
            if not is_admin():
                log("You're not running as Administrator - try again from an Administrator terminal.")
            elif cfg["input_mode"] == "background":
                log('The game doesn\'t accept background input. Set "input_mode": "foreground" in config.json.')
            return 1
        log("SUCCESS: the game reacted to the key press (it left the play menu).")
        if has_template("play_tab"):
            log("Going back with the PLAY tab...")
            if click_when_visible("play_tab", cfg, 5) and wait_for("play_menu", cfg, 5)[1]:
                log("SUCCESS: background clicks work too - you're back on the play menu.")
            else:
                log("The click on PLAY didn't bring the play menu back.")
        else:
            log("Capture 'play_tab' now (you're on the home screen):  python rivals_bot.py capture play_tab")
        return 0
    except InputBlocked:
        log(ADMIN_HELP)
        return 1
    finally:
        game.stop()


def with_banner(screen, lines):
    """Return the screenshot with an instruction strip added above it, and the strip height."""
    scale = max(0.8, screen.shape[1] / 1600)
    line_h = int(40 * scale)
    strip = np.zeros((line_h * len(lines) + line_h // 2, screen.shape[1], 3), np.uint8)
    for i, text in enumerate(lines):
        cv2.putText(strip, text, (int(15 * scale), line_h * (i + 1)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9 * scale, (0, 255, 255) if i == 0 else (255, 255, 255), max(1, int(2 * scale)), cv2.LINE_AA)
    return np.vstack([strip, screen]), strip.shape[0]


def capture_one(name, delay):
    screen_desc, target = CAPTURE_STEPS[name]
    print()
    print("=" * 70)
    print(f" CAPTURE: {name}")
    print(f"   1. In the game, open:   {screen_desc}")
    print(f"   2. Then draw a box on:  {target}")
    print("=" * 70)
    for i in range(delay, 0, -1):
        print(f"  Taking a picture of the game window in {i}...", flush=True)
        time.sleep(1)
    screen = game.frame().copy()
    print(f"Picture taken. In the window that opened: drag a box around -> {target}")
    print("Press ENTER/SPACE to save, C to skip.")

    win = f"Box: {target}  |  ENTER = save, C = skip"
    shown, offset = with_banner(screen, [f"DRAW A BOX AROUND: {target}",
                                         "Keep it tight. ENTER/SPACE = save    C = skip"])
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_TOPMOST, 1)
    x, y, w, h = cv2.selectROI(win, shown, showCrosshair=True)
    cv2.destroyAllWindows()
    # Translate from the bannered image back to the screenshot, clipping off the banner.
    y2 = max(0, y + h - offset)
    y = max(0, y - offset)
    h = y2 - y
    if w == 0 or h == 0:
        print("Skipped.")
        return False
    TEMPLATE_DIR.mkdir(exist_ok=True)
    out = TEMPLATE_DIR / f"{name}.png"
    cv2.imwrite(str(out), screen[y:y + h, x:x + w])  # crop from the clean screenshot
    _templates.pop(name, None)
    print(f"Saved {out}")
    return True


def cmd_capture(args):
    global game
    if args.name and args.name not in CAPTURE_STEPS:
        print(f"Unknown name '{args.name}'. Valid names:\n  " + "\n  ".join(CAPTURE_STEPS))
        return 1
    game = open_game(load_config())
    if not game:
        return 1
    try:
        if args.name:
            return 0 if capture_one(args.name, args.delay) else 1

        # Guided: walk through every image not captured yet.
        todo = [n for n in CAPTURE_STEPS if args.all or not has_template(n)]
        if not todo:
            print("All images are captured. Use --all to redo them, or 'capture <name>' for one.")
            return 0
        print(f"{len(todo)} image(s) to capture. For each one, get the right screen up in the game")
        print("(it can stay in the background), then press ENTER here. 's' skips, 'q' quits.")
        for name in todo:
            screen_desc, target = CAPTURE_STEPS[name]
            opt = "  (optional)" if name in OPTIONAL else ""
            print(f"\nNext: {name}{opt}")
            print(f"   Open in game:  {screen_desc}")
            print(f"   You'll box:    {target}")
            try:
                answer = input("Ready? [ENTER = go, s = skip, q = quit] ").strip("﻿ \t").lower()
            except EOFError:
                break
            if answer == "q":
                break
            if answer == "s":
                continue
            capture_one(name, args.delay)
        cmd_list(args)
        return 0
    finally:
        game.stop()


def cmd_check(args):
    global game
    cfg = load_config()
    game = open_game(cfg)
    if not game:
        return 1
    time.sleep(args.delay)
    screen = game.frame()
    game.stop()
    for name in CAPTURE_STEPS:
        if not has_template(name):
            print(f"  {name:20s} (not captured)")
            continue
        score, _ = match_score(name, screen)
        mark = "VISIBLE" if score >= cfg["match_threshold"] else "-"
        print(f"  {name:20s} {score:.2f}  {mark}")
    return 0


def cmd_list(_args):
    for name, (screen_desc, target) in CAPTURE_STEPS.items():
        status = "ok" if has_template(name) else ("optional" if name in OPTIONAL else "MISSING")
        print(f"  [{status:8s}] {name}")
        print(f"               open in game: {screen_desc}")
        print(f"               box around:   {target}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Marvel Rivals Blood Hunt auto-farmer")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="start the bot")
    r.add_argument("--games", type=int, default=0, help="stop after N matches (0 = forever)")
    r.add_argument("--cleanup-every", type=int, help="smelt relics every N matches (overrides config.json)")
    c = sub.add_parser("capture", help="capture images (guided if no name is given)")
    c.add_argument("name", nargs="?", help="capture just this one image")
    c.add_argument("--all", action="store_true", help="guided mode: redo every image")
    c.add_argument("--delay", type=int, default=0, help="seconds to wait before taking the picture")
    k = sub.add_parser("check", help="show which images are visible in the game right now")
    k.add_argument("--delay", type=int, default=0)
    sub.add_parser("list", help="list images")
    sub.add_parser("test-input", help="check the game accepts background clicks/keys")
    sub.add_parser("cleanup", help="smelt all unlocked relics now (start on the play menu)")
    double_clicked = len(sys.argv) == 1
    args = ap.parse_args(sys.argv[1:] or ["run"])  # no arguments = start the bot
    commands = {"run": cmd_run, "capture": cmd_capture, "check": cmd_check,
                "list": cmd_list, "test-input": cmd_test_input, "cleanup": cmd_cleanup}
    try:
        return commands[args.cmd](args)
    except KeyboardInterrupt:
        log("Stopped.")
        return 0
    finally:
        if double_clicked and getattr(sys, "frozen", False):
            # Started by double-clicking the exe: keep the window open to read the log.
            try:
                input("\nPress Enter to close...")
            except EOFError:
                pass


if __name__ == "__main__":
    sys.exit(main())
