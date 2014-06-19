"""
Test usage of docker 'kill' command

initialize:
1) start VM with test command
run_once:
2) execute docker kill
postprocess:
3) analyze results
"""
import itertools
import random
import time

from autotest.client.shared.utils import wait_for
from dockertest import config, subtest, xceptions
from dockertest.containers import DockerContainers
from dockertest.dockercmd import AsyncDockerCmd, DockerCmd, NoFailDockerCmd
from dockertest.images import DockerImage
from dockertest.output import OutputGood
import os


# TODO: Not all named signals seems to be supported with docker0.9
SIGNAL_MAP = {1: 'HUP', 2: 'INT', 3: 'QUIT', 4: 'ILL', 5: 'TRAP', 6: 'ABRT',
              7: 'BUS', 8: 'FPE', 9: 'KILL', 10: 'USR1', 11: 'SEGV',
              12: 'USR2', 13: 'PIPE', 14: 'ALRM', 15: 'TERM', 16: 'STKFLT',
              17: 'CHLD', 18: 'CONT', 19: 'STOP', 20: 'TSTP', 21: 'TTIN',
              22: 'TTOU', 23: 'URG', 24: 'XCPU', 25: 'XFSZ', 26: 'VTALRM',
              27: 'PROF', 28: 'WINCH', 29: 'IO', 30: 'PWR', 31: 'SYS'}


# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103
class kill(subtest.SubSubtestCaller):

    """ Subtest caller """
    config_section = 'docker_cli/kill'


class kill_base(subtest.SubSubtest):

    """ Base class """

    def _init_container_normal(self, name):
        if self.config.get('run_options_csv'):
            subargs = [arg for arg in
                       self.config['run_options_csv'].split(',')]
        else:
            subargs = []
        subargs.append("--name %s" % name)
        fin = DockerImage.full_name_from_defaults(self.config)
        subargs.append(fin)
        subargs.append("bash")
        subargs.append("-c")
        subargs.append(self.config['exec_cmd'])
        container = AsyncDockerCmd(self.parent_subtest, 'run', subargs)
        self.sub_stuff['container_cmd'] = container
        container.execute()

    def _init_container_attached(self, name):
        if self.config.get('run_options_csv'):
            subargs = [arg for arg in
                       self.config['run_options_csv'].split(',')]
        else:
            subargs = []
        subargs.append("--name %s" % name)
        fin = DockerImage.full_name_from_defaults(self.config)
        subargs.append(fin)
        subargs.append("bash")
        subargs.append("-c")
        subargs.append(self.config['exec_cmd'])
        container = NoFailDockerCmd(self.parent_subtest, 'run', subargs)
        self.sub_stuff['container_cmd'] = container
        container.execute()

        if self.config.get('attach_options_csv'):
            subargs = [arg for arg in
                       self.config['attach_options_csv'].split(',')]
        else:
            subargs = []
        subargs.append(name)
        container = AsyncDockerCmd(self.parent_subtest, 'attach', subargs)
        self.sub_stuff['container_cmd'] = container  # overwrites finished cmd
        container.execute()

    def initialize(self):
        super(kill_base, self).initialize()
        # Prepare a container
        docker_containers = DockerContainers(self.parent_subtest)
        name = docker_containers.get_unique_name("test", length=4)
        self.sub_stuff['container_name'] = name
        config.none_if_empty(self.config)
        if self.config.get('run_container_attached'):
            self._init_container_attached(name)
        else:
            self._init_container_normal(name)

        time.sleep(self.config['wait_start'])

        # Prepare the "kill" command
        if self.config.get('kill_options_csv'):
            subargs = [arg for arg in
                       self.config['kill_options_csv'].split(',')]
        else:
            subargs = []
        subargs.append(name)
        self._populate_kill_cmds(subargs)

    def _create_kill_sequence(self):
        """
        Creates/loads the kill number sequence. Format of this sequence is
        [$MAP] [$LONG] $SIG_NUM ... (eg. 2 M 23 22 M L 12 1 M 27)
        MAP => map the signal number to symbolic name
        LONG => use --signal= (instead of -s)
        """
        map_signals = self.config.get('kill_map_signals')
        if not self.config.get('signals_sequence'):
            sequence = []
            signals = [int(sig) for sig in self.config['kill_signals'].split()]
            signals = range(*signals)   # pylint: disable=W0142
            skipped_signals = (int(_) for _ in
                               self.config.get('skip_signals', "").split())
            for skipped_signal in skipped_signals:
                try:
                    signals.remove(skipped_signal)
                except ValueError:
                    pass
            for _ in xrange(self.config['no_iterations']):
                if (map_signals is True or (map_signals is None and
                                            random.choice((True, False)))):
                    sequence.append("M")    # mapped signal (USR1)
                if random.choice((True, False)):
                    sequence.append("L")    # long cmd (--signal=)
                sequence.append(str(random.choice(signals)))
        else:
            sequence = self.config['signals_sequence'].split()
        return sequence

    def _populate_kill_cmds(self, extra_subargs):
        sequence = self._create_kill_sequence()
        signals_sequence = []
        kill_cmds = []
        mapped = False
        sig_long = False
        sigproxy = self.config.get('kill_sigproxy')
        for item in sequence:
            if item == "M":
                mapped = True
            elif item == "L":
                sig_long = True
            else:
                signal = int(item)
                signals_sequence.append(signal)
                if sigproxy:
                    kill_cmds.append(False)     # False => kill the docker_cmd
                    continue
                if mapped:
                    signal = SIGNAL_MAP.get(signal, signal)
                    mapped = False
                if sig_long:
                    subargs = ["--signal=%s" % signal] + extra_subargs
                    sig_long = False
                else:
                    subargs = ["-s %s" % signal] + extra_subargs
                kill_cmds.append(DockerCmd(self.parent_subtest, 'kill',
                                           subargs, verbose=False))

        # Kill -9 is the last one :-)
        signal = 9
        signals_sequence.append(signal)
        if self.config.get('kill_map_signals'):
            signal = SIGNAL_MAP.get(signal, signal)
        kill_cmds.append(DockerCmd(self.parent_subtest, 'kill',
                                   ["-s %s" % signal] + extra_subargs))

        if sigproxy:
            self.logdebug("kill_command_example: Killing directly the "
                          "container process.")
        else:
            self.logdebug("kill_command_example: %s", kill_cmds[0])
        self.logdebug("signals_sequence: %s", " ".join(sequence))
        self.sub_stuff['signals_sequence'] = signals_sequence
        self.sub_stuff['kill_cmds'] = kill_cmds

    def postprocess(self):
        super(kill_base, self).postprocess()
        for kill_result in self.sub_stuff.get('kill_results', []):
            OutputGood(kill_result)
            self.failif(kill_result.exit_status != 0, "Exit status of the %s "
                        "command was not 0 (%s)"
                        % (kill_result.command, kill_result.exit_status))
        # FIXME: Return number of container changed:
        # with tty=on `docker kill` => 0
        # with tty=off `docker kill` => 255
        # bash `kill -9 $cont_pid` => 137
        # if 'container_results' in self.sub_stuff:
        #     OutputGood(self.sub_stuff['container_results'])
        #     self.failif((self.sub_stuff['container_results'].exit_status
        #                  not in (255, -9)), "Exit status of the docker run "
        #                 "command wasn't 255, nor -9 (%s)"
        #                 % self.sub_stuff['container_results'].exit_status)

    def pre_cleanup(self):
        pass

    def container_cleanup(self):
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

    def cleanup(self):
        super(kill_base, self).cleanup()
        cleanup_log = []
        for method in ('pre_cleanup', 'container_cleanup'):
            try:
                getattr(self, method)()
            except (xceptions.AutotestError, xceptions.DockerExecError,
                    xceptions.DockerTestError, KeyError, ValueError,
                    IOError), details:
                cleanup_log.append("%s failed: %s" % (method, details))
        if cleanup_log:
            msg = "Cleanup failed:\n%s" % "\n".join(cleanup_log)
            self.logerror(msg)  # message is not logged nicely in exc
            raise xceptions.DockerTestError(msg)


class kill_check_base(kill_base):

    """ Base class for signal-check based tests """

    def run_once(self):
        class Output(object):

            def __init__(self, container):
                self.container = container
                self.idx = len(container.stdout)

            def get(self, idx=None):
                if idx is None:
                    idx = self.idx
                out = container_cmd.stdout.splitlines()
                self.idx = len(out)
                return out[idx:]
        # Execute the kill command
        super(kill_check_base, self).run_once()
        container_cmd = self.sub_stuff['container_cmd']
        container_out = Output(container_cmd)
        kill_cmds = self.sub_stuff['kill_cmds']
        signals_sequence = self.sub_stuff['signals_sequence']
        _check = self.config['check_stdout']
        timeout = self.config['stress_cmd_timeout']
        self.sub_stuff['kill_results'] = []
        stopped_log = False
        _container_pid = container_cmd.process_id
        for cmd, signal in itertools.izip(kill_cmds, signals_sequence):
            if cmd is not False:    # Custom command, execute&check cmd status
                result = cmd.execute()
                if signal == -1:
                    if result.exit_status == 0:    # Any bad signal
                        msg = ("Kill command %s returned zero status when "
                               "using bad signal."
                               % (self.sub_stuff['kill_results'][-1].command))
                        raise xceptions.DockerTestFail(msg)
                    continue
                self.sub_stuff['kill_results'].append(result)
                if result.exit_status != 0:
                    msg = ("Kill command %s returned non-zero status. (%s)"
                           % (self.sub_stuff['kill_results'][-1].command,
                              self.sub_stuff['kill_results'][-1].exit_status))
                    raise xceptions.DockerTestFail(msg)
            else:   # Send signal directly to the docker process
                self.logdebug("Sending signal %s directly to container pid %s",
                              signal, _container_pid)
                os.kill(_container_pid, signal)
            if signal == 9 or signal is None:   # SIGTERM
                for _ in xrange(50):    # wait for command to finish
                    if container_cmd.done:
                        break
                    time.sleep(0.1)
                else:
                    raise xceptions.DockerTestFail("Container process did not"
                                                   " finish when kill -9 "
                                                   "was executed.")
                self.sub_stuff['container_results'] = container_cmd.wait()
            elif signal == 19:    # SIGSTOP can't be caught
                if stopped_log is False:
                    stopped_log = set()
            elif signal == 18:  # SIGCONT, check previous payload
                # TODO: Signals 20, 21 and 22 are not reported after SIGCONT
                #       even thought they are reported when docker is not
                #       stopped.
                if stopped_log:
                    endtime = time.time() + timeout
                    _idx = container_out.idx
                    line = None
                    out = None
                    while endtime > time.time():
                        try:
                            out = container_out.get(_idx)
                            for line in [_check % sig for sig in stopped_log]:
                                out.remove(line)
                            break
                        except ValueError:
                            pass
                    else:
                        msg = ("Not all signals were handled inside container "
                               "after SIGCONT execution.\nExpected output "
                               "(unordered):\n  %s\nActual container output:\n"
                               "  %s\nFirst missing line:\n  %s"
                               % ("\n  ".join([_check % sig
                                               for sig in stopped_log]),
                                  "\n  ".join(container_out.get(_idx)), line))
                        raise xceptions.DockerTestFail(msg)
                stopped_log = False
            elif stopped_log is not False:  # if not false it's set()
                if cmd is not False:
                    # Using docker kill: signals are forwarded when the cont
                    #                    is ready
                    # disable E1101, when stopped_log is not False, it's []
                    stopped_log.add(signal)  # pylint: disable=E1101
                # else: using proxy:  signals are not forwarded by proxy, when
                #                     proxy is SIGSTOPped.
            else:
                _idx = container_out.idx
                check = _check % signal
                output_matches = lambda: check in container_out.get(_idx)
                # Wait until the signal gets logged
                if wait_for(output_matches, timeout, step=0) is None:
                    msg = ("Signal %s not handled inside container.\nExpected "
                           "output:\n  %s\nActual container output:\n  %s"
                           % (signal, check,
                              "\n  ".join(container_out.get(_idx))))
                    raise xceptions.DockerTestFail(msg)


class random_num(kill_check_base):

    """
    Test usage of docker 'kill' command (series of random valid numeric
    signals)

    initialize:
    1) start VM with test command
    2) create random sequence of kill signals
    run_once:
    3) execute series of kill signals (NUMERIC) followed with the output check
    3b) in case of SIGSTOP it stores following signals until SIGCONT and
        verifies they were all handled properly
    4) sends docker kill -9 and verifies docker was killed
    postprocess:
    5) analyze results
    """
    pass


class random_name(kill_check_base):

    """
    Test usage of docker 'kill' command (series of random correctly named
    signals)

    initialize:
    1) start VM with test command
    2) create random sequence of kill signals
    run_once:
    3) execute series of kill signals (NAME) followed with the output check
    3b) in case of SIGSTOP it stores following signals until SIGCONT and
        verifies they were all handled properly
    4) sends docker kill -9 and verifies docker was killed
    postprocess:
    5) analyze results
    """
    pass
