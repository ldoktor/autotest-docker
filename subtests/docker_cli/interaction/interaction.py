"""
Simple interaction
"""
# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103
import os
import time

from autotest.client.shared import utils
from dexpect import ShellSession, ShellError
from dockertest import subtest
from dockertest.dockercmd import NoFailDockerCmd, AsyncDockerCmd


class LogInClenupSubSubtest(subtest.SubSubtest):

    def __init__(self, parent_subtest):
        super(LogInClenupSubSubtest, self).__init__(parent_subtest)
        #: Dictionary of stringalizable items, which must be printed during
        #: cleanup. This is intended for unexpected exceptions.
        self.log_in_cleanup = {}

    def cleanup(self):
        super(LogInClenupSubSubtest, self).cleanup()
        if self.log_in_cleanup:
            self.logwarning("There are some uncleaned objects left:")
            try:
                while True:
                    item = self.log_in_cleanup.popitem()
                    self.logwarning("%s: %s" % item)
            except KeyError:
                pass


class TooledSubSubtest(LogInClenupSubSubtest):

    def __init__(self, parent_subtest):
        super(TooledSubSubtest, self).__init__(parent_subtest)
        from dockertest.images import DockerImages
        from dockertest.containers import DockerContainers
        from docker_manager import DockerManager
        self.containers = DockerContainers(self.parent_subtest)
        self.images = DockerImages(self.parent_subtest)
        self.manager = DockerManager(self)


class InteractiveAsyncDockerCmd(AsyncDockerCmd):

    """
    Execute docker command as asynchronous background process on ``execute()``
    with PIPE as stdin and allows use of stdin(data) to interact with process.
    """

    def __init__(self, subtest, subcmd, subargs=None, timeout=None,
                 verbose=True):
        super(InteractiveAsyncDockerCmd, self).__init__(subtest, subcmd,
                                                        subargs, timeout,
                                                        verbose)
        self._stdin = None
        self._stdout_idx = 0

    def execute(self, stdin=None):
        """
        Start execution of asynchronous docker command
        """
        self.close()
        ps_stdin, self._stdin = os.pipe()
        ret = super(InteractiveAsyncDockerCmd, self).execute(ps_stdin)
        os.close(ps_stdin)
        if stdin:
            self.stdin(stdin)
        return ret

    def stdin(self, data):
        """
        Sends data to stdin (partial send is possible!)
        :param data: Data to be send
        :return: Number of written data
        """
        return os.write(self._stdin, data)

    def close(self):
        """
        Closes opened file descriptors. Always call this before exit!
        """
        if self._stdin is not None:
            os.close(self._stdin)
            self._stdin = None

    def __del__(self):
        super(InteractiveAsyncDockerCmd, self).__del__()
        self.close()


def wait_for_alive(sub_test, container):
    """
    :return: True when container is alive
    """
    def is_alive(sub_test, container):
        cmd = NoFailDockerCmd(sub_test, "inspect",
                              ["-f", "'{{.State.Running}}'", container])
        out = cmd.execute().stdout
        if "true" in out.lower():
            return True
        else:
            return False
    return utils.wait_for(lambda: is_alive(sub_test, container.long_id), 5)


def session_responsive(session, timeout=3, internal_timeout=1):
    """
    Sends `true` command and queues for output. When output obtained => True
    :warning: Don't set internal_timeout too low unless stressed machine
              might not response quickly enough.
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            session.cmd_status("true", internal_timeout)
            out = session.read_nonblocking(2)
            if out:
                raise Exception("This shouldn't happened; out:\n%s" % out)
            return True
        except ShellError:
            pass
    try:    # +1 iteration in case we missed end_time of milliseconds...
        session.cmd_status("true", internal_timeout)
        out = session.read_nonblocking(2)
        if out:
            raise Exception("This shouldn't happened; out:\n%s" % out)
        return True
    except ShellError:
        pass
    return False


class interaction(subtest.SubSubtestCaller):

    """ Subtest caller """
    pass


class interaction_base(TooledSubSubtest):

    def initialize(self):
        """
        Creates dexpect session with host's bash, than starts container in it
        and stores host and container hostnames.
        """
        super(interaction_base, self).initialize()
        # Prepare a container
        config = self.config
        ret = self.manager.create_container(config['container_command'],
                                            config['container_options_csv'].split(','))
        self.sub_stuff['container'] = container = ret[0]
        self.sub_stuff['process'] = process = ret[1]
        # No we have container up and running
        if self.config.get('session_attached'):
            self.failif(not wait_for_alive(self, container),
                        "Container did not started in 5s")
            attach = InteractiveAsyncDockerCmd(self, "attach",
                                               [container.long_id])
            attach.execute()
            session = ShellSession(attach, tty=config['is_tty'],
                                   name=container.container_name + '_1',
                                   log_func=self.logdebug)
        else:
            session = ShellSession(process, tty=config['is_tty'],
                                   name=container.container_name + '_1',
                                   log_func=self.logdebug)
        self.sub_stuff['session'] = session

    def run_once(self):
        super(interaction_base, self).run_once()
        session = self.sub_stuff['session']
        self.failif(not session_responsive(session), "Session not responsive.")
        self.failif(session.cmd_status("ls"), "ls command failed.")
        session.send_control('s')
        if self.config['is_tty']:
            self.failif(session_responsive(session), "Session is responsive "
                        "even though we sent ctrl+s")
        else:
            ret = session.cmd_status_output("true")
            self.failif(ret[0] != 127, "ctrl+s should pass \x12 directly to "
                        "the stdin. After that true command was executed "
                        "including this \x12 as first character, which should"
                        "result in err 127, got %s instead. Out:\n%s"
                        % (ret[0], ret[1]))
        session.send_control('q')
        if self.config['is_tty']:
            self.failif(not session_responsive(session), "Session not "
                        "responsive after sending ctrl+q.")
        else:
            ret = session.cmd_status_output("true")
            self.failif(ret[0] != 127, "ctrl+q should pass \x10 directly to "
                        "the stdin. After that true command was executed "
                        "including this \x10 as first character, which should"
                        "result in err 127, got %s instead. Out:\n%s"
                        % (ret[0], ret[1]))
        session.sendline("")    # there might be some chars present...
        session.sendline('exit')
        not_alive = lambda: not session.is_alive()
        self.failif(not utils.wait_for(not_alive, 5), "Session is alive "
                    "even though exit was executed.")

    def cleanup(self):
        """
        Close all sessions and remove the container
        """
        super(interaction_base, self).cleanup()
        # Stop session
        print self.config['remove_after_test']
        if self.config['remove_after_test'] is True:
            self.manager.cleanup_containers()
            self.manager.cleanup_images()


class interactive_tty(interaction_base):

    """
    Using the --interactive mode
    """
    pass


class interactive_nontty(interaction_base):

    """
    Using the --interactive mode
    """
    pass


class attached_tty(interaction_base):

    """
    Using the --detached mode
    """
    pass


class attached_nontty(interaction_base):

    """
    Using the --detached mode
    """
    pass
