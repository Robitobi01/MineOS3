![MineOS3](https://raw.githubusercontent.com/Robitobi01/MineOS3/master/html/img/logo-login.png)


### Custom Minecraft hosting and management scripts.


The web interface is now styled in a darker and more simple theme. It's also not using the boxed layout anymore. 

There are some features in the original MineOS scripts that I didn't bother fixing or simply removed for simplicity's sake. The profiles were useless to me and got completely removed. 
Some other removed things include md5 hashes, git hash checks, legacy mc versions, different server types and more.

I switched to using the default Python 3 pam library and replaced the custom server list ping code by mcstatus.

The CSS color themes now got completely removed because they were fully replaced by the darker theme.

The new config option `misc.web_root = "/"` was added to allow reverse proxying MineOS3 as a sub-directory. All the absolute paths got adjusted to either use relative paths or the new config value instead.


**Required libraries:**
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
  
#

All credit goes to William Dizon "hexparrot" for the original scripts | [MineOS](https://github.com/hexparrot/mineos)