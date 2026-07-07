"""The contract package — the only boundary between agent/ and sandbox/.

Both domains import from here and never from each other.
"""

from contract.models import (
    MBPPTaskInput,
    SandboxConfig,
    SolutionOutput,
    StepMetrics,
    SWEBenchTaskInput,
)

__all__ = [
    "MBPPTaskInput",
    "SandboxConfig",
    "SolutionOutput",
    "StepMetrics",
    "SWEBenchTaskInput",
]
