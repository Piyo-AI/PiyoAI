from piyo.models import parse_openrouter_models

PAYLOAD = {
    "data": [
        {"id": "meta/llama-x:free", "context_length": 131072,
         "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools", "temperature"]},
        {"id": "vendor/zero-priced", "context_length": 8000,
         "pricing": {"prompt": "0.0", "completion": "0"}, "supported_parameters": ["temperature"]},
        {"id": "anthropic/paid", "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "supported_parameters": ["tools"]},
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "no/pricing-info"},
    ]
}


def test_parses_free_paid_and_unknown():
    by_id = {m.id: m for m in parse_openrouter_models(PAYLOAD)}
    assert by_id["meta/llama-x:free"].free is True
    assert by_id["vendor/zero-priced"].free is True
    assert by_id["anthropic/paid"].free is False
    assert by_id["openrouter/auto"].free is False  # variable pricing is not free
    assert by_id["no/pricing-info"].free is None


def test_tools_and_context():
    by_id = {m.id: m for m in parse_openrouter_models(PAYLOAD)}
    assert by_id["meta/llama-x:free"].tools is True
    assert by_id["vendor/zero-priced"].tools is False
    assert by_id["no/pricing-info"].tools is None
    assert by_id["anthropic/paid"].context_length == 200000


def test_sorted_by_id():
    ids = [m.id for m in parse_openrouter_models(PAYLOAD)]
    assert ids == sorted(ids)
