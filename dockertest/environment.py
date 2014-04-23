#!/usr/bin/env python

"""
Low-level/standalone host-environment handling/checking utilities/classes/data

:Note: This module must _NOT_ depend on anything in dockertest package or
       in autotest!
"""

import os
import os.path
import subprocess
import re
import time
import tempfile

class AllGoodBase(object):

    """
    Abstract class representing aggregate True/False value from callables
    """

    #: Mapping of callable name to instance
    callables = None

    #: Mapping of callable name to True/False value
    results = None

    #: Mapping of callable name to detailed results
    details = None

    #: Iterable of callable names to bypass
    skip = None


    # __init__ left abstract on purpose

    def __instattrs__(self, skip=None):
        """
        Override class variables with empty instance values

        :param skip: Iterable of callable names to not run
        """
        self.callables = {}
        self.results = {}
        self.details = {}
        if skip is None:
            self.skip = []
        else:
            self.skip = skip

    def __nonzero__(self):

        """
        Implement truth value testing and for the built-in operation bool()
        """

        return False not in self.results.values()

    def __str__(self):

        """
        Make results of individual checkers accessible in human-readable format.
        """
        goods = [name for (name, result) in self.results.items() if result]
        bads = [name for (name, result) in self.results.items() if not result]
        if self:  # use self.__nonzero__()
            msg = "All Good: %s" % goods
        else:
            msg = "Good: %s; Not Good: %s; " % (goods, bads)
            msg += "Details:"
            dlst = [' (%s, %s)' % (name, self.detail_str(name))
                    for name in bads]
            msg += ';'.join(dlst)
        return msg

    def detail_str(self, name):

        """
        Convert details value for name into string

        :param name: Name possibly in details.keys()
        """

        return str(self.details.get(name, "No details"))

    def callable_args(self, name):

        """
        Return dictionary of arguments to pass through to each callable

        :param name: Name of callable to return args for
        :return: Dictionary of arguments
        """

        del name  # keep pylint happy
        return dict()

    def call_callables(self):

        """
        Call all instances in callables not in skip, storing results
        """

        _results = {}
        for name, call in self.callables.items():
            if callable(call) and name not in self.skip:
                _results[name] = call(**self.callable_args(name))
        self.results.update(self.prepare_results(_results))

    def prepare_results(self, results):

        """
        Called to process results into instance results and details attributes

        :param results: Dict-like of output from callables, keyed by name
        :returns: Dict-like for assignment to instance results attribute.
        """

        # In case call_callables() overridden but this method is not
        return dict(results)

class EnvCheck(AllGoodBase):

    """
    Represent aggregate result of calling all executables in envcheckdir
    """

    #: Dict-like containing configuration options
    config = None

    #: Skip configuration key for reference
    envcheck_skip_option = 'envcheck_skip'

    #: Base path from which check scripts run
    envcheckdir = None

    def __init__(self, config, envcheckdir):

        """
        Run checks, define result attrs or raise

        :param config: Dict-like containing configuration options
        :param envcheckdir: Absolute path to directory holding scripts
        """

        self.config = config
        self.envcheckdir = envcheckdir
        envcheck_skip = self.config.get(self.envcheck_skip_option)
        # Don't support content less than 'x,'
        if envcheck_skip is None or len(envcheck_skip.strip()) < 2:
            self.__instattrs__()
        else:
            self.__instattrs__(envcheck_skip.strip().split(','))
        for (dirpath, _, filenames) in os.walk(envcheckdir, followlinks=True):
            for filename in filenames:
                fullpath = os.path.join(dirpath, filename)
                relpath = fullpath.replace(self.envcheckdir, '')
                if relpath.startswith('/'):
                    relpath = relpath[1:]
                # Don't add non-executable files
                if not os.access(fullpath, os.R_OK | os.X_OK):
                    continue
                self.callables[relpath] = subprocess.Popen
        self.call_callables()

    def prepare_results(self, results):
        dct = {}
        # Wait for all subprocesses to finish
        for relpath, popen in results.items():
            (stdoutdata, stderrdata) = popen.communicate()
            dct[relpath] = popen.returncode == 0
            self.details[relpath] = {'exit':popen.returncode,
                                     'stdout':stdoutdata,
                                     'stderr':stderrdata}
        return dct

    def callable_args(self, name):
        fullpath = os.path.join(self.envcheckdir, name)
        # Arguments to subprocess.Popen for script "name"
        return {'args':fullpath, 'bufsize':1, 'stdout':subprocess.PIPE,
                'stderr':subprocess.PIPE, 'close_fds':True, 'shell':True,
                'env':self.config}


class Event(object):
    slots = ("type", "req_id", "cont_id", "status")

    def __init__(self, ev_type, req_id, cont_id, status=None):
        self.type = ev_type
        self.req_id = req_id
        self.cont_id = cont_id
        self.status = status

    def __str__(self):
        return "%s %s %s %s" % (self.type, self.req_id, self.cont_id,
                                self.status)

    def __eq__(self, other):
        if isinstance(other, Event):
            return self.__eq_event__(other)
        elif isinstance(other, str):
            return self.__eq_str__(other)
        elif isinstance(other, dict):
            return self.__eq_dict__(other)
        else:
            raise ValueError("Can't compare to %s" % other)

    def __eq_event__(self, other):
        for slot in self.slots:
            if getattr(self, slot) != getattr(other, slot):
                return False
        return True

    def __eq_str__(self, other):
        if self.type == other:
            return True
        else:
            return False

    def __eq_dict__(self, other):
        for slot in self.slots:
            try:
                value = other[slot]
                if value != getattr(self, slot):
                    return False
            except KeyError:
                pass
        return True

    def __ne__(self, other):
        return not self.__eq__(other)


class JournalGrabber(object):
    """
    This class returns the `cat` (without time) output of journalctl since
    the last self.reset() time. Additionally you can specify the desired unit.
    """
    def __init__(self):
        ctl = subprocess.Popen("LANG=C journalctl --version", shell=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, close_fds=True)
        wait_for(lambda: ctl.poll() is not None, 5, step=0)
        if ctl.poll() is None:
            ctl.kill()
            raise EnvironmentError("journalctl --version hanged.")
        elif ctl.poll() is not 0:
            raise EnvironmentError("journalctl --version returned non-zero"
                                   " check you have journalctl available.")
        self.start = None

    def reset(self):
        self.start = time.strftime("%Y-%m-%d %H:%M:%S")

    def get(self, unit=None):
        cmd = "LANG=C journalctl -o cat --no-pager"
        if unit:
            cmd += " -u " + str(unit)
        if self.start:
            cmd += " --since '%s'" % self.start
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, close_fds=True)
        wait_for(lambda: process.poll() is not None, 5, step=0)
        if process.poll() is None:
            process.kill()
            raise StopIteration("%s didn't finish in 5s" % cmd)
        elif process.poll() is not 0:
            return ""
        return process.stdout.read()


class MessagesGrabber(object):
    """
    This class reads the /var/log/messages(-like) file and returns the stripped
    messages since the last self.reset(). You can also grep for certain unit.
    """
    def __init__(self, messages=None):
        if messages is None:
            messages = '/var/log/messages'
        self.messages = open(messages, 'r', 0)
        self._service_offset = self._find_offset(self.messages)

    def _find_offset(self, messages):
        for line in messages.xreadlines():
            for keyword in (' kernel: ', ' docker: ', ' NetworkManager: '):
                idx = line.find(keyword)
                if idx > 0:
                    return idx + 1
        else:
            raise ValueError("Can't find /var/log/messages offset of services")

    def reset(self):
        self.messages.seek(0, 2)    # Move to the end

    def get(self, unit=None):
        out = []
        if unit:
            unit += ": "    # journalctl name is followed by ": " in messages
        self.messages.seek(0, 1)    # Without the seek my python won't read
        for line in self.messages.xreadlines():
            line = line[self._service_offset:]
            if unit and line[:len(unit)] != unit:
                continue
            out.append(line.split(':', 1)[1].strip())
        return "\n".join(out)


class EventHandler(object):
    START = ("+start", "+allocate_interface", "-start", "-allocate_interface")
    RUN = ("+create", "-create",) + START
    FINISH = ("+release_interface", "+inspect", "-release_interface",
              "-inspect",)
    LIST = ("+containers", "-containers",)
    REMOVE = ("+container_delete", "-container_delete",)
    re = re.compile(r'\[[^\|]+\|(\w{8})\] ([+-])job (\w+)\((\w*[^\)]*)\)( = '
                    r'\w+ \((\d+)\))?\n?')

    def __init__(self, messages=None):
        if messages is None:
            try:    # try to use journalctl
                self.messages = JournalGrabber()
            except EnvironmentError:    # failback to messages parser
                messages = '/var/log/messages'
        if messages:
            self.messages = MessagesGrabber(messages)
        self.messages.reset()

    def _parse_line(self, line):
        res = self.re.match(line)
        if not res:
            return  # Line doesn't match job prescription
        res = res.groups()
        # +-$job_name, req_id, container_id, result/None
        return Event(res[1] + res[2], res[0], res[3], res[5] or None)

    def get_events(self):
        events = []
        for line in self.messages.get('docker').splitlines():
            event = self._parse_line(line)
            if event:
                events.append(event)
        self.messages.reset()
        return events

    def wait_for(self, events, timeout=None):
        """
        :return: List of matching registered events
        """
        if not events:  # No events, just wait and move to the end of the file
            if timeout:
                time.sleep(timeout)
            return self.get_events()
        handled = {}
        if timeout is None:
            condition = lambda: True
        else:
            endtime = time.time() + timeout
            condition = lambda: time.time() < endtime
        simple_events = []
        specific_events = []
        for event in events:
            if isinstance(event, Event):
                specific_events.append(event)
            elif isinstance(event, dict) and event.get('req_id'):
                specific_events.append(event)
            else:
                simple_events.append(event)
        _specific_events = [False] * len(specific_events)
        current = []
        while condition():
            for event in self.get_events():
                if event in specific_events:    # In specific events
                    _specific_events[specific_events.index(event)] = True
                elif event in simple_events:
                    if event.req_id not in handled:     # New req_id
                        handled[event.req_id] = []
                    current = handled[event.req_id]
                    if event not in current:    # Not there, append it
                        current.append(event)
                else:
                    continue
                if (len(current) == len(simple_events)
                    and False not in _specific_events):
                    return specific_events + handled[event.req_id]
        raise StopIteration("Events did not occur until timeout. Missing "
                            "events: %s" % handled)


def wait_for(func, timeout, first=0.0, step=1.0):
    """
    If func() evaluates to True before timeout expires, return the
    value of func(). Otherwise return None.

    @brief: Wait until func() evaluates to True.

    :param timeout: Timeout in seconds
    :param first: Time to sleep before first attempt
    :param steps: Time to sleep between attempts in seconds
    """
    end_time = time.time() + timeout
    time.sleep(first)
    while time.time() < end_time:
        output = func()
        if output:
            return output
        time.sleep(step)
    return None
