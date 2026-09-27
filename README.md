# What On Earth Server

This lets you play the **"What On Earth" beta**, an early build of **Big Bang Racing by Traplight**.

## How It Works

The game normally communicates with its original online servers. This project runs a small local server on your PC that pretends to be the original server and handles the game's requests locally.

Your game connects to your local server instead of connecting to the original servers.

## Table of Contents

* [Game Screenshots](#game-screenshots)
* [Requirements](#requirements)

  * [iOS](#ios)
  * [Windows](#windows)
* [Setup](#setup)

  * [iOS](#ios-1)
  * [Windows](#windows-1)
* [Troubleshooting](#troubleshooting)

  * [Stuck on "Connecting to Server"](#stuck-on-connecting-to-server)
  * [Port 80 Already in Use / IIS](#port-80-already-in-use--iis)
  * [`/etc/hosts` Changes Don't Seem to Work](#etchosts-changes-dont-seem-to-work)
* [Saving Your Levels](#saving-your-levels)
* [Contact](#contact)

---

## Game Screenshots

<table>
  <tr>
    <td><img src="Screenshots/Screenshot 1.png" width="600" alt="Screenshot 1"></td>
    <td><img src="Screenshots/Screenshot 2.png" width="600" alt="Screenshot 2"></td>
    <td><img src="Screenshots/Screenshot 3.png" width="600" alt="Screenshot 3"></td>
    <td><img src="Screenshots/Screenshot 4.png" width="600" alt="Screenshot 4"></td>
  </tr>
</table>

---

# Requirements

## iOS

* The What On Earth game — [Download the 32-bit version](https://archive.org/details/traplight-whatonearth)
* A jailbroken **32-bit Apple device**
* A file manager such as **iFile** or **Filza**
* **Python 3.7 or newer** — [Download Python](https://www.python.org/downloads/)
* Both your PC and Apple device connected to the **same Wi-Fi network**

## Windows

* The **[What On Earth Windows build](https://github.com/Dodonickey/WOE-Decompiled/releases)**
* **Python 3.7 or newer** — [Download Python](https://www.python.org/downloads/)
* Windows (Obviously)

---

# Setup

## iOS

### 1. Install the Game

Download the **What On Earth** game and sideload it onto your **32-bit jailbroken Apple device**.

You can use whichever sideloading method you prefer.

### 2. Start the Server

Download the server script and run it on your PC.

**Important:** The server requires **ports 80 and 53**, so it must be run with administrator/root privileges:

* **Windows:** Run as Administrator
* **macOS/Linux:** Run with `sudo`

Once the server is running, keep the server window open while playing.

### 3. Find Your PC's IP Address

The server will display your PC's local IP address.

It should look something like:

```text
192.168.1.xxx
```

**You will need this IP address in the next step.**

### 4. Edit the iOS Hosts File

On your Apple device, open your file manager (**iFile** or **Filza**).

Navigate to the root of the filesystem, then open:

```text
/etc/hosts
```

Open the file in the text editor and add the following line at the very end:

```text
YOUR_PC_IP PlayDevLB-1049210432.us-west-2.elb.amazonaws.com
```

Replace `YOUR_PC_IP` with the IP address shown by the server.

For example, if the server shows your PC's IP as `192.168.1.123`:

```text
192.168.1.123 PlayDevLB-1049210432.us-west-2.elb.amazonaws.com
```

**The IP address above is only an example. Use the IP address shown by your server.**

Save the file.

### 5. Restart Your Device

Fully restart your Apple device after editing the hosts file.

### 6. Launch the Game

Make sure:

* Your PC and Apple device are connected to the **same Wi-Fi network**.
* The server is still running on your PC.
* The server window remains open.

Launch the game.

You should see the game's requests appearing in the server's console window as you play.

---

## Windows

> **Note:** The same setup also applies when running the game through the [Unity Editor](https://github.com/Dodonickey/WOE-Decompiled).

### 1. Launch the Game Once

Launch the **What On Earth Windows build**.

The first time the game runs, it will create an `ipconfig` file in its persistent data folder.

Once the file has been created, close the game.

### 2. Start the Server

Download and run the server script on your PC.

**Important:** The server requires **ports 80 and 53**, so it must be run with administrator privileges.

On Windows:

1. Open **Command Prompt** or **PowerShell** as Administrator.
2. Navigate to the folder containing the server script.
3. Run:

```text
python whatonearth_fake_server.py
```

The server will display your PC's local IP address.

**Keep the server window open while playing.**

> The game and server can technically be started in either order. The important part is that the `ipconfig` file has been created before you try to edit it.

### 3. Edit the `ipconfig` File

Open **File Explorer**.

Click the **address bar at the top**, paste the following path, and press **Enter**:

```text
%USERPROFILE%\AppData\LocalLow\riku_rakkola_gmail_com\WhatOnEarth
```

This will take you directly to the game's folder.

Find the file named:

```text
ipconfig
```

Right-click it and select **Open with → Notepad**.

The file will contain the original server URL.

Change the address to **the IP address shown by the server**, while keeping the `http://` part.

For example, if the server shows your PC's IP as `192.168.1.123`, change:

```text
http://PlayDevLB-1049210432.us-west-2.elb.amazonaws.com
```

to:

```text
http://192.168.1.123
```

**The IP address above is only an example. Use the IP address shown by your server.**

> **⚠️ Running the server and game on the same PC?**
>
> You can use:
>
> ```text
> http://127.0.0.1
> ```
>
> Using `127.0.0.1` provides an **offline experience**, as the game connects directly to the local server running on the same PC.

Save the file.

### 4. Relaunch the Game

Launch the game again.

The game will now connect to the local server instead of the original online server.

You should see the game's requests appearing in the server's console window as you play.

---

# Troubleshooting

## Stuck on "Connecting to Server"

Your PC's firewall may be blocking the connection.

Try temporarily disabling the firewall to confirm whether it is the cause.

If disabling the firewall fixes the problem, configure the firewall to allow the server instead of leaving it disabled.

## Port 80 Already in Use / IIS

On Windows, **IIS (Internet Information Services)** can use port 80 and conflict with the server.

To disable IIS:

1. Press **Win + R**.

2. Enter:

   ```text
   optionalfeatures
   ```

3. Press Enter.

4. Find **Internet Information Services**.

5. Uncheck it.

6. Apply the changes.

## `/etc/hosts` Changes Don't Seem to Work

The server also runs a small DNS server as a backup.

If editing `/etc/hosts` doesn't work even after fully restarting the device, you can instead configure your device to use your PC as its DNS server.

On your Apple device:

1. Open **Wi-Fi settings**.
2. Tap the **ⓘ** next to your network.
3. Open **DNS**.
4. Switch from **Automatic** to **Manual**.
5. Set your PC's IP address as the only DNS server.

For example:

```text
192.168.1.123
```

**Remember to switch DNS back to Automatic when you're finished playing.**

Otherwise, your device may lose internet access whenever the server isn't running.

---

# Saving Your Levels

Everything you save in-game is stored locally on your PC in a single SQLite database:

```text
whatonearth.db
```

This includes:

* Levels
* Highscores
* Likes
* Comments
* Screenshots

The database is created next to the server script.

You may also see these files:

```text
whatonearth.db-wal
whatonearth.db-shm
```

These are normal SQLite working files and are **not additional databases**.

Keep all of these files together next to the server script and reuse them when you play again. Your saved data will remain available.

**Do not delete these files if you want to keep your saved data.**

---

# Contact

If you need help or want to get in touch, contact me on Discord:

**@baptistewi92**
