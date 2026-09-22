"""Tests for source-agnostic EPUB archive optimization."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from gutenberg2zim.core import epub_optimizer
from gutenberg2zim.core.epub_optimizer import optimize_epub_bytes

pytestmark = pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")


def _epub_with_members() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.comment = b"archive comment"
        _write_member(
            archive,
            "mimetype",
            b"application/epub+zip",
            compression=ZIP_STORED,
        )
        _write_member(archive, "META-INF/container.xml", b"container")
        _write_member(archive, "OEBPS/chapter.xhtml", b"first chapter")
        _write_member(archive, "OEBPS/images/cover.jpg", b"original jpeg data")
        _write_member(archive, "OEBPS/images/diagram.png", b"original png data")
        _write_member(archive, "OEBPS/chapter.xhtml", b"second chapter")
        _write_member(archive, "OEBPS/toc.ncx", b"navigation")
    return output.getvalue()


def _write_member(
    archive: ZipFile, name: str, data: bytes, *, compression: int = ZIP_DEFLATED
) -> None:
    info = ZipInfo(name, date_time=(2020, 2, 3, 4, 5, 6))
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = 0o100640 << 16
    info.internal_attr = 1
    info.comment = f"comment for {name}".encode()
    archive.writestr(info, data)


def _members(content: bytes) -> tuple[list[ZipInfo], list[bytes], bytes]:
    with ZipFile(BytesIO(content)) as archive:
        infos = archive.infolist()
        return infos, [archive.read(info) for info in infos], archive.comment


def _copy_image(*, src, dst):
    dst.write(src.read())


def _disable_image_optimization(monkeypatch) -> None:
    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".jpg", _copy_image)
    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".png", _copy_image)


def test_optimizer_preserves_epub_zip_invariants_and_member_metadata(monkeypatch):
    _disable_image_optimization(monkeypatch)
    source = _epub_with_members()

    output = optimize_epub_bytes(source)

    source_infos, source_members, source_comment = _members(source)
    output_infos, output_members, output_comment = _members(output)

    assert [info.filename for info in output_infos] == [
        info.filename for info in source_infos
    ]
    assert output_members == source_members
    assert output_comment == source_comment
    assert output_infos[0].filename == "mimetype"
    assert output_infos[0].compress_type == ZIP_STORED

    for source_info, output_info in zip(source_infos, output_infos, strict=True):
        assert output_info.date_time == source_info.date_time
        assert output_info.external_attr == source_info.external_attr
        assert output_info.internal_attr == source_info.internal_attr
        assert output_info.create_system == source_info.create_system
        assert output_info.create_version == source_info.create_version
        assert output_info.extract_version == source_info.extract_version
        assert output_info.comment == source_info.comment


def test_optimizer_keeps_only_smaller_jpeg_and_png_optimizations(monkeypatch):
    def smaller(*, src, dst):
        assert src.read()
        dst.write(b"small")

    def larger(*, src, dst):
        assert src.read()
        dst.write(b"this replacement is larger than the original PNG data")

    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".jpg", smaller)
    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".png", larger)

    _, members, _ = _members(optimize_epub_bytes(_epub_with_members()))

    assert members[3] == b"small"
    assert members[4] == b"original png data"


def test_optimizer_keeps_an_image_when_optimization_fails(monkeypatch):
    def fail(**_kwargs):
        raise ValueError("not an image")

    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".jpg", fail)
    monkeypatch.setitem(epub_optimizer._IMAGE_OPTIMIZERS, ".png", _copy_image)

    _, members, _ = _members(optimize_epub_bytes(_epub_with_members()))

    assert members[3] == b"original jpeg data"


def test_optimizer_applies_source_document_callback_to_document_members(monkeypatch):
    _disable_image_optimization(monkeypatch)
    transformed: list[str] = []

    def transform(filename: str, data: bytes) -> bytes:
        transformed.append(filename)
        return data + b" transformed"

    infos, members, _ = _members(
        optimize_epub_bytes(_epub_with_members(), document_transform=transform)
    )
    contents = dict(zip((info.filename for info in infos), members, strict=True))

    assert transformed == [
        "OEBPS/chapter.xhtml",
        "OEBPS/chapter.xhtml",
        "OEBPS/toc.ncx",
    ]
    assert contents["OEBPS/toc.ncx"] == b"navigation transformed"


def test_optimizer_requires_an_epub_mimetype_member():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("chapter.xhtml", b"chapter")

    with pytest.raises(ValueError, match="mimetype"):
        optimize_epub_bytes(output.getvalue())
