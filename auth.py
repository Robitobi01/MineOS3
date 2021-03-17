import os
from crypt import crypt
from pwd import getpwnam
from spwd import getspnam

import cherrypy
import pam
from cherrypy.lib.static import serve_file

SESSION_KEY = '_cp_username'


def check_credentials(username, password):
    try:
        enc_pwd = getspnam(username)[1]
    except KeyError:
        raise OSError("user '%s' not found" % username)
    else:
        if enc_pwd in ['NP', '!', '', None]:
            raise OSError("user '%s' has no password set" % username)
        elif enc_pwd in ['LK', '*']:
            raise OSError('account is locked')
        elif enc_pwd == "!!":
            raise OSError('password is expired')

        if crypt(password, enc_pwd) == enc_pwd:
            return True
        else:
            raise OSError('incorrect password')


def unix_authenticate(username, password):
    cryptedpasswd = getpwnam(username)[1]
    if cryptedpasswd:
        if cryptedpasswd == 'x' or cryptedpasswd == '*':
            raise NotImplementedError("Shadow passwords not supported")
        return crypt(password, cryptedpasswd) == cryptedpasswd
    else:
        return False


def check_auth(*args, **kwargs):
    conditions = cherrypy.request.config.get('auth.require', None)
    if conditions is not None:
        username = cherrypy.session.get(SESSION_KEY)
        if username:
            cherrypy.request.login = username
            for condition in conditions:
                # A condition is just a callable that returns true or false
                if not condition():
                    raise cherrypy.HTTPRedirect(cherrypy.config['misc.web_root'] + 'auth/login')
        else:
            raise cherrypy.HTTPRedirect(cherrypy.config['misc.web_root'] + 'auth/login')


cherrypy.tools.auth = cherrypy.Tool('before_handler', check_auth)


def require(*conditions):
    def decorate(f):
        if not hasattr(f, '_cp_config'):
            f._cp_config = dict()
        if 'auth.require' not in f._cp_config:
            f._cp_config['auth.require'] = []
        f._cp_config['auth.require'].extend(conditions)
        return f

    return decorate


# Controller to provide login and logout actions

class AuthController(object):
    def __init__(self):
        self.html_directory = cherrypy.config['misc.html_directory']

    def on_login(self, username):
        """Called on successful login"""

    def on_logout(self, username):
        """Called on logout"""

    def get_loginform(self):
        return serve_file(os.path.join(self.html_directory, 'login.html'))

    @cherrypy.expose
    def login(self, username=None, password=None, hide=None, from_page=None):
        if not username or not password:
            return self.get_loginform()

        validated = False
        try:
            validated = check_credentials(username, password)
        except OSError:
            validated = pam.authenticate(username, password)
        except ImportError:
            validated = unix_authenticate(username, password)
        finally:
            if validated:
                cherrypy.session.regenerate()
                cherrypy.session[SESSION_KEY] = cherrypy.request.login = username
                self.on_login(username)
                raise cherrypy.HTTPRedirect(cherrypy.config['misc.web_root'])
            else:
                return self.get_loginform()

    @cherrypy.expose
    def logout(self):
        sess = cherrypy.session
        username = sess.get(SESSION_KEY, None)
        sess[SESSION_KEY] = None
        if username:
            cherrypy.request.login = None
            self.on_logout(username)
        raise cherrypy.HTTPRedirect(cherrypy.config['misc.web_root'] + 'index')
