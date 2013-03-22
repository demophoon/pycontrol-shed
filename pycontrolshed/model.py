# Copyright (C) 2011 Tim Freund and contributors.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from functools import wraps
from pycontrol import pycontrol
import pycontrolshed
import socket

# In [1]: route_domains = bigip.Networking.RouteDomain.get_list()
# In [2]: route_domains
# Out[2]: [2220L]

def partitioned(f):
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        partition = kwargs.get('partition', None)
        if partition:
            orig_partition = self.bigip.Management.Partition.get_active_partition()
            self.bigip.Management.Partition.set_active_partition(partition)
            rc = f(self, *args, **kwargs)
            self.bigip.Management.Partition.set_active_partition(orig_partition)
            return rc
        else:
            return f(self, *args, **kwargs)
    return wrapper
        
class NodeAssistant(object):
    def __init__(self, bigip):
        self.bigip = bigip

    def disable(self, nodes, partition=None):
        self.__enable_disable_nodes(nodes, 'STATE_DISABLED', partition=partition)

    def enable(self, nodes, partition=None):
        self.__enable_disable_nodes(nodes, 'STATE_ENABLED', partition=partition)

    @partitioned
    def __enable_disable_nodes(self, nodes, target_state, partition=None):
        if isinstance(nodes, basestring):
            nodes = [nodes]

        targets = []
        states = []
        for node in nodes:
            targets.append(self.bigip.host_to_node(node))
            states.append(target_state)

        self.bigip.LocalLB.NodeAddress.set_session_enabled_state(node_addresses=targets,
                                                                 states=states)
        return self.status(nodes)

    @partitioned
    def status(self, nodes, partition=None):
        if isinstance(nodes, basestring):
            nodes = [nodes]

        targets = []
        for node in nodes:
            targets.append(self.bigip.host_to_node(node))
        statuses = self.bigip.LocalLB.NodeAddress.get_session_enabled_state(node_addresses=targets)

        rc = []
        for node, status in zip(targets, statuses):
            rc.append({'node': node,
                       'fqdn': self.bigip.node_to_host(node),
                       'status': status})
        return rc

class PyCtrlShedBIGIP(pycontrol.BIGIP):
    def __init__(self, *args, **kwargs):
        pycontrol.BIGIP.__init__(self, *args, **kwargs)
        self.nodes = NodeAssistant(self)
        self._active_partition = None

    @property
    def active_partition(self):
        if self._active_partition:
            return self._active_partition
        self._active_partition = str(self.Management.Partition.get_active_partition())
        return self._active_partition

    @active_partition.setter
    def active_partition(self, partition):
        self.Management.Partition.set_active_partition(partition)
        self._active_partition = partition
        self._route_domains = self.Networking.RouteDomain.get_list()

    def host_to_node(self, host):
        node = socket.gethostbyname(host)
        if (len(self.route_domains) == 1) and self.route_domains[0] != 0:
            node += "%%%d" % self.route_domains[0]
        return node

    def node_to_ip(self, node):
        if node.count('%'):
            return node.split('%')[0]
        return node

    def node_to_host(self, node):
        return socket.getfqdn(self.node_to_ip(node))

    @property
    def route_domains(self):
        if hasattr(self, '_route_domains'):
            return self._route_domains
        self._route_domains = self.Networking.RouteDomain.get_list()
        return self._route_domains
        
class Environment(object):
    def __init__(self, name, **kwargs):
        self.name = name
        self.hosts = []
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        if name == 'password':
            return pycontrolshed.get_password(self.name, self.username)
        else:
            return self.__getattribute__(name)

    def __setattr__(self, name, value):
        if name == 'hosts':
            if isinstance(value, str) or isinstance(value, unicode):
                object.__setattr__(self, name, [host.strip() for host in value.split(',')])
            else:
                object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)

    def configure(self, config):
        for k, v in config.items(self.name):
            setattr(self, k, v)

    @property
    def active_bigip_connection(self):
        for host in self.hosts:
            bigip = self.connect_to_bigip(host)
            if 'FAILOVER_STATE_ACTIVE' == bigip.System.Failover.get_failover_state():
                return bigip
        raise Exception('No active BIGIP devices were found in this environment (%s)' % self.name)

    def connect_to_bigip(self, host, wsdls=['LocalLB.NodeAddress', 'LocalLB.Pool', 'LocalLB.PoolMember', 
                                            'LocalLB.VirtualAddress', 'LocalLB.VirtualServer', 
                                            'Management.Partition', 'Networking.RouteDomain', 
                                            'System.Failover']):
        bigip = PyCtrlShedBIGIP(host,
                                self.username,
                                self.password,
                                fromurl=True,
                                wsdls=wsdls)
        return bigip

