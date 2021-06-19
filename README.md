![MineOS3](https://raw.githubusercontent.com/Robitobi01/MineOS3/master/html/img/logo-login.png)


### Custom Minecraft hosting and management scripts.


The web interface is now styled in a darker and more simple theme. It's also not using the boxed layout anymore. 

There are some features in the original MineOS scripts that I didn't bother fixing or simply removed for simplicity's sake. The profiles were useless to me and got completely removed. 
Some other removed things include md5 hashes, git hash checks, legacy mc versions, different server types and more.

I switched to using the default Python 3 pam library and replaced the custom server list ping code by mcstatus.

The CSS color themes now got completely removed because they were fully replaced by the darker theme.

The new config option `misc.web_root = "/"` was added to allow reverse proxying MineOS3 as a sub-directory. All the absolute paths got adjusted to either use relative paths or the new config value instead.

**Setup**

1. `git clone https://github.com/Robitobi01/MineOS3 /usr/mineos3` 
2. Adjust config file as needed, then `cp mineos.conf /etc/mineos.conf`
3. `ln -s mineos_console.py /usr/local/bin/mineos`
4. `cp .init/* /etc/systemd/system/`
5. `systemctl enable mineos.service && systemctl enable minecraft.service`

**Required libraries:**
- cherrypy
- mcstatus

**Required tools:**
- rdiff-backup 
- rsync
- screen
- java
- nice
- tar
- kill
- wget
  
**If [needrestart](https://github.com/liske/needrestart) is used:**
- In the config file /etc/needrestart/needrestart.conf remove comments around the list $nrconf{blacklist_rc}
- Add the regex line `qr(^mine),` to the $nrconf{blacklist_rc} list

#

All credit goes to William Dizon "hexparrot" for the original scripts | [MineOS](https://github.com/hexparrot/mineos)