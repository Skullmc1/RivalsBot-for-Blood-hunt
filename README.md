# RivalsBot for Blood Hunt

A bot that farms **Blood Hunt** in Marvel Rivals as **Thor**, in the background – you can keep using your PC while it plays.

It loops: PLAY menu → Blood Hunt → START → picks Thor → plays the match → clears the reward screens → repeats.
During a match it switches Thor's **F** skill **on** the instant each wave starts and **off** the moment it ends, so the skill recharges between waves.

## Relic cleanup

Every 20 matches (`cleanup_every` in `config.json`, 0 = off) the bot goes to **Forge → Scrap** and smelts all relics, so you never hit the 2000 limit:

1. SELECT ALL (locked relics are never selected).
2. Checks the best relics first (the list is sorted best → worst): any **legendary** with **4+ [S] stats** (`keep_min_s`) is unticked and kept. It stops at the first relic that isn't a keeper.
3. SMELT → ticks "don't show again today" → CONFIRM → back to the play menu.

Run it on demand with `RivalsBot.exe cleanup`, or `RivalsBot.exe cleanup --dry-run` to only see what it would keep.

## Quick start

1. Download `RivalsBot.zip` from the [latest release](../../releases/latest) and unzip it.
2. Start Marvel Rivals (windowed or borderless, **2560×1600** – see below).
3. Double-click **`RivalsBot.exe`** and click **Yes** on the Administrator prompt.
4. Hold **F8** to stop.

Keep the `templates` folder and `config.json` next to the exe.

## Requirements

- Windows 10/11.
- **Administrator** – the game runs elevated, so the bot needs to as well (the exe asks automatically).
- **Don't minimize the game.** Behind other windows is fine.
- The screen images in `templates/` were captured at **2560×1600**. At another resolution, recapture them:
  `RivalsBot.exe capture --all` (it tells you what to box on each screen), and adjust `skill_icon_region` in `config.json`.

## Commands

| Command | What it does |
|---|---|
| `RivalsBot.exe` | Start the bot (same as `run`) |
| `RivalsBot.exe run --games 5` | Stop after 5 matches |
| `RivalsBot.exe check` | Show which screens are detected right now |
| `RivalsBot.exe capture [name]` | Recapture screen images (guided) |
| `RivalsBot.exe cleanup [--dry-run]` | Smelt relics now, keeping rare legendaries (start on the play menu) |
| `RivalsBot.exe test-input` | Check the game accepts background input (open the PLAY menu first) |

## Run from source

```bash
pip install -r requirements.txt
python rivals_bot.py run        # from an Administrator terminal
```

Build the exe yourself with `build_exe.bat`.

## Disclaimer

Automating gameplay may violate Marvel Rivals' terms of service. Use at your own risk.
