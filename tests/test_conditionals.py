# kas - setup tool for bitbake based projects
#
# Copyright (c) Konsulko Group, 2020
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

import sys
import os
import unittest
import tempfile
import shutil
import yaml
from unittest.mock import patch

# Add the kas module to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                '..')))

from kas.conditionals import (                  # noqa: E402
    ConditionalExpressionParser, ParseError)
from kas.includehandler import IncludeHandler   # noqa: E402
from kas.kasusererror import KasUserError       # noqa: E402


class TestConditionalsParser(unittest.TestCase):
    """Unit tests for the conditional expression parser and evaluator."""

    def test_env_equality(self):
        """Test env[VAR] is value"""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            cond = ConditionalExpressionParser.parse_condition(
                "env[TEST_VAR] is test_value")
            self.assertTrue(cond.evaluate(None))

            cond = ConditionalExpressionParser.parse_condition(
                "env[TEST_VAR] is other_value")
            self.assertFalse(cond.evaluate(None))

    def test_env_equality_reverse(self):
        """Test value is env[VAR]"""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            cond = ConditionalExpressionParser.parse_condition(
                "test_value is env[TEST_VAR]")
            self.assertTrue(cond.evaluate(None))

    def test_env_equals_alias(self):
        """Test env[VAR] equals value"""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            cond = ConditionalExpressionParser.parse_condition(
                "env[TEST_VAR] equals test_value")
            self.assertTrue(cond.evaluate(None))

    def test_env_inclusion(self):
        """Test value in env[VAR]"""
        with patch.dict(os.environ, {'TEST_LIST': 'item1 item2 item3'}):
            cond = ConditionalExpressionParser.parse_condition(
                "item2 in env[TEST_LIST]")
            self.assertTrue(cond.evaluate(None))

            cond = ConditionalExpressionParser.parse_condition(
                "item4 in env[TEST_LIST]")
            self.assertFalse(cond.evaluate(None))

    def test_env_contains_alias(self):
        """Test env[VAR] contains value"""
        with patch.dict(os.environ, {'TEST_LIST': 'item1 item2 item3'}):
            cond = ConditionalExpressionParser.parse_condition(
                "env[TEST_LIST] contains item2")
            self.assertTrue(cond.evaluate(None))

    def test_missing_env_var(self):
        """Test behavior when environment variable is missing"""
        # Ensure variable is not in env
        with patch.dict(os.environ, clear=True):
            # env[MISSING] should resolve to ""
            cond = ConditionalExpressionParser.parse_condition(
                "env[MISSING] is ''")
            self.assertTrue(cond.evaluate(None))

            cond = ConditionalExpressionParser.parse_condition(
                "env[MISSING] is something")
            self.assertFalse(cond.evaluate(None))

    def test_bb_variable_parsing(self):
        """Test that bb[...] parses correctly"""
        # Should not raise ParseError
        ConditionalExpressionParser.parse_condition(
            "bb[MACHINE] is qemux86-64")

    def test_invalid_syntax(self):
        """Test invalid syntax"""
        with self.assertRaises(ParseError):
            ConditionalExpressionParser.parse_condition("invalid syntax")


class TestConditionalsIntegration(unittest.TestCase):
    """Integration tests for conditional includes in kas configuration."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cwd = os.getcwd()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def create_config(self, name, content):
        path = os.path.join(self.test_dir, name)
        with open(path, 'w') as f:
            yaml.dump(content, f)
        return path

    def test_conditional_include_true(self):
        """Test that a file is included when condition is true"""
        self.create_config('included.yml', {
            'header': {'version': 1},
            'machine': 'included-machine'
        })

        main_content = {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'included.yml',
                        'if': 'env[TEST_VAR] is true'
                    }
                ]
            }
        }
        main_path = self.create_config('main.yml', main_content)

        with patch('kas.repos.Repo.get_root_path', return_value=self.test_dir):
            with patch.dict(os.environ, {'TEST_VAR': 'true'}):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                self.assertEqual(config.get('machine'), 'included-machine')

    def test_conditional_include_false(self):
        """Test that a file is NOT included when condition is false"""
        self.create_config('included.yml', {
            'header': {'version': 1},
            'machine': 'included-machine'
        })

        main_content = {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'included.yml',
                        'if': 'env[TEST_VAR] is true'
                    }
                ]
            }
        }
        main_path = self.create_config('main.yml', main_content)

        with patch('kas.repos.Repo.get_root_path', return_value=self.test_dir):
            with patch.dict(os.environ, {'TEST_VAR': 'false'}):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                self.assertNotEqual(config.get('machine'), 'included-machine')

    def test_conditional_include_missing_var(self):
        """Test condition with missing variable (defaults to empty string)"""
        self.create_config('included.yml', {
            'header': {'version': 1},
            'machine': 'included-machine'
        })

        main_content = {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'included.yml',
                        'if': 'env[MISSING_VAR] is ""'
                    }
                ]
            }
        }
        main_path = self.create_config('main.yml', main_content)

        with patch('kas.repos.Repo.get_root_path', return_value=self.test_dir):
            # Ensure var is missing
            with patch.dict(os.environ, clear=True):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                # Should be included because missing var == ""
                self.assertEqual(config.get('machine'), 'included-machine')

    def test_invalid_condition_syntax_error(self):
        """Test that invalid syntax in condition raises an error"""
        main_content = {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'included.yml',
                        'if': 'invalid syntax here'
                    }
                ]
            }
        }
        main_path = self.create_config('main.yml', main_content)

        with patch('kas.repos.Repo.get_root_path', return_value=self.test_dir):
            handler = IncludeHandler([main_path])
            # ParseError should bubble up
            with self.assertRaises(KasUserError):
                handler.get_config()

    def test_nested_conditional_include(self):
        """Test nested conditional includes"""
        # inner.yml
        self.create_config('inner.yml', {
            'header': {'version': 1},
            'machine': 'inner-machine'
        })

        # middle.yml includes inner.yml conditionally
        self.create_config('middle.yml', {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'inner.yml',
                        'if': 'env[INNER_VAR] is true'
                    }
                ]
            }
        })

        # main.yml includes middle.yml conditionally
        main_path = self.create_config('main.yml', {
            'header': {
                'version': 1,
                'includes': [
                    {
                        'file': 'middle.yml',
                        'if': 'env[MIDDLE_VAR] is true'
                    }
                ]
            }
        })

        with patch('kas.repos.Repo.get_root_path', return_value=self.test_dir):
            # Case 1: Both true -> inner-machine
            with patch.dict(os.environ, {'MIDDLE_VAR': 'true',
                                         'INNER_VAR': 'true'}):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                self.assertEqual(config.get('machine'), 'inner-machine')

            # Case 2: Middle true, Inner false -> no machine (or default)
            with patch.dict(os.environ, {'MIDDLE_VAR': 'true',
                                         'INNER_VAR': 'false'}):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                self.assertNotEqual(config.get('machine'), 'inner-machine')

            # Case 3: Middle false -> inner never evaluated
            with patch.dict(os.environ, {'MIDDLE_VAR': 'false',
                                         'INNER_VAR': 'true'}):
                handler = IncludeHandler([main_path])
                config, _ = handler.get_config()
                self.assertNotEqual(config.get('machine'), 'inner-machine')


class TestBitBakeConditionals(unittest.TestCase):
    """Unit tests for BitBake variable evaluation."""

    @patch('kas.conditionals.BitBakeEnvironment.instance')
    def test_bb_variable_resolution(self, mock_instance):
        """Test bb[VAR] resolution via BitBakeEnvironment"""
        mock_env = mock_instance.return_value
        mock_env.get_variable.return_value = 'test_value'

        cond = ConditionalExpressionParser.parse_condition(
            "bb[TEST_VAR] is test_value")
        # Context can be None as we mock get_variable
        self.assertTrue(cond.evaluate(None))

        mock_env.get_variable.assert_called_with('TEST_VAR', None)

    @patch('kas.conditionals.BitBakeEnvironment.instance')
    def test_bb_variable_fallback(self, mock_instance):
        """Test bb[MACHINE] fallback to config"""
        from kas.conditionals import ConditionalProcessingError

        mock_env = mock_instance.return_value
        # Simulate environment not ready
        mock_env.get_variable.side_effect = \
            ConditionalProcessingError("Not ready")

        # Mock context with config
        mock_ctx = unittest.mock.Mock()
        mock_ctx.config._config = {'machine': 'qemux86-64'}

        cond = ConditionalExpressionParser.parse_condition(
            "bb[MACHINE] is qemux86-64")
        self.assertTrue(cond.evaluate(mock_ctx))

    @patch('kas.conditionals.BitBakeEnvironment.instance')
    def test_bb_variable_deferral(self, mock_instance):
        """Test that bb[VAR] raises error when not ready and no fallback"""
        from kas.conditionals import ConditionalProcessingError

        mock_env = mock_instance.return_value
        mock_env.get_variable.side_effect = \
            ConditionalProcessingError("Not ready")

        # Mock context with empty config (no fallback)
        mock_ctx = unittest.mock.Mock()
        mock_ctx.config = None

        cond = ConditionalExpressionParser.parse_condition(
            "bb[OTHER_VAR] is value")

        with self.assertRaises(ConditionalProcessingError):
            cond.evaluate(mock_ctx)


if __name__ == '__main__':
    unittest.main()
