"""
Tests the ctrl+p ctrl+q detach method
"""
# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103
import time

from autotest.client.shared import utils
import dexpect
from dockertest import config, subtest, xceptions
from dockertest.containers import DockerContainers
from dockertest.dockercmd import DockerCmdBase, AsyncDockerCmd
from dockertest.dockercmd import NoFailDockerCmd
from dockertest.images import DockerImage
import os


def not_equal(self, first, second, iteration, msg):
    self.failif(first != second, msg + "(%s != %s, iteration %s"
                % (first, second, iteration))


def session_responsive(session, timeout=5, internal_timeout=1):
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
        except dexpect.ShellError:
            pass
    try:    # +1 iteration in case we missed end_time of milliseconds...
        session.cmd_status("true", internal_timeout)
        out = session.read_nonblocking(2)
        if out:
            raise Exception("This shouldn't happened; out:\n%s" % out)
        return True
    except dexpect.ShellError:
        pass
    return False


class LogMe(object):

    """
    This class stores the provided function and calls it when __str__ is used
    """

    def __init__(self, func):
        self.func = func

    def __str__(self):
        return self.func()


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


class InteractiveBash(InteractiveAsyncDockerCmd):

    """
    This test detaches from docker into bash. In order to be able to do this
    I need to execute bash directly and then using sendline execute container
    """
    @property
    def command(self):
        return "bash -l"


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
            for key, value in self.log_in_cleanup.iteritems():
                self.logwarning("%s: %s" % (key, value))


class detach(subtest.SubSubtestCaller):

    """ Subtest caller """
    pass


class detach_base(LogInClenupSubSubtest):

    def initialize(self):
        """
        Creates dexpect session with host's bash, than starts container in it
        and stores host and container hostnames.
        """
        super(detach_base, self).initialize()
        # Prepare a container
        session_log = self.config.get('session_log')
        if session_log == 'STORE':
            self.sub_stuff['session_log'] = dexpect.STORE
        elif session_log == 'NONE':
            self.sub_stuff['session_log'] = None
        else:   # by default log to debug output
            self.sub_stuff['session_log'] = self.logdebug
        docker_containers = DockerContainers(self.parent_subtest)
        prefix = self.config["container_name_prefix"]
        name = docker_containers.get_unique_name(prefix, length=4)
        self.sub_stuff['container_name'] = name
        config.none_if_empty(self.config)
        subargs = self.config.get('container_options_csv')
        if subargs:
            subargs = [arg for arg in subargs.split(',')]
        else:
            subargs = []
        tty = self.config.get('container_tty')
        if tty:
            subargs.append(tty)
        subargs.append("--name %s" % name)
        fin = DockerImage.full_name_from_defaults(self.config)
        subargs.append(fin)
        subargs.append("bash")
        subargs.append("-c")
        subargs.append(self.config['container_command'])
        # full `docker run` command
        cmd = DockerCmdBase(self.parent_subtest, 'run', subargs).command
        # interactive bash in AsyncDockerCmd-like object
        dkrcmd = InteractiveBash(self.parent_subtest, 'dumm', [])
        dkrcmd.execute()
        # connect session to dkrcmd (we don't have tty => no echo =>
        # is_responsive() fails. session.cmd() works fine
        session = dexpect.ShellSession(dkrcmd, name=name, tty=False,
                                       log_func=self.sub_stuff['session_log'])
        if self.sub_stuff['session_log'] == dexpect.STORE:
            _ = session.get_log_records
            self.log_in_cleanup['session1_log'] = LogMe(_)
        self.failif(not session_responsive(session), "Session not responsive "
                    "after returning from container to bash.")
        self.sub_stuff['host_hostname'] = session.cmd("echo $HOSTNAME",
                                                      timeout=10)
        # After this command container should be in the foreground
        session.sendline(cmd)
        # TODO: FIXME: Adjust this to the latest version before the merge
        if not tty or 'false' in str(tty).lower():
            self.sub_stuff['container_is_tty'] = False
            session.set_tty(False, 0)
        else:
            self.sub_stuff['container_is_tty'] = True
            session.set_tty(True, 0)
        self.failif(not session_responsive(session), "Container not "
                    "initialized in 5s.")
        self.sub_stuff['session'] = session
        self.sub_stuff['attach_cmd'] = DockerCmdBase(self.parent_subtest,
                                                     'attach', [name]).command
        self.sub_stuff['container_hostname'] = session.cmd("echo $HOSTNAME",
                                                           timeout=10)
        if (self.sub_stuff['host_hostname']
                == self.sub_stuff['container_hostname']):
            msg = ("Hostname of host and container seems to be the same. "
                   "Either container didn't started or your host uses the "
                   "same name '%s'" % self.sub_stuff['host_hostname'])
            raise xceptions.DockerTestNAError(msg)

    def postprocess(self):
        """
        When everything went correctly, remove last log records.
        """
        super(detach_base, self).postprocess()
        # All sessions are already closed, remove them from log_in_cleanup
        for name in ('session1_log', 'session2_log'):
            if name in self.log_in_cleanup:
                del self.log_in_cleanup[name]

    def cleanup(self):
        """
        Close all sessions and remove the container
        """
        super(detach_base, self).cleanup()
        # Stop session
        for name in ('session', 'session2'):
            session = self.sub_stuff.get(name)
            if session and session.is_alive():
                session.terminate()
        # Remove container
        if self.sub_stuff.get('container_name') is None:
            return  # Docker was not created, we are clean
        containers = DockerContainers(self.parent_subtest)
        name = self.sub_stuff['container_name']
        conts = containers.list_containers_with_name(name)
        if conts == []:
            return  # Docker was created, but apparently doesn't exist, clean
        elif len(conts) > 1:
            msg = ("Multiple containers matches name %s, not removing any of "
                   "them...", name)
            raise xceptions.DockerTestError(msg)
        NoFailDockerCmd(self.parent_subtest, 'rm', ['--force', '--volumes',
                                                    name]).execute()


class basic(detach_base):

    """
    1) Create bash session
    2) Start interactive docker container inside
    3) Start ping 127.0.0.1 inside the container
    4) Detach using ctrl+p ctrl+q
    5) Attach using docker attach $name
    6) Detach using ctrl+p ctrl+q
    7) Attach again using docker attach $name
    8) Stop ping (ctrl+c), execute true inside container
    9) stop container (ctrl+d) and execute true inside host
    """

    def run_once(self):
        super(basic, self).run_once()
        session = self.sub_stuff['session']

        self.loginfo("Initiating ping process...")
        session.sendline('ping 127.0.0.1')
        self.failif(not session.read_nonblocking(2), "No output after ping "
                    "command execution.")
        self.failif(not session.read_nonblocking(2), "No new output 2s "
                    "after ping command execution")

        self.loginfo("Detaching...")
        session.send_control('p')
        time.sleep(1)       # FIXME: Workaround the known problem
        session.send_control('q')
        time.sleep(1)       # FIXME: Workaround the known problem
        session.set_tty(False, 0)    # we should be in bash (no echo, no tty)
        self.failif(not session_responsive(session), "Session not responsive "
                    "after returning from container to bash.")
        not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        session.read_nonblocking()  # read-out everything
        out = session.read_nonblocking(2)
        self.failif(out, "Unexpected output in underlying bash after "
                    "detaching the container:\n%s" % out)

        self.loginfo("Attaching...")
        session.sendline(self.sub_stuff['attach_cmd'])
        session.read_nonblocking()  # read-out everything
        session.set_tty(self.sub_stuff['container_is_tty'], 5)
        self.failif(not session.read_nonblocking(2), "No new output 2s "
                    "after re-attaching the container")

        self.loginfo("Detaching from attached docker...")
        session.send_control('p')
        time.sleep(1)       # FIXME: Workaround the known problem
        session.send_control('q')
        time.sleep(1)       # FIXME: Workaround the known problem
        session.set_tty(False, 0)    # we should be in bash (no echo, no tty)
        self.failif(not session_responsive(session), "Session not responsive "
                    "after returning from container to bash.")
        not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        session.read_nonblocking()  # read-out everything
        out = session.read_nonblocking(2)
        self.failif(out, "Unexpected output in underlying bash after "
                    "detaching the attached container:\n%s" % out)

        self.loginfo("Attaching for the second time...")
        session.sendline(self.sub_stuff['attach_cmd'])
        session.read_nonblocking()  # read-out everything
        session.set_tty(self.sub_stuff['container_is_tty'], 5)
        self.failif(not session.read_nonblocking(2), "No new output 2s "
                    "after re-attaching the container")

        self.loginfo("Stopping the ping process...")
        session.send_control('c')
        session.read_up_to_prompt(5)
        out = session.read_nonblocking(2)
        self.failif(out, "Unexpected output after killing the ping process "
                    "by ctrl+c\n%s" % out)

        self.loginfo("Verifying container response...")
        not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['container_hostname'], "NONE", "Hostname "
                  "mismatch, docker detach probably failed.")
        session.send_control('d')   # exit the container
        session.set_tty(False, 0)    # we should be in bash (no echo, no tty)
        self.failif(not session_responsive(session), "Session not responsive "
                    "after returning from container to bash.")
        self.loginfo("Verifying host response...")
        not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        # exit session
        session.send_control('d')


class stress(detach_base):

    """
    1) Dettach from docker
    2) Attach to docker
    3) GOTO 1 (unless no_iteration is reached)
    """

    def run_once(self):
        super(stress, self).run_once()
        session = self.sub_stuff['session']
        no_iterations = self.config['no_iterations']
        self.log_in_cleanup['session1_log'] = LogMe(session.get_log_records)
        session.set_logging(dexpect.STORE)
        self.loginfo("Starting %s iterations of detach/check/attach/check..."
                     % no_iterations)
        for i in xrange(no_iterations):
            # Detaching...
            session.send_control('p')
            time.sleep(1)       # FIXME: Workaround the known problem
            session.send_control('q')
            time.sleep(1)       # FIXME: Workaround the known problem
            session.set_tty(False, 0)  # we should be in bash (no echo, no tty)
            self.failif(not session_responsive(session), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                      self.sub_stuff['host_hostname'], i, "Hostname mismatch, "
                      " docker detach probably failed.")

            # Attaching...
            session.sendline(self.sub_stuff['attach_cmd'])
            session.set_tty(self.sub_stuff['container_is_tty'], 5)
            not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                      self.sub_stuff['container_hostname'], i, "Hostname "
                      "mismatch, docker attach probably failed.")
            session.set_logging(dexpect.STORE)  # log only last iteration

        self.loginfo("%s iterations passed, stopping the container...",
                     no_iterations)
        session.set_logging(self.sub_stuff['session_log'])  # Use default logs
        if self.sub_stuff['session_log'] == dexpect.STORE:
            self.log_in_cleanup['session1_log'] = session.get_log_records
        session.send_control('d')   # exit the container
        session.set_tty(False, 0)    # we should be in bash (no echo, no tty)
        self.failif(not session_responsive(session), "Session not responsive "
                    "after returning from container to bash.")
        self.loginfo("Verifying host response...")
        session.sendline()
        session.read_up_to_prompt(10)
        not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['host_hostname'], "<after>", "Hostname "
                  "mismatch, docker probably didn't finish.")
        # exit session
        session.send_control('d')


class multi_session(detach_base):

    """
    1) Create another session
    2) session1 -> start writing command
    3) session2 -> add part of the command and detach
    4) session1 -> add part of the command
    5) session2 -> reattach and add last part of the command
    6) session1 -> execute the command
    7) GOTO 2 (unless no_iteration is reached)
    """

    def initialize(self):
        super(multi_session, self).initialize()
        dkrcmd = InteractiveBash(self.parent_subtest, 'dumm', [])
        dkrcmd.execute()
        # connect session to dkrcmd (we don't have tty => no echo =>
        # is_responsive() fails. session.cmd() works fine
        name = "%s-2" % self.sub_stuff['container_name']
        session2 = dexpect.ShellSession(dkrcmd, name=name, tty=False,
                                        log_func=self.sub_stuff['session_log'])
        if self.sub_stuff['session_log'] == dexpect.STORE:
            _ = session2.get_log_records
            self.log_in_cleanup['session2_log'] = LogMe(_)
        self.sub_stuff['session2'] = session2
        session2.sendline(self.sub_stuff['attach_cmd'])
        session2.set_tty(self.sub_stuff['container_is_tty'], 5)
        not_equal(self, session2.cmd("echo $HOSTNAME", timeout=10),
                  self.sub_stuff['container_hostname'], "<before>", "Hostname "
                  "mismatch, docker attach probably failed.")

    def run_once(self):
        super(multi_session, self).run_once()
        session1 = self.sub_stuff['session']
        session2 = self.sub_stuff['session2']

        no_iterations = self.config['no_iterations']
        self.loginfo("Starting %s iterations of multi_session test..."
                     % no_iterations)
        for i in xrange(no_iterations):
            # Detaching...
            rand1, rand2, rand3 = (utils.generate_random_string(4)
                                   for _ in xrange(3))
            session1.send('echo ')
            session1.read_until_output_matches([r'.*echo $'], timeout=5)
            session2.send(rand1)
            session2.read_until_output_matches([r'.*%s$' % rand1], timeout=5)
            time.sleep(1)       # FIXME: Workaround the known problem
            session2.send_control('p')
            time.sleep(1)       # FIXME: Workaround the known problem
            session2.send_control('q')
            time.sleep(1)       # FIXME: Workaround the known problem
            # No interaction in detached session should affect container
            session2.set_tty(False, 0)    # we should be in bash (no echo, no tty)
            self.failif(not session_responsive(session2), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, session2.cmd("echo $HOSTNAME", timeout=10),
                      self.sub_stuff['host_hostname'], i, "Hostname mismatch, "
                      " docker detach probably failed.")
            session1.send(rand2)
            session1.read_until_output_matches([r'.*%s$' % rand2], timeout=5)
            # Attaching...
            session2.sendline(self.sub_stuff['attach_cmd'])
            session2.send(rand3)
            session2.read_until_output_matches([r'.*%s$' % rand3], timeout=5)
            out = session1.cmd('')  # Command is already written, use '\n'
            not_equal(self, out.strip(), rand1 + rand2 + rand3, i, "echo "
                      "$RANDOM output mismatch.")
            session1.set_logging(dexpect.STORE)  # log only last iteration
            session2.set_logging(dexpect.STORE)  # log only last iteration

        self.loginfo("%s iterations passed, stopping the container...",
                     no_iterations)
        session1.set_logging(self.sub_stuff['session_log'])  # Use default logs
        session2.set_logging(self.sub_stuff['session_log'])
        self.loginfo("Verifying host response...")
        session1.send_control('d')   # exit the container
        # Now container should finish and booth sessions should be in host bash
        for session in (session1, session2):
            session.set_tty(False, 0)  # we should be in bash (no echo, no tty)
            self.failif(not session_responsive(session), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, session.cmd("echo $HOSTNAME", timeout=10),
                      self.sub_stuff['host_hostname'], "<after>", "Hostname "
                      "mismatch, docker probably didn't finish. (%s)"
                      % (session.name))
            # exit session
            session.send_control('d')
            not_alive = lambda: not session.is_alive
            self.failif(utils.wait_for(not_alive, 5, step=0.1),
                        "Session is alive even thought we used ctrl+d")
