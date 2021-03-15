![MineOS3](https://raw.githubusercontent.com/Robitobi01/MineOS3/master/html/img/logo-login.png)

###Custom Minecraft hosting and management scripts.


The Web Interface is now styled in a darker theme.

There are some features in the original MineOS scripts that I didn't bother testing or that I simply removed.
For example profiles, md5 hashes, git hash checks, legacy mc versions and more. I switched to using the default pam library and replaced the custom server list ping code by mcstatus.

The root path was changed from / to /mineos to allow reverse proxying MineOS3 on a normal webserver as a subdirectory


**Required libraries:**
- mcstatus

**Required tools:**
- screen
- rsync
- rdiff-backup

#

All credit goes to William Dizon "hexparrot" for the original scripts | [MineOS](https://github.com/hexparrot/mineos)