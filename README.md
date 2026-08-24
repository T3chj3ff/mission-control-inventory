# Mission Control Inventory

Small, readable scanners that create a local JSON inventory of Git projects for
[Mission Control](https://mission-control-flight-deck.techj3ff.chatgpt.site).

The scanners only inspect folders you explicitly provide. They write the result
to your computer and never upload it. The generated inventory may contain local
paths and Git remote URLs, so review it before sharing it anywhere.

## macOS — clone, update, and scan

Open Terminal and paste this entire command:

```bash
if [ -d "$HOME/mission-control-inventory/.git" ]; then git -C "$HOME/mission-control-inventory" pull --ff-only; else git clone https://github.com/T3chj3ff/mission-control-inventory.git "$HOME/mission-control-inventory"; fi && bash "$HOME/mission-control-inventory/mission-control-inventory-mac.command" "$HOME/Documents"
```

This creates `mission-control-projects-mac.json` on your Desktop. In Mission
Control, open **Projects**, choose **Import inventory**, and select that file.

To scan more than one folder, run:

```bash
bash "$HOME/mission-control-inventory/mission-control-inventory-mac.command" "$HOME/Documents" "$HOME/Developer"
```

Python 3 and Git are required. On a standard development Mac, both are normally
already available. If Python 3 is missing, run `xcode-select --install`.

## Windows — clone, update, and scan

Open PowerShell and run:

```powershell
if (Test-Path "$HOME\mission-control-inventory\.git") { git -C "$HOME\mission-control-inventory" pull --ff-only } else { git clone https://github.com/T3chj3ff/mission-control-inventory.git "$HOME\mission-control-inventory" }
& "$HOME\mission-control-inventory\mission-control-inventory.ps1" -Root "$HOME\Documents" -Output "$HOME\Desktop\mission-control-projects-windows.json"
```

## What is collected

- Project name and local folder path
- Machine name
- Git branch, dirty-file count, last commit time, and origin URL
- A lightweight tech-stack guess based on common project files

No file contents, credentials, environment variables, or Git history are copied.
The source repository contains scanner code only—never generated inventory files.
