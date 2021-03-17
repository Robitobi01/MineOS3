#!/usr/bin/python3

import os
import sys
from argparse import ArgumentParser
from subprocess import CalledProcessError
from time import sleep

import cherrypy
from cherrypy.process.plugins import Daemonizer, PIDFile

import auth
import mounts
from mineos import mc
from procfs_reader import path_owner


class cron(cherrypy.process.plugins.SimplePlugin):
    def __init__(self, base_directory, commit_delay):
        self.base_directory = base_directory
        try:
            self.commit_delay = int(commit_delay)
        except (ValueError, TypeError):
            self.commit_delay = 10

    def check_interval(self):

        crons = []

        for action in ('restart', 'backup', 'archive'):
            for server in mc.list_servers_to_act(action, self.base_directory):
                crons.append((action, server))

        for server in set(s for a, s in crons):
            path_ = os.path.join(self.base_directory, mc.DEFAULT_PATHS['servers'], server)
            instance = mc(server, path_owner(path_), self.base_directory)

            cherrypy.log('[%s] commit' % server)
            try:
                instance._command_stuff('save-off')
                instance.commit()
            except RuntimeError:
                pass
            else:
                cherrypy.log('[%s] commit command received by process; sleeping %ssec' % (server, self.commit_delay))
                sleep(self.commit_delay)

        for action, server in crons:
            path_ = os.path.join(self.base_directory, mc.DEFAULT_PATHS['servers'], server)
            instance = mc(server, path_owner(path_), self.base_directory)

            if action == 'restart':
                cherrypy.log('[%s] stop' % server)
                try:
                    instance._command_stuff('stop')
                except RuntimeError:
                    pass
                else:
                    cherrypy.log('[%s] stop command received by process; sleeping %ssec' % (server, self.commit_delay))
                    sleep(self.commit_delay)
            elif action in ('backup', 'archive'):
                cherrypy.log('[%s] %s (Server Up: %s)' % (server, action, instance.up))
                try:
                    getattr(instance, action)()
                except CalledProcessError as e:
                    cherrypy.log('[%s] %s exception: returncode %s' % (server, action, e.returncode))
                    cherrypy.log(e.output)
                except RuntimeError:
                    cherrypy.log(
                        '[%s] %s exception: server state changed since beginning of %s.' % (server, action, action))
                    cherrypy.log('[%s] %s (Server Up: %s)' % (server, action, instance.up))
                    cherrypy.log('You may need to increase mineos.conf => server.commit_delay')
                else:
                    cherrypy.log('[%s] %s return code reports success; sleeping 3sec' % (server, action))
                    sleep(3)

        for action, server in crons:
            path_ = os.path.join(self.base_directory, mc.DEFAULT_PATHS['servers'], server)
            instance = mc(server, path_owner(path_), self.base_directory)

            if action == 'restart':
                if instance.up:
                    cherrypy.log('[%s] extra delay; sleeping %s seconds' % (server, self.commit_delay))
                    sleep(self.commit_delay)

                cherrypy.log('[%s] start' % server)
                try:
                    instance.start()
                except RuntimeError:
                    pass
                else:
                    cherrypy.log('[%s] started; sleeping %s seconds' % (server, self.commit_delay))
                    sleep(self.commit_delay)


if __name__ == "__main__":

    parser = ArgumentParser(description='MineOS web user interface service')
    parser.add_argument('-p',
                        dest='port',
                        help='the port to listen on',
                        default=8080)
    parser.add_argument('-i',
                        dest='ip_address',
                        help='the ip address to listen on',
                        default='0.0.0.0')
    parser.add_argument('-d',
                        dest='base_directory',
                        help='the base of the mc file structure',
                        default='/var/games/minecraft')
    parser.add_argument('--daemon',
                        action='store_true',
                        default=False,
                        help='run server as a daemon')
    parser.add_argument('--nopid',
                        action='store_false',
                        default='/var/run/mineos.log',
                        help='do not use PID file')
    parser.add_argument('--http',
                        action='store_true',
                        default=False,
                        help='use HTTP over HTTPS')
    parser.add_argument('-c',
                        dest='config_file',
                        help='use external default configuration file',
                        default=None)
    args = parser.parse_args()

    ################

    html_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'html')
    cherrypy.config['misc.html_directory'] = html_dir

    cherrypy.config.update({
        'tools.sessions.on': True,
        'tools.auth.on': True
    })

    if args.config_file:
        cherrypy.config.update(args.config_file)
        base_dir = cherrypy.config['misc.base_directory']

        if not cherrypy.config['misc.require_https']:
            cherrypy.config.update({
                'server.ssl_module': None,
                'server.ssl_certificate': None,
                'server.ssl_private_key': None,
                'server.ssl_certificate_chain': None,
                'server.ssl_ca_certificate': None
            })

        if cherrypy.config['misc.server_as_daemon']:

            Daemonizer(cherrypy.engine).subscribe()
            cherrypy.config.update({'log.screen': False})
        else:
            cherrypy.config.update({'log.screen': True})
            print(cherrypy.config)

        if cherrypy.config['misc.pid_file']:
            PIDFile(cherrypy.engine, cherrypy.config['misc.pid_file']).subscribe()
    else:
        base_dir = args.base_directory or os.path.expanduser("~")

        logfile = "/var/log/mineos.log"
        try:
            with open(logfile, 'a'):
                pass
        except IOError:
            logfile = os.path.join(base_dir, 'mineos.log')

        global_conf = {
            'server.socket_host': args.ip_address,
            'server.socket_port': int(args.port),
            'log.screen': not args.daemon,
            'log.error_file': logfile,
            'misc.base_directory': base_dir
        }

        if not args.http:  # use https instead
            if os.path.isfile('/etc/ssl/certs/mineos.crt') and \
                    os.path.isfile('/etc/ssl/certs/mineos.key'):
                ssl = {
                    'server.ssl_module': 'builtin',
                    'server.ssl_certificate': '/etc/ssl/certs/mineos.crt',
                    'server.ssl_private_key': '/etc/ssl/certs/mineos.key'
                }
            else:
                ssl = {
                    'server.ssl_module': 'builtin',
                    'server.ssl_certificate': 'mineos.crt',
                    'server.ssl_private_key': 'mineos.key'
                }
            global_conf.update(ssl)

        if args.daemon:
            Daemonizer(cherrypy.engine).subscribe()

        if args.nopid:

            PIDFile(cherrypy.engine, args.nopid).subscribe()

            if os.path.isfile(args.nopid):
                print('MineOS instance already running (PID found)')
                sys.exit(1)

        cherrypy.config.update(global_conf)

    if base_dir == '/':
        raise RuntimeError('Cannot start server at filesystem root.')
    else:
        mc._make_skeleton(base_dir)

    web_root = cherrypy.config['misc.web_root']
    if web_root[-1:] != '/':  # fix trailing slash
        web_root += '/'
    if web_root[:1] != '/':  # fix leading slash
        web_root = '/' + web_root
    cherrypy.config['misc.web_root'] = web_root

    root_conf = {
        '/assets': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(html_dir, 'assets')
        },
        '/css': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(html_dir, 'css')
        },
        '/img': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(html_dir, 'img')
        },
        '/js': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(html_dir, 'js')
        },
        '/favicon.png':
            {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(html_dir, 'favicon.png')
            }
    }

    empty_conf = {
        '/': {}
    }
    try:
        cron_instance = cron(base_dir, cherrypy.config['server.commit_delay'])
    except KeyError:
        cron_instance = cron(base_dir, 10)
    finally:
        minute_crontab = cherrypy.process.plugins.Monitor(cherrypy.engine, cron_instance.check_interval, 60)
        minute_crontab.subscribe()

    cherrypy.tree.mount(mounts.Root(), web_root, config=root_conf)
    cherrypy.tree.mount(mounts.ViewModel(), web_root + 'vm', config=empty_conf)
    cherrypy.tree.mount(auth.AuthController(), web_root + 'auth', config=empty_conf)
    # os.system('sudo /usr/local/bin/mineos start')
    cherrypy.engine.start()
    cherrypy.engine.block()
