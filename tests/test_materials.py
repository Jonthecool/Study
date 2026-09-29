import pytest

from study_agent.materials import load_materials


def test_loads_text_and_pdf_from_folder(tmp_path):
    (tmp_path / "notes.md").write_text("# Mitosis")
    (tmp_path / "slides.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "image.png").write_bytes(b"skip me")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.md").write_text("skip me too")

    docs, files = load_materials([tmp_path])

    assert [f.name for f in files] == ["notes.md", "slides.pdf"]
    assert docs[0]["source"] == {"type": "text", "media_type": "text/plain", "data": "# Mitosis"}
    assert docs[1]["source"]["media_type"] == "application/pdf"
    assert docs[1]["title"] == "slides.pdf"


def test_rejects_missing_and_unsupported(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_materials([tmp_path / "nope.md"])
    (tmp_path / "photo.png").write_bytes(b"x")
    with pytest.raises(ValueError):
        load_materials([tmp_path / "photo.png"])
