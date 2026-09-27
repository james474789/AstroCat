"""R2a §5: advice outcomes (pure computation; the SQL feeding it is in loader.py)."""

from datetime import date, datetime, timedelta

from app.schemas.recommendations import OutcomesResponse
from app.services.recommend.outcomes import ImagedRow, Impression, compute_outcomes, imaged_by_night
from app.utils.observing_night import night_of

TODAY = date(2026, 9, 27)
SINCE = TODAY - timedelta(days=90)
N1, N2, N3, N4 = date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)


def imp(night, key, lane="other", hero=False):
    return Impression(night, key, lane, hero)


def test_acted_on_uses_night_of_and_folded_keys():
    # A sub at 01:30 UTC on the 21st (lon 0) belongs to the night of the 20th.
    sub_night = night_of(datetime(2026, 9, 21, 1, 30), None, 0.0)
    assert sub_night == N1
    rows = [ImagedRow("OBJ:M81LUM", sub_night), ImagedRow("NGC7000", N2)]
    imaged, nights = imaged_by_night(rows, key_map={"OBJ:M81LUM": "M81"})
    assert imaged == {N1: {"M81"}, N2: {"NGC7000"}} and nights == {N1, N2}
    out = compute_outcomes([imp(N1, "M81", "active", True), imp(N2, "NGC7000", "pinned", True),
                            imp(N2, "M31")], imaged, nights, [], SINCE, TODAY)
    OutcomesResponse.model_validate(out)
    assert out["hero"] == {"shown": 2, "acted": 2, "rate": 1.0}
    assert out["any"] == {"shown": 3, "acted": 2, "rate": 0.667, "nights_with_any_acted": 2}
    assert out["by_lane"]["pinned"] == {"shown": 1, "acted": 1, "rate": 1.0}
    assert out["by_lane"]["other"] == {"shown": 1, "acted": 0, "rate": 0.0}


def test_denominator_is_imaged_nights_only():
    # N1 cloudy (nothing imaged): its impressions don't count. N2 imaged something else.
    imps = [imp(N1, "M42", hero=True), imp(N1, "M45"), imp(N2, "M42", hero=True), imp(N2, "M45")]
    imaged, nights = imaged_by_night([ImagedRow("M45", N2)])
    out = compute_outcomes(imps, imaged, nights, [], SINCE, TODAY)
    assert out["nights_with_impressions"] == 2 and out["nights_imaged"] == 1
    assert out["hero"] == {"shown": 1, "acted": 0, "rate": 0.0}
    assert out["any"]["shown"] == 2 and out["any"]["acted"] == 1 and out["any"]["rate"] == 0.5


def test_untargeted_frames_still_mark_a_night():
    imaged, nights = imaged_by_night([ImagedRow(None, N3)])
    out = compute_outcomes([imp(N3, "M42", hero=True)], imaged, nights, [], SINCE, TODAY)
    assert out["nights_imaged"] == 1 and out["hero"]["shown"] == 1 and out["hero"]["acted"] == 0


def test_pending_nights_are_excluded():
    last = TODAY - timedelta(days=1)            # evaluable
    imaged, nights = imaged_by_night([ImagedRow("M42", last), ImagedRow("M42", TODAY)])
    out = compute_outcomes([imp(last, "M42", hero=True), imp(TODAY, "M42", hero=True),
                            imp(TODAY + timedelta(days=1), "M42")], imaged, nights, [], SINCE, TODAY)
    assert out["pending_nights"] == 2
    assert out["nights_with_impressions"] == 1 and out["hero"] == {"shown": 1, "acted": 1, "rate": 1.0}


def test_window_start_is_respected():
    old = SINCE - timedelta(days=1)
    imaged, nights = imaged_by_night([ImagedRow("M42", old)])
    out = compute_outcomes([imp(old, "M42", hero=True)], imaged, nights, [], SINCE, TODAY)
    assert out["nights_with_impressions"] == 0 and out["since"] == SINCE.isoformat()


def test_imaged_not_shown():
    imps = [imp(N1, "M42", hero=True), imp(N2, "M31", hero=True), imp(N3, "M33", hero=True)]
    imaged, nights = imaged_by_night([ImagedRow("M42", N1), ImagedRow("NGC7000", N1),   # extra target: not shown
                                      ImagedRow("M31", N2),                              # only what was shown
                                      ImagedRow("IC1396", N4)])                          # no impressions that night
    out = compute_outcomes(imps, imaged, nights, [], SINCE, TODAY)
    assert out["imaged_not_shown"] == 1


def test_self_reported_vs_confirmed():
    imaged, nights = imaged_by_night([ImagedRow("M42", N1), ImagedRow("M31", N2)])
    events = [(N1, "M42"), (N1, "M42"),          # double click: one report
              (N3, "M31"),                       # pressed the next day: N2 in the library confirms it
              (N4, "M33"),                       # nothing in the library
              (TODAY, "M45")]                    # pending
    out = compute_outcomes([], imaged, nights, events, SINCE, TODAY)
    assert out["self_reported"] == {"imaged_events": 3, "confirmed_by_library": 2}


def test_empty_is_the_collecting_data_shape():
    out = compute_outcomes([], {}, set(), [], SINCE, TODAY)
    OutcomesResponse.model_validate(out)
    assert out == {"since": SINCE.isoformat(), "nights_with_impressions": 0, "nights_imaged": 0,
                   "hero": {"shown": 0, "acted": 0, "rate": 0.0},
                   "any": {"shown": 0, "acted": 0, "rate": 0.0, "nights_with_any_acted": 0},
                   "by_lane": {}, "imaged_not_shown": 0,
                   "self_reported": {"imaged_events": 0, "confirmed_by_library": 0}, "pending_nights": 0}
