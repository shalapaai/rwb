"""Раннер сценариев (F2): сброс, пять сценариев одной кнопкой, отчёт docs/scenario-report.md."""

import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.modules.inventory.models import Unit
from app.modules.orders.models import Order

ADMIN = {"X-Role": "admin"}
REPORT = Path(__file__).resolve().parents[3] / "docs" / "scenario-report.md"


def post(client, path, headers=ADMIN, key=None, expect=200):
    r = client.post(path, headers={**headers, "Idempotency-Key": key or uuid4().hex})
    assert r.status_code == expect, r.text
    return r.json()


def rub(kop: int) -> str:
    return f"{kop / 100:,.2f} ₽".replace(",", " ").replace(".", ",")


def _mask(text: str) -> str:
    """Штрихкоды отказов случайные — в отчёте их прячем, чтобы файл был стабильным."""
    return re.sub(r"\([0-9a-fR-][0-9a-f-]{8,}\)", "(…)", text)


def _result(r) -> str:
    if not isinstance(r, dict):
        return str(r)
    keys = [
        "status",
        "variant",
        "source",
        "pvz",
        "placement",
        "reason",
        "chosen",
        "penalty_kop",
        "offset_days",
        "decided",
        "imitation",
        "is_open",
    ]
    parts = []
    for k in keys:
        if k in r and r[k] not in (None, ""):
            v = rub(r[k]) if k.endswith("_kop") else r[k]
            parts.append(f"{k}: {v}")
    return _mask("; ".join(str(p) for p in parts))


def write_report(runs: list[dict]) -> None:
    lines = [
        "# Отчёт по сценариям",
        "",
        "Сгенерирован тестом `backend/tests/scenarios/test_runner.py` на демо-городе после "
        "`POST /scenarios/reset`. Сценарии идут подряд на одних данных. Все суммы — "
        "синтетические допущения модели, не тарифы RWB.",
        "",
        "| № | Сценарий | Проверки | Новых проводок | Все суммируются в 0 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for r in runs:
        ok = sum(c["ok"] for c in r["checks"])
        lines.append(
            f"| {r['scenario']} | {r['title']} | {ok}/{len(r['checks'])} | "
            f"{r['ledger']['txn_count']} | {'да' if r['money_ok'] else 'НЕТ'} |"
        )
    for r in runs:
        lines += [
            "",
            f"## {r['scenario']}. {r['title']}",
            "",
            "**Шаги**",
            "",
            "| Шаг | Результат |",
            "| --- | --- |",
        ]
        lines += [f"| {s['step']} | {_result(s['result'])} |" for s in r["steps"]]
        lines += ["", "**Проверки**", ""]
        lines += [f"- {'✓' if c['ok'] else '✗'} {c['check']}" for c in r["checks"]]
        if r["orders"]:
            lines += [
                "",
                "**Статусы заказов (до → после)**",
                "",
                "| Заказ | До | После |",
                "| --- | --- | --- |",
            ]
            lines += [
                f"| {o['order_id']} | {o['before'] or '—'} | {o['after']} |" for o in r["orders"]
            ]
        if r["shelf"]:
            lines += [
                "",
                "**Остатки на mesh-полках (до → после)**",
                "",
                "| ПВЗ · товар | До | После |",
                "| --- | ---: | ---: |",
            ]
            lines += [f"| {s['place']} | {s['before']} | {s['after']} |" for s in r["shelf"]]
        lines += [
            "",
            "**Проводки по счетам**",
            "",
            "| Схема | Счёт | Итог |",
            "| --- | --- | ---: |",
        ]
        for scheme, accounts in r["ledger"]["by_account"].items():
            lines += [f"| {scheme} | {a} | {rub(v)} |" for a, v in accounts.items()]
        if r["notifications"]:
            n = r["notifications"]
            lines += ["", f"**Уведомления ({len(n)})**", ""]
            lines += [f"- {x['role']}: {_mask(x['text'])}" for x in n[:5]]
            if len(n) > 5:
                lines.append(f"- … и ещё {len(n) - 5}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_all_five_scenarios_and_report(client, db):
    post(client, "/scenarios/reset")
    runs = [post(client, f"/scenarios/{n}/run") for n in range(1, 6)]
    for r in runs:
        failed = [c["check"] for c in r["checks"] if not c["ok"]]
        assert r["ok"], f"сценарий {r['scenario']}: {failed}"
        assert r["money_ok"] and not r["ledger"]["unbalanced"]
    assert runs[0]["orders"][0]["after"] == "issued"
    assert any(o["after"] == "returned" for o in runs[2]["orders"])
    assert runs[3]["closed_pvz"]["after"] == []  # инцидент снят в конце сценария 4
    if REPORT.parent.is_dir():  # в контейнере docs/ нет — пишем только из репозитория
        write_report(runs)


def test_reset_gives_clean_seeded_city(client, db):
    post(client, "/scenarios/reset")
    post(client, "/scenarios/1/run")
    state = post(client, "/scenarios/reset")
    assert state["orders"] == {} and state["closed_pvz"] == []
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.scalar(select(func.count()).where(Unit.status == "on_shelf")) == 39


def test_same_key_does_not_rerun(client, db):
    post(client, "/scenarios/reset")
    first = post(client, "/scenarios/2/run", key="run-2")
    again = post(client, "/scenarios/2/run", key="run-2")
    assert again == first
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_unknown_scenario_and_roles(client, db):
    post(client, "/scenarios/reset")
    post(client, "/scenarios/9/run", expect=404)
    post(client, "/scenarios/1/run", headers={"X-Role": "logist"}, expect=403)
    assert client.get("/scenarios", headers={"X-Role": "seller"}).json()["1"]
