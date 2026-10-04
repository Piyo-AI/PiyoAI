# Install Piyo

## Download

Installers are on the releases page: <https://github.com/Piyo-AI/PiyoAI/releases>. Choose the file for your
system.

| System | File | Notes |
|---|---|---|
| Windows 10 (version 22H2) or 11, 64-bit | the `.exe` installer | Needs Microsoft WebView2, which these versions include |
| macOS 13.5 (Ventura) or newer, on Apple Silicon (M1 and later) | the `.dmg` | There is no build for Intel Macs |
| Linux (64-bit): Ubuntu 22.04 or newer, Debian 12, Fedora 39 or newer | `.AppImage`, `.deb` or `.rpm` | Needs `webkit2gtk-4.1`, which these releases include |

Piyo is not code-signed yet, so your system will warn you the first time. That warning is about the missing
signature, not about something found in the file. Only install files you downloaded from the releases page above.

## First launch

**Windows.** SmartScreen may say "Windows protected your PC". Choose **More info**, then **Run anyway**.

**macOS.** Open the `.dmg` and drag Piyo to Applications. The first time, macOS says it cannot verify the app.
Open **System Settings > Privacy & Security**, scroll to the message about Piyo and choose **Open Anyway**.

**Linux.**

```bash
chmod +x Piyo*.AppImage && ./Piyo*.AppImage      # AppImage
sudo apt install ./piyo*.deb                       # Debian, Ubuntu
sudo dnf install ./piyo*.rpm                       # Fedora
```

The file names on the releases page are the source of truth; adjust the commands to match.

## Setup

The first start opens a short setup: choose a model (see [Models and providers](providers.md)), see what leaves
your computer, and try a first task. You can skip it and come back with Settings > Run setup again.

## Updates

Piyo checks for a new version when it starts and shows a banner. It never downloads or installs one until you press
**Download and install**. The page then shows each step (getting ready, downloading with a progress bar, checking
the signature and installing). Every download is checked against Piyo's update key before it is installed.
Settings > Updates shows your version and lets you check by hand.

If a new version does not start properly twice in a row, the third start offers to go back to the version you had.

## Where your data is

| What | Where |
|---|---|
| Conversations, memory, task log, skills, settings | Windows: `%APPDATA%\Piyo AI\PiyoAI` · macOS: `~/Library/Application Support/PiyoAI` · Linux: `~/.local/share/PiyoAI` |
| Files Piyo creates | `Piyo` in your home folder |
| API keys and tokens | Your operating system's keychain (Credential Manager, Keychain, Secret Service) |

## Remove Piyo

Uninstall it like any other app (Windows: Settings > Apps; macOS: drag it to the Bin; Linux: remove the package
or delete the AppImage). Your data stays until you delete the folders in the table above. Remove the Piyo entries
from your keychain to delete saved keys.
