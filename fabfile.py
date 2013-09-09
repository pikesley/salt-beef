#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A fabfile to initialise a whole saltstack from nothing, using Rackspace.

The use case for this file is either:
    * You have never used rackspace before, and wish to spin up a good and
      salty herd of server cattle.
    * Your entire stack has just died, somehow, and you need to spin it back up

A great man (@pikesley) once said one should treat one's servers like cattle,
not pets.

So, you get a salt master, and then you can give birth to more servers via the
salt master. If you've got a salt stack set of conf files, then it will go and
ensure that the defined minions are configured too - but you can also just
launch a load of random servers as you wish.

Terminology:
    * herd: As in, herding cattle. It means 'use' really, or 'with',
      so herd:myserver will go and get the credentials for that myserver and be
      ready to use it
    * birth: Give birth to a new server, if you have a salt master already, it
      will use salt-cloud commands to bring it up and generate a profile for it
      so you can keep a record of what servers are provisioned (for future when
      it all goes wrong). If you don't have a salt master, it will just use
      pyrax directly to spin up a new box, without salt or anything.
    * euthanise: Kill, destroy, etc sounded a bit harsh for cattle. This takes
      the server (chosen with `herd`) out of action, completely, forever.
    * cattle: Bring up a whole load of servers using a cloud.profiles file,
      which is the same file to which profiles made with salt-cloud are written
      on the salt master and copied locally.

Typical usage (from scratch):
    $ fab connect:user=xxx make_saltmaster
    ...
    $ fab connect:user=xxx birth:someserver,4096
    ...
    $ fab connect:user=xxx herd:someserver shell
    ...
    $ fab connect:user=xxx herd:mysaltmaster euthanise:wait=True herd:someserver euthanise
    ...etc!

"""
import os
import sys
import time

from StringIO import StringIO
from uuid import uuid4
from subprocess import call

from fabric.api import task, put, run, env, local, cd, require, abort, get
from fabric.network import prompt_for_password
from fabric.colors import red, green, white
from fabric.contrib.console import confirm
from fabric.operations import prompt
from fabric.tasks import execute

import pyrax
import yaml
pyrax.set_setting("identity_type", "rackspace")

try:
    from settings import DOMAIN, NAMING_SCHEME
except ImportError:
    print red("You need to define a settings file with DOMAIN and "
              "NAMING_SCHEME")
    sys.exit(1)


SALT_CLOUD_TEMPLATE = {
    'apikey': None,
    'compute_name': 'cloudServersOpenStack',
    'compute_region': 'LON',
    'identity_url': 'https://identity.api.rackspacecloud.com/v2.0/tokens',
    'minion': {'master': None},
    'protocol': 'ipv4',
    'provider': 'openstack',
    'tenant': None,
    'user': None,
}


def refresh_boxen():
    """Just a shortcut to get a list of servers, by name."""
    cs = pyrax.cloudservers
    server_dict = dict([(box.name, box) for box in cs.list()])
    env.boxen = server_dict


@task
def connect(user):
    """Get an authenticated pyrax client, list of current boxen"""
    env.rackspace_user = user

    if not os.environ.get('RACKSPACE_API_KEY', False):
        key = prompt_for_password("Rackspace API key for {0}".format(user))
    else:
        key = os.environ['RACKSPACE_API_KEY']
    env.rackspace_api_key = key

    if not os.environ.get('RACKSPACE_TENANT_ID', False):
        tenant = prompt(
            "Rackspace Tenant ID (account #) for {0}".format(user))
    else:
        tenant = os.environ['RACKSPACE_TENANT_ID']
    env.rackspace_tenant_id = tenant

    pyrax.set_credentials(user, key)

    refresh_boxen()


@task
def cattle(conf_path):
    """Use a json configuration file to spawn a load of boxes."""
    pass


@task
def birth(name, ram_or_disk_size=None, wait=False, no_profile=False):
    """Make a new box called <name> with the wanted <ram_or_disk_size>.

    This may use the direct method (i.e. via pyrax) or it may use salt-cloud,
    depending on whether or not there is a saltmaster present, if that
    saltmaster knows what profile we are talking about, if we are using a
    profile.

    :param str name: (friendly) name of server.
    :param int ram_or_disk_size: size of either RAM wanted or disk.
    :param bool wait: set True to hold off returning until the box is up
    :param bool no_profile: set True to make a 'throwaway' box, and not use
                            salt-cloud, or install salt.

    """
    saltmaster = NAMING_SCHEME['saltmaster']
    profiles = yaml.load(open('cloud.profiles', 'r')) or {}
    if saltmaster in env.boxen:  # then salt-cloud can do this
        herd(name=saltmaster)  # use the saltmaster to do stuff
        execute(get, remote_path='/etc/salt/cloud.profiles', local_path='cloud.profiles')
        profiles = yaml.load(open('cloud.profiles', 'r')) or {}
        if name in profiles:  # salt-cloud knows how to do this!
            execute(run, command="salt-cloud -p {0} {0}".format(name))
            herd(name)
            brand()
            return True
        else:
            print red("Unknown profile, creating new one.")

    cs = pyrax.cloudservers
    ubuntu = [
        img for img in cs.images.list()
        if "Ubuntu 12.04" in img.name
    ][0]
    flavour = [
        flav for flav in cs.flavors.list()
        if float(flav.ram) == float(ram_or_disk_size)
        or float(flav.disk) == float(ram_or_disk_size)
    ][0]

    profiles[name] = {
        'provider': 'rackspace-conf-{0}'.format(env.rackspace_user),
        'size': str(flavour.name),
        'image': str(ubuntu.name),
    }
    with open('cloud.profiles', 'w') as _profiles_file:
        _profiles_file.write(yaml.dump(profiles))

    if saltmaster in env.boxen:
        # update the profiles on the salt master
        execute(
            put,
            local_path='cloud.profiles',
            remote_path='/etc/salt/cloud.profiles'
        )

    if no_profile or not saltmaster in env.boxen:
        # make the box
        env.box = cs.servers.create(name, ubuntu.id, flavour.id)
        print green(
            "Ok, made server {0}:{1}".format(env.box.name, env.box.id))
        print green("Admin password (last chance!):"), red(env.box.adminPass)
        if wait:
            print green("Waiting until server is ready...")
            pyrax.utils.wait_for_build(env.box)
            herd(name, newborn=True)
            print green("Ok, server is ready!")
    else:  # we want profile, so we probably want this done via salt-cloud.
        execute(run, command="salt-cloud -p {0} {0}".format(name))
        herd(name)
    brand()


@task
def brand(aliases=None):
    """Add this server to the DNS records.

    Assumes you have set up the DNS entry for the configured DOMAIN setting on
    rackspace already.

    :param list aliases: list of alternative records to CNAME to base name.

    You brand a cow with a number, right? Or these days, graffiti it on.

    """
    aliases = aliases or []
    if isinstance(aliases, basestring):
        aliases = [a.strip() for a in aliases.split(',')]
    if not getattr(env, 'box', False):
        abort("Must select a server to add to DNS first!")
    record_name = ".".join([env.box.name, DOMAIN])
    dns = pyrax.cloud_dns
    domains = dict((d.name, d) for d in dns.list())
    domain = domains[DOMAIN]
    records = dict((r.name, r) for r in domain.list_records())

    def _manage_name(_type, name, data=None):
        data = data or env.box_public_ips[4]
        if name in records:
            record = records[name]
            if record.type != _type:
                print green("Replacing record {0}...".format(name))
                record.delete()
                del(records[name])
                _manage_name(_type, name, data)
            else:
                record.update(data=data)
                print green("Updated record for {0}".format(name))
        else:
            domain.add_record({
                "type": _type,
                "name": name,
                "data": data,
                "ttl": 300
            })
            print green("Created record for {0}".format(name))
    _manage_name('A', record_name)
    for alias in aliases:
        _manage_name('CNAME', ".".join([alias, DOMAIN]), record_name)


@task
def herd(name, newborn=False):
    """Do something with the box named <name>"""
    refresh_boxen()
    if not newborn:  # keep the one set in env.box, to get the admin pass
        env.box = env.boxen[name]

    # get the IPs:
    ips = env.box.addresses['public']
    env.box_public_ips = dict([(ip['version'], ip['addr']) for ip in ips])
    host = 'root@{0}:22'.format(env.box_public_ips[4])
    env.hosts = [host]

    # since we might not have auth on this box, we just change the admin pass
    # every time - this means that even if provisioning of SSH keys fails, we
    # can still get access and it also means that fabric doesn't need to know
    # anything!
    env.passwords = getattr(env, 'passwords', {})
    if not host in env.passwords:
        password = getattr(env.box, 'adminPass', False)
        if not password:
            password = str(uuid4())[:12]
            env.box.change_password(password)
            print white("Changed password of server to:"), red(password)
            time.sleep(10)  # takes a while for change_password to work it seems
        env.passwords[host] = password
    else:
        env.password = env.passwords[host]

    print green(
        "Ok, found server {0}:{1}".format(env.box.name, env.box.id))


@task
def euthanise(wait=False):
    """Trash the server named <name>. Returns whether or not it deleted."""
    box = env.box
    name = box.name
    if confirm(
            red("Really delete server {0}:{1}???".format(name, box.id)),
            default=False):
        box.delete()
        if wait:
            print green("Waiting for server to go away...")
            cs = pyrax.cloudservers
            while name in env.boxen:
                env.boxen = dict([(box.name, box) for box in cs.list()])
                time.sleep(1)
        else:
            del(env.boxen[name])
        print red("Deleted.")
        return True
    else:
        print green("Ok, not deleting!")
        return False


@task
def bootstrap(master=False):
    """Provision salt-cloud on the bare box. See saltstack/salt-bootstrap."""
    run("apt-get -q update")
    run("apt-get -q --yes install git python-dev build-essential python-pip sshpass")
    if master:
        run("curl -L http://bootstrap.saltstack.org | "
            "sh -s -- -M -N git develop")
        run("pip install psutil apache-libcloud")
        run("pip install git+https://github.com/saltstack/salt-cloud.git"
            "#egg=salt_cloud")
        conf = SALT_CLOUD_TEMPLATE
        conf.update({
            'apikey': env.rackspace_api_key,
            'minion': {'master': str(env.box_public_ips[4])},
            'tenant': str(env.rackspace_tenant_id),
            'user': env.rackspace_user,
        })
        conf = {'rackspace-conf-{0}'.format(env.rackspace_user): conf}
        put(StringIO(yaml.dump(conf)), '/etc/salt/cloud.providers')
        # dummy a profiles file
        put('cloud.profiles', '/etc/salt/cloud.profiles')

    else:  # minion
        run("python -c 'import urllib; print urllib.urlopen("
            "\"http://bootstrap.saltstack.org\").read()' | "
            "sh -s -- git develop")
    execute(shell)


@task
def season():
    """Sends the local salt states/pillars to the salt master."""
    local('tar -czf /tmp/salt.tar.gz salt')
    local('tar -czf /tmp/pillar.tar.gz pillar')
    put('/tmp/salt.tar.gz', '/tmp/salt.tar.gz')
    put('/tmp/pillar.tar.gz', '/tmp/pillar.tar.gz')
    with cd('/tmp/'):
        run('tar -xzf salt.tar.gz -C /srv/')
        run('tar -xzf pillar.tar.gz -C /srv/')


@task
def pasture(name, size, medium):
    """Create volume storage for a server - attaches to server if herded.

    :param str name: The name of this storage
    :param str size: size in GB of storage (100->1024)
    :param str medium: one of: SSD, SATA

    """
    cbs = pyrax.cloud_blockstorage
    cbs.create(name=name, size=int(size), volume_type=medium)

    if env.box:
        execute(graze, name=name, mkfs=True)


@task
def graze(name, dev=None, mkfs=False):
    """Attaches a server to a storage.

    :param str name: name of storage to attach this server
    :param str dev: name of block device to attach, defaults to /dev/xvdb
    :param bool mkfs: set True to mkfs on the (new) device.

    """
    require('box', provided_by=[herd, birth])
    dev = dev or '/dev/xvdb'
    cbs = pyrax.cloud_blockstorage
    storages = cbs.list()
    for vol in storages:
        if vol.name == name:
            mnt = "/mnt/{0}".format(name)
            execute(run, command='mkdir -p {0}'.format(mnt))
            vol.attach_to_instance(env.box, mountpoint=dev)
            print green(
                "Attached storage {0} to {1} on {2}".format(name, env.box, dev)
            )
            print green("Waiting for volume to attach...")
            pyrax.utils.wait_until(vol, 'status', ['in-use'])
            print green("Attached!")
            if mkfs:
                execute(run, command="mkfs.ext4 {0}".format(dev))
                print green("Made fs (ext4) on {0}".format(dev))
            execute(run, command="mount -t ext4 {0} {1}".format(dev, mnt))
            print green("Mounted {0} at {1}".format(dev, mnt))
            return


@task
def make_saltmaster():
    """Spin up and provision a Salt Master on an OpenStack enabled service."""

    name = NAMING_SCHEME['saltmaster']
    try:
        herd(name)
    except KeyError:
        birth(name, '512', wait=True)
    else:
        print red("Saltmaster ('{0}') already exists!".format(name))
        if euthanise(wait=True):  # kill the old one and make a new one
            birth(name, '512', wait=True)
            time.sleep(10)  # takes a little while for SSH to come up...
    time.sleep(5)  # oddly, need more time here...
    execute(bootstrap, master=True)
    execute(season)


@task
def shell():
    """Call SSH to get a shell.

    Don't use fabric's open_shell because it's laggy and disrupts muscle
    memory.

    """
    print green("Password is currently:"), red(env.passwords[env.hosts[0]])
    call('ssh {0}'.format(env.hosts[0].split(':')[0]), shell=True)
