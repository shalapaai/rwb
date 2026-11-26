"""Проверки what-if на базовой сети демо-города.

Сверяем маршруты, суммы, причины отказа и неизменность DayState.
"""

from collections.abc import Iterator
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.routing import router as routing

HEADERS = {"X-Role": "logist"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(routing, "_demo", {})
    monkeypatch.setattr(routing, "_replies", {})
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    ("source", "sku", "dest", "variant", "reason", "money", "hours", "best_variant", "best_source"),
    [
        ("ПВЗ-A", "SKU-001", "ПВЗ-B", "van", "газель G1", 3200, 11.28, "van", "ПВЗ-A"),
        ("ПВЗ-J", "SKU-005", "ПВЗ-H", "hub", "уже проехала ПВЗ-H", 6040, 36.0, "own", "ПВЗ-H"),
        ("ПВЗ-A", "SKU-001", "ПВЗ-D", "hub", "разные газели", 6040, 36.0, "hub", "HUB"),
    ],
)
def test_demo_what_if(
    client: TestClient,
    source: str,
    sku: str,
    dest: str,
    variant: str,
    reason: str,
    money: int,
    hours: float,
    best_variant: str,
    best_source: str,
) -> None:
    response = client.post(
        "/routing/what-if",
        headers=HEADERS,
        json={"source_pvz": source, "sku_id": sku, "dest_pvz": dest},
    )
    assert response.status_code == 200
    data = response.json()
    forced = data["forced"]
    assert (forced["variant"], forced["total_money_kop"], forced["hours"]) == (
        variant,
        money,
        hours,
    )
    assert reason in forced["reason"]
    assert forced["saving_kop"] == 6040 - money
    if variant == "van":
        assert forced["legs"] == [
            {
                "route_id": "G1",
                "from_node": source,
                "to_node": dest,
                "depart_min": 46,
                "arrive_min": 77,
            }
        ]
    else:
        assert forced["source_node"] == "HUB"
        assert forced["legs"] == []
    assert (data["hub"]["total_money_kop"], data["hub"]["hours"]) == (6040, 36.0)
    chosen = data["best"]["assignment"]
    assert (chosen["variant"], chosen["source_node"]) == (best_variant, best_source)
    assert [c["source_node"] for c in data["best"]["candidates"] if c["chosen"]] == [best_source]
    assert routing._demo == {}


def test_repeated_preview_keeps_live_day_state(client: TestClient) -> None:
    assert client.post("/routing/demo/reset", headers=HEADERS).status_code == 200
    before = deepcopy(routing._demo)
    payload = {"source_pvz": "ПВЗ-A", "sku_id": "SKU-001", "dest_pvz": "ПВЗ-B", "qty": 2}
    first = client.post("/routing/what-if", headers=HEADERS, json=payload)
    second = client.post("/routing/what-if", headers=HEADERS, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["forced"]["total_money_kop"] == 6400
    assert first.json()["hub"]["total_money_kop"] == 12080
    assert routing._demo == before
    assert routing._replies == {}


def test_preview_uses_stock_remaining_after_an_order(client: TestClient) -> None:
    assert client.post("/routing/demo/reset", headers=HEADERS).status_code == 200
    order = client.post(
        "/routing/demo/orders",
        headers={**HEADERS, "Idempotency-Key": "consume-stock"},
        json={"id": "consume-stock", "sku_id": "SKU-001", "pvz_id": "ПВЗ-A", "qty": 3},
    )
    assert order.status_code == 200
    assert order.json()["assignment"]["variant"] == "own"
    before = deepcopy(routing._demo)
    response = client.post(
        "/routing/what-if",
        headers=HEADERS,
        json={"source_pvz": "ПВЗ-A", "sku_id": "SKU-001", "dest_pvz": "ПВЗ-B"},
    )
    assert response.status_code == 200
    assert response.json()["forced"]["variant"] == "hub"
    assert "недостаточно свободного остатка" in response.json()["forced"]["reason"]
    assert routing._demo == before


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"source_pvz": "HUB"}, 404),
        ({"dest_pvz": "?"}, 404),
        ({"sku_id": "?"}, 404),
        ({"qty": 0}, 422),
    ],
)
def test_invalid_preview_request(
    client: TestClient, overrides: dict[str, str | int], status: int
) -> None:
    payload = {"source_pvz": "ПВЗ-A", "sku_id": "SKU-001", "dest_pvz": "ПВЗ-B", **overrides}
    assert client.post("/routing/what-if", headers=HEADERS, json=payload).status_code == status
