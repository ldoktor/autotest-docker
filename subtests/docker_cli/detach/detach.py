"""
Tests the ctrl+p ctrl+q detach method
"""
# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103
import os
import time

from autotest.client.shared import utils
from dockertest import config, dexpect, subtest, xceptions
from dockertest.containers import DockerContainers
from dockertest.dockercmd import DockerCmdBase, NoFailDockerCmd, AsyncDockerCmd
from dockertest.images import DockerImage
import re


def not_equal(self, first, second, iteration, msg):
    self.failif(first != second, msg + "(%s != %s, iteration %s"
                % (first, second, iteration))


class InteractiveAsyncDockerCmd(AsyncDockerCmd):
    """
    Execute docker command as asynchronous background process on ``execute()``
    with PIPE as stdin and allows use of stdin(data) to interact with process.
    """
    def __init__(self, subtest, subcmd, subargs=None, timeout=None,
                 verbose=True, name=None):
        super(InteractiveAsyncDockerCmd, self).__init__(subtest, subcmd,
                                                        subargs, timeout,
                                                        verbose)
        self.name = name
        self._stdin = None
        self._stdout_idx = 0

    def execute(self, stdin=None):
        """
        Start execution of asynchronous docker command
        """
        ps_stdin, self._stdin = os.pipe()
        ret = super(InteractiveAsyncDockerCmd, self).execute(ps_stdin)
        os.close(ps_stdin)
        if stdin:
            self.sendline(stdin)
        return ret

    def send(self, data=""):
        """
        Sends data to stdin (partial send is possible!)
        :param data: Data to be send
        :return: Number of written data
        """
        self.log(">>", data)
        return os.write(self._stdin, data)

    def sendline(self, data=""):
        """
        Sends data + EOL to the process
        :param data: string
        """
        return self.send(data + "\n")

    def send_control(self, char):
        """
        This sends a control character to the child such as Ctrl-C or
        Ctrl-D. For example, to send a Ctrl-G (ASCII 7)::
        child.sendcontrol('g')
        :param char: single character you want to send (ctrl+$char)
        :raise KeyError: When unable to map char to ctrl+comand
        """
        char = char.lower()
        val = ord(char)
        if val >= 97 and val <= 122:
            val = val - 97 + 1  # ctrl+a = '\0x01'
            return self.send(chr(val))
        mapping = {'@': 0, '`': 0,
                   '[': 27, '{': 27,
                   '\\': 28, '|': 28,
                   ']': 29, '}': 29,
                   '^': 30, '~': 30,
                   '_': 31,
                   '?': 127}
        return self.send(chr(mapping[char]))

    def _try_read(self, timeout=None):
        """
        Wait up to $timeout seconds for input on stdin
        :param timeout: How long to wait for new data
        :return: new data in stdin
        """
        if timeout is None:
            end_time = False
        else:
            end_time = time.time() + timeout
        while len(self.stdout) <= self._stdout_idx:
            if end_time and time.time() > end_time:
                break
            time.sleep(0.1)
        else:
            out = self.stdout
            _idx = self._stdout_idx
            self._stdout_idx = len(out)
            return out[_idx:]
        return ""

    def read_nonblocking(self, internal_timeout=None, timeout=None):
        """
        Non-blocking read of data from the process
        :param internal_timeout: Maximal wait for single new data to arrive
        :param timeout: Maximal overall read duration
        :return: Read data
        """
        if internal_timeout is None:
            internal_timeout = 0.1
        end_time = None
        if timeout > 0:
            end_time = time.time() + timeout
        data = self._try_read(internal_timeout)
        while end_time and time.time() < end_time:
            _data = self._try_read(internal_timeout)
            if not _data:   # No new data after internal timeout
                self.log("<<", data)
                return data
            data += _data
        self.log("<<", data)
        return data

    def read_until_output_matches(self, regexp, timeout, internal_timeout=0.1):
        """
        Read stdin until re.findall(regexp...) returns any matches.
        :param regexp: multiline regexp we are looking for
        :param timeout: Maximal overall read duration
        :param internal_timeout: Timeout for each read.
        :return: output
        :raise IOError: When timeout expires
        """
        out = self.read_nonblocking(internal_timeout, -1)
        end_time = time.time() + timeout
        while not re.findall(regexp, out, re.M):
            out += self.read_nonblocking(internal_timeout, -1)
            if time.time() > end_time:
                raise IOError("Timeout expired while looking for %r. Output "
                              "so far:\n%r" % (regexp, out))
        return out

    def log(self, prefix, data):
        """
        Logs to self._log using following format "$prefix$name: $data"
        :param prefix: Prefix ('>>', '<<', ...)
        :param data: Data to write.
        """
        for line in data.splitlines(True):
            self.subtest.logdebug("%s%s: %r" % (prefix, self.name, line))

    def is_responsive(self, timeout=5, internal_timeout=1):
        """
        Sends `true` command and waits for output. When output obtained => True
        :warning: Don't set internal_timeout too low unless stressed machine
                  might not response quickly enough.
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                self.cmd("true", internal_timeout)
                out = self.read_nonblocking(2)
            except IOError:
                pass
        try:    # +1 iteration in case we missed end_time of milliseconds...
            self.cmd("true", internal_timeout)
            out = self.read_nonblocking(2)
            if out:
                raise Exception("This shouldn't happened; out:\n%s" % out)
            return True
        except IOError:
            pass
        return False

    def cmd(self, cmd, timeout=60):
        """
        Executes command in this dkrcmd instance
        :param cmd: Command you want to execute
        :param timeout: Maximal command execution duration
        :return: tuple(status, output)
        """
        self.read_nonblocking(0, -1)     # Read out all previous data
        self.sendline(cmd)
        end = ("--< END OF THE COMMAND %s >--"
               % utils.generate_random_string(12))
        end_cmd = 'RET=$?; echo; echo "$RET %s"' % end
        self.sendline(end_cmd)
        end = r'(\d+) ' + end + "\r?\n?"
        out = self.read_until_output_matches(end, timeout)
        stat = re.findall(end, out, re.M)[-1]
        # Remove sent command
        if out.startswith(cmd):
            out = out[len(cmd):].lstrip('\n\r')
        # Remove end command and any output after end_cmd output
        # When end_cmd is executed before main cmd finishes in +echo mode,
        # end_cmd is present twice in the output.
        out = re.sub(r'\r?\n?.*%s\r?\n?' % re.escape(end_cmd), '', out, 2)
        out = re.split(r'\r?\n?.*%s\r?\n?' % end, out, 1)[0]
        return stat, out


class InteractiveBash(InteractiveAsyncDockerCmd):
    """
    This test detaches from docker into bash. In order to be able to do this
    I need to execute bash directly and then using sendline execute container
    """
    @property
    def command(self):
        return "bash -l"


class detach(subtest.SubSubtestCaller):

    """ Subtest caller """
    config_section = 'docker_cli/detach'


class detach_base(subtest.SubSubtest):

    def initialize(self):
        """
        Creates dexpect dkrcmd with host's bash, than starts container in it
        and stores host and container hostnames.
        """
        super(detach_base, self).initialize()
        config.none_if_empty(self.config)
        # Prepare a container
        docker_containers = DockerContainers(self.parent_subtest)
        name = docker_containers.get_unique_name("container", length=4)
        self.sub_stuff['container_name'] = name
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
        cmd = DockerCmdBase(self, 'run', subargs).command
        # interactive bash in AsyncDockerCmd-like object
        dkrcmd = InteractiveBash(self, 'dumm', [], name=name)
        dkrcmd.execute()
        # connect dkrcmd to dkrcmd (we don't have tty => no echo =>
        # is_responsive() fails. dkrcmd.cmd() works fine
        self.failif(not dkrcmd.is_responsive(), "Session not responsive "
                    "after returning from container to bash.")
        self.sub_stuff['host_hostname'] = dkrcmd.cmd("echo $HOSTNAME",
                                                     timeout=10)[1]
        # After this command container should be in the foreground
        dkrcmd.sendline(cmd)
        self.failif(not dkrcmd.is_responsive(), "Container not "
                    "initialized in 5s.")
        self.sub_stuff['dkrcmd'] = dkrcmd
        self.sub_stuff['attach_cmd'] = DockerCmdBase(self, 'attach',
                                                     [name]).command
        self.sub_stuff['container_hostname'] = dkrcmd.cmd("echo $HOSTNAME",
                                                           timeout=10)[1]
        if (self.sub_stuff['host_hostname']
            == self.sub_stuff['container_hostname']):
            msg = ("Hostname of host and container seems to be the same. "
                   "Either container didn't started or your host uses the "
                   "same name '%s'" % self.sub_stuff['host_hostname'])
            raise xceptions.DockerTestNAError(msg)

    def cleanup(self):
        """
        Close all dkrcmds and remove the container
        """
        super(detach_base, self).cleanup()
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
    1) Create bash dkrcmd
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
        dkrcmd = self.sub_stuff['dkrcmd']

        self.loginfo("Initiating ping process...")
        dkrcmd.sendline('ping 127.0.0.1')
        self.failif(not dkrcmd.read_nonblocking(2), "No output after ping "
                    "command execution.")
        self.failif(not dkrcmd.read_nonblocking(2), "No new output 2s "
                    "after ping command execution")

        self.loginfo("Detaching...")
        dkrcmd.send_control('p')
        time.sleep(1)       # FIXME: Workaround the known problem
        dkrcmd.send_control('q')
        time.sleep(1)       # FIXME: Workaround the known problem
        self.failif(not dkrcmd.is_responsive(), "Session not responsive "
                    "after returning from container to bash.")
        not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        dkrcmd.read_nonblocking()  # read-out everything
        out = dkrcmd.read_nonblocking(2)
        self.failif(out, "Unexpected output in underlying bash after "
                    "detaching the container:\n%s" % out)

        self.loginfo("Attaching...")
        dkrcmd.sendline(self.sub_stuff['attach_cmd'])
        dkrcmd.read_nonblocking()  # read-out everything
        self.failif(not dkrcmd.read_nonblocking(2), "No new output 2s "
                    "after re-attaching the container")

        self.loginfo("Detaching from attached docker...")
        dkrcmd.send_control('p')
        time.sleep(1)       # FIXME: Workaround the known problem
        dkrcmd.send_control('q')
        time.sleep(1)       # FIXME: Workaround the known problem
        self.failif(not dkrcmd.is_responsive(), "Session not responsive "
                    "after returning from container to bash.")
        not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        dkrcmd.read_nonblocking()  # read-out everything
        out = dkrcmd.read_nonblocking(2)
        self.failif(out, "Unexpected output in underlying bash after "
                    "detaching the attached container:\n%s" % out)

        self.loginfo("Attaching for the second time...")
        dkrcmd.sendline(self.sub_stuff['attach_cmd'])
        dkrcmd.read_nonblocking()  # read-out everything
        self.failif(not dkrcmd.read_nonblocking(2), "No new output 2s "
                    "after re-attaching the container")

        self.loginfo("Stopping the ping process...")
        dkrcmd.send_control('c')
        dkrcmd.cmd("true", 10)
        out = dkrcmd.read_nonblocking(2)
        self.failif(out, "Unexpected output after killing the ping process "
                    "by ctrl+c\n%s" % out)

        self.loginfo("Verifying container response...")
        not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['container_hostname'], "NONE", "Hostname "
                  "mismatch, docker detach probably failed.")
        dkrcmd.send_control('d')   # exit the container
        self.failif(not dkrcmd.is_responsive(), "Session not responsive "
                    "after returning from container to bash.")
        self.loginfo("Verifying host response...")
        not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['host_hostname'], "NONE", "Hostname mismatch,"
                  " docker detach probably failed.")
        # exit dkrcmd
        dkrcmd.sendline('exit')
        done = lambda: dkrcmd.done
        self.failif(not utils.wait_for(done, 5, step=0.1),
                    "Session is alive even thought we used exit")


class stress(detach_base):
    """
    1) Dettach from docker
    2) Attach to docker
    3) GOTO 1 (unless no_iteration is reached)
    """
    def run_once(self):
        super(stress, self).run_once()
        dkrcmd = self.sub_stuff['dkrcmd']
        no_iterations = self.config['no_iterations']
        self.loginfo("Starting %s iterations of detach/check/attach/check..."
                     % no_iterations)
        for i in xrange(no_iterations):
            # Detaching...
            dkrcmd.send_control('p')
            time.sleep(1)       # FIXME: Workaround the known problem
            dkrcmd.send_control('q')
            time.sleep(1)       # FIXME: Workaround the known problem
            self.failif(not dkrcmd.is_responsive(), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                      self.sub_stuff['host_hostname'], i, "Hostname mismatch, "
                      " docker detach probably failed.")

            # Attaching...
            dkrcmd.sendline(self.sub_stuff['attach_cmd'])
            not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                      self.sub_stuff['container_hostname'], i, "Hostname "
                      "mismatch, docker attach probably failed.")

        self.loginfo("%s iterations passed, stopping the container...",
                     no_iterations)
        dkrcmd.send_control('d')   # exit the container
        self.failif(not dkrcmd.is_responsive(), "Session not responsive "
                    "after returning from container to bash.")
        self.loginfo("Verifying host response...")
        dkrcmd.sendline()
        dkrcmd.cmd("true", 10)
        not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['host_hostname'], "<after>", "Hostname "
                  "mismatch, docker probably didn't finish.")
        # exit dkrcmd
        dkrcmd.sendline('exit')
        done = lambda: dkrcmd.done
        self.failif(not utils.wait_for(done, 5, step=0.1),
                    "Session is alive even thought we used exit")


class multi_session(detach_base):
    """
    1) Create another dkrcmd
    2) dkrcmd1 -> start writing command
    3) dkrcmd2 -> add part of the command and detach
    4) dkrcmd1 -> add part of the command
    5) dkrcmd2 -> reattach and add last part of the command
    6) dkrcmd1 -> execute the command
    7) GOTO 2 (unless no_iteration is reached)
    """
    def initialize(self):
        super(multi_session, self).initialize()
        name = "%s-2" % self.sub_stuff['container_name']
        dkrcmd2 = InteractiveBash(self, 'dumm', [], name=name)
        dkrcmd2.execute()
        self.sub_stuff['dkrcmd2'] = dkrcmd2
        dkrcmd2.sendline(self.sub_stuff['attach_cmd'])
        not_equal(self, dkrcmd2.cmd("echo $HOSTNAME", timeout=10)[1],
                  self.sub_stuff['container_hostname'], "<before>", "Hostname "
                  "mismatch, docker attach probably failed.")

    def run_once(self):
        super(multi_session, self).run_once()
        dkrcmd1 = self.sub_stuff['dkrcmd']
        dkrcmd2 = self.sub_stuff['dkrcmd2']

        no_iterations = self.config['no_iterations']
        self.loginfo("Starting %s iterations of multi_session test..."
                     % no_iterations)
        for i in xrange(no_iterations):
            # Detaching...
            rand1, rand2, rand3 = (utils.generate_random_string(4)
                                   for _ in xrange(3))
            dkrcmd1.send('echo ')
            dkrcmd1.read_until_output_matches(r'.*echo $', timeout=5)
            dkrcmd2.send(rand1)
            dkrcmd2.read_until_output_matches(r'.*%s$' % rand1, timeout=5)
            time.sleep(1)       # FIXME: Workaround the known problem
            dkrcmd2.send_control('p')
            time.sleep(1)       # FIXME: Workaround the known problem
            dkrcmd2.send_control('q')
            time.sleep(1)       # FIXME: Workaround the known problem
            # No interaction in detached dkrcmd2 should affect container
            self.failif(not dkrcmd2.is_responsive(), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, dkrcmd2.cmd("echo $HOSTNAME", timeout=10)[1],
                      self.sub_stuff['host_hostname'], i, "Hostname mismatch, "
                      " docker detach probably failed.")
            dkrcmd1.send(rand2)
            dkrcmd1.read_until_output_matches(r'.*%s$' % rand2, timeout=5)
            # Attaching...
            dkrcmd2.sendline(self.sub_stuff['attach_cmd'])
            dkrcmd2.send(rand3)
            dkrcmd2.read_until_output_matches(r'.*%s$' % rand3, timeout=5)
            out = dkrcmd1.cmd('')[1]  # Command is already written, use '\n'
            not_equal(self, out.strip(), rand1 + rand2 + rand3, i, "echo "
                      "$RANDOM output mismatch.")

        self.loginfo("%s iterations passed, stopping the container...",
                     no_iterations)
        self.loginfo("Verifying host response...")
        dkrcmd1.send_control('d')   # exit the container
        # Now container should finish and booth dkrcmds should be in host bash
        for dkrcmd in (dkrcmd1, dkrcmd2):
            self.failif(not dkrcmd.is_responsive(), "Session not "
                        "responsive after returning from container to bash.")
            not_equal(self, dkrcmd.cmd("echo $HOSTNAME", timeout=10)[1],
                      self.sub_stuff['host_hostname'], "<after>", "Hostname "
                      "mismatch, docker probably didn't finish. (%s)"
                      % (dkrcmd.name))
            # exit dkrcmd
            dkrcmd.sendline('exit')
            done = lambda: dkrcmd.done
            self.failif(not utils.wait_for(done, 5, step=0.1),
                        "Session is alive even thought we used exit")
