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
    This module contains the implementation of conditionals.
"""
import os
from .kasusererror import KasUserError

__all__ = [
    'ParseError',
    'ConditionalProcessingError',
    'ConditionalExpressionParser',
]


class ParseError(KasUserError):
    """
    Error parsing conditional expression or other syntax
    """
    def __init__(self, message, context=None):
        if context:
            message = f'{message}\nContext: "{context}"'
        super().__init__(f'Parse error: {message}')


class ConditionalProcessingError(KasUserError):
    """
    Generic error during conditional processing
    """
    def __init__(self, message):
        super().__init__(f'Error during conditional processing: {message}')


class Operand:
    """Base class for operands in conditional expressions."""
    pass


class Literal(Operand):
    """A literal value operand."""
    def __init__(self, value: str):
        self.value = value

    def __repr__(self):
        return f"Literal({self.value!r})"

    def __str__(self):
        return repr(self.value)

    def resolve(self, context):
        """Return the literal value."""
        return self.value


class Variable(Operand):
    """A variable operand (bb[VAR] or env[VAR])."""
    def __init__(self, source: str, name: str):
        self.source = source  # 'bb' or 'env'
        self.name = name

    def __repr__(self):
        return f"Variable({self.source}, {self.name})"

    def __str__(self):
        return f"{self.source}[{self.name}]"

    def resolve(self, context):
        """Resolve this variable using the provided context."""
        raise NotImplementedError


class EnvironmentVariable(Variable):
    """An environment variable operand."""
    def __init__(self, name: str):
        super().__init__('env', name)

    def resolve(self, context):
        """Resolve environment variable"""
        return os.environ.get(self.name, "")


class ListOperand(Operand):
    """A list operand containing other operands."""
    def __init__(self, values):
        self.values = values  # List of Operand instances

    def __repr__(self):
        return f"ListOperand({self.values})"

    def __str__(self):
        return f"[{', '.join(str(v) for v in self.values)}]"

    def resolve(self, context):
        """Recursively resolve all elements in the list."""
        return [elem.resolve(context) for elem in self.values]


class Condition:
    """Base class for conditions."""
    def __init__(self, left: Operand, right: Operand):
        self.left = left
        self.right = right

    def evaluate(self, context):
        """Evaluate this condition."""
        raise NotImplementedError

    def __str__(self):
        return f"{self.left} {self._operator_str()} {self.right}"

    def _operator_str(self):
        raise NotImplementedError


class Equality(Condition):
    """Equality condition: left == right."""
    def _operator_str(self):
        return "equals"

    def evaluate(self, context):
        left_val = self.left.resolve(context)
        right_val = self.right.resolve(context)
        return str(left_val) == str(right_val)


class Inclusion(Condition):
    """Inclusion condition: left contains right."""
    def _operator_str(self):
        return "contains"

    def evaluate(self, context):
        """
        Evaluate if the container (left) contains the search value (right).

        If the search value is a list, returns True if ANY element of the list
        is present in the container.
        """
        container = self.left.resolve(context)
        search_value = self.right.resolve(context)

        # Use Python's 'in' operator directly
        # Handle different combinations of container and search_value types
        if isinstance(search_value, list):
            # Check if any element of search_value is in container
            return any(item in container for item in search_value)
        else:
            # Check if search_value is in container
            return search_value in container


class ConditionalExpressionParser:
    """
    Recursive descent parser for conditional expressions
    in kas configuration files.

    Grammar:
        condition := operand OPERATOR operand
        OPERATOR := 'is' | 'equals' | 'contains' | 'in'
        operand := variable | list | literal
        variable := ('bb' | 'env') '[' IDENTIFIER ']'
        list := '[' operand (',' operand)* ']'
        literal := any sequence of characters
    """

    def __init__(self, condition_str: str):
        self.input = condition_str.strip()
        self.pos = 0

    def reset(self, condition_str: str):
        """Reset the parser with a new condition string."""
        self.input = condition_str.strip()
        self.pos = 0

    def peek_char(self):
        """Look at current character without consuming it."""
        if self.pos < len(self.input):
            return self.input[self.pos]
        return None

    def consume_char(self):
        """Consume and return current character."""
        if self.pos < len(self.input):
            ch = self.input[self.pos]
            self.pos += 1
            return ch
        return None

    def skip_whitespace(self):
        """Skip whitespace characters."""
        while self.peek_char() and self.peek_char() in ' \t\n\r':
            self.consume_char()

    def parse_identifier(self):
        """Parse an identifier (letters, digits, underscores)."""
        start = self.pos
        ch = self.peek_char()
        if not ch or not (ch.isalpha() or ch == '_'):
            raise ParseError(
                f'Expected identifier at position {self.pos}', self.input)

        while ch and (ch.isalnum() or ch == '_'):
            self.consume_char()
            ch = self.peek_char()

        return self.input[start:self.pos]

    def parse_variable(self):
        """Parse a variable: bb[VAR] or env[VAR]."""
        # Parse variable source (bb or env)
        if self.input[self.pos:self.pos + 2] == 'bb':
            source = 'bb'
            self.pos += 2
        elif self.input[self.pos:self.pos + 3] == 'env':
            source = 'env'
            self.pos += 3
        else:
            raise ParseError(
                f'Expected "bb" or "env" at position {self.pos}', self.input)

        # Expect '['
        if self.consume_char() != '[':
            raise ParseError(
                f'Expected "[" after {source} at position {self.pos}',
                self.input)

        # Parse variable name
        var_name = self.parse_identifier()

        # Expect ']'
        if self.consume_char() != ']':
            raise ParseError(
                f'Expected "]" at position {self.pos}', self.input)

        if source == 'bb':
            raise ParseError(
                'BitBake variables (bb[...]) are not supported '
                'in this version', self.input)
        else:  # source == 'env'
            return EnvironmentVariable(var_name)

    def parse_quoted_string(self):
        """Parse a quoted string."""
        quote_char = self.consume_char()  # ' or "
        start = self.pos

        while self.pos < len(self.input):
            ch = self.peek_char()
            if ch == quote_char:
                value = self.input[start:self.pos]
                self.consume_char()  # Consume closing quote
                return Literal(value)
            self.consume_char()

        raise ParseError(
            f'Unterminated quoted string starting at position {start - 1}',
            self.input)

    def parse_literal(self, stop_at_operator=True, stop_chars=None):
        """
        Parse a literal value.
        Quoted strings can contain spaces, unquoted strings cannot.
        """
        # Check for quoted string
        ch = self.peek_char()
        if ch in ["'", '"']:
            return self.parse_quoted_string()

        start = self.pos
        operators = [' is ', ' equals ', ' contains ', ' in ']

        while self.pos < len(self.input):
            ch = self.input[self.pos]

            # Stop at whitespace (unquoted literals shouldn't contain spaces)
            if ch in ' \t\n\r':
                break

            # Check explicit stop characters (for lists)
            if stop_chars and ch in stop_chars:
                break

            # Check operator boundary
            if stop_at_operator:
                remaining = self.input[self.pos:]
                for op in operators:
                    if remaining.startswith(op):
                        literal_value = self.input[start:self.pos].strip()
                        if not literal_value:
                            raise ParseError(
                                f'Empty literal at position {start}',
                                self.input)
                        return Literal(literal_value)

            self.pos += 1

        literal_value = self.input[start:self.pos].strip()
        if not literal_value:
            raise ParseError(f'Empty literal at position {start}', self.input)
        return Literal(literal_value)

    def parse_operand(self, stop_chars=None):
        """Parse an operand: variable, list, or literal."""
        self.skip_whitespace()
        ch = self.peek_char()

        if ch is None:
            raise ParseError('Unexpected end of input', self.input)

        if ch == '[':
            return self.parse_list()

        # Check for variable (must start with bb[ or env[)
        if self.input[self.pos:].startswith('bb[') or \
           self.input[self.pos:].startswith('env['):
            return self.parse_variable()

        return self.parse_literal(
            stop_at_operator=(stop_chars is None), stop_chars=stop_chars)

    def parse_list(self):
        """Parse a list: [operand, operand, ...]."""
        # Consume '['
        self.consume_char()
        self.skip_whitespace()

        elements = []

        # Handle empty list
        if self.peek_char() == ']':
            self.consume_char()
            return ListOperand(elements)

        # Parse list elements
        while True:
            self.skip_whitespace()
            # Pass stop_chars so literal parsing stops at comma or bracket
            element = self.parse_operand(stop_chars=[',', ']'])
            elements.append(element)
            self.skip_whitespace()

            ch = self.peek_char()
            if ch == ']':
                self.consume_char()
                break
            elif ch == ',':
                self.consume_char()
                continue
            else:
                raise ParseError(
                    f'Expected "," or "]" in list at position {self.pos}',
                    self.input)

        return ListOperand(elements)

    def parse_operator(self):
        """Parse an operator: is, equals, contains, in."""
        self.skip_whitespace()

        # Check longer ones first
        operators = ['equals', 'contains', 'is', 'in']

        for op in operators:
            if self.input[self.pos:].startswith(op):
                # Make sure it's a whole word (followed by whitespace or end)
                next_pos = self.pos + len(op)
                if next_pos >= len(self.input) or \
                   self.input[next_pos] in ' \t\n\r':
                    self.pos = next_pos
                    self.skip_whitespace()
                    return op

        raise ParseError(
            f'Expected operator (is, equals, contains, in) at position '
            f'{self.pos}', self.input)

    def parse(self):
        """Parse the complete condition expression."""
        # Parse: operand OPERATOR operand
        left_operand = self.parse_operand()
        operator = self.parse_operator()
        right_operand = self.parse_operand()

        self.skip_whitespace()

        # Make sure we consumed all input
        if self.pos < len(self.input):
            raise ParseError(
                f'Unexpected characters after condition at position '
                f'{self.pos}: "{self.input[self.pos:]}"', self.input)

        # Determine condition type based on operator
        if operator in ['is', 'equals']:
            # Normalize 'is' to 'equals'
            return Equality(left_operand, right_operand)
        else:  # 'in' or 'contains'
            # Normalize 'in' to 'contains' by swapping operands
            if operator == 'in':
                # "value in container" becomes "container contains value"
                container_operand = right_operand
                value_operand = left_operand
            else:  # operator == 'contains'
                # "container contains value" stays as is
                container_operand = left_operand
                value_operand = right_operand

            return Inclusion(container_operand, value_operand)

    @staticmethod
    def parse_condition(condition_str: str):
        """
        Parse a condition string using recursive descent parser.

        Returns a Condition instance (Equality or Inclusion).
        """
        parser = ConditionalExpressionParser(condition_str)
        return parser.parse()

    @staticmethod
    def evaluate_condition(condition, context) -> bool:
        """
        Evaluate a parsed condition.

        Args:
            condition: Condition instance from parse_condition()
            context: VariableResolutionContext with env and bb variable access
        """
        return condition.evaluate(context)
