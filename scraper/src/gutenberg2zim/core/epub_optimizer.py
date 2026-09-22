"""Source-agnostic in-memory EPUB optimization.

EPUB packages are ZIP archives with stricter interoperability requirements than
ordinary ZIP files.  In particular, ``mimetype`` must be the first member and
must not be compressed.  This module rewrites packages without changing their
member order or metadata, while allowing sources to supply document-specific
transforms.
"""

import io
import zipfile
from collections.abc import Callable
from copy import copy
from pathlib import Path

from zimscraperlib.image.optimization import optimize_jpeg, optimize_png

from gutenberg2zim.constants import logger

DocumentTransform = Callable[[str, bytes], bytes]

_DOCUMENT_SUFFIXES = frozenset({".htm", ".html", ".xhtml", ".ncx"})
_IMAGE_OPTIMIZERS = {
    ".jpg": optimize_jpeg,
    ".jpeg": optimize_jpeg,
    ".png": optimize_png,
}


def optimize_epub_bytes(
    epub_bytes: bytes, *, document_transform: DocumentTransform | None = None
) -> bytes:
    """Optimize EPUB JPEG/PNG members without changing their file format.

    ``document_transform`` is deliberately source-owned: it receives a member
    name and bytes for HTML/XHTML/NCX members and may return replacement bytes.
    JPEG and PNG members are retained unchanged whenever optimization is not
    smaller.  No persistent cache is used.
    """
    source_buffer = io.BytesIO(epub_bytes)
    destination_buffer = io.BytesIO()

    with (
        zipfile.ZipFile(source_buffer, "r") as source,
        zipfile.ZipFile(destination_buffer, "w") as destination,
    ):
        infos = source.infolist()
        mimetype_index = next(
            (index for index, info in enumerate(infos) if info.filename == "mimetype"),
            None,
        )
        if mimetype_index is None:
            raise ValueError("EPUB is missing its mimetype entry")

        destination.comment = source.comment
        _write_member(
            destination,
            infos[mimetype_index],
            source.read(infos[mimetype_index]),
            compress_type=zipfile.ZIP_STORED,
        )

        for index, info in enumerate(infos):
            if index == mimetype_index:
                continue

            data = source.read(info)
            suffix = Path(info.filename).suffix.lower()
            data = _optimize_member(
                info.filename, data, suffix, document_transform=document_transform
            )
            _write_member(destination, info, data)

    return destination_buffer.getvalue()


def _optimize_member(
    filename: str,
    data: bytes,
    suffix: str,
    *,
    document_transform: DocumentTransform | None,
) -> bytes:
    image_optimizer = _IMAGE_OPTIMIZERS.get(suffix)
    if image_optimizer is not None:
        try:
            optimized = _run_image_optimizer(image_optimizer, data)
        except Exception:
            logger.warning(
                "Could not optimize EPUB image %s; retaining the original",
                filename,
                exc_info=True,
            )
            return data
        return optimized if len(optimized) < len(data) else data
    if document_transform is not None and suffix in _DOCUMENT_SUFFIXES:
        return document_transform(filename, data)
    return data


def _run_image_optimizer(optimizer: Callable[..., None], data: bytes) -> bytes:
    destination = io.BytesIO()
    optimizer(src=io.BytesIO(data), dst=destination)
    return destination.getvalue()


def _write_member(
    archive: zipfile.ZipFile,
    source_info: zipfile.ZipInfo,
    data: bytes,
    *,
    compress_type: int | None = None,
) -> None:
    """Write a member with its source ZIP metadata preserved."""
    output_info = copy(source_info)
    if compress_type is not None:
        output_info.compress_type = compress_type
    archive.writestr(output_info, data)
