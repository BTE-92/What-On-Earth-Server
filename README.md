This lets you play the "What On Earth" beta (an early build of Big Bang
Racing, by Traplight) offline.



How it works: this runs a small fake server on your PC that pretends to
be the game's original server, and redirects the game's requests to it.

https://archive.org/details/traplight-whatonearth

## REQUIREMENTS



* A jailbroken 32-bit Apple device with a file manager installed (iFile or Filza)
* A PC with Python installed
* Both devices connected to the same Wi-Fi network



## SETUP



1. Download the server script and run it on your PC.

   IMPORTANT: it must be run as Administrator (Windows) or with sudo
(macOS/Linux). The script needs port 80.



1. Once running, the script prints your PC's local IP address (looks
like 192.168.1.xxx). Keep this window open the whole time you play.
2. On your Apple device, open your file manager (iFile/Filza), go to the
root of the filesystem, then into the `etc` folder, and open the
`hosts` file with the text editor.
3. Tap Edit, and add a new line at the very end (just the IP of your PC and the
hostname. This should look like this :



   YOUR\_PC\_IP PlayDevLB-1049210432.us-west-2.elb.amazonaws.com



1. Save, then fully restart your Apple device.
2. Make sure your PC and Apple device are still on the same Wi-Fi
network, and that the script is still running on the PC.
3. Launch the game. You should see requests show up in the script's
console window as you play.



## TROUBLESHOOTING



* Stuck on "connecting to server" forever: your PC's firewall is
probably blocking the connection. Try temporarily disabling it to
confirm.

* Port 80 already in use / IIS: on Windows, IIS (a built-in web server)
also uses port 80 by default and can conflict with the script. Disable
it via Win+R > type "optionalfeatures" > uncheck "Internet Information
Services".

* Editing /etc/hosts doesn't seem to work even after a full restart:
the script also runs a small DNS server as a backup. Instead of
relying on the hosts file, go to Wi-Fi settings on your device > (i)
next to your network > DNS > switch to Manual > set your PC's IP as
the only DNS server. Remember to switch it back to Automatic when
you're done playing, or your device will lose internet access
whenever the script isn't running.



## SAVING YOUR LEVELS



Everything you save in-game (levels, highscores, likes, comments, screenshots)
is stored on your PC in a single database file, "whatonearth.db", created next
to the script. You may also see "whatonearth.db-wal" and "whatonearth.db-shm"
appear alongside it — these are normal SQLite working files, not extra data,
and can be ignored. As long as you keep all of these next to the script and
reuse it, everything you saved will still be there next time. Just don't delete
them.

## HAVE FUN !

Have fun !

