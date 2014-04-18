import time
import re
import tempfile


START = ("-start", "-allocate_interface")
RUN = ("-create",) + START
FINISH = ("-release_interface", "-inspect",)
LIST = ("-containers",)
REMOVE = ("-container_delete",)


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
                value = other['slot']
                if value != getattr(self, slot):
                    return False
            except KeyError:
                pass
        return True

    def __ne__(self, other):
        return not self.__eq__(other)


class EventHandler(object):
    _re = re.compile(r'\[[^\|]+\|(\w{8})\] ([+-])job (\w+)\((\w*[^\)]*)\)( = \w+ '
                     r'\((\d+)\))?\n?')

    def __init__(self, messages=None):
        if messages is None:
            messages = '/var/log/messages'
        self.messages = open(messages, 'r', 0)
        self._service_offset = self._find_offset(self.messages)
        self._offset = self._service_offset + 8
        self.messages.seek(0, 2)    # Move to the end

    def _find_offset(self, messages):
        for line in messages.xreadlines():
            for keyword in (' kernel: ', ' docker: ', ' NetworkManager: '):
                idx = line.find(keyword)
                if idx > 0:
                    return idx + 1
        else:
            raise ValueError("Can't find /var/log/messages offset of services")

    def _parse_line(self, line):
        if line[self._service_offset:self._offset] != "docker: ":
            return  # Not the "docker:" line
        res = self._re.match(line[self._offset:])
        if not res:
            return  # Line doesn't match job prescription
        res = res.groups()
        # +-$job_name, req_id, container_id, result/None
        return Event(res[1] + res[2], res[0], res[3], res[5] or None)

    def get_events(self):
        events = []
        self.messages.seek(0, 1)    # Without the seek my python won't read
        for line in self.messages.xreadlines():
            event = self._parse_line(line)
            if event:
                events.append(event)
        return events

    def wait_for(self, events, timeout=None):
        """
        :return: Last registered event (you can get req_id and cont_id from it)
        """
        if not events:  # No events, just wait and move to the end of the file
            if timeout:
                time.sleep(timeout)
            self.messages.seek(0, 2)
            return
        handled = {}
        if timeout is None:
            condition = lambda: True
        else:
            endtime = time.time() + timeout
            condition = lambda: time.time() < endtime
        while condition():
            for event in self.get_events():
                if event in events:
                    if event.req_id not in handled:     # New req_id
                        handled[event.req_id] = list(events)
                    try:
                        handled[event.req_id].remove(event)     # Try remove
                    except ValueError:
                        pass    # Event occured twice
                    if handled[event.req_id] == []:
                        return event     # all request were registered
        raise StopIteration("Events did not occur until timeout. Missing "
                            "events: %s" % handled)


_HEAD = """Apr 17 15:34:28 t530 kernel: IPv6: ADDRCONF(NETDEV_UP): em1: link is not ready
"""
_START_FAIL = """Apr 17 16:13:21 t530 docker: 2014/04/17 16:13:21 POST /v1.10/containers/asdf/start
Apr 17 16:13:21 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job start(asdf)
Apr 17 16:13:21 t530 docker: No such container: asdf
Apr 17 16:13:21 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job start(asdf) = ERR (1)
Apr 17 16:13:21 t530 docker: [error] server.go:951 Error: No such container: asdf
Apr 17 16:13:21 t530 docker: [error] server.go:86 HTTP Error: statusCode=404 No such container: asdf
"""
_RUN = """Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job create()
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job create() = OK (0)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job inspect(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, container)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job inspect(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, container) = OK (0)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job attach(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job start(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job allocate_interface(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job allocate_interface(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d) = OK (0)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job start(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d) = OK (0)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job resize(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, 24, 80)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job resize(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, 24, 80) = OK (0)
Apr 17 15:43:24 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job start(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d) = OK (0)
"""

_FINISH = """Apr 17 15:43:30 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job release_interface(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d)
Apr 17 15:43:30 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job release_interface(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d) = OK (0)
Apr 17 15:43:30 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job attach(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d) = OK (0)
Apr 17 15:43:30 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job inspect(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, container)
Apr 17 15:43:30 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job inspect(a95d4e2c17791dff303d33524e6cadb5337b0fe6456b9d18cfc58b67a292ee5d, container) = OK (0)
"""

_LIST = """Apr 17 15:43:35 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job containers()
Apr 17 15:43:35 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job containers() = OK (0)
"""
_REMOVE = """Apr 17 15:43:35 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] +job container_delete(a95d4e2c1779)
Apr 17 15:43:36 t530 docker: [/home/medic/Work/Projekty/Docker/root|39d80f36] -job container_delete(a95d4e2c1779) = OK (0)
"""


if __name__ == '__main__':
    log = tempfile.NamedTemporaryFile('w', bufsize=0)
    print log.name
    log.write(_HEAD)
    events = EventHandler(log.name)
    try:
        events.wait_for(_RUN, 0.1)
    except StopIteration, details:
        print details

    log.write(_START_FAIL)
    print events.wait_for(["+start", "-start"], 0.1)

    log.write(_RUN)
    print events.wait_for(RUN, 0.1)

    log.write(_FINISH)
    log.write(_LIST)
    log.write(_REMOVE)
    print events.wait_for(FINISH + LIST + REMOVE, 0.1)
    print events.wait_for([], 0.1)  # wait for nothing (just moves at the end
