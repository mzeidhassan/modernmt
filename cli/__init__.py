import inspect
import json
import logging
import os
import shutil
import tempfile
import time

from cli.utils import osutils


def argv_has(argv, *args):
    for argv_arg in argv:
        for arg in args:
            if arg == argv_arg or argv_arg.startswith(arg + '='):
                return True
    return False


def argv_valueof(argv, *args):
    for arg in args:
        for i in range(len(argv)):
            if arg == argv[i]:
                return argv[i + 1]
            elif argv[i].startswith(arg + '='):
                return argv[i][len(arg) + 1:]
    return None


class CLIArgsException(Exception):
    def __init__(self, parser, error):
        self.parser = parser
        self.message = error

    def __str__(self):
        return '{prog}: error: {message}'.format(prog=self.parser.prog, message=self.message)


class SkipException(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


def pp_time(elapsed):
    elapsed = int(elapsed)
    parts = []

    if elapsed > 86400:  # days
        d = int(elapsed / 86400)
        elapsed -= d * 86400
        parts.append('%dd' % d)
    if elapsed > 3600:  # hours
        h = int(elapsed / 3600)
        elapsed -= h * 3600
        parts.append('%dh' % h)
    if elapsed > 60:  # minutes
        m = int(elapsed / 60)
        elapsed -= m * 60
        parts.append('%dm' % m)
    parts.append('%ds' % elapsed)

    return ' '.join(parts)


def activitystep(description):
    def decorator(method):
        _, line_no = inspect.getsourcelines(method)
        return StatefulActivity.Step(method, line_no, description)

    return decorator


class Namespace(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, key):
        return self.__dict__[key] if key in self.__dict__ else None

    def __setattr__(self, key, value):
        self.__dict__[key] = value

    def __repr__(self):
        return 'Namespace' + str(self.__dict__)

    def __str__(self):
        return repr(self)


class StatefulActivity(object):
    class Step:
        def __init__(self, f, line_no, description):
            self.id = f.__name__.strip('_')
            self.name = f.__name__
            self.declaring_class = f.__qualname__.split('.')[0]
            self._description = description
            self._line_no = line_no
            self._f = f

        def __lt__(self, other):
            return self._line_no < other._line_no

        def __call__(self, *_args, **_kwargs):
            self._f(*_args, **_kwargs)

        def __str__(self):
            return self._description

        def __repr__(self):
            return 'Step(line=%d, id=%s, desc=%s)' % (self._line_no, self.id, self._description)

    @classmethod
    def steps(cls):
        return sorted([method for _, method in inspect.getmembers(cls)
                       if isinstance(method, StatefulActivity.Step) and method.declaring_class == cls.__name__])

    def __init__(self, args, extra_argv=None, wdir=None, log_file=None, start_step=None, delete_on_exit=True):
        self.args = args
        self.extra_argv = extra_argv
        self.delete_on_exit = delete_on_exit
        self.indentation = 0
        self.has_sub_activities = False

        # Configuring working dir
        if wdir is None:
            self._wdir = self._temp_dir = tempfile.mkdtemp(prefix='mmt_tmp_')
        else:
            self._wdir = wdir
            self._temp_dir = None

        if not os.path.isdir(self._wdir):
            os.makedirs(self._wdir)

        # Configuring logging file
        self._log_fobj = None
        self._close_log_on_exit = False
        if log_file is not None:
            if isinstance(log_file, str):
                self._log_fobj = open(log_file, 'a')
                self._close_log_on_exit = True
            else:
                self._log_fobj = log_file

            logging.basicConfig(format='%(asctime)-15s [%(levelname)s] - %(message)s', level=logging.DEBUG,
                                stream=self._log_fobj)
        self._logger = logging.getLogger(type(self).__name__)

        # Resuming state
        self._state_file = os.path.join(self._wdir, 'state.json')
        self._steps = self.steps()

        if os.path.isfile(self._state_file):
            with open(self._state_file, 'r', encoding='utf-8') as f_input:
                self.state = Namespace(**json.load(f_input))
        else:
            self.state = Namespace(step_no=-1)

        if start_step is not None:
            self.state.step_no = start_step

    @property
    def log_fobj(self):
        return self._log_fobj or osutils.DEVNULL

    def wdir(self, *paths):
        path = os.path.abspath(os.path.join(self._wdir, *paths))
        if not os.path.isdir(path):
            os.makedirs(path)

        return path

    def _index_of_step(self, step_id):
        for i, step in enumerate(self._steps):
            if step.id == step_id:
                return i
        return None

    def _remove_step(self, step_id):
        idx = self._index_of_step(step_id)
        if idx is not None:
            del self._steps[idx]

    def _save_state(self):
        with open(self._state_file, 'w', encoding='utf-8') as f_output:
            f_output.write(json.dumps(self.state.__dict__, indent=2, sort_keys=True))

    def run(self):
        try:
            for i, step in enumerate(self._steps):
                step_desc = '(%d/%d) %s' % (i + 1, len(self._steps), str(step))

                if self.has_sub_activities:
                    print(step_desc, flush=True)
                else:
                    format_str = (' ' * self.indentation) + '{:<%ds}' % (65 - self.indentation)
                    print(format_str.format('%s...' % step_desc), end='', flush=True)

                if self.state.step_no < i:
                    try:
                        self._logger.info('Training step "%s" started' % step.id)
                        begin = time.time()
                        step(self)
                        elapsed_time = time.time() - begin
                        self._logger.info('Training step "%s" completed in %s' % (step.id, pp_time(elapsed_time)))

                        if self.has_sub_activities:
                            print(step_desc + ' ', end='')
                        print('DONE in %s' % pp_time(elapsed_time), flush=True)
                    except SkipException:
                        if self.has_sub_activities:
                            print(step_desc + ' ', end='')
                        print('SKIPPED', flush=True)

                    self.state.step_no = i
                    self._save_state()
                else:
                    if self.has_sub_activities:
                        print(step_desc + ' ', end='')
                    print('SKIPPED', flush=True)

            if self.delete_on_exit:
                shutil.rmtree(self._wdir, ignore_errors=True)
        finally:
            if self._log_fobj is not None and self._close_log_on_exit:
                self._log_fobj.close()
            if self._temp_dir is not None and os.path.isdir(self._temp_dir):
                shutil.rmtree(self._temp_dir, ignore_errors=True)


def ensure_engine_exists(engine):
    if not engine.exists():
        raise ValueError('Invalid engine name "%s", engine does not exist.' % engine.name)


def ensure_node_not_running(node):
    if node.running:
        raise RuntimeError('ModernMT engine "%s" is already running.' % node.engine.name)


def ensure_node_running(node):
    if not node.running:
        raise RuntimeError('ModernMT engine "%s" is not running.' % node.engine.name)


def ensure_node_has_api(node):
    if node.api is None:
        raise RuntimeError('ModernMT engine "%s" has no running REST API.' % node.engine.name)
