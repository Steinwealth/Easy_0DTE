#!/usr/bin/env python3
"""JSON-safe serialization for Trendline position metadata (stealth engine state)."""

from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger(__name__)


def metadata_for_persist(metadata: Dict[str, object]) -> Dict[str, Any]:
    """Serialize option_stealth engine state as JSON-safe dict (not default=str repr)."""
    meta = dict(metadata or {})
    exec_payload = meta.get("execution_payload")
    if isinstance(exec_payload, dict):
        pl = dict(exec_payload)
        nested_meta = pl.get("meta")
        if isinstance(nested_meta, dict):
            nm = dict(nested_meta)
            intent_raw = nm.get("execution_intent")
            if intent_raw is not None and not isinstance(intent_raw, dict):
                try:
                    from modules.execution_intent import execution_intent_to_dict

                    intent_dict = execution_intent_to_dict(intent_raw)
                    if intent_dict is not None:
                        nm["execution_intent"] = intent_dict
                    else:
                        nm.pop("execution_intent", None)
                except Exception:
                    nm.pop("execution_intent", None)
            pl["meta"] = nm
        meta["execution_payload"] = pl
    osnap = meta.get("option_stealth")
    if not isinstance(osnap, dict):
        return meta
    osnap_out = dict(osnap)
    engine = osnap_out.get("engine")
    if isinstance(engine, dict):
        engine_out = dict(engine)
        st = engine_out.get("state")
        if st is not None and not isinstance(st, (dict, str)):
            try:
                from modules.prime_options_stealth_trailing_tp import option_stealth_state_to_dict

                engine_out["state"] = option_stealth_state_to_dict(st)
            except Exception as exc:
                log.debug("Trendline metadata: option_stealth state serialize skipped: %s", exc)
        osnap_out["engine"] = engine_out
    meta["option_stealth"] = osnap_out
    return meta


def metadata_from_persist(metadata: Dict[str, Any]) -> Dict[str, object]:
    """Reattach live OptionStealthState after GCS/local persistence reload."""
    meta = dict(metadata or {})
    osnap = meta.get("option_stealth")
    if not isinstance(osnap, dict):
        return meta
    engine = osnap.get("engine")
    if isinstance(engine, dict):
        st_raw = engine.get("state")
        if isinstance(st_raw, str):
            log.warning(
                "Trendline metadata: option_stealth engine.state is str (corrupt); dropping for monitor re-register"
            )
            engine = dict(engine)
            engine.pop("state", None)
            osnap = dict(osnap)
            osnap["engine"] = engine
            meta["option_stealth"] = osnap
        elif isinstance(st_raw, dict):
            try:
                from modules.prime_options_stealth_trailing_tp import option_stealth_state_from_dict

                engine = dict(engine)
                engine["state"] = option_stealth_state_from_dict(engine["state"])
                osnap = dict(osnap)
                osnap["engine"] = engine
                meta["option_stealth"] = osnap
            except Exception as exc:
                log.debug("Trendline metadata: option_stealth state restore skipped: %s", exc)
    return meta
