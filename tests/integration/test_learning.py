"""Demand-frequency learning: frequent lengths become learned reserved
lengths after confirmations; user-marked rows are untouched."""

from app.models import ReservedLength


def confirm_order(client, mid, lengths):
    r = client.post(
        "/api/orders",
        json={
            "material_id": mid,
            "items": [{"length_mm": l, "quantity": 1} for l in lengths],
        },
    )
    oid = r.json()["id"]
    plan = client.post(f"/api/orders/{oid}/plan").json()
    assert client.post(f"/api/plans/{plan['id']}/confirm").status_code == 200


def test_frequent_length_is_learned(client, db_session):
    mid = client.post(
        "/api/materials",
        json={"name": "Jela", "stock_length_mm": 13_000, "new_board_count": 50},
    ).json()["id"]

    # 5000 appears in 3 confirmed orders (learn_min_orders), 3700 only in 2.
    confirm_order(client, mid, [5_000, 3_700])
    confirm_order(client, mid, [5_000])
    assert db_session.query(ReservedLength).count() == 0  # threshold not yet reached
    confirm_order(client, mid, [5_000, 3_700, 2_200])

    learned = db_session.query(ReservedLength).all()
    assert [(r.length_mm, r.source) for r in learned] == [(5_000, "learned")]


def test_user_reserved_length_is_not_duplicated_or_deleted(client, db_session):
    mid = client.post(
        "/api/materials",
        json={"name": "Jela", "stock_length_mm": 13_000, "new_board_count": 50},
    ).json()["id"]
    client.post("/api/reserved-lengths", json={"material_id": mid, "length_mm": 5_000})

    for _ in range(3):
        confirm_order(client, mid, [5_000])

    rows = db_session.query(ReservedLength).all()
    assert [(r.length_mm, r.source) for r in rows] == [(5_000, "user")]
