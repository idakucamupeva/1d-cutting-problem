"""Smoke tests for the server-rendered UI: the full workshop flow
through HTML forms. All lengths are entered and displayed in mm."""


def test_full_ui_flow(client):
    # Add material via the inventory form.
    r = client.post(
        "/zalihe/materijal",
        data={"name": "Smreka", "stock_length_mm": "13000", "new_board_count": "10"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Add a 5000 mm remnant and reserve 5000 mm.
    client.post("/zalihe/1/ostatak", data={"length_mm": "5000", "count": "1", "action": "dodaj"})
    client.post("/zalihe/1/rezervisana", data={"length_mm": "5000"})

    page = client.get("/zalihe").text
    assert "5000 mm" in page

    # Create an order: 2 x 4000 mm.
    r = client.post(
        "/narudzbe/nova",
        data={
            "material_id": "1",
            "customer": "Amir",
            "length_mm": ["4000", ""],
            "quantity": ["2", "1"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    plan_url = r.headers["location"]

    page = client.get(plan_url).text
    assert "Plan rezanja" in page
    assert "board-svg" in page
    assert "Potvrdi plan" in page
    # Reserved 5000 mm remnant must NOT be in the plan (protected).
    assert "ostatak 5000 mm</b>" not in page.lower()

    # Confirm through the form.
    r = client.post(f"{plan_url}/potvrdi", follow_redirects=False)
    assert r.status_code == 303
    assert "poruka=" in r.headers["location"]

    page = client.get(plan_url).text
    assert "(potvrđen)" in page

    # Inventory now shows the created remnant; history shows the order.
    page = client.get("/zalihe").text
    assert "4992 mm" in page  # 13000 - 2*4000 - 2*4
    page = client.get("/historija").text
    assert "Amir" in page


def test_move_piece_and_settings(client):
    client.post(
        "/zalihe/materijal",
        data={"name": "Bor", "stock_length_mm": "13000", "new_board_count": "5"},
    )
    client.post("/zalihe/1/ostatak", data={"length_mm": "5000", "count": "1", "action": "dodaj"})
    r = client.post(
        "/narudzbe/nova",
        data={"material_id": "1", "length_mm": ["4900"], "quantity": ["1"]},
        follow_redirects=False,
    )
    plan_url = r.headers["location"]
    plan_id = plan_url.rsplit("/", 1)[1]

    # Optimizer used the 5000 mm remnant (tight fit); move the piece to a new board.
    page = client.get(plan_url).text
    assert "ostatak 5000 mm" in page
    r = client.post(
        f"/planovi/{plan_id}/premjesti",
        data={"board_idx": "0", "piece_idx": "0", "target": "new"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get(plan_url).text
    assert "nova daska 13000 mm" in page

    # Settings page round-trip.
    page = client.get("/postavke").text
    assert "kerf" in page.lower()
    r = client.post("/postavke", data={"kerf_mm": "5"}, follow_redirects=False)
    assert r.status_code == 303
    assert 'value="5"' in client.get("/postavke").text
