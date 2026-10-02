"""Venues: resolve free-text event locations to a committed venue list.

See feature-specs/venues.md. The venue registry (`data/venues.json`) and the
location-string map (`data/venue_locations.json`) are committed files, joined
to events at export time — never written into event rows.
"""
