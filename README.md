# mk96-app-pc-rocket-tool

**MK96 PC Rocket Tool**: a Windows tray app that connects to your
[Rocket.Chat](https://rocket.chat) server and plays a **different sound for each chat**, so you
know by ear who is writing without looking at the screen.

It only sounds and notifies for the chats you mark as "special" and for mentions. Everything
else in Rocket.Chat is ignored: it is not a general notifier.

It also includes a meetings calendar with reminders, a Do Not Disturb mode and a few tray tools
(presence, mark as read). It is the Windows version of
[mk96-app-debian-rocket-tool](https://github.com/mk96-app/mk96-app-debian-rocket-tool): same
features, built natively for Windows.

## Features

### Sounds per chat and per mention

- **Room alerts:** a sound for one specific channel, private group or direct message.
- **Mention alert:** a sound for when you are mentioned in any room. Optionally also for `@all`
  and `@here` (off by default).
- When both a room alert and the mention alert match, the room alert wins.
- **No sound if you already read it:** messages are compared with your last read time in
  Rocket.Chat, so reading them in any Rocket.Chat client (web, desktop, mobile) silences them.
- **Repeat**, per alert: once, N times, or until the message is read.
- **Several Rocket.Chat servers** at once, each with its own check interval (15, 30 or 60 s).

### Sounds

- A palette of **501 sounds** ([Kenney](https://kenney.nl) packs, CC0 / public domain) in six
  categories: digital, impacts, interface, music jingles, RPG and sci-fi.
- **Your own sounds** (`.ogg`, `.wav`, `.mp3`, `.flac`) can be added from any alert or meeting.
  They are copied to `%APPDATA%\mk96-app-pc-rocket-tool\sounds\`, which updates and uninstalls
  never touch.

### Do Not Disturb

- **Manual silence** from the tray: 1 hour, 4 hours or until you turn it off.
- **Recurring quiet hours**, including ranges that cross midnight.
- While silenced, chats make no sound and no notification but still show up in the tray menu,
  and the tray icon shows a small red badge. Meeting reminders are never silenced.

### Meetings

A local calendar (works without any Rocket.Chat server):

- **Day, Week and Month** views (Week by default), with a red line for the current time.
- Each meeting has title, date, time, duration, repetition (daily, weekly on chosen days,
  monthly), an optional end date, advance reminder, sound, link and notes.
- When editing or deleting one occurrence of a recurring meeting, the app asks whether it is
  only that one or the whole series. Inactive occurrences stay visible, dimmed.
- Drag on the grid to select a time range and right-click it to create a meeting there.
  Right-click a meeting to edit, deactivate or delete it; double-click to edit. Overlapping
  meetings are drawn side by side.
- The reminder comes with a sound, a Windows notification and an entry in the tray menu (with
  "Open link" when the meeting has one). A reminder whose advance window opened while the app
  was closed is still given when it starts, if the meeting has not begun yet.

### Tray icon

| Action | Result |
|---|---|
| Left click | Opens Settings |
| Right click | Menu |
| Hover | App name, connection status and Do Not Disturb status |
| Click on a chat notification | Opens that chat's history |

| Menu entry | What it does |
|---|---|
| Open settings | Opens the Settings window |
| Connection status | One line per server: connected user or error |
| Meeting reminders | Fired reminders, with "Open link" and "Dismiss" |
| Recent special notifications | Click one to open the history of that room |
| Alerts | Every alert with a checkbox to turn it on or off |
| Meetings | The next three meetings and a shortcut to the calendar |
| Tools | "Mark as read..." and "Own sounds folder" |
| Status | Change your presence (available, away, busy, invisible) |
| Do Not Disturb | Silence for 1 hour, 4 hours or indefinitely, and "Turn off" |
| Quit | Closes the app |

The app's interface is in Spanish, like the Linux version.

### Chat window

Read-only recent history of a room (new messages from others highlighted) and a one-line reply,
without opening Rocket.Chat.

### Settings window

Changes apply as soon as you make them; "Cerrar" or Esc closes the window, which remembers its
size and position.

- **Servers:** name, URL, user id, personal token and check interval. "Probar conexión" checks
  what you typed without saving it. Connect, disconnect, delete, and **start with Windows**.
- **Alerts:** room and mention alerts of the selected server, and the quiet hours.
- **Meetings:** the calendar.

## Credential security

- The server URL, user id and token are stored in **Windows Credential Manager** (one generic
  credential per server, named `<server id>@mk96-app-pc-rocket-tool`). Never in a settings file.
- If Credential Manager is not available, the app fails explicitly instead of storing anything
  in plain text.
- The app warns if a server uses `http://`, because the token would travel unencrypted.
- Use a Rocket.Chat Personal Access Token, not your password.

## Requirements

- Windows 10 or 11, 64-bit.
- A Rocket.Chat server and a Personal Access Token (Rocket.Chat: My Account > Personal Access
  Tokens, which also shows your user id).

The installer bundles everything else (Python, Qt and the FFmpeg audio decoders).

## Installation

1. Download `mk96-app-pc-rocket-tool-setup-<version>.exe` from
   [Releases](../../releases/latest).
2. Run it. It installs for your user only and **does not need administrator rights**
   (`%LOCALAPPDATA%\Programs\MK96 PC Rocket Tool`).
3. The installer asks whether the app should start with Windows (on by default) and whether to
   add a desktop shortcut. You can change "start with Windows" later in Settings > Servers.

To update, run the new installer: if the app is running, it asks to close it first.
To uninstall, use Windows Settings > Apps.

Uninstalling keeps your settings, your own sounds and your stored credentials, so a reinstall
picks them up again. To remove them too, delete `%APPDATA%\mk96-app-pc-rocket-tool` and the
`...@mk96-app-pc-rocket-tool` entries in Credential Manager (Windows credentials).

## Getting started

1. Open the app from the Start menu ("MK96 PC Rocket Tool"). The first time, Settings opens by
   itself.
2. In **Servidores**, add your URL, user id and personal token. Click "Probar conexión", then
   "Guardar" and "Conectar".
3. In **Alertas**, pick the server and create an alert: the room (or the mention) and the sound.

Launching the app again while it is running just brings up Settings.

## Where data is stored

| What | Where |
|---|---|
| Alerts, meetings, Do Not Disturb, room-name cache | `%APPDATA%\mk96-app-pc-rocket-tool\config.yaml` |
| Credentials | Windows Credential Manager |
| Own sounds | `%APPDATA%\mk96-app-pc-rocket-tool\sounds\` |
| Log (rotating, 3 x 1 MB) | `%LOCALAPPDATA%\mk96-app-pc-rocket-tool\logs\app.log` |
| Settings window size and position | `HKCU\Software\mk96\mk96-app-pc-rocket-tool` |
| Start with Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `MK96 PC Rocket Tool` |

## Development

Needs [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`). From the repository root:

```
uv sync                                  # create .venv with the locked dependencies
uv run python -m mk96_app_pc_rocket_tool # run the app
uv run pytest                            # tests
uv run ruff check                        # lint
uv run ruff format                       # format
uv run mypy                              # strict type check
```

The tests cover the logic of every module (rules, polling, meetings, quiet hours, config, REST
client), exercise the real Windows services the app wraps (Credential Manager, the Run registry
key, audio playback, the single-instance channel) with throwaway entries they clean up, and
build and drive every window and dialog.

### Build the installer

Needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`):

```
./build.ps1
```

The script runs lint, type check and tests, builds the app with PyInstaller (`build/`) and the
installer with Inno Setup, and leaves it in `versions/<version>/`. Neither folder is committed.

### Versioning

Versions follow **`YY-M-R`**: two-digit year, month and release number within that month. For
example, `26-10-1` is the first release of October 2026.

The Windows and Linux versions share version numbers: an update is released in both at once,
with the same number, so a given version means the same features on both systems.

To release a new version:

1. Update `version` in `pyproject.toml` (with dots: `26.10.2`) and `__version__` in
   `src/mk96_app_pc_rocket_tool/__init__.py`.
2. Run `./build.ps1`.
3. Create a GitHub release tagged `v26-10-2` and attach the generated installer.

## How it works

The app polls the Rocket.Chat REST API (one poll per connected server, on a worker thread) and
decides which new messages deserve a sound according to your alerts: `subscriptions.get` in
delta mode finds rooms with activity, and the room history brings the new messages of the rooms
an alert cares about. REST was chosen over the realtime WebSocket API because the login it needs
is deprecated by Rocket.Chat; the full rationale is in the Linux version's design notes.

Built with Python 3.12 and Qt 6 (PySide6): `QSystemTrayIcon` for the tray and Windows
notifications, QtMultimedia (FFmpeg) for sound, `keyring` for Credential Manager, PyInstaller and
Inno Setup for packaging.

## Repository layout

```
.
├── build.ps1                          Builds the installer into versions/<version>/
├── pyproject.toml                     Metadata, dependencies and tool settings (ruff, mypy, pytest)
├── uv.lock                            Locked dependency versions
├── README.md
├── LICENSE
├── packaging/
│   ├── launcher.py                    PyInstaller entry script
│   ├── make_icon.py                   Renders the app icon into a multi-size .ico
│   ├── mk96-app-pc-rocket-tool.spec   PyInstaller build (windowed, one folder)
│   └── installer.iss                  Inno Setup per-user installer
├── src/mk96_app_pc_rocket_tool/
│   ├── __init__.py                    App id, name and version
│   ├── __main__.py                    python -m entry point
│   ├── app.py                         Startup and coordination (controller)
│   ├── models.py                      Data types: servers, alerts, meetings, settings
│   ├── config.py                      config.yaml read and write
│   ├── paths.py                       Where files live on Windows
│   ├── rest_client.py                 Rocket.Chat REST calls
│   ├── poller.py                      Periodic polling of Rocket.Chat
│   ├── rules.py                       Which alert matches each message
│   ├── quiet_hours.py                 Do Not Disturb and quiet hours
│   ├── meetings.py                    Meetings: recurrence, reminders, calendar geometry
│   ├── meeting_scheduler.py           Timer that fires meeting reminders
│   ├── notifier.py                    Notification texts
│   ├── tray.py                        Tray icon and menu
│   ├── sound_library.py               Bundled and own sounds
│   ├── sound_player.py                Sound playback (QtMultimedia)
│   ├── secret_store.py                Credentials in Windows Credential Manager
│   ├── autostart.py                   Start with Windows (HKCU Run)
│   ├── single_instance.py             One running copy; a second launch wakes it
│   ├── workers.py                     Background work without blocking the UI
│   ├── ui/
│   │   ├── settings_window.py         Settings window (tabs)
│   │   ├── servers_panel.py           Servers tab
│   │   ├── alerts_panel.py            Alerts tab and quiet hours
│   │   ├── alert_dialog.py            Create or edit an alert, room picker
│   │   ├── meetings_panel.py          Meetings tab
│   │   ├── calendar_views.py          Day, Week and Month views
│   │   ├── meeting_dialog.py          Create or edit a meeting
│   │   ├── chat_window.py             Room history and quick reply
│   │   └── common.py                  Message boxes, links, sound picker
│   └── resources/
│       ├── app-icon.svg               Color icon (exe, shortcuts, windows)
│       ├── tray-icon.svg              Monochrome tray symbol
│       └── sounds/default/            501 CC0 sounds in six categories and LICENSES.md
└── tests/                             Logic, Windows adapters and UI smoke tests
```

## Known limitations

- It only alerts for the chats you configure. It is not a full Rocket.Chat client: the chat
  window is read-only with a one-line reply (no attachments, threads or typing indicator).
- If a configured room never sounds, check that it is not archived and that you are still a
  member.
- At most four sounds play at the same time; one more arriving in the same instant is skipped.
- Notifications use the classic tray notification API, so they have no action buttons.

## License

MIT, see [`LICENSE`](LICENSE). The bundled sounds are CC0 (public domain); details in
[`src/mk96_app_pc_rocket_tool/resources/sounds/default/LICENSES.md`](src/mk96_app_pc_rocket_tool/resources/sounds/default/LICENSES.md).
