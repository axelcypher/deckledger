"""Uploaded trade sheet backgrounds: storage, rendering, ownership and cleanup."""
import io

import pytest
from PIL import Image

import sheet_render
from conftest import ADMIN, EMBER8, deckledger, login, query


@pytest.fixture(autouse=True)
def isolated_backgrounds(tmp_path, monkeypatch):
    monkeypatch.setattr(deckledger.sheets, "SHEET_BACKGROUND_DIR", tmp_path / "backgrounds")
    deckledger.sheets._sheet_swatches.clear()


def upload(client, color="red", size=(320, 180), filename="mat.png"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    buffer.seek(0)
    return client.post("/api/trade-sheets/backgrounds", data={"file": (buffer, filename)}, content_type="multipart/form-data")


def test_upload_stores_jpeg_and_appears_after_built_in_options(client):
    response = upload(client)
    assert response.status_code == 201
    background = response.get_json()
    assert background["id"].startswith("custom-") and background["id"][7:].isdigit()
    assert background["label"] == "mat" and background["custom"] is True
    stored = deckledger.sheets.SHEET_BACKGROUND_DIR / f'{background["id"][7:]}.jpg'
    assert stored.is_file()
    with Image.open(stored) as image:
        assert image.format == "JPEG" and image.mode == "RGB"
    options = client.get("/api/trade-sheets/options").get_json()["backgrounds"]
    assert [item["id"] for item in options[:8]] == list(sheet_render.BACKGROUNDS)
    assert options[8]["id"] == background["id"] and options[8]["label"] == "mat" and options[8]["custom"] is True


def test_large_upload_is_resized_without_stretching(client):
    response = upload(client, size=(5000, 3000))
    assert response.status_code == 201
    background_id = response.get_json()["id"].split("-")[1]
    stored = deckledger.sheets.SHEET_BACKGROUND_DIR / f"{background_id}.jpg"
    with Image.open(stored) as image:
        assert max(image.size) <= sheet_render.PHOTO_MAX_SIDE
        assert abs(image.width / image.height - 5000 / 3000) < 0.001


def test_invalid_or_missing_upload_is_rejected_without_storage(client):
    response = client.post("/api/trade-sheets/backgrounds", data={"file": (io.BytesIO(b"not an image"), "mat.png")}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert query("SELECT COUNT(*) n FROM sheet_backgrounds")[0]["n"] == 0
    assert not list(deckledger.sheets.SHEET_BACKGROUND_DIR.glob("*"))
    assert client.post("/api/trade-sheets/backgrounds", data={}, content_type="multipart/form-data").status_code == 400


def test_custom_background_colors_the_rendered_sheet(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard"}).get_json()["id"]
    assert client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": EMBER8, "delta": 1}).status_code == 200
    midnight = client.get(f"/api/trade-sheets/{sheet_id}/image/1.png")
    assert midnight.status_code == 200
    background = upload(client, color=(255, 0, 0)).get_json()["id"]
    assert client.patch(f"/api/trade-sheets/{sheet_id}", json={"background": background}).get_json()["sheet"]["background"] == background
    response = client.get(f"/api/trade-sheets/{sheet_id}/image/1.png")
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.get_data())) as image:
        red = image.convert("RGB").getpixel((5, image.height - 5))
    with Image.open(io.BytesIO(midnight.get_data())) as image:
        plain = image.convert("RGB").getpixel((5, image.height - 5))
    assert red[0] > 200 and red[1] < 60 and red[2] < 60
    assert not (plain[0] > 200 and plain[1] < 60 and plain[2] < 60)


def test_another_accounts_background_cannot_be_used_or_read(client):
    admin = login(ADMIN)
    background = upload(admin).get_json()["id"]
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard"}).get_json()["id"]
    changed = client.patch(f"/api/trade-sheets/{sheet_id}", json={"background": background}).get_json()["sheet"]
    assert changed["background"] == "midnight"
    assert client.get(f"/api/trade-sheets/backgrounds/{background}.jpg").status_code == 404
    assert client.delete(f"/api/trade-sheets/backgrounds/{background}").status_code == 404


def test_deleting_background_removes_file_and_resets_sheet(client):
    background = upload(client).get_json()["id"]
    stored = deckledger.sheets.SHEET_BACKGROUND_DIR / f'{background[7:]}.jpg'
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard"}).get_json()["id"]
    assert client.patch(f"/api/trade-sheets/{sheet_id}", json={"background": background}).get_json()["sheet"]["background"] == background
    assert client.delete(f"/api/trade-sheets/backgrounds/{background}").get_json() == {"deleted": True}
    assert not stored.exists()
    assert query("SELECT COUNT(*) n FROM sheet_backgrounds")[0]["n"] == 0
    assert client.get(f"/api/trade-sheets/{sheet_id}").get_json()["sheet"]["background"] == "midnight"
    assert client.get(f"/api/trade-sheets/backgrounds/{background}.jpg").status_code == 404


def test_swatches_are_jpegs_and_require_login(client, anonymous):
    background = upload(client).get_json()["id"]
    for name in (background, "midnight"):
        response = client.get(f"/api/trade-sheets/backgrounds/{name}.jpg")
        assert response.status_code == 200 and response.mimetype == "image/jpeg"
        with Image.open(io.BytesIO(response.get_data())) as image:
            assert image.format == "JPEG" and image.size == (240, 150)
    assert client.get("/api/trade-sheets/backgrounds/nope.jpg").status_code == 404
    assert anonymous.get(f"/api/trade-sheets/backgrounds/{background}.jpg").status_code == 401


def test_upload_limit_is_per_user(client, monkeypatch):
    monkeypatch.setattr(deckledger.sheets, "BACKGROUNDS_PER_USER", 2)
    assert upload(client).status_code == 201
    assert upload(client).status_code == 201
    assert upload(client).status_code == 400
    assert query("SELECT COUNT(*) n FROM sheet_backgrounds")[0]["n"] == 2


def test_deleting_account_removes_its_backgrounds(client, admin):
    background = upload(client).get_json()["id"]
    stored = deckledger.sheets.SHEET_BACKGROUND_DIR / f'{background[7:]}.jpg'
    demo_id = query("SELECT id FROM users WHERE username='demo'")[0]["id"]
    assert stored.is_file()
    assert admin.delete(f"/api/admin/users/{demo_id}").get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM sheet_backgrounds WHERE user_id=?", (demo_id,))[0]["n"] == 0
    assert not stored.exists()


def test_accent_of_reflects_photo_color():
    red = sheet_render.accent_of(Image.new("RGB", (10, 10), (255, 0, 0)))
    blue = sheet_render.accent_of(Image.new("RGB", (10, 10), (0, 0, 255)))
    assert len(red) == len(blue) == 7
    assert all(color.startswith("#") and all(character in "0123456789abcdef" for character in color[1:]) for color in (red, blue))
    assert red != blue
