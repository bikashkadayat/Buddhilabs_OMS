"""One upload validator for the whole project.

Phase 11 audit finding M2. The memo serializer already did this properly --
size cap, extension allowlist, and a magic-byte sniff that rejects an HTML file
renamed to ``.pdf``. But that logic lived inside ``memos/serializers.py``, so
when Phase 9 added attachments to attendance-correction requests they inherited
**none** of it: any file, any size, any content.

That is the recurring shape of this class of bug -- validation implemented once,
in the wrong place, and silently not applied the next time the same feature is
built. So it moves here, and both call sites use it.

Also provides ``secure_file_response``, because validating an upload is only
half the job: a stored file still has to be served in a way that cannot execute
in the application's origin. ``ProtectedMediaView`` already set those headers;
the correction-attachment download did not.
"""
import logging

from rest_framework import serializers

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "docx", "xlsx", "png", "jpg", "jpeg"}

# THE DOCUMENT-MODULE POLICY (Phase ATTACHMENT-POLICY-FINAL).
#
# One set, imported by memos, minutes and circulars, so the three cannot drift
# apart again — which is how this started: memo allowed csv/doc/xls that could
# never upload, circulars silently inherited the narrower default above, and
# minutes rejected csv for no recorded reason.
#
# Every entry here has a matching key in ALLOWED_ATTACHMENT_MIMES below, and
# `validate_attachment` now logs loudly if that ever stops being true.
DOCUMENT_ATTACHMENT_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt",
    "png", "jpg", "jpeg", "webp",
}

# The real content type the bytes must have, keyed by extension. docx/xlsx are
# ZIP containers, so libmagic reports either the OOXML type or a generic zip
# depending on its version -- both are accepted.
ALLOWED_ATTACHMENT_MIMES = {
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    # text/plain is NOT a slack default here — libmagic reports a single-column
    # CSV as text/plain because it contains no delimiter to recognise. Measured,
    # not assumed: "name,age\n..." sniffs as text/csv, "total\n42\n" as
    # text/plain. Both are CSV files a person will upload.
    "csv": {"text/csv", "text/plain", "application/csv"},
    "txt": {"text/plain"},
    # Legacy Office is an OLE2 compound document, and libmagic cannot tell a
    # .doc from a .xls from the container alone — both report
    # application/x-ole-storage on this build, while other builds report the
    # specific type. Both forms are accepted; the extension allowlist is what
    # distinguishes them, which is the same trade already made for OOXML above.
    "doc": {"application/msword", "application/x-ole-storage",
            "application/vnd.ms-office"},
    "xls": {"application/vnd.ms-excel", "application/x-ole-storage",
            "application/vnd.ms-office"},
    "ppt": {"application/vnd.ms-powerpoint", "application/x-ole-storage",
            "application/vnd.ms-office"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    # pptx/gif/webp are known here so any caller CAN allow them, but they are
    # deliberately absent from ALLOWED_ATTACHMENT_EXTENSIONS above: widening the
    # default would silently widen what memos and attendance corrections accept.
    # The minute module passes its own extension set (minutes.services).
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    },
    "gif": {"image/gif"},
    "webp": {"image/webp"},
    # ZIP is a container, and libmagic reports it differently depending on how
    # it was produced (a plain archive, an empty one, or a self-extracting
    # header). All three are the same file type as far as an allowlist is
    # concerned. Known here so a caller CAN allow it, and deliberately absent
    # from ALLOWED_ATTACHMENT_EXTENSIONS below: an archive hides its contents
    # from every check this module makes, so each call site opts in explicitly
    # rather than inheriting it. The task module passes its own set.
    "zip": {"application/zip", "application/x-zip-compressed",
            "application/java-archive"},
}


def validate_attachment(value, *, max_size=None, extensions=None, mimes=None):
    """Validate an uploaded file. Returns it, or raises ValidationError.

    Three checks, in cost order -- size first because it is free, magic bytes
    last because it reads from the file.
    """
    if value is None:
        return value

    max_size = max_size or MAX_ATTACHMENT_SIZE
    extensions = extensions or ALLOWED_ATTACHMENT_EXTENSIONS
    mimes = mimes or ALLOWED_ATTACHMENT_MIMES

    if value.size > max_size:
        raise serializers.ValidationError(
            f"Attachment exceeds the {max_size // (1024 * 1024)}MB limit.")

    name = getattr(value, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in extensions:
        raise serializers.ValidationError(
            f"Unsupported file type '.{ext}'. Allowed types: "
            f"{', '.join(sorted(extensions))}.")

    detected = _sniff(value)
    if detected is None:
        # libmagic missing is a deployment problem, not a reason to accept
        # anything: the extension allowlist still applied above, and refusing
        # here would block every upload on a misconfigured host. Logged loudly
        # so it is visible rather than silently degraded.
        logger.error("libmagic unavailable — attachment accepted on extension "
                     "alone. Install libmagic1.")
        return value

    expected = mimes.get(ext)
    if not expected:
        # A CONFIGURATION FAULT, not a bad file — and this is the bug that
        # brought us here. A caller may widen `extensions` without adding the
        # matching MIME entry; `mimes.get(ext, set())` then returned an empty
        # set, so EVERY file of that type failed the content check with a
        # message blaming the user's file. Memo allowed doc, xls and csv this
        # way, and none of the three could ever be uploaded.
        #
        # Handled like the missing-libmagic case directly above: log loudly for
        # operators, accept on the extension allowlist that already passed.
        # Refusing here would keep punishing users for a server misconfiguration.
        logger.error(
            "Attachment extension '.%s' is allowed but has no entry in the MIME "
            "map — accepted on extension alone. Add it to "
            "ALLOWED_ATTACHMENT_MIMES.", ext)
        return value

    if detected not in expected:
        raise serializers.ValidationError(
            f"File content ('{detected}') does not match the '.{ext}' extension.")
    return value


def _mb(n):
    """Whole megabytes when it divides evenly, one decimal otherwise."""
    mb = n / (1024 * 1024)
    return f"{mb:.0f}" if abs(mb - round(mb)) < 0.05 else f"{mb:.1f}"


def validate_attachment_batch(files, *, max_count, max_total_size,
                              existing_count=0, max_per_parent=None,
                              field="files"):
    """Validate an upload BATCH -- how many files, and how much in total.

    `validate_attachment` above answers "is this one file acceptable?", which
    says nothing about a request carrying ten of them. Without this, the only
    bound on a memo upload was the per-file cap, so ten files at the 10MB limit
    made a 94MB request the server accepted and buffered. Measured on the
    development server: resident memory went from 420MB to 1.39GB on a single
    94MB upload and settled ~500MB above where it started.

    Note for anyone reaching for a Django setting instead: DATA_UPLOAD_MAX_
    MEMORY_SIZE does NOT cover this. It is explicitly documented as excluding
    file upload data, so it caps form fields, not a multipart body full of
    attachments. DATA_UPLOAD_MAX_NUMBER_FILES (Django 5.1+) does cap the file
    COUNT and is set as a backstop in settings, but nothing in the framework
    caps total uploaded BYTES -- that has to be enforced here, and at the
    reverse proxy (nginx `client_max_body_size`) in front of it.

    Every message names the limit and what was sent, because "upload failed"
    on a 90MB request the user waited two minutes for is not a usable answer.

    :param files:          the uploaded files in this request
    :param max_count:      most files accepted in ONE request
    :param max_total_size: most bytes accepted in ONE request
    :param existing_count: how many the parent object already holds
    :param max_per_parent: most the parent may hold in total, or None
    :param field:          the key the message is filed under, so the client
                           finds it where it already looks for upload errors
                           rather than in a bare top-level list
    """
    def refuse(message):
        raise serializers.ValidationError({field: [message]})

    count = len(files)
    if count > max_count:
        refuse(f"{count} files were sent, but at most {max_count} can be "
               f"attached at once. Upload them in smaller batches.")

    if max_per_parent is not None and existing_count + count > max_per_parent:
        remaining = max(max_per_parent - existing_count, 0)
        refuse(f"This item already has {existing_count} attachment"
               f"{'' if existing_count == 1 else 's'} and the limit is "
               f"{max_per_parent}. "
               + (f"You can add {remaining} more." if remaining
                  else "Remove one before adding another."))

    total = sum(getattr(f, "size", 0) or 0 for f in files)
    if total > max_total_size:
        refuse(f"These {count} files total {_mb(total)}MB, over the "
               f"{_mb(max_total_size)}MB limit for one upload. Send fewer "
               f"files, or attach the largest separately.")
    return files


def _sniff(value):
    """MIME type from the leading bytes, or None if libmagic is unavailable."""
    try:
        import magic
    except ImportError:
        return None
    try:
        head = value.read(2048)
        value.seek(0)
        return magic.from_buffer(head, mime=True)
    except Exception:  # noqa: BLE001
        logger.exception("Magic-byte sniff failed")
        value.seek(0)
        return None


def harden_file_response(response):
    """Apply the headers that stop a stored file executing in our origin.

    ``Content-Disposition: attachment`` alone is not enough: it has been
    bypassable in older browsers, and a user who chooses "open" gets the file
    rendered. ``nosniff`` stops content-type guessing and the sandbox CSP means
    that even if it is rendered it can run nothing and reach nothing.
    """
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Referrer-Policy"] = "no-referrer"
    return response
