# Changelog

## 0.1.2 — 2026-10-09

### Fixed

- Absent TeX resources, such as the virtual font of every TFM font, are no
  longer looked up with a separate Tectonic call for every label. Resource hits
  and misses are cached by filename for the lifetime of the process. Each
  Tectonic 0.16 call takes over a second, so the repeated lookups made figures
  with many labels several times slower to render.
- U+2212 MINUS SIGN, which Matplotlib's tick labels use by default, renders as
  a minus instead of being silently dropped. Matplotlib's `inputenc` character
  declarations are translated to XeTeX rather than removed.
- A character missing from its font is now an error that quotes TeX's
  "Missing character" message, instead of being silently omitted.
- XDV files are cached under a hash of the source that Tectonic compiles. DVI
  files cached by 0.1.1, which could lack the minus sign, or by `latex` are not
  reused.
- Tectonic's diagnostics, such as the `note:` lines it prints when it fetches
  fonts, are no longer passed through to stderr. They are logged by the
  `mpl_tectonic` logger at debug level and included in the error when a
  Tectonic call fails.

### Changed

- Tectonic 0.17 is the validated version in the Pixi environment, and the
  conda package accepts Tectonic `>=0.15,<0.18`. Tectonic 0.17 no longer
  contacts its bundle server on every call, which makes each label compile
  about ten times faster than with Tectonic 0.16.

## 0.1.1 — 2026-10-02

### Fixed

- Native fonts whose XDV records already carry the `.otf` extension are fetched
  from Tectonic's bundle without a second extension.

## 0.1.0 — 2026-08-09

- Initial release.
