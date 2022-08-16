"""
Reader combined with number.
"""

from numbers import Number
from types import LambdaType
from ..basereader import BaseReader

class Combined:
    """
    A reader combined with a number.
    """

    n: Number
    r: BaseReader
    op: LambdaType

    def __init__(self, n, r, op):
        self.n = n
        self.r = r
        self.op = op

        assert isinstance(n, Number)
        assert isinstance(r, BaseReader)

        self.name = f'NumCombined({n} | {r})'

    @staticmethod
    def add(n, r):
        return Combined(n, r, lambda x: n + x)

    @staticmethod
    def mul(n, r):
        return Combined(n, r, lambda x: n * x)

    @staticmethod
    def sub(n, r):
        return Combined(n, r, lambda x: x - n)

    @staticmethod
    def div(n, r):
        return Combined(n, r, lambda x: x / n)


    def __getattr__(self, attr):
        """
        Forward all other method calls and attributes to reader.
        """
        return getattr(self.r, attr)

    def _get_variables_interpolated_(self, *args, **kwargs):
        env, env_profiles = self.r._get_variables_interpolated_(*args, **kwargs)

        variables = [
            var for var in env.keys() if var not in ['x', 'y', 'time']
        ]

        for var in variables:
            env[var] = self.op(env[var])

        variables = [
            var for var in env_profiles.keys() if var not in ['x', 'y', 'time']
        ]

        for var in variables:
            env_profiles[var] = self.op(env_profiles[var])

        return env, env_profiles

