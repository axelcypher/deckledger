"""Card images as the views get them: VCard's print files are cut to the card, once, for the
card view, the thumbnails, the foil mask and the sheets alike."""
import io
import json

import pytest
from PIL import Image

import sheet_render
from conftest import ELSA, EMBER8, deckledger


def print_file(path, size, image_format="PNG"):
    """A card image with a red bleed around a blue card."""
    image = Image.new("RGB", size, "#ff0000")
    bleed = (round(size[0] * 3 / 69), round(size[1] * 3 / 94))
    image.paste("#0000ff", (bleed[0], bleed[1], size[0] - bleed[0], size[1] - bleed[1]))
    image.save(path, format=image_format)
    return path


@pytest.fixture
def scans(tmp_path, monkeypatch):
    """Stands in for the download: every card's image is a print file from the temp folder."""
    for name in ("IMAGE_TRIM_CACHE", "IMAGE_THUMB_CACHE", "IMAGE_FOIL_MASK_CACHE", "IMAGE_LOCK_CACHE"):
        monkeypatch.setattr(deckledger.images, name, tmp_path / name.lower())
    source = print_file(tmp_path / "source.img", (690, 940))
    monkeypatch.setattr(deckledger.images, "cached_real_image", lambda row, variant_id: (source, "image/png", f"key-{row['game_id']}"))
    return tmp_path


def red_at_the_edge(image):
    return image.convert("RGB").getpixel((2, image.height // 2))[0] > 200


@pytest.mark.parametrize("size, trimmed", [((690, 940), True), ((734, 1000), True), ((630, 880), False), ((500, 700), False)])
def test_bleed_is_cut_off_print_files_only(size, trimmed):
    """3 mm of bleed around a 63 x 88 mm card; an image already in the card's proportions is left alone."""
    image = Image.new("RGB", size, "#ff0000")
    result = sheet_render.trim_bleed(image, sheet_render.PRINT_BLEED)
    assert (result is not image) is trimmed
    assert abs(result.width / result.height - 63 / 88) < .012
    assert sheet_render.trim_bleed(image, None) is image


def test_vcard_images_are_served_trimmed_in_every_size(client, scans):
    full = Image.open(io.BytesIO(client.get(f"/art/{EMBER8}.svg").get_data()))
    thumb = Image.open(io.BytesIO(client.get(f"/art/{EMBER8}.svg?size=thumb").get_data()))
    assert full.size == (630, 880) and not red_at_the_edge(full)
    assert thumb.width == 360 and not red_at_the_edge(thumb)
    assert full.convert("RGB").getpixel((315, 440)) == (0, 0, 255)
    assert client.get(f"/foil-mask/{EMBER8}.webp").headers["X-Image-Source"] == "local-foil-mask-cache"


def test_other_games_keep_their_images_as_published(client, scans):
    full = Image.open(io.BytesIO(client.get(f"/art/{ELSA}.svg").get_data()))
    assert full.size == (690, 940) and red_at_the_edge(full)


def test_trimming_happens_once(client, scans, monkeypatch):
    client.get(f"/art/{EMBER8}.svg")
    monkeypatch.setattr(sheet_render, "trim_bleed", lambda *args: (_ for _ in ()).throw(AssertionError("trimmed again")))
    assert client.get(f"/art/{EMBER8}.svg").status_code == 200


def test_an_image_without_bleed_is_remembered_and_served_as_it_is(client, scans, monkeypatch):
    source = print_file(scans / "cut.img", (630, 880))
    monkeypatch.setattr(deckledger.images, "cached_real_image", lambda row, variant_id: (source, "image/png", "cut-key"))
    assert Image.open(io.BytesIO(client.get(f"/art/{EMBER8}.svg").get_data())).size == (630, 880)
    assert (deckledger.images.IMAGE_TRIM_CACHE / "cut-key.keep").exists()
    monkeypatch.setattr(sheet_render, "trim_bleed", lambda *args: (_ for _ in ()).throw(AssertionError("opened again")))
    assert client.get(f"/art/{EMBER8}.svg").status_code == 200


def test_a_jpeg_scan_stays_a_jpeg(client, scans, monkeypatch):
    source = print_file(scans / "photo.img", (690, 940), "JPEG")
    monkeypatch.setattr(deckledger.images, "cached_real_image", lambda row, variant_id: (source, "image/jpeg", "photo-key"))
    response = client.get(f"/art/{EMBER8}.svg")
    assert response.mimetype == "image/jpeg" and Image.open(io.BytesIO(response.get_data())).size == (630, 880)


def test_sheets_use_the_trimmed_image(client, scans, monkeypatch):
    seen = []
    monkeypatch.setattr(sheet_render, "render_page", lambda tiles, *args, **kwargs: seen.extend(tiles) or Image.new("RGB", (10, 10)))
    sheet = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "S"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": EMBER8, "delta": 1})
    client.get(f"/api/trade-sheets/{sheet}/image/1.jpg")
    assert Image.open(seen[0]["image_path"]).size == (630, 880)


def test_a_publishers_placeholder_counts_as_a_missing_scan(tmp_path, monkeypatch):
    """VCard's CDN answers a scan it has not uploaded yet with a "?" card (200 OK); the next
    image source is used instead, and a placeholder cached before it was known is dropped."""
    import dataclasses
    import hashlib

    placeholder, scan = b"?" * 2000, b"S" * 3000
    vcard = dataclasses.replace(deckledger.games.vcard.GAME, image_placeholders=((len(placeholder), hashlib.sha256(placeholder).hexdigest()),))
    monkeypatch.setattr(deckledger.images, "game_rules", lambda game_id: vcard)
    for name in ("IMAGE_CACHE", "IMAGE_SOURCE_CACHE", "IMAGE_LOCK_CACHE"):
        monkeypatch.setattr(deckledger.images, name, tmp_path / name.lower())
    served = {"https://cdn.example/unlimited.png": placeholder, "https://cdn.example/first.png": scan}

    class Answer:
        def __init__(self, payload):
            self.payload = payload
            self.headers = type("Headers", (), {"get_content_type": lambda self: "image/png"})()

        def read(self, limit):
            return self.payload

    monkeypatch.setattr(deckledger.images, "urlopen", lambda request, timeout: Answer(served[request.full_url]))
    row = {"game_id": "vcard", "variant_attributes": json.dumps({
        "imageUrl": "https://cdn.example/unlimited.png", "imageFallbackUrls": ["https://cdn.example/first.png"]})}

    assert deckledger.images.cached_real_image(row, "v")[0].read_bytes() == scan
    # A placeholder already in the cache from before is removed and the scan served instead.
    stale = deckledger.images.IMAGE_SOURCE_CACHE / f"{hashlib.sha256(b'https://cdn.example/unlimited.png').hexdigest()}.img"
    stale.write_bytes(placeholder)
    stale.with_suffix(".mime").write_text("image/png")
    assert deckledger.images.cached_real_image(row, "v")[0].read_bytes() == scan
    assert not stale.exists()
