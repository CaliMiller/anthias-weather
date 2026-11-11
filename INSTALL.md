# Installing anthias-weather

There are two ways to install anthias-weather on your Anthias PC. Both take about 30 minutes, and no Linux or Docker experience is needed: every command is spelled out, and each step says what you should see.

| | **Method A: Over SSH** | **Method B: USB flash drive** |
| --- | --- | --- |
| Use it if | You can log in to the Anthias PC from another computer with `ssh` | SSH isn't set up, or you'd rather work at the PC itself |
| You need | Another computer on the same network | A USB flash drive and a USB keyboard |
| The sign | Keeps playing the whole time | Shows a text screen until the PC restarts |

Not sure if SSH is set up? Try step A1 below. If it doesn't connect, use Method B; it also includes an optional step that turns SSH on for next time.

**Contents**

- [Before you start (both methods)](#before-you-start-both-methods)
- [Method A: Over SSH](#method-a-over-ssh)
- [Method B: USB flash drive](#method-b-usb-flash-drive)
- [Changing settings later](#changing-settings-later)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [Forgot your Debian username or password](#forgot-your-debian-username-or-password)

---

## Before you start (both methods)

### What you need

- **Anthias installed on Debian 12 or 13** on a PC, the standard [Anthias PC (x86) install](https://anthias.screenly.io/docs/pc/). anthias-weather needs Python 3.11 or newer, which those Debian versions include. Nothing else is downloaded or installed.
- **Your Debian username and password**: the account you created when you installed Debian, before installing Anthias. Forgot them? See [Forgot your Debian username or password](#forgot-your-debian-username-or-password).
- **The Anthias PC's IP address**, for example `192.168.1.50`. It shows on the Anthias screen while it starts up, and in your router's list of connected devices.

In the commands below, replace `jane` with your Debian username and `192.168.1.50` with your Anthias PC's address.

### Typing commands

- Type or paste each command exactly as shown, then press **Enter**. Capital letters, spaces and punctuation all matter.
- When you type a password, **nothing appears on screen**, not even dots. That's normal.
- The **Up arrow** brings back the last command, so you can fix a typo without retyping it.
- A command that finishes with no message usually worked.

### Collect three settings

You'll put these in the settings file, `config.toml`.

1. **The names of your alert pages.** On another computer, open the Anthias dashboard in a web browser at `http://192.168.1.50`. Write down the exact names of the pages to show during a warning, for example **Weather Alerts** and **Weather Radar**. Capital letters don't matter, but spaces do.
2. **Your home's coordinates.** In [Google Maps](https://maps.google.com), right-click your house. The first line of the menu shows two numbers, like `39.76841, -86.15808`; click them to copy. The first is latitude, the second longitude.
3. **Your email address.** The National Weather Service asks every program that uses its data to include a contact. It's sent only to api.weather.gov.

---

## Method A: Over SSH

### A1. Log in to the Anthias PC

On your other computer, open **PowerShell** (Windows: right-click the Start button, then **Terminal** or **Windows PowerShell**) or **Terminal** (Mac: in Applications > Utilities). Then type:

```
ssh jane@192.168.1.50
```

- The first time, it asks `Are you sure you want to continue connecting?` Type `yes` and press Enter.
- Enter your Debian password.

You're logged in when the prompt changes to something like `jane@anthias:~$`. Every command from here on runs on the Anthias PC.

If it says `Connection refused` or times out, SSH isn't turned on; use [Method B](#method-b-usb-flash-drive) instead. Logging in as `root` won't work; Debian blocks root logins over SSH by default.

### A2. Download and install

Paste these one at a time. In PowerShell, right-click pastes. If `sudo` asks for a password, type yours.

```
curl -L -o anthias-weather.tar.gz https://github.com/CaliMiller/anthias-weather/archive/refs/heads/main.tar.gz
tar xzf anthias-weather.tar.gz
cd anthias-weather-main
sudo bash install.sh
```

The installer finishes within a few seconds and prints `Installed. Next steps:` followed by a short list. The steps below cover the same thing.

### A3. Fill in the settings

Open the settings file in the built-in text editor:

```
sudo nano /etc/anthias-weather/config.toml
```

Move around with the **arrow keys**; the mouse doesn't work here. Lines starting with `#` are notes and are ignored. Change these three lines, keeping the quotation marks:

| Line | Change it to | Example |
| --- | --- | --- |
| `point = "39.7684,-86.1581"` | Your coordinates | `point = "39.76841,-86.15808"` |
| `assets = ["Weather Alerts", "Weather Radar"]` | Your alert page names, each in quotes, separated by a comma. The first one shows first. | `assets = ["NWS Alerts", "Radar Loop"]` |
| `user_agent = "anthias-weather (you@example.com)"` | Your email instead of `you@example.com` | `user_agent = "anthias-weather (jane@mail.com)"` |

If you turned on a login for the Anthias dashboard, also find the `# username =` and `# password =` lines, delete the `# ` at the start of each, and fill in your Anthias login.

Save with **Ctrl+O** then **Enter**, and close the editor with **Ctrl+X**.

### A4. Check, start and test

1. Check that your alert pages were found:

    ```
    sudo anthias-weather list-assets
    ```

    This lists your playlist in play order. The pages used during alerts are marked `*`. If a page appears twice in your loop, the second copy is marked `copy` and stays off during alerts.

2. Run the self-test:

    ```
    sudo anthias-weather check
    ```

    The last line should say **All good.** If it doesn't, it names the problem; see [Troubleshooting](#troubleshooting).

3. Start it, and set it to start at every boot:

    ```
    sudo systemctl enable --now anthias-weather
    ```

    A line starting `Created symlink` is normal.

4. Test alert mode:

    ```
    sudo anthias-weather force alert
    ```

    Within about 10 seconds, the sign switches to your first alert page and rotates only between the alert pages. In the Anthias dashboard, everything else shows as switched off.

5. Put it back:

    ```
    sudo anthias-weather force auto
    ```

    Within about 10 seconds the normal rotation returns, in its original order. From now on, only real NWS warnings switch it.

Type `exit` to log out. You're done.

---

## Method B: USB flash drive

Anthias normally covers the PC's text screen with the slideshow, even when you switch screens with Ctrl+Alt+F2. So in this method you start the PC **once with Anthias switched off**, install, and restart normally. Nothing about Anthias is changed; it's off for that one start only.

### B1. On your other computer: prepare the flash drive

1. Download the project from GitHub: on the project page, click the green **Code** button, then **Download ZIP**.
2. Unzip it. Windows: right-click the file, **Extract All**. Mac: double-click it (Safari may already have unzipped it). You get a folder named **anthias-weather-main**.
3. Inside that folder, make a copy of **config.example.toml** and name the copy **config.toml**.
4. Open **config.toml** in a plain text editor:
    - **Windows:** right-click it, **Open with**, **Notepad**.
    - **Mac:** open **TextEdit**, choose **Format > Make Plain Text**, then **File > Open**.
5. Change the three lines shown in the table in [step A3](#a3-fill-in-the-settings), and the Anthias login lines if you use one. Lines starting with `#` are notes.
6. Save. In Notepad, if you use **Save As**, set **Save as type** to **All files** so the name stays `config.toml`. On a Mac, if asked, choose **Use .toml**.
7. Copy the whole **anthias-weather-main** folder to the top level of the flash drive (not inside another folder). The config.toml you edited is inside it.
8. Eject the drive safely.

The drive should be formatted **FAT32** or **exFAT**, which most flash drives are out of the box.

### B2. Start the PC with Anthias switched off

Plug a USB keyboard and the flash drive into the Anthias PC. If the PC has USB ports on the back, use those.

1. **Restart the PC.** Press **Ctrl+Alt+Delete**. If nothing happens within a minute, hold the power button until the PC turns off, then turn it back on.
2. **Open the boot menu.** As the PC starts, a menu appears listing **Debian GNU/Linux**. If it flashes past, restart and press **Esc** a few times right after the manufacturer's logo.
3. With **Debian GNU/Linux** highlighted, press **e**. A screen of text appears.
4. Use the arrow keys to move to the line that starts with `linux`. It may wrap onto a second screen line. Press **End** to jump to the end of it.
5. Type a space, then:

    ```
    systemd.mask=docker.service systemd.mask=docker.socket
    ```

6. Press **Ctrl+X** (or **F10**) to start.

The PC starts to a black text screen ending in `login:`, without the slideshow. This change applies to this one start only; the next restart is back to normal.

### B3. Log in

1. Type your Debian **username** and press Enter.
2. Type your **password** and press Enter. Nothing appears as you type it.

You're logged in when the last line looks like `jane@anthias:~$`. If you don't see `login:`, press **Ctrl+Alt+F2**.

### B4. Copy from the flash drive and install

1. Find the flash drive's name:

    ```
    lsblk -o NAME,SIZE,TRAN,FSTYPE,LABEL
    ```

    Find the drive with **usb** in the TRAN column; its size matches your flash drive. The line under it, starting with `└─`, has the name you need, such as **sdb1**. In this example it's `sdb1`:

    ```
    NAME          SIZE TRAN FSTYPE LABEL
    nvme0n1     238.5G nvme
    ├─nvme0n1p1   512M      vfat
    └─nvme0n1p2   238G      ext4
    sdb          14.6G usb
    └─sdb1       14.6G      vfat   MYDRIVE
    ```

2. Connect the drive, replacing `sdb1` with your name:

    ```
    sudo mkdir -p /mnt/usb
    sudo mount /dev/sdb1 /mnt/usb
    ```

3. Copy the project folder, then install:

    ```
    cp -r /mnt/usb/anthias-weather-main ~
    cd ~/anthias-weather-main
    sudo bash install.sh
    sudo cp config.toml /etc/anthias-weather/config.toml
    ```

    The installer prints `Installed. Next steps:`. The steps below cover the same thing.

### B5. Check your settings

```
sudo anthias-weather check
```

Because Anthias is switched off right now, the **Anthias** part says `PROBLEM: cannot reach Anthias` with a note that this is expected. That's fine.

The **NWS** part should say `reachable`. If it shows `config error` instead, it names the line to fix; edit the file with `sudo nano /etc/anthias-weather/config.toml` as described in [step A3](#a3-fill-in-the-settings), then run `check` again.

### B6. Optional: turn on SSH for next time

With SSH on, future changes and upgrades can be done from your other computer using Method A.

```
sudo apt install -y openssh-server
```

### B7. Start it and set up a test

```
sudo systemctl enable anthias-weather
sudo anthias-weather force alert --minutes 20
```

The second command puts the sign in alert mode for 20 minutes, then switches it back to normal on its own, so you can see it working without the keyboard.

Then disconnect the flash drive and restart. That's `umount`, without an n after the u:

```
sudo umount /mnt/usb
sudo reboot
```

### B8. Watch the sign

The PC restarts normally, with Anthias. Once the slideshow starts (usually within a few minutes), it should go straight to your alert pages and rotate only between them. In the Anthias dashboard, everything else shows as switched off.

About 20 minutes after you ran the command in B7, the normal rotation returns in its original order. From then on, only real NWS warnings switch it. You can unplug the keyboard and flash drive. You're done.

If the sign never switches, check the `assets` names in your config against the Anthias dashboard, and see [Troubleshooting](#troubleshooting).

---

## Changing settings later

1. Log in: over SSH (step A1), or with Method B's steps B2 and B3.
2. Edit the settings: `sudo nano /etc/anthias-weather/config.toml`
3. Check the change: `sudo anthias-weather check`
4. Apply it: `sudo systemctl restart anthias-weather`

**Changes made in the Anthias dashboard during a warning are undone when it ends,** because the playlist saved at the start of the warning is put back. Make playlist changes after the warning, or run `sudo anthias-weather force normal` first and `sudo anthias-weather force auto` when you're done.

---

## Upgrading

Your settings are kept when you upgrade.

**Over SSH:** log in (step A1), then:

```
rm -rf ~/anthias-weather-main anthias-weather.tar.gz
curl -L -o anthias-weather.tar.gz https://github.com/CaliMiller/anthias-weather/archive/refs/heads/main.tar.gz
tar xzf anthias-weather.tar.gz
cd anthias-weather-main
sudo bash install.sh
```

It prints `Upgraded. Your config at /etc/anthias-weather/config.toml was kept.` and restarts the service.

**With a flash drive:**

1. Download and unzip the new version as in step B1, steps 1 and 2, and copy the **anthias-weather-main** folder to the drive. You don't need a config.toml this time.
2. Start the PC with Anthias switched off, log in, and connect the drive: steps B2, B3, and B4 parts 1 and 2.
3. Replace the old copy and install:

    ```
    rm -rf ~/anthias-weather-main
    cp -r /mnt/usb/anthias-weather-main ~
    cd ~/anthias-weather-main
    sudo bash install.sh
    ```

    It prints `Upgraded. Your config at /etc/anthias-weather/config.toml was kept.`

4. Restart normally:

    ```
    sudo umount /mnt/usb
    sudo reboot
    ```

---

## Uninstalling

Log in, then:

```
sudo systemctl disable --now anthias-weather
sudo anthias-weather restore --yes
sudo rm -rf /opt/anthias-weather /etc/anthias-weather /var/lib/anthias-weather
sudo rm /usr/local/bin/anthias-weather /etc/systemd/system/anthias-weather.service
sudo userdel anthias-weather
sudo systemctl daemon-reload
```

`restore --yes` puts your normal playlist back if the sign was in alert mode; otherwise it says there's nothing to restore. It needs Anthias running, so do this from a normal start (over SSH, or at the keyboard after a normal restart), not from Method B's Anthias-off start.

---

## Troubleshooting

### Connecting and logging in

| What you see | What to do |
| --- | --- |
| `ssh: connect to host ... Connection refused` or a timeout | SSH isn't turned on, or the IP address is wrong. Check the address, or use Method B, which includes turning SSH on (step B6). |
| `Permission denied, please try again.` | Wrong username or password. Usernames are lower case. Logging in as `root` over SSH is blocked by Debian by default. |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!` | Happens after Debian is reinstalled. Run `ssh-keygen -R 192.168.1.50` on your other computer, then connect again. |
| The boot menu never appears | Restart and press **Esc** repeatedly as soon as the manufacturer's logo appears. Some PCs use **Shift** instead. |
| The slideshow still starts after editing the boot line | The text wasn't added to the `linux` line, or has a typo. Restart and try B2 again. |
| The keyboard stops working at the text screen | Unplug other USB devices and use a port on the back of the PC. A wireless keyboard's receiver can be unreliable here; a wired keyboard is more dependable. |

### Flash drive

| What you see | What to do |
| --- | --- |
| `mount: ... wrong fs type` | The drive may be formatted NTFS. Try `sudo mount -t ntfs3 /dev/sdb1 /mnt/usb` with your drive's name, or reformat the drive as exFAT on your other computer. |
| `mount: ... does not exist` | The name in the command is wrong. Run the `lsblk` command again. If the usb line has nothing under it, use the top name, such as `sdb`. |
| `No such file or directory` when copying | Run `ls /mnt/usb` and use the folder name exactly as listed. |

### Settings and the self-test

| What you see | What to do |
| --- | --- |
| `config error: ... has a typo near ...` | A quotation mark, comma or bracket is missing on the line it names. |
| `config error: nws.user_agent must identify you` | Put your email in the `user_agent` line. |
| `alert asset(s) not found` | A name in `assets` doesn't match Anthias. Compare it with `sudo anthias-weather list-assets`. |
| `redirected` or `authentication` | The Anthias dashboard has a login. Add the `username` and `password` lines to the `[anthias]` section. |
| `cannot reach Anthias` during a normal start | Anthias may still be starting; wait a few minutes and try again. |
| `cannot reach NWS API` | The PC can't reach the internet right now. Check your network, then try again. |
| `command not found` | Check the spelling. If it's `anthias-weather` itself, run the installer again (`sudo bash install.sh` from the project folder). |

### On the sign

| What you see | What to do |
| --- | --- |
| The sign doesn't switch during a test | Run `sudo anthias-weather status` and `sudo journalctl -u anthias-weather -n 50` (press **q** to close). The log names the problem. |
| An alert page doesn't show even though it's switched on | It may have start/end dates or play times that make it inactive. Check its schedule in the Anthias dashboard. |
| The sign stays in alert mode after a warning | It returns to normal two checks (about 2 minutes) after the last warning ends. If it still shows ALERT, run `sudo anthias-weather force normal`, then `sudo anthias-weather force auto`. |

---

## Forgot your Debian username or password

You can reset it at the PC with a keyboard:

1. Open the boot menu and edit the `linux` line as in [step B2](#b2-start-the-pc-with-anthias-switched-off), but instead of the `systemd.mask` text, type a space and `init=/bin/bash`. Press **Ctrl+X**.
2. The PC starts to a prompt like `root@(none):/#`. Type these quickly, before any USB messages fill the screen:

    ```
    mount -o remount,rw /
    ls /home
    ```

    The first command lets your changes be saved. The second shows your username.

3. Set a new password, replacing `jane` with your username. Type the new password twice:

    ```
    passwd jane
    ```

4. Save the change to disk:

    ```
    sync
    ```

5. Hold the power button until the PC turns off, then turn it back on. It starts normally.
