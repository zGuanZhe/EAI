from __future__ import annotations

CORE_ONTOLOGY = {
    "version": "core.v1",
    "entity_types": [
        "work", "document", "dataset", "benchmark", "metric", "method", "model",
        "task", "project", "thread", "question", "hypothesis", "decision",
        "experiment", "observation", "finding", "artifact", "memory",
        "research_campaign", "idea", "campaign_stage", "experiment_branch",
        "metric_observation", "campaign_artifact", "review", "manuscript_section",
    ],
    "predicates": [
        "supports", "opposes", "extends", "contrasts", "precedes", "uses",
        "evaluated_on", "reports_metric", "addresses", "limited_by", "depends_on",
        "derived_from", "linked_to", "is_version_of",
    ],
}

EMBODIED_AI_ONTOLOGY = {
    "version": "embodied-ai.v1",
    "entity_types": [
        "embodiment", "sensor", "action_representation", "training_regime",
        "safety_constraint", "robot_dataset", "robot_benchmark", "policy",
    ],
    "predicates": [
        "trained_on", "transfers_to", "optimizes", "requires_sensor", "controls",
        "bridges_route", "improves_metric", "constrained_by",
    ],
    "aliases": {
        "action tokenization": "action_representation",
        "post-training": "training_regime",
        "cross embodiment": "transfers_to",
    },
}


def ontology_packs() -> list[dict]:
    return [CORE_ONTOLOGY, EMBODIED_AI_ONTOLOGY]
