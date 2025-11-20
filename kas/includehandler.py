# kas - setup tool for bitbake based projects
#
# Copyright (c) Siemens AG, 2017-2021
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
    This module implements how includes of configuration files are handled in
    kas.
"""

import os
from pathlib import Path
from collections import OrderedDict
from collections.abc import Mapping
from functools import cached_property
import functools
import logging
import json
import yaml

from jsonschema.validators import validator_for

from .kasusererror import KasUserError
from .repos import Repo
from . import __file_version__, __compatible_file_version__, __version__
from . import CONFIGSCHEMA

__license__ = 'MIT'
__copyright__ = 'Copyright (c) Siemens AG, 2017-2021'


class VariableResolver:
    """
    Utility class for retrieving BitBake and environment variables with caching.
    """

    def __init__(self):
        self._cache = {}
        self._target_validated = False
        self._target_recipe = None

    def get_variable(self, source: str, name: str) -> str:
        """
        Get variable value with caching support.

        Args:
            source: 'bb' for BitBake variables, 'env' for environment variables
            name: Variable name

        Returns:
            Variable value as string
        """
        cache_key = f'{source}:{name}'

        if cache_key in self._cache:
            return self._cache[cache_key]

        value = self._get_variable_uncached(source, name)
        self._cache[cache_key] = value
        return value

    def _get_variable_uncached(self, source: str, name: str) -> str:
        """
        Get variable value without caching.
        """
        try:
            if source == 'env':
                # Environment variables
                value = os.environ.get(name, '')
                logging.debug('VariableResolver: env[%s] = "%s"', name, value)
                return value

            elif source == 'bb':
                # BitBake variables using bitbake-getvar with target recipe
                return self._get_bitbake_variable(name)

            else:
                raise IncludeException(f'Unknown variable source: {source}')

        except Exception as e:
            logging.warning('VariableResolver: failed to get %s[%s]: %s',
                          source, name, e)
            return ''

    def _get_target_recipe(self) -> str | None:
        """
        Get the current bitbake target from config.

        Returns:
            str | None: The target recipe to use with bitbake-getvar, 
                        or None if no target is configured.

        Raises:
            IncludeException: If multiple targets are configured or no config available
        """
        if self._target_recipe:
            return self._target_recipe

        from .context import get_context
        ctx = get_context()

        if not hasattr(ctx, 'config') or not ctx.config:
            raise IncludeException('VariableResolver: No configuration available for target validation')

        # Get the configured targets
        targets = ctx.config.get_bitbake_targets()

        if not targets:
            logging.warning('VariableResolver: No targets configured. '
                          'BitBake variable queries will run without recipe context '
                          '(bitbake-getvar without -r flag). This may not consider '
                          'recipe-specific variable values or dependencies.')
            self._target_recipe = None
            return self._target_recipe

        if len(targets) > 1:
            raise IncludeException(
                f'VariableResolver: Multiple targets configured: '
                f'{targets}. Conditional includes with BitBake variables '
                f'(bb[VAR]) cannot be used with multiple targets because '
                f'BitBake variables may differ between recipes. Please use '
                f'either a single target or limit conditional includes to '
                f'environment variables (env[VAR]) only.'
            )

        # Single target - we can proceed
        self._target_recipe = targets[0]
        logging.debug('VariableResolver: Using target recipe "%s" '
                    'for bitbake-getvar queries', self._target_recipe)
        return self._target_recipe

    def _get_bitbake_variable(self, name: str) -> str:
        """
        Get BitBake variable using bitbake-getvar with target recipe context.
        """
        import subprocess
        from .libkas import find_program
        from .context import get_context

        # Validate target configuration first
        target_recipe = self._get_target_recipe()

        ctx = get_context()
        bitbake_getvar = find_program(ctx.environ['PATH'], 'bitbake-getvar')

        if not bitbake_getvar:
            # Fallback: check if the variable is set in the kas config
            logging.warning('VariableResolver: bitbake-getvar not found, '
                          'using config fallback')
            if hasattr(ctx, 'config') and ctx.config:
                config_data = ctx.config._config
                if name == 'MACHINE':
                    value = config_data.get('machine', '')
                elif name == 'DISTRO':
                    value = config_data.get('distro', '')
                else:
                    value = ''
                logging.debug('VariableResolver: bb[%s] = "%s" '
                            '(from config fallback)', name, value)
                return value
            return ''

        # Use bitbake-getvar with or without -r flag depending on target configuration
        if target_recipe:
            # With target recipe context
            logging.debug('VariableResolver: running bitbake-getvar '
                        '--value -q -r %s %s', target_recipe, name)
            cmd = [bitbake_getvar, '--value', '-q', '-r', target_recipe, name]
            debug_context = f'(from bitbake-getvar -r {target_recipe})'
        else:
            # Without target recipe context
            logging.debug('VariableResolver: running bitbake-getvar '
                        '--value -q %s', name)
            cmd = [bitbake_getvar, '--value', '-q', name]
            debug_context = '(from bitbake-getvar without recipe context)'

        result = subprocess.run(
            cmd,
            env=ctx.environ,
            cwd=ctx.build_dir,
            capture_output=True,
            text=True,
            check=True
        )
        value = result.stdout.strip()
        logging.debug('VariableResolver: bb[%s] = "%s" %s', 
                    name, value, debug_context)
        return value

    def clear_cache(self):
        """
        Clear the variable cache.
        """
        self._cache.clear()
        self._target_recipe = None


SOURCE_DIR_OVERRIDE_KEY = '_source_dir'
SOURCE_DIR_HOST_OVERRIDE_KEY = '_source_dir_host'
PROJECT_CONFIG_URL = f'https://kas.readthedocs.io/en/{__version__}/' \
    'userguide/project-configuration.html'


class LoadConfigException(KasUserError):
    """
        Class for exceptions that appear while loading a configuration file.
    """
    def __init__(self, message, filename):
        super().__init__(f'{message}: {filename}')


class IncludeException(KasUserError):
    """
        Class for exceptions that appear in the include mechanism.
    """
    pass


class ConfigFile():
    def __init__(self, filename, is_external, is_lockfile):
        self.filename = Path(filename)
        self.config = {}
        # src_dir must only be set by auto-generated config file
        self.src_dir = None
        self.is_external = is_external
        self.is_lockfile = is_lockfile

    @staticmethod
    def load(filename, is_external=False, is_lockfile=False):
        """
            Load the configuration file and test if version is supported.
        """
        cf = ConfigFile(filename, is_external, is_lockfile)
        (_, ext) = os.path.splitext(filename)
        if ext == '.json':
            with open(filename, 'rb') as fds:
                cf.config = json.load(fds)
        elif ext in ['.yml', '.yaml']:
            try:
                with open(filename, 'rb') as fds:
                    cf.config = yaml.safe_load(fds)
            except yaml.YAMLError as e:
                msg = f'Error in line {e.problem_mark.line + 1}' \
                    if hasattr(e, 'problem_mark') else ''
                raise LoadConfigException(
                    f'Configuration file is not valid YAML: {msg}',
                    filename)
        else:
            raise LoadConfigException('Config file extension not recognized',
                                      filename)

        validator_class = validator_for(CONFIGSCHEMA)
        validator = validator_class(CONFIGSCHEMA)
        validation_error = False

        for error in sorted(validator.iter_errors(cf.config), key=str):
            validation_error = True
            logging.error('Config file validation Error:\n%s', error.message)
            logging.error('For a list of supported configuration elements, '
                          'see %s', PROJECT_CONFIG_URL)
            logging.debug('Validation against this schema failed:\n%s',
                          json.dumps(error.schema, indent=2))

        if validation_error:
            raise LoadConfigException('Error(s) occured while validating the '
                                      'config file', filename)

        try:
            version_value = int(cf.config['header']['version'])
        except ValueError:
            # Be compatible: version string '0.10' is equivalent to file
            # version 1 This check is already done in the config schema so
            # here just set the right version
            version_value = 1

        if version_value < __compatible_file_version__ or \
           version_value > __file_version__:
            raise LoadConfigException('This version of kas is compatible with '
                                      f'version {__compatible_file_version__} '
                                      f'to {__file_version__}, '
                                      f'file has version {version_value}',
                                      filename)

        if cf.config.get('proxy_config'):
            logging.warning('Obsolete ''proxy_config'' detected. '
                            'This has no effect and will be rejected soon.')

        cf.src_dir = cf.config.get(SOURCE_DIR_OVERRIDE_KEY, None)
        return cf


class IncludeHandler:
    """
        Implements a handler where every configuration file should
        contain a dictionary as the base type with and 'includes'
        key containing a list of includes.

        The includes can be specified in two ways: as a string
        containing the path, relative to the repository root from the
        current file, or as a dictionary. The dictionary must have a
        'file' key containing the path to the include file and a 'repo'
        key containing the key of the repository. The path is interpreted
        relative to the repository root path, which is lazy resolved by
        the first access of a method.

        The includes are read and merged from the deepest level upwards.

        In case ``use_lock`` is ``True``, kas checks if a file
        ``<file>.lock.<ext>`` exists next to the first entry in
        ``top_files``. This filename is then appended to the list of
        ``top_files``.
    """

    def __init__(self, top_files, use_lock=True):
        self.top_files = top_files
        self.use_lock = use_lock
        self.config_files = []

        # Initialize conditional include support
        self.conditional_definitions = []
        self.conditional_processor = ConditionalIncludeProcessor()
        self.resolved_includes = []

    def get_lock_filename(self, kasfile=None):
        """
        Returns the lockfile name for the given kas config file.
        """
        file = Path(kasfile or self.top_files[0])
        return file.parent / (file.stem + '.lock' + file.suffix)

    @cached_property
    def top_repo_path(self):
        """
        Lazy resolve top repo path as we might need a prepared environment
        """
        return Repo.get_root_path(os.path.dirname(self.top_files[0]))

    def get_lockfiles(self):
        """
        Returns a list of lockfiles in the order the configuration
        files were parsed.
        """
        return list(filter(lambda x: x.is_lockfile, self.config_files))

    def get_top_repo_path(self):
        return self.top_repo_path

    def ensure_from_same_repo(self):
        """
        Ensure that all concatenated config files belong to the same repository
        """
        repo_paths = [Repo.get_root_path(os.path.dirname(configfile),
                                         fallback=False)
                      for configfile in self.top_files]

        if len(set(repo_paths)) > 1:
            raise IncludeException('All concatenated config files must '
                                   'belong to the same repository or all '
                                   'must be outside of versioning control')

    def get_config(self, repos=None):
        """
        Parameters:
          repos -- A dictionary that maps repo names to directory paths

        Returns:
          (config, repos)
            config -- A dictionary containing the configuration
            repos -- A list of missing repo names that are needed \
                     to create a complete configuration
        """

        repos = repos or {}

        def _internal_include_handler(filename, repo_path,
                                      is_external=False, is_lockfile=False):
            """
            Recursively loads include files and finds missing repos.

            Includes are done in the following way:

            topfile.yml:
            -------
            header:
              includes:
                - include1.yml
                - repo: repo1
                  file: include-repo1.yml
                - repo: repo2
                  file: include-repo2.yml
                - include3.yml
            -------

            Includes are merged in in this order:
            ['include1.yml', 'include2.yml', 'include-repo1.yml',
             'include-repo2.yml', 'include-repo2.yml', 'topfile.yml']
            On conflict the latter includes overwrite previous ones and
            the current file overwrites every include. (evaluation depth first
            and from top to bottom)
            """

            missing_repos = []
            configs = []
            try:
                current_config = \
                    ConfigFile.load(filename, is_external, is_lockfile)
                # if lockfile exists, inject it after current file
                lockfile = self.get_lock_filename(filename)
                if Path(lockfile).exists():
                    (cfg, rep) = _internal_include_handler(
                        lockfile,
                        repo_path,
                        is_external=is_external,
                        is_lockfile=True
                    )
                    configs.extend(cfg)
                    missing_repos.extend(rep)
                # src_dir must only be set by auto-generated config file
                if current_config.src_dir:
                    self.top_repo_path = current_config.src_dir
                    repo_path = current_config.src_dir

            except FileNotFoundError:
                raise LoadConfigException('Configuration file not found',
                                          filename)
            if not isinstance(current_config.config, Mapping):
                raise IncludeException('Configuration file does not contain a '
                                       'dictionary as base type')
            header = current_config.config.get('header', {})

            for include in header.get('includes', []):
                # Check if this is a conditional include (has 'if' key)
                if isinstance(include, Mapping) and 'if' in include:
                    # Store conditional include for later processing
                    conditional_include = {
                        'include': include,
                        'repo_path': repo_path,
                        'is_external': is_external
                    }
                    self.conditional_definitions.append(conditional_include)
                    logging.debug('Found conditional include: %s with condition: %s',
                                include.get('file'), include.get('if'))
                    continue

                # Process regular includes (strings or mappings without 'if')
                if isinstance(include, str):
                    includefile = ''
                    if include.startswith(os.path.pathsep):
                        includefile = include
                    else:
                        includefile = os.path.abspath(
                            os.path.join(repo_path, include))
                        if not os.path.exists(includefile):
                            alternate = os.path.abspath(
                                os.path.join(
                                    os.path.dirname(current_config.filename),
                                    include
                                )
                            )
                            if os.path.exists(alternate):
                                logging.warning(
                                    'Falling back to file-relative addressing '
                                    'of local include "%s"', include)
                                logging.warning(
                                    'Update your layer to repo-relative '
                                    'addressing to avoid this warning')
                                includefile = alternate
                    (cfg, rep) = _internal_include_handler(
                        includefile,
                        repo_path,
                        is_external=is_external
                    )
                    configs.extend(cfg)
                    missing_repos.extend(rep)
                elif isinstance(include, Mapping):
                    includerepo = include.get('repo', None)
                    includedir = repos.get(includerepo, None)
                    if includedir is not None:
                        incexternal = bool(includedir != self.top_repo_path)
                        try:
                            includefile = include['file']
                        except KeyError:
                            raise IncludeException(
                                f'"file" is not specified: {include}')
                        abs_includedir = os.path.abspath(includedir)
                        (cfg, rep) = _internal_include_handler(
                            os.path.join(abs_includedir, includefile),
                            abs_includedir, is_external=incexternal)
                        configs.extend(cfg)
                        missing_repos.extend(rep)
                    else:
                        missing_repos.append(includerepo)
            logging.debug('config file %s (%s)', current_config.filename,
                          'external' if is_external else 'internal')
            configs.append(current_config)
            # Remove all possible duplicates in missing_repos
            missing_repos = list(OrderedDict.fromkeys(missing_repos))
            return (configs, missing_repos)

        def _internal_dict_merge(dest, upd):
            """
            Merges upd recursively into a copy of dest. The order is preserved
            as in the original dict as dict-insertion orders are preserved from
            Python 3.6 onwards.

            If keys in upd intersect with keys in dest we will do a manual
            merge (helpful for non-dict types like FunctionWrapper).
            """
            if (not isinstance(dest, Mapping)) \
                    or (not isinstance(upd, Mapping)):
                raise IncludeException('Cannot merge using non-dict')
            dest = dest.copy()
            updkeys = list(upd.keys())
            if set(list(dest.keys())) & set(updkeys):
                for key in updkeys:
                    val = upd[key]
                    try:
                        dest_subkey = dest.get(key, None)
                    except AttributeError:
                        dest_subkey = None
                    if isinstance(dest_subkey, Mapping) \
                            and isinstance(val, Mapping):
                        ret = _internal_dict_merge(dest_subkey, val)
                        dest[key] = ret
                    else:
                        dest[key] = upd[key]
                return dest
            try:
                for k in upd:
                    dest[k] = upd[k]
            except AttributeError:
                # this mapping is not a dict
                for k in upd:
                    dest[k] = upd[k]
            return dest

        self.config_files = []
        missing_repos = []
        self.ensure_from_same_repo()
        for configfile in self.top_files:
            cfgs, reps = _internal_include_handler(configfile,
                                                   self.get_top_repo_path())
            self.config_files.extend(cfgs)
            for repo in reps:
                if repo not in missing_repos:
                    missing_repos.append(repo)

        config_files = self.config_files
        if not self.use_lock:
            config_files = [x for x in config_files if not x.is_lockfile]

        config = functools.reduce(_internal_dict_merge,
                                  map(lambda x: x.config, config_files))
        # the merged config must have the highest (used) version number
        header_version = max([int(cfg.config['header']['version'])
                              for cfg in config_files])
        config['header']['version'] = header_version
        return config, missing_repos

    def get_conditional_definitions(self):
        """
        Returns the list of conditional include definitions found during configuration parsing.
        """
        return self.conditional_definitions

    def process_conditional_includes(self):
        """
        Process conditional includes and return list of includes to add.

        Returns:
            List of conditional include file paths that should be processed
        """
        return self.conditional_processor.evaluate_conditional_includes(
            self.conditional_definitions
        )

    def clear_variable_cache(self):
        """
        Clear the variable cache. Should be called at the end of each iteration.
        """
        self.conditional_processor.clear_variable_cache()

    def clear_include_cache(self):
        """
        Clear the include handler's internal state to force reloading of configuration.
        This resets parsed config files, conditional definitions, and variable caches.
        Used during conditional include processing.        
        """
        self.config_files = []
        self.conditional_definitions = []        
        self.resolved_includes = []        
        self.conditional_processor.clear_variable_cache()        
        self.conditional_processor.processed_conditionals.clear()

    def commit_conditional_include(self, include_path):
        """
        Commit a resolved conditional include file to be processed in the next iteration.

        Args:
            include_path: Absolute path to the include file to add
        """
        self.resolved_includes.append(include_path)

    def process_resolved_includes(self):
        """
        Process any resolved conditional includes by adding them to the handler.
        Returns True if any includes were processed.
        """
        if not self.resolved_includes:
            return False

        # Add the includes to the handler's top files
        for include_path in self.resolved_includes:
            self.top_files.append(include_path)
            logging.debug('IncludeHandler: added conditional include: %s', include_path)

        # Clear the resolved list
        self.resolved_includes = []

        # Clear cache to force reload on next get_config() call
        self.clear_include_cache()

        return True


class ConditionalExpressionParser:
    """
    Parser for conditional expressions in kas configuration files.

    Supports syntax like:
    - bb[MACHINE] is qemux86-64
    - debug-tweaks in bb[IMAGE_FEATURES]
    - env[BUILD_TYPE] equals development
    """

    @staticmethod
    def parse_condition(condition_str: str) -> dict:
        """
        Parse a condition string into components.

        Returns a dict with:
        - type: 'equality' or 'inclusion'
        - variable_source: 'bb' or 'env'
        - variable_name: the variable name
        - value: the value to compare
        """
        condition_str = condition_str.strip()

        # Handle equality: bb[VAR] is value OR value is bb[VAR] (also supports 'equals')
        if ' is ' in condition_str or ' equals ' in condition_str:
            # Determine which separator was used
            if ' is ' in condition_str:
                left, right = condition_str.split(' is ', 1)
                separator = 'is'
            else:
                left, right = condition_str.split(' equals ', 1)
                separator = 'equals'

            left, right = left.strip(), right.strip()

            # Try to parse left side as variable
            try:
                var_source, var_name = ConditionalExpressionParser._parse_variable(left)
                return {
                    'type': 'equality',
                    'variable_source': var_source,
                    'variable_name': var_name,
                    'value': right
                }
            except IncludeException:
                # Left side is not a variable, try right side
                try:
                    var_source, var_name = ConditionalExpressionParser._parse_variable(right)
                    return {
                        'type': 'equality',
                        'variable_source': var_source,
                        'variable_name': var_name,
                        'value': left
                    }
                except IncludeException:
                    raise IncludeException(f'Invalid equality condition: "{condition_str}". '
                                         f'Expected format: "bb[VAR] {separator} value" or "value {separator} bb[VAR]"')

        # Handle inclusion: value in bb[VAR] OR bb[VAR] contains value
        elif ' in ' in condition_str or ' contains ' in condition_str:
            if ' in ' in condition_str:
                left, right = condition_str.split(' in ', 1)
                operator = 'in'
                # For "in", the value is on the left, container (variable) is on the right
                value_side, container_side = left.strip(), right.strip()
            else:  # ' contains ' in condition_str
                left, right = condition_str.split(' contains ', 1)
                operator = 'contains'
                # For "contains", the container (variable) is on the left, value is on the right
                container_side, value_side = left.strip(), right.strip()

            # Parse the container side - this should be a variable or list literal
            container_info = ConditionalExpressionParser._parse_container_expression(container_side, operator, condition_str)
            
            # Parse the value side - this can be a literal, variable, or list
            value_info = ConditionalExpressionParser._parse_value_expression(value_side, operator, condition_str)

            return {
                'type': 'inclusion',
                'container': container_info,
                'value': value_info
            }

        else:
            raise IncludeException(f'Unsupported condition syntax: "{condition_str}". '
                                 f'Supported formats: '
                                 f'"bb[VAR] is/equals value", "value is/equals bb[VAR]", '
                                 f'"value in bb[VAR]", "bb[VAR] contains value", '
                                 f'"bb[VAR] in [val1, val2]", "bb[VAR1] in bb[VAR2]", '
                                 f'or "[val1, val2] contains value"')

    @staticmethod
    def _parse_variable(var_expr: str) -> tuple:
        """
        Parse a variable expression like bb[MACHINE] or env[BUILD_TYPE].
        Returns (source, name) tuple.
        """
        import re

        match = re.match(r'^(bb|env)\[([A-Z][A-Z0-9_]*)\]$', var_expr.strip())
        if not match:
            raise IncludeException(f'Invalid variable expression: {var_expr}. '
                                 f'Expected format: bb[VARNAME] or env[VARNAME]')

        return match.group(1), match.group(2)

    @staticmethod
    def _parse_container_expression(expr: str, operator: str, full_condition: str) -> dict:
        """
        Parse the container side of an inclusion expression.
        Can be a variable like bb[VAR] or a list literal like [val1, val2].
        """
        expr = expr.strip()
        
        # Try to parse as a list literal first
        if expr.startswith('[') and expr.endswith(']'):
            # Parse as list literal
            list_content = expr[1:-1].strip()
            if not list_content:
                return {'type': 'list', 'values': []}
            
            # Split by comma and clean up values
            values = [v.strip() for v in list_content.split(',')]
            return {'type': 'list', 'values': values}
        
        # Try to parse as a variable
        try:
            var_source, var_name = ConditionalExpressionParser._parse_variable(expr)
            return {
                'type': 'variable',
                'source': var_source,
                'name': var_name
            }
        except IncludeException:
            pass
        
        # If neither variable nor list, it's an invalid container
        raise IncludeException(f'Invalid {operator} condition: "{full_condition}". '
                             f'Container expression "{expr}" must be a variable like bb[VARNAME] '
                             f'or a list literal like [value1, value2]')

    @staticmethod
    def _parse_value_expression(expr: str, operator: str, full_condition: str) -> dict:
        """
        Parse the value side of an inclusion expression.
        Can be a literal value, variable, or list literal.
        """
        expr = expr.strip()
        
        # Try to parse as a list literal first
        if expr.startswith('[') and expr.endswith(']'):
            list_content = expr[1:-1].strip()
            if not list_content:
                return {'type': 'list', 'values': []}
            
            # Split by comma and clean up values
            values = [v.strip() for v in list_content.split(',')]
            return {'type': 'list', 'values': values}
        
        # Try to parse as a variable
        try:
            var_source, var_name = ConditionalExpressionParser._parse_variable(expr)
            return {
                'type': 'variable',
                'source': var_source,
                'name': var_name
            }
        except IncludeException:
            pass
        
        # Otherwise, treat as literal value
        return {'type': 'literal', 'value': expr}

    @staticmethod
    def evaluate_condition(condition: dict, variable_getter) -> bool:
        """
        Evaluate a parsed condition.

        Args:
            condition: Dict from parse_condition()
            variable_getter: Function that takes (source, name) and returns variable value
        """
        if condition['type'] == 'equality':
            var_value = variable_getter(condition['variable_source'], condition['variable_name'])
            expected_value = condition['value']
            return str(var_value) == str(expected_value)

        elif condition['type'] == 'inclusion':
            # Get the container values (what we're searching in)
            container_info = condition['container']
            if container_info['type'] == 'variable':
                container_value = variable_getter(container_info['source'], container_info['name'])
                # Treat as space-separated list
                container_list = str(container_value).split() if container_value else []
            elif container_info['type'] == 'list':
                container_list = container_info['values']
            else:
                raise IncludeException(f'Unknown container type: {container_info["type"]}')

            # Get the value(s) we're looking for
            value_info = condition['value']
            if value_info['type'] == 'literal':
                search_values = [value_info['value']]
            elif value_info['type'] == 'variable':
                var_value = variable_getter(value_info['source'], value_info['name'])
                # If it's a space-separated list, split it; otherwise treat as single value
                if ' ' in str(var_value):
                    search_values = str(var_value).split()
                else:
                    search_values = [str(var_value)]
            elif value_info['type'] == 'list':
                search_values = value_info['values']
            else:
                raise IncludeException(f'Unknown value type: {value_info["type"]}')

            # Check if any of the search values are in the container
            for search_val in search_values:
                if str(search_val) in container_list:
                    return True
            return False

        else:
            raise IncludeException(f'Unknown condition type: {condition["type"]}')


class ConditionalIncludeProcessor:
    """
    Processor for evaluating and applying conditional includes.
    """

    def __init__(self):
        self.parser = ConditionalExpressionParser()
        self.processed_conditionals = set()
        self._variable_resolver = VariableResolver()

    def evaluate_conditional_includes(self, conditional_includes: list) -> list:
        """
        Evaluate all conditional includes and return those that should be processed.

        Args:
            conditional_includes: List of conditional includes to evaluate

        Returns:
            List of conditional includes that should be processed (condition evaluated to True)
        """
        includes_to_process = []

        for cond_include in conditional_includes:
            # Extract condition from include definition
            include_def = cond_include['include']
            condition = include_def.get('if', '')

            if not condition:
                continue

            # Create a unique key for this conditional to track already included files
            condition_key = (condition, include_def.get('file', ''))

            # Skip conditionals that already evaluated to True and were included
            if condition_key in self.processed_conditionals:
                continue

            # Evaluate condition using our own variable resolver
            if self.evaluate_condition(condition):
                includes_to_process.append(cond_include)
                # Only mark as processed when condition is True (include is added)
                # This ensures False conditions can be re-evaluated in next iteration
                self.processed_conditionals.add(condition_key)
                logging.info('ConditionalIncludeProcessor: condition "%s" evaluates to True', condition)
            else:
                # Don't mark as processed - keep for potential re-evaluation
                # Variable values may change due to regular includes in next iteration
                logging.debug('ConditionalIncludeProcessor: condition "%s" evaluates to False, keeping for re-evaluation', condition)

        return includes_to_process

    def evaluate_condition(self, condition: str) -> bool:
        """
        Evaluate a single condition string.

        Args:
            condition: Condition string to evaluate
        """
        try:
            parsed_condition = self.parser.parse_condition(condition)

            # Use our own variable resolver
            variable_getter = self._variable_resolver.get_variable
            result = self.parser.evaluate_condition(parsed_condition, variable_getter)
            return result

        except Exception as e:
            logging.warning('ConditionalIncludeProcessor: error evaluating condition "%s": %s', condition, e)
            return False

    def clear_variable_cache(self):
        """
        Clear the variable cache.
        """
        self._variable_resolver.clear_cache()