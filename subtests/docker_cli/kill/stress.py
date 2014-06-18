import time

from autotest.client import utils
from dockertest import xceptions
from dockertest.dockercmd import DockerCmd
from kill import kill_base, SIGNAL_MAP


class stress(kill_base):

    """
    Test usage of docker 'kill' command (lots of various kills and then check)

    initialize:
    1) start VM with test command
    2) create sequence of signals and prepare bash script, which executes them
       quickly one by one.
    run_once:
    3) executes the bash script, which executes series of kills quickly.
    4) sends docker kill -9 and verifies docker was killed
    postprocess:
    5) analyze results
    """

    def _populate_kill_cmds(self, extra_subargs):
        sequence = self._create_kill_sequence()
        signals_set = set()
        signals_sequence = []
        stopped = False
        mapped = False
        sigproxy = self.config.get('kill_sigproxy')
        for item in sequence:
            if item == "M":
                mapped = True
            elif item == "L":   # Long is ignored in this test
                pass
            else:
                signal = int(item)
                if signal == 18:
                    if stopped:
                        signals_set.add(stopped)
                    stopped = False
                    signals_set.add(signal)
                elif signal == 19:
                    stopped = set()
                else:
                    signals_set.add(str(signal))
                if mapped:
                    signal = SIGNAL_MAP.get(signal, signal)
                    mapped = False
                signals_sequence.append(str(signal))

        subargs = ["-s $SIGNAL"] + extra_subargs
        if sigproxy:
            pid = self.sub_stuff['container_cmd'].process_id
            cmd = "kill -$SIGNAL %s" % pid
        else:
            cmd = DockerCmd(self.parent_subtest, 'kill', subargs).command
        cmd = ("for SIGNAL in %s; do %s || exit 255; done"
               % (" ".join(signals_sequence), cmd))
        self.sub_stuff['kill_cmds'] = [cmd]
        # kill -9
        if sigproxy:
            self.sub_stuff['kill_cmds'].append(False)
        else:
            self.sub_stuff['kill_cmds'].append(DockerCmd(self.parent_subtest,
                                                         'kill',
                                                         extra_subargs,
                                                         verbose=False))
        self.sub_stuff['signals_set'] = signals_set

        self.logdebug("kill_command: %s", cmd)
        self.logdebug("signals_sequence: %s", " ".join(sequence))

    def run_once(self):
        # Execute the kill command
        super(stress, self).run_once()
        container_cmd = self.sub_stuff['container_cmd']
        kill_cmds = self.sub_stuff['kill_cmds']
        signals_set = self.sub_stuff['signals_set']
        timeout = self.config['stress_cmd_timeout']
        _check = self.config['check_stdout']
        self.sub_stuff['kill_results'] = [utils.run(kill_cmds[0],
                                                    verbose=True)]
        endtime = time.time() + timeout
        line = None
        out = None
        while endtime > time.time():
            try:
                out = container_cmd.stdout.splitlines()
                for line in [_check % sig for sig in signals_set]:
                    out.remove(line)
                break
            except ValueError:
                pass
        else:
            msg = ("Not all signals were handled inside container after quick."
                   " series of kill commands.\n"
                   "Expected output (unordered):\n  %s\nActual container "
                   "output:\n  %s\nFirst missing line:\n  %s"
                   % ("\n  ".join([_check % sig
                                   for sig in signals_set]),
                      "\n  ".join(container_cmd.stdout.splitlines()), line))
            raise xceptions.DockerTestFail(msg)
        # Kill -9
        if kill_cmds[1] is not False:   # Custom kill command
            self.sub_stuff['kill_results'].append(kill_cmds[1].execute())
        else:   # kill the container process
            container_cmd._async_job.sp.send_signal(9)
        for _ in xrange(50):
            if container_cmd.done:
                break
            time.sleep(0.1)
        else:
            raise xceptions.DockerTestFail("Container process did not"
                                           " finish when kill -9 "
                                           "was executed.")
        self.sub_stuff['container_results'] = container_cmd.wait()
