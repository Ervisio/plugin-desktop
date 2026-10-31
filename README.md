# Desktop — Ervisio plugin

A Linux desktop manager and **interactive browser remote desktop** for [Ervisio](https://github.com/Ervisio/ervisio), styled after the native Ervisio UI and the team's [`plugin-ai`](https://github.com/Ervisio/plugin-ai).

> **Status: preview / not production-validated.** The Python management and gateway unit tests pass; actual TigerVNC, GNOME/KDE/Cinnamon desktop launches and npm/Vite production bundle have **not** been tested on a desktop VM in this environment. Verify on a disposable Linux VM before exposing to important accounts or servers.

## What it does

- Detect GNOME, KDE Plasma, Cinnamon, Xfce, MATE and LXQt by installed packages and `/usr/share/{xsessions,wayland-sessions}/*.desktop`. Discover other installed session entries.
- Pick *Minimal*, *Standard*, *Complete* or *Custom* components (file manager, terminal, display manager, extras).
- Preview and install via APT, DNF, pacman or zypper. No user-controlled arbitrary package names or shell invocation. APT simulates operations and blocks removal of critical system packages before proceeding.
- Two uninstall modes: **Managed** (only packages newly added by this plugin) and **Advanced** (known desktop packages, never user home folders; not a promise to remove every dependency). Shows a preview first. Display managers are not force-purged.
- Persistent background package operations via systemd transient units with recent output and progress. Changing boot target doesn't terminate current sessions.
- Manage GDM, SDDM, LightDM and LXDM; view loginctl sessions, accumulated CPU time and scope memory. Terminate sessions after confirmation.
- **Open desktop** tab: starts/reconnects a complete *virtual X11 desktop* for the signed-in Linux user, including mouse, keyboard, viewport scaling, desktop resizing (when supported), clipboard text, and fullscreen request. Sessions survive closing the tab until explicitly terminated or gateway restart.
- Every live viewer is tunneled via `sdk.api.httpStream()` and `sdk.api.http()` to a **local Unix socket**, using noVNC's RFB client. No public 5900/6080/3389 ports, no external domain or websocket proxy required.
- On Windows the page explains that Linux desktop environments aren't supported and all Linux host capabilities are excluded by the manifest's `platforms` filters.

## Supported systems

- Ervisio **0.6.2+**, SDK contract v3, Linux with **systemd** and Python 3.10 or newer.
- Package management supported: Debian/Ubuntu-family (APT), Fedora/RHEL-family (DNF), Arch (pacman), openSUSE (zypper). Actual package availability differs per distro; install preview resolves candidates on the current host.
- Virtual desktop: **TigerVNC Xvnc**, `xauth`, `dbus-run-session`, and an installed compatible **X11 session**. The gateway installer attempts to install the runtime dependencies with the host package manager.
- GNOME and KDE **Wayland-only** hosts are detected but **cannot be streamed through this X11 backend**. Install an appropriate X11 session or use Xfce; Wayland screen-sharing is future work.
- New independent desktop sessions, **not takeover of the physical monitor**. No audio, USB redirection or printer redirection in this preview; clipboard supports text only.
- Windows is unsupported by design even if Ervisio itself runs on Windows.

## Install / develop

```sh
npm install
npm test
npm run build
npm run pack
```

Build output: `dist/desktop/`. Load it in **Ervisio → Plugins → Developer** while developer mode is enabled. For a normal marketplace installation, release and register this plugin through the trusted [Ervisio/plugins registry](https://github.com/Ervisio/plugins), which signs the package. This ZIP is **source code**, not a signed marketplace archive.

The declared Python commands use the installed plugin path `/var/lib/ervisio/plugins/desktop/`. In developer mode, that path must point to the generated `dist/desktop/` for backend commands to work. An unmodified dev folder in a different directory won't be reached by the fixed manifest paths. Avoid altering the installed marketplace plugin folder by hand.

First time in **Apri desktop**, choose **Installa gateway remoto**. Requires one administrator unlock; installs TigerVNC and registers `ervisio-desktop-gateway.service`. Afterwards, each Linux user creates their own virtual X11 session by choosing the desktop and resolution, and uses it directly in the embedded viewer.

### Architecture

```text
Ervisio sandboxed plugin page
  |  noVNC RFB client -> ErvisioRfbChannel
  |  SDK httpStream (read) + http POST (write)
  v
Ervisio plugin capability broker (UID of logged-in Linux account)
  |  local socket /run/ervisio-desktop/gateway.sock
  v
Ervisio Desktop Gateway (root service, SO_PEERCRED verification)
  |  restrict to peer uid, random viewer token in header
  |  per-UID directory mode 0700
  |  per-user private VNC Unix socket, no TCP listener
  v
TigerVNC Xvnc + Xauthority + dbus-run-session
  |  processes dropped to Linux user's UID/GID/groups
  v
GNOME (X11) / KDE (X11) / Cinnamon / Xfce / MATE / LXQt / discovered X11 sessions
```

### Security caveats

- The gateway service runs as root to launch a desktop as each signed-in user. Every request uses Linux `SO_PEERCRED`; only normal Linux UIDs may use it, and their sessions are isolated by UID. The local gateway socket is world-connectable, but only permits requests scoped to the verified UID; the VNC socket is mode 0600 under a per-user 0700 folder.
- Remote VNC uses **SecurityTypes=None only on a per-user AF_UNIX socket** and disables TCP (`-rfbport -1`); do not replace it with an exposed `5900` server. The actual X11 display is protected by an xauth MIT-MAGIC-COOKIE file in that same private runtime directory.
- The RFB viewer token is sent in a declared `X-Desktop-Token` header, not logged in request URLs. Do not share browser sessions or Ervisio account credentials.
- Gateway installation changes packages and creates a systemd service. Package removal can still affect dependencies. Do not use on a host without a recovery console/SSH and backups.
- This isn't a multi-tenant paid-hosting sandbox. Resource quotas, frame rate limits, audit retention, lockscreen policies, and timeouts should be implemented and security-reviewed before exposing many untrusted users.
- Browser clipboard and fullscreen are constrained by iframe/browser permissions; manual copy/paste fallback remains available.

## Files

| File | Purpose |
|---|---|
| `plugin/manifest.json` | Declared commands, Unix HTTP API, Linux-only capabilities, contributions |
| `plugin/desktop_bridge.py` | Package operations, session detection, display managers and jobs |
| `plugin/desktop_gateway.py` | Root local gateway and per-user private TigerVNC session lifecycle |
| `src/index.tsx` | Main Ervisio UI (environments, sessions, display managers, jobs) |
| `src/remote.tsx` | Embedded noVNC viewer and Ervisio SDK transport adapter |
| `src/style.css` | Native-themed layout inspired by plugin-ai |
| `tests/` | Python unit and Unix HTTP gateway tests |

## Next increments

1. Real VM smoke tests for Ubuntu, Debian, Fedora and Arch, and automatic recovery when a session crashes.
2. Physical-session capture using x0vncserver where the user owns an active X11 desktop.
3. Wayland remote-desktop backend through compositor-supported protocols (GNOME Remote Desktop or KDE's own solutions); needs a separate security and session model.
4. Keyboard layouts, explicit clipboard permission UI, audio, resource limits and granular session controls.
5. Linux desktop package recipes per distro and reliable removal preflight / package-manager transaction previews beyond APT.
