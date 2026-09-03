This branch is a modification of the WhatOnEarth server so it can run locally on IOS device via terminal


## Game Screenshots:

<table>
  <tr>
    <td><img src="Screenshots/Screenshot 1.png" width="600" alt="Screenshot 1"></td>
    <td><img src="Screenshots/Screenshot 2.png" width="600" alt="Screenshot 2"></td>
    <td><img src="Screenshots/Screenshot 3.png" width="600" alt="Screenshot 3"></td>
    <td><img src="Screenshots/Screenshot 4.png" width="600" alt="Screenshot 4"></td>
  </tr>
</table>


## REQUIREMENTS

* The What On Earth game [Click this to download it (32 bit)](https://archive.org/download/traplight-whatonearth/WhatOnEarth_%28com.traplight.whatonearth%29_0.4.0.ipa)
* A jailbroken 32-bit Apple device with the following packages downloaded from cydia:

- Python (From Cydia/Telesphoreo repo)
- MobileTerminal/MTerminal/NewTerm2 (Get whichever one according to your IOS version: IOS 5 = MobileTerminal, IOS 8 = MTerminal, IOS 10 = NewTerm 2 or MTerminal)
- IFile/Filza (whichever one works)
- Some reading skills

## SETUP

1. Download the game and sideload it to your 32 bit IOS device (sideload it however you want)

2. Download the `woe_localhost.py` script from this repo and move it to `var/mobile` directory on the IOS device.

3. On your Apple device, open your file manager (iFile/Filza), go to the
root of the filesystem, then into the `etc` folder, and open the
`hosts` file with the text editor.

4. Tap Edit, add a new line and write the following:

   127.0.0.1 PlayDevLB-1049210432.us-west-2.elb.amazonaws.com


5. Save, then fully restart your Apple device.
   
6. Now open your terminal app on your IOS device (NewTerm2/MonileTerminal/MTerminal), and type su.
   
7. The terminal will ask you for a password. So enter the root password (usually alpine). If one right you should have root shell now.

8. Run the following command to start the server

   nohup python woe_localhost.py > /tmp/server.log 2>&1 &

9. If you did everything right you should see something in terminal like [somenumber] anothernumber (e.g. [1] 1767)

10. Now open WhatOnEarth and you should be able to bypass the connecting screen and play normally.

## TROUBLESHOOTING

* Stuck on "connecting to server" forever: Double check the `hosts` file in `/etc`. If you restarted your device or killed
  the python process, you will have to rerun the script so follow the setup from step 6.

* Port 80 already in use: ensure you are on root shell. you should see a # on the input space of terminal.

## SAVING YOUR LEVELS

Everything you save in-game (levels, highscores, likes, comments, screenshots)
is stored on your device in a single database file, "whatonearth.db", created next
to the script (so its located in `var/mobile`). You may also see "whatonearth.db-wal" 
and "whatonearth.db-shm" appear alongside it, these are normal SQLite working 
files, not extra data, and can be ignored. As long as you keep all of these 
next to the script and reuse it, everything you saved will still be there next time.
Just don't delete them.

## CONTACT

Contact me @baptistewi92 on Discord

