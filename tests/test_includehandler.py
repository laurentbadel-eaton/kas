# kas - setup tool for bitbake based projects
#
# Copyright (c) Siemens AG, 2017-2018
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


import os
import io
import textwrap
import contextlib

import pytest

from kas import includehandler, context
from kas.includehandler import ConfigFile


@pytest.fixture(autouse=True)
def fixed_version(monkeypatch):
    monkeypatch.setattr(includehandler, '__file_version__', 5)
    monkeypatch.setattr(includehandler, '__compatible_file_version__', 4)


@pytest.fixture(autouse=True)
def with_kas_context():
    context.create_global_context(None)
    yield
    context.__context__ = None


class MockFileIO(io.StringIO):
    def close(self):
        self.seek(0)


def mock_file(indented_content):
    return MockFileIO(textwrap.dedent(indented_content))


@contextlib.contextmanager
def patch_open(component, string='', dictionary=None):
    dictionary = dictionary or {}
    old_attr = getattr(component, 'open', None)
    component.open = lambda f, *a, **k: mock_file(dictionary.get(f, string))
    yield
    if old_attr:
        component.open = old_attr
    else:
        del component.open


class TestLoadConfig:
    def test_err_invalid_ext(self):
        # Test for invalid file extension:
        exception = includehandler.LoadConfigException
        with pytest.raises(exception):
            ConfigFile.load('x.xyz')

    def util_exception_content(self, testvector):
        for string, exception in testvector:
            with patch_open(includehandler, string=string):
                with pytest.raises(exception):
                    ConfigFile.load('x.yml')

    def test_err_header_missing(self):
        exception = includehandler.LoadConfigException
        testvector = [
            ('', exception),
            ('a', exception),
            ('1', exception),
            ('a:', exception)
        ]

        self.util_exception_content(testvector)

    def test_err_header_invalid_type(self):
        exception = includehandler.LoadConfigException
        testvector = [
            ('header:', exception),
            ('header: 1', exception),
            ('header: a', exception),
            ('header: []', exception),
        ]

        self.util_exception_content(testvector)

    def test_err_version_missing(self):
        exception = includehandler.LoadConfigException
        testvector = [
            ('header: {}', exception),
            ('header: {a: 1}', exception),
        ]

        self.util_exception_content(testvector)

    def test_err_version_invalid_format(self):
        exception = includehandler.LoadConfigException
        testvector = [
            ('header: {version: "0.5"}', exception),
            ('header: {version: "x"}', exception),
            ('header: {version: 3}', exception),
            ('header: {version: 6}', exception),
        ]

        self.util_exception_content(testvector)

    def test_err_parse_yaml(self):
        exception = includehandler.LoadConfigException
        testvector = [
            # misaligned column
            ('header:\n  version: 17\n repo:', exception),
        ]
        self.util_exception_content(testvector)

    def test_header_valid(self):
        testvector = [
            'header: {version: 4}',
            'header: {version: 5}',
        ]
        for string in testvector:
            with patch_open(includehandler, string=string):
                ConfigFile.load('x.yml')

    def test_compat_version(self, monkeypatch):
        monkeypatch.setattr(includehandler, '__compatible_file_version__', 1)
        with patch_open(includehandler, string='header: {version: "0.10"}'):
            ConfigFile.load('x.yml')


class TestIncludes:
    header = '''
header:
  version: 5
{}'''

    def util_include_content(self, testvector, monkeypatch):
        # disable schema validation for these tests:
        monkeypatch.setattr(includehandler, 'CONFIGSCHEMA', {})
        for test in testvector:
            with patch_open(includehandler, dictionary=test['fdict']):
                ginc = includehandler.IncludeHandler(['x.yml'])
                config, missing = ginc.get_config(repos=test['rdict'])

                # Remove header, because we dont want to compare it:
                config.pop('header')

                assert test['conf'] == config
                assert test['rmiss'] == missing

                if 'cfiles' in test:
                    assert len(ginc.config_files) == len(test['cfiles'])

                    for i, cf in enumerate(ginc.config_files):
                        assert test['cfiles'][i][0] == str(cf.filename)
                        assert test['cfiles'][i][1] == cf.is_lockfile
                        assert test['cfiles'][i][2] == cf.is_external

    def test_valid_includes_none(self, monkeypatch):
        header = self.__class__.header
        testvector = [
            {
                'fdict': {
                    'x.yml': header.format('')
                },
                'rdict': {
                },
                'conf': {
                },
                'rmiss': [
                ],
                'cfiles': [
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
        ]

        self.util_include_content(testvector, monkeypatch)

    def test_valid_includes_some(self, monkeypatch):
        header = self.__class__.header
        testvector = [
            # Include one file from the same repo:
            {
                'fdict': {
                    'x.yml': header.format('  includes: ["y.yml"]'),
                    os.path.abspath('y.yml'): header.format('\nv:')
                },
                'rdict': {
                },
                'conf': {
                    'v': None
                },
                'rmiss': [
                ],
                'cfiles': [
                    (
                        os.path.abspath('y.yml'),
                        False,
                        False
                    ),
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
            # Include one file from another not available repo:
            {
                'fdict': {
                    'x.yml': header.format(
                        '  includes: [{repo: rep, file: y.yml}]'),
                },
                'rdict': {
                },
                'conf': {
                },
                'rmiss': [
                    'rep',
                ],
                'cfiles': [
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
            # Include one file from the same repo and one from another
            # not available repo:
            {
                'fdict': {
                    'x.yml': header.format('  includes: ["y.yml", '
                                           '{repo: rep, file: y.yml}]'),
                    os.path.abspath('y.yml'): header.format('\nv:')
                },
                'rdict': {
                },
                'conf': {
                    'v': None
                },
                'rmiss': [
                    'rep',
                ],
                'cfiles': [
                    (
                        os.path.abspath('y.yml'),
                        False,
                        False
                    ),
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
            # Include one file from another available repo:
            {
                'fdict': {
                    'x.yml': header.format(
                        '  includes: [{repo: rep, file: y.yml}]'),
                    '/rep/y.yml': header.format('\nv:')
                },
                'rdict': {
                    'rep': '/rep'
                },
                'conf': {
                    'v': None
                },
                'rmiss': [
                ],
                'cfiles': [
                    (
                        '/rep/y.yml',
                        False,
                        True
                    ),
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
            # Include two files from another repo in sub-directories:
            {
                'fdict': {
                    'x.yml': header.format(
                        '  includes: [{repo: rep, file: dir1/y.yml}]'),
                    '/rep/dir1/y.yml': header.format(
                        '  includes: ["dir2/z.yml"]'),
                    '/rep/dir2/z.yml': header.format('\nv:')
                },
                'rdict': {
                    'rep': '/rep'
                },
                'conf': {
                    'v': None
                },
                'rmiss': [
                ],
                'cfiles': [
                    (
                        '/rep/dir2/z.yml',
                        False,
                        True
                    ),
                    (
                        '/rep/dir1/y.yml',
                        False,
                        True
                    ),
                    (
                        'x.yml',
                        False,
                        False
                    )
                ]
            },
        ]

        self.util_include_content(testvector, monkeypatch)

    def test_valid_overwriting(self, monkeypatch):
        header = self.__class__.header
        testvector = [
            {
                'fdict': {
                    'x.yml': header.format('''  includes: ["y.yml"]
v: x'''),
                    os.path.abspath('y.yml'): header.format('''
v: y''')
                },
                'rdict': {
                },
                'conf': {
                    'v': 'x'
                },
                'rmiss': [
                ]
            },
            {
                'fdict': {
                    'x.yml': header.format('''  includes: ["y.yml"]
v: {v: x}'''),
                    os.path.abspath('y.yml'): header.format('''
v: {v: y}''')
                },
                'rdict': {
                },
                'conf': {
                    'v': {'v': 'x'}
                },
                'rmiss': [
                ]
            },
            {
                'fdict': {
                    'x.yml': header.format('''  includes: ["y.yml"]
v1:
v2: []
v3:
  - a: c'''),
                    os.path.abspath('y.yml'): header.format('''
v1: a
v2: [a]
v3:
  - a: b
  - d: c}]''')
                },
                'rdict': {
                },
                'conf': {
                    'v1': None,
                    'v2': [],
                    'v3': [{'a': 'c'}]
                },
                'rmiss': [
                ]
            },
        ]

        self.util_include_content(testvector, monkeypatch)

    def test_valid_merging(self, monkeypatch):
        header = self.__class__.header
        testvector = [
            {
                'fdict': {
                    'x.yml': header.format('''  includes: ["y.yml"]
v1: x
v3:
  a: b
  b:
    e:
  c: d'''),
                    os.path.abspath('y.yml'): header.format('''
v2: y
v3:
  d: e
  b:
    c:
  e: f''')
                },
                'rdict': {
                },
                'conf': {
                    'v1': 'x',
                    'v2': 'y',
                    'v3': {
                        'a': 'b',
                        'b': {'c': None, 'e': None},
                        'c': 'd',
                        'd': 'e',
                        'e': 'f'}
                },
                'rmiss': [
                ]
            },
        ]

        self.util_include_content(testvector, monkeypatch)

    def test_valid_ordering(self, monkeypatch):
        # disable schema validation for this test:
        monkeypatch.setattr(includehandler, 'CONFIGSCHEMA', {})
        header = self.__class__.header
        data = {'x.yml':
                header.format('''  includes: ["y.yml", "z.yml"]
v: {v1: x, v2: x}'''),
                os.path.abspath('y.yml'):
                header.format('''  includes: ["z.yml"]
v: {v2: y, v3: y, v5: y}'''),
                os.path.abspath('z.yml'): header.format('''
v: {v3: z, v4: z}''')}
        with patch_open(includehandler, dictionary=data):
            ginc = includehandler.IncludeHandler(['x.yml'])
            config, _ = ginc.get_config()
            keys = list(config['v'].keys())
            index = {keys[i]: i for i in range(len(keys))}

            # Check for vars in z.yml:
            assert index['v3'] < index['v1']
            assert index['v3'] < index['v2']
            assert index['v3'] < index['v5']
            assert index['v4'] < index['v1']
            assert index['v4'] < index['v2']
            assert index['v4'] < index['v5']

            # Check for vars in y.yml:
            assert index['v2'] < index['v1']
            assert index['v3'] < index['v1']
            assert index['v5'] < index['v1']


def test_conditional_expression_parser_env_variable(monkeypatch):
    """
    Test ConditionalExpressionParser with environment variables.
    """
    from kas.includehandler import ConditionalExpressionParser, VariableResolver
    
    monkeypatch.setenv('TEST_VAR', 'test_value')
    monkeypatch.setenv('TEST_LIST', 'debug-tweaks read-only-rootfs')
    
    parser = ConditionalExpressionParser()
    variable_getter = VariableResolver()
    
    # Test equality conditions
    condition1 = parser.parse_condition('env[TEST_VAR] equals test_value')
    assert parser.evaluate_condition(condition1, variable_getter.get_variable) == True
    
    condition2 = parser.parse_condition('env[TEST_VAR] is test_value')
    assert parser.evaluate_condition(condition2, variable_getter.get_variable) == True
    
    condition3 = parser.parse_condition('env[TEST_VAR] equals different')
    assert parser.evaluate_condition(condition3, variable_getter.get_variable) == False
    
    # Test contains operator (space-separated lists)
    condition4 = parser.parse_condition('env[TEST_LIST] contains debug-tweaks')
    assert parser.evaluate_condition(condition4, variable_getter.get_variable) == True
    
    condition5 = parser.parse_condition('env[TEST_LIST] contains missing-feature')
    assert parser.evaluate_condition(condition5, variable_getter.get_variable) == False
    
    condition6 = parser.parse_condition('env[MISSING_VAR] equals something')
    assert parser.evaluate_condition(condition6, variable_getter.get_variable) == False


def test_conditional_expression_parser_bitbake_variable_mock():
    """
    Test ConditionalExpressionParser with mocked BitBake variables.
    """
    from kas.includehandler import ConditionalExpressionParser, VariableResolver
    from unittest.mock import Mock
    
    parser = ConditionalExpressionParser()
    
    # Create a mock variable getter that simulates BitBake variables
    mock_variable_getter = Mock()
    
    def mock_get_variable(source, name):
        mock_bitbake_vars = {
            'MACHINE': 'qemux86-64',
            'DISTRO': 'poky',
            'IMAGE_FEATURES': 'debug-tweaks read-only-rootfs package-management',
            'EMPTY_VAR': ''
        }
        
        if source == 'bb':
            return mock_bitbake_vars.get(name, '')
        elif source == 'env':
            return os.environ.get(name, '')
        return ''
    
    mock_variable_getter.side_effect = mock_get_variable
    
    # Test equality conditions with BitBake variables
    condition1 = parser.parse_condition('bb[MACHINE] equals qemux86-64')
    assert parser.evaluate_condition(condition1, mock_variable_getter) == True
    
    condition2 = parser.parse_condition('bb[MACHINE] is qemux86-64')
    assert parser.evaluate_condition(condition2, mock_variable_getter) == True
    
    condition3 = parser.parse_condition('bb[DISTRO] equals poky')
    assert parser.evaluate_condition(condition3, mock_variable_getter) == True
    
    condition4 = parser.parse_condition('bb[MACHINE] equals different-machine')
    assert parser.evaluate_condition(condition4, mock_variable_getter) == False
    
    # Test contains operator with BitBake variables (space-separated lists)
    condition5 = parser.parse_condition('bb[IMAGE_FEATURES] contains debug-tweaks')
    assert parser.evaluate_condition(condition5, mock_variable_getter) == True
    
    condition6 = parser.parse_condition('bb[IMAGE_FEATURES] contains package-management')
    assert parser.evaluate_condition(condition6, mock_variable_getter) == True
    
    condition7 = parser.parse_condition('bb[IMAGE_FEATURES] contains missing-feature')
    assert parser.evaluate_condition(condition7, mock_variable_getter) == False
    
    # Test with empty BitBake variable
    condition8 = parser.parse_condition('bb[EMPTY_VAR] equals empty_string')
    assert parser.evaluate_condition(condition8, mock_variable_getter) == False
    
    condition9 = parser.parse_condition('bb[EMPTY_VAR] equals something')
    assert parser.evaluate_condition(condition9, mock_variable_getter) == False
    
    # Verify the mock was called correctly
    mock_variable_getter.assert_called()
    # Check that bb[MACHINE] was requested
    mock_variable_getter.assert_any_call('bb', 'MACHINE')


def test_conditional_expression_parser_mixed_sources(monkeypatch):
    """
    Test ConditionalExpressionParser with both environment and mocked BitBake variables.
    This test provides comprehensive coverage of mixed variable source conditions.
    """
    from kas.includehandler import ConditionalExpressionParser
    from unittest.mock import Mock
    
    # Set up environment variables for testing
    monkeypatch.setenv('BUILD_TYPE', 'debug')
    monkeypatch.setenv('ENABLE_FEATURE', 'yes')
    
    parser = ConditionalExpressionParser()
    
    # Create a mock function that handles both env and bb variables
    def mock_get_variable(source, name):
        if source == 'env':
            return os.environ.get(name, '')
        elif source == 'bb':
            mock_bb_vars = {
                'MACHINE': 'qemux86-64',
                'TARGET_ARCH': 'x86_64',
                'PREFERRED_FEATURES': 'feature1 feature2 debug-info'
            }
            return mock_bb_vars.get(name, '')
        return ''
    
    # Test mixed conditions
    condition1 = parser.parse_condition('env[BUILD_TYPE] equals debug')
    assert parser.evaluate_condition(condition1, mock_get_variable) == True
    
    condition2 = parser.parse_condition('bb[MACHINE] contains qemu')
    assert parser.evaluate_condition(condition2, mock_get_variable) == False  # qemu is not in "qemux86-64" as a separate word
    
    condition3 = parser.parse_condition('bb[MACHINE] equals qemux86-64')
    assert parser.evaluate_condition(condition3, mock_get_variable) == True
    
    condition4 = parser.parse_condition('bb[PREFERRED_FEATURES] contains debug-info')
    assert parser.evaluate_condition(condition4, mock_get_variable) == True
    
    condition5 = parser.parse_condition('env[ENABLE_FEATURE] is yes')
    assert parser.evaluate_condition(condition5, mock_get_variable) == True
    
    # Test some false conditions
    condition6 = parser.parse_condition('env[BUILD_TYPE] equals production')
    assert parser.evaluate_condition(condition6, mock_get_variable) == False
    
    condition7 = parser.parse_condition('bb[TARGET_ARCH] equals arm')
    assert parser.evaluate_condition(condition7, mock_get_variable) == False


def test_variable_resolver_no_target_warning():
    """
    Test that VariableResolver emits warning when no target is configured.
    """
    from kas.includehandler import VariableResolver
    from unittest.mock import Mock, patch
    import logging
    
    # Mock the context to have no targets configured
    mock_ctx = Mock()
    mock_config = Mock()
    mock_config.get_bitbake_targets.return_value = []  # No targets
    mock_ctx.config = mock_config
    
    variable_getter = VariableResolver()
    
    with patch('kas.context.get_context', return_value=mock_ctx):
        with patch('kas.includehandler.logging') as mock_logging:
            target_recipe = variable_getter._get_target_recipe()
            
            # Verify that target_recipe is None (no target configured)
            assert target_recipe is None
            
            # Verify that a warning was logged
            mock_logging.warning.assert_called_once()
            warning_call = mock_logging.warning.call_args[0][0]
            assert 'No targets configured' in warning_call


def test_conditional_expression_parser_inclusion():
    """Test inclusion parsing with flexible operands."""
    from kas.includehandler import ConditionalExpressionParser
    
    parser = ConditionalExpressionParser()

    # Mock variable getter for testing
    def mock_getter(source, name):
        variables = {
            'bb': {
                'MACHINE': 'qemux86-64',
                'BUILD_TARGETS': 'beaglebone qemuarm',
                'IMAGE_FEATURES': 'debug-tweaks ssh-server-dropbear'
            },
            'env': {
                'TARGET_LIST': 'beaglebone qemux86-64 rpi4',
                'CURRENT_MACHINE': 'qemux86-64'
            }
        }
        return variables.get(source, {}).get(name, '')

    # Test variable in list literal
    condition = parser.parse_condition('bb[MACHINE] in [qemux86-64, rpi4]')
    assert condition['type'] == 'inclusion'
    assert condition['container']['type'] == 'list'
    assert condition['container']['values'] == ['qemux86-64', 'rpi4']
    assert condition['value']['type'] == 'variable'
    assert condition['value']['source'] == 'bb'
    assert condition['value']['name'] == 'MACHINE'
    assert parser.evaluate_condition(condition, mock_getter) is True

    # Test variable in variable
    condition = parser.parse_condition('bb[MACHINE] in env[TARGET_LIST]')
    assert condition['type'] == 'inclusion'
    assert condition['container']['type'] == 'variable'
    assert condition['container']['source'] == 'env'
    assert condition['container']['name'] == 'TARGET_LIST'
    assert condition['value']['type'] == 'variable'
    assert condition['value']['source'] == 'bb'
    assert condition['value']['name'] == 'MACHINE'
    assert parser.evaluate_condition(condition, mock_getter) is True

    # Test list literal contains variable
    condition = parser.parse_condition('[qemux86-64, rpi4] contains bb[MACHINE]')
    assert condition['type'] == 'inclusion'
    assert condition['container']['type'] == 'list'
    assert condition['container']['values'] == ['qemux86-64', 'rpi4']
    assert condition['value']['type'] == 'variable'
    assert condition['value']['source'] == 'bb'
    assert condition['value']['name'] == 'MACHINE'
    assert parser.evaluate_condition(condition, mock_getter) is True

    # Test no match cases
    condition = parser.parse_condition('bb[MACHINE] in [beaglebone, rpi4]')
    assert parser.evaluate_condition(condition, mock_getter) is False

    condition = parser.parse_condition('bb[MACHINE] in bb[BUILD_TARGETS]')
    assert parser.evaluate_condition(condition, mock_getter) is False


def test_conditional_expression_parser_invalid_syntax():
    """Test that inclusion parsing properly handles invalid syntax."""
    from kas.includehandler import ConditionalExpressionParser, IncludeException
    
    parser = ConditionalExpressionParser()

    # Test invalid container expression
    with pytest.raises(IncludeException, match='Container expression.*must be a variable.*or a list literal'):
        parser.parse_condition('bb[MACHINE] in invalid_syntax')

    # Test empty list
    condition = parser.parse_condition('bb[MACHINE] in []')
    assert condition['container']['type'] == 'list'
    assert condition['container']['values'] == []

    # Test list with whitespace
    condition = parser.parse_condition('bb[MACHINE] in [  val1  ,  val2  ]')
    assert condition['container']['values'] == ['val1', 'val2']
