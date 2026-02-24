# Changelog

## [Unreleased]

## [v1.0.1-moz.2.0] – 2026-02-24
### Added
- Display roads on consumer mini grid maps (consumer selection and results view)
- Display monitored minigrids and fetch status ([#16](https://github.com/Aiguasol-coop/django-offgridplanner/pull/16))
- Handle changing projects from analyzing to monitoring status([#16](https://github.com/Aiguasol-coop/django-offgridplanner/pull/16))
- Display analyzing projects in potential map ([#15](https://github.com/Aiguasol-coop/django-offgridplanner/pull/15))

### Changed
- Added clustering methodology to methodology page ([#15](https://github.com/Aiguasol-coop/django-offgridplanner/pull/15))
- Remove project from potential table when moved to analyzing ([#15](https://github.com/Aiguasol-coop/django-offgridplanner/pull/15))

### Fixed
- Fix processing and unit issues between API responses and tool ([#15](https://github.com/Aiguasol-coop/django-offgridplanner/pull/15))
- Fix marker snapping on pole editor ([#15](https://github.com/Aiguasol-coop/django-offgridplanner/pull/15))

## [v1.0.1-moz.1.1] – 2026-01-28
### Added
- Added Satellite layer to potential minigrids map.

### Changed
- Keep steps navbar on top while scrolling.
- Minorly changed layout of energy system components and updated names.

### Fixed
- Replaced favicon
- Fixed results page functions (graphs and exports).

## [v1.0.1-moz.1.0] – 2026-01-07
### Added
- First official versioned release.

### Notes
- This app is the Mozambique fork of [django-offgridplanner]([https://github.com/rl-institut/django-offgridplanner) and rebases on changes and features introduced upstream.
- This implementation introduces new features specific to the project.
