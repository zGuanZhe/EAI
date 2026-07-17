import json


scores = [0.54, 0.61, 0.58]
print("EAI_METRIC:" + json.dumps({
    "name": "fixture_score",
    "value": max(scores),
    "direction": "maximize",
    "dataset": "campaign_fixture",
    "split": "validation",
}))
