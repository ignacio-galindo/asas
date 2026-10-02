"""The engine's notion of "today" — the one input every temporal rule reads.

``date.today()`` answers in the *server's* zone. A host deployed in UTC and used
from the Gulf (UTC+4) sees the calendar roll four hours late: a date the user
typed as "today" after 20:00 UTC is refused as ``not_future`` while their clock
plainly says it is today. The zone the rules should agree with is the *users'*
calendar, so the host names it once at boot:

    asas_validation.configure(timezone="Asia/Dubai")

Precedence, most specific first: an explicit ``today=`` passed to ``evaluate``
(tests, batch jobs replaying a past day) → a configured ``today`` callable → a
configured ``timezone`` → ``date.today()``. Calling ``configure()`` with no
arguments returns to the default. The configured zone is also the zone an
**aware** ``datetime`` value is read in before being reduced to a calendar date.
"""

from datetime import date, datetime, tzinfo
from typing import Callable, Optional, Union
from zoneinfo import ZoneInfo

_today: Optional[Callable[[], date]] = None
_tz: Optional[tzinfo] = None


def configure(
    today: Optional[Callable[[], date]] = None,
    timezone: Union[str, tzinfo, None] = None,
) -> None:
    """Host hook (the Asas ``configure`` convention). ``timezone`` is an IANA name
    (``"Asia/Dubai"``) or a ``tzinfo``; an unknown name fails here, at boot, rather
    than on the first request. ``today`` is a zero-argument callable and wins over
    ``timezone`` when both are given. No arguments → back to ``date.today()``."""
    global _today, _tz
    _tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    _today = today


def timezone() -> Optional[tzinfo]:
    """The configured zone, or ``None`` when the host never configured one."""
    return _tz


def today() -> date:
    """The calendar date the rules compare against, per the precedence above."""
    if _today is not None:
        return _today()
    if _tz is not None:
        return datetime.now(_tz).date()
    return date.today()
