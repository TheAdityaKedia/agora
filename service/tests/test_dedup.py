"""Cross-source fuzzy dedup. Positive cases are real duplicate pairs found in
production (2026-09-29); negatives are look-alikes that must stay separate."""
import pytest

import dedup

SAME = [
    # (title A, location A, title B, location B)
    ("Trivia at Belle Cora", "Belle Cora, 565 Green Street, San Francisco",
     "Trivia Night at Belle Cora (San Francisco)", "565 Green St, San Francisco, CA"),
    ("Litquake's Opening Night Bash: Booksellers Ball", "524 Union, 524 Union St, San Francisco",
     "Litquake’s Opening Night Bash: Booksellers Ball", "524 Union Street San Francisco"),
    ("Truong Tran / Launch Party for “How to Exi(s)t: A Poet’s Memoir”",
     "City Lights Booksellers, 261 Columbus Ave, San Francisco",
     "How to Exi(s)t: A Poet’s Memoir with Truong Tran", "City Lights, 261 Columbus Ave, San Francisco"),
    ("A Celebration of César Vallejo’s Trilce", "City Lights Booksellers, 261 Columbus Ave",
     "A Celebration of César Vallejo's Trilce", "City Lights, 261 Columbus Ave"),
    ("Lindy West", "Sydney Goldstein Theater",
     "Offsite: Lindy West at City Arts & Lectures", None),
    ("Gunhild Carling", "Biscuits & Blues, 401 Mason St, San Francisco",
     "Gunhild Carling at Biscuits & Blues", "Biscuits & Blues, 401 Mason St, San Francisco"),
    ("Community Co-Working", "The Berkeley Alembic, 2820 Seventh Street, Berkeley, CA",
     "Alembic Community Co-Working", "The Berkeley Alembic, 2820 Seventh Street, Berkeley, CA"),
    ("Deconstructing Yourself", None, "Deconstructing Yourself with Michael Taft",
     "The Berkeley Alembic, 2820 Seventh Street, Berkeley, CA"),
    # Street name that starts with a digit ("2781 24th Street").
    ("Foutenanny! Fou Fou Ha's 25th Anniversary Celebration",
     "Brava Mainstage, 2781 24th Street, San Francisco, CA, 94110, United States",
     "Foutenanny!  Fou Fou Ha's 25th Anniversary Celebration",
     "Brava Theater Center, 2781 24th Street, San Francisco, CA 94110, San Francisco, CA"),
]

DIFFERENT = [
    # Same time, different venues, similar titles.
    ("Trivia at Belle Cora", "Belle Cora, 565 Green Street, San Francisco",
     "Trivia at the Mucky Duck", "Mucky Duck, 1315 9th Ave, San Francisco"),
    ("Beginner Salsa", "Cheryl Burke Dance, 1 Market St, San Francisco",
     "Advanced Salsa", "Allegro Ballroom, 5855 Christie Ave, Emeryville"),
    # Same venue, different events.
    ("Jazz Jam", "El Rio, 3158 Mission St, San Francisco",
     "Poetry Slam", "El Rio, 3158 Mission St, San Francisco"),
    # One shared word is not enough.
    ("Open Mic", "The Lost Church, 988 Columbus Ave", "Comedy Open House", "The Lost Church, 988 Columbus Ave"),
    # No locations: only containment counts, and a single word is too weak.
    ("Trivia", None, "Trivia at the Mucky Duck", None),
]


@pytest.mark.parametrize("ta,la,tb,lb", SAME)
def test_real_cross_source_duplicates_match(ta, la, tb, lb):
    assert dedup.is_near_duplicate(ta, la, tb, lb)
    assert dedup.is_near_duplicate(tb, lb, ta, la)  # symmetric


@pytest.mark.parametrize("ta,la,tb,lb", DIFFERENT)
def test_look_alikes_stay_separate(ta, la, tb, lb):
    assert not dedup.is_near_duplicate(ta, la, tb, lb)


def test_normalization_handles_accents_and_curly_quotes():
    assert dedup.title_tokens("César Vallejo’s Trilce") == dedup.title_tokens("Cesar Vallejo's TRILCE")


def test_zip_codes_are_not_street_numbers():
    assert not dedup.locations_agree("Cafe A, 1 Main St, San Francisco, CA 94110",
                                     "Bar B, 9 Oak St, San Francisco, CA 94110")
