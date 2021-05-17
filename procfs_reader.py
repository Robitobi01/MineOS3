import collections
import os
from binascii import b2a_qp
from grp import getgrgid
from pwd import getpwuid

_PROCFS_PATHS = ['/proc', '/usr/compat/linux/proc', '/system/lxproc']

for procfs in _PROCFS_PATHS:
    try:
        with open(os.path.join(procfs, 'uptime'), 'r') as procdump:
            _procfs = procfs
            break
    except IOError:
        continue


def pids():
    return set(int(pid) for pid in os.listdir(_procfs) if pid.isdigit())


def pid_cmdline():
    for pid in pids():
        try:
            with open(os.path.join(_procfs, str(pid), 'cmdline'), 'rb') as fh:
                cmdline = b2a_qp(fh.read()).decode('utf-8', 'ignore')
                cmdline = cmdline.replace('=00', ' ').replace('=\n', '').strip()
                yield pid, cmdline
        except IOError:
            continue


def entries(pid, page):
    with open(os.path.join(_procfs, str(pid), page), 'rb') as proc_status:
        for line in proc_status:
            split = b2a_qp(line).decode('utf-8', 'ignore').partition(':')
            yield split[0].strip(), split[2].strip()


def path_owner(path):
    st = os.stat(path)
    uid = st.st_uid
    return getpwuid(uid).pw_name


def pid_owner(pid):
    try:
        status_page = dict(entries(pid, 'status'))
    except IOError:
        raise IOError('Process %s does not exist' % pid)
    else:
        return getpwuid(int(status_page['Uid'].partition('\t')[0]))


def pid_group(pid):
    try:
        status_page = dict(entries(pid, 'status'))
    except IOError:
        raise IOError('Process %s does not exist' % pid)
    else:
        return getgrgid(int(status_page['Gid'].partition('\t')[0]))


def proc_uptime():
    raw = next(entries('', 'uptime'))[0]
    return tuple(float(v) for v in raw.split())


def proc_loadavg():
    raw = next(entries('', 'loadavg'))[0]
    return tuple(float(v) for v in raw.split()[:3])


def human_readable(n):
    symbols = ('K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
    prefix = {}
    for i, s in enumerate(symbols):
        prefix[s] = 1 << (i + 1) * 10
    for s in reversed(symbols):
        if n >= prefix[s]:
            value = float(n) / prefix[s]
            return '%.1f%s' % (value, s)
    return "%sB" % n


def disk_free(path):
    tuple_diskusage = collections.namedtuple('usage', 'total used free')
    st = os.statvfs(path)
    free = st.f_bavail * st.f_frsize
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return tuple_diskusage(human_readable(total), human_readable(used), human_readable(free))


def disk_usage(path):
    return sum(os.path.getsize(os.path.join(dirpath, filename))
               for dirpath, dirnames, filenames in os.walk(path)
               for filename in filenames)


def tail(f, lines=50):
    # https://stackoverflow.com/a/13790289
    lines_found = []
    block_counter = -1
    while len(lines_found) < lines:
        try:
            f.seek(block_counter * 4098, os.SEEK_END)
        except IOError:
            f.seek(0)
            lines_found = f.readlines()
            break
        lines_found = f.readlines()
        block_counter -= 1
    return lines_found[-lines:]
