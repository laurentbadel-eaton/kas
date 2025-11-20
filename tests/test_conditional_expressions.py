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

import pytest
from kas.includehandler import ConditionalExpressionParser, IncludeException


class TestConditionalExpressions:
    """Test conditional expression parsing functionality"""
    
    def test_equals_alias_support(self):
        """Test that 'equals' works as alias for 'is'"""
        # Test both directions
        condition1 = ConditionalExpressionParser.parse_condition("bb[MACHINE] equals qemux86-64")
        assert condition1['type'] == 'equality'
        assert condition1['variable_source'] == 'bb'
        assert condition1['variable_name'] == 'MACHINE'
        assert condition1['value'] == 'qemux86-64'
        
        condition2 = ConditionalExpressionParser.parse_condition("qemux86-64 equals bb[MACHINE]")
        assert condition2['type'] == 'equality'
        assert condition2['variable_source'] == 'bb'
        assert condition2['variable_name'] == 'MACHINE'
        assert condition2['value'] == 'qemux86-64'
    
    def test_contains_alias_support(self):
        """Test that 'contains' works as reversed alias for 'in'"""
        condition = ConditionalExpressionParser.parse_condition("bb[IMAGE_FEATURES] contains debug-tweaks")
        assert condition['type'] == 'inclusion'
        assert condition['container']['type'] == 'variable'
        assert condition['container']['source'] == 'bb'
        assert condition['container']['name'] == 'IMAGE_FEATURES'
        assert condition['value']['type'] == 'literal'
        assert condition['value']['value'] == 'debug-tweaks'
    
    def test_flexible_operand_support(self):
        """Test that flexible operands work with list literals and variable-to-variable comparisons"""
        # Variable in list literal
        condition1 = ConditionalExpressionParser.parse_condition("bb[MACHINE] in [qemux86-64, beaglebone]")
        assert condition1['type'] == 'inclusion'
        assert condition1['container']['type'] == 'list'
        assert condition1['container']['values'] == ['qemux86-64', 'beaglebone']
        assert condition1['value']['type'] == 'variable'
        assert condition1['value']['source'] == 'bb'
        assert condition1['value']['name'] == 'MACHINE'
        
        # Variable in variable
        condition2 = ConditionalExpressionParser.parse_condition("bb[MACHINE] in env[TARGET_LIST]")
        assert condition2['type'] == 'inclusion'
        assert condition2['container']['type'] == 'variable'
        assert condition2['container']['source'] == 'env'
        assert condition2['container']['name'] == 'TARGET_LIST'
        assert condition2['value']['type'] == 'variable'
        assert condition2['value']['source'] == 'bb'
        assert condition2['value']['name'] == 'MACHINE'
        
        # List contains variable
        condition3 = ConditionalExpressionParser.parse_condition("[qemux86-64, beaglebone] contains bb[MACHINE]")
        assert condition3['type'] == 'inclusion'
        assert condition3['container']['type'] == 'list'
        assert condition3['container']['values'] == ['qemux86-64', 'beaglebone']
            
    def test_syntax_validation(self):
        """Test various combinations of valid and invalid syntax"""
        # Valid combinations
        valid_expressions = [
            "bb[VAR] is value",
            "value is bb[VAR]", 
            "bb[VAR] equals value",
            "value equals bb[VAR]",
            "value in bb[VAR]",
            "bb[VAR] contains value",
            "env[VAR] is value",
            "env[VAR] contains value",
            "bb[VAR] in [val1, val2]",
            "bb[VAR] in env[OTHER_VAR]",
            "[val1, val2] contains bb[VAR]"
        ]
        
        for expr in valid_expressions:
            try:
                result = ConditionalExpressionParser.parse_condition(expr)
                assert result['type'] in ['equality', 'inclusion']
            except Exception as e:
                pytest.fail(f"Valid expression '{expr}' failed: {e}")
        
        # Invalid combinations
        invalid_expressions = [
            "bb[VAR] with value",  # Unknown operator
            "bb[var] is value",  # Lowercase variable name
            "value contains value",  # No variables at all
            "bb[MACHINE] in invalid_syntax",  # Invalid container
        ]
        
        for expr in invalid_expressions:
            with pytest.raises(IncludeException):
                ConditionalExpressionParser.parse_condition(expr)
