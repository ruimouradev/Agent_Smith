"""Shared fixtures: minimal valid task inputs used across the suite."""

import pytest

from contract import MBPPTaskInput, SWEBenchTaskInput


@pytest.fixture
def mbpp_task() -> MBPPTaskInput:
    """A minimal valid MBPP task."""
    return MBPPTaskInput(
        task_id=7,
        task_definition="d",
        function_definition="def f():",
        test_imports=["import math"],
        test_list=["assert f() == 1"],
    )


@pytest.fixture
def swe_task() -> SWEBenchTaskInput:
    """A minimal valid SWE-bench task, without hints."""
    return SWEBenchTaskInput(
        instance_id="i-1",
        problem_statement="bug",
        docker_image="img",
        eval_script="s",
    )
