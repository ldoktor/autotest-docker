"""
This module implements pexpect-alike objects to interact with terminal.
It's simplified and tailored to execute simple bash and interact primarily
with `docker`.

:copyright: 2014 Red Hat Inc.
"""

import os
import random
import re
import select
import signal
import string
import subprocess
import tempfile
import time


STORE = -1


class ExpectError(Exception):

    """
    General Expect exception
    """

    def __init__(self, patterns, output):
        Exception.__init__(self, patterns, output)
        self.patterns = patterns
        self.output = output

    def _pattern_str(self):
        """ Format the pattern output nicely """
        if len(self.patterns) == 1:
            return "pattern %r" % self.patterns[0]
        else:
            return "patterns %r" % self.patterns

    def __str__(self):
        return ("Unknown error occurred while looking for %s    (output: %r)" %
                (self._pattern_str(), self.output))


class ExpectTimeoutError(ExpectError):

    """
    Expect timed out
    """

    def __str__(self):
        return ("Timeout expired while looking for %s    (output: %r)" %
                (self._pattern_str(), self.output))


class ExpectProcessTerminatedError(ExpectError):

    """
    Process terminated
    """

    def __init__(self, patterns, status, output):
        ExpectError.__init__(self, patterns, output)
        self.status = status

    def __str__(self):
        return ("Process terminated while looking for %s    "
                "(status: %s,    output: %r)" % (self._pattern_str(),
                                                 self.status, self.output))


class ShellError(Exception):

    """
    General shell exception
    """

    def __init__(self, cmd, output):
        Exception.__init__(self, cmd, output)
        self.cmd = cmd
        self.output = output

    def __str__(self):
        return ("Could not execute shell command %r    (output: %r)" %
                (self.cmd, self.output))


class ShellTimeoutError(ShellError):

    """
    Shell timed out
    """

    def __str__(self):
        return ("Timeout expired while waiting for shell command to "
                "complete: %r    (output: %r)" % (self.cmd, self.output))


class ShellProcessTerminatedError(ShellError):

    """
    Raised when the shell process itself (e.g. bash) terminates unexpectedly
    """

    def __init__(self, cmd, status, output):
        ShellError.__init__(self, cmd, output)
        self.status = status

    def __str__(self):
        return ("Shell process terminated while waiting for command to "
                "complete: %r    (status: %s,    output: %r)" %
                (self.cmd, self.status, self.output))


class ShellCmdError(ShellError):

    """
    Raised when a command executed in a shell terminates with a nonzero
    exit code (status)
    """

    def __init__(self, cmd, status, output):
        ShellError.__init__(self, cmd, output)
        self.status = status

    def __str__(self):
        return ("Shell command failed: %r    (status: %s,    output: %r)" %
                (self.cmd, self.status, self.output))


class ShellStatusError(ShellError):

    """
    Raised when the command's exit status cannot be obtained
    """

    def __str__(self):
        return ("Could not get exit status of command: %r    (output: %r)" %
                (self.cmd, self.output))


def safe_execute(cmd, timeout=5):
    """
    This function executes process inside shell and waits for finish
    :param timeout: Maximal execution time before interruption
    :raise ShellTimeoutError: When process doesn't finish on time
    :raise ShellCmdError: When exit status is not 0
    :return: standard output (without stderr)
    """
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    for _ in xrange(timeout * 10):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        proc.kill()
        raise ShellTimeoutError(cmd, "STDOUT:\n%s\nSTDERR:\n%s"
                                % (proc.stdout.read(), proc.stderr.read()))
    if proc.poll() != 0:
        raise ShellCmdError(cmd, proc.poll(), "STDOUT:\n%s\nSTDERR:\n%s"
                            % (proc.stdout.read(), proc.stderr.read()))
    return proc.stdout.read()


def generate_random_string(length):
    """
    Return a random string using alphanumeric characters.

    :param length: Length of the string that will be generated.
    :return: The generated random string.
    """
    chars = string.letters + string.digits
    return "".join((random.choice(chars) for _ in xrange(length)))


class Expect(object):

    """
    This class runs a child process in the background and provides expect-like
    services.

    :param command: Executed command (bash -i, docker attach $name, ...)
    :param linesep: line separator ('\n', '\r\n', ...)
    :param name: name of this session (used in logging)
    :param log_func: logging function (file.write, logging.debug, ...)
    :warning: This class is not pickable.
    :warning: It is not 100% compatible with aexpect, nor pexpect
    """

    def __init__(self, command, linesep='\n', name=None,
                 log_func=None):
        assert command, "Incorrect command supplied: '%s'" % command
        self.linesep = linesep
        self.command = command
        self.name = name if name else ""
        self.set_logging(log_func)
        self.time_start = time.time()
        (self._pty, slave) = os.openpty()
        self._sp = subprocess.Popen(command, stdin=slave,
                                    stdout=slave,
                                    stderr=slave,
                                    shell=True, close_fds=True)

    def __del__(self):
        """
        Clean up before the object is removed.
        """
        self.terminate()

    def terminate(self):
        """
        Terminates the managed process (-15 eventually followed by -9)
        """
        self._sp.terminate()
        if self.is_alive():
            self._sp.kill()

    def set_logging(self, log_func=None):
        """
        Sets logging function (when log_func is None, disable auto-logging)
        """
        self._log_records = None
        if not log_func:    # non-verbose
            self._log = None
        elif log_func == STORE:
            self._log_records = []
            self._log = lambda msg: self._log_records.append(msg)
        else:
            assert hasattr(log_func, '__call__'), ("logging function has to be"
                                                   " a function (%s)"
                                                   % type(log_func))
            self._log = log_func

    def log(self, prefix, data):
        """
        Logs to self._log using following format "$prefix$name: $data"
        :param prefix: Prefix ('>>', '<<', ...)
        :param data: Data to write.
        """
        if not self._log:    # Logging disabled
            return
        else:
            for line in data.splitlines():
                self._log("%s%s: %s" % (prefix, self.name, line))

    def get_log_records(self):
        """
        When STORE function for logging is used, this returns the stored
        messages.
        :return: all stored messages or None
        """
        if isinstance(self._log_records, list):
            return "\n".join(self._log_records)
        else:
            return "Log not stored, use session.set_logging(dexpect.STORE)"

    def get_pid(self):
        """
        Returns pid of the main command.
        :warning: This pid might not be the current foreground process!
        """
        return self._sp.pid

    @staticmethod
    def get_custom_children_pids(ppid=None, line_filter=None):
        """
        Returns childrens of specified parent pid, useful when you execute
        multiple processes inside the main process (eg. docker inside bash)
        :param ppid: parent pid
        :param line_filter: Function called on the splitted line (len=2)
        :return: list of tuples [($PID, $CMD), ...]
        """
        try:
            out = safe_execute("ps --ppid %s -o pid,cmd -h" % ppid)
        except ShellCmdError:   # No pids
            return []
        pids = []
        for line in out.splitlines():
            line = tuple(_.strip() for _ in line.strip().split(" ", 1))
            if len(line) != 2:
                raise ValueError("Fail to parse pid/name from output: %s"
                                 % out)
            if not line_filter or line_filter(line):
                pids.append(line)
        return pids

    def get_children_pids(self, line_filter=None):
        """
        Returns list of all children pids and process names.
        """
        return self.get_custom_children_pids(self.get_pid(), line_filter)

    def is_alive(self):
        """
        True when the main process is alive
        """
        return self._sp.poll() is None

    def get_status(self, timeout=5):
        """
        Wait for the process to exit and return its exit status, or None
        if the exit status is not available.
        :param timeout: Duration to wait for process to exit
        """
        for _ in xrange(timeout * 10):
            if self._sp.poll() is not None:
                return self._sp.poll()
            time.sleep(0.1)
        return None

    def send(self, data):
        """
        Sends data to the process
        :param data: string
        """
        self.log(">>", data)
        while data:
            written = os.write(self._pty, data)
            data = data[written:]
        # Probably not needed, re-enable in case of weird failures
        # termios.tcflush(self._pty, termios.TCIOFLUSH)
        # termios.tcdrain(self._pty)

    def sendline(self, data=''):
        """
        Sends data + EOL to the process
        :param data: string
        """
        self.send(data + self.linesep)

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
        # TODO: Usually this works fine, docker ignores ctrl+p,ctrl+q sequence
        #       without time.sleep(1) no matter what else we do.
        #            termios.tcflush(self._pty, termios.TCIOFLUSH)
        #            termios.tcdrain(self._pty)
        #            termios.tcsendbreak(self._pty, 0)
        return self.send(chr(mapping[char]))

    def kill(self, sig=signal.SIGKILL):
        """
        Sends signal to the main process
        :param sig: Which signal to send (default: signal.SIGKILL)
        """
        if self.is_alive():
            self._sp.send_signal(sig)

    def _try_read(self, timeout):
        """
        Try to read single piece of data
        :param timeout: Maximal wait for data to occur
        :return: Read data
        """
        read, _, _ = select.select([self._pty], [], [], timeout)
        if read:
            return os.read(self._pty, 1024)
        else:
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

    def read_until_output_matches(self, patterns, filter_func=lambda x: x,
                                  timeout=60, internal_timeout=None):
        """
        Read using read_nonblocking until a match is found using
        match_patterns, or until timeout expires. Before attempting to search
        for a match, the data is filtered using the filter_func function
        provided.

        @brief: Read from child using read_nonblocking until a pattern
                matches.
        :param patterns: List of strings (regular expression patterns)
        :param filter_func: Function to apply to the data read from the child
                before attempting to match it against the patterns (should take
                and return a string)
        :param timeout: The duration (in seconds) to wait until a match is
                found
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: Tuple containing the match index and the data read so far
        :raise ExpectTimeoutError: Raised if timeout expires
        :raise ExpectProcessTerminatedError: Raised if the child process
                terminates while waiting for output
        :raise ExpectError: Raised if an unknown error occurs
        """
        def match_patterns(cont, patterns):
            """
            Match cont against a list of patterns.

            Return the index of the first pattern that matches a substring of
            cont. None and empty strings in patterns are ignored.
            If no match is found, return None.

            :param cont: input string
            :param patterns: List of strings (regular expression patterns).
            """
            for i in range(len(patterns)):
                if not patterns[i]:
                    continue
                if re.search(patterns[i], cont):
                    return i
        out = ""
        end_time = time.time() + timeout
        while True:
            # Read data from child
            data = self.read_nonblocking(internal_timeout,
                                         end_time - time.time())
            # Look for patterns
            out += data
            match = match_patterns(filter_func(out), patterns)
            if match is not None:
                return match, out
            # timeout
            if end_time and time.time() > end_time:
                break

        # Check if the child has terminated
        for _ in xrange(50):
            if not self.is_alive():
                raise ExpectProcessTerminatedError(patterns, self.get_status(),
                                                   out)
            time.sleep(0.1)
        raise ExpectError(patterns, out)

    def read_until_last_line_matches(self, patterns, timeout=60,
                                     internal_timeout=None):
        """
        Read using read_nonblocking until the last non-empty line of the output
        matches one of the patterns (using match_patterns), or until timeout
        expires. Return a tuple containing the match index (or None if no match
        was found) and the data read so far.

        @brief: Read using read_nonblocking until the last non-empty line
                matches a pattern.

        :param patterns: A list of strings (regular expression patterns)
        :param timeout: The duration (in seconds) to wait until a match is
                found
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: A tuple containing the match index and the data read so far
        :raise ExpectTimeoutError: Raised if timeout expires
        :raise ExpectProcessTerminatedError: Raised if the child process
                terminates while waiting for output
        :raise ExpectError: Raised if an unknown error occurs
        """
        def get_last_nonempty_line(cont):
            """ Returns the last non-empty line """
            nonempty_lines = [l for l in cont.splitlines() if l.strip()]
            if nonempty_lines:
                return nonempty_lines[-1]
            else:
                return ""

        return self.read_until_output_matches(patterns, get_last_nonempty_line,
                                              timeout, internal_timeout)


class ShellSession(Expect):

    """
    This class provides all services of Expect.  In addition, it
    provides command running services, and a utility function to test the
    process for responsiveness.
    """

    def __init__(self, command, linesep="\n", prompt=r"[\#\$]\s*$",
                 status_test_command="echo $?", tty=True, timeout=5,
                 name=None, log_func=None):
        """
        Initialize the class and run command as a child process.

        :param command: Command to run, or None if accessing an already running
                server.
        :param linesep: Line separator to be appended to strings sent to the
                child process by sendline().
        :param prompt: Regular expression describing the shell's prompt line.
        :param status_test_command: Command to be used for getting the last
                exit status of commands run inside the shell (used by
                cmd_status_output() and friends).
        :param tty: tty mode (True = interactive, False = non-interactive)
        :param timeout: How long to test responsiveness (set to None when you
                        spawn many sessions and check the responsiveness later)
        :param name: name of this session (used in logging)
        :param log_func: logging function (file.write, logging.debug, ...)
        """
        # Init the superclass
        Expect.__init__(self, command, linesep, name, log_func)
        # Initialize functions, which are defined in self.set_tty
        self.prompt = None
        self.read_up_to_prompt = None
        self.get_last_status = None
        # Remember some attributes
        self._prompt = prompt
        self.status_test_command = status_test_command
        self.set_tty(tty, timeout)

    def set_tty(self, tty, timeout=5):
        """
        Sets the tty mode
        :param tty: True -> normal mode, False -> no echo mode (no prompt)
        :param timeout: How long to wait until the terminal is responsive
        :return: whether the terminal is responsive after the change
        :warning: Unresponsive tty can discard data on the input, DO check
                  the return status.
        """
        if tty:
            self.prompt = self._prompt
            self.read_up_to_prompt = self.read_up_to_prompt_tty
            self.get_last_status = self.get_last_status_tty
        else:
            self.prompt = ""
            self.read_up_to_prompt = self.read_up_to_prompt_nontty
            self.get_last_status = self.get_last_status_nontty
        return self.is_responsive(timeout)

    @classmethod
    def remove_command_echo(cls, cont, cmd):
        """
        Remove the sent command when present in the output.
        :param cont: context/output
        :param cmd: sent command
        :return: output without the echoed command
        """
        if cont and cont.splitlines()[0] == cmd:
            cont = "".join(cont.splitlines(True)[1:])
        return cont

    @classmethod
    def remove_last_nonempty_line(cls, cont):
        """
        Removes the last nonempty line (and all foregoing empty ones)
        """
        return "".join(cont.rstrip().splitlines(True)[:-1])

    def is_responsive(self, timeout=5.0):
        """
        Return True if the process responds to STDIN/terminal input.

        Send a newline to the child process (e.g. SSH or Telnet) and read some
        output using read_nonblocking().
        If all is OK, some output should be available (e.g. the shell prompt).
        In that case return True.  Otherwise return False.

        :param timeout: Time duration to wait before the process is considered
                unresponsive.
        """
        # Read all output that's waiting to be read, to make sure the output
        # we read next is in response to the newline sent
        self.read_nonblocking(internal_timeout=0, timeout=timeout)
        # Send a newline
        self.sendline()
        # Wait up to timeout seconds for some output from the child
        end_time = time.time() + timeout
        while time.time() < end_time:
            time.sleep(0.5)
            if self.read_nonblocking(0, end_time - time.time()):
                return True
        # No output -- report unresponsive
        return False

    def read_up_to_prompt_tty(self, timeout=60, internal_timeout=None):
        """
        Read using read_nonblocking until the last non-empty line of the output
        matches the prompt regular expression set by set_prompt, or until
        timeout expires.
        @brief: Read using read_nonblocking until the last non-empty line
                matches the prompt.

        :param timeout: The duration (in seconds) to wait until a match is
                found
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: The data read so far
        :raise ExpectTimeoutError: Raised if timeout expires
        :raise ExpectProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        :raise ExpectError: Raised if an unknown error occurs
        """
        return self.read_until_last_line_matches([self.prompt], timeout,
                                                 internal_timeout)[1]

    def read_up_to_prompt_nontty(self, timeout=60, internal_timeout=None):
        """
        Executes echo $RANDOM_LINE and waits for it's occurence.
        :param timeout: The duration (in seconds) to wait until a match is
        found
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: The data read so far
        :raise ExpectTimeoutError: Raised if timeout expires
        :raise ExpectProcessTerminatedError: Raised if the shell process
        terminates while waiting for output
        :raise ExpectError: Raised if an unknown error occurs
        """
        def get_last_nonempty_line(cont):
            """ Returns the last non-empty line """
            nonempty_lines = [l for l in cont.splitlines() if l.strip()]
            if nonempty_lines:
                return nonempty_lines[-1]
            else:
                return ""
        prompt = ("--< END OF THE COMMAND %s >--" % generate_random_string(12))
        cmd = 'RET=$?; echo; echo "$RET %s"' % prompt
        # 2xecho makes sure it's on the new line
        self.sendline(cmd)
        prompt = ["^" + self.prompt + r'(\d*) ' + prompt + "$"]
        _, out = super(ShellSession,
                       self).read_until_last_line_matches(prompt, timeout,
                                                          internal_timeout)
        self._return_number = int(re.match(prompt[0],
                                           get_last_nonempty_line(out)
                                           ).groups()[0])
        out = re.sub(r'%s\r?\n?' % re.escape(cmd), '', out)
        return out

    def cmd_output(self, cmd, timeout=60, internal_timeout=None):
        """
        Send a command and return its output.

        :param cmd: Command to send (must not contain newline characters)
        :param timeout: The duration (in seconds) to wait for the prompt to
                return
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: The output of cmd
        :raise ShellTimeoutError: Raised if timeout expires
        :raise ShellProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        :raise ShellError: Raised if an unknown error occurs
        """
        # TODO: Do we need logging? eg. if self.verbose: $name: msg
        # logging.debug("Sending command: %s" % cmd)
        self.read_nonblocking(0, timeout)
        self.sendline(cmd)
        try:
            out = self.read_up_to_prompt(timeout, internal_timeout)
        except ExpectError, exc:
            out = self.remove_command_echo(exc.output, cmd)
            if isinstance(exc, ExpectTimeoutError):
                raise ShellTimeoutError(cmd, out)
            elif isinstance(exc, ExpectProcessTerminatedError):
                raise ShellProcessTerminatedError(cmd, exc.status, out)
            else:
                raise ShellError(cmd, out)

        # Remove the echoed command and the final shell prompt
        return self.remove_last_nonempty_line(self.remove_command_echo(out,
                                                                       cmd))

    def get_last_status_tty(self, internal_timeout=None):
        """
        Get status output of the last command when in tty mode.
        :param internal_timeout: Internal timeout of the discovering command
        :return: exit status (integer)
        :raise ShellError: When unable to obtain/parse the code
        """
        # Send the 'echo $?' (or equivalent) command to get the exit status
        stat = self.cmd_output(self.status_test_command, 10, internal_timeout)
        # Get the first line consisting of digits only
        digit_lines = [_ for _ in stat.splitlines() if _.strip().isdigit()]
        if digit_lines:
            return int(digit_lines[0].strip())
        else:
            raise ShellError(self.status_test_command, stat)

    def get_last_status_nontty(self, internal_timeout=None):
        """
        Get status output of the last command when in non-tty mode
        :param internal_timeout: Ignored
        :return: exit status (integer)
        :warning: This function only works when used after read_up_to_prompt
                  (eg. when you use all cmd* functions)
        """
        return self._return_number

    def cmd_status_output(self, cmd, timeout=60, internal_timeout=None):
        """
        Send a command and return its exit status and output.

        :param cmd: Command to send (must not contain newline characters)
        :param timeout: The duration (in seconds) to wait for the prompt to
                return
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: A tuple (status, output) where status is the exit status and
                output is the output of cmd
        :raise ShellTimeoutError: Raised if timeout expires
        :raise ShellProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        :raise ShellStatusError: Raised if the exit status cannot be obtained
        :raise ShellError: Raised if an unknown error occurs
        """
        out = self.cmd_output(cmd, timeout, internal_timeout)
        try:
            stat = self.get_last_status(internal_timeout)
        except ShellError:
            raise ShellStatusError(cmd, out)
        return stat, out

    def cmd_status(self, cmd, timeout=60, internal_timeout=None):
        """
        Send a command and return its exit status.

        :param cmd: Command to send (must not contain newline characters)
        :param timeout: The duration (in seconds) to wait for the prompt to
                return
        :param internal_timeout: The timeout to pass to read_nonblocking
        :return: The exit status of cmd
        :raise ShellTimeoutError: Raised if timeout expires
        :raise ShellProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        :raise ShellStatusError: Raised if the exit status cannot be obtained
        :raise ShellError: Raised if an unknown error occurs
        """
        return self.cmd_status_output(cmd, timeout, internal_timeout)[0]

    def cmd(self, cmd, timeout=60, internal_timeout=None, ok_status=(0,),
            ignore_all_errors=False):
        """
        Send a command and return its output. If the command's exit status is
        nonzero, raise an exception.

        :param cmd: Command to send (must not contain newline characters)
        :param timeout: The duration (in seconds) to wait for the prompt to
                return
        :param internal_timeout: The timeout to pass to read_nonblocking
        :param ok_status: do not raise ShellCmdError in case that exit status
                is one of ok_status. (default is [0,])
        :param ignore_all_errors: toggles whether or not an exception should be
                raised  on any error.

        :return: The output of cmd
        :raise ShellTimeoutError: Raised if timeout expires
        :raise ShellProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        :raise ShellError: Raised if the exit status cannot be obtained or if
                an unknown error occurs
        :raise ShellStatusError: Raised if the exit status cannot be obtained
        :raise ShellError: Raised if an unknown error occurs
        :raise ShellCmdError: Raised if the exit status is nonzero
        """
        try:
            s, o = self.cmd_status_output(cmd, timeout, internal_timeout)
            if s not in ok_status:
                raise ShellCmdError(cmd, s, o)
            return o
        except Exception:
            if ignore_all_errors:
                pass
            else:
                raise


if __name__ == "__main__":
    # Demonstration
    a = ShellSession()  # Get bash login
    print "Starting docker ..."
    a.sendline("docker run -t -i fedora bash")  # Start docker
    print a.read_nonblocking(1, 10)  # see if it started
    a.is_responsive(2)  # wait for stabilization...
    print "PIDs"
    print a.get_children_pids(lambda line: line[1].startswith("docker"))
    print "Output of ls in container"
    print a.cmd("ls")   # in docker command
    print "Dettaching ..."
    a.send_control('p')
    time.sleep(1)   # Somehow docker doesn't accept ctrl+p ctrl+q without sleep
    # termios.tcflush(a._pty, termios.TCIOFLUSH)
    # termios.tcdrain(a._pty)
    a.send_control('q')     # dettach
    print a.read_nonblocking(1, 10)     # see what happened
    a.is_responsive(2)  # wait for stabilization...
    print "Output of ls on host"
    print a.cmd("ls")
    a.sendline("docker attach `docker ps -q -l`")   # attach it
    print a.read_nonblocking(1, 10)     # see what happened
    a.is_responsive(2)  # wait for stabilization...
    print "PIDs"
    print a.get_children_pids()
    print "Output of ls in container"
    print a.cmd("ls")
    a.send_control('d')
    print a.read_nonblocking(1, 10)     # see what happened
    a.send_control('d')
    print a.read_nonblocking(1, 10)     # see what happened
    print "Process terminated..."
    print a.is_alive()
    """
    import sys
    sys.path.append("/opt/eclipse/plugins/org.python.pydev_2.8.2.2013081517/pysrc")
    import pydevd
    pydevd.settrace("127.0.0.1")
    """
    b = ShellSession()
    b.cmd_output("echo ahoj; sleep 1; ls")
    print "C"
    b.cmd("ls")
    print "AAA"
    b.sendline("docker run -i fedora bash")
    b.set_tty(False)
    b.cmd("ls")
    # b.cmd("ls asdfasdf")    # uncomment this to see the failure

    b = ShellSession()    # No echo login
    print "Output of ls on host"
    print b.cmd("ls")
    print "Starting docker ..."
    did = b.cmd("docker run -d -i fedora bash")  # Start docker
    print "Docker ID"
    print did
    print "Output of ls on host"
    print b.cmd("ls")
    b.sendline("docker attach %s" % did)   # attach it
    print b.set_tty(False)
    print b.read_nonblocking(1, 10)     # see what happened
    print "Output of ls in container"
    print b.cmd("ls")
    pid = b.get_children_pids(lambda line: line[1].startswith("docker"))
    print pid
    b.send_control('d')
    print b.set_tty(False)
    print b.get_children_pids(lambda line: line[1].startswith("docker"))
