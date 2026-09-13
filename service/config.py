"""Cross-scraper configuration.

Kept as a small module rather than as magic numbers scattered across scrapers,
so every new source honors the same conventions.
"""

# How far ahead of "today" the pipeline will look. Scrapers should stop walking
# pagination past this horizon, and save_events() drops any straggling events
# whose start_time is beyond it as defense-in-depth.
LOOKAHEAD_DAYS = 365
