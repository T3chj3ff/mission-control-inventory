# Mission Control Inventory

Small, readable scanners that create a local JSON inventory of Git projects for
[Mission Control](https://mission-control-flight-deck.techj3ff.chatgpt.site).

The scanners only inspect folders you explicitly provide. They write the result
to your computer and never upload it. The generated inventory may contain local
paths and Git remote URLs, so review it before sharing it anywhere.

## Secure command runner

Mission Control can dispatch owner-approved commands to an enrolled computer.
The runner opens no inbound port: it polls the private job queue, starts work
only in roots configured on that computer, blocks destructive command patterns,
and returns bounded stdout/stderr to the command ledger. The root check is a
working-directory guardrail, not an operating-system sandbox; commands retain
the local runner account's permissions.

After choosing **Execute → Enroll machine** in Mission Control, configure the
runner without putting tokens in shell history:

```bash
python3 mission_control_agent.py configure
python3 mission_control_agent.py install
```

The first command securely prompts for the one-time machine token, the private
Sites connection token when required, and allowed folders. Configuration is
stored in `~/.mission-control/agent.json` with user-only permissions where the
operating system supports them. Revoke a machine from Mission Control at any
time to invalidate its machine token and cancel queued work.

`install` creates a user-level background service—LaunchAgent on macOS, a login
task on Windows, or a systemd user service on Linux—and starts it immediately.
Use `python3 mission_control_agent.py status` to inspect it or
`python3 mission_control_agent.py uninstall` to remove the service while
preserving the local configuration.

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

No file contents, credentials, environment variables, or Git history are copied
by the inventory scanners. The source repository contains code only—never
generated inventory files or runner credentials.
