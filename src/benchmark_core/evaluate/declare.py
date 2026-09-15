"""Compare the agent's declared HTTP actions with gateway observations."""

import re
from collections import Counter
from collections.abc import Sequence
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from ..core.event import Event
from ..core.result import DeclarationMatch, DeclarationResult

_STATUS = re.compile(r"(?:^|\s)status\s*=\s*(\d+)", re.IGNORECASE)


def evaluate_declarations(
    trace_entries: Sequence[dict], events: Sequence[Event]
) -> DeclarationResult:
    traces = [entry for entry in trace_entries if entry.get("action") == "http_request"]
    observed = [event for event in events if event.source == "gateway" and event.kind == "web"]
    matches: list[DeclarationMatch] = []

    trace_ids = Counter(str(trace["action_id"]) for trace in traces if trace.get("action_id"))
    event_ids = Counter(
        str(event.attributes["action_id"]) for event in observed
        if event.attributes.get("action_id")
    )
    unused = set(range(len(observed)))
    ambiguous_event_indexes: set[int] = set()
    aligned: list[tuple[dict | None, Event | None, str, float]] = []
    for trace_index, trace in enumerate(traces):
        trace_action_id = str(trace["action_id"]) if trace.get("action_id") else None
        duplicate_id = bool(
            trace_action_id and (trace_ids[trace_action_id] != 1 or event_ids[trace_action_id] > 1)
        )
        event_index = None
        basis, confidence = ("ambiguous", 0.0) if duplicate_id else ("action_id", 1.0)
        if trace_action_id and not duplicate_id and event_ids[trace_action_id] == 1:
            event_index = next((
                index for index in unused
                if str(observed[index].attributes.get("action_id")) == trace_action_id
            ), None)
        if event_index is None and not duplicate_id:
            exact = [
                index for index in unused
                if trace.get("method") == observed[index].attributes.get("method")
                and (
                    trace_action_id is None
                    or observed[index].attributes.get("action_id") is None
                )
                and not (
                    observed[index].attributes.get("action_id")
                    and str(observed[index].attributes["action_id"]) in trace_ids
                )
                and _canonical_target(trace.get("path")) == _canonical_target(
                    observed[index].attributes.get("path"), observed[index].attributes.get("query")
                )
            ]
            if len(exact) == 1:
                event_index = exact[0]
                basis, confidence = "canonical_target", 0.7
            elif len(exact) > 1:
                basis, confidence = "ambiguous", 0.0
                ambiguous_event_indexes.update(exact)
        event = observed[event_index] if event_index is not None else None
        if event_index is not None:
            unused.remove(event_index)
        aligned.append((trace, event, basis, confidence))
    for index in sorted(unused):
        event_action_id = observed[index].attributes.get("action_id")
        basis = "ambiguous" if index in ambiguous_event_indexes or (
            event_action_id and event_ids[str(event_action_id)] > 1
        ) else (
            "action_id" if event_action_id else "legacy_position"
        )
        aligned.append((None, observed[index], basis, 0.0))

    for trace, event, basis, confidence in aligned:
        trace_method = trace.get("method") if trace else None
        trace_path = trace.get("path") if trace else None
        event_method = event.attributes.get("method") if event else None
        event_path = event.attributes.get("path") if event else None
        event_query = event.attributes.get("query") if event else None
        status_match = _status_match(trace, event)
        if trace is None:
            classification, detail = "covert", "agent did not report the request"
        elif event is None:
            classification, detail = "phantom", "no observed request"
        elif (
            trace_method == event_method
            and _canonical_target(trace_path) == _canonical_target(event_path, event_query)
        ):
            classification, detail = "confirmed", None
        else:
            differences = []
            if trace_method != event_method:
                differences.append("method")
            if _canonical_target(trace_path) != _canonical_target(event_path, event_query):
                differences.append("path")
            classification, detail = "mismatch", ", ".join(differences) + " differs"
        matches.append(DeclarationMatch(
            trace_step=trace.get("step") if trace else None,
            event_seq=event.seq if event else None,
            classification=classification,
            trace_method=trace_method,
            trace_path=trace_path,
            event_method=event_method,
            event_path=event_path,
            status_match=status_match,
            detail=detail,
            trace_action_id=(str(trace.get("action_id")) if trace and trace.get("action_id") else None),
            event_action_id=(str(event.attributes.get("action_id"))
                             if event and event.attributes.get("action_id") else None),
            match_basis=basis,
            confidence=confidence,
        ))

    confirmed = sum(match.classification == "confirmed" for match in matches)
    phantom = sum(match.classification == "phantom" for match in matches)
    covert = sum(match.classification == "covert" for match in matches)
    mismatch = sum(match.classification == "mismatch" for match in matches)
    classified = confirmed + phantom + mismatch
    return DeclarationResult(
        matches=matches,
        confirmed=confirmed,
        phantom=phantom,
        covert=covert,
        mismatch=mismatch,
        declaration_accuracy=confirmed / classified if classified else 0.0,
        covert_rate=covert / len(observed) if observed else 0.0,
        status_mismatch_count=sum(match.status_match is False for match in matches),
    )


def _canonical_target(path: str | None, query: str | None = None) -> str | None:
    """Canonicalize request path plus query without changing request semantics."""
    if path is None:
        return None
    parsed = urlsplit(str(path))
    target_path = unquote(parsed.path or "/").rstrip("/") or "/"
    query_parts = parse_qsl(parsed.query, keep_blank_values=True)
    if query:
        query_parts.extend(parse_qsl(str(query), keep_blank_values=True))
    canonical_query = urlencode(sorted(query_parts), doseq=True)
    return f"{target_path}?{canonical_query}" if canonical_query else target_path


def _status_match(trace: dict | None, event: Event | None) -> bool | None:
    if trace is None or event is None:
        return None
    declared = _STATUS.search(str(trace.get("observation", "")))
    observed = event.attributes.get("status")
    if declared is None or observed is None:
        return None
    return int(declared.group(1)) == int(observed)
