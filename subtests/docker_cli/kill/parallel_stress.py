import time

from autotest.client import utils
from dockertest.dockercmd import DockerCmd
from kill import kill_base
from dockertest import xceptions


class parallel_stress(kill_base):

    """
    Test usage of docker 'kill' command (simultaneous kills)

    initialize:
    1) start VM with test command
    2) creates command for each signal, which kills the docker in a loop
    run_once:
    3) executes all the kill scripts to run in parallel
    4) stops the kill scripts
    5) sends docker kill -9 and verifies docker was killed
    postprocess:
    6) analyze results
    """

    def _populate_kill_cmds(self, extra_subargs):
        signals = [int(sig) for sig in self.config['kill_signals'].split()]
        signals = range(*signals)
        for noncatchable_signal in (9, 17):
            try:
                signals.remove(noncatchable_signal)
            except ValueError:
                pass

        cmds = []
        for signal in signals:
            subargs = ["-s %s" % signal] + extra_subargs
            docker_cmd = DockerCmd(self.parent_subtest, 'kill', subargs)
            cmd = ("while [ -e %s/docker_kill_stress ]; "
                   "do %s || exit 255; done" % (self.tmpdir,
                                                docker_cmd.command))
            cmds.append(cmd)
        self.sub_stuff['kill_cmds'] = cmds

        signals.remove(19)  # SIGSTOP is also not catchable
        self.sub_stuff['signals_set'] = signals

        # kill -9
        self.sub_stuff['kill_docker'] = DockerCmd(self.parent_subtest, 'kill',
                                                  extra_subargs)

    def run_once(self):
        # Execute the kill command
        super(parallel_stress, self).run_once()
        container_cmd = self.sub_stuff['container_cmd']
        kill_cmds = self.sub_stuff['kill_cmds']
        signals_set = self.sub_stuff['signals_set']
        _check = self.config['check_stdout']

        # Enable stress loops
        self.sub_stuff['touch_result'] = utils.run("touch %s/docker_kill_"
                                                   "stress" % self.tmpdir)
        # Execute stress loops
        self.sub_stuff['kill_jobs'] = []
        for cmd in kill_cmds:
            job = utils.AsyncJob(cmd, verbose=True)
            self.sub_stuff['kill_jobs'].append(job)

        # Wait test_length (while checking for failures)
        endtime = time.time() + self.config['test_length']
        while endtime > time.time():
            for job in self.sub_stuff['kill_jobs']:
                if job.sp.poll() is not None:   # process finished
                    for job in self.sub_stuff.get('kill_jobs', []):
                        self.logerror("cmd %s (%s)", job.command,
                                      job.sp.poll())
                    out = utils.run("ls %s/docker_kill_stress" % self.tmpdir,
                                    ignore_status=True).exit_status
                    self.logerror("ls %s/docker_kill_stress (%s)", self.tmpdir,
                                  out)
                    raise xceptions.DockerTestFail("stress command finished "
                                                   "unexpectedly, see log for "
                                                   "details.")

        # Stop stressers
        cmd = "rm -f %s/docker_kill_stress" % self.tmpdir
        self.sub_stuff['rm_result'] = utils.run(cmd)

        self.sub_stuff['kill_results'] = []
        for job in self.sub_stuff['kill_jobs']:
            try:
                self.sub_stuff['kill_results'].append(job.wait_for(5))
            except Exception, details:
                self.logerror("Job %s did not finish: %s", job.command,
                              str(details))
        del self.sub_stuff['kill_jobs']

        # Check the output
        endtime = time.time() + self.config['stress_cmd_timeout']
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
        cmd = self.sub_stuff['kill_docker']
        self.sub_stuff['kill_results'].append(cmd.execute())
        for _ in xrange(50):
            if container_cmd.done:
                break
            time.sleep(0.1)
        else:
            raise xceptions.DockerTestFail("Container process did not"
                                           " finish when kill -9 "
                                           "was executed.")
        self.sub_stuff['container_results'] = container_cmd.wait()

    def pre_cleanup(self):
        if not self.sub_stuff.get('rm_result'):
            utils.run("rm -f %s/docker_kill_stress" % self.tmpdir,
                      ignore_status=True)
            for job in self.sub_stuff.get('kill_jobs', []):
                try:
                    job.wait_for(5)     # AsyncJob destroys it on timeout
                except Exception, details:
                    msg = ("Job %s did not finish: %s" % (job.command,
                                                          details))
                    raise xceptions.DockerTestFail(msg)
        for result in ('touch_result', 'rm_result'):
            if result in self.sub_stuff:
                result = self.sub_stuff[result]
                self.failif(result.exit_status != 0,
                            "Exit status of the %s command was not 0 (%s)"
                            % (result.command, result.exit_status))
