# PDF fonts

Drop `Poppins-*.ttf` and `Inter-*.ttf` here for consistent PDF typography.
The PDF templates reference these via `@font-face` and fall back to
`Helvetica, Arial, sans-serif` when the files are absent, so PDFs render
correctly either way (weasyprint uses system fonts as the fallback).
