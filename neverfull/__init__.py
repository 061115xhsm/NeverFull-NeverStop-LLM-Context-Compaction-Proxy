"""NeverFull V12 package facade.

Hexagonal-lite: the gateway entrypoint (compaction-proxy.py) stays in place, but
all domain modules are importable as a package for testing and embedding:

    from neverfull import folding, state_table, evidence, policy
"""

from folding import FoldArchive, EnvelopeCipher, build_fold_digest, render_fold_block  # noqa: F401
from state_table import StateTable  # noqa: F401
from evidence import run_evidence_probes  # noqa: F401
from policy import ContextPolicy, classify_task, effective_threshold  # noqa: F401
from calibration import TokenCalibrator  # noqa: F401
from cache_align import apply_openai_cache, apply_anthropic_cache  # noqa: F401
from virtual_tool import inject_restore_tool, extract_restore_calls, build_restore_messages  # noqa: F401
from tenant import current_tenant, scoped_id, normalize_tenant  # noqa: F401
from shared_cache import SharedCache  # noqa: F401
from cost_tracker import CostTracker, DailyQuota  # noqa: F401

__version__ = "12.0.0"
