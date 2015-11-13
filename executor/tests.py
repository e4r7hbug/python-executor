# Automated tests for the `executor' module.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: November 13, 2015
# URL: https://executor.readthedocs.org

"""Automated tests for the `executor` package."""

# Standard library modules.
import logging
import os
import random
import shlex
import shutil
import socket
import sys
import tempfile
import time
import unittest
import uuid

# External dependencies.
from humanfriendly import Timer, dedent
from humanfriendly.compat import StringIO

# Modules included in our package.
from executor import (
    CommandNotFound,
    ControllableProcess,
    ExternalCommand,
    ExternalCommandFailed,
    execute,
    quote,
    which,
)
from executor.cli import (
    CommandTimedOut,
    apply_fudge_factor,
    get_lock_path,
    main,
    run_command,
)
from executor.concurrent import CommandPool, CommandPoolFailed
from executor.contexts import LocalContext, RemoteContext
from executor.ssh.client import (
    DEFAULT_CONNECT_TIMEOUT,
    RemoteCommand,
    RemoteCommandFailed,
    RemoteConnectFailed,
    foreach,
)
from executor.ssh.server import SSHServer


class ExecutorTestCase(unittest.TestCase):

    """Container for the `executor` test suite."""

    def setUp(self):
        """Set up (colored) logging to the terminal."""
        try:
            # Optional external dependency.
            import coloredlogs
            coloredlogs.install()
            coloredlogs.set_level(logging.DEBUG)
        except ImportError:
            logging.basicConfig(level=logging.DEBUG)

    def assertRaises(self, type, callable, *args, **kw):
        """Replacement for :func:`unittest.TestCase.assertRaises()` that returns the exception."""
        try:
            callable(*args, **kw)
        except Exception as e:
            if isinstance(e, type):
                # Return the expected exception as a regular return value.
                return e
            else:
                # Don't swallow exceptions we can't handle.
                raise
        else:
            assert False, "Expected an exception to be raised!"

    def test_argument_validation(self):
        """Make sure the external command constructor requires a command argument."""
        self.assertRaises(TypeError, ExternalCommand)

    def test_program_searching(self):
        """Make sure which() works as expected."""
        assert which('python')
        assert not which('a-program-name-that-no-one-would-ever-use')

    def test_status_code_checking(self):
        """Make sure that status code handling is sane."""
        assert execute('true') is True
        assert execute('false', check=False) is False
        # Make sure execute('false') raises an exception.
        self.assertRaises(ExternalCommandFailed, execute, 'false')
        # Make sure execute('exit 42') raises an exception.
        e = self.assertRaises(ExternalCommandFailed, execute, 'exit 42')
        # Make sure the exception has the expected properties.
        self.assertEqual(e.command.command_line, ['bash', '-c', 'exit 42'])
        self.assertEqual(e.returncode, 42)
        # Make sure CommandNotFound exceptions work for shell commands.
        self.assertRaises(CommandNotFound, execute, 'a-program-name-that-no-one-would-ever-use')
        # Make sure CommandNotFound exceptions work for non-shell commands.
        self.assertRaises(CommandNotFound, execute, 'a-program-name-that-no-one-would-ever-use', 'just-an-argument')

    def test_stdin(self):
        """Make sure standard input can be provided to external commands."""
        assert execute('tr', 'a-z', 'A-Z', input='test', capture=True) == 'TEST'

    def test_stdout(self):
        """Make sure standard output of external commands can be captured."""
        assert execute('echo', 'this is a test', capture=True) == 'this is a test'
        assert execute('echo', '-e', r'line 1\nline 2', capture=True) == 'line 1\nline 2\n'
        # I don't know how to test for the effect of silent=True in a practical
        # way without creating the largest test in this test suite :-). The
        # least I can do is make sure the keyword argument is accepted and the
        # code runs without exceptions in supported environments.
        assert execute('echo', 'this is a test', silent=True) is True

    def test_stderr(self):
        """Make sure standard error of external commands can be captured."""
        stdout_value = 'this goes to standard output'
        stderr_value = 'and this goes to the standard error stream'
        shell_command = 'echo %s; echo %s >&2' % (stdout_value, stderr_value)
        cmd = ExternalCommand(shell_command, capture=True, capture_stderr=True)
        cmd.start()
        assert stdout_value in cmd.decoded_stdout
        assert stderr_value in cmd.decoded_stderr

    def test_merged_streams(self):
        """Make sure standard output/error of external commands can be captured together."""
        stdout_value = 'this goes to standard output'
        stderr_value = 'and this goes to the standard error stream'
        shell_command = 'echo %s; echo %s >&2' % (stdout_value, stderr_value)
        cmd = ExternalCommand(shell_command, capture=True, merge_streams=True)
        cmd.start()
        assert stdout_value in cmd.decoded_stdout
        assert stderr_value in cmd.decoded_stdout
        assert stdout_value not in (cmd.decoded_stderr or '')
        assert stderr_value not in (cmd.decoded_stderr or '')

    def test_stdout_to_file(self):
        """Make sure the standard output stream of external commands can be redirected and appended to a file."""
        fd, filename = tempfile.mkstemp(prefix='executor-', suffix='-stdout.txt')
        with open(filename, 'w') as handle:
            handle.write('existing contents\n')
        with open(filename, 'a') as handle:
            execute('echo appended output', stdout_file=handle)
        # Make sure the file was _not_ removed.
        assert os.path.isfile(filename)
        # Make sure the output was appended.
        with open(filename) as handle:
            lines = [line.strip() for line in handle]
        assert lines == ['existing contents', 'appended output']

    def test_stderr_to_file(self):
        """Make sure the standard error stream of external commands can be redirected and appended to a file."""
        fd, filename = tempfile.mkstemp(prefix='executor-', suffix='-stderr.txt')
        with open(filename, 'w') as handle:
            handle.write('existing contents\n')
        with open(filename, 'a') as handle:
            execute('echo appended output 1>&2', stderr_file=handle)
        # Make sure the file was _not_ removed.
        assert os.path.isfile(filename)
        # Make sure the output was appended.
        with open(filename) as handle:
            lines = [line.strip() for line in handle]
        assert lines == ['existing contents', 'appended output']

    def test_merged_streams_to_file(self):
        """Make sure the standard streams of external commands can be merged, redirected and appended to a file."""
        fd, filename = tempfile.mkstemp(prefix='executor-', suffix='-merged.txt')
        with open(filename, 'w') as handle:
            handle.write('existing contents\n')
        with open(filename, 'a') as handle:
            execute('echo standard output; echo standard error 1>&2', stdout_file=handle, stderr_file=handle)
        # Make sure the file was _not_ removed.
        assert os.path.isfile(filename)
        # Make sure the output was appended.
        with open(filename) as handle:
            lines = [line.strip() for line in handle]
        assert lines == ['existing contents', 'standard output', 'standard error']

    def test_asynchronous_stream_to_file(self):
        """Make sure the standard streams can be redirected to a file and asynchronously stream output to that file."""
        fd, filename = tempfile.mkstemp(prefix='executor-', suffix='-streaming.txt')
        with open(filename, 'w') as handle:
            cmd = ExternalCommand('for ((i=0; i<25; i++)); do command echo $i; sleep 0.1; done',
                                  async=True, stdout_file=handle)
            cmd.start()

        def expect_some_output():
            """Expect some but not all output to be readable at some point."""
            with open(filename) as handle:
                lines = list(handle)
                assert len(lines) > 0
                assert len(lines) < 25

        def expect_most_output():
            """Expect most but not all output to be readable at some point."""
            with open(filename) as handle:
                lines = list(handle)
                assert len(lines) > 15
                assert len(lines) < 25

        def expect_all_output():
            """Expect all output to be readable at some point."""
            with open(filename) as handle:
                lines = list(handle)
                assert len(lines) == 25

        retry(expect_some_output, 10)
        retry(expect_most_output, 20)
        retry(expect_all_output, 30)

    def test_working_directory(self):
        """Make sure the working directory of external commands can be set."""
        directory = tempfile.mkdtemp()
        try:
            self.assertEqual(execute('echo $PWD', capture=True, directory=directory), directory)
        finally:
            os.rmdir(directory)

    def test_virtual_environment_option(self):
        """Make sure Python virtual environments can be used."""
        directory = tempfile.mkdtemp()
        virtual_environment = os.path.join(directory, 'environment')
        try:
            # Create a virtual environment to run the command in.
            execute('virtualenv', virtual_environment)
            # This is the expected value of `sys.executable'.
            expected_executable = os.path.join(virtual_environment, 'bin', 'python')
            # Get the actual value of `sys.executable' by running a Python
            # interpreter inside the virtual environment.
            actual_executable = execute('python', '-c', 'import sys; print(sys.executable)',
                                        capture=True, virtual_environment=virtual_environment)
            # Make sure the values match.
            assert os.path.samefile(expected_executable, actual_executable)
            # Make sure that shell commands are also supported (command line
            # munging inside executor is a bit tricky and I specifically got
            # this wrong on the first attempt :-).
            output = execute('echo $VIRTUAL_ENV', capture=True, virtual_environment=virtual_environment)
            assert os.path.samefile(virtual_environment, output)
        finally:
            shutil.rmtree(directory)

    def test_fakeroot_option(self):
        """Make sure ``fakeroot`` can be used."""
        filename = os.path.join(tempfile.gettempdir(), 'executor-%s-fakeroot-test' % os.getpid())
        self.assertTrue(execute('touch', filename, fakeroot=True))
        try:
            self.assertTrue(execute('chown', 'root:root', filename, fakeroot=True))
            self.assertEqual(execute('stat', '--format=%U', filename, fakeroot=True, capture=True), 'root')
            self.assertEqual(execute('stat', '--format=%G', filename, fakeroot=True, capture=True), 'root')
            self.assertTrue(execute('chmod', '600', filename, fakeroot=True))
            self.assertEqual(execute('stat', '--format=%a', filename, fakeroot=True, capture=True), '600')
        finally:
            os.unlink(filename)

    def test_sudo_option(self):
        """Make sure ``fakeroot`` can be used."""
        filename = os.path.join(tempfile.gettempdir(), 'executor-%s-sudo-test' % os.getpid())
        self.assertTrue(execute('touch', filename))
        try:
            self.assertTrue(execute('chown', 'root:root', filename, sudo=True))
            self.assertEqual(execute('stat', '--format=%U', filename, sudo=True, capture=True), 'root')
            self.assertEqual(execute('stat', '--format=%G', filename, sudo=True, capture=True), 'root')
            self.assertTrue(execute('chmod', '600', filename, sudo=True))
            self.assertEqual(execute('stat', '--format=%a', filename, sudo=True, capture=True), '600')
        finally:
            self.assertTrue(execute('rm', filename, sudo=True))

    def test_environment_variable_handling(self):
        """Make sure environment variables can be overridden."""
        # Check that environment variables of the current process are passed on to subprocesses.
        self.assertEqual(execute('echo $PATH', capture=True), os.environ['PATH'])
        # Test that environment variable overrides can be given to external commands.
        override_value = str(random.random())
        self.assertEqual(execute('echo $override',
                                 capture=True,
                                 environment=dict(override=override_value)),
                         override_value)

    def test_simple_async_cmd(self):
        """Make sure commands can be executed asynchronously."""
        cmd = ExternalCommand('sleep 4', async=True)
        # Make sure we're starting from a sane state.
        assert not cmd.was_started
        assert not cmd.is_running
        assert not cmd.is_finished
        # Start the external command.
        cmd.start()

        def assert_running():
            """
            Make sure command switches to running state within a reasonable time.

            This is sensitive to timing issues on slow or overloaded systems,
            the retry logic is there to make the test pass as quickly as
            possible while still allowing for some delay.
            """
            assert cmd.was_started
            assert cmd.is_running
            assert not cmd.is_finished

        retry(assert_running, timeout=4)
        # Wait for the external command to finish.
        cmd.wait()
        # Make sure we finished in a sane state.
        assert cmd.was_started
        assert not cmd.is_running
        assert cmd.is_finished
        assert cmd.returncode == 0

    def test_async_with_input(self):
        """Make sure asynchronous commands can be provided standard input."""
        random_file = os.path.join(tempfile.gettempdir(), 'executor-%s-async-input-test' % os.getpid())
        random_value = str(random.random())
        cmd = ExternalCommand('cat > %s' % quote(random_file), async=True, input=random_value)
        try:
            cmd.start()
            cmd.wait()
            assert os.path.isfile(random_file)
            with open(random_file) as handle:
                contents = handle.read()
                assert random_value == contents.strip()
        finally:
            if os.path.isfile(random_file):
                os.unlink(random_file)

    def test_async_with_output(self):
        """Make sure asynchronous command output can be captured."""
        random_value = str(random.random())
        cmd = ExternalCommand('echo %s' % quote(random_value), async=True, capture=True)
        cmd.start()
        cmd.wait()
        assert cmd.output == random_value

    def test_suspend_and_resume_signals(self):
        """Test the sending of ``SIGSTOP``, ``SIGCONT`` and ``SIGTERM`` signals."""
        # Spawn a child that will live for a minute.
        with ExternalCommand('sleep', '60', check=False) as child:
            # Suspend the execution of the child process using SIGSTOP.
            child.suspend()
            # Test that the child process doesn't respond to SIGTERM once suspended.
            child.terminate(wait=False)
            assert child.is_running, "Child responded to signal even though it was suspended?!"
            # Resume the execution of the child process using SIGCONT.
            child.resume()
            # Test that the child process responds to signals again after
            # having been resumed, but give it a moment to terminate
            # (significantly less time then the process is normally expected
            # to run, otherwise there's no point in the test below).
            child.kill(wait=True, timeout=5)
            assert not child.is_running, "Child didn't respond to signal even though it was resumed?!"

    def test_graceful_command_termination(self):
        """Test graceful termination of commands."""
        self.check_command_termination(method='terminate', proxy=False)

    def test_graceful_process_termination(self):
        """Test graceful termination of processes."""
        self.check_command_termination(method='terminate', proxy=True)

    def test_forceful_command_termination(self):
        """Test forceful termination of commands."""
        self.check_command_termination(method='kill', proxy=False)

    def test_forceful_process_termination(self):
        """Test forceful termination of commands."""
        self.check_command_termination(method='kill', proxy=True)

    def check_command_termination(self, method, proxy):
        """Helper method for command/process termination tests."""
        with ExternalCommand('sleep', '60', check=False) as cmd:
            timer = Timer()
            process = ControllableProcess(pid=cmd.pid) if proxy else cmd
            # We use a positive but very low timeout so that all of the code
            # involved gets a chance to run, but without slowing us down.
            getattr(process, method)(timeout=0.1)
            # Call ExternalCommand.wait() -> subprocess.Popen.wait() -> os.waitpid()
            # so that the process (our own subprocess) is reclaimed because
            # until we do so proc.is_running will be True ...
            cmd.wait()
            # Now we can verify our assertions.
            assert not process.is_running, "Child still running despite graceful termination request!"
            assert timer.elapsed_time < 10, "It look too long to terminate the child!"

    def test_repr(self):
        """Make sure that repr() on external commands gives sane output."""
        cmd = ExternalCommand('echo 42',
                              async=True,
                              capture=True,
                              directory='/',
                              environment={'my_environment_variable': '42'})
        assert repr(cmd).startswith('ExternalCommand(')
        assert repr(cmd).endswith(')')
        assert 'echo 42' in repr(cmd)
        assert 'async=True' in repr(cmd)
        assert ('directory=%r' % '/') in repr(cmd)
        assert 'my_environment_variable' in repr(cmd)
        assert 'was_started=False' in repr(cmd)
        assert 'is_running=False' in repr(cmd)
        assert 'is_finished=False' in repr(cmd)
        cmd.start()

        def assert_finished():
            """Allow for some delay before the external command finishes."""
            assert 'was_started=True' in repr(cmd)
            assert 'is_running=False' in repr(cmd)
            assert 'is_finished=True' in repr(cmd)

        retry(assert_finished, 10)

    def test_command_pool(self):
        """Make sure command pools actually run multiple commands in parallel."""
        num_commands = 10
        sleep_time = 4
        pool = CommandPool(5)
        for i in range(num_commands):
            pool.add(ExternalCommand('sleep %i' % sleep_time))
        timer = Timer()
        results = pool.run()
        assert all(cmd.returncode == 0 for cmd in results.values())
        assert timer.elapsed_time < (num_commands * sleep_time)

    def test_command_pool_resumable(self):
        """Make sure command pools can be resumed after raising exceptions."""
        pool = CommandPool()
        # Prepare two commands that will both raise an exception.
        c1 = ExternalCommand('exit 1', check=True)
        c2 = ExternalCommand('exit 42', check=True)
        # Add the commands to the pool and start them.
        pool.add(c1)
        pool.add(c2)
        pool.spawn()
        # Wait for both commands to finish.
        while not pool.is_finished:
            time.sleep(0.1)
        # The first call to collect() should raise an exception about `exit 1'.
        e1 = intercept(ExternalCommandFailed, pool.collect)
        assert e1.command is c1
        # The second call to collect() should raise an exception about `exit 42'.
        e2 = intercept(ExternalCommandFailed, pool.collect)
        assert e2.command is c2

    def test_command_pool_termination(self):
        """Make sure command pools can be terminated on failure."""
        pool = CommandPool()
        # Include a command that just sleeps for a minute.
        sleep_cmd = ExternalCommand('sleep 60')
        pool.add(sleep_cmd)
        # Include a command that immediately exits with a nonzero return code.
        pool.add(ExternalCommand('exit 1', check=True))
        # Start the command pool and terminate it as soon as the control flow
        # returns to us (because `exit 1' causes an exception to be raised).
        try:
            pool.run()
            assert False, "Assumed CommandPool.run() to raise ExternalCommandFailed!"
        except ExternalCommandFailed as e:
            # Make sure the exception was properly tagged.
            assert e.pool == pool
        # Make sure the sleep command was terminated.
        assert sleep_cmd.is_terminated

    def test_command_pool_delay_checks(self):
        """Make sure command pools can delay error checking until all commands have finished."""
        pool = CommandPool(delay_checks=True)
        # Include a command that fails immediately.
        pool.add(ExternalCommand('exit 1', check=True))
        # Include some commands that just sleep for a while.
        pool.add(ExternalCommand('sleep 1', check=True))
        pool.add(ExternalCommand('sleep 2', check=True))
        pool.add(ExternalCommand('sleep 3', check=True))
        # Make sure the expected exception is raised.
        self.assertRaises(CommandPoolFailed, pool.run)
        # Make sure all commands were started.
        assert all(cmd.was_started for id, cmd in pool.commands)
        # Make sure all commands finished.
        assert all(cmd.is_finished for id, cmd in pool.commands)

    def test_command_pool_delay_checks_noop(self):
        """Make sure command pools with delayed error checking don't raise when ``check=False``."""
        pool = CommandPool(delay_checks=True)
        # Include a command that fails immediately.
        pool.add(ExternalCommand('exit 1', check=False))
        # Run the command pool without catching exceptions; we don't except any.
        pool.run()
        # Make sure the command failed even though the exception wasn't raised.
        assert all(cmd.failed for id, cmd in pool.commands)

    def test_command_pool_logs_directory(self):
        """Make sure command pools can log output of commands in a directory."""
        root_directory = tempfile.mkdtemp()
        sub_directory = os.path.join(root_directory, 'does-not-exist-yet')
        identifiers = [1, 2, 3, 4, 5]
        try:
            pool = CommandPool(concurrency=5, logs_directory=sub_directory)
            for i in identifiers:
                pool.add(identifier=i, command=ExternalCommand('echo %i' % i))
            pool.run()
            files = os.listdir(sub_directory)
            assert sorted(files) == sorted(['%s.log' % i for i in identifiers])
            for filename in files:
                with open(os.path.join(sub_directory, filename)) as handle:
                    contents = handle.read()
                assert filename == ('%s.log' % contents.strip())
        finally:
            shutil.rmtree(root_directory)

    def test_ssh_command_lines(self):
        """Make sure SSH client command lines are correctly generated."""
        # Construct a remote command using as much defaults as possible and
        # validate the resulting SSH client program command line.
        cmd = RemoteCommand('localhost', 'true', ssh_user='some-random-user')
        for token in (
                'ssh', '-o', 'BatchMode=yes',
                       '-o', 'ConnectTimeout=%i' % DEFAULT_CONNECT_TIMEOUT,
                       '-o', 'StrictHostKeyChecking=no',
                       '-l', 'some-random-user',
                       'localhost', 'true',
        ):
            assert token in tokenize_command_line(cmd)
        # Make sure batch mode can be disabled.
        assert 'BatchMode=no' in \
            RemoteCommand('localhost', 'date', batch_mode=False).command_line
        # Make sure the connection timeout can be configured.
        assert 'ConnectTimeout=42' in \
            RemoteCommand('localhost', 'date', connect_timeout=42).command_line
        # Make sure the SSH client program command can be configured.
        assert 'Compression=yes' in \
            RemoteCommand('localhost', 'date', ssh_command=['ssh', '-o', 'Compression=yes']).command_line
        # Make sure the known hosts file can be ignored.
        cmd = RemoteCommand('localhost', 'date', ignore_known_hosts=True)
        assert cmd.ignore_known_hosts
        cmd.ignore_known_hosts = False
        assert not cmd.ignore_known_hosts
        # Make sure strict host key checking can be enabled.
        assert 'StrictHostKeyChecking=yes' in \
            RemoteCommand('localhost', 'date', strict_host_key_checking=True).command_line
        assert 'StrictHostKeyChecking=yes' in \
            RemoteCommand('localhost', 'date', strict_host_key_checking='yes').command_line
        # Make sure host key checking can be set to prompt the operator.
        assert 'StrictHostKeyChecking=ask' in \
            RemoteCommand('localhost', 'date', strict_host_key_checking='ask').command_line
        # Make sure strict host key checking can be disabled.
        assert 'StrictHostKeyChecking=no' in \
            RemoteCommand('localhost', 'date', strict_host_key_checking=False).command_line
        assert 'StrictHostKeyChecking=no' in \
            RemoteCommand('localhost', 'date', strict_host_key_checking='no').command_line
        # Make sure fakeroot and sudo requests are honored.
        assert 'fakeroot' in \
            tokenize_command_line(RemoteCommand('localhost', 'date', fakeroot=True))
        assert 'sudo' in \
            tokenize_command_line(RemoteCommand('localhost', 'date', sudo=True))
        assert 'sudo' not in \
            tokenize_command_line(RemoteCommand('localhost', 'date', ssh_user='root', sudo=True))

    def test_ssh_unreachable(self):
        """Make sure a specific exception is raised when ``ssh`` fails to connect."""
        # Make sure invalid SSH aliases raise the expected type of exception.
        self.assertRaises(
            RemoteConnectFailed,
            lambda: RemoteCommand('this.domain.surely.wont.exist.right', 'date', silent=True).start(),
        )

    def test_remote_working_directory(self):
        """Make sure remote working directories can be set."""
        with SSHServer() as server:
            some_random_directory = tempfile.mkdtemp()
            try:
                cmd = RemoteCommand('127.0.0.1',
                                    'pwd',
                                    capture=True,
                                    directory=some_random_directory,
                                    **server.client_options)
                cmd.start()
                assert cmd.output == some_random_directory
            finally:
                shutil.rmtree(some_random_directory)

    def test_remote_error_handling(self):
        """Make sure remote commands preserve exit codes."""
        with SSHServer() as server:
            cmd = RemoteCommand('127.0.0.1', 'exit 42', **server.client_options)
            self.assertRaises(RemoteCommandFailed, cmd.start)

    def test_foreach(self):
        """Make sure remote command pools work."""
        with SSHServer() as server:
            ssh_aliases = ['127.0.0.%i' % i for i in (1, 2, 3, 4, 5, 6, 7, 8)]
            results = foreach(ssh_aliases, 'echo $SSH_CONNECTION',
                              concurrency=3, capture=True,
                              **server.client_options)
            assert sorted(ssh_aliases) == sorted(cmd.ssh_alias for cmd in results)
            assert len(ssh_aliases) == len(set(cmd.output for cmd in results))

    def test_foreach_with_logging(self):
        """Make sure remote command pools can log output."""
        directory = tempfile.mkdtemp()
        try:
            ssh_aliases = ['127.0.0.%i' % i for i in (1, 2, 3, 4, 5, 6, 7, 8)]
            with SSHServer() as server:
                foreach(ssh_aliases, 'echo $SSH_CONNECTION',
                        concurrency=3, logs_directory=directory,
                        capture=True, **server.client_options)
            log_files = os.listdir(directory)
            assert len(log_files) == len(ssh_aliases)
            assert all(os.path.getsize(os.path.join(directory, fn)) > 0 for fn in log_files)
        finally:
            shutil.rmtree(directory)

    def test_local_context(self):
        """Test a local command context."""
        self.check_context(LocalContext())

    def test_remote_context(self):
        """Test a remote command context."""
        with SSHServer() as server:
            self.check_context(RemoteContext('127.0.0.1', **server.client_options))

    def check_context(self, context):
        """Test a command execution context (whether local or remote)."""
        # Make sure __str__() does something useful.
        assert 'system' in str(context)
        # Test context.execute() and cleanup().
        random_file = os.path.join(tempfile.gettempdir(), uuid.uuid4().hex)
        assert not os.path.exists(random_file)
        with context:
            # Create the random file.
            context.execute('touch', random_file)
            # Make sure the file was created.
            assert os.path.isfile(random_file)
            # Schedule to clean up the file.
            context.cleanup('rm', random_file)
            # Make sure the file hasn't actually been removed yet.
            assert os.path.isfile(random_file)
        # Make sure the file has been removed (__exit__).
        assert not os.path.isfile(random_file)
        # Test context.capture().
        assert context.capture('hostname') == socket.gethostname()

    def test_cli_usage(self):
        """Make sure the command line interface properly presents its usage message."""
        for arguments in [], ['-h'], ['--help']:
            with CaptureOutput() as stream:
                assert run_cli(*arguments) == 0
                assert "Usage: executor" in str(stream)

    def test_cli_return_codes(self):
        """Make sure the command line interface doesn't swallow exit codes."""
        assert run_cli(*python_golf('import sys; sys.exit(0)')) == 0
        assert run_cli(*python_golf('import sys; sys.exit(1)')) == 1
        assert run_cli(*python_golf('import sys; sys.exit(42)')) == 42

    def test_cli_fudge_factor(self, fudge_factor=5):
        """Try to ensure that the fudge factor applies (a bit tricky to get right) ..."""
        def fudge_factor_hammer():
            timer = Timer()
            assert run_cli('--fudge-factor=%i' % fudge_factor, *python_golf('import sys; sys.exit(0)')) == 0
            assert timer.elapsed_time > (fudge_factor / 2.0)
        retry(fudge_factor_hammer, 60)

    def test_cli_exclusive_locking(self):
        """Ensure that exclusive locking works as expected."""
        run_cli('--exclusive', *python_golf('import sys; sys.exit(0)')) == 0

    def test_cli_timeout(self):
        """Ensure that external commands can be timed out."""
        def timeout_hammer():
            timer = Timer()
            assert run_cli('--timeout=5', *python_golf('import time; time.sleep(10)')) != 0
            assert timer.elapsed_time < 10
        retry(timeout_hammer, 60)


def intercept(exc_type, func, *args, **kw):
    """Intercept and return a raised exception."""
    try:
        func(*args, **kw)
    except exc_type as e:
        return e
    else:
        assert False, "Expected exception to be raised, but nothing happened! :-s"


def tokenize_command_line(cmd):
    """Tokenize a command line string into a list of strings."""
    return sum(map(shlex.split, cmd.command_line), [])


def retry(func, timeout):
    """Retry a function until it no longer raises assertion errors or time runs out before then."""
    time_started = time.time()
    while True:
        timeout_expired = (time.time() - time_started) >= timeout
        try:
            return func()
        except AssertionError:
            if timeout_expired:
                raise


def python_golf(statements):
    """Generate a Python command line."""
    return sys.executable, '-c', dedent(statements)


def run_cli(*arguments):
    """Run the command line interface (in the same process)."""
    saved_argv = sys.argv
    try:
        sys.argv = ['executor'] + list(arguments)
        main()
    except SystemExit as e:
        return e.code
    else:
        return 0
    finally:
        sys.argv = saved_argv


class CaptureOutput(object):

    """Context manager that captures what's written to :data:`sys.stdout`."""

    def __init__(self):
        """Initialize a string IO object to be used as :data:`sys.stdout`."""
        self.stream = StringIO()

    def __enter__(self):
        """Start capturing what's written to :data:`sys.stdout`."""
        self.original_stdout = sys.stdout
        sys.stdout = self.stream
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        """Stop capturing what's written to :data:`sys.stdout`."""
        sys.stdout = self.original_stdout

    def __str__(self):
        """Get the text written to :data:`sys.stdout`."""
        return self.stream.getvalue()
