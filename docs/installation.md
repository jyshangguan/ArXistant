---
layout: default
title: Installation
description: Install ArXistant on macOS, Debian/Ubuntu, Windows, or another Linux distribution.
nav_order: 1
---

# Installing ArXistant

ArXistant has two cooperating parts:

1. A local Python server that fetches, ranks, and stores papers.
2. A Chrome extension that opens the interface, schedules reminders, and asks
   the server to refresh papers.

The extension cannot perform ranking by itself. The local server must be
running at `http://localhost:8765`.

## Choose an installation method

| Platform | Recommended method | Server startup |
|---|---|---|
| macOS | Repository checkout and bundled helper app | Extension button or `start_server.sh` |
| Debian/Ubuntu | Build and install the `.deb` package | systemd user service |
| Other Linux | Manual Python setup | Terminal or your service manager |
| Windows | Manual Python setup | PowerShell |

Python 3.8 or newer is required for a manual installation.

## macOS

### 1. Download the project and dependencies

```bash
git clone https://github.com/jyshangguan/ArXistant.git
cd ArXistant
python3 -m pip install --user -r requirements.txt
```

The launcher discovers the repository location automatically, so the checkout
does not need to be in a particular directory.

### 2. Load the Chrome extension

1. Open `chrome://extensions/`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select the `chrome-extension` directory inside the repository.
5. Optionally pin the red ArXistant icon to Chrome's toolbar.

Select the directory containing `manifest.json`, not the repository root:

```text
ArXistant/
└── chrome-extension/      ← select this directory
    ├── manifest.json
    ├── popup.html
    └── background.js
```

### 3. Register the server helper

Double-click:

```text
chrome-extension/ArXistantServer.app
```

If macOS blocks it, open **System Settings → Privacy & Security** and choose
**Open Anyway**. The first time the extension opens `arxistant://start`, allow
Chrome to use **ArXistantServer**. The helper has no Dock window; it starts the
server in the background.

You can also start the server directly:

```bash
/bin/bash start_server.sh
```

Logs are written to `local/server.log`.

## Debian and Ubuntu package

The package installs the server, a systemd user service, and a copy of the
Chrome extension. It currently uses Debian's Python, NumPy, and scikit-learn
packages.

### 1. Build and install

From a repository checkout:

```bash
./packaging/linux/build-deb.sh
sudo apt install ./dist/arxistant_0.1.1_all.deb
```

The dependency installation is relatively large because Debian's scientific
Python packages pull in SciPy and supporting native libraries.

To build the package on a non-Debian host with Docker:

```bash
docker run --rm -v "$PWD:/work" -w /work debian:bookworm-slim \
  sh -c 'apt-get update && apt-get install -y dpkg-dev && ./packaging/linux/build-deb.sh'
```

### 2. Start the service

```bash
systemctl --user daemon-reload
systemctl --user enable --now arxistant.service
```

It starts automatically on subsequent logins. Check its state and logs with:

```bash
systemctl --user status arxistant.service
journalctl --user -u arxistant.service
```

### 3. Load the extension

Open `chrome://extensions/`, enable **Developer mode**, choose **Load unpacked**,
and select:

```text
/usr/share/arxistant/chrome-extension
```

The Linux popup shows a **Start Server** button that opens the registered
`arxistant://` URL scheme handler. If the button is unavailable (for example
on a manual installation without the desktop entry), the popup displays the
corresponding `systemctl --user start` command instead.

### Linux data location

The package stores writable data in:

```text
~/.local/share/arxistant
```

If `XDG_DATA_HOME` is available to the service, it uses
`$XDG_DATA_HOME/arxistant` instead. Package upgrades do not remove this data.

### Remove the Linux package

```bash
systemctl --user disable --now arxistant.service
sudo apt remove arxistant
```

Delete the data directory separately only if you also want to erase your paper
database, model, configuration, and generated pages.

## Windows and other manual installations

Clone the project and install its dependencies:

```powershell
git clone https://github.com/jyshangguan/ArXistant.git
Set-Location ArXistant
py -m pip install --user -r requirements.txt
```

Generate a daily page and start the server:

```powershell
py src\arxiv_daily_ranker_html.py --output local\arxiv_ranked_personalized.html
py src\arxiv_db_server.py
```

Keep the PowerShell window open while using ArXistant. Load the repository's
`chrome-extension` directory through `chrome://extensions` as described above.
The automatic **Start Server** helper is not yet available on Windows.

Equivalent commands on an unpackaged Linux installation are:

```bash
python3 -m pip install --user -r requirements.txt
python3 src/arxiv_daily_ranker_html.py \
  --output local/arxiv_ranked_personalized.html
python3 src/arxiv_db_server.py
```

## ADS API token

An ADS token is optional. Daily arXiv ranking and the saved-paper database work
without one. SciX publication import and ADS search require a token from the
[NASA ADS API settings](https://ui.adsabs.harvard.edu/user/settings/token).

For a repository installation, save it as:

```text
local/ads_token.txt
```

For the Debian package, save it as:

```text
~/.local/share/arxistant/ads_token.txt
```

The file should contain only the token.

## Migrating existing data

Stop the old and new servers before copying data. Copy the contents of the old
`local/` directory into the new data directory. Important files include:

- `arxiv_papers.db` — saved papers and publications.
- `ml_ranker/` — trained model and custom keywords.
- `ads_token.txt` and `scix_config.json` — ADS/SciX configuration.
- Generated daily, recent, and ML feature pages.

Never run two ArXistant servers against the same SQLite database concurrently.

## Updating

For a repository installation:

```bash
git pull
python3 -m pip install --user -r requirements.txt
```

Restart the server and click **Reload** for ArXistant on
`chrome://extensions`. For Debian/Ubuntu, build the new package and install it
with `apt` over the existing version, then restart the user service.

## Installation troubleshooting

### Chrome reports that the server is offline

Open [http://localhost:8765/api/health](http://localhost:8765/api/health). A
running server returns JSON containing `"success": true`.

- macOS: inspect `local/server.log` and run `/bin/bash start_server.sh`.
- Linux package: run `systemctl --user status arxistant.service` and inspect
  `journalctl --user -u arxistant.service`.
- Manual setup: look for the traceback in the terminal running the server.

### NumPy architecture errors on Apple Silicon

Stop servers launched by an older checkout and run `/bin/bash start_server.sh`
again. The launcher explicitly selects ARM64 system Python and removes inherited
virtual-environment variables that can select an incompatible NumPy build.

### Port 8765 is already in use

Only one server can listen on the default port. Stop the older ArXistant process
or identify the other program using the port before restarting.

### Notifications do not appear

Use **Extension Settings → Test Notification**. Also verify that Chrome has
notification permission in the operating system and that Focus/Do Not Disturb
is not suppressing banners.
