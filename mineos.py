#!/usr/bin/python3

import configparser
import os
import re
import subprocess
import tarfile
import time
import zipfile
from collections import namedtuple
from datetime import datetime
from distutils.dir_util import copy_tree
from distutils.spawn import find_executable
from functools import wraps
from getpass import getuser
from grp import getgrgid
from itertools import chain
from pwd import getpwuid, getpwnam
from shlex import split
from shutil import rmtree
from string import ascii_letters, digits
from xml.dom.minidom import parseString

import cherrypy
from mcstatus import MinecraftServer

import procfs_reader
from conf_reader import config_file


def sanitize(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        return_func = fn(self, *args, **kwargs)
        if None in list(self.previous_arguments.values()):
            raise RuntimeError('Missing value in %s: %s' % (fn.__name__, str(self.previous_arguments)))
        return return_func

    return wrapper


def server_exists(state):
    def dec(fn):
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            if (self.server_name in self.list_servers(self.base)) == state:
                fn(self, *args, **kwargs)
            else:
                if state:
                    raise RuntimeWarning('Ignoring {%s}: server not found "%s"' % (fn.__name__, self.server_name))
                else:
                    raise RuntimeWarning('Ignoring {%s}: server already exists "%s"' % (fn.__name__, self.server_name))

        return wrapper

    return dec


def server_up(up):
    def dec(fn):
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            if self.up == up:
                fn(self, *args, **kwargs)
            else:
                if up:
                    raise RuntimeError('Server must be running to perform this action.')
                else:
                    raise RuntimeError('Server may not be running when performing this action.')

        return wrapper

    return dec


class mc(object):
    NICE_VALUE = 10
    DEFAULT_PATHS = {
        'servers': 'servers',
        'backup': 'backup',
        'archive': 'archive',
        'import': 'import'
    }
    BINARY_PATHS = {
        'rdiff-backup': find_executable('rdiff-backup'),
        'rsync': find_executable('rsync'),
        'screen': find_executable('screen'),
        'java': find_executable('java'),
        'nice': find_executable('nice'),
        'tar': find_executable('tar'),
        'kill': find_executable('kill'),
        'wget': find_executable('wget'),
    }

    def __init__(self, server_name, owner=None, base_directory=None):

        self._server_name = self.valid_server_name(server_name)
        self._owner = owner or getuser()
        self._base_directory = base_directory or cherrypy.config['misc.base_directory']

        self._set_environment()
        try:
            self._load_config(generate_missing=True)
        except RuntimeError:
            pass

    def _set_environment(self):
        self.server_properties = None
        self.server_config = None

        self.env = {
            'cwd': os.path.join(self.base, self.DEFAULT_PATHS['servers'], self.server_name),
            'bwd': os.path.join(self.base, self.DEFAULT_PATHS['backup'], self.server_name),
            'awd': os.path.join(self.base, self.DEFAULT_PATHS['archive'], self.server_name),
        }

        self.env.update({
            'sp': os.path.join(self.env['cwd'], 'server.properties'),
            'sc': os.path.join(self.env['cwd'], 'server.config'),
            'sp_backup': os.path.join(self.env['bwd'], 'server.properties'),
            'sc_backup': os.path.join(self.env['bwd'], 'server.config')
        })

        self.env['log'] = os.path.join(self.env['cwd'], os.path.join('logs', 'latest.log'))

    def _load_config(self, load_backup=False, generate_missing=False):
        def load_sp():
            self.server_properties = config_file(self.env['sp_backup']) if load_backup else config_file(self.env['sp'])
            self.server_properties.use_sections(False)
            return self.server_properties[:]

        def load_sc():
            self.server_config = config_file(self.env['sc_backup']) if load_backup else config_file(self.env['sc'])
            return self.server_config[:]

        load_sc()
        load_sp()

        if generate_missing and not load_backup:
            if self.server_properties[:] and self.server_config[:]:
                pass
            elif self.server_properties[:] and not self.server_config[:]:
                self._create_sc()
                load_sc()
            elif self.server_config[:] and not self.server_properties[:]:
                self._create_sp()
                load_sp()
            else:
                raise RuntimeError('No config files found: server.properties or server.config')

    @server_exists(True)
    def _create_sp(self, startup_values={}):
        defaults = {
            'server-port': 25565,
            'max-players': 20,
            'level-seed': '',
            'gamemode': 0,
            'difficulty': 1,
            'level-type': 'DEFAULT',
            'level-name': 'world',
            'max-build-height': 256,
            'generate-structures': 'false',
            'generator-settings': '',
            'server-ip': '0.0.0.0',
        }

        sanitize_integers = {'server-port', 'max-players', 'gamemode', 'difficulty'}

        for option in sanitize_integers:
            try:
                defaults[option] = int(startup_values[option])
            except (KeyError, ValueError):
                continue

        for option, value in startup_values.items():
            if option not in sanitize_integers:
                defaults[option] = value

        self._command_direct('touch %s' % self.env['sp'], self.env['cwd'])
        with config_file(self.env['sp']) as sp:
            sp.use_sections(False)
            for key, value in defaults.items():
                sp[key] = str(value)

    def _create_sc(self, startup_values={}):
        defaults = {
            'crontabs': {
                'archive_interval': '',
                'backup_interval': '',
                'restart_interval': '',
            },
            'onreboot': {
                'start': True,
            },
            'java': {
                'java_tweaks': '',
                'java_xmx': 256,
                'java_xms': 256,
                'jarfile': 'minecraft_server.jar'
            }
        }

        sanitize_integers = {('java', 'java_xmx'), ('java', 'java_xms'), ('crontabs', 'archive_interval'),
                             ('crontabs', 'backup_interval'), ('crontabs', 'restart_interval')}

        d = defaults.copy()
        d.update(startup_values)

        for section, option in sanitize_integers:
            try:
                d[section][option] = int(startup_values[section][option])
            except (KeyError, ValueError):
                d[section][option] = defaults[section][option]

        self._command_direct('touch %s' % self.env['sc'], self.env['cwd'])
        with config_file(self.env['sc']) as sc:
            for section in d:
                sc.add_section(section)
                for option in d[section]:
                    sc[section:option] = str(d[section][option])

    @server_exists(False)
    def create(self, sc={}, sp={}):
        for d in ('cwd', 'bwd', 'awd'):
            self._make_directory(self.env[d], True)

        sc = sc if type(sc) is dict else {}
        sp = sp if type(sp) is dict else {}
        self._create_sc(sc)
        self._create_sp(sp)
        self._load_config()

    @server_exists(True)
    def modify_config(self, option, value, section=None):
        if section:
            with self.server_config as sc:
                sc[section:option] = value
        else:
            with self.server_properties as sp:
                sp[option] = value

    @server_exists(True)
    @server_up(False)
    def start(self):
        if self.port in [s.port for s in self.list_ports_up()]:
            if (self.port, self.ip_address) in [(s.port, s.ip_address) for s in self.list_ports_up()]:
                raise RuntimeError('Couldn\'t start server, server already up at %s:%s.' % (self.ip_address, self.port))
            elif self.ip_address == '0.0.0.0':
                raise RuntimeError(
                    'Couldn\'t start server, can not listen on (0.0.0.0) if port %s already in use.' % self.port)
            elif any(s for s in self.list_ports_up() if s.ip_address == '0.0.0.0'):
                raise RuntimeError('Couldn\'t start server, server already listening on ip address (0.0.0.0).')

        self._load_config(generate_missing=True)
        self._command_direct(self.command_start, self.env['cwd'])

    @server_exists(True)
    @server_up(True)
    def kill(self):
        self._command_direct(self.command_kill, self.env['cwd'])

    @server_exists(True)
    @server_up(True)
    def commit(self):
        self._command_stuff('save-all')

    @server_exists(True)
    @server_up(True)
    def stop_and_backup(self):
        last_mirror = self.list_increments().current_mirror

        self._command_stuff('stop')
        while self.up:
            time.sleep(1)

        self._command_direct(self.command_backup, self.env['cwd'])

        while last_mirror == self.list_increments().current_mirror:
            time.sleep(1)

    @server_exists(True)
    @server_up(True)
    def stop(self):
        self._command_stuff('stop')

    @server_exists(True)
    def archive(self):
        self._make_directory(self.env['awd'])
        if self.up:
            self._command_stuff('save-off')
            try:
                self._command_direct(self.command_archive, self.env['cwd'])
            finally:
                self._command_stuff('save-on')
        else:
            self._command_direct(self.command_archive, self.env['cwd'])

    @server_exists(True)
    def backup(self):
        self._make_directory(self.env['bwd'])
        if self.up:
            self._command_stuff('save-off')
            self._command_stuff('save-all')
            self._command_direct(self.command_backup, self.env['cwd'])
            self._command_stuff('save-on')
        else:
            self._command_direct(self.command_backup, self.env['cwd'])

    @server_exists(True)
    @server_up(False)
    def restore(self, step='now', force=False):
        self._load_config(load_backup=True)

        if self.server_properties or self.server_config:
            force = '--force' if force else ''

            self._make_directory(self.env['cwd'])
            try:
                self._command_direct(self.command_restore(step, force), self.env['cwd'])
            except subprocess.CalledProcessError as e:
                raise RuntimeError(e.output)

            self._load_config(generate_missing=True)
        else:
            raise RuntimeError('Ignoring command {restore}; Unable to locate backup')

    @server_exists(False)
    def import_server(self, path, filename):
        filepath = os.path.join(path, filename)

        if tarfile.is_tarfile(filepath):
            archive_ = tarfile.open(filepath, mode='r')
            members_ = archive_.getnames()
            prefix_ = os.path.commonprefix(members_)
        elif zipfile.is_zipfile(filepath):
            archive_ = zipfile.ZipFile(filepath, 'r')
            members_ = archive_.namelist()
            prefix_ = os.path.commonprefix(members_)
        else:
            raise NotImplementedError('Ignoring command {import_server};'
                                      'archive file must be compressed tar or zip')

        if any(f for f in members_ if f.startswith('/') or '../' in f):
            raise RuntimeError('Ignoring command {import_server};'
                               'archive contains files with absolute path or ../')

        archive_.extractall(self.env['cwd'])
        archive_.close()

        if not os.path.samefile(self.env['cwd'], os.path.join(self.env['cwd'], prefix_)):
            prefixed_dir = os.path.join(self.env['cwd'], prefix_)
            copy_tree(prefixed_dir, self.env['cwd'])

            rmtree(prefixed_dir)

        os.chmod(self.env['cwd'], 0o775)

        try:
            self._load_config(generate_missing=True)
        except RuntimeError:
            rmtree(self.env['cwd'])
            raise

    @server_exists(True)
    def prune(self, step):
        self._command_direct(self.command_prune(step), self.env['bwd'])

    def prune_archives(self, filename):
        self._command_direct(self.command_delete_files(filename), self.env['awd'])

    @server_exists(True)
    @server_up(False)
    def delete_server(self):
        self._command_direct(self.command_delete_server, self.env['pwd'])

    @server_exists(True)
    def accept_eula(self):
        with open(os.path.join(self.env['cwd'], 'eula.txt'), 'w') as eula:
            eula.write(
                '#Automatically accepted EULA by MineOS')
            eula.write('\neula=true')
            eula.write('\n')

    @staticmethod
    def server_version(filepath, guess=''):
        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                files = zf.namelist()
                for internal_path in [r'META-INF/maven/org.bukkit/craftbukkit/pom.xml',
                                      r'META-INF/maven/mcpc/mcpc-plus-legacy/pom.xml',
                                      r'META-INF/maven/mcpc/mcpc-plus/pom.xml',
                                      r'META-INF/maven/org.spigotmc/spigot/pom.xml',
                                      r'META-INF/maven/net.md-5/bungeecord-api/pom.xml']:
                    if internal_path in files:
                        for tag in ['minecraft.version', 'version']:
                            try:
                                xml = parseString(zf.read(internal_path))
                                return xml.getElementsByTagName(tag)[0].firstChild.nodeValue
                            except (IndexError, KeyError, AttributeError):
                                continue
        except (IOError, zipfile.BadZipfile):
            return ''
        else:
            match = re.match('https://s3.amazonaws.com/Minecraft.Download/versions/([^/]+)', guess)
            try:
                return match.group(1)
            except AttributeError:
                return ''

    # actual command execution methods

    @staticmethod
    def _demote(user_uid, user_gid):
        def set_ids():
            os.umask(2)
            os.setgid(user_gid)
            os.setuid(user_uid)

        return set_ids

    def _command_direct(self, command, working_directory):
        return subprocess.check_output(split(command), cwd=working_directory, stderr=subprocess.STDOUT,
                                       preexec_fn=self._demote(self.owner.pw_uid, self.owner.pw_gid))

    @server_exists(True)
    @server_up(True)
    def _command_stuff(self, stuff_text):
        command = """%s -S %d -p 0 -X eval 'stuff "%s\012"'""" % (
            self.BINARY_PATHS['screen'], self.screen_pid, stuff_text)
        subprocess.check_call(split(command), preexec_fn=self._demote(self.owner.pw_uid, self.owner.pw_gid))

    # validation checks

    @staticmethod
    def valid_server_name(name):
        valid_chars = set('%s%s_.' % (ascii_letters, digits))

        if not name:
            raise ValueError('Servername must be a string at least 1 length')
        elif any(c for c in name if c not in valid_chars):
            raise ValueError('Servername contains invalid characters')
        elif name.startswith('.'):
            raise ValueError('Servername may not start with "."')
        return name

    @staticmethod
    def valid_filename(filename):
        valid_chars = set('%s%s-_.' % (ascii_letters, digits))

        if not filename:
            raise ValueError('Filename is empty')
        elif any(c for c in filename if c not in valid_chars):
            raise ValueError('Disallowed characters in filename "%s"' % filename)
        elif filename.startswith('.'):
            raise ValueError('Files should not be hidden: "%s"' % filename)
        return filename

    @property
    def server_name(self):
        return self._server_name

    @property
    def base(self):
        return self._base_directory

    @property
    def owner(self):
        return getpwnam(self._owner)

    @property
    def up(self):
        return any(s.server_name == self.server_name for s in self.list_servers_up())

    @property
    def java_pid(self):
        for server, java_pid, screen_pid in self.list_servers_up():
            if self.server_name == server:
                return java_pid
        else:
            return None

    @property
    def screen_pid(self):
        for server, java_pid, screen_pid in self.list_servers_up():
            if self.server_name == server:
                return screen_pid
        else:
            return None

    @property
    def jarfile(self):
        try:
            return self.server_config['java':'jarfile']
        except KeyError:
            return None

    @property
    def port(self):
        try:
            return int(self.server_properties['server-port'])
        except (ValueError, KeyError):
            ''' KeyError: server-port option does not exist
                ValueError: value is not an integer
                exception Note: when value is absent or not an int, vanilla
                adds/replaces the value in server.properties to 25565'''
            return 25565

    @property
    def ip_address(self):
        return self.server_properties['server-ip'] or '0.0.0.0'

    @property
    def memory(self):
        def bytesto(num, to, bsize=1024):
            a = {'k': 1, 'm': 2, 'g': 3, 't': 4, 'p': 5, 'e': 6}
            r = float(num)
            for i in range(a[to]):
                r = r / bsize
            return r

        try:
            mem_str = dict(procfs_reader.entries(self.java_pid, 'status'))['VmRSS']
            mem = int(mem_str.split()[0]) * 1024
            return '%s MB' % bytesto(mem, 'm')
        except IOError:
            return '0'

    @property
    def ping(self):
        server_ping = namedtuple('ping',
                                 ['protocol_version', 'server_version', 'motd', 'players_online', 'max_players'])

        error_ping = server_ping(None, None, self.server_properties['motd'::''], '0',
                                 self.server_properties['max-players'])

        if self.up:
            try:
                server = MinecraftServer(self.ip_address, self.port)
                status = server.status()
                return server_ping(status.version.protocol, status.version.name, status.description,
                                   status.players.online, status.players.max)
            except:
                return error_ping
        else:
            if self.server_name in self.list_servers(self.base):
                return server_ping(None, None, self.server_properties['motd'::''],
                                   '0', self.server_properties['max-players'])
            else:
                raise RuntimeWarning('Server not found "%s"' % self.server_name)

    @property
    def sp(self):
        return self.server_properties[:]

    @property
    def sc(self):
        return self.server_config[:]

    @property
    def eula(self):
        try:
            cf = config_file(os.path.join(self.env['cwd'], 'eula.txt'))
            return cf['eula']
        except (SyntaxError, KeyError):
            return None

    # shell command constructor properties

    @property
    def previous_arguments(self):
        try:
            return self._previous_arguments
        except AttributeError:
            return {}

    @property
    @sanitize
    def command_start(self):
        required_arguments = {
            'screen_name': 'mc-%s' % self.server_name,
            'screen': self.BINARY_PATHS['screen'],
            'java': self.BINARY_PATHS['java'],
            'java_xmx': self.server_config['java':'java_xmx'],
            'java_xms': self.server_config['java':'java_xmx'],
            'java_tweaks': self.server_config['java':'java_tweaks':''],
            'java_debug': '',
            'jarfile': self.server_config['java':'jarfile'],
            'jar_args': 'nogui'
        }

        try:
            java_xms = self.server_config.getint('java', 'java_xms')
            if 0 < java_xms <= int(required_arguments['java_xmx']):
                required_arguments['java_xms'] = java_xms
        except (configparser.NoOptionError, ValueError):
            pass

        try:
            if self.server_config.getboolean('java', 'java_debug'):
                required_arguments['java_debug'] = ' '.join([
                    '-verbose:gc',
                    '-XX:+PrintGCTimeStamps',
                    '-XX:+PrintGCDetails',
                    '-Xloggc:{0}'.format(os.path.join(self.env['cwd'], 'java_gc.log'))
                ])
        except (configparser.NoOptionError, ValueError):
            pass

        self._previous_arguments = required_arguments
        return '%(screen)s -dmS %(screen_name)s ' \
               '%(java)s -server %(java_debug)s -Xmx%(java_xmx)sM -Xms%(java_xms)sM %(java_tweaks)s ' \
               '-jar %(jarfile)s %(jar_args)s' % required_arguments

    @property
    @sanitize
    def command_debug(self):
        command = self.command_start
        match = re.match(r'^.+ mc-.+? (.+)', command)
        return match.group(1)

    @property
    @sanitize
    def command_archive(self):
        required_arguments = {
            'nice': self.BINARY_PATHS['nice'],
            'tar': self.BINARY_PATHS['tar'],
            'nice_value': self.NICE_VALUE,
            'archive_filename': os.path.join(self.env['awd'], 'server-%s_%s.tar.gz' % (
                self.server_name, time.strftime("%Y-%m-%d_%H:%M:%S"))), 'cwd': '.'
        }
        self._previous_arguments = required_arguments
        return '%(nice)s -n %(nice_value)s %(tar)s czf %(archive_filename)s %(cwd)s' % required_arguments

    @property
    @sanitize
    def command_backup(self):
        required_arguments = {
            'nice': self.BINARY_PATHS['nice'],
            'nice_value': self.NICE_VALUE,
            'rdiff': self.BINARY_PATHS['rdiff-backup'],
            'cwd': self.env['cwd'],
            'bwd': self.env['bwd']
        }
        self._previous_arguments = required_arguments
        return '%(nice)s -n %(nice_value)s %(rdiff)s %(cwd)s/ %(bwd)s' % required_arguments

    @property
    @sanitize
    def command_kill(self):
        required_arguments = {
            'kill': self.BINARY_PATHS['kill'],
            'pid': self.screen_pid
        }
        self._previous_arguments = required_arguments
        return '%(kill)s %(pid)s' % required_arguments

    @sanitize
    def command_restore(self, step, force):
        required_arguments = {
            'rdiff': self.BINARY_PATHS['rdiff-backup'],
            'force': '--force' if force else '',
            'step': step,
            'bwd': self.env['bwd'],
            'cwd': self.env['cwd']
        }
        self._previous_arguments = required_arguments
        return '%(rdiff)s %(force)s --restore-as-of %(step)s %(bwd)s %(cwd)s' % required_arguments

    @sanitize
    def command_prune(self, step):
        required_arguments = {
            'rdiff': self.BINARY_PATHS['rdiff-backup'],
            'step': step,
            'bwd': self.env['bwd']
        }
        if type(required_arguments['step']) is int:
            required_arguments['step'] = '%sB' % required_arguments['step']

        self._previous_arguments = required_arguments
        return '%(rdiff)s --force --remove-older-than %(step)s %(bwd)s' % required_arguments

    @property
    @sanitize
    def command_list_increments(self):
        required_arguments = {
            'rdiff': self.BINARY_PATHS['rdiff-backup'],
            'bwd': self.env['bwd']
        }
        self._previous_arguments = required_arguments
        return '%(rdiff)s --list-increments %(bwd)s' % required_arguments

    @property
    @sanitize
    def command_list_increment_sizes(self):
        required_arguments = {
            'rdiff': self.BINARY_PATHS['rdiff-backup'],
            'bwd': self.env['bwd']
        }
        self._previous_arguments = required_arguments
        return '%(rdiff)s --list-increment-sizes %(bwd)s' % required_arguments

    @sanitize
    def command_delete_files(self, files):
        required_arguments = {
            'files': files,
        }
        self._previous_arguments = required_arguments
        return 'rm -- %(files)s' % required_arguments

    @property
    @sanitize
    def command_delete_server(self):
        required_arguments = {
            'live': self.env['cwd'],
            'backup': self.env['bwd'],
            'archive': self.env['awd']
        }
        self._previous_arguments = required_arguments
        return 'rm -rf -- %(live)s %(backup)s %(archive)s' % required_arguments

    @sanitize
    def command_chown(self, user, path):
        required_arguments = {
            'user': user,
            'path': path
        }
        self._previous_arguments = required_arguments
        return 'chown -R %(user)s %(path)s' % required_arguments

    @sanitize
    def command_chgrp(self, group, path):
        required_arguments = {
            'group': group,
            'path': path
        }
        self._previous_arguments = required_arguments
        return 'chgrp -R %(group)s %(path)s' % required_arguments

    # generator expressions

    @classmethod
    def list_servers(cls, base_directory):
        return list(set(chain(
            cls._list_subdirs(os.path.join(base_directory, cls.DEFAULT_PATHS['servers'])),
            cls._list_subdirs(os.path.join(base_directory, cls.DEFAULT_PATHS['backup']))
        )))

    @classmethod
    def list_ports_up(cls):
        instance_connection = namedtuple('instance_connection', 'server_name port ip_address')
        for name, java, screen in cls.list_servers_up():
            instance = cls(name, base_directory=cls.base)
            yield instance_connection(name, instance.port, instance.ip_address)

    def list_increments(self):
        incs = namedtuple('increments', 'current_mirror increments')

        try:
            output = self._command_direct(self.command_list_increments, self.env['bwd'])
            assert output is not None
        except (subprocess.CalledProcessError, AssertionError):
            return incs('', [])

        output_list = output.split('\n')
        increment_string = output_list.pop(0)
        output_list.pop()  # empty newline throwaway
        current_string = output_list.pop()
        timestamp = current_string.partition(':')[-1].strip()
        return incs(timestamp, [d.strip() for d in output_list])

    def list_increment_sizes(self):
        incs = namedtuple('increments', 'step timestamp increment_size cumulative_size')

        try:
            output = self._command_direct(self.command_list_increment_sizes, self.env['bwd'])
            assert output is not None
        except (subprocess.CalledProcessError, AssertionError):
            return incs('', '', 0, 0)

        regex = re.compile(r'^(\w.*?) {3,}(.*?) {2,}([^ ]+ \w*)')
        count = 0
        try:
            output = output.decode('utf-8', 'ignore')
        except (UnicodeDecodeError, AttributeError):
            pass

        for line in output.split('\n'):
            hits = regex.match(line)
            try:
                yield incs('%sB' % count, hits.group(1), hits.group(2), hits.group(3))
                count += 1
            except AttributeError:
                continue

    def list_archives(self):
        arcs = namedtuple('archives', 'filename size timestamp friendly_timestamp path')

        for i in self._list_files(self.env['awd']):
            info = os.stat(os.path.join(self.env['awd'], i))
            yield arcs(i, info.st_size, int(info.st_mtime), time.ctime(info.st_mtime), self.env['awd'])

    @classmethod
    def list_servers_up(cls):
        pids = dict(procfs_reader.pid_cmdline())
        instance_pids = namedtuple('instance_pids', 'server_name java_pid screen_pid')

        def name_jarfile():
            for cmdline in pids.values():
                if 'screen' in cmdline.lower():
                    serv = re.search(r'SCREEN.*?mc-([\w._]+).*?-jar ([\w._/]+)', cmdline, re.IGNORECASE)
                    try:
                        yield serv.groups()[0], serv.groups()[1]
                    except AttributeError:
                        continue

        for name, jarfile in name_jarfile():
            java = None
            screen = None

            for pid, cmdline in pids.items():
                if '-jar' in cmdline:
                    if 'screen' in cmdline.lower() and 'mc-%s' % name in cmdline:
                        screen = int(pid)
                    elif jarfile in cmdline:
                        java = int(pid)
                    if java and screen:
                        break
            yield instance_pids(name, java, screen)

    def list_last_loglines(self, lines=100):
        try:
            with open(self.env['log'], 'r') as log:
                return procfs_reader.tail(log, int(lines))
        except IOError:
            pass
        return []

    @classmethod
    def list_servers_to_act(cls, action, base_directory):
        hits = []
        msm = cls.minutes_since_midnight()

        section_option = ('crontabs', '%s_interval' % action)

        for i in cls.list_servers(base_directory):
            try:
                path_ = os.path.join(base_directory, cls.DEFAULT_PATHS['servers'], i)
                owner_ = procfs_reader.path_owner(path_)
                instance = cls(i, owner_, base_directory)

                interval = instance.server_config.getint(section_option[0], section_option[1])
                if msm == 0:
                    hits.append(i)
                elif msm % interval == 0:
                    hits.append(i)
            except:
                continue

        return hits

    @classmethod
    def list_servers_start_at_boot(cls, base_directory):
        hits = []
        for i in cls.list_servers(base_directory):
            try:
                path_ = os.path.join(base_directory, cls.DEFAULT_PATHS['servers'], i)
                owner_ = procfs_reader.path_owner(path_)
                instance = cls(i, owner_, base_directory)
                if instance.server_config.getboolean('onreboot', 'start'):
                    hits.append(i)
            except:
                pass
        return hits

    @classmethod
    def list_servers_restore_at_boot(cls, base_directory):
        hits = []
        for i in cls.list_servers(base_directory):
            try:
                path_ = os.path.join(base_directory, cls.DEFAULT_PATHS['backup'], i)
                owner_ = procfs_reader.path_owner(path_)
                instance = cls(i, owner_, base_directory)
                instance._load_config(load_backup=True)
                if instance.server_config.getboolean('onreboot'):
                    hits.append(i)
            except:
                pass
        return hits

    @staticmethod
    def _mtime(filepath):
        try:
            return time.ctime(os.path.getmtime(filepath))
        except os.error:
            return ''

    # filesystem functions

    def _make_directory(self, path, do_raise=False):
        try:
            os.makedirs(path)
        except OSError:
            if do_raise: raise
        else:
            os.chown(path, self.owner.pw_uid, self.owner.pw_gid)
            os.chmod(path, 0o775)

    @staticmethod
    def has_ownership(username, path):
        st = os.stat(path)
        uid = st.st_uid
        gid = st.st_gid

        owner_user = getpwuid(uid)
        owner_group = getgrgid(gid)
        user_info = getpwnam(username)

        if user_info.pw_uid == uid or \
                user_info.pw_gid == gid or \
                username in owner_group.gr_mem:
            return owner_user.pw_name
        elif username == 'root':
            return owner_user.pw_name
        else:
            raise OSError("User '%s' does not have permissions on %s" % (username, path))

    @classmethod
    def has_server_rights(cls, username, server_name, base_directory):
        has_rights = False
        for d in ('servers', 'backup'):
            try:
                path_ = os.path.join(base_directory, cls.DEFAULT_PATHS[d], server_name)
                has_rights = cls.has_ownership(username, path_)
                break
            except OSError:
                pass
        return has_rights

    def chown(self, user):
        for d in ('cwd', 'bwd', 'awd'):
            self._make_directory(self.env[d])
            self._command_direct(self.command_chown(user, self.env[d]), self.env[d])

    def chgrp(self, group):
        for d in ('cwd', 'bwd', 'awd'):
            self._make_directory(self.env[d])
            self._command_direct(self.command_chgrp(group, self.env[d]), self.env[d])

    @staticmethod
    def _list_subdirs(directory):
        try:
            return next(os.walk(directory))[1]
        except StopIteration:
            return []

    @staticmethod
    def _list_files(directory):
        try:
            return next(os.walk(directory))[2]
        except StopIteration:
            return []

    @classmethod
    def _make_skeleton(cls, base_directory):
        for d in cls.DEFAULT_PATHS:
            try:
                os.makedirs(os.path.join(base_directory, d))
            except OSError:
                pass

    @staticmethod
    def minutes_since_midnight():
        now = datetime.now()
        return int(((now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()) / 60)
