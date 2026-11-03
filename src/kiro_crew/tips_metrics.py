"""Counters for ``POST /api/tips/feedback``: label a tip and record its reaction.

Split from :mod:`kiro_crew.tips` so the route module stays a route module. The
labels are closed by :func:`kiro_crew.metrics.feature_usage.record_tip_feedback`;
this module only works out which pool a tip id came from and which doc it is
about. Nothing here raises into the route.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import DashboardState
    from kiro_crew.tips import TipsCache, TipsState

logger = logging.getLogger(__name__)


def _catalog_fallback_doc(tip: object, cache: "TipsCache") -> str:
    """The catalog doc *tip* was built from by ``tips._fallback_tips``, else ``""``.

    A fallback tip is stored in the same generated pool as a model-written one
    when generation fails, so its pool says nothing about its origin. Its fields
    do: ``_fallback_tips`` derives the id, doc, title and call to action from one
    catalog entry, in a shape a model-written tip does not reproduce.
    """
    if not isinstance(tip, dict):
        return ""
    for entry in cache.catalog:
        if (
            tip.get("id") == entry.doc.replace(".md", "-tip")
            and tip.get("doc") == entry.doc
            and tip.get("title") == entry.feature
            and tip.get("cta_prompt") == f"Tell me about {entry.feature}"
        ):
            return entry.doc
    return ""


def tip_metric_labels(cache: "TipsCache", st: "TipsState", tip_id: str) -> tuple[str, str]:
    """``(source, doc)`` of *tip_id* for ``kirocrew.tips.feedback``. Never raises.

    The tip the user saw is the one to label, so the record of what was served
    is read before the pool, which a background refresh can replace under the
    same id: curated first (it wins a duplicate id when served), then the
    offered tip, then ``shown_docs`` (the doc a served id was shown with), then
    the stored pool, then the catalog itself (a fallback tip has the id
    ``X-tip`` for ``X.md``). A fallback tip is told from a model-written one by
    :func:`_catalog_fallback_doc`, or for a ``shown_docs`` entry by that id
    shape. The doc is returned raw; the counter keeps it only when it is in
    ``TIP_DOC_ALLOWLIST``.
    """
    try:
        if not tip_id:
            return "other", ""
        for t in cache.curated:
            if isinstance(t, dict) and t.get("id") == tip_id:
                # A curated tip's "doc" is usually empty; "doc_link" names the
                # doc it points at.
                return "curated", str(t.get("doc") or t.get("doc_link") or "")
        offered = st.offered
        if isinstance(offered, dict) and offered.get("id") == tip_id:
            return _pool_tip_labels(offered, cache)
        shown_doc = st.shown_docs.get(tip_id)
        if isinstance(shown_doc, str) and shown_doc:
            shown_source = st.shown_sources.get(tip_id)
            if shown_source in ("curated", "catalog", "generated"):
                return shown_source, shown_doc
            # No kept source for this id: read the id shape.
            catalog_docs = {e.doc for e in cache.catalog}
            if shown_doc in catalog_docs and tip_id == shown_doc.replace(".md", "-tip"):
                return "catalog", shown_doc
            return "generated", shown_doc
        for t in st.tips:
            if isinstance(t, dict) and t.get("id") == tip_id:
                return _pool_tip_labels(t, cache)
        for entry in cache.catalog:
            if entry.doc.replace(".md", "-tip") == tip_id:
                return "catalog", entry.doc
        return "other", ""
    except Exception:
        logger.debug("tips metric labelling failed", exc_info=True)
        return "other", ""


def _pool_tip_labels(tip: dict, cache: "TipsCache") -> tuple[str, str]:  # type: ignore[type-arg]
    """``(source, doc)`` of a tip from the stored pool or the offered slot."""
    fallback_doc = _catalog_fallback_doc(tip, cache)
    if fallback_doc:
        return "catalog", fallback_doc
    return "generated", str(tip.get("doc") or "")


def record_tip_feedback_metric(
    state: "DashboardState", request: web.Request, action: str, source: str, doc: str
) -> None:
    """Count one saved tip reaction and note a tip recommendation. Never raises.

    Runs only after the feedback was saved, so it cannot change the response. A
    tip that was shown or acknowledged counts as a recommendation of its feature
    for ``milestone.reached{recommended_by}``. A temporary or incognito session
    records neither, the same as the feature-video and milestones routes.
    """
    try:
        from kiro_crew.dashboard.handlers._shared import _is_restricted_session
        from kiro_crew.metrics.feature_usage import record_tip_feedback

        if _is_restricted_session(state, request):
            return
        record_tip_feedback(action, source, doc)
        if action in ("shown", "ack") and doc:
            from kiro_crew import milestones

            milestones.record_recommended("tip", doc)
    except Exception:
        logger.debug("tips feedback metric failed", exc_info=True)
