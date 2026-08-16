"""Integration tests for the plan lifecycle and the atomic confirm.

Workshop defaults from config: kerf 4 mm, min_usable 2000 mm.
"""

from app.models import Order, OrderStatus, Remnant, RemnantStatus

KERF = 4


def make_material(client, boards=10):
    r = client.post(
        "/api/materials",
        json={"name": "Smreka 50x80", "stock_length_mm": 13_000, "new_board_count": boards},
    )
    assert r.status_code == 201
    return r.json()["id"]


def make_order(client, material_id, items):
    r = client.post(
        "/api/orders",
        json={
            "material_id": material_id,
            "customer": "Test",
            "items": [{"length_mm": length, "quantity": qty} for length, qty in items],
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


class TestHappyPath:
    def test_reserved_remnant_scenario_end_to_end(self, client):
        """The 5.0 m scenario, through the whole stack."""
        mid = make_material(client, boards=10)
        client.post(
            "/api/inventory/remnants",
            json={"material_id": mid, "length_mm": 5_000, "count": 1},
        )
        client.post(
            "/api/reserved-lengths", json={"material_id": mid, "length_mm": 5_000}
        )
        oid = make_order(client, mid, [(4_000, 1)])

        plan = client.post(f"/api/orders/{oid}/plan").json()
        assert len(plan["boards"]) == 1
        assert plan["boards"][0]["source_kind"] == "new"  # 5.0 m remnant protected
        assert plan["boards"][0]["leftover_mm"] == 13_000 - 4_000 - KERF
        assert plan["boards"][0]["leftover_kind"] == "remnant"

        summary = client.post(f"/api/plans/{plan['id']}/confirm").json()
        assert summary["new_boards_used"] == 1
        assert summary["remnants_created"] == [8_996]
        assert summary["scrap_mm"] == 0

        # Inventory after: 5.0 m kept, 8996 added, board count decremented.
        remnants = client.get(f"/api/inventory/{mid}/remnants").json()
        assert {(g["length_mm"], g["count"]) for g in remnants} == {(8_996, 1), (5_000, 1)}
        materials = client.get("/api/materials").json()
        assert materials[0]["new_board_count"] == 9

        order = client.get(f"/api/orders/{oid}").json()
        assert order["status"] == "confirmed"

    def test_scrap_is_logged(self, client):
        mid = make_material(client)
        oid = make_order(client, mid, [(11_500, 1)])  # leftover 1496 < 2000 -> scrap
        plan = client.post(f"/api/orders/{oid}/plan").json()
        summary = client.post(f"/api/plans/{plan['id']}/confirm").json()
        assert summary["scrap_mm"] == 13_000 - 11_500 - KERF
        stats = client.get(f"/api/stats/{mid}").json()
        assert stats["scrap_total_mm"] == 1_496
        assert stats["scrap_events"] == 1


class TestFifo:
    def test_oldest_remnant_of_length_is_consumed_first(self, client, db_session):
        mid = make_material(client)
        client.post(
            "/api/inventory/remnants",
            json={"material_id": mid, "length_mm": 5_000, "count": 2},
        )
        first_id = (
            db_session.query(Remnant.id).order_by(Remnant.id).first()[0]
        )
        oid = make_order(client, mid, [(4_900, 1)])  # tight fit -> uses a remnant
        plan = client.post(f"/api/orders/{oid}/plan").json()
        assert plan["boards"][0]["source_kind"] == "remnant"
        client.post(f"/api/plans/{plan['id']}/confirm")

        consumed = (
            db_session.query(Remnant)
            .filter(Remnant.status == RemnantStatus.CONSUMED)
            .all()
        )
        assert [r.id for r in consumed] == [first_id]


class TestFailures:
    def test_double_confirm_is_rejected(self, client):
        mid = make_material(client)
        oid = make_order(client, mid, [(4_000, 1)])
        plan = client.post(f"/api/orders/{oid}/plan").json()
        assert client.post(f"/api/plans/{plan['id']}/confirm").status_code == 200
        assert client.post(f"/api/plans/{plan['id']}/confirm").status_code == 409

    def test_stale_inventory_rolls_back_cleanly(self, client, db_session):
        mid = make_material(client, boards=5)
        client.post(
            "/api/inventory/remnants",
            json={"material_id": mid, "length_mm": 5_000, "count": 1},
        )
        oid = make_order(client, mid, [(4_900, 1)])
        plan = client.post(f"/api/orders/{oid}/plan").json()
        assert plan["boards"][0]["source_kind"] == "remnant"

        # Someone takes the remnant off the rack before confirmation.
        r = client.post(
            "/api/inventory/remnants/remove",
            json={"material_id": mid, "length_mm": 5_000, "count": 1},
        )
        assert r.status_code == 200

        resp = client.post(f"/api/plans/{plan['id']}/confirm")
        assert resp.status_code == 409

        # Nothing changed: order still draft, no remnants created, stock intact.
        db_session.expire_all()
        order = db_session.get(Order, oid)
        assert order.status == OrderStatus.DRAFT
        assert (
            db_session.query(Remnant)
            .filter(Remnant.status == RemnantStatus.AVAILABLE)
            .count()
            == 0
        )
        assert client.get("/api/materials").json()[0]["new_board_count"] == 5

    def test_invalid_manual_edit_cannot_be_confirmed(self, client):
        mid = make_material(client)
        oid = make_order(client, mid, [(4_000, 2)])
        plan = client.post(f"/api/orders/{oid}/plan").json()

        # Drop one required piece.
        result = client.put(
            f"/api/plans/{plan['id']}",
            json={"boards": [
                {"source_kind": "new", "source_length_mm": 13_000, "pieces": [4_000]}
            ]},
        ).json()
        assert result["ok"] is False
        assert any("planned 1 of 2" in e for e in result["demand_errors"])
        assert client.post(f"/api/plans/{plan['id']}/confirm").status_code == 409


class TestManualOverride:
    def test_forced_fresh_board_edit_confirms(self, client):
        """User overrides the optimizer: keep the (unreserved) remnant,
        cut from a fresh board instead."""
        mid = make_material(client)
        client.post(
            "/api/inventory/remnants",
            json={"material_id": mid, "length_mm": 5_000, "count": 1},
        )
        oid = make_order(client, mid, [(4_000, 1)])
        plan = client.post(f"/api/orders/{oid}/plan").json()
        assert plan["boards"][0]["source_kind"] == "remnant"  # optimizer's pick

        result = client.put(
            f"/api/plans/{plan['id']}",
            json={"boards": [
                {"source_kind": "new", "source_length_mm": 13_000, "pieces": [4_000]}
            ]},
        ).json()
        assert result["ok"] is True

        summary = client.post(f"/api/plans/{plan['id']}/confirm").json()
        assert summary["remnants_consumed"] == 0
        assert summary["remnants_created"] == [8_996]
        remnants = client.get(f"/api/inventory/{mid}/remnants").json()
        assert {(g["length_mm"], g["count"]) for g in remnants} == {(8_996, 1), (5_000, 1)}
